"""Draft-only actions with operation-bound, read-back evidence."""
import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from uploader.draft_evidence import (CHECKS, IDENTITY_KEYS, POLICIES, begin_draft_verification,
                                    description_values, end_draft_verification, field_match, platform_id, verify_evidence)
from uploader.draft_evidence import evaluate_main
from uploader.draft_session import DraftInterrupted
from uploader.draft_progress import (emit as emit_progress, operation as progress_operation,
                                     part as progress_part, set_plan as set_progress_plan)

DRAFT_BUTTON_NAMES = ('保存草稿', '存草稿', '暂存草稿', '保存为草稿', '存为草稿',
                      '保存到草稿箱', '暂存离开', '保存并离开')
GUARD_SCRIPT = r"""(() => {
 if(window.__sauDraftGuardControl){window.__sauDraftGuardControl.setEnabled(true);return;}
 let enabled=true;window.__sauDraftGuard=true;
 window.__sauDraftGuardControl={setEnabled(value){
   if(value===true&&!enabled)window.__sauBlockedPublishClicks=0;
   enabled=value!==false;window.__sauDraftGuard=enabled;
 }};
 const forbidden=/^(发布|发表|投稿|提交投稿|立即投稿|立即发布|定时发布|发布视频|发布笔记|确认发布|确定发布|继续发布|继续发表|发布作品|提交审核|保存并发布|发布并保存)$/;
 function isForbidden(node) {
   return node instanceof Element && !node.closest('input:not([type="button"]):not([type="submit"]),textarea,[contenteditable="true"]') &&
      node.matches('button,a,input[type="submit"],input[type="button"],[role="button"],[class*="btn"],[class*="button"],[class*="submit"]') &&
      forbidden.test((node.innerText||node.value||node.getAttribute('aria-label')||'').trim());
 }
 function stop(e){e.preventDefault();e.stopImmediatePropagation();window.__sauBlockedPublishClicks=(window.__sauBlockedPublishClicks||0)+1;}
 document.addEventListener('click',e=>{if(enabled&&e.composedPath().some(isForbidden))stop(e);},true);
 document.addEventListener('submit',e=>{if(enabled&&isForbidden(e.submitter))stop(e);},true);
})()"""

# Explicit final submission paths only. In particular publishInfo/snapshot/save,
# upload allocation, transcode, draft/add and draft/update remain allowed.
PUBLISH_PATHS = tuple(re.compile(pattern) for pattern in (
    r'^/web/api/media/aweme/create(?:_v2)?/?$',
    r'^/aweme/v1/co/item/publish/?$',
    r'^/rest/cp/works/v[23]/video/pc/(?:publish|(?:edit/|isolation/)?submit)/?$',
    r'^/openapi/photo/publish/?$',
    r'^/x/vu/web/(?:add|edit)/?$',
    r'^/arcopen/fn/archive/add-by-utoken/?$',
    r'^/(?:cgi-bin/mmfinderassistant-bin/)?post/post_(?:create|update|publish)/?$',
    r'^/api/media/v1/note/(?:create|publish)/?$',
    r'^/web_api/sns/v[12]/note(?:/publish)?/?$',
))


def is_publish_request(url, method=None):
    if method is not None and method.upper() not in ('POST', 'PUT', 'PATCH'):
        return False
    path = urlparse(url).path
    # The currently installed TC creator app mounts its official API under
    # /micro/content. Keep the same final-submit denylist for both mounts.
    if path.startswith('/micro/content/cgi-bin/mmfinderassistant-bin/'):
        path = path[len('/micro/content'):]
    return any(pattern.fullmatch(path) for pattern in PUBLISH_PATHS)


async def install_draft_guard(context, page=None):
    target = page or context
    if not getattr(target, '_sau_guard_route', False):
        async def guard(route):
            if is_publish_request(route.request.url, route.request.method):
                owner = page
                if owner is None:
                    try:
                        owner = route.request.frame.page
                    except Exception:
                        owner = None
                if owner is not None:
                    owner._sau_blocked_publish_requests = getattr(owner, '_sau_blocked_publish_requests', 0) + 1
                await route.abort('blockedbyclient')
            else:
                # Preserve other routes (including the runner's local fixtures).
                await route.fallback()
        await target.route('**/*', guard)
        target._sau_guard_route = True
        target._sau_guard_route_handler = guard
    if not getattr(target, '_sau_guard_script', False):
        await target.add_init_script(GUARD_SCRIPT)
        target._sau_guard_script = True
    pages = [page] if page else list(context.pages)
    for current in pages:
        for frame in current.frames:
            try:
                await evaluate_main(frame, GUARD_SCRIPT)
            except Exception:
                pass


async def uninstall_draft_guard(context, page=None):
    """Remove only this client's request route before releasing the app page.

    Click protection stays enabled until the trusted desktop shell explicitly
    enables manual publication; other fixtures/routes are never removed.
    """
    target = page or context
    handler = getattr(target, '_sau_guard_route_handler', None)
    if handler is not None:
        await target.unroute('**/*', handler)
        target._sau_guard_route_handler = None
        target._sau_guard_route = False


RISK = re.compile(r'操作(?:过于)?频繁|访问(?:过于)?频繁|账号.{0,12}(?:受限|封禁|异常)|'
                  r'安全(?:验证|校验)|实名验证|身份验证|验证码|请完成.{0,8}(?:验证|拼图)|拖动.{0,8}滑块|疑似.{0,6}(?:AI|自动化)')
UPLOAD_FAILURE = re.compile(r'上传失败|上传出错|转码失败|视频处理失败|草稿保存失败|保存草稿失败')
LOGIN_FAILURE = re.compile(r'登录(?:状态)?已(?:过期|失效)|请(?:重新)?登录|扫码登录|账号已退出')


async def assert_draft_safe(page, platform=None):
    """Stop on an active challenge/failure; never refresh or retry an upload."""
    if page.is_closed():
        raise DraftInterrupted('平台页面已关闭，草稿结果未知。')
    if getattr(page, '_sau_blocked_publish_requests', 0):
        raise DraftInterrupted('检测到最终发布请求，草稿保护已阻止请求并停止任务。', 'risk_paused')
    for frame in page.frames:
        path = urlparse(frame.url).path.lower()
        login_url = bool(re.search(r'/login(?:\.html)?(?:/|$)', path) or
                         (urlparse(frame.url).hostname == 'open.weixin.qq.com' and path == '/connect/qrconnect'))
        challenge_url = bool(re.search(r'/(?:captcha|security-check)(?:\.html)?(?:/|$)', path))
        if login_url or challenge_url:
            visible = frame is page.main_frame
            if not visible:
                try:
                    visible = await (await frame.frame_element()).is_visible()
                except Exception:
                    visible = False
            if visible:
                status = 'needs_reauthentication' if login_url else 'risk_paused'
                raise DraftInterrupted('平台要求登录或安全验证，任务已暂停且不自动重传。', status)
        for pattern, status, message in (
            (RISK, 'risk_paused', '平台出现验证码或风险提示，任务已停止且不自动重传。'),
            (UPLOAD_FAILURE, 'result_unknown', '平台显示上传或草稿保存失败，已停止且不自动重传。'),
            (LOGIN_FAILURE, 'needs_reauthentication', '登录状态失效，已停止且保留当前页面。'),
        ):
            matches = frame.get_by_text(pattern)
            for index in range(await matches.count()):
                item = matches.nth(index)
                # A creator can legitimately describe a captcha/upload failure.
                # Text being edited is not a platform security alert.
                if await item.is_visible() and not await item.evaluate("e => !!e.closest('input,textarea,[contenteditable=\"true\"]')"):
                    raise DraftInterrupted(message, status)
        try:
            if await evaluate_main(frame, 'window.__sauBlockedPublishClicks || 0'):
                raise DraftInterrupted('检测到发布控件动作，草稿保护已阻止并停止任务。', 'risk_paused')
        except DraftInterrupted:
            raise
        except Exception:
            pass


async def _visible(page, selectors):
    for frame in page.frames:
        for selector in selectors:
            matches = frame.locator(selector)
            for index in range(await matches.count()):
                if await matches.nth(index).is_visible():
                    return True
    return False


async def wait_draft_upload(page, platform, ready_selectors, busy_selectors=(), timeout=900, poll_interval=.25):
    """UI readiness only; this is insufficient for a verified draft receipt."""
    if not ready_selectors:
        raise ValueError('必须提供明确的上传完成控件。')
    deadline = time.monotonic() + timeout
    try:
        async with asyncio.timeout(timeout):
            while time.monotonic() < deadline:
                await assert_draft_safe(page, platform)
                if await _visible(page, ready_selectors) and not await _visible(page, busy_selectors):
                    return True
                await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    except TimeoutError:
        pass
    raise DraftInterrupted(f'{platform}上传完成等待超时，已停止且不自动重新上传。')


def draft_resume_active(page):
    """Only the fresh evidence session can authorize skipping video input."""
    session = getattr(page, '_sau_draft_evidence', None)
    return bool(session is not None and not session.closed and
                getattr(session, '_checkpoint_restored_at', None) is not None)


async def checkpoint_after_upload(page):
    """Capture verified upload observations before content/cover can fail."""
    runtime = getattr(page, '_sau_checkpoint_runtime', None)
    session = getattr(page, '_sau_draft_evidence', None)
    return await runtime.capture(session) if runtime is not None and session is not None else False


async def _checkpoint_before_save(page, session):
    runtime = getattr(page, '_sau_checkpoint_runtime', None)
    if runtime is not None:
        await runtime.before_save(session)


async def _click_exact(page, names):
    for frame in page.frames:
        for name in names:
            buttons = frame.get_by_role('button', name=name, exact=True)
            for index in range(await buttons.count()):
                button = buttons.nth(index)
                if await button.is_visible() and await button.is_enabled():
                    await button.click(timeout=30000)
                    return name
    return None


async def _draft_action(page, platform, timeout):
    if platform == 'kuaishou':
        return 'automatic_snapshot'
    if platform == 'bilibili':
        from uploader.bili_controls import bili_draft_action
        return await bili_draft_action(page)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await assert_draft_safe(page, platform)
        action = await _click_exact(page, DRAFT_BUTTON_NAMES)
        if action:
            return action
        # XHS saves the normal editor to IndexedDB on its own timer/unload.
        if platform == 'xiaohongshu':
            return 'automatic_local_draft'
        await asyncio.sleep(.2)
    return None


def _id_in_url(url, draft_id):
    return any(str(draft_id) in values for key, values in parse_qs(urlparse(url).query).items()
               if key in ('draft_id', 'draftId', 'objectId', 'draftid'))


async def _reopen(page, platform, candidate):
    """Use observed UI controls/links, never construct or replay a draft API."""
    if platform == 'bilibili':
        from uploader.bili_reopen import bili_reopen_normal
        return await bili_reopen_normal(page, candidate)
    if platform == 'tencent':
        from uploader.tc_reopen import reopen_tc_draft
        return await reopen_tc_draft(page, candidate)
    if platform == 'kuaishou':
        await page.reload(wait_until='domcontentloaded', timeout=60000)
        await _click_exact(page, ('继续编辑',))
        return True
    if platform == 'douyin':
        # The normal upload entry restores publish_form_cache:<uid>.
        # Saving normally navigates to /content/upload before its recovery
        # banner mounts. Wait for that control instead of permanently failing
        # the reopen check during this short loading interval.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            await assert_draft_safe(page, platform)
            controls = []
            for frame in page.frames:
                for name in ('继续编辑', '继续创作', '恢复草稿'):
                    # This official styled div/span has no button role.
                    matches = frame.get_by_text(name, exact=True)
                    for index in range(await matches.count()):
                        item = matches.nth(index)
                        if await item.is_visible() and await item.is_enabled():
                            controls.append((item, name))
            if len(controls) > 1:
                return False
            if len(controls) == 1:
                session = getattr(page, '_sau_draft_evidence', None)
                if session:
                    session.normal_ui_binding = {'id': candidate['id'], 'key': candidate.get('storage_key'),
                                                 'owner': candidate.get('owner'), 'control': controls[0][1]}
                await controls[0][0].click(timeout=30000)
                return True
            await asyncio.sleep(.1)
        return False
    if platform == 'xiaohongshu':
        from uploader.xhs_reopen import reopen_xhs_draft
        return await reopen_xhs_draft(page, candidate)
    if _id_in_url(page.url, candidate['id']):
        await page.reload(wait_until='domcontentloaded', timeout=60000)
        return True
    # A href must contain the exact draft ID returned by the website. Same-title
    # rows do not qualify and are not clicked.
    for frame in page.frames:
        links = frame.locator('a[href]')
        for index in range(await links.count()):
            link = links.nth(index)
            href = await link.get_attribute('href')
            if href and _id_in_url(href, candidate['id']) and await link.is_visible():
                await link.click(timeout=30000)
                return True
    return False


async def _editor_values(page, selectors):
    values = []
    for frame in page.frames:
        for selector in selectors:
            matches = frame.locator(selector)
            for index in range(await matches.count()):
                control = matches.nth(index)
                if not await control.is_visible():
                    continue
                try:
                    value = await control.evaluate(r"""e => {
                        if(/^(INPUT|TEXTAREA)$/.test(e.tagName))return e.value;
                        const paragraphs=Array.from(e.children);
                        if(paragraphs.length && paragraphs.every(p=>p.tagName==='P'))
                            return paragraphs.map(p=>p.innerText.replace(/\n$/, '')).join('\n');
                        return e.innerText;
                    }""")
                    values.append(str(value).replace('\r\n', '\n').strip())
                except Exception:
                    pass
    return values


async def _ui_fields(page, platform, expected):
    fields = expected.get('platform_fields', {})
    body = fields.get('caption') if platform in ('douyin', 'kuaishou', 'tencent') else fields.get('description', '')
    bodies = await _editor_values(page, (
        'textarea[placeholder*="简介"]', 'textarea[placeholder*="正文"]',
        'div.input-editor[contenteditable]:not([contenteditable="false"])',
        '#work-description-edit', '.zone-container[contenteditable="true"]',
        '.ql-editor[contenteditable="true"]', 'div[contenteditable="true"]',
    ))
    wanted_bodies = {str(body or '').replace('\r\n', '\n').strip()} if platform in ('douyin', 'kuaishou', 'tencent') else description_values(platform, fields)
    if platform == 'douyin':
        from uploader.dy_editor import normalize_douyin_editor_text
        bodies = [normalize_douyin_editor_text(value) for value in bodies]
        wanted_bodies = {normalize_douyin_editor_text(value) for value in wanted_bodies}
    if not wanted_bodies.intersection(bodies):
        return False
    if platform in ('douyin', 'xiaohongshu', 'bilibili'):
        titles = await _editor_values(page, ('input[placeholder*="标题"]', 'textarea[placeholder*="标题"]'))
        if str(fields.get('title', '')).strip() not in titles:
            return False
    if platform == 'tencent' and fields.get('short_title'):
        short = await _editor_values(page, ('input[placeholder*="短标题"]',))
        if fields['short_title'] not in short:
            return False
    # Saved record already checks exact tag values. The reopened editor must
    # also show those tags, rather than matching hidden data or a list row.
    if platform in ('xiaohongshu', 'bilibili'):
        combined = '\n'.join(part for part in (str(fields.get('description') or '').strip(), ' '.join('#' + tag for tag in fields.get('tags', []))) if part)
        if platform == 'xiaohongshu' and combined in bodies:
            return True
        for tag in fields.get('tags', []):
            found = False
            for frame in page.frames:
                for value in (tag, '#' + tag):
                    locator = frame.get_by_text(value, exact=True)
                    for index in range(await locator.count()):
                        found = found or await locator.nth(index).is_visible()
            if not found:
                return False
    return True


def _asset_id_matches(url, resource_id):
    if isinstance(resource_id, str) and re.fullmatch(r'tc-resource:[0-9a-f]{64}', resource_id):
        from uploader.tc_resource import tc_resource_identity
        return tc_resource_identity(str(url)) == resource_id
    if str(url) == str(resource_id):
        return True
    parsed = urlparse(str(url))
    if parsed.scheme == 'blob':
        return False
    identifier = str(resource_id).strip('/')
    if '/' in identifier:
        return parsed.path.strip('/') == identifier
    return identifier in parsed.path.strip('/').split('/') or any(identifier in values for values in parse_qs(parsed.query).values())


async def _ui_assets(page, candidate, *, video_only=False):
    """A saved cache alone does not prove that its media was restored in the UI."""
    if platform_id(getattr(getattr(page, '_sau_draft_evidence', None), 'platform', '')) == 'tencent' and not video_only:
        from uploader.tc_assets import tc_reopened_assets_match
        return await tc_reopened_assets_match(page, candidate)
    dy = platform_id(getattr(getattr(page, '_sau_draft_evidence', None), 'platform', '')) == 'douyin'
    video_seen, cover_seen = False, False
    if dy:
        from uploader.dy_reopen import read_dy_visible_video
        from uploader.dy_cover_readback import read_dy_visible_cover_cards
        observed = await read_dy_visible_video(page, candidate.get('video_ids', []))
        video_seen = bool(observed and observed.get('video_count') == 1)
        if video_only:
            return video_seen
        covers = await read_dy_visible_cover_cards(page, candidate.get('cover_ids', []))
        rows = covers.get('covers', []) if isinstance(covers, dict) else []
        cover_seen = bool(rows) and all(any(resource in row['ids'] for row in rows)
                                      for resource in candidate.get('cover_ids', []))
        for slot, resources in candidate.get('cover_slots', {}).items():
            if resources and not any(row.get('slot') == slot and set(resources).issubset(row['ids']) for row in rows):
                cover_seen = False
        return video_seen and cover_seen
    for frame in page.frames:
        media = frame.locator('video,[data-video-id],[data-file-id],[data-cid]')
        for index in range(await media.count()):
            node = media.nth(index)
            if not await node.is_visible():
                continue
            values = await node.evaluate("e => [e.currentSrc,e.getAttribute('src'),e.getAttribute('data-video-id'),e.getAttribute('data-file-id'),e.getAttribute('data-cid')].filter(Boolean)")
            if not dy:
                video_seen = video_seen or any(_asset_id_matches(value, resource) for value in values for resource in candidate.get('video_ids', []))
        if video_only:
            continue
        images = frame.locator('img[src],[data-cover-id],[style*="background"],[class*="cover"]')
        for index in range(await images.count()):
            node = images.nth(index)
            if not await node.is_visible():
                continue
            values = await node.evaluate("e => [e.getAttribute('src'),e.getAttribute('data-cover-id'),...Array.from(getComputedStyle(e).backgroundImage.matchAll(/url\\([\"']?(.*?)[\"']?\\)/g),m=>m[1])].filter(Boolean)")
            cover_seen = cover_seen or any(_asset_id_matches(value, resource) for value in values for resource in candidate.get('cover_ids', []))
    return video_seen if video_only else video_seen and cover_seen


async def _reopened_ui_gates(page, platform, expected, candidate):
    """Read both visible-field and asset gates without short-circuiting."""
    fields_ok = await _ui_fields(page, platform, expected)
    assets_ok = await _ui_assets(page, candidate)
    return fields_ok, assets_ok, bool(fields_ok and assets_ok)


def _tc_reopen_gate_diagnostic(session, candidate, reopened, expected, ui_fields, ui_assets,
                               account_after, state):
    """Emit redacted Tencent reopen gates once per distinct gate state."""
    candidate_record = candidate if isinstance(candidate, dict) else {}
    reopened_record = reopened if isinstance(reopened, dict) else {}
    original_account = getattr(session, 'account', {})
    original_account = original_account if isinstance(original_account, dict) else {}
    after_account = account_after if isinstance(account_after, dict) else {}

    def list_count(record, key):
        value = record.get(key)
        return len(value) if isinstance(value, (list, tuple, set)) else 0

    candidate_id = candidate_record.get('id')
    reopen_at = getattr(session, 'reopen_at', None)
    reads_after_reopen = []
    same_id_reads = []
    for row in (getattr(session, 'reads', None) or []):
        if not isinstance(row, dict):
            continue
        try:
            in_window = reopen_at is not None and float(row.get('observed_at')) >= float(reopen_at)
        except (TypeError, ValueError):
            in_window = False
        if in_window:
            reads_after_reopen.append(row)
            if row.get('id') == candidate_id:
                same_id_reads.append(row)

    try:
        fields_match = reopened is not None and field_match('tencent', reopened, expected) is True
    except Exception:
        fields_match = False
    gates = {
        'candidate_found': bool(candidate),
        'reopened_found': bool(reopened),
        'ui_fields': ui_fields is True,
        'ui_assets': ui_assets is True,
        'same_id': bool(candidate and reopened and reopened_record.get('id') == candidate_record.get('id')),
        'video_ids_equal': bool(candidate and reopened and reopened_record.get('video_ids') == candidate_record.get('video_ids')),
        'cover_ids_equal': bool(candidate and reopened and reopened_record.get('cover_ids') == candidate_record.get('cover_ids')),
        'cover_slots_equal': bool(candidate and reopened and reopened_record.get('cover_slots') == candidate_record.get('cover_slots')),
        'field_match': fields_match,
        'account_after_verified': after_account.get('verified') is True,
        'account_identity_equal': after_account.get('identity') == original_account.get('identity'),
    }
    counts = {
        'reads_after_reopen': len(reads_after_reopen),
        'same_id_reads_after_reopen': len(same_id_reads),
        'candidate_video_ids': list_count(candidate_record, 'video_ids'),
        'reopened_video_ids': list_count(reopened_record, 'video_ids'),
        'candidate_cover_ids': list_count(candidate_record, 'cover_ids'),
        'reopened_cover_ids': list_count(reopened_record, 'cover_ids'),
        'candidate_cover_slots': len(candidate_record.get('cover_slots')) if isinstance(candidate_record.get('cover_slots'), dict) else 0,
        'reopened_cover_slots': len(reopened_record.get('cover_slots')) if isinstance(reopened_record.get('cover_slots'), dict) else 0,
    }
    signature = tuple(gates.values())
    if state.get('signature') == signature:
        return False
    diagnostic = {'event': 'tc_reopen_gates', **gates, 'counts': counts}
    print('DRAFT_DIAGNOSTIC=' + json.dumps(diagnostic, ensure_ascii=False), flush=True)
    state['signature'] = signature
    return True


async def read_checkpoint_composer_media(page, proof):
    """Confirm a unique current component against previously sealed resources.

    This returns IDs only after the normal _ui_assets match. It creates no
    upload proof, ignores Blob URLs, and refuses multiple visible components.
    XHS uses its existing mounted publisher getter instead of DOM guesses.
    """
    if proof.get('identity', {}).get('platform_id') == 'douyin':
        from uploader.dy_reopen import read_dy_visible_video
        known = sorted({value for link in proof.get('upload_links', []) if link.get('kind') == 'video'
                        for value in link.get('ids', [])})
        media = await read_dy_visible_video(page, known)
        return {**media, 'source': 'platform_composer'} if media else None
    if proof.get('identity', {}).get('platform_id') == 'xiaohongshu':
        from uploader.xhs_evidence import read_xhs_composer_media
        return await read_xhs_composer_media(page)
    if proof.get('identity', {}).get('platform_id') == 'tencent':
        from uploader.tc_editor import read_tc_composer_media
        return await read_tc_composer_media(page)
    if proof.get('identity', {}).get('platform_id') == 'bilibili':
        import base64
        import hashlib
        from uploader.bili_controls import _STATE, VIDEO_INPUT
        session = getattr(page, '_sau_draft_evidence', None)
        observer = getattr(session, 'bili_observer', None)
        transport = getattr(observer, 'transport', None)
        identity, asset = proof.get('identity', {}), proof.get('assets', {}).get('video', {})
        links = [link for link in proof.get('upload_links', []) if link.get('kind') == 'video']
        digest = identity.get('video_sha256')
        if (observer is None or transport is None or not observer._current() or
                not transport._canonical(transport.context_id) or not re.fullmatch(r'[0-9a-f]{64}', str(digest)) or
                session.expected.get('video_sha256') != digest or asset.get('sha256') != digest or
                type(asset.get('size')) is not int or asset['size'] <= 0 or
                not isinstance(asset.get('name'), str) or not asset['name'] or len(links) != 1 or
                links[0].get('complete') is not True or links[0].get('sha256') != digest or
                not isinstance(links[0].get('ids'), list) or len(links[0]['ids']) != 2 or
                not all(isinstance(value, str) and value for value in links[0]['ids'])):
            return None
        # Capture actual objects in the unique native default context. The
        # retained remote object is private to this read; no page globals,
        # SDK methods, API requests or observer upload reports are created.
        capture = r"""function(){
          try{
            const state=(STATE)();
            if(!state||state.task_count!==1||state.done_count!==1||state.record_count!==1||state.input_count!==1||state.selected_count!==1)return null;
            const seen=new Set();for(const el of document.querySelectorAll('*'))for(let v=el.__vue__,n=0;v&&n<32;v=v.$parent,n++){
              if(seen.has(v))break;seen.add(v);
            }
            const ps=[...seen].filter(v=>v.$el?.isConnected&&Array.isArray(v.videoTasks)&&typeof v.checkSubTask==='function'&&typeof v.handleSubmitDraft==='function');
            if(ps.length!==1)return null;
            const p=ps[0],m=p.$taskConfig,parent=m.tasks[0],t=p.videoTasks[0],sdk=t.uploader,inner=sdk?.uploader;
            const record=p.$recordConfig.data[0],video=record?.videos?.[0],file=t.file;
            if(!record||record.id!==p.id||!Array.isArray(record.videos)||record.videos.length!==1||
               !(file instanceof File)||sdk?.file!==file||inner?.file!==file||inner?.completed!==true||
               !['stdSlice','slice'].includes(sdk.uploaderType)||document.querySelector(VIDEO_INPUT).files[0]!==file||
               !['string','number'].includes(typeof t.cid)||!['string','number'].includes(typeof sdk.cid)||
               !/^[1-9][0-9]{0,19}$/.test(String(t.cid))||String(t.cid)!==String(sdk.cid)||String(t.cid)!==String(video?.cid)||
               typeof t.filename!=='string'||!t.filename||t.filename.length>2048||/[\s\x00-\x1f\x7f]/.test(t.filename)||
               t.filename!==sdk.filename||t.filename!==video.filename)return null;
            const size=Reflect.apply(Object.getOwnPropertyDescriptor(Blob.prototype,'size').get,file,[]);
            const name=Reflect.apply(Object.getOwnPropertyDescriptor(File.prototype,'name').get,file,[]);
            if(!Number.isSafeInteger(size)||size<=0||!name)return null;
            return {p,m,parent,t,sdk,inner,record,video,file,cid:String(t.cid),filename:t.filename,size,name};
          }catch(_){return null;}
        }""".replace('STATE', _STATE).replace('VIDEO_INPUT', json.dumps(VIDEO_INPUT))
        live = """const now=(CAPTURE)();if(!now||!['p','m','parent','t','sdk','inner','record','video','file','cid','filename','size','name'].every(k=>now[k]===this[k]))return null;""".replace('CAPTURE', capture)
        metadata = 'function(){' + live + 'return {cid:this.cid,filename:this.filename,size:this.size,name:this.name};}'
        chunk = r"""async function(offset,length){
          LIVE
          const part=Reflect.apply(Blob.prototype.slice,this.file,[offset,offset+length]);
          const bytes=new Uint8Array(await Reflect.apply(Blob.prototype.arrayBuffer,part,[]));
          {LIVE}
          let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
          return btoa(binary);
        }""".replace('LIVE', live)
        client, context_id, object_id = transport.client, transport.context_id, None
        def current():
            return observer._current() and transport._canonical(context_id)
        async def call(function, arguments=()):
            if not current():
                return None
            result = await client.send('Runtime.callFunctionOn', {'objectId': object_id,
                'functionDeclaration': function, 'arguments': [{'value': value} for value in arguments],
                'awaitPromise': True, 'returnByValue': True})
            return result.get('result', {}).get('value') if current() and not result.get('exceptionDetails') else None
        try:
            result = await client.send('Runtime.evaluate', {'expression': '(' + capture + ')()',
                'contextId': context_id, 'returnByValue': False})
            object_id = result.get('result', {}).get('objectId')
            if not object_id or result.get('exceptionDetails') or not current():
                return None
            before = await call(metadata)
            if (not isinstance(before, dict) or before.get('size') != asset['size'] or before.get('name') != asset['name'] or
                    {before.get('cid'), before.get('filename')} != set(links[0]['ids'])):
                return None
            hashed = hashlib.sha256()
            for offset in range(0, asset['size'], 1048576):
                length = min(1048576, asset['size'] - offset)
                encoded = await call(chunk, (offset, length))
                if not isinstance(encoded, str):
                    return None
                data = base64.b64decode(encoded, validate=True)
                if len(data) != length:
                    return None
                hashed.update(data)
            if hashed.hexdigest() != digest or await call(metadata) != before:
                return None
            return {'source': 'platform_composer', 'video_count': 1,
                    'video_ids': sorted([before['cid'], before['filename']])}
        except Exception:
            return None
        finally:
            if object_id:
                try:
                    await client.send('Runtime.releaseObject', {'objectId': object_id})
                except Exception:
                    pass
    known = sorted({value for link in proof.get('upload_links', []) if link.get('kind') == 'video'
                    for value in link.get('ids', [])})
    if not known or not await _ui_assets(page, {'video_ids': known}, video_only=True):
        return None
    components = []
    script = r"""() => {
      const selector='video,[data-video-id],[data-file-id],[data-cid]';
      const roots=[document],seen=new Set(roots),nodes=[];
      for(let index=0;index<roots.length;index++){
        const root=roots[index];nodes.push(...root.querySelectorAll(selector));
        for(const element of root.querySelectorAll('*'))if(element.shadowRoot?.mode==='open'&&!seen.has(element.shadowRoot)){
          seen.add(element.shadowRoot);roots.push(element.shadowRoot);
        }
      }
      const groups=new Map();
      for(const node of nodes){
        const box=node.getBoundingClientRect(),style=getComputedStyle(node);
        if(!box.width||!box.height||style.display==='none'||style.visibility==='hidden')continue;
        let root=node,ancestor=node.parentElement?.closest(selector);
        while(ancestor){root=ancestor;ancestor=ancestor.parentElement?.closest(selector);}
        const values=[node.currentSrc,node.getAttribute('src'),node.getAttribute('data-video-id'),
                      node.getAttribute('data-file-id'),node.getAttribute('data-cid')].filter(Boolean);
        if(!groups.has(root))groups.set(root,[]);
        groups.get(root).push(...values);
      }
      return Array.from(groups.values(),values=>Array.from(new Set(values)));
    }"""
    for frame in page.frames:
        values = await evaluate_main(frame, script)
        if not isinstance(values, list):
            return None
        components.extend(values)
        # In TC Wujie, the same physical video is rendered in the main shadow
        # root and projected by the owner iframe. Inspect the rendered main
        # component once; fallback child frames serve ordinary iframe editors.
        if proof.get('identity', {}).get('platform_id') == 'tencent' and components:
            break
    if len(components) != 1:
        return None
    matched = sorted({resource for resource in known if any(_asset_id_matches(value, resource)
                     for value in components[0] if not str(value).startswith('blob:'))})
    return {'source': 'platform_composer', 'video_count': 1, 'video_ids': matched} if matched else None


async def _restored_dy_record(page, candidate, binding, account):
    """Read a consumed local cache from its independently restored editor.

    Binding comes from a unique normal recovery control. Only its logical cache
    ID is carried forward; all fields/resource matches below come from live DOM.
    """
    if not binding or binding.get('id') != candidate['id'] or binding.get('owner') != account.get('uid'):
        return None
    titles = set(await _editor_values(page, ('input[placeholder*="标题"]', 'textarea[placeholder*="标题"]')))
    captions = set(await _editor_values(page, ('#work-description-edit', '.zone-container[contenteditable="true"]',
                                               '.ql-editor[contenteditable="true"]', 'div[contenteditable="true"]')))
    if len(titles) != 1 or len(captions) != 1:
        return None
    from uploader.dy_reopen import read_dy_visible_video
    video = await read_dy_visible_video(page, candidate.get('video_ids', []))
    if not video:
        return None
    from uploader.dy_cover_readback import read_dy_visible_cover_cards
    covers = await read_dy_visible_cover_cards(page, candidate.get('cover_ids', []))
    if not covers:
        return None
    rows = covers['covers']
    cover_ids = {resource for row in rows for resource in row['ids']
                 if resource in candidate.get('cover_ids', [])}
    video_ids = set(video['video_ids'])
    slots = {slot: sorted(resource for resource in resources
                         if any(row.get('slot') == slot and resource in row['ids'] for row in rows))
             for slot, resources in candidate.get('cover_slots', {}).items()}
    return {'id': binding['id'], 'owner': str(account.get('uid') or ''), 'title': titles.pop(),
            'caption': captions.pop(), 'description': None, 'tags': None, 'short_title': None,
            'video_ids': sorted(video_ids), 'cover_ids': sorted(cover_ids), 'cover_slots': slots,
            'video_count': 1 if video_ids else 0, 'fresh': True, 'readback_source': 'normal_recovery_editor_dom'}


def _write_receipt(result, receipt_path):
    if receipt_path:
        path = Path(receipt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    print('DRAFT_RESULT=' + json.dumps(result, ensure_ascii=False), flush=True)
    return result


async def save_draft(page, platform, title, receipt_path=None, button_timeout=15, receipt_timeout=45, expected=None):
    """Preserve legacy positional args; only complete evidence can be verified."""
    p = platform_id(platform)
    session = getattr(page, '_sau_draft_evidence', None)
    if session is None:
        session = await begin_draft_verification(page, p, expected or {})
        session.issues.append('observer_started_after_upload')
    elif p != session.platform or (expected is not None and any(expected.get(k) != session.expected.get(k) for k in IDENTITY_KEYS)):
        raise DraftInterrupted('草稿观察器与本次任务标识不匹配，已停止。')
    expected = session.expected
    if str(title).strip() != str(expected.get('title', '')).strip():
        session.issues.append('title_argument_mismatch')
    candidate, reopened, action = None, None, None
    checks, reasons, account_after = dict.fromkeys(CHECKS, False), [], {}
    attempted = False
    tc_reopen_diagnostic_state = {}
    progress_stage = 'save'
    active_progress_operation = None

    def start_progress_operation(stage, name):
        nonlocal active_progress_operation
        progress_operation(stage, name)
        active_progress_operation = (stage, name)

    def finish_progress_operation(stage, name, state):
        nonlocal active_progress_operation
        progress_operation(stage, name, state)
        if active_progress_operation == (stage, name):
            active_progress_operation = None

    set_progress_plan('save', ['guard', 'action', 'response'])
    emit_progress(progress_stage, 'running')
    try:
        with progress_part('save', 'guard'):
            await install_draft_guard(page.context, page)
            await assert_draft_safe(page, p)
            await _checkpoint_before_save(page, session)
        session.action_at = time.time()
        start_progress_operation('save', 'action')
        try:
            action = await _draft_action(page, p, button_timeout)
        except BaseException:
            finish_progress_operation('save', 'action', 'failed')
            raise
        finish_progress_operation('save', 'action', 'completed' if action else 'failed')
        if not action:
            session.issues.append('explicit_draft_action_unavailable')
        deadline = time.monotonic() + receipt_timeout
        if p == 'bilibili':
            from uploader.bili_reopen import bili_open_draft_management
            observer = getattr(session, 'bili_observer', None)
            if not action:
                finish_progress_operation('save', 'response', 'skipped')
                session.issues.append('bili_saved_ack_not_verified')
                deadline = time.monotonic()
            elif observer is None:
                progress_operation('save', 'response', 'failed')
                session.issues.append('bili_saved_ack_not_verified')
                deadline = time.monotonic()
            else:
                start_progress_operation('save', 'response')
                try:
                    saved_ack = await observer.wait_saved_ack(timeout=min(30, max(.01, receipt_timeout)))
                except BaseException:
                    finish_progress_operation('save', 'response', 'failed')
                    raise
                if not saved_ack:
                    finish_progress_operation('save', 'response', 'failed')
                    session.issues.append('bili_saved_ack_not_verified')
                    deadline = time.monotonic()
            if action and observer is not None and saved_ack:
                try:
                    draft_management_open = await bili_open_draft_management(page, timeout=min(10, max(.01, receipt_timeout)))
                except BaseException:
                    raise
                if not draft_management_open:
                    session.issues.append('bili_normal_draft_management_unavailable')
                    deadline = time.monotonic()
            elif action and observer is None:
                pass
        elif action:
            start_progress_operation('save', 'response')
        else:
            progress_operation('save', 'response', 'skipped')
        verify_response_done = False
        verify_content_done = False
        verify_owner_done = False
        while time.monotonic() < deadline:
            await assert_draft_safe(page, p)
            current = await session.candidate()
            if current:
                candidate = current
            # A clicked button or automatic-save timer alone is not persistence.
            # Move on only when the existing observer sees a fresh bound record.
            if candidate and candidate.get('fresh') and progress_stage == 'save':
                finish_progress_operation('save', 'response', 'completed')
                emit_progress('save', 'completed')
                progress_stage = 'verify'
                emit_progress(progress_stage, 'running')
                set_progress_plan('verify', ['response', 'reopen', 'content', 'owner'])
                start_progress_operation('verify', 'response')
            checks, reasons = verify_evidence(p, expected, candidate, session.upload_links(), session.account,
                                               reopened=reopened, ui_fields=False, account_after=account_after)
            before_reopen = all(checks[key] is True for key in CHECKS if key != 'reopened')
            if p == 'bilibili' and candidate:
                # List rows lack description/tags. This gate permits only a
                # normal same-ID read; full fields cannot be copied from ACK.
                before_reopen = await session.bili_observer.pre_reopen_verified(candidate)
            if progress_stage == 'verify' and before_reopen and not verify_response_done:
                finish_progress_operation('verify', 'response', 'completed')
                verify_response_done = True
            if candidate and candidate.get('id') and candidate.get('fresh') and before_reopen and not session.issues and not attempted:
                session.reopen_at = time.time()
                attempted = True
                start_progress_operation('verify', 'reopen')
                if p == 'tencent' and not await session.tc_processing_observer.freeze_for_reopen(candidate):
                    finish_progress_operation('verify', 'reopen', 'failed')
                    session.issues.append('tc_processing_reopen_chain_not_verified')
                    break
                try:
                    reopened_ok = await _reopen(page, p, candidate)
                except BaseException:
                    finish_progress_operation('verify', 'reopen', 'failed')
                    raise
                finish_progress_operation('verify', 'reopen', 'completed' if reopened_ok else 'failed')
                if not reopened_ok:
                    session.issues.append('normal_ui_reopen_unavailable')
            if attempted and not session.issues:
                reopened = await session.reopened_record(candidate)
                account_after = await session.read_account()
                consumed_cache = p == 'douyin' and reopened is None
                if consumed_cache:
                    reopened = await _restored_dy_record(page, candidate, getattr(session, 'normal_ui_binding', None), account_after)
                if active_progress_operation is None:
                    start_progress_operation('verify', 'content')
                if p == 'bilibili':
                    from uploader.bili_controls import bili_reopened_ui_verified
                    ui_verified = await bili_reopened_ui_verified(page, reopened, expected)
                    checks, reasons = verify_evidence(p, expected, reopened or candidate, session.upload_links(), session.account,
                                                       reopened=reopened, ui_fields=ui_verified, account_after=account_after)
                elif p == 'tencent':
                    ui_fields, ui_assets, ui_verified = await _reopened_ui_gates(page, p, expected, candidate)
                    try:
                        _tc_reopen_gate_diagnostic(session, candidate, reopened, expected, ui_fields, ui_assets,
                                                   account_after, tc_reopen_diagnostic_state)
                    except Exception:
                        # Diagnostics are observational only and never affect the verifier gate.
                        pass
                    checks, reasons = verify_evidence(p, expected, candidate, session.upload_links(), session.account,
                                                       reopened=reopened, ui_fields=ui_verified, account_after=account_after)
                else:
                    checks, reasons = verify_evidence(p, expected, candidate, session.upload_links(), session.account,
                                                       reopened=reopened,
                                                       ui_fields=await _ui_fields(page, p, expected) and await _ui_assets(page, candidate),
                                                       account_after=account_after)
                content_ok = all(checks.get(key) is True for key in ('fields', 'cover', 'media', 'reopened'))
                if content_ok and not verify_content_done:
                    finish_progress_operation('verify', 'content', 'completed')
                    verify_content_done = True
                if verify_content_done and not verify_owner_done:
                    start_progress_operation('verify', 'owner')
                    if checks.get('account') is True:
                        finish_progress_operation('verify', 'owner', 'completed')
                        verify_owner_done = True
                if not reasons and all(checks.values()):
                    if consumed_cache:
                        # Official recovery can consume the cache. Re-save via
                        # its normal draft control so the verified result also
                        # remains persisted after we leave the editor.
                        if not await _click_exact(page, DRAFT_BUTTON_NAMES):
                            session.issues.append('restored_local_cache_not_retained')
                            continue
                        retained = await session.candidate()
                        until_retained = min(deadline, time.monotonic() + 5)
                        while not retained and time.monotonic() < until_retained:
                            await asyncio.sleep(.1)
                            await assert_draft_safe(page, p)
                            retained = await session.candidate()
                        if not retained or retained['id'] != candidate['id'] or retained['video_ids'] != reopened['video_ids'] or retained['cover_ids'] != reopened['cover_ids'] or not field_match(p, retained, expected):
                            session.issues.append('restored_local_cache_not_retained')
                            continue
                        candidate = retained
                        account_after = await session.read_account()
                        checks, reasons = verify_evidence(p, expected, candidate, session.upload_links(), session.account,
                                                           reopened=reopened, ui_fields=True, account_after=account_after)
                        if reasons or not all(checks.values()):
                            continue
                    break
            # Missing expected payload cannot be remedied by waiting for a toast.
            if session.issues and (candidate or not action):
                break
            await asyncio.sleep(.25)
        reasons = list(dict.fromkeys(session.issues + reasons))
        verified = bool(not reasons and all(checks.values()) and p in POLICIES)
        if active_progress_operation is not None:
            finish_progress_operation(*active_progress_operation, 'failed')
        cover_observer = getattr(session, 'tc_cover_observer', None)
        if cover_observer is not None:
            trace = await cover_observer.diagnostic_trace()
            realms = [('main', trace['main'])] + [(f'owner_{i}', values)
                      for i, values in enumerate(trace['owner'])]
            for realm, slots in realms:
                for slot, values in slots.items():
                    print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'tc_cover_gates',
                          'realm': realm, 'slot': slot,
                          'failed_gates': {k: v for k, v in values.items() if k.endswith('_false') and v},
                          'counts': {k: v for k, v in values.items() if not k.endswith(('_true', '_false')) and v}},
                          ensure_ascii=False), flush=True)
        print('草稿核验观察：' + json.dumps({'selected_files': len(session.files),
              'upload_exchanges': len(session.exchanges), 'completed_upload_links': len(session.upload_links()),
              'cover_diagnostics': session.tc_cover_observer.diagnostic_counts() if getattr(session, 'tc_cover_observer', None) else None,
              'saved_records': len(session.saves), 'read_records': len(session.reads),
              'matched_record': bool(candidate), 'checks': checks, 'reasons': reasons}, ensure_ascii=False), flush=True)
        location, verified_status = POLICIES.get(p, ('unknown', 'result_unknown'))
        result = {key: expected.get(key) for key in IDENTITY_KEYS}
        result.update(status=verified_status if verified else ('saved_unverified' if candidate or session.saves else 'result_unknown'),
                      platform_id=p, platform=platform, title=title, url=page.url, saved_at=time.time(), verified_at=time.time(),
                      verification=checks, reasons=reasons, account=account_after if verified else session.account,
                      draft_id=candidate.get('id') if candidate else None, save_location=location,
                      action=action, partial=not verified, evidence_version=2,
                      evidence_boundary='This receipt verifies the current browser/UI round trip; future platform resource retention is unknown.')
        if not verified:
            result['message'] = ('已观察到草稿保存记录，但内容、素材或重开核验尚未全部通过；请打开平台检查。'
                                 if candidate or session.saves else
                                 '未收到与本次素材匹配的草稿保存记录；现场已保留，请打开平台检查。')
        written = _write_receipt(result, receipt_path)
        emit_progress(progress_stage, 'completed' if verified and progress_stage == 'verify' else 'failed')
        return written
    except BaseException:
        if active_progress_operation is not None:
            finish_progress_operation(*active_progress_operation, 'failed')
        emit_progress(progress_stage, 'failed')
        raise
    finally:
        await session.close()
