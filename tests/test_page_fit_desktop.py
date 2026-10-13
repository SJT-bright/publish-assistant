"""Native Electron page-fit acceptance with local fixtures and isolated profiles.

By default stages current desktop sources; --app accepts a built candidate.
No production profile, backend, account, material or platform is opened.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent


class Fixture(BaseHTTPRequestHandler):
    envelope = {"available": True, "job": None}

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/api/state'):
            data = {"app": "sau-draft-workbench", "embedded_supported": True,
                    "api_version": 2, "job": {"active": False}}
        elif self.path == '/api/progress':
            data = Fixture.envelope
        else:
            body = '''<!doctype html><meta charset="utf-8"><title>Isolated page-fit fixture</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f0f3f8;font:20px -apple-system}
.tc{height:940px;width:723px;position:absolute;top:10px;left:50%;transform:translateX(-50%);background:white;border:1px solid #ddd;padding:24px}
.tc footer{position:absolute;bottom:20px;left:24px;right:24px;display:flex;justify-content:flex-end}
.bili{position:relative;min-width:1280px;height:820px;padding:24px;background:white}
#continueEditing{position:absolute;right:24px;top:90px}
button,input{font:inherit;padding:12px;border-radius:10px;border:1px solid #ccd6e5;background:#f3f7ff}
</style><script>
document.addEventListener('click',event=>{const events=JSON.parse(document.body.dataset.clickEvents||'[]');
 events.push({target:event.target.id,clientX:event.clientX,clientY:event.clientY,trusted:event.isTrusted});
 document.body.dataset.clickEvents=JSON.stringify(events);},true);
function recordButton(event){const key=event.currentTarget.id;
 const counts=JSON.parse(document.body.dataset.buttonCounts||'{}');counts[key]=(counts[key]||0)+1;
 document.body.dataset.buttonCounts=JSON.stringify(counts);}
</script>'''
            if self.path.startswith('/tencent'):
                body += '<article class="tc"><h1>视频号封面模拟弹窗</h1><input id="editor" aria-label="模拟编辑"><button onclick="window.open(\'/popup\')" id="openPopup">打开子页</button><footer><button id="confirmCover" onclick="recordButton(event)">确认封面</button></footer></article>'
            elif self.path.startswith('/bilibili'):
                body += '<article class="bili"><h1>B站宽页面模拟</h1><input id="editor" aria-label="模拟编辑"><button id="continueEditing" onclick="recordButton(event)">继续编辑</button><button id="publish" onclick="recordButton(event)">发布</button><button id="saveDraft" onclick="recordButton(event)">保存草稿</button></article>'
            elif self.path == '/douyin-frame':
                body += '<article><button id="framePublish" onclick="recordButton(event)">发布</button></article>'
            elif self.path.startswith('/douyin'):
                body += '<article><h1>抖音本机隔离模拟页</h1><button id="publish" onclick="recordButton(event)">发布</button><button id="saveDraft" onclick="recordButton(event)">暂存离开</button><iframe id="hiddenFrame" title="隐藏平台子页" src="/douyin-frame" style="display:none"></iframe></article>'
            else:
                body += '<article><h1>本机隔离模拟页面</h1><input id="editor" aria-label="模拟编辑"></article>'
            self.respond(body.encode(), 'text/html; charset=utf-8')
            return
        self.respond(json.dumps(data).encode(), 'application/json')

    def respond(self, body, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def wait_for(check, seconds=15):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(.1)
    raise AssertionError('Acceptance condition timed out')


async def contained(page, selector):
    return await page.locator(selector).evaluate('''e=>{const b=e.getBoundingClientRect();
      return b.left>=0&&b.top>=0&&b.right<=innerWidth&&b.bottom<=innerHeight;}''')


async def click_probe(page, selector, factor, method):
    control = page.locator(selector)
    await control.scroll_into_view_if_needed()
    box = await control.bounding_box()
    assert box is not None
    dom = await control.evaluate('''e=>{const b=e.getBoundingClientRect();return {
      x:b.x,y:b.y,width:b.width,height:b.height,viewport:[innerWidth,innerHeight],
      pixelRatio:devicePixelRatio,scroll:[scrollX,scrollY]};}''')
    before = await page.evaluate("({counts:JSON.parse(document.body.dataset.buttonCounts||'{}'),events:JSON.parse(document.body.dataset.clickEvents||'[]')})")
    result = {'method': method, 'factor': factor, 'bbox': box, 'dom': dom}
    try:
        if method == 'locator':
            await control.click(timeout=2000)
        else:
            multiplier = factor if method == 'mouse_scaled' else 1
            result['dispatch_point'] = {'x': (box['x']+box['width']/2)*multiplier,
                                        'y': (box['y']+box['height']/2)*multiplier}
            await page.mouse.click(**result['dispatch_point'])
        result['call'] = 'returned'
    except Exception as error:
        result['call'] = 'failed'
        result['error'] = str(error)[:500]
    await asyncio.sleep(.05)
    after = await page.evaluate("({counts:JSON.parse(document.body.dataset.buttonCounts||'{}'),events:JSON.parse(document.body.dataset.clickEvents||'[]')})")
    key = selector.removeprefix('#')
    result['button_count_delta'] = after['counts'].get(key, 0)-before['counts'].get(key, 0)
    result['events'] = after['events'][len(before['events']):]
    result['trusted_button_clicked'] = result['button_count_delta'] == 1 and any(
        event['target'] == key and event['trusted'] for event in result['events'])
    return result


def draft_job():
    now = time.time()
    return {"id": "isolated-fit-job", "action": "draft", "active": True,
            "platform": "tencent", "platform_index": 1, "platform_count": 2,
            "status": "running", "message": "本机模拟步骤", "progress": {
                "stage": "cover", "state": "running", "started_at": now,
                "updated_at": now, "completed": ['session', 'video', 'upload', 'content'], "skipped": []}}


def resize_isolated_window(inspector, width=None, height=None):
    # CDP's Browser.setWindowBounds is not implemented by Electron. Use the
    # explicitly launched isolated process's local Node inspector instead;
    # no production IPC or app permission is introduced for this test.
    script = r'''
const socket=new WebSocket(process.argv[1]);
const timer=setTimeout(()=>{console.error('Native resize timed out');process.exit(1);},5000);
socket.onopen=()=>socket.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{
  expression:`(()=>{const electron=process.mainModule?process.mainModule.require('electron'):
    process.getBuiltinModule('module').createRequire(process.cwd()+'/desktop/main.cjs')('electron');
    const window=electron.BrowserWindow.getAllWindows()[0];
    if(${process.argv[2]==='focus'}){
      const role=wc=>wc?(wc.getURL().split('/').at(-1).split('?')[0]||'dashboard'):null;
      return {windowFocused:window.isFocused(),focusedContents:role(electron.webContents.getFocusedWebContents()),
        mountedPages:window.contentView.children.filter(view=>view.webContents).map(view=>role(view.webContents)),
        contents:electron.webContents.getAllWebContents().map(wc=>({role:role(wc),focused:wc.isFocused(),zoom:wc.getZoomFactor()}))};
    }
    window.setContentSize(${Number(process.argv[2])},${Number(process.argv[3])});return window.getContentSize();})()`,
  returnByValue:true}}));
socket.onmessage=event=>{const result=JSON.parse(event.data);if(result.id!==1)return;
  if(result.error||result.result?.exceptionDetails){console.error(JSON.stringify(result));process.exitCode=1;}
  else console.log(JSON.stringify(result.result.result.value));
  clearTimeout(timer);socket.close();};
socket.onerror=()=>{console.error('Native inspector connection failed');process.exit(1);};
'''
    completed = subprocess.run(['node', '-e', script, inspector, 'focus' if width is None else str(width), str(height)],
                               cwd=ROOT, check=True, capture_output=True, text=True, timeout=10)
    return json.loads(completed.stdout)



async def run(app, output, click_probe_only=False, probe_driver='playwright'):
    from playwright.async_api import async_playwright
    service = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{service.server_port}'
    checks, errors = {}, []
    output.parent.mkdir(parents=True, exist_ok=True)
    source_files = [ROOT/'desktop'/name for name in ['main.cjs', 'backend-health.cjs', 'native-view-paint.cjs', 'preload.cjs', 'platform-preload.cjs', 'shell.html', 'shell.js']]
    source_files.append(ROOT/'ui-assets/glass.css')
    if app:
        source_files = [app/'Contents/Resources/app.asar']
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    with tempfile.TemporaryDirectory(prefix='page-fit-app-', dir=ROOT/'.draft-workbench') as tmp:
        folder = Path(tmp)
        private = folder/'state'
        private.mkdir()
        profile = folder/'profile'
        if app:
            command = [str(app/'Contents/MacOS/DraftWorkbench'), '--inspect=127.0.0.1:0']
        else:
            source_root = folder/'source'
            source = source_root/'desktop'
            source.mkdir(parents=True)
            for name in ['main.cjs', 'backend-health.cjs', 'native-view-paint.cjs', 'preload.cjs', 'platform-preload.cjs', 'shell.html', 'shell.js', 'package.json']:
                shutil.copy2(ROOT/'desktop'/name, source/name)
            shutil.copytree(ROOT/'ui-assets', source_root/'ui-assets')
            shutil.copytree(ROOT/'ui-assets', source/'ui-assets')
            command = [str(ROOT/'desktop/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), '--inspect=127.0.0.1:0', str(source)]
        env = {**os.environ, 'SAU_DESKTOP_TEST': '1', 'SAU_DESKTOP_TEST_URL': base,
               'SAU_DESKTOP_PROFILE': str(profile), 'SAU_DESKTOP_STATE_DIR': str(private),
               'SAU_WORKBENCH_PRIVATE': str(private), 'SAU_WORKBENCH_PORT': str(service.server_port),
               'SAU_EMBEDDED_DESCRIPTOR': str(private/'embedded-browser.json')}
        proc = None
        patchright_manager = None
        result = {'status': 'failed', 'checks': checks, 'real_accounts': 'not_touched',
                  'real_platform_upload': 'not_tested', 'runtime': 'candidate' if app else 'staged source',
                  'click_probe_driver': probe_driver}
        log = (folder/'runtime.log').open('w')
        try:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)

            async def ready():
                if proc.poll() is not None:
                    raise AssertionError('App exited: '+(folder/'runtime.log').read_text())
                try:
                    status = json.loads((private/'embedded-app-status.json').read_text())
                    return status if status['pid'] == proc.pid else None
                except (OSError, ValueError, KeyError):
                    return None

            await wait_for(ready, 25)
            match = re.search(r'Debugger listening on (ws://127\.0\.0\.1:\d+/[^\s]+)', (folder/'runtime.log').read_text())
            assert match, 'Isolated native inspector did not start'
            inspector = match.group(1)
            await asyncio.to_thread(resize_isolated_window, inspector, 1320, 868)
            port = (profile/'DevToolsActivePort').read_text().splitlines()[0]
            endpoint = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version'))['webSocketDebuggerUrl']
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(endpoint)
                shell = next(p for c in browser.contexts for p in c.pages if p.url.endswith('/shell.html'))
                shell.on('pageerror', lambda error: errors.append(str(error)))
                await shell.locator('#pageZoom').wait_for(state='hidden')
                assert await shell.evaluate('[innerWidth,innerHeight]') == [1320, 868]
                dashboard = next(p for c in browser.contexts for p in c.pages if p.url.startswith(base+'/?embedded='))
                focus_sessions = []

                async def use_native_focus(page):
                    # Playwright initializes focus emulation on every CDP page;
                    # disable that override before checking macOS first responder.
                    cdp = await page.context.new_cdp_session(page)
                    await cdp.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
                    focus_sessions.append(cdp)

                await use_native_focus(shell)
                await use_native_focus(dashboard)
                assert await dashboard.evaluate('devicePixelRatio') == await shell.evaluate('devicePixelRatio')
                checks['shell_and_workbench_keep_normal_scale'] = True
                Fixture.envelope = {'available': True, 'job': draft_job()}
                await shell.locator('#taskProgress').wait_for(state='visible')
                await shell.evaluate("window.draftDesktop.select('tencent')")

                async def page_at(suffix):
                    return next((p for c in browser.contexts for p in c.pages if p.url == base+suffix), None)

                probe_browser = browser
                if probe_driver == 'patchright':
                    # Shell controls remain on standard Playwright because
                    # Patchright evaluates in an isolated world by default.
                    # Only the actual platform clicks use the uploader's driver.
                    from patchright.async_api import async_playwright as patchright_playwright
                    patchright_manager = await patchright_playwright().start()
                    probe_browser = await patchright_manager.chromium.connect_over_cdp(endpoint)

                async def probe_page(page):
                    async def match():
                        return next((p for c in probe_browser.contexts for p in c.pages if p.url == page.url), None)
                    return await wait_for(match)

                tc = await wait_for(lambda: page_at('/tencent'))
                await tc.locator('#confirmCover').wait_for()
                await use_native_focus(tc)
                state = await shell.evaluate('window.draftDesktop.state()')
                assert state['zoom']['fit'] and .65 <= state['zoom']['factor'] < 1
                result['geometry'] = {'content_width': 1320, 'content_height': 868,
                                      'view_width': 1096, 'view_height': 706, 'tc_zoom': state['zoom']['factor']}
                assert await tc.evaluate('innerHeight') >= 960
                assert await contained(tc, '#confirmCover')
                physical = await shell.evaluate('innerHeight')-162
                assert abs(await tc.evaluate('innerHeight')*state['zoom']['factor']-physical) <= 1
                assert await shell.locator('#taskProgress').evaluate('e=>e.getBoundingClientRect().bottom') <= 162
                checks['tc_tall_modal_footer_fully_visible_with_progress'] = True
                checks['native_view_still_reserves_progress_header'] = True

                click_results = {'tc_fit': []}
                tc_probe = await probe_page(tc)
                for method in ['locator', 'mouse_normal', 'mouse_scaled']:
                    click_results['tc_fit'].append(await click_probe(tc_probe, '#confirmCover', state['zoom']['factor'], method))
                result['click_probes'] = click_results
                await tc.evaluate('scrollTo(0,0)')

                await shell.evaluate('window.draftDesktop.zoom(1)')
                assert not await contained(tc, '#confirmCover')
                assert not (await shell.evaluate('window.draftDesktop.state()'))['zoom']['fit']
                click_results['tc_manual_100'] = []
                for method in ['locator', 'mouse_normal', 'mouse_scaled']:
                    click_results['tc_manual_100'].append(await click_probe(tc_probe, '#confirmCover', 1, method))
                await tc.evaluate('scrollTo(0,0)')
                await shell.locator('#fitPage').click()
                assert await contained(tc, '#confirmCover')
                checks['manual_100_percent_reproduces_clip_and_fit_recovers'] = True

                await shell.evaluate("window.draftDesktop.select('bilibili')")
                bili = await wait_for(lambda: page_at('/bilibili'))
                await bili.locator('#continueEditing').wait_for()
                await use_native_focus(bili)
                assert await bili.evaluate('innerWidth') >= 1280
                assert await contained(bili, '#continueEditing')
                await shell.evaluate('window.draftDesktop.zoom(1)')
                assert not await contained(bili, '#continueEditing')
                await shell.locator('#fitPage').click()
                assert await contained(bili, '#continueEditing')
                checks['bili_continue_editing_fully_visible_and_fit_recovers'] = True

                state = await shell.evaluate('window.draftDesktop.state()')
                click_results['bili_fit'] = []
                bili_probe = await probe_page(bili)
                for method in ['locator', 'mouse_normal', 'mouse_scaled']:
                    click_results['bili_fit'].append(await click_probe(bili_probe, '#continueEditing', state['zoom']['factor'], method))
                checks['platform_locator_clicks_have_real_trusted_effect'] = all(
                    probes[0]['trusted_button_clicked'] for probes in click_results.values())
                assert checks['platform_locator_clicks_have_real_trusted_effect'], 'Native platform locator.click did not produce the requested trusted button action'

                # Exercise fixed draft-only messaging in this isolated
                # localhost Electron page. No real creator site or account is
                # opened by this acceptance path.
                await shell.evaluate("window.draftDesktop.select('douyin')")
                dy = await wait_for(lambda: page_at('/douyin'))
                await dy.locator('#publish').wait_for()
                await use_native_focus(dy)
                dy_notice = '当前仅保存草稿；抖音请点击「暂存离开」保存。'
                other_notice = '当前仅保存草稿；请回到素材工作台，点击「开始上传并保存草稿」。'

                async def has_notice(expected):
                    return await shell.evaluate('(expected)=>window.draftDesktop.state().then(s=>s.notice===expected)', expected)

                await dy.locator('#publish').click()
                await wait_for(lambda: has_notice(dy_notice))
                dy_counts = await dy.evaluate("JSON.parse(document.body.dataset.buttonCounts||'{}')")
                assert dy_counts.get('publish', 0) == 0, 'Douyin page publish handler ran despite the guard'
                assert await shell.locator('#address').inner_text() == dy_notice
                checks['douyin_publish_is_blocked_with_fixed_shell_notice'] = True

                # There is no page-facing workbench or Node bridge. A page
                # cannot pass its own text through window messages to replace
                # the trusted, fixed IPC notice.
                page_bridge = await dy.evaluate("({desktop:typeof window.draftDesktop,require:typeof window.require})")
                assert page_bridge == {'desktop': 'undefined', 'require': 'undefined'}
                await dy.evaluate("window.postMessage({type:'draft-publish-blocked',text:'伪造网页通知'},'*')")
                await asyncio.sleep(.1)
                assert await has_notice(dy_notice)
                assert await shell.locator('#address').inner_text() == dy_notice
                checks['page_cannot_invoke_or_rewrite_fixed_notice'] = True

                # Draft save remains available after a blocked publish click.
                await dy.locator('#saveDraft').click()
                dy_counts = await dy.evaluate("JSON.parse(document.body.dataset.buttonCounts||'{}')")
                assert dy_counts.get('saveDraft', 0) == 1, 'Douyin draft-save handler was blocked'
                assert dy_counts.get('publish', 0) == 0
                checks['douyin_draft_save_remains_available'] = True

                # A hidden same-origin iframe cannot change the notice, even
                # when its document emits a publish click.
                await shell.evaluate("window.draftDesktop.select('workbench')")
                await shell.evaluate("window.draftDesktop.select('douyin')")
                await wait_for(lambda: has_notice(''))
                hidden_frame = dy.frame_locator('#hiddenFrame')
                assert await hidden_frame.locator('#framePublish').count() == 1
                await hidden_frame.locator('#framePublish').evaluate('(e)=>e.click()')
                await hidden_frame.locator('body').evaluate("() => window.postMessage({type:'draft-publish-blocked',text:'iframe伪造文案'},'*')")
                await asyncio.sleep(.15)
                assert await has_notice(''), 'Hidden iframe changed the shell notice'
                checks['hidden_iframe_notification_does_not_change_shell_notice'] = True

                # Another platform gets its own fixed workbench wording.
                await shell.evaluate("window.draftDesktop.select('bilibili')")
                await wait_for(lambda: has_notice(''))
                await bili.locator('#publish').click()
                await wait_for(lambda: has_notice(other_notice))
                bili_counts = await bili.evaluate("JSON.parse(document.body.dataset.buttonCounts||'{}')")
                assert bili_counts.get('publish', 0) == 0, 'Bilibili page publish handler ran despite the guard'
                assert await shell.locator('#address').inner_text() == other_notice
                checks['other_platform_publish_uses_fixed_workbench_notice'] = True

                # A background platform page may still run scripts, but it
                # cannot replace the notice for the currently selected page.
                await shell.evaluate("window.draftDesktop.select('bilibili')")
                await wait_for(lambda: has_notice(''))
                await dy.locator('#publish').evaluate('(e)=>e.click()')
                await dy.evaluate("window.postMessage({type:'draft-publish-blocked',text:'隐藏页伪造文案'},'*')")
                await asyncio.sleep(.15)
                assert await has_notice(''), 'Hidden Douyin page changed the shell notice'
                checks['hidden_platform_notification_does_not_change_shell_notice'] = True
                result['draft_only_notice'] = {
                    'douyin': dy_notice,
                    'other_platform': other_notice,
                    'douyin_handlers': {'publish': dy_counts.get('publish', 0), 'save_draft': dy_counts.get('saveDraft', 0)},
                    'bilibili_publish_handler': bili_counts.get('publish', 0),
                }
                if click_probe_only:
                    result['status'] = 'passed'
                    print(json.dumps({'status': result['status'], 'driver': probe_driver,
                        'locator_trusted_effect': {name: probes[0]['trusted_button_clicked'] for name, probes in click_results.items()},
                        'scaled_mouse_trusted_effect': {name: probes[2]['trusted_button_clicked'] for name, probes in click_results.items()},
                        'draft_only_notice': result['draft_only_notice']}, ensure_ascii=False), flush=True)
                    return

                if patchright_manager:
                    # The two concurrent driver clients can both own Chromium
                    # emulation overrides. Disconnect the probe driver before
                    # the separate native first-responder acceptance checks.
                    await patchright_manager.stop()
                    patchright_manager = None
                    await use_native_focus(tc)
                    await use_native_focus(bili)

                await shell.evaluate("window.draftDesktop.select('tencent')")
                result['focus_after_platform_clicks'] = {
                    'tc_document': await tc.evaluate('document.hasFocus()'),
                    'bili_document': await bili.evaluate('document.hasFocus()'),
                    'native': await asyncio.to_thread(resize_isolated_window, inspector)}
                assert result['focus_after_platform_clicks']['tc_document']
                assert not result['focus_after_platform_clicks']['bili_document'], 'Hidden Bili document still reports focus after actual platform click and explicit TC switch'
                assert result['focus_after_platform_clicks']['native']['focusedContents'] == 'tencent'
                assert 'bilibili' not in result['focus_after_platform_clicks']['native']['mountedPages']
                assert 'dashboard' not in result['focus_after_platform_clicks']['native']['mountedPages']
                await bili.reload()
                # New documents can inherit the driver's emulated focus again.
                # Clear only that test-driver override before native assertions.
                await use_native_focus(bili)
                result['focus_after_background_navigation'] = {
                    'tc_document': await tc.evaluate('document.hasFocus()'),
                    'bili_document': await bili.evaluate('document.hasFocus()'),
                    'native': await asyncio.to_thread(resize_isolated_window, inspector)}
                assert result['focus_after_background_navigation']['tc_document']
                assert not result['focus_after_background_navigation']['bili_document']
                assert result['focus_after_background_navigation']['native']['focusedContents'] == 'tencent'
                checks['hidden_background_navigation_cannot_take_native_focus'] = True
                await tc.locator('#editor').focus()
                await asyncio.sleep(1.2)
                assert await tc.evaluate("document.hasFocus()&&document.activeElement.id==='editor'")
                checks['explicit_switch_focuses_current_page_and_poll_does_not_steal'] = True
                await tc.locator('#openPopup').click()
                popup = await wait_for(lambda: page_at('/popup'))
                await popup.locator('#editor').wait_for()
                await use_native_focus(popup)
                assert (await shell.evaluate('window.draftDesktop.state()'))['popup']
                state = await shell.evaluate('window.draftDesktop.zoom(.8)')
                assert not state['zoom']['fit'] and state['zoom']['factor'] == .8
                assert abs(await popup.evaluate('innerHeight')*.8-physical) <= 1
                assert abs(await tc.evaluate('innerHeight')*.8-physical) <= 1
                await shell.evaluate('window.draftDesktop.fit()')
                assert await popup.evaluate('document.hasFocus()')
                checks['popup_shares_zoom_and_selected_popup_receives_focus'] = True
                await shell.locator('#closePopup').click()
                assert await tc.evaluate('document.hasFocus()')
                checks['closing_popup_restores_parent_focus'] = True

                # A lease only on the local fixture: no runner or upload is started.
                descriptor = json.loads((private/'embedded-browser.json').read_text())
                request = urllib.request.Request(descriptor['url']+'/prepare', data=json.dumps({
                    'platform': 'tencent', 'action': 'draft'}).encode(), headers={
                    'Content-Type': 'application/json', 'X-Embedded-Token': descriptor['token']}, method='POST')
                prepared = json.load(await asyncio.to_thread(urllib.request.urlopen, request))
                result['focus_immediately_after_prepare'] = await asyncio.to_thread(resize_isolated_window, inspector)
                assert result['focus_immediately_after_prepare']['focusedContents'] == 'tencent'
                assert await tc.evaluate('document.hasFocus()')
                assert not await bili.evaluate('document.hasFocus()')
                assert not await dashboard.evaluate('document.hasFocus()')
                checks['prepare_returns_only_after_target_native_and_document_focus'] = True
                state = await shell.evaluate('window.draftDesktop.state()')
                assert state['navigationLocked'] and not await shell.locator('#fitPage').is_disabled()
                await shell.locator('#zoomIn').click()
                state = await shell.evaluate('window.draftDesktop.state()')
                assert state['navigationLocked'] and not state['zoom']['fit']
                assert await tc.evaluate('document.hasFocus()')
                request = urllib.request.Request(descriptor['url']+'/finished', data=json.dumps({
                    'lease': prepared['lease']}).encode(), headers={
                    'Content-Type': 'application/json', 'X-Embedded-Token': descriptor['token']}, method='POST')
                await asyncio.to_thread(urllib.request.urlopen, request)
                checks['zoom_is_available_during_local_draft_lease_without_navigation'] = True
                await tc.evaluate("window.paintWitness={document,href:location.href,scrollY};document.querySelector('input').value='保留文案';")
                before_fit=await shell.evaluate('window.draftDesktop.state()')
                assert await shell.evaluate('window.draftDesktop.repairDisplay().then(()=>true)')
                after_fit=await shell.evaluate('window.draftDesktop.state()')
                assert after_fit['zoom']==before_fit['zoom'] and after_fit['active']==before_fit['active']
                assert await tc.evaluate("paintWitness.document===document && paintWitness.href===location.href && document.querySelector('input').value==='保留文案'")
                checks['display_repair_keeps_live_document_fields_zoom_and_active_lease'] = True

                await asyncio.to_thread(resize_isolated_window, inspector, 1000, 700)
                await wait_for(lambda: shell.evaluate('innerWidth===1000'))
                await shell.evaluate('window.draftDesktop.fit()')
                for selector in ['#fitPage', '#zoomOut', '#zoomValue', '#zoomIn', '#repairDisplay', '#reload', '.toolbar .pill']:
                    assert await contained(shell, selector), selector
                assert (await shell.evaluate('window.draftDesktop.state()'))['zoom']['factor'] == .65
                checks['compact_toolbar_is_contained_at_1000px_and_fit_recomputes_on_resize'] = True
                await shell.evaluate('window.draftDesktop.zoom(.8)')
                await asyncio.to_thread(resize_isolated_window, inspector, 1320, 868)
                await wait_for(lambda: shell.evaluate('innerWidth===1320'))
                assert (await shell.evaluate('window.draftDesktop.state()'))['zoom']['factor'] == .8
                await shell.locator('#fitPage').click()
                assert await contained(tc, '#confirmCover')
                checks['native_resize_preserves_manual_mode_and_fit_restores_tall_footer'] = True
                await shell.evaluate("window.draftDesktop.select('workbench')")
                assert await shell.locator('#pageZoom').is_hidden()
                assert await dashboard.evaluate('document.hasFocus()')
                assert not await tc.evaluate('document.hasFocus()')
                assert not await bili.evaluate('document.hasFocus()')
                result['focus_after_workbench'] = await asyncio.to_thread(resize_isolated_window, inspector)
                assert result['focus_after_workbench']['focusedContents'] == 'dashboard'
                assert 'tencent' not in result['focus_after_workbench']['mountedPages']
                assert 'bilibili' not in result['focus_after_workbench']['mountedPages']
                assert await dashboard.evaluate('devicePixelRatio') == await shell.evaluate('devicePixelRatio')
                checks['return_to_workbench_hides_zoom_and_restores_editor_focus'] = True
                assert not errors, errors
                checks['no_shell_javascript_errors'] = True
                await shell.screenshot(path=str(output.with_suffix('.png')))
            result['status'] = 'passed'
        except BaseException as error:
            result['error'] = str(error)
            raise
        finally:
            if patchright_manager:
                await patchright_manager.stop()
            if proc and proc.poll() is None:
                proc.terminate()
                await asyncio.to_thread(proc.wait, 15)
            log.close()
            output.with_suffix('.log').write_text((folder/'runtime.log').read_text())
            result['sources_or_candidate_unchanged_after'] = all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p)] for p in source_files)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            service.shutdown()
            service.server_close()
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--click-probe-only', action='store_true')
    parser.add_argument('--probe-driver', choices=['playwright', 'patchright'], default='playwright')
    args = parser.parse_args()
    asyncio.run(run(args.app.resolve() if args.app else None, args.out.resolve(), args.click_probe_only, args.probe_driver))
