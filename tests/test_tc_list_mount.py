"""The ordinary POST can still be pending at the next reload cadence."""
import asyncio
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from tests.test_tc_reopen_account_settle import fixture, OWNER
from uploader.tc_reopen import wait_tc_processing_list


class PendingListMountTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_previous_generation_success_cannot_override_current_pending_read(self):
        page, session, candidate, _ = fixture(status=1)
        session.tc_processing_read_timeout = .4
        current = SimpleNamespace(dispose=AsyncMock())
        old = dict(session.reads[0])
        async def reload(**kwargs):
            session.reads.append({**old, 'tc_handle_status':1,
                'request_at':time.time(), 'observed_at':time.time()})
            # This old request completes last, after the current pending read.
            session.reads.append({**old, 'tc_handle_status':2, 'observed_at':time.time()})
        page.reload.side_effect = reload
        async def lookup(page, mode, *args):
            return ('none', None) if mode == 'fields' else ('unique', current)
        with patch('uploader.tc_reopen._lookup', new=lookup):
            self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
        self.assertGreaterEqual(page.reload.await_count, 2)

    async def test_request_order_takes_precedence_over_response_completion_order(self):
        page, session, candidate, _ = fixture(status=1)
        session.tc_processing_read_timeout = 1
        current = SimpleNamespace(dispose=AsyncMock())
        old = dict(session.reads[0])
        now = time.time()
        session.reads = [{**old, 'tc_handle_status':1, 'request_at':now-.01, 'observed_at':now},
            {**old, 'tc_handle_status':2, 'request_at':now-.02, 'observed_at':now}]
        async def reload(**kwargs):
            session.reads.append({**old, 'tc_handle_status':2,
                'request_at':time.time(), 'observed_at':time.time()})
        page.reload.side_effect = reload
        async def lookup(page, mode, *args):
            return ('none', None) if mode == 'fields' else ('unique', current)
        with patch('uploader.tc_reopen._lookup', new=lookup):
            self.assertIs(await wait_tc_processing_list(page, candidate, session, {}, None), current)
        page.reload.assert_awaited_once()

    async def test_reload_waits_for_its_own_read_even_when_dom_row_is_already_unique(self):
        page, session, candidate, _ = fixture(status=1)
        session.tc_processing_read_timeout = 1
        session.tc_list_response_timeout = .6
        current = SimpleNamespace(dispose=AsyncMock())
        pending = []
        async def finish_read():
            await asyncio.sleep(.25)
            session.reads.append({**session.reads[-1], 'tc_handle_status':2,
                'request_at':time.time(), 'observed_at':time.time()})
        async def reload(**kwargs):pending.append(asyncio.create_task(finish_read()))
        page.reload.side_effect = reload
        async def lookup(page, mode, *args):
            return ('none', None) if mode == 'fields' else ('unique', current)
        try:
            with patch('uploader.tc_reopen._lookup', new=lookup):
                self.assertIs(await wait_tc_processing_list(page, candidate, session, {}, None), current)
            page.reload.assert_awaited_once()
        finally:
            if pending:await asyncio.gather(*pending)

    async def test_late_old_request_get_wrong_owner_or_invalid_time_cannot_release_reload_barrier(self):
        for case in ('old-request', 'get', 'owner', 'future-time', 'no-response'):
            with self.subTest(case=case):
                page, session, candidate, _ = fixture(status=1)
                session.tc_processing_read_timeout = 1
                session.tc_list_response_timeout = .15
                current = SimpleNamespace(dispose=AsyncMock())
                async def reload(**kwargs):
                    old = session.reads[0]
                    row = {**old, 'tc_handle_status':2, 'request_at':time.time(), 'observed_at':time.time()}
                    if case == 'old-request':row['request_at'] = old['request_at']
                    if case == 'get':row['request_method'] = 'GET'
                    if case == 'owner':row['owner'] = 'foreign-owner'
                    if case == 'future-time':row['observed_at'] += 100
                    if case != 'no-response':session.reads.append(row)
                page.reload.side_effect = reload
                async def lookup(page, mode, *args):
                    return ('none', None) if mode == 'fields' else ('unique', current)
                output = io.StringIO()
                with patch('uploader.tc_reopen._lookup', new=lookup), redirect_stdout(output):
                    self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
                self.assertIn('normal_list_response_deadline_expired', output.getvalue())
                page.reload.assert_awaited_once()

    async def test_fresh_pending_read_restarts_interval_without_entering_editor(self):
        page, session, candidate, _ = fixture(status=1)
        session.tc_processing_read_timeout = 1
        reloads = []
        current = SimpleNamespace(dispose=AsyncMock())
        async def reload(**kwargs):
            reloads.append(time.monotonic())
            session.reads.append({**session.reads[-1], 'tc_handle_status':2 if len(reloads) == 2 else 1,
                'request_at':time.time(), 'observed_at':time.time()})
        page.reload.side_effect = reload
        async def lookup(page, mode, *args):
            return ('none', None) if mode == 'fields' else ('unique', current)
        with patch('uploader.tc_reopen._lookup', new=lookup):
            self.assertIs(await wait_tc_processing_list(page, candidate, session, {}, None), current)
        self.assertEqual(len(reloads), 2)
        self.assertGreaterEqual(reloads[1] - reloads[0], session.tc_processing_reload_interval)

    async def test_pending_normal_row_waits_without_another_reload_until_success(self):
        page, session, candidate, _ = fixture(status=1)
        session.tc_processing_read_timeout = 1
        session.tc_row_mount_timeout = .6
        current = SimpleNamespace(dispose=AsyncMock())
        attempts = 0
        async def lookup(page, mode, *args):
            nonlocal attempts
            if mode == 'fields':return 'none', None
            attempts += 1
            if attempts == 3:
                session.reads.append({**session.reads[-1], 'tc_handle_status':2,
                    'request_at':time.time(), 'observed_at':time.time()})
            return ('unique', current) if attempts > 3 else ('none', None)
        output = io.StringIO()
        with patch('uploader.tc_reopen._lookup', new=lookup), redirect_stdout(output):
            result = await wait_tc_processing_list(page, candidate, session, {}, None)
        self.assertIs(result, current)
        self.assertIn('normal_row_mount_pending', output.getvalue())
        page.reload.assert_not_awaited()
        current.dispose.assert_not_awaited()
        self.assertGreaterEqual(session.account_reader.await_count, 5)

    async def test_empty_list_expires_and_invalid_ambiguous_or_live_editor_reject_immediately(self):
        for case in ('empty', 'invalid', 'ambiguous', 'editor'):
            with self.subTest(case=case):
                page, session, candidate, _ = fixture(status=1)
                session.tc_processing_read_timeout = 1
                session.tc_row_mount_timeout = .13
                async def lookup(page, mode, *args):
                    if mode == 'fields':return ('unique' if case == 'editor' else 'none'), None
                    return (case if case in ('invalid', 'ambiguous') else 'none'), None
                output = io.StringIO()
                with patch('uploader.tc_reopen._lookup', new=lookup), redirect_stdout(output):
                    self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
                page.reload.assert_not_awaited()
                self.assertIn('normal_row_mount_deadline_expired' if case == 'empty' else
                    'normal_list_scope_unavailable', output.getvalue())

    async def test_scope_or_account_change_during_pending_list_stops(self):
        for case in ('owner', 'closed', 'issues', 'binding'):
            with self.subTest(case=case):
                page, session, candidate, _ = fixture(status=1)
                session.tc_processing_read_timeout = 1
                async def lookup(page, mode, *args):
                    if mode == 'row':
                        if case == 'owner':session.account_reader.return_value = {**OWNER, 'uid':'foreign'}
                        if case == 'closed':session.closed = True
                        if case == 'issues':session.issues.append('fixed-scope-error')
                        if case == 'binding':page._sau_draft_evidence = object()
                    return 'none', None
                with patch('uploader.tc_reopen._lookup', new=lookup):
                    self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
                page.reload.assert_not_awaited()


class DelayedNormalListBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_post_response_and_row_arrive_after_reload_cadence(self):
        await self.run_normal_list(False)

    async def test_own_reload_does_not_interrupt_delayed_post_or_row_mount(self):
        await self.run_normal_list(True)

    async def run_normal_list(self, reload_once):
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True, channel='chrome')
        context = await browser.new_context()
        page = await context.new_page()
        _, session, candidate, _ = fixture(status=1)
        session.page = page
        session.tc_processing_read_timeout = 3
        session.tc_row_mount_timeout = 2
        page._sau_draft_evidence = session
        html = '''<div id="root"></div><script>
        fetch('/normal-list', {method:'POST'}).then(r=>r.json()).then(post=>setTimeout(()=>{
          const row=document.createElement('div');row.className='post-draft-item';
          row.style.cssText='display:block;width:400px;height:100px';
          row.innerHTML='<div class="opr"><div class="opr-item" style="width:30px;height:30px">'+
            '<svg class="icon-edit" width="20" height="20"><use href="#icon-edit"></use></svg></div></div>';
          row.__vue__={$el:row,post};document.querySelector('#root').append(row);
        },150));</script>'''
        initial = '''<div class="post-draft-item" style="width:400px;height:100px"><div class="opr">
        <div class="opr-item" style="width:30px;height:30px"><svg class="icon-edit" width="20" height="20">
        <use href="#icon-edit"></use></svg></div></div></div><script>
        const row=document.querySelector('.post-draft-item');row.__vue__={$el:row,post:{objectId:'fixture-saved'}};
        </script>'''
        requests, callbacks, documents = [], set(), []
        async def serve(route):
            if route.request.url.endswith('/normal-list'):
                requests.append(route.request.method)
                await asyncio.sleep(.4)
                await route.fulfill(content_type='application/json', body=json.dumps({
                    'objectId':candidate['id'], 'handleStatus':2}))
            else:
                documents.append(route.request.url)
                await route.fulfill(content_type='text/html', body=initial if reload_once and len(documents) == 1 else html)
        async def observed(response):
            if response.url.endswith('/normal-list'):
                data = await response.json()
                session.reads.append({**candidate, 'tc_handle_status':data['handleStatus'],
                    'tc_normal_read':'get_draft_list', 'request_method':'POST',
                    'request_at':response.request.timing['startTime']/1000, 'observed_at':time.time()})
        def on_response(response):
            task = asyncio.create_task(observed(response));callbacks.add(task)
            task.add_done_callback(callbacks.discard)
        await context.route('**/*', serve)
        page.on('response', on_response)
        try:
            await page.goto('https://channels.weixin.qq.com/platform/post/list', wait_until='domcontentloaded')
            original_reload = page.reload
            reload = AsyncMock(side_effect=original_reload if reload_once else AssertionError('Pending normal response must not be interrupted'))
            output = io.StringIO()
            with patch.object(page, 'reload', reload), redirect_stdout(output):
                control = await wait_tc_processing_list(page, candidate, session, {}, None)
            self.assertIsNotNone(control)
            self.assertIn('normal_list_reload' if reload_once else 'normal_row_mount_pending', output.getvalue())
            self.assertTrue(await control.evaluate("el=>el.closest('.post-draft-item').__vue__.post.objectId==='fixture-saved'", isolated_context=False))
            self.assertEqual(requests, ['POST'])
            if reload_once:reload.assert_awaited_once()
            else:reload.assert_not_awaited()
            await control.dispose()
        finally:
            page.remove_listener('response', on_response)
            if callbacks:await asyncio.gather(*callbacks, return_exceptions=True)
            await context.close();await browser.close();await p.stop()
