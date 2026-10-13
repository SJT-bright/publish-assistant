"""Fine-grained draft progress follows isolated, observed UI gates only."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uploader import bili_controls, draft_only, draft_progress


def progress_events(output):
    return [json.loads(line[len('DRAFT_PROGRESS='):]) for line in output.splitlines()
            if line.startswith('DRAFT_PROGRESS=')]


class ProgressHookTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        with patch.dict(os.environ, {'SAU_PROGRESS_JOB_ID': '', 'SAU_PROGRESS_PLATFORM': ''}):
            draft_progress.configure('bilibili')

    async def test_bili_transfer_completes_only_after_native_done_state_without_percent(self):
        page = SimpleNamespace()
        states = [
            {'task_count': 1, 'done_count': 0},
            {'task_count': 1, 'done_count': 1},
        ]
        output = io.StringIO()
        with patch.dict(os.environ, {'SAU_PROGRESS_JOB_ID': 'hook-fixture', 'SAU_PROGRESS_PLATFORM': 'bilibili'}), \
             redirect_stdout(output), \
             patch('uploader.draft_only.assert_draft_safe', AsyncMock()), \
             patch.object(bili_controls, '_frame', AsyncMock(return_value=object())), \
             patch.object(bili_controls, '_state', AsyncMock(side_effect=states)) as read_state, \
             patch.object(bili_controls.asyncio, 'sleep', AsyncMock()):
            draft_progress.configure('bilibili')
            await bili_controls.bili_wait_uploaded(page, timeout=1)

        rows = progress_events(output.getvalue())
        self.assertEqual([row['plan'] for row in rows if row.get('plan')], [['transfer']])
        transfer = [row for row in rows if row.get('operation') == 'transfer']
        self.assertEqual([row['operation_state'] for row in transfer], ['running', 'completed'])
        self.assertTrue(all('operation_percent' not in row for row in transfer))
        self.assertEqual(read_state.await_count, 2)

    async def test_bili_saved_ack_does_not_complete_save_before_fresh_candidate(self):
        expected = {'title': 'fixture-title', 'job_id': 'hook-fixture'}
        observer = SimpleNamespace(wait_saved_ack=AsyncMock(return_value=True),
                                   pre_reopen_verified=AsyncMock(return_value=False))
        session = SimpleNamespace(platform='bilibili', expected=expected, issues=[], saves=[], account={},
                                  files=[], exchanges=[], reads=[], bili_observer=observer,
                                  candidate=AsyncMock(return_value=None), upload_links=lambda: [],
                                  read_account=AsyncMock(return_value={}), close=AsyncMock())
        page = SimpleNamespace(_sau_draft_evidence=session, context=object(), url='http://fixture.invalid/editor')
        checks = dict.fromkeys(draft_only.CHECKS, False)
        output = io.StringIO()

        with patch.dict(os.environ, {'SAU_PROGRESS_JOB_ID': 'hook-fixture', 'SAU_PROGRESS_PLATFORM': 'bilibili'}), \
             redirect_stdout(output), \
             patch.object(draft_only, 'install_draft_guard', AsyncMock()), \
             patch.object(draft_only, 'assert_draft_safe', AsyncMock()), \
             patch.object(draft_only, '_checkpoint_before_save', AsyncMock()), \
             patch.object(draft_only, '_draft_action', AsyncMock(return_value='保存草稿')), \
             patch.object(draft_only, '_reopen', AsyncMock()) as reopen, \
             patch.object(draft_only, 'verify_evidence', return_value=(checks, ['record_not_observed'])), \
             patch('uploader.bili_reopen.bili_open_draft_management', AsyncMock(return_value=True)) as open_management:
            draft_progress.configure('bilibili')
            result = await draft_only.save_draft(page, 'B站', 'fixture-title', receipt_timeout=.01,
                                                  expected=expected)

        rows = progress_events(output.getvalue())
        response = [row for row in rows if row.get('stage') == 'save' and row.get('operation') == 'response']
        self.assertEqual([row['operation_state'] for row in response], ['running', 'failed'])
        self.assertFalse(any(row.get('stage') == 'verify' for row in rows))
        self.assertEqual([(row['stage'], row['state']) for row in rows
                          if 'operation' not in row and 'plan' not in row],
                         [('save', 'running'), ('save', 'failed')])
        self.assertEqual(result['status'], 'result_unknown')
        observer.wait_saved_ack.assert_awaited_once()
        open_management.assert_awaited_once()
        session.candidate.assert_awaited_once()
        reopen.assert_not_awaited()
        session.close.assert_awaited_once()
