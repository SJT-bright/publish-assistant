"""Official-shaped local DOM flows; all requests fulfilled or aborted locally.

These tests prove ordering and current native media binding, not a real
platform upload, login, persisted draft or public publication.
"""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from tests.test_bili_controls import HTML as BILI_HTML, URL as BILI_URL
from uploader import bili_controls
from uploader.bili_pipeline import bili_prepare_upload
from uploader.draft_session import DraftInterrupted
from uploader.tencent_uploader.main import TencentVideo
from uploader.tc_pipeline import capture_tc_upload, read_tc_upload_binding, release_tc_upload

TC_URL='https://channels.weixin.qq.com/platform/post/create'
TC_HTML=r'''<!doctype html><meta charset="utf-8"><style>.input-editor{min-height:80px;white-space:pre-wrap}</style>
<div id="material"></div><input type="file" accept="video/mp4" id="native">
<div class="input-editor" contenteditable="true"></div><input placeholder="填写短标题有机会获得更多流量">
<script>
const cfg=CONFIG;window.trace=[];window.businessCalls=0;const business=()=>{businessCalls++;throw Error('forbidden')};
window.material={fileList:[],$refs:{postUpload:{value:[],handleCropVideo:business,updateVideo:business,updateFile:business,setFileList:business}},
 handleFileListChange:business,handleSetFileList:business,handleDeleteFile:business,updateFile:business,
 get allUploaded(){return this.fileList.length&&this.fileList.every(x=>x.status==='done')},
 get allCoverCroped(){return this.fileList.length&&this.fileList.every(x=>!!x.thumbUrl)},
 get canSaveDraft(){return !!this.allUploaded},get disablePostTip(){throw Error('mutable getter')},
 get mediaList(){throw Error('mutable getter')}};
document.querySelector('#material').__vue__=material;
document.querySelector('#native').onchange=e=>{
 trace.push('selected');const file=e.target.files[0],item={originFileObj:file,isVideo:true,isImage:false,isValid:true,status:'uploading'};
 material.fileList=[item];material.$refs.postUpload.value=[item];
 if(cfg.resetInput)e.target.value='';if(cfg.removeInput)e.target.remove();
 setTimeout(()=>{item.status=cfg.failure?'error':'done';if(!cfg.noThumb)item.thumbUrl='fixture-thumb';trace.push(cfg.failure?'failed':'uploaded');
  if(cfg.resetContent){document.querySelector('.input-editor').textContent='';document.querySelector('input[placeholder]').value='';}
 },cfg.delay);
};
document.querySelector('.input-editor').oninput=()=>trace.push('caption');
document.querySelector('input[placeholder]').oninput=()=>trace.push('title');
</script>'''


class ParallelPlatformTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sau-native-pipeline-')
        self.video=Path(self.temp.name)/'fixture.mp4';self.video.write_bytes(b'isolated-native-video')
        self.cover=Path(self.temp.name)/'fixture.jpg';self.cover.write_bytes(b'isolated-native-image')
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context()
        self.external=[]
        self.page=await self.context.new_page()

    async def asyncTearDown(self):
        await release_tc_upload(getattr(self.page,'_sau_tc_native_import',None))
        from uploader.tc_stage import release_tc_native_stage
        await release_tc_native_stage(getattr(self.page,'_sau_tc_native_stage',None))
        await self.context.close();await self.browser.close();await self.pw.stop();self.temp.cleanup()

    async def route_fixture(self,url,html):
        async def route(request):
            if request.request.url!=url:
                self.external.append(request.request.url);await request.abort();return
            await request.fulfill(content_type='text/html;charset=utf-8',body=html)
        await self.context.route('**/*',route);await self.page.goto(url)

    async def bili_fixture(self,**options):
        cfg={'tasks':0,'done':False,'records':0,'stored':0,'tags':[],'delay':30,'uploadDelay':1600,**options}
        html=BILI_HTML.replace('CONFIG',json.dumps(cfg))
        html=html.replace('},cfg.delay)};\ndocument.querySelector(\'#tag\')',
                          'window.trace.push("uploaded");},cfg.uploadDelay)};\ndocument.querySelector(\'#tag\')')
        if cfg.get('manualUpload'):
            html=html.replace('setTimeout(()=>{publisher.videoTasks[0].status=3;', 'window.finishUpload=()=>{publisher.videoTasks[0].status=3;')
            html=html.replace('window.trace.push("uploaded");},cfg.uploadDelay)};', 'window.trace.push("uploaded");};};')
        html=html.replace("video.onchange=()=>{++counters.video;",'video.onchange=()=>{window.trace.push("selected");++counters.video;')
        html+='''<script>window.trace=[];document.querySelector('#title').oninput=()=>trace.push('title');
         document.querySelector('#description').addEventListener('input',()=>trace.push('description'));
         document.querySelector('#tag').addEventListener('keydown',e=>{if(e.key==='Enter')trace.push('tag')});
         document.addEventListener('click',e=>{if(e.target.matches('.cover-editor .button.submit')){trace.push('cover');if(window.finishUpload)queueMicrotask(window.finishUpload);}});</script>'''
        await self.route_fixture(BILI_URL,html)

    async def bili_select(self):
        control=await bili_controls.bili_empty_video_control(self.page)
        await control.set_input_files(str(self.video))

    async def bili_prepare(self,**kwargs):
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
            await bili_prepare_upload(self.page,'完整标题','第一段正文\n第二段正文',['话题一','话题二'],timeout=4,**kwargs)

    async def test_bili_fills_and_covers_while_uploading_then_waits_for_sdk_completion(self):
        await self.bili_fixture(manualUpload=True);await self.bili_select()
        await self.bili_prepare(cover=str(self.cover))
        trace=await self.page.evaluate('trace',isolated_context=False)
        for action in ('title','description','tag','cover'):
            self.assertLess(trace.index(action),trace.index('uploaded'))
        counts=await self.page.evaluate('counters',isolated_context=False)
        self.assertEqual(counts['video'],1);self.assertEqual(counts['cover'],1)
        self.assertEqual(counts['draft'],0);self.assertEqual(counts['publish'],0)
        self.assertFalse(self.external)

    async def test_bili_upload_completion_recommendations_reconciled_on_same_native_task(self):
        await self.bili_fixture(autoOnUpload=True);await self.bili_select();await self.bili_prepare()
        self.assertEqual((await bili_controls._tags(self.page.main_frame))['tags'],['话题一','话题二'])
        self.assertEqual((await self.page.evaluate('counters',isolated_context=False))['video'],1)

    async def test_bili_fast_upload_still_joins_complete_content_and_asynchronous_tags(self):
        await self.bili_fixture(uploadDelay=20);await self.bili_select();await self.bili_prepare()
        self.assertEqual((await bili_controls._tags(self.page.main_frame))['tags'],['话题一','话题二'])
        self.assertEqual(await self.page.locator('#description').inner_text(),'第一段正文\n第二段正文')

    async def test_bili_old_completed_task_and_file_replacement_cannot_complete_new_pipeline(self):
        await self.bili_fixture(tasks=1,done=True)
        with self.assertRaises(DraftInterrupted):await self.bili_prepare()
        await self.context.unroute('**/*');await self.bili_fixture();await self.bili_select()
        await self.page.evaluate("()=>{publisher.videoTasks[0].file=new File(['same size not same File'],'fixture.mp4',{type:'video/mp4'});}",isolated_context=False)
        with self.assertRaises(DraftInterrupted):await self.bili_prepare()
        self.assertEqual(await self.page.locator('#title').input_value(),'')

    async def test_bili_failed_task_stops_without_selecting_again_or_final_action(self):
        await self.bili_fixture();await self.bili_select()
        await self.page.evaluate("()=>setTimeout(()=>{publisher.videoTasks[0].status=4},100)",isolated_context=False)
        with self.assertRaises(DraftInterrupted):await self.bili_prepare()
        counts=await self.page.evaluate('counters',isolated_context=False)
        self.assertEqual((counts['video'],counts['draft'],counts['publish']),(1,0,0))

    async def test_bili_done_badge_without_completed_native_sdk_cannot_finalize(self):
        await self.bili_fixture(uploadDelay=10000);await self.bili_select()
        await self.page.evaluate("()=>{publisher.videoTasks[0].status=3;publisher.videoTasks[0].uploader.uploader.completed=false;}",isolated_context=False)
        with self.assertRaises(DraftInterrupted):await self.bili_prepare()
        counts=await self.page.evaluate('counters',isolated_context=False)
        self.assertEqual((counts['video'],counts['draft'],counts['publish']),(1,0,0))

    async def tc_fixture(self,**options):
        cfg={'delay':700,'failure':False,'resetContent':False,'resetInput':False,'removeInput':False,**options}
        await self.route_fixture(TC_URL,TC_HTML.replace('CONFIG',json.dumps(cfg)))
        data=self.video.read_bytes()
        self.page._sau_draft_evidence=SimpleNamespace(capture_tc_cdn_snapshot=AsyncMock(return_value=True),
            expected={'job_id':'local-pipeline','receipt_nonce':'local-nonce'},assets={'video':{
                'name':self.video.name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}})
        uploader=TencentVideo('完整标题',str(self.video),['科普'],0,'unused.json',desc='正文内容',short_title='完整短标题六字')
        uploader.embedded=SimpleNamespace(browser=self.browser,context=self.context,page=self.page)
        uploader.draft_only=True;uploader.draft_upload_timeout=3
        uploader.draft_expected={'platform_fields':{'caption':'完整标题\n正文内容\n#科普','short_title':'完整短标题六字'}}
        uploader.validate_upload_args=AsyncMock();uploader.open_upload_page=AsyncMock()
        async def final_action(page):
            self.assertEqual(await read_tc_upload_binding(page._sau_tc_native_import),'done')
            await page.evaluate("trace.push('final')",isolated_context=False)
            return {'status':'platform_draft_verified'}
        uploader.submit_publish=AsyncMock(side_effect=final_action)
        return uploader

    async def tc_run(self,uploader):
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()), \
             patch('uploader.draft_only.install_draft_guard',new=AsyncMock()), \
             patch('uploader.draft_only.draft_resume_active',return_value=False):
            await uploader.upload(self.pw)

    async def test_tc_caption_topics_and_title_before_upload_done_then_final_action(self):
        uploader=await self.tc_fixture();await self.tc_run(uploader)
        trace=await self.page.evaluate('trace',isolated_context=False)
        self.assertLess(trace.index('caption'),trace.index('uploaded'))
        self.assertLess(trace.index('title'),trace.index('uploaded'))
        self.assertLess(trace.index('uploaded'),trace.index('final'))
        self.assertEqual(trace.count('selected'),1)
        self.assertEqual(await self.page.locator('.input-editor').inner_text(),'完整标题\n正文内容\n#科普')
        self.assertEqual(await self.page.evaluate('businessCalls',isolated_context=False),0)
        self.assertFalse(self.external)

    async def test_tc_done_without_automatic_thumb_enters_cover_before_final_action(self):
        uploader=await self.tc_fixture(noThumb=True)
        uploader.thumbnail_landscape_path=str(self.cover)
        async def custom_cover(page):
            self.assertEqual(await read_tc_upload_binding(page._sau_tc_native_import),'done')
            self.assertFalse(await page.evaluate('!!material.fileList[0].thumbUrl',isolated_context=False))
            await page.evaluate("trace.push('custom-cover')",isolated_context=False)
        uploader.set_thumbnail=AsyncMock(side_effect=custom_cover)
        await self.tc_run(uploader)
        trace=await self.page.evaluate('trace',isolated_context=False)
        self.assertLess(trace.index('uploaded'),trace.index('custom-cover'))
        self.assertLess(trace.index('custom-cover'),trace.index('final'))
        self.assertEqual(trace.count('selected'),1)
        self.assertEqual(await self.page.evaluate('businessCalls',isolated_context=False),0)
        self.assertFalse(self.external)

    async def test_tc_pending_upload_without_thumb_never_enters_cover_or_final_action(self):
        uploader=await self.tc_fixture(noThumb=True,delay=10000)
        uploader.draft_upload_timeout=.2
        uploader.thumbnail_landscape_path=str(self.cover)
        uploader.set_thumbnail=AsyncMock()
        with self.assertRaisesRegex(RuntimeError,'上传或处理尚未完成'):
            await self.tc_run(uploader)
        uploader.set_thumbnail.assert_not_awaited()
        uploader.submit_publish.assert_not_awaited()

    async def test_tc_duplicate_current_entries_without_thumb_never_enter_cover(self):
        uploader=await self.tc_fixture(noThumb=True,delay=10000)
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
            await uploader.upload_video_file(self.page,str(self.video))
            await self.page.evaluate("()=>{const item=material.fileList[0];item.status='done';material.fileList=[item,item];material.$refs.postUpload.value=[item,item];}",isolated_context=False)
            with self.assertRaisesRegex(RuntimeError,'不一致|重复素材'):
                await uploader.wait_for_upload_complete(self.page)
        uploader.submit_publish.assert_not_awaited()

    async def test_tc_processing_reset_only_restores_same_imported_task_content(self):
        uploader=await self.tc_fixture(resetContent=True);await self.tc_run(uploader)
        trace=await self.page.evaluate('trace',isolated_context=False)
        self.assertEqual(trace.count('selected'),1)
        self.assertIn('caption',trace[:trace.index('uploaded')])
        self.assertIn('caption',trace[trace.index('uploaded')+1:])
        self.assertLess(trace.index('uploaded'),len(trace)-1)
        self.assertEqual(await self.page.locator('.input-editor').inner_text(),'完整标题\n正文内容\n#科普')

    async def test_tc_failed_upload_cancels_preparation_and_never_finalizes(self):
        uploader=await self.tc_fixture(failure=True)
        with self.assertRaisesRegex(RuntimeError,'失败'):await self.tc_run(uploader)
        uploader.submit_publish.assert_not_awaited()
        self.assertEqual((await self.page.evaluate('trace',isolated_context=False)).count('selected'),1)

    async def test_tc_old_completed_state_does_not_bind_or_upload_again(self):
        uploader=await self.tc_fixture()
        await self.page.evaluate("()=>{const native=new File(['old'],'fixture.mp4',{type:'video/mp4'}),item={originFileObj:native,isVideo:true,isImage:false,isValid:true,status:'done',thumbUrl:'old'};material.fileList=[item];material.$refs.postUpload.value=[item];}",isolated_context=False)
        with self.assertRaisesRegex(RuntimeError,'已有素材'):await self.tc_run(uploader)
        self.assertEqual(await self.page.evaluate('trace',isolated_context=False),[])
        uploader.submit_publish.assert_not_awaited()

    async def test_tc_bound_native_file_rejects_same_name_size_replacement(self):
        uploader=await self.tc_fixture()
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
            await uploader.upload_video_file(self.page,str(self.video))
        binding=self.page._sau_tc_native_import
        self.assertEqual(await read_tc_upload_binding(binding),'uploading')
        await self.page.evaluate("()=>{const old=material.fileList[0].originFileObj;material.fileList[0].originFileObj=new File([new Uint8Array(old.size)],old.name,{type:old.type});}",isolated_context=False)
        self.assertEqual(await read_tc_upload_binding(binding),'changed')
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
            with self.assertRaisesRegex(RuntimeError,'不一致'):await uploader.wait_for_upload_complete(self.page)

    async def test_tc_native_input_reset_or_remount_preserves_observed_file_binding(self):
        for mode in ('resetInput','removeInput'):
            await self.context.unroute('**/*')
            uploader=await self.tc_fixture(**{mode:True});await self.tc_run(uploader)
            trace=await self.page.evaluate('trace',isolated_context=False)
            self.assertEqual(trace.count('selected'),1);self.assertEqual(trace[-1],'final')


if __name__=='__main__':unittest.main(verbosity=2)
