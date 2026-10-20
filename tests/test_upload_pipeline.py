import asyncio
import unittest

from uploader.upload_pipeline import run_upload_pipeline


class UploadPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepares_content_during_upload_and_joins_before_final_action(self):
        uploading, edited, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sequence = []
        async def observe():
            sequence.append('upload-start');uploading.set()
            await release.wait();sequence.append('upload-complete')
        async def edit():
            await uploading.wait();sequence.extend(['title', 'description', 'topics']);edited.set()
        task = asyncio.create_task(run_upload_pipeline(None, 'douyin', observe, edit))
        await asyncio.wait_for(edited.wait(), 1)
        self.assertFalse(task.done())
        self.assertEqual(sequence, ['upload-start', 'title', 'description', 'topics'])
        release.set();await task;sequence.append('final-action')
        self.assertLess(sequence.index('topics'), sequence.index('upload-complete'))
        self.assertLess(sequence.index('upload-complete'), sequence.index('final-action'))

    async def test_upload_failure_cancels_editor_without_starting_a_second_upload(self):
        editing, cancelled = asyncio.Event(), asyncio.Event();calls = []
        async def observe():
            calls.append('observe');await editing.wait();raise RuntimeError('upload-failed')
        async def edit():
            editing.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'upload-failed'):
            await run_upload_pipeline(None, 'bilibili', observe, edit)
        self.assertTrue(cancelled.is_set());self.assertEqual(calls, ['observe'])

    async def test_editor_failure_cancels_observation_and_caller_cancellation_leaves_no_orphan(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def observe():
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        async def edit():
            await started.wait();raise ValueError('editor-failed')
        with self.assertRaisesRegex(ValueError, 'editor-failed'):
            await run_upload_pipeline(None, 'tencent', observe, edit)
        self.assertTrue(cancelled.is_set())
        cancelled.clear();started.clear()
        async def waiting_edit():await asyncio.Event().wait()
        task=asyncio.create_task(run_upload_pipeline(None, 'tencent', observe, waiting_edit))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(cancelled.is_set())
