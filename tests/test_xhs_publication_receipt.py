"""Observed XHS native response plus official success redirect; no network."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_publish_action import FakeFrame, FakePage, FakeRequest, FakeResponse
from uploader import publish_action as action


BEFORE = 'https://creator.xiaohongshu.com/publish/publish?source=&target=video'
AFTER = 'https://creator.xiaohongshu.com/publish/publish?source=&published=true'
POST = 'https://edith.xiaohongshu.com/web_api/sns/v2/note'


class XhsSuccessURLTests(unittest.TestCase):
    def test_current_video_to_exact_published_true_or_original_success_page(self):
        self.assertTrue(action._xhs_success_page(BEFORE, AFTER))
        self.assertTrue(action._xhs_success_page(BEFORE, 'https://creator.xiaohongshu.com/publish/success?source=web'))

    def test_foreign_schemes_authorities_draft_edit_queries_and_preexisting_success_rejected(self):
        invalid = [AFTER.replace('https:', 'http:'), AFTER.replace('creator.', 'creator.evil.'),
            AFTER.replace('creator.xiaohongshu.com', 'user@creator.xiaohongshu.com'),
            AFTER.replace('creator.xiaohongshu.com', 'creator.xiaohongshu.com:444'),
            AFTER + '&draft_id=', AFTER + '&type=edit', AFTER + '&target=video', AFTER + '&unknown=x',
            AFTER + '&published=true', AFTER + '#fragment', AFTER.replace('published=true', 'published=false'),
            AFTER.replace('published=true', 'published=True')]
        for value in invalid:
            with self.subTest(value=value): self.assertFalse(action._xhs_success_page(BEFORE, value))
        for value in (AFTER, BEFORE + '&draft_id=', BEFORE + '&type=edit', BEFORE + '&target=video',
                      BEFORE.replace('target=video', 'target=image'), BEFORE.replace('https:', 'http:')):
            with self.subTest(before=value): self.assertFalse(action._xhs_success_page(value, AFTER))

    def test_native_submit_observation_has_exact_production_endpoint(self):
        self.assertTrue(action._xhs_native_submit_url(POST))
        for value in (POST.replace('https:', 'http:'), POST + '/edit', POST + '#f',
                      POST.replace('edith.', 'other.'), POST.replace('edith.', 'user@edith.'),
                      POST.replace('.com/', '.com:444/')):
            with self.subTest(value=value): self.assertFalse(action._xhs_native_submit_url(value))


class XhsObservedReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.frame = FakeFrame(BEFORE); self.page = FakePage(self.frame); self.page.main_frame = self.frame
        self.account = {'platform': 'xiaohongshu', 'verified': True, 'identity': 'synthetic-owner', 'uid': 'synthetic-uid'}
        self.expected = {'job_id': 'synthetic-job', 'receipt_nonce': 'synthetic-new-nonce',
                         'workflow_action': 'publish', 'account': copy.deepcopy(self.account)}
        self.session = SimpleNamespace(page=self.page, closed=False, platform='xiaohongshu',
                                       expected=copy.deepcopy(self.expected), account=copy.deepcopy(self.account))
        self.page._sau_draft_evidence = self.session
        self.observation = action._Observation(self.frame, 'xiaohongshu', armed=True)
        self.observation.receipt_scope = action._xhs_receipt_scope(self.page, self.expected, self.account, self.frame)
        self.handlers = action._bind_observer(self.page, self.observation)

    async def asyncTearDown(self):
        action._remove_observer(self.page, self.handlers)
        if self.observation.tasks: await asyncio.gather(*self.observation.tasks, return_exceptions=True)

    def navigate(self, url=AFTER):
        self.page.url = url; self.frame.url = url

    def native_post(self, *, status=200, url=POST, frame=None, method='POST', response=True):
        request = FakeRequest(url, frame or self.frame, method)
        self.page.emit('request', request)
        if response: self.page.emit('response', FakeResponse(request, {}, status=status))
        return request

    async def receipt(self, timeout=.015, account=None):
        with patch.object(action, 'inspect_session', AsyncMock(return_value=self.account if account is None else account)):
            return await action._wait_for_receipt(self.page, self.observation, BEFORE, timeout)

    async def test_unique_same_scope_post_response_and_success_return_verifies_submission(self):
        self.native_post(); self.navigate()
        self.assertEqual(await self.receipt(), {'kind': 'success_page', 'platform': 'xiaohongshu'})
        self.assertEqual(len(self.observation.requests), 1); self.assertEqual(len(self.observation.responses), 1)

    async def test_url_before_response_waits_for_this_response(self):
        request = self.native_post(response=False); self.navigate()
        async def later():
            await asyncio.sleep(.01)
            self.page.emit('response', FakeResponse(request, {}, status=200))
        delivery = asyncio.create_task(later())
        self.assertIsNotNone(await self.receipt(timeout=.08)); await delivery

    async def test_zero_request_request_only_failed_response_or_multiple_posts_never_verify(self):
        self.navigate(); self.assertIsNone(await self.receipt(timeout=0))
        request = self.native_post(response=False); self.assertIsNone(await self.receipt(timeout=0))
        self.page.emit('response', FakeResponse(request, {}, status=500))
        self.assertIsNone(await self.receipt(timeout=0))
        self.native_post(); self.assertIsNone(await self.receipt(timeout=0))

    async def test_foreign_frame_host_or_non_post_cannot_satisfy_success_gate(self):
        for values in ({'frame': FakeFrame(BEFORE)}, {'url': POST.replace('edith.', 'foreign.')}, {'method': 'PUT'}):
            with self.subTest(values=values):
                self.native_post(**values); self.navigate()
                self.assertIsNone(await self.receipt(timeout=0))

    async def test_nonce_job_account_session_or_root_frame_switch_rejects_old_receipt(self):
        self.native_post(); self.navigate()
        for changed in ('nonce', 'job', 'account', 'session', 'frame'):
            with self.subTest(changed=changed):
                self.session.expected = copy.deepcopy(self.expected); self.session.account = copy.deepcopy(self.account)
                self.page._sau_draft_evidence = self.session; self.page.main_frame = self.frame
                if changed == 'nonce': self.session.expected['receipt_nonce'] = 'different'
                elif changed == 'job': self.session.expected['job_id'] = 'different'
                elif changed == 'account': self.session.account['identity'] = 'different'
                elif changed == 'session': self.page._sau_draft_evidence = copy.copy(self.session)
                elif changed == 'frame': self.page.main_frame = FakeFrame(AFTER)
                self.assertIsNone(await self.receipt(timeout=0))

    async def test_live_account_mismatch_and_scope_change_while_reading_reject(self):
        self.native_post(); self.navigate()
        self.assertIsNone(await self.receipt(timeout=0, account={**self.account, 'identity': 'foreign'}))
        async def inspect(*_):
            self.session.expected['receipt_nonce'] = 'changed-during-read'; return self.account
        with patch.object(action, 'inspect_session', side_effect=inspect):
            self.assertIsNone(await action._wait_for_receipt(self.page, self.observation, BEFORE, 0))


if __name__ == '__main__': unittest.main()
