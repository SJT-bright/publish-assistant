"""Seven-platform registration and pending-adapter boundaries; isolated local data."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import draft_execution as execution
import draft_workbench as wb
from platform_limits import TEXT_LIMITS, check_fields, text_fields
from workbench_model import (
    IDENTITY_FIELDS, PLATFORM_POLICIES, preflight_payload, validate_receipt,
)


class PlatformExpansionTests(unittest.TestCase):
    def test_registries_agree_and_original_platforms_remain_available(self):
        expected = {'douyin', 'xiaohongshu', 'tencent', 'bilibili', 'kuaishou', 'toutiao', 'xigua'}
        self.assertEqual(set(execution.PLATFORMS), expected)
        self.assertEqual(set(execution.URLS), expected)
        self.assertEqual(set(PLATFORM_POLICIES), expected)
        self.assertEqual(set(TEXT_LIMITS), expected)
        for platform in expected - execution.PORTAL_ONLY:
            self.assertTrue(PLATFORM_POLICIES[platform].get('automation_ready', True))

    def test_pending_start_rejects_whole_batch_before_files_history_or_worker(self):
        current = {'id': 'existing-user-task', 'active': False, 'results': []}
        history = [{'id': 'earlier'}]
        for action, platforms in [('login', ['toutiao']), ('login', ['xigua']),
                                  ('draft', ['toutiao']), ('draft', ['xigua']),
                                  ('draft', ['douyin', 'toutiao'])]:
            with self.subTest(action=action, platforms=platforms):
                with patch.object(wb, 'CURRENT', copy.deepcopy(current)), \
                     patch.object(wb, 'HISTORY', copy.deepcopy(history)), \
                     patch.object(wb, 'ensure_store') as store, \
                     patch.object(wb, 'preflight') as preflight, \
                     patch.object(wb.threading, 'Thread') as thread:
                    with self.assertRaisesRegex(ValueError, '打开平台'):
                        wb.start_job({'action': action, 'platforms': platforms,
                                      'payload': {'video': '/does-not-exist.mp4'}})
                    store.assert_not_called()
                    preflight.assert_not_called()
                    thread.assert_not_called()
                    self.assertEqual(wb.CURRENT, current)
                    self.assertEqual(wb.HISTORY, history)

    def test_public_state_exposes_readiness_without_certifying_login(self):
        with patch.object(wb, 'ensure_store'), patch.object(wb, 'CURRENT', None), \
             patch.object(wb, 'HISTORY', []), patch.object(wb, 'EDITOR', {}), \
             patch.object(wb, 'current_sessions', return_value={}), \
             patch.object(wb, 'account_context', return_value={
                 'verified_at': None, 'session_record_present': False}):
            state = wb.state(embedded=True)
        cards = {p['id']: p for p in state['platforms']}
        for platform in ('toutiao', 'xigua'):
            self.assertFalse(cards[platform]['automation_ready'])
            self.assertTrue(cards[platform]['readiness_message'])
            self.assertEqual(cards[platform]['login_state'], 'needs_confirmation')

    def test_new_material_overrides_are_independent_and_preserve_common_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / 'video.mp4'; video.write_bytes(b'isolated media')
            horizontal = root / 'horizontal.jpg'; horizontal.write_bytes(b'isolated horizontal')
            portrait = root / 'portrait.png'; portrait.write_bytes(b'isolated portrait')
            payload = {'video': str(video), 'title': '共同标题', 'description': '共同文案',
                       'tags': ['共同话题'], 'cover_landscape': str(horizontal),
                       'cover_portrait': str(portrait), 'platform_overrides': {
                           'toutiao': {'title': '头条标题', 'description': '头条文案', 'tags': ['#头条', '头条']},
                           'xigua': {'title': '西瓜标题', 'tags': ['#西瓜']}}}
            original = copy.deepcopy(payload)
            check = preflight_payload(payload, ['toutiao', 'xigua'])
        self.assertFalse(check['errors'])
        self.assertEqual(payload, original)
        self.assertEqual(check['mappings']['toutiao']['title'], '头条标题')
        self.assertEqual(check['mappings']['toutiao']['description'], '头条文案')
        self.assertEqual(check['mappings']['toutiao']['tags'], ['头条'])
        self.assertEqual(check['mappings']['xigua']['description'], '共同文案')
        self.assertEqual(check['mappings']['xigua']['tags'], ['西瓜'])
        for fields in check['mappings'].values():
            self.assertEqual(fields['cover'], str(horizontal.resolve()))
            self.assertNotIn('cover_portrait', fields)
            self.assertNotIn('short_title', fields)
            self.assertNotIn('caption', fields)
        self.assertEqual({p['platform'] for p in check['warnings'] if p['field'] == 'platform'},
                         {'toutiao', 'xigua'})

    def test_unknown_video_limits_are_explicit_and_never_silently_truncate(self):
        for platform in ('toutiao', 'xigua'):
            fields = text_fields(platform, {'title': '测' * 100, 'description': '文' * 3000,
                                            'tags': ['话题' + str(i) for i in range(20)]})
            checked = check_fields(platform, fields)
            self.assertFalse(checked['errors'])
            for metric in checked['metrics'].values():
                self.assertIsNone(metric['max'])
                self.assertIn('待现场确认', metric['text'])
            self.assertEqual(fields['description'], '文' * 3000)

    def test_pending_adapter_cannot_accept_a_fabricated_success_receipt(self):
        for platform in ('toutiao', 'xigua'):
            expected = {key: 'isolated:' + key for key in IDENTITY_FIELDS}
            expected['platform_id'] = platform
            receipt = {**expected, 'status': 'platform_draft_verified'}
            with self.assertRaisesRegex(ValueError, '尚未核验'):
                validate_receipt(receipt, expected)


class PendingExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_platform_never_falls_through_to_another_uploader(self):
        for platform in ('toutiao', 'xigua'):
            with patch.object(execution, 'browser_session') as login_session, \
                 patch.object(execution, 'prepared_draft_session') as draft_session:
                with self.assertRaises(execution.DraftInterrupted):
                    await execution.login(platform, embedded=True)
                with self.assertRaises(execution.DraftInterrupted):
                    await execution.stage(platform, {}, Path('/not-written.json'), embedded=True)
                login_session.assert_not_called()
                draft_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
