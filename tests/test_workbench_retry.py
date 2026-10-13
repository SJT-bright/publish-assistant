"""Explicit recovery with isolated PRIVATE, mock workers and local HTTP only."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import draft_workbench as wb


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(wb, 'PRIVATE', self.root / 'private'),
                        patch.object(wb, 'STORE_ROOT', None), patch.object(wb, 'CURRENT', None),
                        patch.object(wb, 'EDITOR', {}), patch.object(wb, 'LEDGER', {}),
                        patch.object(wb, 'ACCOUNTS', {}), patch.object(wb, 'HISTORY', []),
                        patch.object(wb, 'ASSETS', set()), patch.object(wb, 'PROCESS', None),
                        patch.object(wb, 'account_file', side_effect=lambda p: self.root / (p + '-session.json')),
                        patch.object(wb, 'embedded_account_file', side_effect=lambda p: self.root / (p + '-embedded.json'))]
        for item in self.patches: item.start()
        self.video = self.root / 'video.mp4'; self.video.write_bytes(b'isolated video')
        self.cover = self.root / 'cover.jpg'; self.cover.write_bytes(b'isolated cover')
        self.payload = {'video': str(self.video), 'title': '恢复测试', 'description': '隔离正文',
                        'cover': str(self.cover), 'tags': ['测试']}
        self.browser = 'external'

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def start(self, platforms=('xiaohongshu',), **extra):
        with patch.object(wb.threading, 'Thread') as thread:
            job = wb.start_job({'action': 'draft', 'platforms': list(platforms),
                                'browser': self.browser, 'payload': self.payload, **extra})
        return job, thread

    def held(self, status='result_unknown', platforms=('xiaohongshu',), message=None, reuse=True):
        original, _ = self.start(platforms)
        for item in original['results']: wb.transition(original, item, status, message=message)
        original['active'] = False; wb.persist()
        shown, thread = self.start(platforms) if reuse else (original, None)
        if thread: thread.assert_not_called()
        return original, shown

    def request(self, platform='xiaohongshu'):
        item = next(i for i in wb.CURRENT['results'] if i['platform'] == platform)
        return {'expected_job_id': wb.CURRENT['id'], 'platform': platform,
                'previous_job_id': item.get('previous_job_id', wb.CURRENT['id']),
                'browser': self.browser, 'payload': copy.deepcopy(self.payload)}

    def retry(self, data=None):
        with patch.object(wb.threading, 'Thread') as thread:
            result = wb.retry_job(data or self.request())
        return result, thread

    def test_explicit_retry_creates_one_platform_with_new_nonce_and_keeps_old_job(self):
        original, shown = self.held(platforms=('xiaohongshu', 'bilibili'))
        old_ledger = copy.deepcopy(wb.LEDGER)
        request = self.request('bilibili')
        retried, thread = self.retry(request)
        thread.assert_called_once(); thread.return_value.start.assert_called_once()
        self.assertTrue(retried['active'])
        self.assertEqual([i['platform'] for i in retried['results']], ['bilibili'])
        item = retried['results'][0]
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['recovery_of']['job_id'], original['id'])
        self.assertEqual(item['recovery_of']['requested_from_job_id'], shown['id'])
        self.assertNotEqual(item['expected']['receipt_nonce'], original['results'][1]['expected']['receipt_nonce'])
        self.assertNotEqual(retried['id'], shown['id'])
        self.assertEqual(wb.LEDGER, old_ledger)
        self.assertIn(shown, wb.HISTORY)
        self.assertEqual(original['results'][1]['status'], 'result_unknown')

    def test_allowed_unresolved_statuses_and_historical_control_mismatch(self):
        for status, message in [('result_unknown', None), ('saved_unverified', None),
                                ('adapter_mismatch', None),
                                ('platform_restricted', '当前页面有多个可能的素材或内容控件，已停止以免填错。')]:
            with self.subTest(status=status):
                wb.CURRENT = None; wb.HISTORY = []; wb.LEDGER = {}
                self.held(status, message=message)
                self.assertTrue(wb.state()['job']['results'][0]['retryable'])
                retried, thread = self.retry()
                self.assertEqual(retried['results'][0]['status'], 'pending')
                thread.assert_called_once()

    def test_risk_real_restriction_verified_and_other_statuses_cannot_retry(self):
        for status in ('risk_paused', 'platform_restricted', 'local_draft_verified', 'failed',
                       'cancelled', 'needs_reauthentication', 'account_mismatch'):
            with self.subTest(status=status):
                wb.CURRENT = None; wb.HISTORY = []; wb.LEDGER = {}
                self.held(status, reuse=False)
                self.assertFalse(wb.state()['job']['results'][0]['retryable'])
                before = copy.deepcopy(wb.CURRENT)
                with patch.object(wb.threading, 'Thread') as thread, self.assertRaises(ValueError):
                    wb.retry_job(self.request())
                thread.assert_not_called(); self.assertEqual(wb.CURRENT, before)

    def test_ordinary_start_cannot_bypass_hold_with_extra_parameters(self):
        self.held()
        before = copy.deepcopy(wb.LEDGER)
        job, thread = self.start(force_retry=True, retry=True, recovery_of={'job_id': 'fake'})
        self.assertFalse(job['active'])
        self.assertEqual(job['results'][0]['status'], 'result_unknown')
        self.assertTrue(job['results'][0]['reused'])
        self.assertNotIn('recovery_of', job['results'][0])
        thread.assert_not_called(); self.assertEqual(wb.LEDGER, before)

    def test_duplicate_rows_preserve_status_specific_error_and_real_source_steps(self):
        original, _ = self.held(reuse=False, message='Locator.set_input_files: Timeout at fixture cover control')
        item = original['results'][0]
        item['progress'] = {'stage': 'cover', 'label': '设置封面', 'state': 'unknown',
                            'started_at': 100, 'updated_at': 110,
                            'completed': ['session', 'video', 'upload', 'content'], 'skipped': []}
        wb.persist()
        shown, worker = self.start()
        worker.assert_not_called()
        result = wb.state()['job']['results'][0]
        self.assertIn('fixture cover control', result['message'])
        self.assertNotIn('前次结果未确认或账号受限', result['message'])
        self.assertEqual(result['progress'], item['progress'])
        self.assertEqual(shown['results'][0]['progress'], item['progress'])
        self.assertEqual(wb.progress_state()['job']['progress'], item['progress'])
        self.assertEqual(result['progress']['completed'], ['session', 'video', 'upload', 'content'])
        self.assertNotIn('save', result['progress']['completed'])
        for status, message in [('risk_paused', 'fixture risk'), ('platform_restricted', 'fixture permission'),
                                ('saved_unverified', 'fixture save not verified'), ('adapter_mismatch', 'fixture controls')]:
            with self.subTest(status=status):
                wb.CURRENT = None; wb.HISTORY = []; wb.LEDGER = {}
                self.held(status, message=message)
                row = wb.state()['job']['results'][0]
                self.assertEqual(row['status'], status)
                self.assertIn(message, row['message'])
                self.assertNotIn('前次结果未确认或账号受限', row['message'])

    def test_existing_reused_rows_get_source_progress_read_only_and_missing_source_is_not_invented(self):
        original, shown = self.held(message='fixture original reason')
        original['results'][0]['progress'] = {'stage': 'upload', 'label': '等待视频处理', 'state': 'unknown',
                                            'started_at': 100, 'updated_at': 110, 'completed': ['video'], 'skipped': []}
        shown['results'][0].pop('progress', None)
        before = copy.deepcopy(shown)
        with patch.object(wb, 'persist') as writes:
            self.assertEqual(wb.state()['job']['results'][0]['progress']['completed'], ['video'])
            self.assertEqual(wb.progress_state()['job']['progress']['stage'], 'upload')
        writes.assert_not_called(); self.assertEqual(shown, before)
        wb.HISTORY = []
        (wb.PRIVATE / 'jobs' / original['id'] / '任务结果.json').unlink()
        self.assertNotIn('progress', wb.state()['job']['results'][0])
        self.assertIsNone(wb.progress_state()['job']['progress'])

    def test_stale_current_id_wrong_original_platform_browser_and_active_reject(self):
        self.held()
        request = self.request()
        bad = [{**request, 'expected_job_id': 'old-current'}, {**request, 'previous_job_id': 'old-original'},
               {**request, 'platform': 'bilibili'}, {**request, 'platform': ['xiaohongshu']},
               {**request, 'browser': 'embedded'}, {**request, 'payload': None}]
        for data in bad:
            with self.subTest(data={k: v for k, v in data.items() if k != 'payload'}):
                before = copy.deepcopy(wb.CURRENT)
                with patch.object(wb.threading, 'Thread') as thread, self.assertRaises(ValueError): wb.retry_job(data)
                thread.assert_not_called(); self.assertEqual(wb.CURRENT, before)
        wb.CURRENT['active'] = True
        with self.assertRaises(ValueError): self.retry(request)

    def test_video_content_and_cover_must_all_match_original_fingerprints(self):
        self.held()
        changed_video = self.root / 'other.mp4'; changed_video.write_bytes(b'another video')
        changed_cover = self.root / 'other.jpg'; changed_cover.write_bytes(b'another cover')
        for field, value in [('video', str(changed_video)), ('description', '变更正文'),
                             ('cover', str(changed_cover)), ('tags', ['另一个话题'])]:
            with self.subTest(field=field):
                data = self.request(); data['payload'][field] = value
                before = copy.deepcopy(wb.CURRENT)
                with patch.object(wb.threading, 'Thread') as thread, self.assertRaisesRegex(ValueError, '素材与待恢复记录不同'):
                    wb.retry_job(data)
                thread.assert_not_called(); self.assertEqual(wb.CURRENT, before)

    def test_preflight_limits_remain_enforced(self):
        self.held(); data = self.request(); data['payload']['title'] = '超出小红书标题限制' * 5
        with patch.object(wb.threading, 'Thread') as thread, self.assertRaisesRegex(ValueError, '预检未通过'):
            wb.retry_job(data)
        thread.assert_not_called()

    def test_preflight_cannot_switch_the_bound_account_or_item_platform(self):
        self.held(); data = self.request()
        check = wb.preflight({'payload': self.payload, 'platforms': ['xiaohongshu'], 'browser': self.browser})
        check['fingerprints']['xiaohongshu']['account_key'] = 'external:xiaohongshu:another-owner'
        with patch.object(wb, 'preflight', return_value=check), patch.object(wb.threading, 'Thread') as thread:
            with self.assertRaisesRegex(ValueError, '账号发生变化'): wb.retry_job(data)
            thread.assert_not_called()
        wb.CURRENT['results'][0]['expected']['platform_id'] = 'bilibili'
        self.assertFalse(wb.state()['job']['results'][0]['retryable'])
        with self.assertRaises(ValueError): self.retry(data)

    def test_identified_old_account_cannot_bind_to_a_different_current_account(self):
        wb.ensure_store()
        wb.ACCOUNTS['external:xiaohongshu'] = {'account': {'identity': 'owner-a'}, 'verified_at': 1}
        self.held()
        wb.ACCOUNTS['external:xiaohongshu'] = {'account': {'identity': 'owner-b'}, 'verified_at': 2}
        self.assertFalse(wb.state()['job']['results'][0]['retryable'])
        with patch.object(wb.threading, 'Thread') as thread, self.assertRaises(ValueError): wb.retry_job(self.request())
        thread.assert_not_called()

    def test_legacy_unknown_default_can_bind_current_account_without_erasing_legacy(self):
        original, _ = self.held()
        old_ledger = copy.deepcopy(wb.LEDGER)
        wb.ACCOUNTS['external:xiaohongshu'] = {'account': {'identity': 'owner-now'}, 'verified_at': 1}
        self.assertTrue(wb.state()['job']['results'][0]['retryable'])
        retried, _ = self.retry()
        item = retried['results'][0]
        self.assertEqual(item['expected']['expected_account_identity'], 'owner-now')
        self.assertNotEqual(item['expected']['operation_key'], original['results'][0]['expected']['operation_key'])
        wb.transition(retried, item, 'running')
        for key, record in old_ledger.items(): self.assertEqual(wb.LEDGER[key], record)

    def test_same_account_key_retry_preserves_flat_attempt_history_once_per_job(self):
        original, _ = self.held()
        old_record = copy.deepcopy(next(iter(wb.LEDGER.values())))
        retried, _ = self.retry(); item = retried['results'][0]
        wb.transition(retried, item, 'running')
        wb.transition(retried, item, 'result_unknown')
        record = wb.LEDGER[item['expected']['operation_key']]
        self.assertEqual(record['attempts'], [old_record])
        self.assertEqual(record['recovery_of']['job_id'], original['id'])
        self.assertEqual(record['job_id'], retried['id'])
        retried['active'] = False; wb.persist()
        next_job, _ = self.retry(self.request())
        wb.transition(next_job, next_job['results'][0], 'running')
        attempts = wb.LEDGER[next_job['results'][0]['expected']['operation_key']]['attempts']
        self.assertEqual([entry['job_id'] for entry in attempts], [original['id'], retried['id']])
        self.assertTrue(all('attempts' not in entry for entry in attempts))

    def test_second_request_and_restart_do_not_replay_an_authorized_attempt(self):
        self.held(); data = self.request(); retried, _ = self.retry(data)
        with patch.object(wb.threading, 'Thread') as thread, self.assertRaises(ValueError): wb.retry_job(data)
        thread.assert_not_called()
        wb.transition(retried, retried['results'][0], 'running')
        wb.STORE_ROOT = None
        with patch.object(wb.threading, 'Thread') as thread, patch.object(wb.subprocess, 'Popen') as process:
            wb.ensure_store()
            thread.assert_not_called(); process.assert_not_called()
            self.assertFalse(wb.CURRENT['active'])
            self.assertEqual(wb.CURRENT['results'][0]['status'], 'result_unknown')
            with self.assertRaises(ValueError): wb.retry_job(data)
            thread.assert_not_called(); process.assert_not_called()

    def test_concurrent_requests_can_create_only_one_new_job(self):
        self.held(); data = self.request()
        barrier = threading.Barrier(3)
        results = []
        def request():
            barrier.wait(timeout=2)
            try:
                results.append(('ok', wb.retry_job(copy.deepcopy(data))['id']))
            except ValueError as error:
                results.append(('rejected', str(error)))
        # Construct actual test threads before patching the worker launcher.
        threads = [threading.Thread(target=request) for _ in range(2)]
        with patch.object(wb.threading, 'Thread') as worker_thread:
            for thread in threads: thread.start()
            barrier.wait(timeout=2)
            for thread in threads: thread.join(timeout=3)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            worker_thread.assert_called_once()
        self.assertEqual(sorted(result[0] for result in results), ['ok', 'rejected'])

    def test_earlier_account_hold_is_not_hidden_by_later_material_record(self):
        self.held()
        matching = next(iter(wb.LEDGER.values()))
        hold = {**copy.deepcopy(matching), 'job_id': 'risk-job', 'status': 'risk_paused',
                'operation_key': 'risk-key', 'content_sha256': 'another-material'}
        wb.LEDGER = {'risk-key': hold, matching['operation_key']: matching}
        self.assertFalse(wb.state()['job']['results'][0]['retryable'])
        with self.assertRaises(ValueError): self.retry()

    def test_public_state_only_exposes_scoped_retry_reference(self):
        original, shown = self.held()
        result = wb.state()['job']['results'][0]
        self.assertTrue(result['retryable'])
        self.assertEqual(result['retry_previous_job_id'], original['id'])
        for hidden in ('expected', 'evidence', 'account_identity'): self.assertNotIn(hidden, result)
        for job in wb.state()['history']:
            self.assertTrue(all(not item['retryable'] and item['retry_previous_job_id'] is None for item in job['results']))
        self.assertEqual(shown['results'][0]['status'], 'result_unknown')

    def test_retry_worker_without_receipt_stays_unknown_and_keeps_original_attempt(self):
        original, _ = self.held(); retried, _ = self.retry()
        class Process:
            def __init__(self, cmd, **kwargs): self.stdout = iter(())
            def wait(self): return 0
        with patch.object(wb.subprocess, 'Popen', Process): wb.worker(retried, self.payload)
        self.assertFalse(retried['active'])
        self.assertEqual(retried['results'][0]['status'], 'result_unknown')
        record = wb.LEDGER[retried['results'][0]['expected']['operation_key']]
        self.assertEqual(record['attempts'][0]['job_id'], original['id'])
        self.assertEqual(record['attempts'][0]['status'], 'result_unknown')

    def test_retry_http_route_keeps_token_origin_and_host_guards(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), wb.Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{port}'
        # Exercise our loopback handler, including a deliberately invalid Host,
        # without an inherited system proxy intercepting that negative case.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            headers = {'Content-Type': 'application/json', 'X-Workbench-Token': wb.TOKEN, 'Origin': url}
            with patch.object(wb, 'PORT', port), patch.object(wb, 'URL', url), patch.object(wb, 'retry_job', return_value={'id': 'authorized-mock'}) as retry:
                request = urllib.request.Request(url + '/api/retry', data=b'{}', headers=headers)
                with opener.open(request) as response:
                    self.assertEqual(json.loads(response.read()), {'result': {'id': 'authorized-mock'}})
                retry.assert_called_once_with({}); retry.reset_mock()
                for changed in ({'X-Workbench-Token': 'wrong'}, {'Origin': 'https://wrong.test'}, {'Host': 'wrong.test'}):
                    request = urllib.request.Request(url + '/api/retry', data=b'{}', headers={**headers, **changed})
                    with self.assertRaises(urllib.error.HTTPError) as error: opener.open(request)
                    self.assertEqual(error.exception.code, 403)
                retry.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__': unittest.main(verbosity=2)
