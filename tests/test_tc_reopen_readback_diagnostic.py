import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uploader import draft_only


class TencentReopenReadbackDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_ui_gates_are_read_when_fields_fail_and_combined_gate_stays_false(self):
        with patch.object(draft_only, '_ui_fields', new=AsyncMock(return_value=False)) as fields, \
             patch.object(draft_only, '_ui_assets', new=AsyncMock(return_value=True)) as assets:
            field_ok, asset_ok, combined = await draft_only._reopened_ui_gates(
                object(), 'tencent', {'platform_fields': {}}, {'video_ids': ['private-video']})

        self.assertFalse(field_ok)
        self.assertTrue(asset_ok)
        self.assertFalse(combined)
        fields.assert_awaited_once()
        assets.assert_awaited_once()

    def test_diagnostic_emits_only_on_gate_state_changes_and_redacts_values(self):
        secret_values = (
            'private-draft-id', 'private-owner-id', 'private-video-id', 'private-cover-id',
            'private-slot-name', 'private-caption', 'private-short-title', 'private-account-identity',
            'https://private.example/resource?token=secret',
        )
        candidate = {
            'id': secret_values[0], 'owner': secret_values[1],
            'video_ids': [secret_values[2]], 'cover_ids': [secret_values[3]],
            'cover_slots': {secret_values[4]: [secret_values[3]]},
            'caption': secret_values[5], 'short_title': secret_values[6],
            'private_url': secret_values[8],
        }
        reopened = dict(candidate)
        expected = {'platform_fields': {'caption': secret_values[5], 'short_title': secret_values[6]}}
        session = SimpleNamespace(
            account={'verified': True, 'identity': secret_values[7]},
            reopen_at=10.0,
            reads=[{'id': secret_values[0], 'observed_at': 11.0, 'url': secret_values[8]}],
        )
        account_after = {'verified': True, 'identity': secret_values[7]}
        state = {}
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertTrue(draft_only._tc_reopen_gate_diagnostic(
                session, candidate, reopened, expected, True, True, account_after, state))
            self.assertFalse(draft_only._tc_reopen_gate_diagnostic(
                session, candidate, reopened, expected, True, True, account_after, state))
            self.assertTrue(draft_only._tc_reopen_gate_diagnostic(
                session, candidate, reopened, expected, False, True, account_after, state))

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        diagnostics = [json.loads(line.split('=', 1)[1]) for line in lines]
        self.assertTrue(diagnostics[0]['reopened_found'])
        self.assertTrue(diagnostics[0]['same_id'])
        self.assertTrue(diagnostics[0]['video_ids_equal'])
        self.assertTrue(diagnostics[0]['cover_ids_equal'])
        self.assertTrue(diagnostics[0]['cover_slots_equal'])
        self.assertTrue(diagnostics[0]['field_match'])
        self.assertTrue(diagnostics[0]['account_after_verified'])
        self.assertTrue(diagnostics[0]['account_identity_equal'])
        self.assertFalse(diagnostics[1]['ui_fields'])
        self.assertEqual(diagnostics[0]['counts']['same_id_reads_after_reopen'], 1)
        self.assertEqual(diagnostics[0]['counts']['candidate_video_ids'], 1)
        self.assertEqual(diagnostics[0]['counts']['reopened_cover_slots'], 1)
        for diagnostic in diagnostics:
            self.assertEqual(diagnostic['event'], 'tc_reopen_gates')
            gate_keys = {'candidate_found', 'reopened_found', 'ui_fields', 'ui_assets', 'same_id',
                         'video_ids_equal', 'cover_ids_equal', 'cover_slots_equal', 'field_match',
                         'account_after_verified', 'account_identity_equal'}
            self.assertEqual(set(diagnostic) - {'event', 'counts'}, gate_keys)
            self.assertTrue(all(type(diagnostic[key]) is bool for key in gate_keys))
            self.assertTrue(all(type(value) is int for value in diagnostic['counts'].values()))
        for secret in secret_values:
            self.assertNotIn(secret, output.getvalue())


if __name__ == '__main__':
    unittest.main()
