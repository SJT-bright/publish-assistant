"""Synthetic current-page evidence tests; no account, CDP, or platform API."""
import asyncio
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright

from uploader.dy_cover_evidence import install_dy_cover_observer


FIXTURE = r'''<!doctype html><html><body>
<div id="root">
  <div class="semi-upload" id="normal-upload">
    <div class="semi-upload-drag-area-main-text">Upload image</div>
    <input id="normal-input" class="semi-upload-hidden-input" type="file" accept="image/png">
  </div>
  <div class="semi-upload" id="ai-upload">
    <div class="semi-upload-drag-area-custom">AI reference</div>
    <input id="ai-input" class="semi-upload-hidden-input" type="file" accept="image/png">
  </div>
  <canvas id="uploadCrop" width="10" height="10"></canvas>
</div>
<script>
window.fixture={direction:'vertical',sourceOverride:null,processing:null,blob:null,uriIndex:0,
  syncThrow:false,rejectUpload:false,cropCalls:0,uploadCalls:0,skipFileChange:false,wrongFileChangeArg:false};
const canvas=document.querySelector('#uploadCrop');
const input=document.querySelector('#normal-input');
const uploadProps={dragable:true,limit:1,showUploadList:false,dragMainText:{fixture:true},onFileChange(files){
  const chosen=files[0];window.fixture.processing=(async()=>{
    const source=window.fixture.sourceOverride||chosen;
    const preview=await imageBlob(source),url=URL.createObjectURL(preview);
    cropProps.url=url;handle.direction=window.fixture.direction;
    const c=document.createElement('canvas');c.width=9;c.height=7;c.getContext('2d').fillRect(0,0,9,7);
    window.fixture.blob=await new Promise(resolve=>c.toBlob(resolve,'image/jpeg'));
  })();
}};
const uploadFiber={tag:0,memoizedProps:uploadProps,pendingProps:null,return:null};
const inputFiber={tag:5,memoizedProps:{type:'file',accept:'image/png'},stateNode:input,return:uploadFiber};
Object.defineProperty(input,'__reactFiber$fixture',{value:inputFiber,configurable:true});
const handle={direction:'vertical',cbCrop(){window.fixture.cropCalls++;const p=Promise.resolve(window.fixture.blob);window.fixture.cropPromise=p;return p;}};
const cropProps={cropId:'uploadCrop',url:''};
const cropFiber={tag:11,memoizedProps:cropProps,ref:{current:handle},return:null,child:null};
const hostFiber={tag:5,memoizedProps:{id:'uploadCrop'},stateNode:canvas,return:cropFiber,child:null};
cropFiber.child=hostFiber;
const fiberRoot={};const currentFiber={tag:3,stateNode:fiberRoot,child:cropFiber};fiberRoot.current=currentFiber;cropFiber.return=currentFiber;
Object.defineProperty(canvas,'__reactFiber$fixture',{value:hostFiber,configurable:true});
Object.defineProperty(document.querySelector('#root'),'__reactContainer$fixture',
  {value:{tag:3,stateNode:fiberRoot},configurable:true});
class SDKWrapper {
  constructor(options){this.uploaderOptions=options||{};this.byteVideoUploader={};}
  _handleCompleteReject(){} _handleCompleteResolve(){} _intiEvent(){} _resetCompleteInfo(){}
  cancel(){} offProcess(){} onProcess(){} pause(){} resume(){} resetVideoUploadConfig(){}
  setVideoUploadConfig(){} updateUploadOption(){}
  uploadFile(blob){
    window.fixture.uploadCalls++;
    if(window.fixture.syncThrow)throw window.fixture.syncThrow;
    if(window.fixture.rejectUpload){const promise=Promise.reject(window.fixture.rejectUpload);
      window.fixture.sdkPromise=promise;window.fixture.lastUploadBlob=blob;return promise;}
    const promise=Promise.resolve({uri:'tos://fixture-cover-'+(++window.fixture.uriIndex)});
    window.fixture.sdkPromise=promise;window.fixture.lastUploadBlob=blob;return promise;
  }
  uploadImgArr(){} uploadSrc(){}
}
window.fixture.SDKWrapper=SDKWrapper;window.fixture.handle=handle;window.fixture.cropFiber=cropFiber;
window.fixture.videoUploader=new SDKWrapper({type:'video'});
window.__sauDYUploadObserver={scope:{observer_nonce:'d'.repeat(48),binding_name:'__sauDYUploaded_'+'d'.repeat(48)},
  current:{uploader:window.fixture.videoUploader},async ensureReady(){return true;}};
async function pngFile(name,width,height,color){
  const c=document.createElement('canvas');c.width=width;c.height=height;
  const ctx=c.getContext('2d');ctx.fillStyle=color;ctx.fillRect(0,0,width,height);
  const blob=await new Promise(resolve=>c.toBlob(resolve,'image/png'));
  return new File([blob],name,{type:'image/png'});
}
async function fileHash(file){const bytes=await file.arrayBuffer();const h=await crypto.subtle.digest('SHA-256',bytes);
  return Array.from(new Uint8Array(h),x=>x.toString(16).padStart(2,'0')).join('');}
async function imageBlob(file){
  const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file)});
  const image=await new Promise((resolve,reject)=>{const i=new Image();i.onload=()=>resolve(i);i.onerror=reject;i.src=data});
  const scale=Math.min(1,2160/Math.max(image.naturalWidth,image.naturalHeight));
  const width=image.naturalWidth*scale,height=image.naturalHeight*scale,c=document.createElement('canvas');
  c.width=width;c.height=height;c.getContext('2d').drawImage(image,0,0,width,height);
  return await new Promise(resolve=>c.toBlob(resolve,'image/jpeg'));
}
window.fixture.makeFiles=async()=>({
  portrait:await pngFile('portrait.png',600,2400,'#e22'),
  landscape:await pngFile('landscape.png',2400,600,'#26e'),
  alternate:await pngFile('alternate.png',600,2400,'#2a4')
});
window.fixture.describe=async file=>({name:file.name,size:file.size,type:file.type,sha256:await fileHash(file)});
input.addEventListener('change',()=>{
  if(window.fixture.skipFileChange)return;
  const selected=input.files.item(0),arg=window.fixture.wrongFileChangeArg?window.fixture.files.alternate:selected;
  uploadFiber.memoizedProps.onFileChange([arg]);
});
window.fixture.select=async(role,wrong=false,reselect=false)=>{
  const f=window.fixture.files[role==='cover_portrait'?'portrait':'landscape'];
  const chosen=reselect?new File([f],f.name,{type:f.type}):f;
  const dt=new DataTransfer();dt.items.add(chosen);input.files=dt.files;
  window.fixture.sourceOverride=wrong?window.fixture.files.alternate:null;
  window.fixture.direction=role==='cover_portrait'?'vertical':'horizontal';
  input.dispatchEvent(new Event('change',{bubbles:true}));await window.fixture.processing;
  return await window.fixture.describe(chosen);
};
window.fixture.finish=async(options={})=>{
  const returnedCrop=handle.cbCrop(),cropSame=returnedCrop===window.fixture.cropPromise;
  const blob=await returnedCrop;
  const type=options.type||'image',sdk=new SDKWrapper({type});
  const passed=options.wrongBlob?new Blob(['other'],{type:'image/jpeg'}):blob;
  let returnedUpload;
  try{returnedUpload=sdk.uploadFile(passed)}catch(error){return {cropSame,thrown:error===window.fixture.syncThrow};}
  const uploadSame=returnedUpload===window.fixture.sdkPromise;
  try{await returnedUpload;
    if(options.unmountAfterFulfilled){cropFiber.ref.current=null;canvas.remove();input.remove();}
    return {cropSame,uploadSame,blobSame:window.fixture.lastUploadBlob===passed};}
  catch(_){return {cropSame,uploadSame,rejected:true};}
};
window.fixture.originals={url:URL.createObjectURL,upload:Object.getPrototypeOf(window.fixture.videoUploader).uploadFile,
  crop:handle.cbCrop};
</script></body></html>'''


class FakeTransport:
    def __init__(self, page):
        self.page = page
        self.tasks = set()

    async def evaluate(self, expression):
        return await self.page.main_frame.evaluate(expression, isolated_context=False)


class FakeDYObserver:
    def __init__(self, page):
        self.page = page
        self.observer_nonce = 'd' * 48
        self.binding_name = '__sauDYUploaded_' + self.observer_nonce
        self.transport = FakeTransport(page)

    async def ensure_ready(self, timeout=5):
        return True


class DYCoverEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.p = await async_playwright().start()
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        self.browser = await self.p.chromium.launch(headless=True,
            executable_path=str(shell) if shell.is_file() else None, timeout=30000)
        self.context = await self.browser.new_context(ignore_https_errors=True)
        self.page = await self.context.new_page()
        async def cover_binding(source, report):
            observer = getattr(self, 'current_observer', None)
            return bool(observer and observer.accept(source, report))
        await self.page.expose_binding('__fixtureDYCover', cover_binding)
        await self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
        await self.page.goto('https://creator.douyin.com/creator-micro/content/post/video?enter_from=home_draft')
        self.observers = []
        await self.page.evaluate('fixture.makeFiles().then(files=>fixture.files=files)', isolated_context=False)

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.context.close()
        await self.browser.close()
        await self.p.stop()

    async def make_session(self, roles=('cover_portrait', 'cover_landscape')):
        assets = {}
        hashes = {}
        for role in roles:
            value = await self.page.evaluate(
                'fixture.describe(fixture.files.' + ('portrait' if role == 'cover_portrait' else 'landscape') + ')',
                isolated_context=False)
            hashes[role] = value['sha256']
            assets[role] = {**value, 'kind': 'cover'}
        session = SimpleNamespace(page=self.page, platform='douyin', closed=False,
            started_at=time.time(), expected={'job_id':'fixture-job','receipt_nonce':'fixture-receipt',
                'cover_sha256':hashes}, assets=assets, dy_observer=FakeDYObserver(self.page))
        await self.page.evaluate('window["' + session.dy_observer.binding_name + '"]={cover:report=>window.__fixtureDYCover(report)}',
            isolated_context=False)
        self.page._sau_draft_evidence = session
        observer = await install_dy_cover_observer(session)
        self.current_observer = observer
        self.observers.append(observer)
        self.assertTrue(observer.available)
        return session, observer

    async def select(self, role, wrong=False, reselect=False):
        return await self.page.evaluate(
            'fixture.select(' + repr(role) + ',' + str(wrong).lower() + ',' + str(reselect).lower() + ')',
            isolated_context=False)

    async def finish(self, observer, options=None):
        outcome = await self.page.evaluate('fixture.finish(' + json.dumps(options or {}) + ')', isolated_context=False)
        await observer.drain(timeout=2)
        return outcome

    async def test_two_role_chains_and_original_returns(self):
        _session, observer = await self.make_session()
        await self.select('cover_portrait')
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        portrait = await self.finish(observer)
        self.assertEqual(portrait, {'cropSame':True,'uploadSame':True,'blobSame':True})
        await self.select('cover_landscape')
        self.assertTrue(await observer.ensure_ready('cover_landscape', self.page.locator('#normal-input')))
        landscape = await self.finish(observer)
        self.assertEqual(landscape, {'cropSame':True,'uploadSame':True,'blobSame':True})
        links = observer.upload_links()
        self.assertEqual({link['slot'] for link in links}, {'cover_portrait','cover_landscape'})
        self.assertEqual({link['sha256'] for link in links}, set(observer.session.expected['cover_sha256'].values()))
        self.assertEqual({link['ids'][0] for link in links}, {'tos://fixture-cover-1','tos://fixture-cover-2'})

    async def test_wrong_source_preview_sha_is_rejected(self):
        _session, observer = await self.make_session(('cover_portrait',))
        await self.select('cover_portrait', wrong=True)
        self.assertFalse(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        self.assertEqual(observer.upload_links(), [])

    async def test_normal_modal_unmount_after_fulfillment_retains_proof(self):
        _session, observer = await self.make_session(('cover_portrait',))
        await self.select('cover_portrait')
        self.assertTrue(await observer.ensure_ready('cover_portrait', '#normal-input'))
        result = await self.finish(observer, {'unmountAfterFulfilled': True})
        self.assertEqual(result, {'cropSame': True, 'uploadSame': True, 'blobSame': True})
        self.assertEqual([link['slot'] for link in observer.upload_links()], ['cover_portrait'])

    async def test_requires_exact_semi_upload_file_argument(self):
        _session, observer = await self.make_session(('cover_portrait',))
        await self.page.evaluate('fixture.skipFileChange=true', isolated_context=False)
        await self.select('cover_portrait')
        self.assertFalse(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        await self.page.evaluate('fixture.skipFileChange=false;fixture.wrongFileChangeArg=true', isolated_context=False)
        await self.select('cover_portrait', reselect=True)
        self.assertFalse(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        self.assertEqual(observer.upload_links(), [])

    async def test_ai_input_and_wrong_crop_blob_do_not_link(self):
        _session, observer = await self.make_session(('cover_portrait',))
        await self.select('cover_portrait')
        self.assertFalse(await observer.ensure_ready('cover_portrait', self.page.locator('#ai-input')))
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        outcome = await self.finish(observer, {'wrongBlob':True})
        self.assertTrue(outcome['cropSame'] and outcome['uploadSame'])
        self.assertEqual(observer.upload_links(), [])

    async def test_same_role_reselection_invalidates_only_that_slot(self):
        _session, observer = await self.make_session()
        await self.select('cover_portrait')
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        await self.finish(observer)
        await self.select('cover_landscape')
        self.assertTrue(await observer.ensure_ready('cover_landscape', self.page.locator('#normal-input')))
        await self.finish(observer)
        self.assertEqual({x['slot'] for x in observer.upload_links()}, {'cover_portrait','cover_landscape'})
        await self.select('cover_portrait', reselect=True)
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        self.assertEqual({x['slot'] for x in observer.upload_links()}, {'cover_landscape'})

    async def test_sdk_wrong_type_sync_throw_and_rejection_are_not_proof(self):
        _session, observer = await self.make_session(('cover_portrait',))
        await self.select('cover_portrait')
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        wrong_type = await self.finish(observer, {'type':'video'})
        self.assertTrue(wrong_type['cropSame'] and wrong_type['uploadSame'])
        self.assertEqual(observer.upload_links(), [])

        await self.select('cover_portrait', reselect=True)
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        await self.page.evaluate('fixture.syncThrow=new Error("fixture")', isolated_context=False)
        thrown = await self.finish(observer)
        self.assertEqual(thrown, {'cropSame':True,'thrown':True})

        await self.select('cover_portrait', reselect=True)
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        await self.page.evaluate('fixture.syncThrow=false;fixture.rejectUpload=new Error("reject")', isolated_context=False)
        rejected = await self.finish(observer)
        self.assertTrue(rejected['rejected'] and rejected['cropSame'] and rejected['uploadSame'])
        await observer.drain(timeout=1)
        self.assertEqual(observer.upload_links(), [])

    async def test_close_restores_only_observer_patches(self):
        _session, observer = await self.make_session(('cover_portrait',))
        await self.select('cover_portrait')
        self.assertTrue(await observer.ensure_ready('cover_portrait', self.page.locator('#normal-input')))
        await observer.close()
        restored = await self.page.evaluate('fixture.originals.url===URL.createObjectURL && '
            'fixture.originals.upload===Object.getPrototypeOf(fixture.videoUploader).uploadFile && '
            'fixture.originals.crop===fixture.handle.cbCrop', isolated_context=False)
        self.assertTrue(restored)


if __name__ == '__main__':
    unittest.main()
