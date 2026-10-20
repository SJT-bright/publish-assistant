"""Bounded Douyin preview readiness, with isolated files and local fixtures."""
import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uploader import publication_workflow as workflow
from uploader.draft_evidence import DraftEvidenceSession, IDENTITY_KEYS, _sha_file


class DYPublicationReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='dy-preview-readiness-')
        self.video = Path(self.folder.name) / 'fixture.mp4'
        self.video.write_bytes(b'isolated video fixture')
        asset = {**_sha_file(self.video), 'kind': 'video'}
        self.expected = {key: 'fixture-' + key for key in IDENTITY_KEYS}
        self.expected.update(platform_id='douyin', workflow_action='publish', cover_sha256={},
                             video=str(self.video), video_sha256=asset['sha256'], platform_fields={},
                             account={'verified': True, 'identity': 'fixture-owner', 'uid': 'fixture-uid',
                                      'platform': 'douyin'})
        self.page = SimpleNamespace(main_frame=object())
        self.session = SimpleNamespace(page=self.page, platform='douyin', closed=False, issues=[],
                                       expected=copy.deepcopy(self.expected), assets={'video': asset}, files=[],
                                       account=copy.deepcopy(self.expected['account']))
        self.page._sau_draft_evidence = self.session
        self.links = [{'kind': 'video', 'sha256': asset['sha256'], 'ids': ['current-vid'], 'complete': True}]
        self.session.upload_links = lambda: copy.deepcopy(self.links)
        self.observer = SimpleNamespace(closed=False, scope={'job_id': self.expected['job_id'],
                                        'receipt_nonce': self.expected['receipt_nonce'], 'observer_nonce': 'fixture-observer'},
                                        latest_invocation='fixture-sdk-invocation', upload_links=self.session.upload_links)
        self.native = AsyncMock(side_effect=lambda *_: {'scoped': True, 'invalid': False, 'ready': True,
                                                       'vid': self.links[0]['ids'][0] if self.links else 'current-vid'})
        self.observer.transport = SimpleNamespace(evaluate=self.native)
        self.session.dy_observer = self.observer
        self.session.read_account = AsyncMock(side_effect=lambda: copy.deepcopy(self.session.account))
        self.session._checkpoint_assets = lambda: DraftEvidenceSession._checkpoint_assets(self.session)
        self.sdk = AsyncMock(return_value=True)
        self.reader = AsyncMock(return_value=None)
        self.pure = AsyncMock(return_value=None)
        self.patchers = [patch('uploader.draft_only.assert_draft_safe', AsyncMock()),
                         patch('uploader.dy_upload_ready.wait_dy_video_uploaded', self.sdk),
                         patch('uploader.dy_reopen.read_dy_visible_video', self.reader),
                         patch.object(workflow, 'evaluate_main', self.pure)]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.folder.cleanup()

    @staticmethod
    def ready(vid='current-vid'):
        return {'source': 'normal_visible_video_component', 'video_count': 1, 'video_ids': [vid]}

    async def wait(self, timeout=.12):
        return await workflow._wait_dy_publication_preview(self.page, self.expected, self.session,
                                                          timeout=timeout, poll_interval=.002)

    async def test_late_decoded_preview_uses_one_presentation_and_pure_polls(self):
        self.pure.side_effect = [None, None, self.ready()]
        self.assertTrue(await self.wait())
        self.reader.assert_awaited_once_with(self.page, ['current-vid'])
        self.assertEqual(self.pure.await_count, 3)
        self.sdk.assert_awaited_once()
        self.assertGreater(self.native.await_count, 2)
        self.assertIn('["current-vid"]', self.pure.await_args.args[1])

    async def test_initial_ready_preview_still_rechecks_current_sdk_binding(self):
        self.reader.return_value = self.ready()
        self.assertTrue(await self.wait())
        self.reader.assert_awaited_once()
        self.pure.assert_not_awaited()
        self.sdk.assert_awaited_once()
        self.assertGreaterEqual(self.native.await_count, 2)

    async def test_unready_or_foreign_preview_stops_at_deadline(self):
        self.pure.return_value = self.ready('old-vid')
        started = time.monotonic()
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_current_preview_not_ready'):
            await self.wait(timeout=.025)
        self.assertLess(time.monotonic() - started, .2)
        self.reader.assert_awaited_once()

    async def test_account_change_during_presentation_cancels_reader(self):
        stopped = asyncio.Event()
        async def present(*_args):
            self.session.read_account.return_value = {**self.session.account, 'identity': 'other-owner'}
            self.session.read_account.side_effect = None
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.reader.side_effect = present
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_account_changed'):
            await self.wait()
        self.assertTrue(stopped.is_set())
        self.pure.assert_not_awaited()

    async def test_source_replacement_during_poll_is_rejected_before_ready(self):
        async def poll(*_args):
            self.video.write_bytes(b'changed video fixture')
            return self.ready()
        self.pure.side_effect = poll
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_scope_or_source_changed'):
            await self.wait()

    async def test_changed_observer_scope_or_invocation_is_rejected(self):
        for field in ('scope', 'latest_invocation'):
            original = copy.deepcopy(getattr(self.observer, field))
            async def poll(*_args):
                setattr(self.observer, field, {'observer_nonce': 'other'} if field == 'scope' else 'other-invocation')
                return self.ready()
            self.reader.reset_mock()
            self.pure.side_effect = poll
            with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_observer_changed'):
                await self.wait()
            setattr(self.observer, field, original)

    async def test_changed_vid_is_rejected_even_if_old_preview_is_ready(self):
        async def poll(*_args):
            self.links[0]['ids'] = ['replaced-vid']
            return self.ready()
        self.pure.side_effect = poll
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_current_vid_changed'):
            await self.wait()

    async def test_missing_sdk_receipt_never_uses_preview_as_upload_proof(self):
        self.links.clear()
        self.reader.return_value = self.ready()
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_current_vid_unverified'):
            await self.wait()
        self.reader.assert_not_awaited()
        self.pure.assert_not_awaited()

    async def test_upload_state_must_still_bind_current_file_before_preview(self):
        for native in ({'scoped': False, 'ready': True, 'vid': 'current-vid'},
                       {'scoped': True, 'ready': True, 'invalid': True, 'vid': 'current-vid'},
                       {'scoped': True, 'ready': False, 'vid': 'current-vid'},
                       {'scoped': True, 'ready': True, 'vid': 'other-vid'}):
            self.native.side_effect = None
            self.native.return_value = native
            with self.assertRaises(workflow.PublicationBlocked):
                await self.wait()
        self.reader.assert_not_awaited()
        self.pure.assert_not_awaited()

    def restored(self):
        self.session._checkpoint_restored_at = time.time() - 10
        self.session._checkpoint_expires_at = time.time() + 60
        self.session._checkpoint_links = copy.deepcopy(self.links)
        self.observer.latest_invocation = None

    async def test_restored_checkpoint_uses_sealed_vid_without_new_sdk_invocation(self):
        self.restored()
        self.reader.return_value = self.ready()
        self.assertTrue(await self.wait())
        self.sdk.assert_not_awaited()
        self.reader.assert_awaited_once_with(self.page, ['current-vid'])

    async def test_restored_checkpoint_expiry_or_new_video_selection_stops(self):
        self.restored()
        self.session._checkpoint_expires_at = time.time() - 1
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_checkpoint_expired'):
            await self.wait()
        self.session._checkpoint_expires_at = time.time() + 60
        self.session.files.append({'name': 'other.mp4', 'type': 'video/mp4', 'size': 12,
                                   'selected_at': time.time()})
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_video_reselected'):
            await self.wait()
        self.reader.assert_not_awaited()
        self.sdk.assert_not_awaited()

    async def test_restored_checkpoint_link_change_during_wait_stops(self):
        self.restored()
        async def poll(*_args):
            self.session._checkpoint_links[0]['ids'] = ['another-vid']
            return self.ready()
        self.pure.side_effect = poll
        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_readiness_checkpoint_changed'):
            await self.wait()
        self.sdk.assert_not_awaited()

    async def test_real_visible_component_waits_for_decode_and_requires_https_positive_duration(self):
        from patchright.async_api import async_playwright
        from tests.test_dy_reopen import FIXTURE
        from uploader.draft_evidence import evaluate_main
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel='chromium')
            try:
                page = await browser.new_page()
                await page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
                await page.goto('https://creator.douyin.com/creator-micro/content/post/video')
                self.page = page
                self.session.page = page
                page._sau_draft_evidence = self.session
                await page.evaluate('fixture.video.readyState=1;fixture.video.duration=0', isolated_context=False)
                async def decoded():
                    await asyncio.sleep(.015)
                    await page.evaluate('fixture.video.readyState=2;fixture.video.duration=6', isolated_context=False)
                transition = asyncio.create_task(decoded())
                with patch.object(workflow, 'evaluate_main', evaluate_main):
                    self.assertTrue(await self.wait(timeout=.3))
                    await transition
                    for mutation in ("fixture.video.duration=0",
                                     "fixture.video.duration=6;fixture.video.src='http://fixture.invalid/preview.mp4';"
                                     "fixture.video.currentSrc=fixture.video.src;fixture.player.memoizedProps.playUrl=fixture.video.src"):
                        await page.evaluate(mutation, isolated_context=False)
                        with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_current_preview_not_ready'):
                            await self.wait(timeout=.04)
            finally:
                await browser.close()

    async def test_invalid_deadline_and_poll_are_rejected(self):
        for value in (True, 0, -1, float('nan'), float('inf')):
            with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_preview_wait_invalid'):
                await workflow._wait_dy_publication_preview(self.page, self.expected, self.session, timeout=value)
            with self.assertRaisesRegex(workflow.PublicationBlocked, 'douyin_preview_wait_invalid'):
                await workflow._wait_dy_publication_preview(self.page, self.expected, self.session, poll_interval=value)
