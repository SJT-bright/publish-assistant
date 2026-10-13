"""Content-free protocol and routed local adapter scenarios, no live accounts."""
import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from uploader import draft_only, draft_progress
from uploader.draft_session import DraftInterrupted


def events(output):
    return [json.loads(line[len('DRAFT_PROGRESS='):]) for line in output.splitlines()
            if line.startswith('DRAFT_PROGRESS=')]


def stage_events(rows):
    """Keep legacy stage assertions separate from additive operation details."""
    return [row for row in rows if 'operation' not in row and 'plan' not in row]


def configured(platform='douyin'):
    return patch.dict(os.environ, {'SAU_PROGRESS_JOB_ID': 'fixture-job-1', 'SAU_PROGRESS_PLATFORM': platform})


class ProtocolTests(unittest.TestCase):
    def tearDown(self):
        with patch.dict(os.environ, {'SAU_PROGRESS_JOB_ID': '', 'SAU_PROGRESS_PLATFORM': ''}):
            draft_progress.configure('douyin')

    def test_exact_task_binding_and_one_line_fixed_details(self):
        stream = io.StringIO()
        with configured(), redirect_stdout(stream):
            self.assertTrue(draft_progress.configure('douyin'))
            with draft_progress.step('video'):
                pass
            draft_progress.emit('cover', 'skipped')
        rows = events(stream.getvalue())
        self.assertEqual([(row['stage'], row['state']) for row in rows],
                         [('video', 'running'), ('video', 'completed'), ('cover', 'skipped')])
        for row in rows:
            self.assertEqual(set(row), {'v', 'job_id', 'platform', 'stage', 'state', 'detail'})
            self.assertEqual((row['v'], row['job_id'], row['platform']), (1, 'fixture-job-1', 'douyin'))
        self.assertIn('平台默认封面', rows[-1]['detail'])

    def test_disabled_without_configuration_or_with_wrong_platform_or_unsafe_id(self):
        for environment, platform in (({}, 'douyin'),
                                     ({'SAU_PROGRESS_JOB_ID': 'fixture-job-1', 'SAU_PROGRESS_PLATFORM': 'douyin'}, 'bilibili'),
                                     ({'SAU_PROGRESS_JOB_ID': '/private/token\nsecret', 'SAU_PROGRESS_PLATFORM': 'douyin'}, 'douyin')):
            with self.subTest(environment=environment, platform=platform), patch.dict(os.environ, environment, clear=True):
                stream = io.StringIO()
                with redirect_stdout(stream):
                    self.assertFalse(draft_progress.configure(platform))
                    with draft_progress.step('content'):
                        pass
                self.assertEqual(stream.getvalue(), '')

    def test_exception_is_identical_and_never_copied_into_protocol(self):
        private_error = RuntimeError('/private/material.mov secret-description uid=123 token=abc')
        stream = io.StringIO()
        with configured(), redirect_stdout(stream):
            draft_progress.configure('douyin')
            with self.assertRaises(RuntimeError) as caught:
                with draft_progress.step('upload'):
                    raise private_error
        self.assertIs(caught.exception, private_error)
        self.assertEqual([row['state'] for row in events(stream.getvalue())], ['running', 'failed'])
        for secret in ('material.mov', 'secret-description', 'uid=123', 'token=abc'):
            self.assertNotIn(secret, stream.getvalue())

    def test_nested_wait_does_not_finish_outer_cover(self):
        stream = io.StringIO()
        with configured(), redirect_stdout(stream):
            draft_progress.configure('douyin')
            with draft_progress.step('cover'):
                with draft_progress.step('cover'):
                    pass
                self.assertEqual([row['state'] for row in events(stream.getvalue())], ['running'])
        self.assertEqual([row['state'] for row in events(stream.getvalue())], ['running', 'completed'])

    def test_closed_session_scope_does_not_fail_after_later_upload_error(self):
        stream = io.StringIO()
        with configured(), redirect_stdout(stream):
            draft_progress.configure('douyin')
            with self.assertRaises(RuntimeError):
                with draft_progress.step('session') as session:
                    session.complete()
                    with draft_progress.step('upload'):
                        raise RuntimeError('synthetic upload failure')
        self.assertEqual([(row['stage'], row['state']) for row in events(stream.getvalue())],
                         [('session', 'running'), ('session', 'completed'), ('upload', 'running'), ('upload', 'failed')])

    def test_progress_output_failure_is_observational_only(self):
        with configured():
            draft_progress.configure('douyin')
            with patch('builtins.print', side_effect=BrokenPipeError):
                with draft_progress.step('video'):
                    self.assertFalse(draft_progress.emit('upload', 'running'))


class SaveProgressTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, verified=False, candidate=False, action_error=None):
        expected = {'title': 'private-title', 'job_id': 'fixture-job-1'}
        record = {'id': 'private-draft-id', 'fresh': True} if candidate else None
        observer = SimpleNamespace(wait_saved_ack=AsyncMock(return_value=True),
                                   pre_reopen_verified=AsyncMock(return_value=True))
        session = SimpleNamespace(platform='bilibili', expected=expected, issues=[], saves=[], account={},
                                  files=[], exchanges=[], reads=[], bili_observer=observer,
                                  candidate=AsyncMock(return_value=record), upload_links=lambda: [],
                                  reopened_record=AsyncMock(return_value=record), read_account=AsyncMock(return_value={}),
                                  close=AsyncMock())
        page = SimpleNamespace(_sau_draft_evidence=session, context=object(), url='http://fixture.invalid/editor')
        checks = dict.fromkeys(draft_only.CHECKS, verified)
        result_check = (checks, [] if verified else ['synthetic_missing_media'])
        stream = io.StringIO()
        with configured('bilibili'), redirect_stdout(stream):
            draft_progress.configure('bilibili')
            with patch.object(draft_only, 'install_draft_guard', AsyncMock()), \
                 patch.object(draft_only, 'assert_draft_safe', AsyncMock()), \
                 patch.object(draft_only, '_draft_action', AsyncMock(side_effect=action_error, return_value='保存草稿')), \
                 patch.object(draft_only, '_reopen', AsyncMock(return_value=True)), \
                 patch.object(draft_only, '_ui_fields', AsyncMock(return_value=True)), \
                 patch.object(draft_only, '_ui_assets', AsyncMock(return_value=True)), \
                 patch('uploader.bili_reopen.bili_open_draft_management', AsyncMock(return_value=True)), \
                 patch('uploader.bili_controls.bili_reopened_ui_verified', AsyncMock(return_value=True)), \
                 patch.object(draft_only, 'verify_evidence', return_value=result_check):
                try:
                    result = await draft_only.save_draft(page, 'B站', 'private-title', receipt_timeout=.01, expected=expected)
                except BaseException:
                    self.rows = events(stream.getvalue())
                    raise
        self.rows = events(stream.getvalue())
        self.assertNotIn('private-title', '\n'.join(json.dumps(row) for row in self.rows))
        self.assertNotIn('private-draft-id', '\n'.join(json.dumps(row) for row in self.rows))
        session.close.assert_awaited_once()
        return result

    async def test_button_without_bound_save_evidence_never_completes_save(self):
        result = await self.scenario()
        self.assertEqual(result['status'], 'result_unknown')
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(self.rows)], [('save', 'running'), ('save', 'failed')])

    async def test_partial_saved_record_never_completes_verify(self):
        result = await self.scenario(candidate=True)
        self.assertEqual(result['status'], 'saved_unverified')
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(self.rows)],
                         [('save', 'running'), ('save', 'completed'), ('verify', 'running'), ('verify', 'failed')])

    async def test_verified_response_reopen_content_media_and_account_complete_verify(self):
        result = await self.scenario(verified=True, candidate=True)
        self.assertEqual(result['status'], 'platform_draft_verified')
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(self.rows)],
                         [('save', 'running'), ('save', 'completed'), ('verify', 'running'), ('verify', 'completed')])

    async def test_save_action_exception_preserved_and_failed(self):
        failure = DraftInterrupted('private-token', 'risk_paused')
        with self.assertRaises(DraftInterrupted) as caught:
            await self.scenario(action_error=failure)
        self.assertIs(caught.exception, failure)
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(self.rows)], [('save', 'running'), ('save', 'failed')])


class SessionProgressTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, connect_error=None, upload_error=None):
        import draft_execution
        failure = connect_error or upload_error
        context, page = SimpleNamespace(browser=object()), SimpleNamespace()
        expected = {'video': 'synthetic-only.mp4', 'title': 'synthetic-title'}

        @asynccontextmanager
        async def browser_session(*args, **kwargs):
            if connect_error:
                raise connect_error
            yield None, context.browser, context, page, None

        async def watched(platform, page, account, coroutine, **kwargs):
            return await coroutine

        stream = io.StringIO()
        with configured('bilibili'), redirect_stdout(stream), \
             patch.object(draft_execution, 'prepare_expected', return_value=expected), \
             patch('material_probe.media_info', return_value={}), \
             patch.object(draft_execution, 'browser_session', browser_session), \
             patch.object(draft_execution, 'open_creator', AsyncMock()), \
             patch.object(draft_execution, 'verify_session', AsyncMock(return_value={'identity': 'synthetic-owner'})), \
             patch.object(draft_execution, 'current_owner_state', AsyncMock(return_value={})), \
             patch('platform_limits.check_fields', return_value={'errors': []}), \
             patch.object(draft_execution, 'read_marker', return_value={}), \
             patch.object(draft_execution, 'write_marker'), \
             patch.object(draft_execution, 'install_draft_guard', AsyncMock()), \
             patch.object(draft_only, 'begin_draft_verification', AsyncMock()), \
             patch.object(draft_only, 'end_draft_verification', AsyncMock()), \
             patch.object(draft_execution, 'run_with_session_watch', watched), \
             patch.object(draft_execution, 'bilibili_draft', AsyncMock(side_effect=upload_error)):
            with self.assertRaises(RuntimeError) as caught:
                await draft_execution.stage('bilibili', {}, 'unused.json')
        self.assertIs(caught.exception, failure)
        return events(stream.getvalue())

    async def test_connection_failure_reports_session_failed_without_replacing_error(self):
        rows = await self.scenario(connect_error=RuntimeError('synthetic connection error'))
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(rows)], [('session', 'running'), ('session', 'failed')])

    async def test_confirmed_borrowed_session_is_not_relabelled_failed_after_later_error(self):
        rows = await self.scenario(upload_error=RuntimeError('synthetic downstream error'))
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(rows)], [('session', 'running'), ('session', 'completed')])


class TencentOrderProgressTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, cover_error=None):
        from uploader.tencent_uploader.main import TencentVideo
        adapter = TencentVideo('synthetic-title', 'unused.mp4', [], 0, 'unused.json',
                               thumbnail_landscape_path='unused-cover.jpg')
        adapter.draft_only = True
        page = object()
        adapter.embedded = SimpleNamespace(browser=object(), context=object(), page=page)
        for name in ('validate_upload_args', 'open_upload_page', 'upload_video_file',
                     'wait_for_upload_complete', 'prepare_video_for_publish', 'submit_publish'):
            setattr(adapter, name, AsyncMock())
        async def fill_description_and_readback(*args):
            with draft_progress.part('content', 'description'):
                pass
            with draft_progress.part('content', 'readback'):
                pass
        adapter.prepare_video_for_publish = AsyncMock(side_effect=fill_description_and_readback)
        adapter.set_thumbnail = AsyncMock(side_effect=cover_error)
        stream = io.StringIO()

        async def short_title(*args):
            # Cover has ended; the user must see content active while short title fills.
            self.assertEqual([(row['stage'], row['state']) for row in stage_events(events(stream.getvalue()))][-1],
                             ('content', 'running'))
        adapter.set_short_title = AsyncMock(side_effect=short_title)
        with configured('tencent'), redirect_stdout(stream), \
             patch.object(draft_only, 'install_draft_guard', AsyncMock()), \
             patch.object(draft_only, 'assert_draft_safe', AsyncMock()):
            draft_progress.configure('tencent')
            if cover_error:
                with self.assertRaises(RuntimeError) as caught:
                    await adapter.upload(None)
                self.assertIs(caught.exception, cover_error)
            else:
                await adapter.upload(None)
        return adapter, events(stream.getvalue())

    async def test_cover_failure_remains_cover_failed_and_content_never_completes(self):
        adapter, rows = await self.scenario(RuntimeError('synthetic cover error'))
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(rows)],
                         [('content', 'running'), ('cover', 'running'), ('cover', 'failed')])
        from workbench_progress import apply_progress, parse_progress, public_progress
        item = {'status': 'running'}
        for index, row in enumerate(rows, 1):
            event = parse_progress('DRAFT_PROGRESS=' + json.dumps(row), 'fixture-job-1', 'tencent')
            self.assertIsNotNone(event)
            self.assertTrue(apply_progress(item, event, index))
        content = public_progress(item['progress'])['operations']['content']
        self.assertEqual(content['completed'], ['description', 'readback'])
        self.assertNotIn('title', content['completed'])
        adapter.set_short_title.assert_not_awaited()
        adapter.submit_publish.assert_not_awaited()

    async def test_short_title_resumes_content_after_cover_before_it_completes(self):
        adapter, rows = await self.scenario()
        self.assertEqual([(row['stage'], row['state']) for row in stage_events(rows)],
                         [('content', 'running'), ('cover', 'running'), ('cover', 'completed'),
                          ('content', 'running'), ('content', 'completed')])
        from workbench_progress import apply_progress, parse_progress, public_progress
        item = {'status': 'running'}
        for index, row in enumerate(rows, 1):
            event = parse_progress('DRAFT_PROGRESS=' + json.dumps(row), 'fixture-job-1', 'tencent')
            self.assertIsNotNone(event)
            self.assertTrue(apply_progress(item, event, index))
        content = public_progress(item['progress'])['operations']['content']
        self.assertEqual(content['plan'], ['description', 'readback', 'title'])
        self.assertEqual(content['completed'], ['description', 'readback', 'title'])
        self.assertEqual(content['completed'].count('description'), 1)
        self.assertEqual(content['completed'].count('readback'), 1)
        self.assertEqual(content['completed'].count('title'), 1)
        self.assertEqual([row['plan'] for row in rows if row.get('plan') and row['stage'] == 'content'],
                         [['description', 'readback', 'title'], ['description', 'readback', 'title']])
        adapter.set_short_title.assert_awaited_once()
        adapter.submit_publish.assert_awaited_once()

    async def test_real_upload_wait_timeout_keeps_upload_unfinished(self):
        stream = io.StringIO()
        page = SimpleNamespace(frames=[], is_closed=lambda: False)
        with configured('tencent'), redirect_stdout(stream):
            draft_progress.configure('tencent')
            with self.assertRaises(DraftInterrupted):
                with draft_progress.step('upload'):
                    await draft_only.wait_draft_upload(page, '视频号', ['.ready'], timeout=.01, poll_interval=.001)
        self.assertEqual([(row['stage'], row['state']) for row in events(stream.getvalue())],
                         [('upload', 'running'), ('upload', 'failed')])


class TencentLiveMarkupTests(unittest.TestCase):
    """Official markup-shaped local fixtures; no live upload or account."""
    def test_completed_preview_playback_slider_and_actual_editable_node(self):
        from uploader.tencent_uploader.main import TencentVideo

        async def scenario():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, channel='chrome')
                context = await browser.new_context()
                await context.route('**/*', lambda route: route.abort())
                page = await context.new_page()
                adapter = TencentVideo('完整标题', 'unused.mp4', ['话题'], 0, 'unused.json', desc='完整正文')
                adapter.draft_only = True
                adapter.draft_upload_timeout = 3
                adapter.draft_expected = {'platform_fields': {'caption': '完整标题\n完整正文\n#话题'}}

                async def material_state(actual_page):
                    if await actual_page.locator('.media-status-content .media-progress').count():
                        return dict(recognized=True, count=1, video_count=1, done_count=0,
                                    all_uploaded=False, all_cover_cropped=False)
                    if await actual_page.locator('.post-media-preview-wrap video').count() == 1:
                        return dict(recognized=True, count=1, video_count=1, done_count=1,
                                    all_uploaded=True, all_cover_cropped=True)
                    return dict(recognized=True, count=0, video_count=0, done_count=0,
                                all_uploaded=False, all_cover_cropped=False)

                try:
                    # Material readiness comes from a separately tested observer
                    # contract; this fixture focuses on preview/editable-node flow.
                    with patch('uploader.tc_editor.read_tc_material_state', side_effect=material_state):
                        # A completed observer state paired with the normal preview
                        # must allow the real editor and field readback path.
                        await page.set_content('''<div class="post-media-preview-wrap"><div class="media-wrap">
                          <video src="blob:synthetic-video"></video><div class="progress">播放进度</div></div>
                          <div class="media-opr"><div class="tag-inner">删除</div></div></div>
                          <div class="input-editor"><div id="editor" contenteditable="true"></div></div>''')
                        await adapter.wait_for_upload_complete(page)
                        await adapter.prepare_video_for_publish(page)
                        self.assertEqual(await page.locator('#editor').inner_text(), adapter.draft_expected['platform_fields']['caption'])

                        # The same preview cannot finish while the observer reports
                        # transfer/processing still active.
                        adapter.draft_upload_timeout = .1
                        await page.set_content('''<div class="post-media-preview-wrap"><video src="blob:synthetic-video"></video>
                          <div class="media-opr"><div class="tag-inner">删除</div></div></div>
                          <div class="media-status-content"><div class="media-progress">50%</div></div>''')
                        with self.assertRaises(RuntimeError):
                            await adapter.wait_for_upload_complete(page)

                        # User content can say success; it does not qualify as video readiness.
                        await page.set_content('<div class="input-editor" contenteditable="true">上传成功</div>')
                        with self.assertRaises(RuntimeError):
                            await adapter.wait_for_upload_complete(page)

                        # Ambiguous editable descendants are rejected before any write.
                        await page.set_content('''<div class="input-editor"><div contenteditable="true"></div>
                          <div contenteditable="true"></div></div>''')
                        with self.assertRaisesRegex(RuntimeError, '唯一'):
                            await adapter.prepare_video_for_publish(page)
                        self.assertEqual(await page.locator('[contenteditable]').all_text_contents(), ['', ''])
                finally:
                    await context.close()
                    await browser.close()
        asyncio.run(scenario())


class AdapterProgressTests(unittest.TestCase):
    def test_four_real_adapter_branches_in_routed_local_browser(self):
        from tests.test_draft_upload_safety import DraftUploadSafetyTests
        helpers = DraftUploadSafetyTests()

        async def scenario(browser, video):
            for platform in ('douyin', 'xiaohongshu', 'kuaishou', 'tencent'):
                with self.subTest(platform=platform):
                    context, page, adapter = await helpers.setup_adapter(browser, video, platform)
                    stream = io.StringIO()
                    save = AsyncMock()
                    try:
                        with configured(platform), redirect_stdout(stream):
                            draft_progress.configure(platform)
                            with patch.object(draft_only, 'save_draft', save):
                                if platform == 'tencent':
                                    adapter.embedded = SimpleNamespace(browser=browser, context=context, page=page)
                                    adapter.validate_upload_args = AsyncMock()
                                    async def open_fixture(page):
                                        await page.goto('https://fixture.invalid/tencent')
                                    adapter.open_upload_page = AsyncMock(side_effect=open_fixture)
                                    reader = page._sau_draft_evidence.read_tc_material_state
                                    with patch('uploader.tc_editor.read_tc_material_state', reader):
                                        await adapter.upload(None)
                                else:
                                    await helpers.invoke(platform, page, adapter)
                        rows = events(stream.getvalue())
                        completed = [row['stage'] for row in rows if row['state'] == 'completed']
                        self.assertEqual(completed, ['video', 'upload', 'content'])
                        self.assertIn(('cover', 'skipped'), [(row['stage'], row['state']) for row in rows])
                        observed_operations = {(row['stage'], row['operation'], row['operation_state'])
                                               for row in rows if 'operation' in row}
                        expected_operations = {
                            'douyin': {('video', 'locate', 'completed'), ('video', 'select', 'completed'),
                                       ('upload', 'transfer', 'completed'), ('content', 'title', 'completed'),
                                       ('content', 'description', 'completed'), ('content', 'readback', 'completed')},
                            'xiaohongshu': {('video', 'locate', 'completed'), ('video', 'select', 'completed'),
                                            ('upload', 'transfer', 'completed'), ('content', 'title', 'completed'),
                                            ('content', 'description', 'completed'), ('content', 'topics', 'completed'),
                                            ('content', 'readback', 'completed')},
                            'kuaishou': {('video', 'locate', 'completed'), ('video', 'select', 'completed'),
                                         ('upload', 'transfer', 'completed'), ('content', 'description', 'completed'),
                                         ('content', 'readback', 'completed')},
                            'tencent': {('video', 'locate', 'completed'), ('video', 'select', 'completed'),
                                        ('upload', 'transfer', 'completed'), ('upload', 'processing', 'completed'),
                                        ('content', 'description', 'completed'), ('content', 'readback', 'completed'),
                                        ('content', 'title', 'completed')},
                        }[platform]
                        self.assertTrue(expected_operations <= observed_operations,
                                        (platform, expected_operations - observed_operations))
                        self.assertEqual(await helpers.video_submissions(page), 1)
                        save.assert_awaited_once()
                        self.assertTrue(all(row['platform'] == platform for row in rows))
                    finally:
                        await context.close()
        helpers.run_scenario(scenario)

    def test_bilibili_real_branch_with_actual_cover_dialog(self):
        from tests.test_bili_controls import BiliControlTests
        import draft_execution
        helpers = BiliControlTests()

        async def scenario():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, channel='chromium')
                helpers.browser = browser
                helpers.setUp()
                try:
                    context, page, external = await helpers.fixture(tasks=0)
                    stream = io.StringIO()
                    save = AsyncMock()
                    payload = {'video': str(helpers.video), 'title': '明确标题',
                               'description': '完整文案', 'tags': ['标签一', '标签二'],
                               'cover': str(helpers.cover)}
                    try:
                        with configured('bilibili'), redirect_stdout(stream):
                            draft_progress.configure('bilibili')
                            with patch.object(draft_execution, 'save_draft', save):
                                await draft_execution.bilibili_draft(payload, 'unused.json', page=page)
                        rows = events(stream.getvalue())
                        self.assertEqual([row['stage'] for row in rows if row['state'] == 'completed'],
                                         ['video', 'upload', 'content', 'cover'])
                        operations = {(row.get('stage'), row.get('operation'), row.get('operation_state'))
                                      for row in rows if row.get('operation')}
                        self.assertTrue({('video', 'locate', 'completed'), ('video', 'select', 'completed'),
                                         ('upload', 'transfer', 'completed'), ('content', 'title', 'completed'),
                                         ('content', 'description', 'completed'), ('content', 'topics', 'completed'),
                                         ('content', 'readback', 'completed'), ('cover', 'editor', 'completed'),
                                         ('cover', 'landscape', 'completed'), ('cover', 'confirm', 'completed'),
                                         ('cover', 'readback', 'completed')} <= operations)
                        counts = await helpers.counters(page)
                        self.assertEqual(counts['video'], 1)
                        self.assertEqual(counts['cover'], 1)
                        self.assertEqual(counts['complete'], 1)
                        self.assertEqual(counts['tag'], 2)
                        self.assertFalse(external)
                        save.assert_awaited_once()
                    finally:
                        await context.close()
                finally:
                    helpers.tearDown()
                    await helpers.browser.close()
        asyncio.run(scenario())
