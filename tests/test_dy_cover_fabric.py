"""Local synthetic tests for the normal Douyin Fabric cover path.

No account, formal browser, CDP endpoint, profile, or platform API is used.
"""
import asyncio
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright

from uploader.dy_cover_evidence import install_dy_cover_observer


# Copied from the captured vertical_coverCanvas SDK object in
# research/acceptance-20261004/dy9-cloud-sdk-shape.json. Kept independent of
# the implementation's candidate-key list so fixture drift is detectable.
SDK_OWN_KEYS = (
    'uid', 'canvasW', 'canvasH', 'cropW', 'cropH', 'initScale', 'direction',
    'getNewId', 'canvas', '_eventAbility', 'on', 'off', 'offAll',
    'onCurrentLayerChanged', 'onTextEdit', 'onError', 'onLayerChanged',
    '_loadingAbility', '_canvasAbility', '_cropAbility', 'compile',
    'getPreviewDataURL', 'replaceBackground', 'getRawBackgroundInfo',
    'moveLayerOnTop', 'moveLayerOnBottom', 'recoverFromDraft',
    'getCheckDataUrl', 'deleteObjects', 'filterAbility', 'addComposerFilter',
    'removeComposerFilter', 'removeFilter', 'addFilter', 'setFilterProperty',
    'getFilterParams', 'recoverDraftFilter', 'imageAbility', 'addSticker',
    'templateAbility', 'addTemplate', 'removeTemplate', 'recoverDraftTemplate',
    'getTemplateCurrent', 'textAbility', 'addText', 'addTitle', 'updateTitle',
    'updateTextProperty', 'aiTextAbility',
)

# Captured module 64477 Fabric image-uploader instance and prototype shapes.
# The image class is intentionally independent from the video SDK class.
IMAGE_SDK_OWN_KEYS = (
    '_completeReject', '_completeResolve', '_uploadedFile', 'byteVideoUploader',
    'defaultVideoUploadConfig', 'disableRouterSelect', 'isSupportPDF', 'isTosDirect',
    'isUsingRegionalVideoHostConfig', 'processListeners', 'timestamp', 'uid',
    'uploadTime', 'uploaderOptions',
)
IMAGE_SDK_PROTO_KEYS = (
    '_handleCompleteReject', '_handleCompleteResolve', '_intiEvent', '_resetCompleteInfo',
    'cancel', 'offProcess', 'onProcess', 'resetVideoUploadConfig', 'updateUploadOption',
    'uploadFile', 'uploadImgArr', 'uploadSrc',
)


FIXTURE = r'''<!doctype html><html><body>
<div id="root">
  <div class="semi-upload" id="normal-upload">
    <div class="semi-upload-drag-area-main-text">Upload image</div>
    <input id="normal-input" class="semi-upload-hidden-input" type="file" accept="image/png">
  </div>
  <canvas id="vertical_coverCanvas" width="12" height="12"></canvas>
  <canvas id="horizontal_coverCanvas" width="12" height="12"></canvas>
</div>
<script>
window.fixture={clearOnChange:false,sourceOverride:null,processing:null,sourceBlob:null,
  sourceData:null,previewURL:null,capturedFile:null,compilePromise:null,compiledBlob:null,
  uploadPromises:{},lastUploadBlob:{},uriIndex:0,currentInput:document.querySelector('#normal-input')};
const f=window.fixture,container=document.querySelector('#root'),
  verticalCanvas=document.querySelector('#vertical_coverCanvas'),horizontalCanvas=document.querySelector('#horizontal_coverCanvas');
const SDK_KEYS=__SDK_OWN_KEYS__;
const IMAGE_KEYS=__IMAGE_SDK_OWN_KEYS__;
function makeFabricHandle(direction,lowerCanvasEl){
  const handle={};for(const key of SDK_KEYS)handle[key]=function(){};
  Object.assign(handle,{uid:'fixture-user',canvasW:12,canvasH:12,cropW:12,cropH:12,initScale:1,
    direction,canvas:{lowerCanvasEl}});
  handle.compile=function(...args){
    f.compileArgs=args;
    const promise=Promise.resolve({dataURL:f.sourceData,
      draftBase64:this._canvasAbility.rawBackgroundInfo.base64});
    f.compilePromise=promise;return promise;
  };
  handle._canvasAbility={core:handle,canvas:handle.canvas,rawBackgroundInfo:{base64:''}};
  return handle;
}
const handles={vertical:makeFabricHandle('vertical',verticalCanvas),
  horizontal:makeFabricHandle('horizontal',horizontalCanvas)};
f.handle=handles.vertical;f.canvas=verticalCanvas;f.canvasHandles=handles;f.currentHandle=handles.vertical;

class VideoSDKWrapper {
  constructor(options){this.uploaderOptions=options||{};this.byteVideoUploader={};}
  _handleCompleteReject(){} _handleCompleteResolve(){} _intiEvent(){} _resetCompleteInfo(){}
  cancel(){} offProcess(){} onProcess(){} pause(){} resume(){} resetVideoUploadConfig(){}
  setVideoUploadConfig(){} updateUploadOption(){}
  uploadFile(blob){return Promise.resolve({uri:'fixture-video-'+(++f.uriIndex)});}
  uploadImgArr(){} uploadSrc(){}
}
class FabricImageSDK {
  constructor(){
    this._completeReject=function(){};this._completeResolve=function(){};this._uploadedFile=null;
    this.byteVideoUploader={};this.defaultVideoUploadConfig={};this.disableRouterSelect=false;
    this.isSupportPDF=false;this.isTosDirect=false;this.isUsingRegionalVideoHostConfig=false;
    this.processListeners={};this.timestamp=0;this.uid='fixture-image-sdk';this.uploadTime=0;
    this.uploaderOptions={type:'image'};
  }
  _handleCompleteReject(){} _handleCompleteResolve(){} _intiEvent(){} _resetCompleteInfo(){}
  cancel(){} offProcess(){} onProcess(){} resetVideoUploadConfig(){} updateUploadOption(){}
  uploadFile(blob){
    const direction=f.imageDirections.get(this);
    f.lastUploadBlob[direction]=blob;
    const promise=Promise.resolve({uri:'fixture-cover-'+direction+'-'+(++f.uriIndex)});
    f.uploadPromises[direction]=promise;return promise;
  }
  uploadImgArr(){} uploadSrc(){}
}
f.videoUploader=new VideoSDKWrapper({type:'video'});
f.imageSDKs={horizontal:new FabricImageSDK(),vertical:new FabricImageSDK()};
f.imageDirections=new WeakMap([[f.imageSDKs.horizontal,'horizontal'],[f.imageSDKs.vertical,'vertical']]);
f.VideoSDKWrapper=VideoSDKWrapper;f.FabricImageSDK=FabricImageSDK;

const originalOnFileChange=function(files){
  const captured=files[0];f.capturedFile=captured;
  if(f.clearOnChange)f.currentInput.value='';
  const source=f.sourceOverride||captured;
  f.processing=(async()=>{
    const blob=await imageBlob(source),data=await blobDataURL(blob);
    f.sourceBlob=blob;f.sourceData=data;
    f.currentHandle._canvasAbility.rawBackgroundInfo.base64=data;
    f.previewURL=URL.createObjectURL(blob);
  })();
};
const uploadProps={dragable:true,limit:1,showUploadList:false,dragMainText:{fixture:true},
  onFileChange:originalOnFileChange};
const uploadComponent={props:uploadProps,inputRef:{current:f.currentInput}};
const inputFiber={tag:5,memoizedProps:{type:'file',accept:'image/png'},stateNode:f.currentInput,
  return:null,child:null,sibling:null};
const uploadFiber={tag:1,memoizedProps:uploadProps,pendingProps:null,stateNode:uploadComponent,
  return:null,child:inputFiber,sibling:null};
uploadComponent.props=uploadProps;inputFiber.return=uploadFiber;
Object.defineProperty(f.currentInput,'__reactFiber$fixture',{value:inputFiber,configurable:true});
f.uploadProps=uploadProps;f.originalOnFileChange=originalOnFileChange;f.uploadComponent=uploadComponent;
f.uploadFiber=uploadFiber;f.initialUploadProps=uploadProps;

const canvasFiber={tag:5,memoizedProps:{id:'vertical_coverCanvas'},stateNode:verticalCanvas,
  return:null,child:null,sibling:null};
const horizontalCanvasFiber={tag:5,memoizedProps:{id:'horizontal_coverCanvas'},stateNode:horizontalCanvas,
  return:null,child:null,sibling:null};
const sdkHooks=Array.from({length:66},(_,index)=>({memoizedState:{current:
  index===22?handles.vertical:index===23?handles.horizontal:
  index===64?f.imageSDKs.horizontal:index===65?f.imageSDKs.vertical:null},next:null}));
for(let index=0;index<sdkHooks.length-1;index++)sdkHooks[index].next=sdkHooks[index+1];
const sdkFiber={tag:0,memoizedProps:{},memoizedState:sdkHooks[0],return:null,child:canvasFiber,sibling:null};
canvasFiber.return=sdkFiber;canvasFiber.sibling=horizontalCanvasFiber;horizontalCanvasFiber.return=sdkFiber;
const providerValue={rootProps:{currentEditStep:'vertical'},UIStates:{},getEditorPerfTracker(){return {};}};
const providerFiber={tag:10,memoizedProps:{value:providerValue},return:null,child:uploadFiber,sibling:null};
uploadFiber.return=providerFiber;sdkFiber.return=providerFiber;uploadFiber.sibling=sdkFiber;
const rootState={containerInfo:container,current:null};
const committedRoot={tag:3,stateNode:rootState,return:null,child:providerFiber,sibling:null,alternate:null};
rootState.current=committedRoot;providerFiber.return=committedRoot;
Object.defineProperty(container,'__reactContainer$fixture',
  {value:{tag:3,stateNode:rootState},configurable:true});
f.providerValue=providerValue;f.providerFiber=providerFiber;f.sdkFiber=sdkFiber;
f.rootState=rootState;f.committedRoot=committedRoot;

function dataURL(blob){return new Promise((resolve,reject)=>{
  const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;
  reader.readAsDataURL(blob);
});}
async function blobDataURL(blob){return await dataURL(blob);}
async function imageBlob(file){
  const data=await dataURL(file);
  const image=await new Promise((resolve,reject)=>{const i=new Image();i.onload=()=>resolve(i);i.onerror=reject;i.src=data;});
  const width=image.naturalWidth,height=image.naturalHeight,horizontal=width>=height;
  const shrinkWidth=horizontal&&width>2160,shrinkHeight=height>2160;
  const targetWidth=shrinkWidth?2160:shrinkHeight?width/height*2160:width;
  const targetHeight=shrinkWidth?height/width*2160:shrinkHeight?2160:height;
  const c=document.createElement('canvas');c.width=targetWidth;c.height=targetHeight;
  c.getContext('2d').drawImage(image,0,0,targetWidth,targetHeight);
  return await new Promise(resolve=>c.toBlob(resolve,'image/jpeg'));
}
async function pngFile(name,width,height,color){
  const c=document.createElement('canvas');c.width=width;c.height=height;
  const ctx=c.getContext('2d');ctx.fillStyle=color;ctx.fillRect(0,0,width,height);
  const blob=await new Promise(resolve=>c.toBlob(resolve,'image/png'));
  return new File([blob],name,{type:'image/png'});
}
async function fileHash(file){const bytes=await file.arrayBuffer();const h=await crypto.subtle.digest('SHA-256',bytes);
  return Array.from(new Uint8Array(h),x=>x.toString(16).padStart(2,'0')).join('');}
f.makeFiles=async()=>({
  portrait:await pngFile('portrait.png',600,2400,'#d24b5a'),
  landscape:await pngFile('landscape.png',2400,600,'#2f6edb'),
  alternate:await pngFile('alternate.png',600,2400,'#2b9a68')
});
f.describe=async file=>({name:file.name,size:file.size,type:file.type,sha256:await fileHash(file)});
f.select=async(options={})=>{
  const role=options.role||'cover_portrait',direction=role==='cover_landscape'?'horizontal':'vertical';
  const input=f.currentInput,file=f.files[role==='cover_landscape'?'landscape':'portrait'],dt=new DataTransfer();
  f.activeRole=role;f.currentHandle=f.canvasHandles[direction];f.currentCanvas=f.currentHandle.canvas.lowerCanvasEl;
  f.providerValue.rootProps.currentEditStep=direction;
  f.clearOnChange=!!options.clear;f.sourceOverride=options.wrongSource?f.files.alternate:null;
  dt.items.add(file);input.files=dt.files;
  input.dispatchEvent(new Event('change',{bubbles:true}));
  await f.processing;
  return {selected:await f.describe(file),fileListLength:input.files.length,
    capturedSame:f.capturedFile===file,role};
};
f.remountInput=mode=>{
  const prior=f.currentInput,next=document.createElement('input');
  next.id='normal-input';next.className='semi-upload-hidden-input';next.type='file';next.accept='image/png';
  prior.replaceWith(next);
  const nextInputFiber={tag:5,memoizedProps:{type:'file',accept:'image/png'},stateNode:next,
    return:null,child:null,sibling:null};
  if(mode==='same'||mode==='same-stale'){
    f.uploadComponent.inputRef.current=next;nextInputFiber.return=f.uploadFiber;f.uploadFiber.child=nextInputFiber;
    if(mode==='same-stale'){
      const staleProps={dragable:false,limit:1,showUploadList:false};
      const staleComponent={props:staleProps,inputRef:{current:null}};
      const staleUploadFiber={tag:1,memoizedProps:staleProps,stateNode:staleComponent,return:f.providerFiber,
        child:null,sibling:null};
      const staleInputFiber={tag:5,memoizedProps:{type:'file',accept:'image/png'},stateNode:next,
        return:staleUploadFiber,child:null,sibling:null};
      staleUploadFiber.child=staleInputFiber;
      Object.defineProperty(next,'__reactFiber$fixture',{value:staleInputFiber,configurable:true});
    }else Object.defineProperty(next,'__reactFiber$fixture',{value:nextInputFiber,configurable:true});
    f.providerFiber.child=f.uploadFiber;f.uploadFiber.return=f.providerFiber;f.uploadFiber.sibling=f.sdkFiber;
  }else{
    const props={...f.uploadProps,onFileChange:f.originalOnFileChange};
    const component={props,inputRef:{current:next}};
    const classFiber={tag:1,memoizedProps:props,stateNode:component,return:f.providerFiber,
      child:nextInputFiber,sibling:f.sdkFiber};
    nextInputFiber.return=classFiber;
    Object.defineProperty(next,'__reactFiber$fixture',{value:nextInputFiber,configurable:true});
    f.providerFiber.child=classFiber;f.sdkFiber.return=f.providerFiber;
  }
  f.currentInput=next;
  attachFileChange(next);
  return true;
};
f.setDirection=direction=>{f.currentHandle.direction=direction;};
f.setAlternateOnlyRoot=enabled=>{
  if(enabled){
    const current={tag:3,stateNode:f.rootState,return:null,child:null,sibling:null,alternate:f.committedRoot};
    f.committedRoot.alternate=current;f.rootState.current=current;
  }else{
    f.rootState.current=f.committedRoot;f.committedRoot.child=f.providerFiber;
    f.providerFiber.return=f.committedRoot;
  }
};
f.quickReady=async role=>{
  const observer=window.__sauDYCoverObserver,nonce=observer.scope.observer_nonce,realNow=Date.now;
  let calls=0;Date.now=()=>realNow()+(++calls>=3?7000:0);
  try{return await observer.ensureReady(nonce,role,f.currentInput);}
  finally{Date.now=realNow;}
};
f.compileOnly=async()=>{
  const returnedCompile=f.currentHandle.compile('fixture-compile-arg');
  const compileSame=returnedCompile===f.compilePromise,compiled=await returnedCompile;
  f.compiledBlob=await (async()=>{const match=/^data:([^;]+);base64,(.*)$/.exec(compiled.dataURL);
      const binary=atob(match[2]),bytes=new Uint8Array(binary.length);
      for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
      return new Blob([bytes],{type:match[1]});})();
  return {compileSame,compiled:true};
};
f.uploadWith=async(direction,options={})=>{
  const uploadedBlob=options.wrongBlob?new Blob(['wrong compiled image'],{type:'image/jpeg'}):f.compiledBlob;
  const returnedUpload=f.imageSDKs[direction].uploadFile(uploadedBlob);
  const uploadSame=returnedUpload===f.uploadPromises[direction],blobSame=f.lastUploadBlob[direction]===f.compiledBlob;
  await returnedUpload;
  if(options.unmount){f.currentInput.remove();f.currentCanvas.remove();}
  return {uploadSame,blobSame,direction};
};
f.status=()=>{
  const observer=window.__sauDYCoverObserver;
  return observer.status(observer.scope.observer_nonce);
};
f.originalOnFileChange=originalOnFileChange;
f.sdkShapes=()=>({
  canvasOwnKeys:Object.keys(f.canvasHandles.vertical).sort(),expectedCanvasOwnKeys:SDK_KEYS.slice().sort(),
  imageOwnKeys:Object.keys(f.imageSDKs.vertical).sort(),expectedImageOwnKeys:IMAGE_KEYS.slice().sort(),
  imageProtoKeys:Object.getOwnPropertyNames(Object.getPrototypeOf(f.imageSDKs.vertical))
    .filter(key=>key!=='constructor').sort(),expectedImageProtoKeys:__IMAGE_SDK_PROTO_KEYS__
});
f.propsPreserved=()=>f.uploadComponent.props===f.initialUploadProps&&f.uploadProps===f.initialUploadProps;
f.fiberOwnership=()=>{
  const expando=f.currentInput[Object.getOwnPropertyNames(f.currentInput).find(k=>k.startsWith('__reactFiber$'))];
  const currentOwner=f.providerFiber.child===f.uploadFiber&&f.uploadFiber.stateNode===f.uploadComponent&&
    f.uploadComponent.inputRef.current===f.currentInput&&
    f.uploadFiber.child?.stateNode===f.currentInput;
  return {domFiberIsAlternate:!!expando&&expando.stateNode===f.currentInput&&
      expando!==f.uploadFiber.child,currentOwner};
};
function attachFileChange(input){input.addEventListener('change',()=>{
  const selected=input.files.item(0);
  f.uploadProps.onFileChange([selected]);
});}
attachFileChange(f.currentInput);
window.__sauDYUploadObserver={scope:{observer_nonce:'e'.repeat(48),binding_name:'__sauDYUploaded_'+'e'.repeat(48)},
  current:{uploader:f.videoUploader},async ensure_ready(){return true;}};
</script></body></html>'''.replace('__SDK_OWN_KEYS__', json.dumps(list(SDK_OWN_KEYS))) \
    .replace('__IMAGE_SDK_OWN_KEYS__', json.dumps(list(IMAGE_SDK_OWN_KEYS))) \
    .replace('__IMAGE_SDK_PROTO_KEYS__', json.dumps(sorted(IMAGE_SDK_PROTO_KEYS)))


class FakeTransport:
    def __init__(self, page):
        self.page = page
        self.tasks = set()

    async def evaluate(self, expression):
        return await self.page.main_frame.evaluate(expression, isolated_context=False)


class FakeDYObserver:
    def __init__(self, page):
        self.page = page
        self.observer_nonce = 'e' * 48
        self.binding_name = '__sauDYUploaded_' + self.observer_nonce
        self.transport = FakeTransport(page)
        self.tasks = set()

    async def ensure_ready(self, timeout=5):
        return True


class DYFabricCoverEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.playwright = await async_playwright().start()
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        self.browser = await self.playwright.chromium.launch(
            headless=True, executable_path=str(shell) if shell.is_file() else None, timeout=30000)
        self.context = await self.browser.new_context(ignore_https_errors=True)
        self.page = await self.context.new_page()
        async def cover_binding(source, report):
            observer = getattr(self, 'current_observer', None)
            return bool(observer and observer.accept(source, report))
        await self.page.expose_binding('__fixtureDYCover', cover_binding)
        await self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
        await self.page.goto('https://creator.douyin.com/creator-micro/content/post/video?fixture=1')
        await self.page.evaluate('fixture.makeFiles().then(files=>fixture.files=files)', isolated_context=False)
        self.observers = []

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def make_session(self):
        portrait, landscape = await self.page.evaluate(
            'Promise.all([fixture.describe(fixture.files.portrait),fixture.describe(fixture.files.landscape)])',
            isolated_context=False)
        covers = {'cover_portrait':portrait['sha256'],'cover_landscape':landscape['sha256']}
        assets = {'cover_portrait':{**portrait,'kind':'cover'},
                  'cover_landscape':{**landscape,'kind':'cover'}}
        session = SimpleNamespace(
            page=self.page, platform='douyin', closed=False, started_at=time.time(),
            expected={'job_id':'fabric-fixture-job','receipt_nonce':'fabric-fixture-receipt',
                      'cover_sha256':covers}, assets=assets, dy_observer=FakeDYObserver(self.page))
        await self.page.evaluate(
            'window["' + session.dy_observer.binding_name + '"]={cover:report=>window.__fixtureDYCover(report)}',
            isolated_context=False)
        self.page._sau_draft_evidence = session
        observer = await install_dy_cover_observer(session)
        self.current_observer = observer
        self.observers.append(observer)
        self.assertTrue(observer.available)
        return session, observer

    async def select(self, options=None):
        return await self.page.evaluate('fixture.select(' + json.dumps(options or {}) + ')',
                                        isolated_context=False)

    async def compile_only(self):
        return await self.page.evaluate('fixture.compileOnly()', isolated_context=False)

    async def upload_with(self, observer, direction, options=None):
        result = await self.page.evaluate('fixture.uploadWith(' + json.dumps(direction) + ',' +
                                          json.dumps(options or {}) + ')', isolated_context=False)
        await observer.drain(timeout=2)
        return result

    async def test_normal_fabric_chain_preserves_original_returns_and_survives_unmount(self):
        _session, observer = await self.make_session()
        selected = await self.select()
        self.assertTrue(selected['capturedSame'])
        self.assertTrue(await observer.ensure_ready('cover_portrait', '#normal-input'))
        compiled = await self.compile_only()
        self.assertTrue(compiled['compileSame'])
        shapes = await self.page.evaluate('fixture.sdkShapes()', isolated_context=False)
        self.assertEqual(shapes['canvasOwnKeys'], shapes['expectedCanvasOwnKeys'])
        self.assertEqual(shapes['imageOwnKeys'], shapes['expectedImageOwnKeys'])
        self.assertEqual(shapes['imageProtoKeys'], shapes['expectedImageProtoKeys'])
        wrong_role = await self.upload_with(observer, 'horizontal')
        self.assertTrue(wrong_role['uploadSame'] and wrong_role['blobSame'])
        self.assertEqual(observer.upload_links(), [])
        result = await self.upload_with(observer, 'vertical', {'unmount':True})
        self.assertEqual(result, {'uploadSame':True,'blobSame':True,'direction':'vertical'})
        links = observer.upload_links()
        self.assertEqual([link['slot'] for link in links], ['cover_portrait'])
        self.assertEqual(links[0]['sha256'], selected['selected']['sha256'])
        record = next(iter(observer.records.values()))
        self.assertEqual(record['source_blob_sha256'], record['crop_blob_sha256'])
        self.assertEqual(record['source_blob_type'], 'image/jpeg')

    async def test_filelist_can_be_cleared_after_exact_file_is_captured(self):
        _session, observer = await self.make_session()
        selected = await self.select({'clear':True})
        self.assertEqual(selected['fileListLength'], 0)
        self.assertTrue(selected['capturedSame'])
        self.assertTrue(await observer.ensure_ready('cover_portrait', '#normal-input'))
        self.assertTrue((await self.compile_only())['compileSame'])
        result = await self.upload_with(observer, 'vertical')
        self.assertTrue(result['uploadSame'] and result['blobSame'])
        self.assertEqual(len(observer.upload_links()), 1)

    async def test_only_same_normal_upload_component_can_rebind_remounted_input(self):
        _session, observer = await self.make_session()
        await self.select({'clear':True})
        await self.page.evaluate("fixture.remountInput('different')", isolated_context=False)
        self.assertFalse(await observer.ensure_ready('cover_portrait', '#normal-input'))
        self.assertEqual(observer.upload_links(), [])
        await self.page.evaluate("fixture.remountInput('same-stale')", isolated_context=False)
        ownership = await self.page.evaluate('fixture.fiberOwnership()', isolated_context=False)
        self.assertTrue(ownership['domFiberIsAlternate'] and ownership['currentOwner'])
        self.assertTrue(await observer.ensure_ready('cover_portrait', '#normal-input'))
        self.assertTrue((await self.compile_only())['compileSame'])
        result = await self.upload_with(observer, 'vertical')
        self.assertTrue(result['uploadSame'] and result['blobSame'])
        self.assertEqual([link['slot'] for link in observer.upload_links()], ['cover_portrait'])

        await self.page.evaluate("fixture.remountInput('same-stale')", isolated_context=False)
        ownership = await self.page.evaluate('fixture.fiberOwnership()', isolated_context=False)
        self.assertTrue(ownership['domFiberIsAlternate'] and ownership['currentOwner'])
        second = await self.select({'role':'cover_landscape','clear':True})
        self.assertTrue(second['capturedSame'])
        self.assertEqual(second['fileListLength'], 0)
        self.assertTrue(await self.page.evaluate('fixture.propsPreserved()', isolated_context=False))
        self.assertTrue(await observer.ensure_ready('cover_landscape', '#normal-input'))
        self.assertTrue((await self.compile_only())['compileSame'])
        second_upload = await self.upload_with(observer, 'horizontal')
        self.assertTrue(second_upload['uploadSame'] and second_upload['blobSame'])
        self.assertEqual({link['slot'] for link in observer.upload_links()},
                         {'cover_portrait','cover_landscape'})

    async def test_wrong_source_image_cannot_be_bound_to_selected_file(self):
        _session, observer = await self.make_session()
        await self.select({'wrongSource':True})
        self.assertFalse(await observer.ensure_ready('cover_portrait', '#normal-input'))
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.records, {})

    async def test_uploaded_blob_must_match_compile_dataurl_blob(self):
        _session, observer = await self.make_session()
        await self.select()
        self.assertTrue(await observer.ensure_ready('cover_portrait', '#normal-input'))
        self.assertTrue((await self.compile_only())['compileSame'])
        result = await self.upload_with(observer, 'vertical', {'wrongBlob':True})
        self.assertTrue(result['uploadSame'])
        self.assertFalse(result['blobSame'])
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.records, {})

    async def test_wrong_direction_and_noncurrent_alternate_tree_fail_closed(self):
        _session, observer = await self.make_session()
        await self.select()
        await self.page.evaluate("fixture.setDirection('horizontal')", isolated_context=False)
        self.assertFalse(await self.page.evaluate("fixture.quickReady('cover_portrait')", isolated_context=False))
        self.assertEqual((await self.page.evaluate('fixture.status()', isolated_context=False))['ready_count'], 0)
        await self.page.evaluate("fixture.setDirection('vertical');fixture.setAlternateOnlyRoot(true)",
                                 isolated_context=False)
        self.assertFalse(await self.page.evaluate("fixture.quickReady('cover_portrait')", isolated_context=False))
        self.assertEqual((await self.page.evaluate('fixture.status()', isolated_context=False))['ready_count'], 0)
        await self.page.evaluate('fixture.setAlternateOnlyRoot(false)', isolated_context=False)
        self.assertTrue(await observer.ensure_ready('cover_portrait', '#normal-input'))
        self.assertTrue((await self.compile_only())['compileSame'])
        await self.upload_with(observer, 'vertical')
        self.assertEqual([link['slot'] for link in observer.upload_links()], ['cover_portrait'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
