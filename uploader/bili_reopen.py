"""Normal Bili draft navigation from an observed unique management link only."""
import json
import re
import asyncio
import time
from urllib.parse import parse_qs, urlsplit


def _management(url):
    try:
        u=urlsplit(url)
        return (u.scheme=='https' and u.netloc=='member.bilibili.com' and not u.fragment and
                u.path=='/platform/upload-manager/article' and parse_qs(u.query).get('group')==['draft'])
    except (ValueError,TypeError):return False


def _management_page(url):
    try:
        u = urlsplit(url)
        return u.scheme == 'https' and u.netloc == 'member.bilibili.com' and not u.fragment and u.path == '/platform/upload-manager/article'
    except (ValueError, TypeError):
        return False


async def bili_open_draft_management(page, timeout=10):
    """Use only existing normal UI entry/tab controls after a proven save ACK."""
    from uploader.draft_only import assert_draft_safe
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        return False
    deadline = time.monotonic() + timeout
    entry_clicked = draft_clicked = False
    while time.monotonic() < deadline:
        await assert_draft_safe(page, 'B站')
        if _management(page.url):
            return True
        names = ('草稿',) if _management_page(page.url) else ('内容管理', '稿件管理')
        if (draft_clicked if _management_page(page.url) else entry_clicked):
            await asyncio.sleep(.1)
            continue
        for name in names:
            controls = [item for item in await page.get_by_text(name, exact=True).all()
                        if await item.is_visible() and await item.is_enabled()]
            if len(controls) > 1:
                return False
            if len(controls) == 1:
                in_management = _management_page(page.url)
                await controls[0].click(timeout=10000)
                if in_management:
                    draft_clicked = True
                else:
                    entry_clicked = True
                break
        await asyncio.sleep(.1)
    return False


async def bili_reopen_normal(page,candidate):
    """No URL construction, API request, list mutation or business VM call.

    Caller sets reopen_at before this single normal page navigation and still
    must independently observe same-ID draft/view with complete saved fields.
    Duplicate physical title links to one identical route are not new records.
    """
    ident=candidate.get('id') if isinstance(candidate,dict) else None
    if not isinstance(ident,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',ident) or not _management(page.url):return False
    client=await page.context.new_cdp_session(page)
    contexts=[]
    created=lambda event:contexts.append(event.get('context',{}))
    try:
        await client.send('Emulation.setFocusEmulationEnabled',{'enabled':False})
        client.on('Runtime.executionContextCreated',created)
        await client.send('Page.enable');tree=await client.send('Page.getFrameTree');root=tree['frameTree']['frame']['id']
        await client.send('Runtime.enable')
        owners=[c['id'] for c in contexts if c.get('auxData',{}).get('isDefault') is True and c.get('auxData',{}).get('frameId')==root]
        if len(owners)!=1:return False
        expression=r'''(()=>{const id=ID,urls=new Set();const a=document.querySelectorAll('a[href]');if(a.length>4096)return null;
          for(const link of a){const r=link.getBoundingClientRect(),s=getComputedStyle(link);if(!link.isConnected||r.width<=0||r.height<=0||s.display==='none'||s.visibility==='hidden')continue;
           try{const u=new URL(link.href);if(u.protocol==='https:'&&u.host==='member.bilibili.com'&&!u.username&&!u.password&&!u.port&&!u.hash&&
            u.pathname==='/platform/upload/video/frame'&&u.searchParams.getAll('type').length===1&&u.searchParams.get('type')==='draft'&&
            u.searchParams.getAll('draftId').length===1&&u.searchParams.get('draftId')===id)urls.add(u.href)}catch(_){}}
          return urls.size===1?[...urls][0]:null;})()'''.replace('ID',json.dumps(ident))
        result=await client.send('Runtime.evaluate',{'expression':expression,'contextId':owners[0],'returnByValue':True})
        href=result.get('result',{}).get('value')
        if not isinstance(href,str) or len(href)>4096:return False
    except Exception:return False
    finally:
        client.remove_listener('Runtime.executionContextCreated',created)
        await client.detach()
    # This is the actual href provided by the normal server-list management UI.
    await page.goto(href,wait_until='domcontentloaded',timeout=30000)
    return True
