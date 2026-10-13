"""Local DOM/SDK fixtures only; no real platform, accounts or uploaded media."""
import asyncio
import hashlib
import io
import json
import socket
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from patchright.async_api import async_playwright

from uploader.xhs_evidence import SDK_OBSERVER_SCRIPT, install_xhs_observer


FIXTURE = r"""<!doctype html><meta charset="utf-8"><body><div id="creator-publish-dom"></div>
<script>
window.fixture={factoryCalls:0,createCalls:[],calls:[],pending:[],resolved:[],rejected:[],reads:[]};
window.webpackChunkugc=[];
// The current official runtime does not expose require.c. Observing it must
// never execute this lazy module just to obtain an SDK instance.
const factories={67490(module){
  fixture.factoryCalls++;
  const map=new Map();
  class Manager {
    getUploader(name){fixture.reads.push(name);return map.get(name)||null;}
    createUploader(name,options){
      fixture.createCalls.push({name,receiver:this,options});
      if(map.has(name))return map.get(name);
      const uploader={name,upload:function(file,options,...rest){
        fixture.calls.push({receiver:this,file,options,rest,uploader});
        if(options?.syncThrow)throw options.syncThrow;
        if(options?.returnedObject)return options.returnedObject;
        let resolve,reject;
        const promise=new Promise((r,j)=>{resolve=r;reject=j;});
        fixture.pending.push({promise,resolve,reject,file,options,uploader});
        return promise;
      }};
      map.set(name,uploader);return uploader;
    }
  }
  const publisher={uploader:new Manager()};
  window._publisher=publisher;module.exports={t$:publisher};
}};
const modules={};
window.fixtureRequire=id=>{
 if(!modules[id]){const module={exports:{}};factories[id](module);modules[id]=module;}
 return modules[id].exports;
};
window.fixtureLoad=()=>fixtureRequire(67490);
window.fixtureFile=(wrong=false)=>{
 const bytes=new Uint8Array(256*4301);
 for(let i=0;i<bytes.length;i++)bytes[i]=i%256;
 if(wrong)bytes[1048577]^=1;
 return new File([bytes,'streaming SHA-256 tail'],'synthetic.mp4',{type:'video/mp4'});
};
window.fixtureStart=(name='video-uploader',wrong=false)=>{
 const options={sentinel:{nested:'unchanged'}};
 const creatorOptions={scene:name==='video-uploader'?'preview_video':'video'};
 const uploader=_publisher.uploader.createUploader(name,creatorOptions);
 const file=fixtureFile(wrong);
 const returned=uploader.upload(file,options,'third-argument');
 window.fixtureLast={returned,options,creatorOptions,uploader,file};
 return returned===fixture.pending.at(-1).promise;
};
window.fixtureResolve=(index,fileId='fixture-resource')=>{
 const result={fileId,previewUrl:'fixture-preview',extra:{unchanged:true}};
 fixture.resolved[index]=result;fixture.pending[index].resolve(result);
};
window.fixtureReject=index=>{
 const error=new Error('fixture SDK failure');
 fixture.rejected[index]=error;fixture.pending[index].reject(error);
};
window.fixtureShadow=(composed=false)=>{
 const host=document.createElement('section'),outer=host.attachShadow({mode:'open'});
 const child=document.createElement('section');outer.append(child);
 const inner=child.attachShadow({mode:'open'}),input=document.createElement('input');
 input.type='file';inner.append(input);document.body.append(host);
 input.addEventListener('change',()=>{
   const uploader=_publisher.uploader.createUploader('video-uploader',{scene:'preview_video'});
   const options={shadow:composed};
   const returned=uploader.upload(input.files[0],options);
   fixtureLast={returned,options,uploader,file:input.files[0]};
 });
 const data=new DataTransfer();data.items.add(fixtureFile());input.files=data.files;
 input.dispatchEvent(new Event('change',{bubbles:true,composed}));
 return host.shadowRoot===outer&&child.shadowRoot===inner;
};
</script>"""


class XHSSDKFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.playwright = await async_playwright().start()
        bundled_shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        launch = {'executable_path': str(bundled_shell)} if bundled_shell.exists() else {'channel': 'chrome'}
        self.browser = await self.playwright.chromium.launch(headless=True, timeout=90000, **launch)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.requests = []

        async def serve(route):
            self.requests.append(route.request.url)
            await route.fulfill(content_type='text/html', body=FIXTURE)

        await self.context.route('**/*', serve)
        await self.page.goto('http://127.0.0.1:45999/xhs-sdk-fixture')
        self.observers = []
        self.video_bytes = bytes(range(256)) * 4301 + b'streaming SHA-256 tail'
        self.sha256 = hashlib.sha256(self.video_bytes).hexdigest()

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def js(self, script):
        return await self.page.evaluate(script, isolated_context=False)

    async def install(self, job_id='job-a', nonce='nonce-a', load=True, cover=False):
        if load:
            await self.js('fixtureLoad();true')
        session = SimpleNamespace(page=self.page, platform='xiaohongshu', closed=False,
            expected={'job_id': job_id, 'receipt_nonce': nonce, 'video_sha256': self.sha256},
            assets={'video': {'kind': 'video', 'name': 'synthetic.mp4', 'size': len(self.video_bytes), 'sha256': self.sha256}})
        if cover:
            cover_bytes = b'synthetic cover source'
            cover_hash = hashlib.sha256(cover_bytes).hexdigest()
            session.assets['cover'] = {'kind': 'cover', 'name': 'synthetic.png',
                'size': len(cover_bytes), 'sha256': cover_hash}
            session.expected['cover_sha256'] = {'cover': cover_hash}
        self.page._sau_draft_evidence = session
        observer = await install_xhs_observer(session)
        self.observers.append(observer)
        return session, observer

    async def settle(self, observer):
        # The fixture resolver is synchronous; allow the native Promise observer
        # to run, then await only SHA and binding delivery, never the SDK task.
        await observer.drain()
        await asyncio.sleep(.01)
        await observer.drain()

    async def test_missing_publisher_never_executes_lazy_module_or_mutates_webpack(self):
        _, observer = await self.install(load=False)
        self.assertFalse(observer.available)
        await asyncio.sleep(.25)
        await observer.drain()
        self.assertEqual(await self.js('fixture.factoryCalls'), 0)
        self.assertEqual(await self.js('webpackChunkugc.length'), 0)
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        # Natural platform initialization is still lazy and happens exactly once.
        await self.js('fixtureLoad();true')
        await asyncio.sleep(.25)
        self.assertTrue(await self.js('fixtureStart()'))
        await self.js('fixtureResolve(0);true')
        await self.settle(observer)
        self.assertEqual(await self.js('fixture.factoryCalls'), 1)
        self.assertEqual(len(observer.upload_links()), 1)

    async def test_ready_barrier_observes_immediate_lazy_creation_without_polling_or_business_calls(self):
        _, observer = await self.install(load=False)
        self.assertFalse(observer.available)
        await self.js('fixtureLoad();true')
        # No 200 ms timer tick, and no uploader exists until the UI's next call.
        self.assertTrue(await observer.ensure_ready(timeout=.2))
        self.assertEqual(await self.js('fixture.factoryCalls'), 1)
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertTrue(status['manager_available'])
        self.assertTrue(status['create_wrapped'])
        self.assertEqual(status['wrapped_video_count'], 0)
        self.assertTrue(status['ready'])
        self.assertTrue(await self.js('fixtureStart()'))
        await self.js('fixtureResolve(0,"immediate-resource");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['immediate-resource'])

    async def test_ready_barrier_accepts_only_same_scope_sdk_cover_creator_chain(self):
        _, observer = await self.install(cover=True)
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertTrue(status['create_wrapped'])
        self.assertTrue(status['ready'])
        self.assertEqual(status['existing_video_count'], 0)
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        self.assertTrue(await self.js('''(() => {
          const manager=_publisher.uploader;
          return manager.createUploader===__sauXHSCoverObserver.managers.get(manager).wrapper&&
            __sauXHSCoverObserver.managers.get(manager).original===__sauXHSSDKObserver.managers.get(manager).wrapper;
        })()'''))
        self.assertTrue(await observer.ensure_ready(timeout=.2))
        self.assertTrue(await self.js('fixtureStart()'))
        await self.js('fixtureResolve(0,"same-scope-cover-resource");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['same-scope-cover-resource'])
        self.assertEqual(await self.js('fixture.createCalls.length'), 1)

    async def test_ready_barrier_rejects_unknown_or_wrong_scope_cover_creator_chain(self):
        _, observer = await self.install(cover=True)
        await self.js('''(() => {
          const current=__sauXHSCoverObserver.active;
          window.fixtureCoverScope=current;
          __sauXHSCoverObserver.active=Object.freeze({...current,observer_nonce:'other-scope'});
        })()''')
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertFalse(status['create_wrapped'])
        self.assertFalse(status['ready'])
        await self.js('''(() => {
          __sauXHSCoverObserver.active=fixtureCoverScope;
          const manager=_publisher.uploader,original=manager.createUploader;
          manager.createUploader=function(...args){return Reflect.apply(original,this,args);};
        })()''')
        self.assertFalse(await observer.ensure_ready(timeout=.05))
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        self.assertEqual(observer.upload_links(), [])

    async def test_ready_barrier_rejects_unsupported_creator_without_creating_uploader(self):
        await self.js('''(() => {
          fixtureLoad();window.fixtureReadonlyCreator=_publisher.uploader.createUploader;
          Object.defineProperty(_publisher.uploader,'createUploader',{
            value:fixtureReadonlyCreator,writable:false,configurable:false});
        })()''')
        _, observer = await self.install(load=False)
        self.assertFalse(await observer.ensure_ready(timeout=.05))
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        self.assertTrue(await self.js('_publisher.uploader.createUploader===fixtureReadonlyCreator'))
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertTrue(status['manager_available'])
        self.assertFalse(status['create_wrapped'])
        self.assertFalse(status['ready'])

    async def test_closed_legacy_observer_upgrades_without_sdk_or_old_observer_calls(self):
        await self.js('''(() => {
          fixtureLoad();window.fixtureOldObserverCalls=0;
          window.fixtureOldObserver=window.__sauXHSSDKObserver={active:null,timer:null,
            pending:new Set(),managers:new Map(),uploaders:new Map(),
            configure(){fixtureOldObserverCalls++;},flush(){fixtureOldObserverCalls++;},close(){fixtureOldObserverCalls++;}};
        })()''')
        _, observer = await self.install(load=False)
        self.assertTrue(await observer.ensure_ready())
        self.assertTrue(await self.js('__sauXHSSDKObserver!==fixtureOldObserver&&__sauXHSSDKObserver.api_version===4'))
        self.assertEqual(await self.js('fixtureOldObserverCalls'), 0)
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        await self.js('fixtureStart();fixtureResolve(0,"new-worker-resource");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['new-worker-resource'])

    async def test_live_legacy_observer_is_never_replaced_or_reconfigured(self):
        await self.js('''(() => {
          fixtureLoad();window.fixtureOldObserverCalls=0;
          window.fixtureOldObserver=window.__sauXHSSDKObserver={active:{observer_nonce:'legacy-live'},timer:null,
            pending:new Set(),managers:new Map(),uploaders:new Map(),
            configure(){fixtureOldObserverCalls++;},flush(){fixtureOldObserverCalls++;},close(){fixtureOldObserverCalls++;}};
        })()''')
        _, observer = await self.install(load=False)
        self.assertFalse(await observer.ensure_ready(timeout=.05))
        self.assertTrue(await self.js('__sauXHSSDKObserver===fixtureOldObserver&&__sauXHSSDKObserver.active.observer_nonce==="legacy-live"'))
        self.assertEqual(await self.js('fixtureOldObserverCalls'), 0)
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        self.assertEqual(observer.upload_links(), [])
        # Close only our idle Python observer after assertions. The live legacy
        # SDK object's close is outside this fixture's ownership.
        await observer.transport.close()
        observer.closed = True

    async def test_layered_diagnostics_use_only_fixed_event_boolean_and_counts(self):
        captured = io.StringIO()
        with redirect_stdout(captured):
            _, observer = await self.install(job_id='private-job', nonce='private-nonce')
            self.assertTrue(await observer.ensure_ready())
            await self.js('fixtureStart("video-uploader",true);fixtureResolve(0,"private-resource-wrong");true')
            await self.settle(observer)
            self.assertEqual(observer.upload_links(), [])
            await self.js('fixtureStart();fixtureResolve(1,"private-resource-correct");true')
            await self.settle(observer)
        raw = captured.getvalue()
        reports = [json.loads(line.split('=', 1)[1]) for line in raw.splitlines() if line.startswith('DRAFT_DIAGNOSTIC=')]
        self.assertEqual({r['event'] for r in reports}, {'xhs_sdk_configured','xhs_sdk_ready','xhs_sdk_received','xhs_sdk_invoked','xhs_sdk_callback'})
        for report in reports:
            self.assertTrue(all(type(value) in (bool, int) for key, value in report.items() if key != 'event'))
        received = [r for r in reports if r['event'] == 'xhs_sdk_received']
        self.assertEqual([r['received_count'] for r in received], [1, 2])
        self.assertEqual([r['accepted_count'] for r in received], [0, 1])
        self.assertFalse(received[0]['asset_hash_match'])
        self.assertTrue(received[0]['asset_name_match'])
        self.assertTrue(received[0]['asset_size_match'])
        self.assertTrue(received[1]['asset_hash_match'])
        invoked = [r for r in reports if r['event'] == 'xhs_sdk_invoked'][-1]
        self.assertEqual(invoked['invoked_count'], 2)
        self.assertTrue(invoked['native_file'])
        self.assertTrue(invoked['promise'])
        for private in ('private-job','private-nonce','synthetic.mp4','private-resource','preview','sha256'):
            self.assertNotIn(private, raw)

    async def test_diagnostic_output_failure_preserves_sdk_promise_and_success(self):
        with patch('builtins.print', side_effect=BrokenPipeError('private-output-error')):
            _, observer = await self.install()
            self.assertTrue(await observer.ensure_ready())
            self.assertTrue(await self.js('fixtureStart()'))
            await self.js('fixtureResolve(0,"still-success");true')
            await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['still-success'])

    async def test_promise_metrics_distinguish_pending_from_success_without_waiting_for_sdk(self):
        _, observer = await self.install()
        await self.js('fixtureStart();true')
        await asyncio.wait_for(self.settle(observer), timeout=5)
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertEqual(status['handler_attached_count'], 1)
        self.assertEqual(status['fulfilled_count'], 0)
        self.assertEqual(status['rejected_count'], 0)
        self.assertEqual(status['digest_ready_count'], 1)
        self.assertEqual(status['digest_failed_count'], 0)
        self.assertTrue(status['digest_scope_match'])
        self.assertEqual(status['binding_called_count'], 0)
        self.assertEqual(observer.upload_links(), [])
        await self.js('fixtureResolve(0,"successful-result");true')
        self.assertTrue(await self.js('fixtureLast.returned.then(value=>value===fixture.resolved[0])'))
        await self.settle(observer)
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertEqual(status['fulfilled_count'], 1)
        self.assertTrue(status['fulfilled_scope_match'])
        self.assertTrue(status['result_object'])
        self.assertTrue(status['file_id_present'])
        self.assertTrue(status['file_id_string'])
        self.assertTrue(status['file_id_valid'])
        self.assertTrue(status['binding_present'])
        self.assertTrue(status['binding_scope_match'])
        self.assertEqual(status['binding_called_count'], 1)
        self.assertEqual(status['binding_fulfilled_count'], 1)
        self.assertEqual(status['binding_accepted_count'], 1)
        self.assertEqual(status['binding_not_accepted_count'], 0)
        self.assertEqual(status['binding_rejected_count'], 0)
        self.assertEqual(status['observer_error_count'], 0)
        self.assertEqual(len(observer.upload_links()), 1)

    async def test_promise_rejection_and_bad_result_shapes_have_distinct_safe_metrics(self):
        previous = None
        for mode in ('reject', 'missing-id', 'number-id', 'getter-throws'):
            with self.subTest(mode=mode):
                if previous:
                    await previous.close()
                _, observer = await self.install(job_id='shape-' + mode, nonce='nonce-' + mode)
                index = await self.js('fixture.pending.length')
                await self.js('fixtureStart();true')
                if mode == 'reject':
                    await self.js(f'fixtureReject({index});true')
                    self.assertTrue(await self.js(f'fixtureLast.returned.then(()=>false,error=>error===fixture.rejected[{index}])'))
                else:
                    shape = '{}' if mode == 'missing-id' else '{fileId:99}' if mode == 'number-id' else '{get fileId(){throw new Error("private-shape-error");}}'
                    await self.js(f'fixture.resolved[{index}]={shape};fixture.pending[{index}].resolve(fixture.resolved[{index}]);true')
                    self.assertTrue(await self.js(f'fixtureLast.returned.then(value=>value===fixture.resolved[{index}])'))
                await self.settle(observer)
                status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
                self.assertEqual(status['fulfilled_count'], 0 if mode == 'reject' else 1)
                self.assertEqual(status['rejected_count'], 1 if mode == 'reject' else 0)
                self.assertTrue(status['rejected_scope_match'] if mode == 'reject' else status['fulfilled_scope_match'])
                self.assertEqual(status['digest_ready_count'], 1)
                self.assertFalse(status['file_id_valid'])
                self.assertEqual(status['file_id_present'], mode in ('number-id', 'getter-throws'))
                self.assertEqual(status['observer_error_count'], int(mode == 'getter-throws'))
                self.assertEqual(status['binding_called_count'], 0)
                self.assertEqual(observer.upload_links(), [])
                previous = observer

    async def test_native_read_failure_is_diagnostic_without_changing_sdk_fulfilled_value(self):
        await self.js('''(() => {
          Blob.prototype.arrayBuffer=function(){return Promise.reject(new Error('private-native-read-error'));};
        })()''')
        _, observer = await self.install()
        await self.js('fixtureStart();fixtureResolve(0,"normal-sdk-result");true')
        self.assertTrue(await self.js('fixtureLast.returned.then(value=>value===fixture.resolved[0])'))
        await self.settle(observer)
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
        self.assertEqual(status['fulfilled_count'], 1)
        self.assertTrue(status['file_id_valid'])
        self.assertEqual(status['digest_ready_count'], 0)
        self.assertEqual(status['digest_failed_count'], 1)
        self.assertTrue(status['digest_scope_match'])
        self.assertEqual(status['binding_called_count'], 0)
        self.assertEqual(observer.upload_links(), [])

    async def test_missing_or_rejected_binding_does_not_change_original_sdk_result(self):
        previous = None
        for mode in ('missing', 'rejected', 'declined'):
            with self.subTest(mode=mode):
                if previous:
                    await previous.close()
                _, observer = await self.install(job_id='binding-' + mode, nonce='nonce-' + mode)
                binding = 'window[' + json.dumps(observer.binding_name) + ']'
                await self.js('window.fixtureOriginalBinding=' + binding + ';true')
                replacement = {'missing': 'undefined', 'rejected': '()=>Promise.reject(new Error("private-binding-error"))',
                    'declined': '()=>Promise.resolve(false)'}[mode]
                await self.js(binding + '=' + replacement + ';true')
                index = await self.js('fixture.pending.length')
                await self.js(f'fixtureStart();fixtureResolve({index},"normal-result");true')
                self.assertTrue(await self.js(f'fixtureLast.returned.then(value=>value===fixture.resolved[{index}])'))
                await self.settle(observer)
                status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(observer.observer_nonce) + ')')
                self.assertEqual(status['fulfilled_count'], 1)
                self.assertEqual(status['digest_ready_count'], 1)
                self.assertEqual(status['binding_present'], mode != 'missing')
                self.assertEqual(status['binding_called_count'], int(mode != 'missing'))
                self.assertEqual(status['binding_rejected_count'], int(mode == 'rejected'))
                self.assertEqual(status['binding_fulfilled_count'], int(mode == 'declined'))
                self.assertEqual(status['binding_accepted_count'], 0)
                self.assertEqual(status['binding_not_accepted_count'], int(mode == 'declined'))
                self.assertEqual(observer.upload_links(), [])
                await self.js(binding + '=fixtureOriginalBinding;true')
                previous = observer

    async def test_unique_transport_probe_requires_current_nonce_and_observer(self):
        _, observer = await self.install()
        binding = 'window[' + json.dumps(observer.binding_name) + ']'
        probe = {'v': 1, 'probe': True, **observer.scope}
        self.assertTrue(await self.js(binding + '(' + json.dumps(probe) + ')'))
        self.assertFalse(await self.js(binding + '(' + json.dumps({**probe, 'observer_nonce': 'other-worker'}) + ')'))
        self.assertFalse(await self.js(binding + '(' + json.dumps({**probe, 'unexpected': True}) + ')'))
        current_session = self.page._sau_draft_evidence
        self.page._sau_draft_evidence = object()
        self.assertFalse(await self.js(binding + '(' + json.dumps(probe) + ')'))
        self.page._sau_draft_evidence = current_session
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.received_count, 0)
        await observer.close()
        self.assertFalse(await self.js(binding + '(' + json.dumps(probe) + ')'))

    async def add_child_frame(self):
        await self.js("""(() => {
          const child=document.createElement('iframe');child.id='fixture-child';
          child.srcdoc='<!doctype html><body>Local transport child';document.body.append(child);
        })()""")
        for _ in range(100):
            children = [frame for frame in self.page.frames if frame.parent_frame is self.page.main_frame]
            if len(children) == 1 and await children[0].evaluate('document.readyState', isolated_context=False) == 'complete':
                return children[0]
            await asyncio.sleep(.01)
        self.fail('local child fixture did not load')

    async def test_child_default_evaluation_does_not_reassign_main_transport(self):
        child = await self.add_child_frame()
        mapped_sources = []

        async def legacy_probe(source):
            mapped_sources.append(source['frame'] is self.page.main_frame)
            return True

        await self.page.expose_binding('__fixtureLegacySourceProbe', legacy_probe)
        await self.js('__fixtureLegacySourceProbe()')
        self.assertFalse(await child.evaluate('window===top', isolated_context=False))
        self.assertTrue(await self.js('window===top'))
        await self.js('__fixtureLegacySourceProbe()')
        # This is the pinned Patchright regression: a top-window binding can
        # report the child. Our own CDP session never consumes that mapping.
        self.assertFalse(mapped_sources[-1])
        _, observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=.2))
        self.assertFalse(await child.evaluate('window===top', isolated_context=False))
        self.assertTrue(await observer.ensure_ready(timeout=.2))
        self.assertTrue(await self.js('fixtureStart()'))
        await self.js('fixtureResolve(0,"main-after-child-read");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['main-after-child-read'])
        self.assertEqual(observer.accepted_count, 1)

    async def test_native_transport_rejects_child_isolated_unknown_and_old_channel(self):
        child = await self.add_child_frame()
        _, observer = await self.install()
        self.assertTrue(await observer.ensure_ready(timeout=.2))
        transport = observer.transport
        now = time.time()
        report = {'v': 1, **observer.scope, 'invocation_id': '1', 'uploader': 'video-uploader',
                  'name': 'synthetic.mp4', 'size': len(self.video_bytes), 'type': 'video/mp4',
                  'sha256': self.sha256, 'file_id': 'fixture-private-resource',
                  'started_at': now, 'completed_at': now}
        message = {'v': 1, 'kind': 'video', 'sequence': '1', 'observer_nonce': observer.observer_nonce,
                   'channel_nonce': transport.channel_nonce, 'report': report}
        event = {'name': transport.raw_name, 'executionContextId': transport.context_id,
                 'payload': json.dumps(message)}
        child_ids = [key for key, value in transport.contexts.items()
                     if value.get('isDefault') is True and value.get('frameId') != transport.root_frame]
        self.assertEqual(len(child_ids), 1)
        isolated = await transport.client.send('Page.createIsolatedWorld',
            {'frameId': transport.root_frame, 'worldName': 'fixture-foreign-observer-world'})
        isolated_id = isolated['executionContextId']
        for wrong_context in (child_ids[0], isolated_id, -1):
            self.assertFalse(await transport._dispatch({**event, 'executionContextId': wrong_context}))
        for changes in ({'observer_nonce': 'stale-observer'}, {'channel_nonce': 'old-context-channel'},
                        {'unexpected': True}):
            self.assertFalse(await transport._dispatch({**event, 'payload': json.dumps({**message, **changes})}))
        for changes in ({'job_id': 'foreign-job'}, {'receipt_nonce': 'foreign-receipt'},
                        {'observer_nonce': 'foreign-observer'}):
            self.assertFalse(await transport._dispatch({**event,
                'payload': json.dumps({**message, 'report': {**report, **changes}})}))
        current = self.page._sau_draft_evidence
        self.page._sau_draft_evidence = object()
        self.assertFalse(await transport._dispatch(event))
        self.page._sau_draft_evidence = current
        self.assertEqual(observer.upload_links(), [])
        self.assertFalse(observer.accept({'page': object(), 'frame': self.page.main_frame}, report))
        self.assertEqual(observer.records, {})
        # A child really calling an identically named CDP binding cannot enter
        # the channel, even with all current metadata/nonces. Only this local
        # test registers the name in the child to exercise the native event.
        await transport.client.send('Runtime.addBinding', {'name': transport.raw_name,
            'executionContextId': child_ids[0]})
        await child.evaluate('window[' + json.dumps(transport.raw_name) + '](' + json.dumps(json.dumps(message)) + ');true',
            isolated_context=False)
        await asyncio.sleep(.02)
        self.assertEqual(observer.records, {})
        self.assertTrue(await self.js('fixtureStart()'))
        await self.js('fixtureResolve(0,"actual-main-completion");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['actual-main-completion'])

    async def test_native_context_channel_changes_on_navigation(self):
        _, observer = await self.install()
        transport = observer.transport
        old_context, old_nonce = transport.context_id, transport.channel_nonce
        await self.page.goto('http://127.0.0.1:45999/xhs-new-native-context')
        await observer.drain()
        await self.js('fixtureLoad();true')
        self.assertTrue(await observer.ensure_ready(timeout=.2))
        self.assertNotEqual(transport.channel_nonce, old_nonce)
        probe = {'v': 1, 'probe': True, **observer.scope}
        stale = {'v': 1, 'kind': 'video', 'sequence': '1', 'observer_nonce': observer.observer_nonce,
                 'channel_nonce': old_nonce, 'report': probe}
        self.assertFalse(await transport._dispatch({'name': transport.raw_name,
            'executionContextId': old_context, 'payload': json.dumps(stale)}))
        self.assertFalse(await transport._dispatch({'name': transport.raw_name,
            'executionContextId': transport.context_id, 'payload': json.dumps(stale)}))
        self.assertEqual(observer.records, {})
        self.assertTrue(await self.js('window[' + json.dumps(observer.binding_name) + '](' + json.dumps(probe) + ')'))

    async def test_rejected_source_and_scope_reports_are_diagnosed_without_values(self):
        _, observer = await self.install(job_id='private-diagnostic-job', nonce='private-diagnostic-nonce')
        report = {'v': 1, **observer.scope, 'invocation_id': '1', 'uploader': 'video-uploader',
                  'name': 'synthetic.mp4', 'size': len(self.video_bytes), 'type': 'video/mp4',
                  'sha256': self.sha256, 'file_id': 'private-diagnostic-resource',
                  'started_at': time.time(), 'completed_at': time.time()}
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertFalse(observer.accept({'page': self.page, 'frame': object()}, report))
            self.assertFalse(observer.accept({'page': self.page, 'frame': self.page.main_frame},
                {**report, 'observer_nonce': 'private-other-observer'}))
        events = [json.loads(line.split('=', 1)[1]) for line in output.getvalue().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertFalse(events[0]['source_frame_match'])
        self.assertTrue(events[0]['scope_observer_match'])
        self.assertTrue(events[1]['source_frame_match'])
        self.assertFalse(events[1]['scope_observer_match'])
        self.assertTrue(all(event['accepted'] is False for event in events))
        self.assertTrue(all(type(value) in (bool, int) for event in events
            for key, value in event.items() if key != 'event'))
        for private in ('private-diagnostic-job', 'private-diagnostic-nonce', 'private-diagnostic-resource',
                        'private-other-observer', self.sha256, 'synthetic.mp4'):
            self.assertNotIn(private, output.getvalue())
        self.assertEqual(observer.records, {})

    async def test_late_fulfillment_after_close_is_counted_only_in_retired_scope(self):
        _, old = await self.install()
        await self.js('fixtureStart();window.fixtureRetiredMetrics=__sauXHSSDKObserver.metrics;true')
        await self.settle(old)
        await old.close()
        _, current = await self.install(job_id='new-task', nonce='new-nonce', load=False)
        await self.js('fixtureResolve(0,"late-old-result");true')
        await self.settle(current)
        retired = await self.js('({fulfilled_count:fixtureRetiredMetrics.fulfilled_count,fulfilled_scope_match:fixtureRetiredMetrics.fulfilled_scope_match,closed:fixtureRetiredMetrics.closed,binding_called_count:fixtureRetiredMetrics.binding_called_count})')
        self.assertEqual(retired, {'fulfilled_count': 1,'fulfilled_scope_match': False,'closed': True,'binding_called_count': 0})
        status = await self.js('__sauXHSSDKObserver.status(' + json.dumps(current.observer_nonce) + ')')
        self.assertEqual(status['fulfilled_count'], 0)
        self.assertEqual(status['binding_called_count'], 0)
        self.assertEqual(old.records, {})
        self.assertEqual(current.upload_links(), [])

    async def test_existing_sdk_complete_file_hash_and_exact_promise_semantics(self):
        _, observer = await self.install()
        self.assertTrue(observer.available)
        self.assertEqual(await self.js('fixture.createCalls.length'), 0)
        self.assertTrue(await self.js('fixtureStart()'))
        semantics = await self.js('''({
          receiver:fixture.calls[0].receiver===fixtureLast.uploader,
          options:fixture.calls[0].options===fixtureLast.options,
          file:fixture.calls[0].file===fixtureLast.file,
          rest:fixture.calls[0].rest[0]==='third-argument',
          creatorReceiver:fixture.createCalls[0].receiver===_publisher.uploader,
          creatorOptions:fixture.createCalls[0].options===fixtureLast.creatorOptions
        })''')
        self.assertTrue(all(semantics.values()))
        await self.js('fixtureResolve(0);true')
        self.assertTrue(await self.js('fixtureLast.returned.then(value=>value===fixture.resolved[0])'))
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [{'kind': 'video', 'sha256': self.sha256,
            'ids': ['fixture-resource'], 'complete': True}])
        self.assertEqual(len(observer.records), 1)
        self.assertEqual(set(observer.records['1']), {'v','job_id','receipt_nonce','observer_nonce','invocation_id',
            'uploader','name','size','type','sha256','file_id','started_at','completed_at'})
        self.assertEqual(len(self.requests), 1)

    async def test_native_file_full_sha_matches_hashlib_at_padding_and_stream_boundaries(self):
        previous = None
        # Both SHA block padding boundaries and bounded native 1 MiB reads.
        for length in (1, 55, 56, 63, 64, 65, 127, 128, 129, 1048575, 1048576, 1048577, 2097153):
            with self.subTest(length=length):
                if previous is not None:
                    await previous.close()
                self.video_bytes = (bytes(range(251)) * ((length + 250) // 251))[:length]
                self.sha256 = hashlib.sha256(self.video_bytes).hexdigest()
                _, observer = await self.install(job_id=f'sha-{length}', nonce=f'nonce-{length}')
                index = await self.js('fixture.pending.length')
                self.assertTrue(await self.js('''(() => {
                  const length=''' + str(length) + ''',bytes=new Uint8Array(length);
                  for(let i=0;i<length;i++)bytes[i]=i%251;
                  const file=new File([bytes],'synthetic.mp4',{type:'video/mp4'});
                  const uploader=_publisher.uploader.createUploader('video-uploader',{});
                  const returned=uploader.upload(file,{});
                  return file instanceof File&&returned===fixture.pending.at(-1).promise;
                })()'''))
                await self.js(f'fixtureResolve({index},"sha-resource-{length}");true')
                await self.settle(observer)
                self.assertEqual(observer.upload_links(), [{'kind': 'video', 'sha256': self.sha256,
                    'ids': [f'sha-resource-{length}'], 'complete': True}])
                self.assertEqual(next(iter(observer.records.values()))['size'], length)
                previous = observer

    async def test_fake_native_file_and_promise_brands_never_generate_positive_evidence(self):
        _, observer = await self.install()
        self.assertTrue(await self.js('''(() => {
          const uploader=_publisher.uploader.createUploader('video-uploader',{});
          const fake=Object.create(File.prototype);
          Object.defineProperties(fake,{name:{value:'synthetic.mp4'},size:{value:1101078},
            type:{value:'video/mp4'},arrayBuffer:{value:()=>fixtureFile().arrayBuffer()}});
          const returned=uploader.upload(fake,{});
          fixtureResolve(0,'fake-native-file');
          return fake instanceof File&&returned===fixture.pending[0].promise&&fixture.calls[0].file===fake;
        })()'''))
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertTrue(await self.js('''(() => {
          const uploader=_publisher.uploader.getUploader('video-uploader');
          const fakePromise=Object.create(Promise.prototype),file=fixtureFile();
          const returned=uploader.upload(file,{returnedObject:fakePromise});
          return fakePromise instanceof Promise&&returned===fakePromise;
        })()'''))
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertTrue(await self.js('''(() => {
          const uploader=_publisher.uploader.getUploader('video-uploader');
          fixture.thenableCalls=0;const thenable={then(){fixture.thenableCalls++;}};
          return uploader.upload(fixtureFile(),{returnedObject:thenable})===thenable;
        })()'''))
        await self.settle(observer)
        self.assertEqual(await self.js('fixture.thenableCalls'), 0)
        self.assertEqual(observer.upload_links(), [])

    async def test_nested_shadow_inputs_and_composed_events_are_observed_once(self):
        _, observer = await self.install()
        for index, composed in enumerate((False, True)):
            self.assertTrue(await self.js('fixtureShadow(' + str(composed).lower() + ')'))
            await self.js('fixtureResolve(' + str(index) + ',"fixture-shadow-' + str(index) + '");true')
            await self.settle(observer)
            self.assertEqual(len(observer.upload_links()), index + 1)
        self.assertEqual({link['sha256'] for link in observer.upload_links()}, {self.sha256})

    async def test_rejection_and_synchronous_throw_are_preserved_without_success(self):
        _, observer = await self.install()
        self.assertTrue(await self.js('fixtureStart()'))
        await self.js('fixtureReject(0);true')
        self.assertTrue(await self.js('fixtureLast.returned.then(()=>false,error=>error===fixture.rejected[0])'))
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertTrue(await self.js('''(() => {
          const error=new Error('same synchronous error'),options={syncThrow:error};
          const uploader=_publisher.uploader.getUploader('video-uploader');
          try{uploader.upload(fixtureFile(),options);}catch(caught){return caught===error;}
          return false;
        })()'''))
        await observer.drain()
        self.assertEqual(observer.upload_links(), [])

    async def test_old_unobserved_completion_cannot_be_retroactively_claimed(self):
        await self.js('fixtureLoad();fixtureStart();true')
        _, observer = await self.install(load=False)
        await self.js('fixtureResolve(0);true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertTrue(await self.js('fixtureStart("not-transcode-video-uploader")'))
        await self.js('fixtureResolve(1,"new-resource");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['new-resource'])

    async def test_task_switch_and_nonce_switch_reject_late_completion(self):
        old_session, old = await self.install()
        await self.js('fixtureStart();true')
        # Deliberately reuse the same job ID; nonce and observer ownership matter.
        new_session, current = await self.install(job_id='job-a', nonce='nonce-b', load=False)
        await self.js('fixtureResolve(0,"old-resource");true')
        await self.settle(current)
        self.assertEqual(old.records, {})
        self.assertEqual(current.upload_links(), [])
        await old.close()
        # Closing the old observer must not disable the new observer's wrapper.
        await self.js('fixtureStart();fixtureResolve(1,"current-resource");true')
        await self.settle(current)
        self.assertEqual(current.upload_links()[0]['ids'], ['current-resource'])
        self.assertIs(self.page._sau_draft_evidence, new_session)
        self.assertIsNot(self.page._sau_draft_evidence, old_session)

    async def test_new_task_drain_does_not_wait_for_old_file_fingerprint(self):
        # Simulate a slow, asynchronous native read without changing its bytes.
        # The old read stays unresolved until the new observer has drained.
        await self.js('''(() => {
          const original=Blob.prototype.arrayBuffer;
          window.fixtureHoldNext=true;
          Blob.prototype.arrayBuffer=function(){
            if(fixtureHoldNext){
              fixtureHoldNext=false;
              return new Promise((resolve,reject)=>{
                window.fixtureReleaseRead=()=>Reflect.apply(original,this,[]).then(resolve,reject);
              });
            }
            return Reflect.apply(original,this,[]);
          };
        })()''')
        _, old = await self.install()
        await self.js('fixtureStart();true')
        until = asyncio.get_running_loop().time() + 5
        while not await self.js('typeof fixtureReleaseRead==="function"'):
            if asyncio.get_running_loop().time() >= until:
                self.fail('fixture asynchronous read never started')
            await asyncio.sleep(.01)
        _, current = await self.install(job_id='job-b', nonce='nonce-b', load=False)
        await asyncio.wait_for(current.drain(), timeout=5)
        self.assertEqual(current.upload_links(), [])
        await self.js('fixtureResolve(0,"old-resource");fixtureReleaseRead();true')
        await asyncio.sleep(.05)
        await self.js('fixtureStart();fixtureResolve(1,"new-resource");true')
        await self.settle(current)
        self.assertEqual(old.records, {})
        self.assertEqual(current.upload_links()[0]['ids'], ['new-resource'])

    async def test_same_name_and_size_with_different_contents_cannot_verify(self):
        _, observer = await self.install()
        await self.js('fixtureStart("video-uploader",true);fixtureResolve(0,"wrong-file");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        await self.js('fixtureStart();fixtureResolve(1,"right-file");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['right-file'])

    async def test_file_instance_slice_override_cannot_forge_original_bytes(self):
        _, observer = await self.install()
        await self.js('''(() => {
          const uploader=_publisher.uploader.createUploader('video-uploader',{});
          const wrong=fixtureFile(true),right=fixtureFile();
          wrong.slice=(...args)=>right.slice(...args);
          wrong.arrayBuffer=()=>right.arrayBuffer();
          uploader.upload(wrong,{});fixtureResolve(0,'forged-fingerprint');
        })()''')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])

    async def test_borrowed_upload_receiver_and_readonly_method_stay_unmodified(self):
        _, observer = await self.install()
        semantics = await self.js('''(() => {
          const uploader=_publisher.uploader.createUploader('video-uploader',{}),receiver={borrowed:true};
          const returned=uploader.upload.call(receiver,fixtureFile(),{});
          return {samePromise:returned===fixture.pending[0].promise,sameReceiver:fixture.calls[0].receiver===receiver};
        })()''')
        self.assertTrue(all(semantics.values()))
        await self.js('fixtureResolve(0,"borrowed-call");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        # An unsupported SDK descriptor must stay unchanged and unverified.
        await observer.close()
        await self.js('''(() => {
          const uploader=_publisher.uploader.createUploader('not-transcode-video-uploader',{});
          window.fixtureReadonly=uploader.upload;
          Object.defineProperty(uploader,'upload',{value:fixtureReadonly,writable:false,configurable:false});
        })()''')
        _, current = await self.install(job_id='job-b', nonce='nonce-b', load=False)
        self.assertTrue(await self.js('_publisher.uploader.getUploader("not-transcode-video-uploader").upload===fixtureReadonly'))
        await self.js('fixtureStart("not-transcode-video-uploader");fixtureResolve(1,"readonly-sdk");true')
        await self.settle(current)
        self.assertEqual(current.upload_links(), [])

    async def test_missing_invalid_or_error_response_file_id_has_no_positive_link(self):
        _, observer = await self.install()
        for expression in ('undefined', 'null', 'true', '99', '""'):
            await self.js('fixtureStart();fixture.pending.at(-1).resolve({fileId:' + expression + '});true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])

    async def test_image_or_transformed_cover_is_not_video_or_original_cover_evidence(self):
        _, observer = await self.install()
        await self.js('''(() => {
          const image=_publisher.uploader.createUploader('image-uploader',{scene:'image'});
          image.upload(new File(['transformed JPEG'],'cover.jpeg',{type:'image/jpeg'}),{});
          fixtureResolve(0,'cover-resource');
        })()''')
        await self.settle(observer)
        self.assertEqual(observer.upload_links(), [])
        self.assertEqual(observer.records, {})

    async def test_close_restores_methods_and_late_completion_does_not_leak(self):
        _, observer = await self.install()
        await self.js('fixtureStart();true')
        await observer.close()
        await self.js('fixtureResolve(0);true')
        await asyncio.sleep(.1)
        self.assertEqual(observer.records, {})
        self.assertTrue(await self.js('''(() => {
          const root=__sauXHSSDKObserver;
          return root.active===null&&root.timer===null&&root.uploaders.size===0&&root.managers.size===0&&
            !Object.hasOwn(_publisher.uploader,'createUploader');
        })()'''))

    async def test_reinjection_is_idempotent_and_sdk_pending_does_not_block_drain(self):
        _, observer = await self.install()
        await self.js(SDK_OBSERVER_SCRIPT)
        await self.js('fixtureStart();true')
        await asyncio.wait_for(observer.drain(), timeout=10)
        self.assertEqual(observer.upload_links(), [])
        await self.js('fixtureResolve(0);true')
        await self.settle(observer)
        self.assertEqual(len(observer.records), 1)
        self.assertEqual(len(observer.upload_links()), 1)
        self.assertEqual(await self.js('fixture.createCalls.length'), 1)

    async def test_navigation_installs_only_into_current_main_frame(self):
        _, observer = await self.install()
        await self.page.goto('http://127.0.0.1:45999/xhs-sdk-second')
        await observer.drain()
        await self.js('fixtureLoad();true')
        await asyncio.sleep(.25)
        await self.js('fixtureStart();fixtureResolve(0,"after-navigation");true')
        await self.settle(observer)
        self.assertEqual(observer.upload_links()[0]['ids'], ['after-navigation'])

    async def test_binding_rejects_stale_extra_fields_bad_times_and_conflicting_identity(self):
        _, observer = await self.install()
        now = time.time()
        valid = {'v':1, **observer.scope, 'invocation_id':'1','uploader':'video-uploader',
                 'name':'synthetic.mp4','size':len(self.video_bytes),'type':'video/mp4',
                 'sha256':self.sha256,'file_id':'fixture-resource','started_at':now,'completed_at':now}
        source = {'frame': self.page.main_frame}
        for changes in ({'job_id':'other-job'}, {'receipt_nonce':'wrong'}, {'observer_nonce':'wrong'},
                        {'raw_payload':'must not be accepted'}, {'size':True}, {'completed_at':float('nan')},
                        {'started_at':observer.started_at - 30}, {'completed_at':now - 30},
                        {'file_id':True}, {'uploader':'image-uploader'}):
            self.assertFalse(observer.accept(source, {**valid, **changes}))
        self.assertFalse(observer.accept({'frame': object()}, valid))
        self.assertTrue(observer.accept(source, valid))
        self.assertTrue(observer.accept(source, dict(valid)))
        self.assertFalse(observer.accept(source, {**valid, 'file_id':'conflicting-resource'}))
        self.assertEqual(observer.upload_links(), [])
        self.assertFalse(observer.accept(source, valid))

    async def test_non_xhs_platform_is_noop(self):
        session = SimpleNamespace(platform='bilibili')
        self.assertIsNone(await install_xhs_observer(session))
        self.assertFalse(await self.js('!!window.__sauXHSSDKObserver'))

    async def test_two_cdp_workers_deliver_only_to_current_observer(self):
        await self.run_two_cdp_workers(keep_old_client=False)

    async def test_two_overlapping_cdp_workers_deliver_only_to_current_observer(self):
        await self.run_two_cdp_workers(keep_old_client=True)

    async def run_two_cdp_workers(self, keep_old_client):
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        bundled_shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        launch = {'executable_path': str(bundled_shell)} if bundled_shell.exists() else {'channel': 'chrome'}
        browser = await self.playwright.chromium.launch(headless=True,
            args=[f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1'], **launch)
        clients, observers = [], []
        try:
            context = await browser.new_context()
            await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
            page = await context.new_page()
            await page.goto('http://127.0.0.1:45999/xhs-cdp-fixture')
            await page.evaluate('fixtureLoad();true', isolated_context=False)
            await page.evaluate("""(() => {
              const child=document.createElement('iframe');child.srcdoc='<!doctype html><body>Local CDP child';
              document.body.append(child);
            })()""", isolated_context=False)
            old_records = None
            for phase in ('a', 'b'):
                client = await async_playwright().start()
                clients.append(client)
                connected = await client.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
                targets = [target for scope in connected.contexts for target in scope.pages
                           if target.url.endswith('/xhs-cdp-fixture')]
                self.assertEqual(len(targets), 1)
                target = targets[0]
                session = SimpleNamespace(page=target, platform='xiaohongshu', closed=False,
                    expected={'job_id': 'cdp-job-' + phase, 'receipt_nonce': 'cdp-nonce-' + phase,
                              'video_sha256': self.sha256},
                    assets={'video': {'kind': 'video', 'name': 'synthetic.mp4',
                            'size': len(self.video_bytes), 'sha256': self.sha256}})
                target._sau_draft_evidence = session
                observer = await install_xhs_observer(session)
                observers.append(observer)
                self.assertTrue(await observer.ensure_ready(timeout=1))
                children = [frame for frame in target.frames if frame.parent_frame is target.main_frame]
                self.assertEqual(len(children), 1)
                self.assertFalse(await children[0].evaluate('window===top', isolated_context=False))
                self.assertTrue(await observer.ensure_ready(timeout=1))
                await target.evaluate('fixtureStart();fixtureResolve(fixture.pending.length-1,"cdp-resource");true', isolated_context=False)
                await self.settle(observer)
                self.assertEqual(len(observer.upload_links()), 1, phase)
                if old_records is not None:
                    self.assertEqual(old_records, dict(observers[0].records))
                old_records = dict(observer.records)
                await observer.close()
                if not keep_old_client:
                    await client.stop()
                    clients.remove(client)
        finally:
            for observer in observers:
                await observer.close()
            for client in clients:
                await client.stop()
            await browser.close()


MOJITO_FIXTURE = r'''<!doctype html><meta charset="utf-8"><style>
.main-cover-editor-modal{width:700px;height:460px}.upload-btn{display:block;width:200px;height:32px}
button{width:100px;height:32px}</style><div id="creator-publish-dom"></div>
<div class="main-cover-editor-modal cover-modal"><div class="d-modal-header"><h6>设置封面</h6><button class="d-modal-close">关闭</button></div>
<div class="cover-choose-container"><div class="upload-slot"><label class="upload-btn"><input type="file" class="upload-input" accept="image/*"></label></div></div>
<div class="editor-panel"><div class="bottom"><div class="bottom-content"><button class="mojito-button">完成</button></div></div></div></div>
<script>(()=>{
window.fixture={pending:[],calls:[],localCalls:[],localResults:[],exportCalls:0,paths:new Map(),fs:new Map(),
  cover:{fileid:'',selectionSource:'default'},draftCover:{fileid:'stale-draft',selectionSource:'upload'},layerCount:1};
const input=document.querySelector('.upload-input'),modal=document.querySelector('.main-cover-editor-modal');
const canvas=document.createElement('canvas');canvas.width=100;canvas.height=70;
let currentPath='default-frame';
const layer={getType:()=>({value:1}),getMainResource:()=>({path:currentPath})};
const renderer={getLayerCount:()=>fixture.layerCount,getLayerByIndex:()=>layer,
 saveJpeg(path){fixture.exportCalls++;const encoded=canvas.toDataURL('image/jpeg');
   const bytes=Uint8Array.from(atob(encoded.split(',')[1]),c=>c.charCodeAt(0));fixture.fs.set(path,bytes)},
 savePng(){throw Error('unsupported fixture export')}};
class Engine{
 constructor(){this.canvas=renderer;this.zeusModule={LayerType:{LT_IMAGE:{value:1}},
   Vector2:class{constructor(x,y){this.x=x;this.y=y}delete(){}},Rect:class{delete(){}},FS:{readFile:p=>fixture.fs.get(p)}};}
 getCanvas(){return this.canvas}
 getZeusModule(){return this.zeusModule}
 uploadLocalFile(file){fixture.localCalls.push({receiver:this,file});
   if(fixture.localThrow)throw fixture.localThrow;
   const promise=Promise.resolve().then(async()=>{const path='/Zeus/native-'+fixture.localCalls.length;
      fixture.paths.set(path,file);const result={success:true,path};fixture.localResults.push(result);return result});
   fixture.localPromise=promise;return promise;}
 async changeImageLayer(file){const promise=this.uploadLocalFile(file);fixture.localReturn=promise;
   const result=await promise;if(!result.success)return result;currentPath=result.path;
   const img=new Image(),url=URL.createObjectURL(file);img.src=url;await img.decode();
   canvas.getContext('2d').drawImage(img,0,0,100,70);URL.revokeObjectURL(url);return {success:true};}
}
Engine.prototype.getExportImage=__OFFICIAL_EXPORT__;
window.zeusEngine=new Engine();fixture.originalLocal=zeusEngine.uploadLocalFile;fixture.originalExport=zeusEngine.getExportImage;
const uploader={upload(file,options,...rest){fixture.calls.push({receiver:this,file,options,rest});
 if(options?.throw)throw options.throw;
 let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});
 fixture.pending.push({promise,resolve,reject,file});return promise;}};
const manager={getUploader:name=>name==='image-uploader'?uploader:null,
 createUploader(name){if(name!=='image-uploader')throw Error('unexpected SDK factory');return uploader}};
window._publisher={uploader:manager};
window.publish={app:{},el:document.querySelector('#creator-publish-dom'),
 getDraftState:()=>({cover:fixture.draftCover}),getContextState:()=>({coverContext:{cover:fixture.cover}})};
const source=document.createElement('canvas');source.width=100;source.height=70;
source.getContext('2d').fillStyle='rgb(15,80,190)';source.getContext('2d').fillRect(0,0,100,70);
source.getContext('2d').fillStyle='white';source.getContext('2d').fillRect(5,5,30,20);
const png=Uint8Array.from(atob(source.toDataURL('image/png').split(',')[1]),c=>c.charCodeAt(0));
window.fixtureFile=wrong=>{const bytes=png.slice();if(wrong)bytes[bytes.length-2]^=1;
 return new File([bytes],'native-cover.png',{type:'image/png'})};
window.fixtureAsset=async()=>{const f=fixtureFile(false),s=await crypto.subtle.digest('SHA-256',await f.arrayBuffer());
 return {kind:'cover',name:f.name,size:f.size,sha256:Array.from(new Uint8Array(s),x=>x.toString(16).padStart(2,'0')).join('')}};
input.addEventListener('change',()=>{fixture.selection=input.files[0];
 fixture.changed=zeusEngine.changeImageLayer(fixture.selection).catch(e=>fixture.changeError=e)});
window.fixtureSelect=wrong=>{const dt=new DataTransfer();dt.items.add(fixtureFile(wrong));input.files=dt.files;
 input.dispatchEvent(new Event('change',{bubbles:true,composed:true}));};
window.fixtureUpload=file=>{const options={preserved:true},ret=uploader.upload(file,options,'extra');
 fixture.last={file,returned:ret,options};ret.then(result=>{fixture.cover={fileid:fixture.wrongApplied?'other-cover':result.fileId,selectionSource:'upload'}},()=>{});
 return ret===fixture.pending.at(-1).promise};
window.fixtureExport=()=>zeusEngine.getExportImage(false,0,0,100,70,100,70);
document.querySelector('.mojito-button').onclick=()=>{queueMicrotask(()=>{
 fixtureUpload(fixture.fakeFile?{name:'saveImag.jpg',size:1,type:'image/jpg'}:fixtureExport());modal.style.display='none';})};
document.querySelector('.d-modal-close').onclick=()=>modal.style.display='none';
window.fixtureResolve=(id='final-cover')=>{const result={fileId:id,previewUrl:'fixture-only-preview',extra:{same:true}};
 fixture.result=result;fixture.pending.at(-1).resolve(result)};
window.fixtureReject=()=>{const error=new Error('SDK fixture failure');fixture.error=error;fixture.pending.at(-1).reject(error)};
})();</script>'''


class XHSMojitoEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from uploader.draft_evidence import FILE_OBSERVER_SCRIPT
        asyncio.get_running_loop().set_debug(False)
        self.pw = await async_playwright().start()
        shell = Path.home()/'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        options = {'executable_path':str(shell)} if shell.exists() else {'channel':'chrome'}
        self.browser = await self.pw.chromium.launch(headless=True, args=['--disable-gpu'], **options)
        self.page = await self.browser.new_page()
        self.observers, self.session = [], None
        self.requests = []
        library = Path(__file__).resolve().parents[1]/'research/acceptance-20261004/cover-origin-chain.sources/library-zeus-engine.1cc8bc98.js'
        source = library.read_text()
        begin = source.index('A.getExportImage=') + len('A.getExportImage=')
        end = source.index(',F=', begin)
        official_export = source[begin:end]
        self.assertEqual(hashlib.sha256(official_export.encode()).hexdigest(),
            'd2d474c418bd55bed04596ac55373d3991db05e9db730bb018cd8dbb038efa0a')
        self.html = MOJITO_FIXTURE.replace('__OFFICIAL_EXPORT__', official_export)
        async def serve(route):
            self.requests.append(route.request.url)
            await route.fulfill(content_type='text/html', body=self.html)
        await self.page.route('**/*', serve)
        async def selected(source, report):
            if self.session and source.get('frame') is self.page.main_frame:
                self.session.files.append(report)
        await self.page.expose_binding('__sauDraftSelected', selected)
        await self.page.add_init_script(FILE_OBSERVER_SCRIPT)
        await self.reset()

    async def reset(self):
        for observer in self.observers:
            await observer.close()
        self.session = None
        self.page._sau_draft_evidence = None
        await self.page.goto('http://127.0.0.1:45993/mojito-cover-fixture')

    async def js(self, expression):
        return await self.page.evaluate(expression, isolated_context=False)

    async def install(self, suffix='a'):
        asset = await self.js('fixtureAsset()')
        self.session = SimpleNamespace(page=self.page,platform='xiaohongshu',closed=False,files=[],
            expected={'job_id':'mojito-job-'+suffix,'receipt_nonce':'mojito-receipt-'+suffix,
                      'video_sha256':'0'*64,'cover_sha256':{'cover':asset['sha256']}},assets={'cover':asset})
        self.page._sau_draft_evidence = self.session
        observer = await install_xhs_observer(self.session)
        self.observers.append(observer)
        self.assertTrue(await observer.ensure_cover_ready(timeout=5))
        return observer

    async def select(self, observer):
        await self.js('fixtureSelect(false);true')
        await self.js('fixture.changed')
        await observer.drain()
        deadline = asyncio.get_running_loop().time()+2
        while not self.session.files and asyncio.get_running_loop().time()<deadline:
            await asyncio.sleep(.01)
        self.assertEqual(len(self.session.files),1)

    async def complete(self):
        await self.page.locator('.mojito-button').click()
        deadline = asyncio.get_running_loop().time()+2
        while not await self.js('fixture.pending.length>0') and asyncio.get_running_loop().time()<deadline:
            await asyncio.sleep(.01)
        self.assertTrue(await self.js('fixture.pending.length>0'))

    async def settle(self, observer):
        await observer.drain()
        await asyncio.sleep(.02)
        await observer.drain()

    async def asyncTearDown(self):
        for observer in self.observers:
            await observer.close()
        await self.browser.close()
        await self.pw.stop()

    async def test_native_png_fs_resource_actual_official_export_sdk_and_effective_context(self):
        observer = await self.install()
        await self.select(observer)
        self.assertTrue(await observer.ensure_cover_selection_ready(timeout=5))
        self.assertTrue(await self.js('fixture.localReturn===fixture.localPromise'))
        await self.complete()
        self.assertTrue(await self.js('fixture.last.returned===fixture.pending[0].promise'))
        self.assertTrue(await self.js('fixture.calls[0].file===fixture.last.file&&fixture.calls[0].options===fixture.last.options&&fixture.calls[0].rest[0]==="extra"'))
        await self.js('fixtureResolve();true')
        await self.settle(observer)
        self.assertEqual(observer.cover_upload_links(),[{'kind':'cover','sha256':self.session.assets['cover']['sha256'],
            'ids':['final-cover'],'complete':True}])
        record = observer.cover_transforms()[0]
        self.assertNotEqual(record['origin_sha256'],record['derived_sha256'])
        derived = await self.js("(async()=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await fixture.last.file.arrayBuffer())),x=>x.toString(16).padStart(2,'0')).join(''))()")
        self.assertEqual(record['derived_sha256'],derived)
        self.assertTrue(await self.js('fixture.pending[0].promise.then(value=>value===fixture.result)'))
        await observer.close()
        self.assertTrue(await self.js('zeusEngine.uploadLocalFile===fixture.originalLocal&&zeusEngine.getExportImage===fixture.originalExport'))

    async def test_preview_sdk_only_mixed_layer_wrong_resource_cancel_and_task_switch_rejected(self):
        for scenario in ('preview','sdk_only','mixed','wrong_resource','cancel','task_switch','changed_source','fake'):
            with self.subTest(scenario=scenario):
                await self.reset()
                observer = await self.install(scenario.replace('_','-'))
                await self.select(observer)
                if scenario == 'preview':
                    await self.js('fixtureUpload(fixtureExport());true')
                elif scenario == 'sdk_only':
                    await self.js('fixtureUpload(fixture.selection);true')
                else:
                    if scenario == 'mixed':
                        await self.js('fixture.layerCount=2')
                        self.assertFalse(await observer.ensure_cover_selection_ready(timeout=.1))
                    if scenario == 'wrong_resource':
                        await self.js('fixture.wrongApplied=true')
                    if scenario == 'fake':
                        await self.js('fixture.fakeFile=true')
                    await self.complete()
                    if scenario == 'cancel':
                        await self.js("document.querySelector('.main-cover-editor-modal').style.display='block';document.querySelector('.d-modal-close').click();true")
                    elif scenario == 'task_switch':
                        await observer.close()
                        await self.js("document.querySelector('.main-cover-editor-modal').style.display='block'")
                        observer = await self.install('replacement')
                    elif scenario == 'changed_source':
                        await self.js("document.querySelector('.main-cover-editor-modal').style.display='block';fixtureSelect(false);true")
                        await self.js('fixture.changed')
                await self.js('fixtureResolve();true')
                await self.settle(observer)
                self.assertEqual(observer.cover_upload_links(),[])
                self.assertEqual(observer.cover_transforms(),[])

    async def test_failure_sync_throw_and_unsupported_engine_leave_original_semantics(self):
        observer = await self.install()
        await self.select(observer)
        self.assertTrue(await self.js("(()=>{const e=new Error('unchanged throw');fixture.localThrow=e;try{zeusEngine.uploadLocalFile(fixture.selection)}catch(error){return error===e}})()"))
        await self.complete()
        await self.js('fixture.last.returned.catch(error=>fixture.callerError=error);fixtureReject();true')
        await self.settle(observer)
        self.assertTrue(await self.js('fixture.callerError===fixture.error'))
        self.assertEqual(observer.cover_upload_links(),[])
        await observer.close()
        await self.js("document.querySelector('.main-cover-editor-modal').style.display='block';zeusEngine={getCanvas(){return {}},getZeusModule(){return {}},uploadLocalFile(){return Promise.resolve({success:true,path:'fake'})},getExportImage(){return fixtureFile(false)}}")
        # Installation wraps neither unsupported prototype nor forged methods.
        asset = await self.js('fixtureAsset()')
        self.session = SimpleNamespace(page=self.page,platform='xiaohongshu',closed=False,files=[],
            expected={'job_id':'unsupported','receipt_nonce':'unsupported-receipt','video_sha256':'0'*64,
                      'cover_sha256':{'cover':asset['sha256']}},assets={'cover':asset})
        self.page._sau_draft_evidence = self.session
        observer = await install_xhs_observer(self.session)
        self.observers.append(observer)
        self.assertFalse(await observer.ensure_cover_ready(timeout=.1))
        self.assertEqual(observer.upload_links(),[])


if __name__ == '__main__':
    unittest.main()
