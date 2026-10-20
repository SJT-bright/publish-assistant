"""Bound normal image collection to the frozen task, before row JSON parsing."""
import time
import unittest

from tests import test_tc_cover_derivative as derivative_fixture
from tests import test_tc_preview_evidence as preview_fixture
from tests.test_tc_preview_mime import small_jpeg


class FrozenPreviewScopeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = derivative_fixture.CoverDerivativeMappingTests.asyncSetUp
    make_session = derivative_fixture.CoverDerivativeMappingTests.make_session
    allocate = derivative_fixture.CoverDerivativeMappingTests.allocate
    finish = derivative_fixture.CoverDerivativeMappingTests.finish
    attest_normal_reopen = derivative_fixture.CoverDerivativeMappingTests.attest_normal_reopen
    pending_snapshot = derivative_fixture.CoverDerivativeMappingTests.pending_snapshot
    pending = derivative_fixture.CoverDerivativeMappingTests.pending
    prepared_saved = derivative_fixture.CoverDerivativeMappingTests.prepared_saved
    record = derivative_fixture.CoverDerivativeMappingTests.record
    proof = derivative_fixture.CoverDerivativeMappingTests.proof
    ready = derivative_fixture.CoverDerivativeMappingTests.ready
    set_preview = derivative_fixture.CoverDerivativeMappingTests.set_preview

    async def test_twenty_unrelated_images_over_256_requests_cannot_starve_frozen_target_before_json(self):
        for family in ('original', 'derivative'):
            with self.subTest(family=family):
                await self.asyncSetUp()
                await self.ready()
                self.preview.records.clear()
                self.session.reads.clear()  # Normal JSON callback is still pending.
                unrelated = 0
                for _ in range(14):
                    for index in range(20):
                        url = preview_fixture.image_resource('unrelated-' + str(index))
                        response = preview_fixture.image_response(self.page, url, self.bodies['cover_portrait'])
                        self.assertTrue(await self.preview.observe(response, time.time()))
                        response.body.assert_not_awaited()
                        unrelated += 1
                self.assertGreater(unrelated, 256)
                self.assertEqual(self.preview.records, [])
                if family == 'original':
                    self.urls = {slot: value['url'] for slot, value in self.candidate['tc_cover_media'].items()}
                    self.new_bodies = dict(self.bodies)
                await self.set_preview()
                self.assertEqual(len(self.preview.records), 2)
                # Both exact source resources and rotated-auth derived URLs
                # remain eligible without any parsed row to select them.
                if family == 'derivative':
                    self.urls = {slot: derivative_fixture.replace_query(url, token='normal-rotated-auth')
                                 for slot, url in self.urls.items()}
                    await self.set_preview()
                self.session.reads = [self.reopened]
                proof, native = await self.proof()
                self.assertIsNotNone(proof)
                native.assert_awaited_once()
                self.assertNotIn('record_limit', self.preview.rejections)

    async def test_identical_response_coalesces_to_latest_normal_timing_without_swallowing_conflicting_bytes(self):
        await self.ready()
        slot = 'cover_portrait'
        for _ in range(280):
            response = preview_fixture.image_response(self.page, self.urls[slot], self.new_bodies[slot])
            await self.preview.observe(response, time.time())
        self.assertEqual(len(self.preview.records), 2)
        latest = next(value for value in self.preview.records if value['url'] == self.urls[slot])
        at = latest['request_at']
        response = preview_fixture.image_response(self.page, self.urls[slot], self.new_bodies[slot],
            when=self.reopened['tc_response_start_at'] + .000001)
        await self.preview.observe(response, time.time())
        self.assertEqual(next(value for value in self.preview.records if value['url'] == self.urls[slot])['request_at'], at)
        proof, _ = await self.proof()
        self.assertIsNotNone(proof)
        response = preview_fixture.image_response(self.page, self.urls[slot], small_jpeg('magenta'))
        await self.preview.observe(response, time.time())
        self.assertEqual(len(self.preview.records), 3)
        self.assertEqual(len({value['sha256'] for value in self.preview.records if value['url'] == self.urls[slot]}), 2)
        # A later repeat of the good JPEG cannot erase the conflicting body.
        response = preview_fixture.image_response(self.page, self.urls[slot], self.new_bodies[slot])
        await self.preview.observe(response, time.time())
        self.assertEqual(len(self.preview.records), 3)
        proof, native = await self.proof()
        self.assertIsNone(proof)
        native.assert_not_awaited()
        self.assertNotIn('record_limit', self.preview.rejections)

    async def test_different_encrypted_key_or_invalidated_frozen_scope_cannot_contribute_body(self):
        for case in ('encrypted-key', 'frozen-crop-changed'):
            with self.subTest(case=case):
                await self.asyncSetUp()
                await self.ready()
                self.preview.records.clear()
                url = self.urls['cover_portrait']
                if case == 'encrypted-key':url = derivative_fixture.replace_query(url, encfilekey='other-encrypted-file')
                else:next(iter(self.session.tc_cover_observer.records.values()))['derived_sha256'] = '0' * 64
                response = preview_fixture.image_response(self.page, url, self.new_bodies['cover_portrait'])
                await self.preview.observe(response, time.time())
                response.body.assert_not_awaited()
                self.assertEqual(self.preview.records, [])
                proof, native = await self.proof()
                self.assertIsNone(proof)
                native.assert_not_awaited()

    async def test_fresh_exact_normal_row_role_remains_an_observation_candidate_for_legacy_byte_proof(self):
        await self.ready()
        self.urls = {slot: preview_fixture.image_resource('different-opaque-' + slot) for slot in self.urls}
        self.new_bodies = dict(self.bodies)
        await self.set_preview()
        self.assertEqual(len(self.preview.records), 2)
        proof, native = await self.proof()
        self.assertIsNotNone(proof)
        native.assert_awaited_once()
        self.assertEqual(self.preview.cover_derivatives, {})
        # An eligible normal-row URL is only a candidate: different bytes with
        # different opaque fields still cannot use the derivative exception.
        self.new_bodies['cover_portrait'] = small_jpeg('magenta')
        await self.set_preview()
        self.assertEqual(len(self.preview.records), 2)
        proof, native = await self.proof()
        self.assertIsNone(proof)
        native.assert_not_awaited()

    async def test_old_foreign_failed_non_post_or_unbound_normal_row_cannot_expand_candidates(self):
        for case in ('id', 'owner', 'method', 'endpoint', 'status', 'old', 'future', 'nonfinite', 'unbound-role'):
            with self.subTest(case=case):
                await self.asyncSetUp()
                await self.ready()
                self.preview.records.clear()
                slot = 'cover_portrait'
                url = preview_fixture.image_resource('different-opaque-normal-candidate')
                from uploader.tc_resource import tc_resource_identity
                resource = tc_resource_identity(url)
                self.reopened['tc_cover_media'][slot] = {'url': url, 'resource': resource}
                self.reopened['cover_slots'][slot] = [resource]
                if case == 'id':self.reopened['id'] = 'other-row'
                if case == 'owner':self.reopened['owner'] = 'other-owner'
                if case == 'method':self.reopened['request_method'] = 'GET'
                if case == 'endpoint':self.reopened['tc_normal_read'] = 'other'
                if case == 'status':self.reopened['tc_handle_status'] = 1
                if case == 'old':self.reopened['request_at'] = self.session.reopen_at - 1
                if case == 'future':self.reopened['observed_at'] = time.time() + 1
                if case == 'nonfinite':self.reopened['request_at'] = float('nan')
                if case == 'unbound-role':self.reopened['cover_slots'][slot] = []
                response = preview_fixture.image_response(self.page, url, self.bodies[slot])
                await self.preview.observe(response, time.time())
                response.body.assert_not_awaited()
                self.assertEqual(self.preview.records, [])
