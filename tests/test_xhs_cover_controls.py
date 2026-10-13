"""Current Mojito controls, local DOM only; no platform/account operations."""
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from patchright.async_api import async_playwright
from uploader.xhs_cover_controls import (open_xhs_cover_editor,
    import_xhs_cover_file, finish_xhs_cover_editor)


FIXTURE = '''<!doctype html><meta charset="utf-8"><style>
.cover-plugin-preview{width:220px;height:160px;position:relative}
.cover{width:220px;height:160px}.default{width:210px;height:150px}
.cover-edit-entry{display:none;position:absolute;top:40px;left:40px}
.cover-plugin-preview:hover .cover-edit-entry{display:block}
.d-modal{display:none;width:800px;height:480px;background:white}
.upload-btn{display:block;width:200px;height:40px}button{min-width:80px;height:30px}
</style><div class="cover-plugin-preview"><div class="cover"><div class="default default--ai-cover-layout"></div>
<div class="operator default column center"><div class="cover-edit-stack"><div class="cover-edit-entry"><span>编辑封面</span></div></div></div></div></div>
<button id="ai">AI修改封面</button><button id="pk">封面PK</button><button id="publish">发布</button>
<div class="d-modal main-cover-editor-modal cover-modal"><div class="d-modal-header"><h6>设置封面</h6></div>
<div class="cover-choose-container"><div class="upload-slot"><label class="upload-btn">上传封面图片<input class="upload-input" type="file" accept="image/*"></label></div></div>
<div class="category"><div class="items"><div class="item">AI修改</div><div class="item" id="crop">裁剪</div></div></div>
<div class="crop-panel" hidden><div class="ratio-list"><button class="ratio-option"><span>3:4</span></button><button class="ratio-option"><span>4:3</span></button><button class="ratio-option"><span>1:1</span></button></div></div>
<div class="editor-panel"><div class="bottom"><div class="bottom-content"><button class="mojito-button">完成</button></div></div></div></div>
<input id="caption" value="正文保留"><video id="video"></video><script>(()=>{
window.counts={entry:0,complete:0,other:0,ratio:0};
const modal=document.querySelector('.main-cover-editor-modal');
document.querySelector('.cover-edit-entry').onclick=()=>{counts.entry++;setTimeout(()=>modal.style.display='block',250)};
document.querySelector('.mojito-button').onclick=()=>{counts.complete++;modal.style.display='none'};
document.querySelector('#crop').onclick=()=>document.querySelector('.crop-panel').hidden=false;
document.querySelectorAll('.ratio-option').forEach(b=>b.onclick=()=>{counts.ratio++;document.querySelectorAll('.ratio-option').forEach(n=>n.classList.remove('active'));b.classList.add('active')});
for(const id of ['ai','pk','publish'])document.getElementById(id).onclick=()=>counts.other++;
})();</script>'''


class XHSCoverControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.pw = await async_playwright().start()
        shell = Path.home()/'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        options = {'executable_path': str(shell)} if shell.exists() else {'channel': 'chrome'}
        self.browser = await self.pw.chromium.launch(headless=True, args=['--disable-gpu'], **options)
        self.page = await self.browser.new_page()
        await self.page.set_content(FIXTURE)
        self.guard = AsyncMock()
        self.ready = AsyncMock(return_value=True)
        self.selection_ready = AsyncMock(return_value=True)
        self.setter = AsyncMock()
        self.page._sau_draft_evidence = SimpleNamespace(closed=False,
            xhs_observer=SimpleNamespace(ensure_cover_ready=self.ready,
                ensure_cover_selection_ready=self.selection_ready))

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_hover_actual_entry_delayed_popup_unique_native_input_and_crop(self):
        modal = await import_xhs_cover_file(self.page, '/fixture/native.png',
            ratio='3:4', guard=self.guard, file_setter=self.setter)
        self.assertTrue(await modal.is_visible())
        self.setter.assert_awaited_once()
        self.assertEqual(await self.setter.call_args.args[1].count(), 1)
        self.ready.assert_awaited_once_with(timeout=5)
        self.assertTrue(await finish_xhs_cover_editor(self.page, guard=self.guard))
        self.assertEqual(await self.page.evaluate('counts', isolated_context=False), {'entry':1,'complete':1,'other':0,'ratio':1})
        self.assertEqual(await self.page.locator('#caption').input_value(), '正文保留')
        self.assertEqual(await self.page.locator('#video').count(), 1)

    async def test_existing_one_physical_modal_reused_without_entry_click(self):
        await self.page.locator('.main-cover-editor-modal').evaluate("e=>e.style.display='block'")
        await open_xhs_cover_editor(self.page, guard=self.guard)
        self.assertEqual(await self.page.evaluate('counts.entry', isolated_context=False), 0)

    async def test_ambiguous_cards_modals_or_native_inputs_fail_before_import(self):
        for kind in ('cards','modals','inputs'):
            with self.subTest(kind=kind):
                await self.page.set_content(FIXTURE)
                if kind == 'cards':
                    await self.page.evaluate("document.body.append(document.querySelector('.cover-plugin-preview').cloneNode(true))")
                else:
                    await self.page.locator('.main-cover-editor-modal').evaluate("e=>e.style.display='block'")
                    selector = '.main-cover-editor-modal' if kind=='modals' else '.upload-input'
                    await self.page.locator(selector).evaluate("e=>e.parentNode.append(e.cloneNode(true))")
                self.setter.reset_mock()
                with self.assertRaises(ValueError):
                    await import_xhs_cover_file(self.page, '/fixture/native.png', guard=self.guard, file_setter=self.setter)
                self.setter.assert_not_awaited()
                self.assertEqual(await self.page.evaluate('counts.complete+counts.other', isolated_context=False), 0)

    async def test_missing_observer_or_unsupported_ratio_never_assign_files(self):
        self.ready.return_value = False
        with self.assertRaises(ValueError):
            await import_xhs_cover_file(self.page, '/fixture/native.png', guard=self.guard, file_setter=self.setter)
        self.setter.assert_not_awaited()
        self.ready.reset_mock()
        with self.assertRaises(ValueError):
            await import_xhs_cover_file(self.page, '/fixture/native.png', ratio='9:16', guard=self.guard, file_setter=self.setter)
        self.ready.assert_not_awaited()
        self.setter.assert_not_awaited()

    async def test_unbound_selected_source_stops_before_ratio_or_complete(self):
        self.selection_ready.return_value = False
        with self.assertRaises(ValueError):
            await import_xhs_cover_file(self.page, '/fixture/native.png', ratio='3:4',
                guard=self.guard, file_setter=self.setter)
        self.setter.assert_awaited_once()
        with self.assertRaises(ValueError):
            await finish_xhs_cover_editor(self.page, guard=self.guard)
        self.assertEqual(await self.page.evaluate('counts.ratio+counts.complete+counts.other', isolated_context=False), 0)

    async def test_duplicate_complete_buttons_and_disabled_completion_never_click(self):
        await self.page.locator('.main-cover-editor-modal').evaluate("e=>e.style.display='block'")
        button = self.page.locator('.mojito-button')
        await button.evaluate("e=>e.disabled=true")
        with self.assertRaises(ValueError):
            await finish_xhs_cover_editor(self.page, timeout=100, guard=self.guard)
        await button.evaluate("e=>{e.disabled=false;e.parentNode.append(e.cloneNode(true))}")
        with self.assertRaises(ValueError):
            await finish_xhs_cover_editor(self.page, guard=self.guard)
        self.assertEqual(await self.page.evaluate('counts.complete+counts.other', isolated_context=False), 0)
