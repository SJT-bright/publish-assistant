"""Isolated fixtures for the official Douyin editor current-user provider.

The creator origin is fulfilled locally by Patchright. The fixture shape below
is copied from the retained official bundle contract, not inferred from the
implementation under test. No live platform or account is contacted.
"""
import hashlib
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from patchright.async_api import async_playwright

from uploader.draft_session import current_owner_state, inspect_session


ROOT = Path(__file__).resolve().parent.parent
EDITOR_URL = 'https://creator.douyin.com/creator-micro/content/post/video?enter_from=home_draft'
OWNER_ID = '72631845092'

# Exact default value schema from research/acceptance-20261004/dy-editor-owner-contract.md
# (which records dy_creator_main.js offsets 398288 and 400671).
DOUYIN_PROVIDER_KEYS = (
    'userInfo', 'fastUserInfo', 'loginType', 'userLoading', 'abLoading', 'fg',
    'abTest', 'newPcLayoutVersion', 'setUserInfo', 'updateUserInfo',
    'loadUserInfo', 'permissions', 'lng', 'isOversea', 'setLng', 'playerModal',
    'fansIndexConfig', 'accountSwitch', 'aiCreationEntries',
    'aiCreationEntriesLoaded', 'selectedAICreationTabId',
    'setSelectedAICreationTabId',
)


def _fixture_script(config):
    config_json = json.dumps(config, ensure_ascii=False, separators=(',', ':'))
    keys_json = json.dumps(DOUYIN_PROVIDER_KEYS)
    return f'''(() => {{
      const config = {config_json};
      const expectedKeys = {keys_json};
      const makeValue = (spec = {{}}) => {{
        const userInfo = spec.userInfo === null ? null : {{
          uid: Object.prototype.hasOwnProperty.call(spec, 'uid') ? spec.uid : {json.dumps(OWNER_ID)},
          nickname: Object.prototype.hasOwnProperty.call(spec, 'nickname') ? spec.nickname : '隔离当前创作者',
          ...(spec.userInfoFields || {{}}),
        }};
        const value = {{
          userInfo,
          fastUserInfo: {{ uid: Object.prototype.hasOwnProperty.call(spec, 'fastUid') ? spec.fastUid : '98887776665' }},
          loginType: Object.prototype.hasOwnProperty.call(spec, 'loginType') ? spec.loginType : 'personal',
          userLoading: Object.prototype.hasOwnProperty.call(spec, 'userLoading') ? spec.userLoading : false,
          abLoading: false,
          fg: {{}},
          abTest: {{}},
          newPcLayoutVersion: 0,
          setUserInfo() {{}},
          updateUserInfo() {{}},
          loadUserInfo() {{}},
          permissions: {{}},
          lng: 'zh-CN',
          isOversea: false,
          setLng() {{}},
          playerModal: {{}},
          fansIndexConfig: {{}},
          accountSwitch: false,
          aiCreationEntries: [],
          aiCreationEntriesLoaded: false,
          selectedAICreationTabId: '',
          setSelectedAICreationTabId() {{}},
        }};
        for (const key of (spec.omitKeys || [])) delete value[key];
        for (const key of (spec.nonFunctionMethods || [])) value[key] = null;
        for (const key of (spec.extraKeys || [])) value[key] = true;
        if (value.userInfo && typeof value.userInfo === 'object')
          for (const key of (spec.omitUserInfoKeys || [])) delete value.userInfo[key];
        return value;
      }};
      const provider = value => ({{
        tag: 10, type: {{ _context: {{}} }}, memoizedProps: {{ value }},
        stateNode: null, child: null, sibling: null, return: null, alternate: null,
      }});
      const rootFor = providers => {{
        const root = {{ tag: 3, stateNode: null, child: null, sibling: null, return: null, alternate: null }};
        const wrapper = {{ tag: 0, stateNode: null, child: null, sibling: null, return: root, alternate: null }};
        root.child = wrapper;
        const children = providers.map(provider);
        wrapper.child = children[0] || null;
        for (let i = 0; i < children.length; i++) {{
          children[i].return = wrapper;
          children[i].sibling = children[i + 1] || null;
        }}
        return {{ root, wrapper, children }};
      }};
      const host = document.querySelector('body > #root');
      const providers = (config.providers || [{{}}]).map(makeValue);
      const current = rootFor(providers);
      const totalNodes = config.totalNodes || (2 + current.children.length);
      const fillerCount = Math.max(0, totalNodes - 2 - current.children.length);
      let previous = current.children[current.children.length - 1] || current.wrapper;
      for (let i = 0; i < fillerCount; i++) {{
        const filler = {{ tag: 5, type: null, memoizedProps: null, stateNode: null,
          child: null, sibling: null, return: current.wrapper, alternate: null }};
        if (previous === current.wrapper) current.wrapper.child = filler;
        else previous.sibling = filler;
        previous = filler;
      }}
      if (config.siblingCycle && previous !== current.wrapper) previous.sibling = current.wrapper.child;
      const stateNode = {{ current: current.root }};
      current.root.stateNode = stateNode;
      const old = rootFor([makeValue({{ uid: config.alternateUid || '99887776655' }})]);
      old.root.stateNode = stateNode;
      old.root.alternate = current.root;
      current.root.alternate = old.root;
      old.root.child.return = old.root;
      const anchorNames = config.anchorNames || ['__reactContainer$opaqueHash7'];
      for (const name of anchorNames) host[name] = old.root;
      const nested = host.querySelector('[data-garfish-root]');
      if (nested) {{
        const nestedOwner = rootFor([makeValue({{ uid: config.nestedUid || '99112233445' }})]);
        const nestedState = {{ current: nestedOwner.root }};
        nestedOwner.root.stateNode = nestedState;
        nested['__reactContainer$nestedHash2'] = nestedOwner.root;
      }}
      if (config.globalOwner) window.USER_INFO = {{ userInfo: {{
        uid: config.globalOwner, nickname: '隔离全局账号',
        ...(config.globalIsLogin === false ? {{ isLogin: false }} : {{}}),
      }} }};
      else if (config.globalIsLogin === false) window.USER_INFO = {{ userInfo: {{ uid: {json.dumps(OWNER_ID)}, nickname: '隔离全局账号', isLogin: false }} }};
      // Verify the fixture itself stays tied to the official fixed schema.
      document.body.dataset.fixtureSchemaExact = String(
        Object.keys(providers[0] || {{}}).sort().join('|') === [...expectedKeys].sort().join('|'));
    }})();'''


def _fixture_html(config=None):
    config = config or {}
    extra_root = '<div id="root" data-second-host-root></div>' if config.get('secondBodyRoot') else ''
    return f'''<!doctype html><html><head><meta charset="utf-8"></head><body>
      <div id="root"><main><div id="root" data-garfish-root></div>
        <label>标题<textarea>隔离标题</textarea></label>
        <div contenteditable="true">隔离正文</div>
        <input type="file" accept="video/mp4"><div>内容管理 视频投稿 草稿箱</div>
      </main></div>{extra_root}
      <script>{_fixture_script(config)}</script>
    </body></html>'''


class DouyinOwnerProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.html = _fixture_html()
        self.requests = []
        self.load_count = 0

        async def local_route(route):
            parsed = urlparse(route.request.url)
            self.requests.append((route.request.method, parsed.hostname, parsed.path))
            await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=self.html)

        await self.context.route('**/*', local_route)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def load(self, config=None, url=None):
        self.html = _fixture_html(config)
        self.load_count += 1
        target = url or EDITOR_URL
        target += ('&' if '?' in target else '?') + 'fixture_case=' + str(self.load_count)
        await self.page.goto(target)
        self.assertTrue(all(host in {'creator.douyin.com', 'creator.douyin.com.evil.example'}
                            for _, host, _ in self.requests))
        return await current_owner_state('douyin', self.page)

    async def test_current_committed_provider_is_owner_and_inspection_identity_matches(self):
        state = await self.load()
        self.assertEqual(await self.page.locator('body').get_attribute('data-fixture-schema-exact'), 'true')
        self.assertEqual(state.get('owner'), OWNER_ID)
        self.assertEqual(state.get('source'), 'douyin-current-user-provider')
        inspected = await inspect_session('douyin', self.page)
        self.assertTrue(inspected['verified'])
        self.assertEqual(inspected['uid'], OWNER_ID)
        self.assertEqual(inspected['identity'], hashlib.sha256(('douyin:' + OWNER_ID).encode()).hexdigest())
        self.assertTrue(inspected['editor_ready'])

    async def test_nested_garfish_root_is_ignored_but_two_body_roots_are_rejected(self):
        state = await self.load({'nestedUid': '99112233445'})
        self.assertEqual(state.get('owner'), OWNER_ID)
        state = await self.load({'secondBodyRoot': True})
        self.assertIsNone(state.get('owner'))

    async def test_old_alternate_owner_is_never_used(self):
        state = await self.load({'providers': [], 'alternateUid': OWNER_ID})
        self.assertIsNone(state.get('owner'))

    async def test_anchor_hash_must_be_unique(self):
        state = await self.load({'anchorNames': ['__reactContainer$hashA', '__reactContainer$hashB']})
        self.assertIsNone(state.get('owner'))

    async def test_multiple_current_provider_owners_are_ambiguous(self):
        state = await self.load({'providers': [{'uid': OWNER_ID}, {'uid': '77123456001'}]})
        self.assertIsNone(state.get('owner'))

    async def test_full_tree_must_finish_within_bound_and_without_repeated_nodes(self):
        state = await self.load({'totalNodes': 4097, 'providers': [{'uid': OWNER_ID}]})
        self.assertIsNone(state.get('owner'))
        state = await self.load({'siblingCycle': True, 'providers': [{'uid': OWNER_ID}]})
        self.assertIsNone(state.get('owner'))

    async def test_all_source_login_modes_are_accepted_only_with_loaded_current_user(self):
        for login_type in ('personal', 'oversea_mobile_personal', 'org', 'mcn', 'confer', 'open_platform'):
            with self.subTest(login_type=login_type):
                state = await self.load({'providers': [{'uid': OWNER_ID, 'loginType': login_type}]})
                self.assertEqual(state.get('owner'), OWNER_ID)
        for login_type in ('', 'unknown', None, 'not_login'):
            with self.subTest(login_type=login_type):
                state = await self.load({'providers': [{'uid': OWNER_ID, 'loginType': login_type}]})
                self.assertIsNone(state.get('owner'))
                if login_type == 'not_login':
                    self.assertTrue(state.get('loggedOut'))

    async def test_loading_schema_and_auth_methods_fail_closed(self):
        cases = (
            {'userLoading': True},
            {'omitKeys': ['userLoading']},
            {'omitKeys': ['fastUserInfo']},
            {'omitKeys': ['permissions']},
            {'omitKeys': ['setUserInfo']},
            {'omitKeys': ['updateUserInfo']},
            {'extraKeys': ['unexpected']},
            {'nonFunctionMethods': ['setUserInfo']},
            {'nonFunctionMethods': ['updateUserInfo']},
        )
        for spec in cases:
            with self.subTest(spec=spec):
                state = await self.load({'providers': [{'uid': OWNER_ID, **spec}]})
                self.assertIsNone(state.get('owner'))

    async def test_initial_not_login_while_user_loading_is_pending(self):
        state = await self.load({'providers': [{'uid': OWNER_ID, 'loginType': 'not_login', 'userLoading': True}]})
        self.assertIsNone(state.get('owner'))
        self.assertFalse(state.get('loggedOut'))
        state = await self.load({'providers': [{'uid': OWNER_ID, 'loginType': 'not_login', 'userLoading': False}]})
        self.assertTrue(state.get('loggedOut'))

    async def test_only_userinfo_uid_is_authoritative_and_uid_shape_is_strict(self):
        for invalid in ('0', '-1', 'null', 'undefined', '', 0, -1, False, True, None, {}, []):
            with self.subTest(uid=invalid):
                state = await self.load({'providers': [{'uid': invalid, 'fastUid': OWNER_ID}]})
                self.assertIsNone(state.get('owner'))
        state = await self.load({'providers': [{'userInfo': None, 'fastUid': OWNER_ID}]})
        self.assertIsNone(state.get('owner'))
        state = await self.load({'providers': [{'uid': '1' + '2' * 39}]})
        self.assertIsNotNone(state.get('owner'))
        state = await self.load({'providers': [{'uid': '1' + '2' * 40}]})
        self.assertIsNone(state.get('owner'))
        state = await self.load({'providers': [{'uid': 123456789}]})
        self.assertEqual(state.get('owner'), '123456789')
        state = await self.load({'providers': [{'uid': 9007199254740992}]})
        self.assertIsNone(state.get('owner'))

    async def test_display_name_is_not_identity_and_logout_or_global_conflicts_reject(self):
        state = await self.load({'providers': [{'uid': OWNER_ID, 'omitUserInfoKeys': ['nickname']}]})
        self.assertEqual(state.get('owner'), OWNER_ID)
        state = await self.load({'providers': [{'uid': OWNER_ID, 'userInfoFields': {'isLogin': False}}]})
        self.assertIsNone(state.get('owner'))
        state = await self.load({'globalOwner': '77123456001'})
        self.assertIsNone(state.get('owner'))
        state = await self.load({'globalIsLogin': False})
        self.assertTrue(state.get('loggedOut'))

    async def test_route_and_top_window_gates_are_preserved(self):
        state = await self.load(url='https://creator.douyin.com/creator-micro/content/post/video/?enter_from=home_draft')
        self.assertIsNone(state.get('owner'))
        state = await self.load(url='https://creator.douyin.com/creator-micro/content/other')
        self.assertIsNone(state.get('owner'))
        state = await self.load(url='https://creator.douyin.com.evil.example/creator-micro/content/post/video?x=1')
        self.assertIsNone(state.get('owner'))
        state = await self.load(url='http://creator.douyin.com/creator-micro/content/post/video?x=1')
        self.assertIsNone(state.get('owner'))

        await self.page.goto(EDITOR_URL + '&fixture_case=iframe_parent')
        iframe_html = _fixture_html()
        owner_script = (ROOT / 'ui-assets/session-owner.js').read_text().strip()
        evaluation = '<script>document.body.dataset.ownerProbe=JSON.stringify((' + owner_script + ')("douyin"));</script>'
        iframe_html = iframe_html.replace('</body>', evaluation + '</body>')
        await self.page.evaluate('''html => {
          const frame = document.createElement('iframe');
          frame.id = 'nested-owner-fixture';
          frame.srcdoc = html;
          document.body.appendChild(frame);
        }''', iframe_html)
        await self.page.wait_for_function("document.querySelector('#nested-owner-fixture')?.contentDocument?.body?.dataset.ownerProbe")
        nested_state = json.loads(await self.page.evaluate(
            "document.querySelector('#nested-owner-fixture').contentDocument.body.dataset.ownerProbe"))
        self.assertIsNone(nested_state.get('owner'))

    async def test_upload_route_accepts_the_same_canonical_provider(self):
        state = await self.load(url='https://creator.douyin.com/creator-micro/content/upload')
        self.assertEqual(state.get('owner'), OWNER_ID)
        self.assertEqual(state.get('source'), 'douyin-current-user-provider')
        inspected = await inspect_session('douyin', self.page)
        self.assertTrue(inspected['verified'])
        self.assertEqual(inspected['uid'], OWNER_ID)


if __name__ == '__main__':
    unittest.main(verbosity=2)
