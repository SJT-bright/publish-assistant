"""Private identities for the observed official TC media URL format.

This is a correlation function, never an upload/saved-draft attestation. Callers
must first verify this operation's complete DFS/SNS upload chain, native file
SHA, owner, and terminal response. Apply the same function independently to
the current composer and the saved/reopened record before comparing identities.
Do not learn formats from requests or treat an arbitrary matching URL as proof.

Source: preserved official tc-content-841.d575b04c.js, SHA256
022cd6da6b8f3afed8aded29b19aed5c360ea504784cd69c969c6e3578d7198b.
Zero-based Unicode offsets: 683664 SNS fileurl -> httpsUrl; 882611 / 2319466
DFS DownloadURL -> httpsUrl. These replace only http://wxapp.tc.qq.com with
https://finder.video.qq.com. 1931820 handleFileListChange copies httpsUrl into
objectDesc.media[].url; 1936400 / 2287200 restore that URL into response fields.
859852 / 2308246 show /251/20304/stodownload and the older filekey/storeid form.

On 2026-10-04, two read-only snapshots of the installed creator component had
the same five URLs at /251/20302/stodownload, three distinct encfilekey/uzid
pairs, and the same present-but-empty m field.
The two HTTP/HTTPS pairs had identical queries. This confirms stability across
the observed copies, not indefinite server-side stability. The client does not
define encfilekey/uzid semantics: retain *all* recognized non-auth fields,
including basedata/scene/dotrans, and reject unknown fields. The known auth
fields token/sign/findertoken are never retained. A changed opaque field fails
correlation; no fallback guesses a different field or decodes a second time.

The same installed bundle does not construct image CDN query strings: at
1749494, customUpload stores the image upload response's httpsUrl as coverUrl;
at 1915436, handleUpdateCover stores the returned httpsUrl in coverUrl or
shareCoverUrl; at 1931820, handleFileListChange copies those fields into the
current media record. On 2026-10-04 the native current and saved records showed
image resources at /251/20304/stodownload in two observed shapes: the cover
shape uses encfilekey, hy, idx, m, picformat, scene, uzid; the thumbnail shape
uses encfilekey, hy, idx, m, picformat, bizid, dotrans, uzid. `picformat` is
an opaque server value: all eight observed copies had the same non-empty,
three-digit ASCII value, while the official client has no literal query key or
value allowlist for it. Require a non-empty visible-ASCII value and hash its
exact decoded contents; do not guess an enum.

On 2026-10-15, the normal post_draft request from a fresh native upload had
thumbUrl/coverUrl/fullCoverUrl/shareCoverUrl at /251/20304/stodownload with
exact non-auth keys bizid,dotrans,encfilekey,hy,idx,m,scene,uzid, without
picformat. This native-upload image family has its own path-sensitive identity;
it is not the processed picformat image family or the 20302 video family.
All non-auth values remain opaque, including the present-but-empty m field.
"""

import hashlib
import json
import re
from urllib.parse import unquote_to_bytes, urlsplit


_ORIGINS = {
    ('http', 'wxapp.tc.qq.com'),
    ('https', 'finder.video.qq.com'),
}
_PATHS = {
    'encrypted': '/251/20302/stodownload',
    'filekey': '/251/20304/stodownload',
    'encrypted_scene': '/251/20302/stodownload',
    'image_cover': '/251/20304/stodownload',
    'image_thumbnail': '/251/20304/stodownload',
    'native_uploaded_image': '/251/20304/stodownload',
}
_AUTH = frozenset({'token', 'sign', 'findertoken'})
_ENCRYPTED_REQUIRED = frozenset({'encfilekey', 'm', 'uzid', 'bizid', 'hy', 'idx'})
_ENCRYPTED_OPTIONAL = frozenset({'scene', 'dotrans', 'basedata'})
# Exact observed normal processed-preview family. It has its own identity;
# changing encfilekey/uzid/basedata never aliases another server read.
_ENCRYPTED_SCENE_REQUIRED = frozenset({'encfilekey', 'm', 'uzid', 'hy', 'idx', 'scene', 'basedata'})
_IMAGE_COMMON = frozenset({'encfilekey', 'm', 'uzid', 'hy', 'idx', 'picformat'})
_IMAGE_COVER_REQUIRED = _IMAGE_COMMON | {'scene'}
_IMAGE_THUMBNAIL_REQUIRED = _IMAGE_COMMON | {'bizid', 'dotrans'}
_NATIVE_UPLOADED_IMAGE_REQUIRED = _ENCRYPTED_REQUIRED | {'dotrans', 'scene'}
_PLAIN_REQUIRED = frozenset({'filekey', 'm', 'storeid', 'bizid', 'hy'})
_PLAIN_OPTIONAL = frozenset({'dotrans', 'adaptivelytrans'})
_ALL_KEYS = (_AUTH | _ENCRYPTED_REQUIRED | _ENCRYPTED_OPTIONAL | _IMAGE_COMMON |
             _IMAGE_COVER_REQUIRED | _IMAGE_THUMBNAIL_REQUIRED |
             _PLAIN_REQUIRED | _PLAIN_OPTIONAL | _ENCRYPTED_SCENE_REQUIRED)
_BAD_ESCAPE = re.compile(r'%(?![0-9a-fA-F]{2})')


def _query_once(query):
    """Require literal, unique field names; decode each value exactly once."""
    if not query:
        return None
    pairs = query.split('&')
    if len(pairs) > len(_ALL_KEYS):
        return None
    result = {}
    for pair in pairs:
        if '=' not in pair:
            return None
        key, encoded = pair.split('=', 1)
        # Encoded key aliases are unnecessary in the official format. Refuse
        # them, including encfilekey + %65ncfilekey duplicate aliases.
        if (key not in _ALL_KEYS or key in result or (not encoded and key != 'm') or
                _BAD_ESCAPE.search(encoded)):
            return None
        try:
            value = unquote_to_bytes(encoded.replace('+', ' ')).decode('ascii')
        except (UnicodeError, ValueError):
            return None
        if ((not value and key != 'm') or len(value) > 4096 or
                any(not 33 <= ord(char) <= 126 for char in value)):
            return None
        result[key] = value
    return result


def tc_resource_identity(url):
    """Return ``tc-resource:<SHA256>`` or None, without returning URL/secrets.

    Only the observed origin pair, fixed download paths and exact query
    families are supported. Query order and the official host replacement do
    not alter identity. Every non-auth field's value and presence participate
    in the hash; unknown/mixed formats, duplicate keys and malformed URLs fail
    closed. No I/O, SDK call, logging or persistence occurs here.
    """
    if (not isinstance(url, str) or not 0 < len(url) <= 8192 or
            any(not 33 <= ord(char) <= 126 for char in url) or '#' in url or '\\' in url):
        return None
    try:
        parsed = urlsplit(url)
        if ((parsed.scheme, parsed.netloc) not in _ORIGINS or parsed.path not in _PATHS.values() or
                parsed.username or parsed.password or parsed.fragment):
            return None
        fields = _query_once(parsed.query)
    except (UnicodeError, ValueError):
        return None
    if fields is None:
        return None
    public = {key: value for key, value in fields.items() if key not in _AUTH}
    names = set(public)
    if parsed.path == _PATHS['native_uploaded_image'] and names == _NATIVE_UPLOADED_IMAGE_REQUIRED:
        family = 'native_uploaded_image'
    elif _ENCRYPTED_REQUIRED <= names <= _ENCRYPTED_REQUIRED | _ENCRYPTED_OPTIONAL:
        family = 'encrypted'
    elif names == _ENCRYPTED_SCENE_REQUIRED:
        family = 'encrypted_scene'
    elif names == _IMAGE_COVER_REQUIRED:
        family = 'image_cover'
    elif names == _IMAGE_THUMBNAIL_REQUIRED:
        family = 'image_thumbnail'
    elif _PLAIN_REQUIRED <= names <= _PLAIN_REQUIRED | _PLAIN_OPTIONAL:
        if not public['m']:
            return None
        family = 'filekey'
    else:
        return None
    if parsed.path != _PATHS[family]:
        return None
    canonical = json.dumps(
        {'version': 1, 'host': 'finder.video.qq.com', 'path': parsed.path,
         'family': family, 'query': public},
        ensure_ascii=True, sort_keys=True, separators=(',', ':'),
    ).encode('ascii')
    return 'tc-resource:' + hashlib.sha256(canonical).hexdigest()
