"""Native Cropper/Canvas/SDK fixtures; no actual platform or account operations."""
import asyncio
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright

from uploader.draft_evidence import FILE_OBSERVER_SCRIPT
from uploader.xhs_evidence import install_xhs_observer


LIBRARY = Path(__file__).resolve().parents[1] / 'research/acceptance-20261004/cover-origin-chain.sources/library-cropper.18277c8a.js'
FIXTURE = r'''<!doctype html><meta charset="utf-8"><style>
 #cover-modal{width:360px;height:360px}#upload-cover-containner{width:300px;height:220px}
 #upload-cover-containner img{max-width:100%}.cropper-hidden{display:none!important}
 </style><div id="creator-publish-dom"></div><div id="cover-modal">
 <div id="upload-cover-containner"><img class="cropper"></div>
 <div class="upload-wrapper"><input id="original" type="file" accept="image/*"></div>
 <button class="btn-confirm">确定</button><button class="cancelBtn">取消</button></div>
 <script>window.webpackChunkugc=[];</script><script src="/official-cropper.js"></script><script>
 const module={exports:{}};
 const requireFixture=id=>id===39841?{_:x=>x}:id===51391?{_:x=>typeof x}:{};
 webpackChunkugc[0][1][92060](module,module.exports,requireFixture);
 const Cropper=module.exports;
 const fixture={pending:[],calls:[],cover:{fileid:'',selectionSource:'default'},ready:false};
 const img=document.querySelector('img.cropper'),modal=document.querySelector('#cover-modal');
 window.fixture=fixture;window.nativeCanvasEncode=HTMLCanvasElement.prototype.toDataURL;
 const map=new Map();
 const manager={getUploader(name){return map.get(name)||null;},createUploader(name){
   if(map.has(name))return map.get(name);
   const uploader={upload:function(file,options,...rest){
     fixture.calls.push({file,options,rest,receiver:this});
     if(options?.syncThrow)throw options.syncThrow;
     let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});
     fixture.pending.push({file,options,promise,resolve,reject});return promise;
   }};map.set(name,uploader);return uploader;
 }};
 window._publisher={uploader:manager};
 window.publish={app:{},el:document.querySelector('#creator-publish-dom'),
   getDraftState(){return {cover:fixture.cover}},getPublishState(){return {publishType:0}}};
 window.fixtureFile=wrong=>{
   const canvas=document.createElement('canvas');canvas.width=40;canvas.height=30;
   const ctx=canvas.getContext('2d');ctx.fillStyle=wrong?'#05a6d1':'#d13a05';ctx.fillRect(0,0,40,30);
   ctx.fillStyle='#123456';ctx.fillRect(4,5,20,8);
   const value=canvas.toDataURL('image/png'),raw=atob(value.split(',')[1]);
   const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
   return new File([bytes],'original.png',{type:'image/png'});
 };
 window.fixtureOriginalInfo=async()=>{
   const file=fixtureFile(false),sha=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
   return {kind:'cover',name:file.name,size:file.size,sha256:Array.from(new Uint8Array(sha),x=>x.toString(16).padStart(2,'0')).join('')};
 };
 original.onchange=()=>{
   fixture.ready=false;if(img.cropper)img.cropper.destroy();
   img.src=URL.createObjectURL(original.files[0]);
   new Cropper(img,{aspectRatio:4/3,checkOrientation:false,viewMode:1,ready(){fixture.ready=true}});
   original.value='';
 };
 window.fixtureSelect=wrong=>{modal.style.display='block';const data=new DataTransfer();
   data.items.add(fixtureFile(wrong));original.files=data.files;
   original.dispatchEvent(new Event('change',{bubbles:true,composed:true}));};
 window.fixtureJPEG=()=>img.cropper.getCroppedCanvas({imageSmoothingQuality:'high'}).toDataURL('image/jpeg');
 window.fixtureUpload=(data,fake=false)=>{
   const raw=atob(data.split(',')[1]),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
   const blob=new Blob([bytes],{type:'image/jpeg'});
   const file=fake?{name:'cover.jpeg',size:bytes.length,type:'',arrayBuffer:()=>blob.arrayBuffer()}:
     new File([blob],'cover.jpeg');
   const uploader=manager.createUploader('image-uploader');
   const options={sentinel:{same:true}},returned=uploader.upload(file,options,'third');
   fixture.last={uploader,file,options,returned,data};
   returned.then(result=>{if(result.fileId&&result.previewUrl)fixture.cover={
     fileid:fixture.wrongResource?'wrong-applied':result.fileId,selectionSource:'upload'};},()=>{});
   return returned===fixture.pending.at(-1).promise;
 };
 document.querySelector('.btn-confirm').onclick=()=>{
   const data=fixtureJPEG();fixtureUpload(data,fixture.fakeFile);modal.style.display='none';
 };
 document.querySelector('.cancelBtn').onclick=()=>{modal.style.display='none'};
 window.fixtureResolve=(id='cover-resource')=>{const result={fileId:id,previewUrl:'fixture-preview',extra:{same:true}};
   fixture.lastResult=result;fixture.pending.at(-1).resolve(result);};
 window.fixtureReject=()=>{const error=new Error('fixture SDK rejection');fixture.lastError=error;fixture.pending.at(-1).reject(error)};
 </script>'''


class XHSCoverFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chrome', timeout=90000)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.observers, self.requests, self.session = [], [], None
        self.fulfilled_requests = 0

        async def serve(route):
            self.requests.append(route.request.url)
            if route.request.url.endswith('/official-cropper.js'):
                await route.fulfill(content_type='application/javascript', body=LIBRARY.read_text())
            else:
                await route.fulfill(content_type='text/html', body=FIXTURE)
            self.fulfilled_requests += 1

        await self.context.route('**/*', serve)

        async def selected(source, report):
            if self.session and source['frame'] is self.page.main_frame:
                self.session.files.append(report)

        await self.page.expose_binding('__sauDraftSelected', selected)
        await self.page.add_init_script(FILE_OBSERVER_SCRIPT)
        await self.reset()

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def js(self, script):
        return await self.page.evaluate(script, isolated_context=False)

    async def reset(self):
        for observer in self.observers:
            await observer.close()
        self.session = None
        self.page._sau_draft_evidence = None
        await self.page.goto('http://127.0.0.1:45994/xhs-cover-fixture')

    async def install(self, job='cover-job', nonce='cover-nonce'):
        asset = await self.js('fixtureOriginalInfo()')
        self.session = SimpleNamespace(page=self.page,platform='xiaohongshu',closed=False,files=[],
            expected={'job_id':job,'receipt_nonce':nonce,'video_sha256':'0'*64,'cover_sha256':{'cover':asset['sha256']}},
            assets={'cover':asset})
        self.page._sau_draft_evidence = self.session
        observer = await install_xhs_observer(self.session)
        self.observers.append(observer)
        return observer

    async def select(self, observer, wrong=False):
        await self.js('fixtureSelect(' + ('true' if wrong else 'false') + ');true')
        until = asyncio.get_running_loop().time() + 10
        while not await self.js('fixture.ready') and asyncio.get_running_loop().time() < until:
            await asyncio.sleep(.05)
        self.assertTrue(await self.js('fixture.ready'))
        await observer.drain()
        until = asyncio.get_running_loop().time() + 5
        while not self.session.files and asyncio.get_running_loop().time() < until:
            await asyncio.sleep(.02)
        self.assertTrue(self.session.files)

    async def settle(self, observer):
        await observer.drain()
        await asyncio.sleep(.05)
        await observer.drain()

    async def confirm(self):
        # This fixture tests the real DOM event/transform path, not Cropper CSS
        # hit testing (its production stylesheet is not part of this fixture).
        await self.js('document.querySelector("#cover-modal .btn-confirm").click();true')

    async def test_real_original_png_actual_crop_jpeg_sdk_success_and_applied_cover(self):
        observer = await self.install()
        await self.select(observer)
        await self.confirm()
        self.assertTrue(await self.js('fixture.last.returned===fixture.pending[0].promise'))
        semantics = await self.js('''() => {
          const call=fixture.calls[0],last=fixture.last;
          fixture.returnedResult=null;last.returned.then(value=>fixture.returnedResult=value);
          return {receiver:call.receiver===last.uploader,file:call.file===last.file,
                  options:call.options===last.options,extra:call.rest[0]==='third',
                  globalCanvasUnchanged:HTMLCanvasElement.prototype.toDataURL===nativeCanvasEncode};
        }''')
        self.assertTrue(all(semantics.values()))
        await self.js('fixtureResolve();true')
        await self.settle(observer)
        links = observer.cover_upload_links()
        self.assertEqual(links, [{'kind':'cover','sha256':self.session.assets['cover']['sha256'],
                                 'ids':['cover-resource'],'complete':True}])
        transformation = observer.cover_transforms()[0]
        self.assertNotEqual(transformation['origin_sha256'], transformation['derived_sha256'])
        derived_sha = await self.js('''async () => {
          const value=await crypto.subtle.digest('SHA-256',await fixture.last.file.arrayBuffer());
          return Array.from(new Uint8Array(value),x=>x.toString(16).padStart(2,'0')).join('');
        }''')
        self.assertEqual(transformation['derived_sha256'], derived_sha)
        self.assertTrue(await self.js('fixture.returnedResult===fixture.lastResult'))
        self.assertTrue(await self.js('''() => {
          const error=new Error('same synchronous SDK error');
          try{fixture.last.uploader.upload(fixture.last.file,{syncThrow:error});}
          catch(received){return received===error;}return false;
        }'''))
        await observer.close()
        self.assertTrue(await self.js('HTMLCanvasElement.prototype.toDataURL===nativeCanvasEncode'))
        self.assertNotIn('getCroppedCanvas', await self.js('Object.keys(document.querySelector("img.cropper").cropper)'))

    async def test_existing_video_observer_namespace_accepts_cover_without_page_reload(self):
        await self.js('''() => {
          window.fixtureOriginalFactory=_publisher.uploader.createUploader;
          // Simulate an already-installed video-only observer's public lifecycle.
          window.__sauXHSSDKObserver={configure:()=>true,flush:async()=>true,close:()=>true};
        }''')
        observer = await self.install('legacy-video-observer','legacy-cover-nonce')
        await self.select(observer)
        await self.confirm()
        self.assertTrue(await self.js('fixture.last.returned===fixture.pending[0].promise'))
        await self.js('fixtureResolve("legacy-cover-resource");true')
        await self.settle(observer)
        self.assertEqual(observer.cover_upload_links(), [{'kind':'cover',
            'sha256':self.session.assets['cover']['sha256'],'ids':['legacy-cover-resource'],'complete':True}])
        await observer.close()
        self.assertTrue(await self.js('_publisher.uploader.createUploader===fixtureOriginalFactory'))

    async def test_incomplete_preview_changed_cancelled_wrong_task_or_forged_chains_rejected(self):
        cases = ('preview','only_sdk','changed_image','cancel','different_task','wrong_resource',
                 'sdk_reject','fake_cropper','fake_file','missing_native_selection')
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                await self.reset()
                observer = await self.install('cover-' + str(index), 'nonce-' + str(index))
                await self.select(observer)
                if case == 'preview':
                    await self.js('fixtureUpload(fixtureJPEG());true')
                elif case == 'only_sdk':
                    await self.js('fixtureUpload(document.createElement("canvas").toDataURL("image/jpeg"));true')
                else:
                    if case == 'wrong_resource':
                        await self.js('fixture.wrongResource=true')
                    if case == 'fake_file':
                        await self.js('fixture.fakeFile=true')
                    if case == 'fake_cropper':
                        await self.js('''() => {
                          const img=document.querySelector('img.cropper'),real=img.cropper;
                          const output=real.getCroppedCanvas();
                          img.cropper={element:img,isImg:true,ready:true,originalUrl:real.originalUrl,
                            getCroppedCanvas(){return output;}};
                        }''')
                    if case == 'missing_native_selection':
                        self.session.files.clear()
                    await self.confirm()
                if case == 'changed_image':
                    await self.select(observer,wrong=True)
                elif case == 'cancel':
                    await self.js('document.querySelector("#cover-modal").style.display="block"')
                    await self.js('document.querySelector("#cover-modal .cancelBtn").click();true')
                elif case == 'different_task':
                    observer = await self.install('new-task','new-nonce')
                if case == 'sdk_reject':
                    await self.js('fixture.last.returned.catch(error=>fixture.callerError=error);true')
                    await self.js('fixtureReject();true')
                else:
                    await self.js('fixtureResolve();true')
                await self.settle(observer)
                self.assertEqual(observer.cover_upload_links(), [])
                self.assertEqual(observer.cover_transforms(), [])
                if case == 'sdk_reject':
                    self.assertTrue(await self.js('fixture.callerError===fixture.lastError'))
        # Blob/image/source-map request URLs may differ from the fixture page.
        # Every intercepted request is fulfilled locally; none is continued.
        self.assertEqual(self.fulfilled_requests, len(self.requests))


class CoverSelectionBoundaryTests(unittest.TestCase):
    def test_hash_callback_order_does_not_replace_the_latest_native_selection(self):
        from uploader.xhs_evidence import XHSSDKObserver
        now = time.time()
        current = {'name':'original.png','size':200,'type':'image/png','sha256':'a'*64,'selected_at':now}
        old = {**current, 'selected_at':now - 2}
        session = SimpleNamespace(page=SimpleNamespace(),expected={},assets={},files=[current, old])
        observer = XHSSDKObserver(session)
        report = {'origin_name':current['name'],'origin_size':current['size'],'origin_type':current['type'],
                  'origin_sha256':current['sha256'],'selected_at':now}
        self.assertTrue(observer._selected_cover(report))
        session.files.append({**current, 'sha256':'b'*64, 'selected_at':now + 1})
        self.assertFalse(observer._selected_cover(report))
        session.files = [{**current, 'selected_at':float('nan')}, old]
        self.assertFalse(observer._selected_cover(report))


if __name__ == '__main__':
    unittest.main()
