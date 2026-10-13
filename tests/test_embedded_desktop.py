"""Installed/candidate Electron integration; isolated profiles, synthetic media, no real accounts.

Run: .venv/bin/python tests/test_embedded_desktop.py --app /absolute/path/to/视频草稿工作台.app
"""
import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HTML = '''<!doctype html><meta charset="utf-8"><title>内嵌浏览器测试</title>
<body data-published="0" data-video=""><span data-account-id="fixture-owner">隔离创作者</span><button class="_upload-btn" type="button" onclick="document.querySelector('#video').click()">上传视频</button>
<input id="video" type="file" accept="video/mp4" onchange="document.body.dataset.video=this.files[0].name;document.querySelector('#uploadStatus').textContent='上传成功'">
<span id="uploadStatus"></span>
<span>描述</span><div id="description" contenteditable="true"></div>
<div><span>封面设置</span></div><div><div onclick="document.querySelector('#coverModal').style.display='block'">设置封面</div></div>
<div id="coverModal" role="document" class="ant-modal" style="display:none"><span>上传封面</span><input type="file" accept="image/jpeg" onchange="document.body.dataset.cover=this.files[0]?.name||''"><button onclick="document.querySelector('#coverModal').style.display='none'">确认</button></div>
<button onclick="document.body.dataset.published=Number(document.body.dataset.published)+1">发布</button>
<button onclick="document.querySelector('#receipt').textContent='草稿保存成功'">保存草稿</button><div id="receipt"></div>
<button id="popup" onclick="window.open('/auth-child','login-child')">登录子页</button>
</body>'''

class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        html = HTML if 'xiaohongshu' not in self.path else '<meta charset="utf-8"><div class="login-box">扫码登录</div><button id="popup" onclick="window.open(\'/auth-child\',\'login-child\')">登录子页</button>'
        if self.path == '/auth-child': html = '<meta charset="utf-8"><button onclick="window.close()">完成验证</button>'
        data = html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]

async def wait_for(predicate, timeout=30):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        result = await predicate()
        if result: return result
        await asyncio.sleep(.2)
    raise AssertionError('Integration wait timed out')

async def run(app_path, output):
    from playwright.async_api import async_playwright
    checks = {}
    fixture = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    temp = tempfile.TemporaryDirectory(prefix='embedded-acceptance-', dir=ROOT/'.draft-workbench')
    folder = Path(temp.name); profile = folder/'profile'; private = folder/'private'; private.mkdir()
    backend_port = free_port(); base = f'http://127.0.0.1:{backend_port}'
    env = {**os.environ, 'SAU_DESKTOP_TEST':'1', 'SAU_DESKTOP_TEST_URL':f'http://127.0.0.1:{fixture.server_port}',
           'SAU_DESKTOP_PROFILE':str(profile), 'SAU_DESKTOP_STATE_DIR':str(private), 'SAU_WORKBENCH_PRIVATE':str(private),
           'SAU_WORKBENCH_PORT':str(backend_port), 'SAU_EMBEDDED_DESCRIPTOR':str(private/'embedded-browser.json')}
    if app_path:
        executable = app_path/'Contents/MacOS/DraftWorkbench'; command = [str(executable)]
    else:
        executable = ROOT/'desktop/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'; command = [str(executable), str(ROOT/'desktop')]
    app_process = None
    backend_pid = None
    log = (folder/'electron.log').open('w')
    def launch(): return subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
    def state():
        with urllib.request.urlopen(base+'/api/state?embedded=1', timeout=2) as response:return json.load(response)
    def bridge(action, data):
        descriptor=json.loads((private/'embedded-browser.json').read_text())
        request=urllib.request.Request(descriptor['url']+action,data=json.dumps(data).encode(),
            headers={'Content-Type':'application/json','X-Embedded-Token':descriptor['token']},method='POST')
        with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)
    async def app_ready():
        if app_process.poll() is not None: raise AssertionError('App exited: '+(folder/'electron.log').read_text())
        try:
            data = json.loads((private/'embedded-app-status.json').read_text())
            return data if data['pid']==app_process.pid and state().get('embedded_supported') else None
        except (OSError, KeyError, ValueError): return None
    async def endpoint():
        port = (profile/'DevToolsActivePort').read_text().splitlines()[0]
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version') as response:return json.load(response)['webSocketDebuggerUrl']
    last_job=None
    observed_job=None
    async def completed():
        nonlocal last_job, observed_job
        try:
            s=state()
        except (OSError, TimeoutError):
            return None
        job=s.get('job') or {}
        observed=(job.get('id'),job.get('active'),tuple(i.get('status') for i in job.get('results',[])),len(job.get('logs',[])))
        if observed!=observed_job:
            print(time.strftime('%H:%M:%S'),job.get('action'),observed[1:],flush=True)
            observed_job=observed
        if s.get('job') and not s['job']['active'] and s['job']['id']!=last_job:
            last_job=s['job']['id'];return s
        return None
    try:
        app_process=launch(); ready=await wait_for(app_ready)
        checks['actual_executable']=str(executable)
        checks['installed_version']=ready['version']
        async with async_playwright() as p:
            browser=await p.chromium.connect_over_cdp(await endpoint())
            shell=next(page for c in browser.contexts for page in c.pages if page.url.endswith('/shell.html'))
            dashboard=next(page for c in browser.contexts for page in c.pages if page.url.startswith(base))
            await dashboard.locator('.login').first.wait_for()
            assert await dashboard.locator('.login').count()==7
            assert await shell.locator('.nav[data-platform]').count()==7
            assert await dashboard.locator('input[data-platform="toutiao"]').is_checked() is False
            assert await dashboard.locator('input[data-platform="toutiao"]').is_disabled()
            assert await dashboard.locator('input[data-platform="xigua"]').is_checked() is False
            assert await dashboard.locator('input[data-platform="xigua"]').is_disabled()
            assert await dashboard.get_by_role('button',name='打开平台',exact=True).count()==2
            assert await dashboard.locator('.platform:has(input[data-platform="toutiao"]) .icon[data-brand="toutiao"]').count()==1
            assert await dashboard.locator('.platform:has(input[data-platform="xigua"]) .icon[data-brand="xigua"]').count()==1
            checks['seven_platforms_inside_app_and_new_platforms_unselected']=True
            await dashboard.locator('.login[data-id="toutiao"]').click()
            async def toutiao_ready():return next((page for c in browser.contexts for page in c.pages if page.url.endswith('/toutiao')),None)
            toutiao=await wait_for(toutiao_ready)
            assert state().get('job') is None
            await toutiao.evaluate("document.cookie='session_test=toutiao;max-age=86400;path=/';localStorage.setItem('session_test','toutiao')")
            await shell.locator('.nav[data-platform="xigua"]').click()
            async def xigua_ready():return next((page for c in browser.contexts for page in c.pages if page.url.endswith('/xigua')),None)
            xigua=await wait_for(xigua_ready)
            assert await xigua.evaluate("document.cookie.includes('session_test')")==False
            assert await xigua.evaluate("localStorage.getItem('session_test')") is None
            await shell.locator('.nav[data-platform="toutiao"]').click()
            assert await toutiao.evaluate("document.cookie.includes('session_test=toutiao')")
            assert await toutiao.evaluate("localStorage.getItem('session_test')")=='toutiao'
            checks['new_platform_sessions_are_persistent_and_isolated']=True
            await dashboard.locator('.login[data-id="douyin"]').click()
            s=await wait_for(completed,90)
            assert s['job']['results'][0]['status']=='login_verified',s['job']
            douyin=next(page for c in browser.contexts for page in c.pages if page.url.endswith('/douyin'))
            await douyin.evaluate("document.cookie='session_test=douyin;max-age=86400;path=/';localStorage.setItem('session_test','douyin')")
            await douyin.evaluate("""() => new Promise((resolve,reject)=>{const r=indexedDB.open('session-test',1);r.onupgradeneeded=()=>r.result.createObjectStore('data');r.onsuccess=()=>{const db=r.result;const t=db.transaction('data','readwrite');t.objectStore('data').put('douyin','platform');t.oncomplete=()=>{db.close();resolve(true)};t.onerror=reject;};r.onerror=reject;})""")
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            await dashboard.locator('.login[data-id="kuaishou"]').filter(has_text='登录').click()
            s=await wait_for(completed,90)
            assert s['job']['results'][0]['status']=='login_verified',s['job']
            kuaishou=next(page for c in browser.contexts for page in c.pages if page.url.endswith('/kuaishou'))
            assert await kuaishou.evaluate("document.cookie.includes('session_test')")==False
            assert await kuaishou.evaluate("localStorage.getItem('session_test')") is None
            checks['same_origin_platform_sessions_isolated']=True
            await kuaishou.get_by_role('button',name='发布',exact=True).click()
            assert await kuaishou.locator('body').get_attribute('data-published')=='0'
            checks['native_publish_guard']=True
            # Popup stays inside the native window and shares only the opener's partition.
            await kuaishou.locator('#popup').click()
            async def popup_ready(): return await shell.evaluate('window.draftDesktop.state().then(s=>s.popup)')
            await wait_for(popup_ready)
            child=next(page for c in browser.contexts for page in c.pages if page.url.endswith('/auth-child'))
            assert await child.evaluate('!!window.opener')
            print('Popup viewport',await child.evaluate('({width:innerWidth,height:innerHeight})'),flush=True)
            await child.get_by_role('button',name='完成验证').click()
            async def popup_closed():return not await shell.evaluate('window.draftDesktop.state().then(s=>s.popup)')
            await wait_for(popup_closed)
            checks['oauth_popup_inside_same_window_with_opener']=True
            # This bare HTML fixture lacks the current platform's native media
            # consumer. The worker must reject it before selecting any file.
            await kuaishou.route('https://cp.kuaishou.com/**',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=HTML))
            import cv2,numpy as np
            media=folder/'materials';media.mkdir()
            video=media/'synthetic.mp4'
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),10,(64,64))
            for _ in range(10):writer.write(np.zeros((64,64,3),dtype=np.uint8))
            writer.release()
            (media/'publish.json').write_text(json.dumps({'title':'隔离草稿测试','description':'仅用于本地内嵌浏览器验证'},ensure_ascii=False))
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            await dashboard.get_by_text('粘贴文件夹或视频路径',exact=True).click()
            await dashboard.locator('#path').fill(str(media));await dashboard.locator('#load').click()
            await dashboard.locator('#title').wait_for()
            async def imported():return await dashboard.locator('#title').input_value()=='隔离草稿测试'
            await wait_for(imported)
            async def cover_ready():
                try:
                    cover_path=state().get('editor',{}).get('payload',{}).get('cover')
                except OSError:
                    return None
                return cover_path if cover_path and Path(cover_path).is_file() else None
            generated_cover=await wait_for(cover_ready)
            await dashboard.locator('input[data-platform="kuaishou"]').check()
            await dashboard.locator('#save').click()
            # Native-composer readiness follows the session preflight and must
            # fail closed rather than treating bare HTML as an upload component.
            s=await wait_for(completed,180)
            assert s['job']['action']=='draft' and s['job']['results'][0]['status']=='result_unknown',s['job']
            observed_progress=s['job']['results'][0]['progress']
            assert observed_progress['stage']=='video',observed_progress
            assert observed_progress['state']=='unknown',observed_progress
            assert observed_progress['completed']==['session'],observed_progress
            assert 'save' not in observed_progress['completed'] and 'verify' not in observed_progress['completed']
            diagnostics=[json.loads(line.split('=',1)[1]) for line in s['job']['logs'] if line.startswith('DRAFT_DIAGNOSTIC=')]
            checkpoint=next(item for item in diagnostics if item.get('event')=='upload_checkpoint_unavailable')
            assert checkpoint['selected_files']==0 and checkpoint['upload_exchanges']==0,checkpoint
            assert await kuaishou.locator('body').get_attribute('data-video')==''
            assert await kuaishou.locator('#description').inner_text()==''
            assert await kuaishou.locator('body').get_attribute('data-cover') is None
            assert await kuaishou.locator('body').get_attribute('data-published')=='0'
            assert not kuaishou.is_closed() and browser.is_connected()
            checks['worker_rejects_unbound_native_composer']=True
            checks['draft_completion_retains_platform_page']=True
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            await dashboard.locator('#save').click()
            s=await wait_for(completed)
            assert s['job']['results'][0].get('reused') is True,s['job']
            checks['unresolved_job_does_not_reupload']=True
            # Only an explicit, platform-scoped recovery action may start a new
            # attempt. Opening/cancelling its dialog has no upload side effect.
            previous=s['job']['id']
            assert s['job']['results'][0]['retryable'] is True
            await dashboard.get_by_role('button',name='重新上传快手',exact=True).click()
            await dashboard.locator('#retryDialog').wait_for(state='visible')
            assert state()['job']['id']==previous and not state()['job']['active']
            await dashboard.locator('#retryCancel').click()
            assert state()['job']['id']==previous
            await dashboard.get_by_role('button',name='重新上传快手',exact=True).click()
            await dashboard.locator('#retryConfirm').click()
            async def retry_started():
                j=state()['job']
                return j if j['id']!=previous and j['active'] else None
            recovered=await wait_for(retry_started)
            assert recovered['results'][0]['recovery_of']
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            await dashboard.locator('#cancel').click()
            await wait_for(completed)
            checks['explicit_recovery_dialog_cancel_and_one_platform_new_attempt']=True
            # A draft lease still locks other platforms, but its own page remains reachable
            # after returning to the workbench. Ending a login wait must not cancel a draft.
            lease=await asyncio.to_thread(bridge,'/prepare',{'platform':'kuaishou','action':'draft'})
            try:
                await shell.get_by_role('button',name='素材工作台',exact=False).click()
                assert not await shell.locator('.nav[data-platform="kuaishou"]').is_disabled()
                for id in ('douyin','xiaohongshu','tencent','bilibili'):
                    assert await shell.locator('.nav[data-platform="'+id+'"]').is_disabled()
                await shell.locator('.nav[data-platform="kuaishou"]').click()
                assert await shell.evaluate('window.draftDesktop.state().then(s=>s.selected)')=='kuaishou'
                rejected=await shell.evaluate("window.draftDesktop.select('douyin').then(()=>false,()=>true)")
                assert rejected
                rejected=await shell.evaluate('window.draftDesktop.stopLogin().then(()=>false,()=>true)')
                assert rejected
                assert await shell.evaluate('window.draftDesktop.state().then(s=>s.activeAction)')=='draft'
                checks['draft_lock_retains_return_path_and_rejects_login_cancel']=True
            finally:
                await asyncio.to_thread(bridge,'/finished',{'lease':lease['lease']})
            # A pending login can be stopped without losing the browser or getting a stuck lease.
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            await dashboard.locator('.login[data-id="xiaohongshu"]').click()
            async def active():return await shell.evaluate('window.draftDesktop.state().then(s=>s.active)')
            await wait_for(active)
            pending_login_id=state()['job']['id']
            async def xhs_ready():return next((page for c in browser.contexts for page in c.pages if page.url.endswith('/xiaohongshu')),None)
            xhs=await wait_for(xhs_ready)
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            for id in ('douyin','xiaohongshu','tencent','bilibili','kuaishou'):
                assert not await shell.locator('.nav[data-platform="'+id+'"]').is_disabled()
            await shell.locator('.nav[data-platform="xiaohongshu"]').click()
            assert await shell.evaluate('window.draftDesktop.state().then(s=>s.activePlatform)')=='xiaohongshu'
            await shell.locator('.nav[data-platform="douyin"]').click()
            assert await douyin.evaluate("localStorage.getItem('session_test')")=='douyin'
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            await dashboard.locator('.login[data-id="tencent"]').click()
            async def tencent_selected():return await shell.evaluate("window.draftDesktop.state().then(s=>s.selected==='tencent')")
            await wait_for(tencent_selected)
            assert state()['job']['id']==pending_login_id and state()['job']['active']
            checks['pending_login_allows_workbench_platform_and_login_card_navigation']=True
            # A background login popup must not replace the page the user switched to.
            await xhs.locator('#popup').click(force=True)
            async def background_child():return next((page for c in browser.contexts for page in c.pages if page.url.endswith('/auth-child')),None)
            child=await wait_for(background_child)
            await child.wait_for_load_state('domcontentloaded')
            shell_state=await shell.evaluate('window.draftDesktop.state()')
            assert shell_state['selected']=='tencent' and not shell_state['popup']
            assert await shell.locator('#closePopup').is_hidden()
            assert shell_state['viewVisibility']['platforms']['tencent']
            assert not any(p['visible'] for p in shell_state['viewVisibility']['popups'])
            tencent=next(page for c in browser.contexts for page in c.pages if page.url.endswith('/tencent'))
            existing_pages=[page for c in browser.contexts for page in c.pages]
            await tencent.locator('#popup').click()
            async def tencent_child_ready():return next((page for c in browser.contexts for page in c.pages if page not in existing_pages and page.url.endswith('/auth-child')),None)
            tencent_child=await wait_for(tencent_child_ready)
            await tencent_child.wait_for_load_state('domcontentloaded')
            shell_state=await shell.evaluate('window.draftDesktop.state()')
            assert {p['platform']:p['visible'] for p in shell_state['viewVisibility']['popups']}=={'xiaohongshu':False,'tencent':True}
            await shell.locator('.nav[data-platform="xiaohongshu"]').click()
            shell_state=await shell.evaluate('window.draftDesktop.state()')
            assert shell_state['popup']
            assert {p['platform']:p['visible'] for p in shell_state['viewVisibility']['popups']}=={'xiaohongshu':True,'tencent':False}
            await shell.get_by_role('button',name='素材工作台',exact=False).click()
            shell_state=await shell.evaluate('window.draftDesktop.state()')
            assert not shell_state['popup'] and shell_state['viewVisibility']['workbench']
            assert not any(p['visible'] for p in shell_state['viewVisibility']['popups'])
            await shell.locator('.nav[data-platform="xiaohongshu"]').click()
            await shell.locator('#closePopup').click()
            await wait_for(popup_closed)
            assert child.is_closed() and not tencent_child.is_closed()
            await shell.locator('.nav[data-platform="tencent"]').click()
            shell_state=await shell.evaluate('window.draftDesktop.state()')
            assert shell_state['popup'] and shell_state['viewVisibility']['popups']==[{'platform':'tencent','visible':True}]
            await shell.locator('#closePopup').click();await wait_for(popup_closed)
            assert tencent_child.is_closed()
            await shell.locator('.nav[data-platform="xiaohongshu"]').click()
            checks['two_login_popups_native_visibility_and_close_follow_selected_platform']=True
            # Stop directly from a platform page, retaining the same page and session.
            await shell.locator('#stopLogin').click()
            s=await wait_for(completed)
            assert s['job']['cancelled'] and s['job']['results'][0]['status']=='cancelled'
            assert not await shell.evaluate('window.draftDesktop.state().then(s=>s.active)')
            assert browser.is_connected()
            checks['cancel_releases_lease_without_closing_browser']=True
        app_process.terminate();await asyncio.to_thread(app_process.wait,10)
        # Restart the same isolated profile. Cookies/localStorage/IndexedDB should remain.
        app_process=launch();await wait_for(app_ready)
        async with async_playwright() as p:
            browser=await p.chromium.connect_over_cdp(await endpoint())
            shell=next(page for c in browser.contexts for page in c.pages if page.url.endswith('/shell.html'))
            await shell.get_by_role('button',name='抖音',exact=False).click()
            async def platform():return next((page for c in browser.contexts for page in c.pages if page.url.endswith('/douyin')),None)
            douyin=await wait_for(platform)
            await douyin.wait_for_load_state('domcontentloaded')
            assert await douyin.evaluate("document.cookie.includes('session_test=douyin')")
            assert await douyin.evaluate("localStorage.getItem('session_test')")=='douyin'
            value=await douyin.evaluate("""() => new Promise((resolve,reject)=>{const r=indexedDB.open('session-test',1);r.onsuccess=()=>{const db=r.result;const t=db.transaction('data');const q=t.objectStore('data').get('platform');q.onsuccess=()=>{resolve(q.result);db.close()};q.onerror=reject;};r.onerror=reject;})""")
            assert value=='douyin'
            checks['cookies_localstorage_indexeddb_survive_app_restart']=True
        checks['status']='passed';checks['real_platform_draft']='not_tested';checks['date']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
        output.write_text(json.dumps(checks,ensure_ascii=False,indent=2));print(json.dumps(checks,ensure_ascii=False,indent=2),flush=True)
    except Exception as exc:
        checks.update(status='failed',error=str(exc),real_platform_draft='not_tested')
        try:
            current=state()
            checks['job']=current.get('job')
        except Exception:
            pass
        checks['electron_log']=(folder/'electron.log').read_text()[-6000:]
        output.write_text(json.dumps(checks,ensure_ascii=False,indent=2))
        raise
    finally:
        try:
            current=state()
            if current.get('job',{}).get('active'):
                request=urllib.request.Request(base+'/api/cancel',data=b'{}',headers={
                    'Content-Type':'application/json','X-Workbench-Token':current['token']},method='POST')
                await asyncio.to_thread(urllib.request.urlopen,request,timeout=5)
        except Exception:
            pass
        if app_process and app_process.poll() is None:
            app_process.terminate()
            try:await asyncio.to_thread(app_process.wait,10)
            except subprocess.TimeoutExpired:
                os.killpg(app_process.pid,signal.SIGKILL);await asyncio.to_thread(app_process.wait,5)
        # Only our isolated backend has this unique port/profile environment.
        try:
            result=subprocess.check_output(['/usr/sbin/lsof','-t',f'-iTCP:{backend_port}','-sTCP:LISTEN'],text=True)
            for pid in result.split():os.kill(int(pid),signal.SIGTERM)
        except (subprocess.CalledProcessError,ProcessLookupError):pass
        log.close();fixture.shutdown();temp.cleanup()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path)
    parser.add_argument('--out',type=Path,default=ROOT/'.draft-workbench/embedded-desktop-verification.json')
    args=parser.parse_args();asyncio.run(run(args.app,args.out))
