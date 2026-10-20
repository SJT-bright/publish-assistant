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
   let restoreVideo=null;
   if(scope.restore){
    const expected=scope.restore,file=files[0],ext=file.extInfo,response=file.response,m=media[0],upload=material.$refs?.postUpload;
    if(!upload||!Array.isArray(upload.value)||upload.value.length!==1||upload.value[0]!==file||
       !['handleCropVideo','updateVideo','updateFile','setFileList'].every(k=>typeof upload[k]==='function'))return null;
    const size=v=>typeof v==='number'?Number.isSafeInteger(v)&&v>0&&v<=21474836480?v:null:
     typeof v==='string'&&/^[1-9][0-9]{0,10}$/.test(v)&&Number(v)<=21474836480?Number(v):null;
    if(!ext||!response||file.uuid!==expected.uuid||size(file.size)!==expected.metadata.fileSize||
       file.url!==expected.url||file.httpsUrl!==expected.url||ext.localFileUrl!==expected.url||response.httpsUrl!==expected.url||
       ext.width!==expected.metadata.width||ext.height!==expected.metadata.height||ext.duration!==expected.metadata.duration||
       m.url!==expected.url||m.md5sum!==expected.uuid||m.urlCdnTaskId!==expected.task||size(m.fileSize)!==expected.metadata.fileSize||
       m.width!==expected.metadata.width||m.height!==expected.metadata.height||m.videoPlayLen!==expected.metadata.duration)return null;
    // Inspect already assigned props, never invoke mediaList/getVideoElement.
    const consumers=[...seenVM].filter(vm=>Array.isArray(vm._props?.mediaList)&&vm._props.mediaList.length===1&&vm._props.mediaList[0]===file);
    const full=consumers.filter(vm=>vm.$refs?.video?.tagName==='VIDEO'&&physicalVisible(vm.$refs.video)&&
       vm.$el&&call(contains,material.$el,vm.$el)&&call(contains,vm.$el,vm.$refs.video));
    if(consumers.length!==2||full.length!==1)return null;
    const parent=consumers.filter(vm=>vm!==full[0]&&vm.$refs?.fullScreen===full[0]&&vm.$el&&
       call(contains,material.$el,vm.$el)&&call(contains,vm.$el,full[0].$el));
    if(parent.length!==1)return null;
    restoreVideo=full[0].$refs.video;
    if(restoreVideo.currentSrc!==expected.url||restoreVideo.src!==expected.url||restoreVideo.getAttribute('src')!==expected.url||
       restoreVideo.readyState<1||restoreVideo.videoWidth!==expected.metadata.width||
       restoreVideo.videoHeight!==expected.metadata.height)return null;
    if(scope.coverRoles){
     const roles=scope.coverRoles;
     const covers=[...seenVM].filter(vm=>vm._props?.video===file&&vm.$el&&
      vm.$el.matches('.cover-preview-wrap')&&physicalVisible(vm.$el)&&call(contains,material.$el,vm.$el)&&
      ['handleUpdateCover','handleClickEdit'].every(k=>typeof vm[k]==='function'));
     if(covers.length!==1)return null;
     for(const [slot,url] of Object.entries(roles)){
      const key=slot==='cover_portrait'?'coverUrl':slot==='cover_landscape'?'shareCoverUrl':null;
      if(!key||file[key]!==url||m[key]!==url)return null;
      const selector=slot==='cover_portrait'?'.vertical-cover-wrap .vertical-img-size':'.horizon-cover-wrap .horizon-img-size';
      const imgs=[...covers[0].$el.querySelectorAll(selector)].filter(physicalVisible);
      if(imgs.length!==1||imgs[0].tagName!=='IMG'||!imgs[0].complete||imgs[0].naturalWidth<=0||imgs[0].naturalHeight<=0||
         imgs[0].src!==url||imgs[0].currentSrc!==url||imgs[0].getAttribute('src')!==url)return null;
     }
    }
    if(scope.thumb && (file.thumbUrl!==scope.thumb||m.thumbUrl!==scope.thumb))return null;
   }
   binding={editor,material,url:media[0].url,restoreVideo};return binding;
  };
  const visible=el=>{
   if(!physicalVisible(el))return false;
   const projected=el.ownerDocument!==call(owner,el)||el.getRootNode()!==call(rootNode,el);
   if(!projected)return true;
   const current=projectedBinding();
   return !!current && call(contains,current.editor.$el,el);
  };
  if(scope?.restore&&!projectedBinding())return null;
  for(const el of seenElements){
   if(!visible(el))continue;
   if(el.tagName==='VIDEO'){
    if(scope?.restore&&el!==projectedBinding().restoreVideo)return null;
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


async def tc_reopened_assets_match(page, candidate, *, restored_from=None, cover_roles=None, strict_cover_media=False) -> bool:
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
        if custom and restored_from is not None and strict_cover_media:
            session = getattr(page, '_sau_draft_evidence', None)
            expected = getattr(session, 'expected', None)
            thumb = candidate.get('tc_thumb_media')
            # Legacy desc parsing mirrors absent shareCoverUrl from thumbUrl.
            # With no requested crops, keep that field an automatic thumbnail.
            if (isinstance(expected, dict) and expected.get('cover_sha256') == {} and
                    not candidate.get('tc_cover_media') and isinstance(thumb, dict) and
                    not slot_ids.get('cover_portrait') and slot_ids.get('cover_landscape') == {thumb.get('resource')}):
                custom = False
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
        source_candidate = restored_from if isinstance(restored_from, dict) else candidate
        session = getattr(page, '_sau_draft_evidence', None)
        scope, bound_record, bound_binding = None, None, None
        if session is not None:
            binding = getattr(session, 'normal_ui_binding', None)
            record_id, owner = source_candidate.get('id'), source_candidate.get('owner')
            if (getattr(session, 'platform', None) == 'tencent' and not getattr(session, 'closed', True) and
                    isinstance(record_id, str) and record_id and source_candidate.get('fresh') is True and
                    owner == getattr(session, 'account', {}).get('uid') and
                    binding == {'id': record_id, 'owner': owner, 'control': 'post_draft_item_edit'} and
                    any(item == source_candidate for item in getattr(session, 'saves', []))):
                scope = {'id': record_id}
                bound_record, bound_binding = copy.deepcopy(source_candidate), dict(binding)
                if restored_from is not None:
                    from uploader.tc_reopen_mapping import _row
                    row = _row(candidate)
                    if (not row or candidate.get('id') != record_id or candidate.get('owner') != owner or
                            not any(item == candidate for item in getattr(session, 'reads', []))):
                        return False
                    scope['restore'] = row
                    if cover_roles is not None:
                        if (not isinstance(cover_roles, dict) or not cover_roles or set(cover_roles) - _SLOTS or
                                any(candidate.get('tc_cover_media', {}).get(slot) != {'url': url, 'resource': _https_identity(url)}
                                    for slot, url in cover_roles.items())):
                            return False
                        scope['coverRoles'] = dict(cover_roles)
                    if strict_cover_media:
                        thumb = candidate.get('tc_thumb_media', {})
                        if thumb:
                            if _https_identity(thumb.get('url')) != thumb.get('resource'):
                                return False
                            scope['thumb'] = thumb['url']
        if restored_from is not None and (scope is None or 'restore' not in scope):
            return False
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
