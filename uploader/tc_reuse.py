"""Bound, read-only diagnostics for TC's normal DFS cache reuse.

Public 841.d575b04c.js: the normal PostUpload uses 6ab5.default (1735850).
Its constructor/init accepts chunkSize/cdnFileType/sceneType/authParams only
(866150); startUpload's second argument changes reporting, but still invokes
reuseUpload unconditionally (867360). ReuseByUrl/Confirm restore an old UUID
and UploadID (872144) and finish that ID without a fresh allocation (881532).
No SDK method is invoked, SDK option is written, or upload proof is created.
"""
import asyncio
import json
from urllib.parse import urlsplit

from uploader.tc_pipeline import _READ


_MODES = {'none', 'expired', 'reused_parts', 'reused_confirm', 'reused_url'}
_COUNTS = ('planned_parts', 'stored_parts', 'current_allocations', 'current_parts', 'current_finishes')
_BOOLS = ('recognized', 'snapshot_reused', 'terminal', 'has_upload_id')

_READ_REUSE = '(binding,known)=>{' + r"""
 try {
  const readBinding=READ_BINDING,state=readBinding(binding);
  if(!['uploading','done'].includes(state))return null;
  const file=binding.material.fileList[0],sdk=file.uploader;
  const method=(o,k)=>{for(let i=0;o&&i++<8;o=Object.getPrototypeOf(o)){const d=Object.getOwnPropertyDescriptor(o,k);if(d)return 'value'in d&&typeof d.value==='function';}return false;};
  if(!sdk||typeof sdk!=='object'||!['startUpload','reuseUpload','initTaskId','handleComplete','getAvailableUploadedInfo'].every(k=>method(sdk,k)))return null;
  // Read declared storage fields, not business/computed getters. The normal
  // SDK declares these as instance data fields in its constructor.
  const own=(o,k)=>{const d=Object.getOwnPropertyDescriptor(o,k);return d&&'value'in d?d.value:undefined;};
  const native=binding.file,sdkFile=own(sdk,'file');
  if(sdkFile!==native&&!(sdkFile===file&&sdkFile.originFileObj===native))return null;
  const reuse=own(sdk,'reuseUploadType'),snapshot=own(sdk,'isUseSnapshotUpload'),terminal=own(sdk,'isTerminalUpload'),
    count=own(sdk,'chunkCount'),parts=own(sdk,'partInfo'),uuid=own(sdk,'uuid'),upload=own(sdk,'uploadTaskId');
  if(![1,2,3,4,5].includes(reuse)||typeof snapshot!=='boolean'||typeof terminal!=='boolean'||
     !Number.isInteger(count)||count<1||count>65536||!Array.isArray(parts)||parts.length>count||
     typeof uuid!=='string'||uuid.length>2048||typeof upload!=='string'||upload.length>2048)return null;
  const stored=parts.filter(Boolean);
  if(stored.some(p=>!p||typeof p!=='object'||!Number.isInteger(p.PartNumber)||p.PartNumber<1||p.PartNumber>count||
    typeof p.ETag!=='string'||!p.ETag||p.ETag.length>2048)||new Set(stored.map(p=>p.PartNumber)).size!==stored.length)return null;
  const matching=known.filter(x=>x.task_id===uuid&&x.upload_id===upload);
  if(file.uploader!==sdk||readBinding(binding)!==state)return null;
  return {recognized:true,state,reuse_mode:['none','expired','reused_parts','reused_confirm','reused_url'][reuse-1],
    snapshot_reused:snapshot,terminal,has_upload_id:!!uuid&&!!upload,planned_parts:count,stored_parts:stored.length,
    current_allocations:matching.filter(x=>x.stage==='allocate').length,
    current_parts:matching.filter(x=>x.stage==='part').length,
    current_finishes:matching.filter(x=>x.stage==='finish').length};
 }catch(_){return null;}
}""".replace('READ_BINDING', '(' + _READ + ')')


def _current(page, session, expected, binding):
    return (session is not None and session.page is page and not session.closed and session.platform == 'tencent' and
            getattr(page, '_sau_draft_evidence', None) is session and
            getattr(page, '_sau_tc_native_import', None) is binding and binding is not None and
            all(expected.get(key) and expected.get(key) == session.expected.get(key)
                for key in ('job_id', 'receipt_nonce', 'video_sha256')))


async def read_tc_upload_reuse(page, session, expected):
    """Return only fixed enums/counts, or None if original task ownership fails."""
    binding = getattr(page, '_sau_tc_native_import', None)
    try:
        if not _current(page, session, expected, binding):
            return None
        before, frame = page.url, page.main_frame
        route = urlsplit(before)
        if (route.scheme != 'https' or route.netloc != 'channels.weixin.qq.com' or
                route.path.rstrip('/') != '/platform/post/create' or frame.url != before):
            return None
        asset = session.assets.get('video', {})
        if (asset.get('sha256') != expected['video_sha256'] or not isinstance(asset.get('name'), str) or
                type(asset.get('size')) is not int or asset['size'] <= 0):
            return None
        metadata = (asset['name'], asset['size'])
        known = []
        for item in session.exchanges:
            if item.get('tc_protocol') != 'dfs' or item.get('metadata') != metadata:
                continue
            task, upload = item.get('task_id'), item.get('upload_id')
            if not all(isinstance(value, str) and 0 < len(value) <= 2048 for value in (task, upload)):
                return None
            stage = 'allocate' if 'plan' in item else 'part' if 'part_number' in item else 'finish' if item.get('stage') == 'complete' else None
            if stage:
                known.append({'task_id': task, 'upload_id': upload, 'stage': stage})
        if len(known) > 256:
            return None
        result = await asyncio.wait_for(binding.evaluate(_READ_REUSE, known), 5)
        if (not _current(page, session, expected, binding) or page.url != before or
                page.main_frame is not frame or frame.url != before or type(result) is not dict or
                set(result) != set(_COUNTS + _BOOLS) | {'state', 'reuse_mode'} or
                any(type(result[key]) is not bool for key in _BOOLS) or result['recognized'] is not True or
                result['state'] not in ('uploading', 'done') or result['reuse_mode'] not in _MODES or
                any(type(result[key]) is not int or not 0 <= result[key] <= 65536 for key in _COUNTS) or
                result['stored_parts'] > result['planned_parts']):
            return None
        return result
    except Exception:
        return None


async def tc_unverified_upload_reason(page, session, expected):
    """Improve a denied upload's explanation; never turn it into acceptance."""
    diagnostic = await read_tc_upload_reuse(page, session, expected)
    print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'tencent_upload_reuse',
          **(diagnostic or {'recognized': False})}, sort_keys=True, separators=(',', ':')), flush=True)
    if (diagnostic and diagnostic['snapshot_reused'] and diagnostic['has_upload_id'] and
            diagnostic['reuse_mode'] in ('reused_parts', 'reused_confirm', 'reused_url') and
            diagnostic['current_allocations'] == 0):
        return 'tencent_cached_upload_chain_unverified'
    return 'current_upload_unverified'
