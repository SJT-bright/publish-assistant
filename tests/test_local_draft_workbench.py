"""Local Chromium checks with synthetic files; no platform sessions or uploads."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patchright.async_api import async_playwright
from uploader.draft_only import install_draft_guard
import draft_workbench as workbench
import draft_runner


class DraftBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chromium')
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(workbench, 'PRIVATE', self.root / 'private'), patch.object(workbench, 'STORE_ROOT', None),
            patch.object(workbench, 'CURRENT', None), patch.object(workbench, 'EDITOR', {}),
            patch.object(workbench, 'LEDGER', {}), patch.object(workbench, 'ACCOUNTS', {}),
            patch.object(workbench, 'HISTORY', []), patch.object(workbench, 'ASSETS', set()),
            patch.object(workbench, 'account_file', return_value=self.root/'no-session.json'),
            patch.object(workbench, 'embedded_account_file', return_value=self.root/'no-embedded-session.json')]
        for item in self.patches: item.start()

    async def asyncTearDown(self):
        await self.context.close(); await self.browser.close(); await self.playwright.stop()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    async def test_publish_buttons_and_shadow_dom_are_blocked(self):
        await install_draft_guard(self.context)
        names = ['发布','发表','投稿','提交投稿','立即发布','定时发布','发布视频','发布笔记','确认发布','确定发布','发布作品','提交审核','保存并发布']
        html = '<meta charset="utf-8"><body data-sends="0">' + ''.join(f'<button onclick="document.body.dataset.sends=Number(document.body.dataset.sends)+1">{x}</button>' for x in names) + '</body>'
        await self.page.route('http://draft.test/**', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=html))
        await self.page.goto('http://draft.test/editor')
        for name in names: await self.page.get_by_role('button', name=name, exact=True).click()
        await self.page.evaluate("""() => {const host=document.createElement('div');document.body.append(host);host.attachShadow({mode:'open'}).innerHTML='<span role="button" onclick="document.body.dataset.sends=Number(document.body.dataset.sends)+1">发布</span>'; }""")
        await self.page.locator('[role="button"]').click()
        self.assertEqual(await self.page.evaluate('document.body.dataset.sends'), '0')

    async def workbench_fixture(self, editor):
        workbench.save_editor(editor)
        self.started = []
        async def route(request):
            path = request.request.url.split('http://workbench.test', 1)[1].split('?', 1)[0]
            if path == '/':
                return await request.fulfill(content_type='text/html; charset=utf-8', body=(workbench.ROOT/'draft_workbench.html').read_text())
            if path == '/api/state':
                return await request.fulfill(json=workbench.state())
            if path.startswith('/ui-assets/'):
                asset = workbench.ROOT / path.removeprefix('/')
                return await request.fulfill(path=asset)
            data = request.request.post_data_json
            if path == '/api/editor': result = workbench.save_editor(data)
            elif path == '/api/text-check':
                from platform_limits import check_text
                result=check_text(data.get('payload',{}),data.get('platforms',[]))
            elif path == '/api/preflight': result = workbench.preflight(data)
            elif path == '/api/start': self.started.append(data); result = {'id': 'fake-no-operation'}
            else: result = None
            return await request.fulfill(json={'result': result})
        await self.page.route('http://workbench.test/**', route)
        await self.page.goto('http://workbench.test/')
        await self.page.locator('.login').first.wait_for()

    def material(self, title='素材标题'):
        video = self.root/'video.mp4'; video.write_bytes(b'local synthetic fixture')
        return {'video': str(video), 'videos': [str(video)], 'folder': str(self.root), 'title': title, 'description': '完整正文', 'tags': ['生活']}

    async def test_ui_shows_exact_over_limit_title_and_never_schedules_upload(self):
        title = '小红书标题' * 5
        await self.workbench_fixture({'payload': self.material(title), 'platforms': ['xiaohongshu']})
        await self.page.wait_for_function('document.querySelector("#textLimitIssues").classList.contains("limit-error")')
        self.assertFalse(await self.page.locator('#save').is_disabled())
        await self.page.locator('#save').click()
        await self.page.get_by_text('预检未通过',exact=True).wait_for()
        self.assertEqual(self.started, [])
        self.assertEqual(await self.page.locator('#title').input_value(), title)
        self.assertIn('/20 字', await self.page.locator('#issues').inner_text())
        self.assertIn(title, await self.page.locator('#preview').inner_text())

    async def test_ui_persists_platform_override_and_restores_selected_platform(self):
        await self.workbench_fixture({'payload': self.material(), 'platforms': ['kuaishou']})
        self.assertTrue(await self.page.locator('[data-platform="kuaishou"]').is_checked())
        widget=self.page.locator('[data-override-field="title"]')
        await widget.locator('.field-override-toggle').click()
        await widget.get_by_role('button',name='快手',exact=True).click()
        await widget.get_by_role('button',name='快手 · 单独设置',exact=True).click()
        await widget.get_by_label('平台独立标题').fill('单独标题')
        await self.page.locator('#precheck').click()
        await self.page.get_by_text('预检通过 · 提交前还会现场核验上传与内容', exact=True).wait_for()
        self.assertIn('单独标题\n完整正文\n#生活', await self.page.locator('#preview').inner_text())
        self.assertIn('平台单份暂存快照', await self.page.locator('#preview').inner_text())
        self.assertEqual(self.started, [])
        await self.page.reload()
        await self.page.locator('.login').first.wait_for()
        self.assertTrue(await self.page.locator('[data-platform="kuaishou"]').is_checked())
        widget=self.page.locator('[data-override-field="title"]')
        await widget.locator('.field-override-toggle').click()
        await widget.get_by_role('button',name='快手',exact=True).click()
        self.assertEqual(await widget.get_by_label('平台独立标题').input_value(), '单独标题')
        self.assertIn('打开平台页确认登录', await self.page.locator('#status-kuaishou').inner_text())
        self.assertNotIn('已保存登录', await self.page.locator('#platforms').inner_text())


class WorkbenchBoundaryTests(unittest.TestCase):
    def test_metadata_and_cover_import_without_silent_short_title(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'测试视频.mp4').write_bytes(b'test'); (root/'cover.jpg').write_bytes(b'test')
            (root/'publish.json').write_text(json.dumps({'title':'统一标题','description':'统一文案','tags':['生活'],'cover':'cover.jpg'},ensure_ascii=False))
            data = workbench.load_material(folder)
            self.assertEqual(data['title'], '统一标题'); self.assertEqual(data['description'], '统一文案')
            self.assertEqual(data['tags'], ['生活']); self.assertEqual(data['cover'], str((root/'cover.jpg').resolve()))
            self.assertEqual(data['short_title'], '')

    def test_public_publish_still_requires_valid_material(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(workbench, 'PRIVATE', Path(folder)), patch.object(workbench, 'STORE_ROOT', None):
            with self.assertRaisesRegex(ValueError, '素材预检未通过'):
                workbench.start_job({'action':'publish','platforms':['douyin']})

    def test_unknown_platform_cannot_fall_back_to_tencent(self):
        with self.assertRaisesRegex(ValueError, '未知平台'): draft_runner.account_file('not-a-platform')


if __name__ == '__main__': unittest.main(verbosity=2)
