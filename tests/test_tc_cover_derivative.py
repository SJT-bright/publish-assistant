"""Scoped synthetic TC CDN derivatives; no account or remote image requests."""
import copy
import hashlib
import time
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tests import test_tc_preview_evidence as preview_fixture
from tests import test_tc_reopen_mapping as mapping
from tests.test_tc_preview_mime import small_jpeg
from uploader.draft_evidence import verify_evidence
from uploader.tc_cover_derivative import TCCoverResourceDerivative, cover_resource_derivative
from uploader.tc_cover_evidence import TCCoverObserver
from uploader.tc_preview_evidence import TCPreviewObserver
from uploader.tc_resource import tc_resource_identity


def replace_query(url, **changes):
    parsed = urlsplit(url)
    fields = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in changes.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    return urlunsplit(parsed._replace(query=urlencode(fields)))


class CoverResourceDerivativeTests(unittest.TestCase):
    def relation(self, *, source=None, target=None, **changes):
        slot = 'cover_portrait'
        source = source or preview_fixture.image_resource('portrait', uploaded=True)
        target = target or preview_fixture.image_resource('portrait')
        crop = {'slot': slot, 'applied_id': source, 'derived_sha256': hashlib.sha256(small_jpeg('red')).hexdigest(),
            'derived_size': len(small_jpeg('red'))}
        body = {'url': target, 'resource': tc_resource_identity(target),
            'sha256': hashlib.sha256(small_jpeg('blue')).hexdigest(), 'size': len(small_jpeg('blue'))}
        arguments = dict(slot=slot, source_url=source, target_url=target, crop=crop, body=body)
        arguments.update(changes)
        return cover_resource_derivative(**arguments)

    def test_only_exact_family_transition_matches_all_six_once_decoded_opaque_fields(self):
        relation = self.relation()
        self.assertIsInstance(relation, TCCoverResourceDerivative)
        self.assertNotEqual(relation.source_resource, relation.target_resource)
        self.assertNotEqual(relation.source_crop_sha256, relation.target_sha256)
        self.assertEqual(relation.slot, 'cover_portrait')
        with self.assertRaises(FrozenInstanceError):
            relation.target_sha256 = '0' * 64
        source = preview_fixture.image_resource('portrait', uploaded=True)
        target = preview_fixture.image_resource('portrait')
        # Once-decoded opaque content is compared, never a second URL decode.
        source = replace_query(source, encfilekey='opaque%2Fvalue')
        target = replace_query(target, encfilekey='opaque%2Fvalue', token='rotated-token', picformat='opaque-format')
        self.assertIsNotNone(self.relation(source=source, target=target))
        self.assertIsNone(self.relation(source=source, target=replace_query(target, encfilekey='opaque/value')))
        crop = {'slot': 'cover_portrait', 'applied_id': replace_query(source, token='earlier-token').replace(
            'https://finder.video.qq.com', 'http://wxapp.tc.qq.com'),
            'derived_sha256': 'a' * 64, 'derived_size': 100}
        self.assertIsNotNone(self.relation(source=source, target=target, crop=crop))
        self.assertNotIn('https://', repr(relation))

    def test_changed_opaque_values_extra_unknown_mixed_paths_or_families_never_derive(self):
        target = preview_fixture.image_resource('portrait')
        for key in ('encfilekey', 'uzid', 'm', 'hy', 'idx', 'scene'):
            with self.subTest(key=key):
                self.assertIsNone(self.relation(target=replace_query(target, **{key: 'other'})))
        for bad in (
            target + '&unknown=extra', target + '&bizid=1023', target + '&dotrans=0',
            target + '&scene=duplicate', target + '&%73cene=encoded-key',
            replace_query(target, picformat=None), replace_query(target, scene=None),
            replace_query(target, picformat=''), target.replace('/20304/', '/20302/'),
            target.replace('finder.video.qq.com', 'foreign.invalid'),
            preview_fixture.image_resource('portrait', uploaded=True),
        ):
            with self.subTest(index=len(bad)):
                self.assertIsNone(self.relation(target=bad))
        self.assertIsNone(self.relation(source=target))

    def test_role_applied_source_response_identity_and_full_body_metadata_are_required(self):
        self.assertIsNone(self.relation(slot='automatic-thumb'))
        self.assertIsNone(self.relation(slot=[]))
        source = preview_fixture.image_resource('portrait', uploaded=True)
        target = preview_fixture.image_resource('portrait')
        crop = {'slot': 'cover_portrait', 'applied_id': source, 'derived_sha256': 'a' * 64, 'derived_size': 100}
        body = {'url': target, 'resource': tc_resource_identity(target), 'sha256': 'b' * 64, 'size': 80}
        for key, value in (('slot', 'cover_landscape'), ('applied_id', 'other'), ('derived_sha256', 'invalid'),
                           ('derived_size', True), ('derived_size', 0), ('derived_size', 33554433)):
            self.assertIsNone(self.relation(crop={**crop, key: value}, body=body))
        for key, value in (('url', 'other'), ('resource', 'tc-resource:' + '0' * 64), ('sha256', 'invalid'),
                           ('size', False), ('size', 0), ('size', 33554433)):
            self.assertIsNone(self.relation(crop=crop, body={**body, key: value}))


class CoverDerivativeMappingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = preview_fixture.CustomCoverMappingTests.asyncSetUp
    make_session = preview_fixture.CustomCoverMappingTests.make_session
    allocate = preview_fixture.CustomCoverMappingTests.allocate
    finish = preview_fixture.CustomCoverMappingTests.finish
    attest_normal_reopen = preview_fixture.CustomCoverMappingTests.attest_normal_reopen
    pending_snapshot = preview_fixture.CustomCoverMappingTests.pending_snapshot
    pending = preview_fixture.CustomCoverMappingTests.pending
    prepared_saved = preview_fixture.CustomCoverMappingTests.prepared_saved
    record = preview_fixture.CustomCoverMappingTests.record
    proof = preview_fixture.CustomCoverMappingTests.proof

    async def ready(self):
        self.asset_id = '12345678-abcd-1234-abcd-123456789abc'
        self.state['uuid'] = self.asset_id
        await self.pending()
        self.candidate = self.record(mapping.fixture.SOURCE, self.size, 'original')
        self.candidate['fresh'] = True
        await self.prepared_saved(self.candidate)
        # Genuine locally encoded/decoded JPEGs differ from the normal CDN
        # derivative. Frozen records and complete transport use source bytes.
        self.bodies = {slot: small_jpeg(color) for slot, color in
            zip(('cover_portrait', 'cover_landscape'), ('red', 'green'))}
        self.new_bodies = {slot: small_jpeg(color) for slot, color in
            zip(self.bodies, ('blue', 'yellow'))}
        cover = TCCoverObserver(self.session)
        self.session.tc_cover_observer = cover
        selected = time.time()
        self.session.expected['cover_sha256'] = {}
        for index, (slot, body) in enumerate(self.bodies.items()):
            sha = hashlib.sha256(('original-' + slot).encode()).hexdigest()
            self.session.expected['cover_sha256'][slot] = sha
            asset = {'kind': 'cover', 'name': slot + '.png', 'size': 123 + index, 'sha256': sha}
            self.session.assets[slot] = asset
            self.session.files.append({**asset, 'selected_at': selected})
            applied = self.candidate['tc_cover_media'][slot]['url']
            record = {'slot': slot, 'invocation_id': slot + '-invocation', 'origin_sha256': sha,
                'origin_name': asset['name'], 'origin_size': asset['size'], 'selected_at': selected,
                'derived_name': 'finder_video_img.jpeg', 'derived_size': len(body),
                'derived_sha256': hashlib.sha256(body).hexdigest(), 'encoded_at': selected + .001,
                'applied_at': selected + .003, 'applied_id': applied}
            cover.records[record['invocation_id']] = record
            self.session.exchanges.append({'tc_protocol': 'sns', 'task_id': slot + '-upload',
                'metadata': (record['derived_name'], len(body)), 'observed_at': selected + .002,
                'range': (0, len(body)), 'stage': 'complete', 'ids': [tc_resource_identity(applied)],
                'whole_body': True, 'body_sha256': record['derived_sha256']})
        self.session.upload_links = lambda: self.observer.augment_links(self.links + cover.upload_links())
        self.assertTrue(await self.observer.freeze_for_reopen(self.candidate))
        self.attest_normal_reopen(self.candidate)
        self.state = None
        await self.observer.drain()
        # 'original' chooses the native-upload family for every source role;
        # this exact six-field projection changes only to image_cover family.
        self.reopened = self.record(mapping.scene_resource(), 23, 'normal-read')
        self.urls = {slot: preview_fixture.image_resource('original-' + name) for slot, name in
            (('cover_portrait', 'portrait'), ('cover_landscape', 'landscape'))}
        self.reopened.update(tc_normal_read='get_draft_list', request_method='POST',
            request_at=time.time(), observed_at=time.time(), tc_response_start_at=time.time())
        self.session.reads = [self.reopened]
        self.page.frames = [SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/create')]
        self.preview = TCPreviewObserver(self.session)
        self.session.tc_preview_observer = self.preview
        await self.set_preview()
        self.old_links = copy.deepcopy(self.session.upload_links())

    async def set_preview(self):
        self.preview.records.clear()
        for slot, url in self.urls.items():
            resource = tc_resource_identity(url)
            self.reopened['tc_cover_media'][slot] = {'url': url, 'resource': resource}
            self.reopened['cover_slots'][slot] = [resource]
            response = preview_fixture.image_response(self.page, url, self.new_bodies[slot])
            response.headers['content-type'] = 'image/jpg'
            await self.preview.observe(response, time.time())
        self.reopened['cover_ids'] = sorted([value['resource'] for value in self.reopened['tc_cover_media'].values() if value['resource']] +
            [self.reopened['tc_thumb_media']['resource']])

    async def test_real_jpeg_derivative_keeps_distinct_source_new_hashes_and_unchanged_upload_chain(self):
        await self.ready()
        proof, native = await self.proof()
        self.assertIsNotNone(proof)
        native.assert_awaited_once_with(self.page, self.reopened, restored_from=self.candidate,
            strict_cover_media=True, cover_roles=self.urls)
        self.assertEqual(self.session.upload_links(), self.old_links)
        self.assertEqual(set(self.preview.cover_derivatives), set(self.urls))
        for slot, relation in self.preview.cover_derivatives.items():
            self.assertIsInstance(relation, TCCoverResourceDerivative)
            self.assertEqual(relation.source_resource, self.candidate['tc_cover_media'][slot]['resource'])
            self.assertEqual(relation.target_resource, self.reopened['tc_cover_media'][slot]['resource'])
            self.assertNotEqual(relation.source_resource, relation.target_resource)
            self.assertEqual(relation.source_crop_sha256, hashlib.sha256(self.bodies[slot]).hexdigest())
            self.assertEqual(relation.target_sha256, hashlib.sha256(self.new_bodies[slot]).hexdigest())
        checks, _ = verify_evidence('tencent', self.session.expected, self.candidate, self.old_links,
            self.session.account, reopened=self.reopened, ui_fields=True, account_after=self.session.account,
            tc_processing_mapping=proof)
        self.assertTrue(all(checks.values()))
        self.preview.records[0]['sha256'] = '0' * 64
        self.assertFalse(proof.matches(self.session.expected, self.candidate, self.reopened,
            self.old_links, self.session.account, self.session.account))

    async def test_wrong_shared_keys_family_extra_fields_wrong_role_or_missing_body_reject(self):
        for case in ('encfilekey', 'uzid', 'm', 'hy', 'idx', 'scene', 'extra', 'same-family',
                     'wrong-role', 'missing-body', 'conflicting-body'):
            with self.subTest(case=case):
                await self.asyncSetUp()
                await self.ready()
                if case in ('encfilekey', 'uzid', 'm', 'hy', 'idx', 'scene'):
                    self.urls['cover_portrait'] = replace_query(self.urls['cover_portrait'], **{case: 'other'})
                if case == 'extra':self.urls['cover_portrait'] += '&unknown=extra'
                if case == 'same-family':self.urls['cover_portrait'] = self.candidate['tc_cover_media']['cover_portrait']['url']
                if case == 'wrong-role':self.urls = dict(zip(self.urls, reversed(list(self.urls.values()))))
                await self.set_preview()
                if case == 'missing-body':self.preview.records.pop()
                if case == 'conflicting-body':self.preview.records.append({**self.preview.records[0], 'sha256': '0' * 64})
                proof, native = await self.proof()
                self.assertIsNone(proof)
                native.assert_not_awaited()
                self.assertEqual(self.preview.cover_derivatives, {})

    async def test_source_crop_transport_slot_scope_same_row_and_current_native_consumer_are_still_required(self):
        for case in ('crop', 'transport', 'slot', 'owner', 'nonce', 'job', 'row-id', 'failed-row',
                     'old-body', 'current-native', 'normal-read'):
            with self.subTest(case=case):
                await self.asyncSetUp()
                await self.ready()
                if case == 'crop':next(iter(self.session.tc_cover_observer.records.values()))['derived_sha256'] = '0' * 64
                if case == 'transport':self.session.exchanges[-1]['body_sha256'] = '0' * 64
                if case == 'slot':self.session.tc_cover_observer.invalidated['cover_portrait'] = time.time()
                if case == 'owner':self.session.account['uid'] = 'other-owner'
                if case == 'nonce':self.session.expected['receipt_nonce'] = 'other-nonce'
                if case == 'job':self.session.expected['job_id'] = 'other-job'
                if case == 'row-id':self.reopened['id'] = 'other-row'
                if case == 'failed-row':self.reopened['tc_handle_status'] = 3
                if case == 'old-body':self.preview.records[0]['request_at'] = self.reopened['tc_response_start_at'] - 1
                if case == 'normal-read':self.reopened['tc_normal_read'] = 'other'
                proof, native = await self.proof(native=case != 'current-native')
                self.assertIsNone(proof)
                if case == 'current-native':native.assert_awaited_once()
                else:native.assert_not_awaited()

    async def test_new_derivative_hash_or_typed_relation_changes_during_native_scan_invalidate(self):
        from unittest.mock import patch
        from uploader.tc_reopen_mapping import verify_processing_reopen
        for case in ('body', 'relation'):
            await self.asyncSetUp()
            await self.ready()
            async def mutate(*args, **kwargs):
                if case == 'body':self.preview.records[0]['sha256'] = '0' * 64
                else:self.preview.cover_derivatives.clear()
                return True
            with patch('uploader.tc_assets.tc_reopened_assets_match', new=mutate):
                self.assertIsNone(await verify_processing_reopen(self.session, self.candidate,
                    self.reopened, self.session.account))
