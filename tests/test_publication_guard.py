"""Isolated browser contract; never loads or publishes a real platform."""
import asyncio
import unittest
from patchright.async_api import async_playwright
from uploader.draft_only import install_draft_guard, uninstall_draft_guard
from uploader.draft_evidence import evaluate_main


class PublicationGuardTests(unittest.TestCase):
    def test_manual_mode_releases_only_own_route_and_new_task_restores_guard(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                context = await browser.new_context()
                submissions = []
                async def fixture(route):
                    if route.request.method == 'POST':
                        submissions.append(route.request.url)
                        await route.fulfill(content_type='application/json', body='{"code":0}')
                    else:
                        await route.fulfill(content_type='text/html; charset=utf-8', body='''<!doctype html><meta charset="utf-8">
                          <button id="publish">发表</button><button id="draft">保存草稿</button>
                          <script>window.clicks=0;document.querySelector('#publish').onclick=()=>{
                            window.clicks++;fetch('/post/post_create',{method:'POST'}).catch(()=>{});
                          };document.querySelector('#draft').onclick=()=>window.drafts=(window.drafts||0)+1;</script>''')
                await context.route('**/*', fixture)
                try:
                    page = await context.new_page()
                    await page.goto('https://fixture.invalid/editor')
                    await install_draft_guard(context, page)
                    await page.locator('#publish').click()
                    self.assertEqual(await evaluate_main(page, 'window.clicks'), 0)
                    await page.locator('#draft').click()
                    self.assertEqual(await evaluate_main(page, 'window.drafts'), 1)
                    # A DOM toggle alone cannot remove draft-time request guards.
                    await evaluate_main(page, 'window.__sauDraftGuardControl.setEnabled(false)')
                    async with page.expect_event('requestfailed'):
                        await page.locator('#publish').click()
                    self.assertEqual(submissions, [])
                    await uninstall_draft_guard(context, page)
                    await uninstall_draft_guard(context, page)  # Idempotent release.
                    async with page.expect_response(lambda response: response.request.method == 'POST'):
                        await page.locator('#publish').click()
                    self.assertEqual(len(submissions), 1)
                    self.assertTrue(submissions[0].endswith('/post/post_create'))
                    # App remains in control after navigation: init script is safe by default.
                    await page.goto('https://fixture.invalid/editor')
                    await page.locator('#publish').click()
                    self.assertEqual(await evaluate_main(page, 'window.clicks'), 0)
                    await evaluate_main(page, 'window.__sauDraftGuardControl.setEnabled(false)')
                    await install_draft_guard(context, page)
                    await page.locator('#publish').click()
                    self.assertEqual(await evaluate_main(page, 'window.clicks'), 0)
                    self.assertEqual(len(submissions), 1)
                finally:
                    await browser.close()
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
