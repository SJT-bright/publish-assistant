"""Observe the normal Kuaishou upload and a single official cover cropper.

Source contracts (2026-10-04, zero-based JS offsets):
ks_publish_page.js 490728/493792/495346: pre -> native localFileData -> SDK
upload -> notifyUploadFinish -> snapshotInfo.fileToken/fileId; status 3 is
ALL_UPLOAD (ks_video_core.js 810311). No business method is invoked here.
Official index-Dld_zsLa.js SHA256
3e342bc4738834c6848239a61810a49856ef09dab617114effb28425c54db865:
633827 generateImage, 645513 normal v4 cropped-Blob and v2 preview uploads.
The latter preview is already JPEG: it must NOT be mistaken for an input PNG.
Only an observed draw of the exact input bytes to this instance's raw canvas,
its normal generateImage result, the actual upload-body SHA, and the applied
coverKey can establish a transformed-cover link.
SDK uploader-D0umJtz_.js SHA256
41f77ac09b4932630fc50d18783c330465d7c97686543b33c94f7f2c3bbd5c33:
235739 resume may report existed=true; 236670 complete is optional. A mere
fragment/HTTP acknowledgement is never the application's finish result=1.
231063/233005: optional endpoints are filtered with Boolean before the SDK
adds https:// to each authority. Only exact empty strings are ignored here;
every remaining authority must still match the original host allowlist.
"""
from __future__ import annotations

import asyncio
import math
import copy
import hashlib
import ipaddress
import json
import re
import secrets
import time
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

PREFIX = '/rest/cp/works/'
PATHS = {
    PREFIX + 'v2/video/pc/upload/pre': 'pre',
    PREFIX + 'v2/video/pc/upload/finish': 'finish',
    PREFIX + 'v4/video/pc/cover/edit/cover/upload': 'cover',
    PREFIX + 'v2/video/pc/upload/cover/upload': 'preview',
    PREFIX + 'v2/video/pc/upload/cover/view': 'cover_view',
}
KEYS = frozenset(('uploadType', 'result', 'data', 'fileId', 'token', 'endPoints',
    'fileName', 'fileLength', 'fileType', 'coverKey', 'duration', 'width', 'height'))


def ks_endpoint(url):
    try:
        value = urlsplit(url)
        if (value.scheme == 'https' and value.netloc == 'cp.kuaishou.com' and
                not value.fragment and not value.username and not value.password):
            return PATHS.get(value.path)
    except (TypeError, ValueError):
        pass
    return None


def _scalar(value):
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and 0 < len(value) <= 2048 and value.strip() == value and all(ord(c) >= 32 for c in value):
        return value
    return None


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _token(value):
    value = _scalar(value)
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def _trusted_pre_candidates(endpoints):
    """Project known upload candidates; an unknown fallback is never a target.

    The official pre response can carry an unused additional DNS fallback. Its
    presence does not negate the same current native-file/token/finish chain.
    Keep the original suffix allowlist for eligible targets and require its
    primary; ordinary unknown fallback names are omitted from that allowlist.
    URLs, credentials, ports, whitespace, IP literals and malformed names still
    invalidate the entire shape. The list bound is local, not an SDK contract.
    """
    if not isinstance(endpoints, list) or not 1 <= len(endpoints) <= 128:
        return ()
    available = []
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    for value in endpoints:
        if value == '':
            continue
        if (not isinstance(value, str) or len(value) > 253
                or re.fullmatch(label + r'(?:\.' + label + r')+', value) is None):
            return ()
        try:
            ipaddress.ip_address(value)
            return ()
        except ValueError:
            pass
        if all(part.isdigit() for part in value.split('.')):
            return ()
        available.append(value)
    if not available or not available[0].endswith('.kuaishouzt.com'):
        return ()
    return tuple(dict.fromkeys(value for value in available
                              if value.endswith('.kuaishouzt.com')))


def _pre_endpoints_valid(endpoints):
    return bool(_trusted_pre_candidates(endpoints))


def _pre_gates(payload):
    """Describe rejected protocol shape without retaining any endpoint or token."""
    endpoints = payload.get('endPoints')
    values = endpoints if isinstance(endpoints, list) else []
    strings = [value for value in values if isinstance(value, str)]
    host = r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.kuaishouzt\.com'
    classes={key:0 for key in ('protocol_relative','whitespace','leading_whitespace','trailing_whitespace','uppercase',
        'ipv4','path','query','fragment','auth','port','invalid_port','parse_invalid','oversize',
        'domain_kuaishouzt','domain_kuaishou','domain_ksapisrv','domain_gifshow','domain_kwai','domain_other')}
    for value in strings[:128]:
        for key,hit in (('protocol_relative',value.startswith('//')),('whitespace',bool(re.search(r'\s',value))),
                        ('leading_whitespace',value!=value.lstrip()),('trailing_whitespace',value!=value.rstrip()),
                        ('uppercase',value!=value.lower())):
            classes[key]+=int(hit)
        if len(value)>8192:
            classes['oversize']+=1
            continue
        try:
            parsed=urlsplit(value if value.startswith(('https://','http://','//')) else '//'+value)
            hostname=(parsed.hostname or '').lower()
            classes['path']+=int(bool(parsed.path))
            classes['query']+=int(bool(parsed.query))
            classes['fragment']+=int(bool(parsed.fragment))
            classes['auth']+=int(parsed.username is not None or parsed.password is not None)
            try:classes['port']+=int(parsed.port is not None)
            except ValueError:classes['invalid_port']+=1
            try:
                ipaddress.IPv4Address(hostname)
                classes['ipv4']+=1
            except ipaddress.AddressValueError:
                pass
            domain=next((name for name in ('kuaishouzt','kuaishou','ksapisrv','gifshow','kwai')
                if hostname==name+'.com' or hostname.endswith('.'+name+'.com')),None)
            if hostname:classes['domain_'+(domain or 'other')]+=1
        except ValueError:
            classes['parse_invalid']+=1
    return {'file_id_valid': bool(_scalar(payload.get('fileId'))),
        'token_valid': bool(_token(payload.get('token'))),
        'endpoints_list': isinstance(endpoints, list),
        'endpoint_count': min(len(values), 1000000),
        'endpoint_strings': min(len(strings), 1000000),
        'endpoint_empty': sum(value=='' for value in strings),
        'endpoint_host_only': sum(bool(re.fullmatch(host, value)) for value in strings),
        'endpoint_host_port': sum(bool(re.fullmatch(host + r':[0-9]{1,5}', value)) for value in strings),
        'endpoint_https': sum(value.startswith('https://') for value in strings),
        'endpoint_http': sum(value.startswith('http://') for value in strings),
        'endpoint_objects': sum(isinstance(value, dict) for value in values),
        'endpoint_classified':min(len(strings),128),'endpoint_classification_truncated':len(strings)>128,
        **{'endpoint_'+key:count for key,count in classes.items()}}


def _body(request):
    try:
        value = request.post_data_json
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    try:
        return {k: v[0] for k, v in parse_qs(request.post_data or '').items() if len(v) == 1}
    except Exception:
        return {}


async def _cover_body_sha(request):
    """Only one real multipart file part. Never hash textual metadata instead."""
    try:
        headers = await request.all_headers()
        content_type = headers.get('content-type', '')
        body = request.post_data_buffer
        if not isinstance(body, bytes) or len(body) > 32 * 1024 ** 2 or not content_type.startswith('multipart/form-data'):
            return None
        message = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
        files = [part.get_payload(decode=True) for part in message.walk() if part.get_filename()]
        if len(files) == 1 and isinstance(files[0], bytes) and files[0]:
            return hashlib.sha256(files[0]).hexdigest()
    except Exception:
        pass
    return None


GENERATE_IMAGE_SOURCE = 'function I(){return new Promise((k,D)=>{var j;if(!v.current)return D();const L=(j=v.current)==null?void 0:j.toDataURL("image/jpeg",1);let T=new Image;T.crossOrigin="anonymous",T.src=L;const O=setTimeout(()=>{T.onload=T.onerror=null,D()},5e3);T.onload=()=>{clearTimeout(O);const N=document.createElement("canvas"),V=N.getContext("2d"),F=T.width>T.height?T.height/w.height:T.width/w.width;N.width=t.width*F,N.height=t.height*F,V.drawImage(T,f.x*F,f.y*F,t.width*F,t.height*F,0,0,t.width*F,t.height*F);const K=N.toDataURL("image/jpeg",1);N.toBlob(te=>{te?k({blob:te,url:K,mediaHeight:t.height*F,mediaWidth:t.width*F,rawInfo:{currentPreview:An(L,"cover.png"),height:t.height,width:t.width,x:f.x,y:f.y}}):D()},"image/jpeg",1)},T.onerror=()=>{clearTimeout(O),D()}})}'

CROP_GATE_NAMES = (
    'scan_calls', 'scan_unique', 'scan_nonunique', 'scan_error', 'attach_source_mismatch', 'attach_success',
    'bind_source_mismatch', 'refresh_source_mismatch', 'ref_replaced', 'source_changed', 'generate_bound',
    'generate_calls', 'generate_inactive', 'generate_receiver_mismatch', 'generate_draw_missing',
    'generate_revision_mismatch', 'generate_current_mismatch', 'generate_args', 'generate_nonpromise', 'generate_rejected',
    'draw_calls', 'draw_receiver_mismatch', 'draw_transform_mismatch', 'draw_projection_nonunique',
    'draw_canvas_mismatch', 'draw_ref_mismatch', 'draw_shape_mismatch', 'draw_source_mismatch',
    'draw_source_type_mismatch', 'draw_accepted', 'draw_error', 'preview_calls', 'preview_draw_missing',
    'preview_current_mismatch', 'preview_accepted', 'result_calls', 'result_inactive', 'result_preview_missing',
    'result_revision_mismatch', 'result_current_mismatch', 'result_ref_replaced_allowed', 'result_unmounted_allowed',
    'result_digest_invalid', 'result_preview_hash_mismatch',
    'result_dimensions_invalid', 'result_size_invalid', 'result_native_error', 'result_error',
    'source_hash_failed', 'ready_calls', 'ready_projection_nonunique', 'ready_candidate_missing',
    'ready_draw_missing', 'ready_revision_mismatch', 'ready_current_mismatch', 'ready_source_changed',
    'ready_origin_mismatch', 'ready_accepted', 'report_attempts', 'report_accepted', 'report_rejected')
CROP_STATE_COUNTS = ('crop_instances', 'retained_candidates', 'pending')
CROP_STATE_FLAGS = ('current_ref', 'draw_present', 'draw_revision_match', 'preview_present',
    'preview_revision_match', 'projection_failed')


def _safe_crop_diagnostics(value):
    """Only fixed gate names and bounded primitive counts may reach logs."""
    if not isinstance(value, dict):
        return {'gates': {}, 'state': {}}
    counts = value.get('gates') if isinstance(value.get('gates'), dict) else {}
    state = value.get('state') if isinstance(value.get('state'), dict) else {}
    return {'gates': {key: counts[key] for key in CROP_GATE_NAMES
            if type(counts.get(key)) is int and 0 < counts[key] <= 1000000},
        'state': {**{key: state[key] for key in CROP_STATE_COUNTS
                if type(state.get(key)) is int and 0 <= state[key] <= 1000000},
            **{key: state[key] for key in CROP_STATE_FLAGS if type(state.get(key)) is bool}}}

# Native SHA implementation is inserted below from the already-tested streamed
# digest. It uses 1 MiB chunks, yields between chunks, and guards this nonce.
DIGEST_JS = r'''  const K = new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
  const R=(x,n)=>(x>>>n)|(x<<(32-n));
  async function digest(file,size){
   const h=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]),w=new Uint32Array(64);
   function block(bytes,off){
    for(let i=0;i<16;i++){const p=off+i*4;w[i]=((bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3])>>>0;}
    for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(R(a,7)^R(a,18)^(a>>>3))+w[i-7]+(R(b,17)^R(b,19)^(b>>>10)))>>>0;}
    let[a,b,c,d,e,f,g,j]=h;for(let i=0;i<64;i++){const t1=(j+(R(e,6)^R(e,11)^R(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0,t2=((R(a,2)^R(a,13)^R(a,22))+((a&b)^(a&c)^(b&c)))>>>0;j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}[a,b,c,d,e,f,g,j].forEach((v,i)=>h[i]=(h[i]+v)>>>0);
   }
   let tail=new Uint8Array(0);
   for(let off=0;off<size;off+=1048576){
    if(window.__sauKSObserver!==state)throw Error('obsolete observer');
    const part=Reflect.apply(blobSlice,file,[off,off+1048576]),bytes=new Uint8Array(await Reflect.apply(blobArrayBuffer,part,[]));
    const merged=new Uint8Array(tail.length+bytes.length);merged.set(tail);merged.set(bytes,tail.length);
    const whole=merged.length-merged.length%64;for(let p=0;p<whole;p+=64)block(merged,p);tail=merged.slice(whole);await new Promise(r=>setTimeout(r,0));
   }
   const last=new Uint8Array(tail.length<56?64:128);last.set(tail);last[tail.length]=128;
   const view=new DataView(last.buffer);view.setUint32(last.length-8,Math.floor(size/536870912));view.setUint32(last.length-4,(size*8)>>>0);
   for(let p=0;p<last.length;p+=64)block(last,p);return Array.from(h,x=>x.toString(16).padStart(8,'0')).join('');
  }
'''

DISCOVER_JS = r"""
 const own=(o,k)=>{try{return Object.getOwnPropertyDescriptor(o,k)?.value;}catch(_){return undefined;}};
 function discover(){
  const queue=[document],roots=new Set(),rootFibers=new Set(),fibers=new Set();let count=0;
  while(queue.length){const root=queue.shift();if(roots.has(root))continue;roots.add(root);if(roots.size>64)throw Error('too many roots');
   for(const e of root.querySelectorAll('*')){
    if(++count>20000)throw Error('too many nodes');if(e.shadowRoot)queue.push(e.shadowRoot);
    if(e.tagName==='IFRAME'){try{const d=e.contentDocument;if(d)queue.push(d);}catch(_){}}
    for(const k of Object.keys(e)){if(!k.startsWith('__reactFiber$')&&!k.startsWith('__reactInternalInstance$'))continue;
     let f=own(e,k),n=0;while(f?.return&&n++<128)f=f.return;
     const current=own(own(f,'stateNode'),'current');if(current)rootFibers.add(current);
     else if(f)rootFibers.add(f);
    }
   }
  }
  const work=[...rootFibers];while(work.length){const f=work.pop();if(!f||fibers.has(f))continue;fibers.add(f);if(fibers.size>10000)throw Error('too many fibers');
   if(f.child)work.push(f.child);if(f.sibling)work.push(f.sibling);
  }
  return [...fibers];
 }
 function project(){
  const forms=new Set(),progress=new Set(),crops=[];
  const inspect=(o,depth=0)=>{if(!o||typeof o!=='object'||depth>2)return;
   if(Array.isArray(o)){if(o.length<64)for(const v of o)inspect(v,depth+1);return;}
   if(['fileId','coverKey','caption','pkCoverKey','profileCoverKey','fileName','fileSize'].every(k=>Object.hasOwn(o,k)))forms.add(o);
   if(['localFileData','percent','status'].every(k=>Object.hasOwn(o,k)))progress.add(o);
   for(const k of ['current','snapshotInfo','progress']){const v=own(o,k);if(v&&v!==o)inspect(v,depth+1);}
  };
  for(const f of discover()){
   let hook=f.memoizedState,n=0;const raw=[];
   while(hook&&n++<128){inspect(hook.memoizedState);inspect(hook.baseState);const v=own(hook.memoizedState,'current');
    try{if(v?.tagName==='CANVAS'&&String(v.className).includes('cutter-raw')&&v.isConnected)raw.push(v);}catch(_){}
    hook=hook.next;
   }
   const ref=own(f.ref,'current'),method=own(ref,'generateImage'),props=f.memoizedProps;
   if(raw.length===1&&props&&typeof props.currentPreview==='string'&&typeof method==='function')
    crops.push({fiber:f,ref,canvas:raw[0],method,props});
  }
  return {forms:[...forms],progress:[...progress],crops};
 }
"""

OBSERVER_JS = r"""cfg => {
 if(location.origin!=='https://cp.kuaishou.com')return false;
 const key=JSON.stringify(cfg.scope);let previous=window.__sauKSObserver;
 if(previous?.key===key)return true;previous?.close?.();
 const state={key,scope:cfg.scope,cache:new WeakMap(),pending:new Set(),patches:[],closed:false,candidates:new Map(),bound:new WeakSet(),generators:new WeakMap()};
 const names=__CROP_GATE_NAMES__,counts=Object.fromEntries(names.map(k=>[k,0]));
 const bump=k=>{if(Object.hasOwn(counts,k))counts[k]=Math.min(1000000,counts[k]+1);};
 window.__sauKSObserver=state;
 const active=()=>!state.closed&&window.__sauKSObserver===state;
 const nativeSize=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get,nativeName=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
 const blobSlice=Blob.prototype.slice,blobArrayBuffer=Blob.prototype.arrayBuffer;
 __DIGEST__
 __DISCOVER__
 const hash=async value=>{const size=Reflect.apply(nativeSize,value,[]);if(!Number.isSafeInteger(size)||size<=0||size>21474836480)throw Error('invalid native blob');
  let p=state.cache.get(value);if(!p){p=digest(value,size);state.cache.set(value,p);}return await p;};
 const nativeFetch=window.fetch;
 const bytes=async data=>{if(typeof data!=='string'||data.length>48*1024*1024)throw Error('invalid image source');
  if(data.startsWith('blob:')){if(new URL(data).origin!=='https://cp.kuaishou.com')throw Error('foreign blob source');
   const response=await Reflect.apply(nativeFetch,window,[data]);if(!response.ok)throw Error('unavailable blob source');
   const value=await response.blob();if(Reflect.apply(nativeSize,value,[])>32*1024*1024)throw Error('large cover source');return value;}
  if(!/^data:image\/(png|jpeg|webp);base64,/.test(data))throw Error('invalid image source');
  const b=atob(data.slice(data.indexOf(',')+1)),out=new Uint8Array(b.length);for(let i=0;i<b.length;i++)out[i]=b.charCodeAt(i);return new Blob([out]);};
 const sourceHash=async data=>{try{return await hash(await bytes(data));}catch(_){bump('source_hash_failed');return null;}};
 function patch(object,key,wrapper){const d=Object.getOwnPropertyDescriptor(object,key);const original=object[key];if(typeof original!=='function')return false;
  try{const installed=wrapper(original);Object.defineProperty(object,key,{configurable:true,writable:true,value:installed});state.patches.push(()=>{try{if(object[key]!==installed)return;if(d)Object.defineProperty(object,key,d);else delete object[key];}catch(_){}});return true;}catch(_){return false;}}
 const current=c=>{try{const candidates=project().crops;return candidates.length===1&&candidates[0].canvas===c.canvas&&candidates[0].ref===c.ref&&candidates[0].props.currentPreview===c.source;}catch(_){return false;}};
 // The official async generateImage call owns the exported pixels even when
 // React replaces its imperative ref or removes the dialog before our hashes
 // finish. Keep this invocation's immutable draw/preview rather than borrowing
 // a later instance. A changed original source/draw/revision or multiple live
 // croppers can never inherit it.
 const generationLive=g=>{try{
  const c=g.candidate;if(!active()||c.canvas!==g.canvas||c.source!==g.source||c.draw!==g.draw||c.revision!==g.revision||
    g.draw.revision!==g.revision||g.preview?.revision!==g.revision)return false;
  const found=project().crops;if(found.length>1)return false;
  if(found.length===0)return !g.canvas.isConnected;
  const next=found[0],attested=state.generators.get(next.ref)===next.method||Function.prototype.toString.call(next.method)===cfg.generate_source;
  return attested&&next.props.currentPreview===g.source&&(next.canvas===g.canvas||!g.canvas.isConnected);
 }catch(_){return false;}};
 const track=p=>{state.pending.add(p);p.finally(()=>state.pending.delete(p)).catch(()=>{});};
 function bindGenerator(c,ref){
  if(state.generators.get(ref)===ref.generateImage)return;
  if(Function.prototype.toString.call(ref.generateImage)!==cfg.generate_source){bump('bind_source_mismatch');return;}
  patch(ref,'generateImage',fn=>function(...args){
   bump('generate_calls');
   const generated_at=Date.now()/1000,draw=c.draw,revision=c.revision;
   const returned=Reflect.apply(fn,this,args);
   if(!active()){bump('generate_inactive');return returned;}
   if(this!==ref){bump('generate_receiver_mismatch');return returned;}
   if(!draw){bump('generate_draw_missing');return returned;}
   if(draw.revision!==revision){bump('generate_revision_mismatch');return returned;}
   if(!current(c)){bump('generate_current_mismatch');return returned;}
   if(args.length){bump('generate_args');return returned;}
   const generation=Object.freeze({candidate:c,canvas:c.canvas,ref,source:c.source,draw,revision,preview:c.preview});
   try{const then=Promise.prototype.then;
    const pending=Reflect.apply(then,returned,[async result=>{try{
     bump('result_calls');const preview=generation.preview;
     if(!active()){bump('result_inactive');return;}
     if(!preview){bump('result_preview_missing');return;}
     if(preview.revision!==revision||c.revision!==revision){bump('result_revision_mismatch');return;}
     if(!generationLive(generation)){bump('result_current_mismatch');return;}
     const cropped=result?.blob,raw=result?.rawInfo?.currentPreview;
     let cropSize;try{Reflect.apply(nativeName,raw,[]);cropSize=Reflect.apply(nativeSize,cropped,[]);}catch(_){bump('result_native_error');return;}
     const [original_sha256,preview_sha256,raw_sha256,cropped_sha256]=await Promise.all([draw.sha,preview.sha,hash(raw),hash(cropped)]);
     if(!active()){bump('result_inactive');return;}
     if(![original_sha256,preview_sha256,cropped_sha256].every(x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x))){bump('result_digest_invalid');return;}
     if(raw_sha256!==preview_sha256){bump('result_preview_hash_mismatch');return;}
     if(c.revision!==revision){bump('result_revision_mismatch');return;}
     if(!generationLive(generation)){bump('result_current_mismatch');return;}
     if(!Number.isFinite(result.mediaWidth)||result.mediaWidth<=0||!Number.isFinite(result.mediaHeight)||result.mediaHeight<=0){bump('result_dimensions_invalid');return;}
     if(cropSize>32*1024*1024){bump('result_size_invalid');return;}
     if(!generation.canvas.isConnected)bump('result_unmounted_allowed');
     else if(project().crops[0]?.ref!==generation.ref)bump('result_ref_replaced_allowed');
     bump('report_attempts');const accepted=await window[cfg.binding]({scope:cfg.scope,generated_at,original_sha256,cropped_sha256,preview_sha256,
       cropped_size:cropSize,width:result.mediaWidth,height:result.mediaHeight});
     bump(accepted===true?'report_accepted':'report_rejected');
    }catch(_){bump('result_error');}},()=>{bump('generate_rejected');}]);track(pending);
   }catch(_){bump('generate_nonpromise');}return returned;
  });
  state.generators.set(ref,ref.generateImage);
  bump('generate_bound');
 }
 function refresh(c,next){
  const method=next.ref.generateImage;
  if(state.generators.get(next.ref)!==method&&Function.prototype.toString.call(method)!==cfg.generate_source){bump('refresh_source_mismatch');return false;}
  if(c.ref!==next.ref)bump('ref_replaced');
  if(c.source!==next.props.currentPreview){bump('source_changed');c.revision++;c.draw=null;c.preview=null;c.source=next.props.currentPreview;}
  c.ref=next.ref;c.props=next.props;bindGenerator(c,next.ref);return true;
 }
 function attach(c){
  if(state.candidates.has(c.canvas)){refresh(state.candidates.get(c.canvas),c);return;}
  if(Function.prototype.toString.call(c.method)!==cfg.generate_source){bump('attach_source_mismatch');return;}
  c.source=c.props.currentPreview;c.revision=0;c.draw=null;c.preview=null;
  state.candidates.set(c.canvas,c);
  bump('attach_success');
  const ctx=c.canvas.getContext('2d');if(!ctx)return;
  const getSrc=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src').get;
  for(const k of ['fillRect','strokeRect','putImageData','fill','stroke','fillText','strokeText','rotate','scale','translate','transform','setTransform','resetTransform'])
   patch(ctx,k,fn=>function(...args){const result=Reflect.apply(fn,this,args);c.revision++;c.draw=null;return result;});
  patch(ctx,'clearRect',fn=>function(...args){try{const found=project().crops;if(found.length===1&&found[0].canvas===c.canvas)refresh(c,found[0]);}catch(_){}const result=Reflect.apply(fn,this,args);c.revision++;c.draw=null;return result;});
  patch(ctx,'drawImage',fn=>function(...args){const result=Reflect.apply(fn,this,args);bump('draw_calls');c.revision++;c.draw=null;
   try{const source=Reflect.apply(getSrc,args[0],[]),projection=project().crops;
    const matrix=ctx.getTransform(),identity=matrix.a===1&&matrix.b===0&&matrix.c===0&&matrix.d===1&&matrix.e===0&&matrix.f===0;
    if(this!==ctx){bump('draw_receiver_mismatch');return result;}
    if(!identity){bump('draw_transform_mismatch');return result;}
    if(projection.length!==1){bump('draw_projection_nonunique');return result;}
    if(projection[0].canvas!==c.canvas){bump('draw_canvas_mismatch');return result;}
    if(projection[0].ref!==c.ref){bump('draw_ref_mismatch');return result;}
    if(args.length!==5||args[1]!==0||args[2]!==0||args[3]!==c.canvas.width||args[4]!==c.canvas.height){bump('draw_shape_mismatch');return result;}
    if(source!==projection[0].props.currentPreview){bump('draw_source_mismatch');return result;}
    if(!(/^(data:image\/(png|jpeg|webp);base64,|blob:https:\/\/cp\.kuaishou\.com\/)/.test(source))){bump('draw_source_type_mismatch');return result;}
    c.source=source;c.draw={revision:c.revision,source,sha:sourceHash(source)};bump('draw_accepted');
   }catch(_){bump('draw_error');}return result;});
  patch(c.canvas,'toDataURL',fn=>function(...args){const result=Reflect.apply(fn,this,args);
   if(this===c.canvas&&args[0]==='image/jpeg'&&args[1]===1){bump('preview_calls');
    if(!c.draw||c.draw.revision!==c.revision)bump('preview_draw_missing');
    else if(!current(c))bump('preview_current_mismatch');
    else{c.preview={value:result,sha:sourceHash(result),revision:c.revision};bump('preview_accepted');}}
   return result;});
  bindGenerator(c,c.ref);
 }
 function scan(){if(!active())return;bump('scan_calls');try{
  const found=project().crops;if(found.length===1){bump('scan_unique');attach(found[0]);}else bump('scan_nonunique');
  const roots=[document],seen=new Set();while(roots.length){const root=roots.shift();if(seen.has(root))continue;seen.add(root);
   if(!state.bound.has(root)){state.bound.add(root);mutation.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['style','width','height','src']});}
   for(const e of root.querySelectorAll('*')){if(e.shadowRoot)roots.push(e.shadowRoot);if(e.tagName==='IFRAME'){try{if(e.contentDocument)roots.push(e.contentDocument);}catch(_){}}}
  }
 }catch(_){bump('scan_error');}}
 const mutation=new MutationObserver(scan);scan();
 state.readProgress=async()=>{if(!active())return null;
  const p=project();if(p.forms.length!==1||p.progress.length!==1)return null;
  const form=p.forms[0],progress=p.progress[0],file=progress.localFileData;
  if(progress.status!==2||typeof progress.percent!=='number'||!Number.isFinite(progress.percent)||progress.percent<0||progress.percent>100)return null;
  const name=Reflect.apply(nativeName,file,[]),size=Reflect.apply(nativeSize,file,[]),sha256=await hash(file);
  const next=project();if(!active()||next.forms.length!==1||next.progress.length!==1||next.forms[0]!==form||
      next.progress[0]!==progress||progress.localFileData!==file||name!==form.fileName||size<=0)return null;
  return {name,size,sha256,percent:progress.percent};
 };
 state.read=async()=>{await Promise.allSettled([...state.pending]);if(!active())return null;
  const p=project();if(p.forms.length!==1||p.progress.length!==1||p.progress[0].status!==3)return null;
  const form=p.forms[0],progress=p.progress[0],file=progress.localFileData;
  const name=Reflect.apply(nativeName,file,[]),size=Reflect.apply(nativeSize,file,[]);
  if(name!==form.fileName||typeof form.fileToken!=='string'||!form.fileToken||size<=0)return null;
  const token=form.fileToken,file_id=form.fileId;
  const sha256=await hash(file),token_sha256=await hash(new Blob([token]));
  const next=project();if(!active()||next.forms.length!==1||next.progress.length!==1||next.forms[0]!==form||next.progress[0]!==progress||
      progress.status!==3||progress.localFileData!==file||form.fileName!==name||form.fileToken!==token||form.fileId!==file_id)return null;
  return {name,size,sha256,file_id:form.fileId,token_sha256,cover_keys:[form.coverKey,form.pkCoverKey,form.profileCoverKey].filter(v=>typeof v==='string'&&v)};
 };
 state.coverReady=async sha=>{scan();bump('ready_calls');if(!active()||typeof sha!=='string'||!/^[a-f0-9]{64}$/.test(sha))return false;
  const p=project();if(p.crops.length!==1){bump('ready_projection_nonunique');return false;}
  const c=state.candidates.get(p.crops[0].canvas),draw=c?.draw;
  if(!c){bump('ready_candidate_missing');return false;}
  if(!draw){bump('ready_draw_missing');return false;}
  if(draw.revision!==c.revision){bump('ready_revision_mismatch');return false;}
  if(!current(c)){bump('ready_current_mismatch');return false;}
  const revision=c.revision,origin=await draw.sha;
  if(!active()||!current(c)||c.draw!==draw||c.revision!==revision||draw.revision!==revision){bump('ready_source_changed');return false;}
  if(origin!==sha){bump('ready_origin_mismatch');return false;}
  bump('ready_accepted');return true;
 };
 state.diagnostics=()=>{const gates=Object.fromEntries(names.filter(k=>counts[k]).map(k=>[k,counts[k]]));
  try{const p=project(),c=p.crops.length===1?state.candidates.get(p.crops[0].canvas):null;
   return {gates,state:{crop_instances:p.crops.length,retained_candidates:state.candidates.size,pending:state.pending.size,
    current_ref:!!c&&current(c),draw_present:!!c?.draw,draw_revision_match:!!c?.draw&&c.draw.revision===c.revision,
    preview_present:!!c?.preview,preview_revision_match:!!c?.preview&&c.preview.revision===c.revision}};
  }catch(_){return {gates,state:{projection_failed:true}};}};
 state.close=()=>{if(state.closed)return;state.closed=true;mutation.disconnect();for(const undo of state.patches.reverse())undo();state.candidates.clear();};
 return true;
}"""


class _KSMainTransport:
    """Private native CDP root-default-context channel, never Patchright source.

    The observer uses foreign/adopted native objects only through the actual
    root's current React instance. A child/isolated/stale execution context is
    never allowed to deliver a crop observation to Python.
    """
    def __init__(self, observer):
        self.observer, self.page = observer, observer.page
        self.client = None
        self.root_frame = self.context_id = self.bridge_id = self.channel_nonce = None
        self.contexts, self.tasks = {}, set()
        self.closed = False
        self.raw_name = '__sauKSTransport_' + observer.scope['observer_nonce']
        self.lock = asyncio.Lock()
        self.handlers = {
            'Runtime.executionContextCreated': self._created,
            'Runtime.executionContextDestroyed': self._destroyed,
            'Runtime.executionContextsCleared': self._cleared,
            'Runtime.bindingCalled': self._called,
            'Page.frameNavigated': self._frame_navigated,
        }

    def _created(self, event):
        context = event.get('context', {})
        if type(context.get('id')) is int:
            self.contexts[context['id']] = dict(context.get('auxData') or {})

    def _destroyed(self, event):
        ident = event.get('executionContextId')
        self.contexts.pop(ident, None)
        if ident == self.context_id:
            self.context_id = self.bridge_id = self.channel_nonce = None

    def _cleared(self, _event):
        self.contexts.clear()
        self.context_id = self.bridge_id = self.channel_nonce = None

    def _frame_navigated(self, event):
        frame = event.get('frame', {})
        if frame.get('id') and not frame.get('parentId'):
            self.root_frame = frame['id']

    def _canonical(self, ident):
        native = self.contexts.get(ident, {})
        candidates = [key for key, value in self.contexts.items()
            if value.get('isDefault') is True and value.get('frameId') == self.root_frame]
        return (not self.closed and ident == self.context_id and self.bridge_id is not None and
            native.get('isDefault') is True and native.get('frameId') == self.root_frame and candidates == [ident])

    async def prepare(self):
        async with self.lock:
            if self.closed or not self.observer._current():
                return False
            if self.client is None:
                self.client = await self.page.context.new_cdp_session(self.page)
                await self.client.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
                for event, handler in self.handlers.items():
                    self.client.on(event, handler)
                await self.client.send('Page.enable')
                tree = await self.client.send('Page.getFrameTree')
                self.root_frame = tree['frameTree']['frame']['id']
                await self.client.send('Runtime.enable')
            tree = await self.client.send('Page.getFrameTree')
            self.root_frame = tree['frameTree']['frame']['id']
            candidates = [key for key, value in self.contexts.items()
                if value.get('isDefault') is True and value.get('frameId') == self.root_frame]
            if len(candidates) != 1:
                return False
            ident = candidates[0]
            if self._canonical(ident):
                return True
            self.context_id = self.bridge_id = self.channel_nonce = None
            channel = secrets.token_hex(24)
            await self.client.send('Runtime.addBinding', {'name': self.raw_name, 'executionContextId': ident})
            expression = r"""(() => {
                if(window!==top)return null;
                const raw=window[RAW], publicName=PUBLIC, nonce=NONCE, channel=CHANNEL,
                      descriptor=Object.getOwnPropertyDescriptor(window,publicName), pending=new Map();
                if(typeof raw!=='function')return null;
                let closed=false, sequence=0;
                const send=report=>{
                    if(closed)return Promise.resolve(false);
                    return new Promise(resolve=>{
                        const key=String(++sequence);pending.set(key,resolve);
                        try{raw(JSON.stringify({v:1,kind:'crop',sequence:key,observer_nonce:nonce,channel_nonce:channel,report}));}
                        catch(_){pending.delete(key);resolve(false);}
                    });
                };
                Object.defineProperty(window,publicName,{configurable:true,writable:true,value:send});
                return {
                    deliver(key,accepted){const resolve=pending.get(key);if(!resolve)return false;
                        pending.delete(key);resolve(accepted===true);return true;},
                    close(){closed=true;for(const resolve of pending.values())resolve(false);pending.clear();
                        if(window[publicName]===send){if(descriptor)Object.defineProperty(window,publicName,descriptor);else delete window[publicName];}
                        if(window[RAW]===raw)delete window[RAW];}
                };
            })()""".replace('RAW', json.dumps(self.raw_name)).replace('PUBLIC', json.dumps(self.observer.binding)).replace('NONCE', json.dumps(self.observer.scope['observer_nonce'])).replace('CHANNEL', json.dumps(channel))
            result = await self.client.send('Runtime.evaluate', {'expression': expression,
                'contextId': ident, 'returnByValue': False})
            if result.get('exceptionDetails') or result.get('result', {}).get('type') != 'object':
                return False
            self.context_id = ident
            self.bridge_id = result.get('result', {}).get('objectId')
            self.channel_nonce = channel
            return self._canonical(ident)

    async def evaluate(self, expression):
        if not self._canonical(self.context_id):
            raise RuntimeError('ks_observer_main_context_unavailable')
        ident = self.context_id
        result = await self.client.send('Runtime.evaluate', {'expression': expression,
            'contextId': ident, 'awaitPromise': True, 'returnByValue': True})
        if result.get('exceptionDetails') or not self._canonical(ident):
            raise RuntimeError('ks_observer_main_context_changed')
        return result.get('result', {}).get('value')

    def _called(self, event):
        if event.get('name') != self.raw_name:
            return
        task = asyncio.create_task(self._dispatch(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _dispatch(self, event):
        ident = event.get('executionContextId')
        if not self._canonical(ident) or event.get('name') != self.raw_name:
            return False
        payload = event.get('payload')
        if not isinstance(payload, str) or len(payload) > 32768:
            return False
        try:
            message = json.loads(payload)
        except (ValueError, TypeError):
            return False
        if (not isinstance(message, dict) or set(message) != {'v', 'kind', 'sequence', 'observer_nonce', 'channel_nonce', 'report'} or
            type(message['v']) is not int or message['v'] != 1 or message['kind'] != 'crop' or
            message['observer_nonce'] != self.observer.scope['observer_nonce'] or
            message['channel_nonce'] != self.channel_nonce or not isinstance(message['sequence'], str) or
            not re.fullmatch(r'[1-9][0-9]{0,15}', message['sequence'])):
            return False
        # This synthesized source is used only after native root identity is
        # established. It does not consume Page.expose_binding's frame mapping.
        accepted = self.observer.accept({'page': self.page, 'frame': self.page.main_frame}, message['report'])
        if not self._canonical(ident):
            return False
        try:
            await self.client.send('Runtime.callFunctionOn', {'objectId': self.bridge_id,
                'functionDeclaration': 'function(key,accepted){return this.deliver(key,accepted);}',
                'arguments': [{'value': message['sequence']}, {'value': accepted is True}], 'returnByValue': True})
        except Exception:
            return False
        return accepted is True

    async def close(self):
        if self.closed:
            return
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
            if self.bridge_id:
                await self.client.send('Runtime.callFunctionOn', {'objectId': self.bridge_id,
                    'functionDeclaration': 'function(){this.close();}', 'returnByValue': True})
                await self.client.send('Runtime.releaseObject', {'objectId': self.bridge_id})
            await self.client.send('Runtime.removeBinding', {'name': self.raw_name})
        except Exception:
            pass
        finally:
            try:
                await self.client.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
                await self.client.detach()
            except Exception:
                pass


class KSEvidenceObserver:
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.started_at = time.time()
        self.scope = {k: session.expected.get(k) for k in ('job_id', 'receipt_nonce')}
        self.scope['observer_nonce'] = secrets.token_hex(16)
        self.account = copy.deepcopy(session.account)
        self.binding = '__sauKSCrop_' + secrets.token_hex(16)
        self.records, self.crops, self.native = [], [], None
        self.attested = []
        self.closed, self.tasks = False, set()
        self.script = OBSERVER_JS.replace('__DIGEST__', DIGEST_JS).replace('__DISCOVER__', DISCOVER_JS).replace(
            '__CROP_GATE_NAMES__', json.dumps(CROP_GATE_NAMES))
        self.config = {'scope': self.scope, 'binding': self.binding, 'generate_source': GENERATE_IMAGE_SOURCE}
        self._last_summary = None
        self.crop_diagnostics = _safe_crop_diagnostics(None)
        self.transport = _KSMainTransport(self)

    def _current(self):
        wanted = self.session.expected.get('account', {})
        return (not self.closed and not self.session.closed and self.session.platform == 'kuaishou' and
            getattr(self.page, '_sau_draft_evidence', None) is self.session and self.account == self.session.account and
            self.account.get('verified') is True and isinstance(wanted, dict) and
            bool(wanted.get('uid')) and self.account.get('uid') == wanted.get('uid') and
            bool(wanted.get('identity')) and self.account.get('identity') == wanted.get('identity') and
            all(isinstance(self.scope[k], str) and self.scope[k] and self.scope[k] == self.session.expected.get(k)
                for k in ('job_id', 'receipt_nonce')))

    def accept(self, source, value):
        """Synchronous validator; native transport owns the caller realm proof."""
        if not self._current() or not isinstance(value, dict) or value.get('scope') != self.scope:
            return False
        if source.get('page') is not self.page or source.get('frame') is not self.page.main_frame:
            return False
        if (not all(_digest(value.get(k)) for k in ('original_sha256', 'cropped_sha256', 'preview_sha256')) or
            type(value.get('generated_at')) not in (int, float) or not self.started_at <= value['generated_at'] <= time.time() + 1 or
            type(value.get('cropped_size')) is not int or not 0 < value['cropped_size'] <= 32 * 1024 ** 2):
            return False
        if len(self.crops) >= 64:
            self.closed = True
            return False
        self.crops.append({k: value[k] for k in ('original_sha256', 'cropped_sha256', 'preview_sha256', 'generated_at', 'cropped_size')})
        return True

    async def initialize(self):
        self.page.on('framenavigated', self._navigated)
        await self._install()

    async def _install(self):
        if not self._current():
            return False
        try:
            if not await asyncio.wait_for(self.transport.prepare(), 8):
                return False
            return await asyncio.wait_for(self.transport.evaluate(
                '(' + self.script + ')(' + json.dumps(self.config) + ')'), 8) is True
        except Exception:
            return False

    def _navigated(self, frame):
        if not self._current() or frame.parent_frame is not None:
            return
        task = asyncio.create_task(self._install())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def ensure_ready(self):
        """Inspect/attach existing objects before native input; start no upload."""
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if not await self._install() or not self._current():
            return False
        try:
            key = json.dumps(json.dumps(self.scope, separators=(',', ':')))
            return await asyncio.wait_for(self.transport.evaluate(
                '(() => {const s=window.__sauKSObserver;return !!s&&s.key===' + key + '&&!s.closed;})()'), 5) is True
        except Exception:
            return False

    def _cover_selections(self):
        names = {asset.get('name') for asset in self.session.assets.values() if asset.get('kind') == 'cover'}
        return [value for value in self.session.files
            if str(value.get('type', '')).startswith('image/') or value.get('name') in names]

    async def ensure_cover_ready(self, timeout=10):
        """Wait for this selection's actual original-byte draw; never crop it."""
        deadline = time.monotonic() + min(max(float(timeout), 0), 30)
        while self._current() and time.monotonic() < deadline:
            selections = self._cover_selections()
            if len(selections) > 1:
                return False
            if len(selections) == 1:
                value = selections[0]
                selected = tuple(value.get(k) for k in ('name', 'size', 'sha256', 'selected_at'))
                valid = (type(value.get('selected_at')) in (int, float) and
                    self.session.started_at <= value['selected_at'] <= time.time() + 1 and _digest(value.get('sha256')) and
                    any(asset.get('kind') == 'cover' and
                        (asset.get('name'), asset.get('size'), asset.get('sha256')) == selected[:3]
                        for asset in self.session.assets.values()))
                if not valid:
                    return False
                try:
                    remaining = max(.01, deadline - time.monotonic())
                    key = json.dumps(json.dumps(self.scope, separators=(',', ':')))
                    ready = await asyncio.wait_for(self.transport.evaluate(
                        '(async()=>{const s=window.__sauKSObserver;return s?.key===' + key +
                        '&&!s.closed?await s.coverReady(' + json.dumps(selected[2]) + '):false;})()'), min(3, remaining))
                    current = self._cover_selections()
                    if (ready is True and self._current() and len(current) == 1 and
                        tuple(current[0].get(k) for k in ('name', 'size', 'sha256', 'selected_at')) == selected):
                        return True
                except Exception:
                    pass
            await asyncio.sleep(min(.1, max(0, deadline - time.monotonic())))
        return False

    async def observe(self, response, data, observed_at):
        operation = ks_endpoint(response.url)
        if not operation or not self._current():
            return
        if operation == 'cover_view':
            # Observe only the normal page's post-reopen GET response. No
            # platform call is issued here; a Blob URL later inherits its
            # coverKey only by matching these exact response bytes.
            try:
                request = response.request
                parsed = urlsplit(response.url)
                query = parse_qs(parsed.query, keep_blank_values=True)
                keys = query.get('coverKey', [])
                request_at = request.timing.get('startTime') / 1000
                reopen_at = getattr(self.session, 'reopen_at', None)
                valid = (request.method == 'GET' and request.frame is self.page.main_frame and
                         type(reopen_at) in (int, float) and self.started_at <= request_at <= observed_at and
                         reopen_at <= request_at and len(keys) == 1 and bool(_scalar(keys[0])) and
                         200 <= response.status < 300)
                content = await response.body() if valid else None
                valid = bool(valid and isinstance(content, bytes) and 0 < len(content) <= 32 * 1024 ** 2)
                record = {'operation': operation, 'request_at': request_at, 'observed_at': observed_at,
                          'valid': valid, 'cover_key': keys[0] if len(keys) == 1 else None,
                          'body_sha256': hashlib.sha256(content).hexdigest() if valid else None}
                if self._current() and len(self.records) < 512:
                    self.records.append(record)
            except Exception:
                pass
            return
        body, reply = _body(response.request), data if isinstance(data, dict) else {}
        payload = reply.get('data') if isinstance(reply.get('data'), dict) else {}
        try:
            request_at = response.request.timing.get('startTime') / 1000
        except Exception:
            request_at = -1
        timed = self.started_at <= request_at <= observed_at and self.session.started_at <= request_at
        success = (timed and response.request.method == 'POST' and 200 <= response.status < 300 and
                   type(reply.get('result')) is int and reply['result'] == 1)
        record = {'operation': operation, 'request_at': request_at, 'observed_at': observed_at, 'valid': False}
        if operation == 'pre':
            endpoint_list = payload.get('endPoints')
            candidates = _trusted_pre_candidates(endpoint_list)
            record.update(file_id=_scalar(payload.get('fileId')), token_sha256=_token(payload.get('token')),
                trusted_endpoint_count=len(candidates),
                valid=bool(success and _scalar(payload.get('fileId')) and _token(payload.get('token')) and candidates))
        elif operation == 'finish':
            length = body.get('fileLength')
            if isinstance(length, str) and re.fullmatch(r'[1-9][0-9]*', length):
                length = int(length)
            record.update(file_id=_scalar(payload.get('fileId')), token_sha256=_token(body.get('token')),
                name=body.get('fileName'), size=length, file_type=body.get('fileType'),
                valid=bool(success and _scalar(payload.get('fileId')) and _token(body.get('token')) and
                    isinstance(body.get('fileName'), str) and type(length) is int and 0 < length <= 12 * 1024 ** 3 and
                    isinstance(body.get('fileType'), str) and body['fileType'].startswith('video/')))
        else:
            body_sha = await _cover_body_sha(response.request) if success else None
            record.update(cover_key=_scalar(payload.get('coverKey')), body_sha256=body_sha,
                valid=bool(success and _scalar(payload.get('coverKey')) and body_sha))
        if len(self.records) < 512:
            self.records.append(record)
        else:
            self.closed = True
        try:
            diagnostic = {'event': 'kuaishou_upload_protocol', 'operation': operation,
                'http_status': response.status, 'request_keys': sorted(KEYS.intersection(body)),
                'response_keys': sorted(KEYS.intersection(reply)), 'data_keys': sorted(KEYS.intersection(payload)),
                'result_code': reply.get('result') if type(reply.get('result')) is int else None,
                'request_timing_valid': timed, 'parsed': record['valid']}
            if operation == 'pre':
                diagnostic['pre_gates'] = _pre_gates(payload)
                diagnostic['trusted_endpoint_count'] = len(candidates)
            print('DRAFT_DIAGNOSTIC=' + json.dumps(diagnostic, sort_keys=True), flush=True)
        except Exception:
            pass

    async def upload_progress(self):
        """Report this native File's SDK percent; it never proves completion."""
        if not self._current():
            return None
        try:
            key=json.dumps(json.dumps(self.scope,separators=(',',':')))
            value=await asyncio.wait_for(self.transport.evaluate(
                '(async()=>{const s=window.__sauKSObserver;return s?.key==='+key+'&&!s.closed?await s.readProgress():null;})()'),45)
            asset=self.session.assets.get('video',{})
            if (not self._current() or not isinstance(value,dict) or
                    any(value.get(k)!=asset.get(k) for k in ('name','size','sha256')) or
                    value.get('sha256')!=self.session.expected.get('video_sha256') or
                    type(value.get('percent')) not in (int,float) or not math.isfinite(value['percent']) or
                    not 0<=value['percent']<=100):
                return None
            return value['percent']
        except Exception:
            return None

    async def drain(self):
        if not self._current():
            self.native = None
            return
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        try:
            key = json.dumps(json.dumps(self.scope, separators=(',', ':')))
            value = await asyncio.wait_for(self.transport.evaluate(
                '(async() => {const s=window.__sauKSObserver;if(s?.key!==' + key + '||s.closed)return null;'
                'const native=await s.read();return {native,diagnostics:s.diagnostics()};})()'), 45)
        except Exception:
            value = None
            try:
                diagnostic = await asyncio.wait_for(self.transport.evaluate(
                    '(() => {const s=window.__sauKSObserver;return s?.key===' + key + '?s.diagnostics():null;})()'), 3)
                self.crop_diagnostics = _safe_crop_diagnostics(diagnostic)
            except Exception:
                pass
        native = value.get('native') if isinstance(value, dict) else None
        self.native = native if self._current() and isinstance(native, dict) else None
        if self._current() and isinstance(value, dict):
            self.crop_diagnostics = _safe_crop_diagnostics(value.get('diagnostics'))
        summary = {'event': 'kuaishou_evidence_state', 'allocations': sum(x['operation'] == 'pre' for x in self.records),
            'finishes': sum(x['operation'] == 'finish' for x in self.records), 'crop_proofs': len(self.crops),
            'cover_uploads': sum(x['operation'] == 'cover' and x['valid'] for x in self.records),
            'native_valid': self.native is not None, 'video_sha_match': bool(self.native and self.native.get('sha256') == self.session.expected.get('video_sha256')),
            'video_links': sum(x['kind'] == 'video' for x in self.upload_links()), 'cover_links': sum(x['kind'] == 'cover' for x in self.upload_links()),
            'crop_gates': self.crop_diagnostics['gates'], 'crop_state': self.crop_diagnostics['state']}
        if summary != self._last_summary:
            self._last_summary = summary
            try:
                print('DRAFT_DIAGNOSTIC=' + json.dumps(summary, sort_keys=True), flush=True)
            except Exception:
                pass

    def upload_links(self):
        if not self._current():
            return []
        cached = []
        for proof in self.attested:
            selections = [x for x in self.session.files if tuple(x.get(k) for k in ('name', 'size', 'sha256', 'selected_at')) == proof['selection']]
            relevant = [x for x in self.session.files if x.get('sha256') == proof['selection'][2] and x.get('name') == proof['selection'][0]]
            link = proof['link']
            if len(selections) != 1 or len(relevant) != 1:
                continue
            if link['kind'] == 'video':
                videos = [x for x in self.session.files if str(x.get('type', '')).startswith('video/') or x.get('name') == self.session.assets.get('video', {}).get('name')]
                allocations = [x for x in self.records if x['operation'] == 'pre']
                finishes = [x for x in self.records if x['operation'] == 'finish']
                if len(videos) != 1 or len(allocations) != 1 or len(finishes) != 1 or not allocations[0]['valid'] or not finishes[0]['valid']:
                    continue
                if self.native and ((self.native.get('name'), self.native.get('size'), self.native.get('sha256')) != proof['selection'][:3] or
                    _scalar(self.native.get('file_id')) not in link['ids'] or self.native.get('token_sha256') != allocations[0]['token_sha256']):
                    continue
            elif self.native and not set(link['ids']).issubset(self.native.get('cover_keys', [])):
                continue
            cached.append(copy.deepcopy(link))
        if not self.native:
            return cached
        native, asset = self.native, self.session.assets.get('video', {})
        sha = self.session.expected.get('video_sha256')
        selected = [x for x in self.session.files if x.get('name') == asset.get('name') or str(x.get('type', '')).startswith('video/')]
        pre = [x for x in self.records if x['operation'] == 'pre']
        finish = [x for x in self.records if x['operation'] == 'finish']
        links = []
        if (len(selected) == 1 and len(pre) == 1 and pre[0]['valid'] and len(finish) == 1 and finish[0]['valid'] and
            _digest(sha) and native.get('sha256') == asset.get('sha256') == selected[0].get('sha256') == sha and
            (native.get('name'), native.get('size')) == (asset.get('name'), asset.get('size')) ==
            (finish[0].get('name'), finish[0].get('size')) == (selected[0].get('name'), selected[0].get('size')) and
            _scalar(native.get('file_id')) == pre[0].get('file_id') == finish[0].get('file_id') and
            native.get('token_sha256') == pre[0].get('token_sha256') == finish[0].get('token_sha256') and
            type(selected[0].get('selected_at')) in (int, float) and
            self.session.started_at <= selected[0]['selected_at'] <= pre[0]['request_at'] <= pre[0]['observed_at'] <= finish[0]['request_at']):
            links.append({'kind': 'video', 'sha256': sha, 'ids': [pre[0]['file_id']], 'complete': True})
        for upload in (x for x in self.records if x['operation'] == 'cover' and x['valid']):
            if upload['cover_key'] not in native.get('cover_keys', []):
                continue
            proofs = [x for x in self.crops if x['cropped_sha256'] == upload['body_sha256'] and x['generated_at'] <= upload['request_at']]
            origins = {x['original_sha256'] for x in proofs}
            if len(origins) == 1:
                origin = origins.pop()
            elif not proofs:
                origin = upload['body_sha256']  # Direct original-file upload only.
            else:
                continue
            matching = [x for x in self.session.files if x.get('sha256') == origin and
                type(x.get('selected_at')) in (int, float) and self.session.started_at <= x['selected_at'] <= upload['request_at'] and
                any(a.get('kind') == 'cover' and (a.get('name'), a.get('size'), a.get('sha256')) ==
                    (x.get('name'), x.get('size'), origin) for a in self.session.assets.values())]
            if len(matching) != 1 or proofs and any(matching[0]['selected_at'] > p['generated_at'] for p in proofs):
                continue
            if any(x['operation'] == 'cover' and x['cover_key'] == upload['cover_key'] and not x['valid'] for x in self.records):
                continue
            link = {'kind': 'cover', 'sha256': origin, 'ids': [upload['cover_key']], 'complete': True}
            if link not in links:
                links.append(link)
        for link in links:
            matches = [x for x in self.session.files if x.get('sha256') == link['sha256'] and
                any(a.get('kind') == link['kind'] and (a.get('name'), a.get('size'), a.get('sha256')) ==
                    (x.get('name'), x.get('size'), x.get('sha256')) for a in self.session.assets.values())]
            if len(matches) == 1:
                proof = {'link': copy.deepcopy(link), 'selection': tuple(matches[0].get(k) for k in ('name', 'size', 'sha256', 'selected_at'))}
                if proof not in self.attested:
                    self.attested.append(proof)
        return links + [x for x in cached if x not in links]

    async def close(self):
        self.closed, self.native = True, None
        self.page.remove_listener('framenavigated', self._navigated)
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        try:
            key = json.dumps(json.dumps(self.scope, separators=(',', ':')))
            await asyncio.wait_for(self.transport.evaluate(
                '(() => {const s=window.__sauKSObserver;if(s?.key===' + key + ')s.close();})()'), 3)
        except Exception:
            pass
        await self.transport.close()


async def install_ks_observer(session):
    observer = KSEvidenceObserver(session)
    session.ks_observer = observer
    await observer.initialize()
    return observer
