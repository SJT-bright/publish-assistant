"""Isolated DOM readback with the two DIV lines observed on the real editor."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader.douyin_uploader.main import DouYinVideo
from uploader.draft_only import _ui_fields
from uploader.draft_evidence import field_match


class DouyinTextReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, channel='chromium')
        self.page = await self.browser.new_page()
        self.caption = '第一行👩\u200d💻\n第二行 #科普 #验收'
        self.expected = {'platform_fields': {'title': '隔离验收', 'caption': self.caption}}
        self.actor = SimpleNamespace(draft_only=True, draft_expected=self.expected)
        await self.page.set_content('''<input placeholder="填写作品标题">
<div class="zone-container" contenteditable="true"></div>
''')

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def fill(self):
        with patch('uploader.draft_only.assert_draft_safe', AsyncMock()):
            await DouYinVideo.fill_title_and_description(self.actor, self.page, '隔离验收', '', [])
        # Add the observed paragraph markers after input, without replacing the
        # live caret on every input event (which no longer models this editor).
        await self.page.evaluate('''() => {
          const editor=document.querySelector('.zone-container'),lines=editor.innerText.split('\\n');
          editor.replaceChildren(...lines.map(text=>{const line=document.createElement('div');
          line.dataset.node='paragraph';line.textContent=text+'\\u200b';return line;}));
        }''')

    async def test_keyboard_paragraphs_and_independent_ui_read_accept_editor_markers(self):
        await self.fill()
        self.assertEqual(await self.page.locator('.zone-container>div').count(), 2)
        self.assertTrue(await _ui_fields(self.page, 'douyin', self.expected))
        self.assertTrue(field_match('douyin', {'title': '隔离验收', 'caption': self.caption + '\u200b'}, self.expected))

    async def test_ui_read_rejects_changed_line_and_topic(self):
        await self.fill()
        for caption in ('第一行👩\u200d💻 第二行 #科普 #验收', '第一行👩\u200d💻\n第二行 #科普 #其他'):
            await self.page.locator('.zone-container').fill(caption)
            self.assertFalse(await _ui_fields(self.page, 'douyin', self.expected))

    async def test_ui_read_rejects_wrong_title(self):
        await self.fill()
        await self.page.locator('input').fill('另一条作品')
        self.assertFalse(await _ui_fields(self.page, 'douyin', self.expected))


if __name__ == '__main__':
    unittest.main()
