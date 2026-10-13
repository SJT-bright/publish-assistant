"""Reopened TC editor DOM fixtures, fulfilled locally without an account."""
import unittest
from patchright.async_api import async_playwright
from uploader.draft_only import _ui_fields


class ReopenedTencentFields(unittest.IsolatedAsyncioTestCase):
    async def test_empty_contenteditable_attribute_and_topic_spans_preserve_text(self):
        expected = {'platform_fields': {'caption': '标题\n正文\n#科普 #验收', 'short_title': '草稿验收短标题'}}
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<div id="host"></div>')
                await page.evaluate("""() => {
                    const root=document.querySelector('#host').attachShadow({mode:'open'});
                    root.innerHTML='<div class="input-editor" style="white-space:pre-wrap" contenteditable></div>'+
                        '<input placeholder="填写短标题有机会获得更多流量">';
                    const e=root.querySelector('.input-editor');
                    e.append(document.createTextNode('标题\\n正文\\n'));
                    for(const tag of ['科普','验收']){
                        const span=document.createElement('span');span.className='hl topic';
                        span.textContent='#'+tag;e.append(span,document.createTextNode(' '));
                    }
                    root.querySelector('input').value='草稿验收短标题';
                }""")
                self.assertTrue(await _ui_fields(page, 'tencent', expected))
                await page.locator('input').fill('其他短标题')
                self.assertFalse(await _ui_fields(page, 'tencent', expected))
                await page.locator('input').fill('草稿验收短标题')
                await page.locator('.input-editor').fill('标题\n丢失正文\n#科普 #验收')
                self.assertFalse(await _ui_fields(page, 'tencent', expected))
                await page.locator('.input-editor').fill(expected['platform_fields']['caption'])
                await page.locator('.input-editor').evaluate("e=>e.style.display='none'")
                self.assertFalse(await _ui_fields(page, 'tencent', expected))
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
