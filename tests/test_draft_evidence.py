"""Synthetic browser fixtures; no real creator account, media or platform calls.

The HTTP layer is fulfilled locally. These tests prove correlation and rejection
logic, not acceptance or persistence by any live platform.
"""
import asyncio
import hashlib
import hmac
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from urllib.parse import urlencode, urlparse
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader.draft_evidence import (FILE_OBSERVER_SCRIPT, CHECKPOINT_MAX_AGE, CheckpointError, DraftEvidenceSession,
                                    begin_draft_verification, end_draft_verification, validate_checkpoint,
                                    _upload_stage, _resource_ids, _tc_cdn_endpoint, _tc_resource_aliases,
                                    endpoint_kind, field_match, parse_record, read_local_records, verify_evidence)
from uploader.draft_only import assert_draft_safe, install_draft_guard, is_publish_request, save_draft
from uploader.draft_session import DraftInterrupted
from workbench_model import build_platform_payload, preflight_payload, validate_receipt


def account(platform='douyin', uid='fixture-owner'):
    return {'verified': True, 'platform': platform, 'uid': uid,
            'identity': hashlib.sha256((platform + ':' + uid).encode()).hexdigest()}


def minimal_expected(platform='bilibili'):
    return {'video_sha256': 'video-bytes', 'cover_sha256': {'cover': 'cover-bytes'},
            'account': account(platform), 'platform_fields': {'title': '重复标题', 'description': '完整正文', 'tags': ['话题']}}


def record_bili(draft_id='new-draft', media='new-video', cover='new-cover'):
    result = parse_record('bilibili', {'id': draft_id, 'archive': {'title': '重复标题', 'desc': '完整正文', 'tag': '话题', 'cover': cover},
                                     'videos': [{'filename': media}]}, owner='fixture-owner')
    result['fresh'] = True
    return result


class TencentDraftProtocolTests(unittest.IsolatedAsyncioTestCase):
    class Request:
        method = 'POST'
        def __init__(self, body):
            self.post_data_json = body

    class Response:
        status = 200
        def __init__(self, path, body, reply):
            self.url = 'https://channels.weixin.qq.com' + path
            self.request = TencentDraftProtocolTests.Request(body)
            self.reply = reply
        async def json(self):
            return self.reply

    def test_only_exact_official_draft_prefixes_and_existing_bare_paths_match(self):
        for prefix in ('', '/cgi-bin/mmfinderassistant-bin', '/micro/content/cgi-bin/mmfinderassistant-bin'):
            for tail, kind in (('/post/post_draft', 'save'), ('/post/post_ad_create_draft', 'save'),
                               ('/post/get_draft_info', 'read'), ('/post/get_draft_list', 'readlist')):
                with self.subTest(prefix=prefix, tail=tail):
                    self.assertEqual(endpoint_kind('tencent', 'https://fixture.invalid' + prefix + tail), kind)
        for path in ('/arbitrary/post/post_draft', '/other/cgi-bin/mmfinderassistant-bin/post/post_draft',
                     '/micro/content/cgi-bin/mmfinderassistant-bin-extra/post/post_draft',
                     '/micro/content/cgi-bin/mmfinderassistant-bin/post/post_draft/extra',
                     '/micro/content/cgi-bin/mmfinderassistant-bin/post/post_create',
                     '/micro/content/cgi-bin/mmfinderassistant-bin/post//post_draft'):
            with self.subTest(path=path):
                self.assertIsNone(endpoint_kind('tencent', 'https://fixture.invalid' + path))

    async def observe(self, session, tail, body, reply, status=200):
        output = io.StringIO()
        response = self.Response('/micro/content/cgi-bin/mmfinderassistant-bin/post/' + tail, body, reply)
        response.status = status
        with redirect_stdout(output):
            await session._observe(response)
        text = output.getvalue()
        diagnostic = json.loads(text.removeprefix('DRAFT_DIAGNOSTIC='))
        return diagnostic, text

    async def test_real_prefix_save_and_later_read_remain_separate_evidence(self):
        session = DraftEvidenceSession(None, 'tencent', minimal_expected('tencent'))
        session.account = account('tencent')
        desc = {'description': 'private-fixture-description', 'shortTitle': [{'shortTitle': 'private-title'}],
                'media': [{'url': 'fixture-video', 'coverUrl': 'fixture-portrait', 'shareCoverUrl': 'fixture-landscape'}]}
        body = {'postReq': {'objectDesc': desc, 'clientid': 'private-client-id'}, 'objectId': None,
                'rawKeyBuff': 'private-token', 'private-user-key': 'private-value'}
        diagnostic, output = await self.observe(session, 'post_draft', body,
            {'errCode': 0, 'data': {'baseResp': {'errcode': 0}, 'objectId': 'private-draft-id'}})
        self.assertEqual(len(session.saves), 1)
        self.assertEqual(session.reads, [])
        self.assertEqual(session.saves[0]['cover_slots'], {'cover_portrait': ['fixture-portrait'], 'cover_landscape': ['fixture-landscape']})
        self.assertTrue(diagnostic['response_ok'])
        self.assertTrue(diagnostic['parsed_record'])
        self.assertTrue(diagnostic['record_id_present'])
        self.assertEqual(diagnostic['request_keys'], ['objectId', 'postReq'])
        self.assertEqual(diagnostic['codes'], {'errCode': 0})
        self.assertEqual(diagnostic['base_codes'], {'errcode': 0})
        for forbidden in ('private-fixture-description', 'private-title', 'private-client-id', 'private-token',
                          'private-draft-id', 'private-user-key', 'private-value', 'fixture-video', 'fixture-portrait'):
            self.assertNotIn(forbidden, output)
        diagnostic, output = await self.observe(session, 'get_draft_info', {'objectId': 'private-draft-id'},
            {'errCode': 0, 'data': {'objectId': 'private-draft-id', 'desc': desc}})
        self.assertEqual(len(session.reads), 1)
        self.assertFalse(session.reads[0]['fresh'])
        self.assertEqual(session.reads[0]['id'], session.saves[0]['id'])
        self.assertEqual(diagnostic['operation'], 'read')
        self.assertTrue(diagnostic['parsed_record'])

    async def test_prefix_fix_does_not_accept_unknown_codes_or_invalid_schema(self):
        for reply, body in (({'errCode': True, 'data': {'objectId': 'private-draft-id'}}, {'postReq': {'objectDesc': {}}}),
                            ({'errCode': '0', 'data': {'objectId': 'private-draft-id'}}, {'postReq': {'objectDesc': {}}}),
                            ({'errCode': 1, 'data': {'objectId': 'private-draft-id'}}, {'postReq': {'objectDesc': {}}}),
                            ({'errorCode': 0, 'data': {'objectId': 'private-draft-id'}}, {'postReq': {'objectDesc': {}}}),
                            ({'errCode': 0, 'data': {'objectId': 'private-draft-id'}}, {'postReq': {'objectDesc': None}})):
            with self.subTest(reply=reply, body=body):
                session = DraftEvidenceSession(None, 'tencent', minimal_expected('tencent'))
                session.account = account('tencent')
                diagnostic, output = await self.observe(session, 'post_draft', body, reply)
                self.assertEqual(session.saves, [])
                self.assertEqual(session.reads, [])
                self.assertFalse(diagnostic['parsed_record'])
                self.assertNotIn('private-draft-id', output)

    async def saved_session(self):
        expected = minimal_expected('tencent')
        expected['platform_fields'] = {'caption': 'private-fixture-caption', 'short_title': 'private-short-title'}
        session = DraftEvidenceSession(None, 'tencent', expected)
        session.account = account('tencent')
        desc = {'description': 'private-fixture-caption', 'shortTitle': [{'shortTitle': 'private-short-title'}],
                'media': [{'url': 'fixture-video', 'coverUrl': 'fixture-portrait', 'shareCoverUrl': 'fixture-landscape'}]}
        await self.observe(session, 'post_draft', {'postReq': {'objectDesc': desc}, 'objectId': None},
                           {'errCode': 0, 'data': {'objectId': 'private-saved-id'}})
        return session, {'objectId': 'private-saved-id', 'desc': desc}

    async def test_normal_draft_list_only_reads_exact_saved_row_after_reopen_started(self):
        session, row = await self.saved_session()
        reply = {'errCode': 0, 'data': {'list': [row, {**row, 'objectId': 'other-draft'}], 'totalCount': 2}}
        diagnostic, _ = await self.observe(session, 'get_draft_list', {'pageSize': 10, 'currentPage': 1}, reply, status=201)
        self.assertEqual(session.reads, [])
        self.assertFalse(diagnostic['parsed_record'])
        session.reopen_at = float('inf')
        await self.observe(session, 'get_draft_list', {}, reply)
        self.assertEqual(session.reads, [])
        session.reopen_at = 0
        diagnostic, output = await self.observe(session, 'get_draft_list', {}, reply, status=201)
        self.assertEqual(len(session.reads), 1)
        self.assertEqual(session.reads[0]['id'], 'private-saved-id')
        self.assertFalse(session.reads[0]['fresh'])
        self.assertEqual(diagnostic['operation'], 'readlist')
        self.assertEqual(diagnostic['http_status'], 201)
        self.assertTrue(diagnostic['parsed_record'])
        self.assertNotIn('private-saved-id', output)
        self.assertNotIn('private-fixture-caption', output)
        self.assertNotIn('fixture-video', output)

    async def test_duplicate_ids_codes_and_unknown_list_shapes_fail_closed(self):
        session, row = await self.saved_session()
        variants = (
            {'errCode': 0, 'data': {'list': [row, row], 'totalCount': 2}},
            {'errCode': 0, 'data': {'list': [row, {**row, 'desc': None}], 'totalCount': 2}},
            {'errCode': 0, 'data': {'list': [{**row, 'objectId': True}], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': [{**row, 'objectId': ''}], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': [{**row, 'objectId': 'other-draft', 'desc': {}}], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': 'unknown', 'totalCount': 1}},
            {'errCode': 0, 'data': {'draftList': [row], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': [row], 'totalCount': 0}},
            {'errCode': 0, 'data': {'list': [row], 'totalCount': True}},
            {'errCode': 0, 'list': [row], 'totalCount': 1},
            {'errCode': 1, 'data': {'list': [row], 'totalCount': 1}},
            {'errCode': True, 'data': {'list': [row], 'totalCount': 1}},
            {'errCode': '0', 'data': {'list': [row], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': [{**row, 'finderUsername': 'wrong-owner'}], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': [{**row, 'objectId': 'other-draft'}], 'totalCount': 1}},
            {'errCode': 0, 'data': {'list': [{**row, 'desc': {**row['desc'], 'description': 'wrong-caption'}}], 'totalCount': 1}},
        )
        session.reopen_at = 0
        for reply in variants:
            with self.subTest(reply=reply):
                diagnostic, _ = await self.observe(session, 'get_draft_list', {}, reply)
                self.assertEqual(session.reads, [])
                self.assertFalse(diagnostic['parsed_record'])
        empty = {'errCode': 0, 'data': {'list': [], 'totalCount': 0}}
        diagnostic, _ = await self.observe(session, 'get_draft_list', {}, empty)
        self.assertFalse(diagnostic['parsed_record'])
        session.saves.clear()
        diagnostic, _ = await self.observe(session, 'get_draft_list', {}, {'errCode': 0, 'data': {'list': [row], 'totalCount': 1}})
        self.assertFalse(diagnostic['parsed_record'])
        self.assertEqual(session.reads, [])


class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.expected = minimal_expected()
        self.record = record_bili()
        self.links = [{'kind': 'video', 'sha256': 'video-bytes', 'ids': ['new-video'], 'complete': True},
                      {'kind': 'cover', 'sha256': 'cover-bytes', 'ids': ['new-cover'], 'complete': True}]

    def test_tencent_official_object_desc_and_normal_readback_share_one_schema(self):
        desc = {'description': '本次完整描述 #话题', 'shortTitle': [{'shortTitle': '完整六字短标题'}],
                'media': [{'url': 'new-video', 'thumbUrl': 'default-thumb',
                           'coverUrl': 'portrait-cover', 'shareCoverUrl': 'landscape-cover'}]}
        saved = parse_record('tencent', {'objectId': 'new-draft', 'postReq': {'objectDesc': desc}}, owner='fixture-owner')
        read = parse_record('tencent', {'objectId': 'new-draft', 'desc': desc}, owner='fixture-owner')
        self.assertEqual(saved, read)
        self.assertEqual(saved['caption'], desc['description'])
        self.assertEqual(saved['short_title'], '完整六字短标题')
        self.assertEqual(saved['cover_slots'], {'cover_portrait': ['portrait-cover'], 'cover_landscape': ['landscape-cover']})

    def test_tencent_saved_request_does_not_prove_normal_ui_reopen(self):
        desc = {'description': '完整描述', 'shortTitle': [{'shortTitle': '完整六字短标题'}],
                'media': [{'url': 'new-video', 'coverUrl': 'portrait-cover', 'shareCoverUrl': 'landscape-cover'}]}
        saved = parse_record('tencent', {'postReq': {'objectDesc': desc}}, 'new-draft', 'fixture-owner')
        saved['fresh'] = True
        expected = {'video_sha256': 'video-bytes',
                    'cover_sha256': {'cover_portrait': 'portrait-bytes', 'cover_landscape': 'landscape-bytes'},
                    'account': account('tencent'), 'platform_fields': {'caption': '完整描述', 'short_title': '完整六字短标题'}}
        links = [self.links[0], {'kind': 'cover', 'sha256': 'portrait-bytes', 'ids': ['portrait-cover'], 'complete': True},
                 {'kind': 'cover', 'sha256': 'landscape-bytes', 'ids': ['landscape-cover'], 'complete': True}]
        checks, _ = verify_evidence('tencent', expected, saved, links, account('tencent'))
        self.assertTrue(all(value for key, value in checks.items() if key != 'reopened'))
        self.assertFalse(checks['reopened'])
        checks, _ = verify_evidence('tencent', expected, saved, links, account('tencent'),
                                   reopened=dict(saved), ui_fields=True, account_after=account('tencent'))
        self.assertTrue(all(checks.values()))
        swapped = {**saved, 'cover_slots': {'cover_portrait': ['landscape-cover'], 'cover_landscape': ['portrait-cover']}}
        checks, _ = verify_evidence('tencent', expected, swapped, links, account('tencent'))
        self.assertFalse(checks['cover'])

    def test_tencent_default_thumbnail_is_not_a_custom_portrait_or_share_cover(self):
        parsed = parse_record('tencent', {'objectId': 'new-draft', 'objectDesc': {
            'description': '描述', 'media': [{'url': 'new-video', 'thumbUrl': 'default-thumb'}]}})
        self.assertEqual(parsed['cover_slots'], {'cover_portrait': [], 'cover_landscape': []})

    def test_tencent_unknown_object_desc_and_ambiguous_short_title_fail_closed(self):
        self.assertIsNone(parse_record('tencent', {'objectDesc': None, 'desc': {'description': 'hidden fallback'}}))
        for titles in ([{'shortTitle': '标题一'}, {'shortTitle': '标题二'}], [{'unknown': '标题'}], 123):
            with self.subTest(titles=titles):
                self.assertIsNone(parse_record('tencent', {'objectDesc': {'description': '描述', 'shortTitle': titles, 'media': []}}))

    def test_tencent_normal_https_url_resource_is_not_itself_upload_completion(self):
        self.assertEqual(_resource_ids({'data': {'httpsUrl': 'normal-video-resource'}}), ['normal-video-resource'])
        self.assertEqual(_upload_stage('https://fixture.invalid/upload/video?partNumber=1'), 'allocated')
        self.assertIsNone(_upload_stage('https://fixture.invalid/unknown'))

    def test_exact_new_server_record_and_readback_pass_all_six_checks(self):
        checks, reasons = verify_evidence('bilibili', self.expected, self.record, self.links, account('bilibili'),
                                         reopened=dict(self.record), ui_fields=True, account_after=account('bilibili'))
        self.assertTrue(all(checks.values()))
        self.assertEqual(reasons, [])

    def test_cover_toast_without_correlated_record_is_not_saved(self):
        checks, reasons = verify_evidence('bilibili', self.expected, None, self.links, account('bilibili'))
        self.assertFalse(any(checks.values()))
        self.assertIn('no_correlated_draft_record', reasons)

    def test_duplicate_title_with_old_media_is_rejected(self):
        old = record_bili('old-draft', 'old-video')
        checks, _ = verify_evidence('bilibili', self.expected, old, self.links, account('bilibili'),
                                    reopened=old, ui_fields=True, account_after=account('bilibili'))
        self.assertTrue(checks['fields'])
        self.assertFalse(checks['media'])

    def test_wrong_cover_or_owner_or_incomplete_caption_is_rejected(self):
        for record, owner, flag in ((record_bili(cover='old-cover'), account('bilibili'), 'cover'),
                                    (self.record, account('bilibili', 'other-owner'), 'account'),
                                    ({**self.record, 'description': '被截断'}, account('bilibili'), 'fields')):
            with self.subTest(flag=flag):
                checks, _ = verify_evidence('bilibili', self.expected, record, self.links, owner)
                self.assertFalse(checks[flag])

    def test_save_response_alone_cannot_prove_reopened(self):
        checks, _ = verify_evidence('bilibili', self.expected, self.record, self.links, account('bilibili'))
        self.assertFalse(checks['reopened'])

    def test_readback_must_have_same_server_id_and_media(self):
        for reopened in (record_bili('other-id'), record_bili(media='other-media')):
            checks, _ = verify_evidence('bilibili', self.expected, self.record, self.links, account('bilibili'),
                                       reopened=reopened, ui_fields=True, account_after=account('bilibili'))
            self.assertFalse(checks['reopened'])

    def test_unchanged_old_record_and_missing_id_are_not_verified(self):
        for changed in ({**self.record, 'fresh': False}, {**self.record, 'id': ''}):
            _, reasons = verify_evidence('bilibili', self.expected, changed, self.links, account('bilibili'),
                                         reopened=changed, ui_fields=True, account_after=account('bilibili'))
            self.assertTrue(reasons)

    def test_completed_allocation_without_completion_is_not_upload_success(self):
        links = [{**self.links[0], 'complete': False}, self.links[1]]
        checks, _ = verify_evidence('bilibili', self.expected, self.record, links, account('bilibili'))
        self.assertFalse(checks['upload_complete'])

    def test_chunk_url_ack_is_not_classified_as_whole_file_completion(self):
        self.assertEqual(_upload_stage('https://local.invalid/upload/video?partNumber=1'), 'allocated')
        self.assertEqual(_upload_stage('https://local.invalid/upload/image?chunk_index=0'), 'allocated')
        self.assertEqual(_upload_stage('https://local.invalid/upload/finish'), 'complete')

    def test_xhs_literal_hashtags_require_registered_topic_records(self):
        expected = minimal_expected('xiaohongshu')
        raw = {'draftId': 'xhs-current', 'uid': 'fixture-owner', 'content': {'draftStore': {
            'title': '重复标题', 'desc': '完整正文\n#话题', 'hashTag': [],
            'video': {'fileid': 'new-video'}, 'cover': {'fileid': 'new-cover'}}}}
        parsed = parse_record('xiaohongshu', raw)
        self.assertFalse(field_match('xiaohongshu', parsed, expected))
        raw['content']['draftStore']['hashTag'] = [{'name': '话题', 'id': 'topic-1'}]
        self.assertTrue(field_match('xiaohongshu', parse_record('xiaohongshu', raw), expected))

    def test_xhs_effective_cover_prefers_current_context_over_stale_first_frame(self):
        raw = {'draftId': 'xhs-current', 'uid': 'fixture-owner', 'content': {
            'draftStore': {'video': {'fileid': 'video'}, 'cover': {'fileid': 'old-frame'},
                           'pkCoverList': [{'fileid': 'unselected-preview'}]},
            'contextStore': {'coverContext': {'cover': {'fileid': 'current-custom'}}}}}
        parsed = parse_record('xiaohongshu', raw)
        self.assertEqual(parsed['cover_ids'], ['current-custom'])
        self.assertEqual(parsed['cover_slots']['cover'], ['current-custom'])
        raw['content']['contextStore']['coverContext']['cover'] = {'width': 10}
        self.assertEqual(parse_record('xiaohongshu', raw)['cover_ids'], ['old-frame'])

    def test_finite_publish_guard_does_not_block_drafts_upload_or_read_requests(self):
        blocked = ['/web/api/media/aweme/create_v2/', '/x/vu/web/add',
                   '/rest/cp/works/v3/video/pc/publish', '/rest/cp/works/v3/video/pc/submit', '/post/post_create',
                   '/micro/content/cgi-bin/mmfinderassistant-bin/post/post_create',
                   '/micro/content/cgi-bin/mmfinderassistant-bin/post/post_update',
                   '/micro/content/cgi-bin/mmfinderassistant-bin/post/post_publish']
        allowed = ['/rest/cp/works/v3/video/pc/publishInfo/snapshot/save',
                   '/x/vupre/web/draft/add', '/upload/video', '/post/post_draft',
                   '/micro/content/cgi-bin/mmfinderassistant-bin/post/post_draft',
                   '/micro/content/cgi-bin/mmfinderassistant-bin/post/get_draft_list']
        for path in blocked:
            self.assertTrue(is_publish_request('https://local.invalid' + path, 'POST'))
            self.assertFalse(is_publish_request('https://local.invalid' + path, 'GET'))
        for path in allowed:
            self.assertFalse(is_publish_request('https://local.invalid' + path, 'POST'))

    def test_cropped_cover_with_same_filename_size_but_different_bytes_is_rejected(self):
        session = DraftEvidenceSession(None, 'bilibili', self.expected)
        session.assets = {'cover': {'name': 'cover.png', 'size': 20, 'sha256': 'cover-bytes', 'kind': 'cover'}}
        session.files = [{'name': 'cover.png', 'size': 20, 'sha256': 'cover-bytes', 'selected_at': 1}]
        session.exchanges = [{'stage': 'complete', 'ids': ['new-cover'], 'metadata': ('cover.png', 20),
                              'request_ids': [], 'observed_at': 2, 'body_sha256': 'cropped-bytes'}]
        self.assertEqual(session.upload_links(), [])
        session.exchanges[0]['body_sha256'] = 'cover-bytes'
        self.assertEqual(session.upload_links()[0]['ids'], ['new-cover'])

    def test_two_cover_orientations_must_both_exist_in_saved_record(self):
        expected = {'video_sha256': 'video-bytes', 'cover_sha256': {'cover_portrait': 'cover-bytes', 'cover_landscape': 'cover-bytes'},
                    'account': account(), 'platform_fields': {'title': '重复标题', 'caption': '全文'}}
        raw = {'cache': {'itemTitle': '重复标题', 'textResult': {'text': '全文'},
                         'longFileInfo': {'vid': 'new-video'}, 'coverInfo': {'uri': 'new-cover'}}}
        parsed = parse_record('douyin', raw, 'new-draft', 'fixture-owner')
        parsed['fresh'] = True
        checks, _ = verify_evidence('douyin', expected, parsed, self.links, account())
        self.assertFalse(checks['cover'])
        raw['cache']['coverHorizontalInfo'] = {'uri': 'new-cover'}
        parsed = parse_record('douyin', raw, 'new-draft', 'fixture-owner')
        parsed['fresh'] = True
        checks, _ = verify_evidence('douyin', expected, parsed, self.links, account())
        self.assertTrue(checks['cover'])

    def test_ks_finish_ack_links_its_observed_request_file_id_without_repeating_id(self):
        class Request:
            post_data_json = {'fileId': 'allocated-current'}
            method = 'POST'
        class Response:
            url = 'https://cp.kuaishou.com/rest/cp/works/v3/video/pc/upload/finish'
            status = 200
            request = Request()
            async def json(self):
                return {'result': 1}
        async def scenario():
            session = DraftEvidenceSession(None, 'kuaishou', self.expected)
            session.assets = {'video': {'name': 'video.mp4', 'size': 123, 'sha256': 'video-bytes', 'kind': 'video'}}
            session.files = [{'name': 'video.mp4', 'size': 123, 'sha256': 'video-bytes', 'selected_at': 0}]
            session.exchanges = [{'stage': 'allocated', 'ids': ['allocated-current'], 'metadata': ('video.mp4', 123),
                                  'request_ids': [], 'observed_at': 1}]
            await session._observe(Response())
            self.assertEqual(session.upload_links()[0]['ids'], ['allocated-current'])
        asyncio.run(scenario())


class CheckpointFixtureTests(unittest.IsolatedAsyncioTestCase):
    """Attested synthetic upload chains; no page/network/account is operated."""
    class Page:
        frames = []
        _sau_file_binding = True

        def on(self, *args):
            pass

        def remove_listener(self, *args):
            pass

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.video = Path(self.directory.name) / 'checkpoint-fixture.mp4'
        self.video.write_bytes(bytes(range(256)) * 4301 + b'full hash tail')
        self.cover = Path(self.directory.name) / 'checkpoint-cover.png'
        self.cover.write_bytes(b'synthetic-cover')
        self.key = bytes(range(32))
        self.owner = account('xiaohongshu')
        self.original = self.expected()

    async def asyncTearDown(self):
        self.directory.cleanup()

    def expected(self, platform='xiaohongshu', *, custom_cover=False, description='checkpoint private full description'):
        payload = {'video': str(self.video), 'title': 'checkpoint title', 'description': description, 'tags': ['checkpoint-topic']}
        if custom_cover:
            payload['cover'] = str(self.cover)
        checked = preflight_payload(payload, [platform])
        self.assertEqual(checked['errors'], [])
        expected = build_platform_payload(checked, platform, 'checkpoint-current-job', 'checkpoint-current-nonce')
        expected['account'] = account(platform)
        return expected

    async def session(self, expected=None, *, proven=False):
        expected = self.original if expected is None else expected
        page = self.Page()
        session = DraftEvidenceSession(page, expected['platform_id'], expected, account_reader=lambda: dict(self.owner))
        page._sau_draft_evidence = session
        # This fixture proves sealing/restoring an attested synthetic chain.
        # Real native SDK/realm observers have their own browser fixtures;
        # the intentionally browser-free Page cannot initialize those APIs.
        observers = {
            'douyin': ('uploader.dy_evidence.install_dy_observer',),
            'xiaohongshu': ('uploader.xhs_evidence.install_xhs_observer',),
            'tencent': ('uploader.tc_cover_evidence.install_tc_cover_observer',
                        'uploader.tc_processing.install_tc_processing_observer'),
            'kuaishou': ('uploader.ks_evidence.install_ks_observer',),
            'bilibili': ('uploader.bili_evidence.install_bili_observer',),
        }
        with ExitStack() as stack:
            for target in observers.get(session.platform, ()):
                stack.enter_context(patch(target, new=AsyncMock()))
            await session.initialize()
        if proven:
            await self.prove_upload(session)
        return session

    async def prove_upload(self, session, role='video', resource='checkpoint-upload-id'):
        asset = session.assets[role]
        selected = session.started_at + .001
        session.files.append({**asset, 'selected_at': selected, 'type': 'video/mp4' if role == 'video' else 'image/png'})
        session.exchanges.append({'stage': 'complete', 'ids': [resource], 'metadata': (asset['name'], asset['size']),
                                  'request_ids': [], 'body_sha256': asset['sha256'] if role != 'video' else None,
                                  'observed_at': selected + .001})

    def media(self, resource='checkpoint-upload-id', count=1):
        return lambda: {'source': 'platform_composer', 'video_count': count, 'video_ids': [resource]}

    def resign(self, checkpoint):
        encoded = json.dumps(checkpoint['payload'], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        checkpoint['mac'] = hmac.new(self.key, encoded, hashlib.sha256).hexdigest()
        return checkpoint

    async def checkpoint(self):
        session = await self.session(proven=True)
        return await session.export_checkpoint(seal_key=self.key)

    async def test_export_restore_keeps_video_proof_without_claiming_save(self):
        old = await self.session(proven=True)
        checkpoint = await old.export_checkpoint(seal_key=self.key)
        restored = await self.session()
        result = await restored.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        self.assertEqual(result, {'restored': True, 'video_upload_verified': True, 'save_verified': False})
        self.assertEqual(restored.upload_links(), old.upload_links())
        self.assertEqual(restored.files, [])
        self.assertEqual(restored.exchanges, [])
        # Later save/reopen/account gates still require their own evidence.
        record = parse_record('xiaohongshu', {'draftId': 'new-draft', 'uid': 'fixture-owner', 'content': {'draftStore': {
            'title': self.original['platform_fields']['title'], 'desc': self.original['platform_fields']['description'] + '\n#checkpoint-topic',
            'hashTag': [{'name': 'checkpoint-topic'}], 'video': {'fileid': 'checkpoint-upload-id'},
            'cover': {'fileid': 'default-cover'}}}})
        record['fresh'] = True
        checks, _ = verify_evidence('xiaohongshu', self.original, record, restored.upload_links(), self.owner)
        self.assertTrue(all(value for flag, value in checks.items() if flag != 'reopened'))
        self.assertFalse(checks['reopened'])
        checks, reasons = verify_evidence('xiaohongshu', self.original, record, restored.upload_links(), self.owner,
                                         reopened=dict(record), ui_fields=True, account_after=self.owner)
        self.assertTrue(all(checks.values()))
        self.assertEqual(reasons, [])

    async def test_export_contains_hash_baseline_and_no_private_bodies_or_paths(self):
        session = await self.session(proven=True)
        session.baseline = {'old-local-id': 'old full private draft body'}
        session.saves = [{'caption': 'private save body', 'token': 'secret-token'}]
        session.reads = [{'caption': 'private read body', 'cookie': 'secret-cookie'}]
        session.exchanges[0]['token'] = 'secret-permit'
        checkpoint = await session.export_checkpoint(seal_key=self.key)
        serialized = json.dumps(checkpoint, ensure_ascii=False)
        for value in ('checkpoint private full description', 'checkpoint title', 'checkpoint-topic',
                      'old full private draft body', 'private save body', 'private read body',
                      'secret-token', 'secret-cookie', 'secret-permit', str(self.directory.name)):
            self.assertNotIn(value, serialized)
        self.assertEqual(checkpoint['payload']['baseline_hashes']['old-local-id'], hashlib.sha256(b'old full private draft body').hexdigest())
        self.assertEqual(set(checkpoint['payload']['assets']['video']), {'name', 'size', 'sha256', 'kind'})

    async def test_no_allocation_chunk_old_or_ambiguous_selection_can_export(self):
        for kind in ('none', 'allocated', 'ambiguous', 'wrong_hash', 'other_resource'):
            with self.subTest(kind=kind):
                session = await self.session(proven=kind != 'none')
                if kind == 'allocated':
                    session.exchanges[0]['stage'] = 'allocated'
                elif kind == 'ambiguous':
                    session.files.append(dict(session.files[0]))
                elif kind == 'wrong_hash':
                    session.files[0]['sha256'] = '0' * 64
                elif kind == 'other_resource':
                    session.exchanges.append({**session.exchanges[0], 'ids': ['another-allocation-id']})
                with self.assertRaises(CheckpointError):
                    await session.export_checkpoint(seal_key=self.key)

    async def test_seal_tamper_wrong_key_unsigned_and_unknown_version_fail(self):
        original = await self.checkpoint()
        cases = [({}, self.key), ({**original, 'checkpoint_version': 2}, self.key), (original, b'z' * 32)]
        altered = json.loads(json.dumps(original))
        altered['payload']['upload_links'][0]['ids'] = ['old-video']
        cases.append((altered, self.key))
        for checkpoint, key in cases:
            with self.subTest(checkpoint=checkpoint.get('checkpoint_version'), wrong_key=key != self.key):
                fresh = await self.session()
                with self.assertRaises(CheckpointError):
                    await fresh.restore_checkpoint(checkpoint, seal_key=key, media_reader=self.media())
                self.assertEqual(fresh.upload_links(), [])

    async def test_signed_unknown_fields_invalid_assets_and_untrusted_resources_fail(self):
        for mutate in (
            lambda p: p.update(cookie='forbidden'),
            lambda p: p['assets']['video'].update(size=True),
            lambda p: p['assets']['video'].update(name='/absolute/path'),
            lambda p: p['account'].update(token='forbidden'),
            lambda p: p['baseline_hashes'].update(old='unhashed content'),
            lambda p: p['upload_links'][0].update(complete=False),
            lambda p: p['upload_links'][0].update(ids=['https://cdn.invalid/file?X-Amz-Signature=secret']),
            lambda p: p['upload_links'][0].update(raw_body='private'),
            lambda p: p['upload_links'].clear(),
        ):
            checkpoint = await self.checkpoint()
            mutate(checkpoint['payload'])
            self.resign(checkpoint)
            with self.assertRaises(CheckpointError):
                validate_checkpoint(checkpoint, seal_key=self.key, expected=self.original)

    async def test_resources_strip_signed_urls_without_persisting_auth(self):
        session = await self.session(proven=True)
        session.exchanges[0]['ids'].append('https://cdn.invalid/video?token=secret-auth')
        checkpoint = await session.export_checkpoint(seal_key=self.key)
        self.assertNotIn('secret-auth', json.dumps(checkpoint))
        self.assertEqual(checkpoint['payload']['upload_links'][0]['ids'], ['checkpoint-upload-id'])
        session.exchanges[0]['ids'] = ['https://cdn.invalid/video?token=secret-auth']
        with self.assertRaises(CheckpointError):
            await session.export_checkpoint(seal_key=self.key)

    async def test_original_nonce_job_account_key_platform_and_new_content_cannot_resume(self):
        checkpoint = await self.checkpoint()
        for field in ('job_id', 'receipt_nonce', 'account_key', 'platform_id'):
            expected = json.loads(json.dumps(self.original))
            expected[field] = 'different-' + str(expected[field])
            with self.subTest(field=field), self.assertRaises(CheckpointError):
                validate_checkpoint(checkpoint, seal_key=self.key, expected=expected)
        changed = self.expected(description='new private content')
        with self.assertRaises(CheckpointError):
            validate_checkpoint(checkpoint, seal_key=self.key, expected=changed)

    async def test_full_sha_change_after_fresh_initialize_refuses_restore(self):
        checkpoint = await self.checkpoint()
        fresh = await self.session()
        data = self.video.read_bytes()
        self.video.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        self.assertEqual(self.video.stat().st_size, len(data))
        with self.assertRaisesRegex(CheckpointError, 'checkpoint_asset_mismatch'):
            await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        self.assertEqual(fresh.upload_links(), [])

    async def test_account_switch_and_unverified_owner_refuse_restore(self):
        checkpoint = await self.checkpoint()
        for owner in ({**self.owner, 'verified': False}, account('xiaohongshu', 'other-owner'),
                      {**self.owner, 'uid': 'other-uid'}):
            self.owner = account('xiaohongshu')
            fresh = await self.session()
            self.owner = owner
            with self.assertRaisesRegex(CheckpointError, 'checkpoint_account_mismatch'):
                await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        self.owner = account('xiaohongshu')

    async def test_composer_confirmation_is_required_and_does_not_make_upload_proof(self):
        checkpoint = await self.checkpoint()
        for media in (None, lambda: {}, self.media('old-video'), self.media(count=0), self.media(count=2),
                      lambda: {'source': 'list_row', 'video_count': 1, 'video_ids': ['checkpoint-upload-id']},
                      lambda: {'source': 'platform_composer', 'video_count': True, 'video_ids': ['checkpoint-upload-id']}):
            fresh = await self.session()
            with self.assertRaises(CheckpointError):
                await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=media)
            self.assertEqual(fresh.upload_links(), [])
        checkpoint['payload']['upload_links'] = []
        self.resign(checkpoint)
        fresh = await self.session()
        with self.assertRaises(CheckpointError):
            await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())

    async def test_expiry_future_time_and_non_finite_or_boolean_times_fail(self):
        checkpoint = await self.checkpoint()
        with self.assertRaisesRegex(CheckpointError, 'checkpoint_expired'):
            validate_checkpoint(checkpoint, seal_key=self.key, expected=self.original, now=checkpoint['payload']['expires_at'])
        for field, value in (('origin_at', True), ('origin_at', -1), ('expires_at', 10 ** 20),
                             ('exported_at', checkpoint['payload']['expires_at'] + 1)):
            altered = json.loads(json.dumps(checkpoint))
            altered['payload'][field] = value
            self.resign(altered)
            with self.assertRaises(CheckpointError):
                validate_checkpoint(altered, seal_key=self.key, expected=self.original)
        with self.assertRaises(CheckpointError):
            validate_checkpoint(checkpoint, seal_key=self.key, expected=self.original, now=float('nan'))
        with self.assertRaises(CheckpointError):
            validate_checkpoint(checkpoint, seal_key=self.key, expected=self.original,
                                now=checkpoint['payload']['exported_at'] - 61)

    async def test_reexport_preserves_original_age_and_key_ttl_limits(self):
        checkpoint = await self.checkpoint()
        fresh = await self.session()
        await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        again = await fresh.export_checkpoint(seal_key=self.key)
        self.assertEqual(again['payload']['origin_at'], checkpoint['payload']['origin_at'])
        self.assertEqual(again['payload']['expires_at'], checkpoint['payload']['expires_at'])
        for key, ttl in ((b'short', 60), (self.key, 0), (self.key, CHECKPOINT_MAX_AGE + 1), (self.key, True)):
            with self.assertRaises(CheckpointError):
                await fresh.export_checkpoint(seal_key=key, ttl_seconds=ttl)

    async def test_explicit_save_closed_issues_and_nonfresh_restore_refuse(self):
        checkpoint = await self.checkpoint()
        for state in ('save', 'closed', 'issues', 'new_upload'):
            session = await self.session(proven=state == 'new_upload')
            if state == 'save':
                session.action_at = session.started_at
            elif state == 'closed':
                session.closed = True
            elif state == 'issues':
                session.issues.append('unknown_schema')
            with self.assertRaises(CheckpointError):
                await session.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
            if state != 'new_upload':
                with self.assertRaises(CheckpointError):
                    await session.export_checkpoint(seal_key=self.key)

    async def test_original_baseline_hash_excludes_unchanged_old_draft(self):
        record = {'draftId': 'old-id', 'uid': 'fixture-owner', 'content': {'draftStore': {
            'title': self.original['platform_fields']['title'], 'desc': self.original['platform_fields']['description'] + '\n#checkpoint-topic',
            'hashTag': [{'name': 'checkpoint-topic'}], 'video': {'fileid': 'checkpoint-upload-id'}, 'cover': {'fileid': 'default-cover'}}}}
        old = await self.session(proven=True)
        serialized = json.dumps(record)
        old.baseline = {'old-id': serialized}
        checkpoint = await old.export_checkpoint(seal_key=self.key)
        fresh = await self.session()
        fresh.baseline = {'a-draft-created-during-original-operation': 'present at new worker start'}
        await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        rows = [{'key': 'old-id', 'serialized': serialized, 'raw': record, 'owner': 'fixture-owner', 'frame_url': 'http://fixture.invalid'}]
        with patch('uploader.draft_evidence.read_local_records', return_value=rows):
            self.assertIsNone(await fresh.candidate())
        record['draftId'] = 'new-id'
        rows[0].update(key='new-id', serialized=json.dumps(record))
        with patch('uploader.draft_evidence.read_local_records', return_value=rows):
            self.assertEqual((await fresh.candidate())['id'], 'new-id')

    async def test_new_video_selection_invalidates_checkpoint_even_same_bytes(self):
        checkpoint = await self.checkpoint()
        fresh = await self.session()
        await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        fresh.files.append({**fresh.assets['video'], 'type': 'video/mp4', 'selected_at': fresh._checkpoint_restored_at + .01})
        self.assertEqual(fresh.upload_links(), [])
        with self.assertRaises(CheckpointError):
            await fresh.export_checkpoint(seal_key=self.key)

    async def test_native_new_cover_does_not_invalidate_existing_video_proof(self):
        checkpoint = await self.checkpoint()
        fresh = await self.session()
        await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
        fresh.files.append({'name': 'unrelated.png', 'size': 1, 'type': 'image/png', 'sha256': '0' * 64,
                            'selected_at': fresh._checkpoint_restored_at + .01})
        self.assertEqual(fresh.upload_links(), checkpoint['payload']['upload_links'])

    async def test_completed_cover_and_observer_video_links_are_captured_after_drain(self):
        expected = self.expected(custom_cover=True)
        session = await self.session(expected)
        await self.prove_upload(session, role='cover', resource='current-cover-id')
        class Observer:
            drained = False
            closed = False

            async def drain(self):
                self.drained = True

            def upload_links(self):
                return [{'kind': 'video', 'sha256': expected['video_sha256'], 'ids': ['sdk-current-id'], 'complete': True}] if self.drained else []

            async def close(self):
                self.closed = True
        observer = Observer()
        session.xhs_observer = observer
        checkpoint = await session.export_checkpoint(seal_key=self.key)
        self.assertTrue(observer.drained)
        self.assertEqual({link['kind'] for link in checkpoint['payload']['upload_links']}, {'video', 'cover'})
        fresh = await self.session(expected)
        await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media('sdk-current-id'))
        self.assertEqual(len(fresh.upload_links()), 2)
        await session.close()
        self.assertTrue(observer.closed)

    async def test_pure_validator_has_no_file_or_live_account_dependency_and_returns_copy(self):
        checkpoint = await self.checkpoint()
        with patch('uploader.draft_evidence._sha_file', side_effect=AssertionError('file read forbidden')):
            payload = validate_checkpoint(checkpoint, seal_key=self.key, expected=self.original)
        payload['upload_links'][0]['ids'].append('mutated-copy')
        self.assertNotIn('mutated-copy', checkpoint['payload']['upload_links'][0]['ids'])

    async def test_same_checkpoint_contract_applies_to_all_five_platforms(self):
        for platform in ('douyin', 'xiaohongshu', 'tencent', 'kuaishou', 'bilibili'):
            with self.subTest(platform=platform):
                self.owner = account(platform)
                expected = self.expected(platform)
                old = await self.session(expected, proven=True)
                checkpoint = await old.export_checkpoint(seal_key=self.key)
                fresh = await self.session(expected)
                result = await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=self.media())
                self.assertTrue(result['video_upload_verified'])
                self.assertEqual(fresh.upload_links(), old.upload_links())

    async def test_account_switch_during_composer_probe_refuses_atomic_restore(self):
        checkpoint = await self.checkpoint()
        fresh = await self.session()
        def switch():
            self.owner = account('xiaohongshu', 'new-owner-during-probe')
            return self.media()()
        with self.assertRaises(CheckpointError):
            await fresh.restore_checkpoint(checkpoint, seal_key=self.key, media_reader=switch)
        self.assertEqual(fresh.upload_links(), [])


class TencentCdnFixtureTests(unittest.IsolatedAsyncioTestCase):
    """Observed official CDN shapes only; no platform/network/account calls."""

    class Request:
        def __init__(self, method, headers, body=None, raw=None, forbid_raw=False):
            self.method, self.headers, self.body = method, headers, body
            self.raw, self.forbid_raw, self.raw_reads = raw, forbid_raw, 0

        @property
        def post_data_json(self):
            if self.body is None:
                raise ValueError('not JSON')
            return self.body

        @property
        def post_data(self):
            return json.dumps(self.body) if self.body is not None else None

        @property
        def post_data_buffer(self):
            self.raw_reads += 1
            if self.forbid_raw:
                raise AssertionError('video binary must not be re-read')
            return self.raw

    class Response:
        def __init__(self, path, request, data, host='finder.video.qq.com', status=200):
            self.url, self.request, self.data, self.status = 'https://' + host + path, request, data, status

        async def json(self):
            return self.data

    def setUp(self):
        self.payload = b'complete synthetic video bytes'
        self.resource = 'http://wxapp.tc.qq.com/fixture/current-video?resource=exact'
        self.alias = 'https://finder.video.qq.com/fixture/current-video?resource=exact'
        self.diagnostics = []

    def session(self, kind='video'):
        session = DraftEvidenceSession(None, 'tencent', minimal_expected('tencent'))
        session.started_at = 0
        asset = {'name': '中文素材.mp4' if kind == 'video' else 'cover.png', 'size': len(self.payload),
                 'sha256': hashlib.sha256(self.payload).hexdigest(), 'kind': kind}
        session.assets = {kind: asset}
        session.files = [{**asset, 'selected_at': 1}]
        return session

    def headers(self, session, task):
        asset = next(iter(session.assets.values()))
        return {'x-arguments': urlencode({'filekey': asset['name'], 'filesize': asset['size'], 'taskid': task}),
                'content-type': 'application/json', 'authorization': 'private-fixture-token'}

    async def observe(self, session, response):
        captured = io.StringIO()
        with redirect_stdout(captured):
            await session._observe(response)
        self.diagnostics.append(captured.getvalue())
        return response

    async def allocation(self, session, task='task-a', upload='upload-a', count=2, query='', data=None, body=None):
        size = next(iter(session.assets.values()))['size']
        ends = [size] if count == 1 else [size // 2, size]
        request = self.Request('PUT', self.headers(session, task),
                               body if body is not None else {'BlockSum': count, 'BlockPartLength': ends})
        return await self.observe(session, self.Response('/applyuploaddfs' + query, request,
                                  data if data is not None else {'UploadID': upload}))

    async def part(self, session, number, task='task-a', upload='upload-a', data=None, raw=None):
        headers = {**self.headers(session, task), 'content-type': 'application/octet-stream'}
        request = self.Request('PUT', headers, raw=self.payload if raw is None else raw,
                               forbid_raw=next(iter(session.assets.values()))['kind'] == 'video')
        response = self.Response('/uploadpartdfs?' + urlencode({'PartNumber': number, 'UploadID': upload, 'QuickUpload': 0}),
                                 request, data if data is not None else
                                 {'X-Errno': 0, 'ETag': f'{task}-etag-{number}', 'TransFlag': f'{task}-flag'})
        return await self.observe(session, response)

    async def finish(self, session, task='task-a', upload='upload-a', count=2, resource=None, parts=None, flag=None, data=None):
        request = self.Request('POST', self.headers(session, task), {
            'TransFlag': flag if flag is not None else f'{task}-flag',
            'PartInfo': parts if parts is not None else [{'PartNumber': n, 'ETag': f'{task}-etag-{n}'} for n in range(1, count + 1)]})
        response = self.Response('/completepartuploaddfs?' + urlencode({'UploadID': upload}), request,
                                 data if data is not None else {'DownloadURL': resource or self.resource})
        return await self.observe(session, response)

    async def chain(self, session, task='task-a', upload='upload-a', resource=None):
        await self.allocation(session, task, upload)
        responses = [await self.part(session, number, task, upload) for number in (1, 2)]
        await self.finish(session, task, upload, resource=resource)
        return responses

    async def test_signed_resource_identity_requires_full_chain_and_survives_auth_rotation(self):
        from uploader.draft_evidence import _checkpoint_links, parse_record
        from uploader.draft_only import _asset_id_matches
        from uploader.tc_resource import tc_resource_identity
        resource = ('http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=opaque-file&m='
                    '&uzid=opaque-owner&bizid=1023&hy=SH&idx=1&token=private-fixture-a&sign=private-sign-a')
        rotated = resource.replace('http://wxapp.tc.qq.com', 'https://finder.video.qq.com').replace(
            'private-fixture-a', 'private-fixture-b').replace('private-sign-a', 'private-sign-b')
        identity = tc_resource_identity(resource)
        self.assertIsNotNone(identity)
        session = self.session()
        await self.allocation(session)
        await self.finish(session, resource=resource)
        self.assertEqual(session.upload_links(), [])  # A terminal reply alone is insufficient.
        session = self.session()
        await self.chain(session, resource=resource)
        links = _checkpoint_links(session.upload_links(), filter_resources=True)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['ids'], [identity])
        self.assertNotIn('private-', json.dumps(links))
        record = parse_record('tencent', {'objectId':'fixture-draft', 'postReq':{
            'objectDesc':{'description':'fixture-caption', 'media':[{'url':rotated}]}}})
        self.assertEqual(record['video_ids'], [identity])
        self.assertTrue(_asset_id_matches(rotated, identity))
        self.assertFalse(_asset_id_matches(rotated.replace('opaque-file', 'different-file'), identity))

    async def test_raw_sdk_upload_id_preserves_literal_plus_and_decodes_percent_once(self):
        from uploader.draft_evidence import _tc_cdn_query
        for upload_id in ('opaque+a/==', 'opaque%2B+a'):
            with self.subTest(upload_id=upload_id):
                session = self.session()
                await self.allocation(session, upload=upload_id)
                for number in (1, 2):
                    request = self.Request('PUT', self.headers(session, 'task-a'))
                    response = self.Response('/uploadpartdfs?PartNumber=' + str(number) + '&UploadID=' + upload_id,
                        request, {'X-Errno':0,'ETag':f'task-a-etag-{number}','TransFlag':'task-a-flag'})
                    if '%' in upload_id:
                        response.url = response.url.replace('%2B', '%252B')
                    await self.observe(session, response)
                request = self.Request('POST', self.headers(session, 'task-a'), {'TransFlag':'task-a-flag',
                    'PartInfo':[{'PartNumber':n,'ETag':f'task-a-etag-{n}'} for n in (1,2)]})
                response = self.Response('/completepartuploaddfs?UploadID=' + upload_id, request,
                                         {'DownloadURL':self.resource})
                if '%' in upload_id:
                    response.url = response.url.replace('%2B', '%252B')
                await self.observe(session, response)
                self.assertEqual(len(session.upload_links()), 1)
        self.assertIsNone(_tc_cdn_query('UploadID=a+&UploadID=a%2B'))

    async def sns(self, session, start, end, *, finish=False, task='sns-current', resource=None, duplicate_field=False):
        asset = next(iter(session.assets.values()))
        fields = [('filekey', asset['name']), ('totalsize', str(asset['size'])), ('fileuuid', task),
                  ('rangestart', str(start)), ('rangeend', str(end - 1))]
        if duplicate_field:
            fields.append(('fileuuid', task))
        boundary = 'fixture-boundary'
        raw = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                       for key, value in fields)
        raw += (f'--{boundary}\r\nContent-Disposition: form-data; name="filedata"; filename="blob"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + self.payload[start:end]
        raw += f'\r\n--{boundary}--\r\n'.encode()
        request = self.Request('POST', {'content-type': f'multipart/form-data; boundary={boundary}'}, raw=raw)
        return await self.observe(session, self.Response('/snsuploadbig', request,
                                  {'retcode': 0, 'uploadfinish': int(finish), 'fileurl': resource or self.resource}))

    async def test_dfs_new_allocation_parts_and_finish_use_exact_cdn_aliases(self):
        session = self.session()
        responses = await self.chain(session)
        links = session.upload_links()
        self.assertEqual(links, [{'kind': 'video', 'sha256': hashlib.sha256(self.payload).hexdigest(),
                                'ids': sorted((self.resource, self.alias)), 'complete': True}])
        self.assertTrue(all(response.request.raw_reads == 0 for response in responses))
        diagnostics = ''.join(self.diagnostics)
        for private in (self.resource, 'task-a', 'upload-a', 'private-fixture-token', '中文素材.mp4'):
            self.assertNotIn(private, diagnostics)
        self.assertIn('DRAFT_DIAGNOSTIC=', diagnostics)

    async def test_dfs_assistance_hosts_bind_one_actual_allocation_and_resource(self):
        session = self.session()
        request = self.Request('PUT', self.headers(session, 'task-a'),
                               {'BlockSum': 2, 'BlockPartLength': [len(self.payload) // 2, len(self.payload)]})
        await self.observe(session, self.Response('/applyuploaddfs', request, {'UploadID': 'upload-a'},
                                                 host='finderassistancea.video.qq.com'))
        for number, host in ((1, 'finderassistancea.video.qq.com'), (2, 'finderassistancec.video.qq.com')):
            request = self.Request('PUT', self.headers(session, 'task-a'), forbid_raw=True)
            await self.observe(session, self.Response('/uploadpartdfs?' + urlencode({'PartNumber': number, 'UploadID': 'upload-a'}),
                                request, {'X-Errno': 0, 'ETag': f'task-a-etag-{number}', 'TransFlag': 'task-a-flag'}, host=host))
        request = self.Request('POST', self.headers(session, 'task-a'), {'TransFlag': 'task-a-flag',
                               'PartInfo': [{'PartNumber': n, 'ETag': f'task-a-etag-{n}'} for n in (1, 2)]})
        await self.observe(session, self.Response('/completepartuploaddfs?UploadID=upload-a', request,
                                                 {'DownloadURL': self.resource}, host='finderassistancea.video.qq.com'))
        self.assertEqual(len(session.exchanges), 4)
        self.assertEqual(session.upload_links(), [{'kind': 'video', 'sha256': hashlib.sha256(self.payload).hexdigest(),
                                                'ids': sorted((self.resource, self.alias)), 'complete': True}])

    def test_tc_cdn_only_exact_observed_hosts_without_redirect_ports_or_subdomains(self):
        for host in ('finder.video.qq.com', 'wxapp.tc.qq.com', 'finderassistancea.video.qq.com', 'finderassistancec.video.qq.com'):
            with self.subTest(host=host):
                self.assertEqual(_tc_cdn_endpoint('https://' + host + '/applyuploaddfs'), 'allocate')
        for host in ('finderassistancea.video.qq.com.evil.invalid', 'sub.finderassistancea.video.qq.com',
                     'finderassistanceb.video.qq.com', 'evil.invalid', 'finderassistancea.video.qq.com:443',
                     'user@finderassistancea.video.qq.com'):
            with self.subTest(host=host):
                self.assertIsNone(_tc_cdn_endpoint('https://' + host + '/applyuploaddfs'))
                self.assertEqual(_tc_resource_aliases('https://' + host + '/resource'), [])
        self.assertIsNone(_tc_cdn_endpoint('https://evil.invalid/redirect?to=https://finderassistancea.video.qq.com/applyuploaddfs'))
        self.assertIsNone(_tc_cdn_endpoint('https://finderassistancea.video.qq.com/redirect?to=/applyuploaddfs'))

    async def test_header_diagnostic_compares_actual_async_headers_without_leaking_or_changing_gate(self):
        session = self.session()
        actual_headers = self.headers(session, 'task-a')
        class ActualHeadersRequest(self.Request):
            async def all_headers(self):
                return actual_headers
        request = ActualHeadersRequest('POST', {}, {'TransFlag': 'task-a-flag',
                                       'PartInfo': [{'PartNumber': 1, 'ETag': 'task-a-etag-1'}]})
        await self.observe(session, self.Response('/completepartuploaddfs?UploadID=upload-a', request,
                                                 {'DownloadURL': self.resource}, host='finderassistancea.video.qq.com'))
        diagnostic = json.loads(self.diagnostics[-1].split('=', 1)[1])
        self.assertTrue(diagnostic['endpoint_allowed'])
        self.assertFalse(diagnostic['provisional_arguments_present'])
        self.assertFalse(diagnostic['provisional_metadata_valid'])
        self.assertTrue(diagnostic['actual_headers_available'])
        self.assertTrue(diagnostic['actual_arguments_present'])
        self.assertTrue(diagnostic['actual_metadata_valid'])
        self.assertEqual(session.exchanges, [])
        for private in (self.resource, 'task-a', 'upload-a', 'private-fixture-token', '中文素材.mp4'):
            self.assertNotIn(private, self.diagnostics[-1])

    async def test_async_header_diagnostic_failure_preserves_business_evidence(self):
        session = self.session()
        class FailedActualHeadersRequest(self.Request):
            async def all_headers(self):
                raise RuntimeError('fixture-only-secret')
        await self.allocation(session, count=1)
        await self.part(session, 1)
        request = FailedActualHeadersRequest('POST', self.headers(session, 'task-a'), {'TransFlag': 'task-a-flag',
                                            'PartInfo': [{'PartNumber': 1, 'ETag': 'task-a-etag-1'}]})
        await self.observe(session, self.Response('/completepartuploaddfs?UploadID=upload-a', request,
                                                 {'DownloadURL': self.resource}, host='finderassistancea.video.qq.com'))
        self.assertEqual(len(session.upload_links()), 1)
        diagnostic = json.loads(self.diagnostics[-1].split('=', 1)[1])
        self.assertFalse(diagnostic['actual_headers_available'])
        self.assertNotIn('fixture-only-secret', self.diagnostics[-1])

    async def test_sns_diagnostic_does_not_interpret_multipart_body_as_field_names(self):
        original = self.Request
        class TextMultipartRequest(original):
            @property
            def post_data(self):
                return self.raw.decode('utf-8', errors='replace') if self.raw else super().post_data
        self.Request = TextMultipartRequest
        self.payload = b'private-raw-content&credential=private-raw-secret'
        session = self.session()
        await self.sns(session, 0, len(self.payload), finish=True)
        diagnostic = json.loads(self.diagnostics[-1].split('=', 1)[1])
        self.assertEqual(diagnostic['request_keys'], ['filekey', 'fileuuid', 'rangeend', 'rangestart', 'totalsize'])
        self.assertNotIn('private-raw', self.diagnostics[-1])
        self.assertNotIn('中文素材', self.diagnostics[-1])
        self.assertEqual(len(session.upload_links()), 1)

    async def test_diagnostic_print_failure_does_not_change_upload_evidence(self):
        session = self.session()
        with patch('builtins.print', side_effect=BrokenPipeError('closed observer output')):
            await self.chain(session)
        self.assertEqual(len(session.upload_links()), 1)

    async def test_sns_requires_complete_current_range_coverage_even_out_of_order(self):
        session = self.session()
        middle, size = len(self.payload) // 2, len(self.payload)
        await self.sns(session, middle, size, finish=True)
        self.assertEqual(session.upload_links(), [])
        await self.sns(session, 0, middle)
        self.assertEqual(session.upload_links()[0]['ids'], sorted((self.resource, self.alias)))
        await self.sns(session, 0, middle)
        self.assertEqual(len(session.upload_links()), 1)

    async def test_sns_chunk_url_missing_ranges_overlap_and_conflicting_resource_reject(self):
        for mode in ('no-terminal', 'gap', 'overlap', 'resource', 'duplicate-field'):
            with self.subTest(mode=mode):
                session = self.session()
                size = len(self.payload)
                if mode == 'no-terminal':
                    await self.sns(session, 0, size)
                elif mode == 'gap':
                    await self.sns(session, size // 2, size, finish=True)
                elif mode == 'overlap':
                    await self.sns(session, 0, size, finish=True)
                    await self.sns(session, 2, 4)
                elif mode == 'resource':
                    await self.sns(session, 0, size, finish=True)
                    await self.sns(session, 0, size, finish=True, resource='http://wxapp.tc.qq.com/other-resource')
                else:
                    await self.sns(session, 0, size, finish=True, duplicate_field=True)
                self.assertEqual(session.upload_links(), [])

    async def test_dfs_cached_finish_or_confirmed_old_upload_id_is_not_new_allocation(self):
        session = self.session()
        await self.allocation(session, query='?UploadID=upload-a&PartNumberMarker=0')
        for number in (1, 2):
            await self.part(session, number)
        await self.finish(session)
        self.assertEqual(session.upload_links(), [])
        session = self.session()
        await self.finish(session)
        self.assertEqual(session.upload_links(), [])

    async def test_dfs_task_upload_part_flag_and_resource_ambiguities_reject(self):
        for mode in ('task', 'upload', 'etag', 'missing-part', 'flag', 'duplicate-part', 'conflicting-resource'):
            with self.subTest(mode=mode):
                session = self.session()
                await self.allocation(session)
                await self.part(session, 1)
                if mode != 'missing-part':
                    await self.part(session, 2, data={'X-Errno': 0, 'ETag': 'wrong-etag', 'TransFlag': 'task-a-flag'} if mode == 'etag' else None)
                if mode == 'duplicate-part':
                    await self.part(session, 1, data={'X-Errno': 0, 'ETag': 'different-retry', 'TransFlag': 'task-a-flag'})
                await self.finish(session, task='task-b' if mode == 'task' else 'task-a',
                                  upload='upload-b' if mode == 'upload' else 'upload-a', flag='wrong-flag' if mode == 'flag' else None)
                if mode == 'conflicting-resource':
                    await self.finish(session, resource='http://wxapp.tc.qq.com/other-resource')
                self.assertEqual(session.upload_links(), [])

    async def test_same_hash_different_tasks_never_mix_allocation_resources(self):
        session = self.session()
        await self.allocation(session, task='task-b', upload='upload-b')
        await self.chain(session)
        self.assertEqual(session.upload_links()[0]['ids'], sorted((self.resource, self.alias)))
        resource_b = 'http://wxapp.tc.qq.com/fixture/resource-b'
        for number in (1, 2):
            await self.part(session, number, task='task-b', upload='upload-b')
        await self.finish(session, task='task-b', upload='upload-b', resource=resource_b)
        links = session.upload_links()
        self.assertEqual(len(links), 2)
        self.assertFalse(set(links[0]['ids']).intersection(links[1]['ids']))

    async def test_same_task_with_multiple_allocations_rejects_instead_of_selecting_last(self):
        session = self.session()
        await self.allocation(session, upload='other-upload')
        await self.chain(session)
        self.assertEqual(session.upload_links(), [])

    async def test_current_unique_full_sha_selection_is_required(self):
        for mode in ('two-selections', 'old-selection', 'wrong-full-hash'):
            with self.subTest(mode=mode):
                session = self.session()
                if mode == 'two-selections':
                    session.files.append({**session.files[0], 'selected_at': 2})
                elif mode == 'old-selection':
                    session.started_at = 2
                else:
                    session.files[0]['sha256'] = hashlib.sha256(b'different whole file').hexdigest()
                await self.chain(session)
                self.assertEqual(session.upload_links(), [])

    async def test_prefix_md5_does_not_replace_complete_file_sha(self):
        prefix = b'x' * (5 * 1024 * 1024)
        self.payload = prefix + b'tail-a'
        session = self.session()
        session.files[0]['sha256'] = hashlib.sha256(prefix + b'tail-b').hexdigest()
        await self.chain(session)
        self.assertEqual(session.upload_links(), [])

    async def test_cover_requires_observed_original_bytes_not_metadata_or_cropper_alias(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                session = self.session(kind='cover')
                await self.allocation(session, count=1)
                await self.part(session, 1, raw=b'x' * len(self.payload) if changed else self.payload)
                await self.finish(session, count=1)
                self.assertEqual(bool(session.upload_links()), not changed)

    async def test_unknown_endpoint_untrusted_host_error_code_and_plan_do_not_pass(self):
        for mode in ('normaluploadfinish', 'host', 'resource-host', 'error', 'bool-code', 'plan', 'duplicate-query'):
            with self.subTest(mode=mode):
                session = self.session()
                if mode in ('normaluploadfinish', 'host'):
                    request = self.Request('POST', self.headers(session, 'task-a'), {'fileId': 'upload-a'})
                    await self.observe(session, self.Response('/' + mode if mode == 'normaluploadfinish' else '/snsuploadbig', request,
                                       {'code': 0, 'uploadfinish': 1, 'DownloadURL': self.resource},
                                       host='finder.video.qq.com' if mode == 'normaluploadfinish' else 'finder.video.qq.com.evil.invalid'))
                elif mode == 'resource-host':
                    await self.chain(session, resource='http://wxapp.tc.qq.com.evil.invalid/resource')
                elif mode in ('error', 'bool-code'):
                    await self.allocation(session, data={'UploadID': 'upload-a', 'X-Errno': False if mode == 'bool-code' else 1})
                    for number in (1, 2):
                        await self.part(session, number)
                    await self.finish(session)
                elif mode == 'plan':
                    await self.allocation(session, body={'BlockSum': 2, 'BlockPartLength': [len(self.payload), len(self.payload)]})
                    await self.finish(session)
                else:
                    await self.allocation(session, query='?UploadID=a&UploadID=b')
                    await self.finish(session)
                self.assertEqual(session.upload_links(), [])

    def test_generic_same_hash_allocations_are_not_merged_without_shared_task_ids(self):
        session = self.session()
        session.platform = 'bilibili'
        metadata = (session.files[0]['name'], session.files[0]['size'])
        session.exchanges = [
            {'stage': 'allocated', 'ids': ['task-b'], 'request_ids': [], 'metadata': metadata, 'observed_at': 2},
            {'stage': 'allocated', 'ids': ['task-a'], 'request_ids': [], 'metadata': metadata, 'observed_at': 3},
            {'stage': 'complete', 'ids': ['resource-a'], 'request_ids': ['task-a'], 'metadata': None, 'observed_at': 4},
        ]
        self.assertEqual(session.upload_links()[0]['ids'], ['resource-a', 'task-a'])


class BrowserEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.directory = tempfile.TemporaryDirectory(prefix='sau-evidence-local-')
        self.video = Path(self.directory.name) / 'synthetic.mp4'
        self.video.write_bytes(bytes(range(256)) * 4301 + b'streaming SHA-256 tail')
        self.cover = Path(self.directory.name) / 'synthetic.png'
        self.cover.write_bytes(b'isolated cover bytes')
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chrome', timeout=90000)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.snapshot = None
        self.read_wrong_id = False
        self.calls = []
        self.html = '<!doctype html><meta charset="utf-8"><body></body>'

        async def serve(route):
            request = route.request
            path = urlparse(request.url).path
            self.calls.append((request.method, path))
            if path.startswith('/fixture-media/'):
                if path.endswith('.mp4'):
                    await route.fulfill(content_type='video/mp4', body=self.video.read_bytes())
                else:
                    await route.fulfill(content_type='image/png', body=self.decoded_png)
                return
            if path == '/rest/cp/works/v2/video/pc/upload/cover/view':
                await route.fulfill(content_type='image/png', body=self.decoded_png)
                return
            if path in ('/upload/video', '/upload/image', '/upload/pre', '/upload/finish'):
                if path == '/upload/pre':
                    data = {'code': 0, 'data': {'fileId': 'allocated-video'}}
                elif path == '/upload/finish':
                    data = {'code': 0, 'data': {'fileId': 'allocated-video', 'complete': True}}
                else:
                    from urllib.parse import parse_qs
                    slot = parse_qs(urlparse(request.url).query).get('slot', [''])[0]
                    resource = 'new-video' if path == '/upload/video' else ('new-cover-' + slot if slot else 'new-cover')
                    data = {'code': 0, 'data': {'vid': resource, 'complete': True}}
                await route.fulfill(content_type='application/json', body=json.dumps(data))
            elif path.endswith('/publishInfo/snapshot/save'):
                self.snapshot = request.post_data_json
                await route.fulfill(content_type='application/json', body=json.dumps({'result': 1, 'data': {}}))
            elif path.endswith('/snapshot/info'):
                snapshot = dict(self.snapshot or {})
                if self.read_wrong_id:
                    snapshot['fileId'] = 'old-video'
                if snapshot:
                    snapshot.update(url='https://fixture.invalid/fixture-media/new-video.mp4')
                await route.fulfill(content_type='application/json', body=json.dumps({'result': 1, 'data': snapshot, 'currentTime': 1792000000000}))
            else:
                await route.fulfill(content_type='text/html', body=self.html)
        await self.context.route('**/*', serve)

    async def asyncTearDown(self):
        await end_draft_verification(self.page)
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.directory.cleanup()

    async def decoded_assets(self):
        from tests.test_dy_cover_readback import TINY_PNG
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('ffmpeg unavailable for actual decoded-media fixture')
        if not hasattr(type(self), '_decoded_mp4'):
            type(self)._decoded_mp4 = subprocess.check_output([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=blue:s=320x180:r=10:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1'])
        self.video.write_bytes(type(self)._decoded_mp4)
        self.cover.write_bytes(TINY_PNG)
        self.decoded_png = TINY_PNG

    def expected(self, platform='douyin', custom_cover=False):
        payload = {'video': str(self.video), 'title': '重复标题', 'description': '完整正文', 'tags': ['话题']}
        if custom_cover:
            payload['cover'] = str(self.cover)
        checked = preflight_payload(payload, [platform])
        self.assertEqual(checked['errors'], [])
        expected = build_platform_payload(checked, platform, 'fixture-current-job', 'fixture-current-nonce')
        expected['account'] = account(platform)
        return expected

    async def setup_dy(self, *, old_media=False, restored_media=True, custom_cover=False, consume_cache=False):
        await self.decoded_assets()
        expected = self.expected(custom_cover=custom_cover)
        config = json.dumps({'title': expected['title'], 'caption': expected['platform_fields']['caption'],
                             'media': 'old-video' if old_media else 'new-video',
                             'portrait': 'new-cover-portrait' if custom_cover else 'default-cover-portrait',
                             'landscape': 'new-cover-landscape' if custom_cover else 'default-cover-landscape',
                             'restoredMedia': restored_media, 'consumeCache': consume_cache})
        self.html = r'''<!doctype html><meta charset="utf-8"><style>
          #root{width:800px}video{display:block;width:320px;height:180px}
          .coverControl-fixture{display:inline-block;width:160px;height:135px}
          .coverControl-fixture img{display:block;width:100px;height:100px}
          </style><div data-user-id="fixture-owner">本地测试账号</div>
          <input id="video" type="file" accept="video/mp4"><input id="cover" type="file" accept="image/png">
          <div id="root"><div id="editor"><input id="title" placeholder="填写作品标题"><div id="body" contenteditable="true"></div>
            <div id="media" data-video-id="" style="width:100px;height:30px">视频预览</div>
            <video id="nativeVideo" src="https://fixture.invalid/fixture-media/new-video.mp4" preload="auto"></video>
            <div id="portraitCard" class="coverControl-fixture"><span>竖封面3:4</span><img src="https://fixture.invalid/fixture-media/portrait.png"></div>
            <div id="landscapeCard" class="coverControl-fixture"><span>横封面4:3</span><img src="https://fixture.invalid/fixture-media/landscape.png"></div>
          </div></div><button id="save">暂存离开</button><button id="restore" style="display:none">继续编辑</button>
          <div id="toast"></div><button id="coverToast">保存封面</button>
          <button id="publish" onclick="window.publishClicked=true">继续发布</button><script>
          const cfg=CONFIG;
          window.title.value=cfg.title;window.body.innerText=cfg.caption;
          const root={tag:3,child:null},fiberRoot={current:root,containerInfo:document.querySelector('#root')};root.stateNode=fiberRoot;
          document.querySelector('#root').__reactContainer$fixture={tag:3,stateNode:fiberRoot};
          const player={tag:0,memoizedProps:{currentVideo:{vid:'new-video'},width:320,height:180,playUrl:window.nativeVideo.src},
             child:{tag:5,stateNode:window.nativeVideo}};root.child=player;
          const cards=[];
          for(const [id,uri] of [['portraitCard',cfg.portrait],['landscapeCard',cfg.landscape]]){
            const el=document.getElementById(id),img=el.querySelector('img');
            const imageHost={tag:5,stateNode:img,memoizedProps:{src:img.src}},imageComponent={tag:0,memoizedProps:{src:img.src},child:imageHost};
            const card={tag:0,memoizedProps:{coverInfo:{uri},disabled:false,handleEditCover(){},durationStr:'00:01',showOperation:true,className:'cover-card'},
               child:{tag:5,stateNode:el,child:imageComponent}};
            (cards.length?cards[cards.length-1]:player).sibling=card;cards.push(card);
          }
          window.fixture={player,cards,root,fiberRoot};
          window.video.addEventListener('change',async()=>{const f=window.video.files[0];
             await fetch('/upload/video',{method:'POST',body:JSON.stringify({fileName:f.name,fileSize:f.size})});});
          window.cover.addEventListener('change',async()=>{const f=window.cover.files[0];
             for(const slot of ['portrait','landscape'])await fetch('/upload/image?slot='+slot+'&name='+encodeURIComponent(f.name)+'&size='+f.size,{method:'POST',body:f});});
          window.coverToast.onclick=()=>window.toast.innerText='封面保存成功';
          window.save.onclick=()=>{
            const draft={cache:{itemTitle:window.title.value,textResult:{text:window.body.innerText},longFileInfo:{vid:cfg.media,coverUri:cfg.portrait},
               coverInfo:{uri:cfg.portrait},coverHorizontalInfo:{uri:cfg.landscape}}};
            localStorage.setItem('publish_form_cache:fixture-owner',JSON.stringify(draft));
            window.editor.style.display='none';window.restore.style.display='block';window.toast.innerText='已保存';
            history.replaceState(null,'','/creator-micro/content/upload');
          };
          window.restore.onclick=()=>{
            const stored=JSON.parse(localStorage.getItem('publish_form_cache:fixture-owner')).cache;
            if(cfg.consumeCache)localStorage.setItem('publish_form_cache:fixture-owner','null');
            window.title.value=stored.itemTitle;window.body.innerText=stored.textResult.text;
            window.media.dataset.videoId=cfg.restoredMedia?stored.longFileInfo.vid:'wrong-visible-video';
            player.memoizedProps.currentVideo={vid:window.media.dataset.videoId};
            cards[0].memoizedProps.coverInfo={uri:stored.coverInfo.uri};cards[1].memoizedProps.coverInfo={uri:stored.coverHorizontalInfo.uri};
            window.editor.style.display='block';window.restore.style.display='none';
            history.replaceState(null,'','/creator-micro/content/post/video');
          };
          </script>'''.replace('CONFIG', config)
        await self.page.goto('https://creator.douyin.com/creator-micro/content/post/video')
        await self.page.wait_for_function("nativeVideo.readyState>=2&&nativeVideo.videoWidth>0&&[...document.querySelectorAll('img')].every(i=>i.complete&&i.naturalWidth>0)")
        session = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        await self.page.locator('#video').set_input_files(str(self.video))
        if custom_cover:
            await self.page.locator('#cover').set_input_files(str(self.cover))
        await self.wait_fingerprints(session, 2 if custom_cover else 1)
        return expected, session

    async def wait_fingerprints(self, session, count):
        until = asyncio.get_running_loop().time() + 8
        while len(session.files) < count and asyncio.get_running_loop().time() < until:
            await asyncio.sleep(.02)
        self.assertEqual(len(session.files), count)
        await session.drain()

    async def test_file_observer_nested_shadow_changes_and_current_session(self):
        await self.page.goto('http://127.0.0.1:45997/shadow-files')
        # The nested roots already exist when the observer is installed.
        await self.page.evaluate('''() => {
          window.fixtureNativeAttach=Element.prototype.attachShadow;
          window.fixtureMakeInput=native=>{
            const host=document.createElement('div');
            const outer=(native?window.fixtureNativeAttach:host.attachShadow).call(host,{mode:'open'});
            const child=document.createElement('div');outer.append(child);
            const inner=(native?window.fixtureNativeAttach:child.attachShadow).call(child,{mode:'open'});
            const input=document.createElement('input');input.type='file';inner.append(input);
            return {host,outer,inner,input};
          };
          window.fixtureSelect=(input,composed)=>{
            const bytes=new Uint8Array(256*4301);for(let i=0;i<bytes.length;i++)bytes[i]=i%256;
            const file=new File([bytes,'streaming SHA-256 tail'],'synthetic.mp4',{type:'video/mp4'});
            const transfer=new DataTransfer();transfer.items.add(file);input.files=transfer.files;
            input.dispatchEvent(new Event('change',{bubbles:true,composed}));
          };
          window.fixtureExisting=window.fixtureMakeInput(true);document.body.append(window.fixtureExisting.host);
        }''', isolated_context=False)
        expected = self.expected()
        session = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        with self.subTest('existing nested root, noncomposed change'):
            await self.page.evaluate('fixtureSelect(fixtureExisting.input,false)', isolated_context=False)
            await self.wait_fingerprints(session, 1)
            self.assertEqual(session.files[0]['sha256'], expected['video_sha256'])
            self.assertEqual(session.files[0]['size'], self.video.stat().st_size)

        with self.subTest('composed event is observed exactly once after guard reinjection'):
            await self.page.evaluate(FILE_OBSERVER_SCRIPT, isolated_context=False)
            await self.page.evaluate('fixtureSelect(fixtureExisting.input,true)', isolated_context=False)
            await self.wait_fingerprints(session, 2)
            await asyncio.sleep(.1)
            self.assertEqual(len(session.files), 2)
            self.assertEqual(session.files[-1]['sha256'], expected['video_sha256'])

        await end_draft_verification(self.page)
        current = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        with self.subTest('each new selection event routes to current session'):
            await self.page.evaluate('fixtureSelect(fixtureExisting.input,true)', isolated_context=False)
            await self.wait_fingerprints(current, 1)
            self.assertEqual(len(session.files), 2)

        with self.subTest('late attached root on detached host preserves native behavior'):
            semantics = await self.page.evaluate('''() => {
              window.fixtureLate=window.fixtureMakeInput(false);
              const root=window.fixtureLate.host.shadowRoot;
              let duplicateThrows=false;
              try{window.fixtureLate.host.attachShadow({mode:'open'});}catch(e){duplicateThrows=e.name==='NotSupportedError';}
              fixtureSelect(window.fixtureLate.input,false);
              document.body.append(window.fixtureLate.host);
              const closedHost=document.createElement('div'),closedRoot=closedHost.attachShadow({mode:'closed'});
              return {sameRoot:root===window.fixtureLate.outer,duplicateThrows,closedHidden:closedHost.shadowRoot===null,
                      closedReturned:closedRoot.mode==='closed'};
            }''', isolated_context=False)
            self.assertTrue(all(semantics.values()))
            await self.wait_fingerprints(current, 2)

        with self.subTest('mutation observer discovers roots created before mounting'):
            await self.page.evaluate('''async () => {
              const late=window.fixtureMakeInput(true);document.body.append(late.host);
              await new Promise(resolve=>requestAnimationFrame(resolve));
              fixtureSelect(late.input,false);
            }''', isolated_context=False)
            await self.wait_fingerprints(current, 3)
        self.assertTrue(all(row['sha256'] == expected['video_sha256'] for row in current.files))

    async def test_file_observer_foreign_realm_shadow_input_native_sha_and_forgery_rejection(self):
        await self.page.goto('http://127.0.0.1:45997/foreign-shadow-files')
        await self.page.evaluate('''async () => {
          const iframe=document.createElement('iframe');
          const ready=new Promise(resolve=>iframe.addEventListener('load',resolve,{once:true}));
          iframe.srcdoc='<!doctype html><body></body>';document.body.append(iframe);await ready;
          const realm=iframe.contentWindow;
          const nativeAttach=realm.Element.prototype.attachShadow;
          window.fixtureForeign=realm;
          window.fixtureForeignMake=()=>{
            const host=realm.document.createElement('div');
            const outer=Reflect.apply(nativeAttach,host,[{mode:'open'}]);
            const child=realm.document.createElement('div');outer.append(child);
            const inner=Reflect.apply(nativeAttach,child,[{mode:'open'}]);
            const input=realm.document.createElement('input');input.type='file';inner.append(input);
            document.adoptNode(host);document.body.append(host);
            return {host,outer,inner,input};
          };
          window.fixtureForeignExisting=fixtureForeignMake();
          window.fixtureForeignSelect=(input,composed,override)=>{
            const bytes=new Uint8Array(256*4301);for(let i=0;i<bytes.length;i++)bytes[i]=i%256;
            const file=new realm.File([bytes,'streaming SHA-256 tail'],'synthetic.mp4',{type:'video/mp4'});
            const transfer=new realm.DataTransfer();transfer.items.add(file);
            const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').set;
            const getter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').get;
            Reflect.apply(setter,input,[transfer.files]);
            if(override){
              const selected=Reflect.apply(getter,input,[])[0];
              Object.defineProperties(selected,{
                name:{value:'forged.mp4'},size:{value:1},type:{value:'image/png'},
                slice:{value:()=>new Blob(['forged'])},arrayBuffer:{value:async()=>new Uint8Array([9]).buffer}
              });
              Object.defineProperty(input,'files',{value:[{name:'forged.mp4',size:1,type:'image/png'}],configurable:true});
            }
            input.dispatchEvent(new realm.Event('change',{bubbles:true,composed}));
          };
        }''', isolated_context=False)
        expected = self.expected()
        session = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        actual = await self.page.evaluate('''() => {
          const input=fixtureForeignExisting.input;
          const getter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').get;
          return {mainInput:input instanceof HTMLInputElement,mainElement:input instanceof Element,
                  foreignInput:input instanceof fixtureForeign.HTMLInputElement,
                  adopted:input.ownerDocument===document,nativeFiles:Reflect.apply(getter,input,[]).length};
        }''', isolated_context=False)
        self.assertEqual(actual, {'mainInput': False, 'mainElement': False, 'foreignInput': True,
                                  'adopted': True, 'nativeFiles': 0})

        with self.subTest('preexisting foreign shadow roots, noncomposed native change'):
            await self.page.evaluate('fixtureForeignSelect(fixtureForeignExisting.input,false,true)', isolated_context=False)
            await self.wait_fingerprints(session, 1)
            self.assertEqual(session.files[0]['sha256'], expected['video_sha256'])
            self.assertEqual(session.files[0]['name'], self.video.name)
            self.assertEqual(session.files[0]['size'], self.video.stat().st_size)
            self.assertEqual(session.files[0]['type'], 'video/mp4')

        with self.subTest('composed foreign-realm event is emitted exactly once'):
            await self.page.evaluate('fixtureForeignSelect(fixtureForeignExisting.input,true,false)', isolated_context=False)
            await self.wait_fingerprints(session, 2)
            await asyncio.sleep(.1)
            self.assertEqual(len(session.files), 2)

        with self.subTest('native scanner discovers a late foreign host and nested roots'):
            await self.page.evaluate('''async () => {
              const late=fixtureForeignMake();
              await new Promise(resolve=>requestAnimationFrame(resolve));
              fixtureForeignSelect(late.input,false,false);
            }''', isolated_context=False)
            await self.wait_fingerprints(session, 3)

        with self.subTest('forged input prototype and overwritten composed path cannot create evidence'):
            await self.page.evaluate('''() => {
              const fake=document.createElement('div');
              Object.setPrototypeOf(fake,HTMLInputElement.prototype);
              Object.defineProperties(fake,{type:{value:'file'},files:{value:[new File(['forged'],'synthetic.mp4')]}});
              document.body.append(fake);
              fake.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
              const event=new Event('change',{bubbles:true,composed:true});
              Object.defineProperty(event,'composedPath',{value:()=>[fake]});
              document.dispatchEvent(event);
              const empty=document.createElement('input');empty.type='file';document.body.append(empty);
              Object.defineProperty(empty,'files',{value:[new File(['forged'],'synthetic.mp4')]});
              empty.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
            }''', isolated_context=False)
            await asyncio.sleep(.2)
            self.assertEqual(len(session.files), 3)
        self.assertTrue(all(row['sha256'] == expected['video_sha256'] for row in session.files))

    async def test_dy_real_file_hash_local_save_restore_and_receipt_identity(self):
        expected, session = await self.setup_dy(custom_cover=True)
        self.assertEqual(session.files[0]['sha256'], expected['video_sha256'])
        self.assertTrue(any(link['kind'] == 'video' for link in session.upload_links()))
        receipt = Path(self.directory.name) / 'receipt.json'
        result = await save_draft(self.page, '抖音', expected['title'], receipt, button_timeout=2, receipt_timeout=15, expected=expected)
        self.assertEqual(result['status'], 'local_draft_verified')
        self.assertTrue(all(result['verification'].values()))
        self.assertEqual(result['save_location'], 'browser_local')
        validate_receipt(result, expected, 'douyin')
        self.assertIsNone(getattr(self.page, '_sau_draft_evidence', None))
        serialized = receipt.read_text()
        self.assertNotIn('post_data', serialized)
        self.assertNotIn('content-draft-body', serialized)

    async def test_checkpoint_resume_existing_video_then_normal_save_reopen_without_second_upload(self):
        expected, old = await self.setup_dy()
        # The fulfilled upload response has already been independently bound to
        # this native File. The fixture UI now presents that same resource.
        await self.page.evaluate("window.fixture.player.memoizedProps.currentVideo.vid='new-video'", isolated_context=False)
        await self.page.locator('#body').fill('')
        checkpoint = await old.export_checkpoint(seal_key=bytes(range(32)))
        await end_draft_verification(self.page)
        fresh = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        async def current_media(page, platform):
            from uploader.dy_reopen import read_dy_visible_video
            self.assertEqual(platform, 'douyin')
            observed = await read_dy_visible_video(page, ['new-video'])
            return {'source': 'platform_composer', 'video_count': observed['video_count'],
                    'video_ids': observed['video_ids']} if observed else None
        restored = await fresh.restore_checkpoint(checkpoint, seal_key=bytes(range(32)), media_reader=current_media)
        self.assertTrue(restored['video_upload_verified'])
        self.assertEqual(fresh.files, [])
        await self.page.locator('#title').fill(expected['platform_fields']['title'])
        await self.page.locator('#body').fill(expected['platform_fields']['caption'])
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=2, receipt_timeout=15, expected=expected)
        self.assertEqual(result['status'], 'local_draft_verified')
        self.assertTrue(all(result['verification'].values()))
        self.assertEqual(sum(path == '/upload/video' for _, path in self.calls), 1)
        validate_receipt(result, expected, 'douyin')

    async def test_same_title_old_media_and_wrong_restored_media_cannot_verify(self):
        expected, _ = await self.setup_dy(old_media=True)
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=.2, receipt_timeout=.35, expected=expected)
        self.assertEqual(result['status'], 'saved_unverified')
        self.assertFalse(result['verification']['media'])
        expected, _ = await self.setup_dy(restored_media=False)
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=.2, receipt_timeout=.35, expected=expected)
        self.assertEqual(result['status'], 'saved_unverified')
        self.assertFalse(result['verification']['reopened'])

    async def test_dy_normal_restore_consumes_cache_then_independent_dom_readback_and_resave(self):
        expected, _ = await self.setup_dy(consume_cache=True)
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=.2, receipt_timeout=8, expected=expected)
        self.assertEqual(result['status'], 'local_draft_verified')
        self.assertTrue(result['verification']['reopened'])
        persisted = await self.page.evaluate("JSON.parse(localStorage.getItem('publish_form_cache:fixture-owner'))")
        self.assertEqual(persisted['cache']['longFileInfo']['vid'], 'new-video')
        self.assertEqual(sum(path == '/upload/video' for _, path in self.calls), 1)

    async def test_loaded_dy_cover_with_wrong_current_uri_cannot_verify_reopen(self):
        expected, _ = await self.setup_dy(custom_cover=True)
        await self.page.evaluate("restore.addEventListener('click',()=>{fixture.cards[1].memoizedProps.coverInfo.uri='wrong-current-cover';})", isolated_context=False)
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=.2, receipt_timeout=1, expected=expected)
        self.assertEqual(result['status'], 'saved_unverified')
        self.assertTrue(result['verification']['upload_complete'])
        self.assertTrue(result['verification']['cover'])
        self.assertTrue(result['verification']['account'])
        self.assertFalse(result['verification']['reopened'])
        self.assertTrue(await self.page.locator('#landscapeCard img').evaluate('img=>img.complete&&img.naturalWidth>0'))

    async def test_cover_toast_and_late_observer_never_become_verified(self):
        self.html = '<button onclick="document.body.append(\'封面保存成功\')">保存草稿</button>'
        await self.page.goto('http://127.0.0.1:45997/cover-toast')
        expected = self.expected()
        result = await save_draft(self.page, '抖音', expected['title'], button_timeout=.2, receipt_timeout=.25, expected=expected)
        self.assertEqual(result['status'], 'result_unknown')
        self.assertIn('observer_started_after_upload', result['reasons'])

    async def test_publish_network_and_continue_publish_button_blocked_but_upload_allowed(self):
        expected, _ = await self.setup_dy()
        await install_draft_guard(self.context, self.page)
        await self.page.locator('#publish').click()
        self.assertFalse(await self.page.evaluate('!!window.publishClicked', isolated_context=False))
        await self.page.evaluate("fetch('/web/api/media/aweme/create_v2/',{method:'POST'}).catch(()=>null)")
        self.assertFalse(any('/aweme/create' in path for _, path in self.calls))
        self.assertTrue(any(path == '/upload/video' for _, path in self.calls))
        with self.assertRaises(DraftInterrupted) as failure:
            await assert_draft_safe(self.page, 'douyin')
        self.assertEqual(failure.exception.status, 'risk_paused')

    async def test_editor_text_is_not_a_platform_risk_alert(self):
        await self.page.goto('http://127.0.0.1:45997/text-only')
        await self.page.set_content('<div contenteditable="true">讲解账号异常、验证码和上传失败的处理方法</div>')
        await assert_draft_safe(self.page)
        await self.page.locator('body').evaluate("e => {let x=document.createElement('div');x.setAttribute('role','alert');x.innerText='安全验证：请输入验证码';e.append(x);}")
        with self.assertRaises(DraftInterrupted):
            await assert_draft_safe(self.page)

    async def test_xhs_reader_does_not_create_database_and_reads_exact_video_store(self):
        await self.page.goto('http://127.0.0.1:45997/xhs')
        self.assertEqual(await read_local_records(self.page, 'xiaohongshu'), [])
        self.assertEqual(await self.page.evaluate('indexedDB.databases()'), [])
        await self.page.evaluate('''() => new Promise(resolve=>{
          const r=indexedDB.open('draft-database-v1',1);r.onupgradeneeded=()=>r.result.createObjectStore('video-draft',{keyPath:'draftId'});
          r.onsuccess=()=>{const db=r.result,tx=db.transaction('video-draft','readwrite');
            tx.objectStore('video-draft').put({draftId:'xhs-current',uid:'fixture-owner',content:{draftStore:{title:'本次标题',desc:'正文',hashTag:[],video:{fileid:'v-current'},cover:{fileid:'c-current'}}}});
            tx.oncomplete=()=>{db.close();resolve();};};})''')
        rows = await read_local_records(self.page, 'xiaohongshu')
        self.assertEqual(len(rows), 1)
        parsed = parse_record('xiaohongshu', rows[0]['raw'])
        self.assertEqual(parsed['id'], 'xhs-current')
        self.assertEqual(parsed['video_ids'], ['v-current'])

    async def test_xhs_exact_video_draft_id_edit_does_not_select_duplicate_title(self):
        await self.decoded_assets()
        expected = self.expected('xiaohongshu')
        config = json.dumps(expected['platform_fields'])
        self.html = r'''<!doctype html><meta charset="utf-8">
          <input id="video" type="file" accept="video/mp4"><div id="editor">
          <input id="title" placeholder="填写标题"><div id="body" contenteditable="true"></div>
          <video id="media" src="https://fixture.invalid/fixture-media/new-video/preview.mp4" style="width:320px;height:180px" preload="auto"></video>
          <img id="preview" src="https://fixture.invalid/fixture-media/default-cover/display.png" style="width:100px;height:100px"></div>
          <button id="save">暂存离开</button><div id="drawer" class="draft-drawer" style="display:none;width:400px;height:200px">
            <div class="draft-item" data-draft-id="old-id" data-draft-type="video">重复标题<div class="draft-actions"><div class="btn" onclick="window.wrongDraft=true">编辑</div></div></div>
            <div class="draft-item" data-draft-id="new-id" data-draft-type="video">重复标题<div class="draft-actions"><div class="btn" id="edit">编辑</div></div></div>
          </div><script>
          const cfg=CONFIG;window.title.value=cfg.title;window.body.innerText=cfg.description+'\n'+cfg.tags.map(t=>'#'+t).join(' ');
          window._tiptap_editor={view:{dom:window.body,docView:{children:[]}}};
          const dbPromise=new Promise(resolve=>{const r=indexedDB.open('draft-database-v1',1);
             r.onupgradeneeded=()=>r.result.createObjectStore('video-draft',{keyPath:'draftId'});r.onsuccess=()=>resolve(r.result);});
          window.video.onchange=async()=>{const f=window.video.files[0];await fetch('/upload/video',{method:'POST',body:JSON.stringify({fileName:f.name,fileSize:f.size})});};
          window.save.onclick=async()=>{const db=await dbPromise,tx=db.transaction('video-draft','readwrite');
             tx.objectStore('video-draft').put({draftId:'new-id',uid:'fixture-owner',timeStamp:Date.now(),content:{draftStore:{title:cfg.title,desc:window.body.innerText,
                hashTag:cfg.tags.map(name=>({name,id:'topic-1'})),video:{fileid:'new-video'},cover:{fileid:'default-cover'}}}});
             tx.oncomplete=()=>{window.editor.style.display='none';window.drawer.style.display='block';};};
          window.edit.onclick=async()=>{const db=await dbPromise,tx=db.transaction('video-draft','readonly'),r=tx.objectStore('video-draft').get('new-id');
             r.onsuccess=()=>{const stored=r.result.content.draftStore;window.title.value=stored.title;window.body.innerText=stored.desc;
                window.media.src='https://fixture.invalid/fixture-media/'+stored.video.fileid+'/preview.mp4';
                window.preview.src='https://fixture.invalid/fixture-media/'+stored.cover.fileid+'/display.png';
                window.editor.style.display='block';window.drawer.style.display='none';};};
          </script>'''.replace('CONFIG', config)
        await self.page.goto('https://creator.xiaohongshu.com/publish/publish?target=video')
        await self.page.wait_for_function("media.readyState>=2&&media.videoWidth>0&&preview.complete&&preview.naturalWidth>0")
        session = await begin_draft_verification(self.page, 'xiaohongshu', expected, account_reader=lambda: account('xiaohongshu'))
        await self.page.locator('#video').set_input_files(str(self.video))
        await self.wait_fingerprints(session, 1)
        result = await save_draft(self.page, '小红书', expected['title'], button_timeout=2, receipt_timeout=15, expected=expected)
        self.assertEqual(result['status'], 'local_draft_verified')
        self.assertEqual(result['draft_id'], 'new-id')
        self.assertFalse(await self.page.evaluate('!!window.wrongDraft', isolated_context=False))
        validate_receipt(result, expected, 'xiaohongshu')

    async def setup_ks(self):
        await self.decoded_assets()
        expected = self.expected('kuaishou', custom_cover=True)
        config = json.dumps({'caption': expected['platform_fields']['caption']})
        self.html = r'''<!doctype html><meta charset="utf-8"><style>
          #preview-tours{width:400px;height:210px}video{width:320px;height:180px}
          #coverEditor img{display:block;width:100px;height:100px}
          </style><input id="video" type="file" accept="video/mp4"><input id="coverInput" type="file" accept="image/png">
          <div id="body" contenteditable="true"></div><div id="root"><div id="editor">
          <main id="preview-tours"><video id="nativeVideo" preload="auto"></video></main>
          <section id="coverEditor"><img id="nativeCover"></section></div></div>
          <button id="restore">继续编辑</button><script>
          const cfg=CONFIG;window.body.innerText=cfg.caption;
          const form={fileId:-1,coverKey:'',pkCoverKey:'',profileCoverKey:'',caption:cfg.caption,fileName:'',fileSize:'',url:''};
          const progress={status:2,percent:0,localFileData:null};
          const root={tag:3,child:null},fiberRoot={current:root,containerInfo:document.querySelector('#root')};root.stateNode=fiberRoot;
          document.querySelector('#root').__reactContainer$fixture={tag:3,stateNode:fiberRoot};
          const make=(parent,props,state=null,node=null)=>({return:parent,memoizedProps:props,memoizedState:state,stateNode:node,child:null,sibling:null});
          const owner=make(root,{}, {memoizedState:{current:form},next:{memoizedState:progress,next:null}});root.child=owner;
          const vp={url:'',coverUrl:'',captionStr:cfg.caption,coPublishUser:[],hasVideoComposite:false};
          const consumer=make(owner,vp),vf=make(consumer,{src:''},null,window.nativeVideo);owner.child=consumer;consumer.child=vf;window.nativeVideo.__reactFiber$fixture=vf;
          const ep={snapshotInfo:form,fileId:'',uploadFinish:true,coverUrl:'',pkUrl:'',profileUrl:'',supportProfileCover:true,onSaveCover(){}};
          const coverOwner=make(owner,ep),imf=make(coverOwner,{src:''},null,window.nativeCover);consumer.sibling=coverOwner;coverOwner.child=imf;window.nativeCover.__reactFiber$fixture=imf;
          window.fixture={form,progress,root,fiberRoot,owner,vp,ep,vf,imf};
          let videoDone=false,coverDone=false,saved=false;
          async function maybeSave(){if(!videoDone||!coverDone||saved)return;saved=true;
            const snapshot={...form};delete snapshot.url;
            await fetch('/rest/cp/works/v3/video/pc/publishInfo/snapshot/save',{method:'POST',body:JSON.stringify(snapshot)});
          }
          window.video.onchange=async()=>{const f=window.video.files[0];progress.localFileData=f;
            const response=await fetch('/upload/video',{method:'POST',body:JSON.stringify({fileName:f.name,fileSize:f.size})});
            const r=await response.json();form.fileId=r.data.vid;form.fileName=f.name;form.fileSize=String(f.size);progress.status=3;
            videoDone=true;await maybeSave();};
          window.coverInput.onchange=async()=>{const f=window.coverInput.files[0];
            const r=await fetch('/upload/image?name='+encodeURIComponent(f.name)+'&size='+f.size,{method:'POST',body:f}).then(r=>r.json());
            form.coverKey=r.data.vid;coverDone=true;await maybeSave();};
          const snapshotPromise=fetch('/rest/cp/works/v3/video/pc/snapshot/info',{method:'POST',body:'{}'}).then(r=>r.json());
          window.restore.onclick=async()=>{const r=await snapshotPromise;if(!r.data.fileId)return;
            Object.assign(form,r.data);progress.status=3;progress.localFileData=null;window.body.innerText=form.caption;
            vp.url=form.url;vf.memoizedProps.src=form.url;window.nativeVideo.src=form.url;ep.fileId=String(form.fileId);
            const image=await fetch('/rest/cp/works/v2/video/pc/upload/cover/view?coverKey='+encodeURIComponent(form.coverKey)+'&_t='+Date.now()).then(r=>r.blob());
            const coverUrl=URL.createObjectURL(image);vp.coverUrl=coverUrl;ep.coverUrl=coverUrl;imf.memoizedProps.src=coverUrl;window.nativeCover.src=coverUrl;
          };
          </script>'''.replace('CONFIG', config)
        await self.page.goto('https://cp.kuaishou.com/article/publish/video')
        session = await begin_draft_verification(self.page, 'kuaishou', expected, account_reader=lambda: account('kuaishou'))
        await self.page.locator('#video').set_input_files(str(self.video))
        await self.page.locator('#coverInput').set_input_files(str(self.cover))
        await self.wait_fingerprints(session, 2)
        return expected, session

    async def test_server_snapshot_save_and_normal_reload_read_same_id_verified(self):
        expected, _ = await self.setup_ks()
        result = await save_draft(self.page, '快手', expected['title'], button_timeout=2, receipt_timeout=15, expected=expected)
        self.assertEqual(result['status'], 'platform_snapshot_verified')
        self.assertEqual(result['draft_id'], 'new-video')
        self.assertTrue(all(result['verification'].values()))
        self.assertEqual(sum(path.endswith('/snapshot/save') for _, path in self.calls), 1)
        self.assertGreaterEqual(sum(path.endswith('/snapshot/info') for _, path in self.calls), 2)
        validate_receipt(result, expected, 'kuaishou')

    async def test_server_snapshot_wrong_readback_id_is_unverified(self):
        expected, _ = await self.setup_ks()
        self.read_wrong_id = True
        result = await save_draft(self.page, '快手', expected['title'], button_timeout=.2, receipt_timeout=.4, expected=expected)
        self.assertEqual(result['status'], 'saved_unverified')
        self.assertFalse(result['verification']['reopened'])

    async def test_allocation_is_correlated_to_final_upload_response_only(self):
        await self.page.goto('http://127.0.0.1:45997/allocated')
        await self.page.set_content('<input id="video" type="file" accept="video/mp4">')
        expected = self.expected()
        session = await begin_draft_verification(self.page, 'douyin', expected, account_reader=lambda: account())
        await self.page.locator('#video').set_input_files(str(self.video))
        await self.wait_fingerprints(session, 1)
        metadata = {'fileName': self.video.name, 'fileSize': self.video.stat().st_size}
        await self.page.evaluate("data => fetch('/upload/pre',{method:'POST',body:JSON.stringify(data)})", metadata)
        await session.drain()
        self.assertEqual(session.upload_links(), [])
        await self.page.evaluate("fetch('/upload/finish',{method:'POST',body:JSON.stringify({fileId:'allocated-video'})})")
        await session.drain()
        self.assertEqual(session.upload_links()[0]['sha256'], expected['video_sha256'])


if __name__ == '__main__':
    unittest.main()
