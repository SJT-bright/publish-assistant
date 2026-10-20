"""Synthetic official TC processing/read/consumer contracts; no account traffic."""
import asyncio
import copy
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from patchright.async_api import async_playwright
from tests import test_tc_processing as fixture
from tests import test_tc_async_clip as async_fixture
from uploader.draft_evidence import parse_record, verify_evidence
from uploader.tc_assets import tc_reopened_assets_match
from uploader.tc_reopen_mapping import verify_processing_reopen
from uploader.tc_resource import tc_resource_identity


def scene_resource(label='processed'):
    return 'https://finder.video.qq.com/251/20302/stodownload?' + urlencode({
        'encfilekey': 'synthetic-'+label, 'uzid': 'synthetic-id-'+label, 'm': '',
        'hy': 'SH', 'idx': '1', 'scene': '1', 'basedata': 'synthetic-context-'+label, 'token': 'synthetic-token'})


class MappingProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ProcessingProtocolTests.asyncSetUp
    make_session=fixture.ProcessingProtocolTests.make_session
    allocate=fixture.ProcessingProtocolTests.allocate
    finish=fixture.ProcessingProtocolTests.finish
    attest_normal_reopen=fixture.ProcessingProtocolTests.attest_normal_reopen
    pending_snapshot=async_fixture.AsyncClipProtocolTests.pending_snapshot
    pending=async_fixture.AsyncClipProtocolTests.pending
    prepared_saved=async_fixture.AsyncClipProtocolTests.prepared_saved

    def record(self, url, size, *, handle=2):
        return parse_record('tencent', {'objectId':'fixture-saved-record','handleStatus':handle,'desc':{
            'description':'fixture caption','media':[{'url':url,'md5sum':getattr(self,'asset_id','a'*32),'urlCdnTaskId':self.ticket['draftId'],
                'width':320,'height':240,'videoPlayLen':6,'fileSize':str(size),
                'thumbUrl':fixture.resource('automatic-cover')}]}},owner=self.session.account['uid'])

    async def ready(self, uuid='a'*32):
        self.asset_id=uuid
        self.state['uuid']=uuid
        await self.pending()
        self.candidate=self.record(fixture.SOURCE,self.size)
        self.candidate['fresh']=True
        await self.prepared_saved(self.candidate)
        self.assertTrue(await self.observer.freeze_for_reopen(self.candidate))
        self.attest_normal_reopen(self.candidate)
        self.state=None
        await self.observer.drain()
        await asyncio.sleep(.04)
        self.reopened=self.record(scene_resource(),23)
        self.reopened.update(fresh=False,tc_normal_read='get_draft_list',request_method='POST',
            request_at=time.time(),observed_at=time.time())
        self.session.reads=[self.reopened]

    async def proof(self):
        with patch('uploader.tc_assets.tc_reopened_assets_match',new=AsyncMock(return_value=True)) as native:
            proof=await verify_processing_reopen(self.session,self.candidate,self.reopened,self.session.account)
        return proof,native

    async def test_successful_server_task_mapping_preserves_distinct_ids_and_original_sha(self):
        await self.ready()
        proof,native=await self.proof()
        self.assertIsNotNone(proof)
        self.assertNotEqual(self.candidate['video_ids'],self.reopened['video_ids'])
        links=self.session.upload_links()
        self.assertEqual(links,self.links)
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,links,self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account,tc_processing_mapping=proof)
        self.assertTrue(all(checks.values()))
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,links,self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account)
        self.assertFalse(checks['reopened'])
        native.assert_awaited_once_with(self.session.page,self.reopened,restored_from=self.candidate,strict_cover_media=True)

    async def test_exact_native_guid_asset_id_maps_but_modified_stripped_uppercase_or_wrong_task_reject(self):
        guid='12345678-abcd-1234-abcd-123456789abc'
        await self.ready(uuid=guid)
        self.assertEqual(self.candidate['tc_media'][0]['uuid'],guid)
        self.assertEqual(self.observer._frozen_reopen['native']['uuid'],guid)
        proof,_=await self.proof();self.assertIsNotNone(proof)
        original=copy.deepcopy(self.reopened)
        for case in ('one-character','stripped','uppercase','task'):
            with self.subTest(case=case):
                self.reopened=copy.deepcopy(original);self.session.reads=[self.reopened]
                row=self.reopened['tc_media'][0]
                if case=='one-character':row['uuid']=guid[:-1]+'d'
                if case=='stripped':row['uuid']=guid.replace('-','')
                if case=='uppercase':row['uuid']=guid.upper()
                if case=='task':row['task']='foreign-task'
                proof,native=await self.proof();self.assertIsNone(proof);native.assert_not_awaited()
        from uploader.tc_reopen_mapping import canonical_media
        wire={'url':fixture.SOURCE,'md5sum':guid,'urlCdnTaskId':'task','width':320,'height':240,'videoPlayLen':6,'fileSize':str(self.size)}
        self.assertEqual(canonical_media(wire)['uuid'],guid)
        for bad in (guid.upper(),guid+'x','arbitrary-id',guid.replace('-','_',1)):
            self.assertIsNone(canonical_media({**wire,'md5sum':bad}))
        self.assertEqual(canonical_media({**wire,'md5sum':guid.replace('-','')})['uuid'],guid.replace('-',''))
        self.assertNotEqual(canonical_media(wire),canonical_media({**wire,'md5sum':guid.replace('-','')}))

    async def test_already_processed_task_bound_save_uses_same_strict_reopen_contract(self):
        self.state['uuid']='a'*32
        await self.pending();await asyncio.sleep(.04)
        for key in ('mode','consumed_url','url_cdn_task_id'):self.state.pop(key,None)
        self.state['processed_url']=fixture.DERIVED
        self.state['result']={**self.state['origin'],'fileSize':23}
        self.state['consumed']=copy.deepcopy(self.state['result'])
        await self.finish(data={'errCode':0,'data':{'flag':1,'url':fixture.DERIVED,**self.state['result']}},when=time.time())
        await self.observer.drain()
        self.candidate=self.record(fixture.DERIVED,23);self.candidate['fresh']=True
        await self.prepared_saved(self.candidate)
        self.assertTrue(await self.observer.freeze_for_reopen(self.candidate))
        self.attest_normal_reopen(self.candidate);self.state=None;await self.observer.drain()
        self.reopened=self.record(scene_resource(),24)
        self.reopened.update(fresh=False,tc_normal_read='get_draft_list',request_method='POST',request_at=time.time(),observed_at=time.time())
        self.session.reads=[self.reopened]
        proof,_=await self.proof();self.assertIsNotNone(proof)
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,self.session.upload_links(),self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account,tc_processing_mapping=proof)
        self.assertTrue(all(checks.values()))

    async def test_wrong_task_uuid_target_duration_owner_sha_old_read_or_success_state_reject(self):
        await self.ready()
        saved=copy.deepcopy(self.reopened)
        expected=copy.deepcopy(self.session.expected)
        for mode in ('task','uuid','width','duration','size','owner','sha','old','method','running','failed','unknown-url','unobserved','fields','cover'):
            with self.subTest(mode=mode):
                self.reopened=copy.deepcopy(saved);self.session.reads=[self.reopened]
                self.session.expected=copy.deepcopy(expected)
                row=self.reopened['tc_media'][0]
                if mode=='task':row['task']='foreign-task'
                if mode=='uuid':row['uuid']='b'*32
                if mode=='width':row['metadata']['width']+=1
                if mode=='duration':row['metadata']['duration']+=1
                if mode=='size':row['metadata']['fileSize']=0
                if mode=='owner':self.reopened['owner']='foreign-owner'
                if mode=='sha':self.session.expected['video_sha256']='0'*64
                if mode=='old':self.reopened['request_at']=self.session.reopen_at-1
                if mode=='method':self.reopened['request_method']='GET'
                if mode=='running':self.reopened['tc_handle_status']=1
                if mode=='failed':self.reopened['tc_handle_status']=3
                if mode=='unknown-url':row['url']='https://unknown.invalid/video'
                if mode=='unobserved':self.session.reads=[]
                if mode=='fields':self.reopened['caption']='foreign caption'
                if mode=='cover':self.reopened['cover_ids']=[]
                proof,native=await self.proof()
                self.assertIsNone(proof);native.assert_not_awaited()

    async def test_native_consumption_failure_and_mutation_after_read_cannot_create_mapping(self):
        await self.ready()
        with patch('uploader.tc_assets.tc_reopened_assets_match',new=AsyncMock(return_value=False)):
            self.assertIsNone(await verify_processing_reopen(self.session,self.candidate,self.reopened,self.session.account))
        async def mutate(*args,**kwargs):
            self.session.reads.clear();return True
        with patch('uploader.tc_assets.tc_reopened_assets_match',new=mutate):
            self.assertIsNone(await verify_processing_reopen(self.session,self.candidate,self.reopened,self.session.account))

    async def test_proof_is_bound_to_exact_response_expected_links_and_account(self):
        await self.ready();proof,_=await self.proof()
        for mode in ('row','expected','links','account'):
            with self.subTest(mode=mode):
                e,r,l,a=copy.deepcopy(self.session.expected),copy.deepcopy(self.reopened),copy.deepcopy(self.links),copy.deepcopy(self.session.account)
                if mode=='row':r['tc_media'][0]['url']=scene_resource('later-read')
                if mode=='expected':e['receipt_nonce']='foreign-nonce'
                if mode=='links':l[0]['sha256']='0'*64
                if mode=='account':a['uid']='foreign-owner'
                checks,_=verify_evidence('tencent',e,self.candidate,l,a,reopened=r,ui_fields=True,
                    account_after=a,tc_processing_mapping=proof)
                self.assertFalse(checks['reopened'])
        checks,_=verify_evidence('tencent',self.session.expected,self.candidate,self.links,self.session.account,
            reopened=self.reopened,ui_fields=True,account_after=self.session.account,tc_processing_mapping={'verified':True})
        self.assertFalse(checks['reopened'])

    async def test_late_known_terminal_keeps_original_history_but_bad_terminal_revokes_it(self):
        for mode in ('success','width','duration','size','task','failed','timeout'):
            with self.subTest(mode=mode):
                await self.asyncSetUp();await self.ready()
                dims={'width':320,'height':240,'duration':6,'fileSize':'23'}
                body=dict(self.ticket);data={'errCode':0,'data':{'flag':1,'url':scene_resource(),**dims}}
                if mode=='width':data['data']['width']+=1
                if mode=='duration':data['data']['duration']+=1
                if mode=='size':data['data']['fileSize']='0'
                if mode=='task':body['draftId']='foreign-task'
                if mode=='failed':data['data']['flag']=3
                if mode=='timeout':data['data']['flag']=4
                await self.finish(body=body,data=data,when=time.time())
                links=self.session.upload_links()
                if mode=='success':
                    self.assertEqual(links,self.links)
                    self.assertIsNotNone(self.observer._frozen_reopen)
                    proof,_=await self.proof();self.assertIsNotNone(proof)
                else:self.assertIsNone(self.observer._frozen_reopen)


class ExactRestoredConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:self.skipTest('ffmpeg unavailable for actual local video metadata fixture')
        self.temp=tempfile.TemporaryDirectory()
        self.mp4=Path(self.temp.name)/'derived.mp4'
        subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=green:s=320x240:r=1','-t','6',
            '-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart','-y',str(self.mp4)],check=True,capture_output=True)
        self.p=await async_playwright().start()
        self.browser=await self.p.chromium.launch(headless=True,channel='chrome')
        self.context=await self.browser.new_context()
        async def route(r):
            if r.request.url.startswith('https://finder.video.qq.com/'):
                await r.fulfill(content_type='video/mp4',body=self.mp4.read_bytes())
            else:await r.fulfill(content_type='text/html',body='<style>video{width:320px;height:240px}img{width:30px;height:30px}</style><div id="editor"><div id="material"><div id="preview"><div id="full"><video></video></div></div></div></div>')
        await self.context.route('**/*',route)
        self.page=await self.context.new_page();await self.page.goto('https://channels.weixin.qq.com/platform/post/create')
        self.original={'id':'fixture-saved','owner':'fixture-owner','fresh':True}
        self.url=scene_resource()
        raw={'objectId':'fixture-saved','handleStatus':2,'desc':{'description':'fixture caption','media':[{
            'url':self.url,'md5sum':'a'*32,'urlCdnTaskId':'fixture-task','width':320,'height':240,'videoPlayLen':6,
            'fileSize':str(self.mp4.stat().st_size),'coverUrl':fixture.resource('portrait'),'shareCoverUrl':fixture.resource('landscape'),
            'thumbUrl':fixture.resource('thumb')}]}}
        self.record=parse_record('tencent',raw,owner='fixture-owner')
        self.session=SimpleNamespace(platform='tencent',closed=False,account={'uid':'fixture-owner'},saves=[self.original],
            reads=[self.record],normal_ui_binding={'id':'fixture-saved','owner':'fixture-owner','control':'post_draft_item_edit'})
        self.page._sau_draft_evidence=self.session
        await self.page.evaluate('''({url,size,media})=>{
         const editor=document.querySelector('#editor'),el=document.querySelector('#material'),preview=document.querySelector('#preview'),full=document.querySelector('#full'),video=document.querySelector('video');
         const fn=()=>{throw Error('business method called');};
         const file={isVideo:true,isImage:false,isValid:true,status:'done',uuid:'a'.repeat(32),size:String(size),url,httpsUrl:url,
          extInfo:{localFileUrl:url,width:320,height:240,duration:6},response:{httpsUrl:url}};
         const material={$el:el,fileList:[file],$refs:{postUpload:{value:[file],handleCropVideo:fn,updateVideo:fn,updateFile:fn,setFileList:fn}},postStore:{postObjDesc:{media:[media]}},
          handleFileListChange:fn,handleSetFileList:fn,handleDeleteFile:fn,updateFile:fn};
         const fs={$el:full,_props:{mediaList:[file]},$refs:{video},$parent:material};
         const pv={$el:preview,_props:{mediaList:[file]},$refs:{fullScreen:fs},$parent:material};
         editor.__vue__={$el:editor,$refs:{postCreate:{$el:editor}},draftStore:{objectId:'fixture-saved'}};
         el.__vue__=material;preview.__vue__=pv;full.__vue__=fs;
         window.fixture={file,material,fs,pv,editor};video.src=url;
         for(const source of [media.coverUrl,media.shareCoverUrl]){const img=document.createElement('img');img.src=source;el.append(img);}
        }''',{'url':self.url,'size':self.mp4.stat().st_size,'media':raw['desc']['media'][0]},isolated_context=False)
        await self.page.wait_for_function('document.querySelector("video").readyState>=1',timeout=10000)

    async def asyncTearDown(self):
        if hasattr(self,'context'):
            await self.context.close();await self.browser.close();await self.p.stop();self.temp.cleanup()

    async def test_real_browser_response_start_precedes_image_when_python_callback_is_delayed(self):
        from uploader.tc_preview_evidence import TCPreviewObserver,browser_response_times
        from PIL import Image
        import io
        image='https://finder.video.qq.com/251/20304/stodownload?' + urlencode({
            'encfilekey':'timing','uzid':'synthetic-context','m':'','hy':'SH','idx':'1','picformat':'200','scene':'1'})
        body=io.BytesIO();Image.new('RGB',(24,18),'red').save(body,format='JPEG')
        await self.context.route('https://channels.weixin.qq.com/platform/synthetic-read',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'url':image})))
        await self.context.route(image,lambda route:route.fulfill(content_type='image/jpeg',body=body.getvalue()))
        self.session.expected={'job_id':'fixture-job','receipt_nonce':'fixture-nonce'}
        self.session.account.update(verified=True)
        self.session.reopen_at=time.time();self.session.page=self.page
        observer=TCPreviewObserver(self.session);seen={};tasks=[]
        async def capture(response):
            if response.url.endswith('/platform/synthetic-read'):
                seen['timing']=browser_response_times(response,self.session.reopen_at)
                await asyncio.sleep(.05);seen['python_callback']=time.time()
            elif response.url==image:
                await observer.observe(response,time.time())
        def handler(response):tasks.append(asyncio.create_task(capture(response)))
        self.page.on('response',handler)
        try:
            await self.page.evaluate("""async()=>{const row=await(await fetch('/platform/synthetic-read',{method:'POST'})).json();const image=new Image();image.src=row.url;document.body.append(image);await image.decode();}""",isolated_context=False)
            await asyncio.gather(*tasks)
            self.assertIsNotNone(seen['timing']['response_start']);self.assertEqual(len(observer.records),1)
            self.assertGreaterEqual(observer.records[0]['request_at'],seen['timing']['response_start'])
            self.assertLess(observer.records[0]['request_at'],seen['python_callback'])
            self.assertEqual(observer.records[0]['sha256'],__import__('hashlib').sha256(body.getvalue()).hexdigest())
        finally:self.page.remove_listener('response',handler);await observer.close()

    async def test_exact_response_same_id_unique_typed_file_preview_and_video_consumption(self):
        self.assertTrue(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original))

    async def test_restored_guid_asset_id_is_consumed_exactly_without_format_alias(self):
        guid='12345678-abcd-1234-abcd-123456789abc'
        self.record['tc_media'][0]['uuid']=guid
        await self.page.evaluate("""guid=>{fixture.file.uuid=guid;fixture.material.postStore.postObjDesc.media[0].md5sum=guid;}""",guid,isolated_context=False)
        self.assertTrue(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original))
        for value in (guid[:-1]+'d',guid.replace('-',''),guid.upper()):
            await self.page.evaluate('value=>{fixture.file.uuid=value}',value,isolated_context=False)
            self.assertFalse(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original))
        await self.page.evaluate('value=>{fixture.file.uuid=value}',guid,isolated_context=False)
        self.assertTrue(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original))

    async def test_legacy_automatic_thumb_is_same_task_native_media_and_never_a_custom_crop_role(self):
        from PIL import Image
        import io
        thumb='https://finder.video.qq.com/251/20304/stodownload?' + urlencode({
            'encfilekey':'thumb','uzid':'synthetic-context','m':'','hy':'SH','idx':'1','picformat':'200','scene':'1'})
        body=io.BytesIO();Image.new('RGB',(24,18),'red').save(body,format='JPEG')
        await self.context.route(thumb,lambda route:route.fulfill(content_type='image/jpeg',body=body.getvalue()))
        resource=tc_resource_identity(thumb)
        self.record['tc_cover_media']={};self.record['tc_thumb_media']={'url':thumb,'resource':resource}
        self.record['cover_ids']=[resource];self.record['cover_slots']={'cover_portrait':[],'cover_landscape':[resource]}
        self.session.expected={'cover_sha256':{}}
        await self.page.evaluate("""url=>{fixture.file.thumbUrl=url;fixture.material.postStore.postObjDesc.media[0].thumbUrl=url;
          const img=document.createElement('img');img.id='automatic-thumb';img.src=url;fixture.material.$el.append(img);}""",thumb,isolated_context=False)
        await self.page.wait_for_function('document.querySelector("#automatic-thumb").naturalWidth>0')
        self.assertTrue(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original,strict_cover_media=True))
        self.session.expected['cover_sha256']={'cover_landscape':'a'*64}
        self.assertFalse(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original,strict_cover_media=True))

    async def test_same_file_decoded_cover_roles_are_consumed_by_unique_native_preview(self):
        from PIL import Image
        import io
        def image_url(label):
            return 'https://finder.video.qq.com/251/20304/stodownload?' + urlencode({
                'encfilekey':label,'uzid':'synthetic-context','m':'','hy':'SH','idx':'1','picformat':'200','scene':'1'})
        urls={'cover_portrait':image_url('portrait'),'cover_landscape':image_url('landscape')}
        body=io.BytesIO();Image.new('RGB',(24,18),'red').save(body,format='JPEG')
        await self.context.route('https://finder.video.qq.com/251/20304/*',lambda route:route.fulfill(content_type='image/jpeg',body=body.getvalue()))
        for slot,url in urls.items():
            identity=tc_resource_identity(url)
            self.record['tc_cover_media'][slot]={'url':url,'resource':identity}
            self.record['cover_slots'][slot]=[identity]
        self.record['cover_ids']=sorted([value['resource'] for value in self.record['tc_cover_media'].values()]+[self.record['tc_thumb_media']['resource']])
        await self.page.evaluate("""({roles,thumb})=>{
          const {file,material}=fixture,fn=()=>{throw Error('business method called');};
          file.coverUrl=roles.cover_portrait;file.shareCoverUrl=roles.cover_landscape;file.thumbUrl=thumb;
          Object.assign(material.postStore.postObjDesc.media[0],{coverUrl:file.coverUrl,shareCoverUrl:file.shareCoverUrl});
          const el=document.createElement('div');el.className='cover-preview-wrap';el.style.cssText='width:100px;height:100px';
          for(const [slot,url] of Object.entries(roles)){
            const card=document.createElement('div'),img=document.createElement('img');
            card.className=slot==='cover_portrait'?'vertical-cover-wrap':'horizon-cover-wrap';
            img.className=slot==='cover_portrait'?'vertical-img-size':'horizon-img-size';img.src=url;card.append(img);el.append(card);
          }
          el.__vue__={$el:el,$parent:material,_props:{video:file},handleUpdateCover:fn,handleClickEdit:fn};
          material.$el.append(el);fixture.cover=el;
        }""",{'roles':urls,'thumb':self.record['tc_thumb_media']['url']},isolated_context=False)
        await self.page.wait_for_function("[...document.querySelectorAll('.cover-preview-wrap img')].every(x=>x.complete&&x.naturalWidth>0)",timeout=10000)
        async def check():return await tc_reopened_assets_match(self.page,self.record,restored_from=self.original,cover_roles=urls,strict_cover_media=True)
        self.assertTrue(await check())
        for change,restore in [
            ('fixture.cover.__vue__._props.video={}', 'fixture.cover.__vue__._props.video=fixture.file'),
            ('fixture.file.coverUrl="other"','fixture.file.coverUrl=fixture.material.postStore.postObjDesc.media[0].coverUrl'),
            ('fixture.file.thumbUrl="other"','fixture.file.thumbUrl=fixture.material.postStore.postObjDesc.media[0].thumbUrl'),
            ('fixture.cover.append(fixture.cover.querySelector(".vertical-cover-wrap").cloneNode(true))','fixture.cover.lastElementChild.remove()'),
            ('fixture.cover.style.display="none"','fixture.cover.style.display=""'),
        ]:
            with self.subTest(change=change):
                await self.page.evaluate(change,isolated_context=False);self.assertFalse(await check())
                await self.page.evaluate(restore,isolated_context=False);self.assertTrue(await check())
        await self.page.evaluate("()=>{const clone=fixture.cover.cloneNode(true);clone.__vue__={...fixture.cover.__vue__,$el:clone};fixture.material.$el.append(clone)}",isolated_context=False)
        self.assertFalse(await check())

    async def test_other_read_uuid_size_metadata_task_owner_duplicate_or_old_editor_reject(self):
        for change,restore in [
            ('fixture.file.response.httpsUrl="foreign"','fixture.file.response.httpsUrl=fixture.file.url'),
            ('fixture.file.uuid="b".repeat(32)','fixture.file.uuid="a".repeat(32)'),
            ('fixture.file.size="1"',''),
            ('fixture.file.extInfo.width++','fixture.file.extInfo.width--'),
            ('fixture.material.postStore.postObjDesc.media[0].urlCdnTaskId="foreign"','fixture.material.postStore.postObjDesc.media[0].urlCdnTaskId="fixture-task"'),
            ('fixture.fs._props.mediaList=[{}]','fixture.fs._props.mediaList=[fixture.file]'),
            ('fixture.material.$refs.postUpload.value=[fixture.file,fixture.file]','fixture.material.$refs.postUpload.value=[fixture.file]'),
            ('fixture.fs._props.mediaList=[fixture.file,fixture.file]','fixture.fs._props.mediaList=[fixture.file]'),
            ('fixture.editor.__vue__.draftStore.objectId="old"','fixture.editor.__vue__.draftStore.objectId="fixture-saved"'),
        ]:
            with self.subTest(change=change):
                size=await self.page.evaluate('fixture.file.size',isolated_context=False)
                await self.page.evaluate(change,isolated_context=False)
                self.assertFalse(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original))
                if change.startswith('fixture.file.size='):
                    await self.page.evaluate('s=>{fixture.file.size=s}',size,isolated_context=False)
                else:await self.page.evaluate(restore,isolated_context=False)
        clone=copy.deepcopy(self.record);clone['tc_media'][0]['url']=scene_resource('another-read')
        clone['tc_media'][0]['resource']=tc_resource_identity(clone['tc_media'][0]['url']);clone['video_ids']=[clone['tc_media'][0]['resource']]
        self.session.reads=[clone]
        self.assertFalse(await tc_reopened_assets_match(self.page,clone,restored_from=self.original))
        self.session.reads=[self.record]
        await self.page.evaluate('()=>{const v=document.createElement("video");v.src=fixture.file.url;document.querySelector("#material").append(v)}',isolated_context=False)
        self.assertFalse(await tc_reopened_assets_match(self.page,self.record,restored_from=self.original))
