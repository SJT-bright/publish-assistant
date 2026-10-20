"""Isolated Douyin draft-recovery UI and upload-safety fixtures."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from patchright.async_api import async_playwright

from uploader.draft_only import _reopen
from uploader.douyin_uploader.main import DouYinVideo


ROOT_URL = 'https://creator.douyin.com/creator-micro/content/post/video'
RECOVERY_FIXTURES = {
    'div': '<div class="recovery" id="div-control">继续编辑</div>',
    'span': '<span class="recovery" id="span-control">继续创作</span>',
    'multiple': '<div class="recovery" id="visible-a">继续编辑</div>'
                '<span class="recovery" id="visible-b">恢复草稿</span>',
    'hidden': '<div class="recovery" id="visible">继续编辑</div>'
              '<span class="recovery hidden" id="hidden">继续编辑</span>',
    'none': '<div class="recovery" id="near-match">继续编辑草稿</div>',
    'composer': '<input id="video-input" type="file" accept="video/mp4">',
}


class DouyinDraftRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chrome')
        self.context = await self.browser.new_context()
        self.requests = []

        async def serve(route):
            self.requests.append(route.request.url)
            case = parse_qs(urlsplit(route.request.url).query).get('case', ['none'])[0]
            controls = RECOVERY_FIXTURES.get(case, RECOVERY_FIXTURES['none'])
            body = f'''<!doctype html><meta charset="utf-8"><style>
              .recovery {{ display:block; padding:12px; }} .hidden {{ display:none; }}
            </style><div id="fixture">{controls}</div>
            <script>window.clicked=[];document.querySelectorAll('.recovery').forEach(el=>
              el.addEventListener('click',()=>window.clicked.push(el.id)))</script>'''
            await route.fulfill(status=200, content_type='text/html', body=body)

        # Fulfill every request locally; never depend on or contact the public site.
        await self.context.route('**/*', serve)
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def load_case(self, case):
        await self.page.goto(f'{ROOT_URL}?enter_from=draft&case={case}')

    async def clicked(self):
        return await self.page.evaluate('window.clicked', isolated_context=False)

    @staticmethod
    def candidate():
        return {'id': 'fixture-draft-id', 'owner': 'fixture-owner', 'storage_key': 'fixture-cache-key'}

    async def test_exact_div_and_span_recovery_controls_are_clicked_once(self):
        for case, text, element_id in (
            ('div', '继续编辑', 'div-control'),
            ('span', '继续创作', 'span-control'),
        ):
            with self.subTest(case=case):
                await self.load_case(case)
                session = SimpleNamespace(normal_ui_binding=None)
                self.page._sau_draft_evidence = session

                self.assertTrue(await _reopen(self.page, 'douyin', self.candidate()))
                self.assertEqual(await self.clicked(), [element_id])
                self.assertEqual(session.normal_ui_binding, {
                    'id': 'fixture-draft-id', 'key': 'fixture-cache-key',
                    'owner': 'fixture-owner', 'control': text,
                })

    async def test_multiple_visible_recovery_controls_are_rejected_without_click(self):
        await self.load_case('multiple')
        session = SimpleNamespace(normal_ui_binding=None)
        self.page._sau_draft_evidence = session

        self.assertFalse(await _reopen(self.page, 'douyin', self.candidate()))
        self.assertEqual(await self.clicked(), [])
        self.assertIsNone(session.normal_ui_binding)

    async def test_hidden_duplicate_is_ignored_when_one_visible_control_matches(self):
        await self.load_case('hidden')
        session = SimpleNamespace(normal_ui_binding=None)
        self.page._sau_draft_evidence = session

        self.assertTrue(await _reopen(self.page, 'douyin', self.candidate()))
        self.assertEqual(await self.clicked(), ['visible'])
        self.assertEqual(session.normal_ui_binding['control'], '继续编辑')

    async def test_no_exact_recovery_control_does_not_click_near_match(self):
        await self.load_case('none')
        session = SimpleNamespace(normal_ui_binding=None)
        self.page._sau_draft_evidence = session

        self.assertFalse(await _reopen(self.page, 'douyin', self.candidate()))
        self.assertEqual(await self.clicked(), [])
        self.assertIsNone(session.normal_ui_binding)

    @staticmethod
    def uploader(*, sealed=False):
        app = DouYinVideo(
            title='fixture title', file_path='/fixture/video.mp4', tags=[],
            publish_date=0, account_file='unused.json', desc='fixture caption',
        )
        app.draft_only = True
        app.draft_expected = {'platform_fields': {'caption': 'fixture caption'}}
        if sealed:
            app.draft_expected.update({'video_sha256': '0' * 64})
        return app

    async def test_unsealed_existing_composer_preserves_page_without_upload_or_navigation(self):
        await self.load_case('composer')
        app = self.uploader()
        goto = AsyncMock(wraps=self.page.goto)
        with patch.object(self.page, 'goto', new=goto), \
             patch('uploader.douyin_uploader.main.set_local_files', new=AsyncMock()) as set_files, \
             patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, '已有编辑中的作品'):
                await app._upload_draft_content(self.page)

        goto.assert_not_awaited()
        set_files.assert_not_awaited()
        self.assertEqual(self.page.url, f'{ROOT_URL}?enter_from=draft&case=composer')
        self.assertEqual([urlsplit(url).path for url in self.requests], [urlsplit(ROOT_URL).path])

    async def test_disconnected_observer_stops_before_selecting_video(self):
        async def empty_editor(route):
            await route.fulfill(status=200, content_type='text/html', body=RECOVERY_FIXTURES['composer'])
        await self.context.route('**/creator-micro/content/post/video?enter_from=publish_page&type=new', empty_editor)
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload?case=composer')
        observer = SimpleNamespace(ensure_ready=AsyncMock(return_value=False))
        self.page._sau_draft_evidence = SimpleNamespace(
            closed=False, _checkpoint_restored_at=None, dy_observer=observer)
        app = self.uploader()
        with patch('uploader.douyin_uploader.main.set_local_files', new=AsyncMock()) as set_files, \
             patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, '导入视频前停止'):
                await app._upload_draft_content(self.page)
        set_files.assert_not_awaited()
        observer.ensure_ready.assert_awaited_once_with(timeout=10)

    async def test_sealed_resume_on_same_composer_is_not_misclassified_as_new_upload(self):
        await self.load_case('composer')
        self.page._sau_draft_evidence = SimpleNamespace(closed=False, _checkpoint_restored_at=123.0)
        app = self.uploader(sealed=True)
        await self.page.evaluate('''() => {
            const title=document.createElement('input');title.placeholder='填写作品标题';
            const body=document.createElement('div');body.className='zone-container';
            body.contentEditable='true';body.style.minHeight='40px';
            document.body.append(title,body);
        }''')
        app.fill_title_and_description = AsyncMock(wraps=app.fill_title_and_description)
        app.thumbnail_portrait_path = None
        app.thumbnail_landscape_path = None
        goto = AsyncMock(wraps=self.page.goto)
        with patch.object(self.page, 'goto', new=goto), \
             patch('uploader.douyin_uploader.main.set_local_files', new=AsyncMock()) as set_files, \
             patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()), \
             patch('uploader.draft_only.wait_draft_upload', new=AsyncMock()) as wait_upload, \
             patch('uploader.draft_only.checkpoint_after_upload', new=AsyncMock(return_value=True)) as checkpoint, \
             patch('uploader.draft_only.save_draft', new=AsyncMock()) as save:
            await app._upload_draft_content(self.page)

        goto.assert_not_awaited()
        set_files.assert_not_awaited()
        wait_upload.assert_not_awaited()
        checkpoint.assert_awaited_once_with(self.page)
        app.fill_title_and_description.assert_awaited_once_with(
            self.page, 'fixture title', 'fixture caption', [])
        save.assert_awaited_once()
        self.assertEqual(self.page.url, f'{ROOT_URL}?enter_from=draft&case=composer')


if __name__ == '__main__':
    unittest.main()
