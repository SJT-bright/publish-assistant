"""Mock-only tests for the pre-selection Douyin editor readiness wait."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import Error

from uploader.draft_only import DraftInterrupted
from uploader.dy_upload_ready import wait_dy_empty_video_input


EDITOR_URL = 'https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page&type=new'


class VirtualClock:
    def __init__(self, *, expire_on_sleep=False):
        self.now = 0.0
        self.sleeps = 0
        self.expire_on_sleep = expire_on_sleep

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps += 1
        self.now = 100.0 if self.expire_on_sleep else self.now + seconds


class FakeField:
    def __init__(self, value):
        self.value = value

    async def input_value(self):
        return self.value

    async def inner_text(self):
        return self.value


class FakeInput:
    def __init__(self):
        self.actions = []

    async def set_input_files(self, *args, **kwargs):
        self.actions.append(('set_input_files', args, kwargs))

    async def click(self, *args, **kwargs):
        self.actions.append(('click', args, kwargs))


class FakeLocator:
    def __init__(self, items):
        self.items = list(items)

    async def all(self):
        return list(self.items)


class FakeFrame:
    def __init__(self, *, inputs=None, titles=None, bodies=None):
        self.inputs = list(inputs or [])
        self.titles = list(titles or [])
        self.bodies = list(bodies or [])
        self.locator_calls = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        if selector.startswith('input[placeholder*="填写作品标题"]'):
            return FakeLocator(self.titles)
        if selector == 'div.zone-container[contenteditable="true"]':
            return FakeLocator(self.bodies)
        return FakeLocator(self.inputs)


class DYUploadReadyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = VirtualClock()
        self.input = FakeInput()
        self.frame = FakeFrame(inputs=[self.input])
        self.page = SimpleNamespace(url=EDITOR_URL, main_frame=self.frame)

    async def invoke(self, *, page=None, read_state=None, safe_gate=None, timeout=5):
        page = page or self.page
        evaluate = AsyncMock(side_effect=read_state if isinstance(read_state, list) else None,
                             return_value=read_state if not isinstance(read_state, list) else None)
        gate = safe_gate or AsyncMock()
        with patch('uploader.dy_upload_ready.time', SimpleNamespace(monotonic=self.clock.monotonic)), \
             patch('uploader.dy_upload_ready.asyncio', SimpleNamespace(sleep=self.clock.sleep)), \
             patch('uploader.draft_evidence.evaluate_main', new=evaluate), \
             patch('uploader.draft_only.assert_draft_safe', new=gate):
            result = await wait_dy_empty_video_input(page, timeout=timeout)
        return result, evaluate, gate

    async def test_navigation_read_error_retries_then_requires_two_stable_reads(self):
        error = Error('Execution context was destroyed during navigation')
        result, evaluate, gate = await self.invoke(read_state=[error, 'complete', 'complete'])

        self.assertIs(result, self.input)
        self.assertEqual(evaluate.await_count, 3)
        self.assertEqual(self.clock.sleeps, 2)
        self.assertEqual(self.frame.locator_calls.count(
            'input[type="file"][accept*="video"],input[type="file"][accept*=".mp4"],'
            'input.upload-btn-input:not([accept*="image"]),input.upload-input:not([accept*="image"])'), 2)
        gate.assert_awaited()
        self.assertEqual(self.input.actions, [])

    async def test_existing_title_or_body_stops_before_return(self):
        for field_kind in ('title', 'body'):
            with self.subTest(field_kind=field_kind):
                self.frame = FakeFrame(inputs=[self.input],
                    titles=[FakeField('existing title')] if field_kind == 'title' else [],
                    bodies=[FakeField('existing body')] if field_kind == 'body' else [])
                self.page.main_frame = self.frame
                with self.assertRaisesRegex(RuntimeError, '恢复了已有内容'):
                    await self.invoke(read_state='complete')
                self.assertEqual(self.input.actions, [])

    async def test_multiple_video_inputs_are_rejected_without_selecting_a_file(self):
        second = FakeInput()
        self.frame.inputs = [self.input, second]

        with self.assertRaisesRegex(RuntimeError, '控件不唯一'):
            await self.invoke(read_state='complete')

        self.assertEqual(self.input.actions, [])
        self.assertEqual(second.actions, [])
        self.assertNotIn('input[placeholder*="填写作品标题"]', self.frame.locator_calls)

    async def test_safety_gate_draft_interruption_is_not_swallowed_or_retried(self):
        gate = AsyncMock(side_effect=DraftInterrupted('challenge fixture', 'risk_paused'))
        evaluate = AsyncMock()
        with patch('uploader.dy_upload_ready.time', SimpleNamespace(monotonic=self.clock.monotonic)), \
             patch('uploader.dy_upload_ready.asyncio', SimpleNamespace(sleep=self.clock.sleep)), \
             patch('uploader.draft_evidence.evaluate_main', new=evaluate), \
             patch('uploader.draft_only.assert_draft_safe', new=gate):
            with self.assertRaisesRegex(DraftInterrupted, 'challenge fixture'):
                await wait_dy_empty_video_input(self.page, timeout=5)

        gate.assert_awaited_once_with(self.page, '抖音')
        evaluate.assert_not_awaited()
        self.assertEqual(self.frame.locator_calls, [])
        self.assertEqual(self.clock.sleeps, 0)
        self.assertEqual(self.input.actions, [])

    async def test_wrong_route_unstable_page_times_out_without_dom_action(self):
        self.page.url = 'https://creator.douyin.com/creator-micro/content/upload'
        self.clock.expire_on_sleep = True

        with self.assertRaisesRegex(RuntimeError, '尚未稳定'):
            await self.invoke(read_state='complete', timeout=1)

        self.assertEqual(self.clock.sleeps, 1)
        self.assertEqual(self.frame.locator_calls, [])
        self.assertEqual(self.input.actions, [])


if __name__ == '__main__':
    unittest.main()
