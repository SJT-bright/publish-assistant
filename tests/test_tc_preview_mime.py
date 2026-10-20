"""Exact Finder JPEG MIME aliases; only cached normal response bytes are read."""
import copy
import hashlib
import io
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from tests.test_tc_preview_evidence import PreviewByteTests, image_resource, image_response
from uploader.tc_preview_evidence import TCPreviewObserver
from uploader.tc_resource import tc_resource_identity


def small_jpeg(color):
    output = io.BytesIO()
    Image.new('RGB', (3, 2), color).save(output, format='JPEG', quality=80)
    result = output.getvalue()
    with Image.open(io.BytesIO(result)) as decoded:
        decoded.load()
        if decoded.format != 'JPEG' or decoded.size != (3, 2):
            raise AssertionError('local JPEG fixture failed actual decode')
    return result


class TCPreviewMimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await PreviewByteTests.asyncSetUp(self)
        self.body = small_jpeg('#3173bc')
        # These are deliberately unavailable: observing a normal response
        # must never initiate any remote image or business request.
        self.page.request = Mock(side_effect=AssertionError('active_request_forbidden'))
        self.page.evaluate = Mock(side_effect=AssertionError('business_call_forbidden'))

    def response(self, mime='image/jpg', *, body=None, url=None):
        result = image_response(self.page, self.url if url is None else url,
                                self.body if body is None else body)
        result.headers['content-type'] = mime
        return result

    async def test_only_jpeg_and_observed_jpg_alias_keep_existing_case_parameter_normalization(self):
        for mime in ('image/jpeg', 'image/jpg', 'IMAGE/JPG', '  Image/JpG ; charset=binary ',
                     'IMAGE/JPEG; charset=binary'):
            with self.subTest(mime=mime):
                self.observer = TCPreviewObserver(self.session)
                response = self.response(mime)
                self.assertTrue(await self.observer.observe(response, time.time()))
                response.body.assert_awaited_once_with()
                self.assertEqual(len(self.observer.records), 1)
                record = self.observer.records[0]
                self.assertEqual((record['sha256'], record['size']),
                                 (hashlib.sha256(self.body).hexdigest(), len(self.body)))
                self.assertEqual(record['resource'], tc_resource_identity(self.url))
        self.page.request.assert_not_called()
        self.page.evaluate.assert_not_called()

    async def test_wrong_content_types_statuses_and_non_image_requests_reject_before_body(self):
        for mime in ('image/png', 'image/webp', 'image/pjpeg', 'image/jp2', 'image/jpg+json',
                     'image/jpeg2000', 'image/*', 'text/html', 'application/octet-stream', ''):
            with self.subTest(mime=mime):
                self.observer = TCPreviewObserver(self.session)
                response = self.response(mime)
                self.assertTrue(await self.observer.observe(response, time.time()))
                self.assertEqual(self.observer.records, [])
                self.assertEqual(self.observer.last_rejection, 'response_schema')
                response.body.assert_not_awaited()
        for status in (302, 404, 500):
            self.observer = TCPreviewObserver(self.session)
            response = self.response();response.status = status
            self.assertTrue(await self.observer.observe(response, time.time()))
            self.assertEqual(self.observer.records, [])
            response.body.assert_not_awaited()
        for resource_type in ('fetch', 'xhr', 'document'):
            self.observer = TCPreviewObserver(self.session)
            response = self.response();response.request.resource_type = resource_type
            self.assertFalse(await self.observer.observe(response, time.time()))
            self.assertEqual(self.observer.records, [])
            response.body.assert_not_awaited()
        response = self.response();response.request.method = 'POST'
        self.assertFalse(await self.observer.observe(response, time.time()))
        response.body.assert_not_awaited()

    async def test_jpg_alias_cannot_bypass_path_frame_job_account_timing_or_full_length_gates(self):
        for case in ('path', 'frame', 'job', 'nonce', 'account', 'old', 'truncated'):
            with self.subTest(case=case):
                await self.asyncSetUp()
                response = self.response()
                if case == 'path':response.url = response.url.replace('/stodownload?', '/other?')
                if case == 'frame':response.request.frame = SimpleNamespace(url=self.frame.url, unrelated=True)
                if case == 'job':self.session.expected['job_id'] = 'other-job'
                if case == 'nonce':self.session.expected['receipt_nonce'] = 'other-nonce'
                if case == 'account':self.session.account['uid'] = 'other-owner'
                if case == 'old':response.request.timing['startTime'] = (self.session.reopen_at - 1) * 1000
                if case == 'truncated':response.headers['content-length'] = str(len(self.body) + 1)
                await self.observer.observe(response, time.time())
                self.assertEqual(self.observer.records, [])
                if case != 'truncated':response.body.assert_not_awaited()

    async def test_exact_full_jpeg_bytes_map_frozen_roles_and_one_changed_byte_rejects(self):
        # Only this byte observer's frozen-producer boundary is stubbed here.
        # The existing CustomCoverMappingTests separately exercise the actual
        # crop/transport/task/frozen observer contract end to end.
        slots = ('cover_portrait', 'cover_landscape')
        bodies = {slot: small_jpeg(color) for slot, color in zip(slots, ('#3173bc', '#bc7331'))}
        self.session.expected['cover_sha256'] = {slot: hashlib.sha256(('original-' + slot).encode()).hexdigest() for slot in slots}
        frozen = {slot: {'ids': ['original-' + slot], 'record': {
            'origin_sha256': self.session.expected['cover_sha256'][slot],
            'derived_sha256': hashlib.sha256(bodies[slot]).hexdigest(), 'derived_size': len(bodies[slot])}} for slot in slots}
        self.session.tc_cover_observer = SimpleNamespace(frozen_current=Mock(side_effect=lambda value: value == frozen))
        candidate = {'cover_slots': {slot: frozen[slot]['ids'] for slot in slots}}
        urls = {slot: image_resource('normal-' + slot) for slot in slots}
        at = time.time() - .001
        reopened = {'request_at': at, 'tc_response_start_at': at,
            'tc_cover_media': {slot: {'url': urls[slot], 'resource': tc_resource_identity(urls[slot])} for slot in slots},
            'cover_slots': {slot: [tc_resource_identity(urls[slot])] for slot in slots}}
        for slot in slots:
            response = self.response(body=bodies[slot], url=urls[slot])
            self.assertTrue(await self.observer.observe(response, time.time()))
        self.assertEqual(self.observer.cover_roles(candidate, reopened, frozen), urls)
        self.session.tc_cover_observer.frozen_current.assert_called_once_with(frozen)
        original_records = copy.deepcopy(self.observer.records)
        changed = bytearray(bodies['cover_portrait']);changed[-3] ^= 1
        self.assertEqual(len(changed), len(bodies['cover_portrait']))
        self.observer.records.clear()
        for slot, body in (('cover_portrait', bytes(changed)), ('cover_landscape', bodies['cover_landscape'])):
            self.assertTrue(await self.observer.observe(self.response(body=body, url=urls[slot]), time.time()))
        self.assertIsNone(self.observer.cover_roles(candidate, reopened, frozen))
        self.assertEqual(self.observer.last_rejection, 'preview_bytes_mismatch')
        self.assertNotEqual(self.observer.records[0]['sha256'], original_records[0]['sha256'])
        self.assertEqual(self.observer.records[1]['sha256'], original_records[1]['sha256'])
        self.page.request.assert_not_called()
        self.page.evaluate.assert_not_called()
