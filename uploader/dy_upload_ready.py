"""Wait for the current Douyin editor and its exact selected video.

Upload completion comes from the observed native SDK attempt and the current
component state. The editor's new preview no longer uses the old long-card
markup, and its progress may remain at 99 after the SDK has completed.
"""
import asyncio
import json
import math
import time
from urllib.parse import urlsplit

from patchright.async_api import Error

from uploader.dy_editor import normalize_douyin_editor_text


def _navigation_read_error(exc):
    return isinstance(exc, Error) and any(text in str(exc) for text in (
        'Execution context was destroyed',
        "Cannot read properties of undefined (reading 'delegate')",
        'Frame was detached',
    ))


async def wait_dy_empty_video_input(page, timeout=30):
    """Retry only pre-selection reads; never retry a File or SDK upload."""
    from uploader.draft_only import assert_draft_safe
    from uploader.draft_evidence import evaluate_main

    deadline = time.monotonic() + timeout
    stable = 0
    while time.monotonic() < deadline:
        try:
            await assert_draft_safe(page, '抖音')
            current = urlsplit(page.url)
            if (current.scheme != 'https' or current.netloc != 'creator.douyin.com' or
                    current.path != '/creator-micro/content/post/video'):
                stable = 0
            elif await evaluate_main(page.main_frame, 'document.readyState') == 'complete':
                inputs = await page.main_frame.locator(
                    'input[type="file"][accept*="video"],input[type="file"][accept*=".mp4"],'
                    'input.upload-btn-input:not([accept*="image"]),input.upload-input:not([accept*="image"])'
                ).all()
                if len(inputs) > 1:
                    raise RuntimeError('抖音视频上传控件不唯一，已保留现场。')
                if inputs:
                    for selector in ('input[placeholder*="填写作品标题"]',
                                     'div.zone-container[contenteditable="true"]'):
                        for field in await page.main_frame.locator(selector).all():
                            value = await field.input_value() if selector.startswith('input') else await field.inner_text()
                            if normalize_douyin_editor_text(value).strip():
                                raise RuntimeError('抖音新编辑页恢复了已有内容，已在导入视频前停止并保留现场。')
                    stable += 1
                    if stable >= 2:
                        return inputs[0]
                else:
                    stable = 0
            else:
                stable = 0
        except Error as exc:
            if not _navigation_read_error(exc):
                raise
            stable = 0
        await asyncio.sleep(.25)
    raise RuntimeError('抖音编辑页尚未稳定，已在导入视频前停止；未重复上传。')


DY_VIDEO_UPLOAD_STATE = r"""scope => {
 const root=window.__sauDYUploadObserver, active=root?.active;
 const scoped=window===top&&location.origin==='https://creator.douyin.com'&&
   location.pathname==='/creator-micro/content/post/video'&&root?.api_version===1&&
   root.scope?.observer_nonce===scope.observer_nonce&&root.scope?.job_id===scope.job_id&&
   root.scope?.receipt_nonce===scope.receipt_nonce&&active?.observer_nonce===scope.observer_nonce&&
   active.invocation_id===scope.invocation_id&&root.current?.instance===active.component;
 if(!scoped)return {scoped:false,ready:false,invalid:false,percent:null};
 const state=active.component?.state;
 const fileMatch=!!active.file&&(state?.file===active.file||state?.file?.rawFile===active.file);
 const percent=typeof state?.uploadPercent==='number'&&Number.isFinite(state.uploadPercent)&&
   state.uploadPercent>=0&&state.uploadPercent<=100?state.uploadPercent:null;
 const invalid=active.invalid===true||active.sdk_rejected===true;
 const ready=!invalid&&active.sdk_fulfilled===true&&active.result?.file===active.file&&
   state?.uploadStatus===2&&fileMatch&&typeof active.result?.vid==='string';
 return {scoped:true,ready,invalid,percent,vid:ready?active.result.vid:null};
}"""


async def wait_dy_video_uploaded(page, timeout=900, poll_interval=.25):
    """Observe completion of this task's File; do not click or select again.

    The SDK receipt binds the selected File's SHA-256 to a video ID. The same
    current React component must retain that native File in its completed
    state. A video preview, editable fields, or a 100 percent label alone is
    insufficient. In particular 99 percent is valid once both checks pass.
    """
    from uploader.draft_only import assert_draft_safe, DraftInterrupted
    from uploader.draft_progress import operation

    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0 or
            type(poll_interval) not in (int, float) or not math.isfinite(poll_interval) or poll_interval <= 0):
        raise ValueError('抖音上传等待时间必须为正数。')
    session = getattr(page, '_sau_draft_evidence', None)
    observer = getattr(session, 'dy_observer', None)
    if session is None or observer is None or getattr(observer, 'session', None) is not session:
        raise DraftInterrupted('抖音缺少本次视频完成观察器，已保留素材且不自动重新上传。')
    deadline = time.monotonic() + timeout
    last_percent = None
    try:
        async with asyncio.timeout(timeout):
            while time.monotonic() < deadline:
                await assert_draft_safe(page, '抖音')
                if (getattr(page, '_sau_draft_evidence', None) is not session or session.closed or
                        observer.closed):
                    raise DraftInterrupted('抖音上传观察已失效，已保留素材且不自动重新上传。')
                scope = {**observer.scope, 'invocation_id': observer.latest_invocation}
                try:
                    state = await observer.transport.evaluate('(' + DY_VIDEO_UPLOAD_STATE + ')(' +
                                                              json.dumps(scope) + ')')
                except Error as exc:
                    if not _navigation_read_error(exc):
                        raise
                    state = None
                if isinstance(state, dict) and state.get('scoped') is True:
                    if state.get('invalid') is True:
                        raise DraftInterrupted('抖音本次视频上传失败或视频已被替换，已保留现场；不会自动重传。')
                    percent = state.get('percent')
                    if type(percent) in (int, float) and math.isfinite(percent) and 0 <= percent <= 100 and percent != last_percent:
                        operation('upload', 'transfer', percent=percent)
                        last_percent = percent
                    if state.get('ready') is True:
                        # Read links after the asynchronous state read so a
                        # replacement/invalidation cannot reuse an old receipt.
                        links = observer.upload_links()
                        if any(link.get('kind') == 'video' and link.get('complete') is True and
                               link.get('sha256') == observer.video_sha256 and
                               state.get('vid') in link.get('ids', []) for link in links):
                            return True
                await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    except TimeoutError:
        pass
    raise DraftInterrupted('抖音尚未取得本次视频上传完成回执，已保留素材且不自动重新上传。')


async def dismiss_dy_video_preview_tip(page):
    """Close only the official informational video-preview tutorial bubble."""
    from uploader.draft_only import assert_draft_safe

    await assert_draft_safe(page, '抖音')
    titles = page.main_frame.get_by_text('视频预览功能', exact=True)
    visible = [title for title in await titles.all() if await title.is_visible()]
    if not visible:
        return False
    if len(visible) != 1:
        raise RuntimeError('抖音视频预览提示不唯一，已保留现场。')
    container = visible[0]
    for _ in range(5):
        container = container.locator('..')
        # Do not climb into an editor/dialog containing actual task fields.
        if await container.locator('input,textarea,[contenteditable="true"]').count():
            break
        buttons = container.get_by_role('button', name='我知道了', exact=True)
        candidates = [button for button in await buttons.all() if await button.is_visible()]
        if not candidates:
            continue
        if len(candidates) != 1:
            raise RuntimeError('抖音视频预览提示按钮不唯一，已保留现场。')
        await candidates[0].click(timeout=8000)
        return True
    raise RuntimeError('抖音视频预览提示结构已变化，已保留现场。')
