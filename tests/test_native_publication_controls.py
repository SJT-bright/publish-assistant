"""Official native contracts and controls, with synthetic responses/pages only."""
import unittest
from unittest.mock import patch

from patchright.async_api import async_playwright
from uploader import publish_action
from tests.test_publish_action import FakeFrame, FakePage, FakeButton, FakeRequest, FakeResponse, _install_preflight


class DouyinNativeContractTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_https_native_contract(self):
        endpoint = 'https://creator.douyin.com/web/api/media/aweme/create_v2/'
        self.assertEqual(publish_action._documented_contract_url(endpoint), 'douyin_web')
        self.assertEqual(publish_action._documented_contract_url(endpoint[:-1] + '?read_aid=2906'), 'douyin_web')
        for url in (endpoint.replace('https:', 'http:'), endpoint.replace('creator.douyin.com', 'evil.invalid'),
                    endpoint.replace('creator.douyin.com', 'creator.douyin.com:8443'),
                    endpoint.replace('creator.douyin.com', 'username@creator.douyin.com'),
                    endpoint + '/', endpoint.replace('create_v2/', 'create/'), endpoint + 'unexpected'):
            with self.subTest(url=url):
                self.assertIsNone(publish_action._documented_contract_url(url))

    def test_raw_code_and_positive_canonical_item_id(self):
        for identifier in ('7620000000000000001', 7620000000000000001):
            self.assertEqual(publish_action._extract_documented_id('douyin_web',
                             {'status_code': 0, 'item_id': identifier}), str(identifier))
        invalid = [None, [], {}, {'status_code': 0, 'data': {'item_id': '7620000000000000001'}},
                   {'status_code': 0, 'aweme': {'aweme_id': '7620000000000000001'}},
                   {'status_code': 0, 'item_id': '7620000000000000001', 'status_msg': "url doesn't match"}]
        invalid += [{'status_code': code, 'item_id': '7620000000000000001'} for code in
                    (False, True, 0.0, '0', 1, 514, 515, None)]
        invalid += [{'status_code': 0, 'item_id': identifier} for identifier in
                    ('', '0', '00012', ' 12', '12 ', '+12', '-12', '1.2', 'fixture-id', False, True, 0, -1, 1.0, [], {})]
        for body in invalid:
            with self.subTest(body=body):
                self.assertIsNone(publish_action._extract_documented_id('douyin_web', body))

    async def test_one_native_response_is_correlated_with_this_douyin_click(self):
        frame = FakeFrame('https://creator.douyin.com/creator-micro/content/post/video')
        page = FakePage(frame)
        def submit():
            request = FakeRequest('https://creator.douyin.com/web/api/media/aweme/create_v2/?read_aid=2906', frame)
            page.emit('request', request)
            page.emit('response', FakeResponse(request, {'status_code': 0, 'item_id': '7620000000000000001'}))
        button = FakeButton(submit)
        inspect_patch, locate_patch = _install_preflight('douyin', page, button)
        with inspect_patch, locate_patch:
            result = await publish_action.publish_current(page, 'douyin', receipt_timeout=.02)
        self.assertEqual(result['status'], publish_action.PUBLISHED_SUBMISSION_VERIFIED)
        self.assertEqual(result['evidence']['submission_id'], '7620000000000000001')
        self.assertEqual(result['observation'], {'publish_requests': 1, 'publish_responses': 1})
        self.assertEqual(button.clicks, 1)
        self.assertFalse(page.listeners['request'])
        self.assertFalse(page.listeners['response'])

    async def test_invalid_http_json_or_wrong_platform_remains_unverified_without_retry(self):
        cases = [('douyin', {'status_code': 0, 'item_id': '7620000000000000001'}, 500, None),
                 ('douyin', {'status_code': 0, 'item_id': '7620000000000000001'}, 200, {'content-type': 'text/html'}),
                 ('douyin', {'status_code': 0, 'item_id': '7620000000000000001', 'status_msg': "url doesn't match"}, 200, None),
                 ('xiaohongshu', {'status_code': 0, 'item_id': '7620000000000000001'}, 200, None)]
        for platform, body, status, headers in cases:
            with self.subTest(platform=platform, status=status, headers=headers):
                frame = FakeFrame('https://creator.douyin.com/creator-micro/content/post/video' if platform == 'douyin'
                                  else 'https://creator.xiaohongshu.com/publish/publish')
                page = FakePage(frame)
                def submit():
                    request = FakeRequest('https://creator.douyin.com/web/api/media/aweme/create_v2/', frame)
                    page.emit('request', request)
                    page.emit('response', FakeResponse(request, body, status=status, headers=headers))
                button = FakeButton(submit)
                inspect_patch, locate_patch = _install_preflight(platform, page, button)
                with inspect_patch, locate_patch:
                    result = await publish_action.publish_current(page, platform, receipt_timeout=.02)
                self.assertEqual(result['status'], publish_action.PUBLISHED_UNVERIFIED)
                self.assertEqual(button.clicks, 1)
                self.assertEqual(result['evidence'], {})


KS_FIXTURE = '''<meta charset="utf-8"><body><div class="_edit-section-form_ql0z6_100">
<div contenteditable="true" style="height:60px;width:300px">fixture caption</div>
<video src="https://fixture.invalid/video.mp4" style="width:300px;height:180px"></video>
<div class="_edit-section-btns_ql0z6_118"><div id="publish" class="_button_3a3lq_1 _button-primary_3a3lq_60"
 style="width:96px;height:36px"><div>发布</div></div></div></div>
<div class="ant-modal-confirm-centered" style="display:none;width:240px;height:100px">
<button>确认发布</button></div><script>
window.initialClicks=0;window.confirmClicks=0;
window.submitFixture=()=>fetch('/rest/cp/works/v3/video/pc/submit',{method:'POST',body:'fixture'});
document.querySelector('#publish').onclick=()=>{window.initialClicks++;window.submitFixture();};
</script></body>'''


class KuaishouNativeControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chromium')
        self.page = await self.browser.new_page()
        async def fixture_route(route):
            if route.request.method == 'POST':
                await route.fulfill(content_type='application/json', body='{"result":1,"data":{"photo_id":"unknown-native-contract"}}')
            else:
                await route.fulfill(content_type='text/html', body=KS_FIXTURE)
        await self.page.route('**/*', fixture_route)
        await self.page.goto('https://cp.kuaishou.com/article/publish/video')
        self.account = patch.object(publish_action, 'inspect_session', return_value={'verified': True, 'identity': 'fixture-owner'})
        self.account.start()

    async def asyncTearDown(self):
        self.account.stop()
        await self.browser.close()
        await self.playwright.stop()

    async def run_publish(self):
        return await publish_action.publish_current(self.page, 'kuaishou', receipt_timeout=.08)

    async def test_native_div_produces_exactly_one_submit(self):
        self.assertEqual(await self.page.get_by_role('button', name='发布', exact=True).count(), 0)
        candidate, error = await publish_action._locate_current_editor(self.page, 'kuaishou')
        self.assertIsNone(error)
        self.assertIsNotNone(candidate)
        result = await self.run_publish()
        self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 1)
        self.assertEqual(result['observation'], {'publish_requests': 1, 'publish_responses': 1})
        self.assertEqual(result['status'], publish_action.PUBLISHED_UNVERIFIED)
        self.assertTrue(result['clicked'])

    async def test_role_button_remains_supported_only_in_its_editor_form(self):
        await self.page.evaluate('''() => {
          document.querySelector('#publish').outerHTML='<button id="publish">发布</button>';
          document.querySelector('#publish').onclick=()=>{window.initialClicks++;window.submitFixture();};
        }''', isolated_context=False)
        result = await self.run_publish()
        self.assertTrue(result['clicked'])
        self.assertEqual(result['observation']['publish_requests'], 1)
        self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 1)
        await self.page.reload()
        await self.page.evaluate('''() => {
          document.querySelector('#publish').outerHTML='<button id="publish">发布</button>';
          document.body.append(document.querySelector('#publish'));
        }''', isolated_context=False)
        result = await self.run_publish()
        self.assertEqual(result['status'], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(result['code'], 'final_control_disabled')
        self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 0)

    async def test_ambiguous_exact_native_labels_block_without_click(self):
        await self.page.evaluate("document.querySelector('#publish').after(document.querySelector('#publish').cloneNode(true))", isolated_context=False)
        result = await self.run_publish()
        self.assertEqual(result['status'], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(result['code'], 'final_control_not_unique')
        self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 0)

    async def test_disabled_loading_aria_pointer_and_uncompleted_declaration_block(self):
        for mutation in ("classList.add('_disabled_3a3lq_10')", "setAttribute('aria-disabled','true')",
                         "setAttribute('aria-busy','true')", "style.pointerEvents='none'", "style.opacity='.5'"):
            with self.subTest(mutation=mutation):
                await self.page.reload()
                await self.page.evaluate("document.querySelector('#publish')." + mutation, isolated_context=False)
                result = await self.run_publish()
                self.assertEqual(result['status'], publish_action.PUBLICATION_BLOCKED)
                self.assertEqual(result['code'], 'final_control_disabled')
                self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 0)

    async def test_native_control_outside_corresponding_form_or_multiple_forms_block(self):
        await self.page.evaluate("document.body.append(document.querySelector('#publish'))", isolated_context=False)
        self.assertEqual((await self.run_publish())['status'], publish_action.PUBLICATION_BLOCKED)
        await self.page.reload()
        await self.page.evaluate("document.body.append(document.querySelector('[class*=edit-section-form]').cloneNode(true));document.querySelectorAll('#publish')[1].remove()", isolated_context=False)
        self.assertEqual((await self.run_publish())['status'], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 0)

    async def test_exact_native_confirmation_runs_once_after_initial_div(self):
        await self.page.evaluate('''() => {
          const modal=document.querySelector('.ant-modal-confirm-centered');
          document.querySelector('#publish').onclick=()=>{window.initialClicks++;modal.style.display='block';};
          modal.querySelector('button').onclick=()=>{window.confirmClicks++;window.submitFixture();};
        }''', isolated_context=False)
        result = await self.run_publish()
        self.assertEqual(result['observation'], {'publish_requests': 1, 'publish_responses': 1})
        self.assertEqual(await self.page.evaluate('[window.initialClicks,window.confirmClicks]', isolated_context=False), [1,1])
        self.assertTrue(result['clicked'])

    async def test_unknown_confirmation_does_not_retry_initial_control(self):
        await self.page.evaluate('''() => {
          const modal=document.querySelector('.ant-modal-confirm-centered');
          document.querySelector('#publish').onclick=()=>{window.initialClicks++;modal.style.display='block';};
          modal.querySelector('button').innerText='未知确认';
        }''', isolated_context=False)
        result = await self.run_publish()
        self.assertEqual(result['observation']['publish_requests'], 0)
        self.assertEqual(result['status'], publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(await self.page.evaluate('[window.initialClicks,window.confirmClicks]', isolated_context=False), [1,0])

    async def test_visible_modal_before_initial_control_blocks(self):
        await self.page.evaluate("document.querySelector('.ant-modal-confirm-centered').style.display='block'", isolated_context=False)
        result = await self.run_publish()
        self.assertEqual(result['status'], publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(result['code'], 'confirmation_control_contract_unknown')
        self.assertEqual(await self.page.evaluate('window.initialClicks', isolated_context=False), 0)
