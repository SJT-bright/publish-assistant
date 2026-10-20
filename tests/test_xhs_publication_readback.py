"""Fresh XHS native File / typed Vue / Tiptap fixtures; all requests intercepted."""
import copy
import hashlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests import test_xhs_readback as readback_fixtures
from uploader import publication_workflow
from uploader.draft_evidence import IDENTITY_KEYS
from uploader.native_read import NativeReadError, canonical_root_evaluate
from uploader.xhs_evidence import install_xhs_observer
from uploader.xhs_publication_readback import read_xhs_fresh_publication
from uploader.xiaohongshu_uploader.main import draft_xhs_description_plan


class XhsFreshPublicationTests(unittest.IsolatedAsyncioTestCase):
    js = readback_fixtures.XhsReadbackTests.js
    owner_server_source_fixture = readback_fixtures.XhsReadbackTests.owner_server_source_fixture
    reset_session = readback_fixtures.XhsReadbackTests.reset_session

    async def asyncSetUp(self):
        await readback_fixtures.XhsReadbackTests.asyncSetUp(self)
        self.expected = {key: 'isolated-' + key for key in IDENTITY_KEYS}
        self.expected.update(platform_id='xiaohongshu', workflow_action='publish',
            video_sha256=hashlib.sha256(b'isolated-current-native-video').hexdigest(),
            cover_sha256={'cover': 'c' * 64}, platform_fields=copy.deepcopy(self.fields), account=copy.deepcopy(self.account))
        self.session.expected = copy.deepcopy(self.expected)
        self.session.started_at = time.time() - 1
        self.session.issues = []
        self.session.assets = {'video': {'kind': 'video', 'name': 'fixture.mp4', 'size': 29,
                                       'sha256': self.expected['video_sha256']},
                               'cover': {'kind': 'cover', 'name': 'fixture.png', 'size': 10, 'sha256': 'c' * 64}}
        self.session.files = [{'name': 'fixture.mp4', 'size': 29, 'type': 'video/mp4',
                               'sha256': self.expected['video_sha256'], 'selected_at': time.time()}]
        del self.session.normal_ui_binding
        del self.session.normal_ui_candidate
        self.candidate = {'video_ids': ['video-resource'], 'video_count': 1,
                          'cover_ids': ['cover-resource'], 'cover_slots': {'cover': ['cover-resource']}}
        await self.js('''()=>{
            const entries=new Map();class Manager {
                getUploader(name){return entries.get(name)||null;}
                createUploader(name){if(!entries.has(name))entries.set(name,{upload(file){
                    fixture.native=file;return Promise.resolve({fileId:'video-resource'});}});return entries.get(name);}
            }window._publisher={uploader:new Manager()};fixture.manager=_publisher.uploader;
        }''')
        self.observer = await install_xhs_observer(self.session)
        await self.js('''()=>_publisher.uploader.createUploader('video-uploader').upload(
            new File(['isolated-current-native-video'],'fixture.mp4',{type:'video/mp4'}))''')
        await self.observer.drain()
        self.assertEqual(len(self.observer.records), 1)
        self.observer.cover_records = {'fixture-cover': {'slot': 'cover', 'origin_sha256': 'c' * 64,
            'file_id': 'cover-resource', 'revision': '1', 'origin_name': 'fixture.png', 'origin_size': 10,
            'origin_type': 'image/png', 'selected_at': time.time()}}
        cover = self.observer.cover_records['fixture-cover']
        self.session.files.append({'name': 'fixture.png', 'size': 10, 'type': 'image/png', 'sha256': 'c' * 64,
                                   'selected_at': cover['selected_at']})
        self.observer.cover_status = {'selection': True, 'revision': '1', 'applied_id': 'cover-resource'}
        self.session.upload_links = self.observer.upload_links
        self.session.drain = AsyncMock()
        self.session._checkpoint_assets = AsyncMock()
        self.session.read_account = AsyncMock(side_effect=lambda: copy.deepcopy(self.session.account))
        await self.owner_server_source_fixture()
        await self.js('()=>{fixture.mutations=0;}')
        self.before_requests = list(self.requests)

    async def asyncTearDown(self):
        if hasattr(self, 'observer'):
            await self.observer.close()
        await readback_fixtures.XhsReadbackTests.asyncTearDown(self)

    async def read(self):
        return await read_xhs_fresh_publication(self.page, self.expected, self.candidate)

    async def owner_fixture(self):
        await readback_fixtures.XhsReadbackTests.owner_fixture(self)
        # The official onUpdate serializes topic atoms into [话题] and the
        # modifyContent command trims only its state value, not the live DOM.
        serialized = draft_xhs_description_plan(self.fields['description'], self.fields['tags'])['serialized']
        await self.js('()=>{ownerFixture.state.desc.value=' + json.dumps(serialized) + '.trim();}')

    async def test_current_native_sdk_server_handoff_and_rich_topics_pass_without_reopen(self):
        with patch.object(self.page, 'evaluate', AsyncMock(side_effect=AssertionError('library_fallback_forbidden'))):
            result = await self.read()
        self.assertEqual(result, {'valid': True, 'fields_ok': True, 'media_ok': True,
                                  'cover_ok': True, 'reason': 'none'})
        self.assertEqual(await self.js('()=>fixture.business'), 0)
        self.assertEqual(await self.js('()=>fixture.mutations'), 0)
        self.assertEqual(self.requests, self.before_requests)
        self.assertEqual(self.session.saves, [])

    async def test_local_blob_props_still_need_exact_current_server_id_and_native_upload(self):
        await self.owner_fixture()
        self.assertTrue((await self.read())['media_ok'])
        await self.js("()=>{ownerFixture.state.video.value.fileid='foreign';}")
        self.assertFalse((await self.read())['media_ok'])

    async def test_state_uses_official_serialized_trimmed_topics_while_dom_whitespace_remains_exact(self):
        self.expected['platform_fields']['description'] = '  ' + self.fields['description'] + '  '
        self.session.expected = copy.deepcopy(self.expected)
        await self.js('''()=>{
            const editor=document.querySelector('#editor');editor.firstChild.firstChild.data='  '+editor.firstChild.firstChild.data;
            editor.lastChild.append(document.createTextNode('  '));
        }''')
        result = await self.read()
        self.assertTrue(result['fields_ok']); self.assertTrue(result['media_ok'])
        await self.js('()=>{ownerFixture.state.desc.value=' + json.dumps(self.fields['description']) + ';}')
        self.assertFalse((await self.read())['media_ok'])

    async def test_blob_alone_detached_exposed_element_or_wrong_render_branch_is_rejected(self):
        for change in ("ownerFixture.player.exposed.video.value=document.createElement('video')",
                       "ownerFixture.player.appContext.app={}",
                       "ownerFixture.state.videoBlobUrl.value='blob:https://creator.xiaohongshu.com/foreign'",
                       "ownerFixture.preview.props={}",
                       "ownerFixture.playerNode.type.__scopeId='foreign'",
                       "ownerFixture.sdk.config.url='https://fixture.test/foreign'",
                       "ownerFixture.preview.subTree.children.push(ownerFixture.playerNode)"):
            with self.subTest(change=change):
                await self.owner_server_source_fixture()
                await self.js('()=>{' + change + ';}')
                self.assertFalse((await self.read())['media_ok'])

    async def test_wrong_title_topic_atom_and_duplicate_title_are_rejected(self):
        for change in ("document.querySelector('input').value='wrong'",
                       "document.querySelector('a').setAttribute('data-topic','{}')",
                       "document.querySelector('input').after(document.querySelector('input').cloneNode())"):
            with self.subTest(change=change):
                await self.js('()=>{' + change + ';}')
                self.assertFalse((await self.read())['fields_ok'])
                # Restore only the altered title/topic nodes; replacing the
                # publisher root would intentionally invalidate ownership.
                await self.js('''()=>{
                    for(const e of [...document.querySelectorAll('input')].slice(1))e.remove();
                    document.querySelector('input').value='隔离标题';
                    document.querySelector('a').setAttribute('data-topic','{"id":"synthetic-topic-A","name":"科普"}');
                }''')

    async def test_native_hash_file_id_new_selection_scope_and_unknown_manager_fail_closed(self):
        baseline = copy.deepcopy(self.observer.records)
        selected = copy.deepcopy(self.session.files)
        for change in ('hash', 'fileid', 'selection', 'scope', 'manager'):
            with self.subTest(change=change):
                self.observer.records = copy.deepcopy(baseline)
                self.session.files = copy.deepcopy(selected)
                await self.js('()=>{_publisher.uploader=fixture.manager;}')
                await self.js('()=>{__sauXHSSDKObserver.active=' + json.dumps(self.observer.browser_scope) + ';}')
                if change == 'hash': next(iter(self.observer.records.values()))['sha256'] = '0' * 64
                elif change == 'fileid': next(iter(self.observer.records.values()))['file_id'] = 'foreign'
                elif change == 'selection': self.session.files.append({**selected[0], 'selected_at': time.time() + 1})
                elif change == 'scope': await self.js("()=>{__sauXHSSDKObserver.active={...__sauXHSSDKObserver.active,receipt_nonce:'foreign'};}")
                elif change == 'manager': await self.js('()=>{_publisher.uploader={};}')
                self.assertFalse((await self.read())['media_ok'])

    async def test_current_applied_cover_and_visible_resource_both_required(self):
        self.observer.cover_status['applied_id'] = 'foreign'
        self.assertFalse((await self.read())['media_ok'])
        self.observer.cover_status['applied_id'] = 'cover-resource'
        await self.js("()=>{document.querySelector('#cover').style.backgroundImage='url(https://fixture.test/foreign)';}")
        result = await self.read()
        self.assertTrue(result['fields_ok']); self.assertFalse(result['cover_ok']); self.assertFalse(result['media_ok'])

    async def test_video_only_fresh_reader_keeps_reopen_cover_contract_unchanged(self):
        self.expected['cover_sha256'] = {}
        self.session.expected = copy.deepcopy(self.expected)
        self.candidate['cover_ids'] = []; self.candidate['cover_slots'] = {}
        self.observer.cover_records = {}; self.observer.cover_status = None
        result = await self.read()
        self.assertTrue(result['media_ok']); self.assertTrue(result['fields_ok']); self.assertTrue(result['cover_ok'])

    async def test_session_observer_source_account_and_receipt_changes_during_read_are_rejected(self):
        for change in ('observer', 'records', 'file', 'account', 'nonce', 'candidate'):
            with self.subTest(change=change):
                old = (self.session.xhs_observer, copy.deepcopy(self.observer.records), copy.deepcopy(self.session.files),
                       copy.deepcopy(self.session.account), copy.deepcopy(self.session.expected), copy.deepcopy(self.candidate))
                async def mutate(*args, **kwargs):
                    result = await canonical_root_evaluate(*args, **kwargs)
                    if change == 'observer': self.session.xhs_observer = SimpleNamespace()
                    elif change == 'records': self.observer.records.clear()
                    elif change == 'file': self.session.files[0]['sha256'] = '0' * 64
                    elif change == 'account': self.session.account['identity'] = 'foreign'
                    elif change == 'nonce': self.session.expected['receipt_nonce'] = 'foreign'
                    elif change == 'candidate': self.candidate['video_ids'] = ['foreign']
                    return result
                with patch('uploader.xhs_publication_readback.canonical_root_evaluate', side_effect=mutate):
                    self.assertEqual((await self.read())['reason'], 'fresh_scope_changed')
                (self.session.xhs_observer, self.observer.records, self.session.files,
                 self.session.account, self.session.expected, self.candidate) = old

    async def test_prepared_workflow_uses_typed_fresh_reader_instead_of_generic_text_or_blob(self):
        checks = dict.fromkeys(publication_workflow.PREPARED_CHECKS, False)
        with patch('uploader.draft_only.assert_draft_safe', AsyncMock()), \
                patch('uploader.draft_only._ui_fields', AsyncMock(side_effect=AssertionError('generic_fields_forbidden'))) as fields, \
                patch('uploader.draft_only._ui_assets', AsyncMock(side_effect=AssertionError('generic_assets_forbidden'))) as assets:
            account = await publication_workflow._verify_prepared(
                self.page, 'xiaohongshu', self.expected, self.session, checks)
        self.assertEqual(account, self.account)
        self.assertTrue(all(checks.values()))
        fields.assert_not_awaited(); assets.assert_not_awaited()

    async def test_foreign_document_and_native_read_failure_return_fixed_safe_reasons(self):
        with patch('uploader.xhs_publication_readback.canonical_root_evaluate',
                   AsyncMock(side_effect=NativeReadError('native_read_timeout'))):
            self.assertEqual((await self.read())['reason'], 'native_read_timeout')
        with patch('uploader.xhs_publication_readback.canonical_root_evaluate', AsyncMock()) as reader:
            await self.page.goto('https://creator.xiaohongshu.com/other')
            self.assertEqual((await self.read())['reason'], 'fresh_document_invalid')
            reader.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
