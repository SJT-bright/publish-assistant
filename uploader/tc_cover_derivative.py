"""One observed TC cover-family transition; never an identity alias.

The official client assigns the upload URL to a crop role and later restores
that role from the normal same-draft server row. The 2026-10-15 ordinary row
and image responses retain all six shared opaque fields across the exact
native-uploaded-image -> image-cover families, while returning different JPEG
bytes. `picformat` has no client enum: retain it in the new resource identity.

This pure relation is not an upload/save/consumer attestation. Its caller must
already verify the frozen original crop and complete upload chain, task/owner,
normal same-ID row, ordinary image body, and exact decoded native role consumer.
No URL, opaque value, credential, request, SDK call, or log is returned here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from uploader.tc_resource import (
    _AUTH, _IMAGE_COVER_REQUIRED, _NATIVE_UPLOADED_IMAGE_REQUIRED,
    _query_once, tc_resource_identity,
)


_SHARED = frozenset({'encfilekey', 'uzid', 'm', 'hy', 'idx', 'scene'})
_SLOTS = frozenset({'cover_portrait', 'cover_landscape'})
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_PATH = '/251/20304/stodownload'


@dataclass(frozen=True)
class TCCoverResourceDerivative:
    slot: str
    source_resource: str
    target_resource: str
    source_crop_sha256: str
    source_crop_size: int
    target_sha256: str
    target_size: int


def _family(url, names):
    resource = tc_resource_identity(url)
    if not resource:
        return None
    parsed = urlsplit(url)
    if parsed.path != _PATH:
        return None
    fields = _query_once(parsed.query)
    public = {key: value for key, value in fields.items() if key not in _AUTH}
    return (resource, public) if set(public) == names else None


def _body(sha, size):
    return (isinstance(sha, str) and _SHA.fullmatch(sha) is not None and
            type(size) is int and 0 < size <= 32 * 1024 * 1024)


def cover_resource_transition(source_url, target_url):
    """Return only the distinct IDs for the exact observed family relation."""
    source = _family(source_url, _NATIVE_UPLOADED_IMAGE_REQUIRED)
    target = _family(target_url, _IMAGE_COVER_REQUIRED)
    if (source is None or target is None or source[0] == target[0] or
            any(source[1][key] != target[1][key] for key in _SHARED)):
        return None
    return source[0], target[0]


def cover_resource_derivative(slot, source_url, target_url, crop, body):
    """Correlate exact families and bytes after independent frozen-crop gates.

    Family membership uses the existing strict once-decoded query contract.
    The six opaque values must all agree; every non-auth field still remains
    part of its own distinct resource identity. Known auth rotation and query
    order do not change those identities. Same-family resources cannot use
    this relation to bypass exact crop bytes.
    """
    if not isinstance(slot, str) or slot not in _SLOTS or not isinstance(crop, dict) or not isinstance(body, dict):
        return None
    relation = cover_resource_transition(source_url, target_url)
    if (relation is None or
            crop.get('slot') != slot or tc_resource_identity(crop.get('applied_id')) != relation[0] or
            body.get('url') != target_url or body.get('resource') != relation[1] or
            not _body(crop.get('derived_sha256'), crop.get('derived_size')) or
            not _body(body.get('sha256'), body.get('size'))):
        return None
    return TCCoverResourceDerivative(slot, relation[0], relation[1],
        crop['derived_sha256'], crop['derived_size'], body['sha256'], body['size'])
