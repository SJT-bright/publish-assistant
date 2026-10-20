"""Disk integrity plus real native File cache-key fixtures; all routes local."""
import asyncio
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from tests.test_tc_bili_parallel_pipeline import TC_HTML,TC_URL
from uploader import tc_stage
from uploader.tc_pipeline import read_tc_upload_binding,release_tc_upload
from uploader.tencent_uploader.main import TencentVideo


class TCNativeStageDiskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sau-stage-fixture-');self.root=Path(self.temp.name)
        self.source=self.root/'same-name.mp4';self.data=b'full original video bytes'*100000;self.source.write_bytes(self.data)
        self.asset={'name':self.source.name,'size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}
        self.before=self.source.stat();self.stages=[]
        self.clock=patch.object(tc_stage,'_clock_path',return_value=self.root/'private-clock');self.clock.start()

    async def asyncTearDown(self):
        for stage in self.stages:await tc_stage.release_tc_native_stage(stage)
        self.clock.stop();self.temp.cleanup()

    async def create(self,**kwargs):
        stage=await tc_stage.create_tc_native_stage(self.source,self.asset,**kwargs);self.stages.append(stage);return stage

    async def test_same_name_size_full_sha_and_unique_ms_without_source_write(self):
        with patch.object(tc_stage.time,'time_ns',return_value=self.before.st_mtime_ns):
            first,second=await asyncio.gather(self.create(),self.create())
        self.assertEqual(first.path.name,self.source.name);self.assertEqual(first.path.stat().st_size,len(self.data))
        self.assertEqual(first.path.read_bytes(),self.data);self.assertEqual(second.path.read_bytes(),self.data)
        self.assertNotEqual(first.last_modified_ms,self.before.st_mtime_ns//1000000)
        self.assertNotEqual(first.last_modified_ms,second.last_modified_ms)
        after=self.source.stat();self.assertEqual((after.st_ino,after.st_mtime_ns,after.st_ctime_ns),
            (self.before.st_ino,self.before.st_mtime_ns,self.before.st_ctime_ns))
        await asyncio.to_thread(first.verify)

    async def test_source_and_private_copy_changes_fail_closed(self):
        stage=await self.create();self.source.write_bytes(b'x'*len(self.data))
        with self.assertRaises(tc_stage.TCStageError):stage.assert_current()
        self.source.write_bytes(self.data)
        with self.assertRaises(tc_stage.TCStageError):stage.verify()
        fresh=await self.create();fresh.path.write_bytes(b'y'*len(self.data))
        with self.assertRaises(tc_stage.TCStageError):fresh.verify()

    async def test_incomplete_creation_and_cancellation_leave_no_owned_media(self):
        created=[];original_mkdtemp=tc_stage.tempfile.mkdtemp
        def tracked(**kwargs):
            kwargs['dir']=self.root
            result=original_mkdtemp(**kwargs);created.append(Path(result));return result
        with patch.object(tc_stage.tempfile,'mkdtemp',side_effect=tracked):
            with self.assertRaises(tc_stage.TCStageError):
                await tc_stage.create_tc_native_stage(self.source,{**self.asset,'sha256':'a'*64})
            entered,proceed=threading.Event(),threading.Event();original_hash=tc_stage._hash
            def slow(*args,**kwargs):entered.set();proceed.wait(5);return original_hash(*args,**kwargs)
            with patch.object(tc_stage,'_hash',side_effect=slow):
                task=asyncio.create_task(tc_stage.create_tc_native_stage(self.source,self.asset))
                self.assertTrue(await asyncio.to_thread(entered.wait,5));task.cancel();proceed.set()
                with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(created);self.assertTrue(all(not path.exists() for path in created))
        self.assertEqual(self.source.read_bytes(),self.data)

    async def test_failed_prepared_recovery_loads_existing_copy_and_success_releases_it(self):
        scope={'job_id':'fixture-job','receipt_nonce':'fixture-nonce'};root=self.root/'job'/'.tencent-native-input'
        stage=await self.create(recovery_root=root,scope=scope)
        page=SimpleNamespace(_sau_checkpoint_runtime=SimpleNamespace(output=self.root/'job'/'checkpoint.json',resume=None))
        with patch.object(tc_stage,'create_tc_native_stage',side_effect=AssertionError('resume must not stage again')):
            restored=await tc_stage.restore_tc_native_stage(page,self.source,self.asset,scope)
        self.assertEqual(restored.path,stage.path);self.assertEqual(restored.path.read_bytes(),self.data)
        await tc_stage.release_tc_native_stage(restored)
        self.assertFalse(stage.path.exists());self.assertFalse((root/'active.json').exists())

    async def test_two_processes_allocate_distinct_ms_even_with_identical_clock(self):
        stamp=self.before.st_mtime_ns//1000000;clock=self.root/'process-clock'
        script="import sys;from pathlib import Path;from uploader import tc_stage as m;m._clock_path=lambda:Path(sys.argv[1]);m.time.time_ns=lambda:int(sys.argv[2])*1000000;print(m._fresh_millisecond(int(sys.argv[2])))"
        processes=await asyncio.gather(*(asyncio.create_subprocess_exec(sys.executable,'-c',script,str(clock),str(stamp),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE) for _ in range(2)))
        output=await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in processes)),10)
        self.assertTrue(all(p.returncode==0 for p in processes))
        self.assertEqual({int(stdout) for stdout,_ in output},{stamp+1,stamp+2})

    async def test_display_wrapper_uses_validated_original_identity_and_wrong_nonce_is_rejected(self):
        from uploader.draft_evidence import IDENTITY_KEYS
        scope={'job_id':'original-job','receipt_nonce':'original-nonce'}
        original=self.root/'original-job';wrapper=self.root/'display-wrapper'
        stage=await self.create(recovery_root=original/'.tencent-native-input',scope=scope)
        identity={key:'fixture-'+key for key in IDENTITY_KEYS}
        identity.update(**scope,platform_id='tencent',video_sha256=self.asset['sha256'],cover_sha256={})
        session=SimpleNamespace(expected=identity,closed=False,platform='tencent',_checkpoint_restored_at=1)
        runtime=SimpleNamespace(output=wrapper/'checkpoint.json',resume=original/'checkpoint.json',
            proof={'identity':dict(identity),'assets':{'video':self.asset}})
        page=SimpleNamespace(_sau_checkpoint_runtime=runtime,_sau_draft_evidence=session)
        restored=await tc_stage.restore_tc_native_stage(page,self.source,self.asset,scope)
        self.assertEqual(restored.path,stage.path)
        session.expected={**identity,'receipt_nonce':'wrong-new-nonce'}
        with self.assertRaisesRegex(tc_stage.TCStageError,'resume_scope_unverified'):
            await tc_stage.restore_tc_native_stage(page,self.source,self.asset,{'job_id':'original-job','receipt_nonce':'wrong-new-nonce'})
        self.assertTrue(stage.path.exists())


class TCNativeStageBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.temp=tempfile.TemporaryDirectory(prefix='sau-native-cache-fixture-');self.root=Path(self.temp.name)
        self.source=self.root/'same-name.mp4';self.data=b'whole fixture bytes'*1000;self.source.write_bytes(self.data)
        self.asset={'name':self.source.name,'size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}
        self.before=self.source.stat()
        self.clock=patch.object(tc_stage,'_clock_path',return_value=self.root/'clock');self.clock.start()
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context();cfg={'delay':100000,'failure':False,'resetContent':False,'resetInput':False,'removeInput':False}
        await self.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body=TC_HTML.replace('CONFIG',json.dumps(cfg))))
        self.page=await self.context.new_page();await self.page.goto(TC_URL);self.page._sau_local_files=True
        self.session=SimpleNamespace(expected={'job_id':'local-job','receipt_nonce':'local-nonce'},assets={'video':self.asset},
          capture_tc_cdn_snapshot=AsyncMock(return_value=True))
        self.page._sau_draft_evidence=self.session
        self.page._sau_checkpoint_runtime=SimpleNamespace(output=self.root/'job'/'checkpoint.json',resume=None)
        await self.page.evaluate('''md5=>{
          window.partialMD5=md5;window.allocations=0;window.selections=0;window.cache=[];
          // Full official 841:864180 key; strict 870680 cache equality.
          const key=file=>`${file.name}:${file.lastModified}:${file.size}:${file.type}:${partialMD5}`;
          window.cacheKey=key;
          const seed=document.createElement('input');seed.type='file';seed.id='seed';seed.accept='application/octet-stream';document.body.append(seed);
          seed.onchange=e=>{const file=e.target.files[0];window.original={name:file.name,size:file.size,type:file.type,lastModified:file.lastModified,key:key(file)};
            cache.push({fileUploadedInfoKey:key(file),uploadTaskId:'old-fixture-id'});};
          const input=document.querySelector('#native'),original=input.onchange;
          input.onchange=e=>{original(e);selections++;window.native=e.target.files[0];
            const prior=cache.find(row=>row.fileUploadedInfoKey===key(native));if(!prior)allocations++;
            const item=material.fileList[0];item.status='done';item.thumbUrl='fixture-thumb';
            window.bytes=native.arrayBuffer().then(bytes=>crypto.subtle.digest('SHA-256',bytes));
          };
        }''',hashlib.md5(self.data[:5242880],usedforsecurity=False).hexdigest(),isolated_context=False)
        await self.page.locator('#seed').set_input_files(str(self.source))
        self.app=TencentVideo.__new__(TencentVideo);self.app.draft_only=True

    async def asyncTearDown(self):
        await release_tc_upload(getattr(self.page,'_sau_tc_native_import',None))
        await tc_stage.release_tc_native_stage(getattr(self.page,'_sau_tc_native_stage',None))
        await self.context.close();await self.browser.close();await self.pw.stop();self.clock.stop();self.temp.cleanup()

    async def select(self):
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
            await self.app.upload_video_file(self.page,str(self.source))

    def configure_upload(self,status):
        app=self.app
        app.title='完整标题';app.desc='';app.tags=[];app.short_title='完整短标题六字';app.file_path=str(self.source)
        app.thumbnail_landscape_path=app.thumbnail_portrait_path=None;app.draft_expected=None
        app.embedded=SimpleNamespace(browser=self.browser,context=self.context,page=self.page)
        app.validate_upload_args=AsyncMock();app.open_upload_page=AsyncMock()
        app.prepare_video_for_publish=AsyncMock();app.set_short_title=AsyncMock();app.wait_for_upload_complete=AsyncMock()
        app.submit_publish=AsyncMock(return_value={'status':status})

    async def run_upload(self,resume=False):
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),\
             patch('uploader.draft_only.install_draft_guard',new=AsyncMock()),\
             patch('uploader.draft_only.draft_resume_active',return_value=resume),\
             patch('uploader.draft_only.checkpoint_after_upload',new=AsyncMock(return_value=True)):
            await self.app.upload(self.pw)

    async def test_native_lastmodified_full_bytes_cache_miss_and_one_normal_selection(self):
        await self.select();stage=self.page._sau_tc_native_stage
        value=await self.page.evaluate('''async()=>({name:native.name,size:native.size,type:native.type,lastModified:native.lastModified,
          old:original,cacheMiss:!cache.find(r=>r.fileUploadedInfoKey===cacheKey(native)),allocations,selections,
          sha:[...new Uint8Array(await crypto.subtle.digest('SHA-256',await native.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('')})''',isolated_context=False)
        self.assertEqual((value['name'],value['size'],value['type']),(value['old']['name'],value['old']['size'],value['old']['type']))
        self.assertEqual(value['lastModified'],stage.last_modified_ms);self.assertNotEqual(value['lastModified'],value['old']['lastModified'])
        self.assertEqual(value['sha'],self.asset['sha256']);self.assertTrue(value['cacheMiss'])
        self.assertEqual((value['allocations'],value['selections']),(1,1));self.assertEqual(await read_tc_upload_binding(self.page._sau_tc_native_import),'done')
        self.assertEqual(self.source.stat().st_mtime_ns,self.before.st_mtime_ns)

    async def test_prepared_failure_retains_readable_native_file_and_source_change_blocks(self):
        await self.select();binding=self.page._sau_tc_native_import;stage=self.page._sau_tc_native_stage
        await release_tc_upload(binding,preserve_stage=True);self.page._sau_tc_native_import=None
        self.assertTrue(stage.path.exists());self.assertEqual(await self.page.evaluate('async()=>[...new Uint8Array(await native.arrayBuffer())]',isolated_context=False),list(self.data))
        self.source.write_bytes(b'x'*len(self.data))
        with self.assertRaises(tc_stage.TCStageError):stage.verify()

    async def test_resume_and_cancel_do_not_create_a_second_native_selection(self):
        await self.select();stage=self.page._sau_tc_native_stage
        restored=await tc_stage.restore_tc_native_stage(self.page,self.source,self.asset,
            {'job_id':'local-job','receipt_nonce':'local-nonce'})
        self.assertEqual(restored.path,stage.path);self.assertEqual(await self.page.evaluate('selections',isolated_context=False),1)
        await tc_stage.release_tc_native_stage(restored)
        self.assertFalse(stage.path.exists())

    async def test_task_failure_preserves_then_resume_success_cleans_without_reselect(self):
        self.configure_upload('publication_blocked');await self.run_upload()
        stage=self.page._sau_tc_native_stage
        self.assertTrue(stage.path.exists());self.assertTrue(stage.record.exists())
        self.assertEqual(await self.page.evaluate('async()=>[...new Uint8Array(await native.arrayBuffer())]',isolated_context=False),list(self.data))
        self.app.submit_publish=AsyncMock(return_value={'status':'published_submission_verified'})
        with patch.object(tc_stage,'create_tc_native_stage',side_effect=AssertionError('resume cannot copy')):
            await self.run_upload(resume=True)
        self.assertFalse(stage.path.exists());self.assertFalse(stage.record.exists())
        self.assertEqual(await self.page.evaluate('selections',isolated_context=False),1)

    async def test_task_cancellation_releases_owned_copy_without_a_second_selection(self):
        self.configure_upload('publication_blocked')
        self.app.wait_for_upload_complete=AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):await self.run_upload()
        self.assertIsNone(self.page._sau_tc_native_stage)
        self.assertFalse((self.root/'job'/'.tencent-native-input'/'active.json').exists())
        self.assertEqual(await self.page.evaluate('selections',isolated_context=False),1)

    async def test_original_source_change_stops_bound_pipeline_before_final_action(self):
        await self.select();self.source.write_bytes(b'x'*len(self.data))
        self.assertEqual(await read_tc_upload_binding(self.page._sau_tc_native_import),'changed')

    async def test_current_worker_cleans_retained_stage_when_page_leaves_editor(self):
        await self.select();stage=self.page._sau_tc_native_stage;unwatch=tc_stage.watch_tc_native_stage(self.page,stage)
        await self.page.goto(TC_URL.replace('/post/create','/post/list'))
        for _ in range(100):
            if stage.closed:break
            await asyncio.sleep(.01)
        self.assertTrue(stage.closed);self.assertFalse(stage.path.exists());unwatch()


if __name__=='__main__':unittest.main(verbosity=2)
