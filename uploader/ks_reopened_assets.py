"""Read the normally restored Kuaishou React preview, without business calls.

Official contracts, 2026-10-03 cached sources (zero based character offsets):
ks_publish_page.js SHA256 31700ae668ae3ecb4d2204f108ac4b0bee2c4045b33a07b6947b02d422743b7a:
477137 getDraftData -> decode URL -> snapshotInfo -> ALL_UPLOAD;
227265 snapshotInfo.url -> current video-preview props -> video.src 224035;
472919 same snapshotInfo/fileId + cover URLs -> cover editor;
164986 default cover URL -> img.src (PK/profile follow the same component).
fresh-ks-index-Dld_zsLa.js 40618 Yo: URL startsWith(http) passes through;
otherwise uid/currentTime padded to 16 characters, AES-CBC/PKCS7 base64.
ks_video_core.js 957467/834228: coverKey -> normal GET cover/view -> Blob URL.
IDs need not appear in either opaque video URLs or generated cover Blob URLs.
This gate binds those consumers to the fresh saved/read record and full upload
links; an ID present in a form alone cannot pass it.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

from uploader.ks_evidence import DIGEST_JS, DISCOVER_JS, _digest, _scalar
from uploader.native_read import canonical_root_evaluate


READ_RESTORED_JS = r"""(async cfg => {
 if(window!==top||location.origin!=='https://cp.kuaishou.com')return false;
 const nativeSize=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get,
       blobSlice=Blob.prototype.slice,blobArrayBuffer=Blob.prototype.arrayBuffer;
 __DIGEST__
 __DISCOVER__
 const p=project();if(p.forms.length!==1||p.progress.length!==1||p.progress[0].status!==3)return false;
 const form=p.forms[0],progress=p.progress[0],fibers=new Set(discover());
 const scalar=v=>Number.isSafeInteger(v)&&v>0?String(v):typeof v==='string'&&v&&v.trim()===v?v:null;
 const covers=[form.coverKey,form.pkCoverKey,form.profileCoverKey].filter(v=>typeof v==='string'&&v).sort();
 if(scalar(form.fileId)!==cfg.video_id||JSON.stringify(covers)!==JSON.stringify(cfg.cover_ids)||
    form.coverKey!==cfg.cover_slots.cover?.[0]||form.videoComposite)return false;
 const normalize=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.hash?u.href:null;}catch(_){return null;}};
 let expectedURL=cfg.preview_source;
 if(!expectedURL.startsWith('http')){
  if(typeof cfg.uid!=='string'||!/^\d{1,16}$/.test(cfg.uid)||!Number.isSafeInteger(cfg.current_time)||cfg.current_time<=0)return false;
  try{
   const b64=expectedURL.replace(/-/g,'+').replace(/_/g,'/'),bin=atob(b64),bytes=Uint8Array.from(bin,c=>c.charCodeAt(0));
   const enc=new TextEncoder(),key=await crypto.subtle.importKey('raw',enc.encode(cfg.uid.padEnd(16,'0').slice(0,16)),{name:'AES-CBC'},false,['decrypt']);
   const decrypted=await crypto.subtle.decrypt({name:'AES-CBC',iv:enc.encode(String(cfg.current_time).padEnd(16,'0').slice(0,16))},key,bytes);
   expectedURL=new TextDecoder('utf-8',{fatal:true}).decode(decrypted);
  }catch(_){return false;}
 }
 expectedURL=normalize(expectedURL);if(!expectedURL||normalize(form.url)!==expectedURL)return false;
 const visible=el=>{if(!el?.isConnected||el.ownerDocument!==document)return false;
  const r=el.getBoundingClientRect();if(r.width<=0||r.height<=0)return false;
  let node=el,n=0;while(node&&node.nodeType===1&&n++<128){const s=getComputedStyle(node);
   if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0||s.contentVisibility==='hidden')return false;
   node=node.parentElement;
  }return node===null&&n<128;
 };
 const hostFiber=el=>{const found=Object.keys(el).filter(k=>k.startsWith('__reactFiber$')||k.startsWith('__reactInternalInstance$'));
  if(found.length!==1)return null;let f=own(el,found[0]);if(f&&!fibers.has(f)&&fibers.has(f.alternate)&&f.alternate.stateNode===el)f=f.alternate;
  if(!f||f.stateNode!==el||!fibers.has(f))return null;return f;
 };
 const ancestors=f=>{const out=[];let n=0;while(f&&n++<128){if(!fibers.has(f))return [];out.push(f);f=f.return;}return n<128?out:[];};
 const previews=[...document.querySelectorAll('main#preview-tours')].filter(visible);if(previews.length!==1)return false;
 const videos=[...document.querySelectorAll('video')].filter(visible);if(videos.length!==1||!previews[0].contains(videos[0]))return false;
 const video=videos[0],vf=hostFiber(video),vp=own(vf,'memoizedProps');
 if(!vf||normalize(own(vp,'src'))!==expectedURL||normalize(video.getAttribute('src'))!==expectedURL||normalize(video.currentSrc)!==expectedURL||
    video.error||video.readyState<1||!Number.isFinite(video.duration)||video.duration<=0||video.videoWidth<=0||video.videoHeight<=0)return false;
 const consumers=ancestors(vf).filter(f=>{const q=own(f,'memoizedProps');return q&&['url','coverUrl','captionStr','coPublishUser','hasVideoComposite'].every(k=>Object.hasOwn(q,k))&&
  normalize(own(q,'url'))===expectedURL&&own(q,'hasVideoComposite')===false;});
 if(consumers.length!==1)return false;
 const defaultPreviewCover=own(own(consumers[0],'memoizedProps'),'coverUrl');
 const editors=[...fibers].filter(f=>{const q=own(f,'memoizedProps');return q&&
  ['snapshotInfo','fileId','uploadFinish','coverUrl','pkUrl','profileUrl','supportProfileCover','onSaveCover'].every(k=>Object.hasOwn(q,k));});
 // The official p_ wrapper forwards the same props to d_. Collapse that
 // ancestor chain to its one physical editor, never two sibling instances.
 const physicalEditors=editors.filter(f=>!editors.some(other=>other!==f&&ancestors(other).includes(f)));
 if(physicalEditors.length!==1)return false;
 const editor=physicalEditors[0],ep=own(editor,'memoizedProps');if(own(ep,'snapshotInfo')!==form||own(ep,'uploadFinish')!==true||
  scalar(own(ep,'fileId'))!==cfg.video_id||own(ep,'coverUrl')!==defaultPreviewCover)return false;
 const slots=[['coverKey','coverUrl'],['pkCoverKey','pkUrl'],['profileCoverKey','profileUrl']].filter(([k])=>typeof form[k]==='string'&&form[k]);
 const images=[...document.querySelectorAll('img')].filter(visible),evidence=[];
 for(const [key,prop] of slots){const url=own(ep,prop),wanted=cfg.cover_views[form[key]];
  if(typeof url!=='string'||!url.startsWith('blob:https://cp.kuaishou.com/')||typeof wanted!=='string')return false;
  const matched=images.filter(img=>{const f=hostFiber(img);return f&&ancestors(f).includes(editor)&&own(own(f,'memoizedProps'),'src')===url&&img.getAttribute('src')===url&&img.currentSrc===url&&img.complete&&img.naturalWidth>0&&img.naturalHeight>0;});
  if(matched.length!==1)return false;
  // This fetch is restricted to an already existing same-origin Blob URL.
  // It reads browser memory and issues no remote platform request.
  let blob;try{const response=await fetch(url);if(!response.ok)return false;blob=await response.blob();}catch(_){return false;}
  const size=Reflect.apply(nativeSize,blob,[]);if(!Number.isSafeInteger(size)||size<=0||size>32*1024*1024||await digest(blob,size)!==wanted)return false;
  evidence.push([matched[0],url]);
 }
 const next=project();if(next.forms.length!==1||next.progress.length!==1||next.forms[0]!==form||next.progress[0]!==progress||progress.status!==3||
    scalar(form.fileId)!==cfg.video_id||normalize(form.url)!==expectedURL||JSON.stringify([form.coverKey,form.pkCoverKey,form.profileCoverKey].filter(v=>typeof v==='string'&&v).sort())!==JSON.stringify(covers)||
    !visible(video)||video.currentSrc!==expectedURL||!evidence.every(([img,url])=>visible(img)&&img.currentSrc===url&&hostFiber(img)&&ancestors(hostFiber(img)).includes(editor)))return false;
 return true;
})(__CONFIG__)""".replace('__DIGEST__', DIGEST_JS.replace(
    "if(window.__sauKSObserver!==state)throw Error('obsolete observer');", "" )).replace('__DISCOVER__', DISCOVER_JS)


def native_ks_snapshot_response(page, response, observed_at, reopen_at):
    """Attest only the normal post-reopen top-frame snapshot POST response."""
    try:
        parsed = urlsplit(response.url)
        request = response.request
        request_at = request.timing.get('startTime') / 1000
        return (parsed.scheme == 'https' and parsed.netloc == 'cp.kuaishou.com' and not parsed.fragment and
                not parsed.username and not parsed.password and parsed.path in
                ('/rest/cp/works/v2/video/pc/snapshot/info', '/rest/cp/works/v3/video/pc/snapshot/info') and
                request.method == 'POST' and request.frame is page.main_frame and request.url == response.url and
                type(reopen_at) in (int, float) and reopen_at <= request_at <= observed_at and
                200 <= response.status < 300)
    except Exception:
        return False


async def ks_reopened_assets_match(page, candidate):
    """Verify visible consumers using only this operation's existing evidence."""
    session = getattr(page, '_sau_draft_evidence', None)
    observer = getattr(session, 'ks_observer', None)
    if (observer is None or not observer._current() or not isinstance(candidate, dict) or
            candidate.get('fresh') is not True or candidate.get('owner') != session.account.get('uid') or
            type(getattr(session, 'reopen_at', None)) not in (int, float) or session.reopen_at > time.time()):
        return False
    video_ids, cover_ids = candidate.get('video_ids'), candidate.get('cover_ids')
    slots = candidate.get('cover_slots')
    if (not isinstance(video_ids, list) or len(video_ids) != 1 or not _scalar(video_ids[0]) or
            candidate.get('id') != video_ids[0] or candidate.get('video_count') != 1 or
            not isinstance(cover_ids, list) or not 1 <= len(cover_ids) <= 3 or len(set(cover_ids)) != len(cover_ids) or
            not all(_scalar(v) for v in cover_ids) or not isinstance(slots, dict) or
            not isinstance(slots.get('cover'), list) or len(slots['cover']) != 1 or slots['cover'][0] not in cover_ids):
        return False
    reopened = await session.reopened_record(candidate)
    if (not isinstance(reopened, dict) or reopened.get('ks_preview_scope_bound') is not True or reopened.get('owner') != session.account.get('uid') or
            any(reopened.get(k) != candidate.get(k) for k in ('id', 'video_ids', 'cover_ids', 'cover_slots')) or
            type(reopened.get('observed_at')) not in (int, float) or reopened['observed_at'] < session.reopen_at or
            not isinstance(reopened.get('ks_preview_source'), str) or not 0 < len(reopened['ks_preview_source']) <= 8192):
        return False
    # The candidate's IDs inherit only the already verified complete byte links.
    links = session.upload_links()
    video = [v for v in links if v.get('kind') == 'video' and v.get('complete') is True and
             v.get('sha256') == session.expected.get('video_sha256') and video_ids[0] in v.get('ids', [])]
    if len(video) != 1 or not _digest(session.expected.get('video_sha256')):
        return False
    cover_hashes = session.expected.get('cover_sha256')
    if not isinstance(cover_hashes, dict) or not cover_hashes:
        return False
    for slot, sha in cover_hashes.items():
        if not _digest(sha) or not any(v.get('kind') == 'cover' and v.get('complete') is True and v.get('sha256') == sha and
                set(v.get('ids', [])).intersection(slots.get(slot, [])) for v in links):
            return False
    views = {}
    for key in cover_ids:
        hashes = {v.get('body_sha256') for v in observer.records if v.get('operation') == 'cover_view' and
                  v.get('valid') is True and v.get('cover_key') == key and v.get('request_at', -1) >= session.reopen_at}
        if len(hashes) != 1 or not _digest(next(iter(hashes))):
            return False
        views[key] = next(iter(hashes))
    cfg = {'video_id': video_ids[0], 'cover_ids': sorted(cover_ids), 'cover_slots': slots, 'cover_views': views,
           'preview_source': reopened['ks_preview_source'], 'current_time': reopened.get('ks_current_time'),
           'uid': session.account['uid']}
    try:
        result = await canonical_root_evaluate(page, READ_RESTORED_JS.replace('__CONFIG__', json.dumps(cfg)), timeout=5)
    except Exception:
        return False
    return result is True and observer._current()
