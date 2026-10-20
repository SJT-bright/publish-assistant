"""Local byte-response/crop/task contracts; no account or CDN requests."""
import asyncio
import copy
import hashlib
import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from tests import test_tc_reopen_mapping as mapping
from uploader.draft_evidence import DraftEvidenceSession, parse_record, verify_evidence
from uploader.tc_cover_evidence import TCCoverObserver
from uploader.tc_preview_evidence import TCPreviewObserver, browser_response_times
from uploader.tc_reopen import wait_tc_processing_list
from uploader.tc_reopen_mapping import list_diagnostic, verify_processing_reopen
from uploader.tc_resource import tc_resource_identity


def image_resource(label, *, uploaded=False):
    fields={'encfilekey':'synthetic-'+label,'uzid':'synthetic-context','m':'','hy':'SH','idx':'1',
        'scene':'1','token':'synthetic-token'}
    fields.update({'bizid':'1023','dotrans':'0'} if uploaded else {'picformat':'200'})
    return 'https://finder.video.qq.com/251/20304/stodownload?' + urlencode(fields)


def image_response(page, url, body, when=None, **changes):
    when=time.time() if when is None else when
    frame=page.frames[0]
    request=SimpleNamespace(method='GET',resource_type='image',frame=frame,timing={'startTime':when*1000})
    response=SimpleNamespace(request=request,url=url,status=200,
        headers={'content-type':'image/jpeg','content-length':str(len(body))},body=AsyncMock(return_value=body))
    for key,value in changes.items():setattr(response,key,value)
    return response


class PreviewByteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.frame=SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/create')
        self.page=SimpleNamespace(frames=[self.frame])
        self.session=SimpleNamespace(page=self.page,platform='tencent',closed=False,
            expected={'job_id':'fixture-job','receipt_nonce':'fixture-nonce'},account={'verified':True,'uid':'fixture-owner'},
            reopen_at=time.time()-.01)
        self.page._sau_draft_evidence=self.session
        self.observer=TCPreviewObserver(self.session)
        self.body=b'\xff\xd8synthetic-jpeg-crop\xff\xd9';self.url=image_resource('new')

    async def test_passive_normal_image_body_keeps_exact_url_sha_and_no_new_request(self):
        response=image_response(self.page,self.url,self.body)
        self.assertTrue(await self.observer.observe(response,time.time()))
        response.body.assert_awaited_once_with()
        record=self.observer.records[0]
        self.assertEqual(record['url'],self.url)
        self.assertEqual(record['resource'],tc_resource_identity(self.url))
        self.assertEqual((record['sha256'],record['size']),(hashlib.sha256(self.body).hexdigest(),len(self.body)))
        self.assertNotIn('https://',json.dumps(self.observer.diagnostic()))
        await self.observer.close();self.assertEqual(self.observer.records,[])
        self.assertFalse(self.observer.current())

    async def test_old_scope_frame_non_image_unknown_schema_body_failure_or_limits_reject(self):
        for case in ('old','scope','account','frame','unknown','status','html','length','truncated','body-error','limit','record-limit'):
            with self.subTest(case=case):
                await self.asyncSetUp();response=image_response(self.page,self.url,self.body)
                if case=='old':response.request.timing['startTime']=(self.session.reopen_at-1)*1000
                if case=='scope':self.session.expected['receipt_nonce']='other'
                if case=='account':self.session.account['uid']='other'
                if case=='frame':response.request.frame=SimpleNamespace(url='https://foreign.invalid/platform/post/create')
                if case=='unknown':response.url='https://finder.video.qq.com/251/20304/stodownload?encfilekey=unknown'
                if case=='status':response.status=500
                if case=='html':response.headers['content-type']='text/html'
                if case=='length':response.headers['content-length']='33554433'
                if case=='truncated':response.headers['content-length']='1'
                if case=='body-error':response.body.side_effect=RuntimeError('private response contents')
                if case=='limit':response.body.return_value=b''
                if case=='record-limit':self.observer.records=[{}]*256
                self.assertTrue(await self.observer.observe(response,time.time()))
                self.assertEqual(len(self.observer.records),256 if case=='record-limit' else 0)
                self.assertTrue(self.observer.rejections)
                self.assertNotIn('private response contents',json.dumps(self.observer.diagnostic()))
        await self.asyncSetUp();response=image_response(self.page,self.url,self.body);response.request.resource_type='fetch'
        self.assertFalse(await self.observer.observe(response,time.time()));response.body.assert_not_awaited()

    def test_browser_start_time_is_relative_missing_old_or_nonfinite_timing_rejects(self):
        at=time.time()-.03
        response=SimpleNamespace(request=SimpleNamespace(timing={'startTime':at*1000,'responseStart':5,'responseEnd':10}))
        value=browser_response_times(response,at-.01)
        self.assertAlmostEqual(value['response_start'],at+.005,places=5)
        self.assertAlmostEqual(value['response_end'],at+.01,places=5)
        response.request.timing['responseEnd']=-1
        self.assertIsNone(browser_response_times(response,at-.01)['response_end'])
        for timing in ({'startTime':at*1000,'responseStart':-1},{'startTime':at*1000,'responseStart':None},
                {'startTime':at*1000,'responseStart':float('nan')},{'startTime':(at-2)*1000,'responseStart':5}):
            response.request.timing=timing;self.assertIsNone(browser_response_times(response,at-.01)['response_start'])

    async def test_encoded_length_uses_decoded_bytes_and_still_retains_actual_hash(self):
        response=image_response(self.page,self.url,self.body)
        response.headers.update({'content-encoding':'gzip','content-length':'5'})
        self.assertTrue(await self.observer.observe(response,time.time()))
        self.assertEqual(self.observer.records[0]['sha256'],hashlib.sha256(self.body).hexdigest())

    async def test_session_replacement_during_body_read_cannot_accept_late_bytes(self):
        response=image_response(self.page,self.url,self.body)
        async def body():self.page._sau_draft_evidence=object();return self.body
        response.body.side_effect=body
        self.assertTrue(await self.observer.observe(response,time.time()))
        self.assertEqual(self.observer.records,[])


class NormalReadTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_response_parsing_captures_browser_bound_and_rechecks_scope_after_body(self):
        for case in ('valid','nonce-change','page-change','account-change'):
            with self.subTest(case=case):
                page=SimpleNamespace(frames=[])
                session=DraftEvidenceSession(page,'tencent',{'job_id':'fixture-job','receipt_nonce':'fixture-nonce',
                    'platform_fields':{'caption':'fixture caption'}})
                session.account={'verified':True,'uid':'owner','identity':'account','platform':'tencent'}
                page._sau_draft_evidence=session
                session.tc_preview_observer=TCPreviewObserver(session)
                raw={'objectId':'saved','handleStatus':1,'desc':{'description':'fixture caption','media':[
                    {'url':mapping.fixture.SOURCE,'md5sum':'a'*32,'urlCdnTaskId':'fixture-task',
                     'width':320,'height':240,'videoPlayLen':6,'fileSize':'23','thumbUrl':image_resource('thumb')}]}}
                saved=parse_record('tencent',raw,owner='owner');saved['fresh']=True
                session.saves=[saved];session.reopen_at=time.time()-.1
                at=time.time()-.03
                response=SimpleNamespace(url='https://channels.weixin.qq.com/micro/content/cgi-bin/mmfinderassistant-bin/post/get_draft_list',
                    status=200,request=SimpleNamespace(method='POST',post_data_json={},
                        timing={'startTime':at*1000,'responseStart':5,'responseEnd':10}))
                async def body():
                    if case=='nonce-change':session.expected['receipt_nonce']='other'
                    if case=='page-change':page._sau_draft_evidence=object()
                    if case=='account-change':session.account['uid']='other'
                    return {'errCode':0,'data':{'list':[raw],'totalCount':1}}
                response.json=body
                from contextlib import redirect_stdout
                output=io.StringIO()
                with redirect_stdout(output):await session._observe(response)
                if case=='valid':
                    self.assertEqual(len(session.reads),1)
                    self.assertAlmostEqual(session.reads[0]['tc_response_start_at'],at+.005,places=5)
                    self.assertEqual(session.tc_first_target_gate,{'target_handle_status':1,'target_media_schema_valid':True})
                    self.assertNotIn(mapping.fixture.SOURCE,output.getvalue())
                    self.assertNotIn('fixture caption',output.getvalue())
                else:self.assertEqual(session.reads,[])


class CustomCoverMappingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=mapping.MappingProtocolTests.asyncSetUp
    make_session=mapping.MappingProtocolTests.make_session
    allocate=mapping.MappingProtocolTests.allocate
    finish=mapping.MappingProtocolTests.finish
    attest_normal_reopen=mapping.MappingProtocolTests.attest_normal_reopen
    pending_snapshot=mapping.MappingProtocolTests.pending_snapshot
    pending=mapping.MappingProtocolTests.pending
    prepared_saved=mapping.MappingProtocolTests.prepared_saved

    def record(self, url, size, prefix):
        return parse_record('tencent', {'objectId':'fixture-saved-record','handleStatus':2,'desc':{
            'description':'fixture caption','media':[{'url':url,'md5sum':getattr(self,'asset_id','a'*32),'urlCdnTaskId':self.ticket['draftId'],
                'width':320,'height':240,'videoPlayLen':6,'fileSize':str(size),
                'thumbUrl':image_resource(prefix+'-thumb',uploaded=prefix=='original'),'coverUrl':image_resource(prefix+'-portrait',uploaded=prefix=='original'),
                'shareCoverUrl':image_resource(prefix+'-landscape',uploaded=prefix=='original')}]}},owner=self.session.account['uid'])

    async def ready(self, uuid='a'*32):
        self.asset_id=uuid;self.state['uuid']=uuid;await self.pending()
        self.candidate=self.record(mapping.fixture.SOURCE,self.size,'original');self.candidate['fresh']=True
        await self.prepared_saved(self.candidate)
        self.bodies={slot:b'\xff\xd8synthetic-'+slot.encode()+b'\xff\xd9' for slot in ('cover_portrait','cover_landscape')}
        cover=TCCoverObserver(self.session);self.session.tc_cover_observer=cover
        selected=time.time();self.session.expected['cover_sha256']={}
        for index,(slot,body) in enumerate(self.bodies.items()):
            sha=hashlib.sha256(('original-'+slot).encode()).hexdigest()
            self.session.expected['cover_sha256'][slot]=sha
            asset={'kind':'cover','name':slot+'.png','size':123+index,'sha256':sha}
            self.session.assets[slot]=asset;self.session.files.append({**asset,'selected_at':selected})
            applied=self.candidate['tc_cover_media'][slot]['url']
            record={'slot':slot,'invocation_id':slot+'-invocation','origin_sha256':sha,
                'origin_name':asset['name'],'origin_size':asset['size'],'selected_at':selected,
                'derived_name':'finder_video_img.jpeg','derived_size':len(body),'derived_sha256':hashlib.sha256(body).hexdigest(),
                'encoded_at':selected+.001,'applied_at':selected+.003,'applied_id':applied}
            cover.records[record['invocation_id']]=record
            self.session.exchanges.append({'tc_protocol':'sns','task_id':slot+'-upload',
                'metadata':(record['derived_name'],len(body)),'observed_at':selected+.002,'range':(0,len(body)),
                'stage':'complete','ids':[tc_resource_identity(applied)],'whole_body':True,'body_sha256':record['derived_sha256']})
        self.session.upload_links=lambda:self.observer.augment_links(self.links+cover.upload_links())
        self.assertTrue(await self.observer.freeze_for_reopen(self.candidate))
        self.attest_normal_reopen(self.candidate);self.state=None;await self.observer.drain()
        self.reopened=self.record(mapping.scene_resource(),23,'normal-read')
        self.reopened.update(tc_normal_read='get_draft_list',request_method='POST',request_at=time.time(),observed_at=time.time(),tc_response_start_at=time.time(),tc_response_end_at=time.time())
        self.session.reads=[self.reopened]
        self.page.frames=[SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/create')]
        self.preview=TCPreviewObserver(self.session);self.session.tc_preview_observer=self.preview
        for slot,body in self.bodies.items():
            response=image_response(self.page,self.reopened['tc_cover_media'][slot]['url'],body)
            self.assertTrue(await self.preview.observe(response,time.time()))
        self.old_links=copy.deepcopy(self.session.upload_links())

    async def proof(self, native=True):
        with patch('uploader.tc_assets.tc_reopened_assets_match',new=AsyncMock(return_value=native)) as consumer:
            result=await verify_processing_reopen(self.session,self.candidate,self.reopened,self.session.account)
        return result,consumer

    async def test_changed_opaque_cover_resources_need_both_crop_bytes_roles_and_native_consumers(self):
        await self.ready();proof,consumer=await self.proof();self.assertIsNotNone(proof)
        self.assertNotEqual(self.reopened['cover_ids'],self.candidate['cover_ids'])
        self.assertEqual(self.session.upload_links(),self.old_links)
        self.assertFalse(any(set(link['ids']) & set(self.reopened['cover_ids']) for link in self.old_links if link['kind']=='cover'))
        consumer.assert_awaited_once_with(self.page,self.reopened,restored_from=self.candidate,strict_cover_media=True,
            cover_roles={slot:value['url'] for slot,value in self.reopened['tc_cover_media'].items()})
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,self.old_links,self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account,tc_processing_mapping=proof)
        self.assertTrue(all(checks.values()))
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,self.old_links,self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account)
        self.assertFalse(checks['reopened'])
        self.preview.records[0]['sha256']='0'*64
        self.assertFalse(proof.matches(self.session.expected,self.candidate,self.reopened,self.old_links,self.session.account,self.session.account))

    async def test_native_uploaded_covers_and_guid_reopen_as_distinct_processed_picformat_resources(self):
        guid='12345678-abcd-1234-abcd-123456789abc'
        await self.ready(uuid=guid)
        self.assertEqual(self.candidate['tc_media'][0]['uuid'],guid)
        self.assertEqual(self.reopened['tc_media'][0]['uuid'],guid)
        self.assertEqual(self.observer._frozen_reopen['native']['uuid'],guid)
        proof,_=await self.proof();self.assertIsNotNone(proof)
        for slot in self.bodies:
            self.assertNotEqual(self.candidate['cover_slots'][slot],self.reopened['cover_slots'][slot])
            self.assertEqual(len(self.observer._frozen_reopen['covers'][slot]['ids']),1)
            self.assertEqual(self.observer._frozen_reopen['covers'][slot]['chain_ids'],self.candidate['cover_slots'][slot])
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,self.old_links,self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account,tc_processing_mapping=proof)
        self.assertTrue(all(checks.values()))

    async def test_missing_wrong_image_old_request_scope_slot_or_transport_never_maps(self):
        for case in ('missing','wrong-bytes','wrong-role','old-request','scope','replaced-slot','new-url','crop-mutated','transport-mutated','missing-thumb','missing-response-start'):
            with self.subTest(case=case):
                await self.asyncSetUp();await self.ready()
                if case=='missing':self.preview.records.pop()
                if case=='wrong-bytes':self.preview.records[0]['sha256']='0'*64
                if case=='wrong-role':self.preview.records[0]['sha256']=self.preview.records[1]['sha256']
                if case=='old-request':self.preview.records[0]['request_at']=self.reopened['tc_response_start_at']-1
                if case=='scope':self.session.expected['receipt_nonce']='other'
                if case=='replaced-slot':self.session.tc_cover_observer.invalidated['cover_portrait']=time.time()
                if case=='new-url':self.reopened['tc_cover_media']['cover_portrait']['url']=image_resource('other-row')
                if case=='crop-mutated':next(iter(self.session.tc_cover_observer.records.values()))['derived_sha256']='0'*64
                if case=='transport-mutated':self.session.exchanges[-1]['body_sha256']='0'*64
                if case=='missing-thumb':self.reopened['tc_thumb_media']=None
                if case=='missing-response-start':self.reopened['tc_response_start_at']=None
                proof,consumer=await self.proof();self.assertIsNone(proof);consumer.assert_not_awaited()

    async def test_browser_response_precedes_image_even_when_python_callback_is_late(self):
        await self.ready()
        self.reopened['observed_at']=time.time()
        self.assertLess(self.preview.records[0]['request_at'],self.reopened['observed_at'])
        self.assertGreaterEqual(self.preview.records[0]['request_at'],self.reopened['tc_response_start_at'])
        proof,_=await self.proof();self.assertIsNotNone(proof)
        self.preview.records[0]['request_at']=self.reopened['tc_response_start_at']-.001
        proof,consumer=await self.proof();self.assertIsNone(proof);consumer.assert_not_awaited()
        self.assertEqual(self.preview.last_rejection,'preview_request_precedes_read')

    async def test_failed_native_gate_or_byte_conflict_during_scan_cannot_create_proof(self):
        await self.ready();proof,_=await self.proof(False);self.assertIsNone(proof)
        async def mutate(*args,**kwargs):self.preview.records.append({**self.preview.records[0],'sha256':'0'*64});return True
        with patch('uploader.tc_assets.tc_reopened_assets_match',new=mutate):
            self.assertIsNone(await verify_processing_reopen(self.session,self.candidate,self.reopened,self.session.account))


class ProcessingListWaitTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, status=1):
        candidate={'id':'saved','owner':'owner'}
        row={**candidate,'tc_handle_status':status,'tc_normal_read':'get_draft_list','request_method':'POST',
            'request_at':time.time(),'observed_at':time.time()}
        session=SimpleNamespace(closed=False,issues=[],account={'uid':'owner'},reopen_at=time.time()-.1,
            reads=[row],read_account=AsyncMock(return_value={'uid':'owner'}),drain=AsyncMock(),
            tc_processing_read_timeout=.18,tc_processing_reload_interval=.05)
        page=SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/list',_sau_draft_evidence=session,reload=AsyncMock())
        control=SimpleNamespace(dispose=AsyncMock())
        return page,session,candidate,control

    async def test_pending_row_uses_normal_list_reload_until_success_then_returns_unique_edit(self):
        page,s,c,edit=self.fixture();next_edit=SimpleNamespace(dispose=AsyncMock())
        async def reload(**kwargs):s.reads.append({**s.reads[-1],'tc_handle_status':2,'request_at':time.time(),'observed_at':time.time()})
        page.reload.side_effect=reload
        async def lookup(page,mode,*args):return ('none',None) if mode=='fields' else ('unique',next_edit)
        with patch('uploader.tc_reopen._lookup',new=lookup):
            result=await wait_tc_processing_list(page,c,s,{},edit)
        self.assertIs(result,next_edit);edit.dispose.assert_awaited_once();page.reload.assert_awaited_once_with(wait_until='domcontentloaded',timeout=20000)

    async def test_failed_old_missing_wrong_owner_scope_or_editor_never_advances(self):
        for case in ('failed','old','missing','owner','scope','editor','method','none-time','timeout'):
            with self.subTest(case=case):
                page,s,c,edit=self.fixture(3 if case=='failed' else 1)
                if case=='old':s.reads[-1].update(tc_handle_status=2,request_at=s.reopen_at-1)
                if case=='missing':s.reads=[]
                if case=='owner':s.reads[-1].update(tc_handle_status=2,owner='foreign')
                if case=='scope':page._sau_draft_evidence=object()
                if case=='editor':page.url='https://channels.weixin.qq.com/platform/post/create'
                if case=='method':s.reads[-1].update(tc_handle_status=2,request_method='GET')
                if case=='none-time':s.reads[-1].update(tc_handle_status=2,request_at=None)
                async def lookup(page,mode,*args):return ('none',None) if mode=='fields' else ('unique',SimpleNamespace(dispose=AsyncMock()))
                with patch('uploader.tc_reopen._lookup',new=lookup):
                    self.assertIsNone(await wait_tc_processing_list(page,c,s,{},edit))
                edit.dispose.assert_awaited_once()
                if case in ('failed','scope','editor'):page.reload.assert_not_awaited()

    def test_target_specific_redacted_diagnostic_does_not_mix_other_media_failures(self):
        payload={'list':[{'objectId':'saved','handleStatus':1,'desc':{'media':[]}},
            {'objectId':'other','desc':{'media':[{'url':'private-malformed-url'}]}}]}
        value=list_diagnostic(payload,[],{'id':'saved'},None)
        self.assertEqual(value['target_handle_status'],1);self.assertFalse(value['target_media_schema_valid'])
        self.assertNotIn('private-malformed-url',json.dumps(value))


class PendingCoverBudgetTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, proved=True, wrong_ticket=False, failed=False, ready_after=150):
        from uploader.tencent_uploader.main import TencentVideo
        clock=SimpleNamespace(value=0)
        def monotonic():clock.value+=10;return clock.value
        entry=SimpleNamespace(is_visible=AsyncMock(return_value=True),is_enabled=AsyncMock(return_value=True),
            evaluate=AsyncMock(side_effect=lambda *args,**kw:clock.value>=ready_after))
        frame=SimpleNamespace(locator=lambda _:SimpleNamespace(all=AsyncMock(return_value=[entry])))
        observer=SimpleNamespace(drain=AsyncMock(),native={'mode':'async_clip','ticket':{'clipKey':'key','draftId':'task'}},
            records=[{'operation':'result','ticket':('other' if wrong_ticket else 'key','task'),'pending':not failed,'failed':failed}],
            _augment_with_native=lambda links,native:(links,proved))
        session=SimpleNamespace(tc_processing_observer=observer,upload_links=lambda:[{'kind':'video','complete':True,'sha256':'a'*64}])
        page=SimpleNamespace(frames=[frame],_sau_tc_native_import=object(),_sau_draft_evidence=session)
        uploader=TencentVideo.__new__(TencentVideo)
        return uploader,page,entry,clock,SimpleNamespace(monotonic=monotonic)

    async def wait(self,uploader,page,timer):
        with patch('uploader.tencent_uploader.main.time',timer), patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()), \
                patch('uploader.tc_pipeline.read_tc_upload_binding',new=AsyncMock(return_value='done')):
            return await uploader._wait_for_draft_cover_entry(page,['.horizontal-cover-wrap'])

    async def test_verified_pending_ticket_extends_wait_past_120_only_until_dom_ready(self):
        uploader,page,entry,clock,timer=self.fixture()
        frame,current=await self.wait(uploader,page,timer)
        self.assertIs(current,entry);self.assertGreater(clock.value,120);self.assertLess(clock.value,900)
        self.assertEqual(uploader._draft_cover_phase,'打开封面编辑器')
        self.assertFalse(hasattr(entry,'click'))

    async def test_no_sha_chain_foreign_ticket_explicit_budget_failed_or_bounded_pending_never_force_controls(self):
        for case in ('unproven','foreign','explicit','failed','bounded'):
            with self.subTest(case=case):
                u,p,entry,clock,timer=self.fixture(proved=case!='unproven',wrong_ticket=case=='foreign',failed=case=='failed',ready_after=10000)
                if case=='explicit':u.draft_cover_ready_timeout=120
                with self.assertRaises(RuntimeError):await self.wait(u,p,timer)
                self.assertLessEqual(clock.value,930 if case=='bounded' else 150)
                if case=='failed':entry.evaluate.assert_not_awaited()
                self.assertFalse(hasattr(entry,'click'))
