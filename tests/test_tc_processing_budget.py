"""Fast clock-controlled processing budgets using the production frozen observer."""
import copy
import io
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests import test_tc_preview_evidence as cover_fixture
from uploader.tc_processing import TCProcessingObserver
from uploader.tc_reopen import (_processing_read_budget, _processing_frozen_proof,
    _reopen_time_budget, wait_tc_processing_list)


class ProcessingBudgetTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=cover_fixture.CustomCoverMappingTests.asyncSetUp
    make_session=cover_fixture.CustomCoverMappingTests.make_session
    allocate=cover_fixture.CustomCoverMappingTests.allocate
    finish=cover_fixture.CustomCoverMappingTests.finish
    attest_normal_reopen=cover_fixture.CustomCoverMappingTests.attest_normal_reopen
    pending_snapshot=cover_fixture.CustomCoverMappingTests.pending_snapshot
    pending=cover_fixture.CustomCoverMappingTests.pending
    prepared_saved=cover_fixture.CustomCoverMappingTests.prepared_saved
    record=cover_fixture.CustomCoverMappingTests.record
    ready=cover_fixture.CustomCoverMappingTests.ready

    async def setup_wait(self):
        await self.ready(uuid='12345678-abcd-1234-abcd-123456789abc')
        self.assertIs(type(self.observer),TCProcessingObserver)
        self.assertIsNotNone(self.observer._frozen_reopen)
        self.assertTrue(self.observer._frozen_events_current())
        self.session.normal_ui_binding=None  # ordinary list, before edit
        self.session.reads=[{**copy.deepcopy(self.candidate),'fresh':False,'tc_handle_status':1,
            'tc_normal_read':'get_draft_list','request_method':'POST','request_at':time.time(),'observed_at':time.time()}]
        self.page.url='https://channels.weixin.qq.com/platform/post/list'
        self.page.main_frame.url=self.page.url
        self.page.reload=AsyncMock()
        self.session.drain=self.observer.drain
        self.assertIsNotNone(_processing_frozen_proof(self.session,self.candidate))
        clock=SimpleNamespace(value=0)
        def monotonic():
            now=clock.value;clock.value+=1;return now
        timer=SimpleNamespace(monotonic=monotonic,time=time.time)
        return clock,timer

    async def wait(self,clock,timer,*,mutate=None):
        initial=SimpleNamespace(dispose=AsyncMock())
        current=SimpleNamespace(dispose=AsyncMock())
        async def reload(**kwargs):
            if mutate:mutate()
            # Each successful normal reload has its own observed POST read;
            # a pending response must release only that generation's barrier.
            self.session.reads.append({**self.session.reads[-1],
                'tc_handle_status':2 if clock.value>=150 and type(self.session.reads[-1].get('tc_handle_status')) is int and self.session.reads[-1]['tc_handle_status']==1 else self.session.reads[-1].get('tc_handle_status'),
                'request_at':time.time(),'observed_at':time.time()})
        self.page.reload.side_effect=reload
        async def lookup(page,mode,*args):
            return ('none',None) if mode=='fields' else ('unique',current)
        output=io.StringIO()
        with patch('uploader.tc_reopen.time',timer),patch('uploader.tc_reopen._lookup',new=lookup),redirect_stdout(output):
            result=await wait_tc_processing_list(self.page,self.candidate,self.session,{},initial)
        return result,output.getvalue(),current

    async def test_production_full_frozen_crop_upload_save_proof_extends_only_normal_pending_row(self):
        clock,timer=await self.setup_wait()
        frozen=self.observer._frozen_reopen
        self.assertEqual(set(frozen['covers']),{'cover_portrait','cover_landscape'})
        self.assertEqual(frozen['native']['sha256'],self.session.expected['video_sha256'])
        self.assertEqual(_reopen_time_budget(self.session,self.candidate),943)
        result,trace,current=await self.wait(clock,timer)
        self.assertIs(result,current);self.assertGreater(clock.value,90);self.assertLess(clock.value,900)
        self.assertIn('saved_platform_processing',trace)
        self.assertEqual(frozen,self.observer._frozen_reopen)
        self.assertIsNone(self.session.normal_ui_binding)

    async def test_fake_or_missing_observer_unknown_state_and_explicit_budget_never_expand(self):
        for case in ('fake','missing','unknown','boolean','explicit','explicit-long'):
            with self.subTest(case=case):
                await self.asyncSetUp();clock,timer=await self.setup_wait()
                if case=='fake':self.session.tc_processing_observer=SimpleNamespace(_frozen_reopen=self.observer._frozen_reopen,_frozen_events_current=lambda:True)
                if case=='missing':self.session.tc_processing_observer=None
                if case=='unknown':self.session.reads[-1]['tc_handle_status']=None
                if case=='boolean':self.session.reads[-1]['tc_handle_status']=True
                if case=='explicit':self.session.tc_processing_read_timeout=42.5
                if case=='explicit-long':
                    self.session.tc_processing_read_timeout=10000
                    self.assertEqual(_processing_read_budget(self.session),(900,True))
                    self.assertEqual(_reopen_time_budget(self.session,self.candidate),943)
                    continue
                self.assertEqual(_reopen_time_budget(self.session,self.candidate),85.5 if case=='explicit' else 133 if case in ('fake','missing') else 943)
                result,trace,_=await self.wait(clock,timer)
                self.assertIsNone(result);self.assertLessEqual(clock.value,120)
                self.assertNotIn('saved_platform_processing',trace)

    async def test_frozen_expected_events_or_observer_changes_abort_after_grant(self):
        for case in ('frozen','expected','events','observer'):
            with self.subTest(case=case):
                await self.asyncSetUp();clock,timer=await self.setup_wait()
                def mutate():
                    if case=='frozen':self.observer._frozen_reopen['native']['uuid']='changed-native-id'
                    if case=='expected':self.session.expected['receipt_nonce']='foreign'
                    if case=='events':self.session.exchanges.append({'changed-event':True})
                    if case=='observer':self.session.tc_processing_observer=SimpleNamespace()
                result,trace,_=await self.wait(clock,timer,mutate=mutate)
                self.assertIsNone(result);self.assertLess(clock.value,90)
                self.assertIn('processing_expected_changed' if case=='expected' else 'processing_frozen_proof_changed',trace)
                self.page.reload.assert_awaited_once()

    async def test_already_invalid_real_frozen_state_and_server_failure_stop_without_wait(self):
        clock,timer=await self.setup_wait()
        self.observer._frozen_reopen['files'].append({'changed':True})
        self.assertEqual(_reopen_time_budget(self.session,self.candidate),133)
        result,trace,_=await self.wait(clock,timer)
        self.assertIsNone(result);self.assertIn('processing_frozen_proof_changed',trace)
        self.page.reload.assert_not_awaited()
        await self.asyncSetUp();clock,timer=await self.setup_wait()
        self.session.reads[-1]['tc_handle_status']=3
        result,trace,_=await self.wait(clock,timer)
        self.assertIsNone(result);self.assertIn('server_processing_failed',trace)
        self.assertNotIn('saved_platform_processing',trace);self.page.reload.assert_not_awaited()
