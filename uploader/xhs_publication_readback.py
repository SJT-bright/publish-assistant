"""Read this fresh XHS composer without inventing a reopened-draft receipt.

The typed current Vue player must consume the exact server ID delivered by
this task's native File SDK upload. A Blob URL by itself proves nothing.
"""
import copy
import json
import math
from urllib.parse import urlsplit

from uploader.draft_evidence import IDENTITY_KEYS
from uploader.native_read import NativeReadError, canonical_root_evaluate
from uploader.xhs_readback import (XHS_READBACK_PROJECTION, XHS_VIDEO_OWNER_PROJECTION,
                                  _BOOL_KEYS, _COUNT_KEYS, xhs_readback_expression)
from uploader.xiaohongshu_uploader.main import draft_xhs_description_plan


def _failed(reason):
    return {'valid': False, 'fields_ok': False, 'media_ok': False,
            'cover_ok': False, 'reason': reason}


def _official(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.netloc == 'creator.xiaohongshu.com'
                and parsed.path == '/publish/publish' and not parsed.fragment)
    except (TypeError, ValueError):
        return False


def _scope_current(page, session, observer, expected):
    account = getattr(session, 'account', None)
    wanted = expected.get('account')
    return (getattr(page, '_sau_draft_evidence', None) is session
            and session.page is page and not session.closed and not session.issues
            and session.platform == 'xiaohongshu' and session.expected == expected
            and expected.get('workflow_action') == 'publish'
            and all(key in expected and (key == 'cover_sha256' or bool(expected[key])) for key in IDENTITY_KEYS)
            and getattr(session, 'xhs_observer', None) is observer
            and observer.session is session and observer.page is page and not observer.closed
            and observer.scope.get('job_id') == expected['job_id']
            and observer.scope.get('receipt_nonce') == expected['receipt_nonce']
            and bool(observer.scope.get('observer_nonce'))
            and isinstance(account, dict) and account.get('verified') is True
            and account.get('platform') == 'xiaohongshu' and bool(account.get('identity'))
            and isinstance(wanted, dict) and wanted.get('identity') == account['identity']
            and (not wanted.get('uid') or str(wanted['uid']) == str(account.get('uid'))))


def _snapshot(session, observer, candidate):
    return copy.deepcopy((session.expected, session.account, session.assets, session.files,
                          observer.scope, observer.records, observer.cover_records,
                          observer.cover_status, observer.upload_links(), candidate))


def _current_native_record(session, observer, expected, candidate):
    records = list(observer.records.values())
    asset = session.assets.get('video', {})
    if len(records) != 1 or asset.get('kind') != 'video':
        return None
    record = records[0]
    if (any(record.get(key) != value for key, value in observer.scope.items())
            or record.get('uploader') not in ('video-uploader', 'not-transcode-video-uploader')
            or (record.get('name'), record.get('size'), record.get('sha256')) !=
                (asset.get('name'), asset.get('size'), expected.get('video_sha256'))
            or asset.get('sha256') != expected.get('video_sha256')
            or candidate.get('video_ids') != [record.get('file_id')]
            or candidate.get('video_count') != 1):
        return None
    selected = [row for row in session.files if isinstance(row, dict)
                and (str(row.get('type', '')).startswith('video/')
                     or (row.get('name'), row.get('size')) == (asset.get('name'), asset.get('size')))]
    if not selected or any(type(row.get('selected_at')) not in (int, float)
                           or not math.isfinite(row['selected_at']) for row in selected):
        return None
    latest = max(selected, key=lambda row: row['selected_at'])
    stamp, start, finish = latest['selected_at'], record.get('started_at'), record.get('completed_at')
    if (sum(row['selected_at'] == stamp for row in selected) != 1
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (start, finish, session.started_at))
            or not session.started_at <= stamp <= start + .1 or not start <= finish
            or (latest.get('name'), latest.get('size'), latest.get('sha256')) !=
               (asset.get('name'), asset.get('size'), asset.get('sha256'))):
        return None
    links = [row for row in observer.upload_links() if row.get('kind') == 'video']
    if links != [{'kind': 'video', 'sha256': expected['video_sha256'],
                  'ids': candidate['video_ids'], 'complete': True}]:
        return None
    return record


def _cover_bound(observer, expected, candidate):
    hashes = expected.get('cover_sha256')
    if not isinstance(hashes, dict):
        return False
    if not hashes:
        return candidate.get('cover_ids') == []
    links = observer.cover_upload_links()
    ids = set()
    for slot, digest in hashes.items():
        matching = [row for row in links if row.get('sha256') == digest and row.get('complete') is True]
        if len(matching) != 1 or len(matching[0].get('ids', [])) != 1:
            return False
        resource = matching[0]['ids'][0]
        if resource not in candidate.get('cover_slots', {}).get(slot, []):
            return False
        ids.add(resource)
    return ids == set(candidate.get('cover_ids', []))


def _expression(fields, candidate, observer, record):
    covers = candidate.get('cover_ids')
    if covers:
        field_expression = xhs_readback_expression(fields, candidate)
    else:
        # Empty covers are supported only for this fresh video-only reader;
        # the reopened-draft builder keeps requiring a selected cover ID.
        if (not isinstance(fields.get('title'), str) or not isinstance(fields.get('description'), str)
                or not isinstance(fields.get('tags'), list)
                or not all(isinstance(tag, str) and tag for tag in fields['tags'])):
            raise ValueError('fields_invalid')
        plan = draft_xhs_description_plan(fields['description'], fields['tags'])
        args = {key: plan[key] for key in ('plain', 'serialized', 'topics')}
        args.update(title=fields['title'], video_ids=candidate['video_ids'], cover_ids=[])
        field_expression = '(' + XHS_READBACK_PROJECTION + ')(' + json.dumps(args, ensure_ascii=False) + ')'
    plan = draft_xhs_description_plan(fields['description'], fields['tags'])
    args = {'scope': observer.browser_scope, 'uploader': record['uploader'],
            'video_ids': candidate['video_ids'], 'title': fields['title'], 'description': plan['serialized']}
    # Read Maps through their native methods; never invoke manager.getUploader,
    # uploader.upload, a Vue method, or a business-state getter.
    return ('(() => {const input=' + json.dumps(args, ensure_ascii=False) + ';'
            'const root=window.__sauXHSSDKObserver,manager=window._publisher?.uploader;'
            'const sdk_scope_current=!!(root?.api_version===4&&root.active&&manager&&root.manager===manager&&'
            'Object.keys(input.scope).every(key=>root.active[key]===input.scope[key]));'
            'let sdk_uploader_count=0;try{if(sdk_scope_current)'
            'for(const [uploader,entry] of Reflect.apply(Map.prototype.entries,root.uploaders,[]))'
            'if(entry.name===input.uploader&&entry.wrapper===uploader.upload)sdk_uploader_count++;}catch(_){}'
            'const sdk_uploader_current=sdk_uploader_count===1;'
            'return {sdk_scope_current,sdk_uploader_current,fields:' + field_expression + ',owner:('
            # Pinned official customParser serializes topic atoms, then the
            # modifyContent command stores innerText.trim() in state.desc
            # (bundle SHA d1e447e4..., Unicode offsets 761332/743020/63974).
            + XHS_VIDEO_OWNER_PROJECTION + ')({...input,description:input.description.trim()})};})()')


async def read_xhs_fresh_publication(page, expected, candidate, *, timeout=5.0):
    """Return fixed booleans and a reason; read once without editing or retrying."""
    try:
        session = getattr(page, '_sau_draft_evidence', None)
        observer = getattr(session, 'xhs_observer', None)
        if (session is None or observer is None or not isinstance(expected, dict)
                or not isinstance(candidate, dict) or not _scope_current(page, session, observer, expected)):
            return _failed('fresh_session_invalid')
        if not _official(page.url) or page.main_frame.url != page.url:
            return _failed('fresh_document_invalid')
        record = _current_native_record(session, observer, expected, candidate)
        if record is None or not _cover_bound(observer, expected, candidate):
            return _failed('fresh_native_upload_unverified')
        url, frame = page.url, page.main_frame
        before = _snapshot(session, observer, candidate)
        value = await canonical_root_evaluate(page, _expression(expected['platform_fields'], candidate, observer, record),
                                              timeout=timeout)
        if (not _scope_current(page, session, observer, expected) or page.url != url
                or page.main_frame is not frame or frame.url != url
                or _snapshot(session, observer, candidate) != before):
            return _failed('fresh_scope_changed')
        if (not isinstance(value, dict) or set(value) != {'sdk_scope_current', 'sdk_uploader_current', 'fields', 'owner'}
                or type(value['sdk_scope_current']) is not bool or type(value['sdk_uploader_current']) is not bool):
            return _failed('fresh_projection_invalid')
        fields, owner = value['fields'], value['owner']
        if (not isinstance(fields, dict) or set(fields) != _BOOL_KEYS | _COUNT_KEYS
                or any(type(fields[key]) is not bool for key in _BOOL_KEYS)
                or any(type(fields[key]) is not int or not 0 <= fields[key] <= 1024 for key in _COUNT_KEYS)
                or fields['valid'] is not True or fields['root_scope_valid'] is not True
                or not isinstance(owner, dict) or owner.get('valid') is not True
                or type(owner.get('container_scope_valid')) is not bool
                or any(type(owner.get(key)) is not int for key in
                       ('visible_video_count', 'source_consumer_count', 'fully_bound_blob_video_count'))):
            return _failed('fresh_projection_invalid')
        # The diagnostic owner reader is promoted only inside this fresh SDK
        # chain. The existing reopened reader still rejects an unbound Blob.
        media = (value['sdk_scope_current'] is True and value['sdk_uploader_current'] is True
                 and owner.get('abort_reason_code') == 'none'
                 and owner.get('container_scope_valid') is True
                 and owner.get('visible_video_count') == 1
                 and owner.get('source_consumer_count') == 1
                 and owner.get('fully_bound_blob_video_count') == 1)
        cover = not bool(expected['cover_sha256']) or fields['cover_exact_id_match_count'] > 0
        return {'valid': True, 'fields_ok': fields['fields_ok'] is True,
                'media_ok': media and cover, 'cover_ok': cover, 'reason': 'none'}
    except NativeReadError as error:
        return _failed(error.reason)
    except (AttributeError, KeyError, TypeError, ValueError):
        return _failed('fresh_arguments_invalid')
