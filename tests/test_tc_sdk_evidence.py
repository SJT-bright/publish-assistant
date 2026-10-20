"""Passive TC SDK completion and server quick-part confirmation fixtures."""
import asyncio
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from patchright.async_api import async_playwright
from uploader.draft_evidence import DraftEvidenceSession
from uploader.tc_pipeline import capture_tc_upload, release_tc_upload
from uploader.tc_sdk_evidence import install_tc_sdk_observer
from uploader.tc_processing import install_tc_processing_observer
from uploader.tc_resource import tc_resource_identity
from tests.test_tc_processing import SOURCE, response, request_body


DOM = r'''<!doctype html><input id="video" type="file" accept="video/mp4"><div id="material"></div><script>
const fn=()=>{};
const store={asyncClip:true,postObjDesc:{media:[]},createPostStatus:{clipTicket:{clipKey:'fixture-clip-key',draftId:'fixture-draft-id'}}};
const upload={value:[],handleCropVideo:fn,clipVideoRequest:fn,getClipVideoResult:fn,updateVideo:fn,setFileList:fn,
 updateFile(event){fixture.updateCalls++;const clone={name:event.file.name,size:event.file.size,type:event.file.type,...(material.fileList[0]||event.file),...event.parcials};if(fixture.observe)fixture.observe(clone);material.fileList=[clone];this.value=[clone];}};
const material={fileList:[],$refs:{postUpload:upload},postStore:store,handleFileListChange:fn,handleSetFileList:fn,handleDeleteFile:fn,updateFile:fn,
 allUploaded:false,allCoverCroped:false,canSaveDraft:false,disablePostTip:''};
document.querySelector('#material').__vue__=material;
window.fixture={material,upload,store,updateCalls:0,successCalls:0};
fixture.oldBoundUpdate=upload.updateFile.bind(upload);
class SDK{
 constructor(file){this.file=file;this.uuid='fixture-sdk-task';this.uploadTaskId='fixture-sdk-upload';this.chunkSize=Math.ceil(file.size/17);this.chunkCount=17;this.partInfo=Array.from({length:17},(_,i)=>({PartNumber:i+1,ETag:'etag-'+(i+1)}));this.transFlag='fixture-flag';this.uploadSuccessResp={};
  this.promise=new Promise((resolve,reject)=>{this.resolve=resolve;this.reject=reject;});}
 startUpload(){}reuseUpload(){}initTaskId(){}getAvailableUploadedInfo(){}
 handleComplete(){return this.promise;}
 updateSuccessResult(){fixture.successCalls++;const data=this.uploadSuccessResp.data;upload.updateFile({file:this.file,parcials:{status:'done',response:data}});
  material.allUploaded=true;const file=material.fileList[0];store.postObjDesc.media=[{url:data.httpsUrl,width:320,height:240,videoPlayLen:6,fileSize:file.size,md5sum:file.uuid,urlCdnTaskId:store.createPostStatus.clipTicket.draftId}];}
}
document.querySelector('#video').addEventListener('change',event=>{
 const native=event.target.files[0];const file=Object.assign(native,{originFileObj:native,isVideo:true,isImage:false,isValid:true,status:'uploading',uuid:'fixture-file-uuid',extInfo:{width:320,height:240,duration:6}});
 const sdk=new SDK(file);fixture.sdk=sdk;fixture.native=native;fixture.sdkOriginalComplete=sdk.handleComplete;fixture.sdkOriginalSuccess=sdk.updateSuccessResult;
 if(fixture.beforePublish)fixture.beforePublish(sdk);
 if(window.useOldInvoker)fixture.oldBoundUpdate({file,parcials:{uploader:sdk}});else upload.updateFile({file,parcials:{uploader:sdk}});
});
</script>'''


# Faithful Vue2 observe/defineReactive fixture: official cached 894 module
# and the installed same implementation with renamed minified identifiers.
# These getters capture data in closures and assignments notify subscribers;
# they are not plain properties or arbitrary fake getters named "get".
VUE_REACTIVE = r"""
let depId=0;
class Dep {constructor(){this.id=depId++;this.subs=[];}depend(){}notify(){fixture.notifications++;}}
const tS=Dep;tS.target=null;const tD={};const m=Array.isArray;
const tX=v=>!!v&&v.__v_isRef===true,J=(a,b)=>!Object.is(a,b);
const tR=v=>observeFixture(v);
function tF(t,e,n,r,o,i){var a=new tS,s=Object.getOwnPropertyDescriptor(t,e);if(!s||!1!==s.configurable){var c=s&&s.get,u=s&&s.set;(!c||u)&&(n===tD||2==arguments.length)&&(n=t[e]);var l=!o&&tR(n,!1,i);return Object.defineProperty(t,e,{enumerable:!0,configurable:!0,get:function(){var e=c?c.call(t):n;return tS.target&&(a.depend(),l&&(l.dep.depend(),m(e)&&function t(e){for(var n=void 0,r=0,o=e.length;r<o;r++)(n=e[r])&&n.__ob__&&n.__ob__.dep.depend(),m(n)&&t(n)}(e))),tX(e)&&!o?e.value:e},set:function(e){var r=c?c.call(t):n;if(J(r,e)){if(u)u.call(t,e);else if(c)return;else if(!o&&tX(r)&&!tX(e)){r.value=e;return}else n=e;l=!o&&tR(e,!1,i),a.notify()}}}),a}}
const eE=tS,eQ=tX,y=m,Y=J,eR=tR;
function currentDefine(e,key){const s=Object.getOwnPropertyDescriptor(e,key);let n=s.value;const c=s.get,u=s.set,i=false,o=false,a=new Dep();let l=eR(n);
 Object.defineProperty(e,key,{enumerable:true,configurable:true,get:function(){var t=c?c.call(e):n;return eE.target&&(a.depend(),l&&(l.dep.depend(),y(t)&&function e(t){for(var n=void 0,r=0,i=t.length;r<i;r++)(n=t[r])&&n.__ob__&&n.__ob__.dep.depend(),y(n)&&e(n)}(t))),eQ(t)&&!i?t.value:t},set:function(t){var r=c?c.call(e):n;if(Y(r,t)){if(u)u.call(e,t);else if(c)return;else if(!i&&eQ(r)&&!eQ(t)){r.value=t;return}else n=t;l=!i&&eR(t,!1,o),a.notify()}}});}
function observeFixture(value){
 if(!value||!['[object Object]','[object Array]'].includes(Object.prototype.toString.call(value)))return undefined;
 if(Object.hasOwn(value,'__ob__'))return value.__ob__;
 const ob={value,dep:new Dep(),vmCount:0,shallow:false,mock:false};
 Object.defineProperty(value,'__ob__',{value:ob,enumerable:false,configurable:true,writable:true});
 if(Array.isArray(value)){value.forEach(observeFixture);return ob;}
 for(const key of Object.keys(value)){
  if(fixture.reactiveVariant==='official')tF(value,key,tD,undefined,false,false);else currentDefine(value,key);
 }
 return ob;
}
fixture.notifications=0;fixture.observe=observeFixture;
"""


class TCSDKNativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        self.browser = await self.p.chromium.launch(headless=True, executable_path=str(shell) if shell.is_file() else None)
        self.context = await self.browser.new_context()
        await self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=DOM))
        self.page = await self.context.new_page()
        await self.page.goto('https://channels.weixin.qq.com/platform/post/create')
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'fixture.mp4'
        self.data = b'current-complete-native-file-' * 103
        self.path.write_bytes(self.data)
        self.sha = hashlib.sha256(self.data).hexdigest()
        account = {'verified':True,'platform':'tencent','uid':'fixture-owner','identity':'fixture-account'}
        self.session = DraftEvidenceSession(self.page, 'tencent', {'job_id':'fixture-job','receipt_nonce':'fixture-nonce',
            'account': account,'video_sha256': self.sha})
        self.session.account = copy.deepcopy(account)
        self.session.assets = {'video':{'name':self.path.name,'size':len(self.data),'sha256':self.sha,'kind':'video'}}
        self.page._sau_draft_evidence = self.session
        await install_tc_processing_observer(self.session)
        self.observer = await install_tc_sdk_observer(self.session)
        self.binding = await capture_tc_upload(self.page, self.page.locator('#video'))
        self.page._sau_tc_native_import = self.binding
        await self.page.locator('#video').set_input_files(str(self.path))
        self.selected = time.time()
        self.session.files = [{**self.session.assets['video'],'selected_at':self.selected,'type':'video/mp4'}]

    async def asyncTearDown(self):
        await self.observer.close()
        await release_tc_upload(self.binding)
        await self.context.close();await self.browser.close();await self.p.stop()
        self.temp.cleanup()

    async def js(self, script, arg=None):
        return await self.page.evaluate(script, arg, isolated_context=False)

    async def complete(self, *, missing=False):
        allocation_at = time.time()
        base = {'tc_protocol':'dfs','metadata':(self.path.name,len(self.data)),'task_id':'fixture-sdk-task','upload_id':'fixture-sdk-upload'}
        chunk_size = (len(self.data)+16)//17
        self.session.exchanges = [{**base,'stage':'allocated','plan':[min(n*chunk_size,len(self.data)) for n in range(1,18)],'observed_at':allocation_at}]
        # Fresh ordinary replies are only first/last parts, exactly like QuickUpload.
        for number in (1,15,16,17):
            self.session.exchanges.append({**base,'stage':'allocated','part_number':number,'etag':'etag-'+str(number),'trans_flag':'fixture-flag','observed_at':allocation_at})
        await self.js('fixture.returned=fixture.sdk.handleComplete();fixture.samePromise=fixture.returned===fixture.sdk.promise')
        self.session.exchanges.append({**base,'stage':'confirmed','marker':'1','next_marker':None,'truncated':False,
            'parts':[(n,'etag-'+str(n)) for n in range(1,17) if not missing or n!=8], 'observed_at':time.time()})
        finish_at = time.time()
        self.session.exchanges.append({**base,'stage':'complete','parts':[(n,'etag-'+str(n)) for n in range(1,18)],
            'trans_flag':'fixture-flag','ids':[tc_resource_identity(SOURCE)],'observed_at':finish_at})
        await self.js('source=>{fixture.sdk.uploadSuccessResp={data:{httpsUrl:source,DownloadURL:source.replace("https://finder.video.qq.com","http://wxapp.tc.qq.com")}};fixture.sdk.updateSuccessResult();fixture.sdk.resolve();}', SOURCE)
        await self.js('fixture.sdk.promise')
        await self.session.tc_processing_observer.observe(response('allocate',request_body(len(self.data)),time.time()),
            {'errCode':0,'data':{'clipKey':'fixture-clip-key','draftId':'fixture-draft-id'}},time.time()+.01)
        await self.session.tc_processing_observer.drain()
        await self.observer.drain()

    async def test_current_native_fulfillment_and_confirmed_parts_form_full_upload_proof(self):
        await self.complete()
        self.assertEqual(self.session._tc_upload_links(), [])
        self.assertEqual(self.session.tc_processing_observer.native['sha256'], self.sha)
        self.assertEqual(len(self.observer.upload_links()), 1)
        self.assertEqual(self.session.upload_links()[0]['sha256'], self.sha)
        self.assertTrue(await self.js('fixture.samePromise'))
        self.assertEqual(await self.js('fixture.successCalls'), 1)
        self.assertEqual(await self.js('fixture.updateCalls'), 2)

    async def test_coverage_gap_rejects_despite_native_sdk_fulfillment(self):
        await self.complete(missing=True)
        self.assertEqual(self.observer.upload_links(), [])

    async def test_old_allocation_other_task_resource_or_etag_cannot_bind(self):
        await self.complete()
        original = copy.deepcopy(self.session.exchanges)
        for mode in ('old-allocation','task','upload','etag','resource','missing-allocation','contradictory-part'):
            with self.subTest(mode=mode):
                self.session.exchanges = copy.deepcopy(original)
                if mode == 'old-allocation': self.session.exchanges[0]['observed_at']=self.session.started_at-1
                if mode == 'task': self.session.exchanges[0]['task_id']='foreign-task'
                if mode == 'upload': self.session.exchanges[-1]['upload_id']='foreign-upload'
                if mode == 'etag': self.session.exchanges[-1]['parts'][7]=(8,'foreign-etag')
                if mode == 'resource': self.session.exchanges[-1]['ids']=['tc-resource:'+'0'*64]
                if mode == 'missing-allocation': self.session.exchanges=self.session.exchanges[1:]
                if mode == 'contradictory-part': self.session.exchanges.append({**self.session.exchanges[1],'etag':'foreign-etag'})
                self.assertEqual(self.observer.upload_links(), [])

    async def test_same_prefix_wrong_full_sha_and_foreign_sdk_or_current_response_reject(self):
        await self.complete()
        native = self.session.tc_processing_observer.native
        original_sha = native['sha256']
        native['sha256']=hashlib.sha256(self.data[:-1]+b'x').hexdigest()
        self.assertEqual(self.observer.upload_links(), [])
        native['sha256']=original_sha
        await self.js('fixture.material.fileList[0].response={DownloadURL:"foreign",httpsUrl:"foreign"}')
        await self.observer.drain()
        self.assertEqual(self.observer.upload_links(), [])

    async def test_success_state_without_observed_handle_fulfillment_is_not_proof(self):
        await self.complete()
        await self.binding.evaluate('b=>b.__tcSDK.fulfilled=0')
        await self.observer.drain()
        self.assertEqual(self.observer.upload_links(), [])

    async def test_passive_wrappers_restore_instance_methods_and_preserve_rejection(self):
        await self.js('fixture.returned=fixture.sdk.handleComplete();fixture.sdk.reject(Error("fixture rejected"));fixture.returned.catch(()=>{})')
        await self.observer.drain()
        self.assertEqual(self.observer.upload_links(), [])
        await self.observer.close()
        self.assertTrue(await self.js('!Object.hasOwn(fixture.sdk,"handleComplete")&&!Object.hasOwn(fixture.sdk,"updateSuccessResult")'))

    async def test_existing_vue_bound_listener_assignment_is_discovered_without_invoking_it(self):
        # Start a fresh empty input/owner using the old listener, which does
        # not call the newly installed updateFile property.
        await self.observer.close();await release_tc_upload(self.binding)
        await self.page.reload()
        await self.js('window.useOldInvoker=true')
        self.page._sau_draft_evidence = self.session
        self.observer = await install_tc_sdk_observer(self.session)
        self.binding = await capture_tc_upload(self.page, self.page.locator('#video'))
        self.page._sau_tc_native_import = self.binding
        await self.page.locator('#video').set_input_files(str(self.path))
        await asyncio.sleep(.03)
        await self.complete()
        self.assertEqual(len(self.observer.upload_links()), 1)
        self.assertEqual(await self.js('fixture.updateCalls'), 2)


    async def restart_with_reactive_sdk(self, variant='current', *, unknown=False, wrong_marker=False):
        await self.observer.close();await release_tc_upload(self.binding)
        await self.page.reload()
        await install_tc_processing_observer(self.session)
        await self.js(VUE_REACTIVE)
        await self.js('(variant)=>{fixture.reactiveVariant=variant;window.useOldInvoker=true;}', variant)
        if unknown:
            # A business getter has no canonical Vue data descriptor. Reading
            # it would create an observable effect; passive installation must
            # reject it before calling it.
            await self.js('()=>{fixture.unknownReads=0;fixture.beforePublish=sdk=>{Object.defineProperty(sdk,"file",{enumerable:true,configurable:true,get(){fixture.unknownReads++;return fixture.native;}});fixture.observe=null;};}')
        if wrong_marker:
            await self.js('()=>{fixture.beforePublish=sdk=>{fixture.observe(sdk);sdk.__ob__.value={};};}')
        self.page._sau_draft_evidence = self.session
        self.observer = await install_tc_sdk_observer(self.session)
        self.binding = await capture_tc_upload(self.page, self.page.locator('#video'))
        self.page._sau_tc_native_import = self.binding
        await self.page.locator('#video').set_input_files(str(self.path))
        await asyncio.sleep(.03)

    async def test_official_vue_data_accessors_and_real_current_getter_pair_complete(self):
        for variant in ('official', 'current'):
            with self.subTest(variant=variant):
                await self.restart_with_reactive_sdk(variant)
                shape=await self.js('()=>Object.fromEntries(["file","uuid","chunkSize","chunkCount","uploadTaskId","partInfo","uploadSuccessResp","transFlag"].map(k=>{const d=Object.getOwnPropertyDescriptor(fixture.sdk,k);return[k,{getter:typeof d.get,setter:typeof d.set,data:Object.hasOwn(d,"value")}]}))')
                self.assertTrue(all(v=={'getter':'function','setter':'function','data':False} for v in shape.values()))
                await self.complete()
                self.assertEqual(len(self.observer.upload_links()), 1)
                self.assertTrue(await self.js('fixture.samePromise'))
                self.assertGreater(await self.js('fixture.notifications'), 0)
                self.assertEqual(await self.js('fixture.successCalls'), 1)

    async def test_unknown_business_accessor_is_rejected_without_invocation(self):
        await self.restart_with_reactive_sdk(unknown=True)
        self.assertFalse(await self.binding.evaluate('b=>!!b.__tcSDK.sdk'))
        self.assertEqual(await self.js('fixture.unknownReads'), 0)
        self.assertEqual(self.observer.upload_links(), [])

    async def test_canonical_vue_getter_with_foreign_observer_owner_is_rejected(self):
        await self.restart_with_reactive_sdk(wrong_marker=True)
        self.assertFalse(await self.binding.evaluate('b=>!!b.__tcSDK.sdk'))
        self.assertEqual(self.observer.upload_links(), [])

    async def test_unknown_accessor_in_finish_response_is_rejected_without_invocation(self):
        await self.restart_with_reactive_sdk()
        await self.complete()
        self.assertEqual(len(self.observer.upload_links()),1)
        await self.js('()=>{fixture.unknownReads=0;Object.defineProperty(fixture.sdk.uploadSuccessResp.data,"httpsUrl",{enumerable:true,configurable:true,get(){fixture.unknownReads++;throw Error("business getter executed");}});}')
        await self.observer.drain()
        self.assertEqual(self.observer.upload_links(), [])
        self.assertEqual(await self.js('fixture.unknownReads'), 0)

    async def test_normal_same_id_reopen_can_retain_only_verified_sdk_link(self):
        from uploader.draft_evidence import parse_record
        from tests.test_tc_processing import resource
        await self.complete()
        candidate = parse_record('tencent', {'objectId':'fixture-saved','postReq':{'objectDesc':{
            'description':'fixture caption','media':[{'url':SOURCE,'urlCdnTaskId':'fixture-draft-id','thumbUrl':resource('cover')}]}}}, owner='fixture-owner')
        candidate['fresh']=True
        self.session.expected.update(platform_fields={'caption':'fixture caption'}, cover_sha256={})
        self.session.saves=[candidate];self.session.issues=[]
        self.session.action_at=time.time();self.session.reopen_at=None
        async def account():return copy.deepcopy(self.session.account)
        self.session.read_account=account
        self.assertTrue(await self.session.tc_processing_observer.freeze_for_reopen(candidate))
        self.session.reopen_at=time.time()
        self.session.normal_ui_binding={'id':'fixture-saved','owner':'fixture-owner','control':'post_draft_item_edit'}
        await self.js('fixture.material.fileList=[];fixture.upload.value=[]')
        await self.session.tc_processing_observer.drain();await self.observer.drain()
        self.assertEqual(len(self.observer.upload_links()),1)
        self.session.normal_ui_binding['id']='foreign-draft'
        self.assertEqual(self.observer.upload_links(),[])


if __name__ == '__main__':
    unittest.main()
