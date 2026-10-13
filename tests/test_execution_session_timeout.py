"""Local preparation/cleanup deadlines; no browser or platform operation."""
import asyncio
import io
import json
import os
import unittest
from contextlib import AsyncExitStack, asynccontextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import draft_execution as execution
from uploader.draft_evidence import CheckpointError
from uploader.draft_session import DraftInterrupted

REAL_BROWSER_SESSION = execution.browser_session


class ExecutionSessionTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = AsyncExitStack()
        await self.patches.__aenter__()
        self.counts = {'entered': 0, 'exited': 0}
        self.page = SimpleNamespace(context=SimpleNamespace(browser=object()))
        self.evidence = SimpleNamespace()
        self.expected = {'video': 'fixture-only.mp4', 'title': 'fixture title'}

        @asynccontextmanager
        async def browser(*args, **kwargs):
            self.counts['entered'] += 1
            try:
                yield object(), self.page.context.browser, self.page.context, self.page, None
            finally:
                self.counts['exited'] += 1

        async def initialize(*args, **kwargs):
            self.page._sau_draft_evidence = self.evidence
            return self.evidence

        async def watched(platform, page, owner, operation, **kwargs):
            return await operation

        for context in (
            patch.dict(os.environ, SAU_PROGRESS_JOB_ID='fixture-session', SAU_PROGRESS_PLATFORM='bilibili'),
            patch.object(execution, 'prepare_expected', return_value=self.expected),
            patch('material_probe.media_info', return_value={}),
            patch.object(execution, 'browser_session', browser),
            patch.object(execution, 'open_creator', AsyncMock()),
            patch.object(execution, 'verify_session', AsyncMock(return_value={'identity': 'fixture-owner'})),
            patch.object(execution, 'current_owner_state', AsyncMock(return_value={})),
            patch('platform_limits.check_fields', return_value={'errors': []}),
            patch.object(execution, 'read_marker', return_value={}),
            patch.object(execution, 'write_marker'),
            patch.object(execution, 'install_draft_guard', AsyncMock()),
            patch('uploader.draft_only.begin_draft_verification', initialize),
            patch('uploader.draft_only.end_draft_verification', AsyncMock()),
            patch.object(execution, 'run_with_session_watch', watched),
            patch('workbench_model.validate_receipt'),
        ):
            self.patches.enter_context(context)

    async def asyncTearDown(self):
        await self.patches.aclose()

    def events(self, output, prefix):
        events = [json.loads(line.split('=', 1)[1]) for line in output.getvalue().splitlines()
                  if line.startswith(prefix + '=')]
        # These assertions cover outer deadline stages. Optional fine events
        # have their own monotonic/partial-failure tests.
        return [event for event in events if prefix != 'DRAFT_PROGRESS' or
                not any(key in event for key in ('plan', 'operation'))]

    async def test_preparation_timeout_fails_session_without_import_or_second_enter(self):
        @asynccontextmanager
        async def stalled_browser(*args, **kwargs):
            self.counts['entered'] += 1
            try:
                await asyncio.Event().wait()
                yield
            finally:
                self.counts['exited'] += 1

        output = io.StringIO()
        upload = AsyncMock()
        with patch.object(execution, 'SESSION_PREPARE_TIMEOUT', .05), \
             patch.object(execution, 'browser_session', stalled_browser), \
             patch.object(execution, 'bilibili_draft', upload), redirect_stdout(output):
            with self.assertRaises(DraftInterrupted) as caught:
                await asyncio.wait_for(execution.stage('bilibili', {}, 'unused.json'), 2)
        self.assertEqual(caught.exception.status, 'adapter_mismatch')
        self.assertEqual(self.counts, {'entered': 1, 'exited': 1})
        upload.assert_not_awaited()
        progress = self.events(output, 'DRAFT_PROGRESS')
        self.assertEqual([(e['stage'], e['state']) for e in progress],
                         [('session', 'running'), ('session', 'failed')])
        self.assertIn({'event': 'draft_session_prepare', 'phase': 'browser_connect', 'state': 'timeout'},
                      self.events(output, 'DRAFT_DIAGNOSTIC'))

    async def test_business_after_yield_outlives_preparation_budget(self):
        async def longer_business(*args, **kwargs):
            await asyncio.sleep(.12)
            return {'status': 'platform_draft_verified'}
        with patch.object(execution, 'SESSION_PREPARE_TIMEOUT', .05), \
             patch.object(execution, 'bilibili_draft', longer_business), redirect_stdout(io.StringIO()):
            result = await execution.stage('bilibili', {}, 'unused.json')
        self.assertEqual(result['status'], 'platform_draft_verified')
        self.assertEqual(self.counts, {'entered': 1, 'exited': 1})

    async def test_business_timeout_remains_original_and_does_not_refail_session(self):
        original = TimeoutError('fixture business timeout')
        output = io.StringIO()
        with patch.object(execution, 'bilibili_draft', AsyncMock(side_effect=original)), redirect_stdout(output):
            with self.assertRaises(TimeoutError) as caught:
                await execution.stage('bilibili', {}, 'unused.json')
        self.assertIs(caught.exception, original)
        self.assertEqual(self.counts, {'entered': 1, 'exited': 1})
        self.assertEqual([(e['stage'], e['state']) for e in self.events(output, 'DRAFT_PROGRESS')],
                         [('session', 'running'), ('session', 'completed')])

    async def test_stalled_final_capture_and_evidence_close_preserve_business_error(self):
        original = RuntimeError('private fixture business error')
        async def stall(*args, **kwargs):
            await asyncio.Event().wait()
        class Checkpoint:
            def bind(self, page, session):
                page._sau_checkpoint_runtime = self
            capture = staticmethod(stall)
        checkpoint = Checkpoint()
        output = io.StringIO()
        with patch.object(execution, 'ExecutionCheckpoint', return_value=checkpoint), \
             patch.object(execution, 'SESSION_CLEANUP_TIMEOUT', .03), \
             patch.object(execution, 'bilibili_draft', AsyncMock(side_effect=original)), \
             patch('uploader.draft_only.end_draft_verification', stall), redirect_stdout(output):
            with self.assertRaises(RuntimeError) as caught:
                await asyncio.wait_for(execution.stage('bilibili', {}, 'unused.json',
                    checkpoint_key_file='fixture-key', checkpoint_output='fixture-checkpoint'), 2)
        self.assertIs(caught.exception, original)
        self.assertEqual(self.counts, {'entered': 1, 'exited': 1})
        self.assertIsNone(self.page._sau_checkpoint_runtime)
        events = self.events(output, 'DRAFT_DIAGNOSTIC')
        for phase in ('checkpoint_final_capture', 'evidence_end'):
            self.assertIn({'event': 'draft_session_prepare', 'phase': phase, 'state': 'timeout'}, events)
        self.assertNotIn('private fixture business error', output.getvalue())

    async def test_restore_rejection_reports_only_fixed_known_reason(self):
        for reason in ('checkpoint_composer_unverified', 'checkpoint_private_https://fixture.invalid/token'):
            runtime = object.__new__(execution.ExecutionCheckpoint)
            runtime._same_session = lambda session: None
            runtime.require_no_save = lambda: None
            runtime.resume, runtime.key = Path('fixture-checkpoint'), b'0' * 32
            session = SimpleNamespace(expected={}, platform='tencent',
                restore_checkpoint=AsyncMock(side_effect=CheckpointError(reason)))
            output = io.StringIO()
            with patch.object(execution, '_checkpoint_read', return_value={}), \
                 patch.object(execution, 'validate_checkpoint', return_value={}), redirect_stdout(output):
                with self.assertRaises(DraftInterrupted):
                    await runtime.restore(session)
            event = self.events(output, 'DRAFT_DIAGNOSTIC')[0]
            self.assertEqual(event, {'event': 'checkpoint_restore_rejected',
                'reason': reason if reason == 'checkpoint_composer_unverified' else 'checkpoint_restore_unverified'})
            self.assertNotIn('https://fixture.invalid/token', output.getvalue())

    async def test_browser_cleanup_disconnects_once_and_never_masks_body_error(self):
        original = RuntimeError('private fixture business exception')
        owned_browser = SimpleNamespace(close=AsyncMock())
        handle = SimpleNamespace(browser=owned_browser, context=self.page.context, page=self.page,
            connect=AsyncMock(), release=AsyncMock(side_effect=RuntimeError('private release URL')))
        stop = AsyncMock()
        manager = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(stop=stop)))
        removed = []
        output = io.StringIO()
        with patch.object(execution, 'async_playwright', return_value=manager), \
             patch.object(execution, 'EmbeddedBrowser', return_value=handle), \
             patch.object(execution, 'observe_session', return_value=lambda: removed.append(True)), \
             redirect_stdout(output):
            with self.assertRaises(RuntimeError) as caught:
                async with REAL_BROWSER_SESSION('tencent', True, 'draft'):
                    raise original
        self.assertIs(caught.exception, original)
        stop.assert_awaited_once()
        handle.connect.assert_awaited_once()
        owned_browser.close.assert_not_awaited()
        self.assertEqual(removed, [True])
        self.assertNotIn('private release URL', output.getvalue())

    async def test_cancelled_connect_has_bounded_internal_lease_cleanup(self):
        handle = SimpleNamespace(lease=True)
        release_calls = []
        async def original_release():
            if not handle.lease:
                return
            handle.lease = False
            release_calls.append(True)
            await asyncio.Event().wait()
        async def connect(*args):
            try:
                await asyncio.Event().wait()
            except BaseException:
                await handle.release()
                raise
        handle.connect, handle.release = connect, original_release
        stop = AsyncMock()
        manager = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(stop=stop)))
        output = io.StringIO()
        with patch.object(execution, 'async_playwright', return_value=manager), \
             patch.object(execution, 'EmbeddedBrowser', return_value=handle), \
             patch.object(execution, 'SESSION_CLEANUP_TIMEOUT', .03), redirect_stdout(output):
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(.03):
                    async with REAL_BROWSER_SESSION('tencent', True, 'draft'):
                        self.fail('cancelled connect must never yield a page')
        self.assertEqual(release_calls, [True])
        stop.assert_awaited_once()
        self.assertIn({'event': 'draft_session_prepare', 'phase': 'lease_release', 'state': 'timeout'},
                      self.events(output, 'DRAFT_DIAGNOSTIC'))


if __name__ == '__main__':
    unittest.main()
