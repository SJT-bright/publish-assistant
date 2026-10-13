"""Official-shaped Bili controls on isolated routed Chromium fixtures.

No real account, SDK call, save persistence or upload evidence is exercised.
The mounted Vue objects are read-only state projections; their business
methods throw if a helper calls them rather than the normal DOM controls.
"""
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from contextlib import redirect_stdout

from patchright.async_api import async_playwright
from uploader import bili_controls as controls
from uploader.draft_session import DraftInterrupted

URL = 'https://member.bilibili.com/platform/upload/video/frame'

HTML = r'''<!doctype html><meta charset="utf-8">
<style>input {height:30px} .upload {width:200px;height:50px} .cover-slot {width:150px;height:80px}
.cover-img,.cover-empty {width:140px;height:70px} .ql-editor {height:90px} .cover-editor {position:fixed;inset:0;background:white}
.cover-upload {width:200px;height:50px} .button,.sync-checkbox-wrapper {height:30px;width:180px}
.label-item-v2-container {display:inline-flex;align-items:center;margin:3px;height:28px}
svg.close {width:18px;height:18px;cursor:pointer}
</style><script>const cfg=CONFIG;
const business=()=>{throw new Error('business method forbidden')};
window.counters={video:0,tag:0,tagClose:0,cover:0,emptyCover:0,sync43:0,sync169:0,complete:0,draft:0,publish:0,generic:0};
window.manager={tasks:[],currentTaskId:null,createTask:business};
function uploadedTask(file,status=3){return {status,cid:77,filename:'fixture-native',file,
 uploader:{file,cid:77,filename:'fixture-native',uploaderType:'stdSlice',uploader:{file,completed:status===3}}};}
window.publisher={id:'fixture-task',videoTasks:cfg.tasks?[uploadedTask(new File(['fixture-native-video'],'fixture.mp4',{type:'video/mp4'}),cfg.done?3:1)]:[],$taskConfig:manager,$recordConfig:{data:Array.from({length:cfg.records})},checkSubTask:business,handleSubmitDraft:business};
function bindTask(){manager.tasks=publisher.videoTasks.length?[{id:publisher.id,videoTasks:publisher.videoTasks}]:[];manager.currentTaskId=publisher.videoTasks.length?publisher.id:null;}
bindTask();
window.tagVM={$options:{name:'tag-input'},setCustomTag:business,deleteTag:business,submitTags:cfg.tags.slice(),checkCustomTagLoading:!!cfg.tagLoading};
window.coverVM={openCoverEditor:business,submitCoverEditor:business,defaultCoverIndex:0,coverList:cfg.emptyCoverList?[]:[{main:{origin:null,edited:null,source:''},extra:{origin:null,edited:null,source:''}}]};
if(cfg.stored)localStorage.setItem('bili_videoup_record',JSON.stringify({data:Array.from({length:cfg.stored}),mid:'fixture'}));
</script><div class="upload-wrp"><div class="bcc-upload upload"><div class="bcc-upload-wrapper">视频<div id="nested"></div></div><div id="sibling"></div></div></div>
<input id="aux-video" type="file" accept=".mp4" style="display:none">
<div class="video-title"><div class="video-title-content"><input id="title" placeholder="分区通知"></div></div>
<div class="desc-container"><div class="ql-editor" contenteditable="true" id="description"></div></div>
<div class="fans-input-wrp"><div class="ql-editor" contenteditable="true" id="unrelated">保留动态</div></div>
<div class="tag-container"><div class="tag-input-wrp"><input id="tag"></div><div class="tag-input-wrp" style="display:none"><input></div></div>
<div class="cover-module-main"><div class="cover-slot"><div class="cover-empty">添加封面</div></div></div>
<div class="submit-container"><span class="submit-draft">存草稿</span><span class="submit-add">立即投稿</span></div>
<button id="generic" onclick="++counters.generic">确认</button>
<script>
const host=document.querySelector(cfg.sibling?'#sibling':'#nested');
const video=document.createElement('input');video.type='file';video.accept='.mp4,.mov';video.id='video';video.style.display='none';host.replaceWith(video);
if(cfg.duplicate)video.parentElement.append(video.cloneNode());
publisher.$el=document.querySelector('.upload-wrp');publisher.$el.__vue__=cfg.unknown?{}:publisher;
document.querySelector('.tag-input-wrp').__vue__=tagVM;
document.querySelector('.cover-module-main').__vue__=coverVM;
if(cfg.quillParagraphs){const root=document.querySelector('#description'),q={root,getText:business,editor:{delta:{ops:[{insert:'\n'}]}}};
 root.parentElement.__quill=q;root.innerHTML='<p><br></p>';
 root.oninput=()=>{const value=[...root.children].map(paragraph=>paragraph.textContent).join('\n');
  q.editor.delta.ops=[{insert:value+'\n'}];
  if(cfg.quillMismatch)q.editor.delta.ops=[{insert:'丢失内容\n'}];
  if(cfg.quillEmbed)q.editor.delta.ops=[{insert:{image:'fixture-only'}}];
  if(cfg.quillWrongRoot)q.root=document.querySelector('#unrelated');
 };
}
function renderTags(){const wrp=document.querySelector('.tag-input-wrp');wrp.querySelectorAll('.label-item-v2-container').forEach(e=>e.remove());
 for(const value of tagVM.submitTags){const row=document.createElement('div');row.className='label-item-v2-container';
  const content=document.createElement('span');content.className='label-item-v2-content';content.textContent=value;row.append(content);
  const close=document.createElementNS('http://www.w3.org/2000/svg','svg');close.classList.add('close');close.innerHTML='<path d="M1 1L16 16M16 1L1 16" stroke="black"/>';row.append(close);
  close.onclick=async()=>{++counters.tagClose;
   if(cfg.frameRace){const frame=document.createElement('iframe');frame.src=location.href;document.body.append(frame);await new Promise(resolve=>frame.onload=resolve);}
   tagVM.submitTags=tagVM.submitTags.filter(t=>t!==value);
   if(cfg.nativeRace){publisher.videoTasks=[uploadedTask(publisher.videoTasks[0].file)];bindTask();}
   if(cfg.fileRace){publisher.videoTasks[0].file=new File(['changed-native'],'fixture.mp4',{type:'video/mp4'});}
   if(cfg.finiteAutoRace&&counters.tagClose===1)tagVM.submitTags.push('后来推荐');
   if(cfg.recurringAutoRace)tagVM.submitTags.push('重复推荐'+counters.tagClose);
   renderTags();
  };
  if(cfg.duplicateClose)row.append(close.cloneNode(true));wrp.insertBefore(row,wrp.querySelector('input'));
 }}renderTags();
video.onchange=()=>{++counters.video;publisher.videoTasks=[uploadedTask(video.files[0],1)];bindTask();
 setTimeout(()=>{publisher.videoTasks[0].status=3;publisher.videoTasks[0].uploader.uploader.completed=true;
  if(cfg.autoOnUpload){tagVM.submitTags=['推荐一','推荐二','推荐三'];publisher.ai_tags=[];publisher.custom_tags=tagVM.submitTags.slice();renderTags();}
 },cfg.delay)};
document.querySelector('#tag').onkeydown=e=>{if(e.key!=='Enter')return;e.preventDefault();if(tagVM.checkCustomTagLoading)return;
 ++counters.tag;const value=e.target.value;tagVM.checkCustomTagLoading=true;setTimeout(()=>{
 if(!cfg.rejectTags)tagVM.submitTags.push(value);if(cfg.extraTag)tagVM.submitTags.push('非请求自动标签');tagVM.checkCustomTagLoading=false;e.target.value='';renderTags();
 if(cfg.finalRace&&counters.tag===2)setTimeout(()=>{tagVM.submitTags.push('最终竞态推荐');renderTags();},120);
 },cfg.delay);};
document.querySelector('.submit-draft').onclick=()=>{++counters.draft};
document.querySelector('.submit-add').onclick=()=>{++counters.publish};
if(cfg.draftDisabled)document.querySelector('.submit-draft').classList.add('btn-disabled');
if(cfg.draftLoading)document.querySelector('.submit-draft').classList.add('btn-loading');
function openCover(){
 const dialog=document.createElement('div');dialog.className='cover-editor';
 dialog.innerHTML='<div class="cover-editor-head">封面制作</div><div class="empty-uploader bcc-upload"><input type="file" accept="image/png" id="empty43"></div><div class="empty-uploader bcc-upload"><input type="file" accept="image/png" id="empty169"></div><div class="cover-upload bcc-upload"><div class="bcc-upload-wrapper"><input type="file" accept="image/png, image/jpeg" id="source"></div></div><div class="sync-checkbox-wrapper ratio_4_3"><span>双比例同步改动</span><input type="checkbox"></div><div class="sync-checkbox-wrapper ratio_16_9"><span>双比例同步改动</span><input type="checkbox"></div><div class="cover-editor-button"><div class="button">取消</div><div class="button submit">完成</div></div>';
 const vm={handleSubmit:business,handleCancel:business,handleUploadChange:business,isOverseas:false,isSyncEnabled:false,
  isCoverEditorReady_4_3:false,isCoverEditorReady_16_9:false,isProcessing:false,submitDisabled:false,eventQueue:[],
  imageEditor_4_3:null,imageEditor_16_9:null,activeEditor:cfg.active||null,
  coverForEditor:{origin_4_3:null,origin_16_9:null,source_4_3:'',source_16_9:''}};
 dialog.__vue__=vm;window.editor=vm;document.body.append(dialog);
 if(cfg.staleEditorState){const old='blob:https://member.bilibili.com/previous-cover';
  vm.coverForEditor={origin_4_3:old,origin_16_9:old,source_4_3:'upload',source_16_9:'upload'};
  vm.isSyncEnabled=true;vm.isCoverEditorReady_4_3=true;vm.isCoverEditorReady_16_9=true;
  vm.imageEditor_4_3={};vm.imageEditor_16_9={};}
 if(cfg.duplicateSource)dialog.querySelector('.cover-upload .bcc-upload-wrapper').append(dialog.querySelector('#source').cloneNode());
 if(cfg.overseas)vm.isOverseas=true;
 dialog.querySelectorAll('.empty-uploader input').forEach(x=>x.onchange=()=>++counters.emptyCover);
 dialog.querySelector('#source').onchange=e=>{++counters.cover;if(cfg.staleEditorState)return;const origin=URL.createObjectURL(e.target.files[0]);
  if(!vm.activeEditor||vm.isSyncEnabled){vm.coverForEditor.origin_4_3=origin;vm.coverForEditor.origin_16_9=origin;vm.coverForEditor.source_4_3='upload';vm.coverForEditor.source_16_9='upload';}
  else if(vm.activeEditor==='editor_4_3'){vm.coverForEditor.origin_4_3=origin;vm.coverForEditor.source_4_3='upload';}
  else {vm.coverForEditor.origin_16_9=origin;vm.coverForEditor.source_16_9='upload';}
  setTimeout(()=>{vm.activeEditor=vm.activeEditor||'editor_4_3';vm.imageEditor_4_3=vm.coverForEditor.origin_4_3?{}:null;vm.imageEditor_16_9=vm.coverForEditor.origin_16_9?{}:null;
   vm.isCoverEditorReady_4_3=!!vm.imageEditor_4_3;vm.isCoverEditorReady_16_9=!!vm.imageEditor_16_9;
   if(cfg.pendingCover)vm.isProcessing=true;},cfg.delay);
 };
 for(const ratio of ['4_3','16_9'])dialog.querySelector('.ratio_'+ratio+' > span').onclick=()=>{
  ++counters[ratio==='4_3'?'sync43':'sync169'];vm.isSyncEnabled=true;vm.isProcessing=true;
  setTimeout(()=>{const from=vm.activeEditor==='editor_4_3'?'4_3':'16_9',origin=vm.coverForEditor['origin_'+from];
   vm.coverForEditor.origin_4_3=origin;vm.coverForEditor.origin_16_9=origin;vm.coverForEditor.source_4_3=vm.coverForEditor.source_16_9='upload';
   vm.imageEditor_4_3={};vm.imageEditor_16_9={};vm.isCoverEditorReady_4_3=vm.isCoverEditorReady_16_9=true;vm.isProcessing=false;
  },cfg.delay);
 };
 dialog.querySelector('.submit').onclick=()=>{++counters.complete;vm.submitDisabled=true;
  setTimeout(()=>{if(!cfg.staleCommit)coverVM.coverList=[{main:{origin:cfg.foreignCommitOrigin?'foreign-current-source':vm.coverForEditor.origin_16_9,edited:'https://'+(cfg.coverHost||'i0.hdslb.com')+'/bfs/archive/fixture169.jpg',source:'upload'},extra:{origin:cfg.foreignCommitOrigin?'foreign-current-source':cfg.changedCommitOrigin?'different-source':vm.coverForEditor.origin_4_3,edited:typeof cfg.commitExtraURL==='string'?cfg.commitExtraURL:cfg.singleServerCommit?null:'https://'+(cfg.coverHost||'i0.hdslb.com')+'/bfs/archive/fixture43.jpg',source:cfg.changedCommitSource?'video':'upload'}}];dialog.remove();},cfg.delay);
 };
}
document.querySelector('.cover-empty').onclick=openCover;
if(cfg.notice){const e=document.createElement('div');e.textContent='本地浏览器存在1个未提交的视频';document.querySelector('.upload-wrp').append(e);}
</script>'''


class BiliControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop=asyncio.new_event_loop()
        async def start():
            cls.pw=await async_playwright().start()
            cls.browser=await cls.pw.chromium.launch(headless=True,channel='chromium',args=['--disable-gpu'])
        cls.loop.run_until_complete(start())

    @classmethod
    def tearDownClass(cls):
        async def close():
            await cls.browser.close()
            await cls.pw.stop()
        cls.loop.run_until_complete(close());cls.loop.close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sau-bili-controls-')
        self.video=Path(self.temp.name)/'fixture.mp4';self.video.write_bytes(b'isolated video')
        self.cover=Path(self.temp.name)/'fixture.jpg';self.cover.write_bytes(b'isolated image')

    def tearDown(self):self.temp.cleanup()
    def run_async(self,coroutine):return self.loop.run_until_complete(coroutine)

    async def fixture(self,**options):
        cfg={'tasks':0,'done':False,'records':0,'stored':0,'tags':[],'delay':80,'sibling':False,'duplicate':False,'unknown':False,
             'rejectTags':False,'extraTag':False,'draftDisabled':False,'draftLoading':False,'notice':False,'duplicateSource':False,
             'overseas':False,'pendingCover':False,'staleCommit':False,'active':None,**options}
        context=await self.browser.new_context();external=[]
        async def route(request):
            if request.request.url!=URL:
                external.append(request.request.url);return await request.abort()
            await request.fulfill(content_type='text/html;charset=utf-8',body=HTML.replace('CONFIG',json.dumps(cfg,ensure_ascii=False)))
        await context.route('**/*',route);page=await context.new_page();await page.goto(URL)
        await page.locator('#video').first.wait_for(state='attached')
        return context,page,external

    async def counters(self,page):return await page.evaluate('()=>counters',isolated_context=False)

    def test_nested_and_sibling_canonical_inputs_ignore_auxiliary(self):
        async def scenario():
            for sibling in (False,True):
                context,page,external=await self.fixture(sibling=sibling)
                try:
                    locator=await controls.bili_empty_video_control(page)
                    await locator.set_input_files(str(self.video))
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):await controls.bili_wait_uploaded(page,timeout=1)
                    counts=await self.counters(page);self.assertEqual(counts['video'],1)
                    self.assertEqual(await page.locator('#aux-video').evaluate('e=>e.files.length'),0)
                    self.assertFalse(external)
                finally:await context.close()
        self.run_async(scenario())

    def test_old_task_record_cache_notice_and_unknown_reject_without_import(self):
        async def scenario():
            for options in ({'tasks':1,'done':True},{'records':1},{'stored':1},{'notice':True},{'unknown':True},{'duplicate':True}):
                with self.subTest(options=options):
                    context,page,_=await self.fixture(**options)
                    try:
                        with self.assertRaises(DraftInterrupted):await controls.bili_empty_video_control(page)
                        self.assertEqual((await self.counters(page))['video'],0)
                        self.assertEqual(await page.locator('#video').first.evaluate('e=>e.files.length'),0)
                        if options.get('stored'):self.assertTrue(await page.evaluate("()=>!!localStorage.getItem('bili_videoup_record')",isolated_context=False))
                    finally:await context.close()
        self.run_async(scenario())

    def test_content_quill_and_each_asynchronous_tag_exact(self):
        async def scenario():
            context,page,external=await self.fixture(tasks=1,done=True,delay=140)
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'完整标题','第一行\n第二行',['标签一','标签二','标签三'],tag_timeout=1)
                self.assertEqual(await page.locator('#title').input_value(),'完整标题')
                self.assertEqual((await page.locator('#description').inner_text()).rstrip('\n'),'第一行\n第二行')
                self.assertEqual(await page.locator('#unrelated').inner_text(),'保留动态')
                self.assertEqual(await page.evaluate('()=>tagVM.submitTags',isolated_context=False),['标签一','标签二','标签三'])
                self.assertEqual((await self.counters(page))['tag'],3);self.assertFalse(external)
            finally:await context.close()
        self.run_async(scenario())

    def test_same_root_quill_two_paragraphs_preserve_each_user_empty_line(self):
        async def scenario():
            for description in ('第一行\n第二行 #科普 #验收','第一行\n\n第二行 #科普 #验收','\n第一行\n\n第二行\n'):
                context,page,_=await self.fixture(tasks=1,done=True,quillParagraphs=True)
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                        await controls.bili_fill_content(page,'标题',description,['标签一'],tag_timeout=.5)
                    self.assertEqual(await controls._description_text(page.main_frame),description)
                    self.assertNotEqual(await page.locator('#description').inner_text(),description)
                    self.assertEqual((await page.locator('#unrelated').inner_text()),'保留动态')
                    self.assertEqual((await self.counters(page))['draft'],0)
                finally:await context.close()
        self.run_async(scenario())

    def test_quill_unknown_embed_mismatch_or_wrong_root_rejects_before_tags(self):
        async def scenario():
            for options in ({'quillMismatch':True},{'quillEmbed':True},{'quillWrongRoot':True}):
                context,page,_=await self.fixture(tasks=1,done=True,quillParagraphs=True,**options)
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_fill_content(page,'标题','第一行\n第二行 #科普 #验收',['标签一'],tag_timeout=.5)
                    self.assertEqual((await self.counters(page))['tag'],0)
                    self.assertEqual((await self.counters(page))['draft'],0)
                finally:await context.close()
        self.run_async(scenario())

    def test_reopened_quill_uses_same_paragraph_semantics_and_rejects_lost_empty_lines(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,quillParagraphs=True)
            description='第一行\n\n第二行 #科普 #验收\n'
            main='https://i0.hdslb.com/bfs/archive/restored169.jpg';extra='https://i0.hdslb.com/bfs/archive/restored43.jpg'
            expected={'platform_fields':{'title':'恢复标题','description':description,'tags':['标签一']}}
            record={'full_fields_source':'normal_draft_view','title':'恢复标题','description':description,'tags':['标签一'],
                'video_ids':['77','fixture-native'],'video_count':1,'cover_slots':{'cover':[main],'cover43':[extra]}}
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'恢复标题',description,['标签一'],tag_timeout=.5)
                await page.evaluate('values=>{coverVM.coverList=[{main:{edited:values[0]},extra:{edited:values[1]}}]}',[main,extra],isolated_context=False)
                self.assertTrue(await controls.bili_reopened_ui_verified(page,record,expected))
                self.assertFalse(await controls.bili_reopened_ui_verified(page,{**record,'description':description.rstrip('\n')},expected))
                self.assertFalse(await controls.bili_reopened_ui_verified(page,{**record,'description':description.replace('\n\n','\n')},expected))
            finally:await context.close()
        self.run_async(scenario())

    def test_rejected_first_tag_stops_before_next_tag(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,rejectTags=True)
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_fill_content(page,'标题','文案',['标签一','标签二'],tag_timeout=.3)
                self.assertEqual((await self.counters(page))['tag'],1)
                self.assertEqual(await page.evaluate('()=>tagVM.submitTags',isolated_context=False),[])
            finally:await context.close()
        self.run_async(scenario())

    def test_existing_and_added_automatic_tags_reject_without_delete(self):
        async def scenario():
            for options in ({'tags':['旧标签']},{'extraTag':True}):
                context,page,_=await self.fixture(**{'tasks':1,'done':True,**options})
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_fill_content(page,'标题','文案',['请求标签'],tag_timeout=.5)
                    values=await page.evaluate('()=>tagVM.submitTags',isolated_context=False)
                    self.assertIn('旧标签' if 'tags' in options else '非请求自动标签',values)
                    self.assertEqual((await self.counters(page))['draft'],0)
                finally:await context.close()
        self.run_async(scenario())

    async def fresh_uploaded(self,page):
        control=await controls.bili_empty_video_control(page)
        await control.set_input_files(str(self.video))
        with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
            await controls.bili_wait_uploaded(page,timeout=1)

    def test_fresh_native_auto_three_becomes_exact_two_through_normal_close_once(self):
        async def scenario():
            context,page,external=await self.fixture(autoOnUpload=True)
            try:
                await self.fresh_uploaded(page)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'请求标题','完整简介',['请求一','请求二'],tag_timeout=1)
                self.assertEqual(await page.evaluate('()=>tagVM.submitTags',isolated_context=False),['请求一','请求二'])
                counts=await self.counters(page);self.assertEqual((counts['tagClose'],counts['tag'],counts['video']),(3,2,1))
                self.assertIsNone(page._sau_bili_native_import)
                await page.evaluate("()=>{tagVM.submitTags.push('后续旧标签');renderTags()}",isolated_context=False)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_fill_content(page,'后续标题','简介',['请求一','请求二'],tag_timeout=.5)
                self.assertEqual((await self.counters(page))['tagClose'],3)
                self.assertEqual(await page.locator('#title').input_value(),'请求标题')
                self.assertFalse(external)
            finally:await context.close()
        self.run_async(scenario())

    def test_active_checkpoint_restore_allows_exact_normal_tag_replacement(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,tags=['推荐一','推荐二','推荐三'])
            try:
                sha='1'*64
                page._sau_draft_evidence=SimpleNamespace(closed=False,_checkpoint_restored_at=1,platform='bilibili',page=page,
                    expected={'video_sha256':sha},upload_links=lambda:[{'kind':'video','sha256':sha,
                        'complete':True,'ids':['77','fixture-native']}])
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'恢复标题','简介',['请求一','请求二'],tag_timeout=1)
                self.assertEqual((await self.counters(page))['tagClose'],3)
                self.assertEqual((await self.counters(page))['video'],0)
                self.assertEqual(await page.evaluate('()=>tagVM.submitTags',isolated_context=False),['请求一','请求二'])
            finally:await context.close()
        self.run_async(scenario())

    def test_restore_flag_without_same_sealed_video_proof_never_deletes(self):
        async def scenario():
            for ids in ([],['77','foreign-native']):
                context,page,_=await self.fixture(tasks=1,done=True,tags=['保留推荐'])
                try:
                    sha='1'*64
                    page._sau_draft_evidence=SimpleNamespace(closed=False,_checkpoint_restored_at=1,platform='bilibili',page=page,
                        expected={'video_sha256':sha},upload_links=lambda:[{'kind':'video','sha256':sha,
                            'complete':True,'ids':ids}])
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_fill_content(page,'标题','简介',['请求一'],tag_timeout=.5)
                    self.assertEqual((await self.counters(page))['tagClose'],0)
                    self.assertEqual(await page.locator('#title').input_value(),'')
                finally:await context.close()
        self.run_async(scenario())

    def test_fresh_loading_or_duplicate_close_rejects_before_any_delete_or_content(self):
        async def scenario():
            for options in ({'tagLoading':True},{'duplicateClose':True}):
                context,page,_=await self.fixture(autoOnUpload=True,**options)
                try:
                    await self.fresh_uploaded(page)
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_fill_content(page,'标题','简介',['请求一','请求二'],tag_timeout=.5)
                    self.assertEqual((await self.counters(page))['tagClose'],0)
                    self.assertEqual(await page.locator('#title').input_value(),'')
                finally:await context.close()
        self.run_async(scenario())

    def test_native_task_file_or_official_frame_change_stops_after_one_normal_close(self):
        async def scenario():
            for options in ({'nativeRace':True},{'fileRace':True},{'frameRace':True}):
                context,page,_=await self.fixture(autoOnUpload=True,**options)
                try:
                    await self.fresh_uploaded(page)
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_fill_content(page,'标题','简介',['请求一','请求二'],tag_timeout=.5)
                    self.assertEqual((await self.counters(page))['tagClose'],1)
                    self.assertEqual((await self.counters(page))['tag'],0)
                    self.assertEqual(await page.locator('#title').input_value(),'')
                finally:await context.close()
        self.run_async(scenario())

    def test_old_import_permission_cannot_follow_a_new_document_or_native_upload(self):
        async def scenario():
            context,page,_=await self.fixture(autoOnUpload=True)
            try:
                await self.fresh_uploaded(page)
                await page.goto(URL)
                await page.locator('#video').set_input_files(str(self.video))
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_wait_uploaded(page,timeout=1)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_fill_content(page,'标题','简介',['请求一','请求二'],tag_timeout=.5)
                self.assertEqual((await self.counters(page))['tagClose'],0)
                self.assertEqual(await page.locator('#title').input_value(),'')
            finally:await context.close()
        self.run_async(scenario())

    def test_finite_recommendation_update_is_read_back_and_removed_normally(self):
        async def scenario():
            context,page,_=await self.fixture(autoOnUpload=True,finiteAutoRace=True)
            try:
                await self.fresh_uploaded(page)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'标题','简介',['请求一','请求二'],tag_timeout=1)
                self.assertEqual((await self.counters(page))['tagClose'],4)
                self.assertEqual(await page.evaluate('()=>tagVM.submitTags',isolated_context=False),['请求一','请求二'])
            finally:await context.close()
        self.run_async(scenario())

    def test_repeated_recommendation_updates_hit_bound_without_truncating(self):
        async def scenario():
            context,page,_=await self.fixture(autoOnUpload=True,recurringAutoRace=True)
            try:
                await self.fresh_uploaded(page)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_fill_content(page,'标题','简介',['请求一','请求二'],tag_timeout=.5)
                self.assertEqual((await self.counters(page))['tagClose'],10)
                self.assertTrue(await page.evaluate('()=>tagVM.submitTags.length>0',isolated_context=False))
                self.assertEqual((await self.counters(page))['tag'],0)
            finally:await context.close()
        self.run_async(scenario())

    def test_extra_tag_during_final_stability_is_preserved_and_rejected(self):
        async def scenario():
            context,page,_=await self.fixture(autoOnUpload=True,finalRace=True)
            try:
                await self.fresh_uploaded(page)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_fill_content(page,'标题','简介',['请求一','请求二'],tag_timeout=1)
                self.assertIn('最终竞态推荐',await page.evaluate('()=>tagVM.submitTags',isolated_context=False))
                self.assertEqual((await self.counters(page))['tagClose'],3)
                self.assertEqual((await self.counters(page))['draft'],0)
            finally:await context.close()
        self.run_async(scenario())

    def test_exact_draft_span_never_publishes_or_clicks_generic(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True)
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):await controls.bili_draft_action(page)
                counts=await self.counters(page);self.assertEqual(counts['draft'],1)
                self.assertEqual(counts['publish'],0);self.assertEqual(counts['generic'],0)
            finally:await context.close()
        self.run_async(scenario())

    def test_draft_disabled_loading_and_incomplete_never_clicked(self):
        async def scenario():
            for options in ({'draftDisabled':True},{'draftLoading':True},{'done':False}):
                context,page,_=await self.fixture(**{'tasks':1,'done':True,**options})
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):await controls.bili_draft_action(page)
                    self.assertEqual((await self.counters(page))['draft'],0)
                finally:await context.close()
        self.run_async(scenario())

    def test_single_source_normal_dual_ratio_sync_before_one_completion(self):
        async def scenario():
            for active in (None,'editor_4_3','editor_16_9'):
                context,page,external=await self.fixture(tasks=1,done=True,active=active)
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):await controls.bili_set_cover(page,str(self.cover),timeout=2)
                    counts=await self.counters(page);self.assertEqual(counts['cover'],1)
                    self.assertEqual(counts['emptyCover'],0);self.assertEqual(counts['complete'],1)
                    self.assertEqual(counts['sync43']+counts['sync169'],1)
                    self.assertEqual(counts['sync169'],1 if active=='editor_16_9' else 0)
                    self.assertEqual(counts['draft'],0);self.assertEqual(counts['generic'],0);self.assertFalse(external)
                finally:await context.close()
        self.run_async(scenario())

    def test_empty_initial_cover_is_only_a_bound_pre_import_baseline(self):
        async def scenario():
            context,page,external=await self.fixture(tasks=1,done=True,emptyCoverList=True)
            try:
                with self.assertRaises(DraftInterrupted):await controls._committed_cover(page.main_frame)
                initial=await controls._committed_cover(page.main_frame,allow_empty=True)
                self.assertFalse(initial['uploaded'] or initial['server_images'] or initial['same_origin'])
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_set_cover(page,str(self.cover),timeout=2)
                final=await controls._committed_cover(page.main_frame)
                self.assertTrue(final['uploaded'] and final['server_images'] and final['same_origin'])
                self.assertNotEqual(initial['signature'],final['signature'])
                counts=await self.counters(page)
                self.assertEqual((counts['cover'],counts['complete'],counts['draft']),(1,1,0))
                self.assertFalse(external)
            finally:await context.close()
        self.run_async(scenario())

    def test_identical_server_urls_require_this_normal_imported_source_commit(self):
        async def scenario():
            context,page,external=await self.fixture(tasks=1,done=True,coverHost='archive.biliimg.com')
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_set_cover(page,str(self.cover),timeout=2)
                    first=await controls._committed_cover(page.main_frame)
                    await controls.bili_set_cover(page,str(self.cover),timeout=2)
                    second=await controls._committed_cover(page.main_frame)
                self.assertEqual(first['signature'],second['signature'])
                self.assertNotEqual(first['origin_signature'],second['origin_signature'])
                self.assertTrue(second['uploaded'] and second['server_images'] and second['same_origin'])
                ready=await page.evaluate("async()=>{const value=[editor.coverForEditor.origin_4_3,editor.coverForEditor.origin_16_9];const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));return [...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,'0')).join('')}",isolated_context=False)
                self.assertEqual(second['origin_signature'],ready)
                counts=await self.counters(page)
                self.assertEqual((counts['cover'],counts['complete'],counts['draft']),(2,2,0))
                self.assertFalse(external)
            finally:await context.close()
        self.run_async(scenario())

    def test_identical_server_urls_with_old_or_foreign_source_never_complete(self):
        async def scenario():
            for failure in ('staleCommit','foreignCommitOrigin'):
                context,page,_=await self.fixture(tasks=1,done=True,coverHost='archive.biliimg.com')
                output=io.StringIO()
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                        await controls.bili_set_cover(page,str(self.cover),timeout=2)
                    before=await controls._committed_cover(page.main_frame)
                    await page.evaluate('key=>{cfg[key]=true}',failure,isolated_context=False)
                    with redirect_stdout(output),patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_set_cover(page,str(self.cover),timeout=.7)
                    after=await controls._committed_cover(page.main_frame)
                    self.assertEqual(before['signature'],after['signature'])
                    self.assertTrue(after['uploaded'] and after['server_images'] and after['same_origin'])
                    if failure=='staleCommit':self.assertEqual(before['origin_signature'],after['origin_signature'])
                    diagnostic=json.loads(output.getvalue().strip().split('=',1)[1])
                    self.assertFalse(diagnostic['signature_changed'] or diagnostic['current_source_match'])
                    self.assertTrue(diagnostic['uploaded'] and diagnostic['server_images'] and diagnostic['same_origin'])
                    self.assertNotIn('foreign-current-source',output.getvalue())
                    self.assertNotRegex(output.getvalue(),r'\b[0-9a-f]{64}\b')
                    counts=await self.counters(page)
                    self.assertEqual((counts['cover'],counts['complete'],counts['draft']),(2,2,0))
                finally:await context.close()
        self.run_async(scenario())

    def test_stale_ready_editor_state_does_not_count_as_this_normal_import(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,staleEditorState=True)
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_set_cover(page,str(self.cover),timeout=.5)
                counts=await self.counters(page)
                self.assertEqual((counts['cover'],counts['complete'],counts['draft']),(1,0,0))
                state=await controls._cover_state(page.main_frame)
                self.assertTrue(state['ready43'] and state['ready169'] and state['same_origin'])
                self.assertTrue(state['source43_upload'] and state['source169_upload'])
            finally:await context.close()
        self.run_async(scenario())

    def test_archive_cover_format_is_shared_by_normal_commit_and_reopened_ui(self):
        from uploader.bili_evidence import bili_cover_identity
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,emptyCoverList=True,coverHost='archive.biliimg.com')
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'标题','完整正文',['知识'],tag_timeout=.5)
                    await controls.bili_set_cover(page,str(self.cover),timeout=2)
                committed=await controls._committed_cover(page.main_frame)
                self.assertTrue(committed['uploaded'] and committed['server_images'] and committed['same_origin'])
                values=await page.evaluate('()=>[coverVM.coverList[0].main.edited,coverVM.coverList[0].extra.edited]',isolated_context=False)
                expected={'platform_fields':{'title':'标题','description':'完整正文','tags':['知识']}}
                record={'full_fields_source':'normal_draft_view','title':'标题','description':'完整正文','tags':['知识'],
                    'video_ids':['77','fixture-native'],'cover_slots':{'cover':[bili_cover_identity(values[0])],'cover43':[bili_cover_identity(values[1])]}}
                self.assertTrue(await controls.bili_reopened_ui_verified(page,record,expected))
                bad_urls=('https://evilarchive.biliimg.com/bfs/archive/image.jpg','https://archive.biliimg.com.evil.test/bfs/archive/image.jpg',
                    'https://other.biliimg.com/bfs/archive/image.jpg','https://archive.biliimg.com:443/bfs/archive/image.jpg',
                    'https://user:secret@archive.biliimg.com/bfs/archive/image.jpg',values[1]+'?token=secret',values[1]+'#fragment','blob:'+values[1])
                for bad in bad_urls:
                    await page.evaluate('value=>{coverVM.coverList[0].extra.edited=value}',bad,isolated_context=False)
                    self.assertFalse((await controls._committed_cover(page.main_frame))['server_images'])
                    self.assertFalse(await controls.bili_reopened_ui_verified(page,record,expected))
                self.assertEqual((await self.counters(page))['draft'],0)
            finally:await context.close()
        self.run_async(scenario())

    def test_empty_initial_wrong_index_or_ambiguous_nonempty_slot_never_opens(self):
        async def scenario():
            changes=("coverVM.defaultCoverIndex=1",
                     "document.querySelector('.cover-slot').append(document.querySelector('.cover-empty').cloneNode(true))",
                     "document.querySelector('.cover-empty').className='cover-img'",
                     "document.querySelector('.cover-module-main').after(document.querySelector('.cover-module-main').cloneNode(true))")
            for change in changes:
                context,page,_=await self.fixture(tasks=1,done=True,emptyCoverList=True)
                try:
                    await page.evaluate('()=>{'+change+'}',isolated_context=False)
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_set_cover(page,str(self.cover),timeout=.5)
                    counts=await self.counters(page)
                    self.assertEqual((counts['cover'],counts['complete'],counts['draft']),(0,0,0))
                    self.assertEqual(await page.locator('.cover-editor').count(),0)
                finally:await context.close()
        self.run_async(scenario())

    def test_empty_initial_does_not_license_empty_or_incomplete_final_dual_cover(self):
        async def scenario():
            for options in ({'staleCommit':True},{'singleServerCommit':True},{'changedCommitSource':True},{'changedCommitOrigin':True}):
                context,page,_=await self.fixture(tasks=1,done=True,emptyCoverList=True,**options)
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                        await controls.bili_set_cover(page,str(self.cover),timeout=.7)
                    counts=await self.counters(page)
                    self.assertEqual((counts['cover'],counts['complete'],counts['draft']),(1,1,0))
                finally:await context.close()
        self.run_async(scenario())

    def test_cover_success_never_uses_timeout_diagnostic_to_complete(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,emptyCoverList=True)
            diagnostic=AsyncMock()
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),patch.object(controls,'_cover_final_diagnostic',diagnostic):
                    await controls.bili_set_cover(page,str(self.cover),timeout=2)
                diagnostic.assert_not_awaited()
                self.assertEqual((await self.counters(page))['complete'],1)
            finally:await context.close()
        self.run_async(scenario())

    def test_final_cover_diagnostic_identifies_failed_gates_without_protocol_values(self):
        async def scenario():
            cases=({'commitExtraURL':'https://i0.hdslb.com/bfs/archive/do-not-log-marker.jpg?token=do-not-log-marker'},
                   {'changedCommitSource':True},{'changedCommitOrigin':True})
            for options in cases:
                context,page,_=await self.fixture(tasks=1,done=True,emptyCoverList=True,**options)
                output=io.StringIO()
                try:
                    with redirect_stdout(output),patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted) as caught:
                        await controls.bili_set_cover(page,str(self.cover),timeout=.7)
                    self.assertEqual(caught.exception.status,'result_unknown')
                    lines=output.getvalue().splitlines();self.assertEqual(len(lines),1)
                    diagnostic=json.loads(lines[0].split('=',1)[1])
                    self.assertEqual(diagnostic['event'],'bilibili_cover_final_timeout')
                    self.assertEqual(diagnostic['editor_visible_count'],0)
                    self.assertGreater(diagnostic['committed_read_count'],0)
                    self.assertTrue(diagnostic['committed_read'] and diagnostic['signature_changed'] and diagnostic['shape_available'])
                    shape=diagnostic['committed_shape']
                    self.assertEqual((shape['visible_roots'],shape['owner_count'],shape['list_count']),(1,1,1))
                    self.assertTrue(shape['index_zero'])
                    failed='server_images' if 'commitExtraURL' in options else 'uploaded' if 'changedCommitSource' in options else 'same_origin'
                    self.assertFalse(diagnostic[failed])
                    for gate in {'server_images','uploaded','same_origin'}-{failed}:self.assertTrue(diagnostic[gate])
                    if 'commitExtraURL' in options:
                        self.assertFalse(shape['extra']['edited']['no_query'])
                        self.assertTrue(shape['extra']['edited']['official_host'] and shape['extra']['edited']['bfs_path'])
                    if 'changedCommitSource' in options:self.assertFalse(shape['extra']['source_upload'])
                    if 'changedCommitOrigin' in options:self.assertFalse(shape['same_origin'])
                    for value in ('do-not-log-marker','https://','fixture169.jpg','fixture43.jpg','different-source'):
                        self.assertNotIn(value,output.getvalue())
                    self.assertNotRegex(output.getvalue(),r'\b[0-9a-f]{64}\b')
                    self.assertEqual((await self.counters(page))['draft'],0)
                finally:await context.close()
        self.run_async(scenario())

    def test_timeout_diagnostic_read_error_or_unknown_shape_never_serializes_values(self):
        async def scenario():
            for read in (AsyncMock(return_value={'url':'https://do-not-log-marker','id':'do-not-log-marker'}),
                         AsyncMock(side_effect=RuntimeError('https://do-not-log-marker'))):
                output=io.StringIO()
                with redirect_stdout(output):
                    await controls._cover_final_diagnostic(SimpleNamespace(evaluate=read),
                        {'uploaded':'do-not-log-marker','committed_read_count':'do-not-log-marker','secret':'do-not-log-marker'})
                diagnostic=json.loads(output.getvalue().split('=',1)[1])
                self.assertFalse(diagnostic['shape_available'])
                self.assertIsNone(diagnostic['committed_shape'])
                self.assertFalse(diagnostic['uploaded'])
                self.assertEqual(diagnostic['committed_read_count'],0)
                self.assertNotIn('do-not-log-marker',output.getvalue())
                self.assertNotIn('secret',diagnostic)
        self.run_async(scenario())

    def test_ambiguous_source_and_overseas_reject_before_cover_import(self):
        async def scenario():
            for options in ({'duplicateSource':True},{'overseas':True}):
                context,page,_=await self.fixture(tasks=1,done=True,**options)
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):await controls.bili_set_cover(page,str(self.cover),timeout=.5)
                    counts=await self.counters(page);self.assertEqual(counts['cover'],0);self.assertEqual(counts['complete'],0)
                finally:await context.close()
        self.run_async(scenario())

    def test_cover_pending_or_stale_commit_is_not_complete(self):
        async def scenario():
            for options in ({'pendingCover':True},{'staleCommit':True}):
                context,page,_=await self.fixture(tasks=1,done=True,**options)
                try:
                    with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):await controls.bili_set_cover(page,str(self.cover),timeout=.5)
                    counts=await self.counters(page);self.assertEqual(counts['draft'],0)
                    self.assertEqual(counts['complete'],0 if options.get('pendingCover') else 1)
                finally:await context.close()
        self.run_async(scenario())

    def test_execution_preserves_expected_checkpoint_and_single_save_handoff(self):
        import draft_execution as execution
        async def scenario():
            context,page,_=await self.fixture()
            expected={'video':str(self.video),'title':'明确标题','description':'完整文案','tags':['标签一','标签二'],'cover':str(self.cover)}
            checkpoint=AsyncMock();save=AsyncMock(return_value={'fixture':'handoff only, not persisted'})
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),patch.object(execution,'checkpoint_after_upload',checkpoint),patch.object(execution,'save_draft',save):
                    result=await execution.bilibili_draft(expected,Path(self.temp.name)/'receipt.json',page=page)
                checkpoint.assert_awaited_once_with(page);save.assert_awaited_once()
                self.assertIs(save.await_args.kwargs['expected'],expected)
                self.assertEqual(result,{'fixture':'handoff only, not persisted'})
                counts=await self.counters(page)
                self.assertEqual(counts['video'],1);self.assertEqual(counts['cover'],1);self.assertEqual(counts['complete'],1)
                self.assertEqual(counts['tag'],2);self.assertEqual(counts['publish'],0)
            finally:await context.close()
        self.run_async(scenario())

    def test_execution_existing_material_stops_before_content_or_save(self):
        import draft_execution as execution
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True,records=1,tags=['保留标签'])
            expected={'video':str(self.video),'title':'新标题','description':'新文案','tags':['新标签']}
            checkpoint=AsyncMock();save=AsyncMock()
            try:
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),patch.object(execution,'checkpoint_after_upload',checkpoint),patch.object(execution,'save_draft',save),self.assertRaises(DraftInterrupted):
                    await execution.bilibili_draft(expected,Path(self.temp.name)/'receipt.json',page=page)
                checkpoint.assert_not_awaited();save.assert_not_awaited()
                self.assertEqual(await page.locator('#title').input_value(),'')
                self.assertEqual(await page.evaluate('()=>tagVM.submitTags',isolated_context=False),['保留标签'])
                self.assertEqual((await self.counters(page))['video'],0)
            finally:await context.close()
        self.run_async(scenario())


    async def empty_entrance(self,page,**options):
        await page.evaluate("""o=>{const host=document.querySelector('.upload-wrp');document.body.classList.add('video-entrance');
         const config={tasks:o.tasks?[{videoTasks:[]}]:[],currentTaskId:o.current?'old':null,createTask(){throw Error('not called by state reader')}};
         host.__vue__={$el:document.body,record:o.record?[{}]:[],$recordConfig:{data:[]},$taskConfig:config,
           uploadDisabled:!!o.disabled,handleChange(){},initRecord(){},recovery(){}};
         if(o.noCreate)delete config.createTask;
        }""",options,isolated_context=False)

    def test_real_empty_entrance_without_full_publisher_is_recognized(self):
        async def scenario():
            context,page,_=await self.fixture(unknown=True)
            try:
                await self.empty_entrance(page)
                locator=await controls.bili_empty_video_control(page)
                self.assertEqual(await locator.get_attribute('id'),'video')
                self.assertEqual((await self.counters(page))['video'],0)
                state=await controls._state(page.main_frame)
                self.assertEqual((state['task_count'],state['record_count'],state['stored_count']),(0,0,0))
            finally:await context.close()
        self.run_async(scenario())

    def test_empty_entrance_nonempty_manager_record_unknown_or_disabled_rejects(self):
        async def scenario():
            for config in ({'tasks':True},{'record':True},{'current':True},{'disabled':True},{'noCreate':True}):
                context,page,_=await self.fixture(unknown=True)
                try:
                    await self.empty_entrance(page,**config)
                    with self.assertRaises(DraftInterrupted):await controls.bili_empty_video_control(page)
                    self.assertEqual((await self.counters(page))['video'],0)
                finally:await context.close()
        self.run_async(scenario())

    async def sibling_publisher(self,page):
        await page.evaluate("""()=>{
          const root=document.querySelector('.upload-wrp'),entrance=document.createElement('div'),host=document.createElement('div');
          entrance.className='video-entrance';host.className='video-basic-wrp';
          root.before(entrance);entrance.append(root);entrance.after(host);delete root.__vue__;
          publisher.$el=host;host.__vue__=publisher;
          window.entrance={$el:entrance,$taskConfig:manager,$recordConfig:publisher.$recordConfig,record:[],uploadDisabled:false,
            handleChange:()=>{throw Error('business forbidden')},initRecord:()=>{throw Error('business forbidden')},recovery:()=>{throw Error('business forbidden')}};
          entrance.__vue__=window.entrance;
        }""",isolated_context=False)

    def test_live_sibling_publisher_upload_is_bound_to_the_same_current_task(self):
        async def scenario():
            context,page,external=await self.fixture(delay=140)
            try:
                await self.sibling_publisher(page)
                locator=await controls.bili_empty_video_control(page)
                await locator.set_input_files(str(self.video))
                pending=await controls._state(page.main_frame)
                self.assertEqual((pending['task_count'],pending['done_count'],pending['selected_count']),(1,0,1))
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_wait_uploaded(page,timeout=1)
                done=await controls._state(page.main_frame)
                self.assertEqual((done['task_count'],done['done_count'],done['selected_count']),(1,1,1))
                with self.assertRaises(DraftInterrupted):await controls.bili_empty_video_control(page)
                self.assertEqual((await self.counters(page))['video'],1)
                self.assertFalse(external)
            finally:await context.close()
        self.run_async(scenario())

    def test_sibling_publisher_foreign_ambiguous_or_missing_owners_fail_closed(self):
        async def scenario():
            mutations={
                'duplicate publisher':"const host=document.createElement('div');document.body.append(host);host.__vue__={...publisher,$el:host}",
                'foreign manager':"publisher.$taskConfig={...manager,tasks:manager.tasks.slice()}",
                'wrong current task':"manager.currentTaskId='foreign'",
                'foreign task array':"manager.tasks[0].videoTasks=publisher.videoTasks.slice()",
                'wrong publisher task':"publisher.id='foreign'",
                'foreign record config':"entrance.$recordConfig={data:[]}",
                'nonempty entrance without publisher':"delete publisher.$el.__vue__",
                'disconnected publisher':"publisher.$el.remove()",
                'multiple manager tasks':"manager.tasks.push({...manager.tasks[0],id:'second'})",
            }
            for name,script in mutations.items():
                with self.subTest(name=name):
                    context,page,_=await self.fixture(tasks=1,done=True)
                    try:
                        await self.sibling_publisher(page)
                        await page.evaluate('()=>{'+script+'}',isolated_context=False)
                        with self.assertRaises(DraftInterrupted):await controls._state(page.main_frame)
                        self.assertEqual((await self.counters(page))['video'],0)
                        self.assertEqual((await self.counters(page))['draft'],0)
                    finally:await context.close()
        self.run_async(scenario())

    def test_sibling_publisher_incomplete_task_does_not_finish_upload(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=False)
            try:
                await self.sibling_publisher(page)
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()),self.assertRaises(DraftInterrupted):
                    await controls.bili_wait_uploaded(page,timeout=.25)
                self.assertEqual((await controls._state(page.main_frame))['done_count'],0)
                self.assertEqual((await self.counters(page))['draft'],0)
            finally:await context.close()
        self.run_async(scenario())

    def test_restored_composer_actual_fields_and_media_consumer_reject_changes(self):
        async def scenario():
            context,page,_=await self.fixture(tasks=1,done=True)
            main='https://i0.hdslb.com/bfs/archive/restored169.jpg'
            extra='https://i0.hdslb.com/bfs/archive/restored43.jpg'
            expected={'platform_fields':{'title':'恢复标题','description':'完整恢复正文','tags':['标签一']}}
            record={'full_fields_source':'normal_draft_view','title':'恢复标题','description':'完整恢复正文','tags':['标签一'],
                    'video_ids':['77','restored-native'],'video_count':1,'cover_slots':{'cover':[main],'cover43':[extra]}}
            async def restore():
                with patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()):
                    await controls.bili_fill_content(page,'恢复标题','完整恢复正文',['标签一'],tag_timeout=1)
                await page.evaluate("""values=>{publisher.videoTasks=[{status:3,cid:77,filename:'restored-native'}];bindTask();
                  coverVM.coverList=[{main:{edited:values[0]},extra:{edited:values[1]}}];tagVM.submitTags=['标签一'];}""",[main,extra],isolated_context=False)
            try:
                await restore();self.assertTrue(await controls.bili_reopened_ui_verified(page,record,expected))
                mutations=["document.querySelector('#description').textContent='旧正文'", "tagVM.submitTags.push('额外标签')",
                           "publisher.videoTasks[0].filename='foreign-native'", "publisher.videoTasks.push({status:3,cid:88,filename:'other'})",
                           "[coverVM.coverList[0].main.edited,coverVM.coverList[0].extra.edited]=[coverVM.coverList[0].extra.edited,coverVM.coverList[0].main.edited]"]
                for script in mutations:
                    with self.subTest(script=script):
                        await page.evaluate(script,isolated_context=False)
                        self.assertFalse(await controls.bili_reopened_ui_verified(page,record,expected))
                        await page.evaluate("()=>{tagVM.submitTags=['标签一'];publisher.videoTasks=[{status:3,cid:77,filename:'restored-native'}];bindTask()}",isolated_context=False)
                        await restore()
            finally:await context.close()
        self.run_async(scenario())


if __name__=='__main__':unittest.main()
