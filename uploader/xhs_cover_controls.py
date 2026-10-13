"""Strict controls for the already mounted XHS Mojito video cover editor.

Public source project-publish-vue.474cd180.js chars 681028, 690175,
705727 and project-publish-components.c6f26def.js chars 193793, 258384,
324300. Opening/editing a cover never saves or publishes the note.
"""
import asyncio
import re

from uploader.local_files import set_local_files


MODAL = '.main-cover-editor-modal.cover-modal'
IMAGE_INPUT = ('.cover-choose-container .upload-slot > label.upload-btn > '
               'input.upload-input[type="file"][accept*="image"]')
COMPLETE = '.editor-panel .bottom .bottom-content > button.mojito-button'


async def _guard(page, guard):
    if guard is None:
        from uploader.draft_only import assert_draft_safe
        await assert_draft_safe(page, '小红书')
    else:
        await guard(page)


async def _visible_unique(locator, message, *, required=True):
    visible = []
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            visible.append(candidate)
    if len(visible) > 1 or required and len(visible) != 1:
        raise ValueError(message)
    return visible[0] if visible else None


async def _modal(page, *, required=True):
    # The main video modal is distinct from PK and image editors. A reused
    # physical node matching both classes is one locator, never two matches.
    modal = await _visible_unique(page.main_frame.locator(MODAL),
        '小红书没有唯一的主视频封面编辑器。', required=required)
    if modal is not None:
        heading = modal.locator(':scope > .d-modal-header > h6')
        if await heading.count() != 1 or (await heading.inner_text()).strip() != '设置封面':
            raise ValueError('小红书封面弹窗标题或结构已变化。')
    return modal


async def open_xhs_cover_editor(page, *, timeout=15000, guard=None):
    """Reuse one visible main modal or hover the main card and click its entry."""
    await _guard(page, guard)
    modal = await _modal(page, required=False)
    if modal is not None:
        return modal
    card = await _visible_unique(page.main_frame.locator(
        '.cover-plugin-preview > .cover > .default:not(.operator)'),
        '小红书没有唯一的主封面卡片，已停止以免操作其他封面。')
    # The card itself has no business click handler. The actual edit action is
    # revealed by CSS hover and has its own stop-propagation click handler.
    await card.hover(timeout=timeout)
    entry = page.main_frame.locator('.cover-plugin-preview .cover-edit-entry').filter(
        has_text=re.compile(r'^\s*编辑封面\s*$'))
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while True:
        candidate = await _visible_unique(entry, '小红书出现多个编辑封面入口。', required=False)
        if candidate is not None:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise ValueError('小红书主封面的编辑入口没有出现。')
        await asyncio.sleep(.05)
    await _guard(page, guard)
    await candidate.click(timeout=timeout)
    while True:
        modal = await _modal(page, required=False)
        if modal is not None:
            return modal
        if asyncio.get_running_loop().time() >= deadline:
            raise ValueError('小红书主封面编辑器没有出现。')
        await asyncio.sleep(.05)


async def _cover_ready(page):
    session = getattr(page, '_sau_draft_evidence', None)
    observer = getattr(session, 'xhs_observer', None)
    if (session is None or getattr(session, 'closed', True) or
            observer is None or not await observer.ensure_cover_ready(timeout=5)):
        raise ValueError('小红书封面来源观察器尚未就绪，已在导入图片前停止。')


async def _selection_ready(page, timeout):
    session = getattr(page, '_sau_draft_evidence', None)
    observer = getattr(session, 'xhs_observer', None)
    if (observer is None or getattr(session, 'closed', True) or
            not await observer.ensure_cover_selection_ready(timeout=min(30, timeout / 1000))):
        raise ValueError('小红书尚未确认本次原图已成为当前封面，已停止完成操作。')


async def import_xhs_cover_file(page, file_path, *, ratio=None, timeout=15000,
                               guard=None, file_setter=None):
    """Import through the one native input; optional normal Crop ratio action.

Video-cover Mojito offers 3:4, 4:3 and 1:1. 9:16/16:9 belong to its image
editor and must not be invented for a video note. No complete/save here.
"""
    if ratio not in (None, '3:4', '4:3', '1:1'):
        raise ValueError('小红书视频封面当前支持 3:4、4:3 或 1:1。')
    modal = await open_xhs_cover_editor(page, timeout=timeout, guard=guard)
    await _cover_ready(page)
    native_input = modal.locator(IMAGE_INPUT)
    if await native_input.count() != 1 or not await native_input.is_enabled():
        raise ValueError('小红书封面编辑器没有唯一可用的原生图片上传控件。')
    await _guard(page, guard)
    await (file_setter or set_local_files)(page, native_input, file_path)
    await _selection_ready(page, timeout)
    if ratio is not None:
        crop = await _visible_unique(modal.locator('.category > .items > .item').filter(
            has_text=re.compile(r'^\s*裁剪\s*$')), '小红书没有唯一的封面裁剪选项。')
        await _guard(page, guard)
        await crop.click(timeout=timeout)
        option = modal.locator('.crop-panel .ratio-list > button.ratio-option').filter(
            has_text=re.compile(r'^\s*' + re.escape(ratio) + r'\s*$'))
        await option.wait_for(state='visible', timeout=timeout)
        if await option.count() != 1 or not await option.is_enabled():
            raise ValueError('小红书封面比例选项不唯一或不可用。')
        await _guard(page, guard)
        await option.click(timeout=timeout)
    return modal


async def finish_xhs_cover_editor(page, *, timeout=20000, guard=None):
    """Use only the exact Complete button and wait for this physical modal exit."""
    modal = await _modal(page)
    await _selection_ready(page, timeout)
    button = modal.locator(COMPLETE).filter(has_text=re.compile(r'^\s*完成\s*$'))
    if await button.count() != 1:
        raise ValueError('小红书没有唯一的封面完成按钮。')
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while not await button.is_enabled():
        if asyncio.get_running_loop().time() >= deadline:
            raise ValueError('小红书封面仍在处理，完成按钮不可用。')
        await asyncio.sleep(.05)
    await _guard(page, guard)
    await button.click(timeout=timeout)
    await modal.wait_for(state='hidden', timeout=timeout)
    return True
