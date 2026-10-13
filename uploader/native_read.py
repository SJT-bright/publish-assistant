"""One bounded read in the native top-frame default execution context.

Callers supply fixed read-only expressions and retain their platform/session
guards. No binding, observer, navigation, business call or Emulation is added.
"""
from __future__ import annotations

import asyncio
import math
from urllib.parse import urlsplit


class NativeReadError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _origin(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in ('http', 'https') and parsed.netloc and
                not parsed.username and not parsed.password):
            return parsed.scheme + '://' + parsed.netloc
    except (TypeError, ValueError):
        pass
    return None


async def canonical_root_evaluate(page, expression, *, timeout=5.0):
    """Return native Runtime.evaluate's value, or raise a fixed safe reason.

    Each call owns and detaches one CDP session. Native auxData must identify
    exactly one root-frame default context. Its incarnation, root frame, URL
    and calling page/main-frame objects must remain unchanged through the read.
    A reader's legitimate null result is returned as None without any fallback.
    """
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or
            not 0 < timeout <= 5):
        raise NativeReadError('native_read_timeout_invalid')
    if not isinstance(expression, str) or not expression.strip():
        raise NativeReadError('native_read_expression_invalid')
    try:
        frame, url = page.main_frame, page.url
        origin = _origin(url)
        valid_page = origin and frame is not None and frame.url == url
    except Exception:
        valid_page = False
    if not valid_page:
        raise NativeReadError('native_read_page_scope_invalid')
    client, contexts, root_revision = None, {}, 0

    def created(event):
        context = event.get('context', {})
        if type(context.get('id')) is int:
            # Keep each event's distinct object: even an identical ID reused
            # after destruction cannot inherit the preceding incarnation.
            contexts[context['id']] = {'origin': context.get('origin'),
                'aux': dict(context.get('auxData') or {})}

    def destroyed(event):
        contexts.pop(event.get('executionContextId'), None)

    def cleared(_event):
        contexts.clear()

    def navigated(event):
        nonlocal root_revision
        native_frame = event.get('frame', {})
        if native_frame.get('id') and not native_frame.get('parentId'):
            root_revision += 1

    handlers = {'Runtime.executionContextCreated': created,
        'Runtime.executionContextDestroyed': destroyed,
        'Runtime.executionContextsCleared': cleared,
        'Page.frameNavigated': navigated}

    def page_current():
        return page.main_frame is frame and page.url == url and frame.url == url

    def canonical(ident, captured, root):
        candidates = [key for key, value in contexts.items()
            if value['aux'].get('isDefault') is True and value['aux'].get('frameId') == root]
        return (page_current() and candidates == [ident] and contexts.get(ident) is captured and
                captured['origin'] == origin)

    try:
        async with asyncio.timeout(timeout):
            client = await page.context.new_cdp_session(page)
            for name, handler in handlers.items():
                client.on(name, handler)
            await client.send('Page.enable')
            before = (await client.send('Page.getFrameTree')).get('frameTree', {}).get('frame', {})
            root = before.get('id')
            if (not isinstance(root, str) or not root or before.get('parentId') or
                    before.get('url') != url or not page_current()):
                raise NativeReadError('native_read_root_scope_invalid')
            await client.send('Runtime.enable')
            candidates = [key for key, value in contexts.items()
                if value['aux'].get('isDefault') is True and value['aux'].get('frameId') == root]
            if len(candidates) != 1:
                raise NativeReadError('native_read_default_context_not_unique')
            ident = candidates[0]
            captured, revision = contexts[ident], root_revision
            if not canonical(ident, captured, root):
                raise NativeReadError('native_read_context_scope_invalid')
            result = await client.send('Runtime.evaluate', {'expression': expression,
                'contextId': ident, 'returnByValue': True, 'awaitPromise': True,
                'timeout': int(timeout * 1000)})
            if result.get('exceptionDetails'):
                raise NativeReadError('native_read_evaluation_failed')
            if root_revision != revision or not canonical(ident, captured, root):
                raise NativeReadError('native_read_context_changed')
            after = (await client.send('Page.getFrameTree')).get('frameTree', {}).get('frame', {})
            if (after.get('id') != root or after.get('parentId') or after.get('url') != url or
                    root_revision != revision or not canonical(ident, captured, root)):
                raise NativeReadError('native_read_root_changed')
            return result.get('result', {}).get('value')
    except NativeReadError:
        raise
    except TimeoutError:
        raise NativeReadError('native_read_timeout') from None
    except asyncio.CancelledError:
        raise
    except Exception:
        raise NativeReadError('native_read_transport_failed') from None
    finally:
        if client is not None:
            for name, handler in handlers.items():
                try:
                    client.remove_listener(name, handler)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(client.detach(), timeout=1)
            except Exception:
                pass
