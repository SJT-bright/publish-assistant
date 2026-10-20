"""Hash only responses to normal browser image requests; never request images.

Official 841:1924370/1926910 renders portrait/landscape IMG from the same
PostCoverPreview.video. New CDN URLs retain distinct opaque identities. Their
bytes may attest the frozen crop result, not replace its original upload ID.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import time
from urllib.parse import urlsplit

from uploader.tc_cover_derivative import cover_resource_derivative, cover_resource_transition
from uploader.tc_resource import tc_resource_identity


def browser_response_times(response, reopen_at):
    """Correlate this normal browser response, without claiming JS completion.

    Request.timing.responseStart/responseEnd are relative to startTime (epoch
    milliseconds). responseStart is the real first-byte bound for a later
    preview request. Parsed row + exact native/decoded image consumers + crop
    body SHA provide consumption proof; responseEnd is diagnostic only, since
    browser fetch JSON consumption can precede Network.loadingFinished.
    """
    result = {'response_start': None, 'response_end': None}
    try:
        timing = response.request.timing
        start, first, end = (timing.get(k) for k in ('startTime', 'responseStart', 'responseEnd'))
        if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, first, reopen_at)) or first < 0):
            return result
        at, first_at = start / 1000, (start + first) / 1000
        if not reopen_at <= at <= first_at <= time.time():
            return result
        result['response_start'] = first_at
        if type(end) in (int, float) and math.isfinite(end) and end >= first and (start + end) / 1000 <= time.time():
            result['response_end'] = (start + end) / 1000
        return result
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return result


class TCPreviewObserver:
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.account = copy.deepcopy(session.account)
        self.scope = {key: session.expected.get(key) for key in ('job_id', 'receipt_nonce')}
        self.records, self.rejections, self.closed = [], {}, False
        self.cover_derivatives = {}

    def current(self):
        session = self.session
        return (not self.closed and not session.closed and session.platform == 'tencent' and
            getattr(self.page, '_sau_draft_evidence', None) is session and session.account == self.account and
            self.account.get('verified') is True and all(self.scope[k] and self.scope[k] == session.expected.get(k) for k in self.scope))

    def reject(self, reason):
        self.last_rejection = reason
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def frame_current(self, frame):
        return (frame in self.page.frames and urlsplit(frame.url).netloc == 'channels.weixin.qq.com' and
                urlsplit(frame.url).path.startswith(('/platform/', '/micro/content/')))

    def material(self, roles):
        return copy.deepcopy({'bodies': [x for x in self.records if x['url'] in roles.values()],
            'derivatives': {slot: value for slot, value in self.cover_derivatives.items() if slot in roles}})

    def frozen_cover_target(self, url, resource):
        """Filter from frozen sources before a normal read's JSON callback.

        None keeps the existing bounded observer behavior without a frozen
        operation. A frozen operation collects its exact cover resources,
        already-defined family derivatives, or exact role URLs from a fresh
        same-ID normal read. Eligibility never replaces final byte/native gates.
        """
        processing = getattr(self.session, 'tc_processing_observer', None)
        frozen = getattr(processing, '_frozen_reopen', None)
        if not frozen:
            return None
        if not processing._frozen_events_current():
            return False
        covers, expected = frozen.get('covers', {}), self.session.expected.get('cover_sha256', {})
        if set(covers) != set(expected):
            return False
        candidate = frozen['candidate']
        for slot, original in covers.items():
            source = candidate.get('tc_cover_media', {}).get(slot)
            if (not isinstance(source, dict) or set(source) != {'url', 'resource'} or
                    tc_resource_identity(source['url']) != source['resource'] or
                    original['ids'] != [source['resource']]):
                continue
            if resource == source['resource'] or cover_resource_transition(source['url'], url) == (source['resource'], resource):
                return True
        for row in getattr(self.session, 'reads', []):
            if (not isinstance(row, dict) or row.get('id') != candidate.get('id') or
                    row.get('owner') != candidate.get('owner') or row.get('owner') != self.account.get('uid') or
                    row.get('tc_normal_read') != 'get_draft_list' or row.get('request_method') != 'POST' or
                    type(row.get('tc_handle_status')) is not int or row['tc_handle_status'] != 2):
                continue
            times = (self.session.reopen_at, row.get('request_at'), row.get('observed_at'))
            if (any(type(value) not in (int, float) or not math.isfinite(value) for value in times) or
                    not times[0] <= times[1] <= times[2] <= time.time()):
                continue
            if any(row.get('tc_cover_media', {}).get(slot) == {'url': url, 'resource': resource} and
                   row.get('cover_slots', {}).get(slot) == [resource] for slot in covers):
                return True
        return False

    async def observe(self, response, observed_at):
        request = response.request
        try:
            if request.method != 'GET' or getattr(request, 'resource_type', None) != 'image':
                return False
            url = response.url
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.netloc != 'finder.video.qq.com' or parsed.path != '/251/20304/stodownload':
                return False
            resource = tc_resource_identity(url)
            if not resource:
                self.reject('unknown_url'); return True
            if not self.current():
                self.reject('scope_changed'); return True
            target = self.frozen_cover_target(url, resource)
            if target is False:
                self.reject('outside_frozen_cover_scope'); return True
            reopen = getattr(self.session, 'reopen_at', None)
            start = request.timing.get('startTime')
            start = start / 1000 if type(start) in (int, float) else None
            if (type(reopen) not in (int, float) or type(start) not in (int, float) or
                    not math.isfinite(start) or not reopen <= start <= observed_at):
                self.reject('old_request'); return True
            frame = request.frame
            if not self.frame_current(frame):
                self.reject('frame_scope'); return True
            headers = response.headers
            # The normal Finder CDN also serves these JPEGs as image/jpg.
            if not 200 <= response.status < 300 or headers.get('content-type', '').split(';')[0].strip().lower() not in ('image/jpeg', 'image/jpg'):
                self.reject('response_schema'); return True
            length = headers.get('content-length')
            if length is not None and (not str(length).isdigit() or not 0 < int(length) <= 32 * 1024 * 1024):
                self.reject('body_limit'); return True
            body = await asyncio.wait_for(response.body(), 5)
            if not self.current() or not self.frame_current(frame):
                self.reject('scope_changed'); return True
            if target is True and self.frozen_cover_target(url, resource) is not True:
                self.reject('scope_changed'); return True
            if not isinstance(body, bytes) or not 0 < len(body) <= 32 * 1024 * 1024:
                self.reject('body_limit'); return True
            encoding = headers.get('content-encoding', '').strip().lower()
            if length is not None and encoding in ('', 'identity') and len(body) != int(length):
                self.reject('body_length_mismatch'); return True
            record = {'url': url, 'resource': resource, 'sha256': hashlib.sha256(body).hexdigest(),
                'size': len(body), 'request_at': start, 'observed_at': observed_at}
            if target is True:
                for index, previous in enumerate(self.records):
                    if all(previous.get(key) == record[key] for key in ('url', 'resource', 'sha256', 'size')):
                        if (start, observed_at) > (previous['request_at'], previous['observed_at']):
                            self.records[index] = record
                        return True
            if len(self.records) >= 256:
                self.reject('record_limit'); return True
            self.records.append(record)
            return True
        except Exception:
            self.reject('unavailable_body'); return True

    def cover_roles(self, candidate, reopened, frozen):
        """Return exact role URLs only after crop/upload and response byte proof."""
        self.cover_derivatives = {}
        expected = self.session.expected.get('cover_sha256', {})
        if not expected:
            return {}
        observer = getattr(self.session, 'tc_cover_observer', None)
        if (not self.current() or observer is None or not observer.frozen_current(frozen) or
                set(frozen) != set(expected) or set(expected) - {'cover_portrait', 'cover_landscape'}):
            self.reject('frozen_crop_missing'); return None
        rows = reopened.get('tc_cover_media')
        if not isinstance(rows, dict) or set(rows) != set(expected):
            self.reject('cover_roles_missing'); return None
        end = reopened.get('tc_response_start_at')
        start = reopened.get('request_at')
        if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end)) or
                not self.session.reopen_at <= start <= end <= time.time()):
            self.reject('read_response_start_timing_missing'); return None
        output, derivatives = {}, {}
        for slot, value in rows.items():
            original = frozen[slot]
            if (not isinstance(value, dict) or set(value) != {'url', 'resource'} or
                    tc_resource_identity(value['url']) != value['resource'] or
                    candidate.get('cover_slots', {}).get(slot) != original['ids'] or
                    reopened.get('cover_slots', {}).get(slot) != [value['resource']] or
                    original['record']['origin_sha256'] != expected[slot]):
                self.reject('slot_replaced'); return None
            exact = [x for x in self.records if x['url'] == value['url'] and x['resource'] == value['resource']]
            bodies = [x for x in exact if end <= x['request_at'] <= x['observed_at'] <= time.time()]
            if not bodies:
                self.reject('preview_request_precedes_read' if exact else 'preview_response_missing'); return None
            digest, size = original['record']['derived_sha256'], original['record']['derived_size']
            if any(x['sha256'] != digest or x['size'] != size for x in bodies):
                source = candidate.get('tc_cover_media', {}).get(slot)
                relation = (cover_resource_derivative(slot, source.get('url'), value['url'],
                    original['record'], bodies[0]) if isinstance(source, dict) else None)
                if (relation is None or set(source) != {'url', 'resource'} or
                        source['resource'] != relation.source_resource or original['ids'] != [relation.source_resource] or
                        any((x['sha256'], x['size']) != (relation.target_sha256, relation.target_size) for x in bodies)):
                    self.reject('preview_bytes_mismatch'); return None
                derivatives[slot] = relation
            output[slot] = value['url']
        self.cover_derivatives = derivatives
        return output

    def diagnostic(self):
        return {'event': 'tc_preview_bytes', 'accepted_bodies': len(self.records), 'rejections': dict(self.rejections)}

    async def close(self):
        self.closed = True
        self.records.clear()
        self.cover_derivatives.clear()
