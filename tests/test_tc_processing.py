"""Local protocol and native-browser fixtures; no platform/account operations."""
import asyncio
import copy
import hashlib
import io
import itertools
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright
from uploader.tc_processing import (PREFIX, NATIVE_READ_SCRIPT, TCProcessingObserver,
    MAX_VIDEO_BYTES, _file_size, _params, install_tc_processing_observer, processing_endpoint)
from uploader.tc_resource import tc_resource_identity


def resource(key):
    return ('https://finder.video.qq.com/251/20302/stodownload?encfilekey=' + key +
            '&m=&uzid=fixture-private-identity&bizid=1023&hy=SH&idx=1&dotrans=0&basedata=fixture-base&token=fixture-token')


SOURCE, DERIVED = resource('original'), resource('processed')


def request_body(size):
    return {'url': SOURCE, 'timeStart': 0, 'cropDuration': 0, 'width': 320, 'height': 240,
        'x': 0, 'y': 0, 'clipOriginVideoInfo': {'width': 320, 'height': 240, 'duration': 6, 'fileSize': size},
        'targetWidth': 320, 'targetHeight': 240, 'type': 4, 'useAstraThumbCover': False,
        'traceInfo': {'privateMarker': 'never-print-user-content'}}


def response(operation, body, when, status=200, method='POST'):
    return SimpleNamespace(url='https://channels.weixin.qq.com' + PREFIX +
        ('post_clip_video' if operation == 'allocate' else 'post_clip_video_result'), status=status,
        request=SimpleNamespace(method=method, post_data_json=body, timing={'startTime': when * 1000}))


def snapshot(size, sha):
    dims = {'width': 320, 'height': 240, 'duration': 6, 'fileSize': size}
    return {'name': 'fixture.mp4', 'size': size, 'sha256': sha, 'uuid': 'native-upload-uuid',
        'source_url': SOURCE, 'download_url': SOURCE.replace('https://finder.video.qq.com', 'http://wxapp.tc.qq.com'),
        'processed_url': DERIVED, 'ticket': {'clipKey': 'fixture-clip-key', 'draftId': 'fixture-draft-id'},
        'origin': dims, 'result': dict(dims), 'consumed': dict(dims)}


class ProcessingProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.size, self.sha = 17, hashlib.sha256(b'fixture-native').hexdigest()
        self.state = snapshot(self.size, self.sha)
        self.page = SimpleNamespace()
        async def read(*args, **kwargs):
            return copy.deepcopy(self.state)
        self.page.main_frame = SimpleNamespace(evaluate=read)
        self.session = self.make_session(self.page)
        self.observer = await install_tc_processing_observer(self.session)
        self.when = time.time() + .01
        self.ticket = {'clipKey': 'fixture-clip-key', 'draftId': 'fixture-draft-id'}
        self.links = [{'kind': 'video', 'sha256': self.sha, 'complete': True, 'ids': [tc_resource_identity(SOURCE)]}]

    def make_session(self, page):
        started = time.time()
        session = SimpleNamespace(page=page, platform='tencent', closed=False, started_at=started,
            expected={'job_id': 'fixture-job', 'receipt_nonce': 'fixture-nonce',
                      'account': {'uid': 'fixture-owner', 'identity': 'fixture-account'}, 'video_sha256': self.sha},
            account={'verified': True, 'uid': 'fixture-owner', 'identity': 'fixture-account', 'platform': 'tencent'},
            assets={'video': {'name': 'fixture.mp4', 'size': self.size, 'sha256': self.sha, 'kind': 'video'}},
            files=[{'name': 'fixture.mp4', 'size': self.size, 'sha256': self.sha, 'selected_at': started + .00001}])
        page._sau_draft_evidence = session
        return session

    async def allocate(self, body=None, data=None, when=None):
        when = self.when if when is None else when
        body = request_body(self.size) if body is None else body
        data = {'errCode': 0, 'data': dict(self.ticket)} if data is None else data
        await self.observer.observe(response('allocate', body, when), data, when + .001)

    async def finish(self, body=None, data=None, when=None):
        when = self.when + .02 if when is None else when
        body = dict(self.ticket) if body is None else body
        data = {'errCode': 0, 'data': {'flag': 1, 'url': DERIVED, **self.state['result']}} if data is None else data
        await self.observer.observe(response('result', body, when), data, when + .001)

    async def complete(self):
        await self.allocate(); await self.finish(); await self.observer.drain()

    async def prepare_saved_for_reopen(self):
        from uploader.draft_evidence import verify_evidence
        await self.complete()
        self.session.expected.update(platform_fields={'caption': 'fixture caption'}, cover_sha256={})
        candidate = {'id': 'fixture-saved-record', 'fresh': True, 'owner': self.session.account['uid'],
            'caption': 'fixture caption', 'video_ids': [tc_resource_identity(DERIVED)], 'video_count': 1,
            'cover_ids': [tc_resource_identity(resource('automatic-cover'))], 'cover_slots': {}}
        self.session.saves, self.session.issues, self.session.exchanges = [candidate], [], []
        self.session.action_at, self.session.reopen_at = time.time(), None
        self.session.upload_links = lambda: self.observer.augment_links(self.links)
        async def account(): return copy.deepcopy(self.session.account)
        self.session.read_account = account
        checks, _ = verify_evidence('tencent', self.session.expected, candidate,
                                   self.session.upload_links(), self.session.account)
        self.assertTrue(all(value for key, value in checks.items() if key != 'reopened'))
        return candidate

    def attest_normal_reopen(self, candidate):
        self.session.reopen_at = time.time()
        self.session.normal_ui_binding = {'id': candidate['id'], 'owner': candidate['owner'],
                                          'control': 'post_draft_item_edit'}

    async def test_completed_provenance_survives_only_attested_same_record_reopen(self):
        from uploader.draft_evidence import verify_evidence
        candidate = await self.prepare_saved_for_reopen()
        completed = self.session.upload_links()
        self.assertTrue(await self.observer.freeze_for_reopen(candidate))
        self.state = None
        await self.observer.drain()
        self.assertIsNone(self.observer.native)
        # A preceding live success is insufficient during navigation.
        self.assertEqual(self.session.upload_links(), self.links)
        self.attest_normal_reopen(candidate)
        retained = self.session.upload_links()
        self.assertEqual(retained, completed)
        checks, _ = verify_evidence('tencent', self.session.expected, candidate, retained, self.session.account,
            reopened=copy.deepcopy(candidate), ui_fields=False, account_after=self.session.account)
        self.assertTrue(checks['media'])
        self.assertFalse(checks['reopened'])
        foreign = {**candidate, 'video_ids': [tc_resource_identity(resource('foreign'))]}
        checks, _ = verify_evidence('tencent', self.session.expected, candidate, retained, self.session.account,
            reopened=foreign, ui_fields=True, account_after=self.session.account)
        self.assertFalse(checks['reopened'])
        await self.observer.close()
        self.assertEqual(self.session.upload_links(), self.links)

    async def test_malformed_live_state_never_freezes_preceding_success(self):
        candidate = await self.prepare_saved_for_reopen()
        self.state = None
        self.assertFalse(await self.observer.freeze_for_reopen(candidate))
        self.attest_normal_reopen(candidate)
        self.assertEqual(self.session.upload_links(), self.links)

    async def test_freeze_requires_fresh_saved_complete_fields_account_and_clean_session(self):
        candidate = await self.prepare_saved_for_reopen()
        for case in ('not-fresh', 'no-save', 'fields', 'cover', 'owner', 'issue', 'account-after', 'already-navigation'):
            with self.subTest(case=case):
                changed = copy.deepcopy(candidate)
                saves, issues, account_reader = self.session.saves, self.session.issues, self.session.read_account
                if case == 'not-fresh': changed['fresh'] = False
                if case == 'no-save': self.session.saves = []
                if case == 'fields': changed['caption'] = 'foreign'
                if case == 'cover': changed['cover_ids'] = []
                if case == 'owner': changed['owner'] = 'foreign'
                if case == 'issue': self.session.issues = ['existing_failure']
                if case == 'account-after':
                    async def foreign_account(): return {**self.session.account, 'uid': 'foreign'}
                    self.session.read_account = foreign_account
                if case == 'already-navigation': self.session.reopen_at = time.time()
                self.assertFalse(await self.observer.freeze_for_reopen(changed))
                self.session.saves, self.session.issues, self.session.read_account = saves, issues, account_reader
                self.session.reopen_at = None

    async def test_frozen_chain_invalidated_by_selection_protocol_scope_or_evidence_change(self):
        candidate = await self.prepare_saved_for_reopen()
        original = copy.deepcopy(self.state)
        base = {key: copy.deepcopy(getattr(self.session, key)) for key in
                ('expected', 'account', 'files', 'assets', 'exchanges', 'saves', 'issues')}
        records = copy.deepcopy(self.observer.records)
        for case in ('selection', 'allocate', 'transport', 'native', 'nonce', 'account', 'save', 'asset', 'issue', 'session'):
            with self.subTest(case=case):
                for key, value in base.items(): setattr(self.session, key, copy.deepcopy(value))
                self.observer.records = copy.deepcopy(records)
                self.observer._frozen_reopen = None
                self.session.reopen_at = None
                self.session.normal_ui_binding = None
                self.page._sau_draft_evidence = self.session
                self.state = copy.deepcopy(original)
                self.assertTrue(await self.observer.freeze_for_reopen(candidate))
                self.attest_normal_reopen(candidate)
                self.state = None
                await self.observer.drain()
                if case == 'selection': self.session.files.append(copy.deepcopy(self.session.files[0]))
                if case == 'allocate': self.observer.records.append(copy.deepcopy(self.observer.records[0]))
                if case == 'transport': self.session.exchanges.append({'new': True})
                if case == 'native': self.observer.native = {**original, 'sha256': '0'*64}
                if case == 'nonce': self.session.expected['receipt_nonce'] = 'foreign'
                if case == 'account': self.session.account['uid'] = 'foreign'
                if case == 'save': self.session.saves.clear()
                if case == 'asset': self.session.assets['video']['size'] += 1
                if case == 'issue': self.session.issues.append('failure')
                if case == 'session': self.page._sau_draft_evidence = SimpleNamespace()
                self.assertEqual(self.session.upload_links(), self.links)
                self.assertIsNone(self.observer._frozen_reopen)

    async def test_frozen_chain_requires_exact_normal_edit_binding_and_transport_link(self):
        candidate = await self.prepare_saved_for_reopen()
        self.assertTrue(await self.observer.freeze_for_reopen(candidate))
        self.attest_normal_reopen(candidate)
        self.state = None
        await self.observer.drain()
        binding = dict(self.session.normal_ui_binding)
        for key in ('id', 'owner', 'control'):
            self.session.normal_ui_binding = {**binding, key: 'foreign'}
            self.assertEqual(self.session.upload_links(), self.links)
        self.session.normal_ui_binding = binding
        wrong = [{**self.links[0], 'ids': [tc_resource_identity(resource('other-upload'))]}]
        self.assertEqual(self.observer.augment_links(wrong), wrong)
        self.assertEqual(self.session.upload_links(), self.observer._frozen_reopen['links'])

    async def test_exact_source_ticket_terminal_and_native_consumption_extend_one_link(self):
        await self.complete()
        actual = self.observer.augment_links(self.links)
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]['sha256'], self.sha)
        self.assertEqual(set(actual[0]['ids']), {tc_resource_identity(SOURCE), tc_resource_identity(DERIVED)})
        self.assertEqual(self.links[0]['ids'], [tc_resource_identity(SOURCE)])

    async def test_actual_numeric_astra_feature_flag_preserves_original_and_derived_chain(self):
        # The live component's useAstraThumbCover is an integer feature flag.
        body = request_body(self.size)
        body['useAstraThumbCover'] = 1
        await self.allocate(body=body)
        await self.finish()
        await self.observer.drain()
        self.assertEqual(set(self.observer.augment_links(self.links)[0]['ids']),
                         {tc_resource_identity(SOURCE), tc_resource_identity(DERIVED)})

    async def test_nonbinary_or_coerced_astra_flag_never_extends_source_chain(self):
        for value in (2, -1, 1.0, '1', None, [], {}):
            with self.subTest(value=value):
                self.observer.records.clear()
                body = request_body(self.size)
                body['useAstraThumbCover'] = value
                await self.allocate(body=body)
                await self.finish()
                await self.observer.drain()
                self.assertEqual(self.observer.augment_links(self.links), self.links)

    def test_uint64_json_size_is_exact_canonical_and_bounded(self):
        for value in (1, self.size, MAX_VIDEO_BYTES):
            self.assertEqual(_file_size(value), value)
            self.assertEqual(_file_size(str(value)), value)
            self.assertIs(type(_file_size(str(value))), int)
        invalid = (True, False, None, 0, -1, 17.0, 17.5, float('nan'), float('inf'),
                   MAX_VIDEO_BYTES + 1, '', '0', '00', '017', '+17', '-17', ' 17', '17 ',
                   '17\n', '1e1', '1E1', '17.0', '0x11', '１７', 'NaN', 'Infinity',
                   str(MAX_VIDEO_BYTES + 1), '18446744073709551615', '1' * 1000, {}, [])
        for value in invalid:
            with self.subTest(value=repr(value)[:40]):
                self.assertIsNone(_file_size(value))
                self.assertIsNone(_params(request_body(value)))

    async def test_numeric_string_protocol_native_and_consumed_equal_exact_values(self):
        # Protobuf's origin uint64 and derived uint64 independently serialize
        # as strings. The processed file is different in size from the source;
        # only the original immutable File receives the original SHA.
        base = snapshot(self.size, self.sha)
        derived_size = self.size + 3
        base['result']['fileSize'] = base['consumed']['fileSize'] = derived_size
        for encoded in itertools.product((False, True), repeat=5):
            with self.subTest(encoded=encoded):
                self.observer.records.clear(); self.observer.native = None
                self.state = copy.deepcopy(base)
                body = request_body(str(self.size) if encoded[0] else self.size)
                if encoded[1]: self.state['origin']['fileSize'] = str(self.size)
                if encoded[3]: self.state['result']['fileSize'] = str(derived_size)
                if encoded[4]: self.state['consumed']['fileSize'] = str(derived_size)
                terminal = {'flag': 1, 'url': DERIVED, 'width': 320, 'height': 240,
                            'duration': 6, 'fileSize': str(derived_size) if encoded[2] else derived_size}
                await self.allocate(body=body)
                await self.finish(data={'errCode': 0, 'data': terminal})
                await self.observer.drain()
                self.assertTrue(all(record['valid'] for record in self.observer.records))
                self.assertIs(type(self.observer.records[0]['params']['origin']['fileSize']), int)
                self.assertIs(type(self.observer.records[1]['dimensions']['fileSize']), int)
                self.assertEqual(self.observer.native['origin']['fileSize'], self.size)
                self.assertEqual(self.observer.native['result']['fileSize'], derived_size)
                self.assertEqual(self.observer.native['consumed']['fileSize'], derived_size)
                actual = self.observer.augment_links(self.links)
                self.assertEqual(actual[0]['sha256'], self.sha)
                self.assertEqual(set(actual[0]['ids']), {tc_resource_identity(SOURCE), tc_resource_identity(DERIVED)})
                self.assertEqual(self.links[0]['ids'], [tc_resource_identity(SOURCE)])

    async def test_noncanonical_or_inconsistent_size_at_each_boundary_cannot_augment(self):
        invalid = (True, 17.0, 17.5, -1, '', '017', '1.7e1', '+17', '17 ',
                   str(MAX_VIDEO_BYTES + 1), '18446744073709551615')
        base = snapshot(self.size, self.sha)
        for boundary in ('allocate', 'response', 'native-origin', 'native-result', 'native-consumed'):
            for bad_size in invalid:
                with self.subTest(boundary=boundary, bad=bad_size):
                    self.observer.records.clear(); self.observer.native = None
                    self.state = copy.deepcopy(base)
                    body = request_body(self.size)
                    terminal = {'flag': 1, 'url': DERIVED, **base['result']}
                    if boundary == 'allocate': body['clipOriginVideoInfo']['fileSize'] = bad_size
                    elif boundary == 'response': terminal['fileSize'] = bad_size
                    else: self.state[boundary.removeprefix('native-')]['fileSize'] = bad_size
                    await self.allocate(body=body)
                    await self.finish(data={'errCode': 0, 'data': terminal})
                    await self.observer.drain()
                    self.assertEqual(self.observer.augment_links(self.links), self.links)
        # Normalizing only size must not drop unknown metadata, nor make
        # different canonical integers or a foreign ticket/hash equal.
        self.state = copy.deepcopy(base)
        await self.complete()
        for mutate in ('extra-origin', 'extra-result', 'extra-consumed', 'different-size', 'ticket', 'sha'):
            self.state = copy.deepcopy(base)
            if mutate.startswith('extra-'): self.state[mutate[6:]]['unexpected'] = True
            if mutate == 'different-size': self.state['consumed']['fileSize'] = str(self.size + 1)
            if mutate == 'ticket': self.state['ticket']['clipKey'] = 'foreign-ticket'
            if mutate == 'sha': self.state['sha256'] = '0' * 64
            await self.observer.drain()
            self.assertEqual(self.observer.augment_links(self.links), self.links, mutate)

    async def test_multiple_clip_tasks_and_multiple_original_allocations_not_merged_by_sha(self):
        await self.complete()
        self.ticket = {'clipKey': 'other-clip', 'draftId': 'other-draft'}
        await self.allocate(when=self.when + .04)
        self.assertEqual(self.observer.augment_links(self.links), self.links)
        self.observer.records.pop()
        doubled = self.links + [copy.deepcopy(self.links[0])]
        self.assertEqual(self.observer.augment_links(doubled), doubled)

    async def test_wrong_source_ticket_error_and_flag_fail_closed(self):
        mutations = ['source', 'ticket', 'allocate-code', 'result-code', 'flag-bool', 'flag-pending', 'flag-error', 'wrong-url', 'unknown-wrapper']
        for case in mutations:
            with self.subTest(case=case):
                self.observer.records.clear(); self.observer.native = None
                body = request_body(self.size)
                allocated = {'errCode': 0, 'data': dict(self.ticket)}
                rb = dict(self.ticket)
                data = {'errCode': 0, 'data': {'flag': 1, 'url': DERIVED, **self.state['result']}}
                if case == 'source': body['url'] = resource('different-original')
                if case == 'ticket': rb['clipKey'] = 'wrong-key'
                if case == 'allocate-code': allocated['errCode'] = True
                if case == 'result-code': data['errCode'] = 3
                if case == 'flag-bool': data['data']['flag'] = True
                if case == 'flag-pending': data['data']['flag'] = 2
                if case == 'flag-error': data['data']['flag'] = 3
                if case == 'wrong-url': data['data']['url'] = 'https://evil.invalid/fake'
                if case == 'unknown-wrapper': body = {'request': body}
                await self.allocate(body, allocated); await self.finish(rb, data); await self.observer.drain()
                self.assertEqual(self.observer.augment_links(self.links), self.links)

    async def test_custom_edit_parameters_and_wrong_targets_reject(self):
        for key, value in [('timeStart', 1), ('cropDuration', 2), ('x', 1), ('y', 1), ('type', True),
                           ('targetWidth', 319), ('targetHeight', 239), ('width', 321)]:
            with self.subTest(key=key):
                self.observer.records.clear()
                body = request_body(self.size); body[key] = value
                await self.allocate(body); await self.finish(); await self.observer.drain()
                self.assertEqual(self.observer.augment_links(self.links), self.links)

    async def test_native_sha_size_selection_and_consumption_fail_closed(self):
        await self.complete()
        original = copy.deepcopy(self.state)
        for case in ['native-sha', 'native-size', 'consumed-size', 'original-response', 'processed-url', 'native-ticket', 'double-selection', 'different-video-selection', 'late-selection']:
            with self.subTest(case=case):
                self.state = copy.deepcopy(original)
                old_files = copy.deepcopy(self.session.files)
                if case == 'native-sha': self.state['sha256'] = '0' * 64
                if case == 'native-size': self.state['size'] += 1
                if case == 'consumed-size': self.state['consumed']['fileSize'] += 1
                if case == 'original-response': self.state['source_url'] = resource('different-original')
                if case == 'processed-url': self.state['processed_url'] = resource('different-processed')
                if case == 'native-ticket': self.state['ticket']['draftId'] = 'other-draft'
                if case == 'double-selection': self.session.files.append(copy.deepcopy(self.session.files[0]))
                if case == 'different-video-selection': self.session.files.append({'name': 'other.mp4', 'size': self.size, 'sha256': '0'*64, 'selected_at': self.when+.01})
                if case == 'late-selection': self.session.files[0]['selected_at'] = self.when + .01
                await self.observer.drain()
                self.assertEqual(self.observer.augment_links(self.links), self.links)
                self.session.files = old_files
        self.state = original

    async def test_old_request_scope_account_and_closed_session_reject(self):
        await self.allocate(when=self.session.started_at - 2); await self.finish(); await self.observer.drain()
        self.assertEqual(self.observer.augment_links(self.links), self.links)
        self.observer.records.clear(); await self.complete()
        self.session.expected['receipt_nonce'] = 'new-task'
        self.assertEqual(self.observer.augment_links(self.links), self.links)
        self.session.expected['receipt_nonce'] = 'fixture-nonce'
        self.session.account['uid'] = 'other-owner'
        self.assertEqual(self.observer.augment_links(self.links), self.links)
        self.session.account['uid'] = 'fixture-owner'
        self.session.closed = True
        self.assertEqual(self.observer.augment_links(self.links), self.links)

    async def test_pending_before_terminal_allowed_but_conflicting_terminal_rejected(self):
        await self.allocate()
        await self.finish(data={'errCode': 0, 'data': {'flag': 2}}, when=self.when + .01)
        await self.finish(); await self.observer.drain()
        self.assertIn(tc_resource_identity(DERIVED), self.observer.augment_links(self.links)[0]['ids'])
        await self.finish(data={'errCode': 0, 'data': {'flag': 1, 'url': resource('second-output'), **self.state['result']}}, when=self.when + .03)
        self.assertEqual(self.observer.augment_links(self.links), self.links)

    async def test_protocol_diagnostics_never_print_values(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            await self.complete()
        text = stream.getvalue()
        for value in [SOURCE, DERIVED, self.ticket['clipKey'], self.ticket['draftId'], self.sha,
                      'fixture-private-identity', 'fixture-token', 'never-print-user-content', 'fixture-owner']:
            self.assertNotIn(value, text)
        values = [json.loads(x.split('=', 1)[1]) for x in text.splitlines()]
        protocol = [x for x in values if 'operation' in x]
        self.assertEqual([x['operation'] for x in protocol], ['allocate', 'result'])
        self.assertTrue(all(x['parsed'] for x in protocol))
        self.assertTrue(values[-1]['native_read_valid'])

    async def test_evidence_response_drain_single_link_and_signed_checkpoint_restore(self):
        from uploader.draft_evidence import DraftEvidenceSession
        from workbench_model import preflight_payload, build_platform_payload
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'fixture.mp4'
            video.write_bytes(bytes(range(17)))
            self.sha = hashlib.sha256(video.read_bytes()).hexdigest()
            self.state = snapshot(17, self.sha)
            owner = copy.deepcopy(self.session.account)
            expected = build_platform_payload(preflight_payload({'video': str(video), 'title': '测试',
                'description': 'fixture private caption', 'tags': []}, ['tencent']),
                'tencent', 'fixture-job', 'fixture-nonce')
            expected['account'] = owner
            self.page.frames = []
            self.page._sau_file_binding = True
            self.page.on = lambda *args: None
            self.page.remove_listener = lambda *args: None
            original = DraftEvidenceSession(self.page, 'tencent', expected, account_reader=lambda: dict(owner))
            self.page._sau_draft_evidence = original
            await original.initialize()
            self.assertEqual(original.issues, [])
            asset = original.assets['video']
            selected_at = time.time()
            original.files.append({**asset, 'type': 'video/mp4', 'selected_at': selected_at})
            original.exchanges.append({'tc_protocol': 'sns', 'stage': 'complete', 'ids': [tc_resource_identity(SOURCE)],
                'metadata': (asset['name'], asset['size']), 'task_id': 'fixture-upload-task', 'range': (0, 17),
                'whole_body': True, 'body_sha256': None, 'observed_at': selected_at+.001})
            await asyncio.sleep(.003)
            allocated = response('allocate', request_body(17), time.time()-.0001)
            async def allocate_json(): return {'errCode': 0, 'data': dict(self.ticket)}
            allocated.json = allocate_json
            await original._observe(allocated)
            await asyncio.sleep(.002)
            finished = response('result', dict(self.ticket), time.time()-.0001)
            async def finish_json(): return {'errCode': 0, 'data': {'flag': 1, 'url': DERIVED, **self.state['result']}}
            finished.json = finish_json
            await original._observe(finished)
            await original.drain()
            links = original.upload_links()
            self.assertEqual(len(links), 1)
            self.assertEqual(set(links[0]['ids']), {tc_resource_identity(SOURCE), tc_resource_identity(DERIVED)})
            checkpoint = await original.export_checkpoint(seal_key=bytes(range(32)))
            self.assertEqual(len(checkpoint['payload']['upload_links']), 1)
            self.assertEqual(checkpoint['payload']['phase'], 'uploaded_not_saved')
            await original.close()
            fresh = DraftEvidenceSession(self.page, 'tencent', expected, account_reader=lambda: dict(owner))
            self.page._sau_draft_evidence = fresh
            await fresh.initialize()
            result = await fresh.restore_checkpoint(checkpoint, seal_key=bytes(range(32)),
                media_reader=lambda: {'source': 'platform_composer', 'video_count': 1, 'video_ids': [tc_resource_identity(DERIVED)]})
            self.assertTrue(result['video_upload_verified'])
            self.assertFalse(result['save_verified'])
            self.assertEqual(fresh.upload_links(), links)
            saved = {'id': 'restored-new-save', 'fresh': True, 'owner': owner['uid'],
                'caption': expected['platform_fields']['caption'], 'video_ids': [tc_resource_identity(DERIVED)],
                'video_count': 1, 'cover_ids': [tc_resource_identity(resource('automatic-cover'))], 'cover_slots': {}}
            fresh.saves.append(saved)
            fresh.action_at = time.time()
            self.state = None
            # Positive: authenticated full restore is already historical proof;
            # permit the normal UI read, but create no new native/frozen chain.
            self.assertTrue(await fresh.tc_processing_observer.freeze_for_reopen(saved))
            self.assertIsNone(fresh.tc_processing_observer.native)
            self.assertIsNone(fresh.tc_processing_observer._frozen_reopen)
            self.assertEqual(fresh.upload_links(), links)
            # Negative: even equal bytes selected again or a new allocation
            # cannot inherit this preceding sealed resource proof.
            fresh.files.append({**fresh.assets['video'], 'type': 'video/mp4', 'selected_at': time.time()})
            self.assertFalse(await fresh.tc_processing_observer.freeze_for_reopen(saved))
            fresh.files.clear()
            fresh.tc_processing_observer.records.append({'operation': 'allocate', 'valid': False, 'source': None})
            self.assertFalse(await fresh.tc_processing_observer.freeze_for_reopen(saved))
            fresh.tc_processing_observer.records.clear()
            restored_at = fresh._checkpoint_restored_at
            fresh._checkpoint_restored_at = fresh.started_at - 1
            self.assertFalse(await fresh.tc_processing_observer.freeze_for_reopen(saved))
            fresh._checkpoint_restored_at = restored_at
            fresh._checkpoint_expires_at = time.time() - 1
            self.assertFalse(await fresh.tc_processing_observer.freeze_for_reopen(saved))
            await fresh.close()

    def test_only_exact_official_origin_and_endpoints(self):
        good = 'https://channels.weixin.qq.com' + PREFIX + 'post_clip_video'
        self.assertEqual(processing_endpoint(good), 'allocate')
        for wrong in [good.replace('https:', 'http:'), good.replace('channels.weixin.qq.com', 'evil.invalid'),
                      good.replace('channels.weixin.qq.com', 'channels.weixin.qq.com:443'),
                      good.replace('/micro/content/', '/unknown/'), good + '/extra', good + '#fragment',
                      good.replace('channels.weixin.qq.com', 'channels.weixin.qq.com.evil.invalid')]:
            self.assertIsNone(processing_endpoint(wrong))


DOM = r'''<!doctype html><div id="material"></div><div id="shadow"></div><script>
window.buildFixture=(size,source,derived,foreign=false)=>{
 const realm=foreign?document.querySelector('iframe').contentWindow:window;
 const bytes=new Uint8Array(size);for(let i=0;i<size;i++)bytes[i]=(i*17+23)%256;
 const native=new realm.File([bytes],'fixture.mp4',{type:'video/mp4'});
 const origin={width:320,height:240,duration:6,fileSize:size};
 const result={...origin,flag:1,url:derived};
 const file={originFileObj:native,size,isVideo:true,isImage:false,isValid:true,status:'done',uuid:'native-upload-uuid',
   response:{httpsUrl:source,DownloadURL:source.replace('https://finder.video.qq.com','http://wxapp.tc.qq.com')},
   processedResponse:result,extInfo:{width:320,height:240,duration:6}};
 const media={url:derived,width:320,height:240,videoPlayLen:6,fileSize:size,md5sum:file.uuid};
 const fixture=window.fixture={file,media,businessCalls:0};
 const fn=()=>{fixture.businessCalls++;throw Error('business method called')};
 const upload={value:[file],handleCropVideo:fn,clipVideoRequest:fn,getClipVideoResult:fn,updateVideo:fn,updateFile:fn,setFileList:fn};
 const vm=fixture.vm={fileList:[file],$refs:{postUpload:upload},handleFileListChange:fn,handleSetFileList:fn,handleDeleteFile:fn,updateFile:fn,
   postStore:{postObjDesc:{description:'private-caption-never-export',media:[media]},createPostStatus:{clipTicket:{clipKey:'fixture-clip-key',draftId:'fixture-draft-id'}}}};
 const el=foreign?realm.document.createElement('div'):document.querySelector('#material');
 if(foreign){document.querySelector('#shadow').attachShadow({mode:'open'}).append(el);fixture.foreign=true;}
 el.__vue__=vm;vm.$el=el;fixture.el=el;return true;
};
</script>'''


class ProcessingNativeBrowserTests(ProcessingProtocolTests):
    # Reuse protocol setup only, and run the explicitly defined native tests;
    # inherited protocol methods are shadowed below to avoid redundant runs.
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.p = await async_playwright().start()
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        self.browser = await self.p.chromium.launch(headless=True, executable_path=str(shell) if shell.is_file() else None)
        self.context = await self.browser.new_context()
        async def route(r):
            await r.fulfill(content_type='text/html', body='<body></body>' if r.request.url.endswith('/fixture-owner') else DOM)
        await self.context.route('**/*', route)
        self.page = await self.context.new_page()
        await self.page.goto('https://channels.weixin.qq.com/platform/post/create')
        self.size = 2 * 1024 * 1024 + 65
        self.sha = hashlib.sha256(bytes((i * 17 + 23) % 256 for i in range(self.size))).hexdigest()
        self.state = snapshot(self.size, self.sha)
        self.session = self.make_session(self.page)
        self.observer = await install_tc_processing_observer(self.session)
        self.when = time.time() + .01
        self.links = [{'kind': 'video', 'sha256': self.sha, 'complete': True, 'ids': [tc_resource_identity(SOURCE)]}]
        await self.page.evaluate('([size,source,derived])=>buildFixture(size,source,derived)', [self.size, SOURCE, DERIVED], isolated_context=False)

    async def asyncTearDown(self):
        await self.observer.close()
        await self.context.close(); await self.browser.close(); await self.p.stop()

    async def js(self, script):
        return await self.page.evaluate(script, isolated_context=False)

    async def test_normal_saved_editor_without_original_native_file_keeps_only_historical_chain(self):
        candidate = await self.prepare_saved_for_reopen()
        self.assertTrue(await self.observer.freeze_for_reopen(candidate))
        completed = self.session.upload_links()
        self.attest_normal_reopen(candidate)
        # Rehydrated server records legitimately have no upload File/processor.
        await self.js('delete fixture.file.originFileObj; fixture.vm.$refs.postUpload=null')
        await self.observer.drain()
        self.assertIsNone(self.observer.native)
        self.assertEqual(self.session.upload_links(), completed)
        self.assertEqual(await self.js('fixture.businessCalls'), 0)

    async def test_native_file_full_multichunk_sha_and_exact_consumer(self):
        await self.complete()
        self.assertEqual(self.observer.native['sha256'], self.sha)
        self.assertIn(tc_resource_identity(DERIVED), self.observer.augment_links(self.links)[0]['ids'])
        self.assertEqual(await self.js('fixture.businessCalls'), 0)
        self.assertNotIn('description', self.observer.native)
        await self.observer.drain()
        self.assertEqual(self.observer.native['sha256'], self.sha)

    async def test_native_protobuf_string_result_and_consumer_normalize_without_business_call(self):
        # Exact observed D metadata shape, with isolated synthetic bytes and
        # resource/ticket values. The source bytes still undergo the native
        # multi-chunk SHA; the derived size differs by three bytes.
        await self.observer.close()
        self.size = 2_029_555
        self.sha = hashlib.sha256(bytes((i * 17 + 23) % 256 for i in range(self.size))).hexdigest()
        self.session = self.make_session(self.page)
        self.observer = await install_tc_processing_observer(self.session)
        self.when = time.time() + .01
        self.links = [{'kind': 'video', 'sha256': self.sha, 'complete': True, 'ids': [tc_resource_identity(SOURCE)]}]
        await self.page.evaluate('([size,source,derived])=>buildFixture(size,source,derived)',
            [self.size, SOURCE, DERIVED], isolated_context=False)
        derived_size = 2_029_558
        await self.js('''Object.assign(fixture.file.extInfo,{width:1280,height:720});
          Object.assign(fixture.file.processedResponse,{width:1280,height:720,fileSize:String(fixture.file.size+3)});
          Object.assign(fixture.media,{width:1280,height:720,fileSize:fixture.file.processedResponse.fileSize});''')
        body = request_body(str(self.size))
        body.update(width=1280, height=720, targetWidth=1280, targetHeight=720)
        body['clipOriginVideoInfo'].update(width=1280, height=720)
        await self.allocate(body=body)
        await self.finish(data={'errCode': 0, 'data': {'flag': 1, 'url': DERIVED, 'width': 1280,
            'height': 720, 'duration': 6, 'fileSize': str(derived_size)}})
        await self.observer.drain()
        native = self.observer.native
        self.assertEqual(native['size'], self.size)
        self.assertEqual(native['sha256'], self.sha)
        self.assertEqual(native['origin']['fileSize'], self.size)
        self.assertEqual(native['result']['fileSize'], derived_size)
        self.assertEqual(native['consumed']['fileSize'], derived_size)
        self.assertIs(type(native['result']['fileSize']), int)
        self.assertIs(type(native['consumed']['fileSize']), int)
        self.assertIn(tc_resource_identity(DERIVED), self.observer.augment_links(self.links)[0]['ids'])
        self.assertEqual(await self.js('fixture.businessCalls'), 0)

    async def test_native_reader_rejects_noncanonical_processed_or_consumed_sizes(self):
        await self.allocate(); await self.finish()
        for boundary in ('processed', 'consumed'):
            for value in (True, 17.5, 0, -1, '', '017', '+17', '1e1', '17.0',
                          '17\n', str(MAX_VIDEO_BYTES + 1), '18446744073709551615'):
                with self.subTest(boundary=boundary, value=value):
                    await self.page.evaluate('([size,source,derived])=>buildFixture(size,source,derived)',
                        [self.size, SOURCE, DERIVED], isolated_context=False)
                    object_path = 'fixture.file.processedResponse' if boundary == 'processed' else 'fixture.media'
                    await self.js(object_path + '.fileSize=' + json.dumps(value))
                    await self.observer.drain()
                    self.assertIsNone(self.observer.native)
                    self.assertEqual(self.observer.augment_links(self.links), self.links)
        self.assertEqual(await self.js('fixture.businessCalls'), 0)

    async def test_foreign_native_file_in_adopted_open_shadow_node(self):
        await self.js('document.querySelector("#material").__vue__=null;new Promise(resolve=>{const f=document.createElement("iframe");f.onload=resolve;f.src="https://channels.weixin.qq.com/micro/content/fixture-owner";document.body.append(f)})')
        await self.page.evaluate('([size,source,derived])=>buildFixture(size,source,derived,true)', [self.size, SOURCE, DERIVED], isolated_context=False)
        await self.complete()
        self.assertIsNotNone(self.observer.native)
        self.assertEqual(self.observer.native['sha256'], self.sha)
        self.assertIn(tc_resource_identity(DERIVED), self.observer.augment_links(self.links)[0]['ids'])
        self.assertTrue(await self.js('fixture.file.originFileObj instanceof document.querySelector("iframe").contentWindow.File'))
        self.assertEqual(await self.js('fixture.businessCalls'), 0)

    async def test_fake_native_duplicate_material_wrong_uuid_and_wrong_consumer_reject(self):
        await self.allocate(); await self.finish()
        for case in ['fake-file', 'different-native', 'duplicate-material', 'uuid', 'consumer']:
            with self.subTest(case=case):
                await self.page.evaluate('([size,source,derived])=>buildFixture(size,source,derived)', [self.size, SOURCE, DERIVED], isolated_context=False)
                if case == 'fake-file': await self.js('fixture.file.originFileObj={name:"fixture.mp4",size:fixture.file.size}')
                if case == 'different-native': await self.js('fixture.file.originFileObj=new File([new Uint8Array(fixture.file.size)],"fixture.mp4")')
                if case == 'duplicate-material': await self.js('const e=document.createElement("div");e.id="duplicate";e.__vue__={...fixture.vm};document.body.append(e)')
                if case == 'uuid': await self.js('fixture.media.md5sum="wrong-uuid"')
                if case == 'consumer': await self.js('fixture.media.url=fixture.file.response.httpsUrl')
                await self.observer.drain()
                self.assertEqual(self.observer.augment_links(self.links), self.links)
                await self.js('document.querySelector("#duplicate")?.remove()')


# Native-browser workflows are separate; protocol cases run once in their class.
for _name in tuple(ProcessingProtocolTests.__dict__):
    if _name.startswith('test_'):
        setattr(ProcessingNativeBrowserTests, _name, None)


if __name__ == '__main__':
    unittest.main()
