"""One-shot publication observer tests with synthetic pages only."""
import asyncio
import unittest
from unittest.mock import patch

from uploader import publish_action
from uploader.draft_session import DraftInterrupted


class FakeFrame:
    def __init__(self, url):
        self.url = url


class FakePage:
    def __init__(self, frame):
        self.frames = [frame]
        self.url = frame.url
        self.listeners = {"request": [], "response": []}
        self.closed = False

    def is_closed(self):
        return self.closed

    def on(self, event, callback):
        self.listeners[event].append(callback)

    def remove_listener(self, event, callback):
        if callback in self.listeners[event]:
            self.listeners[event].remove(callback)

    def emit(self, event, value):
        for callback in tuple(self.listeners[event]):
            callback(value)


class FakeButton:
    def __init__(self, callback=None, *, fail=False, visible_callback=None):
        self.callback = callback
        self.fail = fail
        self.visible_callback = visible_callback
        self.clicks = 0
        self.visibility_checks = 0

    async def is_visible(self):
        self.visibility_checks += 1
        if self.visible_callback:
            result = self.visible_callback(self.visibility_checks)
            if asyncio.iscoroutine(result):
                await result
        return True

    async def is_enabled(self):
        return True

    async def evaluate(self, _script):
        return True

    async def click(self, **_kwargs):
        self.clicks += 1
        if self.callback:
            result = self.callback()
            if asyncio.iscoroutine(result):
                await result
        if self.fail:
            raise RuntimeError("synthetic click timeout after dispatch")


class FakeRequest:
    def __init__(self, url, frame, method="POST"):
        self.url = url
        self.frame = frame
        self.method = method


class FakeResponse:
    def __init__(self, request, body, *, status=200, headers=None):
        self.request = request
        self.status = status
        self.headers = headers or {"content-type": "application/json", "content-length": "128"}
        self._body = body

    async def json(self):
        return self._body


class FakeRoleLocator:
    def __init__(self, buttons):
        self.buttons = buttons

    async def count(self):
        return len(self.buttons)

    def nth(self, index):
        return self.buttons[index]


class FakeRoleFrame(FakeFrame):
    def __init__(self, url, buttons):
        super().__init__(url)
        self.buttons = buttons

    def get_by_role(self, role, *, name, exact):
        assert role == "button" and exact is True
        return FakeRoleLocator(self.buttons if name == "发布" else [])


def _install_preflight(platform, page, button, *, identity="owner-hash"):
    candidate = publish_action._Candidate(page.frames[0], button)
    return (
        patch.object(publish_action, "inspect_session", return_value={
            "verified": True,
            "identity": identity,
        }),
        patch.object(publish_action, "_locate_current_editor", return_value=(candidate, None)),
    )


class PublishActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_bilibili_receipt_verifies_one_click_and_restores_guard(self):
        frame = FakeFrame("https://member.bilibili.com/platform/upload/video/frame")
        page = FakePage(frame)

        def submit():
            request = FakeRequest(
                "https://member.bilibili.com/arcopen/fn/archive/add-by-utoken?upload_token=redacted",
                frame,
            )
            page.emit("request", request)
            page.emit("response", FakeResponse(request, {
                "code": 0,
                "data": {"resource_id": "BV1synthetic"},
            }))

        button = FakeButton(submit)
        inspect_patch, locate_patch = _install_preflight("bilibili", page, button)
        restored = []
        progress = []
        with inspect_patch, locate_patch:
            result = await publish_action.publish_current(
                page,
                "bilibili",
                before_click=lambda *_: lambda: restored.append(True),
                progress=progress.append,
                receipt_timeout=0.05,
            )

        self.assertEqual(result["status"], publish_action.PUBLISHED_SUBMISSION_VERIFIED)
        self.assertEqual(result["evidence"]["submission_id"], "BV1synthetic")
        self.assertEqual(result["observation"], {"publish_requests": 1, "publish_responses": 1})
        self.assertEqual(button.clicks, 1)
        self.assertEqual(restored, [True])
        self.assertTrue(any(item["stage"] == "receipt" for item in progress))
        self.assertEqual(page.listeners["request"], [])
        self.assertEqual(page.listeners["response"], [])

    async def test_xhs_success_route_without_current_native_post_is_unverified(self):
        frame = FakeFrame("https://creator.xiaohongshu.com/publish/publish")
        page = FakePage(frame)

        def submit():
            page.url = "https://creator.xiaohongshu.com/publish/success?note_id=synthetic"

        button = FakeButton(submit)
        inspect_patch, locate_patch = _install_preflight("xiaohongshu", page, button)
        with inspect_patch, locate_patch:
            result = await publish_action.publish_current(page, "xiaohongshu", receipt_timeout=0.01)

        self.assertEqual(result["status"], publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(result["evidence"], {})
        self.assertEqual(button.clicks, 1)

    async def test_risk_and_unverified_login_block_without_releasing_guard(self):
        frame = FakeFrame("https://creator.douyin.com/creator-micro/content/post/video")
        page = FakePage(frame)
        button = FakeButton()
        candidate = publish_action._Candidate(frame, button)
        before_click_calls = []

        with patch.object(publish_action, "inspect_session", side_effect=DraftInterrupted("challenge", "risk_paused")), \
             patch.object(publish_action, "_locate_current_editor", return_value=(candidate, None)):
            risk = await publish_action.publish_current(
                page, "douyin", before_click=lambda *_: before_click_calls.append(True)
            )

        self.assertEqual(risk["status"], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(risk["code"], "risk_detected")
        self.assertEqual(button.clicks, 0)
        self.assertEqual(before_click_calls, [])

        with patch.object(publish_action, "inspect_session", return_value={"verified": False}), \
             patch.object(publish_action, "_locate_current_editor", return_value=(candidate, None)):
            login = await publish_action.publish_current(page, "douyin")
        self.assertEqual(login["status"], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(login["code"], "account_unverified")
        self.assertEqual(button.clicks, 0)

    async def test_multiple_exact_publish_buttons_are_not_selected(self):
        first, second = FakeButton(), FakeButton()
        frame = FakeRoleFrame("https://creator.douyin.com/creator-micro/content/post/video", [first, second])
        candidate, error = await publish_action._unique_control_in_frame(frame, "douyin")
        self.assertIsNone(candidate)
        self.assertEqual(error, "final_control_not_unique")
        self.assertEqual((first.clicks, second.clicks), (0, 0))

        page = FakePage(frame)
        with patch.object(publish_action, "inspect_session", return_value={"verified": True, "identity": "owner"}), \
             patch.object(publish_action, "_locate_current_editor", return_value=(None, "final_control_not_unique")):
            result = await publish_action.publish_current(page, "douyin")
        self.assertEqual(result["status"], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(result["code"], "final_control_not_unique")
        self.assertEqual((first.clicks, second.clicks), (0, 0))

    async def test_private_ui_endpoints_never_use_guessed_success_fields(self):
        cases = (
            ("douyin", "https://creator.douyin.com/creator-micro/content/post/video",
             "https://creator.douyin.com/web/api/media/aweme/create_v2", {"data": {"aweme_id": "guess"}}),
            ("xiaohongshu", "https://creator.xiaohongshu.com/publish/publish",
             "https://creator.xiaohongshu.com/api/media/v1/note/create", {"data": {"note_id": "guess"}}),
            ("tencent", "https://channels.weixin.qq.com/platform/post/create",
             "https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_create", {"data": {"feed_id": "guess"}}),
            ("bilibili", "https://member.bilibili.com/platform/upload/video/frame",
             "https://member.bilibili.com/x/vu/web/add", {"data": {"bvid": "guess"}}),
            ("kuaishou", "https://cp.kuaishou.com/article/publish/video",
             "https://cp.kuaishou.com/rest/cp/works/v3/video/pc/submit", {"data": {"photo_id": "guess"}}),
        )
        for platform, editor_url, endpoint, body in cases:
            with self.subTest(platform=platform):
                frame = FakeFrame(editor_url)
                page = FakePage(frame)

                def submit(endpoint=endpoint, body=body, frame=frame, page=page):
                    request = FakeRequest(endpoint, frame)
                    page.emit("request", request)
                    page.emit("response", FakeResponse(request, body))

                button = FakeButton(submit)
                inspect_patch, locate_patch = _install_preflight(platform, page, button)
                with inspect_patch, locate_patch:
                    result = await publish_action.publish_current(page, platform, receipt_timeout=0.02)

                self.assertEqual(result["status"], publish_action.PUBLISHED_UNVERIFIED)
                self.assertTrue(result["clicked"])
                self.assertEqual(button.clicks, 1)
                self.assertNotIn("guess", str(result))

    async def test_click_exception_never_retries_and_guard_is_restored(self):
        frame = FakeFrame("https://creator.douyin.com/creator-micro/content/post/video")
        page = FakePage(frame)
        button = FakeButton(fail=True)
        inspect_patch, locate_patch = _install_preflight("douyin", page, button)
        restored = []
        with inspect_patch, locate_patch:
            result = await publish_action.publish_current(
                page,
                "douyin",
                before_click=lambda *_: lambda: restored.append(True),
                receipt_timeout=0,
            )

        self.assertEqual(result["status"], publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(result["code"], "no_verified_receipt")
        self.assertEqual(button.clicks, 1)
        self.assertEqual(restored, [True])

    async def test_old_background_response_before_observer_is_not_this_submission(self):
        frame = FakeFrame("https://member.bilibili.com/platform/upload/video/frame")
        page = FakePage(frame)
        old_request = FakeRequest(
            "https://member.bilibili.com/arcopen/fn/archive/add-by-utoken?upload_token=old",
            frame,
        )
        # It completed before publish_current installs the per-attempt observer.
        page.emit("request", old_request)
        page.emit("response", FakeResponse(old_request, {"code": 0, "data": {"resource_id": "old-id"}}))
        button = FakeButton()
        inspect_patch, locate_patch = _install_preflight("bilibili", page, button)
        with inspect_patch, locate_patch:
            result = await publish_action.publish_current(page, "bilibili", receipt_timeout=0)

        self.assertEqual(result["status"], publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(result["observation"], {"publish_requests": 0, "publish_responses": 0})
        self.assertEqual(button.clicks, 1)

    async def test_background_response_between_observer_install_and_click_is_ignored(self):
        frame = FakeFrame("https://member.bilibili.com/platform/upload/video/frame")
        page = FakePage(frame)

        def background_during_final_check(check_number):
            # First visibility check is preflight; the second runs after the
            # listener is installed but before the explicit click is armed.
            if check_number == 2:
                request = FakeRequest(
                    "https://member.bilibili.com/arcopen/fn/archive/add-by-utoken?upload_token=background",
                    frame,
                )
                page.emit("request", request)
                page.emit("response", FakeResponse(request, {
                    "code": 0,
                    "data": {"resource_id": "background-id"},
                }))

        button = FakeButton(visible_callback=background_during_final_check)
        inspect_patch, locate_patch = _install_preflight("bilibili", page, button)
        with inspect_patch, locate_patch:
            result = await publish_action.publish_current(page, "bilibili", receipt_timeout=0)

        self.assertEqual(result["status"], publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(result["observation"], {"publish_requests": 0, "publish_responses": 0})
        self.assertEqual(button.clicks, 1)
        self.assertNotIn("background-id", str(result))

    async def test_expected_identity_mismatch_and_unsupported_platform_block(self):
        frame = FakeFrame("https://creator.douyin.com/creator-micro/content/post/video")
        page = FakePage(frame)
        button = FakeButton()
        inspect_patch, locate_patch = _install_preflight("douyin", page, button)
        with inspect_patch, locate_patch:
            mismatch = await publish_action.publish_current(
                page, "douyin", expected={"expected_account_identity": "different"}
            )
        unsupported = await publish_action.publish_current(page, "toutiao")
        self.assertEqual(mismatch["code"], "account_mismatch")
        self.assertEqual(mismatch["status"], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(unsupported["code"], "unsupported_platform")
        self.assertEqual(button.clicks, 0)


if __name__ == "__main__":
    unittest.main()

class BilibiliWebContractTests(unittest.TestCase):
    def test_exact_official_v3_endpoint_requires_archive_identity(self):
        from uploader.draft_only import is_publish_request
        endpoint='https://member.bilibili.com/x/vu/web/add/v3'
        self.assertTrue(is_publish_request(endpoint,'POST'))
        self.assertEqual(publish_action._documented_contract_url(endpoint),'bilibili_web')
        self.assertFalse(is_publish_request('https://member.bilibili.com/x/vu/web/add/v2/advance','POST'))
        valid={'code':0,'data':{'aid':123,'bvid':'BV1J7HS6hE52'}}
        self.assertEqual(publish_action._extract_documented_id('bilibili_web',valid),'BV1J7HS6hE52')
        for value in ({'code':1,'data':valid['data']},{'code':False,'data':valid['data']},
                      {'code':0,'data':{'aid':True,'bvid':'BV1J7HS6hE52'}},
                      {'code':0,'data':{'aid':123,'bvid':'guess'}},{'code':0,'data':{'bvid':'BV1J7HS6hE52'}}):
            with self.subTest(value=value):self.assertIsNone(publish_action._extract_documented_id('bilibili_web',value))
