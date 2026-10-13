"""Local Chromium fixtures for the Douyin upload observer; no creator account/network."""
import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright

from uploader.dy_evidence import DYUploadObserver, install_dy_observer


FIXTURE = r'''<!doctype html><meta charset="utf-8"><div id="root"></div>
<input id="video" type="file" accept="video/*">
<script>
(() => {
  window.fixture = {resolvers:[], syncThrow:false, syncError:new Error('fixture sync'),
    baseReturns:[], innerReturns:[], outerReturn:null, originalOuterReturn:null,
    outerObserved:null, outerRejected:null, sdkCalls:0};
  class SDKUploader {
    constructor() { this.byteVideoUploader={}; this.uploaderOptions={type:'video'}; }
    uploadFile(file) {
      fixture.sdkCalls++;
      if (fixture.syncThrow) throw fixture.syncError;
      let resolve, reject;
      const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
      fixture.resolvers.push({resolve,reject,file});
      fixture.baseReturns.push(promise);
      return promise;
    }
    _intiEvent() {} _handleCompleteResolve() {} _handleCompleteReject() {}
    _resetCompleteInfo() {} updateUploadOption() {} resetVideoUploadConfig() {} setVideoUploadConfig() {}
    uploadImgArr() {} uploadSrc() {} cancel() {} pause() {} resume() {} onProcess() {} offProcess() {}
  }
  class Editor {
    constructor() {
      this.state={uploadPercent:0,uploadProgressDetail:{},file:null,metaInfo:{},uploadStatus:2};
      this.uploader=new SDKUploader(); this.changeOption=function(){};
      this.uploadFile=(raw)=>{
        const file=raw instanceof FileList?raw.item(0):raw;
        this.state={...this.state,uploadStatus:1,file};
        const inner=this.uploader.uploadFile(file);fixture.innerReturns.push(inner);
        const outer=inner.then(result=>{this.state={...this.state,uploadStatus:2,file:{rawFile:file}};return undefined;});
        fixture.originalOuterReturn=outer;
        fixture.outerObserved=outer.then(value=>value,error=>{fixture.outerRejected=error;});
        return outer;
      };
    }
    initUploader() {} initUploaderLibra() {} componentDidMount() {} componentDidUpdate() {}
    componentWillUnmount() {} render() {}
  }
  const editor=new Editor(), container=document.querySelector('#root');
  const fiber={tag:1,stateNode:editor,child:null,sibling:null,alternate:null};
  const fiberRoot={current:null};
  const host={tag:3,stateNode:fiberRoot,child:fiber,sibling:null,alternate:null};
  fiberRoot.current=host;container.__reactContainer$fixture=host;
  fixture.editor=editor;fixture.fiber=fiber;fixture.container=container;
  fixture.begin=()=>{
    const file=document.querySelector('#video').files[0];
    try{const p=editor.uploadFile(file);fixture.outerReturn=p;return true;}
    catch(error){fixture.syncCaught=error;return false;}
  };
  fixture.attachResettingInput=()=>{
    document.querySelector('#video').addEventListener('change',event=>{
      const list=event.target.files;
      event.target.remove(); // official resetInput changes React key
      fixture.normalCallback=Promise.resolve().then(()=>{
        const p=editor.uploadFile(list);fixture.outerReturn=p;return true;
      });
    },{once:true});
  };
  fixture.resolveAt=(index,result)=>fixture.resolvers[index].resolve(result);
  fixture.rejectAt=(index,error)=>fixture.resolvers[index].reject(error);
  fixture.addAlternate=()=>{fiber.alternate={tag:1,stateNode:new Editor(),child:null,sibling:null,alternate:fiber};};
  fixture.useStaleRootAnchor=()=>{
    const old={tag:3,stateNode:fiberRoot,child:null,sibling:null,alternate:host};host.alternate=old;
    container.__reactContainer$fixture=old;
  };
  fixture.addDuplicate=()=>{
    const duplicate=new Editor(),second={tag:1,stateNode:duplicate,child:null,sibling:null,alternate:null};
    fiber.sibling=second;fixture.duplicate=duplicate;
  };
})();
</script>'''


class DouyinUploadEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.playwright = await async_playwright().start()
        bundled_shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        launch = {'executable_path': str(bundled_shell)} if bundled_shell.exists() else {'channel': 'chrome'}
        self.browser = await self.playwright.chromium.launch(headless=True, timeout=90000, **launch)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        async def serve(route):
            await route.fulfill(content_type='text/html', body=FIXTURE)
        await self.context.route('**/*', serve)
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')
        self.video_bytes = bytes(range(251)) * 311 + b'douyin-fixture-tail'
        self.sha256 = hashlib.sha256(self.video_bytes).hexdigest()
        self.md5 = hashlib.md5(self.video_bytes).hexdigest()
        self.observers = []
        self.session = None

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def js(self, script):
        return await self.page.evaluate(script, isolated_context=False)

    async def install(self, duplicate=False):
        if duplicate:
            await self.js('fixture.addDuplicate();true')
        self.session = SimpleNamespace(page=self.page, platform='douyin', closed=False,
            expected={'job_id':'dy-fixture-job','receipt_nonce':'dy-fixture-receipt',
                      'video_sha256':self.sha256},
            assets={'video':{'kind':'video','name':'source.mp4','size':len(self.video_bytes),
                             'sha256':self.sha256,'type':'video/mp4'}})
        self.page._sau_draft_evidence = self.session
        observer = await install_dy_observer(self.session)
        self.assertIsInstance(observer, DYUploadObserver)
        self.observers.append(observer)
        return observer

    async def select_video(self):
        await self.page.locator('#video').set_input_files({
            'name':'source.mp4','mimeType':'video/mp4','buffer':self.video_bytes})

    async def begin(self):
        return await self.js('fixture.begin()')

    async def resolve(self, index=0, *, vid='v_fixture_video_01', md5=None, same_file=True):
        md5 = self.md5 if md5 is None else md5
        file_expr = 'item.file' if same_file else "new File([await item.file.arrayBuffer()],item.file.name,{type:item.file.type})"
        return await self.js('''(async() => {
          const item=fixture.resolvers[%d];
          const file=%s;
          fixture.resolveAt(%d,{file,vid:%s,md5:%s});return true;
        })()''' % (index, file_expr, index, json.dumps(vid), json.dumps(md5)))

    async def settle(self, observer):
        await observer.drain(timeout=3)
        try:
            await self.js('fixture.outerObserved')
        except Exception:
            pass
        await observer.drain(timeout=3)

    async def test_current_committed_input_and_sdk_result_create_sha_to_vid_link(self):
        observer = await self.install()
        await self.js('fixture.addAlternate();true')
        await self.js('fixture.useStaleRootAnchor();history.replaceState({},"","/creator-micro/content/post/video");true')
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.js('history.replaceState({},"","/creator-micro/content/upload");true')
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        self.assertTrue(await self.begin())
        self.assertTrue(await self.js('fixture.outerReturn===fixture.originalOuterReturn && fixture.innerReturns[0]===fixture.baseReturns[0]'))
        await self.resolve()
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [{'kind':'video','sha256':self.sha256,
                                                     'ids':['v_fixture_video_01'],'complete':True}])
        self.assertEqual(observer.accepted_count, 1)

    async def test_native_file_list_survives_official_input_reset_before_async_upload(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.js('fixture.attachResettingInput();true')
        await self.select_video()
        self.assertTrue(await self.js('fixture.normalCallback'))
        self.assertTrue(await self.js('!document.querySelector("#video") && fixture.innerReturns[0]===fixture.baseReturns[0]'))
        await self.resolve()
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [{'kind':'video','sha256':self.sha256,
                                                     'ids':['v_fixture_video_01'],'complete':True}])

    async def test_same_bytes_unselected_file_cannot_replace_native_list_file(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.js('''(async()=>{const file=document.querySelector('#video').files[0];
          const other=new File([await file.arrayBuffer()],file.name,{type:file.type});
          fixture.outerReturn=fixture.editor.uploadFile(other);return true})()''')
        await self.resolve()
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])

    async def test_inner_sdk_rejection_preserves_rejection_and_never_links(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await self.js("fixture.rejectAt(0,new Error('fixture rejection'));true")
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertTrue(await self.js('fixture.outerReturn===fixture.originalOuterReturn && fixture.innerReturns[0]===fixture.baseReturns[0] && !!fixture.outerRejected'))

    async def test_synchronous_sdk_throw_is_the_same_error_and_is_not_observed_as_success(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        same = await self.js('fixture.syncThrow=true;fixture.begin()===false && fixture.syncCaught===fixture.syncError')
        self.assertTrue(same)
        await observer.drain(timeout=1)
        self.assertEqual(observer.upload_links(), [])

    async def test_result_file_must_be_the_exact_native_file_object(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await self.resolve(same_file=False)
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.accepted_count, 0)

    async def test_recovered_upload_status_two_without_a_new_outer_call_is_not_evidence(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.js("fixture.editor.state={...fixture.editor.state,uploadStatus:2,file:{rawFile:document.querySelector('#video').files[0]}};true")
        await observer.drain(timeout=1)
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(await self.js('fixture.sdkCalls'), 0)

    async def test_two_current_matching_class_instances_fail_closed(self):
        observer = await self.install(duplicate=True)
        self.assertFalse(await observer.ensure_ready(timeout=.5))
        await self.select_video()
        await self.begin()
        await self.js('fixture.resolveAt(0,{file:fixture.resolvers[0].file,vid:"v_fixture_video_01",md5:' +
                      json.dumps(self.md5) + '});true')
        await observer.drain(timeout=1)
        self.assertEqual(observer.upload_links(), [])

    async def test_foreign_nonce_report_is_rejected_even_from_current_main_frame(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await observer.drain(timeout=1)
        report = {'v':1,'job_id':observer.job_id,'receipt_nonce':observer.receipt_nonce,
                  'observer_nonce':observer.observer_nonce,'invocation_id':observer.latest_invocation or '1',
                  'name':'source.mp4','size':len(self.video_bytes),'type':'video/mp4',
                  'sha256':self.sha256,'vid':'v_foreign','md5':self.md5,
                  'started_at':observer.started_at,'completed_at':observer.started_at+0.1}
        source={'page':self.page,'frame':self.page.main_frame}
        for key,value in (('observer_nonce','0'*48),('job_id','foreign-job'),('receipt_nonce','foreign-receipt')):
            with self.subTest(field=key):
                self.assertFalse(observer._receive(source,'video',{**report,key:value}))
        child=await self.page.evaluate_handle('''() => {const frame=document.createElement('iframe');document.body.append(frame);return frame}''', isolated_context=False)
        child_frame=next(frame for frame in self.page.frames if frame.parent_frame is self.page.main_frame)
        self.assertFalse(observer._receive({'page':self.page,'frame':child_frame},'video',report))
        await child.dispose()
        self.assertEqual(observer.upload_links(), [])

    async def test_new_video_selection_invalidates_old_pending_upload_and_latest_retry_can_link(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await observer.drain(timeout=1)
        await self.select_video()  # same bytes, new native File identity and a new selection generation
        await self.begin()
        await observer.drain(timeout=1)
        await self.resolve(0,vid='v_stale_upload_01')
        await observer.drain(timeout=1)
        self.assertEqual(observer.upload_links(), [])
        await self.resolve(1,vid='v_current_upload_02')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [{'kind':'video','sha256':self.sha256,
                                                     'ids':['v_current_upload_02'],'complete':True}])
        self.assertEqual(observer.accepted_count, 1)

    async def test_malformed_video_id_or_md5_never_produces_a_link(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await self.resolve(vid='not a valid id')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.accepted_count, 0)

    async def test_observer_locate_exception_never_changes_original_call_or_promise(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.js("Object.defineProperty(fixture.fiber,'stateNode',{configurable:true,get(){throw new Error('observer locate fixture')}});true")
        self.assertTrue(await self.begin())
        self.assertTrue(await self.js('fixture.sdkCalls===1 && fixture.outerReturn===fixture.originalOuterReturn && fixture.innerReturns[0]===fixture.baseReturns[0]'))
        await self.resolve()
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])

    async def test_failed_md5_shape_never_produces_a_link(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await self.resolve(md5='not-an-md5')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.accepted_count, 0)

    async def test_sdk_optional_md5_is_not_required_for_exact_file_sha_and_vid(self):
        observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=2))
        await self.select_video()
        await self.begin()
        await self.js('fixture.resolveAt(0,{file:fixture.resolvers[0].file,vid:"v_fixture_video_01",md5:undefined});true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [{'kind':'video','sha256':self.sha256,
                                                     'ids':['v_fixture_video_01'],'complete':True}])


if __name__ == '__main__':
    unittest.main()
