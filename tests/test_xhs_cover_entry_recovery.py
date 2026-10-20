"""Current card hover after real DOM replacement/reflow, local browser only."""
import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from uploader import xhs_cover_controls as controls
from tests.test_xhs_cover_controls import FIXTURE


RECOVERY = r'''<style>
.cover-plugin-preview:not([data-stage=ready]) .cover-edit-entry{display:none!important}
</style><script>(()=>{
counts.hovers=0;counts.files=0;
document.querySelector('input.upload-input').onchange=()=>counts.files++;
const modal=document.querySelector('.main-cover-editor-modal');
const mode=RECOVERY_MODE;
function attach(root, generation){
 root.querySelector('.cover-edit-entry').onclick=()=>{
  counts.entry++;setTimeout(()=>modal.style.display='block',160);
 };
 root.querySelector('.default:not(.operator)').addEventListener('pointerenter',()=>{
  counts.hovers++;
  if(generation===0){
   root.dataset.stage='moving';
   setTimeout(()=>{
    const next=mode==='replacement'?root.cloneNode(true):root;
    next.style.marginTop='280px';next.dataset.stage='pending';
    if(next!==root)root.replaceWith(next);
    attach(next,1);
   },0);
  }else root.dataset.stage='ready';
 },{once:generation===0});
}
const root=document.querySelector('.cover-plugin-preview');root.dataset.stage='initial';attach(root,0);
})();</script>'''


class XHSCoverEntryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.pw = await async_playwright().start()
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        options = {'executable_path': str(shell)} if shell.exists() else {'channel': 'chrome'}
        self.browser = await self.pw.chromium.launch(headless=True, args=['--disable-gpu'], **options)
        self.page = await self.browser.new_page()
        self.temp = tempfile.TemporaryDirectory(prefix='sau-xhs-cover-entry-')
        self.cover = Path(self.temp.name) / 'fixture.png'
        self.cover.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='))
        self.page._sau_draft_evidence = SimpleNamespace(closed=False,
            xhs_observer=SimpleNamespace(ensure_cover_ready=AsyncMock(return_value=True),
                ensure_cover_selection_ready=AsyncMock(return_value=True)))
        self.guard = AsyncMock()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.temp.cleanup()

    async def test_replaced_card_reacquired_and_hovered_native_file_selected_only_once(self):
        await self.page.set_content(FIXTURE + RECOVERY.replace('RECOVERY_MODE', "'replacement'"))
        modal = await controls.import_xhs_cover_file(self.page, str(self.cover), timeout=3000, guard=self.guard)
        self.assertTrue(await modal.is_visible())
        counts = await self.page.evaluate('counts', isolated_context=False)
        self.assertGreaterEqual(counts['hovers'], 2)
        self.assertEqual(counts['entry'], 1)
        self.assertEqual(counts['files'], 1)
        self.assertEqual(counts['other'], 0)
        self.assertEqual(await self.page.locator(controls.IMAGE_INPUT).evaluate('e=>e.files[0].name'), self.cover.name)

    async def test_same_card_layout_moves_out_from_under_pointer_and_rehover_recovers(self):
        await self.page.set_content(FIXTURE + RECOVERY.replace('RECOVERY_MODE', "'reflow'"))
        await controls.import_xhs_cover_file(self.page, str(self.cover), timeout=3000, guard=self.guard)
        counts = await self.page.evaluate('counts', isolated_context=False)
        self.assertGreaterEqual(counts['hovers'], 2)
        self.assertEqual(counts['entry'], 1)
        self.assertEqual(counts['files'], 1)
        self.assertEqual(counts['other'], 0)

    async def test_entry_hard_deadline_does_not_inherit_fifteen_minute_video_timeout(self):
        await self.page.set_content(FIXTURE)
        await self.page.locator('.cover-edit-entry').evaluate('e=>e.remove()')
        started = asyncio.get_running_loop().time()
        with patch.object(controls, 'MAX_COVER_ENTRY_WAIT_MS', 120):
            with self.assertRaisesRegex(ValueError, '入口'):
                await controls.open_xhs_cover_editor(self.page, timeout=900000, guard=self.guard)
        self.assertLess(asyncio.get_running_loop().time() - started, .7)
        self.assertEqual(await self.page.evaluate('counts.entry+counts.other', isolated_context=False), 0)

    async def test_slow_guard_is_included_in_hard_entry_deadline(self):
        await self.page.set_content(FIXTURE)
        async def blocked_guard(page):
            await asyncio.Event().wait()
        with patch.object(controls, 'MAX_COVER_ENTRY_WAIT_MS', 80):
            with self.assertRaisesRegex(ValueError, '超时'):
                await asyncio.wait_for(controls.open_xhs_cover_editor(self.page, timeout=900000, guard=blocked_guard), .5)

    async def test_already_open_physical_modal_does_not_reclick_entry_or_import_twice(self):
        await self.page.set_content(FIXTURE)
        await self.page.locator(controls.MODAL).evaluate("e=>e.style.display='block'")
        modal = await controls.open_xhs_cover_editor(self.page, guard=self.guard)
        self.assertTrue(await modal.is_visible())
        self.assertEqual(await self.page.evaluate('counts.entry', isolated_context=False), 0)

    async def test_exact_named_information_intro_closed_without_enabling_pk_or_other_confirm(self):
        intro = f'''<div aria-label="{controls.PK_INTRO_TITLE}" id="intro"
          style="position:absolute;z-index:1000;left:0;top:0;width:400px;height:220px;background:white">
          <p>{controls.PK_INTRO_TEXT}</p><button onclick="counts.guides++;this.parentNode.style.display='none'">我知道了</button></div>
          <input type="checkbox" aria-label="封面PK" onchange="counts.other++">
          <button onclick="counts.other++">我知道了</button>
          <script>counts.guides=0;counts.files=0;document.querySelector('input.upload-input').onchange=()=>counts.files++;</script>'''
        for timing in ('initial', 'hover'):
            with self.subTest(timing=timing):
                await self.page.set_content(FIXTURE + intro)
                if timing == 'hover':
                    await self.page.locator('#intro').evaluate("e=>e.style.display='none'")
                    await self.page.evaluate("""()=>document.querySelector('.default:not(.operator)')
                        .addEventListener('pointerenter',()=>document.getElementById('intro').style.display='block',{once:true})""",
                        isolated_context=False)
                await controls.import_xhs_cover_file(self.page, str(self.cover), timeout=5000, guard=self.guard)
                counts = await self.page.evaluate('counts', isolated_context=False)
                self.assertEqual(counts['guides'], 1)
                self.assertEqual(counts['entry'], 1)
                self.assertEqual(counts['files'], 1)
                self.assertEqual(counts['other'], 0)
                self.assertFalse(await self.page.get_by_label('封面PK', exact=True).is_checked())

    async def test_unknown_text_input_or_unlabelled_bubble_never_clicks_generic_got_it(self):
        for mode in ('unknown_text', 'pk_input', 'unlabelled'):
            with self.subTest(mode=mode):
                label = f'aria-label="{controls.PK_INTRO_TITLE}"' if mode != 'unlabelled' else ''
                body = controls.PK_INTRO_TEXT if mode != 'unknown_text' else '其他功能提示'
                extra = '<input type="checkbox">' if mode == 'pk_input' else ''
                await self.page.set_content(FIXTURE + f'''<div {label}><p>{body}</p>{extra}
                  <button onclick="counts.other++">我知道了</button></div>''')
                deadline = asyncio.get_running_loop().time() + 1
                self.assertFalse(await controls._dismiss_pk_cover_intro(self.page, deadline, self.guard))
                self.assertEqual(await self.page.evaluate('counts.other', isolated_context=False), 0)

    async def test_duplicate_named_intro_stops_before_any_confirmation_or_file_import(self):
        await self.page.set_content(FIXTURE + f'''<div aria-label="{controls.PK_INTRO_TITLE}" id="intro">
          <p>{controls.PK_INTRO_TEXT}</p><button onclick="counts.other++">我知道了</button></div>''')
        await self.page.locator('#intro').evaluate('e=>e.after(e.cloneNode(true))')
        with self.assertRaisesRegex(ValueError, '多个PK'):
            await controls.open_xhs_cover_editor(self.page, timeout=1000, guard=self.guard)
        self.assertEqual(await self.page.evaluate('counts.other+counts.entry', isolated_context=False), 0)
