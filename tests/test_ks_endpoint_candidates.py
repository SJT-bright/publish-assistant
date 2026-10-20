"""Known Kuaishou candidates plus unused DNS fallback, no platform access."""
import copy
import hashlib
import io
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from uploader.ks_evidence import KSEvidenceObserver, _trusted_pre_candidates
from tests.test_ks_evidence import session, reply


class KSEndpointCandidateTests(unittest.TestCase):
    def test_unknown_fallback_is_omitted_from_eligible_targets(self):
        value = _trusted_pre_candidates(['upload.kuaishouzt.com', 'backup.kuaishouzt.com', 'fallback.example.net'])
        self.assertEqual(value, ('upload.kuaishouzt.com', 'backup.kuaishouzt.com'))
        self.assertNotIn('fallback.example.net', value)

    def test_unknown_primary_or_no_trusted_candidate_is_not_authorized(self):
        for value in (['fallback.example.net'], ['fallback.example.net', 'upload.kuaishouzt.com'],
                      ['kuaishouzt.com'], ['upload.kuaishouzt.com.evil.example']):
            with self.subTest(value=value):
                self.assertEqual(_trusted_pre_candidates(value), ())

    def test_unsafe_fallback_shape_cannot_hide_behind_a_valid_primary(self):
        unsafe = ['https://backup.kuaishouzt.com', '//backup.kuaishouzt.com', 'backup.kuaishouzt.com:443',
            'name@backup.kuaishouzt.com', 'backup.kuaishouzt.com/path', 'backup.kuaishouzt.com?q=x',
            'backup.kuaishouzt.com#x', ' backup.kuaishouzt.com', 'backup.kuaishouzt.com ',
            'Backup.kuaishouzt.com', '192.0.2.10', '999.0.2.10', '::1', 'localhost',
            '-bad.example.net', 'bad-.example.net', 'bad..example.net', 'x' * 64 + '.example.net', None, {}]
        for value in unsafe:
            with self.subTest(value=value):
                self.assertEqual(_trusted_pre_candidates(['upload.kuaishouzt.com', value]), ())

    def test_optional_exact_empty_values_and_duplicates_do_not_expand_targets(self):
        self.assertEqual(_trusted_pre_candidates(['', 'upload.kuaishouzt.com', '', 'upload.kuaishouzt.com']),
                         ('upload.kuaishouzt.com',))
        self.assertEqual(_trusted_pre_candidates(['']), ())
        self.assertEqual(_trusted_pre_candidates(['upload.kuaishouzt.com'] * 129), ())


class KSCompletedFallbackChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.page = SimpleNamespace()
        self.session = session(self.page)
        self.observer = KSEvidenceObserver(self.session)
        self.when = time.time() + .02
        self.observer.native = {'name': 'fixture.mp4', 'size': self.session.assets['video']['size'],
            'sha256': self.session.expected['video_sha256'], 'file_id': 123456789,
            'token_sha256': hashlib.sha256(b'fixture-private-token').hexdigest(), 'cover_keys': []}

    async def completed(self, *, endpoints=None, finish=None):
        endpoints = ['upload.kuaishouzt.com', 'backup.kuaishouzt.com', 'fallback.example.net'] if endpoints is None else endpoints
        pre = {'result': 1, 'data': {'fileId': 123456789, 'token': 'fixture-private-token', 'endPoints': endpoints}}
        body = {'token': 'fixture-private-token', 'fileName': 'fixture.mp4',
                'fileLength': self.session.assets['video']['size'], 'fileType': 'video/mp4'}
        terminal = {'result': 1, 'data': {'fileId': 123456789}} if finish is None else finish
        with redirect_stdout(io.StringIO()) as output:
            await self.observer.observe(reply('pre', {'uploadType': 1}, self.when), pre, self.when + .001)
            await self.observer.observe(reply('finish', body, self.when + .02), terminal, self.when + .021)
        return output.getvalue()

    async def test_current_native_hash_token_id_finish_chain_accepts_known_candidates(self):
        text = await self.completed()
        links = self.observer.upload_links()
        self.assertEqual(links, [{'kind': 'video', 'sha256': self.session.expected['video_sha256'],
                                  'ids': ['123456789'], 'complete': True}])
        self.assertEqual(self.observer.records[0]['trusted_endpoint_count'], 2)
        for value in ('fallback.example.net', 'upload.kuaishouzt.com', 'fixture-private-token', '123456789'):
            self.assertNotIn(value, text)

    async def test_native_hash_name_size_file_id_and_token_must_all_still_match(self):
        await self.completed()
        original = copy.deepcopy(self.observer.native)
        for field, wrong in [('sha256', '0' * 64), ('name', 'different.mp4'),
                             ('size', original['size'] + 1), ('file_id', 'different-id'),
                             ('token_sha256', '0' * 64)]:
            with self.subTest(field=field):
                self.observer.native = {**original, field: wrong}
                self.assertEqual(self.observer.upload_links(), [])

    async def test_missing_native_or_failed_finish_cannot_be_replaced_by_fallback_policy(self):
        await self.completed()
        native = self.observer.native
        self.observer.native = None
        self.assertEqual(self.observer.upload_links(), [])
        self.observer.native = native
        self.observer.records.clear()
        await self.completed(finish={'result': 0, 'data': {'fileId': 123456789}})
        self.assertEqual(self.observer.upload_links(), [])

    async def test_wrong_selection_account_nonce_and_old_request_remain_rejected(self):
        await self.completed()
        self.session.files[0]['sha256'] = '0' * 64
        self.assertEqual(self.observer.upload_links(), [])
        self.session.files[0]['sha256'] = self.session.expected['video_sha256']
        self.session.expected['receipt_nonce'] = 'different-task'
        self.assertEqual(self.observer.upload_links(), [])
        self.session.expected['receipt_nonce'] = 'fixture-nonce'
        self.session.account = {**self.session.account, 'identity': 'different-owner'}
        self.assertEqual(self.observer.upload_links(), [])
        self.session.account = copy.deepcopy(self.observer.account)
        self.observer.records.clear();self.when = self.session.started_at - 1
        await self.completed()
        self.assertEqual(self.observer.upload_links(), [])

    async def test_unknown_primary_never_proves_completion_even_with_all_other_matches(self):
        await self.completed(endpoints=['fallback.example.net', 'upload.kuaishouzt.com'])
        self.assertEqual(self.observer.upload_links(), [])
