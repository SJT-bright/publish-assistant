"""Homepage draft reset is independent of source files, logins and receipts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import draft_workbench as workbench


class EditorClearTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(workbench, name, value) for name, value in {
            'PRIVATE': self.root / 'private', 'STORE_ROOT': None, 'CURRENT': None,
            'EDITOR': {}, 'LEDGER': {}, 'ACCOUNTS': {}, 'HISTORY': [], 'ASSETS': set(),
        }.items()]
        for item in self.patches:
            item.start()
        workbench.ensure_store()
        self.video = self.root / 'video.mp4'
        self.video.write_bytes(b'synthetic material')
        self.image = self.root / 'cover.png'
        self.image.write_bytes(b'synthetic image')
        workbench.save_editor({'payload': {'video': str(self.video), 'title': '原草稿',
            'cover': str(self.image), 'platform_overrides': {'tencent': {'description': '独立文案'}}},
            'platforms': ['tencent', 'kuaishou']})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_clear_persists_empty_editor_and_preserves_files_accounts_jobs_and_history(self):
        workbench.CURRENT = {'id': 'fixture-completed', 'active': False, 'results': []}
        workbench.LEDGER = {'receipt': {'status': 'platform_draft_verified'}}
        workbench.ACCOUNTS = {'embedded:tencent': {'identity': 'fixture-account'}}
        workbench.HISTORY = [{'id': 'previous-task', 'results': []}]
        workbench.ASSETS.update([str(self.video), str(self.image)])
        workbench.persist()
        old = json.loads((workbench.PRIVATE / 'workbench-state.json').read_text())
        result = workbench.clear_editor({})
        self.assertEqual(result['payload'], {})
        self.assertEqual(result['platforms'], ['tencent', 'kuaishou'])
        saved = json.loads((workbench.PRIVATE / 'workbench-state.json').read_text())
        for key in ('job', 'ledger', 'accounts', 'history'):
            self.assertEqual(saved[key], old[key])
        self.assertEqual(self.video.read_bytes(), b'synthetic material')
        self.assertEqual(self.image.read_bytes(), b'synthetic image')
        self.assertIn(str(self.image), workbench.ASSETS)
        workbench.STORE_ROOT = None
        workbench.ensure_store()
        self.assertEqual(workbench.EDITOR['payload'], {})
        self.assertEqual(workbench.EDITOR['platforms'], ['tencent', 'kuaishou'])

    def test_active_task_rejects_reset_without_changing_editor_or_store(self):
        before = (workbench.PRIVATE / 'workbench-state.json').read_bytes()
        original = workbench.EDITOR.copy()
        workbench.CURRENT = {'active': True}
        with self.assertRaisesRegex(ValueError, '当前任务'):
            workbench.clear_editor({})
        self.assertEqual(workbench.EDITOR, original)
        self.assertEqual((workbench.PRIVATE / 'workbench-state.json').read_bytes(), before)

    def test_failed_persistence_restores_current_editor(self):
        before = workbench.EDITOR.copy()
        with patch.object(workbench, 'persist', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                workbench.clear_editor({})
        self.assertEqual(workbench.EDITOR, before)

    def test_platform_selection_is_validated_and_deduplicated(self):
        before = workbench.EDITOR.copy()
        for invalid in (None, 'tencent', ['unknown']):
            with self.assertRaises(ValueError):
                workbench.clear_editor({'platforms': invalid})
            self.assertEqual(workbench.EDITOR, before)
        result = workbench.clear_editor({'platforms': ['tencent', 'tencent']})
        self.assertEqual(result['platforms'], ['tencent'])
