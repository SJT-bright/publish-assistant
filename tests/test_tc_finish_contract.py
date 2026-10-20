"""Official DFS confirmation fixtures. No network, real account or app calls."""
import asyncio
import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout
from urllib.parse import urlencode
from unittest.mock import patch

from uploader.draft_evidence import DraftEvidenceSession, _tc_upload_exchange


class TencentFinishContractTests(unittest.TestCase):
    class Request:
        def __init__(self, method, headers, body):
            self.method, self.headers, self.post_data_json = method, headers, body

    class Response:
        def __init__(self, path, request):
            self.url, self.request = 'https://finder.video.qq.com' + path, request

    def setUp(self):
        self.asset = {'name': 'private-fixture-video.mp4', 'size': 20,
                      'sha256': hashlib.sha256(b'fixture-current-whole-file').hexdigest(), 'kind': 'video'}
        self.session = DraftEvidenceSession(None, 'tencent', {})
        self.session.started_at = 0
        self.session.assets = {'video': self.asset}
        self.session.files = [{**self.asset, 'selected_at': 1}]

    def exchange(self, endpoint, method, body, reply, at, *, task='private-task', upload='private-upload', marker=None):
        path = '/' + endpoint
        query = {}
        if endpoint != 'applyuploaddfs' or marker is not None:
            query['UploadID'] = upload
        if endpoint == 'uploadpartdfs':
            query['PartNumber'] = body.pop('_part')
        if marker is not None:
            query['PartNumberMarker'] = marker
        if query:
            path += '?' + urlencode(query)
        request = self.Request(method, {'x-arguments': urlencode({
            'filekey': self.asset['name'], 'filesize': self.asset['size'], 'taskid': task}),
            'authorization': 'private-credential'}, body)
        parsed = _tc_upload_exchange(self.Response(path, request), reply, at)
        if parsed is not None:
            self.session.exchanges.append(parsed)
        return parsed

    def allocation(self, at=10):
        return self.exchange('applyuploaddfs', 'PUT', {'BlockSum': 2, 'BlockPartLength': [10, 20]},
                             {'UploadID': 'private-upload'}, at)

    def part(self, number, etag=None, at=None):
        return self.exchange('uploadpartdfs', 'PUT', {'_part': number},
                             {'X-Errno': 0, 'ETag': etag or f'quick-{number}', 'TransFlag': 'private-flag'},
                             at if at is not None else 10 + number)

    def confirmation(self, *, parts=None, marker='1', truncated='false', next_marker=None,
                     at=13, task='private-task', upload='private-upload', method='PUT'):
        result = {'Part': parts if parts is not None else [
            {'PartNumber': '1', 'ETag': 'confirmed-1'}, {'PartNumber': '2', 'ETag': 'confirmed-2'}],
            'IsTruncated': truncated}
        if next_marker is not None:
            result['NextPartNumberMarker'] = next_marker
        return self.exchange('applyuploaddfs', method, {}, {'ListPartsResult': result}, at,
                             task=task, upload=upload, marker=marker)

    def finish(self, *, etags=('confirmed-1', 'confirmed-2'), at=14):
        return self.exchange('completepartuploaddfs', 'POST', {'TransFlag': 'private-flag',
            'PartInfo': [{'PartNumber': n, 'ETag': etag} for n, etag in enumerate(etags, 1)]},
            {'DownloadURL': 'http://wxapp.tc.qq.com/fixture/current'}, at)

    def raw_parts(self):
        self.allocation()
        self.part(1)
        self.part(2)

    def test_same_fresh_upload_server_confirmation_overwrites_quick_etags(self):
        self.raw_parts()
        self.confirmation()
        self.finish()
        self.assertEqual(len(self.session.upload_links()), 1)
        diagnostic = self.session.tc_upload_chain_diagnostics()[0]
        self.assertEqual(diagnostic['raw_etag_exact_matches'], 0)
        self.assertEqual(diagnostic['confirmed_etag_exact_matches'], 2)
        self.assertTrue(diagnostic['confirmation_chain_valid'])
        self.assertTrue(diagnostic['fresh_part_coverage'])

    def test_paginated_server_confirmation_requires_complete_marker_chain(self):
        self.raw_parts()
        self.confirmation(parts=[{'PartNumber': '1', 'ETag': 'confirmed-1'}],
                          truncated='true', next_marker='2', at=13)
        self.confirmation(parts=[{'PartNumber': '2', 'ETag': 'confirmed-2'}], marker='2', at=14, method='GET')
        self.finish(at=15)
        self.assertEqual(len(self.session.upload_links()), 1)

    def test_confirm_is_never_a_standalone_current_upload_proof(self):
        self.part(1)
        self.part(2)
        self.confirmation()
        self.finish()
        self.assertEqual(self.session.upload_links(), [])
        self.assertEqual(sum('plan' in item for item in self.session.exchanges), 0)

    def test_old_confirm_or_other_task_upload_cannot_repair_mismatch(self):
        for mode in ('before-allocation', 'different-task', 'different-upload', 'after-finish'):
            with self.subTest(mode=mode):
                self.setUp()
                self.raw_parts()
                self.confirmation(at=9 if mode == 'before-allocation' else 15 if mode == 'after-finish' else 13,
                                  task='other-task' if mode == 'different-task' else 'private-task',
                                  upload='other-upload' if mode == 'different-upload' else 'private-upload')
                self.finish()
                self.assertEqual(self.session.upload_links(), [])

    def test_confirmation_never_supplies_missing_or_conflicting_current_chunks(self):
        for mode in ('missing', 'conflicting-retry', 'chunk-after-confirm'):
            with self.subTest(mode=mode):
                self.setUp()
                self.allocation()
                self.part(1)
                if mode != 'missing':
                    self.part(2, at=13.5 if mode == 'chunk-after-confirm' else 12)
                if mode == 'conflicting-retry':
                    self.part(1, etag='other-quick-response', at=12.5)
                self.confirmation()
                self.finish()
                self.assertEqual(self.session.upload_links(), [])

    def test_incomplete_ambiguous_or_out_of_order_confirmation_is_rejected(self):
        for mode in ('missing-last-page', 'partial-final-page', 'same-marker-conflict',
                     'extra-page', 'backward-time', 'wrong-final-etag'):
            with self.subTest(mode=mode):
                self.setUp()
                self.raw_parts()
                if mode in ('missing-last-page', 'backward-time'):
                    self.confirmation(parts=[{'PartNumber': '1', 'ETag': 'confirmed-1'}],
                                      truncated='true', next_marker='2', at=13.5 if mode == 'backward-time' else 13)
                    if mode == 'backward-time':
                        self.confirmation(parts=[{'PartNumber': '2', 'ETag': 'confirmed-2'}], marker='2', at=13)
                elif mode == 'partial-final-page':
                    self.confirmation(parts=[{'PartNumber': '1', 'ETag': 'confirmed-1'}])
                else:
                    self.confirmation()
                    if mode == 'same-marker-conflict':
                        self.confirmation(parts=[{'PartNumber': '1', 'ETag': 'other'},
                                                 {'PartNumber': '2', 'ETag': 'confirmed-2'}], at=13.5)
                    elif mode == 'extra-page':
                        self.confirmation(marker='2', at=13.5)
                self.finish(etags=('wrong', 'confirmed-2') if mode == 'wrong-final-etag' else ('confirmed-1', 'confirmed-2'))
                self.assertEqual(self.session.upload_links(), [])

    def test_etags_remain_literal_without_decoding_trim_or_unquote(self):
        etags = ('"etag%2B+"', ' trailing%20 ')
        self.allocation()
        for n, etag in enumerate(etags, 1):
            self.part(n, etag=etag)
        self.finish(etags=etags)
        self.assertEqual(len(self.session.upload_links()), 1)
        for changed in ('etag%2B+', '"etag++"', 'trailing%20'):
            with self.subTest(changed=changed):
                self.setUp()
                self.allocation()
                for n, etag in enumerate(etags, 1):
                    self.part(n, etag=etag)
                self.finish(etags=(changed, etags[1]) if changed != 'trailing%20' else (etags[0], changed))
                self.assertEqual(self.session.upload_links(), [])

    def test_confirm_parser_rejects_unknown_flags_bad_markers_and_duplicates(self):
        for kwargs in ({'truncated': True}, {'truncated': None}, {'truncated': 'TRUE'},
                       {'marker': '01'}, {'marker': '0'}, {'truncated': 'true'},
                       {'truncated': 'true', 'next_marker': '1'},
                       {'truncated': 'false', 'next_marker': '2'},
                       {'parts': [{'PartNumber': '1', 'ETag': 'a'}, {'PartNumber': '1', 'ETag': 'a'}]},
                       {'parts': [{'PartNumber': True, 'ETag': 'a'}]}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.confirmation(**kwargs))

    def test_later_chunk_receipts_do_not_prove_an_earlier_finish(self):
        self.allocation()
        self.part(1, at=11)
        self.part(2, at=15)
        self.finish(etags=('quick-1', 'quick-2'), at=14)
        self.assertEqual(self.session.upload_links(), [])
        diagnostic = self.session.tc_upload_chain_diagnostics()[0]
        self.assertEqual(diagnostic['part_received_after_finish_count'], 1)
        self.assertEqual(diagnostic['observed_part_count'], 1)
        self.assertFalse(diagnostic['confirmation_present'])
        self.finish(etags=('quick-1', 'quick-2'), at=16)
        self.assertEqual(len(self.session.upload_links()), 1)

    def test_current_unique_whole_file_binding_remains_required(self):
        for mode in ('wrong-sha', 'duplicate-selection', 'changed-name', 'changed-size'):
            with self.subTest(mode=mode):
                self.setUp()
                self.raw_parts()
                self.confirmation()
                self.finish()
                if mode == 'wrong-sha':
                    self.session.files[0]['sha256'] = hashlib.sha256(b'different-whole-file').hexdigest()
                elif mode == 'duplicate-selection':
                    self.session.files.append({**self.session.files[0], 'selected_at': 2})
                elif mode == 'changed-name':
                    self.session.files[0]['name'] = 'other.mp4'
                else:
                    self.session.files[0]['size'] = 21
                self.assertEqual(self.session.upload_links(), [])

    def test_diagnostics_exclude_content_and_all_opaque_identifiers(self):
        self.raw_parts()
        self.confirmation()
        self.finish()
        diagnostic = json.dumps(self.session.tc_upload_chain_diagnostics())
        for forbidden in ('private-', 'confirmed-1', 'quick-1', self.asset['sha256'],
                          'wxapp.tc.qq.com', 'current', 'filekey', 'ETag'):
            self.assertNotIn(forbidden, diagnostic)

    def test_confirmation_response_diagnostic_is_content_free_and_print_failure_keeps_evidence(self):
        async def observe():
            class Response:
                url = 'https://finder.video.qq.com/applyuploaddfs?UploadID=private-upload&PartNumberMarker=1'
                status = 200
                request = self.Request('PUT', {'x-arguments': urlencode({
                    'filekey': self.asset['name'], 'filesize': self.asset['size'], 'taskid': 'private-task'})}, {})
                async def json(response):
                    return {'ListPartsResult': {'Part': [{'PartNumber': '1', 'ETag': 'private-etag'}],
                                                'IsTruncated': 'false'}}
            captured = io.StringIO()
            with redirect_stdout(captured):
                await self.session._observe(Response())
            diagnostic = json.loads(captured.getvalue().split('=', 1)[1])
            self.assertTrue(diagnostic['parsed'])
            self.assertEqual(diagnostic['part_count'], 1)
            self.assertNotIn('private', json.dumps(diagnostic))
            with patch('builtins.print', side_effect=OSError('closed output')):
                await self.session._observe(Response())
            self.assertEqual(len(self.session.exchanges), 2)
        asyncio.run(observe())


if __name__ == '__main__':
    unittest.main()
