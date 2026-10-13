"""Shared material mapping and receipt contract; no browser or account operations."""
import hashlib
import json
from pathlib import Path
from text_import import normalize_tags, caption_with_topics
from platform_limits import TEXT_LIMITS, check_fields

PLATFORM_POLICIES = {
    'douyin': {'name': '抖音', 'location': 'browser_local', 'label': '当前浏览器本地草稿', 'verified_status': 'local_draft_verified', 'title_max': 30},
    'xiaohongshu': {'name': '小红书', 'location': 'browser_local', 'label': '当前浏览器本地草稿', 'verified_status': 'local_draft_verified', 'title_max': 20, 'tags_max': 10},
    'tencent': {'name': '视频号', 'location': 'platform', 'label': '平台草稿', 'verified_status': 'platform_draft_verified'},
    'bilibili': {'name': 'B站', 'location': 'platform', 'label': '平台草稿', 'verified_status': 'platform_draft_verified', 'title_max': 80, 'tags_max': 10},
    'kuaishou': {'name': '快手', 'location': 'platform_snapshot', 'label': '平台单份暂存快照', 'verified_status': 'platform_snapshot_verified'},
    'toutiao': {'name': '今日头条', 'location': 'platform', 'label': '视频草稿待适配',
                'verified_status': 'platform_draft_verified', 'automation_ready': False,
                'readiness_message': '创作平台已接入；自动上传和草稿核验待适配'},
    'xigua': {'name': '西瓜视频', 'location': 'platform', 'label': '官方创作入口（迁移中）',
              'verified_status': 'platform_draft_verified', 'automation_ready': False,
              'readiness_message': '网页版正在迁移到抖音创作者中心；独立视频草稿流程待确认'},
}
for _id, _limits in TEXT_LIMITS.items():
    PLATFORM_POLICIES[_id].update(_limits)
VERIFIED_STATUSES = frozenset(p['verified_status'] for p in PLATFORM_POLICIES.values())
UNRESOLVED_STATUSES = frozenset({'saved_unverified', 'result_unknown', 'risk_paused', 'platform_restricted', 'adapter_mismatch'})
RESULT_STATUSES = VERIFIED_STATUSES | UNRESOLVED_STATUSES | {'needs_reauthentication', 'account_mismatch', 'failed', 'cancelled'}
IDENTITY_FIELDS = ('job_id', 'platform_id', 'video_sha256', 'account_key', 'content_sha256', 'cover_sha256', 'operation_key', 'receipt_nonce')
VERIFICATION_FIELDS = ('upload_complete', 'fields', 'cover', 'media', 'reopened', 'account')
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
COVER_FIELDS = ('cover', 'cover_landscape', 'cover_portrait')


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def platform_content_fields(platform, fields):
    """Exact text fields the platform saves; shared by preflight and read-back."""
    if platform not in PLATFORM_POLICIES:
        raise ValueError('未知平台。')
    if platform == 'kuaishou':
        return {'caption': fields['caption']}
    if platform == 'douyin':
        return {'title': fields['title'], 'caption': fields['caption']}
    if platform == 'tencent':
        return {'caption': fields['caption'], 'short_title': fields['short_title']}
    return {key: value for key, value in fields.items() if key not in COVER_FIELDS}


def hash_asset(value):
    """Hash bytes, checking the file did not change during the read."""
    path = Path(value).expanduser().resolve()
    before = path.stat()
    if not path.is_file() or before.st_size == 0:
        raise ValueError('素材文件不存在或为空：' + path.name)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('素材在读取时发生变化，请重新预检：' + path.name)
    return digest.hexdigest()


def _asset(value, extensions):
    if not value:
        return ''
    path = Path(str(value)).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in extensions:
        raise ValueError('素材不存在或格式不支持：' + path.name)
    return str(path)


def preflight_payload(payload, platforms, account_keys=None, capabilities=None):
    """Return payload, mappings, fingerprints, errors, warnings; never truncate text.

    platform_overrides may set title/description/tags/short_title and cover paths.
    limits are adapter constraints; live page limits must still be checked by the runner.
    """
    if not isinstance(payload, dict):
        raise ValueError('素材需要是 JSON 对象。')
    selected = list(dict.fromkeys(platforms))
    if not selected or any(p not in PLATFORM_POLICIES for p in selected):
        raise ValueError('请选择有效的平台。')
    normalized = {k: payload.get(k, '') for k in ('folder', 'video', 'title', 'description', 'short_title', *COVER_FIELDS)}
    normalized['title'] = str(normalized['title']).strip()
    normalized['description'] = str(normalized['description'])
    normalized['short_title'] = str(normalized['short_title']).strip()
    tags = payload.get('tags', [])
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ValueError('标签需要是文字数组。')
    normalized['tags'] = normalize_tags(tags)
    normalized['platform_overrides'] = payload.get('platform_overrides', {})
    if not isinstance(normalized['platform_overrides'], dict):
        raise ValueError('各平台的单独设置格式无效。')
    errors, warnings, mappings, fingerprints = [], [], {}, {}
    try:
        normalized['video'] = _asset(normalized['video'], VIDEO_EXT)
        if not normalized['video']:
            raise ValueError('请先选择视频。')
        video_sha = hash_asset(normalized['video'])
    except (OSError, ValueError) as exc:
        errors.append({'platform': '', 'field': 'video', 'message': str(exc)})
        video_sha = ''
    normalized['video_sha256'] = video_sha
    if payload.get('video_sha256') and payload['video_sha256'] != video_sha:
        errors.append({'platform': '', 'field': 'video', 'message': '视频内容与任务建立时不同，已停止。'})
    asset_hashes = {}
    for platform in selected:
        policy = PLATFORM_POLICIES[platform]
        override = normalized['platform_overrides'].get(platform, {})
        if not isinstance(override, dict):
            errors.append({'platform': platform, 'field': 'override', 'message': '平台单独设置需要是对象。'})
            override = {}
        allowed = {'title', 'description', 'tags', 'short_title', *COVER_FIELDS}
        unsupported = set(override) - allowed
        if unsupported:
            errors.append({'platform': platform, 'field': 'override', 'message': '不支持的平台字段：' + '、'.join(sorted(unsupported))})
        fields = {k: override.get(k, normalized.get(k)) for k in allowed}
        fields['title'] = str(fields['title'] or '').strip()
        fields['description'] = str(fields['description'] or '')
        fields['short_title'] = str(fields['short_title'] or '').strip()
        if not isinstance(fields['tags'], list) or any(not isinstance(x, str) for x in fields['tags']):
            errors.append({'platform': platform, 'field': 'tags', 'message': '标签需要是文字数组。'})
            fields['tags'] = []
        fields['tags'] = normalize_tags(fields['tags'])
        for key in COVER_FIELDS:
            try:
                fields[key] = _asset(fields[key], IMAGE_EXT)
                if fields[key] and fields[key] not in asset_hashes:
                    asset_hashes[fields[key]] = hash_asset(fields[key])
            except (OSError, ValueError) as exc:
                errors.append({'platform': platform, 'field': key, 'message': str(exc)})
                fields[key] = ''
        common = fields['cover']
        portrait = fields['cover_portrait'] or common
        landscape = fields['cover_landscape'] or common
        if platform in ('xiaohongshu', 'kuaishou'):
            fields['cover'] = portrait or landscape
            fields.pop('cover_landscape')
            fields.pop('cover_portrait')
        elif platform in ('bilibili', 'toutiao', 'xigua'):
            fields['cover'] = landscape or portrait
            fields.pop('cover_landscape')
            fields.pop('cover_portrait')
        else:
            fields['cover_landscape'], fields['cover_portrait'] = landscape, portrait
            fields.pop('cover')
        if platform != 'tencent':
            fields.pop('short_title')
        if platform in ('douyin', 'kuaishou', 'tencent'):
            # The saved editor is one exact text field on these platforms.
            fields['caption'] = caption_with_topics(fields['title'] if platform != 'douyin' else '',
                                                    fields['description'], fields['tags'])
        errors.extend(check_fields(platform,fields,(capabilities or {}).get(platform))['errors'])
        if policy.get('automation_ready') is False:
            warnings.append({'platform': platform, 'field': 'platform', 'message': policy['readiness_message']})
        if platform == 'kuaishou':
            warnings.append({'platform': platform, 'field': 'caption', 'message': '标题、文案与标签合并为同一份作品描述；平台只保留一份暂存快照。'})
        if not any(fields.get(k) for k in COVER_FIELDS):
            warnings.append({'platform': platform, 'field': 'cover', 'message': '未选择自定义封面，需核验平台实际保留的视频封面。'})
        mappings[platform] = fields
        cover_sha = {k: asset_hashes[v] for k, v in fields.items() if k in COVER_FIELDS and v}
        content_fields = platform_content_fields(platform, fields)
        content_sha = canonical_sha(content_fields)
        account_key = (account_keys or {}).get(platform) or payload.get('account_key') or 'default:' + platform
        fingerprint = {'platform_id': platform, 'account_key': account_key, 'video_sha256': video_sha, 'content_sha256': content_sha, 'cover_sha256': cover_sha}
        fingerprint['operation_key'] = canonical_sha(fingerprint)
        fingerprints[platform] = fingerprint
    return {'payload': normalized, 'mappings': mappings, 'fingerprints': fingerprints, 'errors': errors, 'warnings': warnings}


def build_platform_payload(check, platform, job_id, receipt_nonce):
    if check['errors']:
        raise ValueError('素材预检尚未通过。')
    result = dict(check['payload'])
    # This is a finalized platform job. Do not replay common cover roles or the
    # original override recipe when the runner performs its independent check.
    for key in (*COVER_FIELDS, 'platform_overrides'):
        result.pop(key, None)
    result.update(check['mappings'][platform])
    result.update(check['fingerprints'][platform])
    result.update(job_id=job_id, platform=platform, platform_fields=check['mappings'][platform], receipt_nonce=receipt_nonce)
    return result


def validate_receipt(receipt, expected_payload, platform=None, created_at=None):
    """Verify request identity and strong evidence flags, never trust an old toast receipt."""
    platform = platform or expected_payload.get('platform_id') or expected_payload.get('platform')
    if not isinstance(receipt, dict) or platform not in PLATFORM_POLICIES:
        raise ValueError('草稿回执格式无效。')
    for key in IDENTITY_FIELDS:
        if key not in receipt or receipt[key] != expected_payload.get(key):
            raise ValueError('草稿回执与本次任务不匹配：' + key)
    status = receipt.get('status')
    if status not in RESULT_STATUSES:
        raise ValueError('回执未提供可核验的草稿状态。')
    if status in VERIFIED_STATUSES:
        policy = PLATFORM_POLICIES[platform]
        if policy.get('automation_ready') is False:
            raise ValueError('此平台的自动草稿流程尚未核验，不能接受成功回执。')
        if status != policy['verified_status']:
            raise ValueError('平台草稿保存位置与回执状态不匹配。')
        checks = receipt.get('verification')
        if not isinstance(checks, dict) or any(checks.get(k) is not True for k in VERIFICATION_FIELDS):
            raise ValueError('上传、内容、封面、媒体、重开或账号证据未全部通过。')
        account = receipt.get('account')
        if not isinstance(account, dict) or not account.get('identity') or account.get('platform') != platform or account.get('verified') is not True:
            raise ValueError('回执缺少当前平台账号的验证证据。')
        owner = expected_payload.get('expected_account_identity') or (expected_payload.get('account') or {}).get('identity')
        if owner and owner != account['identity']:
            raise ValueError('草稿回执账号与本次任务账号不匹配。')
        if not receipt.get('draft_id') or receipt.get('save_location') != policy['location']:
            raise ValueError('回执缺少实际草稿标识或保存位置。')
        if receipt.get('partial'):
            raise ValueError('部分完成的任务不能标为草稿核验通过。')
    return receipt
