"""Read the already mounted video-channel MaterialEdit without business calls.

The cached official 841.d575b04c.js defines MaterialEdit.fileList (1931391),
handleSetFileList/updateFile (1937642), its pure readiness getters (1939734),
and $refs.postUpload.value from the same entries (1943192). VideoUploader's
updateVideo uses value[0] (1754076); duplicate entries must remain counted.
Do not read mediaList or disablePostTip: those getters mutate file metadata.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit


_KEYS = ("recognized", "count", "video_count", "done_count", "all_uploaded",
         "all_cover_cropped", "can_save_draft")


def _unknown() -> dict:
    return dict(recognized=False, count=0, video_count=0, done_count=0,
                all_uploaded=False, all_cover_cropped=False, can_save_draft=False)


_READ = r"""(includeResources=false) => {
 try {
  if(location.origin!=='https://channels.weixin.qq.com' || !location.pathname.startsWith('/platform/'))return null;
  const roots=[document],seenRoots=new Set(),seenVM=new Set(),materials=new Set();
  let nodes=0;
  const material=vm=>Array.isArray(vm.fileList) &&
   ['handleFileListChange','handleSetFileList','handleDeleteFile','updateFile'].every(k=>typeof vm[k]==='function') &&
   ['allUploaded','allCoverCroped','canSaveDraft','disablePostTip'].every(k=>k in vm);
  while(roots.length){
   const root=roots.shift();if(seenRoots.has(root))continue;seenRoots.add(root);
   if(seenRoots.size>64)return null;
   for(const el of root.querySelectorAll('*')){
    if(++nodes>20000)return null;
    if(el.shadowRoot)roots.push(el.shadowRoot);
    if(el.tagName==='IFRAME'){
     // Read the existing same-origin owner document in this single realm.
     // Object identity is preserved across projections, unlike JSON equality.
     let owner;try{owner=el.contentDocument;}catch(_){}
     if(owner && owner.location.origin==='https://channels.weixin.qq.com' &&
        owner.location.pathname.startsWith('/micro/content/'))roots.push(owner);
    }
    const chain=new Set();let depth=0;
    for(let vm=el.__vue__;vm;vm=vm.$parent){
     if(chain.has(vm)||++depth>32)return null;chain.add(vm);
     if(seenVM.has(vm))break;seenVM.add(vm);
     if(seenVM.size>2048)return null;
     if(material(vm))materials.add(vm);
    }
   }
  }
  if(materials.size!==1)return null;
  const vm=materials.values().next().value,files=vm.fileList,upload=vm.$refs?.postUpload;
  if(files.length>128 || !upload || Array.isArray(upload) ||
     !['handleCropVideo','updateVideo','updateFile','setFileList'].every(k=>typeof upload[k]==='function') ||
     !Array.isArray(upload.value) || upload.value.length!==files.length)return null;
  const value=upload.value,statuses=new Set(['success','uploading','done','error','removed','croping']);
  const native=file=>{
   try{
    // Native getters perform a browser brand check, including foreign-realm
    // Files; a forged object, filename or same-size Blob cannot stand in.
    return typeof Object.getOwnPropertyDescriptor(File.prototype,'name').get.call(file)==='string' &&
     typeof Object.getOwnPropertyDescriptor(Blob.prototype,'size').get.call(file)==='number';
   }catch(_){return false;}
  };
  const origin=file=>file.originFileObj==null?null:file.originFileObj;
  let videoCount=0,doneCount=0,croppedCount=0;
  for(let i=0;i<files.length;i++){
   const file=files[i],other=value[i];
   if(!file || typeof file!=='object' || Array.isArray(file) || !other || typeof other!=='object' ||
      typeof file.isVideo!=='boolean' || typeof file.isImage!=='boolean' || file.isVideo===file.isImage ||
      typeof file.isValid!=='boolean' || !statuses.has(file.status) ||
      !(file.thumbUrl==null || typeof file.thumbUrl==='string'))return null;
   const a=origin(file),b=origin(other);
   if((a!==null&&!native(a)) || (b!==null&&!native(b)))return null;
   if(file!==other && !(a!==null && a===b))return null;
   if(file.isVideo)videoCount++;
   if(file.status==='done')doneCount++;
   if(file.thumbUrl)croppedCount++;
  }
  const flag=value=>typeof value==='boolean'?value:value===0?false:null;
  const uploaded=flag(vm.allUploaded),cropped=flag(vm.allCoverCroped),save=flag(vm.canSaveDraft);
  if(uploaded===null || cropped===null || save===null ||
     uploaded!==(files.length>0&&doneCount===files.length) ||
     cropped!==(files.length>0&&croppedCount===files.length) || (save&&!uploaded))return null;
  const state={recognized:true,count:files.length,video_count:videoCount,done_count:doneCount,
   all_uploaded:uploaded,all_cover_cropped:cropped,can_save_draft:save};
  if(!includeResources)return state;
  // The official mediaList getter copies response.httpsUrl into httpsUrl but
  // keeps extInfo.localFileUrl in url. The rendered preview therefore uses a
  // Blob, which cannot itself match the attested DFS download resource.
  if(files.length!==1 || videoCount!==1 || doneCount!==1 || !uploaded || !cropped)return null;
  const file=files[0],response=file.response;
  if(!native(origin(file)) || !response || typeof response!=='object' || Array.isArray(response) ||
     typeof response.httpsUrl!=='string' || response.httpsUrl!==file.httpsUrl ||
     typeof response.DownloadURL!=='string' || typeof file.url!=='string' || !file.url)return null;
  const videos=new Set();
  for(const root of seenRoots)for(const video of root.querySelectorAll('video')){
   const box=video.getBoundingClientRect(),style=video.ownerDocument.defaultView.getComputedStyle(video);
   if(!video.isConnected || !box.width || !box.height || style.display==='none' || style.visibility==='hidden')continue;
   // The same physical element can also be projected by Wujie's owner iframe.
   videos.add(video);
  }
  if(videos.size!==1)return null;
  const video=videos.values().next().value;
  if(video.currentSrc!==file.url || video.getAttribute('src')!==file.url)return null;
  return {...state,resources:[response.DownloadURL,response.httpsUrl]};
 }catch(_){return null;}
}"""


def _main(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        value = urlsplit(url)
        return (value.scheme == "https" and value.netloc.lower() == "channels.weixin.qq.com"
                and value.path.startswith("/platform/"))
    except ValueError:
        return False


async def read_tc_material_state(page) -> dict:
    """Return only booleans/counts, or recognized=False when ownership is unclear.

    can_save_draft projects the official getter; a caller's single-video gate
    must also require count == video_count == done_count == 1 and both readiness
    flags. Always check recognized before treating count == 0 as an empty form.
    """
    try:
        before = page.url
        if not _main(before) or not _main(page.main_frame.url):
            return _unknown()
        result = await asyncio.wait_for(page.main_frame.evaluate(_READ, isolated_context=False), timeout=5)
        if page.url != before or set(result or {}) != set(_KEYS) or result.get("recognized") is not True:
            return _unknown()
        for key in ("count", "video_count", "done_count"):
            if type(result[key]) is not int or not 0 <= result[key] <= 128:
                return _unknown()
        if result["video_count"] > result["count"] or result["done_count"] > result["count"]:
            return _unknown()
        if any(type(result[key]) is not bool for key in ("all_uploaded", "all_cover_cropped", "can_save_draft")):
            return _unknown()
        return result
    except Exception:
        return _unknown()


async def read_tc_composer_media(page):
    """Correlate the unique live native upload entry with its visible preview.

    Returns private resource identities only. The caller still checks them
    against the independently sealed upload; this read creates no proof.
    """
    from uploader.tc_resource import tc_resource_identity
    try:
        before=page.url
        if not _main(before) or not _main(page.main_frame.url):
            return None
        result=await asyncio.wait_for(page.main_frame.evaluate(_READ, True, isolated_context=False),5)
        if (page.url!=before or set(result or {})!=set(_KEYS)|{'resources'} or
                result.get('recognized') is not True or
                any(type(result.get(k)) is not int or result[k]!=1 for k in ('count','video_count','done_count')) or
                any(result.get(k) is not True for k in ('all_uploaded','all_cover_cropped'))):
            return None
        urls=result.get('resources')
        if not isinstance(urls,list) or len(urls)!=2:
            return None
        identities=[tc_resource_identity(url) for url in urls]
        if any(identity is None for identity in identities) or len(set(identities))!=1:
            return None
        return {'source':'platform_composer','video_count':1,'video_ids':[identities[0]]}
    except Exception:
        return None
