"""One-shot, observed submission of an already prepared creator-page video.

This module never uploads files, edits fields, chooses declarations, schedules,
or retries a submit. It only acts on a verified current composer and records
whether this single UI action produced a documented submission receipt.
"""
from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from uploader.draft_only import is_publish_request
from uploader.draft_session import DraftInterrupted, inspect_session


PLATFORMS = frozenset({"douyin", "xiaohongshu", "tencent", "bilibili", "kuaishou"})
PUBLISHED_SUBMISSION_VERIFIED = "published_submission_verified"
PUBLISHED_UNVERIFIED = "published_unverified"
PUBLICATION_BLOCKED = "publication_blocked"

# These are the documented public API response contracts, not guessed schemas
# for private creator-page endpoints. A creator page may use a different route;
# that response remains unverified unless a platform success page is explicit.
_DOCUMENTED_API_HOSTS = {
    ("open.douyin.com", "/video/create"): "douyin",
    ("open.kuaishou.com", "/openapi/photo/publish"): "kuaishou",
    ("member.bilibili.com", "/arcopen/fn/archive/add-by-utoken"): "bilibili",
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


async def _button_enabled(button, platform):
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
    if len(found) != 1:
        return None, "final_control_not_unique"
    if not await _button_enabled(found[0], platform):
        return None, "final_control_disabled"
    return found[0], None


async def _locate_current_editor(page, platform):
    frames = [frame for frame in _page_frames(page) if _official_editor_route(getattr(frame, "url", ""), platform)]
    if len(frames) != 1:
        return None, "editor_not_unique_or_wrong_page"

    frame = frames[0]
    if platform == "bilibili":
        observed = await _bili_upload_ready(page)
        if observed is None or observed is not frame:
            return None, "video_not_ready"
        try:
            title = frame.locator(".video-title .video-title-content input")
            description = frame.locator(".desc-container .ql-editor[contenteditable='true']")
            if (await title.count() != 1 or not await title.first.is_visible()
                    or await description.count() != 1 or not await description.first.is_visible()):
                return None, "editor_not_ready"
        except Exception:
            return None, "editor_not_ready"
    elif platform == "tencent":
        if not await _tencent_upload_ready(page):
            return None, "video_not_ready"
        try:
            form = frame.locator("div.form-btns")
            if await form.count() != 1 or not await form.first.is_visible():
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
        host = parsed.hostname
        path = parsed.path.rstrip("/") or "/"
        return _DOCUMENTED_API_HOSTS.get((host, path))
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
    elif platform == "kuaishou":
        info = body.get("video_info")
        if (type(body.get("result")) is int and body.get("result") == 1
                and isinstance(info, dict) and isinstance(info.get("photo_id"), str)
                and info["photo_id"]):
            return info["photo_id"]
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
        except Exception:
            return
        contract_platform = _documented_contract_url(request.url)
        observation.requests[id(request)] = contract_platform if contract_platform == observation.platform else None
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
    return (
        before.geturl() != after.geturl()
        and after.hostname == "creator.xiaohongshu.com"
        and after.path == _XHS_SUCCESS_PATH
        and bool(after.query)
    )


async def _wait_for_receipt(page, observation, before_url, timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if observation.tasks:
            await asyncio.gather(*tuple(observation.tasks), return_exceptions=True)
            observation.tasks.clear()
        if _xhs_success_page(before_url, getattr(page, "url", "")):
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
    if _xhs_success_page(before_url, getattr(page, "url", "")):
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
