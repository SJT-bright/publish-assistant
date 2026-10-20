"""Local, single-user draft workbench. Explicitly selected draft or publication workflow."""
import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from draft_runner import PLATFORMS, ROOT, account_file
from uploader.embedded_browser import embedded_account_file, bridge_request
from workbench_model import (PLATFORM_POLICIES, VERIFIED_STATUSES, UNRESOLVED_STATUSES,
                             preflight_payload, build_platform_payload, validate_receipt, validate_publication_receipt,
                             PUBLICATION_VERIFIED_STATUSES, PUBLICATION_STATUSES, hash_asset, canonical_sha)
from platform_limits import check_text
from workbench_progress import (PREFIX as PROGRESS_PREFIX, parse_progress, apply_progress,
                                finish_progress, progress_summary, public_progress, valid_job_id)
from uploader.draft_evidence import CheckpointError, validate_checkpoint, CHECKPOINT_MAX_BYTES
from runtime_support import runner_command, stop_runner, open_private_read, private_stat_ok, subprocess_options

PORT = int(os.environ.get('SAU_WORKBENCH_PORT', 18761))
URL = f'http://127.0.0.1:{PORT}'
PRIVATE = Path(os.environ.get('SAU_WORKBENCH_PRIVATE', ROOT / '.draft-workbench'))
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
CURRENT = None
PROCESS = None
ASSETS = set()
EDITOR = {}
LEDGER = {}
ACCOUNTS = {}
HISTORY = []
STORE_ROOT = None
RESUME_ASSET_CACHE = {}
RESUME_MAX_ATTEMPTS = 3


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default):
    if not path.is_file(): return default
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise RuntimeError('本地记录损坏，已停止自动操作：' + path.name) from exc


def persist():
    # One atomic document keeps job transitions and the duplicate ledger in sync.
    atomic_json(PRIVATE / 'workbench-state.json', {'version': 2, 'job': CURRENT, 'editor': EDITOR,
                'ledger': LEDGER, 'accounts': ACCOUNTS, 'history': HISTORY[-30:]})
    if CURRENT:
        atomic_json(PRIVATE / 'jobs' / CURRENT['id'] / '任务结果.json', CURRENT)


def ensure_store():
    global STORE_ROOT, CURRENT, EDITOR, LEDGER, ACCOUNTS, HISTORY
    with LOCK:
        key = str(PRIVATE.resolve())
        if STORE_ROOT == key: return
        PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        document = read_json(PRIVATE / 'workbench-state.json', {})
        if document and (not isinstance(document, dict) or document.get('version') != 2):
            raise RuntimeError('本地账本版本无法确认，已停止自动操作并保留原记录。')
        CURRENT, EDITOR = document.get('job'), document.get('editor', {})
        LEDGER, ACCOUNTS = document.get('ledger', {}), document.get('accounts', {})
        HISTORY = document.get('history', [])
        if not all(isinstance(v, dict) for v in (EDITOR, LEDGER, ACCOUNTS)) or not isinstance(HISTORY, list):
            raise RuntimeError('本地账本格式无效，已停止自动操作。')
        STORE_ROOT = key
        if CURRENT and CURRENT.get('active'):
            for item in CURRENT['results']:
                if item['status'] == 'running':
                    item.update(status='result_unknown', message='上次任务中断，草稿结果待确认；不会自动重传')
                    finish_progress(item, 'result_unknown', time.time())
                    record_operation(CURRENT, item)
                elif item['status'] == 'pending':
                    item.update(status='cancelled', message='上次任务中断，此平台尚未执行')
            CURRENT.update(active=False, recovered=True)
            CURRENT['logs'].append('恢复本地账本：中断的上传保持待确认，未自动重试。')
            persist()
        for name in ('video', 'cover', 'cover_landscape', 'cover_portrait'):
            value = EDITOR.get('payload', {}).get(name)
            if value and Path(value).is_file(): ASSETS.add(str(Path(value).resolve()))
        for override in EDITOR.get('payload', {}).get('platform_overrides', {}).values():
            if isinstance(override, dict):
                for name in ('cover', 'cover_landscape', 'cover_portrait'):
                    value = override.get(name)
                    if value and Path(value).is_file(): ASSETS.add(str(Path(value).resolve()))


def sanitize_log(line):
    line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', str(line).rstrip())
    # Never retain browser state JSON, cookie headers, tokens or verification codes.
    if re.search(r'\b(cookies|localStorage|sessionStorage)\b\s*[:=]', line, re.I):
        return '[浏览器登录数据已隐藏]'
    line = re.sub(r'(?i)(authorization\s*[:=]\s*)(?:Bearer\s+)?[^,\s}\"]+', r'\1[已隐藏]', line)
    line = re.sub(r'(?i)([\"\']?(?:cookie|token|access_token|refresh_token|xsec_token|sessionid|session_id|password|sms_code|verification_code)[\"\']?\s*[:=]\s*)[^\n]+', r'\1[已隐藏]', line)
    line = re.sub(r'(?i)([?&](?:token|code|auth|session|uid|openid|user_id)=[^&#\s]*)', '[参数已隐藏]', line)
    line = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[手机号已隐藏]', line)
    return line[:2000]


def marker(platform, browser):
    path = embedded_account_file(platform) if browser == 'embedded' else account_file(platform)
    if not path.is_file(): return {}
    # A storage-state file is a saved session, never evidence of a current login.
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return {'present': True, 'account': value.get('account', {}), 'verified_at': value.get('verified_at')}
    except (OSError, ValueError, AttributeError): return {'present': True}


def account_context(platform, browser):
    saved = marker(platform, browser)
    recent = ACCOUNTS.get(browser + ':' + platform, {})
    account = saved.get('account') or recent.get('account') or {}
    identity = account.get('identity') if isinstance(account, dict) else None
    if browser == 'embedded':
        live = current_sessions().get(platform, {})
        if live.get('status') == 'login_verified' and live.get('identity'):
            # A new task binds to this app's fresh, authenticated page. Disk
            # markers describe an earlier session and never override that page.
            identity = live['identity']
    return {'account_key': browser + ':' + platform + ':' + (identity or 'default'),
            'identity': identity, 'verified_at': saved.get('verified_at') or recent.get('verified_at'),
            'session_record_present': bool(saved.get('present'))}


def preflight(data):
    ensure_store()
    browser = data.get('browser', 'external')
    if browser not in ('external', 'embedded'): raise ValueError('浏览器类型无效。')
    platforms = data.get('platforms', [])
    keys = {p: account_context(p, browser)['account_key'] for p in platforms if p in PLATFORMS}
    capabilities = {p:s.get('capabilities',{}) for p,s in current_sessions().items()} if browser=='embedded' else {}
    check = preflight_payload(data.get('payload', {}), platforms, keys, capabilities)
    for fields in check['mappings'].values():
        for key in ('cover', 'cover_landscape', 'cover_portrait'):
            if fields.get(key): ASSETS.add(fields[key])
    check['policies'] = {p: PLATFORM_POLICIES[p] for p in platforms}
    return check


def save_editor(data):
    global EDITOR
    ensure_store()
    payload = data.get('payload', {})
    platforms = data.get('platforms', [])
    if not isinstance(payload, dict) or not isinstance(platforms, list) or any(p not in PLATFORMS for p in platforms):
        raise ValueError('本地编辑内容格式无效。')
    # The form cannot write job identity, account data or arbitrary state to disk.
    allowed = {'folder', 'videos', 'video', 'title', 'description', 'tags', 'short_title',
               'cover', 'cover_landscape', 'cover_portrait', 'cover_generated', 'cover_generation_error', 'cover_frames', 'platform_overrides'}
    with LOCK:
        EDITOR = {'payload': {k: v for k, v in payload.items() if k in allowed},
                  'platforms': list(dict.fromkeys(platforms)), 'updated_at': time.time()}
        persist()
        return EDITOR


def clear_editor(data):
    """Reset only the homepage material draft, retaining sessions and receipts."""
    global EDITOR
    ensure_store()
    platforms = data.get('platforms', EDITOR.get('platforms', []))
    if not isinstance(platforms, list) or any(p not in PLATFORMS for p in platforms):
        raise ValueError('所选平台格式无效。')
    with LOCK:
        if CURRENT and CURRENT.get('active'):
            raise ValueError('当前任务正在处理，请等待完成或停止后再清空草稿。')
        previous = EDITOR
        EDITOR = {'payload': {}, 'platforms': list(dict.fromkeys(platforms)), 'updated_at': time.time()}
        try:
            persist()
        except Exception:
            EDITOR = previous
            raise
        return EDITOR


def record_operation(job, item):
    expected = item.get('expected')
    if not expected: return
    evidence = item.get('evidence', {})
    account = evidence.get('account', {})
    old = LEDGER.get(expected['operation_key'], {})
    # A retry is a new attempt, never erasure of the previous unknown result.
    # Strip nested histories so repeated attempts remain a flat audit trail.
    old_attempts = old.get('attempts', [])
    attempts = [{k: v for k, v in attempt.items() if k != 'attempts'}
                for attempt in old_attempts if isinstance(attempt, dict)] if isinstance(old_attempts, list) else []
    if old and (old.get('job_id') != job['id'] or old.get('execution_attempt', 0) != item.get('resume_count', 0)):
        attempts.append({k: v for k, v in old.items() if k != 'attempts'})
    LEDGER[expected['operation_key']] = {**{k: expected[k] for k in ('operation_key', 'platform_id', 'account_key', 'video_sha256', 'content_sha256', 'cover_sha256')},
        'actual_account_identity': account.get('identity') or item.get('account_identity') or old.get('actual_account_identity'),
        'workflow_action': job.get('action', 'draft'), 'browser': job['browser'], 'job_id': job['id'], 'execution_attempt': item.get('resume_count', 0),
        'status': item['status'], 'message': item.get('message'),
        'draft_id': evidence.get('draft_id'), 'save_location': evidence.get('save_location'), 'updated_at': time.time()}
    if attempts: LEDGER[expected['operation_key']]['attempts'] = attempts
    if item.get('recovery_of'): LEDGER[expected['operation_key']]['recovery_of'] = dict(item['recovery_of'])


def duplicate_record(fingerprint, identity, browser):
    for record in reversed(list(LEDGER.values())):
        same_account = (record.get('account_key') == fingerprint['account_key']
                        or bool(identity and record.get('actual_account_identity') == identity)
                        or not record.get('actual_account_identity') and str(record.get('account_key', '')).endswith(':default'))
        if record.get('platform_id') != fingerprint['platform_id'] or record.get('browser') != browser or not same_account: continue
        same_workflow = record.get('workflow_action', 'draft') == fingerprint.get('workflow_action', 'draft')
        same_material = same_workflow and all(record.get(k) == fingerprint[k] for k in ('video_sha256', 'content_sha256', 'cover_sha256'))
        status = record.get('status')
        # Older adapters classified an ambiguous control as an account restriction.
        # Keep that operation unresolved without blocking different materials on
        # the account. Actual platform restrictions and risk pauses still hold.
        old_control_mismatch = status == 'platform_restricted' and '多个可能的素材或内容控件' in str(record.get('message', ''))
        account_hold = status == 'risk_paused' or status == 'platform_restricted' and not old_control_mismatch
        if account_hold or same_material and status in VERIFIED_STATUSES | UNRESOLVED_STATUSES | PUBLICATION_VERIFIED_STATUSES | {'published_unverified', 'publication_blocked'}:
            return record
    return None


def retry_status(record):
    return (record.get('status') in ('result_unknown', 'saved_unverified', 'adapter_mismatch')
            or record.get('status') == 'platform_restricted'
            and '多个可能的素材或内容控件' in str(record.get('message', '')))


def retry_owner_matches(record, context):
    actual = record.get('actual_account_identity')
    if actual:
        return bool(context.get('identity') and actual == context['identity'])
    return (record.get('account_key') == context['account_key']
            or str(record.get('account_key', '')).endswith(':default'))


def retry_reference(job, item, context):
    """The only record eligible for recovery is the displayed item's blocker."""
    if (job.get('active') or job.get('action') != 'draft'
            or item.get('status') not in ('result_unknown', 'saved_unverified', 'adapter_mismatch', 'platform_restricted')):
        return None
    expected = item.get('expected')
    if not isinstance(expected, dict) or any(key not in expected for key in ('platform_id', 'account_key', 'video_sha256', 'content_sha256', 'cover_sha256')):
        return None
    if expected['platform_id'] != item['platform']:
        return None
    # A later material record must not mask an earlier account-wide risk hold.
    for hold in LEDGER.values():
        if hold.get('platform_id') != item['platform'] or hold.get('browser') != job['browser']:
            continue
        if (hold.get('status') == 'risk_paused'
                or hold.get('status') == 'platform_restricted' and not retry_status(hold)) and retry_owner_matches(hold, context):
            return None
    record = duplicate_record(expected, context['identity'], job['browser'])
    source_id = item.get('previous_job_id') if item.get('reused') else job['id']
    if not record or record.get('job_id') != source_id or not retry_status(record) or not retry_owner_matches(record, context):
        return None
    if not all(record.get(key) == expected[key] for key in ('video_sha256', 'content_sha256', 'cover_sha256')):
        return None
    return record


def recorded_source(record):
    """Find the exact executed row behind a ledger record, never a reused row."""
    if not isinstance(record, dict) or not valid_job_id(record.get('job_id')):
        return None
    job_id, platform = record['job_id'], record.get('platform_id')
    if platform not in PLATFORMS:
        return None
    candidates = [job for job in [CURRENT, *HISTORY] if isinstance(job, dict) and job.get('id') == job_id]
    if candidates:
        job = candidates[-1]
        if any(candidate != job for candidate in candidates):
            return None
    else:
        directory = PRIVATE / 'jobs' / job_id
        try:
            if directory.is_symlink() or directory.parent.is_symlink():
                return None
            job = json.loads(_private_bytes(directory / '任务结果.json', 2 * 1024 * 1024))
        except (ValueError, OSError, TypeError):
            return None
    if (not isinstance(job, dict) or job.get('id') != job_id or job.get('action') != 'draft'
            or job.get('browser') != record.get('browser') or not isinstance(job.get('results'), list)):
        return None
    rows = [row for row in job['results'] if isinstance(row, dict) and row.get('platform') == platform]
    if len(rows) != 1:
        return None
    row = rows[0]
    expected = row.get('expected')
    if (row.get('reused') or not isinstance(expected, dict) or expected.get('job_id') != job_id
            or row.get('status') != record.get('status')
            or row.get('resume_count', 0) != record.get('execution_attempt', 0)
            or any(expected.get(key) != record.get(key) for key in
                   ('operation_key', 'platform_id', 'account_key', 'video_sha256', 'content_sha256', 'cover_sha256'))):
        return None
    return job, row


def duplicate_details(record, expected):
    """Project real old errors and steps without marking a new execution done."""
    prefixes = {
        'published_unverified': '前次已尝试投稿，回执待确认；未重复提交。',
        'published_submission_verified': '已有本素材的投稿回执，未重复提交。',
        'publication_blocked': '此前投稿停在提交前核验；未重复执行。',
        'saved_unverified': '前次已尝试保存，但尚未核验；请先打开平台检查草稿。',
        'result_unknown': '前次任务未完成核验；未自动重复上传。',
        'adapter_mismatch': '前次停在页面控件识别；未自动重复上传。',
        'risk_paused': '此前平台出现风险提示；此账号的自动处理仍暂停。',
        'platform_restricted': '此前平台权限受限；此账号的自动处理仍暂停。',
    }
    prefix = ('已有本素材的核验记录，未重复上传。' if record.get('status') in VERIFIED_STATUSES
              else prefixes.get(record.get('status'), '此前任务已保留，未自动重复上传。'))
    original_message = sanitize_log(record.get('message') or STATUS_MESSAGES.get(record.get('status'), '原记录未提供具体原因'))
    result = {'message': prefix + '\n原任务原因：' + original_message}
    source = recorded_source(record)
    same_material = all(expected.get(key) == record.get(key) for key in ('video_sha256', 'content_sha256', 'cover_sha256'))
    if source and same_material:
        job, row = source
        progress = public_progress(row.get('progress'))
        if progress:
            result['progress'] = progress
        if row.get('started_at') is not None:
            result['started_at'] = row['started_at']
        result['previous_created_at'] = job.get('created_at')
    return result


def reused_details(job, item):
    if not item.get('reused'):
        return {}
    expected = item.get('expected')
    keys = ('platform_id', 'account_key', 'video_sha256', 'content_sha256', 'cover_sha256')
    if not isinstance(expected, dict) or any(key not in expected for key in keys):
        return {}
    context = account_context(item['platform'], job['browser'])
    record = duplicate_record(expected, context['identity'], job['browser'])
    if not record or record.get('job_id') != item.get('previous_job_id'):
        return {}
    return duplicate_details(record, expected)


def _private_bytes(path, maximum):
    """Read a small private regular file without following a symbolic link."""
    descriptor = open_private_read(path)
    try:
        before = os.fstat(descriptor)
        if (not private_stat_ok(before) or not 0 < before.st_size <= maximum):
            raise ValueError('恢复验证文件的权限或大小无法确认。')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            value = source.read(maximum + 1)
        after = os.fstat(descriptor)
        if ((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(value) != before.st_size):
            raise ValueError('恢复验证文件在读取时发生变化。')
        return value
    finally:
        os.close(descriptor)


def checkpoint_key(create=False):
    """Independent 32-byte seal key; never a CLI argument value or UI field."""
    path = PRIVATE / 'evidence-checkpoint.key'
    if create:
        PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            pass
        else:
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(secrets.token_bytes(32))
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
    value = _private_bytes(path, 32)
    if len(value) != 32:
        raise ValueError('本地恢复验证密钥无法确认。')
    return path, value


def upload_checkpoint_path(job, item):
    if not valid_job_id(job.get('id')) or item.get('platform') not in PLATFORMS:
        raise ValueError('恢复任务的身份无法确认。')
    return PRIVATE / 'jobs' / job['id'] / (item['platform'] + '-upload-checkpoint.json')


def resume_attempt_count(item):
    count = item.get('resume_count', 0)
    if type(count) is not int or not 0 <= count <= RESUME_MAX_ATTEMPTS:
        raise ValueError('继续处理次数无法确认。')
    return count


def resume_checkpoint_path(job, item):
    """Latest existing proof, never an older fallback past a save or bad proof.

    A failed continuation may not export a new checkpoint. Its missing output
    does not erase the preceding sealed upload. An existing output must pass
    the same validator; callers cannot silently skip it for an older file.
    """
    original = upload_checkpoint_path(job, item)
    count = resume_attempt_count(item)
    attempts = original.parent / 'attempts'
    if original.parent.is_symlink() or attempts.is_symlink():
        raise ValueError('继续处理的历史目录无法确认。')
    paths = [(original, 0)] + [
        (attempts / (item['platform'] + '-resume-' + str(number)) / original.name, number)
        for number in range(1, RESUME_MAX_ATTEMPTS + 1)]
    # Check every possible attempt, including a reserved but not yet completed
    # one, before selecting any proof. A later save marker forbids fallback.
    for path, _ in paths:
        if path.parent.is_symlink():
            raise ValueError('继续处理的历史目录无法确认。')
        marker_path = path.with_suffix('.save-started.json')
        if marker_path.exists() or marker_path.is_symlink():
            raise ValueError('原任务已经开始保存，不能继续已上传素材。')
    if any(path.parent.exists() for path, number in paths if number > count):
        raise ValueError('继续处理的次数与历史目录不同。')
    if attempts.exists():
        prefix = item['platform'] + '-resume-'
        for directory in attempts.iterdir():
            if directory.name.startswith(prefix) and directory not in {path.parent for path, _ in paths[1:]}:
                raise ValueError('继续处理的历史目录无法确认。')
    for path, number in reversed(paths[:count + 1]):
        if path.exists() or path.is_symlink():
            return path, number
    raise ValueError('没有可继续的可信上传记录。')


def _resume_asset_hash(path, force=False):
    path = Path(path).expanduser().resolve()
    before = path.stat()
    signature = (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    saved = RESUME_ASSET_CACHE.get(str(path))
    if not force and saved and saved[0] == signature:
        return saved[1]
    digest = hash_asset(path)
    after = path.stat()
    if signature != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('素材在恢复预检时发生变化。')
    if len(RESUME_ASSET_CACHE) >= 512:
        RESUME_ASSET_CACHE.clear()
    RESUME_ASSET_CACHE[str(path)] = (signature, digest)
    return digest


def trusted_upload_checkpoint(job, item, context, force_assets=False):
    """Sealed upload proof only; the worker still verifies the live composer."""
    expected = item.get('expected')
    if (not isinstance(expected, dict) or expected.get('job_id') != job.get('id')
            or expected.get('platform_id') != item.get('platform') or not context.get('identity')):
        raise ValueError('原任务、平台或当前账号无法确认。')
    for owner in (item.get('account_identity'), expected.get('expected_account_identity')):
        if owner and owner != context['identity']:
            raise ValueError('当前账号与原任务不同，未继续处理。')
    path, checkpoint_attempt = resume_checkpoint_path(job, item)
    raw = _private_bytes(path, CHECKPOINT_MAX_BYTES)
    checkpoint = json.loads(raw)
    key_path, seal_key = checkpoint_key()
    bound = {**expected, 'account': {'verified': True, 'platform': item['platform'], 'identity': context['identity']}}
    proof = validate_checkpoint(checkpoint, seal_key=seal_key, expected=bound, platform=item['platform'])
    for role, asset in proof['assets'].items():
        source = Path(expected.get(role, '')).expanduser().resolve()
        if source.name != asset['name'] or source.stat().st_size != asset['size']:
            raise ValueError('素材与已上传记录不同，未继续处理。')
        if _resume_asset_hash(source, force_assets) != asset['sha256']:
            raise ValueError('素材内容与已上传记录不同，未继续处理。')
    return {'path': path, 'key_path': key_path, 'checkpoint_sha256': hashlib.sha256(raw).hexdigest(),
            'expected_sha256': canonical_sha(expected), 'account_identity': context['identity'],
            'checkpoint_attempt': checkpoint_attempt}


def resume_account_holds(job, item, context):
    expected = item.get('expected')
    if not isinstance(expected, dict):
        return True
    records = []
    for record in LEDGER.values():
        if not isinstance(record, dict):
            continue
        records.append(record)
        attempts = record.get('attempts', [])
        if not isinstance(attempts, list) or any(not isinstance(row, dict) or 'attempts' in row for row in attempts):
            if record.get('platform_id') == item['platform'] and record.get('browser') == job.get('browser') and retry_owner_matches(record, context):
                return True
            continue
        records.extend(attempts)
    for record in records:
        if record.get('platform_id') != item['platform'] or record.get('browser') != job.get('browser'):
            continue
        if not retry_owner_matches(record, context):
            continue
        same_material = all(record.get(key) == expected.get(key) for key in ('video_sha256', 'content_sha256', 'cover_sha256'))
        if (record.get('status') == 'risk_paused' or
                record.get('status') == 'platform_restricted' and not retry_status(record) or
                same_material and record.get('status') in VERIFIED_STATUSES | {'saved_unverified'}):
            return True
    return False


def _uploaded_resume_reference(job, item, context, force_assets=False):
    """Validate the original execution, including every save/risk/attempt hold."""
    try:
        count = resume_attempt_count(item)
    except ValueError:
        return None
    if (job.get('active') or job.get('cancelled') or job.get('action') != 'draft'
            or item.get('status') not in ('result_unknown', 'adapter_mismatch')
            or item.get('reused') or count >= RESUME_MAX_ATTEMPTS or PROCESS is not None):
        return None
    if any(row is not item and row.get('status') in ('pending', 'running') for row in job.get('results', [])):
        return None
    expected = item.get('expected')
    if not isinstance(expected, dict):
        return None
    history = item.get('execution_attempts', [])
    if not isinstance(history, list) or len(history) != count or any(not isinstance(row, dict) for row in history):
        return None
    # Resetting progress for a new continuation must not discard any earlier
    # save, cancellation or risk. Keep the original and every stopped attempt.
    for index, row in enumerate(history + [item]):
        if (row.get('status') not in ('result_unknown', 'adapter_mismatch')
                or row.get('platform') != item.get('platform') or row.get('expected') != expected
                or type(row.get('resume_count', 0)) is not int
                or row.get('resume_count', 0) != index):
            return None
        progress = row.get('progress') or {}
        if (not isinstance(progress, dict) or not isinstance(progress.get('completed', []), list)
                or not isinstance(progress.get('skipped', []), list)):
            return None
        if (progress.get('stage') in ('save', 'verify') or
                any(stage in ('save', 'verify') for stage in progress.get('completed', []) + progress.get('skipped', []))):
            return None
        evidence = row.get('evidence') or {}
        if not isinstance(evidence, dict) or not isinstance(evidence.get('verification') or {}, dict):
            return None
        if (evidence.get('draft_id') or evidence.get('saved_records') or
                (evidence.get('verification') or {}).get('reopened') is True):
            return None
    record = LEDGER.get(expected.get('operation_key'), {})
    if (record.get('job_id') != job.get('id') or record.get('status') not in ('result_unknown', 'adapter_mismatch')
            or record.get('execution_attempt', 0) != count
            or not retry_owner_matches(record, context) or resume_account_holds(job, item, context)):
        return None
    try:
        return trusted_upload_checkpoint(job, item, context, force_assets)
    except (ValueError, OSError, RuntimeError, TypeError, AttributeError):
        return None


def resume_reference(job, item, context, force_assets=False):
    """Eligibility for a bounded explicit resume of this exact inactive CURRENT."""
    if job is not CURRENT:
        return None
    return _uploaded_resume_reference(job, item, context, force_assets)


def resume_selection(job, item, context, force_assets=False):
    """Bind an inactive CURRENT reused row to its exact original execution."""
    if (job is not CURRENT or job.get('active') or job.get('cancelled')
            or job.get('action') != 'draft' or PROCESS is not None):
        return None
    if not item.get('reused'):
        proof = resume_reference(job, item, context, force_assets)
        return (job, item, proof) if proof else None
    record = retry_reference(job, item, context)
    source = recorded_source(record)
    if not source:
        return None
    original_job, original_item = source
    # The displayed wrapper must still refer to this material. In particular,
    # an account-wide hold on a different material is never a resume selector.
    if any(item['expected'].get(key) != original_item['expected'].get(key) for key in
           ('platform_id', 'video_sha256', 'content_sha256', 'cover_sha256')):
        return None
    proof = _uploaded_resume_reference(original_job, original_item, context, force_assets)
    return (original_job, original_item, proof) if proof else None


def existing_asset(value, extensions, base=None):
    if not value: return ''
    p = Path(str(value)).expanduser()
    if not p.is_absolute() and base: p = base / p
    p = p.resolve()
    if not p.is_file() or p.suffix.lower() not in extensions:
        raise ValueError(f'文件不存在或格式不支持：{p.name}')
    ASSETS.add(str(p))
    return str(p)


def load_material(value, selected_video=None):
    p = Path(value).expanduser().resolve()
    if not p.exists(): raise ValueError('路径不存在，请重新选择。')
    folder = p if p.is_dir() else p.parent
    videos = sorted(str(x.resolve()) for x in folder.iterdir() if x.is_file() and x.suffix.lower() in VIDEO_EXT)
    if not videos: raise ValueError('这个文件夹里没有视频。')
    chosen = str(Path(selected_video).resolve()) if selected_video else (str(p) if p.is_file() else videos[0])
    if chosen not in videos: raise ValueError('请选择当前素材文件夹里的视频。')
    video = Path(chosen)
    metadata = {}
    config = folder / 'publish.json'
    if config.is_file():
        if config.stat().st_size > 256 * 1024: raise ValueError('publish.json 文件过大。')
        metadata = json.loads(config.read_text(encoding='utf-8-sig'))
        if not isinstance(metadata, dict): raise ValueError('publish.json 需要是一个 JSON 对象。')
    elif (video.with_suffix('.txt')).is_file():
        lines = video.with_suffix('.txt').read_text(encoding='utf-8-sig').splitlines()
        if lines: metadata = {'title': lines[0], 'description': '\n'.join(lines[1:]).strip()}
    else:
        for name in ('文案.txt', 'copy.txt'):
            text = folder / name
            if text.is_file():
                lines = text.read_text(encoding='utf-8-sig').splitlines()
                if lines: metadata = {'title': lines[0], 'description': '\n'.join(lines[1:]).strip()}
                break
    def cover(key, stems):
        if metadata.get(key): return existing_asset(metadata[key], IMAGE_EXT, folder)
        for stem in stems:
            for ext in sorted(IMAGE_EXT):
                candidate = folder / (stem + ext)
                if candidate.is_file(): return existing_asset(candidate, IMAGE_EXT)
        return ''
    tags = metadata.get('tags', [])
    from text_import import normalize_tags
    if not isinstance(tags, (str, list)): raise ValueError('tags 需要是文字数组或用逗号分隔的文字。')
    if isinstance(tags, list) and any(not isinstance(tag, str) for tag in tags):
        raise ValueError('tags 中的每一项都需要是文字。')
    tags = normalize_tags(tags)
    title = str(metadata.get('title') or video.stem)
    material = {
        'folder': str(folder), 'videos': videos, 'video': existing_asset(video, VIDEO_EXT),
        'title': title, 'description': str(metadata.get('description', metadata.get('desc', ''))),
        'tags': tags,
        'short_title': str(metadata.get('short_title') or ''),
        'platform_overrides': metadata.get('platform_overrides', {}),
        'cover': cover('cover', ['cover', '封面', video.stem]),
        'cover_landscape': cover('cover_landscape', ['cover-4x3', 'cover-16x9', '封面-横版']),
        'cover_portrait': cover('cover_portrait', ['cover-3x4', 'cover-9x16', '封面-竖版']),
    }
    if not any(material[key] for key in ('cover', 'cover_landscape', 'cover_portrait')):
        try:
            from material_probe import generate_cover
            material['cover'] = existing_asset(generate_cover(video, PRIVATE / 'auto-covers', hash_asset(video)), IMAGE_EXT)
            material['cover_generated'] = True
        except Exception:
            material['cover_generation_error'] = '未能自动提取视频封面，请选择图片后预检。'
    return material


def choose(kind):
    scripts = {
        'folder': 'POSIX path of (choose folder with prompt "选择视频、封面和文案所在的素材文件夹")',
        'video': 'POSIX path of (choose file with prompt "选择一个视频")',
        'cover': 'POSIX path of (choose file with prompt "选择封面图片")',
    }
    if kind not in scripts: raise ValueError('选择类型无效。')
    result = subprocess.run(['/usr/bin/osascript', '-e', scripts[kind]], capture_output=True, text=True)
    if result.returncode:
        if '-128' in result.stderr: return None
        raise RuntimeError('系统文件选择器未能打开；可以直接粘贴文件夹路径。')
    p = result.stdout.strip()
    return existing_asset(p, IMAGE_EXT) if kind == 'cover' else load_material(p)


def current_sessions():
    """Short-lived status from this running App; never reuse a previous PID."""
    try:
        value=json.loads((PRIVATE / 'embedded-session-status.json').read_text(encoding='utf-8'))
        app=json.loads((PRIVATE / 'embedded-app-status.json').read_text(encoding='utf-8'))
        if value.get('pid')!=app.get('pid') or not 0<=time.time()-value.get('checked_at',0)<=15:
            return {}
        result={}
        for key,item in value.get('platforms',{}).items():
            if key not in PLATFORMS or not isinstance(item,dict):continue
            result[key]={k:item[k] for k in ('status','identity','checked_at','capabilities') if k in item}
        return result
    except (OSError,ValueError,TypeError):
        return {}


def state(embedded=False):
    ensure_store()
    with LOCK:
        browser = 'embedded' if embedded else 'external'
        platforms = []
        sessions = current_sessions() if embedded else {}
        for platform, name in PLATFORMS.items():
            context = account_context(platform, browser)
            platforms.append({'id': platform, 'name': name, 'policy': PLATFORM_POLICIES[platform],
                'automation_ready': PLATFORM_POLICIES[platform].get('automation_ready', True),
                'readiness_message': PLATFORM_POLICIES[platform].get('readiness_message', ''),
                'login_state': 'last_verified' if context['verified_at'] else 'needs_confirmation',
                'current_session':sessions.get(platform,{}),
                'last_verified_at': context['verified_at'], 'session_record_present': context['session_record_present']})
        # Omit internal expected payloads and raw account UID from UI responses.
        def public_job(job):
            if not job: return None
            result = {k: v for k, v in job.items() if k not in ('results', 'check', 'resume_platform')}
            private_fields = {'expected', 'evidence', 'account_identity', 'execution_attempts', 'resume_binding', 'resume_count'}
            result['results'] = [{k: v for k, v in item.items() if k not in private_fields} for item in job['results']]
            for source_item, item in zip(job['results'], result['results']):
                reference = None
                if job is CURRENT and not job.get('active') and job.get('action') == 'draft':
                    context = account_context(source_item['platform'], job['browser'])
                    reference = retry_reference(job, source_item, context)
                item.update(reused_details(job, source_item))
                item['retryable'] = reference is not None
                item['retry_previous_job_id'] = reference['job_id'] if reference else None
                selection = resume_selection(job, source_item, account_context(source_item['platform'], job['browser']))
                item['resumable'] = selection is not None
                item['resume_previous_job_id'] = selection[0]['id'] if selection else None
                message=item.get('message','')
                if item.get('status')=='platform_restricted' and '多个可能的素材或内容控件' in message:
                    item.update(status='adapter_mismatch',message='上次任务未能唯一识别页面控件，现场与记录已保留；登录状态见上方。\n原任务原因：' + message)
                elif 'Cannot transfer files larger than 50' in message:
                    item['message']='上次任务的大视频导入失败，导入方式已修复；该任务尚未核验，不会自动重传。\n原任务原因：' + message
                elif 'Page.goto: net::ERR_ABORTED' in message:
                    item['message']='上次任务遇到页面跳转冲突，跳转方式已修复；该任务尚未核验，不会自动重传。\n原任务原因：' + message
                elif item.get('status')=='needs_reauthentication' and '创作者身份未确认' in message:
                    item['message']='上次任务未能确认创作者身份；当前登录状态见上方，原任务尚未完成。\n原任务原因：' + message
            return result
        return {'app': 'sau-draft-workbench', 'api_version': 2, 'embedded_supported': True, 'token': TOKEN,
                'platforms': platforms, 'job': public_job(CURRENT), 'editor': EDITOR,
                'history': [public_job(j) for j in HISTORY[-10:]]}


def log(line):
    with LOCK:
        if CURRENT:
            CURRENT['logs'].append(sanitize_log(line))
            CURRENT['logs'] = CURRENT['logs'][-160:]
            persist()


def progress_state():
    # main() initializes/reconciles the store before serving HTTP. Polling this
    # endpoint itself must never restore records, write files or replay a job.
    with LOCK:
        # Existing reused displays may predate the source-step projection. Read
        # their exact source without writing user records or replaying a job.
        shown = copy.deepcopy(CURRENT)
        if shown:
            for original, item in zip(CURRENT['results'], shown['results']):
                item.update(reused_details(CURRENT, original))
        return progress_summary(shown, PLATFORMS)


def worker_progress(job, item, line):
    # The worker closure, not stdout alone, binds an event to the running item.
    event = parse_progress(line, job['id'], item['platform'])
    if event is None:
        return False
    with LOCK:
        if CURRENT is not job or not job.get('active') or job.get('cancelled') or item.get('status') != 'running':
            return False
        if not any(candidate is item for candidate in job.get('results', [])):
            return False
        if not apply_progress(item, event, time.time()):
            return False
        persist()
        return True


STATUS_MESSAGES = {
    'published_submission_verified': '投稿已提交，等待平台审核',
    'published_unverified': '已尝试投稿，回执待确认；不会自动再次提交',
    'publication_blocked': '提交前核验未通过，页面已保留',
    'local_draft_verified': '本地草稿已重开核验，保存在当前浏览器',
    'platform_draft_verified': '平台草稿已重开核验',
    'platform_snapshot_verified': '平台暂存快照已重开核验；新快照可能覆盖此份',
    'saved_unverified': '已尝试保存，核验证据不足；不会自动重传',
    'result_unknown': '结果待确认；不会自动重传',
    'risk_paused': '平台出现风险提示，已暂停此账号的自动处理',
    'platform_restricted': '平台权限未开或受限，已停止此账号的自动处理',
    'adapter_mismatch': '平台页面控件待适配，现场已保留；账号登录状态另行核对',
    'needs_reauthentication': '当前登录未通过验证，请在平台页面重新登录',
    'account_mismatch': '当前账号与登录记录不同，已停止；请重新确认账号',
    'failed': '执行失败，请查看运行记录',
    'cancelled': '已停止',
}


def transition(job, item, status, message=None, evidence=None):
    with LOCK:
        item.update(status=status, message=sanitize_log(message or STATUS_MESSAGES.get(status, status)))
        finish_progress(item, status, time.time())
        if evidence is not None: item['evidence'] = evidence
        record_operation(job, item)
        persist()


def worker(job, payload):
    global PROCESS
    directory = PRIVATE / 'jobs' / job['id']
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        for item in job['results']:
            if job.get('resume_platform') and item['platform'] != job['resume_platform']:
                continue
            if item['status'] != 'pending': continue
            if job['cancelled']:
                transition(job, item, 'cancelled', '已停止，此平台尚未执行'); continue
            with LOCK:
                item['started_at'] = time.time()
                transition(job, item, 'running', ('请在 App 内的平台页面扫码登录' if job['browser'] == 'embedded' else '请在浏览器扫码登录') if job['action'] == 'login' else '现场确认账号，上传时填写内容，核验后提交投稿' if job['action'] == 'publish' else '现场确认账号，上传素材并重开核验草稿')
            log(f"—— {item['name']} ——")
            execution_directory = directory
            if item.get('resume_count'):
                execution_directory = directory / 'attempts' / (item['platform'] + '-resume-' + str(item['resume_count']))
                execution_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            receipt = execution_directory / f"{item['platform']}-receipt.json"
            receipt.unlink(missing_ok=True)
            cmd = runner_command() + [job['action'], '--platform', item['platform']]
            if job['action'] in ('draft', 'publish'):
                expected = item['expected']
                # Reject a changed video before starting a subprocess/browser operation.
                if hash_asset(expected['video']) != expected['video_sha256']:
                    transition(job, item, 'failed', '视频在预检后发生变化，上传前已停止'); continue
                key_path, _ = checkpoint_key(create=True)
                checkpoint_output = execution_directory / (item['platform'] + '-upload-checkpoint.json')
                cmd += ['--checkpoint-key-file', str(key_path), '--checkpoint-output', str(checkpoint_output)]
                if item.get('resume_count'):
                    binding = item.get('resume_binding') or {}
                    context = account_context(item['platform'], job['browser'])
                    proof = trusted_upload_checkpoint(job, item, context, force_assets=True)
                    if (CURRENT is not job or job.get('resume_platform') != item['platform']
                            or resume_account_holds(job, item, context) or binding.get('job_id') != job['id']
                            or binding.get('platform') != item['platform'] or binding.get('browser') != job['browser']
                            or binding.get('resume_attempt') != item['resume_count']
                            or any(binding.get(key) != proof[key] for key in ('checkpoint_sha256', 'expected_sha256', 'account_identity', 'checkpoint_attempt'))):
                        raise ValueError('原任务、账号或已上传记录发生变化，已停止继续处理。')
                    cmd += ['--resume-checkpoint', str(proof['path'])]
                payload_path = execution_directory / f"{item['platform']}-素材信息.json"
                atomic_json(payload_path, expected)
                cmd += ['--payload', str(payload_path), '--receipt', str(receipt)]
            if job['browser'] == 'embedded': cmd += ['--embedded']
            login_evidence = None
            with LOCK:
                if job['cancelled']:
                    transition(job, item, 'cancelled', '已停止，此平台尚未执行'); continue
                progress_env = {**os.environ, 'SAU_PROGRESS_JOB_ID': job['id'],
                                'SAU_PROGRESS_PLATFORM': item['platform']}
                PROCESS = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding='utf-8', start_new_session=True, env=progress_env,
                                           **subprocess_options())
                proc = PROCESS
                item['process_started'] = True
                persist()
            log_path = execution_directory / f"{item['platform']}.log"
            with log_path.open('w', encoding='utf-8') as output:
                os.chmod(log_path, 0o600)
                for line in proc.stdout:
                    if line.startswith(PROGRESS_PREFIX):
                        # Neither valid nor rejected progress payloads enter raw logs.
                        if worker_progress(job, item, line):
                            progress = item['progress']
                            output.write('步骤进度：' + progress['label'] + ' · ' + progress['state'] + '\n')
                    elif line.startswith('LOGIN_RESULT='):
                        try: login_evidence = json.loads(line.split('=', 1)[1])
                        except ValueError: pass
                        log('登录现场验证已返回结果')
                        output.write('登录现场验证已返回结果\n')
                    elif line.startswith('DRAFT_RESULT='):
                        log('草稿核验已返回结构化回执，正在核对任务身份与证据')
                        output.write('草稿核验已返回结构化回执\n')
                    else:
                        clean = sanitize_log(line)
                        output.write(clean + '\n'); log(clean)
                    output.flush()
            rc = proc.wait()
            if job['browser'] == 'embedded':
                try: bridge_request('/cancel', {'platform': item['platform']})
                except RuntimeError: pass
            with LOCK: PROCESS = None
            if job['action'] == 'login':
                account = (login_evidence or {}).get('account', {})
                if job['cancelled']:
                    transition(job, item, 'cancelled', '登录等待已结束，已有会话资料保留')
                elif rc == 0 and login_evidence and login_evidence.get('status') == 'login_verified' and account.get('verified') is True and account.get('identity') and account.get('platform') == item['platform']:
                    with LOCK:
                        ACCOUNTS[job['browser'] + ':' + item['platform']] = {'account': account, 'verified_at': time.time()}
                    transition(job, item, 'login_verified', '登录已在现场验证并保留；下次执行仍会复核')
                else:
                    transition(job, item, 'needs_reauthentication', '登录未取得现场账号验证回执，请重新确认')
            elif receipt.is_file():
                try:
                    if receipt.stat().st_mtime < item['started_at']: raise ValueError('收到旧任务回执。')
                    validator = validate_publication_receipt if job['action'] == 'publish' else validate_receipt
                    evidence = validator(json.loads(receipt.read_text(encoding='utf-8')), item['expected'])
                    status = evidence['status']
                    if status in VERIFIED_STATUSES | PUBLICATION_VERIFIED_STATUSES:
                        with LOCK:
                            ACCOUNTS[job['browser'] + ':' + item['platform']] = {'account': evidence['account'], 'verified_at': time.time()}
                    message = STATUS_MESSAGES.get(status, status)
                    if evidence.get('message'): message += '：' + str(evidence['message'])
                    transition(job, item, status, message=message, evidence=evidence)
                except (ValueError, OSError) as exc:
                    log('回执未通过本次任务核验：' + str(exc))
                    transition(job, item, 'result_unknown')
            elif job['cancelled']:
                transition(job, item, 'result_unknown', '任务已停止，已开始的平台结果待确认；不会自动重传')
            else:
                transition(job, item, 'result_unknown', '执行结束但未取得本次任务回执；不会自动重传')
    except Exception as e:
        log('任务未完成：' + str(e))
        for item in job['results']:
            if item['status'] == 'running':
                transition(job, item, 'result_unknown' if item.get('process_started') else 'failed', str(e))
            elif item['status'] == 'pending': transition(job, item, 'cancelled', '任务停止，此平台尚未执行')
    finally:
        with LOCK:
            PROCESS = None
            job['active'] = False
            persist()


def require_automated_platform(platform):
    policy = PLATFORM_POLICIES[platform]
    if policy.get('automation_ready') is False:
        raise ValueError(policy['name'] + '：' + policy['readiness_message'] + '。请使用「打开平台」进入 App 内的创作页面。')


def start_job(data):
    global CURRENT, HISTORY
    action = data.get('action')
    if action not in ('login', 'draft', 'publish'): raise ValueError('只支持登录、保存草稿和提交投稿。')
    if not isinstance(data.get('platforms'), list): raise ValueError('请选择有效的平台。')
    platforms = list(dict.fromkeys(data.get('platforms', [])))
    if not platforms or any(x not in PLATFORMS for x in platforms): raise ValueError('请选择有效的平台。')
    if action == 'login' and len(platforms) != 1: raise ValueError('请逐个平台登录。')
    for platform in platforms:
        require_automated_platform(platform)
    ensure_store()
    payload = dict(data.get('payload') or {})
    browser = data.get('browser', 'external')
    if browser not in ('external', 'embedded'): raise ValueError('浏览器类型无效。')
    if action in ('draft', 'publish'):
        payload['workflow_action'] = action
        check = preflight({**data, 'payload': payload})
        if check['errors']:
            raise ValueError('素材预检未通过：' + '；'.join((PLATFORMS.get(e['platform'], '') + ' ' + e['message']).strip() for e in check['errors']))
        payload = check['payload']
    with LOCK:
        if CURRENT and CURRENT['active']: raise ValueError('当前任务尚未结束，请等待或停止任务。')
        contexts = {platform: account_context(platform, browser) for platform in platforms}
        if action in ('draft', 'publish') and any(check['fingerprints'][platform]['account_key'] != contexts[platform]['account_key'] for platform in platforms):
            raise ValueError('预检期间当前账号发生变化，请重新检查素材；尚未上传。')
        if CURRENT: HISTORY.append(CURRENT)
        CURRENT = {'id': time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8], 'action': action, 'browser': browser, 'active': True,
                   'created_at': time.time(),
                   'cancelled': False, 'logs': [], 'results': [
                       {'platform': x, 'name': PLATFORMS[x], 'status': 'pending', 'message': '等待执行'} for x in platforms]}
        if action in ('draft', 'publish'):
            for item in CURRENT['results']:
                platform = item['platform']
                context = contexts[platform]
                item['account_identity'] = context['identity']
                item['expected'] = build_platform_payload(check, platform, CURRENT['id'], secrets.token_hex(24))
                item['expected']['expected_account_identity'] = context['identity']
                duplicate = duplicate_record(check['fingerprints'][platform], context['identity'], browser)
                if duplicate:
                    item.update(status=duplicate['status'], **duplicate_details(duplicate, item['expected']),
                                reused=True, previous_job_id=duplicate['job_id'], draft_id=duplicate.get('draft_id'), save_location=duplicate.get('save_location'))
        CURRENT['active'] = any(x['status'] == 'pending' for x in CURRENT['results'])
        persist()
        if not CURRENT['active']: return CURRENT
        threading.Thread(target=worker, args=(CURRENT, payload), daemon=True).start()
        return CURRENT


def retry_job(data):
    """One explicit, scoped recovery attempt; ordinary start keeps dedup intact."""
    global CURRENT, HISTORY
    ensure_store()
    platform, browser = data.get('platform'), data.get('browser')
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise ValueError('恢复重试只能选择一个有效平台。')
    require_automated_platform(platform)
    if browser not in ('external', 'embedded') or not isinstance(data.get('payload'), dict):
        raise ValueError('恢复重试的浏览器或素材格式无效。')
    with LOCK:
        previous = CURRENT
        if not previous or previous.get('active'):
            raise ValueError('当前任务尚未结束，不能恢复重试。')
        if data.get('expected_job_id') != previous['id']:
            raise ValueError('当前任务已变化，请刷新后重新选择恢复重试。')
        if previous.get('action') != 'draft' or previous.get('browser') != browser:
            raise ValueError('恢复重试必须使用原草稿任务的浏览器。')
        items = [item for item in previous.get('results', []) if item.get('platform') == platform]
        if len(items) != 1:
            raise ValueError('当前任务没有唯一的对应平台记录。')
        context = account_context(platform, browser)
        record = retry_reference(previous, items[0], context)
        if record is None:
            raise ValueError('此记录不能恢复重试；风险、真实权限限制或已核验草稿不会绕过。')
        if data.get('previous_job_id') != record['job_id']:
            raise ValueError('前次任务引用已变化，未执行恢复重试。')
        check = preflight({'payload': data['payload'], 'platforms': [platform], 'browser': browser})
        if check['errors']:
            raise ValueError('素材预检未通过：' + '；'.join(error['message'] for error in check['errors']))
        fingerprint = check['fingerprints'][platform]
        if fingerprint['account_key'] != context['account_key']:
            raise ValueError('预检期间当前账号发生变化，未执行恢复重试。')
        if not all(fingerprint[key] == record.get(key) for key in ('video_sha256', 'content_sha256', 'cover_sha256')):
            raise ValueError('素材与待恢复记录不同，请使用原视频、内容和封面。')
        # A newly appeared account hold/record must not be bypassed by an older
        # reference. Both checks run under the same CURRENT/ledger lock.
        blocking = duplicate_record(fingerprint, context['identity'], browser)
        if blocking is not record or not retry_owner_matches(record, context):
            raise ValueError('当前账号或阻止记录已变化，未执行恢复重试。')
        job_id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        expected = build_platform_payload(check, platform, job_id, secrets.token_hex(24))
        expected['expected_account_identity'] = context['identity']
        recovery = {'job_id': record['job_id'], 'operation_key': record['operation_key'],
                    'requested_from_job_id': previous['id'], 'requested_at': time.time()}
        item = {'platform': platform, 'name': PLATFORMS[platform], 'status': 'pending',
                'message': '已明确选择恢复重试；执行前仍会现场核对账号和平台',
                'account_identity': context['identity'], 'expected': expected, 'recovery_of': recovery}
        HISTORY.append(previous)
        CURRENT = {'id': job_id, 'action': 'draft', 'browser': browser, 'active': True,
                   'created_at': time.time(), 'cancelled': False, 'logs': [], 'results': [item]}
        persist()
        threading.Thread(target=worker, args=(CURRENT, check['payload']), daemon=True).start()
        return CURRENT


def resume_job(data):
    """Continue one sealed, not-yet-saved upload; never create a new upload job."""
    global CURRENT, HISTORY
    ensure_store()
    platform, browser = data.get('platform'), data.get('browser')
    if platform not in PLATFORMS or browser not in ('external', 'embedded') or not isinstance(data.get('payload'), dict):
        raise ValueError('继续处理的平台、浏览器或素材格式无效。')
    require_automated_platform(platform)
    with LOCK:
        displayed_job = CURRENT
        if not displayed_job or displayed_job.get('active') or data.get('expected_job_id') != displayed_job.get('id'):
            raise ValueError('当前任务已变化或尚未结束，未继续处理。')
        if displayed_job.get('action') != 'draft' or displayed_job.get('browser') != browser:
            raise ValueError('只能在原草稿任务的浏览器继续处理。')
        items = [item for item in displayed_job.get('results', []) if item.get('platform') == platform]
        if len(items) != 1:
            raise ValueError('没有唯一的原平台任务记录，未继续处理。')
        displayed_item = items[0]
        displayed_revision = canonical_sha(displayed_job)
        context = account_context(platform, browser)
        selection = resume_selection(displayed_job, displayed_item, context, force_assets=True)
        if selection is None:
            raise ValueError('此任务没有可继续的可信上传记录，或已经保存、停止、受限、存在风险、已达到继续次数上限；不会重新上传。')
        job, item, proof = selection
        if ((displayed_item.get('reused') and data.get('previous_job_id') != job['id'])
                or data.get('previous_job_id') not in (None, job['id'])):
            raise ValueError('原任务引用已变化，未继续处理。')
        original_revision = canonical_sha(job)
        check = preflight({'payload': data['payload'], 'platforms': [platform], 'browser': browser})
        if check['errors']:
            raise ValueError('素材预检未通过，未继续处理。')
        expected = item['expected']
        fingerprint = check['fingerprints'][platform]
        if (check['payload']['video'] != expected.get('video')
                or any(fingerprint[key] != expected.get(key) for key in ('video_sha256', 'content_sha256', 'cover_sha256'))
                or check['mappings'][platform] != expected.get('platform_fields')):
            raise ValueError('当前视频、封面或内容与原任务不同，未继续处理。')
        # Re-check references after file/account callbacks, still under LOCK.
        confirmed_context = account_context(platform, browser)
        confirmed = resume_selection(displayed_job, displayed_item, confirmed_context, force_assets=True)
        if (CURRENT is not displayed_job or canonical_sha(displayed_job) != displayed_revision
                or canonical_sha(job) != original_revision or confirmed is None
                or canonical_sha(confirmed[0]) != original_revision or confirmed[2] != proof
                or confirmed_context != context):
            raise ValueError('预检期间当前任务、账号或上传记录发生变化，未继续处理。')
        attempt = resume_attempt_count(item) + 1
        attempt_directory = PRIVATE / 'jobs' / job['id'] / 'attempts' / (platform + '-resume-' + str(attempt))
        attempt_directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            attempt_directory.mkdir(mode=0o700)
        except FileExistsError:
            raise ValueError('此轮已经有继续处理的记录，未重复执行。') from None
        original = {key: copy.deepcopy(value) for key, value in item.items() if key not in ('execution_attempts', 'resume_binding')}
        original['logs_snapshot'] = list(job.get('logs', []))
        original['ledger_snapshot'] = copy.deepcopy(LEDGER.get(expected['operation_key'], {}))
        if job is not displayed_job:
            # Keep the reused display as audit history; resume the same original
            # job/nonce/attempt chain so a new upload is never implicitly made.
            HISTORY = [entry for entry in HISTORY if entry.get('id') != job['id']]
            HISTORY.append(displayed_job)
            CURRENT = job
        item.setdefault('execution_attempts', []).append(original)
        item['resume_count'] = attempt
        item['resume_binding'] = {key: proof[key] for key in ('checkpoint_sha256', 'expected_sha256', 'account_identity', 'checkpoint_attempt')}
        item['resume_binding'].update(job_id=job['id'], platform=platform, browser=browser,
                                     resume_attempt=attempt, requested_from_job_id=displayed_job['id'])
        now = time.time()
        item.update(status='pending', message='继续原任务的已上传视频；执行前会复核账号和现有素材，不重新上传',
            progress={'stage': 'session', 'label': '确认登录', 'state': 'running', 'started_at': now,
                      'updated_at': now, 'completed': [], 'skipped': [], 'detail': '正在检查登录状态'})
        for key in ('evidence', 'process_started', 'started_at', 'reused', 'previous_job_id'):
            item.pop(key, None)
        job.update(active=True, cancelled=False, resume_platform=platform)
        job['logs'].append('已明确选择第 ' + str(attempt) + ' 次继续已上传素材：原失败记录保留；不会重新上传。')
        persist()
        threading.Thread(target=worker, args=(job, check['payload']), daemon=True).start()
        return job


def cancel_job(data):
    with LOCK:
        if CURRENT and CURRENT['active']:
            if data.get('expected_job_id') is not None and data['expected_job_id'] != CURRENT['id']:
                raise ValueError('当前任务已变化，请重新确认。')
            if data.get('expected_action') is not None and data['expected_action'] != CURRENT['action']:
                raise ValueError('当前任务类型已变化，请重新确认。')
            CURRENT['cancelled'] = True
            persist()
            if PROCESS and PROCESS.poll() is None: stop_runner(PROCESS)
    return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def reply(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def do_HEAD(self):
        if not self.valid_host(): return self.send_error(403)
        parsed = urlparse(self.path)
        if parsed.path != '/media': return self.send_error(404)
        path = parse_qs(parsed.query).get('path', [''])[0]
        if path not in ASSETS or Path(path).suffix.lower() not in VIDEO_EXT or not Path(path).is_file():
            return self.send_error(404)
        from media_preview import stream_video
        stream_video(self, path, head=True)

    def do_GET(self):
        if not self.valid_host(): return self.reply({'error': 'Invalid host'}, 403)
        parsed = urlparse(self.path)
        if parsed.path == '/api/state': return self.reply(state(parse_qs(parsed.query).get('embedded') == ['1']))
        if parsed.path == '/api/progress': return self.reply(progress_state())
        if parsed.path == '/':
            body = (ROOT / 'draft_workbench.html').read_bytes()
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if parsed.path.startswith('/ui-assets/'):
            # Only these public UI assets are served; private state and paths stay inaccessible.
            name = parsed.path.removeprefix('/ui-assets/')
            if name not in {'glass.css', 'glass.js', 'alpine-dawn-v1.png', 'material-editor.css', 'material-editor.js', 'tag-editor.js', 'field-overrides.js', 'field-overrides.css', 'cover-actions.js'}:
                return self.reply({'error': 'Not found'}, 404)
            path = ROOT / 'ui-assets' / name
            if not path.is_file(): return self.reply({'error': 'Not found'}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'application/octet-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if parsed.path == '/media':
            path = parse_qs(parsed.query).get('path', [''])[0]
            if path not in ASSETS or Path(path).suffix.lower() not in VIDEO_EXT or not Path(path).is_file():
                return self.reply({'error': '视频不可用，请重新导入'}, 404)
            from media_preview import stream_video
            return stream_video(self, path)
        if parsed.path == '/asset':
            path = parse_qs(parsed.query).get('path', [''])[0]
            if path not in ASSETS or Path(path).suffix.lower() not in IMAGE_EXT or not Path(path).is_file(): return self.reply({'error': '图片不可用'}, 404)
            if parse_qs(parsed.query).get('clipboard') == ['1']:
                # Only registered cover images are decoded; never read arbitrary
                # renderer paths or replace the clipboard on a decoding failure.
                from io import BytesIO
                from PIL import Image, ImageOps
                try:
                    if Path(path).stat().st_size > 64 * 1024 * 1024:
                        raise ValueError('图片过大')
                    with Image.open(path) as source:
                        if source.width * source.height > 40_000_000:
                            raise ValueError('图片像素过大')
                        output = BytesIO()
                        ImageOps.exif_transpose(source).convert('RGBA').save(output, format='PNG')
                        body = output.getvalue()
                    if len(body) > 64 * 1024 * 1024:
                        raise ValueError('图片过大')
                except Exception:
                    return self.reply({'error': '封面无法复制，请重新导入图片'}, 422)
                self.send_response(200); self.send_header('Content-Type', 'image/png')
                self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
            body = Path(path).read_bytes()
            self.send_response(200); self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
        return self.reply({'error': 'Not found'}, 404)

    def do_POST(self):
        global PROCESS
        if not self.valid_host() or self.headers.get('X-Workbench-Token') != TOKEN:
            return self.reply({'error': '请从本机工作台操作。'}, 403)
        if self.headers.get('Origin') not in (None, URL, f'http://localhost:{PORT}'):
            return self.reply({'error': 'Invalid origin'}, 403)
        if 'application/json' not in self.headers.get('Content-Type', ''):
            return self.reply({'error': 'Invalid content type'}, 415)
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 256 * 1024: raise ValueError('请求内容过大或为空。')
            data = json.loads(self.rfile.read(size))
            if self.path == '/api/choose': result = choose(data.get('kind'))
            elif self.path == '/api/cover':
                result = existing_asset(data.get('path'), IMAGE_EXT)
                if not result: raise ValueError('请选择封面图片。')
                from material_probe import validate_image
                validate_image(result)
            elif self.path == '/api/import-text':
                from text_import import read_text_field
                result = read_text_field(data.get('path'), data.get('field'))
            elif self.path == '/api/media-info':
                path = existing_asset(data.get('path'), VIDEO_EXT)
                if not path: raise ValueError('请先导入视频。')
                from media_preview import describe_video
                result = describe_video(path)
            elif self.path == '/api/frame':
                path = existing_asset(data.get('path'), VIDEO_EXT)
                if not path: raise ValueError('请先导入视频。')
                from frame_capture import capture_frame
                result = capture_frame(path, PRIVATE / 'frame-covers', data.get('time'), data.get('role'), data.get('ratio', 'original'))
                ASSETS.add(result['path'])
            elif self.path == '/api/load': result = load_material(data.get('path', ''), data.get('video'))
            elif self.path == '/api/editor': result = save_editor(data)
            elif self.path == '/api/editor/clear': result = clear_editor(data)
            elif self.path == '/api/text-check':
                caps={p:s.get('capabilities',{}) for p,s in current_sessions().items()} if data.get('browser')=='embedded' else {}
                result=check_text(data.get('payload',{}),data.get('platforms',[]),caps)
            elif self.path == '/api/preflight': result = preflight(data)
            elif self.path == '/api/start': result = start_job(data)
            elif self.path == '/api/retry': result = retry_job(data)
            elif self.path == '/api/resume':
                resume_job(data)
                result = state(data.get('browser') == 'embedded')['job']
            elif self.path == '/api/cancel': result = cancel_job(data)
            else: return self.reply({'error': 'Not found'}, 404)
            self.reply({'result': result})
        except Exception as e: self.reply({'error': str(e)}, 400)


def healthy():
    try:
        with urllib.request.urlopen(URL + '/api/state', timeout=1) as response:
            return json.loads(response.read()).get('app') == 'sau-draft-workbench'
    except Exception: return False


def main():
    parser = argparse.ArgumentParser(description='Social Auto Upload 本地草稿工作台')
    parser.add_argument('--launch', action='store_true'); parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True, mode=0o700)
    if args.launch:
        if not healthy():
            output = (PRIVATE / 'workbench.log').open('a')
            subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=ROOT, stdout=output, stderr=output, start_new_session=True)
            for _ in range(40):
                if healthy(): break
                time.sleep(0.25)
            else: raise RuntimeError('工作台启动失败，请查看 .draft-workbench/workbench.log。')
        if not args.no_open: subprocess.run(['/usr/bin/open', '-a', 'Google Chrome', URL], check=True)
        print('草稿工作台已启动：' + URL)
        return
    ensure_store()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('草稿工作台：' + URL, flush=True)
    try: server.serve_forever()
    finally: server.server_close()


if __name__ == '__main__': main()
