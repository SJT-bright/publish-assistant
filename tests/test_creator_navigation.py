"""Preserve creator documents, choose exact controls, and reuse shadow editors.

All web requests use isolated local route fixtures; no real account is touched.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from draft_execution import URLS, find_control_priority, open_creator
from uploader.draft_session import DraftInterrupted
from uploader.local_files import set_local_files


class CreatorNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.html = '<!doctype html><meta charset="utf-8"><input id="saved-title" value="用户正在编辑的标题">'
        self.requests = []

        async def local_route(route):
            self.requests.append(route.request.url)
            await route.fulfill(content_type='text/html; charset=utf-8', body=self.html)
        await self.context.route('**/*', local_route)
        # Test-mode bridge routing must not alter these creator URL tests.
        self.env = patch.dict(os.environ, {'SAU_DESKTOP_TEST': '0'})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory(prefix='creator-controls-')
        self.video = Path(self.temp.name) / 'fixture.mp4'
        self.video.write_bytes(b'isolated input fixture')

    async def asyncTearDown(self):
        self.env.stop()
        await self.browser.close()
        await self.p.stop()
        self.temp.cleanup()

    async def test_correct_open_creator_document_is_reused_without_navigation(self):
        for platform, url in URLS.items():
            with self.subTest(platform=platform):
                await self.page.goto(url)
                await self.page.locator('#saved-title').fill('保留编辑：' + platform)
                self.requests.clear()
                with patch.object(self.page, 'goto', new=AsyncMock(side_effect=AssertionError('Existing creator document must not navigate'))) as goto:
                    await open_creator(platform, self.page)
                    goto.assert_not_awaited()
                self.assertEqual(await self.page.locator('#saved-title').input_value(), '保留编辑：' + platform)
                self.assertEqual(self.requests, [])

    async def test_aborted_navigation_is_accepted_only_after_creator_landing(self):
        await self.page.goto('https://fixture.invalid/start')
        original = self.page.goto

        async def redirected_creator(url, **kwargs):
            await original(URLS['tencent'] + '?normal-spa-redirect=1')
            raise RuntimeError('Page.goto: net::ERR_ABORTED')
        with patch.object(self.page, 'goto', new=AsyncMock(side_effect=redirected_creator)) as goto:
            await open_creator('tencent', self.page)
            goto.assert_awaited_once()
        self.assertIn('channels.weixin.qq.com/platform/post/create', self.page.url)

        for destination, message in (('https://accounts.invalid/login', 'net::ERR_ABORTED'),
                                     (URLS['tencent'], 'net::ERR_FAILED')):
            with self.subTest(destination=destination, message=message):
                await original('https://fixture.invalid/start')
                async def bad_landing(url, **kwargs):
                    await original(destination)
                    raise RuntimeError(message)
                with patch.object(self.page, 'goto', new=AsyncMock(side_effect=bad_landing)):
                    with self.assertRaisesRegex(RuntimeError, message):
                        await open_creator('tencent', self.page)

    async def test_bilibili_component_is_preferred_over_first_backup_input(self):
        self.html = '''<!doctype html><input id="backup" type="file" accept="video/mp4" hidden>
            <div class="bcc-upload-wrapper"><input id="active" type="file" accept=".mp4,.mov" hidden></div>
            <script>window.inputChanges=[];for(const input of document.querySelectorAll('input'))input.addEventListener('change',()=>inputChanges.push(input.id));</script>'''
        await self.page.goto(URLS['bilibili'])
        selectors = ['.bcc-upload-wrapper input[type="file"][accept*="mp4"]',
                     'input[type="file"][accept*="video"],input[type="file"][accept*="mp4"]']
        self.assertEqual(await self.page.locator(selectors[1]).count(), 2)
        selected = await find_control_priority(self.page, selectors, timeout=.1, attached=True)
        self.assertEqual(await selected.get_attribute('id'), 'active')
        await set_local_files(self.page, selected, self.video)
        self.assertEqual(await self.page.locator('#active').evaluate('input=>input.files[0].name'), self.video.name)
        self.assertEqual(await self.page.locator('#backup').evaluate('input=>input.files.length'), 0)
        session = await self.context.new_cdp_session(self.page)
        try:
            result = await session.send('Runtime.evaluate', {'expression': 'window.inputChanges', 'returnByValue': True})
            self.assertEqual(result['result']['value'], ['active'])
        finally:
            await session.detach()

    async def test_duplicate_primary_components_fail_without_setting_any_files(self):
        self.html = '<div class="bcc-upload-wrapper"><input type="file" accept=".mp4"><input type="file" accept=".mp4"></div>'
        await self.page.goto(URLS['bilibili'])
        with self.assertRaises(DraftInterrupted) as result:
            await find_control_priority(self.page, ['.bcc-upload-wrapper input[type=file]', 'input[type=file]'], timeout=.1, attached=True)
        self.assertEqual(result.exception.status, 'adapter_mismatch')
        self.assertEqual(await self.page.locator('input[type=file]').evaluate_all('inputs=>inputs.map(input=>input.files.length)'), [0, 0])

    async def test_tencent_empty_iframe_shadow_editor_is_reused_and_editable(self):
        from uploader.tencent_uploader.main import TencentVideo
        self.html = '''<!doctype html><meta charset="utf-8"><section id="micro-app"></section>
            <iframe src="about:blank" title="micro-app transport"></iframe>
            <script>document.getElementById('micro-app').attachShadow({mode:'open'}).innerHTML=
            '<input type="file" hidden accept="video/mp4"><div class="input-editor" contenteditable="" style="min-height:36px;min-width:300px;white-space:pre-wrap">原有文案</div>';</script>'''
        await self.page.goto(URLS['tencent'])
        self.assertEqual(await self.page.frames[1].locator('input').count(), 0)
        editor = self.page.locator('div.input-editor')
        self.assertEqual(await editor.get_attribute('contenteditable'), '')
        self.assertTrue(await editor.evaluate('element=>element.isContentEditable'))
        uploader = TencentVideo('统一标题', str(self.video), ['独立话题'], 0, 'unused-fixture.json', desc='完整文案')
        uploader.draft_only = True
        caption = '统一标题\n第一行正文\n第二行正文 #独立话题'
        uploader.draft_expected = {'platform_fields': {'caption': caption}}
        with patch.object(self.page, 'goto', new=AsyncMock(side_effect=AssertionError('Mounted shadow editor must not navigate'))) as goto:
            await uploader.open_upload_page(self.page)
            goto.assert_not_awaited()
        self.assertEqual(await editor.inner_text(), '原有文案')
        await uploader.prepare_video_for_publish(self.page)
        self.assertEqual(await editor.inner_text(), caption)
        self.assertEqual(await self.page.locator('input[type=file]').evaluate('input=>input.files.length'), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
