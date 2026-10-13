"""Bilibili adapter control flow on local routed fixtures; never real accounts.

One Chromium instance is reused across tests, with a fresh context per scenario.
Mocking save_draft proves handoff/control flow, not actual draft persistence.
"""
import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
import draft_execution as execution
from uploader.draft_session import DraftInterrupted

BASE = 'http://127.0.0.1:19688'
TITLE = 'B站完整标题'
DESCRIPTION = '第一行完整正文\n第二行完整正文'
TAGS = ['话题一', '话题二', '话题三']


class BilibiliDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        async def start():
            cls.playwright = await async_playwright().start()
            cls.browser = await cls.playwright.chromium.launch(headless=True, channel='chromium')
        cls.loop.run_until_complete(start())

    @classmethod
    def tearDownClass(cls):
        async def close():
            await cls.browser.close()
            await cls.playwright.stop()
        cls.loop.run_until_complete(close())
        cls.loop.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sau-bilibili-local-')
        self.video = Path(self.temp.name) / 'synthetic.mp4'; self.video.write_bytes(b'local video input fixture')
        self.cover = Path(self.temp.name) / 'selected-cover.jpg'; self.cover.write_bytes(b'local image input fixture')
        self.payload = {'video': str(self.video), 'title': TITLE, 'description': DESCRIPTION, 'tags': list(TAGS),
                        'platform_id': 'bilibili', 'job_id': 'bound-local-job', 'receipt_nonce': 'bound-local-nonce',
                        'video_sha256': 'local-material-hash', 'account_key': 'fixture-account-key'}

    def tearDown(self): self.temp.cleanup()

    def run_async(self, coroutine): return self.loop.run_until_complete(coroutine)

    async def fixture(self, *, outcome='success', parent_image=True, duplicate_video=False,
                      cover_mode=None, unrelated_dialog=False, late_cover_risk=False, stale_description=False):
        context = await self.browser.new_context()
        external = []
        config = json.dumps({'outcome': outcome, 'coverRisk': late_cover_risk})
        cover_input = '<input id="cover-file" type="file" accept="image/jpeg" onchange="document.body.dataset.coverUploads=String(++window.coverUploads);if(cfg.coverRisk)document.querySelector(\'#risk-dialog\').style.display=\'block\'">' if cover_mode == 'dialog' else ''
        cover_dialog = f'''<button id="choose-cover" onclick="document.querySelector('#cover-dialog').style.display='block'">更换封面</button>
            <div id="cover-dialog" role="dialog" style="display:none">封面设置 {cover_input}
              <button onclick="document.body.dataset.coverConfirms=String(++window.coverConfirms);document.querySelector('#cover-dialog').style.display='none'">完成</button>
            </div>''' if cover_mode else ''
        other_dialog = '''<div id="other-dialog" role="dialog">其他设置
            <button onclick="document.body.dataset.genericConfirms=String(++window.genericConfirms)">确认</button></div>''' if unrelated_dialog else ''
        child = f'''<!doctype html><meta charset="utf-8"><style>[contenteditable=true]{{min-height:35px}}</style>
            <body data-video-uploads="0" data-cover-uploads="0" data-cover-confirms="0" data-generic-confirms="0" data-risk-confirms="0">
            <script>const cfg={config};window.videoUploads=0;window.coverUploads=0;window.coverConfirms=0;window.genericConfirms=0;window.riskConfirms=0;window.enteredTags=[];</script>
            <input id="video-file" type="file" accept="video/mp4" onchange="document.body.dataset.videoUploads=String(++window.videoUploads);document.querySelector('#upload-state').textContent=cfg.outcome==='risk'?'安全验证：请输入验证码':cfg.outcome==='failure'?'上传失败':'上传完成'">
            <div id="upload-state"></div><input id="title" placeholder="请输入稿件标题">
            <textarea id="description" placeholder="填写简介">{'旧稿件残留文案' if stale_description else ''}</textarea>
            <input id="tag-input" placeholder="请输入标签"><div id="tags"></div>
            <script>document.querySelector('#tag-input').addEventListener('keydown',e=>{{if(e.key==='Enter'){{e.preventDefault();window.enteredTags.push(e.target.value);const tag=document.createElement('span');tag.className='tag';tag.textContent=e.target.value;document.querySelector('#tags').append(tag);e.target.value='';}}}});</script>
            {cover_dialog}{other_dialog}<button id="generic-confirm" onclick="document.body.dataset.genericConfirms=String(++window.genericConfirms)">确认</button>
            <div id="risk-dialog" role="dialog" style="display:none">安全验证：请输入验证码<button onclick="document.body.dataset.riskConfirms=String(++window.riskConfirms)">确定</button></div>
            <button>保存草稿</button>'''
        parent = '<!doctype html><meta charset="utf-8"><body data-parent-images="0" data-parent-videos="0"><script>window.parentImages=0;window.parentVideos=0;</script>'
        if parent_image: parent += '<input id="unrelated-image" type="file" accept="image/png" onchange="document.body.dataset.parentImages=String(++window.parentImages)">'
        if duplicate_video: parent += '<input type="file" accept="video/mp4" onchange="document.body.dataset.parentVideos=String(++window.parentVideos)">'
        parent += f'<iframe src="{BASE}/editor"></iframe>'
        async def route(request):
            url = request.request.url
            if not url.startswith(BASE + '/'):
                external.append(url); return await request.abort()
            await request.fulfill(content_type='text/html; charset=utf-8', body=child if url.endswith('/editor') else parent)
        await context.route('**/*', route)
        page = await context.new_page()
        await page.goto(BASE + '/parent')
        frame = page.frame(url=BASE + '/editor')
        if frame is None:
            await page.locator('iframe').wait_for()
            frame = await page.locator('iframe').content_frame()
        await frame.locator('#video-file').wait_for(state='attached')
        return context, page, frame, external

    async def invoke(self, page, payload=None):
        return await asyncio.wait_for(execution.bilibili_draft(payload or self.payload, Path(self.temp.name)/'receipt.json', page=page), timeout=60)

    async def counter(self, frame, name):
        if name == 'enteredTags': return await frame.locator('#tags .tag').all_text_contents()
        attribute = 'data-' + re.sub(r'[A-Z]', lambda match: '-' + match[0].lower(), name)
        return int(await frame.locator('body').get_attribute(attribute))

    def test_iframe_video_ignores_parent_image_and_passes_exact_expected(self):
        async def scenario():
            context, page, frame, external = await self.fixture()
            saved = {'test': 'mock handoff only'}; save = AsyncMock(return_value=saved)
            try:
                with patch.object(execution, 'save_draft', save): result = await self.invoke(page)
                self.assertEqual(result, saved)
                self.assertEqual(await self.counter(frame, 'videoUploads'), 1)
                self.assertEqual(await self.counter(page, 'parentImages'), 0)
                self.assertEqual(await frame.locator('#title').input_value(), TITLE)
                self.assertEqual(await frame.locator('#description').input_value(), DESCRIPTION)
                self.assertEqual(await self.counter(frame, 'enteredTags'), TAGS)
                save.assert_awaited_once()
                self.assertIs(save.await_args.kwargs['expected'], self.payload)
                self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())

    def test_custom_cover_uses_only_explicit_cover_dialog_confirmation(self):
        async def scenario():
            context, page, frame, external = await self.fixture(parent_image=False, cover_mode='dialog')
            save = AsyncMock(); payload = {**self.payload, 'cover': str(self.cover)}
            try:
                with patch.object(execution, 'save_draft', save): await self.invoke(page, payload)
                self.assertEqual(await self.counter(frame, 'coverUploads'), 1)
                self.assertEqual(await self.counter(frame, 'coverConfirms'), 1)
                self.assertEqual(await self.counter(frame, 'genericConfirms'), 0)
                self.assertEqual(await self.counter(frame, 'videoUploads'), 1)
                save.assert_awaited_once(); self.assertIs(save.await_args.kwargs['expected'], payload)
                self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())

    def test_failure_and_risk_stop_promptly_without_save_or_resubmission(self):
        async def scenario():
            for outcome in ('failure', 'risk'):
                with self.subTest(outcome=outcome):
                    context, page, frame, external = await self.fixture(outcome=outcome)
                    save = AsyncMock()
                    try:
                        with patch.object(execution, 'save_draft', save), self.assertRaises(DraftInterrupted) as error:
                            await self.invoke(page)
                        self.assertEqual(error.exception.status, 'risk_paused' if outcome=='risk' else 'result_unknown')
                        self.assertEqual(await self.counter(frame, 'videoUploads'), 1)
                        self.assertEqual(await frame.locator('#title').input_value(), '')
                        save.assert_not_awaited(); self.assertFalse(external)
                    finally: await context.close()
        self.run_async(scenario())

    def test_two_video_slots_stop_before_any_material_submission(self):
        async def scenario():
            context, page, frame, external = await self.fixture(duplicate_video=True)
            save = AsyncMock()
            try:
                with patch.object(execution, 'save_draft', save), self.assertRaises(DraftInterrupted) as error: await self.invoke(page)
                self.assertEqual(error.exception.status, 'adapter_mismatch')
                self.assertIn('控件', str(error.exception))
                self.assertEqual(await self.counter(page, 'parentVideos'), 0)
                self.assertEqual(await self.counter(frame, 'videoUploads'), 0)
                save.assert_not_awaited(); self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())

    def test_cover_file_input_must_belong_to_its_dialog_not_parent_image(self):
        async def scenario():
            context, page, frame, external = await self.fixture(cover_mode='outside-only')
            save = AsyncMock()
            try:
                with patch.object(execution, 'save_draft', save), self.assertRaises(DraftInterrupted): await self.invoke(page, {**self.payload, 'cover': str(self.cover)})
                self.assertEqual(await self.counter(page, 'parentImages'), 0)
                self.assertEqual(await self.counter(frame, 'coverConfirms'), 0)
                save.assert_not_awaited(); self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())

    def test_cover_confirmation_ignores_unrelated_dialog(self):
        async def scenario():
            context, page, frame, external = await self.fixture(parent_image=False, cover_mode='dialog', unrelated_dialog=True)
            save = AsyncMock()
            try:
                with patch.object(execution, 'save_draft', save): await self.invoke(page, {**self.payload, 'cover': str(self.cover)})
                self.assertEqual(await self.counter(frame, 'coverConfirms'), 1)
                self.assertEqual(await self.counter(frame, 'genericConfirms'), 0)
                save.assert_awaited_once(); self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())

    def test_risk_appearing_during_cover_upload_blocks_all_confirmations(self):
        async def scenario():
            context, page, frame, external = await self.fixture(parent_image=False, cover_mode='dialog', late_cover_risk=True)
            save = AsyncMock()
            try:
                with patch.object(execution, 'save_draft', save), self.assertRaises(DraftInterrupted) as error: await self.invoke(page, {**self.payload, 'cover': str(self.cover)})
                self.assertEqual(error.exception.status, 'risk_paused')
                self.assertEqual(await self.counter(frame, 'coverConfirms'), 0)
                self.assertEqual(await self.counter(frame, 'riskConfirms'), 0)
                self.assertEqual(await self.counter(frame, 'videoUploads'), 1)
                save.assert_not_awaited(); self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())

    def test_empty_description_clears_stale_editor_content(self):
        async def scenario():
            context, page, frame, external = await self.fixture(stale_description=True)
            save = AsyncMock()
            try:
                with patch.object(execution, 'save_draft', save): await self.invoke(page, {**self.payload, 'description': ''})
                self.assertEqual(await frame.locator('#description').input_value(), '')
                save.assert_awaited_once(); self.assertFalse(external)
            finally: await context.close()
        self.run_async(scenario())


class BilibiliUploadGateTests(unittest.TestCase):
    """Control identity and the text gate, without launching a browser."""

    def test_two_slots_are_adapter_mismatch_not_an_account_hold(self):
        broad = 'input[type="file"][accept*="video"],input[type="file"][accept*="mp4"]'
        narrow = '.bcc-upload-wrapper input[type="file"][accept*="mp4"]'

        class Item:
            def __init__(self, rows):
                self.rows = rows
            async def all(self):
                return self.rows

        class Frame:
            def __init__(self, counts):
                self.counts = counts
            def locator(self, selector):
                return Item([object()] * self.counts.get(selector, 0))

        class Page:
            def __init__(self):
                self.frames = [Frame({narrow: 0, broad: 1}), Frame({narrow: 0, broad: 1})]

        async def run():
            await execution.find_control_priority(Page(), [narrow, broad], timeout=.3, attached=True)

        with self.assertRaises(DraftInterrupted) as error:
            asyncio.run(run())
        self.assertEqual(error.exception.status, 'adapter_mismatch')
        self.assertNotIn('权限', str(error.exception))

    def test_over_limit_text_stops_before_any_control_lookup(self):
        class Page:
            def __init__(self):
                self.lookups = 0
            @property
            def frames(self):
                self.lookups += 1
                return []

        page = Page()
        with tempfile.TemporaryDirectory(prefix='sau-bili-gate-') as directory:
            receipt = Path(directory) / 'receipt.json'
            payload = {'title': '字' * 81, 'description': '', 'tags': [], 'video': '/tmp/not-a-real-video.mp4'}
            with self.assertRaises(ValueError) as error:
                asyncio.run(execution.bilibili_draft(payload, receipt, page=page))
            self.assertIn('尚未上传', str(error.exception))
            self.assertEqual(page.lookups, 0)
            self.assertFalse(receipt.exists())

            limited = Page()
            limited_payload = {'title': '标题', 'description': '', 'tags': ['一', '二', '三'],
                               'capabilities': {'tags_max': 2, 'new_tag_max': 30}}
            with self.assertRaises(ValueError):
                asyncio.run(execution.bilibili_draft(limited_payload, receipt, page=limited))
            self.assertEqual(limited.lookups, 0)
            self.assertFalse(receipt.exists())


if __name__ == '__main__': unittest.main(verbosity=2)
