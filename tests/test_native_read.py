"""Native root context contracts and isolated iframe/CDP fixtures."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from patchright.async_api import async_playwright
from uploader.native_read import NativeReadError, canonical_root_evaluate


URL = 'https://channels.weixin.qq.com/platform/post/create'
ORIGIN = 'https://channels.weixin.qq.com'


class Client:
    def __init__(self, page):
        self.page, self.handlers, self.commands = page, {}, []
        self.detached, self.tree_reads, self.mode = False, 0, None
        self.entered = asyncio.Event()

    def on(self, name, callback):
        self.handlers[name] = callback

    def remove_listener(self, name, callback):
        if self.handlers.get(name) is callback:
            self.handlers.pop(name)

    def emit(self, name, value):
        if name in self.handlers:
            self.handlers[name](value)

    def created(self, ident, frame='root', default=True, origin=ORIGIN):
        self.emit('Runtime.executionContextCreated', {'context': {'id': ident, 'origin': origin,
            'auxData': {'frameId': frame, 'isDefault': default}}})

    async def send(self, name, args=None):
        self.commands.append((name, copy.deepcopy(args)))
        if name == 'Page.getFrameTree':
            self.tree_reads += 1
            frame = {'id': 'root', 'url': URL}
            if self.tree_reads == 2 and self.mode == 'stale-tree':
                frame['id'] = 'other-root'
            if self.tree_reads == 2 and self.mode == 'stale-url':
                frame['url'] = URL + '?changed'
            return {'frameTree': {'frame': frame}}
        if name == 'Runtime.enable':
            if self.mode != 'child-only' and self.mode != 'isolated-only':
                self.created(11, origin='https://foreign.invalid' if self.mode == 'foreign-origin' else ORIGIN)
            self.created(12, frame='child')
            self.created(13, default=False)
            if self.mode == 'duplicate-root': self.created(14)
            return {}
        if name == 'Runtime.evaluate':
            self.entered.set()
            if self.mode in ('stall', 'cancel'): await asyncio.sleep(60)
            if self.mode == 'destroyed':
                self.emit('Runtime.executionContextDestroyed', {'executionContextId': 11})
            if self.mode == 'reused-id':
                self.emit('Runtime.executionContextDestroyed', {'executionContextId': 11})
                self.created(11)
            if self.mode == 'cleared': self.emit('Runtime.executionContextsCleared', {})
            if self.mode == 'root-navigation':
                self.emit('Page.frameNavigated', {'frame': {'id':'root', 'url':URL}})
            if self.mode == 'child-navigation':
                self.emit('Page.frameNavigated', {'frame': {'id':'child', 'parentId':'root', 'url':URL}})
            if self.mode == 'page-changed': self.page.url = URL + '?changed'
            if self.mode == 'frame-object-changed': self.page.main_frame = SimpleNamespace(url=URL)
            if self.mode == 'exception':
                return {'exceptionDetails': {'text':'private platform content'}}
            return {'result': {'type':'object','value':{'native_root':True}}}
        return {}

    async def detach(self):
        self.detached = True


class NativeReadContractTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, mode=None):
        page = SimpleNamespace(url=URL, main_frame=SimpleNamespace(url=URL))
        client = Client(page)
        client.mode = mode
        page.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=client))
        return page, client

    def assert_cleanup(self, client):
        self.assertTrue(client.detached)
        self.assertEqual(client.handlers, {})
        self.assertTrue(all(name in ('Page.enable','Page.getFrameTree','Runtime.enable','Runtime.evaluate')
                            for name, _ in client.commands))

    async def test_unique_root_default_ignores_real_child_and_isolated_contexts(self):
        page, client = self.fixture()
        self.assertEqual(await canonical_root_evaluate(page, '({native_root:true})'), {'native_root':True})
        call = next(args for name,args in client.commands if name == 'Runtime.evaluate')
        self.assertEqual(call['contextId'], 11)
        self.assertTrue(call['awaitPromise'])
        self.assertTrue(call['returnByValue'])
        self.assert_cleanup(client)

    async def test_child_isolated_ambiguous_and_wrong_origin_never_evaluate(self):
        for mode in ('child-only','isolated-only','duplicate-root','foreign-origin'):
            with self.subTest(mode=mode):
                page,client = self.fixture(mode)
                with self.assertRaises(NativeReadError): await canonical_root_evaluate(page, 'true')
                self.assertFalse(any(name=='Runtime.evaluate' for name,_ in client.commands))
                self.assert_cleanup(client)

    async def test_destroyed_cleared_reused_id_and_same_url_root_navigation_reject(self):
        for mode in ('destroyed','cleared','reused-id','root-navigation'):
            with self.subTest(mode=mode):
                page,client = self.fixture(mode)
                with self.assertRaises(NativeReadError) as error: await canonical_root_evaluate(page, 'true')
                self.assertEqual(error.exception.reason, 'native_read_context_changed')
                self.assert_cleanup(client)

    async def test_root_tree_url_page_and_frame_identity_must_stay_exact(self):
        for mode in ('stale-tree','stale-url','page-changed','frame-object-changed'):
            with self.subTest(mode=mode):
                page,client = self.fixture(mode)
                with self.assertRaises(NativeReadError): await canonical_root_evaluate(page, 'true')
                self.assert_cleanup(client)

    async def test_unrelated_child_navigation_cannot_replace_root_identity(self):
        page,client = self.fixture('child-navigation')
        self.assertEqual(await canonical_root_evaluate(page, 'true'), {'native_root':True})
        self.assert_cleanup(client)

    async def test_exception_reason_does_not_expose_platform_content(self):
        page,client = self.fixture('exception')
        with self.assertRaises(NativeReadError) as error: await canonical_root_evaluate(page, 'true')
        self.assertEqual(str(error.exception), 'native_read_evaluation_failed')
        self.assert_cleanup(client)

    async def test_timeout_and_cancellation_detach_and_remove_all_handlers(self):
        page,client = self.fixture('stall')
        with self.assertRaises(NativeReadError) as error: await canonical_root_evaluate(page,'true',timeout=.01)
        self.assertEqual(error.exception.reason,'native_read_timeout')
        self.assert_cleanup(client)
        page,client = self.fixture('cancel')
        task=asyncio.create_task(canonical_root_evaluate(page,'true'))
        await client.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assert_cleanup(client)

    async def test_invalid_input_and_page_scope_create_no_cdp_session(self):
        for expression,timeout in (('',5),('true',6),('true',True),('true',float('nan'))):
            page,_=self.fixture()
            with self.assertRaises(NativeReadError): await canonical_root_evaluate(page,expression,timeout=timeout)
            page.context.new_cdp_session.assert_not_awaited()
        page,_=self.fixture()
        page.main_frame.url=URL+'?foreign'
        with self.assertRaises(NativeReadError): await canonical_root_evaluate(page,'true')
        page.context.new_cdp_session.assert_not_awaited()
        page,_=self.fixture()
        page.main_frame=None
        with self.assertRaises(NativeReadError): await canonical_root_evaluate(page,'true')
        page.context.new_cdp_session.assert_not_awaited()


class NativeReadBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context()
        async def route(r):
            html='<body><p id="child">Child</p>' if '/micro/content/' in r.request.url else '<body><p id="main">Main</p><iframe src="/micro/content/fixture"></iframe>'
            await r.fulfill(content_type='text/html',body=html)
        await self.context.route('**/*',route)
        self.page=await self.context.new_page()
        await self.page.goto(URL)
        await self.page.frames[1].locator('#child').wait_for(state='attached')

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.pw.stop()

    async def test_actual_iframe_and_isolated_world_do_not_own_native_main_read(self):
        # Prime the child default realm that triggers reused-target synthesis
        # in Patchright. The independent native read still owns the root.
        await self.page.frames[1].evaluate('window===top',isolated_context=False)
        client=await self.context.new_cdp_session(self.page)
        try:
            tree=await client.send('Page.getFrameTree')
            await client.send('Page.createIsolatedWorld',{'frameId':tree['frameTree']['frame']['id'],
                'worldName':'native-read-fixture-isolated'})
            value=await canonical_root_evaluate(self.page,"({root:window===top,main:!!document.querySelector('#main'),child:!!document.querySelector('#child')})")
            self.assertEqual(value,{'root':True,'main':True,'child':False})
        finally:
            await client.detach()

    async def test_real_reader_null_is_returned_without_other_realm_fallback(self):
        self.assertIsNone(await canonical_root_evaluate(self.page,'null'))

    async def test_real_root_navigation_during_async_read_is_rejected(self):
        task=asyncio.create_task(canonical_root_evaluate(self.page,'new Promise(resolve=>setTimeout(()=>resolve(true),400))'))
        await asyncio.sleep(.05)
        await self.page.goto(URL+'?navigation-fixture')
        with self.assertRaises(NativeReadError): await task


if __name__=='__main__':
    unittest.main()
