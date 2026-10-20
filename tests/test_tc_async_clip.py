"""Official asyncClip raw resource + allocation ticket fixtures; no real platform."""
import copy
import sys
import time
import unittest

from tests.test_tc_processing import (ProcessingProtocolTests, ProcessingNativeBrowserTests,
                               SOURCE, DERIVED, resource)
from uploader.draft_evidence import parse_record, verify_evidence
from uploader.publication_workflow import _candidate, PublicationBlocked
from uploader.tc_resource import tc_resource_identity


class AsyncClipProtocolTests(ProcessingProtocolTests):
    def pending_snapshot(self):
        self.state.update(mode='async_clip', processed_url=None, consumed_url=SOURCE,
                          url_cdn_task_id=self.ticket['draftId'])
        self.state['result'] = copy.deepcopy(self.state['origin'])
        self.state['consumed'] = copy.deepcopy(self.state['origin'])

    async def pending(self, *, flag=2):
        self.pending_snapshot()
        await self.allocate()
        await self.finish(data={'errCode': 0, 'data': {'flag': flag}})
        await self.observer.drain()

    def saved_record(self, task=None):
        value = parse_record('tencent', {'objectId': 'fixture-saved-record', 'postReq': {
            'objectDesc': {'description': 'fixture caption', 'media': [{
                'url': SOURCE, 'urlCdnTaskId': task or self.ticket['draftId'],
                'thumbUrl': resource('automatic-cover')}]} }}, owner=self.session.account['uid'])
        value['fresh'] = True
        return value

    async def prepared_saved(self, candidate):
        self.session.expected.update(platform_fields={'caption': 'fixture caption'}, cover_sha256={})
        self.session.saves, self.session.issues, self.session.exchanges = [candidate], [], []
        self.session.action_at, self.session.reopen_at = time.time(), None
        self.session.upload_links = lambda: self.observer.augment_links(self.links)
        async def account():
            return copy.deepcopy(self.session.account)
        self.session.read_account = account

    async def test_flag_two_is_pending_not_failed_or_synchronous_completion(self):
        await self.pending()
        result = self.observer.records[-1]
        self.assertTrue(result['pending'])
        self.assertFalse(result['failed'])
        self.assertFalse(result['valid'])
        self.assertEqual(self.observer.current_publication_resource(self.links), tc_resource_identity(SOURCE))
        self.assertEqual(self.observer.augment_links(self.links), self.links)
        self.assertNotIn(tc_resource_identity(DERIVED), self.observer.augment_links(self.links)[0]['ids'])

    async def test_async_allocation_allows_native_ticket_before_first_poll(self):
        self.pending_snapshot()
        await self.allocate()
        await self.observer.drain()
        self.assertEqual(self.observer.current_publication_resource(self.links), tc_resource_identity(SOURCE))

    async def test_failed_unknown_or_conflicting_terminal_never_permits_async_input(self):
        original = copy.deepcopy(self.state)
        for flag in (3, 4, 0, 1, True, '2', None):
            with self.subTest(flag=flag):
                self.observer.records.clear()
                self.state = copy.deepcopy(original)
                await self.pending(flag=flag)
                self.assertIsNone(self.observer.current_publication_resource(self.links))

    async def test_exact_source_native_sha_metadata_and_clip_ticket_remain_required(self):
        await self.pending()
        original = copy.deepcopy(self.state)
        for mode in ('sha', 'ticket', 'task', 'source', 'consumed', 'size', 'double-selection', 'no-upload-proof'):
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(original)
                old_files = copy.deepcopy(self.session.files)
                if mode == 'sha': self.state['sha256'] = '0' * 64
                if mode == 'ticket': self.state['ticket']['clipKey'] = 'foreign-key'
                if mode == 'task': self.state['url_cdn_task_id'] = 'foreign-task'
                if mode == 'source': self.state['source_url'] = resource('foreign-source')
                if mode == 'consumed': self.state['consumed_url'] = DERIVED
                if mode == 'size': self.state['consumed']['fileSize'] += 1
                if mode == 'double-selection': self.session.files.append(copy.deepcopy(self.session.files[0]))
                await self.observer.drain()
                self.assertIsNone(self.observer.current_publication_resource([] if mode == 'no-upload-proof' else self.links))
                self.session.files = old_files

    async def test_publication_candidate_uses_only_verified_current_async_input(self):
        await self.pending()
        expected = {**self.session.expected, 'cover_sha256': {}}
        self.assertEqual(_candidate('tencent', expected, self.session, self.links, self.links)['video_ids'],
                         [tc_resource_identity(SOURCE)])
        self.state['url_cdn_task_id'] = 'foreign-task'
        await self.observer.drain()
        with self.assertRaises(PublicationBlocked):
            _candidate('tencent', expected, self.session, self.links, self.links)

    async def test_saved_async_payload_freezes_and_reopens_only_same_media_task(self):
        await self.pending()
        candidate = self.saved_record()
        await self.prepared_saved(candidate)
        self.assertTrue(await self.observer.freeze_for_reopen(candidate))
        self.attest_normal_reopen(candidate)
        self.state = None
        await self.observer.drain()
        reopened = copy.deepcopy(candidate)
        checks, _ = verify_evidence('tencent', self.session.expected, candidate,
            self.session.upload_links(), self.session.account, reopened=reopened,
            ui_fields=True, account_after=self.session.account)
        self.assertTrue(all(checks.values()))
        reopened['tc_media_tasks'][0]['draft_id'] = 'foreign-task'
        checks, _ = verify_evidence('tencent', self.session.expected, candidate,
            self.session.upload_links(), self.session.account, reopened=reopened,
            ui_fields=True, account_after=self.session.account)
        self.assertFalse(checks['reopened'])

    async def test_saved_wrong_task_or_missing_task_cannot_freeze_async_proof(self):
        await self.pending()
        for mode in ('wrong', 'missing'):
            with self.subTest(mode=mode):
                candidate = self.saved_record('foreign-task' if mode == 'wrong' else None)
                if mode == 'missing': del candidate['tc_media_tasks']
                await self.prepared_saved(candidate)
                self.assertFalse(await self.observer.freeze_for_reopen(candidate))

    async def test_bound_inflight_pending_poll_keeps_frozen_proof_but_new_error_revokes_it(self):
        await self.pending()
        candidate = self.saved_record()
        await self.prepared_saved(candidate)
        self.assertTrue(await self.observer.freeze_for_reopen(candidate))
        self.attest_normal_reopen(candidate)
        self.state = None
        await self.finish(data={'errCode': 0, 'data': {'flag': 2}}, when=time.time() + .01)
        await self.observer.drain()
        self.assertIsNotNone(self.observer._frozen_reopen)
        self.assertEqual(self.session.upload_links(), self.links)
        self.assertIsNotNone(self.observer._frozen_reopen)
        await self.finish(data={'errCode': 0, 'data': {'flag': 3}}, when=time.time() + .02)
        self.session.upload_links()
        self.assertIsNone(self.observer._frozen_reopen)

    async def test_synchronous_complete_path_still_requires_result_one(self):
        await self.allocate()
        await self.finish(data={'errCode': 0, 'data': {'flag': 2}})
        await self.observer.drain()
        self.assertIsNone(self.observer.current_publication_resource(self.links))
        await self.finish()
        await self.observer.drain()
        self.assertEqual(self.observer.current_publication_resource(self.links), tc_resource_identity(DERIVED))


class AsyncClipNativeTests(ProcessingNativeBrowserTests):
    async def async_fixture(self):
        await self.js('''delete fixture.file.processedResponse;
          fixture.vm.postStore.asyncClip=true;
          fixture.media.url=fixture.file.response.httpsUrl;
          fixture.media.urlCdnTaskId=fixture.vm.postStore.createPostStatus.clipTicket.draftId;''')
        await self.allocate()
        await self.finish(data={'errCode': 0, 'data': {'flag': 2}})

    async def test_actual_native_file_raw_consumer_and_task_hash_without_business_calls(self):
        await self.async_fixture()
        await self.observer.drain()
        self.assertEqual(self.observer.native['mode'], 'async_clip')
        self.assertEqual(self.observer.native['sha256'], self.sha)
        self.assertIsNone(self.observer.native['processed_url'])
        self.assertEqual(self.observer.current_publication_resource(self.links), tc_resource_identity(SOURCE))
        self.assertEqual(await self.js('fixture.businessCalls'), 0)

    async def test_native_async_switch_task_resource_metadata_and_forged_file_fail_closed(self):
        await self.async_fixture()
        for change, restore in [
            ('fixture.vm.postStore.asyncClip=false', 'fixture.vm.postStore.asyncClip=true'),
            ('fixture.vm.postStore.asyncClip=1', 'fixture.vm.postStore.asyncClip=true'),
            ('fixture.media.urlCdnTaskId="foreign"', 'fixture.media.urlCdnTaskId="fixture-draft-id"'),
            ('fixture.media.url="https://invalid.example/foreign"', 'fixture.media.url=fixture.file.response.httpsUrl'),
            ('fixture.media.fileSize++', 'fixture.media.fileSize--'),
            ('fixture.file.status="croping"', 'fixture.file.status="done"'),
            ('fixture.file.originFileObj={name:"fixture.mp4",size:fixture.file.size}', None),
        ]:
            with self.subTest(change=change):
                await self.js(change)
                await self.observer.drain()
                self.assertIsNone(self.observer.native)
                self.assertIsNone(self.observer.current_publication_resource(self.links))
                if restore is not None: await self.js(restore)
        self.assertEqual(await self.js('fixture.businessCalls'), 0)


def load_tests(loader, tests, pattern):
    # Reuse setup machinery, not the inherited regression test methods.
    return unittest.TestSuite(loader.loadTestsFromName(f'{cls.__name__}.{name}', sys.modules[__name__])
        for cls in (AsyncClipProtocolTests, AsyncClipNativeTests)
        for name in cls.__dict__ if name.startswith('test_'))
