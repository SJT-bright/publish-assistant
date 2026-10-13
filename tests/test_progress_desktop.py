"""Isolated actual-App acceptance of the progress API and native view layout."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent


class Fixture(BaseHTTPRequestHandler):
    envelope = {"available": True, "job": None}
    failure = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/api/state'):
            data = {"app": "sau-draft-workbench", "embedded_supported": True,
                    "api_version": 2, "job": {"active": False}}
        elif self.path == '/api/progress':
            if Fixture.failure:
                self.send_error(503)
                return
            data = Fixture.envelope
        else:
            body = ('<!doctype html><meta charset="utf-8"><title>隔离验收页面</title>'
                    '<body style="background:#f4f6fb;font:18px -apple-system;padding:32px">'
                    '平台模拟页面 · 无账号、无素材、无上传'
                    '<button id="fixture-publish" onclick="document.body.dataset.published=String(Number(document.body.dataset.published||0)+1)">发表</button></body>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def job(platform='xiaohongshu', index=1, stage='upload', completed=None,
        status='running', active=True, age=62):
    now = time.time()
    return {"id": "isolated-progress-1", "action": "draft", "active": active,
            "platform": platform, "platform_name": {"xiaohongshu": "小红书", "tencent": "视频号"}[platform],
            "platform_index": index, "platform_count": 2, "status": status,
            "message": "正在处理当前平台", "progress": {
                "stage": stage, "label": "受控步骤", "state": "running" if active else "unknown",
                "started_at": now-age, "updated_at": now,
                "completed": completed if completed is not None else ['session', 'video'], "skipped": []}}


async def wait_for(check, seconds=15):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(.1)
    raise AssertionError('Acceptance condition timed out')


async def run(app, output):
    from playwright.async_api import async_playwright
    archive = app/'Contents/Resources/app.asar'
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    checks = {}
    service = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{service.server_port}'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='progress-app-', dir=ROOT/'.draft-workbench') as tmp:
        folder = Path(tmp)
        private = folder/'state'
        private.mkdir()
        profile = folder/'profile'
        env = {**os.environ, 'SAU_DESKTOP_TEST': '1', 'SAU_DESKTOP_TEST_URL': base,
               'SAU_DESKTOP_PROFILE': str(profile), 'SAU_DESKTOP_STATE_DIR': str(private),
               'SAU_WORKBENCH_PRIVATE': str(private), 'SAU_WORKBENCH_PORT': str(service.server_port),
               'SAU_EMBEDDED_DESCRIPTOR': str(private/'embedded-browser.json')}
        proc = None
        errors = []
        result = {'status': 'failed', 'checks': checks, 'asar_sha256': before,
                  'real_accounts': 'not_touched', 'real_platform_upload': 'not_tested'}
        log = (folder/'runtime.log').open('w')
        try:
            proc = subprocess.Popen([str(app/'Contents/MacOS/DraftWorkbench')], cwd=ROOT,
                                    env=env, stdout=log, stderr=log, start_new_session=True)

            async def ready():
                if proc.poll() is not None:
                    raise AssertionError('App exited: '+(folder/'runtime.log').read_text())
                try:
                    status = json.loads((private/'embedded-app-status.json').read_text())
                    return status if status['pid'] == proc.pid else None
                except (OSError, ValueError, KeyError):
                    return None

            await wait_for(ready, 25)
            port = (profile/'DevToolsActivePort').read_text().splitlines()[0]
            endpoint = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version'))['webSocketDebuggerUrl']
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(endpoint)
                shell = next(p for c in browser.contexts for p in c.pages if p.url.endswith('/shell.html'))
                shell.on('pageerror', lambda error: errors.append(str(error)))
                await shell.locator('#taskProgress').wait_for(state='hidden')
                await shell.locator('[data-platform="tencent"]').click()

                async def platform_page():
                    return next((p for c in browser.contexts for p in c.pages if p.url == base+'/tencent'), None)

                page = await wait_for(platform_page)
                await page.wait_for_load_state('domcontentloaded')
                full_height = await shell.evaluate('innerHeight')
                assert abs(await page.evaluate('innerHeight') * (await shell.evaluate('window.draftDesktop.state()'))['zoom']['factor'] - (full_height-76)) <= 1
                checks['no_task_hidden_and_original_native_bounds'] = True
                await page.locator('#fixture-publish').click()
                assert await page.locator('body').get_attribute('data-published') is None
                await shell.locator('#publicationMode').click()
                await wait_for(lambda: shell.evaluate('window.draftDesktop.state().then(s=>s.manualPublishAllowed)'))
                await page.locator('#fixture-publish').click()
                assert await page.locator('body').get_attribute('data-published') == '1'
                checks['native_publication_requires_explicit_manual_mode'] = True
                Fixture.envelope = {'available': True, 'job': job()}

                async def upload_visible():
                    return (await shell.locator('#taskProgress').get_attribute('data-platform') == 'xiaohongshu'
                            and '等待上传' in await shell.locator('#progressStage').inner_text())

                await wait_for(upload_visible)
                assert await shell.locator('#publicationMode').is_disabled()
                await page.locator('#fixture-publish').click()
                assert await page.locator('body').get_attribute('data-published') == '1'
                checks['running_draft_task_blocks_native_publication'] = True
                assert await shell.locator('#progressPlatform').inner_text() == '平台 1/2 · 小红书'
                assert await shell.locator('#progressScope').inner_text() == '执行中'
                assert await shell.locator('#progressCount').inner_text() == '已完成 2/7 步'
                assert await shell.locator('#progressSteps').get_attribute('aria-valuenow') == '2'
                assert '仍在等待平台响应' in await shell.locator('#progressDetail').inner_text()
                assert abs(await page.evaluate('innerHeight') * (await shell.evaluate('window.draftDesktop.state()'))['zoom']['factor'] - (full_height-216)) <= 1
                bottom = await shell.locator('#taskProgress').evaluate('e=>e.getBoundingClientRect().bottom')
                assert bottom <= 216, bottom
                checks['api_stage_wait_and_native_view_not_covering_progress'] = True
                checks['progress_tracks_worker_platform_not_selected_browser'] = True
                assert await shell.locator('#progressPercent').inner_text() == '流程 28.6%'
                Fixture.envelope['job']['progress']['operations'] = {'upload': {
                    'plan': ['transfer', 'processing'], 'completed': [], 'skipped': [],
                    'current': 'transfer', 'state': 'running', 'percent': 48}}
                async def fine_seen():
                    return await shell.locator('#progressMeter').get_attribute('aria-valuenow') == '32'
                await wait_for(fine_seen)
                assert '48%' in await shell.locator('#progressOperations').inner_text()
                assert '24%' in await shell.locator('[data-stage="upload"]').inner_text()
                await asyncio.sleep(1.3)
                assert await shell.locator('#progressMeter').get_attribute('aria-valuenow') == '32'
                checks['fine_upload_percent_and_wait_do_not_invent_completion'] = True
                await shell.screenshot(path=str(output.with_suffix('.png')))
                Fixture.envelope = {'available': True, 'job': job('tencent', 2, 'session', [], age=0)}

                async def reset_seen():
                    return (await shell.locator('#progressPlatform').inner_text() == '平台 2/2 · 视频号'
                            and await shell.locator('#progressSteps').get_attribute('aria-valuenow') == '0')

                await wait_for(reset_seen)
                assert not '仍在等待平台响应' in await shell.locator('#progressDetail').inner_text()
                checks['next_platform_resets_steps_and_wait'] = True
                all_steps = ['session', 'video', 'upload', 'content', 'cover', 'save', 'verify']
                Fixture.envelope = {'available': True, 'job': job('tencent', 2, 'verify', all_steps,
                                                                  'result_unknown', False)}

                async def unknown_seen():
                    return '结果待确认' in await shell.locator('#progressStage').inner_text()

                await wait_for(unknown_seen)
                assert await shell.locator('#progressSteps').get_attribute('aria-valuenow') == '6'
                assert await shell.locator('#taskProgress').get_attribute('data-tone') != 'verified'
                checks['unverified_receipt_cannot_complete_bar'] = True
                Fixture.envelope = {'available': True, 'job': job('tencent', 2, 'verify', all_steps,
                                                                  'platform_snapshot_verified', False)}

                async def verified_seen():
                    return await shell.locator('#taskProgress').get_attribute('data-tone') == 'verified'

                await wait_for(verified_seen)
                assert await shell.locator('#progressSteps').get_attribute('aria-valuenow') == '7'
                checks['verified_snapshot_can_complete_bar'] = True
                Fixture.envelope = {'available': True, 'job': job('tencent', 2, 'upload', age=62)}
                await wait_for(lambda: shell.locator('#taskProgress').evaluate("e=>e.dataset.tone==='running'"))
                Fixture.failure = True
                await wait_for(lambda: shell.locator('#taskProgress').evaluate("e=>e.dataset.tone==='offline'"))
                elapsed = await shell.locator('#progressWait').inner_text()
                await asyncio.sleep(1.4)
                assert await shell.locator('#progressWait').inner_text() == elapsed
                assert '连接中断' in await shell.locator('#progressStage').inner_text()
                checks['connection_failure_preserves_last_step_and_freezes_timer'] = True
                Fixture.failure = False
                legacy = job('tencent', 2, status='saved_unverified', active=False)
                legacy['progress'] = None
                Fixture.envelope = {'available': True, 'job': legacy}
                await wait_for(lambda: shell.locator('#progressStage').evaluate("e=>e.textContent.includes('保存尚未核验')"))
                assert '具体步骤尚未上报' in await shell.locator('#progressDetail').inner_text()
                assert await shell.locator('#progressScope').inner_text() == '最近任务'
                assert await shell.locator('#progressCount').inner_text() == '历史任务 · 无步骤记录'
                assert await shell.locator('#progressSteps').get_attribute('aria-valuenow') is None
                checks['legacy_task_has_no_fabricated_steps'] = True
                Fixture.envelope = {'available': True, 'job': None}
                await shell.locator('#taskProgress').wait_for(state='hidden')
                assert abs(await page.evaluate('innerHeight') * (await shell.evaluate('window.draftDesktop.state()'))['zoom']['factor'] - (full_height-76)) <= 1
                checks['task_removed_restores_native_bounds'] = True
                await wait_for(lambda: shell.evaluate('window.draftDesktop.state().then(s=>s.manualPublishAllowed)'))
                for ident in ('douyin','xiaohongshu','tencent','bilibili','kuaishou','toutiao','xigua'):
                    await shell.locator('button.nav[data-platform="'+ident+'"]').click()
                    async def opened(ident=ident):
                        return next((p for c in browser.contexts for p in c.pages if p.url == base+'/'+ident), None)
                    target = await wait_for(opened)
                    await target.locator('#fixture-publish').wait_for()
                    previous = int(await target.locator('body').get_attribute('data-published') or 0)
                    await target.locator('#fixture-publish').click()
                    assert int(await target.locator('body').get_attribute('data-published')) == previous+1
                checks['seven_portals_manual_publication_after_finish'] = True
                await shell.locator('#publicationMode').click()
                await wait_for(lambda: shell.evaluate('window.draftDesktop.state().then(s=>s.publicationMode==="draft")'))
                previous = int(await target.locator('body').get_attribute('data-published'))
                await target.locator('#fixture-publish').click()
                assert int(await target.locator('body').get_attribute('data-published')) == previous
                checks['switching_back_to_draft_restores_native_protection'] = True
                assert not errors, errors
                checks['no_shell_javascript_errors'] = True
            result = {'status': 'passed', 'executor': 'Codex isolated native App', 'checks': checks,
                      'asar_sha256': before, 'real_accounts': 'not_touched', 'real_platform_upload': 'not_tested'}
        except BaseException as error:
            result = {'status': 'failed', 'error': str(error), 'checks': checks, 'asar_sha256': before,
                      'real_accounts': 'not_touched', 'real_platform_upload': 'not_tested'}
            raise
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                await asyncio.to_thread(proc.wait, 15)
            log.close()
            output.with_suffix('.log').write_text((folder/'runtime.log').read_text())
            result['archive_unchanged_after'] = hashlib.sha256(archive.read_bytes()).hexdigest() == before
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            service.shutdown()
            service.server_close()
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.app.resolve(), args.out.resolve()))
