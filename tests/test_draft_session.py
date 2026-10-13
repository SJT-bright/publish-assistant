"""Authentication/risk checks against isolated real browser pages, never accounts."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.async_api import async_playwright
from uploader.draft_session import DraftInterrupted, inspect_session, verify_session, run_with_session_watch, _owner_uid
from material_probe import media_info


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def test_public_controls_do_not_prove_login(self):
        await self.page.set_content('<input type="file" accept="video/mp4"><span>内容管理 数据中心</span>')
        self.assertFalse((await inspect_session('douyin', self.page))['verified'])

    async def test_stable_owner_is_required_and_identity_is_platform_specific(self):
        await self.page.set_content('<span data-account-id="123">本人</span><input type="file" accept="video/mp4">')
        first = await verify_session('douyin', self.page, timeout=1, settle=.1)
        second = await inspect_session('kuaishou', self.page)
        self.assertNotEqual(first['identity'], second['identity'])

    async def test_delayed_login_overlay_invalidates_early_positive(self):
        await self.page.set_content('''<span data-account-id="123">本人</span><input type="file" accept="video/mp4">
            <script>setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<div class="login-box">扫码</div>'),100)</script>''')
        with self.assertRaises(DraftInterrupted) as result:
            await verify_session('douyin', self.page, timeout=1, settle=.5)
        self.assertEqual(result.exception.status, 'needs_reauthentication')

    async def test_risk_cancels_upload_and_does_not_repeat_it(self):
        await self.page.set_content('<span data-account-id="123">本人</span><input type="file" accept="video/mp4">')
        account = await inspect_session('douyin', self.page)
        starts = []
        cancelled = []
        async def upload():
            starts.append(1)
            try:
                await self.page.evaluate("setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<div>疑似AI/自动化工具发布笔记</div>'),100)")
                await asyncio.sleep(5)
            finally:
                cancelled.append(1)
        with self.assertRaises(DraftInterrupted) as result:
            await run_with_session_watch('douyin', self.page, account, upload(), timeout=2)
        self.assertEqual(result.exception.status, 'risk_paused')
        self.assertEqual(starts, [1])
        self.assertEqual(cancelled, [1])

    async def test_conflicting_account_ids_cannot_authenticate(self):
        await self.page.set_content('<span data-uid="1">A</span><span data-uid="2">B</span><input type="file" accept="video/mp4">')
        self.assertFalse((await inspect_session('douyin', self.page))['verified'])

    async def test_editor_content_and_hidden_text_are_not_risk_alerts(self):
        await self.page.set_content('''<span data-account-id="123">本人</span><input type="file" accept="video/mp4">
            <textarea>账号异常与验证码</textarea><div contenteditable="true">操作频繁，账号受限</div>
            <div style="display:none">疑似AI/自动化工具发布笔记</div>''')
        self.assertTrue((await inspect_session('douyin', self.page))['verified'])


class LocalBoundaryTests(unittest.TestCase):
    def test_owner_parser_does_not_pick_author_lists(self):
        self.assertIsNone(_owner_uid({'data': {'authors': [{'uid': 'other'}]}}))
        self.assertEqual(_owner_uid({'data': {'finderUser': {'username': 'own-id'}}}), 'own-id')
        self.assertIsNone(_owner_uid({'data': {'user_info': {'user_id': 'post-author'}, 'authors': [{'uid': 'other'}], 'user': {'uid': 'second-author'}}}))
        self.assertEqual(_owner_uid({'result': 1, 'data': {'userId': 'self-owner', 'authors': [{'userId': 'other'}]}}), 'self-owner')

    def test_explicit_logout_is_not_a_missing_flag_or_a_guest_id(self):
        self.assertIsNone(_owner_uid({'code': 0, 'data': {'isLogin': False, 'mid': 8412}}))
        self.assertEqual(_owner_uid({'code': 0, 'data': {'mid': 8412}}), '8412')
        self.assertIsNone(_owner_uid({'result': 1, 'data': {'logined': False, 'userId': 'ks-owner'}}))
        self.assertEqual(_owner_uid({'result': 1, 'data': {'userId': 'ks-owner'}}), 'ks-owner')
        for guest in ('-1', 'null', 'undefined', '0', False, True):
            self.assertIsNone(_owner_uid({'result': 1, 'userId': guest}))
        self.assertIsNone(_owner_uid({'errCode': 0, 'data': {'user': {'uid': 'wx-operator'}}}, platform='tencent'))
        self.assertEqual(_owner_uid({'errCode': 0, 'data': {
            'user': {'uid': 'wx-operator'}, 'finderUser': {'username': 'finder-owner'}}}, platform='tencent'), 'finder-owner')

    def test_verified_marker_survives_reopen_and_logout_is_not_a_current_login(self):
        import uploader.embedded_browser as browser
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(browser, 'PRIVATE', Path(directory)):
                first = browser.EmbeddedBrowser('kuaishou', 'login')
                first.mark_login({'identity': 'ks-identity', 'uid': 'ks-owner', 'verified': True})
                reopened = json.loads(browser.embedded_account_file('kuaishou').read_text())
                self.assertEqual(reopened['status'], 'login_verified')
                self.assertEqual(reopened['account']['identity'], 'ks-identity')
                self.assertEqual(oct(os.stat(browser.embedded_account_file('kuaishou')).st_mode & 0o777), '0o600')
                second = browser.EmbeddedBrowser('kuaishou', 'draft')
                second.mark_login({'identity': 'ks-identity', 'uid': 'ks-owner', 'verified': True})
                reread = json.loads(browser.embedded_account_file('kuaishou').read_text())
                self.assertEqual(reread['account']['identity'], 'ks-identity')
                self.assertEqual(reread['status'], 'login_verified')
                third = browser.EmbeddedBrowser('kuaishou', 'login')
                third.mark_login(None, 'needs_reauthentication')
                failed = json.loads(browser.embedded_account_file('kuaishou').read_text())
                self.assertEqual(failed['status'], 'needs_reauthentication')
                self.assertNotEqual(failed['status'], 'login_verified')

    def test_embedded_reconnect_prompt_uses_the_app_name(self):
        import uploader.embedded_browser as browser
        missing = Path('/tmp/g2-missing-embedded-descriptor.json')
        self.assertFalse(missing.exists())
        with patch.object(browser, 'DESCRIPTOR', missing):
            with self.assertRaises(RuntimeError) as result:
                browser.bridge_request('/prepare', {})
        self.assertIn('发布助手', str(result.exception))
        self.assertNotIn('视频草稿工作台', str(result.exception))

    def test_corrupt_video_is_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'bad.mp4'
            video.write_bytes(b'not a video')
            with self.assertRaisesRegex(ValueError, '无法读取'):
                media_info(video)


if __name__ == '__main__':
    unittest.main(verbosity=2)
