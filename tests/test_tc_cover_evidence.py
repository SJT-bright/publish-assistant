"""Synthetic TC DOM/Vue/canvas + parsed upload fixtures, never real accounts."""
import asyncio
import copy
import hashlib
import json
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

from patchright.async_api import async_playwright
from uploader.tc_cover_evidence import (
    install_tc_cover_observer, TCCoverObserver, TC_COVER_OBSERVER_SCRIPT,
    COVER_TRACE_KEYS, COVER_DIAGNOSTIC_SLOTS,
)
from uploader.draft_evidence import _tc_upload_exchange, _uploaded_file_hash

FIXTURE = r'''<!doctype html><body><div id="preview"><div class="edit-cover-dialog-container"><div class="single-cover-uploader-wrap"><input type="file"></div><div id="crop"></div></div></div><script>
const root=document.querySelector('#preview'),parentEl=document.querySelector('.edit-cover-dialog-container'),singleEl=document.querySelector('.single-cover-uploader-wrap'),cropEl=document.querySelector('#crop'),input=document.querySelector('input');
const fixture=window.fixture={counts:{reader:0,crop:0,full:0,emit:0},pending:[],results:[],cropped:null,lateApplied:false};
const preview=fixture.preview={$el:root,$parent:null,video:{},$options:{methods:{handleUpdateCover(){}}}};
const crop=fixture.crop={$el:cropEl,$options:{methods:{getCropResult(){},getOriginalCanvas(){},initCroppie(){}}},$refs:{croppieRefHorizon:{croppie:{elements:{img:null}}}},isCroppieReady:false,useCustomCover:true,lastImageUrl:'',imageUrl:''};
const parent=fixture.parent={$el:parentEl,$parent:preview,$refs:{cropRef:crop},$options:{name:'EditCoverDialog',methods:{handleChooseCover(){}}},value:true,isSettingCover:false,isCroppieLoading:false,useCustomCover:false,previewMode:'landscape-share'};
const single=fixture.single={$el:singleEl,$parent:parent,$refs:{singleCoverUploader:input},$options:{methods:{getFileImageDataURL(){},handleCustomCoverPreviewUrlChange(){}}},imgSrc:'',customCoverPreviewUrl:''};
single.handleCustomCoverPreviewUrlChange=function(...args){fixture.watcherReceiver=this;fixture.watcherArgs=args;if(fixture.watcherThrow)throw fixture.watcherThrow;this.imgSrc=this.customCoverPreviewUrl;return fixture.watcherReturn;};
root.__vue__=preview;parentEl.__vue__=parent;singleEl.__vue__=single;cropEl.__vue__=crop;
single.getFileImageDataURL=function(file){fixture.counts.reader++;const promise=new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file)});fixture.readerPromise=promise;return promise;};
single.$emit=function(event,canvas){fixture.counts.emit++;if(event==='change'){parent.customCover=canvas;parent.customCoverPreviewUrl=canvas.toDataURL(fixture.useDisplayWatcher||fixture.derivedPreview?'image/jpeg':'image/png');parent.currentCoverImageUrl=parent.customCoverPreviewUrl;parent.useCustomCover=true;crop.imageUrl=parent.customCoverPreviewUrl;crop.lastImageUrl=crop.imageUrl;const image=new Image();image.onload=()=>{crop.$refs.croppieRefHorizon.croppie.elements.img=image;crop.isCroppieReady=true;parent.isCroppieLoading=false;};image.src=crop.imageUrl;if(fixture.useDisplayWatcher)Promise.resolve().then(()=>{single.customCoverPreviewUrl=parent.customCoverPreviewUrl;if(fixture.registeredWatch)fixture.registeredWatch.cb.call(single,'official-tick');else single.handleCustomCoverPreviewUrlChange('official-tick');});}return single;};
parent.$emit=function(event,payload,...rest){if(event==='updateCover'){const apply=()=>{preview.video.shareCoverUrl=payload.coverImgs[0].httpsUrl;};fixture.lateApplied?Promise.resolve().then(apply):apply();}if(event==='input')parent.value=payload;fixture.emitted={event,payload,rest};return parent;};
crop.getCropResult=function(...args){fixture.counts.crop++;if(fixture.syncThrow)throw fixture.syncThrow;const canvas=document.createElement('canvas');canvas.width=24;canvas.height=18;canvas.getContext('2d').drawImage(parent.customCover,0,0,24,18);fixture.cropped=canvas;fixture.cropArgs=args;fixture.cropReceiver=this;fixture.cropPromise=Promise.resolve(canvas);return fixture.cropPromise;};
crop.getOriginalCanvas=function(){fixture.counts.full++;const canvas=document.createElement('canvas');canvas.width=parent.customCover.width;canvas.height=parent.customCover.height;canvas.getContext('2d').drawImage(parent.customCover,0,0);return Promise.resolve(canvas);};
parent.handleChooseCover=function(){if(parent.isSettingCover||parent.isCroppieLoading)return Promise.resolve('early');parent.isSettingCover=true;return crop.getCropResult('same-argument').then(canvas=>crop.getOriginalCanvas().then(full=>new Promise(resolve=>{canvas.toBlob(function(blob){fixture.callbackReceiver=this;blob.name='finder_video_img.jpeg';fixture.blob=blob;fixture.pending.push({resolve,blob,canvas,full});},'image/jpeg',.8)}))).then(result=>{parent.$emit('updateCover',{originImg:{httpsUrl:'https://finder.video.qq.com/full'},coverImgs:[{httpsUrl:result.resource}]},result.full,true,parent.previewMode);parent.$emit('input',false);parent.isSettingCover=false;return result;});};
input.addEventListener('change',()=>{parent.isCroppieLoading=true;crop.isCroppieReady=false;const file=input.files[0];const returned=single.getFileImageDataURL(file);fixture.getterIdentity=returned===fixture.readerPromise;returned.then(url=>{single.imgSrc=url;const image=new Image();image.onload=()=>{const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext('2d').drawImage(image,0,0);single.$emit('change',canvas)};image.src=url;if(fixture.clearNativeInput)input.value='';});});
window.fixtureMakeFile=async(wrong=false)=>{const canvas=document.createElement('canvas');canvas.width=32;canvas.height=24;const ctx=canvas.getContext('2d');ctx.fillStyle=wrong?'blue':'red';ctx.fillRect(0,0,32,24);const blob=await new Promise(r=>canvas.toBlob(r,'image/png'));return new File([blob],'cover.png',{type:'image/png'});};
window.fixtureSelect=async(wrong=false)=>{const file=await fixtureMakeFile(wrong);const data=new DataTransfer();data.items.add(file);input.files=data.files;fixture.selectionAt=Date.now()/1000;input.dispatchEvent(new Event('change',{bubbles:true}));return {name:file.name,size:file.size,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),v=>v.toString(16).padStart(2,'0')).join(''),selected_at:fixture.selectionAt};};
window.fixtureStart=()=>{fixture.lastPromise=parent.handleChooseCover();return true;};
window.fixtureComplete=(resource='https://finder.video.qq.com/cropped')=>{const p=fixture.pending.at(-1),result={resource,full:p.full,unchanged:{yes:true}};fixture.results.push(result);p.resolve(result);return true;};
window.fixtureBlob=async()=>Array.from(new Uint8Array(await fixture.blob.arrayBuffer()));
</script>'''


class TCCoverFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.p = await async_playwright().start()
        shell=Path.home()/'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        self.browser = await self.p.chromium.launch(headless=True, executable_path=str(shell) if shell.is_file() else None, timeout=30000)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        await self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
        await self.page.goto('http://127.0.0.1:45999/tc-cover-fixture')
        self.observers = []
        self.asset = await self.js('fixtureMakeFile().then(async f=>({name:f.name,size:f.size,sha256:Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",await f.arrayBuffer())),v=>v.toString(16).padStart(2,"0")).join("")}))')

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.context.close()
        await self.browser.close()
        await self.p.stop()

    async def js(self, s):
        return await self.page.evaluate(s, isolated_context=False)

    async def wait(self, expression, frame=None):
        for _ in range(250):
            if await (frame or self.page).evaluate(expression,isolated_context=False):
                return
            await asyncio.sleep(.02)
        self.fail('fixture state did not settle: '+expression)

    async def install(self, nonce='nonce-a', covers=None):
        assets={slot:{**value,'kind':'cover'} for slot,value in (covers or {'cover_landscape':self.asset}).items()}
        session=SimpleNamespace(page=self.page,platform='tencent',closed=False,started_at=time.time(),
            expected={'job_id':'job-a','receipt_nonce':nonce,'cover_sha256':{slot:asset['sha256'] for slot,asset in assets.items()}},
            assets=assets,files=[],exchanges=[])
        self.page._sau_draft_evidence=session
        observer=await install_tc_cover_observer(session)
        self.observers.append(observer)
        return session,observer

    async def select(self, session, wrong=False):
        selected=await self.js('fixtureSelect('+str(wrong).lower()+')')
        session.files.append(selected)
        await self.wait('fixture.crop.isCroppieReady===true')
        return selected

    async def start(self):
        await self.js('fixtureStart()')
        await self.wait('!!fixture.blob')
        return bytes(await self.js('fixtureBlob()'))

    async def finish(self,observer, resource='https://finder.video.qq.com/cropped'):
        await self.js('fixtureComplete('+json.dumps(resource)+')')
        await self.js('fixture.lastPromise.then(x=>x===fixture.results.at(-1))')
        await observer.drain()
        await asyncio.sleep(.02)
        await observer.drain()

    def assert_trace_schema(self, trace):
        self.assertEqual(set(trace), {'main', 'owner'})
        self.assertIsInstance(trace['owner'], list)
        for realm in [trace['main'], *trace['owner']]:
            self.assertEqual(set(realm), set(COVER_DIAGNOSTIC_SLOTS))
            for values in realm.values():
                self.assertEqual(set(values), set(COVER_TRACE_KEYS))
                self.assertTrue(all(type(value) is int and 0 <= value <= 1000000000 for value in values.values()))
    def sns(self,session,data, resource='http://wxapp.tc.qq.com/cropped',task='upload-current',complete=True):
        boundary='fixture-boundary'
        body=(('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="finder_video_img.jpeg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()+data+('\r\n--'+boundary+'--\r\n').encode())
        request=SimpleNamespace(method='POST',headers={'content-type':'multipart/form-data; boundary='+boundary},post_data_buffer=body)
        response=SimpleNamespace(url='https://finder.video.qq.com/snsuploadbig',request=request)
        fields={'filekey':'finder_video_img.jpeg','totalsize':str(len(data)),'fileuuid':task,'rangestart':'0','rangeend':str(len(data)-1)}
        exchange=_tc_upload_exchange(response,{'uploadfinish':int(complete),'fileurl':resource},time.time(),fields)
        self.assertIsNotNone(exchange)
        exchange['body_sha256']=_uploaded_file_hash(request)
        session.exchanges.append(exchange)
        return exchange

    async def test_png_to_actual_jpeg_sns_terminal_and_applied_resource(self):
        session,observer=await self.install()
        self.assertTrue(observer.available)
        await self.select(session)
        data=await self.start()
        self.assertNotEqual(hashlib.sha256(data).hexdigest(),self.asset['sha256'])
        self.sns(session,data)
        await self.finish(observer)
        links=observer.upload_links()
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['sha256'],self.asset['sha256'])
        self.assertIn('https://finder.video.qq.com/cropped',links[0]['ids'])
        self.assertTrue(await self.js('fixture.getterIdentity&&fixture.cropReceiver===fixture.crop&&fixture.cropArgs[0]==="same-argument"'))
        self.assertTrue(await self.js('fixture.lastPromise.then(v=>v===fixture.results[0])'))

    async def test_applied_getter_can_update_in_original_vue_microtask(self):
        session,observer=await self.install()
        await self.js('fixture.lateApplied=true')
        await self.select(session);data=await self.start();self.sns(session,data)
        await self.finish(observer)
        self.assertEqual(len(observer.upload_links()),1)

    async def test_official_display_watcher_attests_preview_without_changing_native_origin(self):
        await self.js('fixture.useDisplayWatcher=true;fixture.clearNativeInput=true;fixture.watcherReturn=Promise.resolve("unchanged")')
        session,observer=await self.install();await self.select(session)
        await self.wait('fixture.single.imgSrc===fixture.parent.customCoverPreviewUrl')
        self.assertEqual(await self.js('fixture.single.$refs.singleCoverUploader.files.length'),0)
        self.assertTrue(await self.js('(()=>{const sel=__sauTCCoverObserver.parents.get(fixture.parent);return sel.dataURL!==sel.preview&&sel.displaySource===sel.preview&&fixture.watcherReceiver===fixture.single&&fixture.watcherArgs[0]==="official-tick"})()'))
        self.assertTrue(await self.js('fixture.single.handleCustomCoverPreviewUrlChange("repeat")===fixture.watcherReturn'))
        data=await self.start();self.sns(session,data);await self.finish(observer)
        self.assertEqual(len(observer.upload_links()),1)
        self.assertEqual(observer.upload_links()[0]['sha256'],self.asset['sha256'])
        counts=(await observer.diagnostic_trace())['main']['cover_landscape']
        self.assertEqual(counts['display_source_attested'],2)
        self.assertEqual(counts['live_dataurl_match_false'],0)

    async def test_vue27_registered_bound_callback_observed_and_restored(self):
        await self.js('''fixture.useDisplayWatcher=true;fixture.clearNativeInput=true;
          fixture.savedWatchCallback=fixture.single.handleCustomCoverPreviewUrlChange;
          fixture.registeredWatch={vm:fixture.single,active:true,user:true,cb:fixture.savedWatchCallback};
          fixture.single._scope={effects:[fixture.registeredWatch]};''')
        session,observer=await self.install();await self.select(session)
        await self.wait('fixture.single.imgSrc===fixture.parent.customCoverPreviewUrl')
        self.assertTrue(await self.js('fixture.registeredWatch.cb!==fixture.savedWatchCallback'))
        data=await self.start();self.sns(session,data);await self.finish(observer)
        self.assertEqual(len(observer.upload_links()),1)
        self.assertEqual(observer.upload_links()[0]['sha256'],self.asset['sha256'])
        await observer.close()
        self.assertTrue(await self.js('fixture.registeredWatch.cb===fixture.savedWatchCallback'))

    async def test_multiple_registered_watch_callbacks_never_attest_preview(self):
        await self.js('''fixture.useDisplayWatcher=true;fixture.clearNativeInput=true;
          fixture.savedWatchCallback=fixture.single.handleCustomCoverPreviewUrlChange;
          fixture.registeredWatch={vm:fixture.single,active:true,user:true,cb:fixture.savedWatchCallback};
          fixture.single._scope={effects:[fixture.registeredWatch,{...fixture.registeredWatch}]};''')
        session,observer=await self.install();await self.select(session)
        await self.wait('fixture.single.imgSrc===fixture.parent.customCoverPreviewUrl')
        self.assertTrue(await self.js('fixture.registeredWatch.cb===fixture.savedWatchCallback'))
        data=await self.start();self.sns(session,data);await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])

    async def test_registered_watcher_becoming_ambiguous_or_inactive_preserves_behavior_without_proof(self):
        for mode in ('duplicate','inactive','nonuser'):
            with self.subTest(mode=mode):
                await self.page.reload()
                await self.js('''fixture.useDisplayWatcher=true;fixture.clearNativeInput=true;
                  fixture.savedWatchCallback=fixture.single.handleCustomCoverPreviewUrlChange;
                  fixture.registeredWatch={vm:fixture.single,active:true,user:true,cb:fixture.savedWatchCallback};
                  fixture.single._scope={effects:[fixture.registeredWatch]};''')
                session,observer=await self.install()
                self.assertTrue(await self.js('fixture.registeredWatch.cb!==fixture.savedWatchCallback'))
                if mode=='duplicate':
                    await self.js('fixture.single._scope.effects.push({...fixture.registeredWatch,cb:fixture.savedWatchCallback})')
                elif mode=='inactive':
                    await self.js('fixture.registeredWatch.active=false')
                else:
                    await self.js('fixture.registeredWatch.user=false')
                await self.select(session)
                await self.wait('fixture.single.imgSrc===fixture.parent.customCoverPreviewUrl')
                data=await self.start();self.sns(session,data);await self.finish(observer)
                self.assertEqual(observer.upload_links(),[])

    async def test_unattested_preview_and_wrong_watcher_owner_refs_nonce_reject(self):
        for mode in ('manual','receiver','input','parent','nonce','unknown_setter'):
            with self.subTest(mode=mode):
                await self.js('fixture.derivedPreview=true;fixture.clearNativeInput=true')
                session,observer=await self.install(nonce='nonce-'+mode);await self.select(session)
                await self.js('fixture.single.customCoverPreviewUrl=fixture.parent.customCoverPreviewUrl')
                if mode=='manual':await self.js('fixture.single.imgSrc=fixture.single.customCoverPreviewUrl')
                elif mode=='receiver':await self.js('fixture.single.handleCustomCoverPreviewUrlChange.call({...fixture.single});fixture.single.imgSrc=fixture.single.customCoverPreviewUrl')
                elif mode=='input':await self.js('fixture.single.$refs.singleCoverUploader=document.createElement("input");fixture.single.handleCustomCoverPreviewUrlChange()')
                elif mode=='parent':await self.js('fixture.parent.customCover=document.createElement("canvas");fixture.single.handleCustomCoverPreviewUrlChange()')
                elif mode=='nonce':await self.js('window.__sauTCCoverObserver.scope={...window.__sauTCCoverObserver.scope,observer_nonce:"foreign"};fixture.single.handleCustomCoverPreviewUrlChange()')
                else:await self.js('fixture.single.handleCustomCoverPreviewUrlChange=function(){this.imgSrc=this.customCoverPreviewUrl};fixture.single.handleCustomCoverPreviewUrlChange()')
                await self.wait('fixture.crop.isCroppieReady')
                await self.js('fixture.parent.isSettingCover=true;fixture.crop.getCropResult()')
                await observer.drain()
                self.assertEqual(observer.records,{})
                self.assertEqual((await observer.diagnostic_trace())['main']['cover_landscape']['display_source_attested'],0)
                await observer.close()
                # Return the fixture to a fresh, normally selectable state.
                await self.page.goto('http://127.0.0.1:45999/tc-cover-fixture')

    async def test_display_watcher_original_throw_is_preserved_without_attestation(self):
        session,observer=await self.install();await self.select(session)
        self.assertTrue(await self.js('''(()=>{const error=new Error('original');fixture.watcherThrow=error;
         fixture.single.customCoverPreviewUrl=fixture.parent.customCoverPreviewUrl;
         try{fixture.single.handleCustomCoverPreviewUrlChange('same')}catch(e){return e===error}return false})()'''))
        self.assertEqual((await observer.diagnostic_trace())['main']['cover_landscape']['display_source_attested'],0)

    async def test_only_native_choice_without_before_selection_observation_rejects(self):
        await self.js('fixtureSelect()')
        await self.wait('fixture.crop.isCroppieReady')
        session,observer=await self.install();data=await self.start();self.sns(session,data)
        await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])

    async def test_incomplete_upload_wrong_body_and_wrong_applied_id_reject(self):
        session,observer=await self.install();await self.select(session);data=await self.start()
        exchange=self.sns(session,data,complete=False)
        await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])
        exchange['stage']='complete';exchange['ids']=['http://wxapp.tc.qq.com/cropped'];exchange['body_sha256']='0'*64
        self.assertEqual(observer.upload_links(),[])
        exchange['body_sha256']=hashlib.sha256(data).hexdigest();exchange['ids']=['http://wxapp.tc.qq.com/full']
        self.assertEqual(observer.upload_links(),[])

    async def test_cancel_before_upload_completes_is_not_applied(self):
        session,observer=await self.install();await self.select(session);data=await self.start();self.sns(session,data)
        await self.js('fixture.parent.$emit("input",false)')
        await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])

    async def test_changed_native_original_same_filename_is_rejected(self):
        session,observer=await self.install();await self.select(session,wrong=True);data=await self.start();self.sns(session,data)
        await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])

    async def test_bound_old_croppie_source_is_rejected(self):
        session,observer=await self.install();await self.select(session)
        await self.js('fixture.crop.$refs.croppieRefHorizon.croppie.elements.img.src="data:image/png;base64,AA=="')
        data=await self.start();self.sns(session,data);await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])

    async def test_preview_crop_and_fullcanvas_do_not_create_transform(self):
        session,observer=await self.install();await self.select(session)
        # Preview call does not have the real handler's isSettingCover=true.
        await self.js('fixture.crop.getCropResult().then(c=>new Promise(r=>c.toBlob(b=>{fixture.previewBlob=b;r()},"image/jpeg")))')
        await observer.drain()
        self.assertEqual(observer.records,{})
        self.assertEqual(observer.upload_links(),[])

    async def test_new_task_nonce_rejects_old_late_encoding(self):
        session,old=await self.install();await self.select(session);data=await self.start()
        current,new=await self.install('nonce-b');self.sns(current,data)
        await self.finish(new)
        self.assertEqual(old.upload_links(),[]);self.assertEqual(new.upload_links(),[])

    async def test_original_synchronous_throw_and_return_promise_are_preserved(self):
        session,observer=await self.install();await self.select(session)
        self.assertTrue(await self.js('(()=>{const error=new Error("same");fixture.syncThrow=error;try{fixture.crop.getCropResult("arg")}catch(e){return e===error}return false})()'))
        await self.js('fixture.syncThrow=null;fixture.parent.isSettingCover=true')
        self.assertTrue(await self.js('fixture.crop.getCropResult("arg")===fixture.cropPromise'))
        await observer.drain();self.assertEqual(observer.upload_links(),[])

    async def test_ambiguous_native_events_and_conflicting_task_resource_reject(self):
        session,observer=await self.install();selected=await self.select(session);data=await self.start()
        session.files.append(dict(selected));self.sns(session,data);await self.finish(observer)
        self.assertEqual(observer.upload_links(),[])
        session.files.pop()
        self.assertEqual(len(observer.upload_links()),1)
        session.exchanges.append({**session.exchanges[0],'task_id':'different-upload-same-body-and-resource'})
        self.assertEqual(observer.upload_links(),[])
        session.exchanges.pop()
        self.sns(session,data,resource='http://wxapp.tc.qq.com/conflict')
        self.assertEqual(observer.upload_links(),[])

    async def test_single_part_dfs_complete_proves_actual_blob_only(self):
        session,observer=await self.install();await self.select(session);data=await self.start();at=time.time();meta=('finder_video_img.jpeg',len(data))
        session.exchanges.extend([
            {'tc_protocol':'dfs','stage':'allocated','metadata':meta,'task_id':'task','upload_id':'upload','plan':[len(data)],'observed_at':at},
            {'tc_protocol':'dfs','stage':'allocated','metadata':meta,'task_id':'task','upload_id':'upload','part_number':1,'etag':'etag','trans_flag':'flag','body_sha256':hashlib.sha256(data).hexdigest(),'observed_at':at+.00001},
            {'tc_protocol':'dfs','stage':'complete','metadata':meta,'task_id':'task','upload_id':'upload','parts':[(1,'etag')],'trans_flag':'flag','ids':['https://finder.video.qq.com/cropped'],'observed_at':at+.00002}])
        await self.finish(observer)
        self.assertEqual(len(observer.upload_links()),1)
        session.exchanges[0]['plan']=[len(data)//2,len(data)]
        self.assertEqual(observer.upload_links(),[])

    async def foreign_case(self, install_before_adoption=False):
        # Model Wujie's foreign realm objects and retained ownerDocument getter.
        await self.page.set_content('<iframe></iframe><div id="host"></div>')
        frame=self.page.frames[1]
        await frame.set_content(FIXTURE)
        if install_before_adoption:
            session,observer=await self.install()
        await self.page.evaluate("""() => {
          const foreign=document.querySelector('iframe').contentDocument;
          const nodes=[foreign.querySelector('#preview'),...foreign.querySelector('#preview').querySelectorAll('*')];
          const shadow=document.querySelector('#host').attachShadow({mode:'open'});
          shadow.append(nodes[0]);
          for(const node of nodes)Object.defineProperty(node,'ownerDocument',{configurable:true,get:()=>foreign});
        }""",isolated_context=False)
        if not install_before_adoption:
            session,observer=await self.install()
        await observer.drain()
        selected=await frame.evaluate('fixtureSelect()',isolated_context=False)
        session.files.append(selected)
        await self.wait('fixture.crop.isCroppieReady',frame)
        await frame.evaluate('fixtureStart()',isolated_context=False)
        await self.wait('!!fixture.blob',frame)
        data=bytes(await frame.evaluate('fixtureBlob()',isolated_context=False))
        self.sns(session,data)
        await frame.evaluate('fixtureComplete()',isolated_context=False)
        await frame.evaluate('fixture.lastPromise',isolated_context=False)
        await observer.drain()
        self.assertEqual(len(observer.upload_links()),1)
        self.assertTrue(await frame.evaluate('fixture.getterIdentity',isolated_context=False))

    async def test_foreign_iframe_vue_nodes_adopted_into_outer_open_shadow(self):
        await self.foreign_case()

    async def test_foreign_wrappers_installed_before_physical_adoption_route_selection(self):
        await self.foreign_case(True)

    async def test_wrong_reselection_revokes_previous_applied_slot(self):
        session,observer=await self.install();await self.select(session);data=await self.start();self.sns(session,data)
        await self.finish(observer);self.assertEqual(len(observer.upload_links()),1)
        await self.js('fixture.parent.value=true;fixtureSelect(true)')
        await observer.drain()
        self.assertEqual(observer.upload_links(),[])

    async def test_vertical_actual_preprocess_output_binds_a_different_url(self):
        await self.js("""(() => {
          const crop=fixture.crop,parent=fixture.parent,single=fixture.single;
          delete crop.$refs.croppieRefHorizon;
          crop.$refs.croppieRefVertical={croppie:{elements:{img:null}}};
          crop.$options.methods.preprocessImage=function(){};
          crop.preprocessImage=function(url){return new Promise(resolve=>{const img=new Image();img.onload=()=>{
            const c=document.createElement('canvas');c.width=40;c.height=40;c.getContext('2d').drawImage(img,0,0);
            resolve({url:c.toDataURL('image/png'),rawWidth:40,rawHeight:40});};img.src=url;});};
          single.$emit=function(event,canvas){if(event==='change'){
            parent.customCover=canvas;parent.customCoverPreviewUrl=canvas.toDataURL(fixture.useDisplayWatcher||fixture.derivedPreview?'image/jpeg':'image/png');parent.currentCoverImageUrl=parent.customCoverPreviewUrl;parent.useCustomCover=true;
            crop.imageUrl=parent.customCoverPreviewUrl;crop.lastImageUrl=crop.imageUrl;
            Promise.resolve().then(()=>{if(fixture.useDisplayWatcher){const active=fixture.single;active.customCoverPreviewUrl=parent.customCoverPreviewUrl;active.handleCustomCoverPreviewUrlChange('official-tick');}return crop.preprocessImage(crop.imageUrl);}).then(result=>{const image=new Image();image.onload=()=>{crop.$refs.croppieRefVertical.croppie.elements.img=image;crop.isCroppieReady=true;parent.isCroppieLoading=false;};image.src=result.url;});
          }return single;};return true;
        })()""")
        session,observer=await self.install();await self.select(session)
        self.assertTrue(await self.js('fixture.crop.$refs.croppieRefVertical.croppie.elements.img.src!==fixture.crop.imageUrl'))
        data=await self.start();self.sns(session,data);await self.finish(observer)
        self.assertEqual(len(observer.upload_links()),1)

    async def make_reusable_vertical_crop(self):
        await self.js("""(() => {
          const crop=fixture.crop,parent=fixture.parent,single=fixture.single;
          delete crop.$refs.croppieRefHorizon;
          crop.$refs.croppieRefVertical={croppie:{elements:{img:null}}};
          crop.$options.methods.preprocessImage=function(){};
          crop.preprocessImage=function(url){return new Promise(resolve=>{const img=new Image();img.onload=()=>{
            const c=document.createElement('canvas');c.width=40;c.height=40;c.getContext('2d').drawImage(img,0,0);
            const result={url:c.toDataURL('image/png'),rawWidth:40,rawHeight:40};
            if(fixture.holdPreprocess)fixture.releasePreprocess=()=>resolve(result);else resolve(result);
          };img.src=url;});};
          single.$emit=function(event,canvas){if(event==='change'){
            parent.customCover=canvas;parent.customCoverPreviewUrl=canvas.toDataURL(fixture.useDisplayWatcher||fixture.derivedPreview?'image/jpeg':'image/png');parent.currentCoverImageUrl=parent.customCoverPreviewUrl;parent.useCustomCover=true;
            crop.imageUrl=parent.customCoverPreviewUrl;crop.lastImageUrl=crop.imageUrl;
            Promise.resolve().then(()=>{if(fixture.useDisplayWatcher){const active=fixture.single;active.customCoverPreviewUrl=parent.customCoverPreviewUrl;active.handleCustomCoverPreviewUrlChange('official-tick');}return crop.preprocessImage(crop.imageUrl);}).then(result=>{const image=new Image();image.onload=()=>{crop.$refs.croppieRefVertical.croppie.elements.img=image;crop.isCroppieReady=true;parent.isCroppieLoading=false;};image.src=result.url;});
          }return this;};
          parent.$emit=function(event,payload,...rest){if(event==='updateCover'){
            fixture.preview.video[parent.previewMode==='landscape-share'?'shareCoverUrl':'coverUrl']=payload.coverImgs[0].httpsUrl;
          }if(event==='input')parent.value=payload;fixture.emitted={event,payload,rest};return parent;};
          fixture.originalSingleReader=single.getFileImageDataURL;fixture.originalSingleEmitter=single.$emit;fixture.originalSingleWatcher=single.handleCustomCoverPreviewUrlChange;
          fixture.firstSingle=single;fixture.firstCrop=crop;
        })()""")

    async def test_two_slots_rebuild_single_but_reuse_crop_and_keep_both_native_origins(self):
        # Official CoverSlider is keyed by previewMode; its SingleUploader is
        # rebuilt while a vertical video's same CropImageVertical is retained.
        await self.make_reusable_vertical_crop()
        await self.js('fixture.useDisplayWatcher=true;fixture.clearNativeInput=true')
        portrait=await self.js('fixtureMakeFile(true).then(async f=>({name:f.name,size:f.size,sha256:Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",await f.arrayBuffer())),v=>v.toString(16).padStart(2,"0")).join("")}))')
        self.assertNotEqual(portrait['sha256'],self.asset['sha256'])
        session,observer=await self.install(covers={'cover_landscape':self.asset,'cover_portrait':portrait})
        await self.select(session);data=await self.start();self.sns(session,data,task='first-cover')
        await self.finish(observer)
        self.assertEqual(len(observer.upload_links()),1)
        selected=await self.js("""async () => {
          const old=fixture.single,parent=fixture.parent,crop=fixture.crop;
          const node=document.createElement('div');node.className='single-cover-uploader-wrap';
          const input=document.createElement('input');input.type='file';node.append(input);old.$el.replaceWith(node);
          const single={$el:node,$parent:parent,$refs:{singleCoverUploader:input},$options:{methods:{getFileImageDataURL(){},handleCustomCoverPreviewUrlChange(){}}},imgSrc:'',customCoverPreviewUrl:'',
            getFileImageDataURL:fixture.originalSingleReader,$emit:fixture.originalSingleEmitter,handleCustomCoverPreviewUrlChange:fixture.originalSingleWatcher};
          node.__vue__=single;fixture.single=single;parent.previewMode='landscape-profile';parent.value=true;
          parent.isCroppieLoading=true;crop.isCroppieReady=false;fixture.blob=null;
          input.addEventListener('change',()=>single.getFileImageDataURL(input.files[0]).then(url=>{
            single.imgSrc=url;const image=new Image();image.onload=()=>{const c=document.createElement('canvas');
              c.width=image.naturalWidth;c.height=image.naturalHeight;c.getContext('2d').drawImage(image,0,0);single.$emit('change',c);
            };image.src=url;if(fixture.clearNativeInput)input.value='';
          }));
          await Promise.resolve();const file=await fixtureMakeFile(true),dt=new DataTransfer();dt.items.add(file);input.files=dt.files;
          const at=Date.now()/1000;input.dispatchEvent(new Event('change',{bubbles:true}));
          return {name:file.name,size:file.size,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),v=>v.toString(16).padStart(2,'0')).join(''),selected_at:at};
        }""")
        session.files.append(selected)
        await self.wait('fixture.crop.isCroppieReady===true')
        self.assertTrue(await self.js('fixture.single!==fixture.firstSingle&&fixture.crop===fixture.firstCrop'))
        self.assertTrue(await self.js('window.__sauTCCoverObserver.parents.get(fixture.parent).boundSource===fixture.crop.$refs.croppieRefVertical.croppie.elements.img.src'))
        data=await self.start();self.sns(session,data,resource='http://wxapp.tc.qq.com/second',task='second-cover')
        await self.finish(observer,resource='https://finder.video.qq.com/second')
        links=observer.upload_links()
        self.assertEqual(len(session.files),2)
        self.assertEqual(len(observer.records),2)
        self.assertEqual(len(links),2)
        self.assertEqual({link['sha256'] for link in links},{self.asset['sha256'],portrait['sha256']})
        self.assertTrue(all(link['complete'] is True for link in links))
        self.assertEqual({record['slot'] for record in observer.records.values()},{'cover_landscape','cover_portrait'})
        for counts in observer.diagnostic_counts().values():
            self.assertEqual((counts['records_count'],counts['native_match_count'],counts['links_count']),(1,1,1))
            self.assertEqual(counts['resource_chain_count'],1)
        trace=await observer.diagnostic_trace()
        self.assert_trace_schema(trace)
        for slot in COVER_DIAGNOSTIC_SLOTS:
            counts=trace['main'][slot]
            self.assertEqual(counts['selection_accepted'],1)
            self.assertEqual(counts['reader_accepted'],1)
            self.assertEqual(counts['single_canvas_accepted'],1)
            self.assertEqual(counts['display_source_attested'],1)
            self.assertEqual(counts['preprocess_bound_accepted'],1)
            self.assertEqual(counts['crop_result_called'],1)
            self.assertEqual(counts['crop_invocation_started'],1)
            self.assertEqual(counts['applied_report_delivered'],1)
            self.assertEqual(counts['ready_parent_open_false'],0)
            self.assertEqual(counts['ready_bound_image_match_true'],1)
            self.assertEqual(counts['live_dataurl_match_false'],0)
        self.assertEqual(await self.js('fixture.counts.crop'),2)
        self.assertTrue(await self.js('fixture.parent.value===false'))

    async def test_late_preprocess_of_replaced_crop_does_not_bind_current_selection(self):
        await self.make_reusable_vertical_crop()
        session,observer=await self.install()
        await self.js('fixture.holdPreprocess=true')
        session.files.append(await self.js('fixtureSelect()'))
        await self.wait('typeof fixture.releasePreprocess==="function"')
        await self.js('fixture.parent.$refs.cropRef={};fixture.releasePreprocess()')
        await self.wait('fixture.crop.isCroppieReady===true')
        await observer.drain()
        self.assertFalse(await self.js('typeof window.__sauTCCoverObserver.parents.get(fixture.parent).boundSource==="string"'))
        self.assertEqual(observer.records,{})
        self.assertEqual(observer.upload_links(),[])

    async def test_close_restores_instance_methods_without_global_canvas_changes(self):
        before=await self.js('HTMLCanvasElement.prototype.toBlob.toString()')
        session,observer=await self.install();await self.select(session)
        await observer.close()
        self.assertEqual(await self.js('HTMLCanvasElement.prototype.toBlob.toString()'),before)
        self.assertTrue(await self.js('!Object.hasOwn(HTMLCanvasElement.prototype,"__sauObserver")'))
        self.assertFalse(observer.accepts_upload_metadata(('finder_video_img.jpeg',100)))

    async def test_trace_identifies_parent_open_guard_without_altering_crop_result(self):
        session,observer=await self.install();await self.select(session)
        await self.js('fixture.parent.value=false;fixture.parent.isSettingCover=true')
        self.assertTrue(await self.js('fixture.crop.getCropResult("unchanged").then(c=>c===fixture.cropped)'))
        await observer.drain()
        trace=await observer.diagnostic_trace();self.assert_trace_schema(trace)
        counts=trace['main']['cover_landscape']
        self.assertEqual(counts['crop_result_called'],1)
        self.assertEqual(counts['crop_receiver_true'],1)
        self.assertEqual(counts['crop_selection_true'],1)
        self.assertEqual(counts['ready_parent_open_false'],1)
        self.assertEqual(counts['ready_live_true'],1)
        self.assertEqual(counts['ready_bound_image_match_true'],1)
        self.assertEqual(counts['crop_setting_cover_true'],1)
        self.assertEqual(counts['crop_invocation_started'],0)
        self.assertEqual(await self.js('fixture.counts.crop'),1)
        self.assertEqual(observer.records,{})

    async def test_trace_identifies_live_preview_mismatch_and_reader_receiver_args(self):
        session,observer=await self.install();await self.select(session)
        before=await observer.diagnostic_trace()
        await self.js('fixture.single.getFileImageDataURL.call({},fixture.single.$refs.singleCoverUploader.files[0])')
        await self.js('fixtureMakeFile(true).then(f=>fixture.single.getFileImageDataURL(f))')
        await self.js('fixture.parent.currentCoverImageUrl="private-other-preview";fixture.parent.isSettingCover=true')
        await self.js('fixture.crop.getCropResult("original-args")')
        trace=await observer.diagnostic_trace();self.assert_trace_schema(trace)
        counts=trace['main']['cover_landscape']
        self.assertEqual(counts['reader_called'],before['main']['cover_landscape']['reader_called']+2)
        self.assertEqual(counts['reader_receiver_false'],1)
        self.assertEqual(counts['reader_args_match_false'],1)
        self.assertEqual(counts['reader_accepted'],1)
        self.assertEqual(counts['live_current_preview_match_false'],1)
        self.assertEqual(counts['ready_live_false'],1)
        self.assertEqual(counts['crop_invocation_started'],0)
        self.assertNotIn('private-other-preview',json.dumps(trace))
        self.assertEqual(observer.records,{})

    async def test_trace_is_private_readonly_detached_and_retained_after_close(self):
        session,observer=await self.install();await self.select(session)
        await observer.drain()
        before=await self.js('JSON.stringify(window.__sauTCCoverObserver.trace)')
        await self.js('(()=>{window.__sauTCCoverObserver.flush=()=>{throw Error("diagnostic_must_not_flush")};return true;})()')
        trace=await observer.diagnostic_trace();self.assert_trace_schema(trace)
        self.assertEqual(await self.js('JSON.stringify(window.__sauTCCoverObserver.trace)'),before)
        encoded=json.dumps(trace)
        for sensitive in (self.asset['name'],self.asset['sha256'],observer.observer_nonce,'nonce-a','job-a','https://','data:image'):
            self.assertNotIn(sensitive,encoded)
        detached=copy.deepcopy(trace)
        trace['main']['cover_landscape']['reader_called']=1000
        self.assertEqual(await observer.diagnostic_trace(),detached)
        await observer.close()
        self.assertEqual(await observer.diagnostic_trace(),detached)
        self.assertTrue(await self.js('window.__sauTCCoverObserver.scope===null&&window.__sauTCCoverObserver.wrappers.size===0'))
        # A different execution must reset these numbers, not attribute the
        # previous file selection to the new execution.
        _,other=await self.install(nonce='nonce-new')
        new=await other.diagnostic_trace();self.assert_trace_schema(new)
        self.assertEqual(new['main']['cover_landscape']['reader_called'],0)
        self.assertGreater(new['main']['cover_landscape']['configured'],0)
        self.assertTrue(all(value==0 for values in (await observer.diagnostic_trace())['main'].values() for value in values.values()))

    async def test_trace_upgrade_replaces_only_fully_closed_old_observer(self):
        await self.js('window.__sauTCCoverObserver={trace_version:1,scope:{active:true},roots:new Map(),wrappers:new Map(),pending:new Set()};window.oldObserver=window.__sauTCCoverObserver')
        await self.js(TC_COVER_OBSERVER_SCRIPT)
        self.assertTrue(await self.js('window.__sauTCCoverObserver===window.oldObserver'))
        await self.js('oldObserver.scope=null;oldObserver.pending.add(Promise.resolve())')
        await self.js(TC_COVER_OBSERVER_SCRIPT)
        self.assertTrue(await self.js('window.__sauTCCoverObserver===window.oldObserver'))
        await self.js('oldObserver.pending.clear()')
        await self.js(TC_COVER_OBSERVER_SCRIPT)
        self.assertTrue(await self.js('window.__sauTCCoverObserver!==window.oldObserver&&window.__sauTCCoverObserver.trace_version===3'))


class TCCoverNoPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_projection_keeps_realms_separate_and_rejects_private_or_invalid_leaves(self):
        class Frame:
            def __init__(self, value):
                self.evaluate=AsyncMock(return_value=value)

        def values(count=0):
            result={slot:dict.fromkeys(COVER_TRACE_KEYS,0) for slot in COVER_DIAGNOSTIC_SLOTS}
            result['cover_landscape']['reader_called']=count
            return result

        main,owner=Frame(values(7)),Frame(values(5))
        page=SimpleNamespace(main_frame=main)
        observer=TCCoverObserver(SimpleNamespace(page=page))
        observer.frames={main,owner}
        trace=await observer.diagnostic_trace()
        self.assertEqual(trace['main']['cover_landscape']['reader_called'],7)
        self.assertEqual(len(trace['owner']),1)
        self.assertEqual(trace['owner'][0]['cover_landscape']['reader_called'],5)
        self.assertNotEqual(trace['main']['cover_landscape']['reader_called'],12)
        for invalid in ('private-value',True,-1,1000000001):
            result=values()
            result['cover_landscape']['reader_called']=invalid
            main.evaluate.return_value=result
            trace=await observer.diagnostic_trace()
            self.assertEqual(trace['main'],values())
            self.assertNotIn('private-value',json.dumps(trace))
        result=values();result['cover_landscape']['private_token']='private-secret'
        main.evaluate.return_value=result
        self.assertEqual((await observer.diagnostic_trace())['main'],values())
        self.assertNotIn('private-secret',json.dumps(await observer.diagnostic_trace()))
        # The getter is a projection only, never flush/configure/capture.
        for call in main.evaluate.call_args_list:
            script=call.args[0]
            self.assertNotIn('.flush(',script)
            self.assertNotIn('.configure(',script)
            self.assertNotIn('.capture(',script)
            self.assertIs(call.kwargs['isolated_context'],False)

    def diagnostic_fixture(self):
        origin_hash,derived_hash='7'*64,'8'*64
        at=time.time()
        asset={'kind':'cover','name':'private-origin.png','size':8,'sha256':origin_hash}
        selection={'name':asset['name'],'size':asset['size'],'sha256':origin_hash,'selected_at':at+.01}
        metadata=('finder_video_img.jpeg',12)
        resource='https://finder.video.qq.com/private-resource'
        record={'slot':'cover_landscape','origin_name':asset['name'],'origin_size':asset['size'],
            'origin_sha256':origin_hash,'selected_at':at+.01,'encoded_at':at+.02,'applied_at':at+.1,
            'derived_name':metadata[0],'derived_size':metadata[1],'derived_sha256':derived_hash,
            'applied_id':resource,'account_id':'private-account','token':'private-token'}
        exchanges=[
            {'tc_protocol':'dfs','stage':'allocated','metadata':metadata,'task_id':'private-task','upload_id':'private-upload','plan':[12],'observed_at':at+.03},
            {'tc_protocol':'dfs','stage':'allocated','metadata':metadata,'task_id':'private-task','upload_id':'private-upload','part_number':1,'etag':'private-etag','trans_flag':'private-flag','body_sha256':derived_hash,'observed_at':at+.04},
            {'tc_protocol':'dfs','stage':'complete','metadata':metadata,'task_id':'private-task','upload_id':'private-upload','parts':[(1,'private-etag')],'trans_flag':'private-flag','ids':[resource],'observed_at':at+.05}]
        page=SimpleNamespace()
        session=SimpleNamespace(page=page,platform='tencent',closed=False,started_at=at,
            expected={'cover_sha256':{'cover_landscape':origin_hash}},assets={'cover_landscape':asset},
            files=[selection],exchanges=exchanges)
        observer=TCCoverObserver(session)
        page._sau_draft_evidence=session;session.tc_cover_observer=observer
        observer.records['private-invocation']=record
        return session,observer

    def assert_diagnostic_schema(self,counts):
        slots={'cover_landscape','cover_portrait'}
        keys={'records_count','native_match_count','links_count','metadata_match_count',
              'time_window_match_count','complete_candidate_count','body_sha_match_count',
              'applied_resource_match_count','resource_chain_count'}
        self.assertEqual(set(counts),slots)
        for values in counts.values():
            self.assertEqual(set(values),keys)
            self.assertTrue(all(type(value) is int and value>=0 for value in values.values()))

    async def test_diagnostic_fixed_counts_are_private_free_readonly_and_detached(self):
        session,observer=self.diagnostic_fixture()
        before=copy.deepcopy((observer.records,session.files,session.exchanges,session.expected,session.assets))
        counts=observer.diagnostic_counts()
        self.assert_diagnostic_schema(counts)
        self.assertEqual(counts['cover_landscape'],{'records_count':1,'native_match_count':1,'links_count':1,
            'metadata_match_count':3,'time_window_match_count':3,'complete_candidate_count':1,
            'body_sha_match_count':1,'applied_resource_match_count':1,'resource_chain_count':1})
        self.assertTrue(all(value==0 for value in counts['cover_portrait'].values()))
        encoded=json.dumps(counts)
        for sensitive in ('private-origin.png','private-resource','private-task','private-upload','private-etag',
                          'private-flag','private-invocation','private-account','private-token','7'*64,'8'*64,'https://'):
            self.assertNotIn(sensitive,encoded)
        self.assertEqual((observer.records,session.files,session.exchanges,session.expected,session.assets),before)
        counts['cover_landscape']['links_count']=99
        self.assertEqual(observer.diagnostic_counts()['cover_landscape']['links_count'],1)
        self.assertEqual(len(observer.upload_links()),1)

    async def test_diagnostic_native_ambiguity_still_has_no_link(self):
        session,observer=self.diagnostic_fixture()
        session.files.append(dict(session.files[0]))
        counts=observer.diagnostic_counts()
        self.assert_diagnostic_schema(counts)
        self.assertEqual(counts['cover_landscape']['native_match_count'],2)
        self.assertEqual(counts['cover_landscape']['resource_chain_count'],1)
        self.assertEqual(counts['cover_landscape']['links_count'],0)
        self.assertEqual(observer.upload_links(),[])

    async def test_diagnostic_resource_ambiguity_is_counted_and_never_relaxed(self):
        session,observer=self.diagnostic_fixture()
        session.exchanges.extend([{**row,'task_id':'private-second-task','upload_id':'private-second-upload'}
                                  for row in session.exchanges])
        counts=observer.diagnostic_counts()
        self.assert_diagnostic_schema(counts)
        self.assertEqual(counts['cover_landscape']['metadata_match_count'],6)
        self.assertEqual(counts['cover_landscape']['complete_candidate_count'],2)
        self.assertEqual(counts['cover_landscape']['resource_chain_count'],2)
        self.assertEqual(counts['cover_landscape']['links_count'],0)
        self.assertEqual(observer.upload_links(),[])

    async def test_diagnostic_time_body_and_resource_gates_distinguish_missing_link(self):
        session,observer=self.diagnostic_fixture()
        original=copy.deepcopy(session.exchanges)
        for gate in ('time','body','resource'):
            with self.subTest(gate=gate):
                session.exchanges=copy.deepcopy(original)
                if gate=='time': session.exchanges[1]['observed_at']=observer.records['private-invocation']['applied_at']+1
                elif gate=='body': session.exchanges[1]['body_sha256']='9'*64
                else: session.exchanges[2]['ids']=['https://finder.video.qq.com/wrong-resource']
                counts=observer.diagnostic_counts()
                self.assert_diagnostic_schema(counts)
                row=counts['cover_landscape']
                self.assertEqual(row['records_count'],1);self.assertEqual(row['native_match_count'],1)
                self.assertEqual(row['metadata_match_count'],3)
                self.assertEqual(row['links_count'],0);self.assertEqual(row['resource_chain_count'],0)
                if gate=='time': self.assertEqual(row['time_window_match_count'],2)
                elif gate=='body': self.assertEqual(row['body_sha_match_count'],0)
                else: self.assertEqual(row['applied_resource_match_count'],0)
                self.assertEqual(observer.upload_links(),[])

    async def test_diagnostic_unknown_slots_corrupt_rows_and_noncurrent_are_safe_zero_counts(self):
        session,observer=self.diagnostic_fixture()
        observer.records={'private-extra':{'slot':'private-secret-slot'},'unhashable':{'slot':[]},
            'unrequested':{'slot':'cover_portrait'},'not-object':['private-secret']}
        counts=observer.diagnostic_counts()
        self.assert_diagnostic_schema(counts)
        self.assertTrue(all(value==0 for row in counts.values() for value in row.values()))
        observer.records={'malformed':{'slot':'cover_landscape'}}
        counts=observer.diagnostic_counts()
        self.assert_diagnostic_schema(counts)
        self.assertEqual(counts['cover_landscape']['records_count'],1)
        self.assertEqual(counts['cover_landscape']['links_count'],0)
        page=session.page;page._sau_draft_evidence=None
        counts=observer.diagnostic_counts()
        self.assert_diagnostic_schema(counts)
        self.assertTrue(all(value==0 for row in counts.values() for value in row.values()))

    async def test_video_only_session_does_not_touch_fake_page(self):
        page=SimpleNamespace()
        session=SimpleNamespace(page=page,platform='tencent',closed=False,expected={'cover_sha256':{}},assets={})
        observer=await install_tc_cover_observer(session)
        self.assertIs(session.tc_cover_observer,observer)
        self.assertFalse(observer.available)
        await observer.close()
