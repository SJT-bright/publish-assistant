"""Observe one video upload while serially preparing its creator-page fields."""
import asyncio


async def run_upload_pipeline(page, platform, wait_upload, prepare_content):
    """Start both factories once, join both, and cancel the sibling on failure.

    The upload factory must only observe the already selected file. The content
    factory owns all editor writes. Neither factory retries file selection or
    submits the work; the caller performs its final action after this join.
    """
    if not callable(wait_upload) or not callable(prepare_content):
        raise TypeError('Upload observation and content preparation must be factories.')
    tasks = [asyncio.create_task(wait_upload(), name=platform + '-upload-observation'),
             asyncio.create_task(prepare_content(), name=platform + '-content-preparation')]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task in done:
                task.result()
        if pending:
            await asyncio.gather(*pending)
        return tuple(task.result() for task in tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
