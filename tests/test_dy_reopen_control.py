"""Bounded and fail-closed tests for Douyin's delayed draft recovery control."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uploader.draft_only import _reopen


class VirtualClock:
    def __init__(self, on_sleep=None, *, expire_on_sleep=False):
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep
        self.expire_on_sleep = expire_on_sleep

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps += 1
        self.now = 26.0 if self.expire_on_sleep else self.now + seconds
        if self.on_sleep:
            self.on_sleep(self.sleeps)


class FakeControl:
    def __init__(self, text, *, visible=True, enabled=True):
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.clicks = 0
        self.click_timeouts = []

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def click(self, *, timeout):
        self.clicks += 1
        self.click_timeouts.append(timeout)


class FakeTextLocator:
    def __init__(self, frame, name, exact):
        self.frame = frame
        self.name = name
        self.exact = exact

    async def count(self):
        self.frame.queries.append((self.name, self.exact))
        return sum(control.text == self.name for control in self.frame.controls)

    def nth(self, index):
        return [control for control in self.frame.controls if control.text == self.name][index]


class FakeFrame:
    def __init__(self, controls=()):
        self.controls = list(controls)
        self.queries = []

    def get_by_text(self, name, *, exact):
        return FakeTextLocator(self, name, exact)


class DouyinReopenControlTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def candidate():
        return {'id': 'candidate-id', 'storage_key': 'cache-key', 'owner': 'candidate-owner'}

    async def invoke(self, frame, session, clock, safe_gate=None):
        page = SimpleNamespace(frames=[frame], _sau_draft_evidence=session)
        gate = safe_gate or AsyncMock()
        with patch('uploader.draft_only.time', SimpleNamespace(monotonic=clock.monotonic)), \
             patch('uploader.draft_only.asyncio', SimpleNamespace(sleep=clock.sleep)), \
             patch('uploader.draft_only.assert_draft_safe', new=gate):
            result = await _reopen(page, 'douyin', self.candidate())
        return result, gate

    async def test_delayed_visible_control_clicks_once_and_binds_exact_candidate(self):
        frame = FakeFrame()
        control = FakeControl('继续编辑')

        def mount_after_delay(sleep_count):
            if sleep_count == 3:
                frame.controls.append(control)

        clock = VirtualClock(on_sleep=mount_after_delay)
        session = SimpleNamespace(normal_ui_binding=None)
        result, gate = await self.invoke(frame, session, clock)

        self.assertTrue(result)
        self.assertEqual(clock.sleeps, 3)
        self.assertEqual(control.clicks, 1)
        self.assertEqual(control.click_timeouts, [30000])
        self.assertTrue(all(exact for _, exact in frame.queries))
        self.assertEqual(session.normal_ui_binding, {
            'id': 'candidate-id', 'key': 'cache-key', 'owner': 'candidate-owner', 'control': '继续编辑',
        })
        gate.assert_awaited()

    async def test_hidden_or_disabled_recovery_controls_are_never_clicked(self):
        hidden = FakeControl('继续编辑', visible=False)
        disabled = FakeControl('恢复草稿', enabled=False)
        frame = FakeFrame([hidden, disabled])
        clock = VirtualClock(expire_on_sleep=True)
        session = SimpleNamespace(normal_ui_binding=None)

        result, _ = await self.invoke(frame, session, clock)

        self.assertFalse(result)
        self.assertEqual((hidden.clicks, disabled.clicks), (0, 0))
        self.assertIsNone(session.normal_ui_binding)
        self.assertEqual(clock.sleeps, 1)

    async def test_multiple_visible_enabled_controls_are_ambiguous_and_rejected(self):
        controls = [FakeControl('继续编辑'), FakeControl('恢复草稿')]
        frame = FakeFrame(controls)
        clock = VirtualClock()
        session = SimpleNamespace(normal_ui_binding=None)

        result, _ = await self.invoke(frame, session, clock)

        self.assertFalse(result)
        self.assertEqual([control.clicks for control in controls], [0, 0])
        self.assertIsNone(session.normal_ui_binding)
        self.assertEqual(clock.sleeps, 0)

    async def test_safety_gate_exception_propagates_without_inspecting_or_clicking(self):
        control = FakeControl('继续编辑')
        frame = FakeFrame([control])
        clock = VirtualClock()
        session = SimpleNamespace(normal_ui_binding=None)
        gate = AsyncMock(side_effect=RuntimeError('fixture safety stop'))

        with self.assertRaisesRegex(RuntimeError, 'fixture safety stop'):
            await self.invoke(frame, session, clock, safe_gate=gate)

        self.assertEqual(control.clicks, 0)
        self.assertEqual(frame.queries, [])
        self.assertIsNone(session.normal_ui_binding)
        gate.assert_awaited_once_with(SimpleNamespace(frames=[frame], _sau_draft_evidence=session), 'douyin')


if __name__ == '__main__':
    unittest.main()
