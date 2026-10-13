"""Read a video-channel session's exact CDN configuration before uploading.

Only existing Vue userStore.envInfo getters are read. No SDK, network request,
authentication field, page content, or mutable browser state is used.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from uploader.native_read import canonical_root_evaluate


_ORIGIN_HOST = "channels.weixin.qq.com"
_CDN_SUFFIXES = ("video.qq.com", "wxapp.tc.qq.com")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_FIELDS = ("cdnHost", "cdnHostList", "spareCdnHostList")


def _hostname(value: object) -> str | None:
    if not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 253:
        return None
    host = value.lower()
    if any(_LABEL.fullmatch(label) is None for label in host.split(".")):
        return None
    if not any(host == suffix or host.endswith("." + suffix) for suffix in _CDN_SUFFIXES):
        return None
    return host


def _realm(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    # The exact authority rejects credentials and ports without reading query.
    if parsed.scheme != "https" or parsed.netloc.lower() != _ORIGIN_HOST:
        return None
    if parsed.path.startswith("/platform/"):
        return "main"
    if parsed.path.startswith("/micro/content/"):
        return "owner"
    return None


_CAPTURE = r"""(realm) => {
 try {
  if (location.origin !== 'https://channels.weixin.qq.com') return null;
  const prefix = realm === 'main' ? '/platform/' : '/micro/content/';
  if (!location.pathname.startsWith(prefix)) return null;
  const normalize = (value) => {
   if (typeof value !== 'string' || !value.length || value.length > 253 ||
       !/^[\x00-\x7f]+$/.test(value)) throw new Error('hostname');
   const host = value.toLowerCase();
   if (!host.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
       !['video.qq.com','wxapp.tc.qq.com'].some(s => host === s || host.endsWith('.'+s)))
    throw new Error('hostname');
   return host;
  };
  const project = (env) => {
   const host = env.cdnHost;
   const primary = env.cdnHostList;
   const spare = env.spareCdnHostList;
   if (host != null && typeof host !== 'string') throw new Error('host');
   if (primary != null && !Array.isArray(primary)) throw new Error('primary');
   if (spare != null && !Array.isArray(spare)) throw new Error('spare');
   const list = value => {
    if(value && value.length > 128) throw new Error('list');
    return [...new Set((value || []).map(normalize))].sort();
   };
   const result = {cdnHost:host == null ? null : normalize(host),
    cdnHostList:list(primary),spareCdnHostList:list(spare)};
   if (!result.cdnHost && !result.cdnHostList.length && !result.spareCdnHostList.length)
    throw new Error('empty');
   return result;
  };
  const roots=[document], rootSeen=new Set(), vms=[], vmSeen=new Set(), envs=new Set();
  let nodes=0;
  while(roots.length) {
   const root=roots.shift(); if(rootSeen.has(root))continue; rootSeen.add(root);
   if(rootSeen.size > 32) return null;
   for(const el of root.querySelectorAll('*')) {
    if(++nodes > 12000) return null;
    if(el.shadowRoot)roots.push(el.shadowRoot);
    const vm=el.__vue__; if(vm && !vmSeen.has(vm)){vmSeen.add(vm);vms.push(vm);}
   }
  }
  const inspected=new Set();
  for(const first of vms) {
   let vm=first; const parents=new Set(); let depth=0;
   while(vm) {
    if(parents.has(vm)) return null;
    if(inspected.has(vm)) break;
    if(++depth > 32 || inspected.size >= 2048) return null;
    parents.add(vm); inspected.add(vm);
    const store=vm.userStore;
    if(store != null) {
     if(typeof store !== 'object') return null;
     const env=store.envInfo;
     if(env != null) {
      if(typeof env !== 'object' || Array.isArray(env)) return null;
      envs.add(env); if(envs.size > 1) return null;
     }
    }
    vm=vm.$parent;
   }
  }
  if(envs.size !== 1) return null;
  const env=envs.values().next().value, first=project(env), second=project(env);
  if(JSON.stringify(first) !== JSON.stringify(second)) return null;
  return {origin:location.origin,realm,envCount:1,fields:first};
 } catch { return null; }
}"""


# Keep the prior logical-DOM reader above for the bounded diagnostic comparison.
# Production reads the native root once, including its physically adopted Wujie
# shadow DOM and accessible official owner documents. An empty owner document
# cannot veto a unique store that is actually rendered in the root shadow tree.
_NATIVE_CAPTURE = r"""(diagnostic=false) => {
 const out={ok:false,phase:'scope',native_intrinsics:false,node_count:0,root_count:0,
  env_count:0,same_env:false,same_fields:false,host_type:'unread',primary_type:'unread',
  spare_type:'unread',primary_count:0,spare_count:0,nonempty:false};
 let phase='scope';
 const fail=()=>diagnostic?{...out,phase}:null;
 const type=v=>v==null?'null':Array.isArray(v)?'array':typeof v;
 try {
  const before=location.href;
  if(location.origin!=='https://channels.weixin.qq.com'||!location.pathname.startsWith('/platform/'))return fail();
  phase='intrinsics';
  const owner=Object.getOwnPropertyDescriptor(Node.prototype,'ownerDocument').get,
   rootNode=Node.prototype.getRootNode,
   shadow=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get,
   docQuery=Document.prototype.querySelectorAll,fragmentQuery=DocumentFragment.prototype.querySelectorAll,
   frameDocument=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentDocument').get;
  const native=fn=>typeof fn==='function'&&Function.prototype.toString.call(fn).includes('[native code]');
  if(![owner,rootNode,shadow,docQuery,fragmentQuery,frameDocument].every(native))return fail();
  out.native_intrinsics=true;
  const call=(fn,self,...args)=>Reflect.apply(fn,self,args);
  const scan=()=>{
   const queue=[document],roots=new Set(),vms=new Set(),envs=new Set(),frames=new Map();
   let nodes=0;
   while(queue.length){
    const root=queue.shift();if(roots.has(root))continue;roots.add(root);
    if(roots.size>32)throw Error('limit');
    for(const el of call(root.nodeType===9?docQuery:fragmentQuery,root,'*')){
     if(++nodes>12000)throw Error('limit');
     // Native queries and ancestry must agree; patched logical properties do
     // not select either the nodes or their document ownership.
     if(call(rootNode,el)!==root)throw Error('scope');
     const actualOwner=call(owner,el);
     if(actualOwner!==document&&!frames.has(actualOwner))throw Error('scope');
     const child=call(shadow,el);if(child)queue.push(child);
     if(el.tagName==='IFRAME'){
      const frame=call(frameDocument,el);
      if(frame&&frame.location.origin==='https://channels.weixin.qq.com'&&
         frame.location.pathname.startsWith('/micro/content/')){
       frames.set(frame,frame.location.href);queue.push(frame);
      }
     }
     if(el.__vue__)vms.add(el.__vue__);
    }
   }
   const inspected=new Set();
   for(const first of vms){
    const parents=new Set();let depth=0;
    for(let vm=first;vm;vm=vm.$parent){
     if(parents.has(vm))throw Error('cycle');
     if(inspected.has(vm))break;
     if(++depth>32||inspected.size>=2048)throw Error('limit');
     parents.add(vm);inspected.add(vm);
     const store=vm.userStore;
     if(store!=null){
      if(typeof store!=='object')throw Error('store');
      const env=store.envInfo;
      if(env!=null){
       if(typeof env!=='object'||Array.isArray(env))throw Error('env');
       envs.add(env);
      }
     }
    }
   }
   for(const [frame,url]of frames)if(frame.location.href!==url)throw Error('scope');
   return {envs,frames,roots,nodes};
  };
  phase='first_scan';const first=scan();
  out.node_count=first.nodes;out.root_count=first.roots.size;out.env_count=first.envs.size;
  if(first.envs.size!==1)return fail();
  const env=first.envs.values().next().value;
  const normalize=value=>{
   if(typeof value!=='string'||!value.length||value.length>253||!/^[\x00-\x7f]+$/.test(value))throw Error('hostname');
   const host=value.toLowerCase();
   if(!host.split('.').every(label=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))||
      !['video.qq.com','wxapp.tc.qq.com'].some(s=>host===s||host.endsWith('.'+s)))throw Error('hostname');
   return host;
  };
  const project=value=>{
   const host=value.cdnHost,primary=value.cdnHostList,spare=value.spareCdnHostList;
   out.host_type=type(host);out.primary_type=type(primary);out.spare_type=type(spare);
   out.primary_count=Array.isArray(primary)?primary.length:0;out.spare_count=Array.isArray(spare)?spare.length:0;
   if(host!=null&&typeof host!=='string'||primary!=null&&!Array.isArray(primary)||
      spare!=null&&!Array.isArray(spare))throw Error('field');
   const list=value=>{
    if(value&&value.length>128)throw Error('list');
    return [...new Set((value||[]).map(normalize))].sort();
   };
   const result={cdnHost:host==null?null:normalize(host),cdnHostList:list(primary),spareCdnHostList:list(spare)};
   out.nonempty=!!(result.cdnHost||result.cdnHostList.length||result.spareCdnHostList.length);
   if(!out.nonempty)throw Error('empty');return result;
  };
  phase='first_fields';const fields=project(env);
  phase='second_scan';const second=scan();
  out.same_env=second.envs.size===1&&second.envs.values().next().value===env;
  if(!out.same_env||second.roots.size!==first.roots.size||
     [...first.roots].some(root=>!second.roots.has(root))||second.frames.size!==first.frames.size||
     [...first.frames].some(([frame,url])=>second.frames.get(frame)!==url))return fail();
  phase='second_fields';out.same_fields=JSON.stringify(fields)===JSON.stringify(project(env));
  if(!out.same_fields||location.href!==before)return fail();
  out.ok=true;out.phase='complete';
  return diagnostic?out:{origin:location.origin,realm:'main',envCount:1,fields};
 }catch(error){return diagnostic?{...out,phase,error_type:
  ['Error','TypeError','SecurityError','InvalidStateError'].includes(error.name)?error.name:'OtherError'}:null;}
}"""


def _projection(value: object, realm: str) -> tuple[object, ...] | None:
    if not isinstance(value, dict) or value.get("origin") != f"https://{_ORIGIN_HOST}":
        return None
    if value.get("realm") != realm or type(value.get("envCount")) is not int or value["envCount"] != 1:
        return None
    fields = value.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(_FIELDS):
        return None
    host = fields["cdnHost"]
    if host is not None:
        host = _hostname(host)
        if host is None:
            return None
    lists = []
    for key in _FIELDS[1:]:
        values = fields[key]
        if not isinstance(values, list) or len(values) > 128:
            return None
        normalized = [_hostname(item) for item in values]
        if any(item is None for item in normalized):
            return None
        lists.append(tuple(sorted(set(normalized))))
    if host is None and not any(lists):
        return None
    return (host, *lists)


async def capture_tc_cdn_hosts(page) -> frozenset[str] | None:
    """Return a unique current official configuration, or fail closed with None."""
    try:
        page_before = page.url
        if _realm(page_before) != "main":
            return None
        main_before = page.main_frame
        if main_before is None or main_before.url != page_before:
            return None
        result = await canonical_root_evaluate(page, '(' + _NATIVE_CAPTURE + ')(' + json.dumps(False) + ')')
        found = _projection(result, 'main')
        if (found is None or page.url != page_before or page.main_frame is not main_before or
                main_before.url != page_before):
            return None
        host, primary, spare = found
        return frozenset((*primary, *spare, *((host,) if host else ())))
    except Exception:
        return None
