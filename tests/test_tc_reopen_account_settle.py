"""Normal browser reload/auth bootstrap, never old-auth inheritance or API replay."""
import asyncio
import copy
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from uploader.draft_session import DraftInterrupted
from uploader.tc_reopen import _processing_account_verdict, wait_tc_processing_list

OWNER={'verified':True,'platform':'tencent','uid':'fixture-owner','identity':'fixture-identity'}
PENDING={'verified':False,'platform':'tencent','status':'needs_reauthentication',
    'reason':'创作者身份尚未确认','account_conflict':False}


def fixture(status=2):
    candidate={'id':'fixture-saved','owner':OWNER['uid']}
    row={**candidate,'tc_handle_status':status,'tc_normal_read':'get_draft_list','request_method':'POST',
        'request_at':time.time(),'observed_at':time.time()}
    session=SimpleNamespace(platform='tencent',closed=False,issues=[],account=copy.deepcopy(OWNER),
        reopen_at=time.time()-.1,reads=[row],drain=AsyncMock(),tc_processing_read_timeout=.3,
        tc_processing_reload_interval=.05,tc_account_reload_timeout=.13)
    page=SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/list',_sau_draft_evidence=session,
        main_frame=SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/list'),reload=AsyncMock())
    session.page=page;session.account_reader=AsyncMock(return_value=copy.deepcopy(OWNER))
    edit=SimpleNamespace(dispose=AsyncMock())
    return page,session,candidate,edit


class ProcessingAccountGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_inspect_session_bootstrap_is_pending_without_success(self):
        p,s,c,e=fixture()
        for case in ('pending','wrong-owner','logout','login','risk','conflict','unknown','wrong-platform','false-known-owner'):
            with self.subTest(case=case):
                raw=copy.deepcopy(PENDING)
                if case=='wrong-owner':raw={**OWNER,'uid':'foreign-owner'}
                if case=='logout':raw={'verified':False,'platform':'tencent','status':'needs_reauthentication','diagnostic':'owner_logged_out'}
                if case=='login':raw['diagnostic']='visible_login_marker'
                if case=='risk':s.account_reader=AsyncMock(side_effect=DraftInterrupted('private-security-content','risk_paused'))
                else:s.account_reader=AsyncMock(return_value=raw)
                if case=='conflict':raw['account_conflict']=True
                if case=='unknown':raw.pop('reason')
                if case=='wrong-platform':raw['platform']='douyin'
                if case=='false-known-owner':raw.update(uid=OWNER['uid'],identity=OWNER['identity'])
                result,_=await _processing_account_verdict(s,OWNER)
                self.assertEqual(result,'pending' if case=='pending' else 'changed' if case in ('wrong-owner','conflict','wrong-platform') else 'rejected')

    async def test_verified_same_owner_on_list_does_not_require_editor_file_controls(self):
        p,s,c,e=fixture();s.account_reader.return_value={**OWNER,'editor_ready':False,'checked_at':time.time(),'source':'website-owner-response'}
        self.assertEqual(await _processing_account_verdict(s,OWNER),('same','account_verified_same'))
        result=await wait_tc_processing_list(p,c,s,{},e)
        self.assertIs(result,e);p.reload.assert_not_awaited();e.dispose.assert_not_awaited()

    async def test_pending_at_initial_list_waits_for_fresh_same_account_before_ready_row(self):
        p,s,c,e=fixture();s.account_reader=AsyncMock(side_effect=[copy.deepcopy(PENDING),copy.deepcopy(OWNER),copy.deepcopy(OWNER)])
        fresh=SimpleNamespace(dispose=AsyncMock())
        lookup=AsyncMock(return_value=('unique',fresh))
        with patch('uploader.tc_reopen._lookup',lookup):
            result=await wait_tc_processing_list(p,c,s,{},e)
        self.assertIs(result,fresh);e.dispose.assert_awaited_once();p.reload.assert_not_awaited()
        self.assertEqual(s.account_reader.await_count,3)
        self.assertEqual(s.account,OWNER)

    async def test_explicit_rejection_changed_owner_issues_scope_or_never_settled_cannot_advance(self):
        for case in ('logout','login','risk','changed','issues','scope','bound-account','after-drain-issues','after-lookup-changed','pending-timeout'):
            with self.subTest(case=case):
                p,s,c,e=fixture();fresh=SimpleNamespace(dispose=AsyncMock())
                lookup=AsyncMock(return_value=('unique',fresh))
                if case=='logout':s.account_reader.return_value={'verified':False,'platform':'tencent','diagnostic':'owner_logged_out'}
                if case=='login':s.account_reader.return_value={**PENDING,'diagnostic':'visible_login_marker'}
                if case=='risk':s.account_reader.side_effect=DraftInterrupted('private-account-risk','risk_paused')
                if case=='changed':s.account_reader.return_value={**OWNER,'uid':'foreign'}
                if case=='issues':s.issues.append('fixed-error')
                if case=='scope':p._sau_draft_evidence=object()
                if case=='bound-account':
                    async def change(*_):s.account['uid']='foreign';return copy.deepcopy(OWNER)
                    s.account_reader.side_effect=change
                if case=='after-drain-issues':
                    async def issue():s.issues.append('fixed-error')
                    s.drain.side_effect=issue
                if case=='after-lookup-changed':
                    s.account_reader.side_effect=[copy.deepcopy(PENDING),copy.deepcopy(OWNER),{**OWNER,'uid':'foreign'}]
                if case=='pending-timeout':s.account_reader.return_value=copy.deepcopy(PENDING)
                out=io.StringIO()
                with redirect_stdout(out),patch('uploader.tc_reopen._lookup',lookup):
                    self.assertIsNone(await wait_tc_processing_list(p,c,s,{},e))
                self.assertTrue(out.getvalue());self.assertNotIn('foreign',out.getvalue());self.assertNotIn('private-account-risk',out.getvalue())
                e.dispose.assert_awaited_once();p.reload.assert_not_awaited()
                if case=='after-lookup-changed':fresh.dispose.assert_awaited_once()
                else:lookup.assert_not_awaited()
                if case=='pending-timeout':self.assertIn('account_initialization_deadline_expired',out.getvalue())
                if case=='bound-account':self.assertIn('bound_account_changed',out.getvalue())
                if case=='risk':self.assertIn('account_risk_rejected',out.getvalue())

    async def test_reload_does_not_reuse_auth_and_processing_one_never_advances(self):
        p,s,c,e=fixture(status=1)
        lookup=AsyncMock(side_effect=lambda page,mode,*args:('none',None) if mode=='fields' else ('unique',SimpleNamespace(dispose=AsyncMock())))
        async def reload(**kwargs):
            s.account_reader.return_value={**OWNER,'uid':'foreign'}
            s.reads.append({**s.reads[0],'tc_handle_status':2,'request_at':time.time(),'observed_at':time.time()})
        p.reload.side_effect=reload
        with patch('uploader.tc_reopen._lookup',lookup):self.assertIsNone(await wait_tc_processing_list(p,c,s,{},e))
        p.reload.assert_awaited_once();e.dispose.assert_awaited_once()
        p,s,c,e=fixture(status=1)
        with patch('uploader.tc_reopen._lookup',lookup):self.assertIsNone(await wait_tc_processing_list(p,c,s,{},e))
        self.assertGreaterEqual(p.reload.await_count,1)


class NormalReloadBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_domcontentloaded_reload_waits_for_new_owner_mount_even_if_server_row_is_ready(self):
        playwright=await async_playwright().start()
        browser=await playwright.chromium.launch(headless=True,channel='chrome')
        context=await browser.new_context()
        html='<div id="row">saved row</div><script>window.ownerReady=false;setTimeout(()=>{ownerReady=true},200)</script>'
        await context.route('**/*',lambda r:r.fulfill(content_type='text/html',body=html))
        page=await context.new_page()
        try:
            await page.goto('https://channels.weixin.qq.com/platform/post/list');await page.wait_for_function('ownerReady===true')
            _,session,candidate,_=fixture(status=1)
            session.page=page;session.tc_processing_read_timeout=2;session.tc_account_reload_timeout=1
            session.tc_processing_reload_interval=.05;page._sau_draft_evidence=session
            async def reader():return copy.deepcopy(OWNER if await page.evaluate('ownerReady',isolated_context=False) else PENDING)
            session.account_reader=reader
            normal_reload=page.reload;reloaded=[];row_checked_pending=[]
            async def reload(**kwargs):
                await normal_reload(**kwargs);reloaded.append(time.monotonic())
                session.reads.append({**session.reads[0],'tc_handle_status':2,'request_at':time.time(),'observed_at':time.time()})
            async def lookup(page,mode,*args):
                if mode=='fields':return 'none',None
                row_checked_pending.append(not await page.evaluate('ownerReady',isolated_context=False))
                return 'unique',await page.locator('#row').element_handle()
            initial=await page.locator('#row').element_handle()
            with patch.object(page,'reload',reload),patch('uploader.tc_reopen._lookup',lookup):
                current=await wait_tc_processing_list(page,candidate,session,{},initial)
            self.assertIsNotNone(current);self.assertEqual(len(reloaded),1)
            self.assertGreaterEqual(time.monotonic()-reloaded[0],.15)
            self.assertFalse(any(row_checked_pending));self.assertEqual(session.account,OWNER)
            await current.dispose()
        finally:
            await context.close();await browser.close();await playwright.stop()
