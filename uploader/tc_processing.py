"""Observe the official TC upload -> automatic video-processing provenance.

841.d575b04c.js offsets: updateFile 1750172, handleCropVideo 1750716,
clipVideoRequest 1752879, getClipVideoResult 1753324, success=1 1744821,
MaterialEdit consumption 1931820. 97.98a20fe5.js endpoints 66784/66832.
The same official client permits asyncClip after allocation (1751825):
MaterialEdit consumes response.httpsUrl plus urlCdnTaskId=clipTicket.draftId
(1932409/1933355); getCreateOrPreviewParams retains that task (2275944).
No SDK/business method is called. A derived resource is correlated with the
original native source SHA; its own bytes are never claimed to have that SHA.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import secrets
import time
from urllib.parse import urlsplit

from uploader.tc_resource import tc_resource_identity


PREFIX = '/micro/content/cgi-bin/mmfinderassistant-bin/post/'
PATHS = {PREFIX + 'post_clip_video': 'allocate', PREFIX + 'post_clip_video_result': 'result'}
MAX_VIDEO_BYTES = 20 * 1024 ** 3
KNOWN_KEYS = frozenset(('url', 'timeStart', 'cropDuration', 'height', 'width', 'x', 'y',
    'clipOriginVideoInfo', 'traceInfo', 'targetWidth', 'targetHeight', 'type',
    'useAstraThumbCover', 'errCode', 'errMsg', 'data', 'clipKey', 'draftId', 'flag',
    'fileSize', 'duration'))


def processing_endpoint(url):
    try:
        value = urlsplit(url)
        if (value.scheme == 'https' and value.netloc == 'channels.weixin.qq.com' and
                not value.fragment and not value.username and not value.password):
            return PATHS.get(value.path)
    except (TypeError, ValueError):
        pass
    return None


def _number(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum


def _file_size(value):
    """Exact positive uint64 JSON representation, bounded to TC's 20 GiB.

    The protobuf JSON transport serializes uint64 fileSize as decimal strings;
    native File.size remains an integer. Do not coerce other numeric syntax.
    """
    if type(value) is int:
        return value if 0 < value <= MAX_VIDEO_BYTES else None
    if (type(value) is str and len(value) <= len(str(MAX_VIDEO_BYTES)) and
            re.fullmatch(r'[1-9][0-9]*', value)):
        parsed = int(value)
        return parsed if parsed <= MAX_VIDEO_BYTES else None
    return None


def _dimensions(value):
    return (isinstance(value, dict) and _number(value.get('width'), 16384) and
            _number(value.get('height'), 16384) and _number(value.get('duration'), 28801) and
            _file_size(value.get('fileSize')) is not None)


def _canonical_dimensions(value):
    if not _dimensions(value):
        return None
    # Retain every field: normalization cannot make a formerly non-exact
    # metadata object equal by silently dropping an unexpected field.
    return {**value, 'fileSize': _file_size(value['fileSize'])}


def _canonical_native(value):
    if not isinstance(value, dict):
        return None
    metadata = {key: _canonical_dimensions(value.get(key)) for key in ('origin', 'result', 'consumed')}
    if any(item is None for item in metadata.values()):
        return None
    if 'mode' in value and value['mode'] != 'async_clip':
        return None
    return {**value, **metadata}


def _ticket(value):
    if not isinstance(value, dict):
        return None
    pair = tuple(value.get(key) for key in ('clipKey', 'draftId'))
    return pair if all(isinstance(x, str) and 0 < len(x) <= 2048 and
                       all(ord(c) >= 32 for c in x) for x in pair) else None


def _params(body):
    if not isinstance(body, dict):
        return None
    origin = body.get('clipOriginVideoInfo')
    source = tc_resource_identity(body.get('url'))
    if (not source or not _dimensions(origin) or
            any(type(body.get(key)) not in (int, float) or body[key] != 0
                for key in ('timeStart', 'cropDuration', 'x', 'y')) or
            type(body.get('type')) is not int or body['type'] != 4 or
            body.get('width') != origin['width'] or body.get('height') != origin['height'] or
            'useAstraThumbCover' in body and not (
                type(body['useAstraThumbCover']) is bool or
                type(body['useAstraThumbCover']) is int and body['useAstraThumbCover'] in (0, 1))):
        return None
    width, height = origin['width'], origin['height']
    long, short = max(width, height), min(width, height)
    if long > 1920 or short > 1080:
        scale = 1920 / long if long / short > 1920 / 1080 else 1080 / short
        big, small = math.floor(long * scale), math.floor(short * scale)
        target = (big, small) if width > height else (small, big)
    else:
        target = (width, height)
    if (type(body.get('targetWidth')) not in (int, float) or
            type(body.get('targetHeight')) not in (int, float) or
            (body['targetWidth'], body['targetHeight']) != target):
        return None
    return {'source': source, 'origin': _canonical_dimensions({k: origin[k] for k in ('width', 'height', 'duration', 'fileSize')}),
            'target': target}


def _safe_diagnostic(operation, response, body, data, timing_valid, parsed):
    reply = data if isinstance(data, dict) else {}
    payload = reply.get('data') if isinstance(reply.get('data'), dict) else {}
    shape = {'event': 'tencent_processing_protocol', 'operation': operation,
        'http_status': response.status if type(response.status) is int else None,
        'request_keys': sorted(KNOWN_KEYS.intersection(body)),
        'response_keys': sorted(KNOWN_KEYS.intersection(reply)),
        'data_keys': sorted(KNOWN_KEYS.intersection(payload)),
        'codes': {k: reply[k] for k in ('errCode',) if type(reply.get(k)) is int},
        'flags': {k: payload[k] for k in ('flag',) if type(payload.get(k)) is int},
        'request_timing_valid': timing_valid, 'request_parameters_valid': _params(body) is not None,
        'request_origin_size_valid': isinstance(body.get('clipOriginVideoInfo'), dict) and
            _file_size(body['clipOriginVideoInfo'].get('fileSize')) is not None,
        'response_size_valid': _file_size(payload.get('fileSize')) is not None,
        'request_origin_size_string': isinstance(body.get('clipOriginVideoInfo'), dict) and
            type(body['clipOriginVideoInfo'].get('fileSize')) is str,
        'response_size_string': type(payload.get('fileSize')) is str,
        'request_ticket_valid': _ticket(body) is not None, 'response_ticket_valid': _ticket(payload) is not None,
        'parsed': parsed}
    try:
        print('DRAFT_DIAGNOSTIC=' + json.dumps(shape, sort_keys=True), flush=True)
    except Exception:
        pass


# Native File hashing is streamed in 1 MiB chunks and yields after each chunk.
# The cache contains only immutable native File fingerprints for this nonce.
# The exact digest implementation is copied from FILE_OBSERVER_SCRIPT, whose
# native SHA fixtures already cover padding and multiple blocks.
NATIVE_READ_SCRIPT = r"""async scope => {
 try {
  if(location.origin!=='https://channels.weixin.qq.com' || !location.pathname.startsWith('/platform/'))return null;
  const rootKey=JSON.stringify(scope);
  let state=window.__sauTCProcessingReader;
  if(!state || state.key!==rootKey)state=window.__sauTCProcessingReader={key:rootKey,cache:new WeakMap()};
  const roots=[document],seenRoots=new Set(),seenVM=new Set(),materials=new Set();let nodes=0;
  while(roots.length){
   const root=roots.shift();if(seenRoots.has(root))continue;seenRoots.add(root);if(seenRoots.size>64)return null;
   for(const el of root.querySelectorAll('*')){
    if(++nodes>20000)return null;if(el.shadowRoot)roots.push(el.shadowRoot);
    if(el.tagName==='IFRAME'){try{const d=el.contentDocument;if(d&&d.location.origin==='https://channels.weixin.qq.com'&&d.location.pathname.startsWith('/micro/content/'))roots.push(d);}catch(_){}}
    const chain=new Set();let depth=0;
    for(let v=el.__vue__;v;v=v.$parent){
     if(chain.has(v)||++depth>32)return null;chain.add(v);if(seenVM.has(v))break;seenVM.add(v);if(seenVM.size>2048)return null;
     if(Array.isArray(v.fileList)&&['handleFileListChange','handleSetFileList','handleDeleteFile','updateFile'].every(k=>typeof v[k]==='function'))materials.add(v);
    }
   }
  }
  if(materials.size!==1)return null;
  const vm=materials.values().next().value,files=vm.fileList,upload=vm.$refs?.postUpload;
  if(files.length!==1||!upload||Array.isArray(upload)||!Array.isArray(upload.value)||upload.value.length!==1||
     !['handleCropVideo','clipVideoRequest','getClipVideoResult','updateVideo','updateFile','setFileList'].every(k=>typeof upload[k]==='function'))return null;
  const file=files[0],other=upload.value[0];
  if(!file||file.isVideo!==true||file.isImage!==false||file.isValid!==true||file.status!=='done')return null;
  const native=file.originFileObj;
  const fileName=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
  const blobSize=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
  const blobSlice=Blob.prototype.slice,blobArrayBuffer=Blob.prototype.arrayBuffer;
  const name=Reflect.apply(fileName,native,[]),size=Reflect.apply(blobSize,native,[]);
  if(typeof name!=='string'||!Number.isSafeInteger(size)||size<=0||size>21474836480||file.size!==size||
     file!==other&&other?.originFileObj!==native)return null;
  const response=file.response,processed=file.processedResponse,post=vm.postStore?.postObjDesc;
  const media=post?.media,ticket=vm.postStore?.createPostStatus?.clipTicket,ext=file.extInfo;
  const asyncClip=!processed&&vm.postStore?.asyncClip===true;
  if(!response||(!asyncClip&&(!processed||processed.flag!==1))||!Array.isArray(media)||media.length!==1||
     typeof file.uuid!=='string'||!file.uuid||media[0].md5sum!==file.uuid||
     typeof response.DownloadURL!=='string'||typeof response.httpsUrl!=='string'||
     (!asyncClip&&typeof processed.url!=='string')||!ticket||!ext)return null;
  const consumedUrl=asyncClip?response.httpsUrl:processed.url;
  if(media[0].url!==consumedUrl||asyncClip&&(
      typeof ticket.clipKey!=='string'||!ticket.clipKey||typeof ticket.draftId!=='string'||!ticket.draftId||
      media[0].urlCdnTaskId!==ticket.draftId))return null;
  const captured={file,native,processed,url:consumedUrl,uuid:file.uuid,source:response.httpsUrl,download:response.DownloadURL,
                  clipKey:ticket.clipKey,draftId:ticket.draftId,media:media[0],asyncClip};
  function canonicalSize(value){
   if(typeof value==='number')return Number.isSafeInteger(value)&&value>0&&value<=21474836480?value:null;
   if(typeof value!=='string'||! /^[1-9][0-9]{0,10}$/.test(value))return null;
   const number=Number(value);
   return Number.isSafeInteger(number)&&number<=21474836480&&String(number)===value?number:null;
  }
  function metadata(width,height,duration,fileSize){
   const size=canonicalSize(fileSize);
   if(size===null||typeof width!=='number'||!Number.isFinite(width)||width<=0||width>16384||
      typeof height!=='number'||!Number.isFinite(height)||height<=0||height>16384||
      typeof duration!=='number'||!Number.isFinite(duration)||duration<=0||duration>28801)return null;
   return {width,height,duration,fileSize:size};
  }
  const originMetadata=metadata(ext.width,ext.height,ext.duration,size),
        resultMetadata=asyncClip?originMetadata:metadata(processed.width,processed.height,processed.duration,processed.fileSize),
        consumedMetadata=metadata(media[0].width,media[0].height,media[0].videoPlayLen,media[0].fileSize);
  if(!originMetadata||!resultMetadata||!consumedMetadata)return null;
  if(asyncClip&&JSON.stringify(consumedMetadata)!==JSON.stringify(originMetadata))return null;
  const K = new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
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
    if(window.__sauTCProcessingReader!==state)throw Error('obsolete processing observer');
    const part=Reflect.apply(blobSlice,file,[off,off+1048576]),bytes=new Uint8Array(await Reflect.apply(blobArrayBuffer,part,[]));
    const merged=new Uint8Array(tail.length+bytes.length);merged.set(tail);merged.set(bytes,tail.length);
    const whole=merged.length-merged.length%64;for(let p=0;p<whole;p+=64)block(merged,p);tail=merged.slice(whole);await new Promise(r=>setTimeout(r,0));
   }
   const last=new Uint8Array(tail.length<56?64:128);last.set(tail);last[tail.length]=128;
   const view=new DataView(last.buffer);view.setUint32(last.length-8,Math.floor(size/536870912));view.setUint32(last.length-4,(size*8)>>>0);
   for(let p=0;p<last.length;p+=64)block(last,p);return Array.from(h,x=>x.toString(16).padStart(8,'0')).join('');
  }
  let fingerprint=state.cache.get(native);if(!fingerprint){fingerprint=digest(native,size);state.cache.set(native,fingerprint);}
  const sha256=await fingerprint;
  if(window.__sauTCProcessingReader!==state||vm.fileList.length!==1||vm.fileList[0]!==file||upload.value.length!==1||
     upload.value[0]!==file&&upload.value[0]?.originFileObj!==native||
     file.originFileObj!==native||file.uuid!==captured.uuid||file.processedResponse!==processed||
     (!asyncClip&&processed.url!==captured.url)||
     (asyncClip&&(vm.postStore?.asyncClip!==true||captured.media.urlCdnTaskId!==captured.draftId))||
     file.response?.httpsUrl!==captured.source||file.response?.DownloadURL!==captured.download||
     vm.postStore?.postObjDesc?.media?.length!==1||vm.postStore.postObjDesc.media[0]!==captured.media||
     captured.media.url!==captured.url||captured.media.md5sum!==captured.uuid||
     vm.postStore?.createPostStatus?.clipTicket?.clipKey!==captured.clipKey||vm.postStore.createPostStatus.clipTicket.draftId!==captured.draftId||
     JSON.stringify(metadata(ext.width,ext.height,ext.duration,size))!==JSON.stringify(originMetadata)||
     (!asyncClip&&JSON.stringify(metadata(processed.width,processed.height,processed.duration,processed.fileSize))!==JSON.stringify(resultMetadata))||
     JSON.stringify(metadata(media[0].width,media[0].height,media[0].videoPlayLen,media[0].fileSize))!==JSON.stringify(consumedMetadata))return null;
  const result={name,size,sha256,uuid:captured.uuid,source_url:captured.source,download_url:captured.download,
          processed_url:asyncClip?null:captured.url,ticket:{clipKey:captured.clipKey,draftId:captured.draftId},
          origin:originMetadata,result:resultMetadata,consumed:consumedMetadata};
  if(asyncClip)Object.assign(result,{mode:'async_clip',consumed_url:captured.url,url_cdn_task_id:captured.draftId});
  return result;
 }catch(_){return null;}
}"""


class TCProcessingObserver:
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.started_at = time.time()
        self.scope = {k: session.expected.get(k) for k in ('job_id', 'receipt_nonce')}
        self.scope['observer_nonce'] = secrets.token_hex(16)
        self.account = copy.deepcopy(session.account)
        self.records, self.native = [], None
        self.closed, self._lock = False, asyncio.Lock()
        self._last_summary = None
        self._frozen_reopen = None

    def _current(self):
        expected = self.session.expected
        account = self.session.account
        wanted = expected.get('account', {})
        return (not self.closed and not self.session.closed and
                getattr(self.page, '_sau_draft_evidence', None) is self.session and
                self.session.platform == 'tencent' and self.account.get('verified') is True and
                isinstance(wanted, dict) and self.account.get('uid') == wanted.get('uid') and bool(wanted.get('uid')) and
                self.account.get('identity') == wanted.get('identity') and bool(wanted.get('identity')) and
                account == self.account and all(isinstance(self.scope[k], str) and self.scope[k] and
                    self.scope[k] == expected.get(k) for k in ('job_id', 'receipt_nonce')))

    async def observe(self, response, data, observed_at):
        operation = processing_endpoint(response.url)
        if not operation or not self._current():
            return
        try:
            body = response.request.post_data_json
        except Exception:
            body = None
        body = body if isinstance(body, dict) else {}
        try:
            start = response.request.timing.get('startTime')
            start = start / 1000 if type(start) in (int, float) else -1
        except Exception:
            start = -1
        timed = self.started_at <= start <= observed_at and self.session.started_at <= start
        reply = data if isinstance(data, dict) else {}
        payload = reply.get('data')
        success = 200 <= response.status < 300 and type(reply.get('errCode')) is int and reply['errCode'] == 0
        valid = timed and response.request.method == 'POST'
        record = None
        if operation == 'allocate' and valid:
            params = _params(body)
            source = tc_resource_identity(body.get('url'))
            if source:
                record = {'operation': operation, 'request_at': start, 'observed_at': observed_at,
                          'source': source, 'params': params, 'ticket': _ticket(payload) if success else None,
                          'valid': bool(success and params and _ticket(payload))}
        elif operation == 'result' and valid and _ticket(body):
            terminal = success and isinstance(payload, dict) and type(payload.get('flag')) is int and payload['flag'] == 1
            resource = tc_resource_identity(payload.get('url')) if terminal else None
            record = {'operation': operation, 'request_at': start, 'observed_at': observed_at,
                      'ticket': _ticket(body), 'valid': bool(terminal and resource and _dimensions(payload)),
                      'resource': resource, 'url': payload.get('url') if terminal else None,
                      'dimensions': _canonical_dimensions({k: payload[k] for k in ('width', 'height', 'duration', 'fileSize')}) if terminal and _dimensions(payload) else None,
                      'pending': bool(success and isinstance(payload, dict) and type(payload.get('flag')) is int and payload['flag'] == 2),
                      'failed': not success or isinstance(payload, dict) and type(payload.get('flag')) is int and payload['flag'] in (3, 4)}
        if record is not None and len(self.records) < 512:
            self.records.append(record)
        elif record is not None:
            self.closed = True
        _safe_diagnostic(operation, response, body, data, timed, bool(record and record['valid']))

    async def drain(self):
        if not self._current() or not any(x['operation'] == 'allocate' and x['valid'] for x in self.records):
            self.native = None
            return
        async with self._lock:
            try:
                value = await asyncio.wait_for(self.page.main_frame.evaluate(
                    NATIVE_READ_SCRIPT, self.scope, isolated_context=False), timeout=30)
            except Exception:
                value = None
            self.native = _canonical_native(value) if self._current() else None
            summary = {'event': 'tencent_processing_state', 'allocations': sum(x['operation'] == 'allocate' for x in self.records),
                'valid_allocations': sum(x['operation'] == 'allocate' and x['valid'] for x in self.records),
                'results': sum(x['operation'] == 'result' for x in self.records),
                'terminals': sum(x['operation'] == 'result' and x['valid'] for x in self.records),
                'native_read_valid': self.native is not None,
                'native_async_clip': bool(self.native and self.native.get('mode') == 'async_clip'),
                'native_sha_match': bool(self.native and self.native.get('sha256') == self.session.expected.get('video_sha256')),
                'scope_current': self._current()}
            if summary != self._last_summary:
                self._last_summary = summary
                try:
                    print('DRAFT_DIAGNOSTIC=' + json.dumps(summary, sort_keys=True), flush=True)
                except Exception:
                    pass

    async def freeze_for_reopen(self, candidate):
        """Retain completed provenance only at the verified normal-reopen gate.

        This is historical upload proof, never a current editor/File claim.
        The reopened record, DOM resources and fields must still pass separately.
        """
        from uploader.draft_evidence import CHECKS, verify_evidence
        session = self.session
        if (self._frozen_reopen is not None or not self._current() or
                getattr(session, 'reopen_at', None) is not None or
                getattr(session, 'normal_ui_binding', None) is not None or
                getattr(session, 'issues', None) != [] or not isinstance(candidate, dict) or
                candidate.get('fresh') is not True or not isinstance(candidate.get('id'), str) or not candidate['id'] or
                candidate.get('owner') != self.account.get('uid') or
                type(getattr(session, 'action_at', None)) not in (int, float) or
                session.action_at < session.started_at or
                not any(item == candidate for item in getattr(session, 'saves', []))):
            return False
        account_after = await session.read_account()
        await self.drain()
        if not self._current() or account_after != self.account or session.issues:
            return False
        links = session.upload_links()
        checked_links, valid_chain = self._augment_with_native(links, self.native)
        checks, reasons = verify_evidence('tencent', session.expected, candidate, checked_links, self.account)
        if (any(checks[key] is not True for key in CHECKS if key != 'reopened') or
                reasons != ['reopened_not_verified'] or not any(item == candidate for item in session.saves)):
            return False
        if not valid_chain:
            # restore_checkpoint already authenticated the original completed
            # chain and independently read its current composer. A fresh
            # observer must not manufacture the missing historical requests.
            restored_at = getattr(session, '_checkpoint_restored_at', None)
            expires_at = getattr(session, '_checkpoint_expires_at', None)
            sealed = getattr(session, '_checkpoint_links', [])
            videos = [item for item in sealed if item.get('kind') == 'video' and
                      item.get('complete') is True and item.get('sha256') == session.expected.get('video_sha256')]
            video = session.assets.get('video', {})
            selected = any(item.get('selected_at', 0) >= restored_at and (
                str(item.get('type', '')).startswith('video/') or
                (item.get('name'), item.get('size')) == (video.get('name'), video.get('size')) or
                str(item.get('name', '')).lower().endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v')))
                for item in session.files) if type(restored_at) in (int, float) else True
            return bool(type(restored_at) in (int, float) and session.started_at <= restored_at <= time.time() and
                type(expires_at) in (int, float) and time.time() < expires_at and
                getattr(session, '_checkpoint_baseline_hashes', None) is not None and
                len(videos) == 1 and videos[0] in links and not selected and not self.records and not session.exchanges)
        if self.native.get('mode') == 'async_clip' and not self.async_candidate_verified(candidate):
            return False
        covers = {}
        if session.expected.get('cover_sha256'):
            observer = getattr(session, 'tc_cover_observer', None)
            covers = observer.freeze_records(candidate, checked_links) if observer is not None else None
            if covers is None:
                return False
        self._frozen_reopen = {'native': copy.deepcopy(self.native), 'expected': copy.deepcopy(session.expected),
            'assets': copy.deepcopy(session.assets), 'files': copy.deepcopy(session.files),
            'exchanges': copy.deepcopy(session.exchanges), 'records': copy.deepcopy(self.records),
            'candidate': copy.deepcopy(candidate), 'links': copy.deepcopy(checked_links), 'covers': covers, 'at': time.time()}
        return True

    def _frozen_current(self, links):
        frozen, session = self._frozen_reopen, self.session
        if not frozen or not self._current() or getattr(session, 'issues', None) != []:
            return False
        binding = getattr(session, 'normal_ui_binding', None)
        candidate = frozen['candidate']
        base = (self._frozen_events_current() and
            type(getattr(session, 'reopen_at', None)) in (int, float) and session.reopen_at >= frozen['at'] and
            binding == {'id': candidate['id'], 'owner': candidate['owner'], 'control': 'post_draft_item_edit'})
        if not base:
            return False
        task_rows = frozen['candidate'].get('tc_media', [])
        task_bound = (len(task_rows) == 1 and task_rows[0].get('task') == frozen['native']['ticket']['draftId'])
        if frozen['native'].get('mode') == 'async_clip' or task_bound:
            # Retain the original attested upload after a legitimate terminal.
            # Derived IDs are never rewritten to the original or assigned its SHA.
            videos = [x for x in links if x.get('kind') == 'video']
            old = [x for x in frozen['links'] if x.get('kind') == 'video']
            extra = {x['resource'] for x in self.records if x['operation'] == 'result' and x.get('valid') and
                     x['ticket'] == _ticket(frozen['native']['ticket'])}
            return (len(videos) == len(old) == 1 and videos[0].get('complete') is True and
                videos[0].get('sha256') == old[0]['sha256'] and
                tc_resource_identity(frozen['native']['source_url']) in videos[0].get('ids', []) and
                set(videos[0].get('ids', [])) <= set(old[0]['ids']) | extra and
                [x for x in links if x.get('kind') != 'video'] == [x for x in frozen['links'] if x.get('kind') != 'video'])
        return self._augment_with_native(links, frozen['native']) == (frozen['links'], True)

    def _frozen_events_current(self):
        frozen, session = self._frozen_reopen, self.session
        return bool(frozen and self._current() and not getattr(session, 'issues', ['missing']) and
            session.expected == frozen['expected'] and session.assets == frozen['assets'] and
            session.files == frozen['files'] and session.exchanges == frozen['exchanges'] and
            self._frozen_processing_current(frozen) and
            (not frozen.get('covers') or getattr(session, 'tc_cover_observer', None) is not None and
             session.tc_cover_observer.frozen_current(frozen['covers'])) and
            any(item == frozen['candidate'] for item in getattr(session, 'saves', [])))

    def _frozen_processing_current(self, frozen):
        original = frozen['records']
        if self.records == original:
            return True
        task_rows = frozen['candidate'].get('tc_media', [])
        task_bound = (len(task_rows) == 1 and task_rows[0].get('task') == frozen['native']['ticket']['draftId'])
        if ((frozen['native'].get('mode') != 'async_clip' and not task_bound) or
                self.records[:len(original)] != original):
            return False
        ticket = _ticket(frozen['native'].get('ticket'))
        # A normal in-flight flag=2 poll may finish while leaving the editor.
        # It changes neither the uploaded original nor the saved task. A new
        # known successful terminal must independently match the allocation's
        # target. Errors, unknown states, other tasks or mutation invalidate.
        from uploader.tc_reopen_mapping import processing_target_matches
        allocation_at = max(x['observed_at'] for x in original if x['operation'] == 'allocate')
        return all(item['operation'] == 'result' and item['ticket'] == ticket and item['failed'] is False and
                   (item.get('pending') is True or item.get('valid') is True and item['request_at'] >= allocation_at and
                    tc_resource_identity(item.get('url')) == item.get('resource') and
                    processing_target_matches(self, item.get('dimensions'), frozen))
                   for item in self.records[len(original):])

    def augment_links(self, links):
        if self._frozen_reopen and not self._frozen_events_current():
            self._frozen_reopen = None
        if self.native is not None:
            if self._frozen_reopen and self.native != self._frozen_reopen['native']:
                frozen = self._frozen_reopen
                from uploader.tc_reopen_mapping import processing_target_matches
                original, current = frozen['native'], self.native
                valid_transition = (original.get('mode') == 'async_clip' and current.get('mode') != 'async_clip' and
                    all(current.get(k) == original.get(k) for k in ('sha256', 'uuid', 'source_url', 'download_url', 'ticket', 'origin')) and
                    processing_target_matches(self, current.get('result'), frozen) and
                    self._augment_with_native(links, current)[1])
                if not valid_transition:
                    self._frozen_reopen = None
            if self._frozen_current(links):
                return copy.deepcopy(self._frozen_reopen['links'])
            return self._augment_with_native(links, self.native)[0]
        if self._frozen_current(links):
            return copy.deepcopy(self._frozen_reopen['links'])
        # Before a normal same-ID edit is attested, missing/malformed live
        # state never inherits a preceding successful read.
        return copy.deepcopy(links)

    def current_publication_resource(self, links):
        """A typed current consumer plus the complete original upload proof."""
        _, valid = self._augment_with_native(links, self.native)
        if not valid:
            return None
        value = self.native.get('consumed_url') if self.native.get('mode') == 'async_clip' else self.native.get('processed_url')
        return tc_resource_identity(value)

    def async_candidate_verified(self, candidate):
        native = self.native
        if not isinstance(native, dict) or native.get('mode') != 'async_clip' or not isinstance(candidate, dict):
            return False
        resource = tc_resource_identity(native.get('source_url'))
        return (candidate.get('video_count') == 1 and candidate.get('video_ids') == [resource] and
                candidate.get('tc_media_tasks') == [{'resource': resource, 'draft_id': native['ticket']['draftId']}])

    def _augment_with_native(self, links, native):
        result = copy.deepcopy(links)
        native = _canonical_native(native)
        if not self._current() or not native:
            return result, False
        asset = self.session.assets.get('video', {})
        sha = self.session.expected.get('video_sha256')
        if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha) or asset.get('sha256') != sha:
            return result, False
        selections = [x for x in self.session.files if
            (x.get('name'), x.get('size'), x.get('sha256')) == (native.get('name'), native.get('size'), sha)]
        video_selections = [x for x in self.session.files if
            str(x.get('type', '')).startswith('video/') or x.get('name') == asset.get('name') or
            str(x.get('name', '')).lower().endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'))]
        videos = [x for x in result if x.get('kind') == 'video' and x.get('complete') is True]
        if (len(selections) != 1 or len(video_selections) != 1 or video_selections[0] is not selections[0] or
                len(videos) != 1 or videos[0].get('sha256') != sha or
                native.get('sha256') != sha or (native.get('name'), native.get('size')) != (asset.get('name'), asset.get('size'))):
            return result, False
        original = tc_resource_identity(native.get('source_url'))
        if (not original or tc_resource_identity(native.get('download_url')) != original or
                original not in videos[0].get('ids', []) or not _dimensions(native.get('origin'))):
            return result, False
        allocations = [x for x in self.records if x['operation'] == 'allocate' and x['source'] == original]
        if (not allocations or any(not x['valid'] for x in allocations) or
                len({x['ticket'] for x in allocations}) != 1 or
                any(x['params']['origin'] != native['origin'] for x in allocations)):
            return result, False
        allocation = min(allocations, key=lambda x: x['request_at'])
        selected_at = selections[0].get('selected_at')
        if (type(selected_at) not in (int, float) or not self.session.started_at <= selected_at <= allocation['request_at'] or
                _ticket(native.get('ticket')) != allocation['ticket']):
            return result, False
        terminal = [x for x in self.records if x['operation'] == 'result' and x['ticket'] == allocation['ticket'] and
                    x['request_at'] >= allocation['observed_at']]
        if native.get('mode') == 'async_clip':
            if (native.get('processed_url') is not None or native.get('consumed_url') != native.get('source_url') or
                    native.get('url_cdn_task_id') != allocation['ticket'][1] or
                    native['result'] != native['origin'] or native['consumed'] != native['origin'] or
                    any(item.get('pending') is not True or item['failed'] for item in terminal)):
                return result, False
            # This mode consumes the original uploaded resource and the exact
            # current allocation ticket. Never call it a processed resource.
            return result, True
        complete = [x for x in terminal if x['valid']]
        if (not complete or any(x['failed'] for x in terminal) or
                len({(x['resource'], x['url']) for x in complete}) != 1):
            return result, False
        final = complete[0]
        if (final['url'] != native.get('processed_url') or final['dimensions'] != native.get('result') or
                native.get('consumed') != native.get('result') or tc_resource_identity(final['url']) != final['resource']):
            return result, False
        # Extend this unique original chain only. Never merge separate uploads
        # merely because their source fingerprints happen to be equal.
        videos[0]['ids'] = sorted(set(videos[0]['ids']) | {final['resource']})
        return result, True

    async def close(self):
        self.closed, self.native, self._frozen_reopen = True, None, None


async def install_tc_processing_observer(session):
    observer = TCProcessingObserver(session)
    session.tc_processing_observer = observer
    return observer
