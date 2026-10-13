"""Real Electron disk-file drops and frame capture; isolated profile, no account sends."""
import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_embedded_desktop import Fixture, free_port, wait_for
ROOT=Path(__file__).resolve().parent.parent

async def run(app, output):
    from playwright.async_api import async_playwright
    from PIL import Image
    checks={}
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='materials-acceptance-',dir=ROOT/'.draft-workbench') as tmp:
        folder=Path(tmp);profile=folder/'profile';private=folder/'private';private.mkdir()
        assets=folder/'assets';assets.mkdir();video=assets/'拖入测试.mp4'
        subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=320x180:r=20:d=1','-f','lavfi','-i','color=c=blue:s=320x180:r=20:d=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],check=True)
        original=hashlib.sha256(video.read_bytes()).hexdigest()
        title=assets/'标题.txt';title.write_text('\ufeff\n拖入的标题\n第二行不是标题',encoding='utf-8')
        copy=assets/'文案.md';copy.write_text('这里是完整文案。\n#生活 #生活 ＃日常\n第二段正文',encoding='utf-8')
        portrait=assets/'手工竖版.png';Image.new('RGB',(300,400),'green').save(portrait)
        invalid=assets/'坏图片.png';invalid.write_bytes(b'not an image')
        port=free_port();base=f'http://127.0.0.1:{port}'
        env={**os.environ,'SAU_DESKTOP_TEST':'1','SAU_DESKTOP_TEST_URL':f'http://127.0.0.1:{fixture.server_port}',
             'SAU_DESKTOP_PROFILE':str(profile),'SAU_DESKTOP_STATE_DIR':str(private),'SAU_WORKBENCH_PRIVATE':str(private),
             'SAU_WORKBENCH_PORT':str(port),'SAU_EMBEDDED_DESCRIPTOR':str(private/'embedded-browser.json')}
        cmd=[str(app/'Contents/MacOS/DraftWorkbench')]
        log=(folder/'runtime.log').open('w');proc=None;backend_pid=None
        def launch():return subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
        def state():
            with urllib.request.urlopen(base+'/api/state',timeout=3) as r:return json.load(r)
        async def ready():
            if proc.poll() is not None:raise AssertionError((folder/'runtime.log').read_text())
            try:
                status=json.loads((private/'embedded-app-status.json').read_text())
                return status if status['pid']==proc.pid and state()['app']=='sau-draft-workbench' else None
            except (OSError,ValueError,KeyError):return None
        async def endpoint():
            port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version') as r:return json.load(r)['webSocketDebuggerUrl']
        async def drop_file(page,selector,path):
            await page.evaluate("""() => {if(!document.getElementById('diskDropTest')){const i=document.createElement('input');i.type='file';i.id='diskDropTest';i.hidden=true;document.body.append(i)}}""")
            # CDP disk paths preserve native File metadata; Playwright payloads
            # create memory-backed File objects whose native path is empty.
            cdp=await page.context.new_cdp_session(page)
            doc=await cdp.send('DOM.getDocument')
            node=await cdp.send('DOM.querySelector',{'nodeId':doc['root']['nodeId'],'selector':'#diskDropTest'})
            await cdp.send('DOM.setFileInputFiles',{'nodeId':node['nodeId'],'files':[str(path)]})
            await cdp.detach()
            disk_path=await page.evaluate("window.draftDesktop.filePath(document.getElementById('diskDropTest').files[0])")
            assert disk_path==str(path),disk_path
            await page.evaluate("""selector=>{const d=new DataTransfer();d.items.add(document.getElementById('diskDropTest').files[0]);document.querySelector(selector).dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}));}""",selector)
        async def text_drop(page,selector,text):
            await page.evaluate("""({selector,text})=>{const d=new DataTransfer();d.setData('text/plain',text);document.querySelector(selector).dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}));}""",{'selector':selector,'text':text})
        try:
            proc=launch();status=await wait_for(ready);backend_pid=status.get('backendPid')
            async with async_playwright() as p:
                browser=await p.chromium.connect_over_cdp(await endpoint())
                page=next(t for c in browser.contexts for t in c.pages if t.url.startswith(base))
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.locator('.login').first.wait_for()
                shell=next(t for c in browser.contexts for t in c.pages if t.url.endswith('/shell.html'))
                await page.evaluate('document.body.dataset.refreshFixture="old-document"')
                await shell.evaluate('window.draftDesktop.reconnect()')
                await page.wait_for_function('!document.body.dataset.refreshFixture')
                checks['connected_workbench_menu_refreshes_dashboard']=True
                a=await page.locator('#platformPanel').bounding_box();b=await page.locator('#materialPanel').bounding_box()
                assert b['y']>=a['y']+a['height'] and abs(a['x']-b['x'])<1
                checks['platforms_above_materials']=True
                await drop_file(page,'#videoDrop',video)
                await page.wait_for_function("document.querySelector('#video').value.endsWith('拖入测试.mp4')")
                await page.wait_for_function("document.querySelector('#videoPlayer').readyState>=2")
                assert await page.locator('#videoPlayer').evaluate('(v)=>v.duration')==2
                checks['real_disk_file_drop_and_h264_playback']=True
                response=await page.evaluate("async()=>{const r=await fetch(document.querySelector('#videoPlayer').src,{method:'HEAD'});return {status:r.status,ranges:r.headers.get('Accept-Ranges'),length:r.headers.get('Content-Length')}}")
                assert response['status']==200 and response['ranges']=='bytes' and int(response['length'])==video.stat().st_size
                checks['registered_video_stream_head_and_ranges']=True
                await drop_file(page,'[data-text-drop="title"]',title)
                await page.wait_for_function("document.querySelector('#title').value==='拖入的标题'")
                await drop_file(page,'[data-text-drop="description"]',copy)
                await page.wait_for_function("document.querySelector('#description').value.includes('第二段正文')")
                await text_drop(page,'[data-text-drop="title"]','拖动纯文字标题\n第二行')
                assert await page.locator('#title').input_value()=='拖动纯文字标题'
                checks['title_and_copy_disk_and_plain_text_drop']=True
                await page.locator('#tagSuggest').click()
                await page.locator('#tagSuggestions button').first.click()
                await page.locator('#tagInput').fill('#生活#日常，＃日常 #旅行')
                await page.locator('#tagInput').press('Enter')
                assert await page.locator('#tagChips').inner_text() and await page.locator('#tags').input_value()=='#生活 #日常 #旅行'
                checks['hashtag_suggestions_fullwidth_and_dedup']=True
                await drop_file(page,'[data-cover-slot="portrait"]',portrait)
                await page.wait_for_function("document.querySelector('#coverPortrait').value.endsWith('手工竖版.png')")
                await drop_file(page,'[data-cover-slot="common"]',portrait)
                await page.wait_for_function("document.querySelector('#coverPreview').src.includes('手工竖版.png') || decodeURIComponent(document.querySelector('#coverPreview').src).includes('手工竖版.png')")
                await drop_file(page,'[data-cover-slot="portrait"]',invalid)
                await page.wait_for_function("document.querySelector('#notice').textContent.includes('无法读取')")
                assert await page.locator('#coverPortrait').input_value()==str(portrait)
                checks['cover_drop_and_invalid_image_preserves_previous']=True
                await page.locator('#frameTime').fill('0.30');await page.locator('#frameTime').dispatch_event('change')
                await page.wait_for_function("Math.abs(document.querySelector('#videoPlayer').currentTime-.30)<.03")
                await page.locator('#captureRole').select_option('landscape');await page.locator('#captureRatio').select_option('16:9')
                await page.locator('#captureFrame').click()
                await page.wait_for_function("document.querySelector('#coverLandscape').value.includes('frame-covers')")
                landscape=Path(await page.locator('#coverLandscape').input_value())
                with Image.open(landscape) as im:
                    assert im.size==(1280,720)
                    r,g,b=im.getpixel((640,360));assert r>b+100
                assert await page.locator('#coverPortrait').input_value()==str(portrait)
                checks['landscape_at_selected_red_frame_and_portrait_preserved']=True
                await page.evaluate("""() => {
                    window.__coverActionTrace=[];
                    const record=scope=>event=>window.__coverActionTrace.push({
                        scope,type:event.type,phase:event.eventPhase,isTrusted:event.isTrusted,
                        button:event.button,target:event.target===window?'window':event.target?.tagName||event.target?.nodeName||'',
                        defaultPrevented:event.defaultPrevented,menus:document.querySelectorAll('.cover-actions-menu').length,
                        scrollX,scrollY,active:document.activeElement?.tagName||''
                    });
                    for(const type of ['contextmenu','pointerup','scroll','resize']){
                        document.addEventListener(type,record('document-capture'),true);
                        document.addEventListener(type,record('document-bubble'));
                        window.addEventListener(type,record('window-capture'),true);
                        window.addEventListener(type,record('window-bubble'));
                    }
                }""")
                for role in ('common','landscape','portrait'):
                    await page.evaluate('()=>window.__coverActionTrace=[]')
                    await page.locator(f'[data-cover-slot="{role}"] img').click(button='right')
                    await page.wait_for_timeout(50)
                    menu_count=await page.locator('.cover-actions-menu').count()
                    if not menu_count:
                        observed=await page.evaluate("({role})=>({role,module:typeof window.CoverActions,cover:payload()[{common:'cover',landscape:'cover_landscape',portrait:'cover_portrait'}[role]],source:document.querySelector(`[data-cover-slot=\"${role}\"] img`).getAttribute('src'),events:window.__coverActionTrace})", {'role':role})
                        print('COVER_MENU_DIAGNOSTIC='+json.dumps({**observed,'pageerrors':errors},ensure_ascii=False),flush=True)
                    assert menu_count==1, f'{role} native contextmenu did not leave the custom menu open; events={await page.evaluate("()=>window.__coverActionTrace")}, pageerrors={errors}'
                    if role=='common':checks['native_contextmenu_pointerup_scroll_resize_trace']=await page.evaluate('()=>window.__coverActionTrace')
                    await page.locator('.cover-actions-menu [role="menuitem"]').first.click()
                    try:
                        await page.wait_for_function("document.querySelector('#notice').textContent.includes('已复制')",timeout=5000)
                    except Exception:
                        observed=await page.evaluate("({role})=>({role,notice:document.querySelector('#notice').textContent,path:payload()[{common:'cover',landscape:'cover_landscape',portrait:'cover_portrait'}[role]],bridge:typeof window.draftDesktop?.copyCover,events:window.__coverActionTrace})", {'role':role})
                        print('COVER_COPY_DIAGNOSTIC='+json.dumps({**observed,'pageerrors':errors},ensure_ascii=False),flush=True)
                        raise
                checks['all_three_cover_slots_copy_actual_native_image']=True
                await page.locator('[data-cover-slot="landscape"] img').drag_to(page.locator('[data-cover-slot="common"]'))
                await page.wait_for_function("payload().cover===document.querySelector('#coverLandscape').value")
                await drop_file(page,'[data-cover-slot="common"]',portrait)
                checks['internal_cover_drag_uses_registered_path_and_finder_drop_still_works']=True
                await page.get_by_text('平台专用封面与短标题',exact=True).click()
                await page.locator('#override-tencent').check()
                await drop_file(page,'[data-override-cover="tencent:cover_portrait"]',portrait)
                await page.wait_for_function("document.querySelector('#override-tencent-cover_portrait').value.endsWith('手工竖版.png')")
                await page.locator('[data-override-cover="tencent:cover_portrait"] img').click(button='right')
                await page.locator('.cover-actions-menu [role="menuitem"]').first.click()
                await page.wait_for_function("document.querySelector('#notice').textContent.includes('已复制')")
                await page.locator('#override-tencent').uncheck()
                await page.get_by_text('平台专用封面与短标题',exact=True).click()
                checks['platform_override_cover_drop_and_native_copy']=True
                await page.locator('#frameTime').fill('1.50');await page.locator('#frameTime').dispatch_event('change')
                await page.wait_for_function("Math.abs(document.querySelector('#videoPlayer').currentTime-1.5)<.03")
                await page.locator('#captureRole').select_option('portrait');await page.locator('#captureRatio').select_option('3:4')
                await page.locator('#captureFrame').click()
                await page.wait_for_function("document.querySelector('#coverPortrait').value.includes('frame-covers')")
                portrait_frame=Path(await page.locator('#coverPortrait').input_value())
                with Image.open(portrait_frame) as im:
                    assert im.size==(900,1200)
                    r,g,b=im.getpixel((450,600));assert b>r+100
                assert hashlib.sha256(video.read_bytes()).hexdigest()==original
                checks['portrait_at_selected_blue_frame_and_source_unchanged']=True
                for checkbox in await page.locator('[data-platform]').all():
                    if await checkbox.is_enabled():await checkbox.check()
                await page.locator('#precheck').click()
                await page.get_by_text('预检通过 · 保存前还会现场检查平台',exact=True).wait_for()
                assert await page.locator('#preview .mapping').count()==5
                mappings=await page.locator('#preview').inner_text()
                assert '#生活 #日常 #旅行' in mappings
                # Dedicated covers supersede the common fallback in platform mappings.
                assert landscape.name in mappings and portrait_frame.name in mappings
                assert await page.locator('#coverPreview').get_attribute('src')=='/asset?path='+urllib.parse.quote(str(portrait),safe='')
                checks['all_five_platform_tag_and_cover_mapping']=True
                await page.get_by_text('平台专用封面与短标题',exact=True).click()
                await page.locator('#override-bilibili').check()
                title_widget=page.locator('[data-override-field="title"]')
                await title_widget.locator('.field-override-toggle').click()
                await title_widget.get_by_role('button',name='B站',exact=True).click()
                await title_widget.get_by_role('button',name='B站 · 单独设置',exact=True).click()
                await title_widget.get_by_label('平台独立标题').fill('平台独立标题')
                await title_widget.locator('.field-override-toggle').click()
                await drop_file(page,'[data-override-cover="bilibili:cover_landscape"]',landscape)
                await page.wait_for_function("document.querySelector('#override-bilibili-cover_landscape').value.includes('frame-covers')")
                await page.locator('#precheck').click()
                await page.get_by_text('预检通过 · 保存前还会现场检查平台',exact=True).wait_for()
                assert '平台独立标题' in await page.locator('#preview').inner_text()
                checks['platform_override_cover_also_uses_disk_file_drop']=True
                desc_widget=page.locator('[data-override-field="description"]')
                await desc_widget.locator('.field-override-toggle').click()
                await desc_widget.get_by_role('button',name='小红书',exact=True).click()
                await desc_widget.get_by_role('button',name='小红书 · 单独设置',exact=True).click()
                await desc_widget.get_by_label('平台独立文案').fill('小红书独立正文')
                await desc_widget.locator('.field-override-toggle').click()
                tags_widget=page.locator('[data-override-field="tags"]')
                await tags_widget.locator('.field-override-toggle').click()
                await tags_widget.get_by_role('button',name='抖音',exact=True).click()
                await tags_widget.get_by_role('button',name='抖音 · 单独设置',exact=True).click()
                while await tags_widget.locator('.tag-chip').count():await tags_widget.locator('.tag-chip').first.click()
                await tags_widget.get_by_label('平台独立标签').fill('#独特主题')
                await tags_widget.get_by_label('平台独立标签').press('Enter')
                await tags_widget.locator('.field-override-toggle').click()
                await page.locator('#description').fill('更新统一文案')
                await page.evaluate('window.flushWorkbenchEditor()')
                overrides=state()['editor']['payload']['platform_overrides']
                assert overrides['douyin']=={'tags':['独特主题']},overrides
                assert overrides['xiaohongshu']=={'description':'小红书独立正文'},overrides
                assert overrides['bilibili']['title']=='平台独立标题'
                await page.locator('#precheck').click()
                await page.get_by_text('预检通过 · 保存前还会现场检查平台',exact=True).wait_for()
                current=await page.locator('#preview').inner_text()
                assert '小红书独立正文' in current and '#独特主题' in current and '更新统一文案' in current
                checks['field_only_overrides_inherit_other_global_fields']=True
                boxes=await page.locator('[data-cover-slot]').evaluate_all('els=>Object.fromEntries(els.map(e=>{const r=e.getBoundingClientRect();const i=e.querySelector("img");return [e.dataset.coverSlot,{x:r.x,y:r.y,w:r.width,h:r.height,fit:getComputedStyle(i).objectFit,position:getComputedStyle(i).position}]}))')
                assert boxes['common']['y']<boxes['landscape']['y'] and boxes['common']['x']==boxes['landscape']['x']
                assert boxes['portrait']['x']>boxes['common']['x'] and boxes['portrait']['h']>boxes['common']['h']
                assert all(v['fit']=='contain' and v['position']=='absolute' for v in boxes.values()),boxes
                checks['complete_cover_left_stack_and_tall_right_layout']=boxes
                # Limits block locally without scheduling a platform job.
                await page.locator('#description').fill('文'*1001)
                await page.wait_for_function('document.querySelector("#textLimitIssues").classList.contains("limit-error")')
                assert await page.locator('#save').is_enabled()
                assert await page.locator('#uploadIssues').is_visible()
                assert '快手' in await page.locator('#textLimitIssues').inner_text()
                await page.locator('#save').click()
                await page.wait_for_function('!document.querySelector("#save").disabled && document.querySelector("#notice").textContent.includes("预检未通过")')
                assert state().get('job') is None and not state().get('history')
                await page.locator('#description').fill('更新统一文案')
                await page.wait_for_function('!document.querySelector("#textLimitIssues").classList.contains("limit-error")')
                checks['live_platform_limits_block_before_any_upload']=True
                # A checked platform with fewer topic slots must have a direct,
                # non-destructive repair rather than a permanently grey entry.
                await page.locator('#tagInput').fill('#科普 #记录');await page.locator('#tagInput').press('Enter')
                await page.get_by_role('button',name='设置快手独立话题',exact=True).wait_for()
                assert await page.locator('#save').is_enabled()
                await page.locator('#save').click()
                await page.locator('#tagChoiceDialog').wait_for(state='visible')
                assert await page.locator('#tagChoiceApply').is_disabled()
                before_topics=state()['editor']['payload']['tags']
                await page.locator('#tagChoiceCancel').click()
                assert 'kuaishou' not in state()['editor']['payload']['platform_overrides']
                await page.get_by_role('button',name='设置快手独立话题',exact=True).click()
                await page.get_by_role('checkbox',name='保留话题 #记录',exact=True).uncheck()
                assert await page.locator('#tagChoiceApply').is_enabled()
                await page.locator('#tagChoiceApply').click()
                await page.wait_for_function('document.querySelector("#uploadIssues").hidden')
                await page.evaluate('window.flushWorkbenchEditor()')
                adapted=state()['editor']['payload']
                assert adapted['tags']==before_topics
                assert adapted['platform_overrides']['kuaishou']=={'tags':['生活','日常','旅行','科普']}
                assert adapted['platform_overrides']['douyin']=={'tags':['独特主题']}
                assert adapted['platform_overrides']['xiaohongshu']=={'description':'小红书独立正文'}
                assert state().get('job') is None and not state().get('history')
                await page.reload()
                await page.wait_for_function('document.querySelector("#tags").value.includes("#记录")')
                assert state()['editor']['payload']['platform_overrides']['kuaishou']['tags']==['生活','日常','旅行','科普']
                assert await page.locator('#save').is_enabled()
                # Restore just the fixture common tags for the existing restart assertions.
                await page.get_by_role('button',name='删除标签 #科普',exact=True).click()
                await page.get_by_role('button',name='删除标签 #记录',exact=True).click()
                checks['kuaishou_topic_repair_cancel_confirm_persistence_and_no_upload']=True
                await page.get_by_text('平台专用封面与短标题',exact=True).click()
                assert not (state().get('job') or {}).get('active') and not state().get('history')
                checks['no_platform_job_or_upload_triggered']=True
                await page.evaluate('window.flushWorkbenchEditor()')
                saved=state()['editor']
                screenshot=output.with_suffix('.png');await page.screenshot(path=str(screenshot),full_page=True,timeout=15000)
                checks['screenshot']=str(screenshot)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'horizontal overflow'
                assert not errors,errors
                checks['no_js_error_or_horizontal_overflow']=True
                # Graceful exit/restart tests actual disk persistence and asset registry recovery.
                os.kill(proc.pid,signal.SIGTERM);await asyncio.to_thread(proc.wait,20)
                proc=launch();await wait_for(ready)
                browser2=await p.chromium.connect_over_cdp(await endpoint())
                page2=next(t for c in browser2.contexts for t in c.pages if t.url.startswith(base))
                await page2.locator('.login').first.wait_for()
                await page2.wait_for_function("document.querySelector('#videoPlayer').readyState>=2")
                assert state()['editor']['payload']==saved['payload']
                assert state()['editor']['platforms']==saved['platforms']
                assert await page2.locator('#title').input_value()=='拖动纯文字标题'
                assert await page2.locator('#coverLandscape').input_value()==str(landscape)
                assert await page2.locator('#tags').input_value()=='#生活 #日常 #旅行'
                checks['restart_restores_media_covers_text_tags_and_selection']=True
        finally:
            if proc and proc.poll() is None:
                os.kill(proc.pid,signal.SIGTERM)
                try:proc.wait(20)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            try:
                result=subprocess.run(['/usr/sbin/lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True)
                for pid in result.stdout.split():
                    command=subprocess.check_output(['/bin/ps','-p',pid,'-o','command='],text=True).strip()
                    if command==str(ROOT/'.venv/bin/python')+' -u '+str(ROOT/'draft_workbench.py'):
                        os.kill(int(pid),signal.SIGTERM)
            except (OSError,ValueError):pass
            log.close();fixture.shutdown();fixture.server_close()
    result={'status':'passed','checks':checks,'real_account_login':'not_tested','real_platform_draft':'not_tested'}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--app',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();asyncio.run(run(args.app,args.out))
