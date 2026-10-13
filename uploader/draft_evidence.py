"""Read-only evidence for a draft created by this browser operation.

No endpoint is called here. Responses are observed from the normal creator UI;
IndexedDB and localStorage are read without creating or modifying records.
Unknown schemas fail closed. A toast or a title in a list is never evidence.

2026-10-03 source anchors (zero-based Unicode character offsets; preserved
under research/raw/2026-10-03, with URL/checksum in each manifest):
  DY official-three/dy_async_1060.js:41270 account-scoped cache-on-leave;
     dy_async_396.js:11237 K3 aggregation/textResult; dy_async_8212.js:60991
     text/textExtra/itemTitle; dy_async_8261.js:380304 recovery consumes cache.
  XHS xhs-wechat/official-project-publish-vue.474cd180.js:303085 database/store;
      156255 content/draftId/uid/timeStamp; 1898091 DOM draft ID/type;
      1900700 Edit passes that same draftId to normal recovery.
  KS official-three/ks_video_core.js:805203 snapshot/info and snapshot/save;
     956499 upload finish wrapper; ks_publish_page.js:263188 debounce,
     264262 caption/ALL_UPLOAD gate, 469086 coverKey/pkCoverKey/profileCoverKey.
  Bili official-three/bili_videoup_index.js:360346 add/update wrappers;
       357888 view; 1064139 videos(filename/cid), title/cover/tag/desc mapping;
       656590 draftWhite eligibility gate.
  TC limits-login/tc-content-841.d575b04c.js:683648 SNS uploadfinish/fileurl;
     859361 DFS endpoint, 861863 TransFlag/PartInfo, 871239 fresh UploadID,
     875839 chunk X-Errno/ETag, 882372 complete DownloadURL; 2275939 objectDesc.

Engineering limits: generic progress/chunk replies, Bili UPOS multipart result
aliases, transformed cover Blobs, unknown TC upload endpoints, media
URLs hiding all resource IDs, and missing independently verified owner remain
unverified. Synthetic fixtures exercise our contract, not live acceptance.
"""
import asyncio
import hashlib
import hmac
import inspect
import json
import math
import re
import time
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ALIASES = {'抖音': 'douyin', '小红书': 'xiaohongshu', '视频号': 'tencent',
           'B站': 'bilibili', 'b站': 'bilibili', '快手': 'kuaishou', 'ks': 'kuaishou'}
IDENTITY_KEYS = ('job_id', 'platform_id', 'video_sha256', 'account_key', 'content_sha256',
                 'cover_sha256', 'operation_key', 'receipt_nonce')
CHECKS = ('upload_complete', 'fields', 'cover', 'media', 'reopened', 'account')
POLICIES = {
    'douyin': ('browser_local', 'local_draft_verified'),
    'xiaohongshu': ('browser_local', 'local_draft_verified'),
    'bilibili': ('platform', 'platform_draft_verified'),
    'tencent': ('platform', 'platform_draft_verified'),
    'kuaishou': ('platform_snapshot', 'platform_snapshot_verified'),
}
CHECKPOINT_VERSION = 1
CHECKPOINT_MAX_AGE = 6 * 60 * 60
CHECKPOINT_MAX_BYTES = 256 * 1024
_ASSET_ROLES = ('video', 'cover', 'cover_landscape', 'cover_portrait')


class CheckpointError(ValueError):
    """Fixed reason codes only; never include account, media or input content."""


def _checkpoint_json(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CheckpointError('checkpoint_invalid_json') from None
    if len(encoded) > CHECKPOINT_MAX_BYTES:
        raise CheckpointError('checkpoint_too_large')
    return encoded


def _checkpoint_key(value):
    # The desktop application stores this independent local key, not the
    # worker JSON/receipt nonce. An unsigned JSON cannot attest past observations.
    if not isinstance(value, bytes) or not 32 <= len(value) <= 1024:
        raise CheckpointError('checkpoint_seal_key_invalid')
    return value


def _checkpoint_string(value, limit=1024):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def _checkpoint_digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _checkpoint_resource(value):
    if isinstance(value, str) and re.fullmatch(r'tc-resource:[0-9a-f]{64}', value):
        return True
    if not _checkpoint_string(value, 8192) or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    # Relative resource URLs can contain the same credentials as absolute ones.
    allowed = {'filekey', 'm', 'storeid', 'hy', 'filename', 'fileid', 'id', 'videoid', 'cid'}
    if parsed.fragment or not all(key.lower() in allowed for key in parse_qs(parsed.query, keep_blank_values=True)):
        return False
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            return False
        # Never persist signed/authenticated URLs. Stable resource IDs and the
        # official TC resource query fields suffice for correlation when known.
        return True
    return True


def _checkpoint_links(value, *, filter_resources=False):
    if not isinstance(value, list) or len(value) > 64:
        raise CheckpointError('checkpoint_links_invalid')
    links = {}
    for raw in value:
        if not isinstance(raw, dict) or (not filter_resources and set(raw) != {'kind', 'sha256', 'ids', 'complete'}):
            raise CheckpointError('checkpoint_links_invalid')
        if raw.get('kind') not in ('video', 'cover') or not _checkpoint_digest(raw.get('sha256')) or raw.get('complete') is not True:
            raise CheckpointError('checkpoint_links_invalid')
        ids = raw.get('ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 32:
            raise CheckpointError('checkpoint_links_invalid')
        if filter_resources:
            ids = [value for value in ids if _checkpoint_resource(value)]
        elif not all(_checkpoint_resource(value) for value in ids):
            raise CheckpointError('checkpoint_resource_invalid')
        if not ids:
            continue
        link = {'kind': raw['kind'], 'sha256': raw['sha256'], 'ids': sorted(set(ids)), 'complete': True}
        links[(link['kind'], link['sha256'], tuple(link['ids']))] = link
    return list(links.values())


def _checkpoint_expected_identity(expected, platform):
    identity = {key: expected.get(key) for key in IDENTITY_KEYS}
    if platform not in POLICIES or identity['platform_id'] != platform:
        raise CheckpointError('checkpoint_platform_mismatch')
    for key in ('job_id', 'account_key', 'receipt_nonce'):
        if not _checkpoint_string(identity[key]):
            raise CheckpointError('checkpoint_identity_invalid')
    for key in ('video_sha256', 'content_sha256', 'operation_key'):
        if not _checkpoint_digest(identity[key]):
            raise CheckpointError('checkpoint_identity_invalid')
    covers = identity['cover_sha256']
    if not isinstance(covers, dict) or not set(covers).issubset(set(_ASSET_ROLES) - {'video'}) or not all(_checkpoint_digest(x) for x in covers.values()):
        raise CheckpointError('checkpoint_identity_invalid')
    fields = expected.get('platform_fields')
    if not isinstance(fields, dict):
        raise CheckpointError('checkpoint_identity_invalid')
    from workbench_model import canonical_sha, platform_content_fields
    try:
        content = platform_content_fields(platform, fields)
        fingerprint = {key: identity[key] for key in ('platform_id', 'account_key', 'video_sha256', 'content_sha256', 'cover_sha256')}
        if canonical_sha(content) != identity['content_sha256']:
            raise CheckpointError('checkpoint_content_mismatch')
        if canonical_sha(fingerprint) != identity['operation_key']:
            raise CheckpointError('checkpoint_operation_mismatch')
    except (KeyError, TypeError):
        raise CheckpointError('checkpoint_identity_invalid') from None
    return identity


def validate_checkpoint(checkpoint, *, seal_key, expected, platform=None, now=None):
    """Pure signed metadata validator for the desktop's resumable indicator.

    No page/file/account operations occur here. A successful return allows a
    resume attempt; restore_checkpoint still rehashes files, checks the live
    unique account and matches the existing unique composer to the sealed IDs.
    Returns a detached payload; callers must not log it or expose it to the UI.
    """
    key = _checkpoint_key(seal_key)
    if not isinstance(expected, dict):
        raise CheckpointError('checkpoint_identity_invalid')
    platform = platform_id(platform or expected.get('platform_id'))
    if (not isinstance(checkpoint, dict) or set(checkpoint) != {'checkpoint_version', 'payload', 'mac'} or
            type(checkpoint['checkpoint_version']) is not int or checkpoint['checkpoint_version'] != CHECKPOINT_VERSION or
            not _checkpoint_digest(checkpoint.get('mac'))):
        raise CheckpointError('checkpoint_schema_invalid')
    payload = checkpoint.get('payload')
    encoded = _checkpoint_json(payload)
    if not hmac.compare_digest(checkpoint['mac'], hmac.new(key, encoded, hashlib.sha256).hexdigest()):
        raise CheckpointError('checkpoint_seal_invalid')
    if (not isinstance(payload, dict) or set(payload) != {'phase', 'identity', 'account', 'assets', 'baseline_hashes',
                                                        'upload_links', 'origin_at', 'exported_at', 'expires_at'} or
            payload.get('phase') != 'uploaded_not_saved'):
        raise CheckpointError('checkpoint_schema_invalid')
    now = time.time() if now is None else now
    times = [payload[name] for name in ('origin_at', 'exported_at', 'expires_at')]
    if not all(type(value) in (int, float) and math.isfinite(value) for value in times + [now]):
        raise CheckpointError('checkpoint_time_invalid')
    origin, exported, expires = times
    if not 0 < origin <= exported < expires <= origin + CHECKPOINT_MAX_AGE or exported > now + 60:
        raise CheckpointError('checkpoint_time_invalid')
    if expires <= now:
        raise CheckpointError('checkpoint_expired')
    identity = _checkpoint_expected_identity(expected, platform)
    if payload['identity'] != identity:
        raise CheckpointError('checkpoint_task_mismatch')
    account, wanted = payload['account'], expected.get('account', {})
    if (not isinstance(account, dict) or set(account) - {'verified', 'identity', 'uid', 'platform'} or
            not isinstance(wanted, dict) or account.get('verified') is not True or account.get('platform') != platform or
            not _checkpoint_string(account.get('identity')) or account['identity'] != wanted.get('identity') or
            ('uid' in account and (isinstance(account['uid'], bool) or not _checkpoint_string(str(account['uid'])))) or
            (wanted.get('uid') is not None and str(account.get('uid')) != str(wanted['uid']))):
        raise CheckpointError('checkpoint_account_mismatch')
    assets = payload['assets']
    wanted_roles = {'video'} | set(identity['cover_sha256'])
    if not isinstance(assets, dict) or set(assets) != wanted_roles:
        raise CheckpointError('checkpoint_asset_mismatch')
    for role, asset in assets.items():
        digest = identity['video_sha256'] if role == 'video' else identity['cover_sha256'][role]
        kind = 'video' if role == 'video' else 'cover'
        if (not isinstance(asset, dict) or set(asset) != {'name', 'size', 'sha256', 'kind'} or
                not _checkpoint_string(asset.get('name')) or '/' in asset['name'] or '\\' in asset['name'] or
                type(asset.get('size')) is not int or not 0 < asset['size'] < 2 ** 53 or
                asset.get('sha256') != digest or asset.get('kind') != kind):
            raise CheckpointError('checkpoint_asset_mismatch')
    baseline = payload['baseline_hashes']
    if not isinstance(baseline, dict) or len(baseline) > 512 or not all(_checkpoint_string(k) and _checkpoint_digest(v) for k, v in baseline.items()):
        raise CheckpointError('checkpoint_baseline_invalid')
    links = _checkpoint_links(payload['upload_links'])
    allowed = {('video', identity['video_sha256'])} | {('cover', sha) for sha in identity['cover_sha256'].values()}
    if any((link['kind'], link['sha256']) not in allowed for link in links):
        raise CheckpointError('checkpoint_links_invalid')
    if len([link for link in links if link['kind'] == 'video']) != 1:
        raise CheckpointError('checkpoint_video_upload_unverified')
    return json.loads(encoded)


def platform_id(value):
    return ALIASES.get(value, str(value).lower())


async def evaluate_main(frame, script):
    """Patchright defaults to an isolated world; bindings/counters live in main."""
    try:
        return await frame.evaluate(script, isolated_context=False)
    except TypeError:
        # Regular Playwright has one evaluate signature without this option.
        return await frame.evaluate(script)


def _sha_file(path):
    file = Path(path)
    before = file.stat()
    digest = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = file.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError('素材在核验时发生变化。')
    return {'name': file.name, 'size': before.st_size, 'sha256': digest.hexdigest()}


def _text(value):
    return str(value or '').replace('\r\n', '\n').strip()


def _ids(*values):
    return sorted({str(x) for x in values if x is not None and str(x) and not isinstance(x, (dict, list, bool))})


def _first(mapping, *names):
    if not isinstance(mapping, dict):
        return None
    return next((mapping[k] for k in names if mapping.get(k) is not None), None)


def _tag_names(value):
    if isinstance(value, str):
        value = value.split(',') if ',' in value else ([value] if value else [])
    if not isinstance(value, list):
        return None
    return [_text(_first(x, 'name', 'topicName', 'title') if isinstance(x, dict) else x).lstrip('#') for x in value]


def description_values(platform, fields):
    body = _text(fields.get('description'))
    if platform_id(platform) != 'xiaohongshu':
        return {body}
    from uploader.xiaohongshu_uploader.main import draft_xhs_description_plan
    plan = draft_xhs_description_plan(fields.get('description') or '', fields.get('tags', []))
    return {plan['plain'], plan['serialized'], plan['platform_serialized']}


def parse_record(platform, raw, record_id=None, owner=None):
    """Normalize only known draft containers, rather than flatten arbitrary JSON."""
    p = platform_id(platform)
    if not isinstance(raw, dict):
        return None
    draft_id, uid, title, description, caption, tags, short_title = record_id, owner, None, None, None, None, None
    video_ids, cover_ids, cover_slots, video_count = [], [], {}, 0
    if p == 'douyin':
        cache = raw.get('cache', raw)
        if not isinstance(cache, dict):
            return None
        uid = _first(raw, 'uid', 'userId') or uid
        title = _first(cache, 'itemTitle', 'item_title', 'title')
        text = cache.get('textResult', {})
        caption = text.get('text') if isinstance(text, dict) else None
        media = cache.get('longFileInfo', {})
        if isinstance(media, dict):
            video_ids = _ids(media.get('vid'), media.get('video_id'), media.get('fileId'))
        video_count = int(bool(video_ids))
        for key, slot in (('coverInfo', 'cover_portrait'), ('coverHorizontalInfo', 'cover_landscape')):
            cover = cache.get(key, {})
            if isinstance(cover, dict):
                # The URI identifies the stored image. Its resolved/signed
                # display URL can change during ordinary draft restoration.
                uri = _first(cover, 'uri', 'coverUri')
                values = _ids(uri) if isinstance(uri, str) and uri else _ids(cover.get('url'))
                cover_ids.extend(values)
                cover_slots[slot] = values
        # Default video cover must also be present and survive restoration.
        if not cover_ids and isinstance(media, dict):
            cover_ids = _ids(media.get('coverUri'), media.get('cover_uri'))
    elif p == 'xiaohongshu':
        draft_id = raw.get('draftId') or draft_id
        uid = raw.get('uid') or uid
        content = raw.get('content', {})
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                return None
        store = content.get('draftStore', {}) if isinstance(content, dict) else {}
        if not isinstance(store, dict):
            return None
        title, description, tags = store.get('title'), store.get('desc'), _tag_names(store.get('hashTag', []))
        media = store.get('video', {})
        if isinstance(media, dict):
            video_ids = _ids(media.get('fileid'), media.get('fileId'), media.get('transcodeVideoFileId'))
        video_count = int(bool(video_ids))
        # Official buildDraftData selects contextStore.coverContext.cover when
        # it has an ID, otherwise the old draftStore cover. Do not union a
        # stale first-frame cover with the current editor's selected cover.
        context = content.get('contextStore', {}) if isinstance(content, dict) else {}
        cover_context = context.get('coverContext', {}) if isinstance(context, dict) else {}
        contextual_cover = cover_context.get('cover', {}) if isinstance(cover_context, dict) else {}
        cover = contextual_cover if isinstance(contextual_cover, dict) and _ids(
            contextual_cover.get('fileid'), contextual_cover.get('fileId')) else store.get('cover', {})
        if isinstance(cover, dict):
            cover_ids = _ids(cover.get('fileid'), cover.get('fileId'))
            cover_slots['cover'] = cover_ids[:]
    elif p == 'kuaishou':
        draft_id = raw.get('fileId') or draft_id
        uid = _first(raw, 'userId', 'uid') or uid
        from uploader.ks_caption import decode_ks_caption
        caption = decode_ks_caption(raw.get('caption'))
        video_ids = _ids(raw.get('fileId'))
        video_count = int(bool(video_ids))
        cover_ids = _ids(raw.get('coverKey'), raw.get('pkCoverKey'), raw.get('profileCoverKey'))
        cover_slots['cover'] = _ids(raw.get('coverKey'))
    elif p == 'bilibili':
        draft_id = _first(raw, 'draft_id', 'draftId', 'id') or draft_id
        archive = raw.get('archive', raw)
        if not isinstance(archive, dict):
            return None
        uid = _first(archive, 'mid', 'uid') or uid
        title, description, tags = archive.get('title'), archive.get('desc'), _tag_names(archive.get('tag', []))
        videos = raw.get('videos', archive.get('videos', []))
        if not isinstance(videos, list):
            return None
        video_count = len(videos)
        for video in videos:
            if isinstance(video, dict):
                video_ids.extend(_ids(video.get('filename'), video.get('cid')))
        cover_ids = _ids(archive.get('cover'), archive.get('cover43'))
        cover_slots['cover'] = _ids(archive.get('cover'))
    elif p == 'tencent':
        draft_id = _first(raw, 'objectId', 'object_id') or draft_id
        request = raw.get('postReq', raw)
        if not isinstance(request, dict):
            return None
        uid = _first(raw, 'finderUsername', 'uid') or uid
        # Current official postDraft wraps a postReq with objectDesc. Normal
        # draft readback also has the older desc shape; both use this parser.
        # Do not substitute a different schema when an explicit one is invalid.
        current_schema = 'objectDesc' in request
        desc = request.get('objectDesc') if current_schema else request.get('desc', {})
        if not isinstance(desc, dict):
            return None
        caption = _first(desc, 'description', 'desc')
        short_title = _first(request, 'shortTitle', 'short_title') or _first(desc, 'shortTitle', 'short_title')
        if isinstance(short_title, list):
            if not short_title:
                short_title = None
            elif len(short_title) == 1 and isinstance(short_title[0], dict) and isinstance(short_title[0].get('shortTitle'), str):
                short_title = short_title[0]['shortTitle']
            else:
                return None
        elif short_title is not None and not isinstance(short_title, str):
            return None
        media = desc.get('media', [])
        if not isinstance(media, list):
            return None
        video_count = len(media)
        for video in media:
            if isinstance(video, dict):
                video_ids.extend(_tc_stored_ids(video.get('url'), video.get('fileId'), video.get('videoId')))
                cover_ids.extend(_tc_stored_ids(video.get('thumbUrl'), video.get('coverUrl'), video.get('shareCoverUrl')))
                # The official cover editor writes coverUrl for the personal
                # card and shareCoverUrl for the landscape share card. thumbUrl
                # is the automatic video thumbnail, never a custom portrait.
                cover_slots['cover_portrait'] = _tc_stored_ids(video.get('coverUrl'))
                cover_slots['cover_landscape'] = _tc_stored_ids(video.get('shareCoverUrl'))
                if not current_schema and not video.get('shareCoverUrl'):
                    cover_slots['cover_landscape'] = _tc_stored_ids(video.get('thumbUrl'))
    else:
        return None
    return {'id': str(draft_id) if draft_id is not None else '', 'owner': str(uid) if uid is not None else '',
            'title': title, 'description': description, 'caption': caption, 'tags': tags,
            'short_title': short_title, 'video_ids': sorted(set(video_ids)), 'cover_ids': sorted(set(cover_ids)),
            'cover_slots': cover_slots, 'video_count': video_count}


def field_match(platform, record, expected):
    if not record or not isinstance(expected.get('platform_fields'), dict):
        return False
    fields = expected['platform_fields']
    p = platform_id(platform)
    if p in ('douyin', 'kuaishou', 'tencent'):
        from uploader.dy_editor import normalize_douyin_editor_text
        normalize = (lambda value: normalize_douyin_editor_text(_text(value))) if p == 'douyin' else _text
        if 'caption' not in fields or record.get('caption') is None or normalize(record['caption']) != normalize(fields['caption']):
            return False
        if p == 'douyin' and _text(record.get('title')) != _text(fields.get('title')):
            return False
        if p == 'tencent' and fields.get('short_title') and _text(record.get('short_title')) != _text(fields['short_title']):
            return False
        return True
    if p in ('xiaohongshu', 'bilibili'):
        if record.get('title') is None or record.get('description') is None or record.get('tags') is None:
            return False
        return (_text(record['title']) == _text(fields.get('title')) and
                (str(record['description']).replace('\r\n', '\n').replace('\r', '\n') in description_values(p, fields)
                 if p == 'xiaohongshu' else _text(record['description']) in description_values(p, fields)) and
                sorted(record['tags']) == sorted(fields.get('tags', [])))
    return False


def verify_evidence(platform, expected, record, links, account, reopened=None, ui_fields=False, account_after=None):
    """Pure verification, used by browser observations and local fixtures."""
    result = dict.fromkeys(CHECKS, False)
    reasons = []
    if not record:
        return result, ['no_correlated_draft_record']
    video = [x for x in links if x.get('kind') == 'video' and x.get('complete') is True and
             x.get('sha256') == expected.get('video_sha256')]
    result['upload_complete'] = bool(video)
    uploaded_ids = {i for x in video for i in x.get('ids', [])}
    result['media'] = bool(record.get('video_ids') and uploaded_ids.intersection(record['video_ids']) and record.get('video_count') == 1)
    result['fields'] = field_match(platform, record, expected)
    cover_hashes = expected.get('cover_sha256')
    result['cover'] = bool(record.get('cover_ids'))
    if not isinstance(cover_hashes, dict):
        result['cover'] = False
    elif cover_hashes:
        for slot, digest in cover_hashes.items():
            cover_links = [x for x in links if x.get('kind') == 'cover' and x.get('complete') is True and x.get('sha256') == digest]
            if not any(set(x.get('ids', [])).intersection(record.get('cover_slots', {}).get(slot, [])) for x in cover_links):
                result['cover'] = False
    wanted = expected.get('account', {})
    result['account'] = bool(account.get('verified') is True and account.get('identity') and
                             account.get('platform') == platform_id(platform) and
                             account.get('identity') == wanted.get('identity') and
                             (not wanted.get('uid') or str(account.get('uid')) == str(wanted['uid'])) and
                             (not record.get('owner') or str(account.get('uid')) == str(record['owner'])))
    if reopened:
        after = account_after or {}
        result['reopened'] = bool(ui_fields is True and reopened.get('id') == record.get('id') and
                                  reopened.get('video_ids') == record.get('video_ids') and
                                  reopened.get('cover_ids') == record.get('cover_ids') and
                                  reopened.get('cover_slots') == record.get('cover_slots') and
                                  field_match(platform, reopened, expected) and
                                  after.get('verified') is True and after.get('identity') == account.get('identity'))
    for key, okay in result.items():
        if not okay:
            reasons.append(key + '_not_verified')
    if not record.get('id'):
        reasons.append('draft_id_missing')
    if record.get('fresh') is not True:
        reasons.append('draft_not_new_for_this_operation')
    return result, reasons


# Streaming SHA-256 avoids copying a large video into one ArrayBuffer. Only
# fingerprints and file metadata cross the binding; no account token is read.
FILE_OBSERVER_SCRIPT = r"""(() => {
 if (window.__sauFileObserver) return;
 window.__sauFileObserver = true;
 // Wujie creates iframe-realm inputs which render in a main-realm shadow root.
 // Native WebIDL getters/methods accept their real cross-realm objects while
 // rejecting objects which merely imitate an input, File, or composed path.
 const inputFiles=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').get;
 const inputType=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'type').get;
 const fileName=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
 const blobSize=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
 const blobType=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get;
 const blobSlice=Blob.prototype.slice, blobArrayBuffer=Blob.prototype.arrayBuffer;
 const eventPath=Event.prototype.composedPath;
 const elementShadow=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get;
 const elementQuery=Element.prototype.querySelectorAll;
 const documentQuery=Document.prototype.querySelectorAll;
 const fragmentQuery=DocumentFragment.prototype.querySelectorAll;
 const addListener=EventTarget.prototype.addEventListener;
 const K = new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
 const R = (x,n) => (x>>>n)|(x<<(32-n));
 async function digest(file,size) {
   const h = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
   const w = new Uint32Array(64);
   function block(bytes,off) {
     for(let i=0;i<16;i++) {const p=off+i*4; w[i]=((bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3])>>>0;}
     for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(R(a,7)^R(a,18)^(a>>>3))+w[i-7]+(R(b,17)^R(b,19)^(b>>>10)))>>>0;}
     let [a,b,c,d,e,f,g,j]=h;
     for(let i=0;i<64;i++){const t1=(j+(R(e,6)^R(e,11)^R(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0;const t2=((R(a,2)^R(a,13)^R(a,22))+((a&b)^(a&c)^(b&c)))>>>0;j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
     [a,b,c,d,e,f,g,j].forEach((v,i)=>h[i]=(h[i]+v)>>>0);
   }
   let tail=new Uint8Array(0);
   for(let off=0;off<size;off+=1048576){
     const part=Reflect.apply(blobSlice,file,[off,off+1048576]);
     const bytes=new Uint8Array(await Reflect.apply(blobArrayBuffer,part,[]));
     const merged=new Uint8Array(tail.length+bytes.length);merged.set(tail);merged.set(bytes,tail.length);
     const whole=merged.length-(merged.length%64);for(let p=0;p<whole;p+=64)block(merged,p);tail=merged.slice(whole);
     await new Promise(r=>setTimeout(r,0));
   }
   const last=new Uint8Array(tail.length<56?64:128);last.set(tail);last[tail.length]=128;
   const view=new DataView(last.buffer);view.setUint32(last.length-8,Math.floor(size/536870912));view.setUint32(last.length-4,(size*8)>>>0);
   for(let p=0;p<last.length;p+=64)block(last,p);
   return Array.from(h,x=>x.toString(16).padStart(8,'0')).join('');
 }
 const seenEvents = new WeakSet(), boundRoots = new WeakSet();
 async function selected(e) {
   // A composed event is retargeted to its shadow host at document capture.
   // Native noncomposed changes only reach the root containing the input.
   if(seenEvents.has(e))return;
   let files;
   try {
     for(const node of Reflect.apply(eventPath,e,[])) {
       try {
         if(Reflect.apply(inputType,node,[])!=='file')continue;
         files=Array.from(Reflect.apply(inputFiles,node,[])||[]);break;
       } catch (_) { /* Not a native HTMLInputElement in any realm. */ }
     }
   } catch (_) { return; }
   if(!files)return;
   seenEvents.add(e);
   const selected_at=Date.now()/1000;
   for(const file of files) {
     try {
       const name=Reflect.apply(fileName,file,[]),size=Reflect.apply(blobSize,file,[]),type=Reflect.apply(blobType,file,[]);
       await window.__sauDraftSelected({name,size,type,sha256:await digest(file,size),selected_at});
     }
     catch (_) { /* Missing fingerprints leave the verification unresolved. */ }
   }
 }
 const observer = new MutationObserver(changes=>{
   for(const change of changes)for(const node of change.addedNodes)scan(node);
 });
 function elements(node) {
   // Each native query method performs its own receiver brand check. Avoid
   // instanceof (realm-sensitive) and page-overridden querySelectorAll methods.
   for(const query of [elementQuery,documentQuery,fragmentQuery]) {
     try {return Reflect.apply(query,node,['*']);}catch(_){}
   }
   return [];
 }
 function shadow(node) {
   try {return Reflect.apply(elementShadow,node,[]);}catch(_){return null;}
 }
 function scan(node) {
   const root=shadow(node);
   if(root?.mode==='open')bindRoot(root);
   for(const element of elements(node)) {
     const inner=shadow(element);
     if(inner?.mode==='open')bindRoot(inner);
   }
 }
 function bindRoot(root) {
   if(boundRoots.has(root))return;
   boundRoots.add(root);
   Reflect.apply(addListener,root,['change',selected,true]);
   observer.observe(root,{childList:true,subtree:true});
   scan(root);
 }
 // Observe only mutation subtrees, with no polling or strong root/event list.
 // Wrapping attachShadow also covers a root created before its host is mounted.
 const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,'attachShadow');
 if(descriptor?.value) {
   const original=descriptor.value;
   try {Object.defineProperty(Element.prototype,'attachShadow',{...descriptor,value:function(...args){
     const root=Reflect.apply(original,this,args);
     try {if(root.mode==='open')bindRoot(root);}catch(_){}
     return root;
   }});}catch(_){}
 }
 bindRoot(document);
})()"""

LOCAL_READ_SCRIPT = r"""async platform => {
 if(platform==='douyin') {
   const rows=[];
   for(let i=0;i<localStorage.length;i++) {
     const key=localStorage.key(i);if(!key.startsWith('publish_form_cache:'))continue;
     const text=localStorage.getItem(key);try{rows.push({key,raw:JSON.parse(text),serialized:text,owner:key.slice('publish_form_cache:'.length)});}catch(_){}
   }return rows;
 }
 if(platform!=='xiaohongshu'||!indexedDB.databases)return [];
 const inventory=await indexedDB.databases();if(!inventory.some(x=>x.name==='draft-database-v1'))return [];
 return await new Promise(resolve=>{
   const opening=indexedDB.open('draft-database-v1');
   opening.onupgradeneeded=()=>{opening.transaction.abort();resolve([]);};
   opening.onerror=()=>resolve([]);
   opening.onsuccess=()=>{
     const db=opening.result;if(!db.objectStoreNames.contains('video-draft')){db.close();resolve([]);return;}
     const tx=db.transaction('video-draft','readonly'),request=tx.objectStore('video-draft').getAll();
     request.onsuccess=()=>{const rows=request.result.map(raw=>({key:String(raw.draftId||''),raw,serialized:JSON.stringify(raw),owner:String(raw.uid||'')}));resolve(rows);};
     request.onerror=()=>resolve([]);tx.oncomplete=()=>db.close();tx.onabort=()=>{db.close();resolve([]);};
   };
 });
}"""


async def read_local_records(page, platform):
    rows = []
    for frame in page.frames:
        try:
            value = await frame.evaluate(LOCAL_READ_SCRIPT, platform_id(platform))
            for row in value or []:
                row['frame_url'] = frame.url
                if (row['key'], row['serialized']) not in {(x['key'], x['serialized']) for x in rows}:
                    rows.append(row)
        except Exception:
            continue
    return rows


def _response_ok(platform, data):
    if not isinstance(data, dict):
        return False
    p = platform_id(platform)
    key, wanted = ('result', 1) if p == 'kuaishou' else ('errCode', 0) if p == 'tencent' else ('code', 0)
    # Python True equals 1; it must not be accepted as the platform numeric code.
    return type(data.get(key)) is int and data[key] == wanted


def endpoint_kind(platform, url):
    path = urlparse(url).path.rstrip('/')
    p = platform_id(platform)
    if p == 'kuaishou':
        if path in {f'/rest/cp/works/{v}/video/pc/publishInfo/snapshot/save' for v in ('v2', 'v3')}:
            return 'save'
        if path in {f'/rest/cp/works/{v}/video/pc/snapshot/info' for v in ('v2', 'v3')}:
            return 'read'
    if p == 'bilibili':
        if path in ('/x/vupre/web/draft/add', '/x/vupre/web/draft/update'):
            return 'save'
        if path == '/x/vupre/web/draft/view':
            return 'read'
    if p == 'tencent':
        suffix = path
        for prefix in ('/micro/content/cgi-bin/mmfinderassistant-bin', '/cgi-bin/mmfinderassistant-bin'):
            if path.startswith(prefix + '/'):
                suffix = path[len(prefix):]
                break
        if suffix in ('/post/post_draft', '/post/post_ad_create_draft'):
            return 'save'
        if suffix == '/post/get_draft_info':
            return 'read'
        if suffix == '/post/get_draft_list':
            return 'readlist'
    return None


def _tc_draft_list_records(value, owner):
    """The normal editor consumes unique objectId rows from data.list."""
    if not isinstance(value, dict) or not isinstance(value.get('list'), list):
        return None
    total = value.get('totalCount')
    if type(total) is not int or total < len(value['list']):
        return None
    records, ids = [], set()
    for raw in value['list']:
        if not isinstance(raw, dict) or not _tc_scalar(raw.get('objectId')):
            return None
        record_id = str(raw['objectId'])
        if record_id in ids:
            return None
        ids.add(record_id)
        desc = raw.get('desc')
        if (not isinstance(desc, dict) or not isinstance(desc.get('description'), str) or
                not isinstance(desc.get('media'), list) or
                any(not isinstance(media, dict) for media in desc['media'])):
            return None
        record = parse_record('tencent', raw, record_id, owner)
        if record is None:
            return None
        records.append(record)
    return records


def _tc_draft_diagnostic(kind, status, body, data, record=None):
    """Project fixed structural fields only; never serialize protocol values."""
    if kind not in ('save', 'read', 'readlist'):
        return
    body = body if isinstance(body, dict) else {}
    reply = data if isinstance(data, dict) else {}
    payload = reply.get('data') if isinstance(reply.get('data'), dict) else {}
    post = body.get('postReq') if isinstance(body.get('postReq'), dict) else {}
    base = payload.get('baseResp') if isinstance(payload.get('baseResp'), dict) else {}
    known = frozenset(('errCode', 'errMsg', 'message', 'data', 'baseResp', 'errcode', 'errmsg',
                       'objectId', 'object_id', 'postReq', 'objectDesc', 'desc', 'draftInfo', 'post',
                       'media', 'description', 'shortTitle', 'finderUsername', 'clientid', 'list', 'totalCount'))
    shape = {'event': 'tencent_draft_protocol', 'operation': kind,
             'http_status': status if type(status) is int else None,
             'json_object': isinstance(data, dict),
             'request_keys': sorted(known.intersection(body)),
             'post_req_keys': sorted(known.intersection(post)),
             'response_keys': sorted(known.intersection(reply)),
             'data_keys': sorted(known.intersection(payload)),
             'codes': {key: reply[key] for key in ('errCode',) if type(reply.get(key)) is int},
             'base_codes': {key: base[key] for key in ('errcode',) if type(base.get(key)) is int},
             'response_ok': _response_ok('tencent', data), 'parsed_record': record is not None,
             'record_id_present': bool(record and record.get('id'))}
    try:
        print('DRAFT_DIAGNOSTIC=' + json.dumps(shape), flush=True)
    except Exception:
        pass


def _request_body(request):
    try:
        data = request.post_data_json
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            return {k: v[0] for k, v in parse_qs(request.post_data or '').items() if len(v) == 1}
        except Exception:
            return {}


def _resource_ids(raw):
    """Only explicit upload-result fields and known result wrappers."""
    if not isinstance(raw, dict):
        return []
    result = _ids(*[_first(raw, key) for key in ('fileId', 'file_id', 'fileid', 'vid', 'video_id', 'cid', 'filename', 'url', 'httpsUrl', 'uri', 'key', 'upos_uri', 'upload_id')])
    for key in ('data', 'result', 'video', 'file', 'cover'):
        value = raw.get(key)
        if isinstance(value, dict):
            result.extend(_resource_ids(value))
    return sorted(set(result))


def _file_metadata(raw):
    if not isinstance(raw, dict):
        return None
    name = _first(raw, 'fileName', 'filename', 'file_name', 'name')
    size = _first(raw, 'fileSize', 'file_size', 'size', 'filesize')
    try:
        if name and size is not None:
            return str(name), int(size)
    except (ValueError, TypeError):
        pass
    for key in ('data', 'file', 'video'):
        found = _file_metadata(raw.get(key))
        if found:
            return found
    return None


def _upload_stage(url):
    """Allocation is never completion, even when its HTTP request succeeded."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(x in path for x in ('preupload', '/upload/pre', '/upload/token', '/upload/apply')):
        return 'allocated'
    chunks = {'partnumber', 'part_number', 'partindex', 'part_index', 'chunk', 'chunkindex', 'chunk_index'}
    if any(key.lower() in chunks for key in parse_qs(parsed.query)) and not any(x in path for x in ('/upload/finish', '/upload/complete', '/upload/commit')):
        return 'allocated'
    if any(x in path for x in ('/upload/finish', '/upload/complete', '/upload/commit', '/transend', '/upload/image', '/upload/video')):
        return 'complete'
    return None


# Exact hosts observed in the official client, including its assistance CDN.
# Do not accept arbitrary video.qq.com subdomains or redirect destinations.
TC_CDN_HOSTS = frozenset(('finder.video.qq.com', 'wxapp.tc.qq.com',
                          'finderassistancea.video.qq.com', 'finderassistancec.video.qq.com'))


def _tc_cdn_endpoint(url, allowed_hosts=None):
    parsed = urlparse(url)
    hosts = TC_CDN_HOSTS if allowed_hosts is None else allowed_hosts
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in hosts or parsed.username or parsed.password:
        return None
    if parsed.netloc != parsed.hostname:
        return None
    return {'/snsuploadbig': 'sns', '/applyuploaddfs': 'allocate',
            '/uploadpartdfs': 'part', '/completepartuploaddfs': 'finish'}.get(parsed.path)


def _tc_resource_aliases(value):
    if not isinstance(value, str) or not value:
        return []
    from uploader.tc_resource import tc_resource_identity
    identity = tc_resource_identity(value)
    if identity is not None:
        return [identity]
    parsed = urlparse(value)
    if parsed.hostname not in TC_CDN_HOSTS or parsed.scheme not in ('http', 'https') or parsed.netloc != parsed.hostname or not parsed.path or parsed.username or parsed.password or parsed.fragment:
        return []
    ids = [value]
    # This is the exact replacement in the official client, not a guessed ID.
    if value.startswith('http://wxapp.tc.qq.com/'):
        ids.append(value.replace('http://wxapp.tc.qq.com', 'https://finder.video.qq.com', 1))
    elif value.startswith('https://finder.video.qq.com/'):
        ids.append(value.replace('https://finder.video.qq.com', 'http://wxapp.tc.qq.com', 1))
    return sorted(set(ids))


def _tc_stored_ids(*values):
    """Independently map signed saved/composer URLs to the same safe identity."""
    from uploader.tc_resource import tc_resource_identity
    return sorted(set(tc_resource_identity(value) or value for value in _ids(*values)))


def _tc_query(text):
    pairs = parse_qs(text, keep_blank_values=True)
    return {key: values[0] for key, values in pairs.items()} if all(len(values) == 1 for values in pairs.values()) else None


def _tc_cdn_query(text):
    # The official SDK concatenates opaque UploadID directly into a URL.
    # Literal '+' belongs to that ID; it is not form-encoded whitespace.
    # X-Arguments is a different encoding and continues to use _tc_query.
    return _tc_query(text.replace('+', '%2B'))


def _tc_scalar(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and all(ord(char) >= 32 for char in value)


def _tc_metadata(fields, size_key):
    if not isinstance(fields, dict) or not _tc_scalar(fields.get('filekey')):
        return None
    size = fields.get(size_key)
    if not isinstance(size, str) or not re.fullmatch(r'[1-9][0-9]*', size):
        return None
    return fields['filekey'], int(size)


def _tc_no_error(data):
    if not isinstance(data, dict):
        return False
    # Official allocation/finish schemas expose UploadID/DownloadURL, without
    # requiring a synthetic generic code=0. Explicit error codes still reject.
    return all(type(data[key]) is int and data[key] == 0 for key in
               ('retcode', 'X-Errno', 'errCode', 'code', 'errorcode') if key in data)


async def _tc_dfs_header_diagnostics(request):
    """Compare header availability without returning or recording their values."""
    def project(headers):
        arguments = next((value for key, value in headers.items()
                          if key.lower() == 'x-arguments'), '') if isinstance(headers, dict) else ''
        fields = _tc_query(arguments) if isinstance(arguments, str) else None
        return bool(arguments), bool(_tc_metadata(fields, 'filesize') and _tc_scalar(fields.get('taskid')))

    try:
        provisional = project(request.headers)
    except Exception:
        provisional = (False, False)
    actual, available = (False, False), False
    try:
        reader = getattr(request, 'all_headers', None)
        if callable(reader):
            headers = await asyncio.wait_for(reader(), timeout=1)
            actual, available = project(headers), isinstance(headers, dict)
    except Exception:
        pass
    return {'provisional_arguments_present': provisional[0],
            'provisional_metadata_valid': provisional[1],
            'actual_headers_available': available,
            'actual_arguments_present': actual[0], 'actual_metadata_valid': actual[1]}


def _tc_sns_fields(request):
    """Read only the known multipart fields; never persist the file body."""
    try:
        headers = request.headers
        content_type = next((value for key, value in headers.items() if key.lower() == 'content-type'), '')
        if not content_type.startswith('multipart/form-data'):
            return None
        body = request.post_data_buffer
        if not body or len(body) > 2 * 1024 * 1024:
            return None
        message = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
        wanted, fields = {'filekey', 'totalsize', 'fileuuid', 'rangestart', 'rangeend'}, {}
        for part in message.walk():
            name = part.get_param('name', header='content-disposition')
            if name not in wanted:
                continue
            if name in fields or part.get_filename():
                return None
            fields[name] = part.get_payload(decode=True).decode('utf-8')
        return fields if set(fields) == wanted else None
    except Exception:
        return None


def _tc_upload_exchange(response, data, observed_at, sns_fields=None, *, allowed_hosts=None):
    endpoint = _tc_cdn_endpoint(response.url, allowed_hosts)
    if not endpoint or not _tc_no_error(data):
        return None
    query = _tc_cdn_query(urlparse(response.url).query)
    if query is None:
        return None
    base = {'ids': [], 'request_ids': [], 'body_sha256': None, 'observed_at': observed_at,
            'tc_protocol': 'sns' if endpoint == 'sns' else 'dfs'}
    if endpoint == 'sns':
        if response.request.method != 'POST':
            return None
        fields = sns_fields if sns_fields is not None else _tc_sns_fields(response.request)
        metadata = _tc_metadata(fields, 'totalsize')
        if not metadata or not _tc_scalar(fields.get('fileuuid')):
            return None
        if not all(isinstance(fields.get(key), str) and re.fullmatch(r'[0-9]+', fields[key]) for key in ('rangestart', 'rangeend')):
            return None
        start, end = int(fields['rangestart']), int(fields['rangeend']) + 1
        if not 0 <= start < end <= metadata[1]:
            return None
        terminal = data.get('uploadfinish') is True or type(data.get('uploadfinish')) is int and data['uploadfinish'] == 1
        ids = _tc_resource_aliases(data.get('fileurl')) if terminal else []
        return {**base, 'stage': 'complete' if ids else 'allocated', 'ids': ids,
                'metadata': metadata, 'task_id': fields['fileuuid'], 'range': (start, end),
                'whole_body': start == 0 and end == metadata[1]}
    try:
        arguments = next((value for key, value in response.request.headers.items() if key.lower() == 'x-arguments'), '')
        fields = _tc_query(arguments)
    except Exception:
        return None
    metadata = _tc_metadata(fields, 'filesize')
    if not metadata or not _tc_scalar(fields.get('taskid')):
        return None
    base.update(metadata=metadata, task_id=fields['taskid'])
    body = _request_body(response.request)
    if endpoint == 'allocate':
        # Confirmation of an old UploadID is not a fresh allocation.
        if response.request.method != 'PUT' or query.get('UploadID') or not _tc_scalar(data.get('UploadID')):
            return None
        count, ends = body.get('BlockSum'), body.get('BlockPartLength')
        if type(count) is not int or count <= 0 or not isinstance(ends, list) or len(ends) != count:
            return None
        if not all(type(end) is int and end > 0 for end in ends) or ends[-1] != metadata[1] or any(a >= b for a, b in zip(ends, ends[1:])):
            return None
        return {**base, 'stage': 'allocated', 'upload_id': data['UploadID'], 'plan': ends}
    upload_id = query.get('UploadID')
    if not _tc_scalar(upload_id):
        return None
    base['upload_id'] = upload_id
    if endpoint == 'part':
        part = query.get('PartNumber')
        if response.request.method != 'PUT' or not isinstance(part, str) or not re.fullmatch(r'[1-9][0-9]*', part):
            return None
        if type(data.get('X-Errno')) is not int or data['X-Errno'] != 0 or not _tc_scalar(data.get('ETag')):
            return None
        return {**base, 'stage': 'allocated', 'part_number': int(part), 'etag': data['ETag'],
                'trans_flag': data.get('TransFlag')}
    parts, flag = body.get('PartInfo'), body.get('TransFlag')
    ids = _tc_resource_aliases(data.get('DownloadURL'))
    if response.request.method != 'POST' or not ids or not isinstance(parts, list) or not parts or not _tc_scalar(flag):
        return None
    if any(not isinstance(part, dict) or type(part.get('PartNumber')) is not int or part['PartNumber'] <= 0 or not _tc_scalar(part.get('ETag')) for part in parts):
        return None
    numbers = [part['PartNumber'] for part in parts]
    if numbers != list(range(1, len(parts) + 1)):
        return None
    return {**base, 'stage': 'complete', 'ids': ids, 'parts': [(part['PartNumber'], part['ETag']) for part in parts],
            'trans_flag': flag}


def _uploaded_file_hash(request):
    """Hash the observed upload body, without persisting it or any headers.

    A cropper can replace a selected cover with a new Blob. Metadata alone must
    not link that Blob to the original cover bytes.
    """
    try:
        body = request.post_data_buffer
        if not body:
            return None
        content_type = request.headers.get('content-type', '')
        if content_type.startswith('multipart/form-data'):
            message = BytesParser(policy=policy.default).parsebytes(('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
            files = [part.get_payload(decode=True) for part in message.walk() if part.get_filename()]
            return hashlib.sha256(files[0]).hexdigest() if len(files) == 1 and files[0] is not None else None
        return hashlib.sha256(body).hexdigest()
    except Exception:
        return None


class DraftEvidenceSession:
    def __init__(self, page, platform, expected, account_reader=None):
        self.page, self.platform = page, platform_id(platform)
        self.expected = json.loads(json.dumps(expected or {}))
        self.account_reader = account_reader
        self.started_at = time.time()
        self.baseline = {}
        self.files, self.exchanges, self.saves, self.reads, self.tasks = [], [], [], [], set()
        self.assets = {}
        self.issues = []
        self.account = {}
        self.closed = False
        self.action_at = None
        self.reopen_at = None
        self.handler = self._response
        self.xhs_observer = None
        self.tc_cover_observer = None
        self.tc_processing_observer = None
        self.ks_observer = None
        self.bili_observer = None
        self.dy_observer = None
        self.dy_cover_observer = None
        self.tc_cdn_hosts = TC_CDN_HOSTS
        self._checkpoint_links = []
        self._checkpoint_baseline_hashes = None
        self._checkpoint_origin_at = None
        self._checkpoint_expires_at = None
        self._checkpoint_restored_at = None

    async def initialize(self):
        for key in IDENTITY_KEYS:
            if key not in self.expected or (key != 'cover_sha256' and not self.expected.get(key)):
                self.issues.append('expected_' + key + '_missing')
        if self.expected.get('platform_id') != self.platform:
            self.issues.append('expected_platform_mismatch')
        if not isinstance(self.expected.get('platform_fields'), dict):
            self.issues.append('platform_fields_missing')
        else:
            fields = self.expected['platform_fields']
            for key in ('title', 'description', 'tags'):
                if key not in fields:
                    self.issues.append('platform_field_' + key + '_missing')
            # Hash the fields the platform actually stores (KS/TC have one
            # caption), using the same contract as material preflight.
            from workbench_model import platform_content_fields
            content = platform_content_fields(self.platform, fields)
            digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if self.expected.get('content_sha256') != digest:
                self.issues.append('content_fingerprint_mismatch')
        if not isinstance(self.expected.get('account'), dict) or not self.expected['account'].get('identity'):
            self.issues.append('expected_account_missing')
        for key in ('video', 'cover', 'cover_landscape', 'cover_portrait'):
            # Original payload may retain a common cover which the platform
            # mapping replaced with portrait/landscape fields. Only upload the
            # mapped cover paths belong to this platform operation.
            path = self.expected.get('video') if key == 'video' else self.expected.get('platform_fields', {}).get(key)
            if not path:
                continue
            try:
                asset = await asyncio.to_thread(_sha_file, path)
                asset['kind'] = 'video' if key == 'video' else 'cover'
                self.assets[key] = asset
                wanted = self.expected.get('video_sha256') if key == 'video' else (self.expected.get('cover_sha256') or {}).get(key)
                if wanted != asset['sha256']:
                    self.issues.append(key + '_fingerprint_mismatch')
            except Exception:
                self.issues.append(key + '_not_readable')
        if 'video' not in self.assets:
            self.issues.append('video_file_missing')
        self.baseline = {r['key']: r['serialized'] for r in await read_local_records(self.page, self.platform)}
        self.account = await self.read_account()
        self.page.on('response', self.handler)
        if not getattr(self.page, '_sau_file_binding', False):
            page = self.page
            async def selected(source, metadata):
                current = getattr(page, '_sau_draft_evidence', None)
                if current and not current.closed and isinstance(metadata, dict) and metadata.get('selected_at', 0) >= current.started_at:
                    current.files.append(metadata)
            await self.page.expose_binding('__sauDraftSelected', selected)
            await self.page.add_init_script(FILE_OBSERVER_SCRIPT)
            self.page._sau_file_binding = True
        for frame in self.page.frames:
            try:
                await evaluate_main(frame, FILE_OBSERVER_SCRIPT)
            except Exception:
                pass
        if self.platform == 'douyin':
            from uploader.dy_evidence import install_dy_observer
            await install_dy_observer(self)
        elif self.platform == 'xiaohongshu':
            from uploader.xhs_evidence import install_xhs_observer
            await install_xhs_observer(self)
        elif self.platform == 'tencent':
            from uploader.tc_cover_evidence import install_tc_cover_observer
            await install_tc_cover_observer(self)
            from uploader.tc_processing import install_tc_processing_observer
            await install_tc_processing_observer(self)
        elif self.platform == 'kuaishou':
            from uploader.ks_evidence import install_ks_observer
            await install_ks_observer(self)
        elif self.platform == 'bilibili':
            from uploader.bili_evidence import install_bili_observer
            await install_bili_observer(self)

    async def read_account(self):
        if self.account_reader is None:
            return {'verified': False, 'platform': self.platform}
        try:
            signature = inspect.signature(self.account_reader)
            result = self.account_reader() if len(signature.parameters) == 0 else self.account_reader(self.page, self.platform)
            result = await result if inspect.isawaitable(result) else result
            if not isinstance(result, dict):
                return {'verified': False, 'platform': self.platform}
            return {k: result[k] for k in ('verified', 'identity', 'uid', 'platform') if k in result}
        except Exception:
            return {'verified': False, 'platform': self.platform}

    def _response(self, response):
        if self.closed:
            return
        task = asyncio.create_task(self._observe(response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _observe(self, response):
        observed_at = time.time()
        if self.platform == 'bilibili':
            # Only the native CDP transport owns Bili request time/frame proof.
            # Generic responses must not synthesize a saved record from a body.
            return
        if self.ks_observer is not None:
            from uploader.ks_evidence import ks_endpoint
            if ks_endpoint(response.url):
                try:
                    ks_data = await response.json()
                except Exception:
                    ks_data = None
                await self.ks_observer.observe(response, ks_data, observed_at)
                return
        if self.tc_processing_observer is not None:
            from uploader.tc_processing import processing_endpoint
            if processing_endpoint(response.url):
                try:
                    processing_data = await response.json()
                except Exception:
                    processing_data = None
                await self.tc_processing_observer.observe(response, processing_data, observed_at)
                return
        kind = endpoint_kind(self.platform, response.url)
        stage = _upload_stage(response.url)
        tc_endpoint = _tc_cdn_endpoint(response.url, self.tc_cdn_hosts) if self.platform == 'tencent' else None
        sns_fields = None
        # Structural diagnostics for the known official upload-finish reply.
        # Never print request values, resource IDs, content, cookies or URLs.
        finish_probe = self.platform == 'tencent' and any(urlparse(response.url).path.lower().endswith(x) for x in ('/normaluploadfinish', '/snsuploadbig', '/completepartuploaddfs'))
        if finish_probe:
            shape = {'event': 'tencent_upload_finish', 'endpoint': urlparse(response.url).path.rsplit('/', 1)[-1], 'http_status': response.status}
            shape['endpoint_allowed'] = tc_endpoint is not None
            if urlparse(response.url).path.endswith('/completepartuploaddfs'):
                shape.update(await _tc_dfs_header_diagnostics(response.request))
            try:
                probe = await response.json()
                shape['format'] = 'json'
                if isinstance(probe, dict):
                    shape['keys'] = sorted(k for k in probe if isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,48}', k))
                    shape['codes'] = {k: probe[k] for k in ('errCode', 'code', 'result', 'status', 'errorcode', 'retcode', 'X-Errno') if type(probe.get(k)) is int}
                    shape['data_keys'] = sorted(k for k in probe['data'] if isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,48}', k)) if isinstance(probe.get('data'), dict) else []
                if tc_endpoint == 'sns':
                    sns_fields = _tc_sns_fields(response.request)
                    request_fields = sns_fields or {}
                else:
                    request_fields = _request_body(response.request)
                shape['request_keys'] = sorted(k for k in request_fields if isinstance(k, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,48}', k))
            except Exception:
                shape['format'] = 'non_json'
                try:
                    shape['tags'] = sorted(set(re.findall(r'</?([A-Za-z_][A-Za-z_0-9]{0,48})(?:[\s>/])', (await response.text())[:65536])))
                except Exception:
                    pass
            try:
                print('DRAFT_DIAGNOSTIC=' + json.dumps(shape, ensure_ascii=False), flush=True)
            except Exception:
                pass
        if not kind and not stage and not tc_endpoint:
            return
        try:
            if not 200 <= response.status < 300:
                if self.platform == 'tencent' and kind:
                    _tc_draft_diagnostic(kind, response.status, _request_body(response.request), None)
                return
            data = await response.json()
        except Exception:
            if self.platform == 'tencent' and kind:
                _tc_draft_diagnostic(kind, response.status, _request_body(response.request), None)
            return
        if tc_endpoint:
            exchange = _tc_upload_exchange(response, data, observed_at, sns_fields, allowed_hosts=self.tc_cdn_hosts)
            if exchange:
                is_cover = any(asset['kind'] == 'cover' and exchange['metadata'] == (asset['name'], asset['size'])
                               for asset in self.assets.values())
                if self.tc_cover_observer is not None:
                    is_cover = is_cover or self.tc_cover_observer.accepts_upload_metadata(exchange['metadata'])
                if is_cover and (exchange.get('whole_body') or 'part_number' in exchange):
                    exchange['body_sha256'] = _uploaded_file_hash(response.request)
                self.exchanges.append(exchange)
            return
        body = _request_body(response.request)
        query = {k: v[0] for k, v in parse_qs(urlparse(response.url).query).items() if len(v) == 1}
        if kind:
            if not _response_ok(self.platform, data):
                if self.platform == 'tencent':
                    _tc_draft_diagnostic(kind, response.status, body, data)
                return
            value = data.get('data', data)
            if not isinstance(value, dict):
                if self.platform == 'tencent':
                    _tc_draft_diagnostic(kind, response.status, body, data)
                return
            if self.platform == 'tencent' and kind == 'readlist':
                records = _tc_draft_list_records(data.get('data'), self.account.get('uid'))
                matched = None
                # A list read is positive evidence only after the normal UI
                # reopen started, for this session's exact saved record.
                saved = next((item for item in reversed(self.saves)
                              if field_match(self.platform, item, self.expected) and
                              item.get('owner') == self.account.get('uid')), None)
                if (records is not None and saved and self.reopen_at is not None and
                        observed_at >= self.reopen_at):
                    matches = [item for item in records if item['id'] == saved['id'] and
                               item['owner'] == self.account.get('uid') and
                               field_match(self.platform, item, self.expected)]
                    if len(matches) == 1:
                        matched = matches[0]
                        matched.update(observed_at=observed_at, fresh=False,
                                       request_method=response.request.method)
                        self.reads.append(matched)
                _tc_draft_diagnostic(kind, response.status, body, data, matched)
                return
            record_id = _first(value, 'draft_id', 'draftId', 'id', 'objectId') or _first(body, 'draft_id', 'draftId', 'id', 'objectId') or _first(query, 'draft_id', 'draftId', 'id', 'objectId')
            raw = body if kind == 'save' else value
            # Some normal draft replies wrap the actual record in draftInfo.
            if kind == 'read' and isinstance(value.get('draftInfo'), dict):
                raw = value['draftInfo']
            record = parse_record(self.platform, raw, record_id, self.account.get('uid'))
            if record:
                record.update(observed_at=observed_at, fresh=kind == 'save', request_method=response.request.method)
                (self.saves if kind == 'save' else self.reads).append(record)
            if self.platform == 'tencent':
                _tc_draft_diagnostic(kind, response.status, body, data, record)
            return
        # Strict numeric success codes. HTTP 200 containing an error is not an upload.
        okay = any(type(data.get(key)) is int and data[key] == wanted for key, wanted in (('code', 0), ('result', 1), ('errCode', 0), ('status', 0))) if isinstance(data, dict) else False
        if not okay:
            return
        # A 200/code=0 response from a generic upload/transcode URL may still
        # be a chunk or progress acknowledgement. Require a terminal marker,
        # except the explicitly observed Kuaishou upload-finish operation.
        value = data.get('data', {}) if isinstance(data, dict) else {}
        terminal = (data.get('complete') is True or
                    isinstance(value, dict) and (value.get('complete') is True or value.get('status') in ('uploadSuccess', 'completed', 'success')))
        ks_finish = self.platform == 'kuaishou' and urlparse(response.url).path in (
            '/rest/cp/works/v2/video/pc/upload/finish', '/rest/cp/works/v3/video/pc/upload/finish')
        if stage == 'complete' and not terminal and not ks_finish:
            stage = 'allocated'
        ids = _resource_ids(data)
        if not ids and ks_finish:
            # A successful normal finish reply may only contain result=1.
            # Its own observed request still names the exact allocated fileId.
            ids = _resource_ids(body)
        if not ids:
            return
        metadata = _file_metadata(body) or _file_metadata(query) or _file_metadata(data)
        cover_metadata = metadata and any(asset['kind'] == 'cover' and metadata == (asset['name'], asset['size'])
                                          for asset in self.assets.values())
        self.exchanges.append({'stage': stage, 'ids': ids, 'metadata': metadata, 'request_ids': _resource_ids(body),
                               'body_sha256': _uploaded_file_hash(response.request) if cover_metadata else None,
                               'observed_at': observed_at})

    async def drain(self):
        pending = tuple(self.tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self.xhs_observer is not None:
            await self.xhs_observer.drain()
        if self.tc_cover_observer is not None:
            await self.tc_cover_observer.drain()
        if self.tc_processing_observer is not None:
            await self.tc_processing_observer.drain()
        if self.ks_observer is not None:
            await self.ks_observer.drain()
        if self.bili_observer is not None:
            await self.bili_observer.drain()
        if self.dy_observer is not None:
            await self.dy_observer.drain()
        if self.dy_cover_observer is not None:
            await self.dy_cover_observer.drain()

    def _checkpoint_identity(self):
        return _checkpoint_expected_identity(self.expected, self.platform)

    async def _checkpoint_assets(self):
        assets = {}
        fields = self.expected.get('platform_fields', {})
        for role in _ASSET_ROLES:
            path = self.expected.get('video') if role == 'video' else fields.get(role)
            if not path:
                continue
            try:
                asset = await asyncio.to_thread(_sha_file, path)
            except Exception:
                raise CheckpointError('checkpoint_asset_unreadable') from None
            asset['kind'] = 'video' if role == 'video' else 'cover'
            wanted = self.expected.get('video_sha256') if role == 'video' else self.expected.get('cover_sha256', {}).get(role)
            if asset['sha256'] != wanted or asset != self.assets.get(role):
                raise CheckpointError('checkpoint_asset_mismatch')
            assets[role] = asset
        if 'video' not in assets or set(assets) - {'video'} != set(self.expected.get('cover_sha256', {})):
            raise CheckpointError('checkpoint_asset_mismatch')
        return assets

    async def capture_tc_cdn_snapshot(self):
        if self.platform != 'tencent' or self.closed or getattr(self.page, '_sau_draft_evidence', None) is not self:
            return False
        from uploader.tc_cdn import capture_tc_cdn_hosts
        hosts = await capture_tc_cdn_hosts(self.page)
        if not hosts or self.closed or getattr(self.page, '_sau_draft_evidence', None) is not self:
            return False
        self.tc_cdn_hosts = TC_CDN_HOSTS | hosts
        print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'tencent_cdn_snapshot', 'host_count': len(hosts)}), flush=True)
        return True

    async def _checkpoint_account(self):
        current = await self.read_account()
        wanted = self.expected.get('account', {})
        if (current.get('verified') is not True or current.get('platform') != self.platform or
                not _checkpoint_string(current.get('identity')) or current.get('identity') != wanted.get('identity') or
                (wanted.get('uid') is not None and str(current.get('uid')) != str(wanted['uid'])) or current != self.account):
            raise CheckpointError('checkpoint_account_mismatch')
        if 'uid' in current and (isinstance(current['uid'], bool) or not _checkpoint_string(str(current['uid']))):
            raise CheckpointError('checkpoint_account_mismatch')
        return current

    def _checkpoint_ready(self):
        if self.closed or self.issues:
            raise CheckpointError('checkpoint_session_unverified')
        if self.action_at is not None:
            # A previous save may already have succeeded. Resuming this phase
            # must never turn an unknown save result into a repeated click.
            raise CheckpointError('checkpoint_save_already_started')

    async def export_checkpoint(self, *, seal_key, ttl_seconds=CHECKPOINT_MAX_AGE):
        """Export only verified uploads before explicit save, sealed locally.

        Caller persists the return value privately and keeps seal_key separately.
        No raw responses, file paths, captions, cookies or upload permits leave
        this interface. This does not assert that a draft was saved or reopened.
        """
        key = _checkpoint_key(seal_key)
        self._checkpoint_ready()
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= CHECKPOINT_MAX_AGE:
            raise CheckpointError('checkpoint_ttl_invalid')
        await self.drain()
        self._checkpoint_ready()
        identity = self._checkpoint_identity()
        assets = await self._checkpoint_assets()
        account = await self._checkpoint_account()
        links = _checkpoint_links(self.upload_links(), filter_resources=True)
        videos = [link for link in links if link['kind'] == 'video' and link['sha256'] == identity['video_sha256']]
        if len(videos) != 1:
            raise CheckpointError('checkpoint_video_upload_unverified')
        allowed = {('video', identity['video_sha256'])} | {('cover', sha) for sha in identity['cover_sha256'].values()}
        links = [link for link in links if (link['kind'], link['sha256']) in allowed]
        baseline = self._checkpoint_baseline_hashes
        if baseline is None:
            baseline = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in self.baseline.items()}
        if len(baseline) > 512 or not all(_checkpoint_string(k) and _checkpoint_digest(v) for k, v in baseline.items()):
            raise CheckpointError('checkpoint_baseline_invalid')
        now = time.time()
        origin = self._checkpoint_origin_at if self._checkpoint_origin_at is not None else self.started_at
        expires = min(origin + ttl_seconds, self._checkpoint_expires_at or origin + CHECKPOINT_MAX_AGE)
        if expires <= now:
            raise CheckpointError('checkpoint_expired')
        payload = {'phase': 'uploaded_not_saved', 'identity': identity, 'account': account, 'assets': assets,
                   'baseline_hashes': baseline, 'upload_links': links, 'origin_at': origin,
                   'exported_at': now, 'expires_at': expires}
        encoded = _checkpoint_json(payload)
        checkpoint = {'checkpoint_version': CHECKPOINT_VERSION, 'payload': json.loads(encoded),
                      'mac': hmac.new(key, encoded, hashlib.sha256).hexdigest()}
        validate_checkpoint(checkpoint, seal_key=key, expected=self.expected, platform=self.platform)
        return checkpoint

    async def restore_checkpoint(self, checkpoint, *, seal_key, media_reader):
        """Restore an attested upload only if the unique current composer agrees.

        media_reader reads the platform's existing composer and returns
        {source: 'platform_composer', video_count: 1, video_ids: [stable IDs]}.
        Its DOM IDs are confirmation of the sealed upload, never upload proof.
        Fresh initialize() and the exact original expected payload are required.
        All validation precedes mutation; save/reopen/account gates remain normal.
        """
        key = _checkpoint_key(seal_key)
        self._checkpoint_ready()
        if self._checkpoint_restored_at is not None or self.files or self.exchanges or self.saves or self.reads or self.tasks:
            raise CheckpointError('checkpoint_restore_session_not_fresh')
        payload = validate_checkpoint(checkpoint, seal_key=key, expected=self.expected, platform=self.platform)
        now = time.time()
        times = [payload[name] for name in ('origin_at', 'exported_at', 'expires_at')]
        if not all(type(value) in (int, float) and math.isfinite(value) for value in times):
            raise CheckpointError('checkpoint_time_invalid')
        origin, exported, expires = times
        if not 0 < origin <= exported < expires <= origin + CHECKPOINT_MAX_AGE or exported > now + 60 or origin > self.started_at + 60:
            raise CheckpointError('checkpoint_time_invalid')
        if expires <= now:
            raise CheckpointError('checkpoint_expired')
        identity = self._checkpoint_identity()
        if payload['identity'] != identity:
            raise CheckpointError('checkpoint_task_mismatch')
        assets = await self._checkpoint_assets()
        if payload['assets'] != assets:
            raise CheckpointError('checkpoint_asset_mismatch')
        account = await self._checkpoint_account()
        if payload['account'] != account:
            raise CheckpointError('checkpoint_account_mismatch')
        baseline = payload['baseline_hashes']
        if not isinstance(baseline, dict) or len(baseline) > 512 or not all(_checkpoint_string(k) and _checkpoint_digest(v) for k, v in baseline.items()):
            raise CheckpointError('checkpoint_baseline_invalid')
        links = _checkpoint_links(payload['upload_links'])
        allowed = {('video', identity['video_sha256'])} | {('cover', sha) for sha in identity['cover_sha256'].values()}
        if any((link['kind'], link['sha256']) not in allowed for link in links):
            raise CheckpointError('checkpoint_links_invalid')
        videos = [link for link in links if link['kind'] == 'video' and link['sha256'] == identity['video_sha256']]
        if len(videos) != 1 or not callable(media_reader):
            raise CheckpointError('checkpoint_video_upload_unverified')
        try:
            signature = inspect.signature(media_reader)
            media = media_reader() if len(signature.parameters) == 0 else media_reader(self.page, self.platform)
            media = await media if inspect.isawaitable(media) else media
        except Exception:
            raise CheckpointError('checkpoint_composer_unverified') from None
        if (not isinstance(media, dict) or media.get('source') != 'platform_composer' or
                type(media.get('video_count')) is not int or media['video_count'] != 1 or
                not isinstance(media.get('video_ids'), list) or not 1 <= len(media['video_ids']) <= 32 or
                not all(_checkpoint_resource(value) for value in media['video_ids']) or
                not set(media['video_ids']).intersection(videos[0]['ids'])):
            raise CheckpointError('checkpoint_composer_unverified')
        # Re-check after callbacks: an account switch, new selection or explicit
        # save during the probe cannot receive a stale restored attestation.
        await self._checkpoint_account()
        self._checkpoint_ready()
        if self.files or self.exchanges or self.saves or self.reads or self.tasks:
            raise CheckpointError('checkpoint_restore_session_not_fresh')
        if time.time() >= expires:
            raise CheckpointError('checkpoint_expired')
        self._checkpoint_links = links
        self._checkpoint_baseline_hashes = dict(baseline)
        self._checkpoint_origin_at, self._checkpoint_expires_at = origin, expires
        self._checkpoint_restored_at = time.time()
        return {'restored': True, 'video_upload_verified': True, 'save_verified': False}

    def _tc_selected_asset(self, exchange):
        matching = [selection for selection in self.files
                    if self.started_at <= selection.get('selected_at', -1) <= exchange['observed_at'] and
                    exchange['metadata'] == (selection.get('name'), selection.get('size'))]
        if len(matching) != 1:
            return None
        selected = matching[0]
        digest = selected.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            return None
        assets = [asset for asset in self.assets.values() if asset.get('sha256') == digest and
                  (asset.get('name'), asset.get('size')) == exchange['metadata']]
        kinds = {asset.get('kind') for asset in assets}
        return (selected, kinds.pop()) if len(kinds) == 1 else None

    def _tc_upload_links(self):
        """Task-scoped observed upload chains, never a union by equal SHA256.

        Video fingerprints come from the complete current File selection.
        Native UI allocation metadata binds it to the observed upload task;
        this does not attest a second server-side full-file digest. Covers
        retain the stronger original-bytes gate against cropper replacement.
        """
        links = []
        sns = {}
        dfs = []
        for item in self.exchanges:
            if item.get('tc_protocol') == 'sns':
                sns.setdefault(item['task_id'], []).append(item)
            elif item.get('tc_protocol') == 'dfs':
                dfs.append(item)
        for group in sns.values():
            first = min(group, key=lambda item: item['observed_at'])
            selected_asset = self._tc_selected_asset(first)
            if not selected_asset or any(item['metadata'] != first['metadata'] for item in group):
                continue
            selected, kind = selected_asset
            cursor, coverage = 0, True
            for start, end in sorted({item['range'] for item in group}):
                if start != cursor:
                    coverage = False
                    break
                cursor = end
            if not coverage or cursor != first['metadata'][1]:
                continue
            terminal = [item for item in group if item['stage'] == 'complete']
            resources = {tuple(item['ids']) for item in terminal}
            if len(resources) != 1 or kind == 'cover' and not any(
                    item.get('whole_body') and item.get('body_sha256') == selected['sha256'] for item in terminal):
                continue
            links.append({'kind': kind, 'sha256': selected['sha256'], 'ids': list(resources.pop()), 'complete': True})
        allocations = [item for item in dfs if 'plan' in item]
        seen = set()
        for allocation in allocations:
            task, upload_id = allocation['task_id'], allocation['upload_id']
            task_allocations = [item for item in allocations if item['task_id'] == task]
            fingerprints = {(item['upload_id'], item['metadata'], tuple(item['plan'])) for item in task_allocations}
            if len(fingerprints) != 1 or (task, upload_id) in seen:
                continue
            seen.add((task, upload_id))
            allocation = min(task_allocations, key=lambda item: item['observed_at'])
            selected_asset = self._tc_selected_asset(allocation)
            if not selected_asset:
                continue
            selected, kind = selected_asset
            finishes = [item for item in dfs if item['stage'] == 'complete' and item['task_id'] == task and
                        item['upload_id'] == upload_id and item['metadata'] == allocation['metadata'] and
                        item['observed_at'] >= allocation['observed_at'] and len(item['parts']) == len(allocation['plan'])]
            resources = set()
            for finish in finishes:
                chunks = [item for item in dfs if 'part_number' in item and item['task_id'] == task and
                          item['upload_id'] == upload_id and item['metadata'] == allocation['metadata'] and
                          allocation['observed_at'] <= item['observed_at'] <= finish['observed_at']]
                observed = {}
                for chunk in chunks:
                    observed.setdefault(chunk['part_number'], set()).add(chunk['etag'])
                if observed != {number: {etag} for number, etag in finish['parts']}:
                    continue
                if not any(chunk.get('trans_flag') == finish['trans_flag'] for chunk in chunks):
                    continue
                if kind == 'cover' and (len(allocation['plan']) != 1 or not any(
                        chunk.get('body_sha256') == selected['sha256'] for chunk in chunks)):
                    continue
                resources.add(tuple(finish['ids']))
            if len(resources) == 1:
                links.append({'kind': kind, 'sha256': selected['sha256'], 'ids': list(resources.pop()), 'complete': True})
        return links

    def upload_links(self):
        links = self._tc_upload_links() if self.platform == 'tencent' else []
        allocated = []
        for exchange in sorted(self.exchanges, key=lambda item: item['observed_at']):
            if exchange.get('tc_protocol'):
                continue
            matching = []
            for selection in self.files:
                if selection['selected_at'] > exchange['observed_at']:
                    continue
                if exchange['metadata'] == (selection.get('name'), selection.get('size')):
                    matching.append(selection)
            related = [item for item in allocated if set(item['ids']).intersection(exchange['request_ids'] + exchange['ids'])]
            if len(matching) == 1:
                selected = matching[0]
            else:
                selections = {id(item['file']): item['file'] for item in related}
                selected = next(iter(selections.values())) if len(selections) == 1 else None
            if selected is None or any(item['file'] is not selected for item in related):
                continue
            assets = [asset for asset in self.assets.values() if asset['sha256'] == selected.get('sha256') and asset['name'] == selected.get('name') and asset['size'] == selected.get('size')]
            kinds = {asset['kind'] for asset in assets}
            if len(kinds) != 1:
                continue
            if kinds == {'cover'} and exchange.get('body_sha256') != selected['sha256']:
                # A transformed cover needs its own evidenced transformation
                # chain; generic success responses cannot stand in for it.
                continue
            ids = sorted(set(exchange['ids'] + exchange['request_ids']))
            for allocation in related:
                ids = sorted(set(ids + allocation['ids']))
            allocated.append({'file': selected, 'ids': ids})
            if exchange['stage'] == 'complete':
                links.append({'kind': kinds.pop(), 'sha256': selected['sha256'], 'ids': ids, 'complete': True})
        if self.xhs_observer is not None:
            links.extend(self.xhs_observer.upload_links())
        if self.tc_cover_observer is not None:
            links.extend(self.tc_cover_observer.upload_links())
        if self.ks_observer is not None:
            links.extend(self.ks_observer.upload_links())
        if self.bili_observer is not None:
            links.extend(self.bili_observer.upload_links())
        if self.dy_observer is not None:
            links.extend(self.dy_observer.upload_links())
        if self.dy_cover_observer is not None:
            links.extend(self.dy_cover_observer.upload_links())
        if self._checkpoint_links:
            # A new video selection starts a new upload. Even identical bytes
            # cannot silently inherit the preceding allocation/resource proof.
            video = self.assets.get('video', {})
            replaced = any(selection.get('selected_at', 0) >= self._checkpoint_restored_at and
                           (str(selection.get('type', '')).startswith('video/') or
                            (selection.get('name'), selection.get('size')) == (video.get('name'), video.get('size')) or
                            str(selection.get('name', '')).lower().endswith(('.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v')))
                           for selection in self.files)
            if not replaced:
                links.extend(json.loads(json.dumps(self._checkpoint_links)))
        if self.tc_processing_observer is not None:
            links = self.tc_processing_observer.augment_links(links)
        return links

    async def candidate(self):
        await self.drain()
        if self.platform == 'bilibili':
            return await self.bili_observer.candidate() if self.bili_observer is not None else None
        if self.platform in ('douyin', 'xiaohongshu'):
            candidates = []
            for row in await read_local_records(self.page, self.platform):
                if self._checkpoint_baseline_hashes is not None:
                    unchanged = self._checkpoint_baseline_hashes.get(row['key']) == hashlib.sha256(row['serialized'].encode()).hexdigest()
                else:
                    unchanged = self.baseline.get(row['key']) == row['serialized']
                if unchanged:
                    continue
                draft_id = row['key']
                record = parse_record(self.platform, row['raw'], draft_id, row['owner'])
                if record and field_match(self.platform, record, self.expected):
                    record.update(fresh=True, frame_url=row['frame_url'], storage_key=row['key'])
                    candidates.append(record)
            # Same-title entries from different records must not select arbitrarily.
            return candidates[0] if len(candidates) == 1 else None
        candidates = [r for r in self.saves if field_match(self.platform, r, self.expected)]
        return candidates[-1] if candidates else None

    async def reopened_record(self, candidate):
        await self.drain()
        if self.platform == 'bilibili':
            return await self.bili_observer.reopened_record(candidate) if self.bili_observer is not None else None
        if self.platform in ('douyin', 'xiaohongshu'):
            fresh = await self.candidate()
            return fresh if fresh and fresh['id'] == candidate['id'] else None
        reads = [r for r in self.reads if self.reopen_at is not None and r['observed_at'] >= self.reopen_at and r['id'] == candidate['id']]
        return reads[-1] if reads else None

    async def close(self):
        if not self.closed:
            self.closed = True
            self.page.remove_listener('response', self.handler)
            await self.drain()
            if self.xhs_observer is not None:
                await self.xhs_observer.close()
            if self.tc_cover_observer is not None:
                await self.tc_cover_observer.close()
            if self.tc_processing_observer is not None:
                await self.tc_processing_observer.close()
            if self.ks_observer is not None:
                await self.ks_observer.close()
            if self.bili_observer is not None:
                await self.bili_observer.close()
            if self.dy_cover_observer is not None:
                await self.dy_cover_observer.close()
            if self.dy_observer is not None:
                await self.dy_observer.close()
        if getattr(self.page, '_sau_draft_evidence', None) is self:
            self.page._sau_draft_evidence = None


async def begin_draft_verification(page, platform, expected, account_reader=None):
    previous = getattr(page, '_sau_draft_evidence', None)
    if previous:
        await previous.close()
    session = DraftEvidenceSession(page, platform, expected, account_reader)
    page._sau_draft_evidence = session
    await session.initialize()
    return session


async def end_draft_verification(page):
    session = getattr(page, '_sau_draft_evidence', None)
    if session:
        await session.close()
