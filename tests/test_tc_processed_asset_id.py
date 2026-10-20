"""Frozen native GUID to processed server asset ID; synthetic contracts only."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_tc_reopen_mapping as fixture
from uploader.tc_assets import tc_reopened_assets_match
from uploader.tc_reopen_mapping import verify_processing_reopen

GUID = '12345678-abcd-1234-abcd-123456789abc'
SERVER_ID = 'b' * 32


class UUIDStageProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.MappingProtocolTests.asyncSetUp
    make_session = fixture.MappingProtocolTests.make_session
    allocate = fixture.MappingProtocolTests.allocate
    finish = fixture.MappingProtocolTests.finish
    attest_normal_reopen = fixture.MappingProtocolTests.attest_normal_reopen
    pending_snapshot = fixture.MappingProtocolTests.pending_snapshot
    pending = fixture.MappingProtocolTests.pending
    prepared_saved = fixture.MappingProtocolTests.prepared_saved
    ready = fixture.MappingProtocolTests.ready
    record = fixture.MappingProtocolTests.record

    async def proof(self, *, native=True):
        verifier = verify_processing_reopen
        with patch('uploader.tc_assets.tc_reopened_assets_match', new=AsyncMock(return_value=native)) as consumer:
            proof = await verifier(self.session, self.candidate, self.reopened, self.session.account)
        return proof, consumer

    async def test_stage_only_preserves_distinct_original_and_server_identity_and_original_sha(self):
        await self.ready(uuid=GUID)
        self.reopened['tc_media'][0]['uuid'] = SERVER_ID
        before = copy.deepcopy((self.candidate, self.reopened, self.session.upload_links(),
            self.observer._frozen_reopen))
        proof, consumer = await self.proof()
        self.assertIsNotNone(proof)
        consumer.assert_awaited_once_with(self.page, self.reopened,
            restored_from=self.candidate, strict_cover_media=True)
        self.assertEqual((self.candidate, self.reopened, self.session.upload_links(),
            self.observer._frozen_reopen), before)
        self.assertEqual(self.candidate['tc_media'][0]['uuid'], GUID)
        self.assertEqual(self.reopened['tc_media'][0]['uuid'], SERVER_ID)
        self.assertEqual(self.observer._frozen_reopen['native']['uuid'], GUID)
        links = self.session.upload_links()
        self.assertTrue(proof.matches(self.session.expected, self.candidate, self.reopened,
            links, self.session.account, self.session.account))
        self.assertFalse(any(set(value['ids']) & set(self.reopened['video_ids']) for value in links))
        self.reopened['tc_media'][0]['uuid'] = 'c' * 32
        self.assertFalse(proof.matches(self.session.expected, self.candidate, self.reopened,
            links, self.session.account, self.session.account))

    async def test_same_uuid_paths_remain_unchanged_for_guid_and_hex32(self):
        for uuid in (GUID, 'a' * 32):
            await self.asyncSetUp()
            await self.ready(uuid=uuid)
            proof, consumer = await self.proof()
            self.assertIsNotNone(proof)
            consumer.assert_awaited_once()

    async def test_forbidden_identity_shapes_and_missing_frozen_task_read_owner_sha_or_native_reject(self):
        for case in ('32-to-32', 'guid-to-guid', 'strip', 'uppercase', 'same-resource', 'task',
                     'owner', 'missing-frozen', 'missing-allocation', 'missing-normal-read',
                     'non-post', 'pending', 'old-read', 'full-sha', 'native-consumer'):
            with self.subTest(case=case):
                await self.asyncSetUp()
                await self.ready(uuid='a' * 32 if case == '32-to-32' else GUID)
                row = self.reopened['tc_media'][0]
                row['uuid'] = SERVER_ID
                if case == 'guid-to-guid':row['uuid'] = GUID[:-1] + 'd'
                if case == 'strip':row['uuid'] = GUID.replace('-', '')
                if case == 'uppercase':row['uuid'] = SERVER_ID.upper()
                if case == 'same-resource':
                    row['url'] = self.candidate['tc_media'][0]['url']
                    row['resource'] = self.candidate['tc_media'][0]['resource']
                    self.reopened['video_ids'] = [row['resource']]
                    self.reopened['tc_media_tasks'] = [{'resource': row['resource'], 'draft_id': row['task']}]
                if case == 'task':row['task'] = 'other-task'
                if case == 'owner':self.reopened['owner'] = 'other-owner'
                if case == 'missing-frozen':self.observer._frozen_reopen = None
                if case == 'missing-allocation':self.observer.records.clear()
                if case == 'missing-normal-read':self.session.reads.clear()
                if case == 'non-post':self.reopened['request_method'] = 'GET'
                if case == 'pending':self.reopened['tc_handle_status'] = 1
                if case == 'old-read':self.reopened['request_at'] = self.session.reopen_at - 1
                if case == 'full-sha':self.session.expected['video_sha256'] = '0' * 64
                proof, consumer = await self.proof(native=case != 'native-consumer')
                self.assertIsNone(proof)
                if case == 'native-consumer':consumer.assert_awaited_once()
                else:consumer.assert_not_awaited()


class CurrentNativeServerIDBrowserTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ExactRestoredConsumerTests.asyncSetUp
    asyncTearDown = fixture.ExactRestoredConsumerTests.asyncTearDown

    async def test_decoded_video_exact_server32_native_file_and_media_consumer(self):
        self.record['tc_media'][0]['uuid'] = SERVER_ID
        await self.page.evaluate('''id=>{
          fixture.file.uuid=id;
          fixture.material.postStore.postObjDesc.media[0].md5sum=id;
          fixture.file.thumbUrl=fixture.material.postStore.postObjDesc.media[0].thumbUrl;
        }''', SERVER_ID, isolated_context=False)
        async def check():
            return await tc_reopened_assets_match(self.page, self.record,
                restored_from=self.original, strict_cover_media=True)
        self.assertTrue(await check())
        for target, wrong in (('file', GUID), ('file', GUID.replace('-', '')),
                              ('media', GUID), ('media', GUID.replace('-', ''))):
            await self.page.evaluate('''({target,id})=>{
              if(target==='file')fixture.file.uuid=id;
              else fixture.material.postStore.postObjDesc.media[0].md5sum=id;
            }''', {'target': target, 'id': wrong}, isolated_context=False)
            self.assertFalse(await check())
            await self.page.evaluate('''id=>{
              fixture.file.uuid=id;fixture.material.postStore.postObjDesc.media[0].md5sum=id;
            }''', SERVER_ID, isolated_context=False)
            self.assertTrue(await check())


if __name__ == '__main__':
    unittest.main(verbosity=2)
