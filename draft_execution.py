"""Persistent browser draft execution. No public publishing operation."""
import argparse
import asyncio
import json
import os
import re
import stat
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from patchright.async_api import async_playwright
from uploader.draft_only import (install_draft_guard, uninstall_draft_guard, save_draft, draft_resume_active,
                                 checkpoint_after_upload, read_checkpoint_composer_media)
from uploader.draft_evidence import CheckpointError, validate_checkpoint, CHECKPOINT_MAX_BYTES, IDENTITY_KEYS
from uploader.embedded_browser import EmbeddedBrowser, embedded_account_file, PRIVATE
from uploader.draft_session import DraftInterrupted, inspect_session, observe_session, verify_session, run_with_session_watch, current_owner_state
from uploader.local_files import set_local_files
from uploader.draft_progress import configure as configure_progress, step as progress_step, emit as emit_progress, operation as progress_operation, OPERATIONS
from urllib.parse import urlparse
from runtime_support import open_private_read, private_stat_ok

ROOT = Path(__file__).resolve().parent
PLATFORMS = {'douyin': '抖音', 'xiaohongshu': '小红书', 'tencent': '视频号', 'bilibili': 'B站', 'kuaishou': '快手',
             'toutiao': '今日头条', 'xigua': '西瓜视频'}
URLS = {
    'douyin': 'https://creator.douyin.com/creator-micro/content/upload',
    'xiaohongshu': 'https://creator.xiaohongshu.com/publish/publish?target=video',
    'tencent': 'https://channels.weixin.qq.com/platform/post/create',
    'bilibili': 'https://member.bilibili.com/platform/upload/video/frame',
    'kuaishou': 'https://cp.kuaishou.com/article/publish/video',
    'toutiao': 'https://mp.toutiao.com/profile_v4/xigua/upload-video',
    'xigua': 'https://studio.ixigua.com/',
}

# These portals are available for in-app sign-in and material preparation.
# Their current account/editor/save contracts must be verified before a file
# is sent; in particular Xigua's official portal now redirects to Douyin.
PORTAL_ONLY = frozenset(('toutiao', 'xigua'))


def require_automated_draft(platform):
    if platform in PORTAL_ONLY:
        raise DraftInterrupted(PLATFORMS[platform] + '已接入创作平台入口；自动上传与草稿核验尚待适配，请先在 App 内打开平台。',
                               'adapter_mismatch')

SESSION_PREPARE_TIMEOUT = 120
SESSION_CLEANUP_TIMEOUT = 15
_SESSION_PHASES = frozenset(('preflight', 'media_probe', 'browser_connect', 'creator_open',
    'account_check', 'capability_check', 'evidence_init', 'checkpoint_restore', 'checkpoint_capture',
    'checkpoint_final_capture', 'evidence_end', 'guard_release', 'lease_release', 'context_close', 'playwright_stop', 'observer_remove'))
_CHECKPOINT_RESTORE_REASONS = frozenset(('checkpoint_seal_key_invalid', 'checkpoint_session_unverified',
    'checkpoint_save_already_started', 'checkpoint_restore_session_not_fresh', 'checkpoint_time_invalid',
    'checkpoint_expired', 'checkpoint_task_mismatch', 'checkpoint_asset_unreadable', 'checkpoint_asset_mismatch',
    'checkpoint_account_mismatch', 'checkpoint_baseline_invalid', 'checkpoint_links_invalid',
    'checkpoint_video_upload_unverified', 'checkpoint_composer_unverified', 'checkpoint_platform_mismatch',
    'checkpoint_identity_invalid', 'checkpoint_content_mismatch', 'checkpoint_operation_mismatch',
    'checkpoint_schema_invalid', 'checkpoint_seal_invalid', 'checkpoint_resource_invalid'))


def _session_diagnostic(phase, state):
    if phase not in _SESSION_PHASES or state not in ('running', 'completed', 'failed', 'timeout'):
        return
    if phase in OPERATIONS['session']:
        progress_operation('session', phase, 'failed' if state == 'timeout' else state)
    try:
        print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'draft_session_prepare',
            'phase': phase, 'state': state}), flush=True)
    except Exception:
        pass


async def _bounded_cleanup(awaitable, phase):
    """Detach/release observations without replacing the task's result/error."""
    task = asyncio.ensure_future(awaitable)
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        # Shield lets the timeout return promptly even if a driver's own
        # cancellation cleanup stalls. The timed-out operation is still cancelled.
        await asyncio.wait_for(asyncio.shield(task), SESSION_CLEANUP_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        task.cancel()
        _session_diagnostic(phase, 'timeout')
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception:
        _session_diagnostic(phase, 'failed')
    return False


def _checkpoint_private_bytes(path, *, exact_size=None, max_size=CHECKPOINT_MAX_BYTES):
    """Read a private regular file without following a key/checkpoint symlink."""
    try:
        descriptor = open_private_read(path)
        try:
            info = os.fstat(descriptor)
            if (not private_stat_ok(info, exact_mode=True) or info.st_size > max_size or
                    (exact_size is not None and info.st_size != exact_size)):
                raise CheckpointError('checkpoint_private_file_invalid')
            chunks, size = [], 0
            while True:
                chunk = os.read(descriptor, min(65536, max_size + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_size:
                    raise CheckpointError('checkpoint_private_file_invalid')
            after = os.fstat(descriptor)
            if ((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                    (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
                    (exact_size is not None and size != exact_size)):
                raise CheckpointError('checkpoint_private_file_invalid')
            return b''.join(chunks)
        finally:
            os.close(descriptor)
    except OSError:
        raise CheckpointError('checkpoint_private_file_unreadable') from None


def _checkpoint_read(path):
    try:
        return json.loads(_checkpoint_private_bytes(path))
    except (UnicodeError, ValueError):
        raise CheckpointError('checkpoint_private_json_invalid') from None


def _checkpoint_atomic_create(path, data):
    """An exclusive atomic link never overwrites a prior proof/save marker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    temporary = path.parent / ('.checkpoint-' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        if os.name != 'nt':
            os.fchmod(descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class ExecutionCheckpoint:
    """Runner-only private persistence; the evidence class owns proof gates."""
    def __init__(self, key_file, output, resume=None):
        self.key = _checkpoint_private_bytes(key_file, exact_size=32, max_size=32)
        self.output = Path(output)
        self.resume = Path(resume) if resume is not None else None
        self.bound = None
        self.save_claimed = False
        self.proof = None
        self.require_no_save()

    def markers(self):
        return sorted({path.with_suffix('.save-started.json') for path in (self.output, self.resume) if path is not None})

    def require_no_save(self):
        if self.save_claimed or any(path.exists() or path.is_symlink() for path in self.markers()):
            raise CheckpointError('checkpoint_save_already_started')

    def bind(self, page, session):
        self.bound = {key: session.expected.get(key) for key in IDENTITY_KEYS}
        self.bound['account_identity'] = session.expected.get('account', {}).get('identity')
        page._sau_checkpoint_runtime = self

    def _same_session(self, session):
        if (session is None or session.closed or self.bound is None or
                any(session.expected.get(key) != self.bound[key] for key in IDENTITY_KEYS) or
                session.expected.get('account', {}).get('identity') != self.bound['account_identity']):
            raise CheckpointError('checkpoint_task_mismatch')

    async def capture(self, session):
        # Observability/persistence failures cannot masquerade as an upload
        # success. Preserve an existing proof; no automatic retry of video.
        try:
            self._same_session(session)
            self.require_no_save()
            if self.output.exists() or self.output.is_symlink():
                validate_checkpoint(_checkpoint_read(self.output), seal_key=self.key, expected=session.expected,
                                    platform=session.platform)
                return True
            checkpoint = await session.export_checkpoint(seal_key=self.key)
            self.require_no_save()
            _checkpoint_atomic_create(self.output, checkpoint)
            return True
        except Exception as error:
            reason = str(error) if isinstance(error, CheckpointError) else type(error).__name__
            if not re.fullmatch(r'[A-Za-z0-9_]{1,96}', reason):
                reason = 'checkpoint_unavailable'
            print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'upload_checkpoint_unavailable', 'reason': reason,
                  'selected_files': len(getattr(session, 'files', [])),
                  'upload_exchanges': len(getattr(session, 'exchanges', [])),
                  'tc_allocations': sum('plan' in x for x in getattr(session, 'exchanges', [])),
                  'tc_parts': sum('part_number' in x for x in getattr(session, 'exchanges', [])),
                  'tc_finishes': sum(x.get('tc_protocol') == 'dfs' and x.get('stage') == 'complete' for x in getattr(session, 'exchanges', [])),
                  'tc_binding_checks': [{
                    'selection_name_matches': sum(x.get('name') == item['metadata'][0] for x in session.files),
                    'selection_size_matches': sum(x.get('size') == item['metadata'][1] for x in session.files),
                    'selection_both_matches': sum((x.get('name'), x.get('size')) == item['metadata'] for x in session.files),
                    'selected_asset_bound': session._tc_selected_asset(item) is not None,
                    'has_video_size': any(a['kind'] == 'video' and a['size'] == item['metadata'][1] for a in session.assets.values())
                  } for item in getattr(session, 'exchanges', []) if 'plan' in item],
                  'tc_chain_checks': [{
                    'part_count_matches_plan': len(item['parts']) == len(allocation['plan']),
                    'part_etags_match': {part['part_number']: {part['etag']} for part in session.exchanges if 'part_number' in part and part['task_id'] == item['task_id'] and part['upload_id'] == item['upload_id']} == {number: {etag} for number, etag in item['parts']},
                    'part_flag_matches': any(part.get('trans_flag') == item.get('trans_flag') for part in session.exchanges if 'part_number' in part and part['task_id'] == item['task_id'] and part['upload_id'] == item['upload_id']),
                    'part_flags_present': sum(part.get('trans_flag') is not None for part in session.exchanges if 'part_number' in part and part['task_id'] == item['task_id'] and part['upload_id'] == item['upload_id'])
                  } for item in getattr(session, 'exchanges', []) if item.get('tc_protocol') == 'dfs' and item.get('stage') == 'complete' for allocation in session.exchanges if 'plan' in allocation and allocation['task_id'] == item['task_id'] and allocation['upload_id'] == item['upload_id']],
                  'xhs_sdk_records': len(getattr(getattr(session, 'xhs_observer', None), 'records', {}))}), flush=True)
            return False

    async def restore(self, session):
        self._same_session(session)
        self.require_no_save()
        if self.resume is None:
            raise CheckpointError('checkpoint_resume_missing')
        checkpoint = _checkpoint_read(self.resume)
        self.proof = validate_checkpoint(checkpoint, seal_key=self.key, expected=session.expected, platform=session.platform)
        async def media_reader():
            return await read_checkpoint_composer_media(session.page, self.proof)
        try:
            result = await session.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=media_reader)
            self.require_no_save()
        except CheckpointError as exc:
            reason = str(exc)
            if reason not in _CHECKPOINT_RESTORE_REASONS:
                reason = 'checkpoint_restore_unverified'
            try:
                print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'checkpoint_restore_rejected',
                    'reason': reason}), flush=True)
            except Exception:
                pass
            raise DraftInterrupted('已上传素材的任务、账号或当前编辑器无法核验，已保留现场；不会重传视频。', 'adapter_mismatch') from None
        emit_progress('video', 'completed')
        emit_progress('upload', 'completed')
        return result

    async def before_save(self, session):
        self._same_session(session)
        self.require_no_save()
        marker = {key: session.expected.get(key) for key in ('job_id', 'platform_id', 'receipt_nonce', 'operation_key')}
        marker.update(v=1, phase='save_started', started_at=time.time())
        try:
            for path in self.markers():
                _checkpoint_atomic_create(path, marker)
        except (OSError, ValueError):
            raise DraftInterrupted('无法原子记录本次保存动作，已停止且未点击保存。', 'adapter_mismatch') from None
        self.save_claimed = True


def account_file(platform):
    if platform not in PLATFORMS:
        raise ValueError('未知平台。')
    directory = PRIVATE / 'cookies'
    directory.mkdir(exist_ok=True, mode=0o700)
    return directory / f'{platform}_draft-workbench.json'


def read_marker(platform, embedded):
    file = embedded_account_file(platform) if embedded else account_file(platform)
    try:
        return json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def write_marker(platform, account, handle=None, status='login_verified'):
    if handle:
        handle.mark_login(account, status)
        return
    file = account_file(platform)
    data = {**read_marker(platform, False), 'status': status, 'platform': platform,
            'browser': 'persistent', 'checked_at': time.time()}
    if status == 'login_verified':
        data.update(verified_at=time.time(), account=account)
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    os.chmod(temporary, 0o600)
    temporary.replace(file)


@asynccontextmanager
async def browser_session(platform, embedded=False, action='login'):
    if platform not in PLATFORMS:
        raise ValueError('未知平台。')
    playwright = None
    handle = context = page = remove_observer = None
    try:
        playwright = await async_playwright().start()
        if embedded:
            # Keep the lease owner available even if connect fails halfway.
            handle = EmbeddedBrowser(platform, action)
            original_release = handle.release
            async def release():
                await _bounded_cleanup(original_release(), 'lease_release')
            handle.release = release
            await handle.connect(playwright)
            browser, context, page = handle.browser, handle.context, handle.page
        else:
            profile = PRIVATE / 'profiles' / platform
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            context = await playwright.chromium.launch_persistent_context(str(profile), headless=False, viewport=None)
            browser = context.browser
            page = context.pages[0] if context.pages else await context.new_page()
        remove_observer = observe_session(platform, page)
        yield playwright, browser, context, page, handle
    finally:
        if remove_observer:
            try:
                remove_observer()
            except Exception:
                _session_diagnostic('observer_remove', 'failed')
        if context:
            await _bounded_cleanup(uninstall_draft_guard(context, page if handle else None), 'guard_release')
        if handle:
            await handle.release()
        elif context:
            await _bounded_cleanup(context.close(), 'context_close')
        if playwright:
            # The app owns embedded pages. Stop disconnects this client only.
            await _bounded_cleanup(playwright.stop(), 'playwright_stop')


async def open_creator(platform, page):
    if os.environ.get('SAU_DESKTOP_TEST') == '1' and os.environ.get('SAU_DESKTOP_TEST_URL'):
        url = os.environ['SAU_DESKTOP_TEST_URL'].rstrip('/') + '/' + platform
    else:
        url = URLS[platform]
    # An already open creator document is reused even when owner observation
    # has not settled yet. Reloading it discards editors and races SPA redirects.
    if urlparse(page.url).netloc == urlparse(url).netloc:
        return
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=90000)
    except Exception as exc:
        # Chromium can abort a navigation when the website redirects itself.
        # Accept only a landed creator document; all authentication checks follow.
        if 'ERR_ABORTED' not in str(exc) or urlparse(page.url).netloc != urlparse(url).netloc:
            raise


async def logged_in(platform, page):
    return (await inspect_session(platform, page))['verified']


async def login(platform, embedded=False):
    require_automated_draft(platform)
    async with browser_session(platform, embedded) as (_, _, _, page, handle):
        await open_creator(platform, page)
        print('请在 App 内的平台页完成必要认证；当前登录通过验证即可复用。', flush=True)
        until = time.monotonic() + 600
        while time.monotonic() < until:
            try:
                account = await verify_session(platform, page, timeout=30, settle=6)
            except DraftInterrupted as exc:
                if exc.status != 'needs_reauthentication':
                    raise
                continue
            write_marker(platform, account, handle)
            result = {'status': 'login_verified', 'platform': PLATFORMS[platform], 'platform_id': platform,
                      'verified_at': time.time(), 'account': account}
            print('LOGIN_RESULT=' + json.dumps(result, ensure_ascii=False), flush=True)
            return result
        raise DraftInterrupted('认证等待超过10分钟，页面和素材已保留。', 'needs_reauthentication')


async def find_control(page, selector, timeout=30, attached=False):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        eligible = []
        for frame in page.frames:
            matches = frame.locator(selector)
            for i in range(await matches.count()):
                item = matches.nth(i)
                if attached or await item.is_visible():
                    eligible.append(item)
        if len(eligible) == 1:
            return eligible[0]
        if len(eligible) > 1:
            raise DraftInterrupted('当前页面有多个可能的素材或内容控件，需要更新控件适配；已保留现场。', 'adapter_mismatch')
        await asyncio.sleep(.2)
    raise DraftInterrupted('当前页面缺少所需上传或内容控件，需要检查页面适配；已保留现场。', 'adapter_mismatch')


async def find_control_priority(page, selectors, timeout=30, attached=False):
    """Prefer an identified component before a broad legacy fallback."""
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        for selector in selectors:
            eligible=[]
            for frame in page.frames:
                for item in await frame.locator(selector).all():
                    if attached or await item.is_visible():
                        eligible.append(item)
            if len(eligible)==1:
                return eligible[0]
            if len(eligible)>1:
                raise DraftInterrupted('同一用途的控件仍不唯一，需要检查页面适配；已保留现场。', 'adapter_mismatch')
        await asyncio.sleep(.2)
    raise DraftInterrupted('当前页面尚未显示所需控件，已保留现场。', 'adapter_mismatch')


async def click_draft_control(page, names):
    eligible = []
    for frame in page.frames:
        for name in names:
            matches = frame.get_by_role('button', name=name, exact=True)
            for i in range(await matches.count()):
                button = matches.nth(i)
                if await button.is_visible() and await button.is_enabled():
                    eligible.append(button)
    if len(eligible) == 1:
        await eligible[0].click()
        return
    raise DraftInterrupted('当前页面没有所需的明确控件，已停止。', 'platform_restricted')


async def bilibili_draft(payload, receipt, embedded=False, page=None):
    if page is None:
        raise ValueError('B站草稿需要借用已验证账号的页面。')
    from platform_limits import assert_draft_fields
    caps = payload.get('capabilities') if isinstance(payload, dict) else None
    assert_draft_fields('bilibili', payload if isinstance(payload, dict) else {}, caps)
    from uploader.draft_only import assert_draft_safe
    from uploader.bili_controls import (bili_empty_video_control, bili_wait_uploaded,
                                        bili_fill_content, bili_set_cover)
    session = getattr(page, '_sau_draft_evidence', None)
    if session is not None:
        observer = getattr(session, 'bili_observer', None)
        if observer is None or not await observer.ensure_ready():
            raise DraftInterrupted('B站原生素材观察器尚未就绪，未开始本次上传。', 'adapter_mismatch')
    if not draft_resume_active(page):
        with progress_step("video", operations=["locate", "select"]):
            progress_operation("video", "locate")
            video = await bili_empty_video_control(page)
            progress_operation("video", "locate", "completed")
            await assert_draft_safe(page, 'B站')
            progress_operation('video', 'select')
            await set_local_files(page, video, payload['video'])
            progress_operation('video', 'select', 'completed')
        with progress_step("upload"):
            await bili_wait_uploaded(page, timeout=600)
    await checkpoint_after_upload(page)
    with progress_step("content"):
        await bili_fill_content(page, payload['title'], payload.get('description', ''), payload.get('tags', []))
    # Bili normalizes a single selected source into its official 4:3 home and
    # 16:9 space covers; it does not have a portrait cover slot.
    cover = payload.get('cover') or payload.get('cover_landscape')
    if cover:
        with progress_step("cover"):
            await bili_set_cover(page, cover)
    else:
        emit_progress('cover', 'skipped')
    await assert_draft_safe(page, 'B站')
    return await save_draft(page, 'B站', payload['title'], receipt, expected=payload)


def prepare_expected(platform, payload):
    from workbench_model import preflight_payload, build_platform_payload, IDENTITY_FIELDS
    check = preflight_payload(payload, [platform], {platform: payload.get('account_key', 'default:' + platform)})
    if check['errors']:
        raise ValueError('；'.join(e['message'] for e in check['errors']))
    expected = build_platform_payload(check, platform, payload.get('job_id') or 'cli-' + uuid.uuid4().hex,
                                      payload.get('receipt_nonce') or uuid.uuid4().hex)
    for key in IDENTITY_FIELDS:
        if key in payload and payload[key] != expected[key]:
            raise ValueError('素材或映射与任务预检不一致：' + key)
    if payload.get('expected_account_identity'):
        expected['expected_account_identity'] = payload['expected_account_identity']
    return expected


@asynccontextmanager
async def prepared_draft_session(platform, payload, embedded, *, checkpoint_key_file=None,
                                 checkpoint_output=None, resume_checkpoint=None):
    """Bound only preflight, connection and verification before uploader work."""
    connection = checkpoint = None
    phase = 'preflight'
    async with AsyncExitStack() as resources:
        try:
            try:
                async with asyncio.timeout(SESSION_PREPARE_TIMEOUT):
                    _session_diagnostic(phase, 'running')
                    expected = await asyncio.to_thread(prepare_expected, platform, payload)
                    if bool(checkpoint_key_file) != bool(checkpoint_output) or (resume_checkpoint and not checkpoint_output):
                        raise ValueError('检查点参数必须包含私有密钥和输出路径。')
                    checkpoint = ExecutionCheckpoint(checkpoint_key_file, checkpoint_output, resume_checkpoint) if checkpoint_output else None
                    _session_diagnostic(phase, 'completed')
                    phase = 'media_probe'
                    _session_diagnostic(phase, 'running')
                    from material_probe import media_info, validate_image
                    expected['media_info'] = await asyncio.to_thread(media_info, expected['video'])
                    for key in ('cover', 'cover_landscape', 'cover_portrait'):
                        if expected.get(key):
                            await asyncio.to_thread(validate_image, expected[key])
                    _session_diagnostic(phase, 'completed')
                    phase = 'browser_connect'
                    _session_diagnostic(phase, 'running')
                    connection = await resources.enter_async_context(browser_session(platform, embedded, action='draft'))
                    p, _, context, page, handle = connection
                    _session_diagnostic(phase, 'completed')
                    if resume_checkpoint is None:
                        phase = 'creator_open'
                        _session_diagnostic(phase, 'running')
                        await open_creator(platform, page)
                        _session_diagnostic(phase, 'completed')
                    else:
                        progress_operation('session', 'creator_open', 'skipped')
                    phase = 'account_check'
                    _session_diagnostic(phase, 'running')
                    account = await verify_session(platform, page)
                    _session_diagnostic(phase, 'completed')
                    phase = 'capability_check'
                    _session_diagnostic(phase, 'running')
                    from platform_limits import check_fields
                    live = await current_owner_state(platform, page)
                    fields = {**expected, **expected.get('platform_fields', {})}
                    text_check = check_fields(platform, fields, live.get('capabilities'))
                    if text_check['errors']:
                        raise ValueError('当前平台配置预检未通过；尚未上传：' + '；'.join(e['message'] for e in text_check['errors']))
                    old_owner = read_marker(platform, embedded).get('account', {}).get('identity')
                    if old_owner and old_owner != account['identity']:
                        raise DraftInterrupted('当前账号与任务使用的登录记录不同，素材已保留。', 'needs_reauthentication')
                    if expected.get('expected_account_identity') and expected['expected_account_identity'] != account['identity']:
                        raise DraftInterrupted('当前账号与任务预检的账号不同，已停止且不上传。', 'needs_reauthentication')
                    expected['account'] = account
                    write_marker(platform, account, handle)
                    _session_diagnostic(phase, 'completed')
                    phase = 'evidence_init'
                    _session_diagnostic(phase, 'running')
                    await install_draft_guard(context, page if handle else None)
                    from uploader.draft_only import begin_draft_verification
                    evidence = await begin_draft_verification(page, platform, expected,
                        account_reader=lambda: inspect_session(platform, page))
                    _session_diagnostic(phase, 'completed')
                    if checkpoint is not None:
                        checkpoint.bind(page, evidence)
                        if resume_checkpoint is not None:
                            phase = 'checkpoint_restore'
                            _session_diagnostic(phase, 'running')
                            await checkpoint.restore(evidence)
                            _session_diagnostic(phase, 'completed')
                            phase = 'checkpoint_capture'
                            _session_diagnostic(phase, 'running')
                            await checkpoint.capture(evidence)
                            _session_diagnostic(phase, 'completed')
            except asyncio.TimeoutError:
                _session_diagnostic(phase, 'timeout')
                raise DraftInterrupted('会话与续传初始化等待超时，已保留现场；不会重新上传。', 'adapter_mismatch') from None
            # The preparation deadline has exited. Normal upload/save/verify
            # retains its own existing limits and never inherits this 120 s budget.
            yield p, context, page, handle, expected, account, live
        except DraftInterrupted as exc:
            if connection is not None and exc.status == 'needs_reauthentication':
                write_marker(platform, account=None, handle=connection[4], status='needs_reauthentication')
            raise
        finally:
            if connection is not None:
                page = connection[3]
                if checkpoint is not None:
                    evidence = getattr(page, '_sau_draft_evidence', None)
                    if evidence is not None:
                        await _bounded_cleanup(checkpoint.capture(evidence), 'checkpoint_final_capture')
                    if getattr(page, '_sau_checkpoint_runtime', None) is checkpoint:
                        page._sau_checkpoint_runtime = None
                from uploader.draft_only import end_draft_verification
                await _bounded_cleanup(end_draft_verification(page), 'evidence_end')


async def stage(platform, payload, receipt, embedded=False, *, checkpoint_key_file=None,
                checkpoint_output=None, resume_checkpoint=None):
    if platform not in PLATFORMS:
        raise ValueError('未知平台。')
    require_automated_draft(platform)
    configure_progress(platform)
    with progress_step("session", operations=list(OPERATIONS["session"])) as session_progress:
        async with prepared_draft_session(platform, payload, embedded,
                checkpoint_key_file=checkpoint_key_file, checkpoint_output=checkpoint_output,
                resume_checkpoint=resume_checkpoint) as prepared:
            p, context, page, handle, expected, account, live = prepared
            session_progress.complete()
            if platform == 'bilibili':
                result = await run_with_session_watch(platform, page, account,
                    bilibili_draft(expected, receipt, embedded, page), timeout=1800)
            else:
                common = dict(title=expected['title'], file_path=expected['video'], tags=expected.get('tags', []),
                              publish_date=0, account_file=str(embedded_account_file(platform) if embedded else account_file(platform)),
                              desc=expected.get('description', ''), debug=False, headless=False)
                portrait = expected.get('cover_portrait') or expected.get('cover')
                landscape = expected.get('cover_landscape') or expected.get('cover')
                if platform == 'douyin':
                    from uploader.douyin_uploader.main import DouYinVideo
                    app = DouYinVideo(**common, thumbnail_landscape_path=landscape, thumbnail_portrait_path=portrait)
                elif platform == 'xiaohongshu':
                    from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
                    app = XiaoHongShuVideo(**common, thumbnail_path=portrait or landscape)
                elif platform == 'kuaishou':
                    from uploader.ks_uploader.main import KSVideo
                    app = KSVideo(**common, thumbnail_path=portrait or landscape)
                else:
                    from uploader.tencent_uploader.main import TencentVideo
                    class DraftTencentVideo(TencentVideo):
                        async def apply_original_statement(self, page):
                            return
                        async def submit_publish(self, page):
                            return await save_draft(page, '视频号', self.title, self.draft_receipt,
                                                    expected=self.draft_expected)
                    app = DraftTencentVideo(**common, is_draft=True, thumbnail_landscape_path=landscape,
                                            thumbnail_portrait_path=portrait, short_title=expected.get('short_title'))
                app.draft_only, app.draft_receipt, app.draft_expected = True, receipt, expected
                app.draft_capabilities = live.get('capabilities') if isinstance(live, dict) else None
                if handle:
                    app.embedded = handle
                else:
                    class Borrowed:
                        async def release(self):
                            return
                    borrowed = Borrowed()
                    borrowed.browser, borrowed.context, borrowed.page = context.browser, context, page
                    app.embedded = borrowed
                await run_with_session_watch(platform, page, account, app.upload(p))
                result = json.loads(Path(receipt).read_text(encoding='utf-8')) if Path(receipt).is_file() else None
            if not result:
                raise DraftInterrupted('任务结束但没有本次草稿证据，保留现场且不重传。')
            from workbench_model import validate_receipt
            try:
                validate_receipt(result, expected, platform)
            except Exception:
                emit_progress("verify", "failed")
                raise
            return result


def failure_receipt(payload, platform, path, error):
    from workbench_model import IDENTITY_FIELDS, RESULT_STATUSES
    status = getattr(error, 'status', 'failed' if isinstance(error, ValueError) else 'result_unknown')
    if status not in RESULT_STATUSES:
        status = 'result_unknown'
    data = {k: payload.get(k) for k in IDENTITY_FIELDS}
    data.update(status=status, platform_id=platform, message=str(error), verified_at=time.time(),
                verification={k: False for k in ('upload_complete', 'fields', 'cover', 'media', 'reopened', 'account')})
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(temporary, 0o600)
    temporary.replace(file)
    print('DRAFT_RESULT=' + json.dumps(data, ensure_ascii=False), flush=True)


async def main():
    parser = argparse.ArgumentParser(description='仅登录或保存视频草稿')
    parser.add_argument('action', choices=['login', 'draft'])
    parser.add_argument('--platform', required=True, choices=list(PLATFORMS))
    parser.add_argument('--payload')
    parser.add_argument('--receipt')
    parser.add_argument('--embedded', action='store_true')
    parser.add_argument('--checkpoint-key-file')
    parser.add_argument('--checkpoint-output')
    parser.add_argument('--resume-checkpoint')
    args = parser.parse_args()
    if args.action == 'login':
        await login(args.platform, args.embedded)
    else:
        if not args.payload or not args.receipt:
            parser.error('draft requires --payload and --receipt')
        payload = json.loads(Path(args.payload).read_text(encoding='utf-8'))
        try:
            result = await stage(args.platform, payload, args.receipt, args.embedded,
                                 checkpoint_key_file=args.checkpoint_key_file, checkpoint_output=args.checkpoint_output,
                                 resume_checkpoint=args.resume_checkpoint)
            if result['status'] not in ('local_draft_verified', 'platform_draft_verified', 'platform_snapshot_verified'):
                raise SystemExit(2)
        except Exception as exc:
            failure_receipt(payload, args.platform, args.receipt, exc)
            raise SystemExit(2)
