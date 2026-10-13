"""Wait for an empty, stable normal Douyin editor before selecting a File."""
import asyncio
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
