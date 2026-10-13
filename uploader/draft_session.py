"""Read-only session checks. Disk state is never proof of current authentication."""
import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urlparse
from pathlib import Path


class DraftInterrupted(RuntimeError):
    def __init__(self, message, status='result_unknown'):
        super().__init__(message)
        self.status = status


RISK_TEXT = re.compile(r'疑似\s*(?:AI|自动化)|自动化工具发布|操作(?:过于)?频繁|访问(?:过于)?频繁|账号.{0,12}(?:受限|封禁|异常)|账号安全验证|安全校验|请完成.{0,8}(?:验证|拼图)|拖动.{0,8}滑块')
IDENTITY_SCRIPT = r"""() => {
  const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility!=='hidden');
  const ids = [...document.querySelectorAll('[data-account-id],[data-user-id],[data-uid]')]
    .filter(visible).map(e=>e.getAttribute('data-account-id')||e.getAttribute('data-user-id')||e.getAttribute('data-uid')).filter(Boolean);
  // Public hydration/user state, never cookies, access tokens or request headers.
  for(const key of ['USER_INFO','__USER_INFO__','userInfo','__INITIAL_STATE__']) {
    const v=window[key]; if(!v || typeof v!=='object')continue;
    for(const u of [v.userInfo,v.user?.userInfo,v.user,v.accountInfo,v]) {
      if(!u||typeof u!=='object')continue;
      const id=u.user_id||u.userId||u.uid||u.mid||u.finderUsername;
      if(id && (u.nickname||u.nickName||u.name||u.avatar||u.isLogin===true))ids.push(String(id));
    }
  }
  const names=[...document.querySelectorAll('[class*="nickname"],[class*="user-name"],[class*="userName"],[class*="account-name"]')]
    .filter(visible).map(e=>e.innerText?.trim()).filter(s=>s && s.length<=80);
  const avatar=[...document.querySelectorAll('img[class*="avatar"],img[alt*="头像"],[class*="user-avatar"]')].some(visible);
  const login=[...document.querySelectorAll('[class*="login-box"],main#login-form,[data-auth-state="logged-out"]')].some(visible);
  const challenge=[...document.querySelectorAll('iframe[src*="captcha"],[data-risk-state="blocked"]')].some(visible);
  const files=[...document.querySelectorAll('input[type="file"]')].some(e=>/video|mp4|mov/.test(e.accept||''));
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,{acceptNode:n=>
    n.parentElement?.closest('input,textarea,[contenteditable="true"],script,style')||!visible(n.parentElement)
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  const statusText=[];let textNode;
  while((textNode=walker.nextNode()))statusText.push(textNode.textContent);
  return {ids:[...new Set(ids)],names:[...new Set(names)],avatar,login,challenge,files,
    text:statusText.join('\n').slice(0,30000),url:location.href};
}"""

MARKERS = {
    'douyin': ('内容管理', '作品管理', '数据中心', '互动管理'),
    'xiaohongshu': ('发布笔记', '笔记管理', '数据看板'),
    'tencent': ('发表视频', '发表动态', '内容管理', '数据中心', '视频管理'),
    'bilibili': ('稿件管理', '视频投稿', '上传视频'),
    'kuaishou': ('作品管理', '内容管理', '数据中心', '账号管理', '发布作品', '发布视频'),
}
# Exact creator hosts already used by the embedded app and draft navigation.
# 127.0.0.1 is the existing local fixture host, not a suffix or subdomain match.
CREATOR_HOSTS = {
    'douyin': 'creator.douyin.com',
    'xiaohongshu': 'creator.xiaohongshu.com',
    'tencent': 'channels.weixin.qq.com',
    'bilibili': 'member.bilibili.com',
    'kuaishou': 'cp.kuaishou.com',
}
# Owner responses whose confirmed host is not the creator page.
# Exact host only. Public anonymous nav is code=-101, data.isLogin=false.
OWNER_API_HOSTS = {
    'bilibili': 'api.bilibili.com',
}
_GUEST_IDS = {'', '0', '-1', 'null', 'undefined'}
_OWNER_ID_KEYS = ('user_id', 'userId', 'uid', 'mid', 'finderUsername', 'finder_username')
_OWNER_CHILD_KEYS = ('data', 'finderUser', 'finder_user', 'finderUserInfo', 'user', 'userInfo', 'user_info', 'account', 'currentAccount')
_FINDER_KEYS = ('finderUser', 'finder_user', 'finderUserInfo', 'finder')

# Patchright evaluates in an isolated world: DOM is shared but the website's
# Vue stores/globals are not. Read only the documented owner-shaped branches in
# the page's default world. Never inspect storage, credentials or other users.
OWNER_STATE_SCRIPT = (Path(__file__).resolve().parents[1] / 'ui-assets/session-owner.js').read_text(encoding='utf-8').strip()


def _drop_owner_session(page):
    session = getattr(page, '_sau_owner_cdp', None)
    setattr(page, '_sau_owner_cdp', None)
    setattr(page, '_sau_owner_cdp_url', None)
    return session


def _schedule_owner_detach(page, session):
    """Release one CDP session. The closure never detaches a later session."""
    if session is None:
        return None
    tasks = getattr(page, '_sau_owner_detach_tasks', None)
    if not isinstance(tasks, list):
        tasks = []
        try:
            setattr(page, '_sau_owner_detach_tasks', tasks)
        except Exception:
            pass

    async def detach():
        try:
            await session.detach()
        except Exception:
            pass

    try:
        task = asyncio.get_running_loop().create_task(detach())
    except RuntimeError:
        return None
    tasks.append(task)
    return task


def _owner_response_hosts(platform):
    hosts = {CREATOR_HOSTS[platform], '127.0.0.1'}
    api_host = OWNER_API_HOSTS.get(platform)
    if api_host:
        hosts.add(api_host)
    return hosts


async def _owner_cdp_session(page):
    """Rebind after navigation or close so a destroyed world cannot be reused."""
    if page.is_closed():
        _schedule_owner_detach(page, _drop_owner_session(page))
        return None
    url = page.url
    session = getattr(page, '_sau_owner_cdp', None)
    if session is not None and getattr(page, '_sau_owner_cdp_url', None) == url:
        return session
    previous = _drop_owner_session(page)
    if previous is not None:
        try:
            await previous.detach()
        except Exception:
            pass
    session = await page.context.new_cdp_session(page)
    setattr(page, '_sau_owner_cdp', session)
    setattr(page, '_sau_owner_cdp_url', url)
    return session


async def current_owner_state(platform, page):
    """Same target, default JS world; no navigation or network keepalive."""
    try:
        if page.is_closed() or urlparse(page.url).hostname not in (CREATOR_HOSTS.get(platform), '127.0.0.1'):
            return {}
        expression = OWNER_STATE_SCRIPT + '(' + json.dumps(platform) + ')'
        for attempt in (1, 2):
            session = await _owner_cdp_session(page)
            if session is None:
                return {}
            try:
                result = await session.send('Runtime.evaluate', {
                    'expression': expression,
                    'returnByValue': True,
                })
            except Exception:
                previous = _drop_owner_session(page)
                if previous is not None:
                    try:
                        await previous.detach()
                    except Exception:
                        pass
                if attempt == 2:
                    return {}
                continue
            if result.get('exceptionDetails'):
                previous = _drop_owner_session(page)
                if previous is not None:
                    try:
                        await previous.detach()
                    except Exception:
                        pass
                if attempt == 2:
                    return {}
                continue
            value = result.get('result', {}).get('value')
            return value if isinstance(value, dict) else {}
        return {}
    except Exception:
        return {}

OWNER_PATHS = {
    'douyin': re.compile(r'/(?:web/api/media/user/info|aweme/v1/creator/(?:pc/)?user/info)/?$'),
    'xiaohongshu': re.compile(r'/api/galaxy/user/info/?$'),
    'tencent': re.compile(r'/(?:cgi-bin/mmfinderassistant-bin/)?auth/auth_data/?$'),
    'bilibili': re.compile(r'/x/web-interface/nav/?$'),
    # The works/common current/user response is a permissions object (prGr),
    # not the signed-in creator. Observe the account bootstrap used by the
    # official user-store mutation so a permissions response cannot log out
    # an otherwise verified owner.
    'kuaishou': re.compile(r'/rest/v2/creator/pc/authority/account/current/?$'),
}


def _valid_uid(value):
    # bool is an int subclass; False must not become the owner id "False".
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if text in _GUEST_IDS:
        return None
    return text


def _explicit_logout(data):
    """True only for a boolean false flag. Missing and zero are not logout."""
    return isinstance(data, dict) and (data.get('isLogin') is False or data.get('logined') is False)


def _finder_uid(data, depth=0):
    """Video-account creator only. The WeChat operator uid is not a candidate."""
    if not isinstance(data, dict) or depth > 5 or _explicit_logout(data):
        return None
    found = []
    for key in _FINDER_KEYS:
        child = data.get(key)
        if isinstance(child, dict) and not _explicit_logout(child):
            text = _valid_uid(child.get('finderUsername') or child.get('username'))
            if text:
                found.append(text)
    for key in ('finderUsername', 'finder_username'):
        text = _valid_uid(data.get(key))
        if text:
            found.append(text)
    for key in ('data',) + _FINDER_KEYS:
        child = data.get(key)
        if isinstance(child, dict):
            text = _finder_uid(child, depth + 1)
            if text:
                found.append(text)
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0]
    return None


def _owner_uid(data, depth=0, platform=None):
    if platform == 'tencent':
        return _finder_uid(data)
    found = []

    def walk(node, level):
        if not isinstance(node, dict) or level > 5 or _explicit_logout(node):
            return
        for key in _FINDER_KEYS:
            child = node.get(key)
            if isinstance(child, dict) and not _explicit_logout(child):
                text = _valid_uid(child.get('finderUsername') or child.get('username'))
                if text:
                    found.append(text)
        for key in _OWNER_ID_KEYS:
            text = _valid_uid(node.get(key))
            if text:
                found.append(text)
        if node.get('id') and any(node.get(key) for key in ('nickname', 'nickName', 'avatar', 'userName')):
            text = _valid_uid(node.get('id'))
            if text:
                found.append(text)
        for key in _OWNER_CHILD_KEYS:
            child = node.get(key)
            if isinstance(child, dict):
                walk(child, level + 1)

    if isinstance(data, dict):
        walk(data, depth)
    unique = list(dict.fromkeys(found))
    # More than one distinct id is another author or an operator, not proof.
    if len(unique) == 1:
        return unique[0]
    return None


def _owner_endpoint(platform, url):
    parsed = urlparse(url)
    return parsed.hostname in _owner_response_hosts(platform) and OWNER_PATHS[platform].search(parsed.path or '') is not None


def observe_session(platform, page):
    """Observe the website's own current-user request; never replay signed APIs."""
    previous = getattr(page, '_sau_session_owner_remove', None)
    if previous:
        previous()
    observe_session.epoch = getattr(observe_session, 'epoch', 0) + 1
    epoch = observe_session.epoch
    state = {'owner': None, 'invalid': False, 'invalid_reason': None, 'generation': 0, 'epoch': epoch}
    setattr(page, '_sau_session_owner', state)
    # request id -> (this observer's epoch, navigation generation at send time)
    issued = {}

    def retire():
        state['generation'] += 1
        state.update(owner=None, invalid=False, invalid_reason=None)
        _schedule_owner_detach(page, _drop_owner_session(page))

    def on_request(request):
        if _owner_endpoint(platform, getattr(request, 'url', '')):
            issued[id(request)] = (epoch, state['generation'])

    async def on_response(response):
        request = getattr(response, 'request', None)
        stamp = issued.pop(id(request), None) if request is not None else None
        # A response-time generation would miss a request sent before navigation.
        # Only the stamp taken when that request was issued can be accepted.
        if stamp != (epoch, state['generation']) or page.is_closed():
            return
        try:
            data = await response.json()
        except Exception:
            if stamp != (epoch, state['generation']) or page.is_closed():
                return
            state.update(owner=None, invalid=True, invalid_reason='unreadable_response')
            return
        if stamp != (epoch, state['generation']) or page.is_closed():
            return
        if not isinstance(data, dict):
            state.update(owner=None, invalid=True, invalid_reason='response_not_object')
            return
        code = data.get('code', data.get('status_code', data.get('errCode', data.get('errcode'))))
        result = data.get('result')
        request_path = urlparse(getattr(request, 'url', '')).path
        current_path = urlparse(page.url).path
        if (platform == 'douyin' and request_path in (
                '/aweme/v1/creator/pc/user/info/', '/aweme/v1/creator/user/info/', '/web/api/media/user/info/') and
                current_path in ('/creator-micro/content/upload', '/creator-micro/content/post/video') and
                200 <= response.status < 300 and code in (None, 0, '0') and
                (isinstance(result, (dict, list)) or result in (None, 0, 1, '0', '1'))):
            # The provider constructs one composite userInfo from these three
            # bootstraps. Neither a missing UID in one successful response nor
            # fastUserInfo's different UID can override that composite owner.
            # The exact mounted Provider still supplies (or refuses) identity.
            return
        if response.status in (401, 403) or code not in (None, 0, '0') or result not in (None, 0, 1, '0', '1'):
            state.update(owner=None, invalid=True, invalid_reason='response_rejected')
            return
        uid = _owner_uid(data, platform=platform)
        # A guest, explicit logout, or unreadable body must not keep a prior owner.
        state.update(owner=uid, invalid=uid is None, invalid_reason='owner_not_unique' if uid is None else None)

    def on_navigation(frame):
        if frame == page.main_frame:
            retire()

    def on_close(_page=None):
        retire()

    page.on('request', on_request)
    page.on('response', on_response)
    page.on('framenavigated', on_navigation)
    page.on('close', on_close)

    def remove():
        retire()
        for event, handler in (
            ('request', on_request), ('response', on_response),
            ('framenavigated', on_navigation), ('close', on_close),
        ):
            try:
                page.remove_listener(event, handler)
            except Exception:
                pass
        if getattr(page, '_sau_session_owner_remove', None) is remove:
            setattr(page, '_sau_session_owner_remove', None)
    setattr(page, '_sau_session_owner_remove', remove)
    return remove


async def inspect_session(platform, page):
    if platform not in MARKERS:
        raise ValueError('未知平台。')
    if page.is_closed():
        raise DraftInterrupted('平台页面已关闭，任务已停止。', 'needs_reauthentication')
    records = []
    for frame in page.frames:
        try:
            records.append(await frame.evaluate(IDENTITY_SCRIPT))
        except Exception:
            continue
    risk = next((RISK_TEXT.search(r['text']) for r in records if RISK_TEXT.search(r['text'])), None)
    if risk or any(r['challenge'] for r in records):
        raise DraftInterrupted('平台要求安全验证或提示操作受限，已保留页面并停止。', 'risk_paused')
    if any(r['login'] for r in records) or any('open.weixin.qq.com/connect/qrconnect' in f.url for f in page.frames):
        return {'verified': False, 'platform': platform, 'status': 'needs_reauthentication', 'diagnostic': 'visible_login_marker'}
    if re.search(r'(?:/login(?:[/?#]|$)|passport\.)', page.url, re.I):
        return {'verified': False, 'platform': platform, 'status': 'needs_reauthentication', 'diagnostic': 'login_route'}
    owner_state = await current_owner_state(platform, page)
    if owner_state.get('loggedOut'):
        return {'verified': False, 'platform': platform, 'status': 'needs_reauthentication', 'diagnostic': 'owner_logged_out'}
    host = urlparse(page.url).hostname
    # Production page attributes may belong to other content authors.
    ids = set(i for r in records for i in r['ids']) if host in (None, '127.0.0.1') else set()
    if owner_state.get('owner'):
        # Unrelated data-user-id on a feed is not the creator's identity.
        ids = {owner_state['owner']}
    observed = getattr(page, '_sau_session_owner', {})
    if observed.get('invalid'):
        return {'verified': False, 'platform': platform, 'status': 'needs_reauthentication',
                'diagnostic': observed.get('invalid_reason') or 'owner_response_invalid'}
    if observed.get('owner'):
        ids.add(observed['owner'])
    ready = any(r['files'] for r in records) or any(sum(m in r['text'] for m in MARKERS[platform]) >= 2 for r in records)
    creator_host = CREATOR_HOSTS[platform]
    current_creator = host == creator_host and bool(owner_state.get('owner'))
    if len(ids) != 1 or not (ready or current_creator):
        # A public upload control/menu, avatar, or display name alone is ambiguous.
        return {'verified': False, 'platform': platform, 'status': 'needs_reauthentication',
                'reason': '创作者身份尚未确认', 'account_conflict': len(ids) > 1}
    uid = next(iter(ids))
    return {'verified': True, 'platform': platform, 'uid': uid,
            'identity': hashlib.sha256((platform + ':' + uid).encode()).hexdigest(),
            'checked_at': time.time(), 'source': owner_state.get('source') or 'website-owner-response',
            'editor_ready': ready}


async def verify_session(platform, page, timeout=60, settle=6):
    until = time.monotonic() + timeout
    stable_since = None
    identity = None
    while time.monotonic() < until:
        current = await inspect_session(platform, page)
        if current.get('account_conflict'):
            raise DraftInterrupted('页面与当前会话的账号身份不一致，已停止。', 'needs_reauthentication')
        if current['verified']:
            if current['identity'] != identity:
                identity, stable_since = current['identity'], time.monotonic()
            if time.monotonic() - stable_since >= settle:
                return current
        else:
            identity, stable_since = None, None
        await asyncio.sleep(.4)
    raise DraftInterrupted('当前登录或创作者身份未确认；素材已保留，请在平台页完成认证。', 'needs_reauthentication')


async def watch_session(platform, page, account):
    """No periodic reload/keepalive. Observe only the already open page."""
    while True:
        await asyncio.sleep(.5)
        current = await inspect_session(platform, page)
        if current.get('account_conflict'):
            raise DraftInterrupted('页面与当前会话的账号身份不一致，已停止。', 'needs_reauthentication')
        if current['verified'] and current['identity'] != account['identity']:
            raise DraftInterrupted('当前平台账号发生变化，已停止并保留现场。', 'needs_reauthentication')
        if not current['verified'] and (current.get('reason') is None):
            print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': 'session_watch_unverified',
                  'platform': platform, 'reason': current.get('diagnostic') or 'unknown'}, ensure_ascii=False), flush=True)
            raise DraftInterrupted('登录已失效，已停止并保留上传现场。', 'needs_reauthentication')


async def run_with_session_watch(platform, page, account, coroutine, timeout=7200):
    task = asyncio.create_task(coroutine)
    watch = asyncio.create_task(watch_session(platform, page, account))
    try:
        done, _ = await asyncio.wait([task, watch], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise DraftInterrupted('任务等待超时，上传现场和记录已保留，不自动重传。')
        if watch in done:
            await watch
        return await task
    finally:
        for t in (task, watch):
            if not t.done():
                t.cancel()
        await asyncio.gather(task, watch, return_exceptions=True)
