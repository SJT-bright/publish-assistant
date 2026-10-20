"""A successful normal response can precede the Vue draft-row mount."""
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


class RowMountGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_waits_for_exact_row_and_rechecks_current_account(self):
        page, session, candidate, initial = fixture()
        session.tc_processing_read_timeout = 1
        session.tc_row_mount_timeout = .5
        current = SimpleNamespace(dispose=AsyncMock())
        lookup = AsyncMock(side_effect=[('none', None), ('unique', current)])
        with patch('uploader.tc_reopen._lookup', lookup):
            result = await wait_tc_processing_list(page, candidate, session, {}, None)
        self.assertIs(result, current)
        self.assertGreaterEqual(session.account_reader.await_count, 3)
        page.reload.assert_not_awaited()
        current.dispose.assert_not_awaited()

    async def test_missing_row_times_out_and_invalid_or_ambiguous_never_retry(self):
        for verdict in ('none', 'invalid', 'ambiguous'):
            with self.subTest(verdict=verdict):
                page, session, candidate, _ = fixture()
                session.tc_processing_read_timeout = 1
                session.tc_row_mount_timeout = .13
                lookup = AsyncMock(return_value=(verdict, None))
                output = io.StringIO()
                with patch('uploader.tc_reopen._lookup', lookup), redirect_stdout(output):
                    self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
                page.reload.assert_not_awaited()
                if verdict == 'none':
                    self.assertGreater(lookup.await_count, 1)
                    self.assertIn('successful_row_mount_deadline_expired', output.getvalue())
                else:
                    self.assertEqual(lookup.await_count, 1)
                    self.assertIn('successful_row_control_unavailable', output.getvalue())

    async def test_account_session_or_issues_change_while_mount_pending_cannot_advance(self):
        for case in ('account', 'binding', 'closed', 'issues'):
            with self.subTest(case=case):
                page, session, candidate, _ = fixture()
                session.tc_processing_read_timeout = 1
                session.tc_row_mount_timeout = .5
                async def pending(*args):
                    if case == 'account':
                        session.account_reader.return_value = {**OWNER, 'uid': 'different-owner'}
                    if case == 'binding':
                        page._sau_draft_evidence = object()
                    if case == 'closed':
                        session.closed = True
                    if case == 'issues':
                        session.issues.append('fixed-invalid-scope')
                    return 'none', None
                lookup = AsyncMock(side_effect=pending)
                with patch('uploader.tc_reopen._lookup', lookup):
                    self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
                self.assertEqual(lookup.await_count, 1)
                page.reload.assert_not_awaited()

    async def test_account_change_after_control_lookup_disposes_control(self):
        page, session, candidate, _ = fixture()
        current = SimpleNamespace(dispose=AsyncMock())
        async def lookup(*args):
            session.account_reader.return_value = {**OWNER, 'uid': 'different-owner'}
            return 'unique', current
        with patch('uploader.tc_reopen._lookup', lookup):
            self.assertIsNone(await wait_tc_processing_list(page, candidate, session, {}, None))
        current.dispose.assert_awaited_once()


class NormalResponseRowBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_post_response_ready_before_real_exact_vue_row_is_mounted(self):
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True, channel='chrome')
        context = await browser.new_context()
        html = r'''<!doctype html><div id="root"></div><script>
        fetch('/normal-draft-list', {method:'POST', body:'{}'}).then(r=>r.json()).then(data=>{
          setTimeout(()=>{
            const row=document.createElement('div');row.className='post-draft-item';
            row.style.cssText='display:block;width:400px;height:100px';
            row.innerHTML='<div class="opr"><div class="opr-item" style="width:30px;height:30px">'+
              '<svg class="icon-edit" width="20" height="20"><use href="#icon-edit"></use></svg></div></div>';
            row.__vue__={$el:row,post:data};document.querySelector('#root').append(row);
          },250);
        });</script>'''
        page = await context.new_page()
        response_ready = asyncio.get_running_loop().create_future()
        callbacks = set()
        normal_posts = []
        async def serve(route):
            if route.request.url.endswith('/normal-draft-list'):
                normal_posts.append(route.request.method)
                await route.fulfill(content_type='application/json', body=json.dumps({
                    'objectId':'fixture-saved', 'handleStatus':2}))
            else:
                await route.fulfill(content_type='text/html', body=html)
        await context.route('**/*', serve)
        async def observed(response):
            if response.url.endswith('/normal-draft-list'):
                data = await response.json()
                if not response_ready.done():
                    response_ready.set_result((data, response.request.timing['startTime']/1000, time.time()))
        def on_response(response):
            task = asyncio.create_task(observed(response))
            callbacks.add(task)
            task.add_done_callback(callbacks.discard)
        page.on('response', on_response)
        try:
            start = time.time()
            await page.goto('https://channels.weixin.qq.com/platform/post/list', wait_until='domcontentloaded')
            data, request_at, observed_at = await asyncio.wait_for(response_ready, 2)
            _, session, candidate, _ = fixture()
            session.page = page
            session.reopen_at = start
            session.tc_processing_read_timeout = 2
            session.tc_row_mount_timeout = 1
            session.reads = [{**candidate, 'tc_normal_read':'get_draft_list', 'request_method':'POST',
                'tc_handle_status':data['handleStatus'], 'request_at':request_at, 'observed_at':observed_at}]
            page._sau_draft_evidence = session
            reload = AsyncMock(side_effect=AssertionError('DOM mounting does not reload the page'))
            before = time.monotonic()
            with patch.object(page, 'reload', reload):
                control = await wait_tc_processing_list(page, candidate, session, {}, None)
            self.assertIsNotNone(control)
            self.assertGreaterEqual(time.monotonic()-before, .15)
            self.assertTrue(await control.evaluate("el=>el.closest('.post-draft-item').__vue__.post.objectId==='fixture-saved'", isolated_context=False))
            self.assertEqual(normal_posts, ['POST'])
            reload.assert_not_awaited()
            await control.dispose()
        finally:
            page.remove_listener('response', on_response)
            if callbacks:
                await asyncio.gather(*callbacks, return_exceptions=True)
            await context.close()
            await browser.close()
            await p.stop()
