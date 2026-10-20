"""Failure, recovery and identity isolation using local files and fake workers only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import draft_workbench as wb
from workbench_model import preflight_payload, build_platform_payload, validate_receipt, IDENTITY_FIELDS, COVER_FIELDS


class LocalWorkbench(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(wb, 'PRIVATE', self.root / 'private'),
                        patch.object(wb, 'STORE_ROOT', None), patch.object(wb, 'CURRENT', None),
                        patch.object(wb, 'EDITOR', {}), patch.object(wb, 'LEDGER', {}),
                        patch.object(wb, 'ACCOUNTS', {}), patch.object(wb, 'HISTORY', []),
                        patch.object(wb, 'PROCESS', None),
                        patch.object(wb, 'account_file', side_effect=lambda p: self.root / (p + '-session.json')),
                        patch.object(wb, 'embedded_account_file', side_effect=lambda p: self.root / (p + '-embedded.json'))]
        for value in self.patches: value.start()
        self.video = self.root / 'video.mp4'; self.video.write_bytes(b'local fixture video')
        self.cover = self.root / 'cover.jpg'; self.cover.write_bytes(b'local fixture cover')
        self.payload = {'video': str(self.video), 'title': '素材标题', 'description': '完整正文',
                        'tags': ['生活'], 'cover': str(self.cover)}

    def tearDown(self):
        for value in reversed(self.patches): value.stop()
        self.temp.cleanup()

    def test_new_embedded_task_uses_fresh_verified_owner_without_rewriting_history(self):
        wb.ACCOUNTS['embedded:douyin'] = {'account': {'identity': 'previous-owner'}, 'verified_at': 123}
        before = json.loads(json.dumps(wb.ACCOUNTS))
        with patch.object(wb, 'current_sessions', return_value={'douyin': {'status': 'login_verified', 'identity': 'live-owner'}}):
            context = wb.account_context('douyin', 'embedded')
        self.assertEqual(context['identity'], 'live-owner')
        self.assertEqual(context['account_key'], 'embedded:douyin:live-owner')
        self.assertEqual(wb.ACCOUNTS, before)
        for session in ({}, {'douyin': {'status': 'needs_confirmation', 'identity': 'unverified-owner'}}):
            with patch.object(wb, 'current_sessions', return_value=session):
                self.assertEqual(wb.account_context('douyin', 'embedded')['identity'], 'previous-owner')

    def start(self, platforms=('douyin',), payload=None, action='draft'):
        with patch.object(wb.threading, 'Thread') as thread:
            job = wb.start_job({'action': action, 'platforms': list(platforms), 'payload': payload or self.payload})
        return job, thread

    def receipt(self, expected, **changes):
        platform = expected['platform_id']
        policy = wb.PLATFORM_POLICIES[platform]
        return {**{k: expected[k] for k in IDENTITY_FIELDS}, 'status': policy['verified_status'],
                'verification': {k: True for k in ('upload_complete', 'fields', 'cover', 'media', 'reopened', 'account')},
                'account': {'identity': 'actual-account-' + platform, 'platform': platform, 'verified': True},
                'draft_id': 'draft-123', 'save_location': policy['location'], **changes}

    def run_worker(self, job, receipts=None, lines=(), on_spawn=None):
        seen = []
        test = self
        class Process:
            pid = 12345
            def __init__(self, cmd, **kwargs):
                self.stdout = iter(lines)
                seen.append(cmd)
                if on_spawn: on_spawn()
                if '--receipt' in cmd:
                    expected = json.loads(Path(cmd[cmd.index('--payload') + 1]).read_text())
                    result = receipts(expected) if receipts else test.receipt(expected)
                    Path(cmd[cmd.index('--receipt') + 1]).write_text(json.dumps(result))
            def wait(self): return 0
        with patch.object(wb.subprocess, 'Popen', Process): wb.worker(job, self.payload)
        return seen

    def test_crash_restores_editor_and_holds_started_upload_without_retry(self):
        wb.save_editor({'payload': self.payload, 'platforms': ['douyin', 'bilibili']})
        job, _ = self.start(('douyin', 'bilibili'))
        wb.transition(job, job['results'][0], 'running', '正在上传')
        wb.STORE_ROOT = None
        restored = wb.state()['job']
        self.assertFalse(restored['active'])
        self.assertEqual([i['status'] for i in restored['results']], ['result_unknown', 'cancelled'])
        self.assertEqual(wb.EDITOR['platforms'], ['douyin', 'bilibili'])
        repeat, thread = self.start()
        self.assertEqual(repeat['results'][0]['status'], 'result_unknown')
        thread.assert_not_called()
        wb.ACCOUNTS['external:douyin'] = {'account': {'identity': 'later-login', 'platform': 'douyin'}, 'verified_at': 123}
        repeat, thread = self.start()
        self.assertEqual(repeat['results'][0]['status'], 'result_unknown')
        thread.assert_not_called()

    def test_success_deduplicates_after_placeholder_account_becomes_real(self):
        job, _ = self.start()
        self.assertTrue(job['results'][0]['expected']['account_key'].endswith(':default'))
        self.run_worker(job)
        self.assertEqual(job['results'][0]['status'], 'local_draft_verified')
        repeat, thread = self.start()
        self.assertTrue(repeat['results'][0]['expected']['account_key'].endswith(':actual-account-douyin'))
        self.assertTrue(repeat['results'][0]['reused'])
        thread.assert_not_called()
        changed, thread = self.start(payload={**self.payload, 'description': '另一份正文'})
        self.assertEqual(changed['results'][0]['status'], 'pending')
        thread.assert_called_once()

    def test_account_risk_cannot_be_bypassed_by_changing_content(self):
        job, _ = self.start()
        self.run_worker(job, lambda expected: self.receipt(expected, status='risk_paused'))
        repeat, thread = self.start(payload={**self.payload, 'title': '改了标题'})
        self.assertEqual(repeat['results'][0]['status'], 'risk_paused')
        thread.assert_not_called()

    def test_old_control_mismatch_holds_its_material_without_freezing_account(self):
        job, _ = self.start(('bilibili',))
        expected = job['results'][0]['expected']
        wb.transition(job, job['results'][0], 'platform_restricted',
                      '当前页面有多个可能的素材或内容控件，已停止以免填错。')
        original = json.loads(json.dumps(wb.LEDGER))
        self.assertIsNotNone(wb.duplicate_record(expected, None, 'external'))
        changed = {**expected, 'content_sha256': 'different-content'}
        self.assertIsNone(wb.duplicate_record(changed, None, 'external'))
        self.assertEqual(wb.LEDGER, original)

    def test_real_platform_restriction_holds_different_materials(self):
        job, _ = self.start(('bilibili',))
        expected = job['results'][0]['expected']
        wb.transition(job, job['results'][0], 'platform_restricted', '账号权限受限')
        changed = {**expected, 'content_sha256': 'different-content'}
        self.assertIsNotNone(wb.duplicate_record(changed, None, 'external'))

    def test_adapter_mismatch_holds_only_that_material(self):
        job, _ = self.start(('bilibili',))
        expected = job['results'][0]['expected']
        wb.transition(job, job['results'][0], 'adapter_mismatch',
                      '同一用途的控件仍不唯一，需要检查页面适配；已保留现场。')
        changed = {**expected, 'content_sha256': 'different-content'}
        self.assertIsNone(wb.duplicate_record(changed, None, 'external'))
        self.assertIsNotNone(wb.duplicate_record(expected, None, 'external'))

    def test_old_nonce_or_other_platform_receipt_never_becomes_success(self):
        job, _ = self.start()
        self.run_worker(job, lambda expected: self.receipt(expected, receipt_nonce='old-job-nonce'))
        self.assertEqual(job['results'][0]['status'], 'result_unknown')
        repeat, thread = self.start()
        thread.assert_not_called()
        self.assertEqual(repeat['results'][0]['status'], 'result_unknown')
        expected = job['results'][0]['expected']
        with self.assertRaisesRegex(ValueError, 'platform_id'):
            validate_receipt(self.receipt(expected, platform_id='xiaohongshu'), expected)

    def test_worker_has_separate_bound_payloads_for_each_platform(self):
        payload = {**self.payload, 'platform_overrides': {'xiaohongshu': {'title': '小红书专用'}}}
        job, _ = self.start(('xiaohongshu', 'kuaishou'), payload)
        commands = self.run_worker(job)
        files = [Path(command[command.index('--payload') + 1]) for command in commands]
        self.assertEqual(len(set(files)), 2)
        values = [json.loads(path.read_text()) for path in files]
        self.assertEqual([v['job_id'] for v in values], [job['id']] * 2)
        self.assertEqual(values[0]['platform_fields']['title'], '小红书专用')
        self.assertEqual(values[1]['platform_fields']['caption'], '素材标题\n完整正文\n#生活')
        self.assertNotEqual(values[0]['receipt_nonce'], values[1]['receipt_nonce'])
        self.assertEqual([i['status'] for i in job['results']], ['local_draft_verified', 'platform_snapshot_verified'])

    def test_platform_payload_recheck_preserves_distinct_covers_and_content(self):
        from draft_execution import prepare_expected
        landscape, portrait, override_cover = [self.root / name for name in ('landscape.jpg', 'portrait.jpg', 'override.jpg')]
        for path in (landscape, portrait, override_cover): path.write_bytes(path.name.encode())
        platforms = list(wb.PLATFORM_POLICIES)
        payload = {**self.payload, 'cover_landscape': str(landscape), 'cover_portrait': str(portrait),
                   'short_title': '平台短标题示例', 'platform_overrides': {
                       p: {'title': '单独标题', 'description': '平台完整正文', 'cover': str(override_cover)} for p in platforms}}
        check = preflight_payload(payload, platforms)
        self.assertFalse(check['errors'])
        for platform in platforms:
            with self.subTest(platform=platform):
                expected = build_platform_payload(check, platform, 'bound-job', 'bound-nonce')
                actual_cover_roles = {key for key in COVER_FIELDS if key in expected}
                mapped_cover_roles = {key for key in COVER_FIELDS if key in check['mappings'][platform]}
                self.assertEqual(actual_cover_roles, mapped_cover_roles)
                independently_checked = prepare_expected(platform, expected)
                self.assertEqual(independently_checked['platform_fields'], expected['platform_fields'])
                for key in IDENTITY_FIELDS: self.assertEqual(independently_checked[key], expected[key])

    def test_file_presence_does_not_claim_current_login_or_login_completion(self):
        (self.root / 'douyin-session.json').write_text(json.dumps({'cookies': [{'secret': 'not-read-by-ui'}]}))
        platform = wb.state()['platforms'][0]
        self.assertTrue(platform['session_record_present'])
        self.assertEqual(platform['login_state'], 'needs_confirmation')
        self.assertIsNone(platform['last_verified_at'])
        job, _ = self.start(action='login')
        self.run_worker(job)
        self.assertEqual(job['results'][0]['status'], 'needs_reauthentication')

    def test_ending_login_wait_does_not_claim_authentication_or_remove_saved_state(self):
        job, _ = self.start(action='login')
        saved = wb.embedded_account_file('douyin')
        saved.write_text('previous-session-marker')
        # Simulate the cancelled worker returning a stale success line after cancellation.
        line = 'LOGIN_RESULT=' + json.dumps({'status': 'login_verified', 'account': {
            'verified': True, 'platform': 'douyin', 'identity': 'late-account'}})
        self.run_worker(job, lines=[line], on_spawn=lambda: wb.cancel_job({
            'expected_job_id': job['id'], 'expected_action': 'login'}))
        self.assertEqual(job['results'][0]['status'], 'cancelled')
        self.assertEqual(wb.ACCOUNTS, {})
        self.assertEqual(saved.read_text(), 'previous-session-marker')

    def test_scoped_login_cancel_cannot_cancel_a_new_job_or_a_draft(self):
        job, _ = self.start()
        with patch.object(wb.os, 'killpg') as kill:
            with self.assertRaisesRegex(ValueError, '任务已变化'):
                wb.cancel_job({'expected_job_id': 'previous-job', 'expected_action': 'login'})
            with self.assertRaisesRegex(ValueError, '任务类型已变化'):
                wb.cancel_job({'expected_job_id': job['id'], 'expected_action': 'login'})
            self.assertFalse(job['cancelled'])
            kill.assert_not_called()

    def test_changed_video_stops_before_subprocess_and_sensitive_logs_are_removed(self):
        job, _ = self.start()
        self.video.write_bytes(b'changed local fixture')
        with patch.object(wb.subprocess, 'Popen') as process: wb.worker(job, self.payload)
        process.assert_not_called()
        self.assertEqual(job['results'][0]['status'], 'failed')
        job, _ = self.start()
        self.run_worker(job, lines=['token=hidden-secret\n', 'phone 13812345678\n', 'cookies: secret-state\n'])
        stored = (wb.PRIVATE / 'workbench-state.json').read_text() + (wb.PRIVATE / 'jobs' / job['id'] / 'douyin.log').read_text()
        self.assertNotIn('hidden-secret', stored)
        self.assertNotIn('13812345678', stored)
        self.assertNotIn('secret-state', stored)
        self.assertEqual(os.stat(wb.PRIVATE / 'workbench-state.json').st_mode & 0o777, 0o600)

    def test_receipt_requires_all_proofs_and_account_not_legacy_toast(self):
        check = preflight_payload(self.payload, ['douyin'])
        expected = build_platform_payload(check, 'douyin', 'job-1', 'nonce-1')
        for changes in ({'status': 'draft_saved'}, {'verification': {'fields': True}},
                        {'account': {'identity': 'some-user', 'platform': 'douyin'}},
                        {'partial': ['cover']}, {'job_id': 'older-job'}, {'save_location': 'platform'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_receipt(self.receipt(expected, **changes), expected)
        expected['expected_account_identity'] = 'intended-owner'
        with self.assertRaisesRegex(ValueError, '账号与本次任务账号'):
            validate_receipt(self.receipt(expected), expected)

    def test_xhs_over_limit_is_explicit_and_does_not_change_input(self):
        title = '标题' * 11
        check = preflight_payload({**self.payload, 'title': title}, ['xiaohongshu'])
        self.assertEqual(check['mappings']['xiaohongshu']['title'], title)
        self.assertTrue(any(e['field'] == 'title' and '20' in e['message'] for e in check['errors']))
        rewritten = preflight_payload({**self.payload, 'title': title,
            'platform_overrides': {'xiaohongshu': {'title': '单独改写标题'}}}, ['xiaohongshu', 'bilibili'])
        self.assertFalse(rewritten['errors'])
        self.assertEqual(rewritten['mappings']['bilibili']['title'], title)


if __name__ == '__main__': unittest.main(verbosity=2)
