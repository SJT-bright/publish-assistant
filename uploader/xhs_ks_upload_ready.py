"""Read-only upload completion for the current XHS/Kuaishou native selection.

An editor, a reusable preview, and a progress percentage cannot prove upload
completion. The existing native observers correlate the exact file hash and
current task with the normal platform completion response. UI state only adds
the platform processing gate; it never substitutes for that correlation.
"""
import asyncio
import copy
import math

from uploader.draft_session import DraftInterrupted


_CONTRACTS = {
    "xiaohongshu": {
        "label": "小红书",
        "observer": "xhs_observer",
        "ready": ('.cover-container > .preview-new',
                  'div.preview-new :text-is("上传成功")',
                  'div.preview-new :text-is("重新上传")',
                  'div.preview-new .stage:has-text("分辨率")',
                  ':text-is("视频上传成功")'),
        "busy": ('.cover-container > .uploading', '.cover-container > .transcoding',
                 ':text-is("上传中")', ':text-is("转码中")', ':text-is("处理中")'),
    },
    "kuaishou": {
        "label": "快手",
        "observer": "ks_observer",
        "ready": (':text-is("上传成功")', ':text-is("视频上传成功")',
                  ':text-is("重新上传")', 'video[src]:not([src=""])',
                  'video source[src]:not([src=""])'),
        "busy": (':text-is("上传中")', ':text-is("视频上传中")',
                 ':text-is("转码中")', ':text-is("处理中")'),
    },
}


async def _visible_upload_control(page, selectors):
    for frame in page.frames:
        for selector in selectors:
            for candidate in await frame.locator(selector).all():
                if (await candidate.is_visible() and not await candidate.evaluate(
                        'e=>!!e.closest("input,textarea,[contenteditable=true]")')):
                    return True
    return False


async def wait_xhs_ks_upload_complete(page, platform, *, timeout=900, poll_interval=.25):
    """Wait once, without UI mutation or re-import, and stop on a stale task.

Kuaishou's existing native proof requires ALL_UPLOAD status 3 and the same
fileId/token in allocation, finish and the current React composer. XHS's proof
requires the fulfilled normal SDK upload for the selected native file; its
completed preview additionally excludes uploading/transcoding components.
The bounded timeout covers observer draining as well as DOM reads.
"""
    from uploader.draft_only import assert_draft_safe

    contract = _CONTRACTS.get(platform)
    if contract is None:
        raise ValueError("不支持此平台的上传完成核验。")
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or type(poll_interval) not in (int, float) or not math.isfinite(poll_interval)
            or poll_interval <= 0):
        raise ValueError("上传完成核验必须使用有限的正数超时。")
    label = contract["label"]
    session = getattr(page, "_sau_draft_evidence", None)
    observer = getattr(session, contract["observer"], None)
    expected = getattr(session, "expected", {}) or {}
    video_sha = expected.get("video_sha256")
    if (session is None or observer is None or getattr(session, "closed", True)
            or getattr(session, "page", None) is not page
            or getattr(session, "platform", None) != platform
            or getattr(observer, "session", None) is not session
            or getattr(observer, "page", None) is not page or getattr(observer, "closed", True)
            or not isinstance(video_sha, str) or not video_sha
            or not callable(getattr(observer, "drain", None))
            or not callable(getattr(observer, "upload_links", None))):
        raise DraftInterrupted(f"{label}当前素材上传核验尚未准备好，已保留页面且不自动重新上传。")
    scope = copy.deepcopy({key: expected.get(key)
                           for key in ("job_id", "receipt_nonce", "account", "video_sha256")})
    account = copy.deepcopy(getattr(session, "account", None))
    last_percent = None

    def current():
        return (getattr(page, "_sau_draft_evidence", None) is session
                and not session.closed and not observer.closed
                and session.page is page and session.platform == platform
                and getattr(session, contract["observer"], None) is observer
                and observer.session is session and observer.page is page
                and all(session.expected.get(key) == value for key, value in scope.items())
                and getattr(session, "account", None) == account)

    try:
        loop=asyncio.get_running_loop()
        hard_deadline=loop.time()+max(timeout,min(timeout*3,2700))
        async with asyncio.timeout(timeout) as timer:
            while True:
                await assert_draft_safe(page, label)
                if not current():
                    raise DraftInterrupted(f"{label}任务或当前素材已变化，已停止且不自动重新上传。")
                # Draining only observes already running native uploads. It does
                # not invoke an SDK upload, content edit, or completion callback.
                await observer.drain()
                if platform == 'kuaishou' and callable(getattr(observer,'upload_progress',None)):
                    percent=await observer.upload_progress()
                    if percent is not None and percent!=last_percent:
                        from uploader.draft_progress import operation
                        operation('upload','transfer',percent=percent)
                        # Keep a progressing native transfer alive near the
                        # usual deadline, with a fixed upper bound. A stalled
                        # percentage never renews the wait or completes it.
                        if percent>(last_percent or 0):
                            grace=min(120,max(poll_interval*4,timeout*.25))
                            timer.reschedule(min(hard_deadline,max(timer.when(),loop.time()+grace)))
                        last_percent=percent
                links = [link for link in observer.upload_links()
                         if link.get("kind") == "video"]
                bound = (len(links) == 1 and links[0].get("complete") is True
                         and links[0].get("sha256") == video_sha
                         and bool(links[0].get("ids")))
                if (bound and await _visible_upload_control(page, contract["ready"])
                        and not await _visible_upload_control(page, contract["busy"])):
                    # A drain can yield while the user changes the task. Check
                    # identity again before releasing the final action gate.
                    if not current():
                        raise DraftInterrupted(f"{label}任务或当前素材已变化，已停止且不自动重新上传。")
                    return True
                await asyncio.sleep(poll_interval)
    except TimeoutError:
        raise DraftInterrupted(f"{label}本次视频上传或处理完成核验超时，已保留页面且不自动重新上传。") from None
