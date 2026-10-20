"""Isolated browser fixtures: no real platform requests, credentials or publishing.

The fixtures prove adapter control flow and field preservation only. They do not
prove that a real platform accepted or persisted a draft.
Run: .venv/bin/python -m unittest tests.test_draft_upload_safety
"""
import asyncio
import hashlib
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader import draft_only
from uploader.douyin_uploader.main import DouYinVideo
from uploader.ks_uploader.main import KSVideo
from uploader.draft_session import DraftInterrupted
from uploader.tencent_uploader.main import TencentBaseUploader, TencentVideo
from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
from tests.test_xhs_editor import EDITOR_FIXTURE


TITLE = "完整标题"
BODY = "第一行正文\n第二行正文"
TAGS = ["话题一", "话题二", "话题三", "话题四"]
CAPTION = "\n".join([TITLE, BODY, " ".join("#" + tag for tag in TAGS)])


def fixture(platform, outcome="success"):
    selector_class = {"douyin": "upload-btn-input", "xiaohongshu": "upload-input"}.get(platform, "video-file")
    ready = {
        "douyin": '<div class="long-card"><div>重新上传</div></div>',
        # Official current high-definition Preview has no upload-success text.
        "xiaohongshu": '<div class="cover-container"><div class="preview-new">检测为高清视频。清晰的画面能极大提升观看体验</div></div>',
        "kuaishou": '<div>视频上传成功</div>',
        "tencent": '<div class="media-status-content"><div>视频上传成功</div></div>',
    }[platform]
    if outcome == "failure":
        ready = '<div role="alert">上传失败</div>'
    if outcome == "risk":
        ready = '<div role="dialog">安全验证：请输入验证码</div>'
    if outcome == "pending":
        ready = '<div>上传中</div>'
    fields = {
        "douyin": '<input placeholder="填写作品标题"><div class="zone-container" contenteditable="true"></div>',
        # Reuse the native keyboard/Range/exact topic picker fixture. A popup
        # that merely hides without creating a topic no longer models XHS.
        "xiaohongshu": '',
        "kuaishou": '<span>描述</span><div class="caption" contenteditable="true"></div>',
        "tencent": '<button class="weui-desktop-btn">发表视频</button><div class="input-editor" contenteditable="true"></div><input placeholder="填写短标题有机会获得更多流量">',
    }[platform]
    topic_fixture = EDITOR_FIXTURE.removeprefix('<!doctype html>') if platform == "xiaohongshu" else ""
    return f'''<!doctype html><meta charset="utf-8"><style>[contenteditable=true]{{min-height:24px;min-width:300px;white-space:pre-wrap}}</style>
      <div class="upload-content"><input class="{selector_class}" type="file" accept="video/mp4"
        onchange="document.body.dataset.videoSubmissions=String(Number(document.body.dataset.videoSubmissions||0)+1);document.querySelector('#state').style.display='block'"></div>
      <div id="state" style="display:none">{ready}</div>{fields}{topic_fixture}
      <button>保存草稿</button>'''


class DraftUploadSafetyTests(unittest.TestCase):
    def run_scenario(self, scenario):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel="chrome")
                try:
                    with tempfile.TemporaryDirectory(prefix="sau-draft-fixture-") as directory:
                        video = Path(directory) / "synthetic.mp4"
                        video.write_bytes(b"local fixture; not a real video or account upload")
                        await scenario(browser, str(video))
                finally:
                    await browser.close()
        asyncio.run(run())

    async def setup_adapter(self, browser, video, platform, outcome="success", iframe=False):
        context = await browser.new_context()
        async def route(request):
            html = fixture(platform, outcome)
            if iframe and not request.request.url.endswith("/video-frame"):
                # Wrong image slot is deliberately first in the parent frame.
                html = '<input type="file" accept="image/png"><iframe src="https://fixture.invalid/video-frame"></iframe>'
            await request.fulfill(content_type="text/html", body=html)
        await context.route("**/*", route)
        page = await context.new_page()
        classes = {"douyin": DouYinVideo, "kuaishou": KSVideo, "tencent": TencentVideo, "xiaohongshu": XiaoHongShuVideo}
        uploader = classes[platform](TITLE, video, list(TAGS), 0, "unused.json", desc=BODY)
        uploader.draft_only = True
        uploader.draft_upload_timeout = .1 if outcome == "pending" else 60
        caption = "\n".join([BODY, " ".join("#" + tag for tag in TAGS)]) if platform == "douyin" else CAPTION
        uploader.draft_expected = {"platform_fields": {"caption": caption}, "account": {"uid": "fixture-account"},
                                  "video_sha256": hashlib.sha256(Path(video).read_bytes()).hexdigest()}
        session = SimpleNamespace(page=page,platform=platform,closed=False,expected=uploader.draft_expected,
                                  account=uploader.draft_expected['account'],fixture_complete=False)
        page._sau_draft_evidence=session
        async def selected_count():
            return sum([await frame.locator('input[type="file"][accept*="video"]').evaluate_all(
                'inputs=>inputs.reduce((count,input)=>count+input.files.length,0)') for frame in page.frames])
        async def observe_fixture_completion(*args,**kwargs):
            # This DOM-only fixture explicitly models the observer contract.
            # Actual SDK/hash/task correlation remains exercised by the native
            # per-platform suites. The form alone does not release this proof.
            session.fixture_complete=outcome=='success' and await selected_count()==1
        def upload_links():
            return [{'kind':'video','complete':True,'sha256':session.expected['video_sha256'],
                     'ids':['fixture-current-native-video']}] if session.fixture_complete else []
        if platform in ('tencent', 'xiaohongshu'):
            # These browser fixtures prove adapter control flow/full text, not
            # SDK/CDN evidence. Explicitly supply the existing evidence session
            # contract. Never weaken a production gate for fixture URLs.
            if platform == 'tencent':
                # The actual fail-closed CDN capture/origin/owner gate is tested
                # independently in tests.test_tc_cdn.CaptureBrowserFixture.
                session.capture_tc_cdn_snapshot = AsyncMock(return_value=True)
                if outcome in ('success','failure','risk','pending'):
                    # This fixture has no official Vue composer. Explicitly
                    # model its native input state; test_tc_editor separately
                    # exercises the real read-only helper and strict gate.
                    async def material_state(actual_page):
                        selected=await selected_count()
                        done=selected if outcome=='success' else 0
                        return dict(recognized=True,count=selected,video_count=selected,done_count=done,
                                    all_uploaded=done>0,all_cover_cropped=done>0,can_save_draft=done>0)
                    session.read_tc_material_state=AsyncMock(side_effect=material_state)
            else:
                # Current SDK capture and unavailable/foreign/stale task gates
                # are covered independently in test_xhs_evidence/test_xhs_editor.
                session.xhs_observer = SimpleNamespace(session=session, page=page,
                    closed=False, ensure_ready=AsyncMock(return_value=True),drain=observe_fixture_completion,
                    upload_links=upload_links)
        elif platform == 'douyin':
            # The committed React uploader/SDK observer is tested independently
            # in test_dy_evidence; this DOM-only fixture supplies its ready contract.
            async def dy_state(_expression):
                await observe_fixture_completion()
                return dict(scoped=True,ready=session.fixture_complete,invalid=False,percent=99 if session.fixture_complete else 0,
                            vid='fixture-current-native-video' if session.fixture_complete else None)
            session.dy_observer=SimpleNamespace(session=session,page=page,closed=False,scope={},latest_invocation='fixture-import',
                video_sha256=session.expected['video_sha256'],transport=SimpleNamespace(evaluate=dy_state),
                upload_links=upload_links,ensure_ready=AsyncMock(return_value=True))
        elif platform == 'kuaishou':
            # Native SDK evidence is tested independently in test_ks_evidence;
            # keep this fixture focused on adapter routing and form preservation.
            session.ks_observer=SimpleNamespace(session=session,page=page,closed=False,drain=observe_fixture_completion,
                upload_links=upload_links,ensure_ready=AsyncMock(return_value=True))
        return context, page, uploader

    async def invoke(self, platform, page, uploader):
        if platform == "xiaohongshu":
            await uploader.upload_video_content(page)
        elif platform == "tencent":
            await page.goto("https://fixture.invalid/tencent")
            # Ensure child-frame markup mounted before the adapter selects an input.
            if len(page.frames) > 1:
                await page.frames[1].locator('input[accept*="video"]').wait_for(state="attached")
            reader=getattr(page._sau_draft_evidence,'read_tc_material_state',None)
            async def tc_content():
                await uploader.upload_video_file(page, uploader.file_path)
                await uploader.wait_for_upload_complete(page)
                await uploader.prepare_video_for_publish(page)
                await uploader.set_short_title(page, TITLE, "这是完整短标题")
                await uploader.submit_publish(page)
            if reader is not None:
                # This generic form deliberately has no official Vue material
                # owner. Native File binding itself is tested in tc_pipeline.
                with patch('uploader.tc_editor.read_tc_material_state',reader), \
                     patch('uploader.tc_pipeline.capture_tc_upload',new=AsyncMock(return_value=None)):
                    await tc_content()
            else:
                await tc_content()
        elif platform == 'douyin':
            async def locate_fixture_input(actual_page):
                matches = []
                for frame in actual_page.frames:
                    matches.extend(await frame.locator('input.upload-btn-input[type="file"]').all())
                if len(matches) != 1:
                    raise AssertionError('isolated Douyin fixture must expose one native video input')
                return matches[0]
            # React uploader readiness and SDK wrapping are covered in
            # test_dy_upload_ready/test_dy_evidence; this fixture exercises the
            # subsequent normal file choice, visible wait gate and form flow.
            with patch('uploader.dy_upload_ready.wait_dy_empty_video_input', locate_fixture_input):
                await uploader._upload_draft_content(page)
        else:
            await uploader._upload_draft_content(page)

    async def video_submissions(self, page):
        return sum([await frame.evaluate("Number(document.body?.dataset.videoSubmissions || 0)") for frame in page.frames])

    def test_tencent_single_video_material_gate_keeps_positive_content_flow(self):
        async def scenario(browser,video):
            context,page,uploader=await self.setup_adapter(browser,video,'tencent',iframe=True)
            save=AsyncMock()
            try:
                with patch.object(draft_only,'save_draft',save):
                    await self.invoke('tencent',page,uploader)
                self.assertEqual(await self.video_submissions(page),1)
                save.assert_awaited_once()
                self.assertIs(save.await_args.kwargs['expected'],uploader.draft_expected)
                text='\n'.join([await frame.locator('body').inner_text() for frame in page.frames])
                self.assertIn(TITLE,text)
                self.assertIn(BODY,text)
                for tag in TAGS:
                    self.assertIn('#'+tag,text)
                page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()
                self.assertEqual(page._sau_draft_evidence.read_tc_material_state.await_count,2)
            finally:
                await context.close()
        self.run_scenario(scenario)

    def test_each_adapter_submits_video_once_and_preserves_all_content(self):
        async def scenario(browser, video):
            for platform in ("douyin", "xiaohongshu", "kuaishou", "tencent"):
                with self.subTest(platform=platform):
                    context, page, uploader = await self.setup_adapter(browser, video, platform, iframe=True)
                    save = AsyncMock()
                    try:
                        with patch.object(draft_only, "save_draft", save):
                            await self.invoke(platform, page, uploader)
                        self.assertEqual(await self.video_submissions(page), 1)
                        save.assert_awaited_once()
                        self.assertIs(save.await_args.kwargs["expected"], uploader.draft_expected)
                        scopes = page.frames
                        text = "\n".join([await frame.locator("body").inner_text() for frame in scopes])
                        if platform == 'xiaohongshu':
                            field = next(frame for frame in scopes if frame.url.endswith('/video-frame'))
                            editor = await field.locator('#editor').element_handle()
                            expected = BODY + '\n' + ' '.join('#' + tag for tag in TAGS)
                            encoded = BODY + '\n' + ' '.join('#' + tag + '[话题]#' for tag in TAGS)
                            self.assertEqual(await uploader._draft_topic_snapshot(editor),
                                             {'plain': expected, 'serialized': encoded, 'topics': TAGS})
                            page._sau_draft_evidence.xhs_observer.ensure_ready.assert_awaited_once_with(timeout=5)
                        else:
                            self.assertIn(BODY, text)
                        if platform == 'tencent':
                            page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()
                        for tag in TAGS:
                            self.assertIn("#" + tag, text)
                        if platform in {"kuaishou", "tencent"}:
                            self.assertIn(TITLE, text)
                        else:
                            fields = [frame for frame in scopes if await frame.locator('input[placeholder*="填写"]').count()]
                            field = fields[0]
                            self.assertEqual(await field.locator('input[placeholder*="填写"]').first.input_value(), TITLE)
                    finally:
                        await context.close()
        self.run_scenario(scenario)

    def test_upload_failure_or_verification_stops_without_retry_or_save(self):
        async def scenario(browser, video):
            for platform in ("douyin", "xiaohongshu", "kuaishou", "tencent"):
                for outcome in ("failure", "risk"):
                    with self.subTest(platform=platform, outcome=outcome):
                        context, page, uploader = await self.setup_adapter(browser, video, platform, outcome)
                        save = AsyncMock()
                        try:
                            with patch.object(draft_only, "save_draft", save):
                                with self.assertRaises(RuntimeError):
                                    await asyncio.wait_for(self.invoke(platform, page, uploader), timeout=30)
                            self.assertEqual(await self.video_submissions(page), 1)
                            save.assert_not_awaited()
                        finally:
                            await context.close()
        self.run_scenario(scenario)

    def test_visible_editors_do_not_prove_upload_complete_and_wait_is_bounded(self):
        async def scenario(browser, video):
            for platform in ("douyin", "xiaohongshu", "kuaishou", "tencent"):
                with self.subTest(platform=platform):
                    context, page, uploader = await self.setup_adapter(browser, video, platform, "pending")
                    save = AsyncMock()
                    waits = []
                    if platform=='douyin':
                        from uploader.dy_upload_ready import wait_dy_video_uploaded as wait_upload
                        target='uploader.dy_upload_ready.wait_dy_video_uploaded'
                    elif platform in ('xiaohongshu','kuaishou'):
                        from uploader.xhs_ks_upload_ready import wait_xhs_ks_upload_complete as wait_upload
                        target='uploader.xhs_ks_upload_ready.wait_xhs_ks_upload_complete'
                    else:
                        wait_upload=uploader.wait_for_upload_complete
                        target=None
                    async def measured_wait(*args, **kwargs):
                        started = asyncio.get_running_loop().time()
                        try:
                            return await wait_upload(*args, **kwargs)
                        finally:
                            waits.append(asyncio.get_running_loop().time() - started)
                    try:
                        wait_patch=patch(target,new=measured_wait) if target else patch.object(uploader,'wait_for_upload_complete',new=measured_wait)
                        with patch.object(draft_only, "save_draft", save), wait_patch:
                            with self.assertRaises((RuntimeError, TimeoutError)) as failure:
                                await asyncio.wait_for(self.invoke(platform, page, uploader), timeout=30)
                            self.assertTrue(any(text in str(failure.exception) for text in ('超时','完成回执','尚未完成')) or "timeout" in str(failure.exception).lower())
                            self.assertEqual(len(waits), 1)
                            self.assertLess(waits[0], 2)
                        self.assertEqual(await self.video_submissions(page), 1)
                        save.assert_not_awaited()
                    finally:
                        await context.close()
        self.run_scenario(scenario)

    def test_draft_upload_error_handlers_never_resubmit(self):
        async def scenario():
            for cls in (DouYinVideo, KSVideo, TencentVideo, XiaoHongShuVideo):
                uploader = cls(TITLE, "fixture.mp4", [], 0, "unused.json")
                uploader.draft_only = True
                with self.subTest(adapter=cls.__name__):
                    with self.assertRaisesRegex(RuntimeError, "不自动重新上传"):
                        await uploader.handle_upload_error(object())
        asyncio.run(scenario())

    def test_douyin_keeps_both_requested_cover_orientations(self):
        async def scenario(browser, video):
            context = await browser.new_context()
            page = await context.new_page()
            await page.set_content('''<script>window.coverUploads=[];window.coverTab='';</script>
              <h2>基础信息</h2>
              <div class="coverControl-portrait"><div><span>竖封面3:4</span></div>
                <button onclick="document.querySelector('.dy-creator-content-modal').style.display='block'">编辑封面</button></div>
              <div class="coverControl-landscape"><div><span>横封面4:3</span></div>
                <button onclick="document.querySelector('.dy-creator-content-modal').style.display='block'">编辑封面</button></div>
              <div class="dy-creator-content-modal" style="display:none">
                <button onclick="window.coverTab='portrait'">设置竖封面</button>
                <button onclick="window.coverTab='landscape'">设置横封面</button>
                <div class="semi-upload"><span class="semi-upload-drag-area-main-text">上传封面</span>
                  <input type="file" class="semi-upload-hidden-input" accept="image/png"
                    onchange="if(this.files.length){window.coverUploads.push([window.coverTab,this.files[0].name]);document.body.dataset.coverUploads=JSON.stringify(window.coverUploads)}"></div>
                <button onclick="document.querySelector('.semi-upload-hidden-input').value='';document.querySelector('.dy-creator-content-modal').style.display='none'">完成</button>
              </div>''')
            cover = Path(video).with_suffix(".png")
            cover.write_bytes(b"synthetic image input fixture")
            uploader = DouYinVideo(TITLE, video, [], 0, "unused.json", thumbnail_landscape_path=str(cover), thumbnail_portrait_path=str(cover))
            uploader.draft_only = True
            observed=[]
            async def source_ready(role,selector):
                native=page.locator(selector)
                valid=await native.evaluate('(e)=>e.files.length===1&&e.files[0].name',isolated_context=False)==cover.name
                if valid:observed.append(role)
                return valid
            # The native cover/crop proof has independent browser suites. This
            # form fixture keeps actual orientation clicks and file readback.
            observer=SimpleNamespace(available=True,ensure_ready=source_ready,drain=AsyncMock(),
                                     upload_links=lambda:[{'slot':role} for role in observed])
            page._sau_draft_evidence=SimpleNamespace(page=page,closed=False,dy_cover_observer=observer)
            try:
                await uploader.set_thumbnail(page)
                self.assertEqual(await page.evaluate("JSON.parse(document.body.dataset.coverUploads || '[]')"), [["portrait", cover.name], ["landscape", cover.name]])
                self.assertFalse(await page.locator(".dy-creator-content-modal").is_visible())
            finally:
                await context.close()
        self.run_scenario(scenario)

    def test_borrowed_embedded_session_survives_success_and_failure(self):
        async def scenario(browser, video):
            for platform in ("douyin", "xiaohongshu", "kuaishou", "tencent"):
                for outcome in ("success", "failure"):
                    with self.subTest(platform=platform, outcome=outcome):
                        context, page, uploader = await self.setup_adapter(browser, video, platform, outcome)
                        uploader.embedded = SimpleNamespace(browser=browser, context=context, page=page)
                        uploader.validate_upload_args = AsyncMock()
                        save = AsyncMock()
                        try:
                            with ExitStack() as stack:
                                stack.enter_context(patch.object(draft_only,"save_draft",save))
                                if platform=='tencent':
                                    stack.enter_context(patch('uploader.tc_editor.read_tc_material_state',page._sau_draft_evidence.read_tc_material_state))
                                    stack.enter_context(patch('uploader.tc_pipeline.capture_tc_upload',new=AsyncMock(return_value=None)))
                                if outcome == "failure":
                                    with self.assertRaises(RuntimeError):
                                        await uploader.upload(None)
                                else:
                                    await uploader.upload(None)
                                    save.assert_awaited_once()
                            self.assertFalse(page.is_closed())
                            self.assertIn(page, context.pages)
                            self.assertTrue(browser.is_connected())
                        finally:
                            await context.close()
        self.run_scenario(scenario)


class DraftTextGateTests(unittest.TestCase):
    """No browser and no account. Over-limit text must fail before the video file is required."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sau-draft-text-")
        self.root = Path(self.temp.name)
        self.cookie = self.root / "not-an-account.json"
        self.cookie.write_text("{}", encoding="utf-8")
        self.video = self.root / "synthetic.mp4"
        self.video.write_bytes(b"local fixture; not a real video")
        self.missing = self.root / "missing.mp4"

    def tearDown(self):
        self.temp.cleanup()

    def make(self, cls, title, desc, tags, file=None, short=None, draft=True):
        kwargs = dict(title=title, file_path=str(file or self.missing), tags=list(tags),
                      publish_date=0, account_file=str(self.cookie), desc=desc)
        if short is not None:
            kwargs["short_title"] = short
        app = cls(**kwargs)
        app.draft_only = draft
        app.embedded = object()
        return app

    def assert_blocked(self, app):
        original = (app.title, app.desc, list(app.tags or []))
        with self.assertRaises(ValueError) as caught:
            asyncio.run(app.validate_upload_args())
        self.assertIn("尚未上传", str(caught.exception))
        self.assertEqual((app.title, app.desc, list(app.tags or [])), original)
        self.assertFalse(self.missing.exists())

    def test_over_limit_substituted_text_never_reaches_the_video_file(self):
        cases = (
            ("douyin-emoji", DouYinVideo, "😀" * 16, "", []),
            ("douyin-caption", DouYinVideo, "标题", "a" * 998, ["话题"]),
            ("xhs-body", XiaoHongShuVideo, "标题", "a" * 999, ["t"]),
            ("tencent-caption", TencentVideo, "题", "字" * 1000, []),
            ("ks-caption", KSVideo, "题", "a" * 500, []),
            ("ks-tags", KSVideo, "标题", "正文", ["一", "二", "三", "四", "五"]),
        )
        for name, cls, title, desc, tags in cases:
            with self.subTest(name=name):
                self.assert_blocked(self.make(cls, title, desc, tags))
        with self.subTest(name="tencent-short-charset"):
            self.assert_blocked(self.make(TencentVideo, "标题", "正文", [], short="ABCDEF😀"))

    def test_sent_caption_is_checked_and_original_fields_stay_intact(self):
        accepted = self.make(DouYinVideo, "标题", "a" * 1001, [], file=self.video)
        accepted.draft_expected = {"platform_fields": {"caption": "短正文"}}
        asyncio.run(accepted.validate_upload_args())
        self.assertEqual(accepted.title, "标题")
        self.assertEqual(accepted.desc, "a" * 1001)

        blocked = self.make(DouYinVideo, "标题", "短", [])
        blocked.draft_expected = {"platform_fields": {"caption": "a" * 1001}}
        self.assert_blocked(blocked)

        # Xiaohongshu sends the body and topics, not a substituted caption field.
        xhs = self.make(XiaoHongShuVideo, "标题", "a" * 1001, [])
        xhs.draft_expected = {"platform_fields": {"caption": "短"}}
        self.assert_blocked(xhs)

    def test_smaller_runtime_limit_blocks_before_looser_values_can_widen_it(self):
        strict = self.make(KSVideo, "标题", "正文", ["一", "二", "三"])
        strict.draft_capabilities = {"tags_max": 2}
        self.assert_blocked(strict)

        within_default = self.make(KSVideo, "标题", "正文", ["一", "二", "三", "四"], file=self.video)
        within_default.draft_capabilities = {"tags_max": 9, "caption_max": 900, "new_tag_max": 1}
        asyncio.run(within_default.validate_upload_args())

        ignored = self.make(KSVideo, "标题", "正文", ["一", "二", "三", "四"], file=self.video)
        ignored.draft_capabilities = {"tags_max": "2", "caption_max": True}
        asyncio.run(ignored.validate_upload_args())

        ten = ["话题%02d" % index for index in range(10)]
        xhs = self.make(XiaoHongShuVideo, "标题", "正文", ten, file=self.video)
        xhs.draft_capabilities = {"new_tag_max": 30}
        asyncio.run(xhs.validate_upload_args())
        self.assertEqual(xhs.tags, ten)
        too_many = self.make(XiaoHongShuVideo, "标题", "正文", ten + ["话题10"])
        too_many.draft_capabilities = {"new_tag_max": 30}
        self.assert_blocked(too_many)

    def test_public_validate_still_does_not_use_the_draft_gate(self):
        app = self.make(DouYinVideo, "😀" * 16, "", [], file=self.video, draft=False)
        asyncio.run(app.validate_upload_args())
        self.assertEqual(app.title, "😀" * 16)

    def test_draft_content_entry_stops_before_the_page(self):
        class Page:
            def __getattr__(self, name):
                raise AssertionError("page touched: " + name)

        page = Page()
        douyin = self.make(DouYinVideo, "😀" * 16, "", [])
        with self.assertRaises(ValueError) as caught:
            asyncio.run(douyin._upload_draft_content(page))
        self.assertIn("尚未上传", str(caught.exception))
        kuaishou = self.make(KSVideo, "标题", "正文", ["一", "二", "三", "四", "五"])
        with self.assertRaises(ValueError):
            asyncio.run(kuaishou._upload_draft_content(page))
        xhs = self.make(XiaoHongShuVideo, "标题", "a" * 1001, [])
        with self.assertRaises(ValueError):
            asyncio.run(xhs.upload_video_content(page))
        tencent = self.make(TencentVideo, "标题", "正文", [], short="ABCDEF😀")
        with self.assertRaises(ValueError):
            asyncio.run(tencent.upload_video_file(page, str(self.missing)))


class TencentDraftSafetyGateTests(unittest.TestCase):
    """Base uploaders have no file_path. Their draft safety checks still run."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sau-tencent-gate-")
        self.root = Path(self.temp.name)
        self.cookie = self.root / "not-an-account.json"
        self.cookie.write_text("{}", encoding="utf-8")
        self.video = self.root / "synthetic.mp4"
        self.video.write_bytes(b"local fixture; not a real upload")

    def tearDown(self):
        self.temp.cleanup()

    def page(self, risk=False, inputs=1):
        writes = []

        class TextNode:
            async def is_visible(self):
                return True

            async def evaluate(self, script):
                return False

        class Loc:
            def __init__(self, items):
                self.items = items

            async def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

            async def all(self):
                return list(self.items)

            @property
            def first(self):
                return self.items[0]

        class Frame:
            def __init__(self):
                self.url = "https://channels.weixin.qq.com/platform/post/create"
                self.files = [object() for _ in range(inputs)]

            async def evaluate(self, script, isolated_context=False):
                return 0

            def get_by_text(self, pattern):
                if risk and pattern.search("请输入验证码"):
                    return Loc([TextNode()])
                return Loc([])

            def locator(self, selector):
                if "input" in selector and "file" in selector:
                    return Loc(self.files)
                return Loc([])

        frame = Frame()

        class Page:
            def __init__(self):
                self.main_frame = frame
                self.frames = [frame]
                # This suite exercises risk/control safety independently of
                # the CDN snapshot contract tested in tests.test_tc_cdn.
                self._sau_draft_evidence = SimpleNamespace(
                    capture_tc_cdn_snapshot=AsyncMock(return_value=True))

            def is_closed(self):
                return False

        return Page(), writes

    def upload(self, uploader, page, writes):
        async def record_files(_page, _control, paths):
            writes.append(paths)

        with patch("uploader.tencent_uploader.main.set_local_files", record_files):
            asyncio.run(uploader.upload_video_file(page, str(self.video)))

    def base(self):
        uploader = TencentBaseUploader(0, str(self.cookie))
        uploader.draft_only = True
        self.assertNotIn("file_path", vars(uploader))
        return uploader

    def test_base_without_file_path_rejects_a_dangerous_page_with_no_write(self):
        page, writes = self.page(risk=True, inputs=1)
        caught = None
        try:
            self.upload(self.base(), page, writes)
        except DraftInterrupted as exc:
            caught = exc
        self.assertIsNotNone(caught, "危险页面未被草稿安全门拒绝，文件写入: " + repr(writes))
        self.assertEqual(caught.status, "risk_paused")
        self.assertIn("验证码", str(caught))
        self.assertEqual(writes, [])

    def test_base_without_file_path_still_checks_the_page_controls(self):
        page, writes = self.page(risk=False, inputs=2)
        caught = None
        try:
            self.upload(self.base(), page, writes)
        except RuntimeError as exc:
            caught = exc
        self.assertIsNotNone(caught, "页面控件检查没有执行，文件写入: " + repr(writes))
        self.assertIn("多个视频上传组件", str(caught))
        self.assertEqual(writes, [])

    def test_missing_or_untrusted_cdn_snapshot_stops_before_file_write(self):
        for case in ('missing_session', 'untrusted_snapshot'):
            with self.subTest(case=case):
                page, writes = self.page(risk=False, inputs=1)
                if case == 'missing_session':
                    del page._sau_draft_evidence
                else:
                    page._sau_draft_evidence.capture_tc_cdn_snapshot = AsyncMock(return_value=False)
                with self.assertRaisesRegex(RuntimeError, '上传服务器配置未确认'):
                    self.upload(self.base(), page, writes)
                self.assertEqual(writes, [])

    def test_video_over_limit_stops_before_the_ready_file_input(self):
        caption = self.make_video("题", "字" * 1000, "")
        page, writes = self.page(risk=False, inputs=1)
        with self.assertRaises(ValueError) as caught:
            self.upload(caption, page, writes)
        self.assertIn("尚未上传", str(caught.exception))
        self.assertIn("2003/2000", str(caught.exception))
        self.assertEqual(caption.desc, "字" * 1000)
        self.assertEqual(writes, [])

        short = self.make_video("标题", "正文", "ABCDEF😀")
        page, writes = self.page(risk=False, inputs=1)
        with self.assertRaises(ValueError) as caught:
            self.upload(short, page, writes)
        self.assertIn("尚未上传", str(caught.exception))
        self.assertIn("短标题", str(caught.exception))
        self.assertEqual(short.short_title, "ABCDEF😀")
        self.assertEqual(writes, [])

    def make_video(self, title, desc, short_title):
        app = TencentVideo(title, str(self.root / "missing.mp4"), [], 0, str(self.cookie),
                           desc=desc, short_title=short_title)
        app.draft_only = True
        app.embedded = object()
        self.assertIn("file_path", vars(app))
        return app


if __name__ == "__main__":
    unittest.main()
