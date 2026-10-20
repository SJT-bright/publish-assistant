"""Prepare one native Bilibili import while observing its actual SDK task.

This module does not select videos, save drafts or submit publications. The
caller first obtains bili_empty_video_control and selects exactly one file.
All writes here stay on that native File/task; the final join still requires
the completed SDK chain rather than a mounted editor or a visible preview.
"""
import asyncio
import time

from uploader import bili_controls as controls
from uploader.draft_progress import step, part, emit
from uploader.draft_session import DraftInterrupted
from uploader.upload_pipeline import run_upload_pipeline


async def _bind_import(page, timeout):
    permission=getattr(page,'_sau_bili_native_import',None)
    if permission is None:
        raise DraftInterrupted('B站本次原生导入许可已失效，未开始并行填写。','adapter_mismatch')
    frame=await controls._frame(page)
    until=time.monotonic()+min(timeout,30)
    while time.monotonic()<until:
        state=await controls._state(frame)
        if state['task_count']>1:
            raise DraftInterrupted('B站出现多个视频任务，未开始并行填写。','adapter_mismatch')
        if state['task_count']==1:
            return frame,await controls._tag_binding(page,frame,permission,allow_uploading=True)
        await asyncio.sleep(.1)
    raise DraftInterrupted('B站本次视频尚未进入原生上传任务，已停止。','result_unknown')


async def _content_matches(page,frame,binding,title,description,tags):
    await controls._tag_context(page,frame,binding)
    title_control=await controls._unique_visible(frame,controls.TITLE)
    selected=await controls._tags(frame)
    return (await title_control.input_value()==title and
            (await controls._description_text(frame)).replace('\r\n','\n')==description.replace('\r\n','\n') and
            not selected['loading'] and set(selected['tags'])==set(tags))


async def bili_prepare_upload(page, title, description, tags, *, cover=None, timeout=600):
    """Join upload and prepared fields for the caller's eventual final action.

    Bilibili mounts its normal cover controls during upload. When the one slot
    is enabled, title/description/tags and its synchronized cover are prepared
    before the transfer ends. A disabled cover slot is handled after the join.
    Completion may add platform recommendations: only this same task's content
    is reconciled, then read back again. No file selection is repeated.
    """
    permission=getattr(page,'_sau_bili_native_import',None)
    frame,binding=None,None
    committed_cover=None
    deferred_cover=False
    async def wait_upload():
        with step('upload'):
            await controls.bili_wait_uploaded(page,timeout,upload_binding=binding)
    async def prepare_content():
        nonlocal committed_cover,deferred_cover
        with step('content'):
            await controls.bili_fill_content(page,title,description,tags,upload_binding=binding)
        if not cover:
            emit('cover','skipped')
            return
        slot=await controls._unique_visible(frame,controls.COVER_SLOT)
        deferred_cover=await slot.evaluate("el=>el.classList.contains('cover-slot-disabled')||el.classList.contains('cover-slot-offline-action')")
        if not deferred_cover:
            with step('cover'):
                await controls.bili_set_cover(page,cover,upload_binding=binding)
            committed_cover=await controls._committed_cover(frame)
    try:
        frame,binding=await _bind_import(page,timeout)
        await run_upload_pipeline(page,'B站',wait_upload,prepare_content)
        # Final SDK completion can resolve automatic recommendation callbacks.
        # Fix only content while the imported File and task identity stay bound.
        if not await _content_matches(page,frame,binding,title,description,tags):
            with step('content'):
                await controls.bili_fill_content(page,title,description,tags,upload_binding=binding)
        with part('content','readback'):
            if not await _content_matches(page,frame,binding,title,description,tags):
                raise DraftInterrupted('B站上传完成后内容回读不一致，未提交。','adapter_mismatch')
        if deferred_cover:
            with step('cover'):
                await controls.bili_set_cover(page,cover,upload_binding=binding)
        elif committed_cover is not None and await controls._committed_cover(frame)!=committed_cover:
            raise DraftInterrupted('B站上传完成后封面发生变化，已保留现场，未提交。','adapter_mismatch')
        # Recheck after optional cover work. A later task replacement or SDK
        # regression must not pass simply because its earlier wait completed.
        uploaded=await controls._editor_object(frame,'uploaded')
        try:
            if not await controls._same_task(uploaded,binding):
                raise DraftInterrupted('B站最终完成任务与本次导入不一致，未提交。','adapter_mismatch')
        finally:
            await controls._dispose_editor(uploaded)
        if not await _content_matches(page,frame,binding,title,description,tags):
            raise DraftInterrupted('B站最终内容在封面处理后发生变化，已保留现场，未提交。','adapter_mismatch')
    finally:
        if binding is not None:
            await controls._dispose_editor(binding)
        if permission is not None and getattr(page,'_sau_bili_native_import',None) is permission:
            page._sau_bili_native_import=None
            await controls._dispose_editor(permission['handle'])
