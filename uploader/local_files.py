"""Upload disk files to a trusted, same-machine embedded Chromium target.

Playwright's connect_over_cdp path treats Electron as a remote browser and
buffers small files; it rejects files over 50 MiB.  The app marks its exact,
locally owned page with ``_sau_local_files = True`` after resolving its target.
Only that explicit trust boundary uses CDP disk paths.  Other pages keep the
normal Playwright file upload behavior.
"""
import os
import uuid
from pathlib import Path


def _paths(files):
    values = [files] if isinstance(files, (str, os.PathLike)) else files
    if not isinstance(values, (list, tuple)):
        raise ValueError('请选择本机文件，不能使用文件缓冲区。')
    result = []
    for value in values:
        if not isinstance(value, (str, os.PathLike)):
            raise ValueError('请选择本机文件，不能使用文件缓冲区。')
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError('要导入的文件不存在或无法读取。') from error
        if not path.is_file() or not os.access(path, os.R_OK):
            raise ValueError('要导入的文件必须是可读取的本机文件。')
        result.append(str(path))
    return result


async def _frame_session(page, frame):
    """Use an OOPIF's own target, or its closest ancestor target."""
    current = frame
    while current is not None and current != page.main_frame:
        try:
            return await page.context.new_cdp_session(current)
        except Exception as error:
            # A same-process iframe shares its parent's CDP session.  Other
            # errors (detached frames, closed targets) must stop the upload.
            if 'does not have a separate CDP session' not in str(error):
                raise
        current = current.parent_frame
    return await page.context.new_cdp_session(page)


def _marked_nodes(node, attribute):
    """Find the one marked element, including same-process frame documents."""
    result = []
    pending = [node]
    while pending:
        current = pending.pop()
        attributes = current.get('attributes', [])
        if any(attributes[i] == attribute and attributes[i + 1] == '1'
               for i in range(0, len(attributes) - 1, 2)):
            result.append(current)
        pending.extend(current.get('children', []))
        pending.extend(current.get('shadowRoots', []))
        for name in ('contentDocument', 'templateContent'):
            if current.get(name):
                pending.append(current[name])
    return result


async def set_local_files(page, locator, file_or_list):
    """Set files on exactly one ``input[type=file]``, without buffering video.

    ``locator`` may also be a concrete ElementHandle, such as chooser.element.
    ``page`` must be the same page that owns it.  Multiple matches,
    directory paths, detached inputs and cross-page locators fail before any
    files are assigned.  Hidden native file inputs are intentionally allowed.
    Empty lists clear an input; multiple paths require its ``multiple`` flag.
    """
    page._sau_local_file_phase = '检查本机文件'
    paths = _paths(file_or_list)
    owned_handle = False
    if callable(getattr(locator, 'count', None)):
        page._sau_local_file_phase = '定位唯一上传控件'
        if await locator.count() != 1:
            raise ValueError('当前页面没有唯一的文件上传控件，已停止以免导入错误位置。')
        handle = await locator.element_handle()
        owned_handle = True
    elif callable(getattr(locator, 'owner_frame', None)) and callable(getattr(locator, 'evaluate', None)):
        handle = locator
    else:
        raise ValueError('请提供当前平台的唯一文件上传控件。')
    if handle is None:
        raise ValueError('文件上传控件已失效，请等待页面加载完成。')
    session = None
    attribute = None
    try:
        page._sau_local_file_phase = '确认上传控件归属'
        frame = await handle.owner_frame()
        if frame is None or frame.page != page:
            raise ValueError('文件上传控件不属于当前平台页面。')
        page._sau_local_file_phase = '检查原生文件输入框'
        info = await handle.evaluate("""element => ({
            connected: element.isConnected,
            file: element.tagName === 'INPUT' && element.type === 'file',
            multiple: element.multiple,
            directory: element.webkitdirectory
        })""")
        if not info['connected'] or not info['file'] or info['directory']:
            raise ValueError('当前控件不是可用的文件上传输入框。')
        if len(paths) > 1 and not info['multiple']:
            raise ValueError('当前上传控件只支持单个文件。')
        # Chromium's raw DOM.setFileInputFiles([]) can leave its old FileList
        # intact.  Playwright's empty-file clear operation has no large-file
        # transfer and supplies the proper clear semantics.
        if not paths or getattr(page, '_sau_local_files', False) is not True:
            page._sau_local_file_phase = '导入原生文件'
            await handle.set_input_files(paths)
            return

        # A temporary marker ties CDP to the concrete ElementHandle, rather
        # than independently guessing a CSS selector or choosing .first.
        attribute = 'data-sau-file-' + uuid.uuid4().hex
        page._sau_local_file_phase = '标记准确上传控件'
        await handle.evaluate('(element, attribute) => element.setAttribute(attribute, "1")', attribute)
        page._sau_local_file_phase = '连接本机文件通道'
        session = await _frame_session(page, frame)
        page._sau_local_file_phase = '读取上传控件原生节点'
        document = await session.send('DOM.getDocument', {'depth': -1, 'pierce': True})
        nodes = _marked_nodes(document['root'], attribute)
        if len(nodes) != 1 or not nodes[0].get('nodeId') or nodes[0].get('nodeName') != 'INPUT':
            raise ValueError('无法精确定位当前文件上传控件，文件尚未导入。')
        page._sau_local_file_phase = '交给平台读取文件'
        await session.send('DOM.setFileInputFiles', {'nodeId': nodes[0]['nodeId'], 'files': paths})
        page._sau_local_file_phase = '文件已交给平台'
    finally:
        if attribute is not None:
            try:
                await handle.evaluate('(element, attribute) => element.removeAttribute(attribute)', attribute)
            except Exception:
                pass  # The page may remove the input as soon as upload starts.
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass
        if owned_handle:
            await handle.dispose()
