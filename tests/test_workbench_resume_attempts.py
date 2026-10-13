"""Explicit sealed-upload continuations; isolated state, no browser or platform."""
import copy
import hashlib
import hmac
import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import draft_workbench as wb
from tests import test_workbench_resume as fixtures


class ResumeAttempts(unittest.TestCase):
    # Reuse only the isolated fixture helpers, not the original test methods.
    setUp = fixtures.UploadedResume.setUp
    tearDown = fixtures.UploadedResume.tearDown
    write_checkpoint = fixtures.UploadedResume.write_checkpoint
    request = fixtures.UploadedResume.request
    available = fixtures.UploadedResume.available
    resume = fixtures.UploadedResume.resume
    fake_worker = fixtures.UploadedResume.fake_worker

    def attempt_directory(self, number):
        return self.checkpoint_path.parent / 'attempts' / ('douyin-resume-' + str(number))

    def attempt_checkpoint(self, number):
        return self.attempt_directory(number) / self.checkpoint_path.name

    def write_at(self, path, proof=None, forged=False):
        payload = copy.deepcopy(proof or self.proof)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()
        checkpoint = {'checkpoint_version': 1, 'payload': payload,
                      'mac': '0' * 64 if forged else hmac.new(self.key, encoded, hashlib.sha256).hexdigest()}
        wb.atomic_json(path, checkpoint)
        return checkpoint

    def stopped_attempt(self, lines=('恢复轮日志\n',)):
        self.resume()
        self.fake_worker(lines)
        self.assertFalse(self.job['active'])
        self.assertEqual(self.item['status'], 'result_unknown')

    def assert_blocked_without_mutation(self):
        job, ledger = copy.deepcopy(self.job), copy.deepcopy(wb.LEDGER)
        self.assertFalse(self.available())
        with patch.object(wb.threading, 'Thread') as worker, self.assertRaises(ValueError):
            wb.resume_job(self.request())
        worker.assert_not_called()
        self.assertEqual(self.job, job)
        self.assertEqual(wb.LEDGER, ledger)

    def event(self, stage, state):
        return 'DRAFT_PROGRESS=' + json.dumps({'v': 1, 'job_id': self.job['id'],
            'platform': 'douyin', 'stage': stage, 'state': state})

    def test_continuation_starts_at_real_session_and_counts_only_received_completion(self):
        old_progress = {'stage': 'cover', 'label': '设置封面', 'state': 'unknown',
                        'started_at': 10, 'updated_at': 20,
                        'completed': ['session', 'video', 'upload'], 'skipped': []}
        self.item['progress'] = copy.deepcopy(old_progress)
        self.resume()
        self.assertEqual(self.item['execution_attempts'][0]['progress'], old_progress)
        progress = self.item['progress']
        self.assertEqual((progress['stage'], progress['state'], progress['completed']), ('session', 'running', []))
        start = progress['started_at']
        wb.transition(self.job, self.item, 'running')
        for at in (start + 1, start + 10):
            with patch.object(wb.time, 'time', return_value=at):
                self.assertTrue(wb.worker_progress(self.job, self.item, self.event('session', 'running')))
            self.assertEqual(self.item['progress']['started_at'], start)
            self.assertEqual(self.item['progress']['completed'], [])
        # The runner emits restored video/upload completions before it closes
        # the session scope. Accept that real ordering without prefilled steps.
        for stage in ('video', 'upload', 'session'):
            self.assertTrue(wb.worker_progress(self.job, self.item, self.event(stage, 'completed')))
        self.assertEqual(self.item['progress']['completed'], ['session', 'video', 'upload'])
        self.assertEqual(self.item['status'], 'running')
        self.assertFalse(wb.worker_progress(self.job, self.item, self.event('session', 'running')))

    def test_failed_restore_leaves_session_visible_and_next_explicit_attempt_resets_it(self):
        self.resume()
        wb.transition(self.job, self.item, 'running')
        self.assertTrue(wb.worker_progress(self.job, self.item, self.event('session', 'failed')))
        wb.transition(self.job, self.item, 'adapter_mismatch')
        self.job['active'] = False
        wb.persist()
        self.assertEqual(self.item['progress']['stage'], 'session')
        self.assertEqual(self.item['progress']['completed'], [])
        self.assertTrue(self.available())
        self.resume()
        self.assertEqual(self.item['resume_count'], 2)
        self.assertEqual(self.item['execution_attempts'][1]['progress']['state'], 'failed')
        self.assertEqual(self.item['progress']['state'], 'running')
        self.assertEqual(self.item['progress']['completed'], [])

    def test_three_explicit_attempts_keep_independent_files_and_flat_complete_audit(self):
        original_proof = self.checkpoint_path.read_bytes()
        original_log = self.checkpoint_path.parent / 'douyin.log'
        original_receipt = self.checkpoint_path.parent / 'douyin-receipt.json'
        original_log.write_text('original log')
        original_receipt.write_text('original receipt')
        expected = copy.deepcopy(self.item['expected'])
        for number in range(1, 4):
            self.assertTrue(self.available())
            job, thread = self.resume()
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            directory = self.attempt_directory(number)
            self.assertTrue(directory.is_dir())
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
            self.assertEqual(self.item['progress']['completed'], [])
            calls = self.fake_worker((f'unique attempt {number}\n',))
            self.assertEqual(len(calls), 1)
            command = calls[0][0]
            self.assertEqual(command[command.index('--resume-checkpoint') + 1], str(self.checkpoint_path))
            self.assertEqual(Path(command[command.index('--checkpoint-output') + 1]).parent, directory)
            self.assertIn(f'unique attempt {number}', (directory / 'douyin.log').read_text())
            self.assertEqual(self.item['expected'], expected)
            self.assertEqual(len(self.item['execution_attempts']), number)
            self.assertEqual([row.get('resume_count', 0) for row in self.item['execution_attempts']], list(range(number)))
            self.assertTrue(all(row['expected']['receipt_nonce'] == expected['receipt_nonce'] for row in self.item['execution_attempts']))
            ledger = wb.LEDGER[expected['operation_key']]
            self.assertEqual(ledger['execution_attempt'], number)
            self.assertEqual([row.get('execution_attempt', 0) for row in ledger['attempts']], list(range(number)))
            self.assertTrue(all('attempts' not in row for row in ledger['attempts']))
            self.assertIn('原失败日志', self.item['execution_attempts'][0]['logs_snapshot'])
            if number > 1:
                self.assertIn(f'unique attempt {number-1}', '\n'.join(self.item['execution_attempts'][-1]['logs_snapshot']))
        self.assertEqual(self.checkpoint_path.read_bytes(), original_proof)
        self.assertEqual(original_log.read_text(), 'original log')
        self.assertEqual(original_receipt.read_text(), 'original receipt')
        self.assertEqual(wb.HISTORY, [])
        self.assert_blocked_without_mutation()
        row = wb.state()['job']['results'][0]
        for private in ('expected', 'execution_attempts', 'resume_binding', 'resume_count'):
            self.assertNotIn(private, row)
        self.assertFalse(row['resumable'])

    def test_latest_proof_is_bound_and_missing_later_output_keeps_that_proof(self):
        self.stopped_attempt()
        newer = copy.deepcopy(self.proof)
        newer['exported_at'] += 1
        first = self.attempt_checkpoint(1)
        self.write_at(first, newer)
        saved = first.read_bytes()
        self.resume()
        self.assertEqual(self.item['resume_binding']['checkpoint_attempt'], 1)
        self.assertEqual(self.item['resume_binding']['resume_attempt'], 2)
        calls = self.fake_worker()
        self.assertEqual(calls[0][0][calls[0][0].index('--resume-checkpoint') + 1], str(first))
        self.assertFalse(self.attempt_checkpoint(2).exists())
        self.assertTrue(self.available())
        self.resume()
        calls = self.fake_worker()
        self.assertEqual(calls[0][0][calls[0][0].index('--resume-checkpoint') + 1], str(first))
        self.assertEqual(first.read_bytes(), saved)

    def test_existing_invalid_newer_proof_cannot_fall_back_to_valid_original(self):
        self.stopped_attempt()
        latest = self.attempt_checkpoint(1)
        variants = [('forged', self.proof, True)]
        wrong_owner = copy.deepcopy(self.proof); wrong_owner['account']['identity'] = 'wrong-owner'
        variants.append(('wrong-owner', wrong_owner, False))
        incomplete = copy.deepcopy(self.proof); incomplete['upload_links'][0]['complete'] = False
        variants.append(('incomplete', incomplete, False))
        wrong_nonce = copy.deepcopy(self.proof); wrong_nonce['identity']['receipt_nonce'] = 'wrong-nonce'
        variants.append(('wrong-nonce', wrong_nonce, False))
        expired = copy.deepcopy(self.proof)
        expired.update(origin_at=1, exported_at=2, expires_at=3)
        variants.append(('expired', expired, False))
        for label, proof, forged in variants:
            with self.subTest(label=label):
                self.write_at(latest, proof, forged)
                self.assert_blocked_without_mutation()
        self.write_at(latest)
        os.chmod(latest, 0o644)
        self.assert_blocked_without_mutation()
        latest.unlink()
        latest.symlink_to(self.checkpoint_path)
        self.assert_blocked_without_mutation()

    def test_marker_in_original_or_any_attempt_forbids_all_older_fallback(self):
        self.stopped_attempt()
        self.write_at(self.attempt_checkpoint(1))
        for number in range(4):
            with self.subTest(number=number):
                checkpoint = self.checkpoint_path if number == 0 else self.attempt_checkpoint(number)
                marker = checkpoint.with_suffix('.save-started.json')
                wb.atomic_json(marker, {'started': True})
                self.assert_blocked_without_mutation()
                marker.unlink()
                if number > 1:
                    checkpoint.parent.rmdir()
        marker = self.attempt_checkpoint(1).with_suffix('.save-started.json')
        marker.symlink_to(marker.parent / 'missing')
        self.assert_blocked_without_mutation()

    def test_historical_save_risk_restriction_and_cancelled_evidence_is_not_erased(self):
        self.stopped_attempt()
        row = self.item['execution_attempts'][0]
        original = copy.deepcopy(row)
        variants = [
            {'status': status} for status in ('risk_paused', 'platform_restricted', 'cancelled', 'saved_unverified', 'local_draft_verified')
        ] + [
            {'progress': {'stage': 'save'}},
            {'progress': {'stage': 'content', 'completed': ['save']}},
            {'progress': {'stage': 'content', 'skipped': ['verify']}},
            {'evidence': {'draft_id': 'already-saved'}},
            {'evidence': {'verification': {'reopened': True}}},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                row.clear(); row.update(copy.deepcopy(original)); row.update(variant)
                self.assert_blocked_without_mutation()
        row.clear(); row.update(original)
        self.job['cancelled'] = True
        self.assert_blocked_without_mutation()

    def test_ledger_history_cannot_hide_saved_or_account_risk_behind_latest_unknown(self):
        self.stopped_attempt()
        row = wb.LEDGER[self.item['expected']['operation_key']]['attempts'][0]
        original = copy.deepcopy(row)
        for status in ('risk_paused', 'platform_restricted', 'saved_unverified', 'platform_draft_verified'):
            with self.subTest(status=status):
                row.clear(); row.update(copy.deepcopy(original)); row['status'] = status
                if status == 'risk_paused': row['content_sha256'] = 'different-material'
                self.assert_blocked_without_mutation()

    def test_new_source_after_authorization_stops_before_subprocess(self):
        self.stopped_attempt()
        latest = self.attempt_checkpoint(1)
        self.write_at(latest)
        self.resume()
        self.assertEqual(self.item['resume_binding']['checkpoint_attempt'], 1)
        # An identical seal placed at the current output must not change the
        # authorized source: the source attempt is part of the private binding.
        self.write_at(self.attempt_checkpoint(2))
        with patch.object(wb.subprocess, 'Popen') as process:
            wb.worker(self.job, self.payload)
        process.assert_not_called()
        self.assertFalse(self.job['active'])
        self.assertEqual(self.item['status'], 'failed')

    def test_prior_save_marker_after_authorization_stops_before_subprocess(self):
        self.stopped_attempt()
        self.write_at(self.attempt_checkpoint(1))
        self.resume()
        # Even when a newer proof is bound, a marker at the original source
        # forbids a continuation. No subprocess/browser action may start.
        marker = self.checkpoint_path.with_suffix('.save-started.json')
        wb.atomic_json(marker, {'started': True})
        with patch.object(wb.subprocess, 'Popen') as process:
            wb.worker(self.job, self.payload)
        process.assert_not_called()
        self.assertFalse(self.job['active'])
        self.assertFalse(self.available())

    def test_parallel_explicit_second_requests_reserve_only_one_new_directory(self):
        self.stopped_attempt()
        ready, outcomes = threading.Event(), []
        def request():
            ready.wait()
            try:
                wb.resume_job(self.request()); outcomes.append('started')
            except ValueError:
                outcomes.append('refused')
        first = fixtures.REAL_THREAD(target=request)
        second = fixtures.REAL_THREAD(target=request)
        with patch.object(wb.threading, 'Thread') as worker:
            first.start(); second.start(); ready.set(); first.join(5); second.join(5)
        self.assertEqual(sorted(outcomes), ['refused', 'started'])
        worker.assert_called_once()
        self.assertEqual(self.item['resume_count'], 2)
        self.assertEqual(len(self.item['execution_attempts']), 2)
        self.assertTrue(self.attempt_directory(2).is_dir())
        self.assertFalse(self.attempt_directory(3).exists())

    def test_invalid_counter_or_audit_cannot_bypass_the_bound(self):
        for value in (True, False, -1, 4, '1', 1.0):
            with self.subTest(value=value):
                self.item['resume_count'] = value
                self.assert_blocked_without_mutation()
        self.item.pop('resume_count')
        self.stopped_attempt()
        self.item['execution_attempts'] = []
        self.assert_blocked_without_mutation()

    def test_ordinary_start_still_deduplicates_and_ignores_resume_parameters(self):
        self.stopped_attempt()
        original = copy.deepcopy(self.job)
        with patch.object(wb.threading, 'Thread') as worker:
            shown = wb.start_job({'action': 'draft', 'browser': 'external', 'platforms': ['douyin'],
                'payload': self.payload, 'resume': True, 'resume_count': 0, 'resume_platform': 'douyin'})
        worker.assert_not_called()
        row = shown['results'][0]
        self.assertTrue(row['reused'])
        self.assertNotIn('resume_count', row)
        public = wb.state()['job']['results'][0]
        self.assertTrue(public['resumable'])
        self.assertEqual(public['resume_previous_job_id'], self.job['id'])
        self.assertEqual(wb.HISTORY[-1], original)


if __name__ == '__main__': unittest.main(verbosity=2)
