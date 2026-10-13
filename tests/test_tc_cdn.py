"""Strict CDN configuration contracts and isolated native/Wujie DOM fixtures."""
import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader.native_read import NativeReadError, canonical_root_evaluate
from uploader.tc_cdn import _CAPTURE, _NATIVE_CAPTURE, capture_tc_cdn_hosts


MAIN = 'https://channels.weixin.qq.com/platform/post/create'
OWNER = 'https://channels.weixin.qq.com/micro/content/post/create'
FIELDS = {
    'cdnHost': 'finderassistancea.video.qq.com',
    'cdnHostList': ['finderassistancea.video.qq.com', 'finderassistancee.video.qq.com'],
    'spareCdnHostList': ['snsaispuploade.video.qq.com'],
}
EXPECTED = frozenset((*FIELDS['cdnHostList'], *FIELDS['spareCdnHostList']))


def projection(fields=None, *, realm='main', count=1):
    return {'origin': 'https://channels.weixin.qq.com', 'realm': realm,
            'envCount': count, 'fields': copy.deepcopy(FIELDS if fields is None else fields)}


class CaptureContractTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, value=None, *, url=MAIN):
        frame = SimpleNamespace(url=url, evaluate=AsyncMock(side_effect=AssertionError('logical realm')))
        page = SimpleNamespace(url=url, main_frame=frame, frames=[frame])
        reader = AsyncMock(return_value=copy.deepcopy(value))
        return page, reader

    async def capture(self, page, reader):
        with patch('uploader.tc_cdn.canonical_root_evaluate', reader):
            return await capture_tc_cdn_hosts(page)

    async def test_one_native_root_read_returns_only_exact_configuration_hosts(self):
        page, reader = self.fixture(projection())
        page.frames.append(SimpleNamespace(url=OWNER,
            evaluate=AsyncMock(side_effect=AssertionError('empty logical owner is not read'))))
        result = await self.capture(page, reader)
        self.assertEqual(result, EXPECTED)
        self.assertIsInstance(result, frozenset)
        reader.assert_awaited_once_with(page, '('+_NATIVE_CAPTURE+')(false)')
        for frame in page.frames:
            frame.evaluate.assert_not_awaited()
        self.assertNotIn('finderassistancec.video.qq.com', result)

    async def test_unavailable_ambiguous_wrong_realm_or_native_transport_error_fails_closed(self):
        for value in (None, projection(count=2), projection(realm='owner')):
            page, reader = self.fixture(value)
            self.assertIsNone(await self.capture(page, reader))
        page, reader = self.fixture(projection())
        reader.side_effect = NativeReadError('native_read_context_changed')
        self.assertIsNone(await self.capture(page, reader))
        page.main_frame.evaluate.assert_not_awaited()

    async def test_malicious_domain_url_auth_port_and_non_ascii_rejected(self):
        for value in ['evilvideo.qq.com', 'a.video.qq.com.evil.test',
                      'https://finderassistancea.video.qq.com',
                      'user:password@finderassistancea.video.qq.com',
                      'finderassistancea.video.qq.com:443', ' finderassistancea.video.qq.com',
                      'finderassistancea.video.qq.com.', 'xn--é.video.qq.com',
                      '-bad.video.qq.com', 'bad_.video.qq.com']:
            with self.subTest(value=value):
                fields = copy.deepcopy(FIELDS)
                fields['spareCdnHostList'] = [value]
                page, reader = self.fixture(projection(fields))
                self.assertIsNone(await self.capture(page, reader))

    async def test_wrong_list_type_empty_and_oversized_list_rejected(self):
        for fields in [dict(FIELDS, cdnHostList='finderassistancea.video.qq.com'),
                       {'cdnHost': None, 'cdnHostList': [], 'spareCdnHostList': []},
                       dict(FIELDS, spareCdnHostList=['a.video.qq.com'] * 129)]:
            page, reader = self.fixture(projection(fields))
            self.assertIsNone(await self.capture(page, reader))

    async def test_untrusted_page_origin_never_creates_native_reader(self):
        for url in ['http://channels.weixin.qq.com/platform/post/create',
                    'https://channels.weixin.qq.com.evil.test/platform/post/create',
                    'https://user:password@channels.weixin.qq.com/platform/post/create',
                    'https://channels.weixin.qq.com:443/platform/post/create', OWNER]:
            page, reader = self.fixture(projection(), url=url)
            self.assertIsNone(await self.capture(page, reader))
            reader.assert_not_awaited()

    async def test_native_projection_origin_and_current_page_frame_must_stay_exact(self):
        for mode in ('origin', 'page-url', 'frame-url', 'frame-object', 'missing-frame'):
            with self.subTest(mode=mode):
                page, reader = self.fixture(projection())
                if mode == 'origin':
                    reader.return_value['origin'] = 'https://evil.test'
                elif mode == 'missing-frame':
                    page.main_frame = None
                else:
                    async def change(*_):
                        if mode == 'page-url': page.url = MAIN + '?changed'
                        if mode == 'frame-url': page.main_frame.url = MAIN + '?changed'
                        if mode == 'frame-object': page.main_frame = SimpleNamespace(url=MAIN)
                        return projection()
                    reader.side_effect = change
                self.assertIsNone(await self.capture(page, reader))


class CaptureBrowserFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.driver = await async_playwright().start()
        self.browser = await self.driver.chromium.launch(headless=True, channel='chrome')
        self.context = await self.browser.new_context()
        self.fixture = {'main':'', 'owner':'', 'foreign':''}
        async def intercept(route):
            role = ('foreign' if 'evil.test' in route.request.url else
                    'owner' if '/micro/content/' in route.request.url else 'main')
            await route.fulfill(status=200, content_type='text/html', body=self.fixture[role])
        await self.context.route('**/*', intercept)
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await asyncio.wait_for(self.context.close(), timeout=8)
        await asyncio.wait_for(self.browser.close(), timeout=8)
        await asyncio.wait_for(self.driver.stop(), timeout=8)

    def html(self, fields=FIELDS, *, child=None, duplicate=False, proxy=False,
             unstable=False, replacing=False, logical_override=False, native_override=False):
        setup = 'window.fixtureEnv=' + json.dumps(fields, ensure_ascii=False) + ';'
        setup += '''window.sdkCalls=0;window.accountReads=0;
        function store(value){const s={envInfo:value};Object.defineProperty(s,'accountSecret',
        {get(){window.accountReads++;throw new Error('private')}});return s;}
        const vm={userStore:store(fixtureEnv),postVideo(){window.sdkCalls++;throw new Error('SDK')}};
        const host=document.createElement('div');document.body.appendChild(host);
        const root=host.attachShadow({mode:'open'});const el=document.createElement('div');
        root.appendChild(el);el.__vue__={$parent:vm};'''
        if duplicate:
            setup += '''const other=document.createElement('div');root.appendChild(other);
            other.__vue__={userStore:store(JSON.parse(JSON.stringify(fixtureEnv)))};'''
        if proxy:
            setup += "Object.defineProperty(vm,'userStore',{get(){throw new TypeError('private proxy')}});"
        if unstable:
            setup += "let n=0;Object.defineProperty(fixtureEnv,'cdnHost',{get(){return ++n%2?'a.video.qq.com':'e.video.qq.com'}});"
        if replacing:
            setup += "const original=fixtureEnv.cdnHost;Object.defineProperty(fixtureEnv,'cdnHost',{get(){vm.userStore.envInfo={cdnHost:original,cdnHostList:[],spareCdnHostList:[]};return original}});"
        if child:
            setup += 'const frame=document.createElement("iframe");frame.src='+json.dumps(child)+';document.body.appendChild(frame);'
        if logical_override:
            setup += '''window.addEventListener('load',()=>{
            const doc=frame.contentDocument;
            Object.defineProperty(document,'querySelectorAll',{value:doc.querySelectorAll.bind(doc)});
            Object.defineProperty(el,'ownerDocument',{get(){return doc}});
            Object.defineProperty(el,'getRootNode',{value(){return doc}});
            });'''
        if native_override:
            setup += "Document.prototype.querySelectorAll=function(){return []};"
        return '<!doctype html><body><script>' + setup + '</script>'

    async def load(self, html, owner='', foreign=''):
        self.fixture.update(main=html, owner=owner, foreign=foreign)
        await self.page.goto(MAIN, wait_until='load')

    async def assert_no_private_or_business_calls(self):
        value = await canonical_root_evaluate(self.page,
            '({sdk:window.sdkCalls,account:window.accountReads})')
        self.assertEqual(value, {'sdk':0,'account':0})

    async def test_native_physical_shadow_survives_wujie_logical_empty_owner_and_child_realm(self):
        await self.load(self.html(child=OWNER, logical_override=True), '<body>empty owner')
        await self.page.frames[1].evaluate('window===top', isolated_context=False)
        legacy = await canonical_root_evaluate(self.page, '('+_CAPTURE+')("main")')
        self.assertIsNone(legacy)
        self.assertEqual(await capture_tc_cdn_hosts(self.page), EXPECTED)
        diagnostic = await canonical_root_evaluate(self.page, '('+_NATIVE_CAPTURE+')(true)')
        self.assertTrue(diagnostic['native_intrinsics'])
        self.assertEqual(diagnostic['env_count'],1)
        self.assertTrue(diagnostic['same_env'])
        self.assertTrue(diagnostic['same_fields'])
        self.assertNotIn('video.qq.com',json.dumps(diagnostic))
        await self.assert_no_private_or_business_calls()

    async def test_aggregate_official_owner_requires_same_env_object_not_equal_copy_or_union(self):
        for name, expression, expected in (
            ('same-object','parent.fixtureEnv',EXPECTED),
            ('equal-copy','JSON.parse(JSON.stringify(parent.fixtureEnv))',None),
            ('conflict','({...parent.fixtureEnv,cdnHost:"e.video.qq.com"})',None)):
            with self.subTest(case=name):
                owner = '<body><div id="vm"></div><script>document.getElementById("vm").__vue__={userStore:{envInfo:'+expression+'}};</script>'
                await self.load(self.html(child=OWNER), owner)
                self.assertEqual(await capture_tc_cdn_hosts(self.page),expected)
                await self.assert_no_private_or_business_calls()

    async def test_ambiguous_mutating_missing_or_malformed_config_never_recovers_from_another_realm(self):
        cases = [
            ('two-envs',FIELDS,{'duplicate':True}),
            ('proxy-getter',FIELDS,{'proxy':True}),
            ('unstable-fields',FIELDS,{'unstable':True}),
            ('replaced-env',FIELDS,{'replacing':True}),
            ('non-native-query',FIELDS,{'native_override':True}),
            ('foreign-host',dict(FIELDS,cdnHost='evil.test'),{}),
            ('port',dict(FIELDS,cdnHost='a.video.qq.com:443'),{}),
            ('auth-url',dict(FIELDS,cdnHost='https://user:password@a.video.qq.com'),{}),
            ('empty',{'cdnHost':None,'cdnHostList':[],'spareCdnHostList':[]},{}),
            ('wrong-list',dict(FIELDS,cdnHostList='a.video.qq.com'),{}),
        ]
        for name,fields,options in cases:
            with self.subTest(case=name):
                await self.load(self.html(fields,**options))
                self.assertIsNone(await capture_tc_cdn_hosts(self.page))
                await self.assert_no_private_or_business_calls()

    async def test_foreign_iframe_cannot_supply_or_conflict_with_official_config(self):
        foreign = '<body><div id="vm"></div><script>document.getElementById("vm").__vue__={userStore:{envInfo:'+json.dumps(FIELDS)+'}};</script>'
        await self.load(self.html(child='https://evil.test/micro/content/post/create'),foreign=foreign)
        self.assertEqual(await capture_tc_cdn_hosts(self.page),EXPECTED)
        await self.load(self.html({'cdnHost':None,'cdnHostList':[],'spareCdnHostList':[]},
                                 child='https://evil.test/micro/content/post/create'),foreign=foreign)
        self.assertIsNone(await capture_tc_cdn_hosts(self.page))


if __name__ == '__main__':
    unittest.main(verbosity=2)
