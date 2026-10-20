"""One-shot, observed submission of an already prepared creator-page video.

This module never uploads files, edits fields, chooses declarations, schedules,
or retries a submit. It only acts on a verified current composer and records
whether this single UI action produced a documented submission receipt.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import math
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from uploader.draft_only import is_publish_request
from uploader.draft_session import DraftInterrupted, inspect_session


PLATFORMS = frozenset({"douyin", "xiaohongshu", "tencent", "bilibili", "kuaishou"})
PUBLISHED_SUBMISSION_VERIFIED = "published_submission_verified"
PUBLISHED_UNVERIFIED = "published_unverified"
PUBLICATION_BLOCKED = "publication_blocked"

# Supported response contracts are documented public APIs or exact native
# contracts read from the official creator source. Other responses remain
# unverified unless a platform success page is explicit.
_DOCUMENTED_API_HOSTS = {
    ("open.douyin.com", "/video/create"): "douyin",
    # Official 1060.abf5c0f7.js SHA-256
    # d6cf9d973d443c64cd5216bf5d1d4de8185947a05637121720971f78d6d6697a:
    # create_v2 resolves the raw body and consumes top-level item_id.
    # HTTP module31687 in douyin_content_index_micro_new.3d7bbdb6.js SHA
    # 1d11fe80cc8ee374765b51d9969e56b9d3e8a5d7ca501ec939d229989823a9fc
    # returns response.data, rejecting code0 + status_msg="url doesn't match".
    ("creator.douyin.com", "/web/api/media/aweme/create_v2"): "douyin_web",
    ("open.kuaishou.com", "/openapi/photo/publish"): "kuaishou",
    ("member.bilibili.com", "/arcopen/fn/archive/add-by-utoken"): "bilibili",
    # Official creator source fresh-bili-index.ad9b754d.js SHA-256
    # d0c283365e4210fa95c166a4f99371397e00ce30a4790fafaa755d3c5e6e799a
    # submitArchive returns data at code==0; its consumer reads aid/bvid.
    ("member.bilibili.com", "/x/vu/web/add/v3"): "bilibili_web",
}
_XHS_SUCCESS_PATH = "/publish/success"


@dataclass(frozen=True)
class _Candidate:
    frame: object
    control: object


@dataclass
class _Observation:
    frame: object
    platform: str
    armed: bool = False
    requests: dict | None = None
    responses: list | None = None
    tasks: list | None = None
    event: asyncio.Event | None = None
    receipt_scope: dict | None = None

    def __post_init__(self):
        self.requests = {}
        self.responses = []
        self.tasks = []
        self.event = asyncio.Event()


def _result(platform, status, code, *, clicked=False, evidence=None, counts=None):
    return {
        "platform": platform,
        "status": status,
        "code": code,
        "clicked": bool(clicked),
        "evidence": evidence or {},
        "observation": counts or {"publish_requests": 0, "publish_responses": 0},
    }


async def _emit(progress, platform, stage, state, code):
    if not progress:
        return
    try:
        value = progress({
            "platform": platform,
            "stage": stage,
            "state": state,
            "code": code,
        })
        if inspect.isawaitable(value):
            await value
    except Exception:
        # Progress is diagnostic only; it must never trigger another submit.
        pass


def _page_frames(page):
    try:
        frames = list(page.frames)
    except Exception:
        return []
    return frames


def _official_editor_route(url, platform):
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return False
    host, path = parsed.hostname, parsed.path.rstrip("/") or "/"
    if platform == "douyin":
        return host == "creator.douyin.com" and path in {
            "/creator-micro/content/post/video",
            "/creator-micro/content/publish",
        }
    if platform == "xiaohongshu":
        return host == "creator.xiaohongshu.com" and path == "/publish/publish"
    if platform == "tencent":
        return host == "channels.weixin.qq.com" and path in {
            "/platform/post/create",
            "/micro/content/post/create",
        }
    if platform == "bilibili":
        return host == "member.bilibili.com" and (
            path.startswith("/platform/upload/video/") or path.startswith("/york/videoup")
        )
    if platform == "kuaishou":
        return host == "cp.kuaishou.com" and path == "/article/publish/video"
    return False


def _looks_like_existing_draft_or_edit(url):
    try:
        query = parse_qs(urlsplit(url).query)
    except (TypeError, ValueError):
        return True
    if any(key.lower() in {"draft_id", "draftid", "existing_draft_id"} for key in query):
        return True
    return any(value.lower() in {"draft", "edit"} for value in query.get("type", []))


async def _visible_any(frame, selectors):
    for selector in selectors:
        try:
            matches = frame.locator(selector)
            for index in range(await matches.count()):
                if await matches.nth(index).is_visible():
                    return True
        except Exception:
            continue
    return False


async def _visible_exact_role_buttons(frame, name):
    matches = frame.get_by_role("button", name=name, exact=True)
    result = []
    for index in range(await matches.count()):
        candidate = matches.nth(index)
        if await candidate.is_visible():
            result.append(candidate)
    return result


# Native Jh button in index-D9cEpjz5.js (SHA-256
# 6810b8d05825220acf807a7841f7773c4ea2e623596f956ad84ec5e631e77d2c)
# renders a clickable div with a nested text div. Loading/disabled use its
# _disabled_* class; edit-section-btns lives inside edit-section-form.
_KS_CONTROL_ENABLED = r"""el => {
 if(location.origin!=='https://cp.kuaishou.com'||
    location.pathname!=='/article/publish/video'||!el.isConnected||
    el.innerText.trim()!=='发布'||el.closest('[role="dialog"],.ant-modal'))return false;
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
   return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 let host=el,form=null;
 if(el.tagName!=='BUTTON'){
   while(host&&host!==document.body&&![...host.classList].some(c=>/^_button_[a-z0-9]+_\d+$/.test(c)))
     host=host.parentElement;
   if(!host||host.tagName!=='DIV'||![...host.classList].some(c=>/^_button-primary_[a-z0-9]+_\d+$/.test(c))||
      host.innerText.trim()!=='发布')return false;
   form=host.closest('[class*="edit-section-form"]');
   const footer=host.closest('[class*="edit-section-btns"]');
   if(!form||!footer||!form.contains(footer)||!visible(form)||
      [...document.querySelectorAll('[class*="edit-section-form"]')].filter(visible).length!==1||
      ![...form.querySelectorAll('[contenteditable="true"]')].some(visible))return false;
 }else{
   form=host.closest('[class*="edit-section-form"],form');
   if(!form||!visible(form)||![...form.querySelectorAll('[contenteditable="true"]')].some(visible))return false;
 }
 if(!visible(host)||Number(getComputedStyle(host).opacity)<.99)return false;
 for(let node=el;node;node=node.parentElement){
   const style=getComputedStyle(node);
   if(node.disabled===true||node.hasAttribute('disabled')||node.inert||
      node.getAttribute('aria-disabled')==='true'||node.getAttribute('aria-busy')==='true'||
      [...node.classList].some(c=>/^_disabled_[a-z0-9]+_\d+$/.test(c)||
        ['disabled','btn-loading','ant-btn-loading'].includes(c))||style.pointerEvents==='none')return false;
   if(node===form||node===document.body)break;
 }
 return true;
}"""


async def _button_enabled(button, platform):
    if platform == "tencent":
        try:
            return await button.is_enabled() and await button.evaluate(_TC_CONTROL_CURRENT) is True
        except Exception:
            return False
    if platform == "kuaishou":
        try:
            return await button.is_enabled() and await button.evaluate(_KS_CONTROL_ENABLED) is True
        except Exception:
            return False
    if platform == "bilibili":
        try:
            return await button.evaluate(
                "el => !el.classList.contains('submit-add-disabled') && "
                "!el.classList.contains('btn-loading') && "
                "el.getAttribute('aria-disabled') !== 'true' && "
                "el.getAttribute('aria-busy') !== 'true' && "
                "el.innerText.trim() === '立即投稿'"
            )
        except Exception:
            return False
    try:
        return await button.is_enabled()
    except Exception:
        return False


async def _bili_upload_ready(page):
    try:
        from uploader.bili_controls import _frame, _state

        frame = await _frame(page, timeout=0.4)
        state = await _state(frame)
        ready = state.get("task_count") == 1 and state.get("done_count") == 1
        return frame if ready else None
    except Exception:
        return None


async def _tencent_upload_ready(page):
    try:
        from uploader.tc_editor import read_tc_material_state

        state = await read_tc_material_state(page)
        return bool(
            state.get("recognized") is True
            and state.get("count") == state.get("video_count") == state.get("done_count") == 1
            and state.get("all_uploaded") is True
            and state.get("all_cover_cropped") is True
        )
    except Exception:
        return False


async def _frame_editor_ready(frame, platform):
    if platform == "douyin":
        fields = (
            'input[placeholder*="填写作品标题"]:visible',
            'div.zone-container[contenteditable="true"]:visible',
        )
        video = (
            'video[src]:not([src=""])',
            'video source[src]:not([src=""])',
            '[class^="long-card"] div:has-text("重新上传")',
            ':text-is("上传成功")',
            ':text-is("视频上传成功")',
        )
    elif platform == "xiaohongshu":
        fields = ('input[placeholder*="填写标题"]:visible',)
        video = (
            'div.preview-new :text-is("上传成功")',
            'div.preview-new :text-is("重新上传")',
            'div.preview-new .stage:has-text("分辨率")',
            ':text-is("视频上传成功")',
        )
    elif platform == "kuaishou":
        fields = ('[contenteditable="true"]:visible',)
        video = (
            'video[src]:not([src=""])',
            'video source[src]:not([src=""])',
            ':text-is("上传成功")',
            ':text-is("视频上传成功")',
            ':text-is("重新上传")',
        )
    else:
        # Tencent and Bilibili use their platform-owned task observers below.
        return True
    for selector in fields:
        if not await _visible_any(frame, (selector,)):
            return False
    return await _visible_any(frame, video)


async def _unique_control_in_frame(frame, platform):
    if platform == "bilibili":
        selector = ".submit-container > div > span.submit-add"
        try:
            matches = frame.locator(selector)
            found = []
            for index in range(await matches.count()):
                candidate = matches.nth(index)
                if await candidate.is_visible():
                    found.append(candidate)
            if len(found) != 1:
                return None, "final_control_not_unique"
            if not await _button_enabled(found[0], platform):
                return None, "final_control_disabled"
            return found[0], None
        except Exception:
            return None, "final_control_unreadable"

    label = "发表" if platform == "tencent" else "发布"
    try:
        found = await _visible_exact_role_buttons(frame, label)
    except Exception:
        return None, "final_control_unreadable"
    if platform == "kuaishou" and not found:
        # The official native primary control is a div, so it has no button
        # role. Exact text is only accepted inside its current editor form.
        try:
            matches = frame.get_by_text("发布", exact=True)
            found = [matches.nth(index) for index in range(await matches.count())
                     if await matches.nth(index).is_visible()]
        except Exception:
            return None, "final_control_unreadable"
    if len(found) != 1:
        return None, "final_control_not_unique"
    if not await _button_enabled(found[0], platform):
        return None, "final_control_disabled"
    return found[0], None


_TC_CONTROL_CURRENT = r"""el => {
 const view=el.ownerDocument?.defaultView,location=view?.location;
 if(!location||location.origin!=='https://channels.weixin.qq.com'||
    !['/platform/post/create','/micro/content/post/create'].includes(location.pathname)||
    !el.isConnected||el.tagName!=='BUTTON'||el.innerText.trim()!=='发表')return false;
 const visible=e=>{const r=e.getBoundingClientRect(),s=view.getComputedStyle(e);
   return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const form=el.closest('div.form-btns');
 return !!form&&visible(form)&&visible(el)&&!el.disabled&&el.getAttribute('aria-disabled')!=='true'&&
   el.getAttribute('aria-busy')!=='true'&&view.getComputedStyle(el).pointerEvents!=='none'&&
   [...form.querySelectorAll('button')].filter(e=>visible(e)&&e.innerText.trim()==='发表').length===1;
}"""


def _owned_editor_frame(page, frame, platform):
    try:
        parsed=urlsplit(frame.url)
        host={'tencent':'channels.weixin.qq.com','bilibili':'member.bilibili.com'}[platform]
        return (frame.page is page and frame in _page_frames(page) and not frame.is_detached() and
                parsed.scheme=='https' and parsed.netloc==host and _official_editor_route(frame.url,platform))
    except Exception:
        return False


def _editor_snapshot(page, platform):
    try:
        main=page.main_frame
        if page.is_closed() or page.url!=main.url or not _owned_editor_frame(page,main,platform):
            return None
        return main,page.url,tuple((frame,frame.url) for frame in _page_frames(page))
    except Exception:
        return None


def _editor_unchanged(page, platform, snapshot):
    return snapshot is not None and _editor_snapshot(page,platform)==snapshot


async def _locate_tencent_control(page):
    snapshot=_editor_snapshot(page,'tencent')
    if snapshot is None or urlsplit(snapshot[1]).path!='/platform/post/create':
        return None,'editor_not_unique_or_wrong_page'
    frames=[frame for frame,_ in snapshot[2] if _owned_editor_frame(page,frame,'tencent')]
    if (len(frames) not in (1,2) or snapshot[0] not in frames or
            any(frame is not snapshot[0] and (urlsplit(frame.url).path!='/micro/content/post/create' or
                                             frame.parent_frame is not snapshot[0]) for frame in frames)):
        return None,'editor_not_unique_or_wrong_page'
    if not await _tencent_upload_ready(page) or not _editor_unchanged(page,'tencent',snapshot):
        return None,'video_not_ready'
    handles=[]
    chosen=None
    try:
        physical=[]
        for frame in frames:
            forms=frame.locator('div.form-btns')
            if await forms.count()>8:
                return None,'final_control_not_unique'
            for index in range(await forms.count()):
                form=forms.nth(index)
                if not await form.is_visible():
                    continue
                buttons=form.get_by_role('button',name='发表',exact=True)
                if await buttons.count()>8:
                    return None,'final_control_not_unique'
                visible=[buttons.nth(i) for i in range(await buttons.count()) if await buttons.nth(i).is_visible()]
                if len(visible)!=1:
                    return None,'final_control_not_unique'
                control=await visible[0].element_handle(timeout=1000)
                if control is None:
                    return None,'final_control_unreadable'
                handles.append(control)
                owner=await control.owner_frame()
                if (owner not in frames or not _owned_editor_frame(page,owner,'tencent') or
                        not _editor_unchanged(page,'tencent',snapshot)):
                    return None,'final_control_owner_changed'
                if (not await control.is_visible() or not await control.is_enabled() or
                        await control.evaluate(_TC_CONTROL_CURRENT) is not True):
                    return None,'final_control_disabled'
                duplicate=False
                for previous,previous_owner in physical:
                    if owner is previous_owner and await previous.evaluate('(el,other)=>el===other',control) is True:
                        duplicate=True
                        break
                if not duplicate:
                    physical.append((control,owner))
        if len(physical)!=1:
            return None,'final_control_not_unique'
        control,owner=physical[0]
        if (not await _tencent_upload_ready(page) or not _editor_unchanged(page,'tencent',snapshot) or
                await control.owner_frame() is not owner or not _owned_editor_frame(page,owner,'tencent') or
                await control.evaluate(_TC_CONTROL_CURRENT) is not True):
            return None,'final_control_changed'
        chosen=control
        return _Candidate(owner,control),None
    except Exception:
        return None,'final_control_unreadable'
    finally:
        for handle in handles:
            if handle is chosen:
                continue
            try:
                await handle.dispose()
            except Exception:
                pass


async def _locate_current_editor(page, platform):
    if platform == "tencent":
        return await _locate_tencent_control(page)
    frames = [frame for frame in _page_frames(page) if _official_editor_route(getattr(frame, "url", ""), platform)]
    snapshot = None
    if platform == "bilibili":
        snapshot = _editor_snapshot(page, platform)
        if snapshot is None:
            return None, "editor_not_unique_or_wrong_page"
        frame = await _bili_upload_ready(page)
        if (frame not in frames or not _owned_editor_frame(page, frame, platform) or
                not _editor_unchanged(page, platform, snapshot)):
            return None, "video_not_ready"
    else:
        if len(frames) != 1:
            return None, "editor_not_unique_or_wrong_page"
        frame = frames[0]
    if platform == "bilibili":
        try:
            title = frame.locator(".video-title .video-title-content input")
            description = frame.locator(".desc-container .ql-editor[contenteditable='true']")
            if (await title.count() != 1 or not await title.first.is_visible()
                    or await description.count() != 1 or not await description.first.is_visible()):
                return None, "editor_not_ready"
        except Exception:
            return None, "editor_not_ready"
    elif not await _frame_editor_ready(frame, platform):
        return None, "editor_or_video_not_ready"

    if platform == "kuaishou":
        try:
            modal = frame.locator("div.ant-modal-confirm-centered:visible")
            for index in range(await modal.count()):
                if await modal.nth(index).is_visible():
                    # The current code does not establish an exact confirmation
                    # label. Never click a generic primary modal button.
                    return None, "confirmation_control_contract_unknown"
        except Exception:
            return None, "editor_not_ready"

    control, error = await _unique_control_in_frame(frame, platform)
    if error:
        return None, error
    if platform == "bilibili" and (
            await _bili_upload_ready(page) is not frame or not _editor_unchanged(page, platform, snapshot) or
            not _owned_editor_frame(page, frame, platform)):
        return None, "final_control_changed"
    return _Candidate(frame, control), None


def _is_observed_publish_request(url, method):
    if is_publish_request(url, method):
        return True
    try:
        parsed = urlsplit(url)
        return (
            method.upper() == "POST"
            and parsed.hostname == "open.douyin.com"
            and parsed.path.rstrip("/") == "/video/create"
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _documented_contract_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                parsed.port not in (None, 443)):
            return None
        host = parsed.hostname
        path = parsed.path.rstrip("/") or "/"
        variant = _DOCUMENTED_API_HOSTS.get((host, path))
        if variant == "douyin_web" and parsed.path not in (path, path + "/"):
            return None
        return variant
    except (TypeError, ValueError):
        return None


def _extract_documented_id(platform, body):
    if not isinstance(body, dict):
        return None
    if platform == "douyin":
        extra, data = body.get("extra"), body.get("data")
        if (isinstance(extra, dict) and type(extra.get("error_code")) is int
                and extra.get("error_code") == 0 and isinstance(data, dict)
                and type(data.get("error_code")) is int and data.get("error_code") == 0
                and isinstance(data.get("item_id"), str) and data["item_id"]):
            return data["item_id"]
    elif platform == "douyin_web":
        identifier = body.get("item_id")
        if (type(body.get("status_code")) is int and body["status_code"] == 0 and
                body.get("status_msg") != "url doesn't match"):
            if type(identifier) is int and identifier > 0:
                return str(identifier)
            if isinstance(identifier, str) and re.fullmatch(r"[1-9][0-9]*", identifier):
                return identifier
    elif platform == "kuaishou":
        info = body.get("video_info")
        if (type(body.get("result")) is int and body.get("result") == 1
                and isinstance(info, dict) and isinstance(info.get("photo_id"), str)
                and info["photo_id"]):
            return info["photo_id"]
    elif platform == "bilibili_web":
        data=body.get("data")
        if type(body.get("code")) is int and body["code"]==0 and isinstance(data,dict):
            aid,bvid=data.get("aid"),data.get("bvid")
            positive_aid=type(aid) is int and aid>0 or isinstance(aid,str) and bool(re.fullmatch(r"[1-9][0-9]*",aid))
            if positive_aid and isinstance(bvid,str) and re.fullmatch(r"BV[A-Za-z0-9]{10}",bvid):
                return bvid
    elif platform == "bilibili":
        data = body.get("data")
        if (type(body.get("code")) is int and body.get("code") == 0
                and isinstance(data, dict) and isinstance(data.get("resource_id"), str)
                and data["resource_id"]):
            return data["resource_id"]
    return None


async def _read_documented_id(response, platform):
    try:
        if int(response.status) < 200 or int(response.status) >= 300:
            return None
        headers = response.headers
        if not isinstance(headers, dict):
            return None
        content_type = str(headers.get("content-type", "")).lower()
        if "json" not in content_type:
            return None
        length = headers.get("content-length")
        if length is not None and int(length) > 65536:
            return None
        body = await response.json()
        return _extract_documented_id(platform, body)
    except Exception:
        return None


def _bind_observer(page, observation):
    loop = asyncio.get_running_loop()

    def on_request(request):
        if not observation.armed or not _is_observed_publish_request(request.url, request.method):
            return
        try:
            if request.frame is not observation.frame:
                return
            if observation.platform == 'xiaohongshu' and (request.method != 'POST' or not _xhs_native_submit_url(request.url)):
                return
        except Exception:
            return
        contract_platform = _documented_contract_url(request.url)
        owner = {"douyin_web": "douyin", "bilibili_web": "bilibili"}.get(contract_platform, contract_platform)
        observation.requests[id(request)] = contract_platform if owner == observation.platform else None
        observation.event.set()

    def on_response(response):
        request = getattr(response, "request", None)
        key = id(request)
        if not observation.armed or key not in observation.requests:
            return

        async def collect():
            platform = observation.requests[key]
            response_platform = platform
            identifier = None
            if response_platform:
                identifier = await _read_documented_id(response, response_platform)
            try:
                status = int(response.status)
            except Exception:
                status = None
            observation.responses.append({
                "http_status": status,
                "documented_platform": response_platform,
                "submission_id": identifier,
            })
            observation.event.set()

        try:
            observation.tasks.append(loop.create_task(collect()))
        except RuntimeError:
            pass

    page.on("request", on_request)
    try:
        page.on("response", on_response)
    except Exception:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass
        raise
    return on_request, on_response


def _remove_observer(page, handlers):
    for event, handler in (("request", handlers[0]), ("response", handlers[1])):
        try:
            page.remove_listener(event, handler)
        except Exception:
            pass


async def _call_restore(restore):
    if not callable(restore):
        return True
    try:
        value = restore()
        if inspect.isawaitable(value):
            await value
        return True
    except Exception:
        return False


def _xhs_success_page(before_url, after_url):
    try:
        before, after = urlsplit(before_url), urlsplit(after_url)
    except (TypeError, ValueError):
        return False
    if (before.geturl() == after.geturl() or before.scheme != 'https' or after.scheme != 'https'
            or before.netloc != 'creator.xiaohongshu.com' or after.netloc != before.netloc
            or before.path != '/publish/publish' or before.fragment or after.fragment
            or _looks_like_existing_draft_or_edit(before_url) or _looks_like_existing_draft_or_edit(after_url)):
        return False
    before_query = parse_qs(before.query, keep_blank_values=True)
    if (set(before_query) - {'target', 'source'} or before_query.get('target') != ['video']
            or any(len(values) != 1 for values in before_query.values())):
        return False
    if after.path == _XHS_SUCCESS_PATH:
        return bool(after.query)
    # Official success chunk1727.9cba91b2.js SHA b928154a... Unicode860:
    # k() pushes Post(source,published:'true'), after the success page's
    # countdown (Unicode1516). Current index.240167e3 redirects Post to
    # newPost (/publish/publish); no draft/edit/target is carried forward.
    after_query = parse_qs(after.query, keep_blank_values=True)
    return (after.path == '/publish/publish' and set(after_query) <= {'source', 'published'}
            and after_query.get('published') == ['true']
            and all(len(values) == 1 for values in after_query.values()))


def _xhs_native_submit_url(url):
    # Current official Vue bc75be57... POST_NOTE at Unicode296285, pG=v
    # at1289564 and production v() at1290622. This is a receipt observation
    # predicate; it does not itself authorize or send the native POST.
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.netloc == 'edith.xiaohongshu.com'
                and parsed.path == '/web_api/sns/v2/note' and not parsed.fragment)
    except (TypeError, ValueError):
        return False


def _xhs_receipt_scope(page, expected, account, frame):
    session = getattr(page, '_sau_draft_evidence', None)
    values = getattr(session, 'expected', {})
    if (session is None or session.page is not page or session.closed or session.platform != 'xiaohongshu'
            or frame is not page.main_frame or expected.get('workflow_action') != 'publish'
            or not expected.get('job_id') or not expected.get('receipt_nonce')
            or any(values.get(key) != expected.get(key) for key in ('job_id', 'receipt_nonce', 'workflow_action'))
            or account.get('verified') is not True or not account.get('identity')
            or account.get('identity') != values.get('account', {}).get('identity')):
        return None
    return {'session': session, 'job_id': expected['job_id'], 'receipt_nonce': expected['receipt_nonce'],
            'account': copy.deepcopy(account)}


async def _xhs_observed_success_page(page, observation, before_url):
    scope = observation.receipt_scope
    if (observation.platform != 'xiaohongshu' or not isinstance(scope, dict)
            or len(observation.requests) != 1 or len(observation.responses) != 1
            or type(observation.responses[0].get('http_status')) is not int
            or not 200 <= observation.responses[0]['http_status'] < 300
            or not _xhs_success_page(before_url, getattr(page, 'url', ''))):
        return False
    session = scope['session']
    url, frame = page.url, page.main_frame

    def current():
        return (getattr(page, '_sau_draft_evidence', None) is session and session.page is page
                and not session.closed and session.platform == 'xiaohongshu'
                and page.main_frame is frame and frame is observation.frame and frame.url == page.url
                and page.url == url and session.expected.get('job_id') == scope['job_id']
                and session.expected.get('receipt_nonce') == scope['receipt_nonce']
                and session.expected.get('workflow_action') == 'publish'
                and session.account.get('identity') == scope['account']['identity']
                and (not scope['account'].get('uid') or
                     str(session.account.get('uid')) == str(scope['account']['uid'])))
    if not current():
        return False
    try:
        account = await asyncio.wait_for(inspect_session('xiaohongshu', page), timeout=2)
        return (current() and isinstance(account, dict) and account.get('verified') is True
                and account.get('identity') == scope['account']['identity']
                and (not scope['account'].get('uid') or str(account.get('uid')) == str(scope['account']['uid'])))
    except Exception:
        return False


async def _wait_for_receipt(page, observation, before_url, timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if observation.tasks:
            await asyncio.gather(*tuple(observation.tasks), return_exceptions=True)
            observation.tasks.clear()
        if await _xhs_observed_success_page(page, observation, before_url):
            return {"kind": "success_page", "platform": "xiaohongshu"}
        # A unique, documented API ID proves the submission was accepted. It
        # still says nothing about review completion or public visibility.
        if len(observation.requests) == len(observation.responses) == 1:
            response = observation.responses[0]
            if response.get("submission_id"):
                return {
                    "kind": "documented_submit_response",
                    "submission_id": response["submission_id"],
                    "http_status": response["http_status"],
                }
            if response.get("documented_platform") and response.get("http_status") is not None:
                return None
        if len(observation.requests) > 1 or len(observation.responses) > 1:
            return None
        try:
            await asyncio.wait_for(observation.event.wait(), timeout=min(.05, max(0, deadline - asyncio.get_running_loop().time())))
            observation.event.clear()
        except asyncio.TimeoutError:
            pass
    if observation.tasks:
        await asyncio.gather(*tuple(observation.tasks), return_exceptions=True)
        observation.tasks.clear()
    if await _xhs_observed_success_page(page, observation, before_url):
        return {"kind": "success_page", "platform": "xiaohongshu"}
    if len(observation.requests) == len(observation.responses) == 1:
        response = observation.responses[0]
        if response.get("submission_id"):
            return {
                "kind": "documented_submit_response",
                "submission_id": response["submission_id"],
                "http_status": response["http_status"],
            }
    return None


async def publish_current(page, platform, expected=None, *, before_click=None, progress=None, receipt_timeout=8):
    """Submit the visible, fully prepared video once and observe its receipt.

    before_click(page, platform) may only release a scoped draft guard and must
    return a sync/async zero-argument restore callback. It must restore its own
    state if it raises before returning. When supplied, a missing restore
    callback fails closed. The callback is never invoked until account, editor,
    video, and the unique exact submit control have all passed validation.

    The result is a plain dictionary with status/code/clicked/evidence and
    privacy-minimal observation counts. It never includes page text, request
    bodies, cookies, response bodies, or full URLs.
    """
    if platform not in PLATFORMS:
        return _result(str(platform), PUBLICATION_BLOCKED, "unsupported_platform")
    expected = expected if isinstance(expected, dict) else {}
    if expected.get("platform") not in (None, platform) or expected.get("platform_id") not in (None, platform):
        return _result(platform, PUBLICATION_BLOCKED, "expected_platform_mismatch")
    await _emit(progress, platform, "check", "running", "preflight")
    try:
        if page.is_closed():
            await _emit(progress, platform, "check", "failed", "page_closed")
            return _result(platform, PUBLICATION_BLOCKED, "page_closed")
        page_urls = [getattr(frame, "url", "") for frame in _page_frames(page)]
        if any(_looks_like_existing_draft_or_edit(url) for url in page_urls):
            await _emit(progress, platform, "check", "failed", "draft_or_edit_page")
            return _result(platform, PUBLICATION_BLOCKED, "draft_or_edit_page")

        account = await inspect_session(platform, page)
        if not isinstance(account, dict) or account.get("verified") is not True or not account.get("identity"):
            await _emit(progress, platform, "check", "failed", "account_unverified")
            return _result(platform, PUBLICATION_BLOCKED, "account_unverified")
        wanted_identity = expected.get("expected_account_identity") or (
            expected.get("account", {}).get("identity") if isinstance(expected.get("account"), dict) else None
        )
        if wanted_identity and wanted_identity != account["identity"]:
            await _emit(progress, platform, "check", "failed", "account_mismatch")
            return _result(platform, PUBLICATION_BLOCKED, "account_mismatch")

        candidate, error = await _locate_current_editor(page, platform)
        if error:
            await _emit(progress, platform, "check", "failed", error)
            return _result(platform, PUBLICATION_BLOCKED, error)
        if page.is_closed() or getattr(page, "url", "") not in page_urls:
            await _emit(progress, platform, "check", "failed", "page_changed")
            return _result(platform, PUBLICATION_BLOCKED, "page_changed")
        if not await candidate.control.is_visible() or not await _button_enabled(candidate.control, platform):
            await _emit(progress, platform, "check", "failed", "final_control_changed")
            return _result(platform, PUBLICATION_BLOCKED, "final_control_changed")
    except DraftInterrupted as exc:
        code = "risk_detected" if getattr(exc, "status", None) == "risk_paused" else "account_unverified"
        await _emit(progress, platform, "check", "failed", code)
        return _result(platform, PUBLICATION_BLOCKED, code)
    except Exception:
        await _emit(progress, platform, "check", "failed", "preflight_error")
        return _result(platform, PUBLICATION_BLOCKED, "preflight_error")

    await _emit(progress, platform, "check", "completed", "ready")
    restore = None
    if before_click is not None:
        try:
            release_result = before_click(page, platform)
            if inspect.isawaitable(release_result):
                release_result = await release_result
            if not callable(release_result):
                await _emit(progress, platform, "check", "failed", "guard_restore_callback_required")
                return _result(platform, PUBLICATION_BLOCKED, "guard_restore_callback_required")
            restore = release_result
        except Exception:
            await _emit(progress, platform, "check", "failed", "guard_release_failed")
            return _result(platform, PUBLICATION_BLOCKED, "guard_release_failed")

    before_url = getattr(page, "url", "")
    observation = _Observation(candidate.frame, platform)
    if platform == 'xiaohongshu':
        observation.receipt_scope = _xhs_receipt_scope(page, expected, account, candidate.frame)
    try:
        handlers = _bind_observer(page, observation)
    except Exception:
        restore_ok = await _call_restore(restore)
        if before_click is not None:
            await _emit(progress, platform, "guard", "completed" if restore_ok else "failed",
                        "restored" if restore_ok else "restore_failed")
        await _emit(progress, platform, "check", "failed", "observer_unavailable")
        return _result(platform, PUBLICATION_BLOCKED,
                       "observer_unavailable" if restore_ok else "guard_restore_failed")
    clicked = False
    restore_ok = True
    outcome_code = None
    try:
        if page.is_closed() or getattr(page, "url", "") != before_url:
            outcome_code = "page_changed_before_click"
        elif not await candidate.control.is_visible() or not await _button_enabled(candidate.control, platform):
            outcome_code = "final_control_changed"
        else:
            observation.armed = True
            clicked = True
            await _emit(progress, platform, "submit", "running", "single_click")
            try:
                await candidate.control.click(timeout=8000)
                if platform == 'kuaishou' and not observation.requests:
                    # The creator's first button can open its native confirmation
                    # dialog. Only its exact confirmation button is the final
                    # action; the initial button is never clicked again.
                    modal=candidate.frame.locator('div.ant-modal-confirm-centered:visible')
                    try:
                        await modal.first.wait_for(state='visible',timeout=1200)
                    except Exception:
                        pass
                    if await modal.count()==1 and await modal.first.is_visible():
                        confirms=modal.first.get_by_role('button',name='确认发布',exact=True)
                        if await confirms.count()!=1 or not await confirms.first.is_visible() or not await confirms.first.is_enabled():
                            raise RuntimeError('Unknown publication confirmation control')
                        await confirms.first.click(timeout=8000)
            except Exception:
                # click() can fail after dispatch; never call it again.
                await _emit(progress, platform, "submit", "unknown", "click_outcome_unknown")
            else:
                await _emit(progress, platform, "submit", "completed", "click_dispatched")
            finally:
                # Restore the draft guard as soon as the one UI click returns;
                # keep only the read-only observer installed while waiting.
                restore_ok = await _call_restore(restore)
                restore = None
                if before_click is not None:
                    await _emit(progress, platform, "guard", "completed" if restore_ok else "failed",
                                "restored" if restore_ok else "restore_failed")
    except Exception:
        # A failed action check or click is never retried. If click dispatch was
        # already attempted, the result remains an attempted, unverified one.
        outcome_code = outcome_code or ("click_outcome_unknown" if clicked else "pre_click_error")
        if clicked:
            await _emit(progress, platform, "submit", "unknown", "click_outcome_unknown")
    finally:
        if restore is not None:
            restore_ok = await _call_restore(restore)
            if before_click is not None:
                await _emit(progress, platform, "guard", "completed" if restore_ok else "failed",
                            "restored" if restore_ok else "restore_failed")

    counts = {
        "publish_requests": len(observation.requests),
        "publish_responses": len(observation.responses),
    }
    if not clicked:
        _remove_observer(page, handlers)
        return _result(platform, PUBLICATION_BLOCKED, outcome_code or "not_clicked", counts=counts)
    if not restore_ok:
        _remove_observer(page, handlers)
        await _emit(progress, platform, "receipt", "unknown", "guard_restore_failed")
        return _result(platform, PUBLISHED_UNVERIFIED, "guard_restore_failed", clicked=True, counts=counts)

    await _emit(progress, platform, "receipt", "running", "observing")
    try:
        timeout = float(receipt_timeout)
        if not math.isfinite(timeout):
            timeout = 0
        timeout = min(max(timeout, 0), 30)
    except (TypeError, ValueError):
        timeout = 0
    try:
        evidence = await _wait_for_receipt(page, observation, before_url, timeout)
    except Exception:
        evidence = None
    finally:
        _remove_observer(page, handlers)
        if observation.tasks:
            await asyncio.gather(*tuple(observation.tasks), return_exceptions=True)
    counts = {
        "publish_requests": len(observation.requests),
        "publish_responses": len(observation.responses),
    }
    if evidence:
        await _emit(progress, platform, "receipt", "completed", evidence["kind"])
        return _result(platform, PUBLISHED_SUBMISSION_VERIFIED, "submission_accepted",
                       clicked=True, evidence=evidence, counts=counts)
    await _emit(progress, platform, "receipt", "unknown", "no_verified_receipt")
    return _result(platform, PUBLISHED_UNVERIFIED, "no_verified_receipt", clicked=True, counts=counts)
