"""Current creator-owner evidence in real Patchright isolated-world fixtures.

All creator URLs and current-user responses are fulfilled locally. No real
platform, account, profile, credential or backend is contacted.
"""
import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

from patchright.async_api import async_playwright
from uploader.draft_session import (DraftInterrupted, current_owner_state,
                                    inspect_session, observe_session, verify_session,
                                    watch_session)


ROOT = Path(__file__).resolve().parent.parent
HOSTS = {'kuaishou': 'cp.kuaishou.com', 'bilibili': 'member.bilibili.com',
         'xiaohongshu': 'creator.xiaohongshu.com', 'tencent': 'channels.weixin.qq.com',
         'douyin': 'creator.douyin.com'}
OWNER_SCRIPTS = {
    'kuaishou': "document.querySelector('#app').__vue__={$store:{state:{user:{logined:true,userId:'ks-owner'}}}};",
    'bilibili': "document.querySelector('#app').__vue__={$store:{state:{upInfo:{isLogin:true,mid:8412}}}};",
    'xiaohongshu': "document.querySelector('#app').__vue_app__={_context:{provides:{store:{getters:{authPermissionState:{isLogin:true},authPermissionRes:{isLogin:{userId:'xhs-owner'}}}}}}};",
    'tencent': "window._store={userStore:{isExpired:false,user:{uid:'wx-operator',username:'wechat-id'},finder:{finderUsername:'finder-owner'}}};",
}
OWNER_IDS = {'kuaishou': 'ks-owner', 'bilibili': '8412',
             'xiaohongshu': 'xhs-owner', 'tencent': 'finder-owner'}
RESPONSE_PATHS = {
    'kuaishou': '/rest/v2/creator/pc/authority/account/current',
    'bilibili': '/x/web-interface/nav',
    'tencent': '/cgi-bin/mmfinderassistant-bin/auth/auth_data',
}


class SessionOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.html = '<div id="app"></div>'
        self.responses = {}
        self.requests = []

        async def local_route(route):
            parsed = urlparse(route.request.url)
            self.requests.append((route.request.method, parsed.hostname, parsed.path))
            key = (parsed.hostname, parsed.path)
            if key in self.responses:
                await route.fulfill(content_type='application/json', body=json.dumps(self.responses[key]),
                                    headers={'Access-Control-Allow-Origin': '*'})
            else:
                await route.fulfill(content_type='text/html; charset=utf-8', body=self.html)
        await self.context.route('**/*', local_route)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def load(self, platform, script=None, host=None, extra='', ready=True):
        controls = '<input type="file" accept="video/mp4"><div>内容管理 数据中心 视频投稿 发布笔记</div>' if ready else ''
        self.html = f'<!doctype html><meta charset="utf-8"><div id="app"></div>{controls}{extra}<script>{script if script is not None else OWNER_SCRIPTS[platform]}</script>'
        await self.page.goto('https://' + (host or HOSTS[platform]) + '/creator-fixture')

    async def test_kuaishou_empty_bootstrap_waits_for_fresh_owner(self):
        initial = "document.querySelector('#app').__vue__={$store:{state:{user:{logined:false,userId:0,userName:'',userAvatar:'',userKwaiId:''}}}};"
        await self.load('kuaishou', initial)
        owner = await current_owner_state('kuaishou', self.page)
        self.assertIsNone(owner['owner'])
        self.assertFalse(owner['loggedOut'])
        self.assertFalse((await inspect_session('kuaishou', self.page))['verified'])
        await self.page.evaluate("document.querySelector('#app').__vue__.$store.state.user={logined:true,userId:'ks-owner'}", isolated_context=False)
        self.assertTrue((await inspect_session('kuaishou', self.page))['verified'])
        await self.page.evaluate("document.querySelector('#app').__vue__.$store.state.user.logined=false", isolated_context=False)
        stopped = await inspect_session('kuaishou', self.page)
        self.assertFalse(stopped['verified'])
        self.assertEqual(stopped['diagnostic'], 'owner_logged_out')

    async def test_kuaishou_partial_empty_or_false_owner_remains_rejected(self):
        for script in (
            "document.querySelector('#app').__vue__={$store:{state:{user:{logined:false,userId:0}}}};",
            "document.querySelector('#app').__vue__={$store:{state:{user:{logined:false,userId:'old-owner',userName:'',userAvatar:'',userKwaiId:''}}}};",
        ):
            await self.load('kuaishou', script)
            stopped = await inspect_session('kuaishou', self.page)
            self.assertFalse(stopped['verified'])
            self.assertEqual(stopped['diagnostic'], 'owner_logged_out')

    async def shared_owner(self, platform):
        script = (ROOT / 'ui-assets/session-owner.js').read_text()
        session = await self.context.new_cdp_session(self.page)
        try:
            result = await session.send('Runtime.evaluate', {
                'expression': script + '(' + json.dumps(platform) + ')', 'returnByValue': True,
            })
            self.assertNotIn('exceptionDetails', result)
            return result['result']['value']
        finally:
            await session.detach()

    async def fetch_owner_response(self, platform, payload, host=None):
        host = host or HOSTS[platform]
        path = RESPONSE_PATHS[platform]
        self.responses[(host, path)] = payload
        url = 'https://' + host + path
        session = await self.context.new_cdp_session(self.page)
        try:
            result = await session.send('Runtime.evaluate', {
                'expression': 'fetch(' + json.dumps(url) + ').then(response=>response.json())',
                'awaitPromise': True, 'returnByValue': True,
            })
            self.assertNotIn('exceptionDetails', result)
        finally:
            await session.detach()
        for _ in range(100):
            observed = getattr(self.page, '_sau_session_owner', {})
            if observed.get('owner') or observed.get('invalid'):
                return observed
            await asyncio.sleep(.01)
        return getattr(self.page, '_sau_session_owner', {})

    async def douyin_owner(self, restriction=None, *, uid='dy-owner', logged=True, extra=''):
        user = {'uid': uid, 'nickname': '隔离本人', 'isLogin': logged}
        if restriction is not None:
            user['health_score'] = {'post_text_limit': restriction}
        script = 'window.USER_INFO=' + json.dumps({'userInfo': user}, ensure_ascii=False) + ';' + extra
        await self.load('douyin', script=script)
        current = await current_owner_state('douyin', self.page)
        shared = await self.shared_owner('douyin')
        self.assertEqual(current, shared)
        return current

    async def test_four_creator_owner_shapes_are_read_from_main_world(self):
        for platform, expected in OWNER_IDS.items():
            with self.subTest(platform=platform):
                await self.load(platform)
                # Patchright's ordinary evaluate sees its isolated window and
                # DOM wrappers, rather than the website's owner store.
                isolated = await self.page.evaluate("""() => ({
                    vue:!!document.querySelector('#app').__vue__,
                    vueApp:!!document.querySelector('#app').__vue_app__,
                    store:!!window._store
                })""")
                self.assertEqual(isolated, {'vue': False, 'vueApp': False, 'store': False})
                state = await current_owner_state(platform, self.page)
                shared = await self.shared_owner(platform)
                self.assertEqual(state['owner'], expected)
                self.assertEqual(shared['owner'], expected)
                current = await inspect_session(platform, self.page)
                self.assertTrue(current['verified'])
                self.assertEqual(current['uid'], expected)
                self.assertEqual(current['identity'], hashlib.sha256((platform + ':' + expected).encode()).hexdigest())

    async def test_creator_owner_does_not_require_an_upload_form_to_be_ready(self):
        await self.load('kuaishou', ready=False)
        current = await inspect_session('kuaishou', self.page)
        self.assertTrue(current['verified'])
        self.assertFalse(current['editor_ready'])

    async def test_false_login_flags_and_guest_ids_cannot_authenticate(self):
        scripts = {
            'kuaishou': OWNER_SCRIPTS['kuaishou'].replace('logined:true', 'logined:false'),
            'bilibili': OWNER_SCRIPTS['bilibili'].replace('isLogin:true', 'isLogin:false'),
            'xiaohongshu': OWNER_SCRIPTS['xiaohongshu'].replace('authPermissionState:{isLogin:true}', 'authPermissionState:{isLogin:false}'),
            'tencent': OWNER_SCRIPTS['tencent'].replace('isExpired:false', 'isExpired:true'),
        }
        for platform, script in scripts.items():
            with self.subTest(platform=platform):
                await self.load(platform, script)
                self.assertTrue((await current_owner_state(platform, self.page))['loggedOut'])
                self.assertTrue((await self.shared_owner(platform))['loggedOut'])
                self.assertFalse((await inspect_session(platform, self.page))['verified'])
        for guest in ('0', '-1', 'null', 'undefined'):
            with self.subTest(guest=guest):
                script = OWNER_SCRIPTS['kuaishou'].replace("'ks-owner'", json.dumps(guest))
                await self.load('kuaishou', script)
                self.assertFalse((await current_owner_state('kuaishou', self.page)).get('owner'))
                self.assertFalse((await self.shared_owner('kuaishou')).get('owner'))
                self.assertFalse((await inspect_session('kuaishou', self.page))['verified'])

    async def test_tencent_finder_is_distinct_from_wechat_operator(self):
        await self.load('tencent')
        state = await current_owner_state('tencent', self.page)
        self.assertEqual(state['owner'], 'finder-owner')
        remove = observe_session('tencent', self.page)
        try:
            # The authentication response carries both operator and creator.
            observed = await self.fetch_owner_response('tencent', {
                'errCode': 0, 'data': {'user': {'uid': 'wx-operator'},
                                     'finderUser': {'username': 'finder-owner'}},
            })
            self.assertEqual(observed['owner'], 'finder-owner')
            self.assertTrue((await inspect_session('tencent', self.page))['verified'])
        finally:
            remove()

    async def test_post_authors_are_ignored_and_are_never_an_owner_fallback(self):
        extra = '<article data-user-id="post-author-one">别人的内容</article><article data-uid="post-author-two">别人的内容</article>'
        await self.load('kuaishou', extra=extra)
        current = await inspect_session('kuaishou', self.page)
        self.assertTrue(current['verified'])
        self.assertEqual(current['uid'], 'ks-owner')
        await self.load('kuaishou', script='', extra='<article data-uid="sole-post-author">别人的内容</article>')
        self.assertFalse((await inspect_session('kuaishou', self.page))['verified'])

    async def test_owner_is_only_read_on_its_allowlisted_creator_host(self):
        for host in ('fixture.invalid', 'cp.kuaishou.com.attacker.invalid', HOSTS['bilibili']):
            with self.subTest(host=host):
                await self.load('kuaishou', host=host)
                self.assertFalse((await current_owner_state('kuaishou', self.page)).get('owner'))
                self.assertFalse((await self.shared_owner('kuaishou')).get('owner'))
                self.assertFalse((await inspect_session('kuaishou', self.page))['verified'])

    async def test_owner_response_conflict_rejects_stale_or_changed_account(self):
        await self.load('kuaishou')
        remove = observe_session('kuaishou', self.page)
        try:
            await self.fetch_owner_response('kuaishou', {
                'result': 1, 'data': {'userId': 'other-owner', 'logined': True},
            })
            current = await inspect_session('kuaishou', self.page)
            self.assertFalse(current['verified'])
            self.assertTrue(current['account_conflict'])
            with self.assertRaises(DraftInterrupted) as result:
                await verify_session('kuaishou', self.page, timeout=.1, settle=0)
            self.assertEqual(result.exception.status, 'needs_reauthentication')
        finally:
            remove()

    async def test_kuaishou_permission_metadata_cannot_invalidate_the_live_owner(self):
        # Loaded official 6551.e689a430.js (SHA256 7773132ac47c0315e233b21
        # faf291fccfb4353ff21bac8947ae422f32e777b6e), offsets 225577/227271:
        # common/current/user populates authority.currentUser (only prGr in
        # the actual component). Bootstrap 229286 instead obtains owner data
        # from /rest/v2/creator/pc/authority/account/current (constant396136).
        await self.load('kuaishou')
        remove = observe_session('kuaishou', self.page)
        try:
            self.assertTrue((await inspect_session('kuaishou', self.page))['verified'])
            for path in ('/rest/cp/works/v2/common/pc/current/user',
                         '/rest/cp/works/v3/video/pc/common/current/user'):
                self.responses[(HOSTS['kuaishou'], path)] = {'result': 1, 'data': {'prGr': False}}
                session = await self.context.new_cdp_session(self.page)
                try:
                    result = await session.send('Runtime.evaluate', {
                        'expression': 'fetch(' + json.dumps('https://' + HOSTS['kuaishou'] + path)
                                      + ').then(response=>response.json())',
                        'awaitPromise': True, 'returnByValue': True,
                    })
                    self.assertNotIn('exceptionDetails', result)
                finally:
                    await session.detach()
                await asyncio.sleep(.02)
                self.assertFalse(self.page._sau_session_owner['invalid'])
                self.assertIsNone(self.page._sau_session_owner['owner'])
                current = await inspect_session('kuaishou', self.page)
                self.assertTrue(current['verified'])
                self.assertEqual(current['uid'], 'ks-owner')
        finally:
            remove()

    async def test_guest_owner_response_invalidates_stale_logged_in_store(self):
        await self.load('bilibili')
        remove = observe_session('bilibili', self.page)
        try:
            observed = await self.fetch_owner_response('bilibili', {'code': 0, 'data': {'isLogin': False, 'mid': 0}})
            self.assertTrue(observed['invalid'])
            self.assertFalse((await inspect_session('bilibili', self.page))['verified'])
        finally:
            remove()

    async def test_douyin_caption_reduction_requires_js_truthy_published_flag(self):
        message = json.dumps({'ItemDescTextLimit': 500})
        for published in (True, 1, 'yes', 'false', '0', {}, []):
            with self.subTest(published=published):
                state = await self.douyin_owner({'is_published': published, 'publish_msg': message})
                self.assertEqual(state['owner'], 'dy-owner')
                self.assertEqual(state['capabilities'], {'caption_max': 500})
        for published in (False, 0, None, ''):
            with self.subTest(published=published):
                state = await self.douyin_owner({'is_published': published, 'publish_msg': message})
                self.assertEqual(state['owner'], 'dy-owner')
                self.assertNotIn('capabilities', state)

    async def test_douyin_account_caption_limit_safely_normalizes_numeric_strings(self):
        for value, expected in ((350, 350), ('350', 350), (' 350 ', 350),
                                ('350.0', 350), ('3.5e2', 350), ('999', 999), (1, 1)):
            with self.subTest(value=value):
                state = await self.douyin_owner({'is_published': True,
                    'publish_msg': json.dumps({'ItemDescTextLimit': value})})
                self.assertEqual(state['capabilities'], {'caption_max': expected})

    async def test_douyin_missing_malformed_or_non_lower_integer_limits_do_not_shrink(self):
        messages = ['', '{invalid', 'null', '[]', '"text"', '{}', None,
                    {'ItemDescTextLimit': 500}]
        for value in (0, None, '', False, True, 'abc', '0', ' ', '1000', '1500',
                      1000, 2000, -1, 500.5, '500.5', 'Infinity', 'NaN', [], [500], {}):
            messages.append(json.dumps({'ItemDescTextLimit': value}))
        restrictions = [None, {}, {'is_published': True}]
        restrictions.extend({'is_published': True, 'publish_msg': message} for message in messages)
        for restriction in restrictions:
            with self.subTest(restriction=restriction):
                state = await self.douyin_owner(restriction)
                self.assertEqual(state['owner'], 'dy-owner')
                self.assertNotIn('capabilities', state)

    async def test_douyin_unconfirmed_or_logged_out_owner_never_exposes_capabilities(self):
        restriction = {'is_published': True, 'publish_msg': json.dumps({'ItemDescTextLimit': 500})}
        for uid, logged in (('dy-owner', False), ('0', True), ('-1', True), (None, True), ('', True)):
            with self.subTest(uid=uid, logged=logged):
                state = await self.douyin_owner(restriction, uid=uid, logged=logged)
                self.assertFalse(state.get('owner'))
                self.assertNotIn('capabilities', state)
                self.assertFalse((await inspect_session('douyin', self.page))['verified'])

    async def test_douyin_caption_capability_uses_the_selected_owner_not_post_authors(self):
        authors = 'window.__INITIAL_STATE__={posts:[{uid:"other-author",nickname:"作者",health_score:{post_text_limit:{is_published:true,publish_msg:\'{"ItemDescTextLimit":100}\'}}}]};'
        state = await self.douyin_owner({'is_published': True,
            'publish_msg': json.dumps({'ItemDescTextLimit': 800})}, extra=authors)
        self.assertEqual(state['capabilities'], {'caption_max': 800})
        state = await self.douyin_owner(extra=authors)
        self.assertEqual(state['owner'], 'dy-owner')
        self.assertNotIn('capabilities', state)

    async def test_other_platform_runtime_capabilities_remain_bound_to_verified_owner(self):
        script = OWNER_SCRIPTS['kuaishou'] + 'document.querySelector("#app").__vue__.uploadConfig={maxTagCount:2};'
        await self.load('kuaishou', script=script)
        self.assertEqual((await current_owner_state('kuaishou', self.page))['capabilities'], {'tags_max': 2})
        script = OWNER_SCRIPTS['xiaohongshu'] + 'document.querySelector("#app").__vue_app__._instance={setupState:{features:{isTopicLimit:true}}};'
        await self.load('xiaohongshu', script=script)
        self.assertEqual((await current_owner_state('xiaohongshu', self.page))['capabilities'], {'tags_max': 3})
        script = script.replace('authPermissionState:{isLogin:true}', 'authPermissionState:{isLogin:false}')
        await self.load('xiaohongshu', script=script)
        self.assertNotIn('capabilities', await current_owner_state('xiaohongshu', self.page))

    async def test_missing_login_flag_keeps_the_store_owner_and_explicit_false_logs_out(self):
        missing = "document.querySelector('#app').__vue__={$store:{state:{user:{userId:'ks-owner'}}}};"
        await self.load('kuaishou', missing)
        state = await current_owner_state('kuaishou', self.page)
        shared = await self.shared_owner('kuaishou')
        self.assertEqual(state.get('owner'), 'ks-owner')
        self.assertIs(state.get('loggedOut'), False)
        self.assertEqual(shared.get('owner'), 'ks-owner')
        self.assertEqual(shared.get('loggedOut'), False)
        current = await inspect_session('kuaishou', self.page)
        self.assertTrue(current['verified'])
        self.assertEqual(current['uid'], 'ks-owner')
        await self.load('kuaishou', OWNER_SCRIPTS['kuaishou'].replace('logined:true', 'logined:false'))
        state = await current_owner_state('kuaishou', self.page)
        self.assertTrue(state['loggedOut'])
        self.assertFalse(state.get('owner'))
        expired = "window._store={userStore:{user:{uid:'wx-operator'},finder:{finderUsername:'finder-owner'}}};"
        await self.load('tencent', expired)
        state = await current_owner_state('tencent', self.page)
        self.assertFalse(state.get('owner'))
        self.assertIs(state.get('loggedOut'), False)
        self.assertNotIn('wx-operator', json.dumps(state))

    async def test_other_host_same_path_is_not_the_creator(self):
        await self.load('kuaishou')
        remove = observe_session('kuaishou', self.page)
        try:
            self.responses[('evil.invalid', RESPONSE_PATHS['kuaishou'])] = {'result': 1, 'userId': 'evil-owner'}
            session = await self.context.new_cdp_session(self.page)
            try:
                result = await session.send('Runtime.evaluate', {
                    'expression': 'fetch("https://evil.invalid/rest/v2/creator/pc/authority/account/current").then(r=>r.json())',
                    'awaitPromise': True, 'returnByValue': True,
                })
                self.assertEqual(result['result']['value']['userId'], 'evil-owner')
            finally:
                await session.detach()
            await asyncio.sleep(.2)
            observed = getattr(self.page, '_sau_session_owner', {})
            self.assertNotEqual(observed.get('owner'), 'evil-owner')
            self.assertFalse(observed.get('invalid'))
            current = await inspect_session('kuaishou', self.page)
            self.assertTrue(current['verified'])
            self.assertEqual(current['uid'], 'ks-owner')
        finally:
            remove()

    async def test_wechat_operator_response_without_finder_is_not_the_owner(self):
        await self.load('tencent')
        remove = observe_session('tencent', self.page)
        try:
            observed = await self.fetch_owner_response('tencent', {
                'errCode': 0, 'data': {'user': {'uid': 'wx-operator', 'username': 'wechat-id'}},
            })
            self.assertTrue(observed['invalid'])
            self.assertFalse(observed.get('owner'))
            self.assertFalse((await inspect_session('tencent', self.page))['verified'])
        finally:
            remove()

    async def test_explicit_false_login_response_does_not_keep_a_nonzero_id(self):
        await self.load('bilibili')
        remove = observe_session('bilibili', self.page)
        try:
            observed = await self.fetch_owner_response('bilibili', {'code': 0, 'data': {'isLogin': False, 'mid': 8412}})
            self.assertTrue(observed['invalid'])
            self.assertNotEqual(observed.get('owner'), '8412')
            self.assertFalse((await inspect_session('bilibili', self.page))['verified'])
            remove()
            remove = observe_session('bilibili', self.page)
            observed = await self.fetch_owner_response('bilibili', {'code': 0, 'data': {'mid': 8412}})
            self.assertEqual(observed['owner'], '8412')
            self.assertFalse(observed['invalid'])
            self.assertTrue((await inspect_session('bilibili', self.page))['verified'])
        finally:
            remove()

    async def test_navigation_and_close_do_not_reuse_the_previous_owner(self):
        await self.load('kuaishou')
        remove = observe_session('kuaishou', self.page)
        try:
            observed = await self.fetch_owner_response('kuaishou', {'result': 1, 'userId': 'ks-owner'})
            self.assertEqual(observed['owner'], 'ks-owner')
            self.html = '<!doctype html><div id="app"></div><input type="file" accept="video/mp4"><div>内容管理 数据中心</div><script>' + OWNER_SCRIPTS['kuaishou'].replace('logined:true', 'logined:false') + '</script>'
            await self.page.goto('https://' + HOSTS['kuaishou'] + '/after-navigation')
            self.assertIsNone(self.page._sau_session_owner['owner'])
            state = await current_owner_state('kuaishou', self.page)
            self.assertTrue(state['loggedOut'])
            self.assertFalse(state.get('owner'))
            self.assertFalse((await inspect_session('kuaishou', self.page))['verified'])
            closed = self.page
            await closed.close()
            self.assertIsNone(closed._sau_session_owner['owner'])
            self.page = await self.context.new_page()
            await self.load('kuaishou')
            reopened = await current_owner_state('kuaishou', self.page)
            self.assertEqual(reopened['owner'], 'ks-owner')
            self.assertFalse(hasattr(self.page, '_sau_session_owner') and self.page._sau_session_owner.get('owner'))
        finally:
            remove()

    async def test_observer_cleanup_drops_the_cached_owner(self):
        await self.load('kuaishou')
        remove = observe_session('kuaishou', self.page)
        await self.fetch_owner_response('kuaishou', {'result': 1, 'userId': 'ks-owner'})
        self.assertEqual(self.page._sau_session_owner['owner'], 'ks-owner')
        remove()
        self.assertIsNone(self.page._sau_session_owner['owner'])
        current = await inspect_session('kuaishou', self.page)
        self.assertFalse(current.get('account_conflict', False))
        self.assertTrue(current['verified'])
        self.assertEqual(current['uid'], 'ks-owner')

    async def test_douyin_limit_comes_only_from_the_single_confirmed_owner(self):
        author_limit = {'is_published': True, 'publish_msg': json.dumps({'ItemDescTextLimit': 100})}
        owner_limit = {'is_published': True, 'publish_msg': json.dumps({'ItemDescTextLimit': 800})}
        posts = ('window.__INITIAL_STATE__={posts:[{uid:"other-author",nickname:"作者",health_score:{post_text_limit:'
                 + json.dumps(author_limit, ensure_ascii=False) + '}}]};')
        state = await self.douyin_owner(owner_limit, extra=posts)
        self.assertEqual(state['owner'], 'dy-owner')
        self.assertEqual(state['capabilities'], {'caption_max': 800})
        conflict = {
            'userInfo': {'uid': 'other-author', 'nickname': '作者', 'isLogin': True,
                         'health_score': {'post_text_limit': author_limit}},
            'uid': 'dy-owner', 'nickname': '隔离本人', 'isLogin': True,
            'health_score': {'post_text_limit': owner_limit},
        }
        script = 'window.USER_INFO=' + json.dumps(conflict, ensure_ascii=False) + ';'
        await self.load('douyin', script=script)
        state = await current_owner_state('douyin', self.page)
        self.assertFalse(state.get('owner'))
        self.assertIs(state.get('loggedOut'), False)
        self.assertNotIn('capabilities', state)
        self.assertFalse((await inspect_session('douyin', self.page))['verified'])


class FakePage:
    """Listener page. No browser and no network."""

    def __init__(self, url):
        self.url = url
        self.listeners = {}
        self.main_frame = object()
        self.frames = []
        self._closed = False

    def on(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.listeners[event].remove(handler)

    def is_closed(self):
        return self._closed

    async def emit(self, event, arg):
        pending = []
        for handler in list(self.listeners.get(event, ())):
            result = handler(arg)
            if asyncio.iscoroutine(result):
                pending.append(result)
        if pending:
            await asyncio.gather(*pending)


class FakeRequest:
    def __init__(self, url):
        self.url = url


class FakeResponse:
    def __init__(self, request, payload, status=200, hold=False):
        self.request = request
        self.url = request.url
        self.status = status
        self.payload = payload
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        if not hold:
            self.gate.set()

    async def json(self):
        self.started.set()
        await self.gate.wait()
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeCDP:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def detach(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError('cdp already closed')


def response_time_stamp_would_accept(generation_when_response_arrives, generation_after_body):
    """Old observer read generation when the response event fired, not at send."""
    return generation_when_response_arrives == generation_after_body


KS_USER = 'https://cp.kuaishou.com/rest/v2/creator/pc/authority/account/current'
BILI_NAV = 'https://api.bilibili.com/x/web-interface/nav'


class OwnerObserverFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, page):
        tasks = list(getattr(page, '_sau_owner_detach_tasks', []) or [])
        if tasks:
            await asyncio.gather(*tasks)

    async def test_kuaishou_permission_endpoint_cannot_erase_or_replace_a_confirmed_owner(self):
        page = FakePage('https://cp.kuaishou.com/article/publish/video')
        remove = observe_session('kuaishou', page)
        try:
            request = FakeRequest(KS_USER)
            await page.emit('request', request)
            await page.emit('response', FakeResponse(request, {
                'result': 1, 'data': {'userId': 'ks-owner', 'logined': True},
            }))
            self.assertEqual(page._sau_session_owner['owner'], 'ks-owner')
            expected = dict(page._sau_session_owner)
            for path in ('/rest/cp/works/v2/common/pc/current/user',
                         '/rest/cp/works/v3/video/pc/common/current/user'):
                for payload in ({'result': 1, 'data': {'prGr': False}},
                                {'result': 1, 'data': {'userId': 'unrelated-author', 'logined': False}}):
                    with self.subTest(path=path, owner_shaped='userId' in payload['data']):
                        permission = FakeRequest('https://cp.kuaishou.com' + path)
                        response = FakeResponse(permission, payload)
                        await page.emit('request', permission)
                        await page.emit('response', response)
                        self.assertEqual(page._sau_session_owner, expected)
                        self.assertFalse(response.started.is_set())
        finally:
            remove()

    async def test_kuaishou_actual_owner_logout_http_errors_and_unreadable_owner_fail_closed(self):
        page = FakePage('https://cp.kuaishou.com/article/publish/video')
        cases = [
            (200, {'result': 1, 'data': {'userId': 'ks-owner', 'logined': False}}),
            (200, {'result': 1, 'data': {'logined': False}}),
            (200, {'result': 1, 'data': {}}),
            (200, {'result': 109, 'data': {}}),
            (401, {'result': 1, 'data': {'userId': 'ks-owner', 'logined': True}}),
            (403, {'result': 1, 'data': {'userId': 'ks-owner', 'logined': True}}),
            (200, RuntimeError('synthetic unreadable owner response')),
        ]
        for status, payload in cases:
            with self.subTest(status=status, unreadable=isinstance(payload, BaseException)):
                remove = observe_session('kuaishou', page)
                try:
                    request = FakeRequest(KS_USER)
                    await page.emit('request', request)
                    await page.emit('response', FakeResponse(request, {
                        'result': 1, 'data': {'userId': 'ks-owner', 'logined': True},
                    }))
                    self.assertEqual(page._sau_session_owner['owner'], 'ks-owner')
                    invalid = FakeRequest(KS_USER)
                    await page.emit('request', invalid)
                    await page.emit('response', FakeResponse(invalid, payload, status=status))
                    self.assertIsNone(page._sau_session_owner['owner'])
                    self.assertTrue(page._sau_session_owner['invalid'])
                finally:
                    remove()

    async def test_request_stamp_rejects_both_navigation_orders(self):
        page = FakePage('https://cp.kuaishou.com/creator-fixture')
        remove = observe_session('kuaishou', page)
        try:
            request = FakeRequest(KS_USER)
            await page.emit('request', request)
            await page.emit('response', FakeResponse(request, {'result': 1, 'userId': 'ks-owner'}))
            self.assertEqual(page._sau_session_owner['owner'], 'ks-owner')
            await page.emit('framenavigated', object())
            self.assertEqual(page._sau_session_owner['owner'], 'ks-owner')
            await page.emit('framenavigated', page.main_frame)
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertFalse(page._sau_session_owner['invalid'])

            # Request left before navigation; its response arrives after.
            # Reading generation at response time would accept this body.
            late = FakeRequest(KS_USER)
            await page.emit('request', late)
            sent = page._sau_session_owner['generation']
            await page.emit('framenavigated', page.main_frame)
            arrived = page._sau_session_owner['generation']
            self.assertNotEqual(sent, arrived)
            self.assertTrue(response_time_stamp_would_accept(arrived, arrived))
            await page.emit('response', FakeResponse(late, {'result': 1, 'userId': 'late-owner'}))
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertFalse(page._sau_session_owner['invalid'])
            self.assertNotEqual(page._sau_session_owner.get('owner'), 'late-owner')

            # Response event starts, navigation happens while json() is waiting.
            held = FakeRequest(KS_USER)
            await page.emit('request', held)
            response = FakeResponse(held, {'result': 1, 'userId': 'inflight-owner'}, hold=True)
            pending = asyncio.create_task(page.emit('response', response))
            await response.started.wait()
            generation_at_response = page._sau_session_owner['generation']
            await page.emit('framenavigated', page.main_frame)
            response.gate.set()
            await pending
            self.assertFalse(response_time_stamp_would_accept(
                generation_at_response, page._sau_session_owner['generation']))
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertNotEqual(page._sau_session_owner.get('owner'), 'inflight-owner')
        finally:
            remove()

    async def test_unseen_replaced_and_removed_responses_do_not_apply(self):
        page = FakePage('https://cp.kuaishou.com/creator-fixture')
        remove = observe_session('kuaishou', page)
        try:
            unseen = FakeRequest(KS_USER)
            await page.emit('response', FakeResponse(unseen, {'result': 1, 'userId': 'unseen-owner'}))
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertFalse(page._sau_session_owner['invalid'])

            request = FakeRequest(KS_USER)
            await page.emit('request', request)
            held = FakeResponse(request, {'result': 1, 'userId': 'old-observer'}, hold=True)
            pending = asyncio.create_task(page.emit('response', held))
            await held.started.wait()
            replacement = observe_session('kuaishou', page)
            held.gate.set()
            await pending
            self.assertIs(page._sau_session_owner_remove, replacement)
            self.assertIsNone(page._sau_session_owner['owner'])
            fresh = FakeRequest(KS_USER)
            await page.emit('request', fresh)
            await page.emit('response', FakeResponse(fresh, {'result': 1, 'userId': 'fresh-owner'}))
            self.assertEqual(page._sau_session_owner['owner'], 'fresh-owner')
            remove = replacement
        finally:
            remove()
            self.assertIsNone(page._sau_session_owner['owner'])

    async def test_retire_detaches_the_old_cdp_session_only(self):
        page = FakePage('https://cp.kuaishou.com/creator-fixture')
        remove = observe_session('kuaishou', page)
        try:
            old = FakeCDP()
            page._sau_owner_cdp = old
            await page.emit('framenavigated', page.main_frame)
            replacement = FakeCDP()
            page._sau_owner_cdp = replacement
            await self.drain(page)
            self.assertEqual(old.calls, 1)
            self.assertEqual(replacement.calls, 0)
            await page.emit('framenavigated', page.main_frame)
            newer = FakeCDP(fail=True)
            page._sau_owner_cdp = newer
            await self.drain(page)
            self.assertEqual(replacement.calls, 1)
            self.assertEqual(old.calls, 1)
            page._closed = True
            await page.emit('close', page)
            await self.drain(page)
            self.assertEqual(newer.calls, 1)
            self.assertEqual(replacement.calls, 1)
        finally:
            remove()
            await self.drain(page)

    async def test_bilibili_nav_accepts_only_the_official_api_host(self):
        page = FakePage('https://member.bilibili.com/platform/upload/video/frame')
        remove = observe_session('bilibili', page)
        try:
            async def respond(url, payload):
                request = FakeRequest(url)
                await page.emit('request', request)
                await page.emit('response', FakeResponse(request, payload))

            await respond('https://evil.invalid/x/web-interface/nav', {'code': 0, 'data': {'mid': 'evil-owner'}})
            await respond('https://api.bilibili.com.attacker.invalid/x/web-interface/nav',
                          {'code': 0, 'data': {'mid': 'suffix-owner'}})
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertFalse(page._sau_session_owner['invalid'])
            await respond(BILI_NAV, {'code': -101, 'data': {'isLogin': False}})
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertTrue(page._sau_session_owner['invalid'])
            remove()
            remove = observe_session('bilibili', page)
            await respond(BILI_NAV, {'code': 0, 'data': {'mid': 8412}})
            self.assertEqual(page._sau_session_owner['owner'], '8412')
            self.assertFalse(page._sau_session_owner['invalid'])
            remove()
            remove = observe_session('bilibili', page)
            await respond('https://member.bilibili.com/x/web-interface/nav', {'code': 0, 'data': {'isLogin': False, 'mid': 8412}})
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertTrue(page._sau_session_owner['invalid'])
        finally:
            remove()


DY_FAST_USER = 'https://creator.douyin.com/aweme/v1/creator/pc/user/info/'
DY_CANONICAL_OWNER = '72631845092'


class DouyinFastOwnerObserverFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def _respond(self, page, url, payload, status=200):
        request = FakeRequest(url)
        await page.emit('request', request)
        await page.emit('response', FakeResponse(request, payload, status=status))

    async def _inspect_with_provider(self, page, provider):
        async def current_owner_state(platform, current_page):
            self.assertEqual(platform, 'douyin')
            self.assertIs(current_page, page)
            return provider

        with patch('uploader.draft_session.current_owner_state', new=current_owner_state):
            return await inspect_session('douyin', page)

    async def test_successful_fast_bootstrap_is_not_owner_evidence_on_editor_routes(self):
        pages = ('/creator-micro/content/upload', '/creator-micro/content/post/video')
        responses = (
            {'status_code': 0, 'user_id': 'fast-user-id', 'result': {'profile': 'synthetic'}},
            {'status_code': 0, 'result': {'profile': 'synthetic'}},
            {'result': {'userId': 'result-only-id'}},
        )
        canonical = {'owner': DY_CANONICAL_OWNER, 'loggedOut': False,
                     'source': 'douyin-current-user-provider'}
        for path in pages:
            for payload in responses:
                with self.subTest(path=path, payload_keys=tuple(payload)):
                    page = FakePage('https://creator.douyin.com' + path)
                    remove = observe_session('douyin', page)
                    try:
                        await self._respond(page, DY_FAST_USER, payload)
                        self.assertIsNone(page._sau_session_owner['owner'])
                        self.assertFalse(page._sau_session_owner['invalid'])
                        self.assertIsNone(page._sau_session_owner['invalid_reason'])
                        unknown = await self._inspect_with_provider(page, {
                            'owner': None, 'loggedOut': False,
                            'source': 'douyin-current-user-provider',
                        })
                        self.assertFalse(unknown['verified'])
                        current = await self._inspect_with_provider(page, canonical)
                        self.assertTrue(current['verified'])
                        self.assertEqual(current['uid'], DY_CANONICAL_OWNER)
                        self.assertEqual(current['source'], 'douyin-current-user-provider')
                    finally:
                        remove()

    async def test_fast_endpoint_auth_rejections_still_override_a_stale_provider(self):
        cases = (
            (401, {'status_code': 0, 'user_id': 'fast-user-id'}),
            (403, {'status_code': 0, 'user_id': 'fast-user-id'}),
            (200, {'status_code': 8, 'user_id': 'fast-user-id'}),
        )
        canonical = {'owner': DY_CANONICAL_OWNER, 'loggedOut': False,
                     'source': 'douyin-current-user-provider'}
        for status, payload in cases:
            with self.subTest(status=status, payload=payload):
                page = FakePage('https://creator.douyin.com/creator-micro/content/post/video')
                remove = observe_session('douyin', page)
                try:
                    await self._respond(page, DY_FAST_USER, payload, status=status)
                    self.assertTrue(page._sau_session_owner['invalid'])
                    self.assertEqual(page._sau_session_owner['invalid_reason'], 'response_rejected')
                    current = await self._inspect_with_provider(page, canonical)
                    self.assertFalse(current['verified'])
                    self.assertEqual(current['diagnostic'], 'response_rejected')
                finally:
                    remove()

    async def test_composite_bootstraps_without_individual_uid_do_not_log_out_editor(self):
        for path in ('/aweme/v1/creator/user/info/', '/web/api/media/user/info/'):
            page = FakePage('https://creator.douyin.com/creator-micro/content/post/video')
            remove = observe_session('douyin', page)
            try:
                await self._respond(page, 'https://creator.douyin.com' + path,
                                    {'status_code': 0, 'data': {'profile': 'synthetic'}})
                self.assertFalse(page._sau_session_owner['invalid'])
                current = await self._inspect_with_provider(page, {
                    'owner': DY_CANONICAL_OWNER, 'loggedOut': False,
                    'source': 'douyin-current-user-provider'})
                self.assertTrue(current['verified'])
                await self._respond(page, 'https://creator.douyin.com' + path, {'status_code': 8})
                self.assertTrue(page._sau_session_owner['invalid'])
            finally:
                remove()

    async def test_provider_logout_and_owner_change_remain_hard_stops(self):
        page = FakePage('https://creator.douyin.com/creator-micro/content/post/video')
        remove = observe_session('douyin', page)
        try:
            logged_out = await self._inspect_with_provider(page, {
                'owner': None, 'loggedOut': True,
                'source': 'douyin-current-user-provider',
            })
            self.assertFalse(logged_out['verified'])
            self.assertEqual(logged_out['diagnostic'], 'owner_logged_out')

            expected_owner = 'expected-owner'
            changed_owner = 'different-owner'
            account = {'identity': hashlib.sha256(('douyin:' + expected_owner).encode()).hexdigest()}
            provider = {'owner': changed_owner, 'loggedOut': False,
                        'source': 'douyin-current-user-provider'}
            # watch_session calls inspect_session, which awaits the patched
            # provider reader; use an async reader so the fixture mirrors CDP.
            async def changed_state(_platform, _page):
                return provider
            with patch('uploader.draft_session.current_owner_state', new=changed_state):
                with self.assertRaises(DraftInterrupted) as result:
                    await watch_session('douyin', page, account)
            self.assertEqual(result.exception.status, 'needs_reauthentication')
            self.assertIn('账号发生变化', str(result.exception))
        finally:
            remove()

    async def test_fast_endpoint_keeps_legacy_behavior_on_other_routes(self):
        page = FakePage('https://creator.douyin.com/creator-micro/content/other')
        remove = observe_session('douyin', page)
        try:
            await self._respond(page, DY_FAST_USER, {'status_code': 0, 'user_id': 'legacy-owner'})
            self.assertEqual(page._sau_session_owner['owner'], 'legacy-owner')
            self.assertFalse(page._sau_session_owner['invalid'])
            remove()
            remove = observe_session('douyin', page)
            await self._respond(page, DY_FAST_USER, {'result': {'userId': 'result-only-id'}})
            self.assertIsNone(page._sau_session_owner['owner'])
            self.assertTrue(page._sau_session_owner['invalid'])
            self.assertEqual(page._sau_session_owner['invalid_reason'], 'response_rejected')
        finally:
            remove()


if __name__ == '__main__':
    unittest.main(verbosity=2)
