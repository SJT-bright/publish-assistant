"""Isolated normal-wire and native React/canvas fixtures, no real accounts."""
import asyncio
import base64
import copy
import hashlib
import io
import json
import socket
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright
from uploader.ks_evidence import (GENERATE_IMAGE_SOURCE, KSEvidenceObserver, _pre_gates, _safe_crop_diagnostics, ks_endpoint)


def reply(operation, body, when, status=200, method='POST', binary=None):
    paths = {'pre': 'v2/video/pc/upload/pre', 'finish': 'v2/video/pc/upload/finish',
        'cover': 'v4/video/pc/cover/edit/cover/upload', 'preview': 'v2/video/pc/upload/cover/upload'}
    async def headers():
        return {'content-type': 'multipart/form-data; boundary=fixture'}
    request = SimpleNamespace(method=method, post_data_json=body, post_data='',
        post_data_buffer=binary, all_headers=headers, timing={'startTime': when * 1000})
    return SimpleNamespace(url='https://cp.kuaishou.com/rest/cp/works/' + paths[operation],
        request=request, status=status)


def multipart(data):
    return (b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="blob"\r\n'
        b'Content-Type: image/jpeg\r\n\r\n' + data + b'\r\n--fixture--\r\n')


def session(page, video=b'fixture-native-video', cover=None):
    now, sha = time.time(), hashlib.sha256(video).hexdigest()
    account = {'verified': True, 'platform': 'kuaishou', 'uid': 'fixture-owner', 'identity': 'fixture-account'}
    expected = {'job_id': 'fixture-job', 'receipt_nonce': 'fixture-nonce', 'video_sha256': sha,
        'account': {k: account[k] for k in ('uid', 'identity')}}
    assets = {'video': {'kind': 'video', 'name': 'fixture.mp4', 'size': len(video), 'sha256': sha}}
    files = [{'name': 'fixture.mp4', 'size': len(video), 'sha256': sha, 'type': 'video/mp4', 'selected_at': now + .00001}]
    if cover:
        cs = hashlib.sha256(cover).hexdigest()
        assets['cover'] = {'kind': 'cover', 'name': 'original.png', 'size': len(cover), 'sha256': cs}
        files.append({'name': 'original.png', 'size': len(cover), 'sha256': cs, 'type': 'image/png', 'selected_at': now + .00001})
        expected['cover_sha256'] = {'cover': cs}
    value = SimpleNamespace(page=page, platform='kuaishou', account=account, expected=expected,
        assets=assets, files=files, closed=False, started_at=now)
    page._sau_draft_evidence = value
    return value


class KSProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.page = SimpleNamespace()
        self.session = session(self.page)
        self.observer = KSEvidenceObserver(self.session)
        self.when = time.time() + .02
        self.native = {'name': 'fixture.mp4', 'size': self.session.assets['video']['size'],
            'sha256': self.session.expected['video_sha256'], 'file_id': 123456789,
            'token_sha256': hashlib.sha256(b'fixture-private-token').hexdigest(), 'cover_keys': []}
        self.observer.native = self.native

    async def complete(self, *, pre=None, finish=None, body=None):
        pre = {'result': 1, 'data': {'fileId': 123456789, 'token': 'fixture-private-token',
            'endPoints': ['upload.kuaishouzt.com']}} if pre is None else pre
        finish = {'result': 1, 'data': {'fileId': 123456789}} if finish is None else finish
        body = {'token': 'fixture-private-token', 'fileName': 'fixture.mp4', 'fileLength': self.native['size'],
            'fileType': 'video/mp4'} if body is None else body
        await self.observer.observe(reply('pre', {'uploadType': 1}, self.when), pre, self.when + .001)
        await self.observer.observe(reply('finish', body, self.when + .02), finish, self.when + .021)

    async def test_unique_current_native_sha_pre_token_finish_and_consumer(self):
        await self.complete()
        self.assertEqual(self.observer.upload_links(), [{'kind': 'video', 'sha256': self.native['sha256'],
            'ids': ['123456789'], 'complete': True}])

    async def test_fragment_or_http_ack_and_bad_business_code_not_finish(self):
        for data in ({}, {'result': True, 'data': {'fileId': 123456789}}, {'result': 0, 'data': {'fileId': 123456789}},
                     {'result': 1}, {'result': 1, 'data': {'nested': {'fileId': 123456789}}}):
            self.observer.records.clear()
            await self.complete(finish=data)
            self.assertEqual(self.observer.upload_links(), [])

    async def test_wrong_token_id_size_hash_old_account_or_multiple_allocation_rejected(self):
        await self.complete()
        originals = copy.deepcopy(self.observer.records)
        for field, wrong in [('token_sha256', '0' * 64), ('file_id', 'different-resource'), ('size', self.native['size'] + 1), ('sha256', '0' * 64)]:
            old = self.native[field]; self.native[field] = wrong
            self.assertEqual(self.observer.upload_links(), [], field)
            self.native[field] = old
        self.observer.records.append(copy.deepcopy(self.observer.records[0]))
        self.assertEqual(self.observer.upload_links(), [])
        self.observer.records = originals
        self.session.expected['receipt_nonce'] = 'new-task'
        self.assertEqual(self.observer.upload_links(), [])
        self.session.expected['receipt_nonce'] = 'fixture-nonce'
        self.session.account = {**self.session.account, 'identity': 'different-owner'}
        self.assertEqual(self.observer.upload_links(), [])

    async def test_request_before_scope_and_duplicate_native_selection_rejected(self):
        self.when = self.session.started_at - 1
        await self.complete()
        self.assertEqual(self.observer.upload_links(), [])
        self.observer.records.clear(); self.when = time.time() + .02
        await self.complete()
        self.session.files.append(copy.deepcopy(self.session.files[0]))
        self.assertEqual(self.observer.upload_links(), [])

    async def test_whole_actual_cover_body_required_and_preview_never_applied_cover(self):
        original = b'fixture-original-cover'
        self.session = session(self.page, cover=original)
        self.observer = KSEvidenceObserver(self.session)
        self.observer.native = {**self.native, 'cover_keys': ['fixture-cover-key']}
        now = time.time() + .02
        await self.observer.observe(reply('preview', {}, now, binary=multipart(original)),
            {'result': 1, 'data': {'coverKey': 'fixture-cover-key'}}, now + .001)
        self.assertEqual(self.observer.upload_links(), [])
        await self.observer.observe(reply('cover', {}, now + .02, binary=multipart(original)),
            {'result': 1, 'data': {'coverKey': 'fixture-cover-key'}}, now + .021)
        self.assertEqual(self.observer.upload_links()[0]['sha256'], hashlib.sha256(original).hexdigest())
        self.observer.native['cover_keys'] = ['other-key']
        self.assertEqual(self.observer.upload_links(), [])

    async def test_diagnostics_contain_only_known_keys_counts_and_codes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            await self.complete()
        text = output.getvalue()
        for private in ['fixture-private-token', 'fixture.mp4', '123456789', 'fixture-owner', 'fixture-account']:
            self.assertNotIn(private, text)
        for line in text.splitlines():
            value = json.loads(line.split('=', 1)[1])
            self.assertIn(value['operation'], ('pre', 'finish'))
            self.assertEqual(value['result_code'], 1)

    async def test_rejected_pre_shape_counts_do_not_expand_endpoint_contract(self):
        cases = [(['upload.kuaishouzt.com:443'], 'endpoint_host_port'),
            (['https://upload.kuaishouzt.com'], 'endpoint_https'),
            (['http://upload.kuaishouzt.com'], 'endpoint_http'),
            ([{'host': 'upload.kuaishouzt.com', 'token': 'private-shape-token'}], 'endpoint_objects')]
        for endpoints, gate in cases:
            with self.subTest(gate=gate):
                self.observer.records.clear()
                output = io.StringIO()
                with redirect_stdout(output):
                    await self.complete(pre={'result': 1, 'data': {'fileId': 123456789,
                        'token': 'fixture-private-token', 'endPoints': endpoints}})
                diagnostic = json.loads(output.getvalue().splitlines()[0].split('=', 1)[1])
                self.assertFalse(diagnostic['parsed'])
                self.assertEqual(diagnostic['pre_gates'][gate], 1)
                self.assertTrue(diagnostic['pre_gates']['file_id_valid'])
                self.assertTrue(diagnostic['pre_gates']['token_valid'])
                self.assertEqual(self.observer.upload_links(), [])
                for private in ('upload.kuaishouzt.com', 'private-shape-token', 'fixture-private-token', '123456789'):
                    self.assertNotIn(private, output.getvalue())

    async def test_sdk_optional_empty_fallback_keeps_all_nonempty_hosts_strict(self):
        cases=[(['upload.kuaishouzt.com','backup.kuaishouzt.com',''],True),
            ([''],False),(['upload.kuaishouzt.com',' ',''],False),
            (['upload.kuaishouzt.com','unknown.example.com',''],False),
            (['upload.kuaishouzt.com',None,''],False),
            (['upload.kuaishouzt.com','https://backup.kuaishouzt.com',''],False)]
        for endpoints,allowed in cases:
            with self.subTest(allowed=allowed,endpoints=endpoints):
                self.observer.records.clear();self.observer.attested.clear()
                output=io.StringIO()
                with redirect_stdout(output):
                    await self.complete(pre={'result':1,'data':{'fileId':123456789,
                        'token':'fixture-private-token','endPoints':endpoints}})
                value=json.loads(output.getvalue().splitlines()[0].split('=',1)[1])
                self.assertEqual(value['pre_gates']['endpoint_empty'],1)
                self.assertEqual(value['parsed'],allowed)
                self.assertEqual(bool(self.observer.upload_links()),allowed)

    async def test_pre_route_classes_are_safe_and_do_not_expand_acceptance(self):
        cases=(('//upload.kuaishouzt.com',('protocol_relative','domain_kuaishouzt')),
               (' upload.kuaishouzt.com ',('whitespace','leading_whitespace','trailing_whitespace')),
               ('Upload.kuaishouzt.com',('uppercase','domain_kuaishouzt')),
               ('192.0.2.10',('ipv4','domain_other')),
               ('upload.ksapisrv.com',('domain_ksapisrv',)),
               ('upload.kuaishou.com',('domain_kuaishou',)),
               ('upload.gifshow.com',('domain_gifshow',)),
               ('upload.kwai.com',('domain_kwai',)),
               ('upload.kuaishouzt.com/backup?key=do-not-log-marker#hidden',('path','query','fragment','domain_kuaishouzt')),
               ('do-not-log-marker@upload.kuaishouzt.com',('auth','domain_kuaishouzt')),
               ('upload.kuaishouzt.com:443',('port','domain_kuaishouzt')),
               ('upload.kuaishouzt.com:invalid',('invalid_port','domain_kuaishouzt')))
        for endpoint,gates in cases:
            self.observer.records.clear();self.observer.attested.clear()
            output=io.StringIO()
            with redirect_stdout(output):
                await self.complete(pre={'result':1,'data':{'fileId':123456789,
                    'token':'fixture-private-token','endPoints':[endpoint]}})
            value=json.loads(output.getvalue().splitlines()[0].split('=',1)[1])
            self.assertFalse(value['parsed']);self.assertEqual(self.observer.upload_links(),[])
            for gate in gates:self.assertEqual(value['pre_gates']['endpoint_'+gate],1)
            self.assertEqual(value['pre_gates']['endpoint_classified'],1)
            self.assertFalse(value['pre_gates']['endpoint_classification_truncated'])
            self.assertTrue(all(type(item) in (int,bool) for item in value['pre_gates'].values()))
            for private in (endpoint,'do-not-log-marker','fixture-private-token','123456789'):
                self.assertNotIn(private,output.getvalue())

    def test_pre_route_classification_is_bounded_and_never_retains_raw_values(self):
        value=_pre_gates({'fileId':123456789,'token':'fixture-private-token',
            'endPoints':['do-not-log-marker'*600]+['upload.kuaishouzt.com']*129})
        self.assertEqual(value['endpoint_count'],130)
        self.assertEqual(value['endpoint_classified'],128)
        self.assertTrue(value['endpoint_classification_truncated'])
        self.assertEqual(value['endpoint_oversize'],1)
        text=json.dumps(value)
        for private in ('do-not-log-marker','upload.kuaishouzt.com','fixture-private-token','123456789'):
            self.assertNotIn(private,text)
        self.assertTrue(all(type(item) in (int,bool) for item in value.values()))

    def test_crop_diagnostic_projection_drops_unknown_keys_and_nonprimitive_values(self):
        value = _safe_crop_diagnostics({'gates': {'draw_accepted': 2, 'result_error': True,
                'generate_calls': 1000001, 'token': 'private-value', 'report_attempts': {'private': 'value'}},
            'state': {'crop_instances': 1, 'pending': -1, 'current_ref': True, 'draw_present': 'private-value',
                'url': 'https://private.test/?token=value'}})
        self.assertEqual(value, {'gates': {'draw_accepted': 2},
            'state': {'crop_instances': 1, 'current_ref': True}})

    def test_exact_known_host_path_and_no_credentials_port_redirect_alias(self):
        path = '/rest/cp/works/v2/video/pc/upload/pre'
        self.assertEqual(ks_endpoint('https://cp.kuaishou.com' + path), 'pre')
        for url in ['http://cp.kuaishou.com' + path, 'https://cp.kuaishou.com:443' + path,
            'https://user@cp.kuaishou.com' + path, 'https://cp.kuaishou.com.evil.test' + path,
            'https://other.cp.kuaishou.com' + path, 'https://cp.kuaishou.com' + path + '/extra',
            'https://cp.kuaishou.com' + path + '#fragment']:
            self.assertIsNone(ks_endpoint(url), url)


SETUP_JS = r"""async ({source,foreign,blob})=>{
 const surface=foreign?document.querySelector('iframe').contentWindow:window;
 const raw=surface.document.createElement('canvas');raw.className='_cutter-raw_fixture_1';raw.width=64;raw.height=48;
 const host=document.createElement('div'),shadow=host.attachShadow({mode:'open'});document.body.append(host);shadow.append(raw);
 const input=surface.document.createElement('canvas');input.width=64;input.height=48;
 const ic=input.getContext('2d');ic.fillStyle='#315bbf';ic.fillRect(0,0,64,48);ic.fillStyle='#e7bc47';ic.fillRect(4,5,18,21);
 const data=input.toDataURL('image/png');const original=Uint8Array.from(atob(data.split(',')[1]),x=>x.charCodeAt(0));
 const src=blob?surface.URL.createObjectURL(new surface.File([original],'original.png',{type:'image/png'})):data;
 const props={currentPreview:src,mediaWidth:64,mediaHeight:48};
 const make=surface.Function('canvas','source',`const v={current:canvas},w={width:64,height:48},t={width:32,height:24},f={x:0,y:0};
 const An=(data,name)=>{const b=Uint8Array.from(atob(data.split(',')[1]),x=>x.charCodeAt(0));return new File([b],name,{type:'image/jpeg'});};return (${source});`);
 const ref={current:{generateImage:make(raw,source)}};
 const form={fileId:123456789,fileToken:'fixture-private-token',coverKey:'',pkCoverKey:'',profileCoverKey:'',caption:'private fixture text',fileName:'fixture.mp4',fileSize:'fixture text'};
 const video=Uint8Array.from({length:1048641},(_,i)=>i%251);
 const progress={status:3,percent:0,localFileData:new surface.File([video],'fixture.mp4',{type:'video/mp4'})};
 const root={memoizedState:null,child:null,stateNode:null};root.stateNode={current:root};
 const fiber={ref,memoizedProps:props,memoizedState:{memoizedState:{current:raw},next:{memoizedState:{current:form},next:{memoizedState:progress,next:null}}},return:root};root.child=fiber;
 raw.__reactFiber$fixture=fiber;
 window.fixture={raw,props,ref,form,progress,fiber,video,src,surface,original,make};
 return {video_size:video.length,original:btoa(String.fromCharCode(...original))};
}"""


class KSNativeBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, args=['--disable-gpu'])
        self.page = await self.browser.new_page()
        async def route(r):
            await r.fulfill(content_type='text/html', body='<html><body><iframe src="/fixture-frame"></iframe></body></html>' if '/fixture-frame' not in r.request.url else '<html><body></body></html>')
        await self.page.route('https://cp.kuaishou.com/**', route)
        await self.page.goto('https://cp.kuaishou.com/fixture')
        await self.page.locator('iframe').wait_for()
        self.observer = None

    async def asyncTearDown(self):
        if self.observer:
            await self.observer.close()
        await self.browser.close()
        await self.playwright.stop()

    async def setup_fixture(self, foreign=False, blob=False):
        data = await asyncio.wait_for(self.page.main_frame.evaluate(SETUP_JS, {'source': GENERATE_IMAGE_SOURCE, 'foreign': foreign, 'blob': blob}, isolated_context=False), 20)
        video, cover = bytes(i % 251 for i in range(data['video_size'])), base64.b64decode(data['original'])
        self.session = session(self.page, video=video, cover=cover)
        self.observer = KSEvidenceObserver(self.session)
        await self.observer.initialize()
        return video, cover

    async def draw_and_generate(self, *, draw=True):
        return await self.page.main_frame.evaluate(r'''async draw=>{
         const f=window.fixture;if(draw){const img=new f.surface.Image();await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;img.src=f.src;});
          const c=f.raw.getContext('2d');c.clearRect(0,0,64,48);c.drawImage(img,0,0,64,48);}
         const result=await f.ref.current.generateImage();window.fixtureResult=result;
         return btoa(String.fromCharCode(...new Uint8Array(await result.blob.arrayBuffer())));
        }''', draw, isolated_context=False)

    async def observe_cover(self, cropped):
        now = time.time()
        await self.observer.observe(reply('cover', {}, now, binary=multipart(cropped)),
            {'result': 1, 'data': {'coverKey': 'fixture-cropped-cover'}}, now + .01)
        await self.page.main_frame.evaluate("()=>{window.fixture.form.coverKey='fixture-cropped-cover'}", isolated_context=False)
        await self.observer.drain()

    async def test_png_draw_same_cropper_native_jpeg_upload_and_current_consumption(self):
        video, original = await self.setup_fixture()
        cropped = base64.b64decode(await self.draw_and_generate())
        await self.observe_cover(cropped)
        self.assertEqual(self.observer.native['sha256'], hashlib.sha256(video).hexdigest())
        self.assertEqual(len(self.observer.crops), 1)
        proof = self.observer.crops[0]
        self.assertEqual(proof['original_sha256'], hashlib.sha256(original).hexdigest())
        self.assertNotEqual(proof['preview_sha256'], proof['original_sha256'])
        self.assertEqual(proof['cropped_sha256'], hashlib.sha256(cropped).hexdigest())
        self.assertEqual(self.observer.upload_links(), [{'kind': 'cover', 'sha256': proof['original_sha256'],
            'ids': ['fixture-cropped-cover'], 'complete': True}])
        gates = self.observer.crop_diagnostics['gates']
        self.assertEqual(gates['draw_accepted'], 1)
        self.assertEqual(gates['generate_calls'], 1)
        self.assertEqual(gates['report_accepted'], 1)
        self.assertTrue(self.observer.crop_diagnostics['state']['current_ref'])

    async def test_foreign_native_file_canvas_image_promise_adopted_open_shadow(self):
        _, original = await self.setup_fixture(foreign=True)
        cropped = base64.b64decode(await self.draw_and_generate())
        await self.observe_cover(cropped)
        self.assertEqual(len(self.observer.crops), 1)
        self.assertEqual(self.observer.upload_links()[0]['sha256'], hashlib.sha256(original).hexdigest())

    async def test_actual_blob_preview_source_full_original_sha_same_crop_chain(self):
        _, original = await self.setup_fixture(blob=True)
        cropped = base64.b64decode(await self.draw_and_generate())
        await self.observe_cover(cropped)
        self.assertEqual(len(self.observer.crops), 1)
        self.assertEqual(self.observer.crops[0]['original_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.observer.upload_links()[0]['sha256'], hashlib.sha256(original).hexdigest())

    async def test_react_imperative_ref_refresh_same_canvas_after_ratio_render(self):
        _, original = await self.setup_fixture(blob=True)
        await self.draw_and_generate()
        await self.page.main_frame.evaluate(r'''source=>{
          fixture.ref.current={generateImage:fixture.make(fixture.raw,source)};
          fixture.raw.style.width='64px';
        }''', GENERATE_IMAGE_SOURCE, isolated_context=False)
        await self.page.main_frame.evaluate('()=>new Promise(resolve=>setTimeout(resolve,0))', isolated_context=False)
        cropped = base64.b64decode(await self.draw_and_generate(draw=False))
        await self.observe_cover(cropped)
        self.assertEqual(len(self.observer.crops), 2)
        self.assertEqual(self.observer.upload_links()[0]['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.observer.crop_diagnostics['gates']['ref_replaced'], 1)

    async def test_same_official_ref_replaced_during_normal_generate_keeps_exact_invocation(self):
        _, original = await self.setup_fixture(blob=True)
        await self.page.main_frame.evaluate(r'''async()=>{
          const f=fixture,img=new f.surface.Image();await new Promise(resolve=>{img.onload=resolve;img.src=f.src;});
          f.raw.getContext('2d').clearRect(0,0,64,48);f.raw.getContext('2d').drawImage(img,0,0,64,48);
        }''', isolated_context=False)
        cropped = base64.b64decode(await self.page.main_frame.evaluate(r'''async source=>{
          const f=fixture,pending=f.ref.current.generateImage();
          f.ref.current={generateImage:f.make(f.raw,source)};f.raw.style.width='64px';
          const result=await pending;
          return btoa(String.fromCharCode(...new Uint8Array(await result.blob.arrayBuffer())));
        }''', GENERATE_IMAGE_SOURCE, isolated_context=False))
        await self.observe_cover(cropped)
        self.assertEqual(len(self.observer.crops),1)
        self.assertEqual(self.observer.upload_links()[0]['sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(self.observer.crop_diagnostics['gates']['result_ref_replaced_allowed'],1)

    async def test_normal_cropper_unmount_before_hash_readback_keeps_original_native_chain(self):
        _, original = await self.setup_fixture(blob=True)
        await self.page.main_frame.evaluate(r'''async()=>{
          const f=fixture,img=new f.surface.Image();await new Promise(resolve=>{img.onload=resolve;img.src=f.src;});
          f.raw.getContext('2d').clearRect(0,0,64,48);f.raw.getContext('2d').drawImage(img,0,0,64,48);
        }''', isolated_context=False)
        cropped = base64.b64decode(await self.page.main_frame.evaluate(r'''async()=>{
          const f=fixture;document.querySelector('div').__reactFiber$owner=f.fiber;
          const pending=f.ref.current.generateImage();f.raw.remove();
          const result=await pending;
          return btoa(String.fromCharCode(...new Uint8Array(await result.blob.arrayBuffer())));
        }''', isolated_context=False))
        await self.observe_cover(cropped)
        self.assertEqual(len(self.observer.crops),1)
        self.assertEqual(self.observer.upload_links()[0]['sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(self.observer.crop_diagnostics['gates']['result_unmounted_allowed'],1)
        self.assertEqual(self.observer.crop_diagnostics['state']['crop_instances'],0)

    async def test_changed_original_pixels_or_duplicate_live_cropper_cannot_inherit_generation(self):
        for mode in ('pixels','duplicate'):
            with self.subTest(mode=mode):
                if self.observer:
                    await self.observer.close()
                await self.page.goto('https://cp.kuaishou.com/fixture-' + mode)
                await self.setup_fixture(blob=True)
                await self.page.main_frame.evaluate(r'''async()=>{
                  const f=fixture,img=new f.surface.Image();await new Promise(resolve=>{img.onload=resolve;img.src=f.src;});
                  f.raw.getContext('2d').clearRect(0,0,64,48);f.raw.getContext('2d').drawImage(img,0,0,64,48);
                }''', isolated_context=False)
                cropped = base64.b64decode(await self.page.main_frame.evaluate(r'''async ({mode,source})=>{
                  const f=fixture,pending=f.ref.current.generateImage();
                  if(mode==='pixels')f.raw.getContext('2d').fillRect(0,0,1,1);
                  else{const raw=document.createElement('canvas');raw.className='_cutter-raw_other_1';document.body.append(raw);
                    const fiber={...f.fiber,ref:{current:{generateImage:f.make(raw,source)}},memoizedState:{memoizedState:{current:raw},next:null}};
                    f.fiber.sibling=fiber;raw.__reactFiber$duplicate=fiber;}
                  const result=await pending;
                  return btoa(String.fromCharCode(...new Uint8Array(await result.blob.arrayBuffer())));
                }''', {'mode':mode,'source':GENERATE_IMAGE_SOURCE}, isolated_context=False))
                await self.observe_cover(cropped)
                self.assertEqual(self.observer.crops,[])
                self.assertEqual(self.observer.upload_links(),[])
                gate='result_revision_mismatch' if mode=='pixels' else 'result_current_mismatch'
                self.assertEqual(self.observer.crop_diagnostics['gates'][gate],1)

    async def test_attestation_survives_normal_reopen_without_inheriting_new_selection(self):
        await self.setup_fixture(blob=True)
        cropped = base64.b64decode(await self.draw_and_generate())
        await self.observe_cover(cropped)
        original = self.observer.upload_links()
        await self.page.main_frame.evaluate("()=>{fixture.progress.localFileData=null;fixture.progress.status=1}", isolated_context=False)
        await self.observer.drain()
        self.assertEqual(self.observer.upload_links(), original)
        self.session.files.append({**self.session.files[1], 'selected_at': time.time()})
        self.assertEqual(self.observer.upload_links(), [])

    async def test_unobserved_draw_and_wrong_upload_body_never_imply_origin(self):
        await self.setup_fixture()
        await self.page.main_frame.evaluate("()=>{fixture.raw.getContext('2d').fillRect(0,0,64,48)}", isolated_context=False)
        cropped = base64.b64decode(await self.draw_and_generate(draw=False))
        await self.observe_cover(cropped)
        self.assertEqual(self.observer.crops, [])
        self.assertEqual(self.observer.upload_links(), [])
        self.assertEqual(self.observer.crop_diagnostics['gates']['generate_draw_missing'], 1)
        self.assertEqual(self.observer.crop_diagnostics['gates']['preview_draw_missing'], 1)
        cropped = base64.b64decode(await self.draw_and_generate())
        await self.observe_cover(cropped + b'changed bytes')
        self.assertEqual(self.observer.upload_links(), [])

    async def test_source_changed_during_generation_and_wrong_current_key_rejected(self):
        await self.setup_fixture()
        await self.draw_and_generate()
        await self.page.main_frame.evaluate("async()=>{const promise=fixture.ref.current.generateImage();fixture.props.currentPreview='data:image/png;base64,AA==';await promise}", isolated_context=False)
        self.assertEqual(len(self.observer.crops), 1)
        self.observer.crops.clear()
        # Retained old return data is insufficient to create a fresh proof.
        cropped = await self.page.main_frame.evaluate("async()=>btoa(String.fromCharCode(...new Uint8Array(await fixtureResult.blob.arrayBuffer())))", isolated_context=False)
        await self.observe_cover(base64.b64decode(cropped))
        self.assertEqual(self.observer.upload_links(), [])

    async def test_fake_native_video_and_duplicate_form_ref_fail_closed(self):
        await self.setup_fixture()
        await self.page.main_frame.evaluate("()=>{fixture.progress.localFileData={name:'fixture.mp4',size:1048641,arrayBuffer:()=>Promise.resolve(new ArrayBuffer(1048641))}}", isolated_context=False)
        await self.observer.drain()
        self.assertIsNone(self.observer.native)
        await self.page.main_frame.evaluate("()=>{fixture.progress.localFileData=new File([fixture.video],'fixture.mp4',{type:'video/mp4'});fixture.fiber.memoizedState.next.next.next={memoizedState:{current:{...fixture.form}},next:null}}", isolated_context=False)
        await self.observer.drain()
        self.assertIsNone(self.observer.native)

    async def test_only_instance_methods_are_wrapped_and_close_restores_them(self):
        await self.setup_fixture()
        before = await self.page.main_frame.evaluate("()=>({global:String(HTMLCanvasElement.prototype.toDataURL),context:String(CanvasRenderingContext2D.prototype.drawImage),own:Object.hasOwn(fixture.raw,'toDataURL')})", isolated_context=False)
        self.assertTrue(before['own'])
        await self.observer.close()
        after = await self.page.main_frame.evaluate("()=>({global:String(HTMLCanvasElement.prototype.toDataURL),context:String(CanvasRenderingContext2D.prototype.drawImage),own:Object.hasOwn(fixture.raw,'toDataURL')})", isolated_context=False)
        self.assertEqual(before['global'], after['global'])
        self.assertEqual(before['context'], after['context'])
        self.assertFalse(after['own'])


    async def test_cover_ready_waits_for_selected_native_original_draw_and_refreshes_ref(self):
        await self.setup_fixture(blob=True)
        self.assertFalse(await self.observer.ensure_cover_ready(timeout=.1))
        # A full canvas containing an old image is insufficient: its exact
        # source hash differs from the current native selection.
        await self.page.main_frame.evaluate(r'''async()=>{
          const f=fixture,old=document.createElement('canvas');old.width=64;old.height=48;
          old.getContext('2d').fillRect(0,0,64,48);f.props.currentPreview=old.toDataURL('image/png');
          const img=new Image();await new Promise(resolve=>{img.onload=resolve;img.src=f.props.currentPreview;});
          f.raw.getContext('2d').clearRect(0,0,64,48);f.raw.getContext('2d').drawImage(img,0,0,64,48);
        }''', isolated_context=False)
        self.assertFalse(await self.observer.ensure_cover_ready(timeout=.1))
        await self.page.main_frame.evaluate(r'''source=>{
          const f=fixture;f.props.currentPreview=f.src;
          f.ref.current={generateImage:f.make(f.raw,source)};
          f.drawNew=()=>new Promise(resolve=>setTimeout(async()=>{
            const img=new f.surface.Image();await new Promise(done=>{img.onload=done;img.src=f.src;});
            f.raw.getContext('2d').clearRect(0,0,64,48);f.raw.getContext('2d').drawImage(img,0,0,64,48);resolve(true);
          },100));f.delayedDraw=f.drawNew();
        }''', GENERATE_IMAGE_SOURCE, isolated_context=False)
        self.assertFalse(await self.observer.ensure_cover_ready(timeout=.02))
        self.assertTrue(await self.observer.ensure_cover_ready(timeout=2))
        await self.page.main_frame.evaluate('()=>fixture.delayedDraw', isolated_context=False)
        # React may replace the imperative ref without changing any DOM; the
        # readiness poll must rebind it without proactively generating a crop.
        await self.page.main_frame.evaluate(r'''source=>{
          fixture.ref.current={generateImage:fixture.make(fixture.raw,source)};
        }''', GENERATE_IMAGE_SOURCE, isolated_context=False)
        self.assertTrue(await self.observer.ensure_cover_ready(timeout=2))
        self.assertEqual(self.observer.crops, [])
        await self.draw_and_generate(draw=False)
        await self.observer.drain()
        self.assertEqual(len(self.observer.crops), 1)
        self.session.files.append({**self.session.files[1], 'selected_at': time.time()})
        self.assertFalse(await self.observer.ensure_cover_ready(timeout=.1))

    async def test_native_transport_rejects_child_isolated_stale_and_mapping_pollution(self):
        await self.setup_fixture()
        child = self.page.frames[1]
        # Trigger the pinned source-mapping issue without relying on that
        # mapping anywhere in the production observer.
        self.assertFalse(await child.evaluate('window===top', isolated_context=False))
        self.assertTrue(await self.observer.ensure_ready())
        transport = self.observer.transport
        value = {'scope': self.observer.scope, 'generated_at': time.time(),
            'original_sha256': '1' * 64, 'cropped_sha256': '2' * 64,
            'preview_sha256': '3' * 64, 'cropped_size': 100, 'width': 32, 'height': 24}
        message = {'v': 1, 'kind': 'crop', 'sequence': '1',
            'observer_nonce': self.observer.scope['observer_nonce'],
            'channel_nonce': transport.channel_nonce, 'report': value}
        event = {'name': transport.raw_name, 'executionContextId': transport.context_id,
            'payload': json.dumps(message)}
        child_ids = [key for key, data in transport.contexts.items()
            if data.get('isDefault') is True and data.get('frameId') != transport.root_frame]
        self.assertEqual(len(child_ids), 1)
        isolated = await transport.client.send('Page.createIsolatedWorld',
            {'frameId': transport.root_frame, 'worldName': 'fixture-ks-isolated'})
        for ident in [child_ids[0], isolated['executionContextId'], -1]:
            self.assertFalse(await transport._dispatch({**event, 'executionContextId': ident}))
        for change in [{'observer_nonce': 'old-scope'}, {'channel_nonce': 'old-channel'},
                       {'kind': 'video'}, {'extra': True}, {'v': True}]:
            self.assertFalse(await transport._dispatch({**event, 'payload': json.dumps({**message, **change})}))
        self.assertFalse(self.observer.accept({'page': self.page, 'frame': child}, value))
        await transport.client.send('Runtime.addBinding', {'name': transport.raw_name,
            'executionContextId': child_ids[0]})
        await child.evaluate('window[' + json.dumps(transport.raw_name) + '](' + json.dumps(json.dumps(message)) + ');true', isolated_context=False)
        await asyncio.sleep(.02)
        self.assertEqual(self.observer.crops, [])
        cropped = base64.b64decode(await self.draw_and_generate())
        await self.observe_cover(cropped)
        self.assertEqual(len(self.observer.crops), 1)
        self.assertEqual(len(self.observer.upload_links()), 1)

    async def test_native_transport_navigation_rotates_channel_and_close_restores_binding(self):
        await self.setup_fixture()
        observer, transport = self.observer, self.observer.transport
        old_context, old_channel = transport.context_id, transport.channel_nonce
        await self.page.goto('https://cp.kuaishou.com/fixture-new-context')
        await self.page.main_frame.evaluate(SETUP_JS,
            {'source': GENERATE_IMAGE_SOURCE, 'foreign': False, 'blob': False}, isolated_context=False)
        self.assertTrue(await observer.ensure_ready())
        self.assertNotEqual(transport.channel_nonce, old_channel)
        message = {'v': 1, 'kind': 'crop', 'sequence': '1',
            'observer_nonce': observer.scope['observer_nonce'], 'channel_nonce': old_channel,
            'report': {'scope': observer.scope}}
        for ident in [old_context, transport.context_id]:
            self.assertFalse(await transport._dispatch({'name': transport.raw_name,
                'executionContextId': ident, 'payload': json.dumps(message)}))
        self.assertFalse(await transport.evaluate("Object.hasOwn(window,'__sauXHSCoverUploaded')"))
        await observer.close()
        visible = await self.page.main_frame.evaluate('names=>names.map(name=>Object.hasOwn(window,name))',
            [observer.binding, transport.raw_name], isolated_context=False)
        self.assertEqual(visible, [False, False])

    async def test_two_cdp_workers_scope_channels_and_old_close_cannot_break_current(self):
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        launch = {'executable_path': str(shell)} if shell.exists() else {'channel': 'chrome'}
        browser = await self.playwright.chromium.launch(headless=True,
            args=['--disable-gpu', f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1'], **launch)
        clients, observers = [], []
        try:
            context = await browser.new_context()
            async def local(route):
                await route.fulfill(content_type='text/html', body='<html><body><iframe src="/fixture-frame"></iframe></body></html>' if '/fixture-frame' not in route.request.url else '<html><body></body></html>')
            await context.route('**/*', local)
            owner = await context.new_page()
            await owner.goto('https://cp.kuaishou.com/fixture-cdp-target')
            data = await owner.main_frame.evaluate(SETUP_JS,
                {'source': GENERATE_IMAGE_SOURCE, 'foreign': True, 'blob': True}, isolated_context=False)
            # Borrow the browser's actual WebSocket endpoint, as the embedded
            # product does. This headless shell rejects /json/version/ (with
            # a trailing slash) used by Patchright's HTTP discovery helper.
            def websocket_endpoint():
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=5) as response:
                    return json.load(response)['webSocketDebuggerUrl']
            endpoint = await asyncio.to_thread(websocket_endpoint)
            for phase in ['a', 'b']:
                client = await async_playwright().start()
                clients.append(client)
                connected = await client.chromium.connect_over_cdp(endpoint)
                targets = [p for c in connected.contexts for p in c.pages if p.url.endswith('/fixture-cdp-target')]
                self.assertEqual(len(targets), 1)
                target = targets[0]
                current = session(target, bytes(i % 251 for i in range(data['video_size'])), base64.b64decode(data['original']))
                current.expected.update(job_id='fixture-cdp-job-' + phase, receipt_nonce='fixture-cdp-nonce-' + phase)
                observer = KSEvidenceObserver(current)
                observers.append(observer)
                await observer.initialize()
                self.assertTrue(await observer.ensure_ready())
                child = next(f for f in target.frames if f.parent_frame is target.main_frame)
                self.assertFalse(await child.evaluate('window===top', isolated_context=False))
                self.assertTrue(await observer.ensure_ready())
                if phase == 'a':
                    await observer.transport.evaluate('window.fixtureOldSender=window[' + json.dumps(observer.binding) + '];true')
                    current.closed = True
            old, current = observers
            stale = {'scope': old.scope, 'generated_at': time.time(),
                'original_sha256': '1' * 64, 'cropped_sha256': '2' * 64,
                'preview_sha256': '3' * 64, 'cropped_size': 100, 'width': 32, 'height': 24}
            self.assertFalse(await current.transport.evaluate('fixtureOldSender(' + json.dumps(stale) + ')'))
            self.assertEqual(old.crops, [])
            self.assertFalse(await current.transport.evaluate('window[' + json.dumps(current.binding) + '](' + json.dumps(stale) + ')'))
            self.assertEqual(current.crops, [])
            # Old cleanup after the next install must not undo its specific
            # methods or restore/delete the next public callback.
            await old.close()
            self.assertTrue(await current.ensure_ready())
            await current.transport.evaluate(r'''(async()=>{
                const f=fixture,img=new f.surface.Image();await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;img.src=f.src;});
                f.raw.getContext('2d').clearRect(0,0,64,48);f.raw.getContext('2d').drawImage(img,0,0,64,48);
                await f.ref.current.generateImage();return true;
            })()''')
            await current.drain()
            self.assertEqual(len(current.crops), 1)
            self.assertEqual(old.crops, [])
            self.assertNotEqual(old.transport.raw_name, current.transport.raw_name)
        finally:
            for observer in observers:
                await observer.close()
            for client in clients:
                await client.stop()
            await browser.close()


if __name__ == '__main__':
    unittest.main()
