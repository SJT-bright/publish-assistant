"""Native editor selection and the current official topic command contract.

The isolated picker uses real browser input/Range/click events with the site's
topic DOM and NBSP insertion, not a mock of uploader.fill_meta. No site traffic
or save handler exists. The native Range fallback and existing editor selection
bridge are both exercised; this is not a whole-platform Tiptap E2E test.
"""
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from patchright.async_api import async_playwright
from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo, draft_xhs_description_plan


EDITOR_FIXTURE = r"""<!doctype html><style>
#editor{min-height:120px;white-space:pre-wrap;border:1px solid;padding:10px}
.content-hide{display:none}.item{padding:8px;cursor:pointer}.name{display:inline-block}
</style><input placeholder="填写标题"><div id="editor" class="ProseMirror" contenteditable="true"><p data-placeholder="输入正文描述"><br class="ProseMirror-trailingBreak"></p></div>
<div id="creator-editor-topic-container" style="display:none"></div><script>(()=>{
const editor=document.querySelector('#editor'), picker=document.querySelector('#creator-editor-topic-container');
window.chosen=[];window.inputEvents=0;window.selectionCalls=0;window.candidateMode='normal';window.corruptAfterClick=false;
window.insertGap=true;window.nativeSeparators=false;
let suggestion=null, filled=false;
const close=()=>{suggestion=null;picker.replaceChildren();picker.style.display='none';};
function show(query,range){
 suggestion={query,range:range.cloneRange()};picker.replaceChildren();picker.style.display='block';
 const names=window.candidateMode==='missing'?['验']:window.candidateMode==='ambiguous'?[query,query]:[query+'相似',query];
 for(const name of names){
  const item=document.createElement('div');item.className='item';
  const label=document.createElement('span');label.className='name';label.textContent='#'+name;item.append(label);
  const count=document.createElement('span');count.className='num';count.textContent='123次浏览';item.append(count);
  item.addEventListener('mousedown',event=>event.preventDefault());
  item.addEventListener('click',()=>{
   const current=suggestion;if(!current)return;
   const range=current.range,topic=document.createElement('a');topic.contentEditable='false';topic.className='tiptap-topic';
   topic.dataset.topic=JSON.stringify({id:'fixture-'+name,name,link:''});
   topic.append(document.createTextNode('#'+name));const hidden=document.createElement('span');hidden.className='content-hide';hidden.textContent='[话题]#';topic.append(hidden);
   // Exact official deleteRange(...).insertTopic(...) output. The following
   // user whitespace remains outside the suggestion range.
   range.deleteContents();range.insertNode(topic);
   if(window.insertGap){const gap=document.createTextNode('\u00a0');topic.after(gap);range.setStart(gap,1);}
   else{range.setStartAfter(topic);}
   range.collapse(true);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
   if(window.nativeSeparators && !window.insertGap){
    while(topic.nextSibling?.nodeType===3&&topic.nextSibling.data==='')topic.nextSibling.remove();
    if(!topic.nextSibling){const img=document.createElement('img');img.className='ProseMirror-separator';img.alt='';const br=document.createElement('br');br.className='ProseMirror-trailingBreak';topic.after(img,br);window.attachSeparatorFixture(img);}
   }
   window.chosen.push(name);close();editor.focus();
   if(window.corruptAfterClick)editor.append(document.createTextNode('不可接受的多余正文'));
   editor.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
  });picker.append(item);
 }
}
editor.addEventListener('keydown',event=>{if(event.key==='Escape'){close();event.preventDefault();}});
editor.addEventListener('input',event=>{
 window.inputEvents++;
 const selection=getSelection();if(!selection.rangeCount||!editor.contains(selection.anchorNode))return;
 const cursor=selection.getRangeAt(0);
 // Reproduce the stale picker/caret left by the original whole-description
 // fill: the pending topic is anchored in the middle of 验收, not at the end.
 if(!filled){
  filled=true;
  const walker=document.createTreeWalker(editor,NodeFilter.SHOW_TEXT);let node;
  while(node=walker.nextNode()){
   const at=node.data.indexOf('验收');if(at>=0){const wrong=document.createRange();wrong.setStart(node,at+1);wrong.setEnd(node,at+2);show('科普',wrong);wrong.collapse(true);selection.removeAllRanges();selection.addRange(wrong);return;}
  }
 }
 if(suggestion && suggestion.query==='科普' && window.chosen.length===0 && event.data===null)return;
 const node=cursor.startContainer;if(node.nodeType!==3){close();return;}
 const prefix=node.data.slice(0,cursor.startOffset),match=/#([^#\s]*)$/u.exec(prefix);
 if(!match){close();return;}
 const range=document.createRange();range.setStart(node,cursor.startOffset-match[0].length);range.setEnd(node,cursor.startOffset);
 show(match[1],range);
});
window.enableEditorBridge=()=>{
 const points=new Map();let next=1;
 const bridge={view:{dom:editor,docView:{dom:editor,children:[]},focus:()=>editor.focus(),posAtDOM:(node,offset)=>{const id=next++;points.set(id,[node,offset]);return id;}},state:{selection:{}},commands:{setTextSelection:({from,to})=>{
  window.selectionCalls++;const range=document.createRange();range.setStart(...points.get(from));range.setEnd(...points.get(to));const selection=getSelection();selection.removeAllRanges();selection.addRange(range);bridge.state.selection={from,to};return true;
 }}};window._tiptap_editor=bridge;
};
window.attachSeparatorFixture=img=>{
 const root=window._tiptap_editor.view.docView,parent={dom:img.parentElement,parent:root,children:[]};root.children.push(parent);
 const desc={dom:img,parent,children:[],size:0};parent.children.push(desc);img.pmViewDesc=desc;
};
})();</script>"""


class XhsDescriptionPlanTests(unittest.TestCase):
    def test_exact_whitespace_inline_topics_and_only_missing_append(self):
        text = '  发布助手😀\n\n正文  #科普 #验收\n尾行  \n'
        plan = draft_xhs_description_plan(text, ['科普', '验收', '独立话题', '科普'])
        self.assertEqual(plan['plain'], text + '\n#独立话题')
        self.assertEqual(plan['serialized'], text.replace('#科普', '#科普[话题]#').replace('#验收', '#验收[话题]#') + '\n#独立话题[话题]#')
        self.assertEqual(plan['topics'], ['科普', '验收', '独立话题'])
        self.assertEqual(plan['platform_serialized'], '  发布助手😀\n\t\n正文  #科普[话题]# #验收[话题]#\n尾行  \n\t\n#独立话题[话题]#')

    def test_url_programming_duplicate_and_encoded_mentions_not_lost(self):
        text = 'C#科普 https://fixture.invalid/#验收 #科普 #科普 ＃验收[话题]#。'
        plan = draft_xhs_description_plan(text, ['科普', '验收'])
        self.assertEqual(plan['plain'], 'C#科普 https://fixture.invalid/#验收 #科普 #科普 #验收。')
        self.assertEqual(plan['serialized'], 'C#科普 https://fixture.invalid/#验收 #科普[话题]# #科普 #验收[话题]#。')
        self.assertEqual(plan['topics'], ['科普', '验收'])
        self.assertEqual(draft_xhs_description_plan('', ['科普'])['plain'], '#科普')

    def test_official_cached_source_still_contains_observed_contract(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'research/raw/2026-10-03/limits-login/fresh-xhs-project-publish-vue.474cd180.js').read_text()
        for exact in ['window._tiptap_editor=e', 't.chain().deleteRange(n).insertTopic(a).focus().run()',
                      '.insertContent("\\xa0")', 'class:"content-hide"},"[话题]#"',
                      'dS={class:"name"}', '["prevent"]']:
            self.assertIn(exact, source)


class XhsNativeUploadGateTests(unittest.TestCase):
    def test_sdk_ready_is_required_before_video_selection_and_task_race_is_rejected(self):
        async def run():
            for case in ('ready', 'unavailable', 'switched', 'closed', 'foreign', 'missing'):
                with self.subTest(case=case):
                    events = []
                    input_node = object()
                    locator = SimpleNamespace(count=AsyncMock(return_value=1), all=AsyncMock(return_value=[input_node]))
                    frame = SimpleNamespace(locator=lambda _selector: locator)
                    page = SimpleNamespace(locator=lambda _selector: locator, frames=[frame], goto=AsyncMock())
                    session = SimpleNamespace(page=page, platform='xiaohongshu', closed=False)
                    observer = SimpleNamespace(session=session, page=page, closed=False)
                    session.xhs_observer, page._sau_draft_evidence = observer, session
                    async def ready(timeout):
                        self.assertEqual(timeout, 5)
                        events.append('ready')
                        if case == 'switched':
                            page._sau_draft_evidence = SimpleNamespace()
                        return case != 'unavailable'
                    observer.ensure_ready = AsyncMock(side_effect=ready)
                    if case == 'closed':
                        session.closed = True
                    elif case == 'foreign':
                        observer.page = object()
                    elif case == 'missing':
                        del page._sau_draft_evidence
                    async def selected(actual_page, actual_input, path):
                        self.assertIs(actual_page, page)
                        self.assertIs(actual_input, input_node)
                        self.assertEqual(events, ['ready'])
                        events.append('selected')
                    uploader = XiaoHongShuVideo('完整标题', 'fixture.mp4', [], 0, 'unused.json')
                    uploader.draft_only, uploader.desc = True, '完整正文'
                    uploader.fill_meta = AsyncMock()
                    with patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()), \
                         patch('uploader.draft_only.draft_resume_active', return_value=False), \
                         patch('uploader.xhs_ks_upload_ready.wait_xhs_ks_upload_complete', new=AsyncMock()), \
                         patch('uploader.draft_only.checkpoint_after_upload', new=AsyncMock()), \
                         patch('uploader.draft_only.save_draft', new=AsyncMock()) as save, \
                         patch('uploader.xiaohongshu_uploader.main.set_local_files', new=AsyncMock(side_effect=selected)) as files:
                        if case == 'ready':
                            await uploader.upload_video_content(page)
                            self.assertEqual(events, ['ready', 'selected'])
                            files.assert_awaited_once()
                            save.assert_awaited_once()
                        else:
                            with self.assertRaises(RuntimeError):
                                await uploader.upload_video_content(page)
                            files.assert_not_awaited()
                            save.assert_not_awaited()
                    page.goto.assert_not_awaited()
        asyncio.run(run())

class XhsEditorTests(unittest.TestCase):
    def test_retry_reuses_filled_current_editor_without_placeholder_and_preserves_whole_text(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    await page.route('**/*', lambda route: route.abort())
                    await page.set_content(EDITOR_FIXTURE)
                    await page.evaluate('''()=>{
                        document.querySelector('#editor').classList.add('tiptap');
                        window.enableEditorBridge();window.insertGap=false;window.nativeSeparators=true;
                    }''', isolated_context=False)
                    uploader = XiaoHongShuVideo('旧标题', 'fixture.mp4', ['科普', '验收'], 0, 'unused.json')
                    uploader.draft_only, uploader.desc, uploader.draft_topic_timeout = True, '旧正文\n#科普 #验收', .8
                    with patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()):
                        # The empty editor matches both the placeholder and
                        # its current view. This is one physical editor.
                        first_form = await uploader._draft_content_controls(page)
                        self.assertIsNotNone(first_form)
                        await first_form[2].dispose()
                        await uploader.fill_meta(page)
                        # Native contentEditable clones the fixture's paragraph
                        # attributes. The real current Tiptap renderer removes
                        # its placeholder after normal input (read-only live
                        # projection: two paragraphs, zero placeholder attrs).
                        await page.evaluate('''()=>{
                            for(const paragraph of document.querySelectorAll('#editor p[data-placeholder]'))
                                paragraph.removeAttribute('data-placeholder');
                        }''', isolated_context=False)
                        self.assertEqual(await page.locator('#editor p[data-placeholder]').count(), 0)
                        self.assertEqual(await page.locator('#editor img.ProseMirror-separator').count(), 1)
                        form = await uploader._draft_content_controls(page)
                        self.assertIsNotNone(form)
                        self.assertTrue(await form[2].evaluate(
                            'e=>e===window._tiptap_editor.view.dom', isolated_context=False))
                        await form[2].dispose()
                        uploader.title, uploader.desc = '新完整标题', '  新正文😀\n\n保留原样空白 #科普\u00a0 #验收'
                        await uploader.fill_meta(page)
                    editor = await page.locator('#editor').element_handle()
                    plan = draft_xhs_description_plan(uploader.desc, uploader.tags)
                    self.assertEqual(await uploader._draft_topic_snapshot(editor), {
                        'plain': plan['plain'], 'serialized': plan['serialized'], 'topics': plan['topics']})
                    self.assertEqual(await page.locator('input').input_value(), '新完整标题')
                    self.assertEqual(await page.locator('#editor a[data-topic]').count(), 2)
                    self.assertEqual(await page.evaluate('window.chosen', isolated_context=False),
                                     ['科普', '验收', '科普', '验收'])
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_filled_editor_without_current_ownership_is_rejected_and_ambiguous_controls_stop(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    await page.route('**/*', lambda route: route.abort())
                    uploader = XiaoHongShuVideo('新标题', 'fixture.mp4', [], 0, 'unused.json')
                    for variant in ('css_only', 'old_view', 'wrong_docview', 'destroyed', 'hidden',
                                    'forged_node', 'competing_placeholder', 'two_titles'):
                        with self.subTest(variant=variant):
                            await page.set_content(EDITOR_FIXTURE)
                            await page.evaluate('''variant=>{
                                const e=document.querySelector('#editor');e.classList.add('tiptap');
                                e.innerHTML='<p>既有完整正文</p>';document.querySelector('input').value='旧标题';
                                window.enableEditorBridge();const bridge=window._tiptap_editor;
                                if(variant==='css_only')delete window._tiptap_editor;
                                if(variant==='old_view'){
                                    bridge.view.dom=e.cloneNode(true);bridge.view.docView.dom=bridge.view.dom;
                                }
                                if(variant==='wrong_docview')bridge.view.docView.dom=document.body;
                                if(variant==='destroyed')bridge.isDestroyed=true;
                                if(variant==='hidden')e.style.display='none';
                                if(variant==='forged_node'){
                                    const fake={ownerDocument:document,isConnected:true,isContentEditable:true};
                                    bridge.view.dom=fake;bridge.view.docView.dom=fake;
                                }
                                if(variant==='competing_placeholder'){
                                    const second=document.createElement('div');second.contentEditable='true';
                                    second.innerHTML='<p data-placeholder="输入正文描述">另一份正文</p>';
                                    document.body.append(second);
                                }
                                if(variant==='two_titles')document.body.append(document.querySelector('input').cloneNode());
                            }''', variant, isolated_context=False)
                            if variant in ('competing_placeholder', 'two_titles'):
                                with self.assertRaisesRegex(RuntimeError, '多个标题/正文组件'):
                                    await uploader._draft_content_controls(page)
                            else:
                                self.assertIsNone(await uploader._draft_content_controls(page))
                            self.assertEqual(await page.locator('input').first.input_value(), '旧标题')
                            self.assertEqual(await page.locator('#editor').text_content(), '既有完整正文')
                            self.assertEqual(await page.evaluate('window.inputEvents', isolated_context=False), 0)
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_current_topic_separator_with_no_inserted_gap_keeps_exact_whole_text(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    await page.route('**/*', lambda route: route.abort())
                    with patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()):
                        for insert_gap in (False, True):
                            with self.subTest(insert_gap=insert_gap):
                                await page.set_content(EDITOR_FIXTURE)
                                await page.evaluate('([gap])=>{window.enableEditorBridge();window.insertGap=gap;window.nativeSeparators=true;}', [insert_gap], isolated_context=False)
                                caption = '第一行😀\n保留空白 #科普\u00a0 #验收'
                                uploader = XiaoHongShuVideo('完整标题', 'fixture.mp4', ['科普', '验收'], 0, 'unused.json')
                                uploader.draft_only, uploader.desc, uploader.draft_topic_timeout = True, caption, .8
                                await uploader.fill_meta(page)
                                editor = await page.locator('#editor').element_handle()
                                plan = draft_xhs_description_plan(caption, uploader.tags)
                                self.assertEqual(await uploader._draft_topic_snapshot(editor), {
                                    'plain': plan['plain'], 'serialized': plan['serialized'], 'topics': plan['topics']})
                                self.assertEqual(await page.locator('#editor img.ProseMirror-separator').count(), 0 if insert_gap else 1)
                                self.assertEqual(await page.evaluate('window.chosen', isolated_context=False), ['科普', '验收'])
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_user_unknown_and_forged_separator_images_are_rejected(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    await page.route('**/*', lambda route: route.abort())
                    uploader = XiaoHongShuVideo('完整标题', 'fixture.mp4', [], 0, 'unused.json')
                    for variant in ('ordinary_image', 'css_only', 'unowned_desc', 'old_root', 'nonzero', 'model_image', 'src_added', 'wrong_position'):
                        with self.subTest(variant=variant):
                            await page.set_content(EDITOR_FIXTURE)
                            await page.evaluate(r'''variant=>{
                                const e=document.querySelector('#editor');e.innerHTML='<p>原文 <a contenteditable="false" data-topic=\'{"id":"fixture","name":"科普"}\'>#科普<span class="content-hide">[话题]#</span></a><img class="ProseMirror-separator" alt=""><br class="ProseMirror-trailingBreak"></p>';
                                window.enableEditorBridge();const img=e.querySelector('img');
                                if(variant==='ordinary_image'){img.className='user-image';img.setAttribute('alt','有意义的正文图片');}
                                else if(variant==='unowned_desc'){img.pmViewDesc={dom:img,children:[],size:0};}
                                else if(variant!=='css_only'){
                                    window.attachSeparatorFixture(img);
                                    if(variant==='old_root')window._tiptap_editor.view.docView={dom:e,children:[]};
                                    if(variant==='nonzero')img.pmViewDesc.size=1;
                                    if(variant==='model_image')img.pmViewDesc.node={type:{name:'image'}};
                                    if(variant==='src_added')img.setAttribute('src','');
                                    if(variant==='wrong_position')e.querySelector('p').prepend(img);
                                }
                            }''', variant, isolated_context=False)
                            editor = await page.locator('#editor').element_handle()
                            with self.assertRaisesRegex(Exception, 'unsupported_editor_image'):
                                await uploader._draft_topic_snapshot(editor)
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_native_range_and_existing_editor_selection_preserve_whole_caption(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    await page.route('**/*', lambda route: route.abort())
                    with patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()):
                        cases = [
                            ('发布助手自动上传流程验收。\n保留换行并核对视频、文案与封面。 #科普 #验收', ['科普', '验收']),
                            ('  第一行😀\n\n中间 #科普  原样空格\n最后 #验收\n', ['科普', '验收', '独立']),
                            ('#科普[话题]#。原文 #科普 和 ＃验收。', ['科普', '验收']),
                            ('', ['科普', '验收']),
                            ('原文尾部\n\n', []),
                        ]
                        for bridge in (False, True):
                            for caption, tags in cases:
                                with self.subTest(bridge=bridge, caption=caption):
                                    await page.set_content(EDITOR_FIXTURE)
                                    if bridge:
                                        await page.evaluate('enableEditorBridge()', isolated_context=False)
                                    uploader = XiaoHongShuVideo('完整标题', 'fixture.mp4', tags, 0, 'unused.json')
                                    uploader.draft_only, uploader.desc, uploader.draft_topic_timeout = True, caption, .8
                                    await uploader.fill_meta(page)
                                    plan = draft_xhs_description_plan(caption, tags)
                                    editor = await page.locator('#editor').element_handle()
                                    self.assertEqual(await uploader._draft_topic_snapshot(editor), {
                                        'plain': plan['plain'], 'serialized': plan['serialized'], 'topics': plan['topics']})
                                    self.assertEqual(await page.evaluate('window.chosen', isolated_context=False), plan['topics'])
                                    self.assertEqual(await page.locator('input').input_value(), '完整标题')
                                    self.assertFalse(await page.locator('#editor a[data-topic]').count() > len(set(tags)))
                                    if bridge and tags:
                                        self.assertGreater(await page.evaluate('window.selectionCalls', isolated_context=False), 0)
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_wrong_ambiguous_and_corrupted_results_fail_without_discarding_fields(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    await page.route('**/*', lambda route: route.abort())
                    with patch('uploader.draft_only.assert_draft_safe', new=AsyncMock()):
                        for mode, corrupt in [('missing', False), ('ambiguous', False), ('normal', True)]:
                            with self.subTest(mode=mode, corrupt=corrupt):
                                await page.set_content(EDITOR_FIXTURE)
                                await page.evaluate('([mode,corrupt])=>{window.candidateMode=mode;window.corruptAfterClick=corrupt;}', [mode, corrupt], isolated_context=False)
                                uploader = XiaoHongShuVideo('完整标题', 'fixture.mp4', ['验收'], 0, 'unused.json')
                                uploader.draft_only, uploader.desc, uploader.draft_topic_timeout = True, '首行\n#验收\n完整尾行', .4
                                with self.assertRaises(RuntimeError):
                                    await uploader.fill_meta(page)
                                self.assertEqual(uploader.desc, '首行\n#验收\n完整尾行')
                                self.assertEqual(uploader.tags, ['验收'])
                                self.assertEqual(await page.locator('input').input_value(), '完整标题')
                                self.assertIn('完整尾行', await page.locator('#editor').inner_text())
                                if mode != 'normal':
                                    self.assertEqual(await page.evaluate('window.chosen', isolated_context=False), [])
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_paragraph_spacing_and_inline_breaks_preserve_description(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                try:
                    page = await browser.new_page()
                    uploader = XiaoHongShuVideo('title', 'fixture.mp4', [], 0, 'unused.json')
                    cases = [
                        ('<p>第一行</p><p>第二行</p>', '第一行\n第二行'),
                        ('<p>第一行</p><p><br class="ProseMirror-trailingBreak"></p><p>第三行</p>', '第一行\n\n第三行'),
                        ('<p>同段<br>换行</p><p>#话题 <span>#另一话题</span></p>', '同段\n换行\n#话题 #另一话题'),
                        ('纯文字\n#话题', '纯文字\n#话题'),
                    ]
                    for html, expected in cases:
                        await page.set_content('<style>p{margin:1em 0} #editor{white-space:pre-wrap}</style><div id="editor" contenteditable="true">'+html+'</div>')
                        editor = await page.locator('#editor').element_handle()
                        self.assertEqual(await uploader._draft_editor_text(editor), expected)
                finally:
                    await browser.close()
        asyncio.run(run())

if __name__ == '__main__':
    unittest.main()
