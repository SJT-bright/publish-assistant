"""Task-scoped TC async processing readback; URLs never alias across reads.

Official 858.a1152686.js:74051 defines MMFinderHandleStatus_Succ=2;
755.151a1d93.js:44246 checks the matching success enum. 841:1936285 and
2287072 restore one row URL/metadata to File; 1939165 and 1786324 pass that
same File to the two preview levels and visible VIDEO. This proof creates no
new upload SHA: it retains the frozen original and attests its processed task.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field

from uploader.tc_processing import _canonical_dimensions, _file_size
from uploader.tc_resource import tc_resource_identity


_ASSET_ID = re.compile(r'(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\Z')


def _asset_id(value):
    # Official media.md5sum carries the native File.uuid. The fresh native
    # upload may use an exact lower-case GUID, not a file checksum. Retain
    # punctuation/case and never equate the GUID with a stripped 32-hex ID.
    return isinstance(value, str) and _ASSET_ID.fullmatch(value) is not None


_NATIVE_GUID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z')
_SERVER_ASSET_ID = re.compile(r'[a-f0-9]{32}\Z')


def _same_or_processed_asset_id(original, reopened):
    """Only the observed native-GUID -> independent server-ID stage.

    The caller must retain all frozen upload/allocation, same task/owner/read,
    target metadata and exact current native consumer gates. Neither ID is
    rewritten or assigned the other's resource or byte SHA.
    """
    before, after = original['uuid'], reopened['uuid']
    if before == after:
        return True
    return (_NATIVE_GUID.fullmatch(before) is not None and
            _SERVER_ASSET_ID.fullmatch(after) is not None and
            after != before.replace('-', '') and
            original['resource'] != reopened['resource'])


def canonical_media(value):
    if not isinstance(value, dict):
        return None
    url, uuid, task = value.get('url'), value.get('md5sum'), value.get('urlCdnTaskId')
    resource = tc_resource_identity(url)
    metadata = _canonical_dimensions({'width': value.get('width'), 'height': value.get('height'),
        'duration': value.get('videoPlayLen'), 'fileSize': value.get('fileSize')})
    if (not resource or not _asset_id(uuid) or
            not isinstance(task, str) or not 0 < len(task) <= 2048 or
            any(ord(x) < 32 for x in task) or metadata is None):
        return None
    return {'url': url, 'resource': resource, 'uuid': uuid, 'task': task, 'metadata': metadata}


def list_diagnostic(payload, records, saved, matched):
    """Fixed rejection labels and counts only; no account, URL, ID or caption."""
    rows = payload.get('list') if isinstance(payload, dict) else None
    targets = [x for x in rows if isinstance(x, dict) and saved and str(x.get('objectId')) == saved.get('id')] if isinstance(rows, list) else []
    unknown, metadata = 0, 0
    for row in rows if isinstance(rows, list) else []:
        media = row.get('desc', {}).get('media', []) if isinstance(row, dict) and isinstance(row.get('desc'), dict) else []
        for value in media if isinstance(media, list) else []:
            if isinstance(value, dict):
                unknown += int(tc_resource_identity(value.get('url')) is None)
                metadata += int(canonical_media(value) is None)
    reason = ('matched' if matched else 'list_schema_rejected' if records is None else
              'saved_candidate_missing' if not saved else 'target_not_unique' if len(targets) != 1 else 'target_or_timing_mismatch')
    target = targets[0] if len(targets) == 1 else {}
    target_media = target.get('desc', {}).get('media', []) if isinstance(target.get('desc'), dict) else []
    status = target.get('handleStatus')
    return {'event': 'tencent_draft_list_gate', 'reason': reason, 'list_count': len(rows) if isinstance(rows, list) else 0,
            'target_count': len(targets), 'unknown_media_urls': unknown, 'incomplete_media_metadata': metadata,
            'target_handle_status': status if type(status) is int and status in (1, 2, 3, 4, 5) else None,
            'target_media_schema_valid': isinstance(target_media, list) and len(target_media) == 1 and canonical_media(target_media[0]) is not None,
            'records_parsed': records is not None, 'matched': matched is not None}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class TCVerifiedProcessingReopen:
    # Constructed only after the observer and actual DOM consumption gates.
    # A JSON row with a "verified" boolean cannot activate the pure verifier.
    _bound: tuple[str, ...]
    _image_guard: object = field(default=None, repr=False, compare=False)

    def matches(self, expected, candidate, reopened, links, account, after):
        try:
            return (self._bound == tuple(_digest(x) for x in (expected, candidate, reopened, links, account, after)) and
                (self._image_guard is None or self._image_guard()))
        except (TypeError, ValueError, KeyError, AttributeError):
            return False


def _row(record):
    values = record.get('tc_media') if isinstance(record, dict) else None
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        return None
    row = values[0]
    if (set(row) != {'url', 'resource', 'uuid', 'task', 'metadata'} or
            tc_resource_identity(row.get('url')) != row.get('resource') or not row.get('resource') or
            not _asset_id(row.get('uuid')) or
            not isinstance(row.get('task'), str) or not 0 < len(row['task']) <= 2048 or
            any(ord(x) < 32 for x in row['task']) or
            row.get('metadata') != _canonical_dimensions(row.get('metadata')) or row.get('metadata') is None):
        return None
    return row


def processing_target_matches(observer, metadata, frozen):
    if not isinstance(metadata, dict):
        return False
    native = frozen['native']
    allocations = [x for x in frozen['records'] if x['operation'] == 'allocate' and x['valid'] and
                   x['ticket'] == (native['ticket']['clipKey'], native['ticket']['draftId'])]
    if len(allocations) != 1:
        return False
    params = allocations[0]['params']
    return (metadata == _canonical_dimensions(metadata) and
            (metadata.get('width'), metadata.get('height')) == tuple(params['target']) and
            metadata.get('duration') == params['origin']['duration'] and
            _file_size(metadata.get('fileSize')) is not None)


async def verify_processing_reopen(session, candidate, reopened, account_after):
    """Return a typed proof only for this normal response's exact consumers."""
    from uploader.draft_evidence import field_match
    from uploader.tc_assets import tc_reopened_assets_match
    observer = getattr(session, 'tc_processing_observer', None)
    frozen = getattr(observer, '_frozen_reopen', None)
    if (observer is None or not frozen or not observer._frozen_events_current() or
            candidate != frozen['candidate'] or
            session.normal_ui_binding != {'id': candidate['id'], 'owner': candidate['owner'], 'control': 'post_draft_item_edit'} or
            not isinstance(reopened, dict) or not any(x == reopened for x in session.reads) or
            reopened.get('tc_normal_read') != 'get_draft_list' or reopened.get('request_method') != 'POST' or
            type(reopened.get('tc_handle_status')) is not int or reopened['tc_handle_status'] != 2 or
            account_after != session.account or session.account != observer.account or
            reopened.get('id') != candidate.get('id') or reopened.get('owner') != candidate.get('owner') or
            not field_match('tencent', reopened, session.expected)):
        return None
    times = (session.reopen_at, reopened.get('request_at'), reopened.get('observed_at'))
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in times) or
            not frozen['at'] <= times[0] <= times[1] <= times[2] <= time.time()):
        return None
    original, new = _row(candidate), _row(reopened)
    native = frozen['native']
    if (not original or not new or candidate.get('video_count') != 1 or reopened.get('video_count') != 1 or
            original['uuid'] != native['uuid'] or not _same_or_processed_asset_id(original, new) or
            original['task'] != native['ticket']['draftId'] or new['task'] != original['task'] or
            original['metadata'] != native['consumed'] or
            original['resource'] != tc_resource_identity(native['source_url'] if native.get('mode') == 'async_clip' else native.get('processed_url')) or
            candidate['video_ids'] != [original['resource']] or reopened['video_ids'] != [new['resource']] or
            reopened.get('tc_media_tasks') != [{'resource': new['resource'], 'draft_id': new['task']}] or
            not processing_target_matches(observer, new['metadata'], frozen)):
        return None
    links = session.upload_links()
    videos = [x for x in links if x.get('kind') == 'video' and x.get('complete') is True]
    sha = session.expected.get('video_sha256')
    if (native['sha256'] != sha or session.assets.get('video', {}).get('sha256') != sha or
            len(videos) != 1 or videos[0].get('sha256') != sha or original['resource'] not in videos[0].get('ids', []) or
            tc_resource_identity(native['source_url']) not in videos[0].get('ids', [])):
        return None
    def cover_metadata(record):
        values = record.get('tc_cover_media', {})
        if not isinstance(values, dict) or set(values) - {'cover_portrait', 'cover_landscape'}:
            return None
        ids = set()
        for slot in ('cover_portrait', 'cover_landscape'):
            value = values.get(slot)
            if value is None:
                fallback = record.get('cover_slots', {}).get(slot)
                thumb = record.get('tc_thumb_media')
                # The existing legacy desc parser uses thumbUrl in the absent
                # landscape slot. Keep it automatic; it is never a crop role.
                if fallback and (session.expected.get('cover_sha256') or slot != 'cover_landscape' or
                        not isinstance(thumb, dict) or fallback != [thumb.get('resource')]): return None
                continue
            if (not isinstance(value, dict) or set(value) != {'url', 'resource'} or
                    not value['resource'] or tc_resource_identity(value['url']) != value['resource'] or
                    record.get('cover_slots', {}).get(slot) != [value['resource']]):
                return None
            ids.add(value['resource'])
        thumb = record.get('tc_thumb_media')
        if thumb is not None:
            if (not isinstance(thumb, dict) or set(thumb) != {'url', 'resource'} or
                    not thumb['resource'] or tc_resource_identity(thumb['url']) != thumb['resource']):
                return None
            ids.add(thumb['resource'])
        return values if sorted(ids) == record.get('cover_ids') else None
    original_covers, new_covers = cover_metadata(candidate), cover_metadata(reopened)
    if original_covers is None or new_covers is None:
        _mapping_diagnostic(session, 'cover_metadata_invalid'); return None
    roles = None
    if session.expected.get('cover_sha256'):
        preview = getattr(session, 'tc_preview_observer', None)
        roles = preview.cover_roles(candidate, reopened, frozen.get('covers', {})) if preview is not None else None
        if roles is None:
            _mapping_diagnostic(session, getattr(preview, 'last_rejection', 'custom_cover_bytes_not_verified')); return None
    elif not candidate.get('tc_thumb_media') or not reopened.get('tc_thumb_media'):
        _mapping_diagnostic(session, 'automatic_thumb_missing'); return None
    elif original_covers != new_covers:
        _mapping_diagnostic(session, 'unexpected_cover_role_change'); return None
    before = tuple(copy.deepcopy(x) for x in (session.expected, candidate, reopened, links, session.account, account_after))
    material = preview.material(roles) if roles else None
    arguments = {'restored_from': candidate, 'strict_cover_media': True}
    if roles:
        arguments['cover_roles'] = roles
    if not await tc_reopened_assets_match(session.page, reopened, **arguments):
        _mapping_diagnostic(session, 'normal_native_consumers_not_verified')
        return None
    current = (session.expected, candidate, reopened, session.upload_links(), session.account, account_after)
    if (current != before or not observer._frozen_events_current() or
            roles and (not preview.current() or material != preview.material(roles)) or
            not any(x == reopened for x in session.reads) or
            session.normal_ui_binding != {'id': candidate['id'], 'owner': candidate['owner'], 'control': 'post_draft_item_edit'}):
        return None
    _mapping_diagnostic(session, 'verified')
    guard = (lambda: preview.current() and material == preview.material(roles) and observer._frozen_events_current()) if roles else None
    return TCVerifiedProcessingReopen(tuple(_digest(x) for x in before), guard)


def _mapping_diagnostic(session, reason):
    preview = getattr(session, 'tc_preview_observer', None)
    value = {'event': 'tc_processing_reopen_mapping', 'reason': reason,
             'preview_bodies': len(preview.records) if preview is not None else 0}
    if value != getattr(session, '_tc_mapping_diagnostic', None):
        session._tc_mapping_diagnostic = value
        print('DRAFT_DIAGNOSTIC=' + json.dumps(value), flush=True)
