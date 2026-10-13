"""Normal exact-ID XHS reopen on isolated local fixtures, never real drafts."""
import asyncio
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock

from patchright.async_api import async_playwright
from uploader.xhs_reopen import reopen_xhs_draft
from uploader.xiaohongshu_uploader.main import draft_xhs_description_plan

URL='https://creator.xiaohongshu.com/publish/publish?target=video'
HTML=r'''<!doctype html><meta charset="utf-8"><style>
.draft-title-box{position:fixed;top:81px;height:40px;width:150px;background:#eee}
.draft-drawer{position:fixed;top:70px;left:180px;width:600px;height:350px;background:white;z-index:10}
.draft-tabs{height:48px;background:#ddd;position:sticky;top:0;z-index:20}
.draft-list{height:280px;overflow:auto}.draft-item{height:90px}.draft-actions>.btn{display:inline-block;height:24px;padding:3px;background:#eee}
</style><div class="draft-title-box" onclick="window.entryClicks++;document.querySelector('.draft-drawer').style.display='block'">草稿箱</div>
<div class="draft-drawer" style="display:DISPLAY"><div class="draft-tabs">视频草稿</div><div class="draft-list">ROWS</div></div>
<script>window.entryClicks=0;window.editClicks=0;window.deleteClicks=0;window.selected=null;window.scrollWhenEdited=0;
for(const row of document.querySelectorAll('.draft-item')){row.querySelector('.edit').onclick=()=>{++editClicks;selected=row.dataset.draftId;scrollWhenEdited=document.querySelector('.draft-list').scrollTop;document.querySelector('.draft-drawer').style.display='none'};row.querySelector('.delete').onclick=()=>++deleteClicks;}
</script>'''

def row(ident,kind='video'):
    return '<div class="draft-item" data-draft-id="'+ident+'" data-draft-type="'+kind+'"><div class="draft-info">相同标题<div class="draft-actions"><div class="btn edit">编辑</div><div class="btn delete">删除</div></div></div></div>'


class XhsNormalReopenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(headless=True,channel='chromium',args=['--disable-gpu'])
        self.context=await self.browser.new_context();self.page=await self.context.new_page();self.external=[]
        self.owner={'verified':True,'platform':'xiaohongshu','uid':'isolated-owner','identity':'isolated-owner-fingerprint'}
        fields={'title':'相同标题','description':'完整正文','tags':['话题']}
        self.candidate={'id':'exact-draft','owner':self.owner['uid'],'fresh':True,'title':fields['title'],
                        'description':draft_xhs_description_plan(fields['description'],fields['tags'])['plain'],'tags':fields['tags']}
        self.session=SimpleNamespace(page=self.page,platform='xiaohongshu',closed=False,account=copy.deepcopy(self.owner),
                                    expected={'account':copy.deepcopy(self.owner),'platform_fields':fields},read_account=AsyncMock(return_value=copy.deepcopy(self.owner)))
        self.page._sau_draft_evidence=self.session

    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()

    async def fixture(self,rows,opened=True,overlay=False):
        document=HTML.replace('DISPLAY','block' if opened else 'none').replace('ROWS',rows)
        if overlay:document+='<div style="position:fixed;inset:0;background:#ddd;z-index:1000">其他对话框</div>'
        async def route(r):
            if r.request.url!=URL:self.external.append(r.request.url);return await r.abort()
            await r.fulfill(content_type='text/html;charset=utf-8',body=document)
        await self.context.route('**/*',route);await self.page.goto(URL)

    async def counters(self):
        return await self.page.evaluate('()=>({entry:entryClicks,edit:editClicks,deleted:deleteClicks,selected})',isolated_context=False)

    async def test_already_open_drawer_edits_exact_id_and_never_entry_or_delete(self):
        await self.fixture(row('other-draft')+row('exact-draft'))
        self.assertTrue(await reopen_xhs_draft(self.page,self.candidate,timeout=2))
        self.assertEqual(await self.counters(),{'entry':0,'edit':1,'deleted':0,'selected':'exact-draft'});self.assertFalse(self.external)
        self.assertEqual(self.session.normal_ui_binding,{'id':self.candidate['id'],'owner':self.candidate['owner'],'control':'draft_item_edit'})
        self.assertEqual(self.session.normal_ui_candidate,self.candidate)
        self.assertIsNot(self.session.normal_ui_candidate,self.candidate)
        self.candidate['tags'].append('later-change')
        self.assertNotEqual(self.session.normal_ui_candidate,self.candidate)

    async def test_closed_drawer_normal_entry_then_edit_only_once(self):
        await self.fixture(row('exact-draft'),opened=False)
        self.assertTrue(await reopen_xhs_draft(self.page,self.candidate,timeout=2))
        self.assertEqual((await self.counters())['entry'],1);self.assertEqual((await self.counters())['edit'],1)

    async def test_css_visible_retained_closed_drawer_uses_normal_entry(self):
        await self.fixture(row('exact-draft'))
        await self.page.evaluate('''()=>{
            const drawer=document.querySelector('.draft-drawer');drawer.classList.add('d-drawer-right');
            Object.assign(drawer.style,{left:'auto',right:'12px',transform:'translateX(100%)'});
            document.querySelector('.draft-title-box').onclick=()=>{entryClicks++;drawer.style.transform='none';};
        }''',isolated_context=False)
        self.assertTrue(await self.page.locator('.draft-drawer').is_visible())
        self.assertTrue(await reopen_xhs_draft(self.page,self.candidate,timeout=2))
        self.assertEqual((await self.counters())['entry'],1)
        self.assertEqual((await self.counters())['edit'],1)

    async def test_normal_opening_animation_settles_before_edit_and_never_reclicks_entry(self):
        await self.fixture(row('exact-draft'))
        await self.page.evaluate('''()=>document.querySelector('.draft-drawer').animate(
            [{transform:'translateX(100%)'},{transform:'none'}],{duration:250,fill:'both'})''',isolated_context=False)
        self.assertTrue(await reopen_xhs_draft(self.page,self.candidate,timeout=2))
        self.assertEqual((await self.counters())['entry'],0)
        self.assertEqual((await self.counters())['edit'],1)

    async def test_stalled_opening_animation_is_bounded_and_never_reclicks_covered_entry(self):
        await self.fixture(row('exact-draft'))
        await self.page.evaluate('''()=>{
            const animation=document.querySelector('.draft-drawer').animate(
                [{transform:'translateX(100%)'},{transform:'none'}],{duration:100,fill:'both'});
            animation.pause();animation.currentTime=0;
        }''',isolated_context=False)
        captured=io.StringIO()
        with redirect_stdout(captured):
            self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=.25))
        diagnostic=json.loads(captured.getvalue().split('XHS_REOPEN_DIAGNOSTIC=',1)[1])
        self.assertEqual(diagnostic['reason'],'drawer_not_ready_before_deadline')
        self.assertEqual(diagnostic['status'],'animating')
        self.assertTrue(diagnostic['animation_pending'])
        self.assertNotIn('exact-draft',captured.getvalue())
        self.assertNotIn('isolated-owner',captured.getvalue())
        self.assertEqual((await self.counters())['entry'],0)
        self.assertEqual((await self.counters())['edit'],0)

    async def test_offscreen_untransformed_drawer_is_not_repositioned_or_treated_as_closed(self):
        await self.fixture(row('exact-draft'))
        await self.page.evaluate("()=>{document.querySelector('.draft-drawer').style.left=(innerWidth+20)+'px';}",isolated_context=False)
        before=await self.page.locator('.draft-drawer').bounding_box()
        self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=.25))
        self.assertEqual((await self.page.locator('.draft-drawer').bounding_box())['x'],before['x'])
        self.assertEqual((await self.counters())['entry'],0)
        self.assertEqual((await self.counters())['edit'],0)

    async def test_offscreen_exact_row_normal_scroll_then_edit(self):
        await self.fixture(row('other-draft')+'<div style="height:900px">隔离长列表</div>'+row('exact-draft'))
        self.assertEqual(await self.page.locator('.draft-list').evaluate('e=>e.scrollTop'),0)
        self.assertTrue(await reopen_xhs_draft(self.page,self.candidate,timeout=2))
        self.assertGreater(await self.page.evaluate('window.scrollWhenEdited',isolated_context=False),0)
        self.assertEqual((await self.counters())['edit'],1)

    async def test_missing_duplicate_or_nonvideo_row_never_clicks_open_drawer_entry(self):
        for rows in (row('other-draft'),row('exact-draft')*2,row('exact-draft','image')):
            with self.subTest(rows=rows):
                await self.context.unroute('**/*');await self.fixture(rows)
                self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=.2))
                self.assertEqual((await self.counters())['entry'],0);self.assertEqual((await self.counters())['edit'],0)

    async def test_owner_changed_before_or_after_pointer_trial_never_edits(self):
        await self.fixture(row('exact-draft'))
        wrong={**self.owner,'uid':'another-owner','identity':'another-fingerprint'}
        for values in ([wrong],[copy.deepcopy(self.owner),wrong]):
            self.session.read_account.side_effect=values
            self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=1))
            self.assertEqual((await self.counters())['edit'],0)

    async def test_owner_change_after_normal_click_does_not_attest_reopened_editor(self):
        await self.fixture(row('exact-draft'))
        wrong={**self.owner,'uid':'another-owner','identity':'another-fingerprint'}
        self.session.read_account.side_effect=[copy.deepcopy(self.owner),copy.deepcopy(self.owner),wrong]
        self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=1))
        self.assertEqual((await self.counters())['edit'],1)
        self.assertIsNone(self.session.normal_ui_binding);self.assertIsNone(self.session.normal_ui_candidate)

    async def test_new_failed_edit_cannot_reuse_prior_normal_binding(self):
        await self.fixture(row('other-draft'))
        self.session.normal_ui_binding={'id':self.candidate['id'],'owner':self.candidate['owner'],'control':'draft_item_edit'}
        self.session.normal_ui_candidate=copy.deepcopy(self.candidate)
        self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=.1))
        self.assertIsNone(self.session.normal_ui_binding);self.assertIsNone(self.session.normal_ui_candidate)

    async def test_occluded_edit_does_not_force_or_dom_click(self):
        await self.fixture(row('exact-draft'),overlay=True)
        self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=.3))
        self.assertEqual((await self.counters())['edit'],0);self.assertEqual((await self.counters())['entry'],0)

    async def test_stale_closed_or_wrong_owner_candidate_never_opens(self):
        await self.fixture(row('exact-draft'),opened=False)
        for candidate in ({**self.candidate,'fresh':False},{**self.candidate,'owner':'other-owner'}):
            self.assertFalse(await reopen_xhs_draft(self.page,candidate,timeout=.2))
        self.session.closed=True;self.assertFalse(await reopen_xhs_draft(self.page,self.candidate,timeout=.2))
        self.assertEqual((await self.counters())['entry'],0)


if __name__=='__main__':unittest.main()
