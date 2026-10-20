"""Native SDK/browser fixtures for concurrent Douyin preparation; no account."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from tests.test_dy_evidence import FIXTURE as SDK_FIXTURE
from uploader.douyin_uploader.main import DouYinVideo
from uploader.dy_evidence import install_dy_observer
from uploader.dy_upload_ready import dismiss_dy_video_preview_tip, wait_dy_video_uploaded
from uploader.draft_session import DraftInterrupted


EDITOR_URL = 'https://creator.douyin.com/creator-micro/content/post/video?fixture=1'
FIELDS = '''<h2>基础信息</h2>
<input id="title" placeholder="填写作品标题，为作品获得更多流量">
<div id="caption" class="zone-container" contenteditable="true" style="min-height:80px"></div>
<aside id="unrelated">封面处理中</aside>
<div id="tip" hidden><span>视频预览功能</span><p>视频素材预览说明</p>
<button onclick="this.parentElement.remove()">我知道了</button></div>
'''
FIXTURE = SDK_FIXTURE.replace('<input id="video"', FIELDS + '<input id="video"') + '''<script>
document.querySelector('#video').addEventListener('change',event=>{if(event.target.files.length===1)fixture.begin();});
fixture.finish=()=>{const item=fixture.resolvers[0];fixture.editor.state.uploadPercent=99;
 item.resolve({file:item.file,vid:'v_fixture_current_video'});};
document.querySelector('#title').addEventListener('input',()=>{
 fixture.filledBeforeComplete=fixture.editor.state.uploadStatus===1;
});
</script>'''


class DouyinUploadPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.directory = tempfile.TemporaryDirectory(prefix='dy-pipeline-fixture-')
        self.video = Path(self.directory.name) / 'source.mp4'
        self.video_bytes = b'local-douyin-pipeline-fixture' * 17
        self.video.write_bytes(self.video_bytes)
        self.sha = hashlib.sha256(self.video_bytes).hexdigest()
        self.playwright = await async_playwright().start()
        bundled = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        launch = {'executable_path': str(bundled)} if bundled.exists() else {'channel': 'chrome'}
        self.browser = await self.playwright.chromium.launch(headless=True, timeout=90000, **launch)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.requests = []
        async def serve(route):
            self.requests.append(route.request.url)
            await route.fulfill(content_type='text/html', body=FIXTURE)
        await self.context.route('**/*', serve)
        await self.page.goto(EDITOR_URL)
        self.session = SimpleNamespace(page=self.page, platform='douyin', closed=False,
            expected={'job_id':'dy-pipeline-fixture', 'receipt_nonce':'dy-pipeline-receipt', 'video_sha256':self.sha},
            assets={'video':{'kind':'video','name':self.video.name,'size':len(self.video_bytes),
                             'sha256':self.sha, 'type':'video/mp4'}})
        self.page._sau_draft_evidence = self.session
        self.observer = await install_dy_observer(self.session)
        self.gate = AsyncMock()
        self.gate_patch = patch('uploader.draft_only.assert_draft_safe', self.gate)
        self.gate_patch.start()

    async def asyncTearDown(self):
        self.gate_patch.stop()
        await self.observer.close()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.directory.cleanup()

    async def js(self, script):
        return await self.page.evaluate(script, isolated_context=False)

    async def select(self):
        self.assertTrue(await self.observer.ensure_ready(timeout=2))
        await self.page.locator('#video').set_input_files(str(self.video))

    async def finish(self):
        await self.js('fixture.finish();true')
        await self.observer.drain(timeout=2)

    def uploader(self):
        uploader = DouYinVideo('完整标题', str(self.video), ['话题一', '话题二'], 0, 'fixture-account-unused',
                              desc='第一段正文\n第二段正文')
        uploader.draft_only = True
        uploader.draft_upload_timeout = 2
        return uploader

    async def test_completed_sdk_and_exact_file_accept_99_without_old_ready_markup(self):
        await self.select()
        await self.finish()
        with patch('uploader.draft_progress.operation') as progress:
            self.assertTrue(await wait_dy_video_uploaded(self.page, timeout=1, poll_interval=.01))
        self.assertEqual(await self.js('fixture.editor.state.uploadPercent'), 99)
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)
        progress.assert_called_with('upload', 'transfer', percent=99)
        self.assertEqual(await self.page.locator('[class^="long-card"]').count(), 0)

    async def test_editor_and_percent_100_without_sdk_completion_are_not_enough(self):
        await self.select()
        await self.js('fixture.editor.state.uploadPercent=100;true')
        with self.assertRaisesRegex(DraftInterrupted, '尚未取得本次视频'):
            await wait_dy_video_uploaded(self.page, timeout=.15, poll_interval=.01)
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)

    async def test_completed_receipt_cannot_match_a_different_current_native_file(self):
        await self.select()
        await self.finish()
        await self.js('fixture.editor.state.file={rawFile:new File(["another"],"source.mp4")};true')
        with self.assertRaises(DraftInterrupted):
            await wait_dy_video_uploaded(self.page, timeout=.15, poll_interval=.01)
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)

    async def test_sdk_result_without_an_accepted_sha_link_never_completes(self):
        await self.select()
        await self.finish()
        self.observer.records.clear()
        with self.assertRaises(DraftInterrupted):
            await wait_dy_video_uploaded(self.page, timeout=.15, poll_interval=.01)

    async def test_video_replacement_invalidates_the_completion_without_reupload(self):
        await self.select()
        await self.finish()
        await self.page.locator('#video').set_input_files([])
        with self.assertRaisesRegex(DraftInterrupted, '上传失败或视频已被替换'):
            await wait_dy_video_uploaded(self.page, timeout=.3, poll_interval=.01)
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)

    async def test_sdk_rejection_stops_the_wait_without_any_retry(self):
        await self.select()
        await self.js('fixture.rejectAt(0,new Error("fixture SDK failure"));true')
        await self.observer.drain(timeout=.1)
        with self.assertRaisesRegex(DraftInterrupted, '上传失败或视频已被替换'):
            await wait_dy_video_uploaded(self.page, timeout=.3, poll_interval=.01)
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)

    async def test_changed_session_stops_observation(self):
        await self.select()
        task = asyncio.create_task(wait_dy_video_uploaded(self.page, timeout=2, poll_interval=.01))
        await asyncio.sleep(.02)
        self.page._sau_draft_evidence = SimpleNamespace(closed=False)
        with self.assertRaisesRegex(DraftInterrupted, '观察已失效'):
            await task
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)
        self.page._sau_draft_evidence = self.session

    async def test_normal_tip_closes_and_an_unrelated_button_is_preserved(self):
        await self.js('document.querySelector("#tip").hidden=false;'
                      'const b=document.createElement("button");b.textContent="我知道了";'
                      'b.id="other-tip";document.body.append(b);true')
        self.assertTrue(await dismiss_dy_video_preview_tip(self.page))
        self.assertEqual(await self.page.locator('#tip').count(), 0)
        self.assertEqual(await self.page.locator('#other-tip').count(), 1)

    async def test_tip_absent_does_not_click_any_other_understood_button(self):
        await self.js('document.querySelector("#tip").remove();'
                      'const b=document.createElement("button");b.textContent="我知道了";'
                      'b.onclick=()=>window.unexpectedClick=true;document.body.append(b);true')
        self.assertFalse(await dismiss_dy_video_preview_tip(self.page))
        self.assertFalse(await self.js('window.unexpectedClick===true'))

    async def test_duplicate_preview_tips_fail_closed(self):
        await self.js('const tip=document.querySelector("#tip");tip.hidden=false;'
                      'document.body.append(tip.cloneNode(true));true')
        with self.assertRaisesRegex(RuntimeError, '提示不唯一'):
            await dismiss_dy_video_preview_tip(self.page)
        self.assertEqual(await self.page.locator('#tip').count(), 2)

    async def test_actual_adapter_fills_fields_during_sdk_transfer_then_saves(self):
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')
        uploader = self.uploader()
        saved = AsyncMock()
        checkpoint = AsyncMock()
        task = None
        try:
            with patch('uploader.draft_only.save_draft', saved), \
                 patch('uploader.draft_only.checkpoint_after_upload', checkpoint):
                task = asyncio.create_task(uploader._upload_draft_content(self.page))
                await self.page.locator('#title').wait_for(state='visible')
                deadline = asyncio.get_running_loop().time() + 10
                while not await self.js('fixture.filledBeforeComplete===true'):
                    if task.done():
                        task.result()
                    if asyncio.get_running_loop().time() >= deadline:
                        self.fail('标题没有在 SDK 上传结束前填写')
                    await asyncio.sleep(.01)
                while await self.page.locator('#caption').inner_text() != '第一段正文\n第二段正文\n#话题一 #话题二':
                    if task.done():
                        task.result()
                    if asyncio.get_running_loop().time() >= deadline:
                        self.fail('正文与话题没有在 SDK 上传结束前填写')
                    await asyncio.sleep(.01)
                self.assertEqual(await self.js('fixture.editor.state.uploadStatus'), 1)
                saved.assert_not_awaited()
                await self.finish()
                await task
            saved.assert_awaited_once()
            checkpoint.assert_awaited_once()
            self.assertEqual(await self.js('fixture.sdkCalls'), 1)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_field_failure_cancels_wait_and_never_saves_or_reselects(self):
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')
        uploader = self.uploader()
        uploader.fill_title_and_description = AsyncMock(side_effect=RuntimeError('fixture field failure'))
        saved = AsyncMock()
        with patch('uploader.draft_only.save_draft', saved):
            with self.assertRaisesRegex(RuntimeError, 'fixture field failure'):
                await uploader._upload_draft_content(self.page)
        saved.assert_not_awaited()
        self.assertEqual(await self.js('fixture.sdkCalls'), 1)
        pending = [task for task in asyncio.all_tasks() if task.get_name() == 'douyin-upload-observation']
        self.assertEqual(pending, [])

    async def test_native_completion_reset_is_detected_before_save(self):
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')
        uploader = self.uploader()
        saved = AsyncMock()
        task = None
        try:
            with patch('uploader.draft_only.save_draft', saved), \
                 patch('uploader.draft_only.checkpoint_after_upload', AsyncMock()):
                task = asyncio.create_task(uploader._upload_draft_content(self.page))
                deadline = asyncio.get_running_loop().time() + 10
                while await self.page.locator('#caption').inner_text() != '第一段正文\n第二段正文\n#话题一 #话题二':
                    if task.done():
                        task.result()
                    if asyncio.get_running_loop().time() >= deadline:
                        self.fail('fixture content was not prepared')
                    await asyncio.sleep(.01)
                # Wait for the content lane's early readback to finish, then
                # model an upload-completion callback resetting the form.
                await asyncio.sleep(.03)
                await self.js('fixture.finish();document.querySelector("#caption").innerText="";true')
                with self.assertRaisesRegex(RuntimeError, '上传完成后的标题或正文回读不一致'):
                    await task
            saved.assert_not_awaited()
            self.assertEqual(await self.js('fixture.sdkCalls'), 1)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cover_preparation_runs_in_the_single_editor_lane_during_transfer(self):
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')
        uploader = self.uploader()
        uploader.thumbnail_portrait_path = 'fixture-cover-unused'
        cover_started = asyncio.Event()
        async def prepare_cover(page):
            self.assertEqual(await page.locator('#caption').inner_text(), '第一段正文\n第二段正文\n#话题一 #话题二')
            self.assertEqual(await self.js('fixture.editor.state.uploadStatus'), 1)
            cover_started.set()
        uploader.set_thumbnail = prepare_cover
        saved = AsyncMock()
        task = None
        try:
            with patch('uploader.draft_only.save_draft', saved), \
                 patch('uploader.draft_only.checkpoint_after_upload', AsyncMock()):
                task = asyncio.create_task(uploader._upload_draft_content(self.page))
                await asyncio.wait_for(cover_started.wait(), timeout=1.5)
                saved.assert_not_awaited()
                await self.finish()
                await task
            saved.assert_awaited_once()
            self.assertEqual(await self.js('fixture.sdkCalls'), 1)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
