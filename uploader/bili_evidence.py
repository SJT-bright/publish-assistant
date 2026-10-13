"""Passive evidence for Bili native uploads and idless draft save acknowledgements.

Official cached sources (zero-based Unicode offsets), 2026-10-03:
M limits-login/fresh-bili-index.ad9b754d.js SHA d0c283365e4210fa95c166a4f99371397e00ce30a4790fafaa755d3c5e6e799a:
  975700 existing task.uploader; 981647 uploadComplete updates task.status;
  380558 native File -> verified blob URL; 592944 two JPEG export/upload calls;
  620000 submitCoverEditor commits 16:9 main / 4:3 extra; 1041068 idless save.
V official-three/bili_videoup_607.js SHA 09327db43ccb08413fcf390efa6aba31b17d524fd3ef89fd410fbce81e79b7b2:
  2286298 / 2298284 successful merge -> uploadComplete -> completed=true;
  2300307 SDK.file; 2302289 forwards inner events; 2657062 JPEG dataURL export.
C official-three/bili_actual_ui_main.js:48631 getDraftList code0 data array.

No SDK is created or started. No API is called. Wrappers preserve original
receivers, arguments, synchronous throws and exact Promise/return identity.
Native CDP context/frame identities, not Patchright binding source.frame, own
all observations. Source URLs, native bytes, identity and draft bodies remain
private. An idless code0 reply alone never creates a candidate or a record.
"""
import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

_ACK_DIAGNOSTIC_COUNTERS = (
    'request_caught', 'post_request_caught', 'response_caught', 'http_2xx',
    'response_body_caught', 'response_code0', 'response_shape',
    'body_native_video_identity', 'title_match', 'description_match', 'tags_match',
    'cover_main_match', 'cover_extra_match', 'dual_covers_match',
    'account_checked', 'account_current', 'owner_current', 'source_current', 'observer_timing',
    'action_timing', 'update_id_match', 'accepted',
)


def _empty_ack_diagnostics():
    return dict.fromkeys(_ACK_DIAGNOSTIC_COUNTERS, 0)

# B4's one normal VideoCover owner committed both cover/up response URLs on
# archive.biliimg.com (bili-B4-cover-host.json). This is an exact host addition.
BILI_COVER_HOST_PATTERN = r'(?:(?:i[0-9]|s[0-9])\.hdslb\.com|archive\.biliimg\.com)'
BILI_COVER_FORMAT_JS = r"""function safeCover(value){try{
 if(typeof value!=='string'||value.length>4096||/[\s\x00-\x1f\x7f?#\\]/.test(value))return false;
 const parts=/^(?:https?:)?\/\/([^/]+)(\/bfs\/.*)$/i.exec(value);
 if(!parts||!/^__BILI_COVER_HOST__$/.test(parts[1])||!parts[2].startsWith('/bfs/'))return false;
 const u=new URL(value,location.href);
 return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password&&!u.port&&!u.search&&!u.hash&&u.pathname.startsWith('/bfs/')&&
  !parts[2].replace(/%2e/gi,'.').split('/').some(part=>part==='.'||part==='..');
}catch(_){return false}}""".replace('__BILI_COVER_HOST__',BILI_COVER_HOST_PATTERN)

BILI_OBSERVER_SCRIPT = r"""(() => {
'use strict';
const previous=window.__sauBiliEvidenceObserver;
if(previous){
 if(previous.version===2)return;
 if(previous.active||previous.timer||previous.pending?.size||previous.restore?.length)return;
 delete window.__sauBiliEvidenceObserver;
}
const nativeThen=Promise.prototype.then,nativeSlice=Blob.prototype.slice,nativeArrayBuffer=Blob.prototype.arrayBuffer;
const sizeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
const typeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get;
const nameGetter=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
const root={version:2,active:null,pending:new Set(),restore:[],timer:null,seq:0,video:null,cover:null,
 hooks:new WeakMap(),exports:[],uploads:[],counts:{selected:0,terminal:0,video:0,cover:0,rejected:0}};
 async function digest(file,scope) {
   const K = new Uint32Array([
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
   const h = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                             0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
   const w = new Uint32Array(64), R=(v,n)=>(v>>>n)|(v<<(32-n));
   function block(bytes,off) {
     for(let i=0;i<16;i++){const p=off+i*4;w[i]=((bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3])>>>0;}
     for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(R(a,7)^R(a,18)^(a>>>3))+w[i-7]+(R(b,17)^R(b,19)^(b>>>10)))>>>0;}
     let [a,b,c,d,e,f,g,j]=h;
     for(let i=0;i<64;i++){
       const t1=(j+(R(e,6)^R(e,11)^R(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0;
       const t2=((R(a,2)^R(a,13)^R(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
       j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;
     }
     [a,b,c,d,e,f,g,j].forEach((v,i)=>h[i]=(h[i]+v)>>>0);
   }
   let tail=new Uint8Array(0);
   const size=Reflect.apply(sizeGetter,file,[]);
   for(let off=0;off<size;off+=1048576){
     if(root.active!==scope)throw Error('obsolete observer');
     const chunk=Reflect.apply(nativeSlice,file,[off,off+1048576]);
     const bytes=new Uint8Array(await Reflect.apply(nativeArrayBuffer,chunk,[]));
     const merged=new Uint8Array(tail.length+bytes.length);merged.set(tail);merged.set(bytes,tail.length);
     const whole=merged.length-(merged.length%64);
     for(let p=0;p<whole;p+=64)block(merged,p);
     tail=merged.slice(whole);
     await new Promise(resolve=>setTimeout(resolve,0));
   }
   const last=new Uint8Array(tail.length<56?64:128);last.set(tail);last[tail.length]=128;
   const view=new DataView(last.buffer);
   view.setUint32(last.length-8,Math.floor(size/536870912));
   view.setUint32(last.length-4,(size*8)>>>0);
   for(let p=0;p<last.length;p+=64)block(last,p);
   return Array.from(h,x=>x.toString(16).padStart(8,'0')).join('');
 }
function track(p){root.pending.add(p);Reflect.apply(nativeThen,p,[()=>root.pending.delete(p),()=>root.pending.delete(p)]);return p;}
function later(fn){try{track(Promise.resolve().then(fn).catch(()=>{root.counts.rejected++}));}catch(_){root.counts.rejected++;}}
function current(scope){return root.active===scope&&scope!==null;}
function bounded(v,n=2048){return typeof v==='string'&&v.length>0&&v.length<=n&&!/[\s\x00-\x1f\x7f]/.test(v);}
function fileInfo(file){try{const size=Reflect.apply(sizeGetter,file,[]),name=Reflect.apply(nameGetter,file,[]),type=Reflect.apply(typeGetter,file,[]);
 return Number.isSafeInteger(size)&&size>0&&name?{name,size,type}:null}catch(_){return null}}
function vms(){const out=new Set(),els=document.querySelectorAll('*');if(els.length>20000)return [];
 for(const el of els){let v=el.__vue__;for(let n=0;v&&n<20;n++,v=v.$parent){if(out.has(v))break;out.add(v)}}return [...out].filter(v=>v.$el?.isConnected);}
function publisher(){const found=vms().filter(v=>Array.isArray(v.videoTasks)&&v.$recordConfig&&typeof v.checkSubTask==='function'&&typeof v.handleSubmitDraft==='function');return found.length===1?found[0]:null;}
function manager(){const a=new Set(vms().map(v=>v.$taskConfig).filter(c=>c&&Array.isArray(c.tasks)&&typeof c.createTask==='function'));return a.size===1?[...a][0]:null;}
function editor(){const found=vms().filter(v=>v.$el?.matches?.('.cover-editor')&&typeof v.handleSubmit==='function'&&typeof v.handleUploadChange==='function'&&v.coverForEditor);return found.length===1?found[0]:null;}
function coverParent(){const found=vms().filter(v=>typeof v.submitCoverEditor==='function'&&typeof v.openCoverEditor==='function'&&Array.isArray(v.coverList));return found.length===1?found[0]:null;}
function sourceInput(kind){const query=kind==='video'?'.upload-wrp .upload.bcc-upload > input[type=file], .upload-wrp .upload.bcc-upload > .bcc-upload-wrapper > input[type=file]':'.cover-editor .cover-upload.bcc-upload > input[type=file], .cover-editor .cover-upload.bcc-upload > .bcc-upload-wrapper > input[type=file]';const a=[...document.querySelectorAll(query)];return a.length===1?a[0]:null;}
function wrap(obj,key,observe){if(!obj||typeof obj[key]!=='function')return false;let keys=root.hooks.get(obj);if(keys?.has(key))return true;
 const d=Object.getOwnPropertyDescriptor(obj,key);if(d&&(!('value'in d)||d.writable!==true))return false;
 const original=obj[key];function wrapped(...args){return observe(original,this,args)}
 try{Object.defineProperty(obj,key,{configurable:true,writable:true,value:wrapped});if(!keys){keys=new Set();root.hooks.set(obj,keys)}keys.add(key);
 root.restore.push(()=>{if(obj[key]===wrapped){if(d)Object.defineProperty(obj,key,d);else delete obj[key]}});return true}catch(_){return false}}
function deliver(scope,kind,data){if(!current(scope))return Promise.resolve(false);const fn=window[scope.binding];if(typeof fn!=='function')return Promise.resolve(false);
 const report={v:1,job_id:scope.job_id,receipt_nonce:scope.receipt_nonce,observer_nonce:scope.observer_nonce,kind,...data};return fn(report);}
function selectionLive(sel){return !!sel&&current(sel.scope)&&root[sel.kind]===sel&&manager()===sel.manager&&(sel.kind==='video'||sel.input.isConnected&&sourceInput(sel.kind)===sel.input);}
function taskLive(sel,task,sdk){return selectionLive(sel)&&sel.manager.tasks.length===1&&sel.manager.tasks[0]===sel.parentTask&&sel.parentTask.videoTasks.length===1&&sel.parentTask.videoTasks[0]===task&&task.file===sel.file&&sdk===task.uploader&&sdk.file===sel.file;}
function observeTask(task){const sel=root.video,sdk=task?.uploader;if(!sel||!sdk||!taskLive(sel,task,sdk))return;
 wrap(sdk,'emit',(original,receiver,args)=>{let capture=null;
  try{if(receiver===sdk&&args[0]==='uploadComplete'&&taskLive(sel,task,sdk)&&args.length===2){const result=args[1];if(result&&bounded(String(result.cid),128)&&/^\d+$/.test(String(result.cid))&&bounded(result.filename))capture={cid:String(result.cid),filename:result.filename,completed_at:Date.now()/1000};root.counts.terminal++}}catch(_){}
  const returned=Reflect.apply(original,receiver,args);
  if(capture)later(async()=>{await Promise.resolve();const inner=sdk.uploader;
   if(!taskLive(sel,task,sdk)||!publisher()||publisher().videoTasks[0]!==task||publisher().videoTasks.length!==1||task.status!==3||!inner||inner.completed!==true||inner.file!==sel.file||
    String(task.cid)!==capture.cid||String(sdk.cid)!==capture.cid||task.filename!==capture.filename||sdk.filename!==capture.filename||
    !['stdSlice','slice'].includes(sdk.uploaderType))return;
   const sha256=await sel.digest;if(!taskLive(sel,task,sdk))return;
   if(await deliver(sel.scope,'video',{selection:sel.seq,...sel.info,sha256,cid:capture.cid,filename:capture.filename,
    selected_at:sel.at,completed_at:capture.completed_at}))root.counts.video++;
  });return returned;
 });}
function coverLive(sel,e){return selectionLive(sel)&&sel.editor===e&&editor()===e&&sel.origin&&(sel.verifierWrapped?sel.verified===true:sel.native_origin===true)&&e.coverForEditor.origin_4_3===sel.origin&&e.coverForEditor.origin_16_9===sel.origin&&e.coverForEditor.source_4_3==='upload'&&e.coverForEditor.source_16_9==='upload'&&e.isCoverEditorReady_4_3===true&&e.isCoverEditorReady_16_9===true;}
async function jpegInfo(value,scope){if(typeof value!=='string'||value.length>45000000||!/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(value))return null;
 const binary=atob(value.slice(value.indexOf(',')+1));if(!binary.length||binary.length>33554432)return null;
 const bytes=Uint8Array.from(binary,x=>x.charCodeAt(0));return {derived_sha256:await digest(new Blob([bytes],{type:'image/jpeg'}),scope),derived_size:bytes.length,derived_type:'image/jpeg'};}
function observeCover(e){const sel=root.cover;if(!sel||sel.editor!==e)return;
 // Prefer the actual official verifier when writable; ES module accessors are
 // not redefined. The native object-URL fallback below observes only this File.
 const tool=e.$tool;
 sel.verifierWrapped=wrap(tool,'verifyAndLoadCoverFile',(original,receiver,args)=>{const eligible=receiver===tool&&args[0]===sel.file&&args[1]===true&&selectionLive(sel)&&editor()===e;
  const returned=Reflect.apply(original,receiver,args);if(eligible&&returned instanceof Promise)Reflect.apply(nativeThen,returned,[result=>{if(selectionLive(sel)&&editor()===e&&result&&typeof result.blobUrl==='string'&&result.blobUrl.startsWith('blob:')&&result.width>0&&result.height>0){sel.origin=result.blobUrl;sel.verified=true}},()=>{}]);return returned;});
 for(const ratio of ['4_3','16_9']){const image=e['imageEditor_'+ratio];if(!image)continue;
  wrap(image,'exportImage',(original,receiver,args)=>{let capture=null;try{if(receiver===image&&coverLive(sel,e)&&e['imageEditor_'+ratio]===image&&args.length===1&&args[0]?.quality===.8)capture={sel,e,image,ratio,at:Date.now()/1000,seq:String(++root.seq)}}catch(_){}
   const returned=Reflect.apply(original,receiver,args);if(capture&&returned instanceof Promise)Reflect.apply(nativeThen,returned,[value=>{if(!coverLive(sel,e)||typeof value!=='string')return;
    const record={...capture,value,result_at:Date.now()/1000};record.digest=track(jpegInfo(value,sel.scope));root.exports.push(record)},()=>{}]);return returned;});
 }
 const api=e.$api;
 wrap(api,'uploadImage',(original,receiver,args)=>{let capture=null;try{if(receiver===api&&args.length===1&&coverLive(sel,e)){const records=root.exports.filter(r=>r.sel===sel&&r.e===e&&r.value===args[0]&&!r.used);if(records.length===1){capture=records[0];capture.used=true;capture.started_at=Date.now()/1000}}}catch(_){}
  const returned=Reflect.apply(original,receiver,args);if(capture&&returned instanceof Promise)Reflect.apply(nativeThen,returned,[result=>{if(coverLive(sel,e)&&result&&safeCover(result.url)){capture.url=result.url;capture.completed_at=Date.now()/1000;root.uploads.push(capture)}},()=>{}]);return returned;});
 wrap(e,'$emit',(original,receiver,args)=>{let capture=null;
  try{if(receiver===e&&args[0]==='submit'&&args.length===2&&coverLive(sel,e)){const payload=args[1],p=coverParent();let records=[];
    if(p&&payload&&payload.origin_4_3===sel.origin&&payload.origin_16_9===sel.origin&&payload.source_4_3==='upload'&&payload.source_16_9==='upload'){
     for(const ratio of ['4_3','16_9']){const a=root.uploads.filter(r=>r.sel===sel&&r.ratio===ratio&&r.url===payload['edited_'+ratio]);if(a.length!==1){records=[];break}records.push(a[0])}
     if(records.length===2&&records[0]!==records[1])capture={p,records,applied_at:Date.now()/1000};
    }}}
  catch(_){}const returned=Reflect.apply(original,receiver,args);
  if(capture)later(async()=>{const p=capture.p,list=p.coverList;if(!current(sel.scope)||!p.$el?.isConnected||!Array.isArray(list)||list.length!==1||p.defaultCoverIndex!==0)return;
   const [a,b]=capture.records,main=list[0].main,extra=list[0].extra;
   if(!main||!extra||main.origin!==sel.origin||extra.origin!==sel.origin||main.source!=='upload'||extra.source!=='upload'||main.edited!==b.url||extra.edited!==a.url)return;
   const origin_sha256=await sel.digest;for(const r of capture.records){const derived=await r.digest;if(!derived||!current(sel.scope)||root.cover!==sel)return;
    if(await deliver(sel.scope,'cover',{selection:sel.seq,ratio:r.ratio,origin_name:sel.info.name,origin_size:sel.info.size,origin_type:sel.info.type,
      origin_sha256,...derived,url:r.url,selected_at:sel.at,transform_at:r.at,started_at:r.started_at,completed_at:r.completed_at,applied_at:capture.applied_at}))root.counts.cover++;
   }});return returned;});
}
__BILI_COVER_FORMAT__
function scan(){if(!root.active)return;const m=manager();if(m)wrap(m,'createTask',(original,receiver,args)=>{const sel=root.video;let eligible=false;try{eligible=receiver===m&&selectionLive(sel)&&m.tasks.length===0&&args.length===1&&Array.isArray(args[0]?.videoTasks)&&args[0].videoTasks.length===1&&args[0].videoTasks[0].file===sel.file}catch(_){}const returned=Reflect.apply(original,receiver,args);if(eligible&&returned&&Array.isArray(returned.videoTasks)&&returned.videoTasks.length===1&&returned.videoTasks[0].file===sel.file){sel.parentTask=returned;observeTask(returned.videoTasks[0])}return returned;});const sel=root.video;if(sel?.parentTask)for(const t of sel.parentTask.videoTasks)observeTask(t);const e=editor();if(e)observeCover(e);}
function selected(event){const scope=root.active,input=event.target;if(!scope||!(input instanceof HTMLInputElement)||input.type!=='file'||input.files.length!==1)return;
 let kind=null;if(input===sourceInput('video'))kind='video';else if(input===sourceInput('cover'))kind='cover';if(!kind)return;
 const p=publisher(),m=manager(),e=kind==='cover'?editor():null;if(!m||kind==='video'&&m.tasks.length!==0||kind==='cover'&&(!p||!e||p.videoTasks.length!==1))return;
 const file=input.files[0],info=fileInfo(file);if(!info||kind==='cover'&&!['image/png','image/jpeg'].includes(info.type))return;
 const sel={kind,scope,input,file,info,publisher:p,manager:m,parentTask:kind==='cover'?m.tasks[0]:null,editor:e,seq:String(++root.seq),at:Date.now()/1000,origin:null,verified:false};root[kind]=sel;root.counts.selected++;
 sel.digest=track(digest(file,scope));scan();
}
function nativeURLFallback(){return wrap(URL,'createObjectURL',(original,receiver,args)=>{
 const sel=root.cover;let eligible=false;try{eligible=receiver===URL&&args.length===1&&sel&&args[0]===sel.file&&selectionLive(sel)&&editor()===sel.editor}catch(_){}
 const returned=Reflect.apply(original,receiver,args);if(eligible&&typeof returned==='string'&&returned.startsWith('blob:')){sel.origin=returned;sel.native_origin=true}return returned;
});}
root.configure=scope=>{if(root.active)return root.active.observer_nonce===scope.observer_nonce;
 if(!scope||!manager())return false;root.active=scope;document.addEventListener('change',selected,true);nativeURLFallback();scan();root.timer=setInterval(scan,20);return true;};
root.status=nonce=>{if(root.active?.observer_nonce!==nonce)return null;const p=publisher();return {...root.counts,ready:!!manager(),publisher_count:p?1:0,task_count:p?.videoTasks.length??-1,selection_live:selectionLive(root.video)};};
root.flush=async nonce=>{if(root.active?.observer_nonce!==nonce)return false;scan();for(let i=0;i<8&&root.pending.size;i++)await Promise.allSettled([...root.pending]);return root.active?.observer_nonce===nonce;};
root.close=nonce=>{if(root.active?.observer_nonce!==nonce)return false;root.active=null;clearInterval(root.timer);root.timer=null;document.removeEventListener('change',selected,true);for(const restore of root.restore.reverse())try{restore()}catch(_){}root.restore=[];root.hooks=new WeakMap();root.video=root.cover=null;root.exports=[];root.uploads=[];return true;};
window.__sauBiliEvidenceObserver=root;
})()""".replace('__BILI_COVER_FORMAT__',BILI_COVER_FORMAT_JS)

BILI_OWNER_PROBE = r"""(() => {
 const els=document.querySelectorAll('*');if(els.length>20000)return {publishers:0};
 const seen=new Set(),found=new Set();for(const el of els){let v=el.__vue__;for(let i=0;v&&i<20;i++,v=v.$parent){if(seen.has(v))break;seen.add(v);
 if(v.$el?.isConnected&&v.$taskConfig&&Array.isArray(v.$taskConfig.tasks)&&typeof v.$taskConfig.createTask==='function')found.add(v.$taskConfig);}}
 return {publishers:found.size};
})()"""


def _official(url, paths=None):
    try:
        u = urlsplit(url)
        return (u.scheme == 'https' and u.netloc == 'member.bilibili.com' and not u.fragment and
                (paths is None or u.path in paths))
    except (ValueError, TypeError):
        return False


def _token(value, size=2048):
    return isinstance(value, str) and 0 < len(value) <= size and not re.search(r'[\s\x00-\x1f\x7f]', value)


def _numeric(value):
    return type(value) in (str, int) and re.fullmatch(r'[1-9][0-9]{0,19}', str(value)) is not None


def bili_cover_identity(value):
    """Official M376509 ez() representation of one unsigned static resource.

    Only the documented public CDN/path is eligible; no parameter is removed,
    no unknown URL is normalized, and this identity alone is not upload proof.
    """
    if not _token(value, 4096):
        return None
    try:
        u = urlsplit('https:' + value if value.startswith('//') else value)
        if (u.scheme not in ('http', 'https') or re.fullmatch(BILI_COVER_HOST_PATTERN, u.netloc) is None
                or not u.path.startswith('/bfs/') or u.query or u.fragment or re.search(r'[?#\\]',value) or
                any(part in ('.','..') for part in re.sub(r'%2e','.',u.path,flags=re.I).split('/'))):
            return None
        return 'https://' + u.netloc + u.path
    except ValueError:
        return None


def _cover_url(value):
    return bili_cover_identity(value) is not None


SAVE_PATHS = {'/x/vupre/web/draft/add', '/x/vupre/web/draft/update'}
LIST_PATHS = {'/x/vupre/web/draft/list'}
VIEW_PATHS = {'/x/vupre/web/draft/view'}


class _NativeTransport:
    """Native default contexts and normal Network events; no business calls."""
    def __init__(self, observer):
        self.observer = observer
        self.client = None
        self.contexts, self.frames, self.requests = {}, {}, {}
        self.context_id = self.root_frame = self.owner_frame = None
        self.raw = '__sauBiliTransport_' + observer.nonce
        self.closed = False
        self.tasks = set()
        self.handlers = {'Runtime.executionContextCreated': self._created,
                         'Runtime.executionContextDestroyed': self._destroyed,
                         'Runtime.executionContextsCleared': self._cleared,
                         'Runtime.bindingCalled': self._called,
                         'Page.frameNavigated': self._frame,
                         'Network.requestWillBeSent': self._request,
                         'Network.responseReceived': self._response,
                         'Network.loadingFinished': self._finished}

    def _created(self, event):
        ctx = event.get('context', {})
        if type(ctx.get('id')) is int:
            self.contexts[ctx['id']] = dict(ctx.get('auxData') or {})

    def _destroyed(self, event):
        ident = event.get('executionContextId')
        self.contexts.pop(ident, None)
        if ident == self.context_id:
            self.context_id = self.owner_frame = None

    def _cleared(self, _):
        self.contexts.clear()
        self.context_id = self.owner_frame = None

    def _frame(self, event):
        frame = event.get('frame', {})
        if frame.get('id'):
            self.frames[frame['id']] = frame
            if not frame.get('parentId'):
                self.root_frame = frame['id']

    def _allowed_frame(self, ident):
        visited = set()
        while ident and ident not in visited and len(visited) < 8:
            visited.add(ident)
            frame = self.frames.get(ident, {})
            if not _official(frame.get('url')):
                return False
            if ident == self.root_frame:
                return True
            ident = frame.get('parentId')
        return False

    def _canonical(self, ident):
        aux = self.contexts.get(ident, {})
        return (not self.closed and ident == self.context_id and aux.get('isDefault') is True and
                aux.get('frameId') == self.owner_frame and self._allowed_frame(self.owner_frame))

    async def prepare(self):
        if not self.observer._current():
            return False
        if self.client is None:
            self.client = await self.observer.page.context.new_cdp_session(self.observer.page)
            await self.client.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
            for event, handler in self.handlers.items():
                self.client.on(event, handler)
            await self.client.send('Page.enable')
            def store_tree(tree, parent=None):
                frame = dict(tree['frame'])
                if parent:
                    frame['parentId'] = parent
                self._frame({'frame': frame})
                for child in tree.get('childFrames', []):
                    store_tree(child, frame['id'])
            store_tree((await self.client.send('Page.getFrameTree'))['frameTree'])
            await self.client.send('Runtime.enable')
            # Normal draft save immediately leaves the upload renderer. CDP's
            # durable buffer preserves the observed reply across that navigation;
            # without it a finished ACK can vanish before getResponseBody runs.
            await self.client.send('Network.enable', {'maxPostDataSize': 1048576,
                    'maxTotalBufferSize': 8388608, 'maxResourceBufferSize': 2097152,
                    'enableDurableMessages': True})
        candidates = []
        for ident, aux in tuple(self.contexts.items()):
            frame = self.frames.get(aux.get('frameId'), {})
            if not aux.get('isDefault') or not self._allowed_frame(aux.get('frameId')):
                continue
            path = urlsplit(frame.get('url', '')).path
            if not (path.startswith('/platform/upload/video/') or path.startswith('/york/videoup')):
                continue
            result = await self.client.send('Runtime.evaluate', {'expression': BILI_OWNER_PROBE,
                     'contextId': ident, 'returnByValue': True})
            value = result.get('result', {}).get('value')
            if isinstance(value, dict) and value.get('publishers') == 1:
                candidates.append(ident)
        if len(candidates) != 1:
            return False
        ident = candidates[0]
        if self._canonical(ident):
            return True
        self.context_id, self.owner_frame = ident, self.contexts[ident]['frameId']
        await self.client.send('Runtime.addBinding', {'name': self.raw, 'executionContextId': ident})
        # Native binding calls return undefined; acknowledgements are not proof.
        # The observer only forwards metadata from this exact current context.
        expression = '(window[' + json.dumps(self.observer.binding) + ']=report=>{window[' + json.dumps(self.raw) + '](JSON.stringify(report));return true},true)'
        await self.evaluate(expression)
        return True

    async def evaluate(self, expression):
        ident = self.context_id
        if not self._canonical(ident):
            raise RuntimeError('bili_native_context_unavailable')
        result = await self.client.send('Runtime.evaluate', {'expression': expression, 'contextId': ident,
                                                           'awaitPromise': True, 'returnByValue': True})
        if result.get('exceptionDetails') or not self._canonical(ident):
            raise RuntimeError('bili_native_context_changed')
        return result.get('result', {}).get('value')

    def _called(self, event):
        if event.get('name') != self.raw or not self._canonical(event.get('executionContextId')):
            return
        value = event.get('payload')
        if not isinstance(value, str) or len(value) > 16384:
            return
        try:
            report = json.loads(value)
        except (ValueError, TypeError):
            return
        self.observer.accept(report)

    def _request(self, event):
        request = event.get('request', {})
        url = request.get('url')
        if (not self.observer._current() or event.get('redirectResponse') is not None or
                not self._allowed_frame(event.get('frameId')) or not _official(url, SAVE_PATHS | LIST_PATHS | VIEW_PATHS)):
            return
        is_save_ack = urlsplit(url).path in SAVE_PATHS
        if is_save_ack:
            self.observer._note_ack_request(request.get('method'))
        if len(self.requests) >= 128:
            return
        self.requests[event['requestId']] = {'url': url, 'method': request.get('method'),
                   'body': request.get('postData'), 'started_at': event.get('wallTime'),
                   'received_at': None, 'status': None, 'is_save_ack': is_save_ack,
                   'response_caught': False}

    def _response(self, event):
        entry = self.requests.get(event.get('requestId'))
        response = event.get('response', {})
        if entry and response.get('url') == entry['url']:
            entry.update(status=response.get('status'), received_at=time.time())
            if entry.get('is_save_ack') and not entry.get('response_caught'):
                entry['response_caught'] = True
                self.observer._note_ack_response(entry.get('status'))

    def _finished(self, event):
        ident = event.get('requestId')
        entry = self.requests.pop(ident, None)
        if not entry or entry['status'] is None:
            return
        task = asyncio.create_task(self._read(ident, entry))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _read(self, ident, entry):
        try:
            raw = await self.client.send('Network.getResponseBody', {'requestId': ident})
            if raw.get('base64Encoded') or not isinstance(raw.get('body'), str) or len(raw['body']) > 2097152:
                return
            data = json.loads(raw['body'])
            body = json.loads(entry['body']) if isinstance(entry['body'], str) and len(entry['body']) <= 1048576 else None
            await self.observer.observe_exchange(**{key: entry[key] for key in ('url', 'method', 'status', 'started_at')},
                                                  observed_at=entry['received_at'], data=data, body=body)
        except (ValueError, TypeError, KeyError):
            return
        except Exception:
            return

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def close(self):
        self.closed = True
        if self.client is None:
            return
        for event, handler in self.handlers.items():
            self.client.remove_listener(event, handler)
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        try:
            await self.client.send('Runtime.removeBinding', {'name': self.raw})
        except Exception:
            pass
        await self.client.detach()


class BiliEvidenceObserver:
    """Private per-session proof. Partial candidates permit readback only."""
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.nonce = secrets.token_hex(24)
        self.binding = '__sauBiliEvidence_' + self.nonce
        self.scope = {'job_id': session.expected.get('job_id'),
                      'receipt_nonce': session.expected.get('receipt_nonce'), 'observer_nonce': self.nonce}
        self.started_at = time.time()
        self.closed = self.available = False
        self.records, self.invalid = {}, set()
        self.acks, self.lists, self.views = [], [], []
        self._ack_diagnostic_counts = _empty_ack_diagnostics()
        self.transport = _NativeTransport(self)

    def _current(self):
        return (not self.closed and not self.session.closed and self.session.platform == 'bilibili' and
                getattr(self.page, '_sau_draft_evidence', None) is self.session and
                getattr(self.session, 'bili_observer', None) is self and
                self.session.expected.get('job_id') == self.scope['job_id'] and
                self.session.expected.get('receipt_nonce') == self.scope['receipt_nonce'])

    def _note_ack_request(self, method):
        self._ack_diagnostic_counts['request_caught'] += 1
        if method == 'POST':
            self._ack_diagnostic_counts['post_request_caught'] += 1

    def _note_ack_response(self, status):
        self._ack_diagnostic_counts['response_caught'] += 1
        if (type(status) in (int, float) and math.isfinite(status) and 200 <= status < 300):
            self._ack_diagnostic_counts['http_2xx'] += 1

    def _ack_diagnostic_snapshot(self, wait_result=None):
        safe = {key: int(self._ack_diagnostic_counts[key]) for key in _ACK_DIAGNOSTIC_COUNTERS}
        safe['source_current_at_wait_end'] = bool(self._current())
        if wait_result is not None:
            safe['wait_result'] = bool(wait_result)
        return safe

    def _emit_ack_diagnostic(self, wait_result):
        # This public record deliberately contains only fixed gate names and
        # counters. It never includes bodies, endpoints, IDs, or account data.
        print('BILI_SAVE_ACK_DIAGNOSTIC=' + json.dumps(
            self._ack_diagnostic_snapshot(wait_result), sort_keys=True, separators=(',', ':')), flush=True)

    async def initialize(self):
        return await self.ensure_ready()

    async def ensure_ready(self, timeout=5):
        if type(timeout) not in (int, float) or not 0 < timeout <= 10 or not self._current():
            return False
        deadline = time.monotonic() + timeout
        while self._current() and time.monotonic() < deadline:
            try:
                if await self.transport.prepare():
                    await self.transport.evaluate(BILI_OBSERVER_SCRIPT)
                    self.available = bool(await self.transport.evaluate(
                        'window.__sauBiliEvidenceObserver.version===2&&window.__sauBiliEvidenceObserver.configure(' + json.dumps({**self.scope, 'binding': self.binding}) + ')'))
                    if self.available:
                        return True
            except Exception:
                pass
            await asyncio.sleep(.1)
        return False

    def accept(self, report):
        if not self._current() or not isinstance(report, dict) or type(report.get('v')) is not int or report['v'] != 1:
            return False
        if any(report.get(key) != value for key, value in self.scope.items()):
            return False
        base = {'v', 'job_id', 'receipt_nonce', 'observer_nonce', 'kind', 'selection'}
        kind = report.get('kind')
        if kind == 'video':
            if set(report) != base | {'name', 'size', 'type', 'sha256', 'cid', 'filename', 'selected_at', 'completed_at'}:
                return False
            asset = self.session.assets.get('video', {})
            if (report.get('name'), report.get('size'), report.get('sha256')) != (asset.get('name'), asset.get('size'), asset.get('sha256')):
                return False
            if (type(report['size']) is not int or report['size'] <= 0 or report['sha256'] != self.session.expected.get('video_sha256') or
                    not re.fullmatch(r'[0-9a-f]{64}', str(report['sha256'])) or not _numeric(report.get('cid')) or not _token(report.get('filename')) or
                    not isinstance(report.get('type'), str) or len(report['type']) > 128):
                return False
            times = [report.get('selected_at'), report.get('completed_at')]
            key = ('video', report.get('selection'))
        elif kind == 'cover':
            if set(report) != base | {'ratio', 'origin_name', 'origin_size', 'origin_type', 'origin_sha256', 'derived_sha256',
                                      'derived_size', 'derived_type', 'url', 'selected_at', 'transform_at', 'started_at', 'completed_at', 'applied_at'}:
                return False
            asset = self.session.assets.get('cover', {})
            if (report.get('origin_name'), report.get('origin_size'), report.get('origin_sha256')) != (asset.get('name'), asset.get('size'), asset.get('sha256')):
                return False
            if (report.get('origin_sha256') != self.session.expected.get('cover_sha256', {}).get('cover') or
                    report.get('ratio') not in ('4_3', '16_9') or report.get('derived_type') != 'image/jpeg' or
                    report.get('origin_type') not in ('image/png', 'image/jpeg') or not _cover_url(report.get('url'))):
                return False
            if any(type(report.get(k)) is not int or not 0 < report[k] <= 33554432 for k in ('origin_size', 'derived_size')):
                return False
            if any(not re.fullmatch(r'[0-9a-f]{64}', str(report.get(k))) for k in ('origin_sha256', 'derived_sha256')):
                return False
            times = [report.get(k) for k in ('selected_at', 'transform_at', 'started_at', 'completed_at', 'applied_at')]
            key = ('cover', report.get('selection'), report.get('ratio'))
        else:
            return False
        if (not _numeric(report.get('selection')) or any(type(v) not in (int, float) or not math.isfinite(v) for v in times) or
                not self.started_at <= times[0] <= times[-1] <= time.time() + 5 or times != sorted(times)):
            return False
        if key in self.invalid:
            return False
        if key in self.records and self.records[key] != report:
            self.records.pop(key, None)
            self.invalid.add(key)
            return False
        self.records[key] = dict(report)
        return True

    def _video(self):
        records = [r for r in self.records.values() if r['kind'] == 'video']
        return records[0] if len(records) == 1 else None

    def _covers(self):
        values = [r for r in self.records.values() if r['kind'] == 'cover']
        if not self.session.assets.get('cover'):
            return {} if not values else None
        if len(values) != 2 or len({r['selection'] for r in values}) != 1 or {r['ratio'] for r in values} != {'4_3', '16_9'}:
            return None
        return {r['ratio']: r for r in values}

    def upload_links(self):
        if not self._current():
            return []
        video, covers = self._video(), self._covers()
        links = []
        if video:
            links.append({'kind': 'video', 'sha256': video['sha256'], 'ids': [str(video['cid']), video['filename']], 'complete': True})
        if covers:
            links.extend({'kind': 'cover', 'sha256': r['origin_sha256'], 'ids': [bili_cover_identity(r['url'])], 'complete': True} for r in covers.values())
        return links

    async def _owner_state(self):
        current = await self.session.read_account()
        expected = self.session.expected.get('account', {})
        initial = self.session.account
        account_current = (isinstance(current, dict) and current.get('verified') is True and
                           current.get('platform') == 'bilibili' and _numeric(current.get('uid')) and
                           current.get('identity') == expected.get('identity') and
                           str(current.get('uid')) == str(expected.get('uid')))
        owner_current = bool(self._current() and account_current and current == initial)
        return (str(current['uid']) if owner_current else None), bool(account_current), owner_current

    async def _owner(self):
        owner, _, _ = await self._owner_state()
        return owner

    def _ack_body_diagnostic(self, body):
        from uploader.draft_evidence import _text, description_values, parse_record
        video, covers = self._video(), self._covers()
        items = body.get('videos') if isinstance(body, dict) else None
        item = items[0] if isinstance(items, list) and len(items) == 1 else None
        video_match = bool(video and isinstance(item, dict) and
                           str(item.get('cid')) == str(video['cid']) and item.get('filename') == video['filename'])
        record = parse_record('bilibili', body) if isinstance(body, dict) else None
        fields = self.session.expected.get('platform_fields', {})
        title_match = bool(isinstance(body, dict) and isinstance(body.get('title'), str) and record and
                           _text(record.get('title')) == _text(fields.get('title')))
        description_match = bool(isinstance(body, dict) and isinstance(body.get('desc'), str) and record and
                                 _text(record.get('description')) in description_values('bilibili', fields))
        tags_match = bool(isinstance(body, dict) and isinstance(body.get('tag'), str) and record and
                          record.get('tags') is not None and
                          sorted(record['tags']) == sorted(fields.get('tags', [])))
        main_match = bool(covers is not None and (not covers or
                          bili_cover_identity(body.get('cover') if isinstance(body, dict) else None) ==
                          bili_cover_identity(covers['16_9']['url'])))
        extra_match = bool(covers is not None and (not covers or
                           bili_cover_identity(body.get('cover43') if isinstance(body, dict) else None) ==
                           bili_cover_identity(covers['4_3']['url'])))
        return {'body_native_video_identity': video_match, 'title_match': title_match,
                'description_match': description_match, 'tags_match': tags_match,
                'cover_main_match': main_match, 'cover_extra_match': extra_match,
                'dual_covers_match': main_match and extra_match}

    def _ack_matches(self, body):
        from uploader.draft_evidence import field_match, parse_record
        video, covers = self._video(), self._covers()
        if not video or covers is None or (self.session.action_at is not None and (video['completed_at'] > self.session.action_at or
                any(r['applied_at'] > self.session.action_at for r in (covers or {}).values()))) or not isinstance(body, dict) or not isinstance(body.get('videos'), list) or len(body['videos']) != 1:
            return False
        item = body['videos'][0]
        if not isinstance(item, dict) or str(item.get('cid')) != str(video['cid']) or item.get('filename') != video['filename']:
            return False
        if (not isinstance(body.get('title'), str) or not isinstance(body.get('desc'), str) or not isinstance(body.get('tag'), str) or
                not field_match('bilibili', parse_record('bilibili', body), self.session.expected)):
            return False
        if covers and (bili_cover_identity(body.get('cover')) != bili_cover_identity(covers['16_9']['url']) or bili_cover_identity(body.get('cover43')) != bili_cover_identity(covers['4_3']['url'])):
            return False
        return True

    async def _observe_save_ack(self, *, path, method, status, started_at, observed_at, data, body):
        counts = self._ack_diagnostic_counts
        counts['response_body_caught'] += 1
        source_current = self._current()
        if source_current:
            counts['source_current'] += 1
        if not source_current:
            return False

        http_2xx = type(status) in (int, float) and math.isfinite(status) and 200 <= status < 300
        code0 = isinstance(data, dict) and type(data.get('code')) is int and data['code'] == 0
        shape = (isinstance(data, dict) and set(data) == {'code', 'message', 'ttl'} and
                 type(data.get('code')) is int and isinstance(data.get('message'), str) and
                 type(data.get('ttl')) is int)
        timing = (all(type(t) in (int, float) and math.isfinite(t) for t in (started_at, observed_at)) and
                  self.started_at <= started_at <= observed_at <= time.time() + 5)
        video, covers = self._video(), self._covers()
        action_timing = (self.session.action_at is not None and timing and started_at >= self.session.action_at and
                         video is not None and covers is not None and video['completed_at'] <= self.session.action_at and
                         all(r['applied_at'] <= self.session.action_at for r in covers.values()))
        try:
            body_gates = self._ack_body_diagnostic(body)
        except Exception:
            body_gates = {key: False for key in ('body_native_video_identity', 'title_match', 'description_match',
                                                  'tags_match', 'cover_main_match', 'cover_extra_match',
                                                  'dual_covers_match')}
        try:
            owner, account_current, owner_current = await self._owner_state()
        except Exception:
            owner, account_current, owner_current = None, False, False
        counts['account_checked'] += 1
        update_id_match = path not in SAVE_PATHS or not path.endswith('/update') or (
            isinstance(body, dict) and _numeric(body.get('id')))
        for key, passed in {
            'response_code0': code0, 'response_shape': shape,
            **body_gates,
            'account_current': account_current, 'owner_current': owner_current,
            'observer_timing': timing, 'action_timing': action_timing,
            'update_id_match': update_id_match,
        }.items():
            if passed:
                counts[key] += 1

        # Keep the existing acceptance gate intact. These diagnostics only
        # explain which fixed checks passed for the current observer nonce.
        if (not http_2xx or not code0 or not timing or owner is None or method != 'POST' or
                not shape or self.session.action_at is None or started_at < self.session.action_at or
                not self._ack_matches(body)):
            return False
        if path.endswith('/update') and not _numeric(body.get('id')):
            return False
        self.acks.append({'owner': owner, 'body': json.loads(json.dumps(body)), 'started_at': started_at,
                          'observed_at': observed_at, 'path': path})
        counts['accepted'] += 1
        return True

    async def observe_exchange(self, *, url, method, status, started_at, observed_at, data, body=None):
        if not _official(url, SAVE_PATHS | LIST_PATHS | VIEW_PATHS):
            return False
        path = urlsplit(url).path
        if path in SAVE_PATHS:
            return await self._observe_save_ack(path=path, method=method, status=status, started_at=started_at,
                                                observed_at=observed_at, data=data, body=body)
        if not self._current():
            return False
        if (type(status) not in (int, float) or not 200 <= status < 300 or not isinstance(data, dict) or
                type(data.get('code')) is not int or data['code'] != 0 or
                any(type(t) not in (int, float) or not math.isfinite(t) for t in (started_at, observed_at)) or
                not self.started_at <= started_at <= observed_at <= time.time() + 5):
            return False
        owner = await self._owner()
        if owner is None:
            return False
        if method != 'GET':
            return False
        if path in LIST_PATHS:
            rows = data.get('data')
            if not isinstance(rows, list) or len(rows) > 128:
                return False
            if any(not isinstance(row, dict) or not _numeric(row.get('id')) or not _numeric(row.get('mid')) for row in rows):
                return False
            self.lists.append({'rows': json.loads(json.dumps(rows)), 'owner': owner,
                               'started_at': started_at, 'observed_at': observed_at})
            return True
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        ids = query.get('id')
        if (self.session.reopen_at is None or started_at < self.session.reopen_at or not ids or len(ids) != 1 or not _numeric(ids[0])):
            return False
        raw = data.get('data')
        if not isinstance(raw, dict) or str(raw.get('id')) != ids[0] or not _numeric(raw.get('mid')) or str(raw['mid']) != owner:
            return False
        # A real draft view must contain both the actual saved archive fields
        # and exactly one real server media entry. No caller body is supplied.
        from uploader.draft_evidence import parse_record
        if any(not isinstance(raw.get(key), str) for key in ('title', 'desc', 'tag')):
            return False
        videos = raw.get('videos')
        if (not isinstance(videos, list) or len(videos) != 1 or not isinstance(videos[0], dict) or
                not _numeric(videos[0].get('cid')) or not _token(videos[0].get('filename'))):
            return False
        record = parse_record('bilibili', raw, ids[0])
        if (not record or record.get('owner') != owner or record.get('video_count') != 1 or
                not isinstance(record.get('title'), str) or not isinstance(record.get('description'), str) or
                not isinstance(record.get('tags'), list)):
            return False
        if any(bili_cover_identity(value) is None for value in record['cover_ids']):
            return False
        record['cover_ids'] = sorted(set(bili_cover_identity(value) for value in record['cover_ids']))
        record['cover_slots'] = {'cover': [bili_cover_identity(raw['cover'])] if raw.get('cover') else [],
                                 'cover43': [bili_cover_identity(raw['cover43'])] if raw.get('cover43') else []}
        record.update(observed_at=observed_at, request_started_at=started_at, full_fields_source='normal_draft_view')
        self.views.append(record)
        return True

    async def wait_saved_ack(self, timeout=10):
        """Wait for the existing normal save response; never click/retry save."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            return False
        deadline = time.monotonic() + timeout
        accepted = False
        while self._current() and time.monotonic() < deadline:
            await self.drain()
            if len(self.acks) > 1:
                break
            if len(self.acks) == 1:
                accepted = bool(self._ack_matches(self.acks[0]['body']) and await self._owner() is not None)
                break
            await asyncio.sleep(min(.1, max(0, deadline - time.monotonic())))
        self._emit_ack_diagnostic(accepted)
        return accepted

    async def candidate(self):
        await self.drain()
        if not self._current() or len(self.acks) != 1:
            return None
        ack = self.acks[0]
        eligible = [listing for listing in self.lists if listing['started_at'] >= ack['observed_at'] and listing['owner'] == ack['owner']]
        if not eligible:
            return None
        listing = max(eligible, key=lambda item: item['observed_at'])
        body, video = ack['body'], self._video()
        if not video:
            return None
        rows = [r for r in listing['rows'] if str(r.get('mid')) == ack['owner'] and
                str(r.get('cid')) == str(video['cid']) and r.get('title') == body['title'] and
                bili_cover_identity(r.get('cover')) == bili_cover_identity(body.get('cover')) and
                bili_cover_identity(r.get('cover43')) == bili_cover_identity(body.get('cover43'))]
        if len(rows) != 1:
            return None
        row = rows[0]
        if ack['path'].endswith('/update') and str(row['id']) != str(body['id']):
            return None
        return {'id': str(row['id']), 'owner': str(row['mid']), 'title': row['title'],
            'description': None, 'tags': None, 'caption': None, 'short_title': None,
            'video_ids': [str(row['cid'])], 'video_count': 1,
            'cover_ids': sorted(set(x for x in (bili_cover_identity(row.get('cover')), bili_cover_identity(row.get('cover43'))) if x)),
            'cover_slots': {'cover': [bili_cover_identity(row['cover'])] if bili_cover_identity(row.get('cover')) else [],
                            'cover43': [bili_cover_identity(row['cover43'])] if bili_cover_identity(row.get('cover43')) else []},
            'fresh': True, 'observed_at': listing['observed_at'], 'readback_source': 'normal_draft_list',
            'full_fields_source': None}

    async def pre_reopen_verified(self, candidate):
        current = await self.candidate()
        if current is None or not isinstance(candidate, dict) or current != candidate or await self._owner() is None:
            return False
        return bool(len(self.acks) == 1 and self._ack_matches(self.acks[0]['body']))

    async def reopened_record(self, candidate):
        await self.drain()
        if (not self._current() or not isinstance(candidate, dict) or
                not await self.pre_reopen_verified(candidate) or self.session.reopen_at is None):
            return None
        from uploader.draft_evidence import field_match
        video = self._video()
        reads = [r for r in self.views if r['id'] == candidate.get('id') and r['owner'] == candidate.get('owner') and
                 r['request_started_at'] >= self.session.reopen_at]
        if not reads:
            return None
        latest = max(reads, key=lambda item: item['observed_at'])
        if (not video or set(latest['video_ids']) != {str(video['cid']), video['filename']} or
                set(candidate.get('cover_ids', [])) != set(latest['cover_ids']) or
                candidate.get('cover_slots') != latest.get('cover_slots') or
                not field_match('bilibili', latest, self.session.expected)):
            return None
        return {**latest, 'fresh': True, 'readback_source': candidate['readback_source'],
                'saved_list_observed_at': candidate['observed_at'], 'save_ack_observed_at': self.acks[0]['observed_at']}

    async def drain(self):
        if not self._current():
            return
        try:
            if self.transport._canonical(self.transport.context_id):
                await self.transport.evaluate('window.__sauBiliEvidenceObserver.flush(' + json.dumps(self.nonce) + ')')
        except Exception:
            pass
        await self.transport.drain()

    async def diagnostic_counts(self):
        safe = {'video_records': int(self._video() is not None), 'cover_records': len([r for r in self.records.values() if r['kind'] == 'cover']),
                'ack_count': len(self.acks), 'list_count': len(self.lists), 'view_count': len(self.views)}
        return safe

    async def close(self):
        if self.closed:
            return
        try:
            if self.transport._canonical(self.transport.context_id):
                await self.transport.evaluate('window.__sauBiliEvidenceObserver.close(' + json.dumps(self.nonce) + ')')
        except Exception:
            pass
        self.closed = True
        await self.transport.close()


async def install_bili_observer(session):
    observer = BiliEvidenceObserver(session)
    session.bili_observer = observer
    await observer.initialize()
    return observer
