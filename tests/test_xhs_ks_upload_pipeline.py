"""Local browser pipelines; native SDK contracts are tested separately.

All network requests are fulfilled from local fixture HTML. These tests prove
actual text inputs occur before upload completion, and the final action joins
both branches. The observer here explicitly models the production completion
contract; it does not make these fixtures real platform upload evidence.
"""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader import draft_only
from uploader.draft_session import DraftInterrupted
from uploader.ks_uploader.main import KSVideo
from uploader.xhs_cover_controls import open_xhs_cover_editor
from uploader.xhs_ks_upload_ready import wait_xhs_ks_upload_complete
from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
from tests.test_draft_upload_safety import fixture, TITLE, BODY, TAGS, CAPTION
from tests.test_xhs_cover_controls import FIXTURE as COVER_FIXTURE


class FixtureObserver:
    """Explicit contract mock; the actual observers keep their strict gates."""
    def __init__(self, session):
        self.session, self.page, self.complete, self.closed = session, session.page, False, False
        self.sha = session.expected["video_sha256"]
        self.ensure_ready = AsyncMock(return_value=True)
        self.calls = 0

    async def drain(self):
        self.calls += 1
        self.complete = await self.session.page.locator("body").get_attribute("data-complete") == "true"

    def upload_links(self):
        return ([{"kind": "video", "complete": True, "sha256": self.sha,
                  "ids": ["fixture-current-upload"]}] if self.complete else [])


class XHSKSPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.pw = await async_playwright().start()
        shell = Path.home() / "Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell"
        options = {"executable_path": str(shell)} if shell.exists() else {"channel": "chrome"}
        self.browser = await self.pw.chromium.launch(headless=True, args=["--disable-gpu"], **options)
        self.temp = tempfile.TemporaryDirectory(prefix="sau-xhs-ks-pipeline-")
        self.video = Path(self.temp.name) / "synthetic.mp4"
        self.video.write_bytes(b"isolated native file; no real material or account")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.temp.cleanup()

    async def setup_adapter(self, platform, *, timeout=20):
        context = await self.browser.new_context()
        ready = ("<div class='cover-container'><div class='preview-new'>检测为高清视频</div></div>"
                 if platform == "xiaohongshu" else "<div>视频上传成功</div>")
        html = fixture(platform, "pending") + """<script>
          window.events=[];
          document.addEventListener('input', e=>{if(e.target.type!=='file')
            events.push({event:'content',time:performance.now(),text:e.target.value||e.target.innerText})});
          document.querySelector('input[type=file]').addEventListener('change',()=>
            events.push({event:'selected',time:performance.now()}));
          window.completeUpload = html => {document.getElementById('state').innerHTML=html;
            document.body.dataset.complete='true';events.push({event:'uploaded',time:performance.now()})};
        </script>"""
        await context.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
        page = await context.new_page()
        await page.goto("https://fixture.invalid/creator")
        cls = XiaoHongShuVideo if platform == "xiaohongshu" else KSVideo
        uploader = cls(TITLE, str(self.video), list(TAGS), 0, "unused.json", desc=BODY)
        uploader.draft_only = True
        uploader.draft_upload_timeout = timeout
        uploader.draft_expected = {"platform_fields": {"caption": CAPTION},
                                   "video_sha256": hashlib.sha256(self.video.read_bytes()).hexdigest()}
        session = SimpleNamespace(page=page, platform=platform, closed=False, expected=uploader.draft_expected)
        observer = FixtureObserver(session)
        setattr(session, "xhs_observer" if platform == "xiaohongshu" else "ks_observer", observer)
        page._sau_draft_evidence = session
        return context, page, uploader, observer, ready

    async def invoke(self, platform, page, uploader):
        if platform == "xiaohongshu":
            await uploader.upload_video_content(page)
        else:
            await uploader._upload_draft_content(page)

    async def wait_content(self, platform, page):
        if platform == "xiaohongshu":
            await page.wait_for_function("tags=>document.querySelectorAll('a[data-topic]').length===tags.length", arg=TAGS,
                                         timeout=15000)
            self.assertEqual(await page.locator('input[placeholder="填写标题"]').input_value(), TITLE)
            self.assertEqual(await page.evaluate("chosen", isolated_context=False), TAGS)
        else:
            await page.wait_for_function("caption=>document.querySelector('[contenteditable=true]').innerText===caption", arg=CAPTION,
                                         timeout=15000)
        await page.wait_for_timeout(100)

    async def selected_count(self, page):
        return await page.evaluate("Number(document.body.dataset.videoSubmissions||0)")

    async def test_native_progress_can_extend_wait_but_never_replaces_completion_proof(self):
        context,page,uploader,observer,ready=await self.setup_adapter('kuaishou')
        await page.locator('#state').evaluate('el=>el.style.display="block"')
        start=asyncio.get_running_loop().time()
        async def progress():
            return min(95,(asyncio.get_running_loop().time()-start)*100)
        observer.upload_progress=progress
        async def complete():
            await asyncio.sleep(.65)
            await page.evaluate('completeUpload',ready,isolated_context=False)
        task=asyncio.create_task(complete())
        try:
            with patch.object(draft_only,'assert_draft_safe',AsyncMock()):
                self.assertTrue(await wait_xhs_ks_upload_complete(page,'kuaishou',timeout=.4,poll_interval=.02))
            await task
            observer.complete=False
            await page.locator('body').evaluate('e=>delete e.dataset.complete')
            observer.upload_progress=AsyncMock(return_value=95)
            with patch.object(draft_only,'assert_draft_safe',AsyncMock()),self.assertRaises(DraftInterrupted):
                await wait_xhs_ks_upload_complete(page,'kuaishou',timeout=.15,poll_interval=.02)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True);await context.close()

    async def test_both_platforms_fill_complete_content_while_upload_pending_and_save_after_join(self):
        for platform in ("xiaohongshu", "kuaishou"):
            with self.subTest(platform=platform):
                context, page, uploader, observer, ready = await self.setup_adapter(platform)
                save = AsyncMock()
                checkpoint = AsyncMock()
                task = None
                try:
                    with patch.object(draft_only, "save_draft", save), patch.object(draft_only, "checkpoint_after_upload", checkpoint):
                        task = asyncio.create_task(self.invoke(platform, page, uploader))
                        await self.wait_content(platform, page)
                        self.assertFalse(task.done())
                        self.assertIsNone(await page.locator("body").get_attribute("data-complete"))
                        save.assert_not_awaited()
                        checkpoint.assert_not_awaited()
                        await page.evaluate("completeUpload", ready, isolated_context=False)
                        await asyncio.wait_for(task, timeout=5)
                    save.assert_awaited_once()
                    checkpoint.assert_awaited_once_with(page)
                    self.assertEqual(await self.selected_count(page), 1)
                    events = await page.evaluate("events", isolated_context=False)
                    completed = next(e["time"] for e in events if e["event"] == "uploaded")
                    self.assertTrue(all(e["time"] < completed for e in events if e["event"] == "content"))
                    self.assertGreater(observer.calls, 0)
                finally:
                    if task is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    await context.close()

    async def test_stalled_upload_keeps_filled_fields_never_saves_or_reimports(self):
        for platform in ("xiaohongshu", "kuaishou"):
            with self.subTest(platform=platform):
                context, page, uploader, _, _ = await self.setup_adapter(platform, timeout=1.8)
                save = AsyncMock()
                try:
                    with patch.object(draft_only, "save_draft", save):
                        with self.assertRaisesRegex(DraftInterrupted, "超时"):
                            await self.invoke(platform, page, uploader)
                    save.assert_not_awaited()
                    self.assertEqual(await self.selected_count(page), 1)
                    self.assertGreater(len(await page.evaluate("events.filter(e=>e.event==='content')", isolated_context=False)), 0)
                finally:
                    await context.close()

    async def test_platform_upload_failure_stops_parallel_branch_without_final_action(self):
        for platform in ("xiaohongshu", "kuaishou"):
            with self.subTest(platform=platform):
                context, page, uploader, _, _ = await self.setup_adapter(platform)
                save = AsyncMock()
                task = None
                try:
                    with patch.object(draft_only, "save_draft", save):
                        task = asyncio.create_task(self.invoke(platform, page, uploader))
                        await self.wait_content(platform, page)
                        await page.locator("#state").evaluate("e=>e.innerHTML='<div role=alert>上传失败</div>'")
                        with self.assertRaisesRegex(DraftInterrupted, "失败"):
                            await asyncio.wait_for(task, 5)
                    save.assert_not_awaited()
                    self.assertEqual(await self.selected_count(page), 1)
                    self.assertFalse(page.is_closed())
                finally:
                    if task is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    await context.close()

    async def test_old_completed_preview_and_wrong_material_never_release_upload_gate(self):
        for platform in ("xiaohongshu", "kuaishou"):
            for wrong in ("missing_completion", "wrong_hash"):
                with self.subTest(platform=platform, wrong=wrong):
                    context, page, _, observer, ready = await self.setup_adapter(platform)
                    try:
                        await page.locator("#state").evaluate("(e,html)=>{e.style.display='block';e.innerHTML=html}", ready)
                        if wrong == "wrong_hash":
                            observer.sha = "0" * 64
                            await page.locator("body").evaluate("e=>e.dataset.complete='true'")
                        with self.assertRaisesRegex(DraftInterrupted, "超时"):
                            await wait_xhs_ks_upload_complete(page, platform, timeout=.12, poll_interval=.02)
                        self.assertEqual(await self.selected_count(page), 0)
                    finally:
                        await context.close()

    async def test_current_completion_still_waits_for_platform_processing(self):
        for platform in ("xiaohongshu", "kuaishou"):
            with self.subTest(platform=platform):
                context, page, _, _, ready = await self.setup_adapter(platform)
                try:
                    await page.evaluate("completeUpload", ready + "<div id='processing'>转码中</div>", isolated_context=False)
                    await page.locator("#state").evaluate("e=>e.style.display='block'")
                    task = asyncio.create_task(wait_xhs_ks_upload_complete(page, platform, timeout=2, poll_interval=.02))
                    await page.wait_for_timeout(100)
                    self.assertFalse(task.done())
                    await page.locator("#processing").evaluate("e=>e.remove()")
                    self.assertTrue(await task)
                finally:
                    await context.close()

    async def test_session_replaced_during_observation_stops_without_accepting_old_completion(self):
        context, page, _, observer, _ = await self.setup_adapter("kuaishou")
        try:
            original_drain = observer.drain
            async def replace_session():
                await original_drain()
                page._sau_draft_evidence = SimpleNamespace(closed=False)
            observer.drain = replace_session
            with self.assertRaisesRegex(DraftInterrupted, "变化"):
                await wait_xhs_ks_upload_complete(page, "kuaishou", timeout=.2, poll_interval=.01)
        finally:
            await context.close()

    async def test_busy_words_in_user_caption_do_not_hide_current_completed_upload(self):
        for platform in ("xiaohongshu", "kuaishou"):
            with self.subTest(platform=platform):
                context, page, _, _, ready = await self.setup_adapter(platform)
                try:
                    editor = page.locator('[contenteditable="true"]').first
                    await editor.fill("示例正文：上传中、转码中、处理中")
                    await page.evaluate("completeUpload", ready, isolated_context=False)
                    await page.locator("#state").evaluate("e=>e.style.display='block'")
                    self.assertTrue(await wait_xhs_ks_upload_complete(page, platform, timeout=1))
                finally:
                    await context.close()

    async def test_slow_observer_is_bounded_by_upload_deadline(self):
        context, page, _, observer, _ = await self.setup_adapter("xiaohongshu")
        try:
            async def blocked():
                await asyncio.Event().wait()
            observer.drain = blocked
            with self.assertRaisesRegex(DraftInterrupted, "超时"):
                await asyncio.wait_for(wait_xhs_ks_upload_complete(page, "xiaohongshu", timeout=.1), 1)
        finally:
            await context.close()

    async def test_same_session_new_task_nonce_does_not_accept_prior_completion(self):
        context, page, _, observer, ready = await self.setup_adapter("xiaohongshu")
        observer.session.expected["receipt_nonce"] = "original-task"
        try:
            await page.evaluate("completeUpload", ready, isolated_context=False)
            await page.locator("#state").evaluate("e=>e.style.display='block'")
            original_drain = observer.drain
            async def change_nonce():
                await original_drain()
                observer.session.expected["receipt_nonce"] = "changed-task"
            observer.drain = change_nonce
            with self.assertRaisesRegex(DraftInterrupted, "变化"):
                await wait_xhs_ks_upload_complete(page, "xiaohongshu", timeout=1)
        finally:
            await context.close()

    async def test_delayed_cover_control_waits_for_actual_mount_without_restarting_upload(self):
        page = await self.browser.new_page()
        await page.set_content(COVER_FIXTURE)
        await page.locator(".cover-plugin-preview").evaluate("e=>e.style.display='none'")
        await page.evaluate("setTimeout(()=>document.querySelector('.cover-plugin-preview').style.display='',150)")
        modal = await open_xhs_cover_editor(page, timeout=2000, guard=AsyncMock())
        self.assertTrue(await modal.is_visible())
        self.assertEqual(await page.evaluate("counts.entry", isolated_context=False), 1)
        self.assertEqual(await page.evaluate("counts.complete+counts.other", isolated_context=False), 0)

    async def test_kuaishou_delayed_cover_controls_use_current_page_after_mount(self):
        page = await self.browser.new_page()
        await page.set_content('''<div id="cover" style="display:none"><div><span>封面设置</span></div>
          <div><div onclick="document.querySelector('.ant-modal').style.display='block'">当前封面</div></div></div>
          <div role="document" class="ant-modal" style="display:none"><button>上传封面</button>
          <input type="file" accept="image/png"><button onclick="this.parentNode.style.display='none'">确认</button></div>''')
        observer = SimpleNamespace(ensure_ready=AsyncMock(return_value=True),
                                   ensure_cover_ready=AsyncMock(return_value=True))
        page._sau_draft_evidence = SimpleNamespace(ks_observer=observer)
        cover = Path(self.temp.name) / "synthetic.png"
        cover.write_bytes(b"fixture native image")
        uploader = KSVideo(TITLE, str(self.video), [], 0, "unused.json", thumbnail_path=str(cover))
        uploader.draft_only, uploader.draft_upload_timeout = True, 2
        await page.evaluate("setTimeout(()=>document.getElementById('cover').style.display='',150)")
        await uploader.set_thumbnail(page)
        self.assertFalse(await page.locator('.ant-modal').is_visible())
        self.assertEqual(await page.locator('input[type=file]').evaluate('e=>e.files[0].name'), cover.name)
        observer.ensure_cover_ready.assert_awaited_once()
