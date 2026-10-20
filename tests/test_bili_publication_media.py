"""Fresh submission gate with native File/SDK fixtures and no platform requests."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright
from tests.test_bili_checkpoint_media import HTML, URL
from uploader.bili_controls import _REOPENED_MEDIA
from uploader.bili_evidence import BiliEvidenceObserver, bili_cover_identity
from uploader.publication_workflow import _bili_current_media


class BiliPreparedMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.temp=tempfile.TemporaryDirectory(prefix='sau-bili-fresh-')
        self.data=b'current-native-file-'*70000
        self.video=Path(self.temp.name)/'sealed.mp4';self.video.write_bytes(self.data)
        self.sha=hashlib.sha256(self.data).hexdigest()
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context()
        await self.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body=HTML))
        self.page=await self.context.new_page();await self.page.goto(URL)
        await self.page.locator('#native').set_input_files(str(self.video))
        self.expected={'job_id':'fixture-job','receipt_nonce':'fixture-nonce','video_sha256':self.sha,'cover_sha256':{'cover':'b'*64}}
        asset={'kind':'video','name':self.video.name,'size':len(self.data),'sha256':self.sha}
        self.session=SimpleNamespace(page=self.page,platform='bilibili',closed=False,expected=self.expected,assets={'video':asset})
        self.page._sau_draft_evidence=self.session;self.observer=BiliEvidenceObserver(self.session)
        self.session.bili_observer=self.observer
        self.assertTrue(await self.observer.transport.prepare())
        self.observer.records={'fixture-selection':{'kind':'video','sha256':self.sha,'cid':77,'filename':'native-resource'}}
        self.covers=['https://i0.hdslb.com/bfs/archive/current-main.jpg','https://i0.hdslb.com/bfs/archive/current-extra.jpg']
        self.candidate={'video_ids':['77','native-resource'],'cover_ids':[bili_cover_identity(x) for x in self.covers]}
        # Model the observer's captured native selection, while leaving every
        # current File/SDK/record read and full byte hash to production readers.
        await self.page.evaluate('''cfg=>{
          const scope=cfg.scope;window.__sauBiliEvidenceObserver={version:2,active:scope,
            video:{kind:'video',scope,manager,parentTask:manager.tasks[0],file:task.file,input:document.querySelector('#native')}};
          const el=document.createElement('div');el.className='cover-module-main';el.style='width:240px;height:100px';document.body.append(el);
          window.cover={$el:el,$parent:publisher,defaultCoverIndex:0,openCoverEditor:()=>{businessCalls++;throw Error('forbidden')},
            submitCoverEditor:()=>{businessCalls++;throw Error('forbidden')},coverList:[{main:{edited:cfg.covers[0],origin:'blob:fixture-current',source:'upload'},
              extra:{edited:cfg.covers[1],origin:'blob:fixture-current',source:'upload'}}]};el.__vue__=cover;
          document.querySelector('.video-entrance').style.display='none';
        }''',{'scope':self.observer.scope,'covers':self.covers},isolated_context=False)

    async def asyncTearDown(self):
        await self.observer.transport.close();await self.context.close();await self.browser.close();await self.pw.stop();self.temp.cleanup()

    async def read(self):
        result=await _bili_current_media(self.page,self.expected,self.candidate)
        self.assertEqual(await self.page.evaluate('businessCalls',isolated_context=False),0)
        return result

    async def test_fresh_hidden_entrance_with_native_complete_file_and_crops_is_allowed(self):
        self.assertIsNone(await self.page.evaluate(_REOPENED_MEDIA,isolated_context=False))
        self.assertTrue(await self.read())

    async def test_default_cover_needs_no_custom_cover_projection(self):
        self.expected['cover_sha256']={};self.candidate['cover_ids']=[]
        await self.page.locator('.cover-module-main').evaluate('el=>el.remove()')
        self.assertTrue(await self.read())

    async def test_replaced_file_same_name_size_ids_cannot_release_old_receipt(self):
        await self.page.evaluate('''()=>{const file=new File([new Uint8Array(task.file.size)],task.file.name,{type:task.file.type}),dt=new DataTransfer();
          dt.items.add(file);document.querySelector('#native').files=dt.files;task.file=file;task.uploader.file=file;task.uploader.uploader.file=file;}''',isolated_context=False)
        self.assertFalse(await self.read())

    async def test_replaced_task_using_same_file_and_ids_cannot_release_old_receipt(self):
        await self.page.evaluate('''()=>{task={...task};publisher.videoTasks=[task];manager.tasks=[{id:publisher.id,videoTasks:publisher.videoTasks}];}''',isolated_context=False)
        self.assertFalse(await self.read())

    async def test_incomplete_sdk_even_with_status_done_is_rejected(self):
        await self.page.evaluate('task.uploader.uploader.completed=false',isolated_context=False)
        self.assertFalse(await self.read())

    async def test_old_or_partial_server_media_and_cover_projection_are_rejected(self):
        for script,restore in (
            ("recordConfig.data[0].videos[0].cid=88","recordConfig.data[0].videos[0].cid=77"),
            ("cover.coverList[0].main.edited='https://i0.hdslb.com/bfs/archive/old.jpg'","cover.coverList[0].main.edited="+repr(self.covers[0])),
            ("cover.coverList[0].extra.source='auto'","cover.coverList[0].extra.source='upload'"),
            ("cover.coverList[0].extra.origin='blob:foreign'","cover.coverList[0].extra.origin='blob:fixture-current'")):
            with self.subTest(script=script):
                await self.page.evaluate('()=>{'+script+'}',isolated_context=False);self.assertFalse(await self.read())
                await self.page.evaluate('()=>{'+restore+'}',isolated_context=False)

    async def test_original_selection_scope_and_canonical_context_are_required(self):
        self.observer.scope['observer_nonce']='foreign';self.assertFalse(await self.read())

    async def test_task_swap_during_full_file_hash_is_rejected(self):
        await self.page.evaluate('''()=>{const original=Blob.prototype.slice;let count=0;
          Blob.prototype.slice=function(...args){const part=Reflect.apply(original,this,args);if(++count===2)recordConfig.data[0]={...recordConfig.data[0]};return part;};}''',isolated_context=False)
        self.assertFalse(await self.read())


if __name__=='__main__':unittest.main(verbosity=2)
