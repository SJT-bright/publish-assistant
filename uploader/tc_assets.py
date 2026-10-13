"""Correlate a reopened TC editor's visible media in one read-only DOM scan.

This creates no upload or save proof. The caller must already hold the same
operation's independently attested saved record. Only HTTPS server previews
are correlated through tc_resource_identity; Blob previews and Vue metadata
cannot substitute for a reopened server resource. No URL is logged or exposed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from urllib.parse import urlsplit

from uploader.tc_resource import tc_resource_identity
from uploader.native_read import canonical_root_evaluate


_IDENTITY = re.compile(r"tc-resource:[0-9a-f]{64}\Z")
_SLOTS = frozenset({"cover_portrait", "cover_landscape"})

_READ = r"""(scope=null) => {
 try {
  if(location.origin!=='https://channels.weixin.qq.com' || !location.pathname.startsWith('/platform/'))return null;
  // Wujie patches an adopted node's own ownerDocument/getRootNode and its
  // shadow HTML parentNode. Read physical ancestry with main-realm native
  // intrinsics, never with those logical owner projections.
  const parent=Object.getOwnPropertyDescriptor(Node.prototype,'parentNode').get,
        owner=Object.getOwnPropertyDescriptor(Node.prototype,'ownerDocument').get,
        connected=Object.getOwnPropertyDescriptor(Node.prototype,'isConnected').get,
        rootNode=Node.prototype.getRootNode,contains=Node.prototype.contains,
        rect=Element.prototype.getBoundingClientRect,
        docQuery=Document.prototype.querySelectorAll,fragmentQuery=DocumentFragment.prototype.querySelectorAll,
        frameDocument=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentDocument').get;
  const native=fn=>typeof fn==='function'&&Function.prototype.toString.call(fn).includes('[native code]');
  if(![parent,owner,connected,rootNode,contains,rect,docQuery,fragmentQuery,frameDocument].every(native))return null;
  const call=(fn,value,...args)=>Reflect.apply(fn,value,args),query=root=>call(root.nodeType===9?docQuery:fragmentQuery,root,'*');
  const roots=[document],seenRoots=new Set(),seenElements=new Set(),frames=new Map();
  const videos=[],images=new Set();let nodes=0,resourceCount=0;
  const keep=value=>{
   if(typeof value!=='string' || !value)return null;
   if(value.length>8192 || ++resourceCount>4096)throw Error('resource_limit');
   return value;
  };
  const physicalVisible=el=>{
   if(!call(connected,el))return false;
   const box=call(rect,el);if(box.width<=0 || box.height<=0)return false;
   const ancestors=new Set();let current=el;
   while(current){
    if(ancestors.has(current) || ancestors.size>=128)return false;ancestors.add(current);
    if(current.nodeType!==1)return false;
    const actualOwner=call(owner,current),style=actualOwner.defaultView.getComputedStyle(current);
    if(style.display==='none' || style.visibility==='hidden' || style.visibility==='collapse' ||
       Number(style.opacity)===0 || style.contentVisibility==='hidden')return false;
    const p=call(parent,current);
    if(p?.nodeType===1){current=p;continue;}
    const root=call(rootNode,current);
    if(root!==p)return false;
    if(root?.nodeType===11&&root.host){current=root.host;continue;}
    if(root===document)return true;
    if(root?.nodeType!==9)return false;
    const frame=frames.get(root);
    if(!frame)return false;
    const frameBox=call(rect,frame);
    if(frameBox.width<=0 || frameBox.height<=0)return false;
    current=frame;
   }
   return false;
  };
  while(roots.length){
   const root=roots.shift();if(seenRoots.has(root))continue;seenRoots.add(root);
   if(seenRoots.size>64)return null;
   for(const el of query(root)){
    if(++nodes>20000)return null;
    if(seenElements.has(el))continue;seenElements.add(el);
    if(el.shadowRoot)roots.push(el.shadowRoot);
    if(el.tagName==='IFRAME'){
     const frame=call(frameDocument,el);
     if(frame && frame.location.origin==='https://channels.weixin.qq.com' &&
        frame.location.pathname.startsWith('/micro/content/')){frames.set(frame,el);roots.push(frame);}
    }
   }
  }
  let binding;
  const projectedBinding=()=>{
   if(binding!==undefined)return binding;
   binding=null;
   if(!scope || typeof scope.id!=='string'||!scope.id)return null;
   const seenVM=new Set(),editors=new Set(),materials=new Set();
   for(const el of seenElements){
    const chain=new Set();let depth=0;
    for(let vm=el.__vue__;vm;vm=vm.$parent){
     if(chain.has(vm)||++depth>32)return null;chain.add(vm);if(seenVM.has(vm))break;
     seenVM.add(vm);if(seenVM.size>2048)return null;
     const component=vm.$refs?.postCreate;
     if(component&&!Array.isArray(component)&&component.$el&&physicalVisible(component.$el)&&
        vm.draftStore?.objectId===scope.id)editors.add(component);
     if(Array.isArray(vm.fileList)&&['handleFileListChange','handleSetFileList','handleDeleteFile','updateFile']
        .every(k=>typeof vm[k]==='function'))materials.add(vm);
    }
   }
   if(editors.size!==1||materials.size!==1)return null;
   const editor=editors.values().next().value,material=materials.values().next().value,
         files=material.fileList,media=material.postStore?.postObjDesc?.media;
   if(!material.$el || !call(contains,editor.$el,material.$el)||files.length!==1||
      files[0]?.isVideo!==true||files[0]?.isImage!==false||files[0]?.isValid!==true||files[0]?.status!=='done'||
      !Array.isArray(media)||media.length!==1||typeof media[0]?.url!=='string'||!media[0].url.startsWith('https://'))return null;
   binding={editor,material,url:media[0].url};return binding;
  };
  const visible=el=>{
   if(!physicalVisible(el))return false;
   const projected=el.ownerDocument!==call(owner,el)||el.getRootNode()!==call(rootNode,el);
   if(!projected)return true;
   const current=projectedBinding();
   return !!current && call(contains,current.editor.$el,el);
  };
  for(const el of seenElements){
   if(!visible(el))continue;
   if(el.tagName==='VIDEO'){
    const sources=[...new Set([el.currentSrc,el.src].map(keep).filter(Boolean))];
    const projected=el.ownerDocument!==call(owner,el)||el.getRootNode()!==call(rootNode,el);
    if(projected){const current=projectedBinding();
     if(!current||!call(contains,current.material.$el,el)||!sources.includes(current.url))return null;}
    const poster=keep(el.poster);
    videos.push({sources,poster});if(videos.length>1)return null;
   }
   if(el.tagName==='IMG'){
    for(const value of [el.currentSrc,el.src]){const url=keep(value);if(url)images.add(url);}
   }
   const background=call(owner,el).defaultView.getComputedStyle(el).backgroundImage;
   if(typeof background!=='string' || background.length>32768)return null;
   for(const match of background.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*))\s*\)/g)){
    const url=keep(match[1] ?? match[2] ?? match[3].trim());if(url)images.add(url);
   }
  }
  return {videos,images:[...images]};
 }catch(_){return null;}
}"""


def _main(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        value = urlsplit(url)
        return (value.scheme == "https" and value.netloc == "channels.weixin.qq.com"
                and value.path.startswith("/platform/") and not value.fragment)
    except ValueError:
        return False


def _ids(values: object, *, maximum: int = 32) -> frozenset[str] | None:
    if (type(values) is not list or len(values) > maximum or
            any(type(value) is not str or not _IDENTITY.fullmatch(value) for value in values) or
            len(set(values)) != len(values)):
        return None
    return frozenset(values)


def _https_identity(url: object) -> str | None:
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    return tc_resource_identity(url)


async def tc_reopened_assets_match(page, candidate) -> bool:
    """Require one matching server video and all requested visible covers.

    Candidate IDs are already canonical tc-resource hashes from the saved
    record. Custom portrait/landscape slots require exactly one ID each and
    visible image/background evidence for both. Only when both custom slots
    are empty may the automatic cover use the video's poster as evidence.
    The single real-world scan is bounded to 20k visits / 64 roots / 5 seconds.
    """
    try:
        if type(candidate) is not dict:
            return False
        video_ids = _ids(candidate.get("video_ids"), maximum=1)
        cover_ids = _ids(candidate.get("cover_ids", []))
        slots = candidate.get("cover_slots", {})
        if (not video_ids or cover_ids is None or type(slots) is not dict or set(slots) - _SLOTS or
                ("video_count" in candidate and
                 (type(candidate["video_count"]) is not int or candidate["video_count"] != 1))):
            return False
        slot_ids = {key: _ids(value, maximum=1) for key, value in slots.items()}
        if any(value is None for value in slot_ids.values()):
            return False
        custom = any(slot_ids.values())
        if custom and (set(slot_ids) != _SLOTS or any(not value for value in slot_ids.values())):
            return False
        if not custom and not cover_ids:
            return False
        # Detach all required hashes before awaiting; a mutable caller's
        # candidate must not silently change the record being checked.
        required_covers = frozenset().union(*slot_ids.values()) if custom else cover_ids
        before = page.url
        if not _main(before) or not _main(page.main_frame.url):
            return False
        session = getattr(page, '_sau_draft_evidence', None)
        scope, bound_record, bound_binding = None, None, None
        if session is not None:
            binding = getattr(session, 'normal_ui_binding', None)
            record_id, owner = candidate.get('id'), candidate.get('owner')
            if (getattr(session, 'platform', None) == 'tencent' and not getattr(session, 'closed', True) and
                    isinstance(record_id, str) and record_id and candidate.get('fresh') is True and
                    owner == getattr(session, 'account', {}).get('uid') and
                    binding == {'id': record_id, 'owner': owner, 'control': 'post_draft_item_edit'} and
                    any(item == candidate for item in getattr(session, 'saves', []))):
                scope = {'id': record_id}
                bound_record, bound_binding = copy.deepcopy(candidate), dict(binding)
        snapshot = await canonical_root_evaluate(page, '(' + _READ + ')(' + json.dumps(scope) + ')')
        if scope and (getattr(page, '_sau_draft_evidence', None) is not session or session.closed or
                session.platform != 'tencent' or session.account.get('uid') != bound_record['owner'] or
                getattr(session, 'normal_ui_binding', None) != bound_binding or
                not any(item == bound_record for item in session.saves)):
            return False
        if (page.url != before or not _main(page.main_frame.url) or type(snapshot) is not dict or
                set(snapshot) != {"videos", "images"} or type(snapshot["videos"]) is not list or
                len(snapshot["videos"]) != 1 or type(snapshot["images"]) is not list or
                len(snapshot["images"]) > 4096):
            return False
        video = snapshot["videos"][0]
        if (type(video) is not dict or set(video) != {"sources", "poster"} or
                type(video["sources"]) is not list or not 1 <= len(video["sources"]) <= 2 or
                any(type(url) is not str or not 0 < len(url) <= 8192 for url in video["sources"]) or
                video["poster"] is not None and
                (type(video["poster"]) is not str or not 0 < len(video["poster"]) <= 8192) or
                any(type(url) is not str or not 0 < len(url) <= 8192 for url in snapshot["images"])):
            return False
        source_ids = {_https_identity(url) for url in video["sources"]}
        if None in source_ids or source_ids != video_ids:
            return False
        preview_ids = {_https_identity(url) for url in snapshot["images"]}
        if not custom:
            preview_ids.add(_https_identity(video["poster"]))
        return required_covers <= preview_ids
    except Exception:
        return False
