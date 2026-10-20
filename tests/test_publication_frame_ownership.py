"""Physical creator controls across official shell/child frames; local fixtures."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from uploader import publish_action
from tests.test_tc_editor import FIXTURE as TC_FIXTURE
from tests.test_bili_controls import HTML as BILI_FIXTURE


class Items:
    def __init__(self, values):
        self.values = values
    async def count(self):
        return len(self.values)
    def nth(self, index):
        return self.values[index]
    @property
    def first(self):
        return self.values[0]


class NativeHandle:
    def __init__(self, node, owner, *, enabled=True):
        self.node, self.owner, self.enabled = node, owner, enabled
        self.disposed = False
        self.read_owner = AsyncMock(side_effect=lambda: self.owner)
    async def owner_frame(self):
        return await self.read_owner()
    async def is_visible(self):
        return True
    async def is_enabled(self):
        return self.enabled
    async def evaluate(self, script, other=None):
        return self.node is other.node if other is not None else self.enabled
    async def dispose(self):
        self.disposed = True


class NativeLocator:
    def __init__(self, handle):
        self.handle = handle
    async def is_visible(self):
        return True
    async def element_handle(self, **kwargs):
        return self.handle


class NativeForm:
    def __init__(self, handles):
        self.handles = handles
    async def is_visible(self):
        return True
    def get_by_role(self, role, *, name, exact):
        assert role == 'button' and name == '发表' and exact is True
        return Items([NativeLocator(handle) for handle in self.handles])


class OwnerFrame:
    def __init__(self, url, page, parent=None):
        self.url, self.page, self.parent_frame = url, page, parent
        self.forms = []
    def is_detached(self):
        return False
    def locator(self, selector):
        assert selector == 'div.form-btns'
        return Items(self.forms)


class PhysicalTCControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page = SimpleNamespace(is_closed=lambda: False)
        self.main = OwnerFrame('https://channels.weixin.qq.com/platform/post/create', self.page)
        self.micro = OwnerFrame('https://channels.weixin.qq.com/micro/content/post/create', self.page, self.main)
        self.page.url, self.page.main_frame, self.page.frames = self.main.url, self.main, [self.main, self.micro]
        self.ready = AsyncMock(return_value=True)
        self.patch = patch.object(publish_action, '_tencent_upload_ready', self.ready)
        self.patch.start()
    def tearDown(self):
        self.patch.stop()
    def add(self, frame, node=None, *, owner=None, enabled=True):
        handle = NativeHandle(node if node is not None else object(), owner or frame, enabled=enabled)
        frame.forms.append(NativeForm([handle]))
        return handle

    async def test_same_physical_node_projected_twice_uses_actual_owner_once(self):
        node = object()
        first = self.add(self.main, node, owner=self.micro)
        second = self.add(self.micro, node)
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(error)
        self.assertIs(candidate.frame, self.micro)
        self.assertIs(candidate.control, first)
        self.assertFalse(first.disposed)
        self.assertTrue(second.disposed)
        self.assertEqual(self.ready.await_count, 2)

    async def test_two_independent_physical_controls_are_not_deduplicated(self):
        first, second = self.add(self.main), self.add(self.micro)
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(candidate)
        self.assertEqual(error, 'final_control_not_unique')
        self.assertTrue(first.disposed and second.disposed)

    async def test_multiple_buttons_in_one_form_or_disabled_control_reject(self):
        first, second = NativeHandle(object(), self.micro), NativeHandle(object(), self.micro)
        self.micro.forms = [NativeForm([first, second])]
        self.assertEqual((await publish_action._locate_current_editor(self.page, 'tencent'))[1], 'final_control_not_unique')
        self.micro.forms.clear()
        disabled = self.add(self.micro, enabled=False)
        self.assertEqual((await publish_action._locate_current_editor(self.page, 'tencent'))[1], 'final_control_disabled')
        self.assertTrue(disabled.disposed)

    async def test_about_blank_foreign_page_or_detached_owner_never_match(self):
        for owner in (OwnerFrame('about:blank', self.page),
                      OwnerFrame(self.micro.url, object(), self.main)):
            self.micro.forms.clear()
            handle = self.add(self.micro, owner=owner)
            candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
            self.assertIsNone(candidate)
            self.assertEqual(error, 'final_control_owner_changed')
            self.assertTrue(handle.disposed)
        self.micro.forms.clear()
        self.micro.is_detached = lambda: True
        self.add(self.main, owner=self.micro)
        self.assertIsNone((await publish_action._locate_current_editor(self.page, 'tencent'))[0])

    async def test_owner_navigation_or_page_change_during_handle_read_reject(self):
        handle = self.add(self.micro)
        async def owner():
            self.micro.url += '?changed'
            return self.micro
        handle.read_owner.side_effect = owner
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(candidate)
        self.assertEqual(error, 'final_control_owner_changed')
        self.assertTrue(handle.disposed)

    async def test_material_readiness_change_after_owner_check_rejects(self):
        handle = self.add(self.micro)
        self.ready.side_effect = [True, False]
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(candidate)
        self.assertEqual(error, 'final_control_changed')
        self.assertTrue(handle.disposed)

    async def test_two_micro_composers_or_non_child_micro_frame_reject(self):
        extra = OwnerFrame(self.micro.url, self.page, self.main)
        self.page.frames.append(extra)
        self.assertIsNone((await publish_action._locate_current_editor(self.page, 'tencent'))[0])
        self.page.frames.remove(extra)
        self.micro.parent_frame = None
        self.assertIsNone((await publish_action._locate_current_editor(self.page, 'tencent'))[0])
        self.ready.assert_not_awaited()


class BrowserFrameOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chromium')
        self.page = await self.browser.new_page()
    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def tc(self):
        async def route(route):
            body = ('<meta charset="utf-8"><div id="owner"></div><div class="form-btns"><button>发表</button></div>'
                    if '/micro/content/' in route.request.url else TC_FIXTURE)
            await route.fulfill(content_type='text/html;charset=utf-8', body=body)
        await self.page.route('**/*', route)
        await self.page.goto('https://channels.weixin.qq.com/platform/post/create')
        micro = self.page.frames[1]
        await micro.locator('button').wait_for(state='visible')
        await self.page.evaluate("window.material=makeMaterial(['done']);document.querySelector('#main').__vue__=material", isolated_context=False)
        return micro

    async def test_real_tc_two_frames_choose_micro_physical_button(self):
        micro = await self.tc()
        self.assertEqual(len(self.page.frames), 2)
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(error)
        self.assertIs(candidate.frame, micro)
        self.assertIs(await candidate.control.owner_frame(), micro)
        self.assertEqual(await self.page.evaluate('window.calls', isolated_context=False), {'business': 0, 'mutable': 0, 'private': 0})
        await candidate.control.dispose()

    async def test_tc_click_and_observed_post_use_the_actual_micro_owner_once(self):
        micro = await self.tc()
        await micro.evaluate('''() => {
          window.fixtureClicks=0;
          document.querySelector('button').onclick=()=>{
            window.fixtureClicks++;
            fetch('/cgi-bin/mmfinderassistant-bin/post/post_create',{method:'POST',body:'fixture'});
          };
        }''', isolated_context=False)
        with patch.object(publish_action, 'inspect_session', AsyncMock(return_value={'verified': True, 'identity': 'fixture-owner'})):
            result = await publish_action.publish_current(self.page, 'tencent', receipt_timeout=.08)
        self.assertEqual(result['observation'], {'publish_requests': 1, 'publish_responses': 1})
        self.assertEqual(result['status'], publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(await micro.evaluate('window.fixtureClicks', isolated_context=False), 1)

    async def test_shadow_projection_deduplicates_real_handles_with_actual_owner_metadata(self):
        micro = await self.tc()
        await self.page.evaluate('''() => {
          const shadow=document.querySelector('#shadow-host').attachShadow({mode:'open'});
          shadow.append(document.querySelector('#owner-frame').contentDocument.querySelector('div.form-btns'));
        }''', isolated_context=False)
        original = micro.locator
        # Model Wujie's owner-document projection at the locator boundary.
        # The elements, distinct ElementHandles, owner_frame metadata and DOM
        # equality evaluation are real Chromium values from the shadow root.
        micro.locator = lambda selector: self.page.main_frame.locator(selector) if selector == 'div.form-btns' else original(selector)
        try:
            candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
            self.assertIsNone(error)
            self.assertIs(candidate.frame, self.page.main_frame)
            self.assertIs(await candidate.control.owner_frame(), self.page.main_frame)
            await candidate.control.dispose()
        finally:
            micro.locator = original

    async def test_real_tc_two_independent_controls_or_materials_reject(self):
        await self.tc()
        await self.page.evaluate("document.querySelector('#main').insertAdjacentHTML('beforeend','<div class=\"form-btns\"><button>发表</button></div>')", isolated_context=False)
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(candidate)
        self.assertEqual(error, 'final_control_not_unique')
        await self.page.evaluate("document.querySelector('#owner-frame').contentDocument.querySelector('#owner').__vue__=makeMaterial(['done'])", isolated_context=False)
        candidate, error = await publish_action._locate_current_editor(self.page, 'tencent')
        self.assertIsNone(candidate)
        self.assertEqual(error, 'video_not_ready')

    async def bili(self, both=False):
        cfg = {'tasks': 1, 'done': True, 'records': 0, 'stored': 0, 'tags': [], 'delay': 80,
               'sibling': False, 'duplicate': False, 'unknown': False}
        editor = BILI_FIXTURE.replace('CONFIG', json.dumps(cfg)).replace(
            '<span class="submit-add">立即投稿</span>', '<div><span class="submit-add">立即投稿</span></div>')
        iframe = '<iframe src="https://member.bilibili.com/york/videoup"></iframe>'
        async def route(route):
            body = editor if '/york/videoup' in route.request.url else ((editor if both else '<meta charset="utf-8">') + iframe)
            await route.fulfill(content_type='text/html;charset=utf-8', body=body)
        await self.page.route('**/*', route)
        await self.page.goto('https://member.bilibili.com/platform/upload/video/frame')
        await self.page.frames[1].locator('.submit-add').wait_for(state='visible')
        return self.page.frames[1]

    async def test_real_bili_shell_and_native_child_select_only_typed_publisher(self):
        child = await self.bili()
        candidate, error = await publish_action._locate_current_editor(self.page, 'bilibili')
        self.assertIsNone(error)
        self.assertIs(candidate.frame, child)
        self.assertEqual(await child.evaluate('counters.publish', isolated_context=False), 0)
        self.assertEqual(await child.evaluate('counters.video', isolated_context=False), 0)

    async def test_real_bili_two_typed_publishers_are_not_merged(self):
        await self.bili(both=True)
        candidate, error = await publish_action._locate_current_editor(self.page, 'bilibili')
        self.assertIsNone(candidate)
        self.assertEqual(error, 'video_not_ready')

    async def test_bili_child_navigation_or_different_page_during_typed_read_rejects(self):
        child = await self.bili()
        original = publish_action._bili_upload_ready
        async def changed(page):
            frame = await original(page)
            await child.evaluate("history.replaceState(null,'','/york/videoup?changed')", isolated_context=False)
            return frame
        with patch.object(publish_action, '_bili_upload_ready', changed):
            self.assertIsNone((await publish_action._locate_current_editor(self.page, 'bilibili'))[0])
        other_page = await self.browser.new_page()
        try:
            with patch.object(publish_action, '_bili_upload_ready', AsyncMock(return_value=other_page.main_frame)):
                self.assertIsNone((await publish_action._locate_current_editor(self.page, 'bilibili'))[0])
        finally:
            await other_page.close()
