"""Controlled progress and recovery using isolated files and mock workers."""
import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

import draft_workbench as wb
from workbench_progress import (PREFIX, parse_progress, apply_progress, public_progress,
                                progress_summary, LABELS)


def line(job='job-1', platform='xiaohongshu', stage='session', state='running', **extra):
    return PREFIX + json.dumps({'v': 1, 'job_id': job, 'platform': platform,
                               'stage': stage, 'state': state, **extra}, ensure_ascii=False)


class ProtocolTests(unittest.TestCase):
    def test_upload_and_editor_progress_overlap_without_hiding_work_or_resetting_wait_timer(self):
        item = {'status': 'running'}
        for stage, state, at in [('upload', 'running', 10), ('content', 'running', 20), ('upload', 'running', 30)]:
            self.assertTrue(apply_progress(item, parse_progress(line(stage=stage,state=state),'job-1','xiaohongshu'),at))
        self.assertEqual(item['progress']['stage'], 'content')
        self.assertEqual(public_progress(item['progress'])['active_stages'], ['upload','content'])
        apply_progress(item, parse_progress(line(stage='content',state='completed'),'job-1','xiaohongshu'),40)
        self.assertEqual(item['progress']['stage'], 'upload')
        self.assertEqual(item['progress']['started_at'], 10)
        apply_progress(item, parse_progress(line(stage='upload',state='completed'),'job-1','xiaohongshu'),50)
        self.assertEqual(item['progress']['completed'], ['upload','content'])
        self.assertNotIn('active_stages',public_progress(item['progress']))

    def test_all_real_emitter_phrases_are_accepted_without_payload_text(self):
        from uploader import draft_progress as emitter
        with patch.dict(emitter.os.environ, {'SAU_PROGRESS_JOB_ID': 'job-1', 'SAU_PROGRESS_PLATFORM': 'xiaohongshu'}):
            self.assertTrue(emitter.configure('xiaohongshu'))
            for stage in emitter.STAGES:
                for state in emitter.STATES:
                    output = io.StringIO()
                    with redirect_stdout(output): self.assertTrue(emitter.emit(stage, state))
                    event = parse_progress(output.getvalue(), 'job-1', 'xiaohongshu')
                    self.assertIsNotNone(event, (stage, state))
        with patch.dict(emitter.os.environ, {'SAU_PROGRESS_JOB_ID': '', 'SAU_PROGRESS_PLATFORM': ''}):
            emitter.configure('xiaohongshu')

    def test_event_requires_exact_identity_and_controlled_fields(self):
        self.assertIsNotNone(parse_progress(line(), 'job-1', 'xiaohongshu'))
        invalid = [line(job='old-job'), line(platform='bilibili'), line(v=True), line(v=2),
                   line(stage='publish'), line(state='verified'), line(stage=[]), line(state=[]),
                   line(token='private-token'), line(detail='/private/body.txt'), line(detail=[]),
                   PREFIX + '[]', PREFIX + '{}', PREFIX + '{', PREFIX + ' ' * 2049,
                   'DRAFT_RESULT=' + line()[len(PREFIX):],
                   line().replace('"v": 1', '"v": 1, "v": 1')]
        for value in invalid:
            with self.subTest(value=value[:140]):
                self.assertIsNone(parse_progress(value, 'job-1', 'xiaohongshu'))

    def test_repeated_phase_preserves_timer_and_next_phase_resets(self):
        item = {'status': 'running'}
        for stage, state, at in [('session', 'running', 10), ('session', 'running', 40),
                                 ('session', 'completed', 50), ('video', 'running', 60)]:
            self.assertTrue(apply_progress(item, parse_progress(line(stage=stage, state=state), 'job-1', 'xiaohongshu'), at))
            if stage == 'session': self.assertEqual(item['progress']['started_at'], 10)
        self.assertEqual(item['progress']['started_at'], 60)
        self.assertEqual(item['progress']['completed'], ['session'])
        self.assertEqual(item['status'], 'running')
        before = copy.deepcopy(item)
        self.assertFalse(apply_progress(item, parse_progress(line(stage='session'), 'job-1', 'xiaohongshu'), 80))
        self.assertEqual(item, before)

    def test_completed_and_skipped_are_distinct_and_cannot_reopen(self):
        item = {'status': 'running'}
        for stage, state, at in [('video', 'completed', 10), ('upload', 'completed', 20),
                                 ('cover', 'skipped', 30), ('content', 'running', 40)]:
            self.assertTrue(apply_progress(item, parse_progress(line(stage=stage, state=state), 'job-1', 'xiaohongshu'), at))
        self.assertEqual(item['progress']['completed'], ['video', 'upload'])
        self.assertEqual(item['progress']['skipped'], ['cover'])
        self.assertFalse(apply_progress(item, parse_progress(line(stage='cover'), 'job-1', 'xiaohongshu'), 50))

    def test_public_progress_filters_raw_fields_and_bad_types(self):
        item = {'status': 'running'}
        apply_progress(item, parse_progress(line(detail='等待扫码登录'), 'job-1', 'xiaohongshu'), 10)
        item['progress'].update(token='secret', path='/private', uid='owner', body='private-body')
        result = public_progress(item['progress'])
        self.assertEqual(set(result), {'stage', 'label', 'state', 'started_at', 'updated_at', 'completed', 'skipped'})
        self.assertEqual(result['label'], LABELS['session'])
        for change in ({'stage': []}, {'state': {}}, {'started_at': True}, {'updated_at': float('nan')}):
            self.assertIsNone(public_progress({**item['progress'], **change}))

    def test_legacy_and_empty_jobs_do_not_invent_steps(self):
        self.assertEqual(progress_summary(None, wb.PLATFORMS), {'available': True, 'job': None})
        job = {'id': 'job-1', 'action': 'draft', 'active': False,
               'results': [{'platform': 'bilibili', 'status': 'result_unknown',
                            'message': 'secret body /private/path uid=123'}]}
        result = progress_summary(job, wb.PLATFORMS)['job']
        self.assertIsNone(result['progress'])
        self.assertEqual(result['platform_index'], 1)
        self.assertEqual(result['status'], 'result_unknown')
        self.assertNotIn('secret', json.dumps(result))

    def test_running_then_last_started_platform_selection(self):
        job = {'id': 'job-1', 'action': 'draft', 'active': True, 'results': [
            {'platform': 'xiaohongshu', 'status': 'result_unknown', 'started_at': 10},
            {'platform': 'bilibili', 'status': 'running', 'started_at': 20},
            {'platform': 'kuaishou', 'status': 'pending'}]}
        self.assertEqual(progress_summary(job, wb.PLATFORMS)['job']['platform'], 'bilibili')
        job['results'][1]['status'] = 'failed'; job['active'] = False
        result = progress_summary(job, wb.PLATFORMS)['job']
        self.assertEqual((result['platform_index'], result['platform_count']), (2, 3))
        self.assertEqual(result['platform'], 'bilibili')


class WorkbenchProgressTests(unittest.TestCase):
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
        for value in self.patches: value.start()
        self.video = self.root / 'video.mp4'; self.video.write_bytes(b'isolated synthetic video')
        self.cover = self.root / 'cover.jpg'; self.cover.write_bytes(b'isolated synthetic cover')
        self.payload = {'video': str(self.video), 'cover': str(self.cover),
                        'title': '进度测试', 'description': '隔离测试', 'tags': ['测试']}

    def tearDown(self):
        for value in reversed(self.patches): value.stop()
        self.temp.cleanup()

    def start(self, platforms=('xiaohongshu',)):
        with patch.object(wb.threading, 'Thread'):
            return wb.start_job({'action': 'draft', 'platforms': list(platforms), 'payload': self.payload})

    def progress(self, job, item, stage='session', state='running', at=100):
        with patch.object(wb.time, 'time', return_value=at):
            return wb.worker_progress(job, item, line(job=job['id'], platform=item['platform'], stage=stage, state=state))

    def test_platform_switch_starts_an_independent_step_and_timer(self):
        job = self.start(('xiaohongshu', 'bilibili'))
        first, second = job['results']
        wb.transition(job, first, 'running')
        self.progress(job, first, 'video', 'completed', 100)
        self.progress(job, first, 'upload', 'running', 110)
        wb.transition(job, first, 'result_unknown')
        wb.transition(job, second, 'running')
        self.progress(job, second, 'session', 'running', 500)
        result = wb.progress_state()['job']
        self.assertEqual((result['platform'], result['platform_index'], result['platform_count']), ('bilibili', 2, 2))
        self.assertEqual(result['progress']['started_at'], 500)
        self.assertEqual(result['progress']['completed'], [])
        self.assertEqual(first['progress']['stage'], 'upload')
        self.assertEqual(first['progress']['state'], 'unknown')

    def test_stale_other_platform_cancelled_and_terminal_events_cannot_mutate(self):
        job = self.start(); item = job['results'][0]
        wb.transition(job, item, 'running')
        self.progress(job, item)
        before = copy.deepcopy(item)
        for value in (line(job='old-job'), line(job=job['id'], platform='bilibili'),
                      line(job=job['id'], token='not-accepted'), line(job=job['id'], state='verified')):
            self.assertFalse(wb.worker_progress(job, item, value))
            self.assertEqual(item, before)
        with patch.object(wb, 'CURRENT', copy.deepcopy(job)):
            self.assertFalse(self.progress(job, item, 'video'))
        job['cancelled'] = True
        self.assertFalse(self.progress(job, item, 'video'))
        job['cancelled'] = False
        wb.transition(job, item, 'failed')
        before = copy.deepcopy(item)
        self.assertFalse(self.progress(job, item, 'verify', 'completed'))
        self.assertEqual(item, before)

    def test_terminal_unknown_cancelled_and_failed_keep_the_last_phase(self):
        for status, progress_state in [('result_unknown', 'unknown'), ('cancelled', 'cancelled'),
                                       ('failed', 'failed'), ('saved_unverified', 'unverified')]:
            with self.subTest(status=status):
                self.payload['title'] = status
                job = self.start(); item = job['results'][0]
                wb.transition(job, item, 'running')
                self.progress(job, item, 'cover', 'running', 100)
                wb.transition(job, item, status)
                job['active'] = False
                result = wb.progress_state()['job']
                self.assertEqual(result['status'], status)
                self.assertEqual(result['progress']['stage'], 'cover')
                self.assertEqual(result['progress']['started_at'], 100)
                self.assertEqual(result['progress']['state'], progress_state)

    def test_worker_environment_is_preserved_and_verify_progress_never_proves_success(self):
        job = self.start(); captured = []
        class Process:
            def __init__(self, cmd, **kwargs):
                captured.append(kwargs['env'])
                self.stdout = iter([line(job=job['id'], stage='video', state='running'),
                                    line(job=job['id'], stage='video', state='completed'),
                                    line(job=job['id'], stage='verify', state='completed'),
                                    line(job='old-job', token='must-not-enter-logs'),
                                    'DRAFT_RESULT={"status":"platform_draft_verified"}\n'])
            def wait(self): return 0
        with patch.dict(wb.os.environ, {'SAU_PROGRESS_TEST_KEEP': 'unchanged'}), patch.object(wb.subprocess, 'Popen', Process):
            wb.worker(job, self.payload)
        self.assertEqual(captured[0]['SAU_PROGRESS_TEST_KEEP'], 'unchanged')
        self.assertEqual(captured[0]['SAU_PROGRESS_JOB_ID'], job['id'])
        self.assertEqual(captured[0]['SAU_PROGRESS_PLATFORM'], 'xiaohongshu')
        self.assertFalse(job['active'])
        self.assertEqual(job['results'][0]['status'], 'result_unknown')
        self.assertEqual(job['results'][0]['progress']['state'], 'unknown')
        self.assertEqual(job['results'][0]['progress']['completed'], ['video', 'verify'])
        stored = (wb.PRIVATE / 'workbench-state.json').read_text() + (wb.PRIVATE / 'jobs' / job['id'] / 'xiaohongshu.log').read_text()
        self.assertNotIn('must-not-enter-logs', stored)

    def test_recovery_keeps_phase_and_never_replays(self):
        job = self.start(('xiaohongshu', 'bilibili')); item = job['results'][0]
        wb.transition(job, item, 'running')
        self.progress(job, item, 'content', 'running', 100)
        wb.STORE_ROOT = None
        with patch.object(wb.subprocess, 'Popen') as process:
            wb.ensure_store()
            process.assert_not_called()
        result = wb.progress_state()['job']
        self.assertFalse(result['active'])
        self.assertEqual(result['status'], 'result_unknown')
        self.assertEqual(result['progress']['stage'], 'content')
        self.assertEqual(result['progress']['started_at'], 100)
        self.assertEqual(result['progress']['state'], 'unknown')
        self.assertEqual(wb.CURRENT['results'][1]['status'], 'cancelled')

    def test_get_endpoint_preserves_host_validation_and_is_read_only(self):
        job = self.start(); item = job['results'][0]
        item.update(status='running', message='private body /private/path', account_identity='private-owner')
        self.progress(job, item)
        server = ThreadingHTTPServer(('127.0.0.1', 0), wb.Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(wb, 'PORT', port), patch.object(wb, 'persist') as persist, patch.object(wb, 'ensure_store') as store:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/progress') as response:
                    result = json.loads(response.read())
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(result['job']['id'], job['id'])
                encoded = json.dumps(result)
                for hidden in ('private body', '/private/path', 'private-owner', str(self.video), 'expected', 'evidence', 'token'):
                    self.assertNotIn(hidden, encoded)
                self.assertEqual(set(result['job']), {'id', 'action', 'active', 'platform', 'platform_name',
                                 'platform_index', 'platform_count', 'status', 'message', 'progress'})
                request = urllib.request.Request(f'http://127.0.0.1:{port}/api/progress', headers={'Host': 'evil.test'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
                persist.assert_not_called(); store.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__': unittest.main(verbosity=2)
