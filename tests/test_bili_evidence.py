"""Bili evidence on isolated fixtures. Never operates a real creator account."""
import asyncio
import copy
import contextlib
import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from patchright.async_api import async_playwright
from uploader.bili_evidence import BiliEvidenceObserver, BILI_OBSERVER_SCRIPT, BILI_COVER_FORMAT_JS, bili_cover_identity

URL='https://member.bilibili.com/platform/upload/video/frame'
HTML=r'''<!doctype html><meta charset="utf-8"><style>.upload{width:200px;height:40px}.cover-editor{width:500px;height:400px}</style>
<div class="video-entrance"><div class="upload-wrp"><div class="upload bcc-upload"><div class="bcc-upload-wrapper"><input id="video" type="file" accept=".mp4"></div></div></div></div>
<div class="cover-module-main"></div><button id="finish">Complete SDK</button><button id="cover">Open cover</button>
<script>
window.config=CONFIG;window.counts={started:0,terminal:0,export:0,uploadImage:0,submit:0};
const m={tasks:[],currentTaskId:null,createTask(args){counts.started++;const file=args.videoTasks[0].file;
 const task={file,status:1,cid:77,filename:'upos-bili-native',uploader:null};
 const inner={file,completed:false};const sdk={file,cid:77,filename:'upos-bili-native',uploader:inner,uploaderType:'stdSlice',emit(event,value){
  window.emitReceiver=this===sdk;window.emitPayload=value;if(event==='uploadComplete'){task.status=3;task.cid=value.cid;task.filename=value.filename}return 'original-return';}};
 task.uploader=sdk;const parent={id:'fixture',videoTasks:[task]};this.tasks.push(parent);this.currentTaskId='fixture';
 const old=document.querySelector('.video-entrance');old.__vue__=null;old.className='video-publisher';
 window.pub={$el:old,id:parent.id,$taskConfig:m,$recordConfig:{data:[]},videoTasks:parent.videoTasks,checkSubTask(){},handleSubmitDraft(){}};old.__vue__=pub;
 window.task=task;window.sdk=sdk;return parent;}};window.manager=m;
const entrance=document.querySelector('.video-entrance');entrance.__vue__={$el:entrance,$taskConfig:m,$recordConfig:{data:[]},record:[],handleChange(){},initRecord(){},recovery(){}};
const coverParent=document.querySelector('.cover-module-main');window.coverVM={$el:coverParent,$taskConfig:m,coverList:[{main:{},extra:{}}],defaultCoverIndex:0,openCoverEditor(){},submitCoverEditor(){}};coverParent.__vue__=coverVM;
document.querySelector('#video').onchange=e=>{m.createTask({videoTasks:[{file:e.target.files[0]}]});if(config.duplicate)m.tasks.push({videoTasks:[]})};
document.querySelector('#finish').onclick=()=>{counts.terminal++;if(config.manual){task.status=3;return}if(config.swap)sdk.file=new File(['different'],'video.mp4',{type:'video/mp4'});
 window.emitReturned=sdk.emit(config.partial?'initUploaderSuccess':'uploadComplete',{cid:77,filename:'upos-bili-native'});sdk.uploader.completed=true;};
document.querySelector('#cover').onclick=()=>{const el=document.createElement('div');el.className='cover-editor';el.innerHTML='<div class="cover-upload bcc-upload"><div class="bcc-upload-wrapper"><input id="source" type="file" accept="image/png,image/jpeg"></div></div><button id="done">完成</button>';document.body.append(el);
 const tool={};Object.defineProperty(tool,'verifyAndLoadCoverFile',{get:()=>function(file){const blobUrl=URL.createObjectURL(file);return Promise.resolve({blobUrl,width:1200,height:900})}});
 const api={uploadImage(data){counts.uploadImage++;const index=counts.uploadImage;return config.apiReject?Promise.reject(Error('rejected')):Promise.resolve({url:'https://'+(config.coverHost||'i0.hdslb.com')+'/bfs/archive/fixture-'+index+'.jpg'})}};
 const e={$el:el,$taskConfig:m,$tool:tool,$api:api,coverForEditor:{},handleSubmit(){},handleUploadChange(){},isCoverEditorReady_4_3:false,isCoverEditorReady_16_9:false,
  imageEditor_4_3:null,imageEditor_16_9:null,$emit(event,value){counts.submit++;if(event==='submit'&&!config.noCommit){coverVM.coverList=[{main:{origin:value.origin_16_9,edited:value.edited_16_9,source:'upload'},extra:{origin:value.origin_4_3,edited:value.edited_4_3,source:'upload'}}];el.remove()}return 'emit-return';}};
 el.__vue__=e;window.editor=e;
 el.querySelector('#source').onchange=event=>{const file=event.target.files[0];tool.verifyAndLoadCoverFile(file,true).then(result=>{
  e.coverForEditor={origin_4_3:result.blobUrl,origin_16_9:result.blobUrl,source_4_3:'upload',source_16_9:'upload'};
  for(const ratio of ['4_3','16_9']){const canvas=document.createElement('canvas');canvas.width=ratio==='4_3'?120:160;canvas.height=90;canvas.getContext('2d').fillRect(0,0,40,40);
   e['imageEditor_'+ratio]={exportImage(opts){counts.export++;window.exportReceiver=this===e['imageEditor_'+ratio];const promise=Promise.resolve(canvas.toDataURL('image/jpeg',opts.quality));window.originalExportPromise=promise;return promise;}};
   e['isCoverEditorReady_'+ratio]=true;
  }if(config.fakeOrigin)e.coverForEditor.origin_4_3=e.coverForEditor.origin_16_9=URL.createObjectURL(new Blob(['foreign']));
 });};
 el.querySelector('#done').onclick=async()=>{try{const d169=await e.imageEditor_16_9.exportImage({quality:.8}),d43=await e.imageEditor_4_3.exportImage({quality:.8});
  const r43=await api.uploadImage(d43),r169=await api.uploadImage(d169);window.coverReturn=e.$emit('submit',{...e.coverForEditor,edited_4_3:r43.url,edited_16_9:r169.url});}catch(_){}};
};
</script>'''


def make_session(page=None):
    video=b'complete native video bytes'
    image=b'complete native png source'
    sha=lambda value:hashlib.sha256(value).hexdigest()
    account={'verified':True,'platform':'bilibili','identity':'owner-fingerprint','uid':'9'}
    expected={'job_id':'job-bili','receipt_nonce':'nonce-bili','platform_id':'bilibili',
              'video_sha256':sha(video),'cover_sha256':{'cover':sha(image)},'account':copy.deepcopy(account),
              'platform_fields':{'title':'标题','description':'完整正文','tags':['知识']}}
    session=SimpleNamespace(page=page or SimpleNamespace(),platform='bilibili',expected=expected,closed=False,
          started_at=time.time(),action_at=None,reopen_at=None,account=account,read_account=AsyncMock(return_value=copy.deepcopy(account)),
          assets={'video':{'name':'video.mp4','size':len(video),'sha256':sha(video),'kind':'video'},
                  'cover':{'name':'cover.png','size':len(image),'sha256':sha(image),'kind':'cover'}})
    session.page._sau_draft_evidence=session
    observer=BiliEvidenceObserver(session);session.bili_observer=observer
    return session,observer,video,image


def reports(observer):
    now=time.time()
    video={'v':1,**observer.scope,'kind':'video','selection':'1','name':'video.mp4','size':len(b'complete native video bytes'),
           'type':'video/mp4','sha256':observer.session.expected['video_sha256'],'cid':'77','filename':'upos-bili-native',
           'selected_at':now,'completed_at':now}
    cover=[]
    for i,ratio in enumerate(('4_3','16_9'),1):
        cover.append({'v':1,**observer.scope,'kind':'cover','selection':'2','ratio':ratio,'origin_name':'cover.png',
          'origin_size':len(b'complete native png source'),'origin_type':'image/png','origin_sha256':observer.session.expected['cover_sha256']['cover'],
          'derived_sha256':str(i)*64,'derived_size':3000,'derived_type':'image/jpeg',
          'url':f'https://i0.hdslb.com/bfs/archive/fixture-{i}.jpg','selected_at':now,'transform_at':now,'started_at':now,'completed_at':now,'applied_at':now})
    return video,cover


def save_body():
    return {'title':'标题','desc':'完整正文','tag':'知识','videos':[{'cid':77,'filename':'upos-bili-native'}],
            'cover':'https://i0.hdslb.com/bfs/archive/fixture-2.jpg','cover43':'https://i0.hdslb.com/bfs/archive/fixture-1.jpg'}


def list_row():
    body=save_body()
    return {'id':91,'mid':9,'cid':77,'title':body['title'],'body':'not a serialized draft',
            'cover':body['cover'],'cover43':body['cover43'],'mtime':int(time.time())}


class BiliProofTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session,self.observer,_,_=make_session()
        self.observer.drain=AsyncMock()
        video,covers=reports(self.observer)
        self.assertTrue(self.observer.accept(video))
        for c in covers:self.assertTrue(self.observer.accept(c))
        self.session.action_at=time.time()

    async def exchange(self,path,data,body=None,method=None,started_at=None,status=200):
        actual_method=method or ('POST' if path.endswith(('/add','/update')) else 'GET')
        if path.endswith(('/add','/update')):
            self.observer._note_ack_request(actual_method)
            self.observer._note_ack_response(status)
        return await self.observer.observe_exchange(url='https://member.bilibili.com'+path,
            method=actual_method,status=status,
            started_at=started_at or time.time(),observed_at=time.time(),data=data,body=body)

    async def ack(self):
        return await self.exchange('/x/vupre/web/draft/add',{'code':0,'message':'','ttl':1},save_body())

    async def test_idless_ack_is_not_candidate_then_exact_server_list_is_partial(self):
        self.assertTrue(await self.ack());self.assertIsNone(await self.observer.candidate())
        self.assertTrue(await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[list_row()]}))
        c=await self.observer.candidate();self.assertEqual(c['id'],'91');self.assertIsNone(c['tags']);self.assertIsNone(c['description'])
        self.assertEqual(c['readback_source'],'normal_draft_list');self.assertTrue(await self.observer.pre_reopen_verified(c))

    async def test_exact_official_cover_protocol_mapping_preserves_identity(self):
        url='https://i0.hdslb.com/bfs/archive/image.jpg'
        self.assertEqual(bili_cover_identity(url.replace('https:','http:')),url)
        self.assertEqual(bili_cover_identity(url.replace('https:','')),url)
        for bad in ('https://evil.test/bfs/archive/image.jpg',url+'?token=secret',url+'#fragment',
                    'https://i0.hdslb.com:443/bfs/archive/image.jpg','https://name@i0.hdslb.com/bfs/archive/image.jpg','blob:'+url):
            self.assertIsNone(bili_cover_identity(bad))
        video,covers=reports(self.observer)
        self.observer.records={}
        self.assertTrue(self.observer.accept(video))
        for cover in covers:self.assertTrue(self.observer.accept({**cover,'url':cover['url'].replace('https:','http:')}))
        self.session.action_at=time.time()
        self.assertTrue(await self.ack())
        row=list_row();row['cover']=row['cover'].replace('https:','');row['cover43']=row['cover43'].replace('https:','http:')
        await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[row]})
        candidate=await self.observer.candidate();self.assertTrue(await self.observer.pre_reopen_verified(candidate))

    async def test_exact_archive_host_still_requires_scoped_dual_export_proof(self):
        url='https://archive.biliimg.com/bfs/archive/image.jpg'
        self.assertEqual(bili_cover_identity(url),url)
        self.assertEqual(bili_cover_identity(url.replace('https:','http:')),url)
        self.assertEqual(bili_cover_identity(url.replace('https:','')),url)
        self.observer.records={}
        video,covers=reports(self.observer);self.assertTrue(self.observer.accept(video))
        for cover in covers:
            cover['url']=cover['url'].replace('i0.hdslb.com','archive.biliimg.com')
            self.assertTrue(self.observer.accept(cover))
        self.session.action_at=time.time()
        body={key:value.replace('i0.hdslb.com','archive.biliimg.com') if key in ('cover','cover43') else value for key,value in save_body().items()}
        self.assertTrue(await self.exchange('/x/vupre/web/draft/add',{'code':0,'message':'','ttl':1},body))
        row=list_row();row['cover']=body['cover'];row['cover43']=body['cover43']
        await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[row]})
        candidate=await self.observer.candidate();self.assertTrue(await self.observer.pre_reopen_verified(candidate))
        self.session.reopen_at=time.time()
        await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':{**body,'id':91,'mid':9}})
        self.assertIsNotNone(await self.observer.reopened_record(candidate))
        for bad in ('https://evilarchive.biliimg.com/bfs/archive/image.jpg','https://archive.biliimg.com.evil.test/bfs/archive/image.jpg',
                    'https://i0.biliimg.com/bfs/archive/image.jpg','https://archive.biliimg.com:443/bfs/archive/image.jpg',
                    'https://name@archive.biliimg.com/bfs/archive/image.jpg',url+'?token=secret',url+'#fragment','blob:'+url):
            self.assertIsNone(bili_cover_identity(bad))

    async def test_saved_ack_wait_and_later_ambiguous_list_revoke_partial(self):
        self.assertFalse(await self.observer.wait_saved_ack(.01))
        await self.ack();self.assertTrue(await self.observer.wait_saved_ack(.01))
        await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[list_row()]})
        candidate=await self.observer.candidate();self.assertIsNotNone(candidate)
        await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[list_row(),list_row()]})
        self.assertIsNone(await self.observer.candidate());self.assertFalse(await self.observer.pre_reopen_verified(candidate))

    async def test_same_id_normal_view_only_full_saved_fields(self):
        await self.ack();await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[list_row()]})
        c=await self.observer.candidate();self.session.reopen_at=time.time()
        raw={**save_body(),'id':91,'mid':9}
        self.assertTrue(await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':raw}))
        r=await self.observer.reopened_record(c);self.assertEqual(r['tags'],['知识']);self.assertEqual(r['description'],'完整正文')
        self.assertEqual(r['full_fields_source'],'normal_draft_view');self.assertIsNone(c['tags'])

    async def test_preaction_old_response_published_list_wrong_owner_and_duplicate_refuse(self):
        self.assertFalse(await self.exchange('/x/vupre/web/draft/add',{'code':0,'message':'','ttl':1},save_body(),started_at=self.observer.started_at))
        await self.ack()
        self.assertFalse(await self.exchange('/x/vu/web/archives',{'code':0,'data':[list_row()]}))
        for rows in ([{**list_row(),'mid':99}], [list_row(),list_row()], [{**list_row(),'cid':88}], [{**list_row(),'cover43':'wrong'}]):
            self.observer.lists=[];await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':rows});self.assertIsNone(await self.observer.candidate())

    async def test_ack_wrong_full_file_cover_fields_and_extra_data_refuse(self):
        for body in ({**save_body(),'tag':'非请求'}, {**save_body(),'desc':'少'}, {**save_body(),'cover43':'wrong'},
                     {**save_body(),'videos':[{'cid':77,'filename':'old'}]}):
            self.assertFalse(await self.exchange('/x/vupre/web/draft/add',{'code':0,'message':'','ttl':1},body))
        self.assertFalse(await self.exchange('/x/vupre/web/draft/add',{'code':0,'message':'','ttl':1,'data':{'id':91}},save_body()))
        self.assertFalse(await self.exchange('/x/vupre/web/draft/add',{'code':True,'message':'','ttl':1},save_body()))
        self.assertIsNone(await self.observer.candidate())

    async def test_ack_wait_emits_only_fixed_public_gate_counts(self):
        await self.ack()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertTrue(await self.observer.wait_saved_ack(.01))
        lines=output.getvalue().splitlines()
        self.assertEqual(len(lines),1)
        self.assertTrue(lines[0].startswith('BILI_SAVE_ACK_DIAGNOSTIC='))
        diagnostic=json.loads(lines[0].split('=',1)[1])
        expected={'request_caught':1,'post_request_caught':1,'response_caught':1,'http_2xx':1,
                  'response_body_caught':1,'response_code0':1,'response_shape':1,
                  'body_native_video_identity':1,'title_match':1,'description_match':1,'tags_match':1,
                  'cover_main_match':1,'cover_extra_match':1,'dual_covers_match':1,
                  'account_checked':1,'account_current':1,'owner_current':1,'source_current':1,'observer_timing':1,
                  'action_timing':1,'update_id_match':1,'accepted':1,
                  'source_current_at_wait_end':True,'wait_result':True}
        self.assertEqual(diagnostic,expected)
        for private_value in ('标题','完整正文','https://','owner-fingerprint','nonce-bili','77','upos-bili-native'):
            self.assertNotIn(private_value,lines[0])

    async def test_ack_wait_diagnostics_separate_request_response_and_body_capture(self):
        stages=(
            ('none',0,0,0,0),
            ('request only',1,0,0,0),
            ('response without parsed body',1,1,0,1),
        )
        for name, requests, responses, bodies, http_ok in stages:
            with self.subTest(name=name):
                self.observer._ack_diagnostic_counts={key:0 for key in self.observer._ack_diagnostic_counts}
                if requests:
                    self.observer._note_ack_request('POST')
                if responses:
                    self.observer._note_ack_response(200)
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertFalse(await self.observer.wait_saved_ack(.01))
                diagnostic=json.loads(output.getvalue().strip().split('=',1)[1])
                self.assertEqual(diagnostic['request_caught'],requests)
                self.assertEqual(diagnostic['response_caught'],responses)
                self.assertEqual(diagnostic['response_body_caught'],bodies)
                self.assertEqual(diagnostic['http_2xx'],http_ok)
                self.assertEqual(diagnostic['accepted'],0)

    async def test_ack_diagnostics_separate_response_and_each_payload_gate(self):
        cases=(
            ('http',{'status':503},'http_2xx'),
            ('code',{'data':{'code':1,'message':'','ttl':1}},'response_code0'),
            ('shape',{'data':{'code':0,'message':'','ttl':1,'extra':True}},'response_shape'),
            ('video',{'body':{**save_body(),'videos':[{'cid':88,'filename':'upos-bili-native'}]}},'body_native_video_identity'),
            ('title',{'body':{**save_body(),'title':'other'}},'title_match'),
            ('description',{'body':{**save_body(),'desc':'other'}},'description_match'),
            ('tags',{'body':{**save_body(),'tag':'other'}},'tags_match'),
            ('main cover',{'body':{**save_body(),'cover':'https://i0.hdslb.com/bfs/archive/other.jpg'}},'cover_main_match'),
            ('extra cover',{'body':{**save_body(),'cover43':'https://i0.hdslb.com/bfs/archive/other.jpg'}},'cover_extra_match'),
        )
        for name, options, failed_gate in cases:
            with self.subTest(name=name):
                self.observer.acks=[]
                self.observer._ack_diagnostic_counts={key:0 for key in self.observer._ack_diagnostic_counts}
                data=options.get('data',{'code':0,'message':'','ttl':1})
                body=options.get('body',save_body())
                self.assertFalse(await self.exchange('/x/vupre/web/draft/add',data,body,
                    status=options.get('status',200)))
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertFalse(await self.observer.wait_saved_ack(.01))
                diagnostic=json.loads(output.getvalue().strip().split('=',1)[1])
                self.assertEqual(diagnostic[failed_gate],0)
                self.assertEqual(diagnostic['accepted'],0)
                self.assertTrue(diagnostic['response_body_caught'])
                self.assertTrue(diagnostic['request_caught'])
                if failed_gate in ('cover_main_match','cover_extra_match'):
                    self.assertFalse(diagnostic['dual_covers_match'])

    async def test_ack_diagnostics_separate_account_source_and_timing(self):
        self.session.read_account.return_value={**self.session.account,'uid':'99'}
        self.assertFalse(await self.ack())
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertFalse(await self.observer.wait_saved_ack(.01))
        diagnostic=json.loads(output.getvalue().strip().split('=',1)[1])
        self.assertEqual(diagnostic['account_current'],0)
        self.assertEqual(diagnostic['owner_current'],0)
        self.assertEqual(diagnostic['body_native_video_identity'],1)

        self.observer._ack_diagnostic_counts={key:0 for key in self.observer._ack_diagnostic_counts}
        self.session.read_account.return_value=copy.deepcopy(self.session.account)
        self.session.expected['job_id']='stale-job'
        self.assertFalse(await self.ack())
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertFalse(await self.observer.wait_saved_ack(.01))
        diagnostic=json.loads(output.getvalue().strip().split('=',1)[1])
        self.assertEqual(diagnostic['source_current'],0)
        self.assertFalse(diagnostic['source_current_at_wait_end'])
        self.assertEqual(diagnostic['accepted'],0)

    async def test_ack_diagnostics_mark_observer_timing_failure(self):
        stale_started=self.observer.started_at-1
        self.assertFalse(await self.exchange('/x/vupre/web/draft/add',{'code':0,'message':'','ttl':1},
                                             save_body(),started_at=stale_started))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertFalse(await self.observer.wait_saved_ack(.01))
        diagnostic=json.loads(output.getvalue().strip().split('=',1)[1])
        self.assertEqual(diagnostic['observer_timing'],0)
        self.assertEqual(diagnostic['action_timing'],0)
        self.assertEqual(diagnostic['accepted'],0)

    async def test_account_switch_stale_nonce_and_conflicting_selection_revoke(self):
        self.session.read_account.return_value={**self.session.account,'uid':'99'};self.assertFalse(await self.ack())
        self.session.read_account.return_value=copy.deepcopy(self.session.account)
        video,_=reports(self.observer);self.assertFalse(self.observer.accept({**video,'receipt_nonce':'old'}))
        # Same selection with contradictory completion is invalidated, never last-wins.
        self.assertFalse(self.observer.accept({**video,'filename':'other'}));self.assertFalse(self.observer.upload_links() and any(x['kind']=='video' for x in self.observer.upload_links()))
        self.assertFalse(await self.ack())

    async def test_reopen_old_response_wrong_id_wrong_caption_and_two_media_refuse(self):
        await self.ack();await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[list_row()]});c=await self.observer.candidate();self.session.reopen_at=time.time()
        raw={**save_body(),'id':91,'mid':9}
        self.assertFalse(await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':raw},started_at=self.observer.started_at))
        self.assertFalse(await self.exchange('/x/vupre/web/draft/view?id=92',{'code':0,'data':raw}))
        self.assertTrue(await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':{**raw,'desc':'错误'}}))
        self.assertIsNone(await self.observer.reopened_record(c))
        self.assertFalse(await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':{**raw,'videos':raw['videos']*2}}))

    async def test_full_view_missing_tag_wrong_filename_and_swapped_slots_never_pass(self):
        await self.ack();await self.exchange('/x/vupre/web/draft/list',{'code':0,'data':[list_row()]})
        c=await self.observer.candidate();self.session.reopen_at=time.time()
        raw={**save_body(),'id':91,'mid':9}
        missing={k:v for k,v in raw.items() if k!='tag'}
        self.assertFalse(await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':missing}))
        for wrong in ({**raw,'videos':[{'cid':77,'filename':'foreign-native'}]},
                      {**raw,'cover':raw['cover43'],'cover43':raw['cover']}):
            self.assertTrue(await self.exchange('/x/vupre/web/draft/view?id=91',{'code':0,'data':wrong}))
            self.assertIsNone(await self.observer.reopened_record(c))


class BiliSDKFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop=asyncio.new_event_loop()
        async def begin():
            cls.pw=await async_playwright().start();cls.browser=await cls.pw.chromium.launch(headless=True,channel='chromium',args=['--disable-gpu'])
        cls.loop.run_until_complete(begin())
        cls.temp=tempfile.TemporaryDirectory();cls.video=Path(cls.temp.name)/'video.mp4';cls.cover=Path(cls.temp.name)/'cover.png'
        cls.video.write_bytes(b'complete native video bytes');cls.cover.write_bytes(b'complete native png source')
    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.browser.close());cls.loop.run_until_complete(cls.pw.stop());cls.loop.close();cls.temp.cleanup()
    def run_async(self, fn):return self.loop.run_until_complete(fn)
    async def fixture(self,**config):
        context=await self.browser.new_context()
        async def route(r):await r.fulfill(status=200,content_type='text/html',body=HTML.replace('CONFIG',json.dumps(config)))
        await context.route('**/*',route);page=await context.new_page();await page.goto(URL)
        session,observer,_,_=make_session(page)
        self.assertTrue(await observer.initialize())
        return context,page,session,observer
    async def native_video(self,page,observer):
        await page.locator('#video').set_input_files(str(self.video));await asyncio.sleep(.12)
        await page.locator('#finish').click();await asyncio.sleep(.1);await observer.drain()
    def test_native_file_sdk_complete_real_context_and_original_return(self):
        async def run():
            c,p,s,o=await self.fixture()
            try:
                await self.native_video(p,o);links=o.upload_links();self.assertEqual(len(links),1);self.assertEqual(links[0]['sha256'],hashlib.sha256(self.video.read_bytes()).hexdigest())
                self.assertTrue(await o.transport.evaluate('window.emitReceiver'));self.assertEqual(await o.transport.evaluate('window.emitReturned'),'original-return')
                self.assertEqual(await o.transport.evaluate('window.counts.started'),1)
                self.assertEqual((await o.diagnostic_counts())['video_records'],1)
            finally:await o.close();await c.close()
        self.run_async(run())
    def test_allocation_manual_old_duplicate_and_replaced_file_never_complete(self):
        async def run():
            for cfg in ({'partial':True},{'manual':True},{'duplicate':True},{'swap':True}):
                c,p,s,o=await self.fixture(**cfg)
                try:await self.native_video(p,o);self.assertFalse(o.upload_links(),cfg)
                finally:await o.close();await c.close()
        self.run_async(run())
    def test_owner_context_rejects_child_isolated_and_stale_job(self):
        async def run():
            c,p,s,o=await self.fixture()
            try:
                self.assertTrue(o.transport._canonical(o.transport.context_id));self.assertFalse(o.transport._canonical(o.transport.context_id+10000))
                await p.locator('#video').set_input_files(str(self.video));s.expected['job_id']='changed'
                await p.locator('#finish').click();await asyncio.sleep(.1);self.assertFalse(o.upload_links())
            finally:await o.close();await c.close()
        self.run_async(run())
    def test_native_png_actual_jpeg_export_api_and_dual_slot_commit(self):
        async def run():
            c,p,s,o=await self.fixture()
            try:
                await self.native_video(p,o);await p.locator('#cover').click();await asyncio.sleep(.1)
                await p.locator('#source').set_input_files(str(self.cover));await asyncio.sleep(.15)
                await p.locator('#done').click();await asyncio.sleep(.1);await o.drain()
                links=o.upload_links();self.assertEqual(len(links),3);self.assertEqual(sum(x['kind']=='cover' for x in links),2)
                self.assertTrue(await o.transport.evaluate('window.exportReceiver'));self.assertEqual(await o.transport.evaluate('window.coverReturn'),'emit-return')
                self.assertEqual(await o.transport.evaluate('window.counts.uploadImage'),2)
                cover=[r for r in o.records.values() if r['kind']=='cover'];self.assertEqual({r['ratio'] for r in cover},{'4_3','16_9'})
                self.assertTrue(all(r['derived_sha256']!=r['origin_sha256'] for r in cover))
                self.assertEqual(await o.diagnostic_counts(),{'video_records':1,'cover_records':2,'ack_count':0,'list_count':0,'view_count':0})
            finally:await o.close();await c.close()
        self.run_async(run())

    def test_normal_api_archive_host_links_exact_native_exports_and_rejects_lookalikes(self):
        async def run():
            for host,allowed in (('archive.biliimg.com',True),('evilarchive.biliimg.com',False),('archive.biliimg.com.evil.test',False)):
                c,p,s,o=await self.fixture(coverHost=host)
                try:
                    await self.native_video(p,o);await p.locator('#cover').click();await asyncio.sleep(.1)
                    await p.locator('#source').set_input_files(str(self.cover));await asyncio.sleep(.15)
                    await p.locator('#done').click();await asyncio.sleep(.1);await o.drain()
                    self.assertEqual(sum(x['kind']=='cover' for x in o.upload_links()),2 if allowed else 0)
                    self.assertEqual(await o.transport.evaluate('window.counts.uploadImage'),2)
                    if allowed:
                        covers=[r for r in o.records.values() if r['kind']=='cover']
                        self.assertEqual({r['ratio'] for r in covers},{'4_3','16_9'})
                        self.assertTrue(all(r['origin_sha256']==s.expected['cover_sha256']['cover'] for r in covers))
                finally:await o.close();await c.close()
        self.run_async(run())

    def test_js_cover_format_matches_python_for_exact_host_and_rejected_attributes(self):
        async def run():
            good='https://archive.biliimg.com/bfs/archive/image.jpg'
            urls=[good,good.replace('https:','http:'),good.replace('https:',''),
                  'https://i0.hdslb.com/bfs/archive/image.jpg','https://s9.hdslb.com/bfs/archive/image.jpg',
                  'https://evilarchive.biliimg.com/bfs/archive/image.jpg','https://archive.biliimg.com.evil.test/bfs/archive/image.jpg',
                  'https://other.biliimg.com/bfs/archive/image.jpg','https://archive.biliimg.com:443/bfs/archive/image.jpg',
                  'https://archive.biliimg.com:8443/bfs/archive/image.jpg','https://user:secret@archive.biliimg.com/bfs/archive/image.jpg',
                  'https://@archive.biliimg.com/bfs/archive/image.jpg',good+'?token=secret',good+'#fragment',good+'?',good+'#',
                  'blob:'+good,'data:image/jpeg;base64,AA==','https://archive.biliimg.com/not-bfs/image.jpg',
                  good+'\\other','https://archive.biliimg.com/bfs/../image.jpg','https://archive.biliimg.com/bfs/%2e%2e/image.jpg']
            c,p,s,o=await self.fixture()
            try:
                actual=await p.evaluate('values=>{'+BILI_COVER_FORMAT_JS+';return values.map(safeCover)}',urls,isolated_context=False)
                self.assertEqual(actual,[bili_cover_identity(value) is not None for value in urls])
                self.assertEqual(actual[:5],[True]*5)
                self.assertFalse(any(actual[5:]))
            finally:await o.close();await c.close()
        self.run_async(run())

    def test_closed_previous_cdn_observer_upgrades_without_inheriting_old_media_proof(self):
        async def run():
            c,p,s,o=await self.fixture(coverHost='archive.biliimg.com')
            next_observer=None
            try:
                await self.native_video(p,o);self.assertEqual(len(o.upload_links()),1)
                await o.close()
                await p.evaluate('()=>{window.__sauBiliEvidenceObserver.version=1}',isolated_context=False)
                next_session,next_observer,_,_=make_session(p)
                self.assertTrue(await next_observer.initialize())
                self.assertEqual(await next_observer.transport.evaluate('window.__sauBiliEvidenceObserver.version'),2)
                self.assertEqual(next_observer.upload_links(),[])
                await p.locator('#cover').click();await asyncio.sleep(.1)
                await p.locator('#source').set_input_files(str(self.cover));await asyncio.sleep(.15)
                await p.locator('#done').click();await asyncio.sleep(.1);await next_observer.drain()
                links=next_observer.upload_links()
                self.assertEqual(sum(x['kind']=='cover' for x in links),2)
                self.assertEqual(sum(x['kind']=='video' for x in links),0)
                self.assertEqual(await p.evaluate('()=>task.file===sdk.file&&sdk.file===sdk.uploader.file',isolated_context=False),True)
            finally:
                if next_observer:await next_observer.close()
                await o.close();await c.close()
        self.run_async(run())

    def test_active_previous_cdn_observer_is_never_replaced(self):
        async def run():
            c,p,s,o=await self.fixture()
            next_observer=None
            try:
                await p.evaluate('()=>{window.__sauBiliEvidenceObserver.version=1}',isolated_context=False)
                _,next_observer,_,_=make_session(p)
                self.assertFalse(await next_observer.ensure_ready(timeout=.05))
                self.assertEqual(await p.evaluate('()=>window.__sauBiliEvidenceObserver.version',isolated_context=False),1)
                self.assertEqual(next_observer.upload_links(),[])
                self.assertEqual(await p.evaluate('()=>counts.started',isolated_context=False),0)
            finally:
                if next_observer:await next_observer.close()
                await o.close();await c.close()
        self.run_async(run())
    def test_unknown_origin_rejected_api_and_uncommitted_pair_not_cover_proof(self):
        async def run():
            for cfg in ({'fakeOrigin':True},{'apiReject':True},{'noCommit':True}):
                c,p,s,o=await self.fixture(**cfg)
                try:
                    await self.native_video(p,o);await p.locator('#cover').click();await asyncio.sleep(.1)
                    await p.locator('#source').set_input_files(str(self.cover));await asyncio.sleep(.15);await p.locator('#done').click();await asyncio.sleep(.1);await o.drain()
                    self.assertEqual(sum(x['kind']=='cover' for x in o.upload_links()),0,cfg)
                finally:await o.close();await c.close()
        self.run_async(run())

    def test_full_session_normal_save_list_link_view_and_restored_composer(self):
        from uploader.draft_evidence import begin_draft_verification
        from uploader.draft_only import save_draft
        from workbench_model import build_platform_payload, preflight_payload
        async def run():
            context=await self.browser.new_context();requests=[];events=[]
            management='https://member.bilibili.com/platform/upload-manager/article?group=draft'
            reopened=URL+'?type=draft&draftId=91'
            row=list_row();body=save_body();raw={**body,'id':91,'mid':9}
            # Official openPlatform 1/2 remains in the composer after save;
            # the caller then uses normal Content Management. This avoids
            # route.fulfill bodies being evicted on immediate renderer exit.
            initial=HTML.replace('CONFIG','{}')+r'''<div class="submit-container"><span class="submit-draft">存草稿</span><span class="submit-add">立即投稿</span></div><a href=MANAGEMENT>内容管理</a>
              <script>document.querySelector('.submit-draft').onclick=async()=>{await fetch('/x/vupre/web/draft/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(BODY)}).then(r=>r.json())};</script>'''.replace('BODY',json.dumps(body,ensure_ascii=False)).replace('MANAGEMENT',json.dumps(management))
            listing='<a href="'+reopened+'">标题</a><script>fetch("/x/vupre/web/draft/list")</script>'
            restore=r'''<div class="upload-wrp" style="width:400px;height:120px"><div class="upload bcc-upload"><input type="file" accept=".mp4"></div></div>
              <div class="video-title"><div class="video-title-content"><input value="标题"></div></div><div class="desc-container"><div class="ql-editor" contenteditable="true">完整正文</div></div>
              <div class="tag-container"><div class="tag-input-wrp"><input></div></div><div class="cover-module-main"></div>
              <script>const business=()=>{throw Error('business method must not be called')};
               const manager={tasks:[],currentTaskId:'restored-fixture',createTask:business};
               const publisher={$el:document.querySelector('.upload-wrp'),id:manager.currentTaskId,$taskConfig:manager,videoTasks:[{cid:77,filename:'upos-bili-native',status:3}],$recordConfig:{data:[]},checkSubTask:business,handleSubmitDraft:business};
               manager.tasks=[{id:publisher.id,videoTasks:publisher.videoTasks}];
               document.querySelector('.upload-wrp').__vue__=publisher;
               document.querySelector('.tag-input-wrp').__vue__={$options:{name:'tag-input'},setCustomTag:business,submitTags:['知识'],checkCustomTagLoading:false};
               document.querySelector('.cover-module-main').__vue__={openCoverEditor:business,submitCoverEditor:business,defaultCoverIndex:0,coverList:[{main:{edited:COVER},extra:{edited:COVER43}}]};
               fetch('/x/vupre/web/draft/view?id=91');</script>'''.replace('COVER43',json.dumps(body['cover43'])).replace('COVER',json.dumps(body['cover']))
            async def route(r):
                # Patchright add_init_script rewrites the fallback routing URL
                # to an internal marker. Fulfill the original local document;
                # no native CDP request/response metadata is replaced here.
                url=r.request._impl_obj._initializer['url'];requests.append((r.request.method,url))
                if '/x/vupre/web/draft/' in url:
                    path=url.split('/x/vupre/web/draft/',1)[1].split('?',1)[0];events.append(path)
                    reply={'code':0,'message':'','ttl':1} if path=='add' else {'code':0,'data':[row] if path=='list' else raw}
                    return await r.fulfill(content_type='application/json',body=json.dumps(reply,ensure_ascii=False))
                document=initial if url==URL else listing if url==management else restore if url==reopened else None
                if document is None:return await r.abort()
                await r.fulfill(content_type='text/html;charset=utf-8',body='<!doctype html><meta charset="utf-8">'+document)
            await context.route('**/*',route);page=await context.new_page();await page.goto(URL)
            payload={'video':str(self.video),'cover':str(self.cover),'title':'标题','description':'完整正文','tags':['知识']}
            checked=preflight_payload(payload,['bilibili']);self.assertEqual(checked['errors'],[])
            expected=build_platform_payload(checked,'bilibili','real-fixture-job','real-fixture-nonce')
            owner={'verified':True,'platform':'bilibili','identity':'owner-fingerprint','uid':'9'};expected['account']=owner
            session=await begin_draft_verification(page,'bilibili',expected,account_reader=lambda:dict(owner))
            try:
                self.assertEqual(session.issues,[]);self.assertIsNotNone(session.bili_observer)
                await self.native_video(page,session.bili_observer)
                await page.locator('#cover').click();await asyncio.sleep(.1);await page.locator('#source').set_input_files(str(self.cover))
                await asyncio.sleep(.15);await page.locator('#done').click();await asyncio.sleep(.1);await session.drain()
                result=await save_draft(page,'B站','标题',expected=expected,receipt_timeout=8)
                self.assertEqual(result['status'],'platform_draft_verified',result['reasons']);self.assertTrue(all(result['verification'].values()))
                self.assertEqual(events,['add','list','view']);self.assertEqual(result['draft_id'],'91')
                self.assertEqual(sum(method=='POST' for method,url in requests),1)
                self.assertFalse(any('/x/vu/web/add' in url for method,url in requests))
                self.assertIsNone(page._sau_draft_evidence)
            finally:await session.close();await context.close()
        self.run_async(run())

if __name__=='__main__':unittest.main()
