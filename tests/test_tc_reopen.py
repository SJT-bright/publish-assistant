import os
"""Isolated ordinary-UI fixtures; no platform account or external requests."""
import unittest
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright

from uploader.draft_evidence import parse_record
from uploader.tc_reopen import _lookup, reopen_tc_draft


FIXTURE = r'''<!doctype html><meta charset="utf-8"><style>
.post-draft-item{padding:10px;border:1px solid #ccc}.opr{display:flex;gap:20px}
.opr-item{width:40px;height:35px;background:#eee;cursor:pointer}svg{width:20px;height:20px}
.input-editor{width:300px;min-height:40px}.common-dialog{padding:20px;border:1px solid}
#content-group{position:relative;width:70px;height:56px}#content{display:block;width:56px;height:56px}
#popup{position:absolute;left:70px;top:0;background:#eee;padding:8px;width:100px}
#owner{position:absolute;left:-9999px;width:2px;height:2px}
</style><div class="finder-ui-desktop-menu__item finder-ui-desktop-menu__sub__wrp" id="content-group">
<a class="finder-ui-desktop-menu__link finder-ui-desktop-menu__sub__link" id="content" href="javascript:;">
<svg><use href="#icon-content-manager-outlined"></use><use href="#icon-content-manager-filled"></use></svg><span id="content-label">内容管理</span></a>
<div class="finder-ui-desktop-menu__popup_menu finder-ui-desktop-menu__popup_sub_menu" id="popup" style="display:none">
<a class="finder-ui-desktop-menu__link finder-ui-desktop-menu__only-icon" id="draft" href="javascript:;">草稿箱</a></div></div>
<div id="form"><div class="input-editor" contenteditable="">fixture-caption</div>
<input placeholder="填写短标题有机会获得更多流量" value="fixture-short-title"></div>
<div id="list-host"></div><iframe id="owner" src="/micro/content/post/draftListManager"></iframe>
<script>
window.calls={menu:0,contentClick:0,draft:0,edit:0,trash:0,save:0,discard:0,business:0,private:0};
window.cfg={rows:['saved-fixture-id'],foreign:false,duplicateEdit:false,icon:true,leave:false,editorId:'saved-fixture-id',editorDelay:0,editorComment:false,editorCycle:false,hoverDelay:150,hoverChangesCaption:false};
window.reset=async options=>{
 Object.assign(cfg,options);history.replaceState({},'', '/platform/post/create');
 document.querySelector('#popup').style.display='none';
 document.querySelector('#form').style.display='';
 document.querySelector('.input-editor').textContent=options.caption??'fixture-caption';
 document.querySelector('#form').__vue__=null;
 document.querySelector('.common-dialog')?.remove();
 document.querySelectorAll('.duplicate-menu,.unrelated-draft').forEach(el=>el.remove());
 document.querySelector('#content-label').style.display=options.narrow?'none':'';
 const old=document.querySelector('#list-host'),host=document.createElement('div');host.id='list-host';old.replaceWith(host);
 window.calls={menu:0,contentClick:0,draft:0,edit:0,trash:0,save:0,discard:0,business:0,private:0};
};
window.mountEditor=()=>{
 document.querySelector('#list-host').shadowRoot?.replaceChildren();
 history.pushState({},'', '/platform/post/create');
 const form=document.querySelector('#form');form.style.display='';
 form.__vue__={$el:form,$refs:{postCreate:{$el:form}},draftStore:{objectId:cfg.editorId}};
 if(cfg.editorCycle)form.__vue__.$parent=form.__vue__;
};
window.mountPendingEditor=()=>{
 document.querySelector('#list-host').shadowRoot?.replaceChildren();history.pushState({},'', '/platform/post/create');
 const form=document.querySelector('#form');form.style.display='';
 form.__vue__={$el:form,$refs:{postCreate:{$el:document.createComment('async editor')}},draftStore:{objectId:cfg.editorId}};
 setTimeout(mountEditor,cfg.editorDelay);
};
window.mountList=()=>{
 history.pushState({},'', '/platform/post/draftListManager');
 document.querySelector('#form').style.display='none';
 const host=document.querySelector('#list-host'),shadow=host.shadowRoot||host.attachShadow({mode:'open'});shadow.replaceChildren();
 const foreign=document.querySelector('#owner').contentDocument;
 const doc=cfg.foreign?foreign:document;
 for(const id of cfg.rows){
  const row=doc.createElement('div');row.className='post-draft-item';
  row.innerHTML='<div class="center-info"><div class="post-title">same fixture title</div></div><div class="opr"></div>';
  const vm={$el:row,post:{objectId:id},$options:{name:'C'}};row.__vue__=vm;
  for(const key of ['desc','finderUsername'])Object.defineProperty(vm.post,key,{get(){calls.private++;throw Error('private-getter');}});
  vm.enterEditPost=()=>{calls.business++;throw Error('business-method');};
  const opr=row.querySelector('.opr');
  const edit=doc.createElement('div');edit.className='opr-item';
  edit.innerHTML=cfg.icon?'<svg class="svg-icon icon-edit"><use xlink:href="#icon-edit"></use></svg>':'<span>编辑</span>';
  edit.addEventListener('click',()=>{calls.edit++;cfg.editorComment?mountPendingEditor():cfg.editorDelay?setTimeout(mountEditor,cfg.editorDelay):mountEditor();});opr.append(edit);
  if(cfg.duplicateEdit)opr.append(edit.cloneNode(true));
  const trash=doc.createElement('div');trash.className='opr-item';trash.innerHTML='<span class="weui-icon-outlined-delete"></span>';
  trash.addEventListener('click',()=>calls.trash++);opr.append(trash);shadow.append(row);
  if(cfg.foreign)for(const el of [row,...row.querySelectorAll('*')])Object.defineProperty(el,'ownerDocument',{configurable:true,get:()=>foreign});
 }
 if(cfg.foreign){
  // Wujie projects the same physical nodes through the owner document.
  const original=foreign.querySelectorAll.bind(foreign);
  foreign.querySelectorAll=selector=>selector==='*'?shadow.querySelectorAll('*'):original(selector);
 }
};
window.showLeave=()=>{
 const d=document.createElement('div');d.className='common-dialog';
 d.innerHTML='<h3>将此次编辑保留?</h3><div class="footer"><button class="discard">不保存</button><button class="save">保存</button></div>';
 d.querySelector('.discard').onclick=()=>{calls.discard++;d.remove();mountList();};
 d.querySelector('.save').onclick=()=>calls.save++;document.body.append(d);
};
document.querySelector('#content-group').onmouseenter=()=>{
 calls.menu++;
 if(cfg.hoverChangesCaption)document.querySelector('.input-editor').textContent='changed-during-hover';
 setTimeout(()=>document.querySelector('#popup').style.display='',cfg.hoverDelay);
};
document.querySelector('#content').onclick=()=>{calls.contentClick++;};
document.querySelector('#draft').onclick=()=>{calls.draft++;cfg.leave?showLeave():mountList();};
</script>'''


class TcReopenSourceTests(unittest.TestCase):
    def test_official_dom_id_binding_and_normal_edit_leave_contract(self):
        raw = Path(os.environ.get('SAU_TC_SOURCE_CACHE', str(Path(__file__).resolve().parents[1] / 'research/raw/2026-10-03/limits-login')))
        source = (raw / 'tc-content-755.151a1d93.js').read_text()
        self.assertIn('staticClass:"post-draft-item"', source)
        self.assertIn('this.draftStore.setDraftObjectId(t.objectId)', source)
        self.assertIn('staticClass:"icon-edit svg-icon"', source)
        self.assertIn('t.enterEditPost(t.post)', source)
        leave = (raw / 'tc-content-772.4b4c8d62.js').read_text()
        self.assertIn('staticClass:"common-dialog"', leave)
        self.assertIn('on:{click:t.leave}},[t._v("不保存")]', leave)


class TcReopenBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chrome')
        self.context = await self.browser.new_context()
        async def serve(route):
            body = '<div id="owner-root"></div>' if '/micro/content/' in route.request.url else FIXTURE
            await route.fulfill(content_type='text/html', body=body)
        await self.context.route('**/*', serve)
        self.page = await self.context.new_page()
        await self.page.goto('https://channels.weixin.qq.com/platform/post/create')
        await self.page.frames[1].locator('#owner-root').wait_for(state='attached')
        desc = {'description': 'fixture-caption', 'shortTitle': [{'shortTitle': 'fixture-short-title'}],
                'media': [{'url': 'fixture-video', 'coverUrl': 'fixture-portrait', 'shareCoverUrl': 'fixture-landscape'}]}
        self.candidate = parse_record('tencent', {'objectId': 'saved-fixture-id', 'desc': desc}, owner='fixture-owner')
        self.candidate['fresh'] = True
        self.session = SimpleNamespace(platform='tencent', closed=False, saves=[self.candidate], account={'uid':'fixture-owner'},
            expected={'platform_fields':{'caption':'fixture-caption','short_title':'fixture-short-title'}}, normal_ui_binding=None)
        self.page._sau_draft_evidence = self.session

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def setup(self, **options):
        await self.page.evaluate('reset', options, isolated_context=False)
        self.session.normal_ui_binding = None

    async def counts(self):
        result = await self.page.evaluate('window.calls', isolated_context=False)
        self.assertEqual(result['trash'], 0)
        self.assertEqual(result['save'], 0)
        self.assertEqual(result['business'], 0)
        self.assertEqual(result['private'], 0)
        self.assertEqual(result['contentClick'], 0)
        return result

    async def test_normal_menu_draft_edit_click_creates_binding_only_after_editor_mounts(self):
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual(self.session.normal_ui_binding, {'id':'saved-fixture-id','owner':'fixture-owner','control':'post_draft_item_edit'})
        result = await self.counts()
        self.assertEqual((result['menu'], result['draft'], result['edit']), (1, 1, 1))
        self.assertEqual(self.page.url, 'https://channels.weixin.qq.com/platform/post/create')

    async def test_foreign_owner_shadow_projection_is_one_physical_row_and_edit(self):
        await self.setup(foreign=True)
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        result = await self.counts()
        self.assertEqual(result['edit'], 1)
        self.assertIsNotNone(self.session.normal_ui_binding)

    async def test_folded_sidebar_hidden_label_and_delayed_normal_hover_menu(self):
        await self.setup(narrow=True, hoverDelay=350)
        self.assertEqual(await self.page.locator('#content').inner_text(), '')
        self.assertFalse(await self.page.locator('#draft').is_visible())
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        result = await self.counts()
        self.assertEqual((result['menu'], result['draft'], result['edit']), (1, 1, 1))

    async def test_duplicate_content_or_visible_draft_controls_fail_closed(self):
        for selector in ('#content', '#draft'):
            await self.setup()
            await self.page.evaluate('''selector=>{
                const el=document.querySelector(selector),clone=el.cloneNode(true);clone.removeAttribute('id');
                clone.classList.add('duplicate-menu');el.parentElement.append(clone);
                if(selector==='#draft')document.querySelector('#popup').style.display='';
            }''', selector, isolated_context=False)
            self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
            self.assertEqual((await self.counts())['draft'], 0)
            self.assertIsNone(self.session.normal_ui_binding)

    async def test_unrelated_visible_draft_label_is_not_the_content_submenu(self):
        await self.setup()
        await self.page.evaluate('''()=>{
            const a=document.createElement('a');a.className='finder-ui-desktop-menu__link unrelated-draft';
            a.textContent='草稿箱';a.onclick=()=>calls.trash++;document.body.append(a);
        }''', isolated_context=False)
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['draft'], 1)

    async def test_editor_mount_can_complete_after_the_previous_five_second_budget(self):
        await self.setup(editorDelay=6200)
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['edit'], 1)
        self.assertIsNotNone(self.session.normal_ui_binding)

    async def test_comment_root_is_pending_until_same_id_editor_normally_mounts(self):
        await self.setup(editorComment=True, editorDelay=450)
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['edit'], 1)
        self.assertEqual(self.session.normal_ui_binding,
                         {'id':'saved-fixture-id','owner':'fixture-owner','control':'post_draft_item_edit'})

    async def test_destroyed_navigation_context_retries_only_editor_read_without_reclick(self):
        await self.setup(editorDelay=350)
        attempts = 0
        async def evaluate_handle(expression, values, **kwargs):
            nonlocal attempts
            if values['mode'] == 'editor':
                attempts += 1
                if attempts == 1:
                    raise RuntimeError('Execution context was destroyed, private-fixture-detail')
            return await self.page.main_frame.evaluate_handle(expression, values, **kwargs)
        proxy = SimpleNamespace(url=self.page.url, _sau_draft_evidence=self.session,
                                main_frame=SimpleNamespace(url=self.page.main_frame.url, evaluate_handle=evaluate_handle))
        captured = io.StringIO()
        with redirect_stdout(captured):
            self.assertTrue(await reopen_tc_draft(proxy, self.candidate))
        self.assertGreater(attempts, 1)
        self.assertEqual((await self.counts())['edit'], 1)
        self.assertIn('navigation_context_unavailable', captured.getvalue())
        self.assertNotIn('private-fixture-detail', captured.getvalue())
        self.assertIsNotNone(self.session.normal_ui_binding)

    async def test_vue_parent_cycle_remains_invalid_and_diagnostic_omits_private_values(self):
        await self.setup(editorCycle=True)
        captured = io.StringIO()
        with redirect_stdout(captured):
            self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['edit'], 1)
        self.assertIsNone(self.session.normal_ui_binding)
        self.assertIn('vue_parent_cycle', captured.getvalue())
        self.assertNotIn('saved-fixture-id', captured.getvalue())
        self.assertNotIn('fixture-caption', captured.getvalue())

    async def test_unreadable_iframe_scope_fails_closed_even_with_same_id_editor(self):
        for accessor in ('location', 'contentDocument'):
            with self.subTest(accessor=accessor):
                await self.page.evaluate("""accessor=>{mountEditor();const frame=document.querySelector('#owner');
                  Object.defineProperty(frame,'contentDocument',{configurable:true,get:()=>{
                    if(accessor==='contentDocument')throw new DOMException('private-scope-detail','SecurityError');
                    return {get location(){throw new DOMException('private-scope-detail','SecurityError')}};
                  }});
                }""",accessor,isolated_context=False)
                captured = io.StringIO()
                with redirect_stdout(captured):
                    state, control = await _lookup(self.page, 'editor', self.candidate, self.session.expected['platform_fields'])
                self.assertEqual(state, 'invalid')
                self.assertIsNone(control)
                self.assertIsNone(self.session.normal_ui_binding)
                self.assertIn('iframe_scope_unavailable', captured.getvalue())
                self.assertIn('SecurityError', captured.getvalue())
                self.assertNotIn('private-scope-detail', captured.getvalue())
                self.assertNotIn('saved-fixture-id', captured.getvalue())

    async def test_wrong_id_duplicate_row_and_duplicate_edit_are_rejected(self):
        for options in ({'rows':['wrong-id']}, {'rows':['saved-fixture-id','saved-fixture-id']},
                        {'rows':['saved-fixture-id'],'duplicateEdit':True}, {'duplicateEdit':False,'icon':False}):
            with self.subTest(options=options):
                await self.setup(**options)
                # Start on the list to exercise rejection without waiting for
                # a nonexistent record to load after navigation.
                await self.page.evaluate('mountList()', isolated_context=False)
                self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
                self.assertEqual((await self.counts())['edit'], 0)
                self.assertIsNone(self.session.normal_ui_binding)

    async def test_leave_discards_only_unchanged_already_saved_fields_without_second_save(self):
        await self.setup(leave=True)
        self.assertTrue(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['discard'], 1)
        await self.setup(caption='changed-private-caption', leave=True)
        self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['discard'], 0)
        self.assertIsNone(self.session.normal_ui_binding)

    async def test_fields_changed_during_menu_hover_are_not_discarded(self):
        await self.setup(leave=True, hoverChangesCaption=True)
        self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['discard'], 0)
        self.assertIsNone(self.session.normal_ui_binding)

    async def test_guard_diagnostic_preserves_strict_owner_types_and_never_emits_private_values(self):
        self.candidate['owner'] = '987654321'
        self.session.account['uid'] = 987654321
        captured = io.StringIO()
        with redirect_stdout(captured):
            self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        output = captured.getvalue()
        self.assertNotIn('987654321', output)
        self.assertNotIn('fixture-caption', output)
        self.assertNotIn('saved-fixture-id', output)
        diagnostic = json.loads(output.split('TC_REOPEN_DIAGNOSTIC=', 1)[1])
        self.assertEqual(diagnostic['stage'], 'entry')
        self.assertFalse(diagnostic['owner_exact'])
        self.assertEqual((diagnostic['candidate_owner_type'], diagnostic['account_uid_type']), ('str', 'int'))
        self.assertEqual((await self.counts())['menu'], 0)

    async def test_guard_diagnostic_identifies_unmatched_raw_save_without_trusting_enriched_candidate(self):
        self.session.saves = [{**self.candidate, 'caption':'different-private-caption'}]
        captured = io.StringIO()
        with redirect_stdout(captured):
            self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        output = captured.getvalue()
        self.assertNotIn('different-private-caption', output)
        diagnostic = json.loads(output.split('TC_REOPEN_DIAGNOSTIC=', 1)[1])
        self.assertTrue(diagnostic['candidate_fields_match'])
        self.assertEqual(diagnostic['same_id_count'], 1)
        self.assertEqual(diagnostic['same_id_fields_match_count'], 0)
        self.assertEqual(diagnostic['same_id_full_match_count'], 0)
        self.assertEqual((await self.counts())['menu'], 0)

    async def test_child_realm_misdirection_is_rejected_and_reports_only_scope_booleans(self):
        # Reproduce a platform main_frame whose transport selected the micro
        # child's default realm. The location guard must reject it, even when
        # the Python-visible root URL remains a valid platform URL.
        proxy = SimpleNamespace(url=self.page.url, main_frame=SimpleNamespace(
            url=self.page.main_frame.url, evaluate_handle=self.page.frames[1].evaluate_handle))
        captured = io.StringIO()
        with redirect_stdout(captured):
            state, control = await _lookup(proxy, 'content', self.candidate, self.session.expected['platform_fields'])
        self.assertEqual(state, 'invalid')
        self.assertIsNone(control)
        diagnostic = json.loads(captured.getvalue().split('TC_REOPEN_DIAGNOSTIC=', 1)[1])
        self.assertEqual(diagnostic['reason'], 'document_scope_invalid')
        self.assertTrue(diagnostic['origin_matches'])
        self.assertFalse(diagnostic['platform_path'])
        self.assertTrue(diagnostic['micro_path'])
        self.assertNotIn('fixture-caption', captured.getvalue())

    async def test_unattested_candidate_and_wrong_editor_id_cannot_create_binding(self):
        self.session.saves = []
        self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['menu'], 0)
        self.session.saves = [self.candidate]
        await self.setup(editorId='different-id')
        self.assertFalse(await reopen_tc_draft(self.page, self.candidate))
        self.assertEqual((await self.counts())['edit'], 1)
        self.assertIsNone(self.session.normal_ui_binding)


if __name__ == '__main__':
    unittest.main()
