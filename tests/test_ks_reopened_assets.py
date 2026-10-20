"""Isolated real DOM/media + committed React consumer fixtures, no accounts."""
import asyncio
import base64
import copy
import hashlib
import shutil
import subprocess
import time
import unittest
from types import SimpleNamespace

from patchright.async_api import async_playwright
from uploader.draft_only import _ui_assets
from uploader.ks_evidence import KSEvidenceObserver
from uploader.ks_reopened_assets import ks_reopened_assets_match, native_ks_snapshot_response

VIDEO_ID = '123456789'
COVER_ID = 'fixture-cover-key'
URL = 'https://cdn.fixture.test/opaque/signed-video?signature=fixture'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')

SETUP = r"""async ({url,cover})=>{
 const bytes=Uint8Array.from(atob(cover),c=>c.charCodeAt(0)),blob=new Blob([bytes],{type:'image/png'}),coverUrl=URL.createObjectURL(blob);
 const host=document.createElement('main');host.id='preview-tours';document.body.append(host);
 const video=document.createElement('video');video.width=160;video.height=90;video.preload='metadata';host.append(video);video.src=url;
 const wrap=document.createElement('section'),img=document.createElement('img');img.width=64;img.height=48;wrap.append(img);document.body.append(wrap);img.src=coverUrl;
 const form={fileId:123456789,coverKey:'fixture-cover-key',pkCoverKey:'',profileCoverKey:'',caption:'fixture-caption',fileName:'fixture.webm',fileSize:'fixture',url};
 const progress={status:3,percent:0,localFileData:null};
 const root={memoizedState:null,child:null,stateNode:null};root.stateNode={current:root};
 const make=(parent,props,state=null,node=null)=>({return:parent,memoizedProps:props,memoizedState:state,stateNode:node,child:null,sibling:null});
 const owner=make(root,{}, {memoizedState:{current:form},next:{memoizedState:progress,next:null}});root.child=owner;
 const vp={url,coverUrl,captionStr:'fixture-caption',coPublishUser:[],hasVideoComposite:false};
 const consumer=make(owner,vp),vf=make(consumer,{src:url},null,video);owner.child=consumer;consumer.child=vf;video.__reactFiber$fixture=vf;
 const ep={snapshotInfo:form,fileId:'123456789',uploadFinish:true,coverUrl,pkUrl:'',profileUrl:'',supportProfileCover:true,onSaveCover(){}};
 const wrapper=make(owner,ep),editor=make(wrapper,{...ep}),imf=make(editor,{src:coverUrl},null,img);consumer.sibling=wrapper;wrapper.child=editor;editor.child=imf;img.__reactFiber$fixture=imf;
 window.fixture={root,owner,form,progress,video,img,consumer,vf,ep,wrapper,editor,imf,host,wrap,coverUrl,make};
 await Promise.all([new Promise((resolve,reject)=>{if(video.readyState>=1)return resolve();video.onloadedmetadata=resolve;video.onerror=reject;}),
 new Promise((resolve,reject)=>{if(img.complete&&img.naturalWidth)return resolve();img.onload=resolve;img.onerror=reject;})]);
 return {duration:video.duration,width:video.videoWidth};
}"""


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg fixture dependency unavailable')
class KSReopenedMediaTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.video_bytes = subprocess.check_output([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
            'color=c=blue:s=160x90:r=10:d=1', '-c:v', 'libvpx', '-f', 'webm', 'pipe:1'])

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, args=['--disable-gpu'])
        self.page = await self.browser.new_page()
        self.remote_requests = []
        async def route(r):
            self.remote_requests.append(r.request.url)
            if r.request.url.startswith('https://cdn.fixture.test/'):
                await r.fulfill(content_type='video/webm', body=self.video_bytes)
            elif '/cover/view?' in r.request.url:
                await r.fulfill(content_type='image/png', body=PNG)
            else:
                await r.fulfill(content_type='text/html', body='<html><body></body></html>')
        await self.page.route('**/*', route)
        await self.page.goto('https://cp.kuaishou.com/article/publish/video')
        await self.page.main_frame.evaluate(SETUP, {'url': URL, 'cover': base64.b64encode(PNG).decode()}, isolated_context=False)
        now = time.time()
        self.account = {'platform': 'kuaishou', 'uid': '1234567890', 'identity': 'fixture-account', 'verified': True}
        self.candidate = {'id': VIDEO_ID, 'owner': self.account['uid'], 'fresh': True, 'video_count': 1,
            'video_ids': [VIDEO_ID], 'cover_ids': [COVER_ID], 'cover_slots': {'cover': [COVER_ID]}}
        self.read = {**copy.deepcopy(self.candidate), 'fresh': False, 'observed_at': now, 'ks_preview_source': URL,
                     'ks_current_time': 1234567890000, 'ks_preview_scope_bound': True}
        self.links = [{'kind': 'video', 'complete': True, 'sha256': 'a' * 64, 'ids': [VIDEO_ID]},
                      {'kind': 'cover', 'complete': True, 'sha256': 'b' * 64, 'ids': [COVER_ID]}]
        async def reopened(_):
            return self.read
        self.session = SimpleNamespace(platform='kuaishou', page=self.page, account=self.account, closed=False,
            started_at=now - 2, reopen_at=now - 1, expected={'job_id': 'fixture-job', 'receipt_nonce': 'fixture-nonce',
            'account': {k: self.account[k] for k in ('uid', 'identity')}, 'video_sha256': 'a' * 64,
            'cover_sha256': {'cover': 'b' * 64}}, reopened_record=reopened, upload_links=lambda: self.links)
        self.page._sau_draft_evidence = self.session
        self.observer = KSEvidenceObserver(self.session)
        self.session.ks_observer = self.observer
        self.observer.records.append({'operation': 'cover_view', 'valid': True, 'cover_key': COVER_ID,
            'body_sha256': hashlib.sha256(PNG).hexdigest(), 'request_at': now, 'observed_at': now})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def change(self, code):
        await self.page.main_frame.evaluate('()=>{' + code + '}', isolated_context=False)

    async def gate(self):
        return await ks_reopened_assets_match(self.page, self.candidate)

    async def test_opaque_video_and_existing_blob_cover_require_full_consumer_chain(self):
        self.assertNotIn(VIDEO_ID, URL)
        before = len(self.remote_requests)
        self.assertTrue(await self.gate())
        self.assertTrue(await _ui_assets(self.page, self.candidate))
        self.assertEqual(len(self.remote_requests), before, 'read gate must issue no remote fetch')
        self.assertFalse(await _ui_assets(self.page, self.candidate, video_only=True))

    async def test_missing_fresh_saved_read_or_complete_original_hash_links_rejected(self):
        for key, wrong in [('fresh', False), ('owner', 'other-owner'), ('video_count', 2), ('id', 'other-video')]:
            old = self.candidate[key];self.candidate[key] = wrong
            self.assertFalse(await self.gate(), key);self.candidate[key] = old
        for key, wrong in [('ks_preview_scope_bound', False), ('owner', 'other-owner'), ('id', 'other-video'), ('observed_at', self.session.reopen_at - 1), ('ks_preview_source', 'https://cdn.fixture.test/other')]:
            old = self.read[key];self.read[key] = wrong
            self.assertFalse(await self.gate(), key);self.read[key] = old
        self.links[0]['sha256'] = 'c' * 64
        self.assertFalse(await self.gate())
        self.links[0]['sha256'] = 'a' * 64;self.links[1]['complete'] = False
        self.assertFalse(await self.gate())
        self.links[1]['complete'] = True;self.session.expected['receipt_nonce'] = 'other-operation'
        self.assertFalse(await self.gate())

    async def test_same_ids_without_current_physical_preview_are_rejected(self):
        await self.change('fixture.video.remove()')
        self.assertFalse(await self.gate())

    async def test_hidden_wrong_props_wrong_form_duplicate_form_are_rejected(self):
        cases = [('fixture.host.style.display="none"', 'fixture.host.style.display=""'),
                 ('fixture.vf.memoizedProps.src="https://cdn.fixture.test/stale"', 'fixture.vf.memoizedProps.src=fixture.form.url'),
                 ('fixture.consumer.memoizedProps.url="https://cdn.fixture.test/stale"', 'fixture.consumer.memoizedProps.url=fixture.form.url'),
                 ('fixture.editor.memoizedProps.snapshotInfo={...fixture.form}', 'fixture.editor.memoizedProps.snapshotInfo=fixture.form'),
                 ('fixture.owner.memoizedState.next.next={memoizedState:{current:{...fixture.form}},next:null}', 'fixture.owner.memoizedState.next.next=null'),
                 ('fixture.progress.status=2', 'fixture.progress.status=3')]
        for change, undo in cases:
            await self.change(change);self.assertFalse(await self.gate(), change);await self.change(undo)
        self.assertTrue(await self.gate())

    async def test_cover_wrong_bytes_unobserved_view_and_hidden_editor_rejected(self):
        saved = list(self.observer.records);self.observer.records.clear()
        self.assertFalse(await self.gate())
        self.observer.records[:] = saved;self.observer.records[0]['body_sha256'] = 'c' * 64
        self.assertFalse(await self.gate())
        self.observer.records[:] = saved;self.observer.records[0]['body_sha256'] = hashlib.sha256(PNG).hexdigest()
        await self.change('fixture.wrap.style.opacity="0"')
        self.assertFalse(await self.gate())
        await self.change('fixture.wrap.style.opacity="1"');self.assertTrue(await self.gate())
        self.observer.records.append({**self.observer.records[0], 'body_sha256': 'd' * 64})
        self.assertFalse(await self.gate(), 'ambiguous server bytes for same cover key')

    async def test_dom_key_old_alternate_selects_only_committed_host_fiber(self):
        await self.change('const old={...fixture.vf,alternate:fixture.vf};fixture.vf.alternate=old;fixture.video.__reactFiber$fixture=old')
        self.assertTrue(await self.gate())
        await self.change('fixture.video.__reactFiber$fixture={...fixture.vf,alternate:null}')
        self.assertFalse(await self.gate())

    async def test_official_encrypted_preview_is_decoded_locally_and_wrong_time_rejected(self):
        encrypted = await self.page.main_frame.evaluate(r'''async ({url,uid,time})=>{
         const enc=new TextEncoder(),key=await crypto.subtle.importKey('raw',enc.encode(uid.padEnd(16,'0').slice(0,16)),{name:'AES-CBC'},false,['encrypt']);
         const bytes=await crypto.subtle.encrypt({name:'AES-CBC',iv:enc.encode(String(time).padEnd(16,'0').slice(0,16))},key,enc.encode(url));
         return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_');
        }''', {'url': URL, 'uid': self.account['uid'], 'time': self.read['ks_current_time']}, isolated_context=False)
        self.read['ks_preview_source'] = encrypted
        self.assertTrue(await self.gate())
        self.read['ks_current_time'] += 1
        self.assertFalse(await self.gate())

    async def test_view_observes_only_normal_fresh_get_main_frame_and_exact_bytes(self):
        self.observer.records.clear()
        async with self.page.expect_response('**/cover/view?*') as event:
            await self.page.main_frame.evaluate("()=>fetch('/rest/cp/works/v2/video/pc/upload/cover/view?coverKey=fixture-cover-key&_t=1').then(r=>r.blob())", isolated_context=False)
        response = await event.value
        await self.observer.observe(response, None, time.time())
        self.assertEqual(self.observer.records[0]['body_sha256'], hashlib.sha256(PNG).hexdigest())
        self.assertTrue(self.observer.records[0]['valid'])
        self.assertTrue(await self.gate())
        saved = dict(self.observer.records[0])
        for method, start, frame, url in [('POST', time.time(), self.page.main_frame, response.url),
                ('GET', self.session.reopen_at - 1, self.page.main_frame, response.url),
                ('GET', time.time(), object(), response.url),
                ('GET', time.time(), self.page.main_frame, response.url + '&coverKey=other')]:
            async def body():return PNG
            fake = SimpleNamespace(url=url, status=200, request=SimpleNamespace(method=method, timing={'startTime': start * 1000}, frame=frame), body=body)
            self.observer.records.clear();await self.observer.observe(fake, None, time.time() + .01)
            self.assertFalse(self.observer.records[0]['valid'], (method,start,frame,url))
        self.assertEqual(saved['body_sha256'], hashlib.sha256(PNG).hexdigest())


class KSSnapshotResponseScopeTests(unittest.TestCase):
    def test_top_current_snapshot_post_time_host_and_request_are_required(self):
        frame = object()
        page = SimpleNamespace(main_frame=frame)
        url = 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/snapshot/info?photoType=0'
        response = SimpleNamespace(url=url, status=200, request=SimpleNamespace(url=url, method='POST', frame=frame, timing={'startTime': 2000}))
        self.assertTrue(native_ks_snapshot_response(page, response, 3, 1))
        for key, value in [('url', url.replace('cp.kuaishou.com', 'evil.test')), ('url', url + '#fragment'), ('status', 400)]:
            original=getattr(response,key);setattr(response,key,value)
            self.assertFalse(native_ks_snapshot_response(page,response,3,1));setattr(response,key,original)
        for key, value in [('url',url+'x'),('method','GET'),('frame',object()),('timing',{'startTime':500})]:
            original=getattr(response.request,key);setattr(response.request,key,value)
            self.assertFalse(native_ks_snapshot_response(page,response,3,1));setattr(response.request,key,original)
