"""Bounded normal Bilibili creator controls; no publishing/API operations.

The actual 2026-10-04 mounted editor confirms the nested video input, title,
description Quill, tag-input Vue state and submit-draft span. The archived BCC
template also has a sibling native input; both exact structures are supported.
The actual cover dialog also confirms its single source uploader, two aspect
editors, synchronization label and non-button completion control. These
helpers only prove normal control completion; the caller owns media evidence.
"""

import asyncio
import json
import time
from urllib.parse import urlsplit
from patchright.async_api import Error as BrowserError

from uploader.draft_session import DraftInterrupted
from uploader.bili_evidence import BILI_COVER_FORMAT_JS, BILI_COVER_HOST_PATTERN
from uploader.draft_progress import operation as progress_operation, part as progress_part, set_plan as set_progress_plan


VIDEO_INPUT = (
    '.upload-wrp .upload.bcc-upload > input[type="file"][accept*=".mp4"],'
    '.upload-wrp .upload.bcc-upload > .bcc-upload-wrapper > input[type="file"][accept*=".mp4"]'
)
TITLE = '.video-title .video-title-content input'
DESCRIPTION = '.desc-container .ql-editor[contenteditable="true"]'
TAG = '.tag-container .tag-input-wrp input'
TAG_ROWS = '.tag-container .tag-input-wrp .label-item-v2-container'
DRAFT = '.submit-container > span.submit-draft'
COVER_SLOT = '.cover-module-main .cover-slot'
COVER_INPUT = (
    '.cover-editor .cover-upload.bcc-upload > input[type="file"][accept*="image"],'
    '.cover-editor .cover-upload.bcc-upload > .bcc-upload-wrapper > input[type="file"][accept*="image"]'
)
COVER_DONE = '.cover-editor .cover-editor-button > .button.submit'
COVER_SYNC = '.cover-editor .sync-checkbox-wrapper > span'

_COVER_STATE = r"""async () => {
 try{
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const roots=[...document.querySelectorAll('.cover-editor')].filter(visible),seen=new Set(),matches=new Set();
  if(roots.length!==1)return null;
  for(let n=roots[0];n;n=n.parentElement){let depth=0;for(let vm=n.__vue__;vm&&!seen.has(vm);vm=vm.$parent){
   if(++depth>32)return null;seen.add(vm);
   if(typeof vm.handleSubmit==='function'&&typeof vm.handleCancel==='function'&&typeof vm.handleUploadChange==='function')matches.add(vm);
  }}
  if(matches.size!==1)return null;
  const vm=[...matches][0],info=vm.coverForEditor;
  for(const key of ['isOverseas','isSyncEnabled','isCoverEditorReady_4_3','isCoverEditorReady_16_9','isProcessing','submitDisabled'])if(typeof vm[key]!=='boolean')return null;
  if(!info||typeof info!=='object'||!Array.isArray(vm.eventQueue)||vm.eventQueue.length>128)return null;
  const a=info.origin_4_3,b=info.origin_16_9;
  if((a!==null&&typeof a!=='string')||(b!==null&&typeof b!=='string'))return null;
  if(![null,'editor_4_3','editor_16_9'].includes(vm.activeEditor))return null;
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([a,b])));
  return {overseas:vm.isOverseas,sync:vm.isSyncEnabled,ready43:vm.isCoverEditorReady_4_3,ready169:vm.isCoverEditorReady_16_9,
   processing:vm.isProcessing,submit_disabled:vm.submitDisabled,queue_empty:vm.eventQueue.length===0,
   editor43:!!vm.imageEditor_4_3,editor169:!!vm.imageEditor_16_9,origin43:typeof a==='string'&&!!a,origin169:typeof b==='string'&&!!b,
   same_origin:typeof a==='string'&&!!a&&a===b,source43_upload:info.source_4_3==='upload',source169_upload:info.source_16_9==='upload',
   active_ratio:vm.activeEditor==='editor_4_3'?'4:3':vm.activeEditor==='editor_16_9'?'16:9':'none',
   signature:[...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,'0')).join('')};
 }catch(_){return null;}
}"""

_COVER_COMMITTED = r"""async allowEmpty => {
 try{
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const roots=[...document.querySelectorAll('.cover-module-main')].filter(visible),seen=new Set(),matches=new Set();
  if(roots.length!==1)return null;
  for(const el of roots)for(let n=el;n;n=n.parentElement){let depth=0;
   for(let vm=n.__vue__;vm&&!seen.has(vm);vm=vm.$parent){if(++depth>32)return null;seen.add(vm);
    if(typeof vm.openCoverEditor==='function'&&typeof vm.submitCoverEditor==='function')matches.add(vm);
   }
  }
  if(matches.size!==1)return null;
  const vm=[...matches][0];
  if(!Array.isArray(vm.coverList)||vm.defaultCoverIndex!==0)return null;
  __BILI_COVER_FORMAT__
  const https=safeCover;
  let pair,origins,uploaded=false,server_images=false,same_origin=false;
  if(vm.coverList.length===0){
   const slots=roots[0].querySelectorAll('.cover-slot');
   if(allowEmpty!==true||slots.length!==1||!visible(slots[0])||slots[0].children.length!==1||
      !slots[0].children[0].matches('.cover-empty')||!visible(slots[0].children[0]))return null;
   pair=[null,null];origins=[null,null];
  }else{
   if(vm.coverList.length!==1)return null;
   const {main,extra}=vm.coverList[0];if(!main||!extra)return null;
   pair=[main.edited||null,extra.edited||null];origins=[main.origin||null,extra.origin||null];uploaded=main.source==='upload'&&extra.source==='upload';
   server_images=https(main.edited)&&https(extra.edited);
   same_origin=typeof main.origin==='string'&&!!main.origin&&main.origin===extra.origin;
  }
  const raw=JSON.stringify(pair);
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));
  const originHash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(origins)));
  return {uploaded,server_images,same_origin,
   signature:[...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,'0')).join(''),
   origin_signature:[...new Uint8Array(originHash)].map(x=>x.toString(16).padStart(2,'0')).join('')};
 }catch(_){return null;}
}""".replace('__BILI_COVER_FORMAT__',BILI_COVER_FORMAT_JS)

_COVER_COMMITTED_SHAPE = r"""() => {try{
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const type=x=>x===null?'null':Array.isArray(x)?'array':typeof x;
 const image=value=>{let u;try{if(typeof value==='string'&&value.length<65536)u=new URL(value,location.href)}catch(_){}
  return {type:type(value),nonempty:typeof value==='string'&&value.length>0,url_parsed:!!u,
   protocol_http:!!u&&['https:','http:'].includes(u.protocol),official_host:!!u&&/^__BILI_COVER_HOST__$/.test(u.hostname),
   bfs_path:!!u&&u.pathname.startsWith('/bfs/'),no_credentials:!!u&&!u.username&&!u.password,no_port:!!u&&!u.port,
   no_query:!!u&&!u.search,no_fragment:!!u&&!u.hash};};
 const slot=value=>({type:type(value),edited:image(value?.edited),origin_type:type(value?.origin),
  origin_nonempty:typeof value?.origin==='string'&&value.origin.length>0,source_type:type(value?.source),source_upload:value?.source==='upload'});
 const roots=[...document.querySelectorAll('.cover-module-main')].filter(visible),seen=new Set(),matches=new Set();
 let bounded=true;
 for(const el of roots.slice(0,128))for(let n=el;n;n=n.parentElement){let depth=0;
  for(let vm=n.__vue__;vm&&!seen.has(vm);vm=vm.$parent){if(++depth>32){bounded=false;break;}seen.add(vm);
   if(typeof vm.openCoverEditor==='function'&&typeof vm.submitCoverEditor==='function')matches.add(vm);
  }
 }
 const vm=matches.size===1?[...matches][0]:null,list=vm?.coverList,row=Array.isArray(list)&&list.length===1?list[0]:null;
 return {bounded:bounded&&roots.length<=128,visible_roots:roots.length,owner_count:matches.size,
  list_type:type(list),list_count:Array.isArray(list)?list.length:null,index_type:type(vm?.defaultCoverIndex),index_zero:vm?.defaultCoverIndex===0,
  main:slot(row?.main),extra:slot(row?.extra),same_origin:typeof row?.main?.origin==='string'&&!!row.main.origin&&row.main.origin===row?.extra?.origin};
}catch(_){return null}}""".replace('__BILI_COVER_HOST__',BILI_COVER_HOST_PATTERN)

_STATE = r"""() => {
 try{
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const roots=[...document.querySelectorAll('.upload-wrp')],els=document.querySelectorAll('*'),seen=new Set(),publishers=new Set(),entrances=new Set(),managers=new Set();
  if(roots.length!==1||els.length>20000)return null;
  // The live Publisher (.video-basic-wrp) is a sibling of video-entrance,
  // which remains mounted after import. Its upload-wrp ancestors cannot own it.
  for(const el of els){let depth=0;
   for(let vm=el.__vue__;vm&&!seen.has(vm);vm=vm.$parent){if(++depth>32)return null;seen.add(vm);
    if(!vm.$el?.isConnected)continue;
    if(Array.isArray(vm.videoTasks)&&typeof vm.checkSubTask==='function'&&typeof vm.handleSubmitDraft==='function')publishers.add(vm);
    if(vm.$el.matches?.('.video-entrance')&&vm.$el.contains(roots[0])&&typeof vm.handleChange==='function'&&typeof vm.initRecord==='function'&&typeof vm.recovery==='function')entrances.add(vm);
    const config=vm.$taskConfig;
    if(config&&Array.isArray(config.tasks)&&typeof config.createTask==='function')managers.add(config);
   }
  }
  if(publishers.size>1||entrances.size>1||managers.size!==1)return null;
  const config=[...managers][0];
  let vm,tasks,record;
  if(publishers.size===1){
   vm=[...publishers][0];tasks=vm.videoTasks;record=vm.$recordConfig?.data;
   if(vm.$taskConfig!==config)return null;
   if(entrances.size===1){const entrance=[...entrances][0];if(entrance.$taskConfig!==config||entrance.$recordConfig!==vm.$recordConfig)return null;}
   if(tasks.length){
    if(config.tasks.length!==1||config.tasks[0].videoTasks!==tasks||config.tasks[0].id!==config.currentTaskId||vm.id!==config.currentTaskId)return null;
   }else if(config.tasks.length!==0||config.currentTaskId!==null)return null;
  }
  else {
   if(entrances.size!==1)return null;vm=[...entrances][0];
   if(vm.$taskConfig!==config||config.tasks.length!==0||config.currentTaskId!==null||
      !Array.isArray(vm.record)||vm.record.length!==0||typeof vm.uploadDisabled!=='boolean'||vm.uploadDisabled)return null;
   tasks=[];record=vm.$recordConfig?.data;
  }
  if(tasks.length>128||!Array.isArray(record)||record.length>128)return null;
  const raw=localStorage.getItem('bili_videoup_record');let stored=0;
  if(raw){const data=JSON.parse(raw);if(!data||typeof data!=='object'||Array.isArray(data)||!Array.isArray(data.data)||data.data.length>128)return null;stored=data.data.length;}
  const inputs=[...document.querySelectorAll('VIDEO_SELECTOR')];
  return {recognized:true,task_count:tasks.length,done_count:tasks.filter(t=>t.status===3).length,
   record_count:record.length,stored_count:stored,input_count:inputs.length,
   input_usable:inputs.length===1&&!inputs[0].disabled&&visible(inputs[0].closest('.upload.bcc-upload')),
   selected_count:inputs.length===1?inputs[0].files.length:0,
   unsubmitted_notice:[...document.querySelectorAll('.upload-wrp div')].some(e=>visible(e)&&/本地浏览器存在\d+个未提交的视频/.test(e.textContent))};
 }catch(_){return null;}
}""".replace('VIDEO_SELECTOR', VIDEO_INPUT)

_TAGS = r"""() => {
 try{
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const inputs=[...document.querySelectorAll('.tag-container .tag-input-wrp input')].filter(visible),seen=new Set(),matches=new Set();
  if(inputs.length!==1)return null;
  for(let el=inputs[0];el;el=el.parentElement){let depth=0;for(let vm=el.__vue__;vm&&!seen.has(vm);vm=vm.$parent){
   if(++depth>32)return null;seen.add(vm);
   if(vm.$options?.name==='tag-input'&&typeof vm.setCustomTag==='function')matches.add(vm);
  }}
  if(matches.size!==1)return null;
  const vm=[...matches][0];
  if(!Array.isArray(vm.submitTags)||vm.submitTags.length>10||typeof vm.checkCustomTagLoading!=='boolean'||
     !vm.submitTags.every(t=>typeof t==='string'&&t.length>0&&t.length<=100)||new Set(vm.submitTags).size!==vm.submitTags.length)return null;
  return {tags:vm.submitTags.slice(),loading:vm.checkCustomTagLoading};
 }catch(_){return null;}
}"""

_DESCRIPTION_TEXT = r"""() => {try{
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const roots=[...document.querySelectorAll('.desc-container .ql-editor[contenteditable="true"]')].filter(visible);
 if(roots.length!==1)return null;const el=roots[0],inline=new Set(['SPAN','B','I','U','S','STRONG','EM','A','CODE']),blocks=new Set(['P','DIV']);let nodes=0;
 function text(node){if(++nodes>4096)throw Error('large description');
  if(node.nodeType===3)return node.nodeValue;
  if(node.nodeType!==1)throw Error('unknown description node');
  if(node.tagName==='BR')return '\n';
  if(!inline.has(node.tagName))throw Error('unknown inline description');
  return [...node.childNodes].map(text).join('');}
 const parts=[];let pending='';
 for(const node of el.childNodes){
  if(node.nodeType===1&&blocks.has(node.tagName)){
   if(pending){parts.push(pending);pending='';}
   const children=[...node.childNodes];
   parts.push(children.length===1&&children[0].nodeType===1&&children[0].tagName==='BR'?'':children.map(text).join(''));
  }else pending+=text(node);
 }
 if(pending||!parts.length)parts.push(pending);
 let dom=parts.join('\n');if(el.childNodes.length===1&&el.firstChild?.nodeName==='BR')dom='';
 if(dom.length>65536)return null;
 const seen=new Set(),quills=new Set();
 for(let node=el;node;node=node.parentElement){if(node.__quill)quills.add(node.__quill);
  for(let vm=node.__vue__,n=0;vm&&!seen.has(vm)&&n++<32;vm=vm.$parent){seen.add(vm);
   if(vm.quill)quills.add(vm.quill);if(vm.$refs?.quillEditor?.quill)quills.add(vm.$refs.quillEditor.quill);}}
 if(quills.size>1)return null;
 if(quills.size===1){const q=[...quills][0],ops=q.editor?.delta?.ops;
  if(q.root!==el||!Array.isArray(ops)||ops.length>4096)return null;
  let raw='';for(const op of ops){if(!op||typeof op!=='object'||typeof op.insert!=='string'||
    Object.keys(op).some(k=>k!=='insert'&&k!=='attributes')||
    op.attributes!==undefined&&(!op.attributes||typeof op.attributes!=='object'||Array.isArray(op.attributes)))return null;
   raw+=op.insert;if(raw.length>65537)return null;}
  // Quill owns one mandatory document terminator. User empty paragraphs and
  // all their newlines precede it and must survive this exact single removal.
  if(!raw.endsWith('\n')||raw.slice(0,-1)!==dom)return null;
 }
 return {text:dom,quill:quills.size===1};
}catch(_){return null;}}"""

# Hold only read-only object identities in this call's realm. These are never
# passed to a business method or written back to Vue. A fresh import permission
# starts with the actual empty manager and native input, then binds its one
# completed File/SDK/task. A restored session binds that completed chain anew.
_EDITOR_OBJECT_JS = r"""
 function read(mode){try{
  const roots=[...document.querySelectorAll('.upload-wrp')],seen=new Set(),publishers=new Set(),managers=new Set(),records=new Set();
  if(roots.length!==1||document.querySelectorAll('*').length>20000)return null;
  for(const el of document.querySelectorAll('*'))for(let vm=el.__vue__,n=0;vm&&!seen.has(vm)&&n++<32;vm=vm.$parent){
   seen.add(vm);if(!vm.$el?.isConnected)continue;
   if(Array.isArray(vm.videoTasks)&&typeof vm.checkSubTask==='function'&&typeof vm.handleSubmitDraft==='function')publishers.add(vm);
   if(vm.$taskConfig&&Array.isArray(vm.$taskConfig.tasks)&&typeof vm.$taskConfig.createTask==='function')managers.add(vm.$taskConfig);
   if(vm.$recordConfig&&Array.isArray(vm.$recordConfig.data))records.add(vm.$recordConfig);
  }
  const inputs=[...document.querySelectorAll('VIDEO_SELECTOR')];
  if(managers.size!==1||records.size!==1||inputs.length!==1)return null;
  const manager=[...managers][0],record=[...records][0],input=inputs[0];
  if(mode==='empty')return manager.tasks.length===0&&manager.currentTaskId===null?
   {document,manager,record,input}:null;
  if(publishers.size!==1)return null;const publisher=[...publishers][0];
  if(publisher.$taskConfig!==manager||publisher.$recordConfig!==record||publisher.videoTasks.length!==1||manager.tasks.length!==1)return null;
  const parent=manager.tasks[0],tasks=publisher.videoTasks,task=tasks[0],file=task.file,sdk=task.uploader,inner=sdk?.uploader;
  if(parent.videoTasks!==tasks||parent.id!==manager.currentTaskId||publisher.id!==manager.currentTaskId||task.status!==3||
     !sdk||sdk.file!==file||!inner||inner.file!==file||inner.completed!==true||!['stdSlice','slice'].includes(sdk.uploaderType)||
     !/^[1-9][0-9]{0,19}$/.test(String(task.cid))||String(sdk.cid)!==String(task.cid)||typeof task.filename!=='string'||
     !task.filename||task.filename.length>2048||/[\s\x00-\x1f\x7f]/.test(task.filename)||sdk.filename!==task.filename)return null;
  const name=Reflect.apply(Object.getOwnPropertyDescriptor(File.prototype,'name').get,file,[]),
   size=Reflect.apply(Object.getOwnPropertyDescriptor(Blob.prototype,'size').get,file,[]),
   type=Reflect.apply(Object.getOwnPropertyDescriptor(Blob.prototype,'type').get,file,[]);
  if(!name||!Number.isSafeInteger(size)||size<=0||!type.startsWith('video/'))return null;
  return {document,manager,record,input,publisher,parent,tasks,task,file,sdk,inner,cid:String(task.cid),filename:task.filename};
 }catch(_){return null;}}
""".replace('VIDEO_SELECTOR', VIDEO_INPUT)

_EDITOR_REFS = ('document','manager','record','input','publisher','parent','tasks','task','file','sdk','inner','cid','filename')

_REOPENED_MEDIA = r"""() => {
 try{
  const roots=[...document.querySelectorAll('.upload-wrp')],seen=new Set(),publishers=new Set(),covers=new Set();
  if(roots.length!==1)return null;
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  if(!visible(roots[0]))return null;
  for(const el of [...roots,...document.querySelectorAll('.cover-module-main')])for(let n=el;n;n=n.parentElement){let depth=0;
   for(let vm=n.__vue__;vm&&!seen.has(vm);vm=vm.$parent){if(++depth>32)return null;seen.add(vm);
    if(Array.isArray(vm.videoTasks)&&typeof vm.checkSubTask==='function'&&typeof vm.handleSubmitDraft==='function')publishers.add(vm);
    if(typeof vm.openCoverEditor==='function'&&typeof vm.submitCoverEditor==='function'&&Array.isArray(vm.coverList))covers.add(vm);
   }
  }
  if(publishers.size!==1||covers.size!==1)return null;
  const p=[...publishers][0],c=[...covers][0];
  if(p.videoTasks.length!==1||p.videoTasks[0].status!==3||c.coverList.length!==1||c.defaultCoverIndex!==0)return null;
  const v=p.videoTasks[0],cover=c.coverList[0];
  if(!/^[1-9][0-9]{0,19}$/.test(String(v.cid))||typeof v.filename!=='string'||!v.filename||v.filename.length>2048||/[\s\x00-\x1f\x7f]/.test(v.filename)||
     typeof cover.main?.edited!=='string'||typeof cover.extra?.edited!=='string')return null;
  return {video_count:p.videoTasks.length,video_ids:[String(v.cid),v.filename],cover:cover.main.edited,cover43:cover.extra.edited};
 }catch(_){return null;}
}"""


def _official(url):
    try:
        parsed=urlsplit(url)
        return (parsed.scheme=='https' and parsed.netloc=='member.bilibili.com' and
                (parsed.path.startswith('/platform/upload/video/') or parsed.path.startswith('/york/videoup')))
    except (ValueError, TypeError):
        return False


async def _frame(page, timeout=5):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        candidates=[]
        for frame in page.frames:
            if not _official(frame.url):
                continue
            if await frame.locator('.upload-wrp .upload.bcc-upload').count()==1:
                candidates.append(frame)
        if len(candidates)==1:
            return candidates[0]
        if len(candidates)>1:
            raise DraftInterrupted('B站编辑器不唯一，已保留现场。','adapter_mismatch')
        await asyncio.sleep(.15)
    raise DraftInterrupted('B站正常视频编辑器尚未唯一确认，已保留现场。','adapter_mismatch')


async def _state(frame):
    result=await asyncio.wait_for(frame.evaluate(_STATE,isolated_context=False),5)
    counts=('task_count','done_count','record_count','stored_count','input_count','selected_count')
    if (type(result) is not dict or set(result)!=set(counts)|{'recognized','input_usable','unsubmitted_notice'} or
            result['recognized'] is not True or any(type(result[k]) is not int or not 0<=result[k]<=128 for k in counts) or
            any(type(result[k]) is not bool for k in ('input_usable','unsubmitted_notice')) or result['done_count']>result['task_count']):
        raise DraftInterrupted('B站当前素材与本地未提交记录无法确认，已保留现场。','adapter_mismatch')
    return result


async def bili_empty_video_control(page):
    """Return the canonical native input only for an empty recognized editor."""
    frame=await _frame(page)
    state=await _state(frame)
    if state['task_count'] or state['record_count'] or state['stored_count'] or state['selected_count'] or state['unsubmitted_notice']:
        raise DraftInterrupted('B站当前仍有素材或未提交记录，请先通过平台正常保存；不会覆盖原稿。','adapter_mismatch')
    if state['input_count']!=1 or not state['input_usable']:
        raise DraftInterrupted('B站主视频输入控件不可用或不唯一，已停止。','adapter_mismatch')
    permission=await _editor_object(frame,'empty')
    previous=getattr(page,'_sau_bili_native_import',None)
    if previous is not None:
        await _dispose_editor(previous['handle'])
    page._sau_bili_native_import={'frame':frame,'handle':permission}
    return frame.locator(VIDEO_INPUT)


async def bili_wait_uploaded(page, timeout=600):
    from uploader.draft_only import assert_draft_safe
    set_progress_plan('upload', ['transfer'])
    progress_operation('upload', 'transfer')
    try:
        frame=await _frame(page)
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            await assert_draft_safe(page,'B站')
            state=await _state(frame)
            if state['task_count']>1:
                raise DraftInterrupted('B站出现多个素材任务，已停止并保留现场。','adapter_mismatch')
            if state['task_count']==state['done_count']==1:
                progress_operation('upload', 'transfer', 'completed')
                return
            await asyncio.sleep(.2)
        raise DraftInterrupted('B站视频上传尚未完成，已保留现场。','result_unknown')
    except BaseException:
        progress_operation('upload', 'transfer', 'failed')
        raise


async def _unique_visible(frame, selector):
    matches=frame.locator(selector)
    found=[item for item in await matches.all() if await item.is_visible()]
    if len(found)!=1 or not await found[0].is_enabled():
        raise DraftInterrupted('B站所需内容控件不唯一或不可用，已停止。','adapter_mismatch')
    return found[0]


async def _tags(frame):
    result=await asyncio.wait_for(frame.evaluate(_TAGS,isolated_context=False),5)
    if (type(result) is not dict or set(result)!={'tags','loading'} or type(result['tags']) is not list or
            len(result['tags'])>10 or any(type(t) is not str or not t for t in result['tags']) or
            len(set(result['tags']))!=len(result['tags']) or type(result['loading']) is not bool):
        raise DraftInterrupted('B站已选标签状态无法确认，已停止。','adapter_mismatch')
    return result


async def _description_text(frame):
    result=await asyncio.wait_for(frame.evaluate(_DESCRIPTION_TEXT,isolated_context=False),5)
    if type(result) is not dict or set(result)!={'text','quill'} or type(result['text']) is not str or type(result['quill']) is not bool:
        raise DraftInterrupted('B站简介段落与同一编辑器文本无法完整对应，已停止。','adapter_mismatch')
    return result['text']


async def _editor_object(frame, mode):
    try:
        handle=await asyncio.wait_for(frame.evaluate_handle('mode=>{'+_EDITOR_OBJECT_JS+'return read(mode);}',
            mode,isolated_context=False),5)
        valid=await asyncio.wait_for(handle.evaluate('value=>value!==null'),5)
    except (BrowserError,TimeoutError):
        raise DraftInterrupted('B站原生编辑器上下文已改变，已保留现场。','adapter_mismatch') from None
    if not valid:
        await _dispose_editor(handle)
        raise DraftInterrupted('B站本次原生视频任务无法唯一绑定，已保留现场。','adapter_mismatch')
    return handle


async def _dispose_editor(handle):
    try:
        await asyncio.wait_for(handle.dispose(),5)
    except (BrowserError,TimeoutError):
        pass


def _resume_tag_proof(page):
    from uploader.draft_only import draft_resume_active
    session=getattr(page,'_sau_draft_evidence',None)
    if not draft_resume_active(page) or getattr(session,'platform',None)!='bilibili' or getattr(session,'page',None) is not page:
        return None
    expected=getattr(session,'expected',{});sha=expected.get('video_sha256') if isinstance(expected,dict) else None
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        return None
    try:
        links=[link for link in session.upload_links() if isinstance(link,dict) and link.get('kind')=='video' and
            link.get('complete') is True and link.get('sha256')==sha]
    except (AttributeError,TypeError,ValueError):
        return None
    if len(links)!=1 or not isinstance(links[0].get('ids'),list) or len(links[0]['ids'])!=2 or \
            any(not isinstance(value,str) or not value for value in links[0]['ids']) or len(set(links[0]['ids']))!=2:
        return None
    return session,sha,frozenset(links[0]['ids'])


async def _tag_binding(page,frame,permission):
    from uploader.draft_only import draft_resume_active
    if permission is None and not draft_resume_active(page):
        return None
    resumed=_resume_tag_proof(page) if permission is None else None
    if permission is None and resumed is None:
        raise DraftInterrupted('B站恢复的封存视频证明无法确认，未替换标签。','adapter_mismatch')
    if permission is not None and permission['frame'] is not frame:
        raise DraftInterrupted('B站本次导入编辑器已改变，未替换标签。','adapter_mismatch')
    binding=await _editor_object(frame,'uploaded')
    try:
        valid=await asyncio.wait_for(binding.evaluate(r'''(value,prior)=>!!value&&!!prior&&
            value.document===prior.document&&value.manager===prior.manager&&value.record===prior.record&&
            value.input===prior.input&&prior.input.files.length===1&&prior.input.files[0]===value.file''',permission['handle']),5) \
            if permission is not None else frozenset(await binding.evaluate('value=>[value.cid,value.filename]'))==resumed[2]
    except (BrowserError,TimeoutError):
        valid=False
    if not valid:
        await _dispose_editor(binding)
        raise DraftInterrupted('B站原生导入与当前素材无法对应，未替换标签。','adapter_mismatch')
    binding._sau_bili_resumed_proof=resumed
    return binding


async def _tag_context(page,frame,binding):
    from uploader.draft_only import assert_draft_safe
    await assert_draft_safe(page,'B站')
    if await _frame(page,timeout=.3) is not frame:
        raise DraftInterrupted('B站唯一编辑器已改变，已停止标签操作。','adapter_mismatch')
    state=await _state(frame)
    if state['task_count']!=state['done_count'] or state['task_count']!=1:
        raise DraftInterrupted('B站视频任务已改变，已停止标签操作。','adapter_mismatch')
    if binding is not None:
        keys=repr(list(_EDITOR_REFS)).replace("'",'"')
        try:
            valid=await asyncio.wait_for(binding.evaluate('value=>{'+_EDITOR_OBJECT_JS+'const live=read("uploaded");return !!live&&'+
                keys+'.every(key=>live[key]===value[key]);}'),5)
        except (BrowserError,TimeoutError):
            valid=False
        saved=binding._sau_bili_resumed_proof
        resumed=_resume_tag_proof(page) if saved is not None else None
        if not valid or saved is not None and (resumed is None or resumed[0] is not saved[0] or resumed[1:]!=saved[1:]):
            raise DraftInterrupted('B站原生视频、任务或编辑器已改变，已停止标签操作。','adapter_mismatch')


async def _close_extra_tags(page,frame,binding,tags,budget,timeout):
    wanted=set(tags)
    while True:
        await _tag_context(page,frame,binding)
        before=await _tags(frame)
        if before['loading']:
            raise DraftInterrupted('B站标签仍在加载，未删除标签。','adapter_mismatch')
        extras=[tag for tag in before['tags'] if tag not in wanted]
        if not extras:
            return
        if binding is None:
            raise DraftInterrupted('B站已有额外或自动推荐标签，请在平台明确处理后再继续；不会擅自删除。','adapter_mismatch')
        if budget[0]>=10:
            raise DraftInterrupted('B站推荐标签反复变化，已停止且保留现场。','adapter_mismatch')
        tag=extras[0];rows=[]
        for row in await frame.locator(TAG_ROWS).all():
            content=row.locator('.label-item-v2-content')
            if await row.is_visible() and await content.count()==1 and await content.inner_text()==tag:
                rows.append(row)
        if len(rows)!=1 or await rows[0].locator('svg.close').count()!=1:
            raise DraftInterrupted('B站该标签的正常关闭控件不唯一，未删除。','adapter_mismatch')
        close=rows[0].locator('svg.close')
        await close.scroll_into_view_if_needed(timeout=timeout*1000)
        await close.click(trial=True,timeout=timeout*1000)
        await _tag_context(page,frame,binding)
        current=await _tags(frame)
        if current['loading'] or current['tags']!=before['tags'] or not await close.is_visible() or \
                await rows[0].locator('svg.close').count()!=1 or await rows[0].locator('.label-item-v2-content').inner_text()!=tag:
            raise DraftInterrupted('B站标签或正常关闭控件已改变，未删除。','adapter_mismatch')
        await close.click(timeout=timeout*1000);budget[0]+=1
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            await _tag_context(page,frame,binding)
            after=await _tags(frame)
            if after['loading'] or not (set(before['tags'])&wanted)<=set(after['tags']):
                raise DraftInterrupted('B站标签删除回读出现加载或非目标变化，已停止。','adapter_mismatch')
            if tag not in after['tags']:
                break
            await asyncio.sleep(.05)
        else:
            raise DraftInterrupted('B站该标签删除尚未确切回读，未继续删除。','adapter_mismatch')


async def bili_fill_content(page, title, description, tags, tag_timeout=10):
    if (type(tags) is not list or len(tags)>10 or any(type(t) is not str or not t.strip() or t!=t.strip() for t in tags) or len(set(tags))!=len(tags)):
        raise DraftInterrupted('B站话题标签格式不正确，已停止。','adapter_mismatch')
    content_operations = ['title', 'description'] + (['topics'] if tags else []) + ['readback']
    set_progress_plan('content', content_operations)
    tag_timeout=min(max(float(tag_timeout),.05),10)
    frame=await _frame(page)
    state=await _state(frame)
    if state['task_count']!=1 or state['done_count']!=1:
        raise DraftInterrupted('B站单一视频尚未完成，未填写内容。','adapter_mismatch')
    permission=getattr(page,'_sau_bili_native_import',None)
    page._sau_bili_native_import=None
    binding=None
    try:
        binding=await _tag_binding(page,frame,permission)
        title_control=await _unique_visible(frame,TITLE)
        desc=await _unique_visible(frame,DESCRIPTION)
        tag_control=await _unique_visible(frame,TAG)
        budget=[0]
        await _close_extra_tags(page,frame,binding,tags,budget,tag_timeout)
        await _tag_context(page,frame,binding)
        with progress_part('content', 'title'):
            await title_control.fill(title)
            if await title_control.input_value()!=title:
                raise DraftInterrupted('B站标题回读与预检内容不一致，已停止。','adapter_mismatch')
        await _tag_context(page,frame,binding)
        with progress_part('content', 'description'):
            await desc.fill(description)
            if (await _description_text(frame)).replace('\r\n','\n')!=description.replace('\r\n','\n'):
                raise DraftInterrupted('B站简介回读与预检内容不一致，已停止。','adapter_mismatch')
        if tags:
            progress_operation('content', 'topics')
            try:
                for tag in tags:
                    await _close_extra_tags(page,frame,binding,tags,budget,tag_timeout)
                    current=await _tags(frame)
                    if tag in current['tags']:
                        continue
                    await _tag_context(page,frame,binding)
                    await tag_control.fill(tag)
                    await _tag_context(page,frame,binding)
                    await tag_control.press('Enter')
                    until=time.monotonic()+tag_timeout
                    while time.monotonic()<until:
                        await _tag_context(page,frame,binding)
                        current=await _tags(frame)
                        if not set(current['tags'])<=set(tags) and (binding is None or not current['loading']):
                            await _close_extra_tags(page,frame,binding,tags,budget,max(.05,until-time.monotonic()))
                            current=await _tags(frame)
                        if tag in current['tags'] and not current['loading']:
                            break
                        await asyncio.sleep(.05)
                    else:
                        raise DraftInterrupted('B站该标签尚未确切加入，未继续输入后续标签。','adapter_mismatch')
                await _close_extra_tags(page,frame,binding,tags,budget,tag_timeout)
            except BaseException:
                progress_operation('content', 'topics', 'failed')
                raise
            progress_operation('content', 'topics', 'completed')
        with progress_part('content', 'readback'):
            # Recommendations may resolve after a synchronous close or tag check.
            # Once completion starts, any new extra tag fails instead of a retry
            # that could silently accept an unstable final set.
            stable_until=time.monotonic()+min(.3,tag_timeout)
            while True:
                await _tag_context(page,frame,binding)
                final=await _tags(frame)
                if final['loading'] or set(final['tags'])!=set(tags):
                    raise DraftInterrupted('B站最终标签与预检内容不一致，已停止。','adapter_mismatch')
                if time.monotonic()>=stable_until:
                    break
                await asyncio.sleep(.05)
    finally:
        if binding is not None:
            await _dispose_editor(binding)
        if permission is not None:
            await _dispose_editor(permission['handle'])


async def bili_draft_action(page):
    """Click only the official draft span, after exact state/loading gates.

    The caller owns the save marker and independent response/readback evidence.
    This click does not claim that a draft has been saved.
    """
    from uploader.draft_only import assert_draft_safe
    frame=await _frame(page)
    state=await _state(frame)
    if state['task_count']!=1 or state['done_count']!=1:
        raise DraftInterrupted('B站视频任务尚未完整就绪，未点击存草稿。','adapter_mismatch')
    control=await _unique_visible(frame,DRAFT)
    if (await control.inner_text()).strip()!='存草稿' or not await control.evaluate(
            "el=>!el.classList.contains('btn-disabled')&&!el.classList.contains('btn-loading')&&"
            "el.getAttribute('aria-disabled')!=='true'&&el.getAttribute('aria-busy')!=='true'"):
        raise DraftInterrupted('B站存草稿控件仍在处理或不可用，未点击。','platform_restricted')
    await assert_draft_safe(page,'B站')
    await control.click()
    return '存草稿'


async def bili_reopened_ui_verified(page, record, expected):
    """Read the actual restored normal composer, independently of draft/view.

    No video DOM is required: the official visible Publisher consumes its one
    restored server task and VideoCover consumes both saved cover slots.
    """
    from uploader.bili_evidence import bili_cover_identity
    from uploader.draft_evidence import field_match
    if not isinstance(record, dict) or record.get('full_fields_source') != 'normal_draft_view':
        return False
    try:
        frame = await _frame(page, timeout=.3)
        state = await _state(frame)
        if state['task_count'] != 1 or state['done_count'] != 1:
            return False
        title = await _unique_visible(frame, TITLE)
        desc = await _unique_visible(frame, DESCRIPTION)
        tags = await _tags(frame)
        media = await asyncio.wait_for(frame.evaluate(_REOPENED_MEDIA, isolated_context=False), 5)
        if (not isinstance(media, dict) or set(media) != {'video_count', 'video_ids', 'cover', 'cover43'} or
                media['video_count'] != 1 or not isinstance(media['video_ids'], list) or tags['loading']):
            return False
        slots = {'cover': [bili_cover_identity(media['cover'])], 'cover43': [bili_cover_identity(media['cover43'])]}
        live = {'title': await title.input_value(), 'description': (await _description_text(frame)).replace('\r\n', '\n'),
                'tags': tags['tags']}
        return bool(field_match('bilibili', live, expected) and live['title'] == record['title'] and
                    live['description'] == record['description'].replace('\r\n', '\n') and
                    live['description'] == expected.get('platform_fields',{}).get('description','').replace('\r\n','\n') and
                    sorted(live['tags']) == sorted(record['tags']) and
                    set(media['video_ids']) == set(record['video_ids']) and None not in slots['cover'] + slots['cover43'] and
                    slots == record.get('cover_slots'))
    except (DraftInterrupted, TimeoutError):
        return False


async def _cover_state(frame):
    value=await asyncio.wait_for(frame.evaluate(_COVER_STATE,isolated_context=False),5)
    bools=('overseas','sync','ready43','ready169','processing','submit_disabled','queue_empty','editor43','editor169',
           'origin43','origin169','same_origin','source43_upload','source169_upload')
    if (type(value) is not dict or set(value)!=set(bools)|{'signature','active_ratio'} or
            any(type(value[k]) is not bool for k in bools) or value['active_ratio'] not in ('none','4:3','16:9') or not _signature(value['signature'])):
        raise DraftInterrupted('B站当前封面编辑器状态无法唯一确认，已保留现场。','adapter_mismatch')
    return value


def _signature(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


async def _committed_cover(frame, *, allow_empty=False):
    value=await asyncio.wait_for(frame.evaluate(_COVER_COMMITTED,allow_empty,isolated_context=False),5)
    if (type(value) is not dict or set(value)!={'uploaded','server_images','same_origin','signature','origin_signature'} or
            any(type(value[k]) is not bool for k in ('uploaded','server_images','same_origin')) or
            not _signature(value['signature']) or not _signature(value['origin_signature'])):
        raise DraftInterrupted('B站主封面双比例回读无法确认，已保留现场。','adapter_mismatch')
    return value


def _safe_committed_cover_shape(value):
    """Accept only fixed diagnostic enums, counts and booleans from the page."""
    types={'undefined','null','array','string','number','boolean','object','function','symbol','bigint'}
    image_bools={'nonempty','url_parsed','protocol_http','official_host','bfs_path','no_credentials','no_port','no_query','no_fragment'}
    def image(item):
        return (type(item) is dict and set(item)==image_bools|{'type'} and type(item['type']) is str and item['type'] in types and
                all(type(item[key]) is bool for key in image_bools))
    def slot(item):
        return (type(item) is dict and set(item)=={'type','edited','origin_type','origin_nonempty','source_type','source_upload'} and
                all(type(item[key]) is str and item[key] in types for key in ('type','origin_type','source_type')) and
                all(type(item[key]) is bool for key in ('origin_nonempty','source_upload')) and image(item['edited']))
    if (type(value) is not dict or set(value)!={'bounded','visible_roots','owner_count','list_type','list_count','index_type','index_zero','main','extra','same_origin'} or
            any(type(value[key]) is not bool for key in ('bounded','index_zero','same_origin')) or
            any(type(value[key]) is not int or not 0<=value[key]<=20000 for key in ('visible_roots','owner_count')) or
            not (value['list_count'] is None or type(value['list_count']) is int and 0<=value['list_count']<=20000) or
            any(type(value[key]) is not str or value[key] not in types for key in ('list_type','index_type')) or
            not slot(value['main']) or not slot(value['extra'])):
        return None
    return value


async def _cover_final_diagnostic(frame, snapshot):
    """One bounded timeout observation; it cannot grant cover completion."""
    counts=('editor_visible_count','committed_read_count')
    bools=('committed_read','uploaded','server_images','same_origin','signature_changed','current_source_match')
    try:
        value=await asyncio.wait_for(frame.evaluate(_COVER_COMMITTED_SHAPE,isolated_context=False),2)
        shape=_safe_committed_cover_shape(value)
    except Exception:
        shape=None
    result={'event':'bilibili_cover_final_timeout'}
    result.update({key:snapshot.get(key) if type(snapshot.get(key)) is int and 0<=snapshot[key]<=20000 else 0 for key in counts})
    result.update({key:snapshot.get(key) if type(snapshot.get(key)) is bool else False for key in bools})
    result['shape_available']=shape is not None
    result['committed_shape']=shape
    try:
        print('DRAFT_DIAGNOSTIC='+json.dumps(result),flush=True)
    except Exception:
        pass


async def bili_set_cover(page, cover, timeout=45):
    """Import one selected source through official 4:3/16:9 synchronized UI.

    Edited server images are checked as UI readiness only. This does not bind
    their exported bytes to the local source or attest persisted draft slots.
    """
    from uploader.draft_only import assert_draft_safe
    from uploader.local_files import set_local_files
    set_progress_plan('cover', ['editor', 'landscape', 'confirm', 'readback'])
    frame=await _frame(page)
    state=await _state(frame)
    if state['task_count']!=1 or state['done_count']!=1:
        raise DraftInterrupted('B站单视频尚未就绪，未编辑封面。','adapter_mismatch')
    slot=await _unique_visible(frame,COVER_SLOT)
    if await slot.evaluate("el=>el.classList.contains('cover-slot-disabled')||el.classList.contains('cover-slot-offline-action')"):
        raise DraftInterrupted('B站主封面不可编辑，未点击。','platform_restricted')
    if any([await item.is_visible() for item in await frame.locator('.cover-editor').all()]):
        raise DraftInterrupted('B站已有封面编辑现场，未覆盖。','adapter_mismatch')
    # VideoCover starts with coverList=[] and its one visible empty slot.
    # Only this pre-import baseline permits that state; completion is strict.
    committed_before=await _committed_cover(frame,allow_empty=True)
    targets=slot.locator(':scope > .cover-img,:scope > .cover-empty')
    target=await _unique_visible(frame,COVER_SLOT+' > .cover-img,'+COVER_SLOT+' > .cover-empty')
    if await targets.count()!=1:
        raise DraftInterrupted('B站主封面入口不唯一，未点击。','adapter_mismatch')
    with progress_part('cover', 'editor'):
        await assert_draft_safe(page,'B站')
        await target.click()
        await frame.locator('.cover-editor').wait_for(state='visible',timeout=min(timeout,10)*1000)
        initial=await _cover_state(frame)
        if initial['overseas']:
            raise DraftInterrupted('B站当前封面比例模式与双比例预检不同，已停止。','adapter_mismatch')
        inputs=frame.locator(COVER_INPUT)
        if await inputs.count()!=1 or not await inputs.is_enabled() or not await inputs.evaluate(
                "el=>{const p=el.closest('.cover-upload.bcc-upload'),r=p.getBoundingClientRect(),s=getComputedStyle(p);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}"):
            raise DraftInterrupted('B站封面来源上传器不可用或不唯一，未导入图片。','adapter_mismatch')
    with progress_part('cover', 'landscape'):
        await assert_draft_safe(page,'B站')
        await set_local_files(page,inputs,cover)
        until=time.monotonic()+timeout
        sync_clicked=False
        while time.monotonic()<until:
            await assert_draft_safe(page,'B站')
            current=await _cover_state(frame)
            changed=current['signature']!=initial['signature']
            if changed and not current['processing'] and current['queue_empty']:
                if not current['sync'] and not sync_clicked and (
                        (current['ready43'] and current['source43_upload']) or (current['ready169'] and current['source169_upload'])):
                    ratio='ratio_4_3' if current['active_ratio']=='4:3' else 'ratio_16_9' if current['active_ratio']=='16:9' else None
                    if ratio is None:
                        await asyncio.sleep(.15)
                        continue
                    selector='.cover-editor .sync-checkbox-wrapper.'+ratio+' > span'
                    visible_labels=[item for item in await frame.locator(selector).all() if await item.is_visible()]
                    if not visible_labels:
                        await asyncio.sleep(.15)
                        continue
                    label=await _unique_visible(frame,selector)
                    if (await label.inner_text()).strip()!='双比例同步改动':
                        raise DraftInterrupted('B站双比例同步控件不匹配，未点击。','adapter_mismatch')
                    await label.click()
                    sync_clicked=True
                if (current['sync'] and current['ready43'] and current['ready169'] and current['editor43'] and current['editor169'] and
                        current['same_origin'] and current['source43_upload'] and current['source169_upload'] and not current['submit_disabled']):
                    break
            await asyncio.sleep(.15)
        else:
            raise DraftInterrupted('B站双比例封面尚未同步就绪，未点击完成。','result_unknown')
    with progress_part('cover', 'confirm'):
        done=await _unique_visible(frame,COVER_DONE)
        if (await done.inner_text()).strip()!='完成' or not await done.evaluate(
                "el=>!el.classList.contains('submit-disabled')&&el.getAttribute('aria-disabled')!=='true'&&el.getAttribute('aria-busy')!=='true'"):
            raise DraftInterrupted('B站封面仍在制作，未点击完成。','adapter_mismatch')
        await assert_draft_safe(page,'B站')
        await done.click()
    # Synchronization was explicitly confirmed before completion, so an
    # unexpected secondary confirmation is retained rather than guessed.
    until=time.monotonic()+timeout
    final_snapshot={'editor_visible_count':0,'committed_read_count':0,'committed_read':False,
                    'uploaded':False,'server_images':False,'same_origin':False,'signature_changed':False,'current_source_match':False}
    progress_operation('cover', 'readback')
    try:
        while time.monotonic()<until:
            await assert_draft_safe(page,'B站')
            visible=[item for item in await frame.locator('.cover-editor').all() if await item.is_visible()]
            final_snapshot['editor_visible_count']=len(visible)
            if len(visible)>1:
                raise DraftInterrupted('B站封面完成区域不唯一，已停止。','adapter_mismatch')
            if not visible:
                committed=await _committed_cover(frame)
                final_snapshot.update(committed_read=True,committed_read_count=final_snapshot['committed_read_count']+1,
                    uploaded=committed['uploaded'],server_images=committed['server_images'],same_origin=committed['same_origin'],
                    signature_changed=committed['signature']!=committed_before['signature'],
                    current_source_match=committed['origin_signature']==current['signature'])
                # The official server may deduplicate identical JPEGs to the same
                # edited URLs. Completion must consume this call's freshly imported
                # and synchronized source, rather than require new server URLs.
                # Export/API provenance remains an independent observer gate.
                if (committed['uploaded'] and committed['server_images'] and committed['same_origin'] and
                        committed['origin_signature']==current['signature']):
                    progress_operation('cover', 'readback', 'completed')
                    return
            await asyncio.sleep(.15)
        await _cover_final_diagnostic(frame,final_snapshot)
        raise DraftInterrupted('B站封面制作或双比例回读尚未完成，已保留现场。','result_unknown')
    except BaseException:
        progress_operation('cover', 'readback', 'failed')
        raise
