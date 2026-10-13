import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from uploader import draft_progress as emitter
from workbench_progress import parse_progress, apply_progress, finish_progress, public_progress, PREFIX


class FineProgressTests(unittest.TestCase):
    def test_real_operations_survive_worker_to_public_snapshot_and_failure(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {'SAU_PROGRESS_JOB_ID': 'fixture', 'SAU_PROGRESS_PLATFORM': 'douyin'}), redirect_stdout(stream):
            emitter.configure('douyin')
            with emitter.step('video', operations=['locate', 'select']):
                with emitter.part('video', 'locate'): pass
                with emitter.part('video', 'select'): pass
            with emitter.step('upload', operations=['transfer', 'processing']) as scope:
                emitter.operation('upload', 'transfer', percent=12.5)
                emitter.operation('upload', 'transfer', percent=48)
                scope.pause()
        item = {'status': 'running'}
        for at, line in enumerate(stream.getvalue().splitlines(), 1):
            event = parse_progress(line, 'fixture', 'douyin')
            self.assertIsNotNone(event)
            self.assertTrue(apply_progress(item, event, at))
        row = public_progress(item['progress'])['operations']['upload']
        self.assertEqual(row['percent'], 48)
        before = json.dumps(item, sort_keys=True)
        for fields in ({'operation_percent': 30}, {'plan': ['processing', 'transfer']}):
            base = {'v': 1, 'job_id': 'fixture', 'platform': 'douyin', 'stage': 'upload', 'state': 'running'}
            if 'operation_percent' in fields: base.update(operation='transfer', operation_state='running')
            event = parse_progress(PREFIX + json.dumps({**base, **fields}), 'fixture', 'douyin')
            self.assertFalse(apply_progress(item, event, 99))
            self.assertEqual(json.dumps(item, sort_keys=True), before)
        finish_progress(item, 'result_unknown', 100)
        self.assertEqual(item['progress']['operations']['upload']['percent'], 48)
        self.assertEqual(item['progress']['state'], 'unknown')
        self.assertNotIn('verify', item['progress']['completed'])
        emitter._configuration.set(None)

    def test_bound_closed_detail_protocol_rejects_bad_percent_or_user_fields(self):
        base = dict(v=1, job_id='fixture', platform='douyin', stage='content', state='running', operation='title', operation_state='running')
        for extra in ({'operation_percent': True}, {'operation_percent': -1}, {'operation_percent': 101},
                      {'operation_percent': float('nan')}, {'operation_state': []}, {'operation': '/private/video.mp4'},
                      {'plan': ['title', 'title']}, {'path': '/private/video.mp4'}, {'operation_state': 'verified'}):
            self.assertIsNone(parse_progress(PREFIX + json.dumps({**base, **extra}), 'fixture', 'douyin'))
        for stage, name, state, percent in (([], 'title', 'running', None), ('content', [], 'running', None),
                                           ('content', 'title', [], None), ('content', 'title', 'running', True)):
            self.assertFalse(emitter.operation(stage, name, state, percent))

    def test_corrupt_operations_never_become_content_or_progress(self):
        clean = public_progress(dict(stage='content', state='running', started_at=1, updated_at=2,
                                     operations={'content': {'plan': ['token-secret']}, 'upload': {'plan': ['transfer'], 'current': ['bad']}}))
        self.assertNotIn('content', clean['operations'])
        self.assertIsNone(clean['operations']['upload']['current'])


if __name__ == '__main__': unittest.main()
