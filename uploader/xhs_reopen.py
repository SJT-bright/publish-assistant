"""Normal XHS local-draft editing from one exact-ID row and current owner.

Official project-publish-vue.474cd180.js, zero-based Unicode offsets:
1891185 draft-tabs; 1896281 drawer entry/visible state; 1897865 draft-list;
1900700 draft-actions .btn Edit sets the same draftId, closes the drawer,
then its normal handler calls recoverDraftById. No business method is called
by this helper. An already open drawer never authorizes clicking the entry
behind its overlay. The caller still must verify the restored full record.
"""
import asyncio
import copy
import json
import re
import time
from urllib.parse import urlsplit

from patchright.async_api import Error as BrowserError


def _official(url):
    try:
        parsed=urlsplit(url)
        return parsed.scheme=='https' and parsed.netloc=='creator.xiaohongshu.com'
    except (ValueError,TypeError):
        return False


async def _owner_matches(page,session,candidate):
    if (session is None or session.closed or session.platform!='xiaohongshu' or session.page is not page or
            getattr(page,'_sau_draft_evidence',None) is not session):
        return False
    wanted=session.expected.get('account',{})
    initial=session.account
    if (not isinstance(wanted,dict) or not isinstance(initial,dict) or
            initial.get('verified') is not True or initial.get('platform')!='xiaohongshu' or
            not initial.get('uid') or not initial.get('identity') or
            candidate.get('owner')!=str(initial['uid']) or wanted.get('identity')!=initial['identity'] or
            str(wanted.get('uid'))!=str(initial['uid'])):
        return False
    try:
        current=await session.read_account()
    except Exception:
        return False
    return bool(isinstance(current,dict) and current.get('verified') is True and
                current.get('platform')=='xiaohongshu' and current.get('identity')==initial['identity'] and
                str(current.get('uid'))==str(initial['uid']) and not session.closed and
                getattr(page,'_sau_draft_evidence',None) is session)


async def _visible_frames(page,selector):
    result=[]
    for frame in page.frames:
        if not _official(frame.url):
            continue
        for item in await frame.locator(selector).all():
            if await item.is_visible():
                result.append(item)
    return result


_DRAWER_STATE = r"""el => {
 const r=el.getBoundingClientRect(),s=getComputedStyle(el);
 const visible=r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&s.visibility!=='collapse';
 const inViewport=visible&&r.left>=-1&&r.top>=-1&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1;
 const animations=el.getAnimations();
 const pending=animations.some(a=>a.pending||a.playState==='running'||a.playState==='paused');
 let shiftedClosed=false;
 try{const m=new DOMMatrixReadOnly(s.transform);shiftedClosed=el.classList.contains('d-drawer-right')&&
  Math.abs(m.m41-r.width)<2&&Math.abs(m.m42)<2;}catch(_){}
 return {status:!visible?'closed':pending?'animating':inViewport?'ready':shiftedClosed?'closed':'offscreen',
  in_viewport:inViewport,animation_pending:pending,animation_count:animations.length};
}"""

_CONTROL_STATE = r"""el => {
 const r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
 const inViewport=r.width>0&&r.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight;
 const hit=inViewport?el.ownerDocument.elementFromPoint(x,y):null;
 return {in_viewport:inViewport,hit:!!hit&&(hit===el||el.contains(hit))};
}"""


def _diagnostic(stage,reason,**values):
    # Fixed names and geometry/animation booleans only. Never emit draft IDs,
    # account values, captions, URLs, or an exception's message.
    print('XHS_REOPEN_DIAGNOSTIC='+json.dumps({'stage':stage,'reason':reason,**values},
                                           ensure_ascii=True,sort_keys=True),flush=True)


async def reopen_xhs_draft(page,candidate,timeout=5):
    """One normal edit action only; no save, force click, URL or VM mutation.

    A fresh operation-bound candidate and current verified owner are required.
    Normal scrolling and Playwright pointer trials handle a clipped list row.
    Missing/duplicate rows, owner switches or an occluded control return false
    and preserve the existing draft. This return value is not verification.
    """
    if (not isinstance(candidate,dict) or not isinstance(candidate.get('id'),str) or
            not 0<len(candidate['id'])<=256 or re.search(r'[\x00-\x1f\x7f]',candidate['id']) or
            candidate.get('fresh') is not True or type(timeout) not in (int,float) or not 0<timeout<=30):
        return False
    session=getattr(page,'_sau_draft_evidence',None)
    if (session is not None and not session.closed and session.platform=='xiaohongshu' and
            session.page is page and getattr(page,'_sau_draft_evidence',None) is session):
        # A failed new normal edit must not reuse an earlier click's proof.
        session.normal_ui_binding=None
        session.normal_ui_candidate=None
    if not await _owner_matches(page,session,candidate):
        return False
    from uploader.draft_evidence import field_match
    if not field_match('xiaohongshu',candidate,session.expected):
        return False
    deadline=time.monotonic()+timeout
    opened=False
    stage='drawer_wait'
    last_drawer=None
    try:
        while time.monotonic()<deadline:
            drawers=await _visible_frames(page,'.draft-drawer')
            if len(drawers)>1:
                _diagnostic('drawer_lookup','ambiguous',count=len(drawers))
                return False
            if drawers:
                last_drawer=await drawers[0].evaluate(_DRAWER_STATE,isolated_context=False)
                if last_drawer.get('status')!='ready':
                    if last_drawer.get('status')!='closed':
                        # Do not re-click an entry beneath an opening drawer's
                        # invisible pointer-blocking mask. Wait for its ordinary
                        # animation and on-screen geometry within this budget.
                        await asyncio.sleep(min(.1,max(0,deadline-time.monotonic())))
                        continue
                    drawers=[]
            if drawers:
                rows=[]
                for item in await drawers[0].locator('.draft-item[data-draft-id][data-draft-type="video"]').all():
                    if await item.get_attribute('data-draft-id')==candidate['id'] and await item.is_visible():
                        rows.append(item)
                if len(rows)>1:
                    _diagnostic('saved_row_lookup','ambiguous',count=len(rows))
                    return False
                if rows:
                    edits=rows[0].locator('.draft-actions > .btn').filter(has_text=re.compile(r'^\s*编辑\s*$'))
                    if await edits.count()!=1 or not await edits.is_visible() or not await edits.is_enabled():
                        _diagnostic('edit_lookup','unique_enabled_control_unavailable')
                        return False
                    remaining=max(.01,deadline-time.monotonic())
                    stage='edit_scroll'
                    await edits.scroll_into_view_if_needed(timeout=remaining*1000)
                    control=await edits.evaluate(_CONTROL_STATE,isolated_context=False)
                    if control.get('in_viewport') is not True or control.get('hit') is not True:
                        _diagnostic('edit_pointer','not_reachable',in_viewport=control.get('in_viewport') is True,
                                    hit=control.get('hit') is True)
                        return False
                    stage='edit_trial'
                    await edits.click(trial=True,timeout=max(.01,deadline-time.monotonic())*1000)
                    if not await _owner_matches(page,session,candidate):
                        return False
                    # Re-check the physical row after scrolling and account read.
                    live_drawers=await _visible_frames(page,'.draft-drawer')
                    if len(live_drawers)!=1:
                        return False
                    live_geometry=await live_drawers[0].evaluate(_DRAWER_STATE,isolated_context=False)
                    if live_geometry.get('status')!='ready':
                        _diagnostic('edit_recheck','drawer_not_ready',**live_geometry)
                        return False
                    live_rows=[]
                    for item in await live_drawers[0].locator('.draft-item[data-draft-id][data-draft-type="video"]').all():
                        if await item.get_attribute('data-draft-id')==candidate['id'] and await item.is_visible():
                            live_rows.append(item)
                    if (len(live_rows)!=1 or await edits.count()!=1 or
                            await rows[0].get_attribute('data-draft-id')!=candidate['id'] or
                            await rows[0].get_attribute('data-draft-type')!='video'):
                        return False
                    stage='edit_click'
                    clicked_candidate=copy.deepcopy(candidate)
                    clicked_expected=copy.deepcopy(session.expected)
                    clicked_account=copy.deepcopy(session.account)
                    await edits.click(timeout=max(.01,deadline-time.monotonic())*1000)
                    if (not await _owner_matches(page,session,candidate) or candidate!=clicked_candidate or
                            session.expected!=clicked_expected or session.account!=clicked_account):
                        _diagnostic('edit_owner_recheck','normal_edit_binding_changed')
                        return False
                    session.normal_ui_binding={'id':clicked_candidate['id'],'owner':clicked_candidate['owner'],
                                               'control':'draft_item_edit'}
                    session.normal_ui_candidate=clicked_candidate
                    _diagnostic('edit_click','normal_edit_completed')
                    return True
                # A drawer can render its list asynchronously. Keep this drawer
                # and wait briefly; never click the covered draft-title-box.
            elif not opened:
                entries=await _visible_frames(page,'.draft-title-box')
                if len(entries)>1:
                    return False
                if entries:
                    entry=entries[0]
                    stage='entry_trial'
                    await entry.scroll_into_view_if_needed(timeout=max(.01,deadline-time.monotonic())*1000)
                    await entry.click(trial=True,timeout=max(.01,deadline-time.monotonic())*1000)
                    if not await _owner_matches(page,session,candidate):
                        return False
                    await entry.click(timeout=max(.01,deadline-time.monotonic())*1000)
                    opened=True
                    stage='drawer_wait'
            await asyncio.sleep(min(.1,max(0,deadline-time.monotonic())))
    except BrowserError as error:
        _diagnostic(stage,'pointer_operation_failed',error_type=type(error).__name__)
        return False
    if last_drawer and last_drawer.get('status')!='ready':
        _diagnostic('drawer_wait','drawer_not_ready_before_deadline',**last_drawer)
    else:
        _diagnostic('saved_row_lookup','deadline_expired')
    return False
