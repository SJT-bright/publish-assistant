"""Isolated checkpoint runner/adapters; every browser route is fulfilled locally."""
import asyncio
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import draft_execution as execution
from uploader.draft_evidence import (CheckpointError, DraftEvidenceSession, IDENTITY_KEYS,
                                    begin_draft_verification, end_draft_verification)
from uploader.draft_only import (checkpoint_after_upload, draft_resume_active, read_checkpoint_composer_media,
                                save_draft)
from tests import test_draft_evidence as evidence_fixtures
from workbench_model import build_platform_payload, preflight_payload

account = evidence_fixtures.account


class ExecutionCheckpointTests(unittest.IsolatedAsyncioTestCase):
    class Page:
        frames = []
        _sau_file_binding = True
        context = SimpleNamespace(browser=None)

        def on(self, *args):
            pass

        def remove_listener(self, *args):
            pass

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.directory = tempfile.TemporaryDirectory(prefix='execution-checkpoint-fixture-')
        self.root = Path(self.directory.name)
        self.video = self.root / 'synthetic.mp4'
        self.video.write_bytes(bytes(range(256)) * 4301 + b'synthetic whole file tail')
        self.key = self.root / 'private.key'
        self.key.write_bytes(bytes(range(32)))
        self.key.chmod(0o600)
        self.output = self.root / 'video-upload-checkpoint.json'
        self.receipt = self.root / 'receipt.json'
        self.expected = self.payload()

    async def asyncTearDown(self):
        self.directory.cleanup()

    def payload(self, platform='douyin'):
        raw = {'video': str(self.video), 'title': 'fixture title', 'description': 'fixture private description', 'tags': []}
        checked = preflight_payload(raw, [platform])
        self.assertEqual(checked['errors'], [])
        result = build_platform_payload(checked, platform, 'fixture-checkpoint-job', 'fixture-checkpoint-nonce')
        result['account'] = account(platform)
        result['expected_account_identity'] = account(platform)['identity']
        return result

    async def evidence(self, *, proven=False, expected=None, page=None):
        expected = expected or self.expected
        page = page or self.Page()
        session = DraftEvidenceSession(page, expected['platform_id'], expected,
                                       account_reader=lambda: account(expected['platform_id']))
        page._sau_draft_evidence = session
        with patch('uploader.xhs_evidence.install_xhs_observer', new=AsyncMock()):
            await session.initialize()
        if proven:
            self.prove(session)
        return session

    def prove(self, session):
        asset = session.assets['video']
        selected = time.time()
        session.files.append({**asset, 'selected_at': selected, 'type': 'video/mp4'})
        session.exchanges.append({'stage': 'complete', 'ids': ['fixture-current-video'], 'metadata': (asset['name'], asset['size']),
                                  'request_ids': [], 'body_sha256': None, 'observed_at': selected + .001})

    async def original(self):
        session = await self.evidence(proven=True)
        runtime = execution.ExecutionCheckpoint(self.key, self.output)
        runtime.bind(session.page, session)
        self.assertTrue(await checkpoint_after_upload(session.page))
        return session, runtime

    async def test_private_key_must_be_regular_owned_nofollow_0600_exact_32_bytes(self):
        self.assertEqual(execution._checkpoint_private_bytes(self.key, exact_size=32, max_size=32), bytes(range(32)))
        for value in (b'x' * 31, b'x' * 33):
            self.key.write_bytes(value)
            with self.assertRaises(CheckpointError):
                execution.ExecutionCheckpoint(self.key, self.output)
        self.key.write_bytes(bytes(range(32)))
        self.key.chmod(0o644)
        with self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(self.key, self.output)
        self.key.chmod(0o600)
        link = self.root / 'key-link'
        link.symlink_to(self.key)
        with self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(link, self.output)
        with self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(self.root, self.output)
        original = os.fstat
        def other_owner(descriptor):
            info = original(descriptor)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid + 1, st_size=info.st_size)
        with patch('draft_execution.os.fstat', side_effect=other_owner), self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(self.key, self.output)

    async def test_atomic_capture_is_0600_before_fill_and_preserves_valid_original(self):
        session, runtime = await self.original()
        first = self.output.read_bytes()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertNotIn('fixture private description', first.decode())
        self.assertNotIn(str(self.root), first.decode())
        self.assertTrue(await runtime.capture(session))
        self.assertEqual(self.output.read_bytes(), first)
        self.assertEqual(list(self.root.glob('.checkpoint-*.tmp')), [])

    async def test_missing_complete_upload_never_creates_checkpoint(self):
        session = await self.evidence()
        runtime = execution.ExecutionCheckpoint(self.key, self.output)
        runtime.bind(session.page, session)
        self.assertFalse(await checkpoint_after_upload(session.page))
        self.assertFalse(self.output.exists())
        self.prove(session)
        session.exchanges[0]['stage'] = 'allocated'
        self.assertFalse(await runtime.capture(session))
        self.assertFalse(self.output.exists())

    async def test_capture_failure_does_not_replace_or_mask_business_failure(self):
        session, runtime = await self.original()
        first = self.output.read_bytes()
        session.export_checkpoint = AsyncMock(side_effect=RuntimeError('synthetic unavailable observer'))
        self.assertTrue(await runtime.capture(session))
        self.assertEqual(first, self.output.read_bytes())
        self.output.unlink()
        self.assertFalse(await runtime.capture(session))
        self.assertFalse(self.output.exists())

    async def test_restore_checks_live_composer_and_continues_without_new_native_selection(self):
        old, _ = await self.original()
        await old.close()
        fresh = await self.evidence()
        runtime = execution.ExecutionCheckpoint(self.key, self.root / 'attempt2.json', self.output)
        runtime.bind(fresh.page, fresh)
        with patch('draft_execution.read_checkpoint_composer_media', new=AsyncMock(return_value={
                'source': 'platform_composer', 'video_count': 1, 'video_ids': ['fixture-current-video']})), \
             patch('draft_execution.emit_progress') as progress:
            result = await runtime.restore(fresh)
        self.assertTrue(result['video_upload_verified'])
        self.assertFalse(result['save_verified'])
        self.assertTrue(draft_resume_active(fresh.page))
        self.assertEqual([call.args for call in progress.call_args_list], [('video', 'completed'), ('upload', 'completed')])
        self.assertEqual(fresh.files, [])
        self.assertEqual(fresh.exchanges, [])
        self.assertTrue(await runtime.capture(fresh))
        self.assertEqual(runtime.proof['origin_at'], json.loads(runtime.output.read_text())['payload']['origin_at'])

    async def test_ambiguous_or_blob_only_current_video_refuses_and_does_not_import(self):
        await self.original()
        for media in (None, {'source': 'platform_composer', 'video_count': 2, 'video_ids': ['fixture-current-video']},
                      {'source': 'platform_composer', 'video_count': 1, 'video_ids': ['blob:fixture-local']}):
            fresh = await self.evidence()
            runtime = execution.ExecutionCheckpoint(self.key, self.root / 'attempt2.json', self.output)
            runtime.bind(fresh.page, fresh)
            with patch('draft_execution.read_checkpoint_composer_media', new=AsyncMock(return_value=media)):
                with self.assertRaises(execution.DraftInterrupted):
                    await runtime.restore(fresh)
            self.assertFalse(draft_resume_active(fresh.page))
            self.assertEqual(fresh.files, [])
            self.assertFalse(runtime.output.exists())

    async def test_save_marker_is_atomic_private_and_blocks_resume_but_proves_no_success(self):
        session, runtime = await self.original()
        checkpoint_before = self.output.read_bytes()
        await runtime.before_save(session)
        marker = self.output.with_suffix('.save-started.json')
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(json.loads(marker.read_text())['phase'], 'save_started')
        self.assertNotIn('verification', marker.read_text())
        self.assertFalse(self.receipt.exists())
        with self.assertRaises(CheckpointError):
            await runtime.before_save(session)
        with self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(self.key, self.root / 'attempt2.json', self.output)
        self.assertFalse(await runtime.capture(session))
        self.assertEqual(checkpoint_before, self.output.read_bytes())

    async def test_resume_save_marks_both_original_and_attempt_before_action(self):
        _, _ = await self.original()
        fresh = await self.evidence()
        runtime = execution.ExecutionCheckpoint(self.key, self.root / 'attempt2.json', self.output)
        runtime.bind(fresh.page, fresh)
        with patch('draft_execution.read_checkpoint_composer_media', new=AsyncMock(return_value={
                'source': 'platform_composer', 'video_count': 1, 'video_ids': ['fixture-current-video']})):
            await runtime.restore(fresh)
        await runtime.before_save(fresh)
        self.assertTrue(self.output.with_suffix('.save-started.json').exists())
        self.assertTrue(runtime.output.with_suffix('.save-started.json').exists())

    async def test_atomic_claim_never_overwrites_existing_marker_or_symlink(self):
        marker = self.output.with_suffix('.save-started.json')
        execution._checkpoint_atomic_create(marker, {'prior': True})
        first = marker.read_bytes()
        with self.assertRaises(FileExistsError):
            execution._checkpoint_atomic_create(marker, {'prior': False})
        self.assertEqual(first, marker.read_bytes())
        link = self.root / 'link-marker.json'
        link.symlink_to(marker)
        with self.assertRaises(FileExistsError):
            execution._checkpoint_atomic_create(link, {'overwritten': True})
        self.assertEqual(first, marker.read_bytes())
        self.assertEqual(list(self.root.glob('.checkpoint-*.tmp')), [])

    async def test_marker_write_failure_stops_real_save_entry_before_action(self):
        session, _ = await self.original()
        action = AsyncMock()
        with patch('uploader.draft_only.install_draft_guard', new=AsyncMock()), \
             patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()), \
             patch('uploader.draft_only._draft_action', new=action), \
             patch('draft_execution._checkpoint_atomic_create', side_effect=OSError('synthetic disk error')):
            with self.assertRaises(execution.DraftInterrupted):
                await save_draft(session.page, '抖音', self.expected['title'], expected=self.expected)
        action.assert_not_awaited()
        self.assertIsNone(session.action_at)
        self.assertFalse(self.output.with_suffix('.save-started.json').exists())

    async def test_incomplete_checkpoint_cli_config_refuses_before_browser(self):
        for config in ({'checkpoint_key_file': self.key}, {'checkpoint_output': self.output},
                       {'resume_checkpoint': self.output}):
            browser = AsyncMock(side_effect=AssertionError('browser must not start'))
            with patch('draft_execution.browser_session', new=browser), self.assertRaises(ValueError):
                await execution.stage('douyin', self.expected, self.receipt, **config)
            browser.assert_not_called()

    async def test_cli_wires_all_checkpoint_args(self):
        payload = self.root / 'payload.json'
        payload.write_text(json.dumps(self.expected))
        stage = AsyncMock(return_value={'status': 'local_draft_verified'})
        argv = ['draft_runner.py', 'draft', '--platform', 'douyin', '--payload', str(payload), '--receipt', str(self.receipt),
                '--embedded', '--checkpoint-key-file', str(self.key), '--checkpoint-output', str(self.output),
                '--resume-checkpoint', str(self.root / 'original.json')]
        with patch.object(sys, 'argv', argv), patch('draft_execution.stage', new=stage):
            await execution.main()
        kwargs = stage.await_args.kwargs
        self.assertEqual(kwargs, {'checkpoint_key_file': str(self.key), 'checkpoint_output': str(self.output),
                                  'resume_checkpoint': str(self.root / 'original.json')})
        self.assertTrue(stage.await_args.args[3])

    async def test_stage_exports_before_content_failure_and_resume_does_not_navigate(self):
        payload = self.payload('xiaohongshu')
        page = self.Page()
        calls = []
        @asynccontextmanager
        async def browser(*args, **kwargs):
            yield object(), object(), page.context, page, None
        async def watched(platform, page, owner, coroutine, **kwargs):
            return await coroutine
        async def opened(*args):
            calls.append('navigation')
        async def uploaded(app, playwright):
            session = app.embedded.page._sau_draft_evidence
            if not draft_resume_active(app.embedded.page):
                calls.append('video-input')
                self.prove(session)
            await checkpoint_after_upload(app.embedded.page)
            self.assertTrue(Path(checkpoint_output[0]).is_file())
            calls.append('content')
            raise RuntimeError('fixture content stop')
        from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
        checkpoint_output = [self.output]
        with patch('draft_execution.browser_session', new=browser), patch('draft_execution.open_creator', new=opened), \
             patch('draft_execution.verify_session', new=AsyncMock(return_value=account('xiaohongshu'))), \
             patch('draft_execution.inspect_session', new=AsyncMock(return_value=account('xiaohongshu'))), \
             patch('draft_execution.current_owner_state', new=AsyncMock(return_value={})), \
             patch('draft_execution.read_marker', return_value={}), patch('draft_execution.write_marker'), \
             patch('draft_execution.install_draft_guard', new=AsyncMock()), \
             patch('draft_execution.run_with_session_watch', new=watched), \
             patch('material_probe.media_info', return_value={}), \
             patch('uploader.xhs_evidence.install_xhs_observer', new=AsyncMock()), \
             patch.object(XiaoHongShuVideo, 'upload', new=uploaded):
            with self.assertRaisesRegex(RuntimeError, 'fixture content stop'):
                await execution.stage('xiaohongshu', payload, self.receipt, checkpoint_key_file=self.key, checkpoint_output=self.output)
            first = self.output.read_bytes()
            checkpoint_output[0] = self.root / 'attempt2.json'
            with patch('draft_execution.read_checkpoint_composer_media', new=AsyncMock(return_value={
                    'source': 'platform_composer', 'video_count': 1, 'video_ids': ['fixture-current-video']})):
                with self.assertRaisesRegex(RuntimeError, 'fixture content stop'):
                    await execution.stage('xiaohongshu', payload, self.root / 'attempt2-receipt.json', checkpoint_key_file=self.key,
                                          checkpoint_output=checkpoint_output[0], resume_checkpoint=self.output)
            self.assertEqual(self.output.read_bytes(), first)
        self.assertEqual(calls.count('video-input'), 1)
        self.assertEqual(calls.count('navigation'), 1)
        self.assertEqual(calls.count('content'), 2)


class AdapterResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_adapter_real_methods_skip_video_input_wait_and_tc_navigation(self):
        from uploader.douyin_uploader.main import DouYinVideo
        from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
        from uploader.ks_uploader.main import KSVideo
        from uploader.tencent_uploader.main import TencentVideo
        editor = SimpleNamespace(fill=AsyncMock(), inner_text=AsyncMock(return_value='fixture caption'))
        frame = SimpleNamespace(locator=lambda selector: SimpleNamespace(all=AsyncMock(return_value=[editor])))
        page = SimpleNamespace(_sau_draft_evidence=SimpleNamespace(closed=False, _checkpoint_restored_at=time.time()),
                               frames=[frame], goto=AsyncMock(side_effect=AssertionError('resume navigation')), context=object())
        for cls, method in ((DouYinVideo, '_upload_draft_content'), (XiaoHongShuVideo, 'upload_video_content'),
                            (KSVideo, '_upload_draft_content'), (TencentVideo, 'upload')):
            with self.subTest(adapter=cls.__name__):
                app = cls.__new__(cls)
                app.draft_only, app.title, app.desc, app.tags = True, 'fixture title', 'fixture body', []
                app.short_title = ''
                app.draft_expected = {'platform_fields': {'caption': 'fixture caption'}}
                app.thumbnail_path = app.thumbnail_landscape_path = app.thumbnail_portrait_path = None
                app.productLink = app.productTitle = None
                app.fill_meta = AsyncMock()
                app.fill_title_and_description = AsyncMock()
                app.close_guide_overlay = AsyncMock()
                app.validate_upload_args = AsyncMock()
                app.embedded = SimpleNamespace(page=page, context=page.context, browser=object())
                app.open_upload_page = AsyncMock(side_effect=AssertionError('resume navigation'))
                app.upload_video_file = AsyncMock(side_effect=AssertionError('second video upload'))
                app.wait_for_upload_complete = AsyncMock(side_effect=AssertionError('second video wait'))
                app.prepare_video_for_publish = AsyncMock()
                app.set_short_title = AsyncMock()
                app.submit_publish = AsyncMock()
                with patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()), \
                     patch('uploader.draft_only.install_draft_guard', new=AsyncMock()), \
                     patch('uploader.draft_only.save_draft', new=AsyncMock()) as save, \
                     patch('uploader.draft_only.wait_draft_upload', new=AsyncMock(side_effect=AssertionError('second video wait'))) as wait, \
                     patch('uploader.draft_only.checkpoint_after_upload', new=AsyncMock(return_value=True)) as capture:
                    await getattr(app, method)(object() if cls is TencentVideo else page)
                wait.assert_not_awaited()
                capture.assert_awaited_once()
                page.goto.assert_not_awaited()
                if cls is TencentVideo:
                    app.open_upload_page.assert_not_awaited()
                    app.upload_video_file.assert_not_awaited()
                    app.wait_for_upload_complete.assert_not_awaited()
                    app.prepare_video_for_publish.assert_awaited_once()
                    app.set_short_title.assert_awaited_once()
                    app.submit_publish.assert_awaited_once()
                else:
                    save.assert_awaited_once()

    async def test_bilibili_real_method_skips_native_input_and_wait_but_fills(self):
        page = SimpleNamespace(_sau_draft_evidence=SimpleNamespace(closed=False, _checkpoint_restored_at=time.time()))
        title = SimpleNamespace(fill=AsyncMock(), input_value=AsyncMock(return_value='fixture title'))
        desc = SimpleNamespace(fill=AsyncMock())
        with patch('draft_execution.find_control', new=AsyncMock(return_value=title)) as find_title, \
             patch('draft_execution.find_control_priority', new=AsyncMock(return_value=desc)) as find_desc, \
             patch('draft_execution.set_local_files', new=AsyncMock(side_effect=AssertionError('second native upload'))) as upload, \
             patch('uploader.draft_only.wait_draft_upload', new=AsyncMock(side_effect=AssertionError('second wait'))) as wait, \
             patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()), \
             patch('draft_execution.checkpoint_after_upload', new=AsyncMock(return_value=True)) as capture, \
             patch('draft_execution.save_draft', new=AsyncMock()) as save:
            await execution.bilibili_draft({'video': '/synthetic/video.mp4', 'title': 'fixture title', 'description': 'fixture body', 'tags': []},
                                          None, page=page)
        upload.assert_not_awaited()
        wait.assert_not_awaited()
        find_title.assert_awaited_once()
        find_desc.assert_awaited_once()
        title.fill.assert_awaited_once_with('fixture title')
        desc.fill.assert_awaited_once_with('fixture body')
        capture.assert_awaited_once()
        save.assert_awaited_once()


class ExecutionCheckpointBrowserTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = evidence_fixtures.BrowserEvidenceTests.asyncSetUp
    asyncTearDown = evidence_fixtures.BrowserEvidenceTests.asyncTearDown
    expected = evidence_fixtures.BrowserEvidenceTests.expected
    setup_dy = evidence_fixtures.BrowserEvidenceTests.setup_dy
    wait_fingerprints = evidence_fixtures.BrowserEvidenceTests.wait_fingerprints

    def runtime_paths(self, filename='upload-checkpoint.json'):
        key = Path(self.directory.name) / 'private.key'
        key.write_bytes(bytes(range(32)))
        key.chmod(0o600)
        return key, Path(self.directory.name) / filename

    async def wait_upload_proof(self, session):
        # setup_dy waits for the native SHA binding, whereas the normal adapter
        # additionally waits for the upload UI. Fulfilled HTTP can finish after
        # that binding, so reproduce the completed-upload condition explicitly.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            await session.drain()
            if session.upload_links():
                return
            await asyncio.sleep(.02)
        self.fail('fulfilled native upload completion did not arrive')

    async def test_native_browser_upload_capture_resume_save_reopen_and_no_second_upload(self):
        expected, old = await self.setup_dy()
        await self.wait_upload_proof(old)
        key, output = self.runtime_paths()
        runtime = execution.ExecutionCheckpoint(key, output)
        runtime.bind(self.page, old)
        self.assertTrue(await checkpoint_after_upload(self.page))
        await self.page.evaluate("window.media.dataset.videoId='new-video';window.preview.dataset.coverId='default-cover'", isolated_context=False)
        await self.page.locator('#body').fill('')
        await end_draft_verification(self.page)
        fresh = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        resumed = execution.ExecutionCheckpoint(key, output.parent / 'attempt2.json', output)
        resumed.bind(self.page, fresh)
        await resumed.restore(fresh)
        self.assertEqual(fresh.files, [])
        self.assertTrue(await checkpoint_after_upload(self.page))
        await self.page.locator('#body').fill(expected['platform_fields']['caption'])
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=2, receipt_timeout=15, expected=expected)
        self.assertEqual(result['status'], 'local_draft_verified')
        self.assertTrue(all(result['verification'].values()))
        self.assertEqual(sum(path == '/upload/video' for _, path in self.calls), 1)
        for marker in resumed.markers():
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        with self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(key, output.parent / 'attempt3.json', output)

    async def test_save_marker_exists_before_click_and_unknown_result_cannot_be_retried(self):
        expected, session = await self.setup_dy()
        await self.wait_upload_proof(session)
        key, output = self.runtime_paths()
        runtime = execution.ExecutionCheckpoint(key, output)
        runtime.bind(self.page, session)
        self.assertTrue(await checkpoint_after_upload(self.page))
        clicked = []
        async def failed_action(*args):
            clicked.append(True)
            self.assertTrue(output.with_suffix('.save-started.json').exists())
            raise RuntimeError('synthetic unknown save result')
        with patch('uploader.draft_only._draft_action', new=failed_action):
            with self.assertRaisesRegex(RuntimeError, 'synthetic unknown save result'):
                await save_draft(self.page, '抖音', expected['title'], button_timeout=1, receipt_timeout=1, expected=expected)
        self.assertEqual(clicked, [True])
        self.assertFalse(await runtime.capture(session))
        with self.assertRaises(CheckpointError):
            execution.ExecutionCheckpoint(key, output.parent / 'attempt2.json', output)
        fresh = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        with patch('uploader.draft_only._draft_action', new=AsyncMock()) as action:
            with self.assertRaises(CheckpointError):
                await save_draft(self.page, '抖音', expected['title'], button_timeout=1, receipt_timeout=1, expected=expected)
        action.assert_not_awaited()

    async def test_read_only_composer_rejects_blob_multiple_and_accepts_open_shadow_exact_resource(self):
        expected = self.expected()
        await self.page.goto('http://127.0.0.1:45997/resume-composer')
        proof = {'identity': {'platform_id': 'tencent'}, 'upload_links': [
            {'kind': 'video', 'sha256': expected['video_sha256'], 'ids': ['known-video'], 'complete': True}]}
        await self.page.set_content('<video src="blob:fixture-known-video" style="width:100px;height:30px"></video>')
        self.assertIsNone(await read_checkpoint_composer_media(self.page, proof))
        await self.page.set_content('<div data-video-id="known-video">one</div><div data-video-id="other-video">two</div>')
        self.assertIsNone(await read_checkpoint_composer_media(self.page, proof))
        await self.page.set_content('<div id="host"></div>')
        await self.page.evaluate("document.getElementById('host').attachShadow({mode:'open'}).innerHTML='<div data-video-id=known-video style=\"width:100px;height:30px\">video</div>'", isolated_context=False)
        result = await read_checkpoint_composer_media(self.page, proof)
        self.assertEqual(result, {'source': 'platform_composer', 'video_count': 1, 'video_ids': ['known-video']})


if __name__ == '__main__':
    unittest.main()
