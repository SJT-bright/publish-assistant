"""Sealed-upload recovery; isolated files and mocked workers, no real platform."""
import copy
import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import draft_workbench as wb
from workbench_model import IDENTITY_FIELDS

REAL_THREAD = threading.Thread


class UploadedResume(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(wb, 'PRIVATE', self.root / 'private'), patch.object(wb, 'STORE_ROOT', None),
            patch.object(wb, 'CURRENT', None), patch.object(wb, 'EDITOR', {}), patch.object(wb, 'LEDGER', {}),
            patch.object(wb, 'ACCOUNTS', {}), patch.object(wb, 'HISTORY', []), patch.object(wb, 'PROCESS', None),
            patch.object(wb, 'ASSETS', set()), patch.object(wb, 'RESUME_ASSET_CACHE', {}),
            patch.object(wb, 'account_file', side_effect=lambda p: self.root / (p + '-session.json')),
            patch.object(wb, 'embedded_account_file', side_effect=lambda p: self.root / (p + '-embedded.json'))]
        for value in self.patches: value.start()
        self.video = self.root / 'fixture.mp4'; self.video.write_bytes(b'fixture-video-data')
        self.cover = self.root / 'fixture.jpg'; self.cover.write_bytes(b'fixture-cover-data')
        self.payload = {'video': str(self.video), 'title': '原素材标题', 'description': '原完整文案', 'tags': ['生活'], 'cover': str(self.cover)}
        self.owner = {'verified': True, 'platform': 'douyin', 'identity': 'fixture-owner'}
        wb.ensure_store()
        wb.ACCOUNTS['external:douyin'] = {'account': self.owner, 'verified_at': time.time()}
        with patch.object(wb.threading, 'Thread'):
            self.job = wb.start_job({'action': 'draft', 'browser': 'external', 'platforms': ['douyin'], 'payload': self.payload})
        self.item = self.job['results'][0]
        wb.transition(self.job, self.item, 'result_unknown', '保留原失败记录', evidence={'account': self.owner})
        self.job['active'] = False
        self.job['logs'].append('原失败日志')
        wb.persist()
        self.checkpoint_path = wb.upload_checkpoint_path(self.job, self.item)
        _, self.key = wb.checkpoint_key(create=True)
        now = time.time()
        expected = self.item['expected']
        assets = {}
        for role in ('video', *expected['cover_sha256']):
            source = Path(expected[role])
            assets[role] = {'name': source.name, 'size': source.stat().st_size,
                'sha256': wb.hash_asset(source), 'kind': 'video' if role == 'video' else 'cover'}
        self.proof = {'phase': 'uploaded_not_saved', 'identity': {key: expected[key] for key in IDENTITY_FIELDS},
            'account': self.owner, 'assets': assets, 'baseline_hashes': {},
            'upload_links': [{'kind': 'video', 'sha256': expected['video_sha256'], 'ids': ['fixture-resource-id'], 'complete': True}],
            'origin_at': now - 120, 'exported_at': now - 20, 'expires_at': now + 3600}
        self.write_checkpoint(self.proof)

    def tearDown(self):
        for value in reversed(self.patches): value.stop()
        self.temp.cleanup()

    def write_checkpoint(self, payload):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        checkpoint = {'checkpoint_version': 1, 'payload': payload, 'mac': hmac.new(self.key, encoded, hashlib.sha256).hexdigest()}
        wb.atomic_json(self.checkpoint_path, checkpoint)
        return checkpoint

    def request(self, **changes):
        return {'expected_job_id': self.job['id'], 'platform': 'douyin', 'browser': 'external',
                'payload': copy.deepcopy(self.payload), **changes}

    def available(self):
        return wb.state()['job']['results'][0]['resumable']

    def resume(self, data=None):
        with patch.object(wb.threading, 'Thread') as worker_thread:
            result = wb.resume_job(data or self.request())
        return result, worker_thread

    def fake_worker(self, lines=('恢复轮日志\n',)):
        calls = []
        class Process:
            pid = 45678
            def __init__(self, command, **kwargs):
                calls.append((command, kwargs))
                self.stdout = iter(lines)
            def wait(self): return 0
        with patch.object(wb.subprocess, 'Popen', Process):
            wb.worker(self.job, self.payload)
        return calls

    def test_trusted_checkpoint_sets_only_a_public_boolean_without_private_ids_or_key_fields(self):
        self.assertTrue(self.available())
        public = wb.state()['job']
        row = public['results'][0]
        self.assertNotIn('expected', row)
        self.assertNotIn('evidence', row)
        text = json.dumps(public)
        self.assertNotIn('fixture-resource-id', text)
        self.assertNotIn('evidence-checkpoint.key', text)
        self.assertNotIn('upload-checkpoint.json', text)
        self.assertNotIn(self.key.hex(), text)

    def show_reused(self):
        with patch.object(wb.threading, 'Thread') as worker:
            shown = wb.start_job({'action': 'draft', 'browser': 'external', 'platforms': ['douyin'], 'payload': self.payload})
        worker.assert_not_called()
        return shown

    def reused_request(self, shown, **changes):
        return self.request(expected_job_id=shown['id'], previous_job_id=self.job['id'], **changes)

    def test_reused_selection_resumes_exact_original_job_nonce_and_attempt_history(self):
        expected = copy.deepcopy(self.item['expected'])
        self.item['progress'] = {'stage': 'cover', 'label': '设置封面', 'state': 'unknown',
                                'started_at': 100, 'updated_at': 110, 'completed': ['video', 'upload', 'content'], 'skipped': []}
        wb.persist()
        shown = self.show_reused()
        row = wb.state()['job']['results'][0]
        self.assertTrue(row['resumable'])
        self.assertEqual(row['resume_previous_job_id'], self.job['id'])
        self.assertEqual(row['progress'], self.item['progress'])
        # A second ordinary click still only references the same original.
        shown = self.show_reused()
        self.assertEqual(shown['results'][0]['previous_job_id'], self.job['id'])
        old_ledger = copy.deepcopy(wb.LEDGER)
        resumed, worker = self.resume(self.reused_request(shown))
        self.assertIs(resumed, self.job); self.assertIs(wb.CURRENT, self.job)
        self.assertEqual(self.item['expected'], expected)
        self.assertEqual(self.item['execution_attempts'][0]['status'], 'result_unknown')
        self.assertEqual(self.item['execution_attempts'][0]['progress']['completed'], ['video', 'upload', 'content'])
        self.assertEqual(self.item['progress']['completed'], [])
        self.assertEqual(self.item['resume_binding']['requested_from_job_id'], shown['id'])
        self.assertIn(shown, wb.HISTORY)
        self.assertNotIn(self.job, wb.HISTORY)
        self.assertEqual(wb.LEDGER, old_ledger)
        worker.assert_called_once()
        calls = self.fake_worker()
        self.assertIn('--resume-checkpoint', calls[0][0])
        self.assertEqual(calls[0][0][calls[0][0].index('--resume-checkpoint') + 1], str(self.checkpoint_path))

    def test_reused_resume_requires_both_current_selection_and_exact_source_ids(self):
        shown = self.show_reused()
        request = self.reused_request(shown)
        for changed in ({'previous_job_id': None}, {'previous_job_id': 'wrong-source'}, {'expected_job_id': self.job['id']}):
            with self.subTest(changed=changed):
                before = copy.deepcopy(wb.CURRENT)
                with patch.object(wb.threading, 'Thread') as worker, self.assertRaises(ValueError):
                    wb.resume_job({**request, **changed})
                worker.assert_not_called(); self.assertEqual(wb.CURRENT, before)
        self.resume(request)
        with patch.object(wb.threading, 'Thread') as worker, self.assertRaises(ValueError):
            wb.resume_job(request)
        worker.assert_not_called()

    def test_reused_source_outside_retained_history_can_only_load_exact_private_execution(self):
        shown = self.show_reused()
        wb.HISTORY = []
        self.assertTrue(self.available())
        resumed, worker = self.resume(self.reused_request(shown))
        self.assertEqual(resumed['id'], self.job['id'])
        self.assertEqual(resumed['results'][0]['expected'], self.item['expected'])
        worker.assert_called_once()

    def test_reused_resume_preserves_every_save_risk_owner_and_checkpoint_guard(self):
        shown = self.show_reused()
        marker = self.checkpoint_path.with_suffix('.save-started.json')
        wb.atomic_json(marker, {'started': True})
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume(self.reused_request(shown))
        marker.unlink()
        original = copy.deepcopy(self.item)
        for extra in ({'progress': {'stage': 'save'}}, {'evidence': {'draft_id': 'already-saved'}},
                      {'execution_attempts': [{'status': 'risk_paused'}]}, {'resume_count': 3}):
            with self.subTest(extra=extra):
                self.item.update(extra)
                self.assertFalse(self.available())
                with self.assertRaises(ValueError): self.resume(self.reused_request(shown))
                self.item.clear(); self.item.update(copy.deepcopy(original))
        wb.ACCOUNTS['external:douyin']['account'] = {**self.owner, 'identity': 'different-owner'}
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume(self.reused_request(shown))

    def test_reused_resume_source_change_during_preflight_refuses_without_switching_current(self):
        shown = self.show_reused()
        real_preflight = wb.preflight
        def change(data):
            check = real_preflight(data)
            self.job['logs'].append('concurrent source change')
            return check
        with patch.object(wb, 'preflight', side_effect=change), patch.object(wb.threading, 'Thread') as worker:
            with self.assertRaises(ValueError): wb.resume_job(self.reused_request(shown))
        worker.assert_not_called(); self.assertIs(wb.CURRENT, shown)
        self.assertFalse(self.job['active']); self.assertNotIn('resume_count', self.item)

    def test_two_reused_resume_requests_can_switch_current_and_start_only_once(self):
        shown = self.show_reused()
        data = self.reused_request(shown)
        ready, outcomes = threading.Event(), []
        def submit():
            ready.wait()
            try: wb.resume_job(copy.deepcopy(data)); outcomes.append('started')
            except ValueError: outcomes.append('refused')
        first, second = REAL_THREAD(target=submit), REAL_THREAD(target=submit)
        with patch.object(wb.threading, 'Thread') as worker:
            first.start(); second.start(); ready.set(); first.join(5); second.join(5)
        self.assertEqual(sorted(outcomes), ['refused', 'started'])
        self.assertIs(wb.CURRENT, self.job); self.assertEqual(self.item['resume_count'], 1)
        worker.assert_called_once()

    def test_private_source_file_must_not_be_public_symlink_or_mismatched_execution(self):
        shown = self.show_reused()
        wb.HISTORY = []
        source_file = wb.PRIVATE / 'jobs' / self.job['id'] / '任务结果.json'
        os.chmod(source_file, 0o644)
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume(self.reused_request(shown))
        os.chmod(source_file, 0o600)
        source = json.loads(source_file.read_text())
        source['results'][0]['expected']['receipt_nonce'] = 'different-nonce'
        wb.atomic_json(source_file, source)
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume(self.reused_request(shown))
        wb.atomic_json(source_file, self.job)
        saved = source_file.with_name('saved-source.json'); source_file.rename(saved)
        source_file.symlink_to(saved)
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume(self.reused_request(shown))

    def test_no_checkpoint_or_no_key_keeps_old_task_nonresumable(self):
        self.checkpoint_path.unlink()
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume()
        self.write_checkpoint(self.proof)
        (wb.PRIVATE / 'evidence-checkpoint.key').unlink()
        self.assertFalse(self.available())

    def test_forged_signature_and_metadata_never_allow_a_worker(self):
        checkpoint = json.loads(self.checkpoint_path.read_text())
        checkpoint['payload']['upload_links'][0]['ids'] = ['forged-resource']
        wb.atomic_json(self.checkpoint_path, checkpoint)
        self.assertFalse(self.available())
        with patch.object(wb.threading, 'Thread') as worker, self.assertRaises(ValueError):
            wb.resume_job(self.request())
        worker.assert_not_called()

    def test_signed_wrong_job_nonce_owner_or_incomplete_upload_still_rejected(self):
        variants = []
        for field, value in (('job_id', 'other-job'), ('receipt_nonce', 'other-nonce')):
            proof = copy.deepcopy(self.proof); proof['identity'][field] = value; variants.append(proof)
        proof = copy.deepcopy(self.proof); proof['account']['identity'] = 'other-owner'; variants.append(proof)
        proof = copy.deepcopy(self.proof); proof['upload_links'] = []; variants.append(proof)
        proof = copy.deepcopy(self.proof); proof['upload_links'][0]['complete'] = False; variants.append(proof)
        for proof in variants:
            with self.subTest(proof=proof['identity']['job_id']):
                self.write_checkpoint(proof); self.assertFalse(self.available())
                with self.assertRaises(ValueError): self.resume()

    def test_expired_checkpoint_is_not_a_recovery_permission(self):
        proof = copy.deepcopy(self.proof)
        now = time.time(); proof.update(origin_at=now-1000, exported_at=now-500, expires_at=now-1)
        self.write_checkpoint(proof); self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume()

    def test_changed_owner_or_same_size_video_change_invalidates_cached_asset_proof(self):
        self.assertTrue(self.available())
        self.video.write_bytes(b'x' * self.video.stat().st_size)
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume()
        self.video.write_bytes(b'fixture-video-data')
        wb.ACCOUNTS['external:douyin']['account'] = {**self.owner, 'identity': 'other-owner'}
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume()

    def test_changed_cover_or_current_payload_cannot_continue_old_material(self):
        self.cover.write_bytes(b'x' * self.cover.stat().st_size)
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume()
        self.cover.write_bytes(b'fixture-cover-data')
        with self.assertRaises(ValueError): self.resume(self.request(payload={**self.payload, 'description': '更改的文案'}))
        alternate = self.root / 'other.mp4'; alternate.write_bytes(self.video.read_bytes())
        with self.assertRaises(ValueError): self.resume(self.request(payload={**self.payload, 'video': str(alternate)}))

    def test_active_current_wrong_job_browser_or_platform_is_refused(self):
        for request in (self.request(expected_job_id='stale-job'), self.request(browser='embedded'), self.request(platform='bilibili')):
            with self.subTest(request=request['platform']), self.assertRaises(ValueError): self.resume(request)
        self.job['active'] = True
        self.assertFalse(self.available())
        with self.assertRaises(ValueError): self.resume()

    def test_risk_restriction_saved_and_other_terminal_outcomes_are_not_eligible(self):
        for status in ('risk_paused', 'platform_restricted', 'saved_unverified', 'local_draft_verified', 'failed', 'cancelled', 'account_mismatch'):
            self.item['status'] = status
            self.assertFalse(self.available(), status)
            with self.assertRaises(ValueError): self.resume()

    def test_older_account_risk_and_saved_material_holds_are_not_masked_by_latest_unknown(self):
        expected = self.item['expected']
        old = copy.deepcopy(wb.LEDGER[expected['operation_key']])
        for status in ('risk_paused', 'platform_restricted', 'local_draft_verified', 'saved_unverified'):
            hold = {**old, 'status': status, 'message': '真实权限受限', 'job_id': 'older-job'}
            if status == 'risk_paused': hold['content_sha256'] = 'different-material'
            wb.LEDGER['older-hold'] = hold
            self.assertFalse(self.available(), status)
            with self.assertRaises(ValueError): self.resume()

    def test_legacy_control_mismatch_does_not_block_a_new_sealed_upload(self):
        record = copy.deepcopy(wb.LEDGER[self.item['expected']['operation_key']])
        record.update(status='platform_restricted', job_id='older-control-failure',
                      content_sha256='different-material',
                      message='当前页面有多个可能的素材或内容控件，已停止以免填错。')
        wb.LEDGER['older-control-failure'] = record
        before = copy.deepcopy(record)
        self.assertTrue(self.available())
        resumed, worker = self.resume()
        self.assertIs(resumed, self.job)
        worker.assert_called_once()
        self.assertEqual(wb.LEDGER['older-control-failure'], before)

    def test_legacy_control_phrase_never_removes_real_risk_or_save_holds(self):
        record = copy.deepcopy(wb.LEDGER[self.item['expected']['operation_key']])
        for status in ('risk_paused', 'saved_unverified', 'local_draft_verified'):
            wb.LEDGER['older-control-failure'] = {**record, 'status': status, 'job_id': 'older-job',
                'message': '当前页面有多个可能的素材或内容控件'}
            self.assertFalse(self.available(), status)
            with self.assertRaises(ValueError): self.resume()

    def test_save_marker_progress_or_saved_record_blocks_resume_after_a_later_save(self):
        marker = self.checkpoint_path.with_suffix('.save-started.json')
        wb.atomic_json(marker, {'started': True})
        self.assertFalse(self.available())
        marker.unlink()
        for extra in ({'progress': {'stage': 'save'}}, {'progress': {'stage': 'content', 'completed': ['save']}},
                      {'evidence': {'draft_id': 'already-saved'}}, {'evidence': {'saved_records': 1}}):
            original = copy.deepcopy(self.item)
            self.item.update(extra)
            self.assertFalse(self.available(), extra)
            with self.assertRaises(ValueError): self.resume()
            self.item.clear(); self.item.update(original)

    def test_resume_preserves_job_nonce_failed_audit_and_waits_for_real_session_progress(self):
        expected = copy.deepcopy(self.item['expected']); original_status = self.item['status']
        job, worker = self.resume()
        self.assertIs(job, self.job); self.assertIs(wb.CURRENT, self.job)
        self.assertEqual(self.item['expected'], expected)
        self.assertEqual(self.item['expected']['receipt_nonce'], expected['receipt_nonce'])
        self.assertEqual(self.item['execution_attempts'][0]['status'], original_status)
        self.assertIn('原失败日志', self.item['execution_attempts'][0]['logs_snapshot'])
        self.assertEqual(self.item['progress']['stage'], 'session')
        self.assertEqual(self.item['progress']['state'], 'running')
        self.assertEqual(self.item['progress']['completed'], [])
        self.assertEqual(wb.HISTORY, [])
        worker.assert_called_once(); worker.return_value.start.assert_called_once()
        self.fake_worker()
        self.assertTrue(self.available())
        row = wb.state()['job']['results'][0]
        self.assertNotIn('execution_attempts', row); self.assertNotIn('resume_binding', row)

    def test_resume_worker_uses_trusted_checkpoint_key_file_and_separate_artifacts(self):
        directory = self.checkpoint_path.parent
        original_log = directory / 'douyin.log'; original_log.write_text('original failure log')
        original_receipt = directory / 'douyin-receipt.json'; original_receipt.write_text('original failure receipt')
        self.resume(); calls = self.fake_worker()
        self.assertEqual(len(calls), 1)
        command, options = calls[0]
        self.assertEqual(command[command.index('--resume-checkpoint') + 1], str(self.checkpoint_path))
        self.assertEqual(command[command.index('--checkpoint-key-file') + 1], str(wb.PRIVATE / 'evidence-checkpoint.key'))
        output = Path(command[command.index('--checkpoint-output') + 1])
        self.assertEqual(output.parent, directory / 'attempts' / 'douyin-resume-1')
        expected = json.loads(Path(command[command.index('--payload') + 1]).read_text())
        self.assertEqual(expected['receipt_nonce'], self.item['expected']['receipt_nonce'])
        self.assertNotIn(self.key.hex(), ' '.join(command))
        self.assertEqual(original_log.read_text(), 'original failure log')
        self.assertEqual(original_receipt.read_text(), 'original failure receipt')
        self.assertIn('恢复轮日志', (output.parent / 'douyin.log').read_text())
        record = wb.LEDGER[self.item['expected']['operation_key']]
        self.assertEqual(record['execution_attempt'], 1)
        self.assertEqual(record['attempts'][0]['status'], 'result_unknown')

    def test_normal_worker_also_writes_checkpoint_parameters_without_secret_values(self):
        self.item.update(status='pending'); self.job['active'] = True
        calls = self.fake_worker()
        command = calls[0][0]
        self.assertNotIn('--resume-checkpoint', command)
        self.assertEqual(command[command.index('--checkpoint-output') + 1], str(self.checkpoint_path))
        self.assertNotIn(self.key.hex(), ' '.join(command))
        self.assertEqual(os.stat(wb.PRIVATE / 'evidence-checkpoint.key').st_mode & 0o777, 0o600)
        self.assertEqual(len((wb.PRIVATE / 'evidence-checkpoint.key').read_bytes()), 32)

    def test_checkpoint_change_after_authorization_stops_before_subprocess(self):
        self.resume()
        proof = copy.deepcopy(self.proof); proof['exported_at'] += 1; self.write_checkpoint(proof)
        with patch.object(wb.subprocess, 'Popen') as process:
            wb.worker(self.job, self.payload)
        process.assert_not_called()
        self.assertFalse(self.job['active'])

    def test_current_replaced_or_mutated_during_preflight_is_not_authorized(self):
        real_preflight = wb.preflight
        def change(data):
            result = real_preflight(data)
            self.job['logs'].append('concurrent change')
            return result
        with patch.object(wb, 'preflight', side_effect=change), patch.object(wb.threading, 'Thread') as worker:
            with self.assertRaises(ValueError): wb.resume_job(self.request())
        worker.assert_not_called(); self.assertFalse(self.job['active'])
        def replace(data):
            result = real_preflight(data); wb.CURRENT = copy.deepcopy(self.job); return result
        with patch.object(wb, 'preflight', side_effect=replace), self.assertRaises(ValueError): self.resume()

    def test_two_parallel_requests_start_only_one_resume_worker(self):
        ready = threading.Event(); outcomes = []
        def request():
            ready.wait()
            try: wb.resume_job(self.request()); outcomes.append('started')
            except ValueError: outcomes.append('refused')
        first = REAL_THREAD(target=request); second = REAL_THREAD(target=request)
        with patch.object(wb.threading, 'Thread') as worker:
            first.start(); second.start(); ready.set(); first.join(5); second.join(5)
        self.assertEqual(sorted(outcomes), ['refused', 'started'])
        worker.assert_called_once(); self.assertEqual(self.item['resume_count'], 1)

    def test_restart_does_not_resume_automatically_but_allows_an_explicit_presave_continuation(self):
        self.resume()
        wb.transition(self.job, self.item, 'running')
        wb.STORE_ROOT = None
        with patch.object(wb.threading, 'Thread') as worker, patch.object(wb.subprocess, 'Popen') as process:
            wb.ensure_store()
        worker.assert_not_called(); process.assert_not_called()
        self.assertFalse(wb.CURRENT['active'])
        self.assertEqual(wb.CURRENT['results'][0]['resume_count'], 1)
        self.assertEqual(wb.CURRENT['results'][0]['status'], 'result_unknown')
        self.assertTrue(wb.state()['job']['results'][0]['resumable'])
        with patch.object(wb.threading, 'Thread') as worker:
            wb.resume_job(self.request())
        worker.assert_called_once()
        self.assertEqual(wb.CURRENT['results'][0]['resume_count'], 2)

    def test_symlink_or_public_permissions_cannot_be_used_as_a_trusted_checkpoint(self):
        os.chmod(self.checkpoint_path, 0o644); self.assertFalse(self.available())
        os.chmod(self.checkpoint_path, 0o600)
        saved = self.checkpoint_path.with_name('saved.json'); self.checkpoint_path.rename(saved)
        self.checkpoint_path.symlink_to(saved); self.assertFalse(self.available())

    def test_resume_route_reuses_host_token_origin_protection_and_returns_filtered_job(self):
        body = json.dumps(self.request()).encode()
        base = {'Host': f'127.0.0.1:{wb.PORT}', 'X-Workbench-Token': wb.TOKEN,
                'Origin': wb.URL, 'Content-Type': 'application/json', 'Content-Length': str(len(body))}
        for changed in ({'Host': 'attacker.example'}, {'X-Workbench-Token': 'wrong'}, {'Origin': 'https://attacker.example'}):
            handler = object.__new__(wb.Handler); handler.path = '/api/resume'; handler.headers = {**base, **changed}
            handler.rfile = io.BytesIO(body); handler.reply = Mock()
            with patch.object(wb.threading, 'Thread') as worker: handler.do_POST()
            worker.assert_not_called(); self.assertEqual(handler.reply.call_args.args[1], 403)
        handler = object.__new__(wb.Handler); handler.path = '/api/resume'; handler.headers = base
        handler.rfile = io.BytesIO(body); handler.reply = Mock()
        with patch.object(wb.threading, 'Thread') as worker: handler.do_POST()
        worker.assert_called_once()
        response = handler.reply.call_args.args[0]['result']
        self.assertNotIn('resume_binding', response['results'][0])
        self.assertNotIn('fixture-resource-id', json.dumps(response))


if __name__ == '__main__': unittest.main(verbosity=2)
