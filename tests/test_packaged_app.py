"""Run the actual release executable with disposable sessions and local fixture sites."""
import argparse,asyncio,json,os,socket,subprocess,sys,tempfile,threading,time,urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ['NO_PROXY']=os.environ.get('NO_PROXY','')+',127.0.0.1,localhost,::1'
os.environ['no_proxy']=os.environ.get('no_proxy','')+',127.0.0.1,localhost,::1'
PLATFORMS=['douyin','xiaohongshu','tencent','bilibili','kuaishou','toutiao','xigua']
class Fixture(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def do_GET(self):
  data=b'<html><meta charset="utf-8"><title>Local fixture</title><input id="video" type="file" accept="video/mp4" onchange="document.body.dataset.bytes=this.files[0].size"><button id="popup" onclick="window.open(\'/auth-child\')">Auth child</button><button onclick="window.close()">Close child</button><span role="button" class="submit-add" id="submit" onclick="fetch(\'/x/vu/web/add\',{method:\'POST\'}).then(()=>document.body.dataset.submitted=Number(document.body.dataset.submitted||0)+1)">&#31435;&#21363;&#25237;&#31295;</span></html>'
  self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  assert self.path=='/x/vu/web/add'
  self.server.publication_requests+=1
  data=b'{"code":0,"data":{"aid":123,"bvid":"fixture"}}'
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

def free_port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
async def wait(fn,timeout=40):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  try:
   value=await fn()
   if value:return value
  except (OSError,ValueError,KeyError):pass
  await asyncio.sleep(.15)
 raise AssertionError('Packaged application wait timed out')
async def run(app,output):
 from playwright.async_api import async_playwright
 from PIL import Image
 from uploader.local_files import set_local_files
 app=Path(app).resolve()
 if sys.platform=='darwin':
  executable=app/'Contents/MacOS/DraftWorkbench';resources=app/'Contents/Resources'
 else:
  executable=app/'DraftWorkbench.exe';resources=app/'resources'
 backend=resources/'backend'/('PublishAssistantBackend.exe' if os.name=='nt' else 'PublishAssistantBackend')
 tools=resources/'tools'
 initial_files={str(p.relative_to(resources)) for p in resources.rglob('*') if p.is_file() or p.is_symlink()}
 version=json.loads((Path(__file__).resolve().parent.parent/'desktop/package.json').read_text())['version']
 checks={'version':version,'platform':sys.platform,'checks':{}}
 server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);server.publication_requests=0;threading.Thread(target=server.serve_forever,daemon=True).start()
 with tempfile.TemporaryDirectory(prefix='发布助手 package QA ') as tmp:
  folder=Path(tmp).resolve();private=folder/'state';private.mkdir();profile=folder/'profile';port=free_port();base=f'http://127.0.0.1:{port}'
  env={**os.environ,'SAU_DESKTOP_TEST':'1','SAU_DESKTOP_TEST_URL':f'http://127.0.0.1:{server.server_port}',
       'SAU_DESKTOP_PROFILE':str(profile),'SAU_DESKTOP_STATE_DIR':str(private),'SAU_WORKBENCH_PRIVATE':str(private),
       'SAU_WORKBENCH_PORT':str(port),'SAU_EMBEDDED_DESCRIPTOR':str(private/'embedded-browser.json'),
       'SAU_MEDIA_TOOLS':str(tools),'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
  proof=subprocess.run([str(backend),'self-test'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
  assert proof.returncode==0,(proof.stdout,proof.stderr)
  assert json.loads(proof.stdout)['frozen'] is True
  checks['checks']['frozen_backend_and_platform_imports']=True
  helper=subprocess.run([str(backend),'runner','--help'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
  assert helper.returncode==0 and 'login' in helper.stdout
  checks['checks']['frozen_runner_dispatch']=True
  ffmpeg=tools/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg')
  media=folder/'测试素材';media.mkdir();video=media/'测试视频.mp4'
  subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10','-t','2','-c:v','mpeg4','-y',str(video)],check=True,timeout=30)
  Image.new('RGB',(240,320),'royalblue').save(media/'封面.png')
  (media/'publish.json').write_text(json.dumps({'title':'本地验收作品','description':'只在本机验证素材，不发送到互联网。','tags':['生活','创作']},ensure_ascii=False),encoding='utf-8')
  big=media/'64MB测试视频.mp4'
  with big.open('wb') as stream:stream.truncate(64*1024*1024)
  proc=None;browser=None;backend_pid=None;log=(folder/'app.log').open('w',encoding='utf-8')
  def launch():return subprocess.Popen([str(executable)],env=env,cwd=folder,stdout=log,stderr=log)
  def state():
   with urllib.request.urlopen(base+'/api/state?embedded=1',timeout=3) as response:return json.load(response)
  def api(endpoint,data):
   request=urllib.request.Request(base+endpoint,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Workbench-Token':state()['token']},method='POST')
   with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)['result']
  def bridge_api(endpoint,data):
   descriptor=json.loads((private/'embedded-browser.json').read_text(encoding='utf-8'))
   request=urllib.request.Request(descriptor['url']+endpoint,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Embedded-Token':descriptor['token']},method='POST')
   with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
  def quit_app():
   descriptor=json.loads((private/'embedded-browser.json').read_text(encoding='utf-8'))
   request=urllib.request.Request(descriptor['url']+'/test-quit',data=b'{}',headers={'Content-Type':'application/json','X-Embedded-Token':descriptor['token']},method='POST')
   with urllib.request.urlopen(request,timeout=5) as response:assert json.load(response)['ok']
  async def ready():
   if proc.poll() is not None:raise AssertionError('App exited: '+(folder/'app.log').read_text(encoding='utf-8'))
   status=json.loads((private/'embedded-app-status.json').read_text(encoding='utf-8'))
   return status if status['pid']==proc.pid and status['status']=='ready' and state().get('embedded_supported') else None
  async def connect(p):
   port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version',timeout=3) as response:url=json.load(response)['webSocketDebuggerUrl']
   return await p.chromium.connect_over_cdp(url)
  try:
   proc=launch();status=await wait(ready);assert status['version']==version;checks['checks']['real_app_cold_start']=True
   owner=json.loads((private/'backend-owner.json').read_text());assert Path(owner['executable']).resolve()==backend.resolve();backend_pid=owner['pid']
   async with async_playwright() as p:
    browser=await connect(p)
    shell=next(page for context in browser.contexts for page in context.pages if page.url.endswith('/shell.html'))
    dashboard=next(page for context in browser.contexts for page in context.pages if page.url.startswith(base))
    await dashboard.locator('#title').wait_for()
    assert version in await shell.locator('#appVersion').inner_text()
    assert str(status['build']) in await shell.locator('#appVersion').inner_text()
    checks['checks']['running_version_and_build_visible_in_sidebar']=True
    assert await shell.locator('#githubStar').count()==1
    assert await shell.locator('#githubStar').get_attribute('aria-label')=='在 GitHub 上给发布助手点 Star'
    shell_state=await shell.evaluate('window.draftDesktop.state()')
    assert shell_state['repositoryURL']=='https://github.com/SJT-bright/publish-assistant'
    assert shell_state['softwareUpdate']['status']=='disabled'
    assert await shell.locator('#softwareUpdate').is_disabled()
    checks['checks']['github_star_entry_and_isolated_updater_visible']=True
    assert await dashboard.locator('#finishMode').input_value()=='publish'
    await dashboard.locator('#finishMode').select_option('draft')
    assert await dashboard.locator('#saveLabel').inner_text()=='开始上传并保存草稿'
    await dashboard.locator('#finishMode').select_option('publish')
    assert await dashboard.locator('#saveLabel').inner_text()=='开始上传并发布'
    checks['checks']['explicit_finish_mode_and_button_match']=True
    assert await shell.locator('.nav[data-platform]').count()==7
    checks['checks']['seven_platforms_inside_actual_app']=True
    for platform in PLATFORMS:
     await shell.locator(f'.nav[data-platform="{platform}"]').click()
     async def native_page():return next((page for context in browser.contexts for page in context.pages if page.url.endswith('/'+platform)),None)
     page=await wait(native_page)
     assert await page.evaluate("localStorage.getItem('package_session')")==None
     await page.evaluate("id=>{document.cookie='package_session='+id+';max-age=86400;path=/';localStorage.setItem('package_session',id)}",platform)
     if platform=='douyin':
      page._sau_local_files=True
      await set_local_files(page,page.locator('#video'),str(big))
      assert await page.evaluate('Number(document.body.dataset.bytes)')==64*1024*1024
      checks['checks']['native_upload_over_50MB_without_file_buffering']=True
      await page.locator('#popup').click()
      async def popup():return next((page for context in browser.contexts for page in context.pages if page.url.endswith('/auth-child')),None)
      child=await wait(popup);assert await child.evaluate('!!window.opener');await child.get_by_role('button',name='Close child').click()
      checks['checks']['login_popup_embedded_with_opener']=True
     if platform=='bilibili':
      await page.locator('#submit').click()
      assert await page.evaluate('Number(document.body.dataset.submitted||0)')==0
      assert server.publication_requests==0
      async def blocked_notice():return '开启网页发表' in await shell.locator('#address').inner_text()
      await wait(blocked_notice)
      checks['checks']['bilibili_draft_mode_blocks_submit_with_actionable_notice']=True
      await shell.locator('#publicationMode').click()
      async def manual_ready():return await shell.locator('#publicationLabel').inner_text()=='可在平台发表'
      await wait(manual_ready)
      async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/x/vu/web/add')):
       await page.locator('#submit').click()
      async def submitted():return await page.evaluate('Number(document.body.dataset.submitted||0)')==1
      await wait(submitted);assert server.publication_requests==1
      checks['checks']['manual_bilibili_submit_reaches_local_fixture_once']=True
      await shell.locator('#publicationMode').click()
      async def draft_ready():return await shell.locator('#publicationLabel').inner_text()=='仅保存草稿'
      await wait(draft_ready);await page.locator('#submit').click()
      assert server.publication_requests==1
      checks['checks']['switching_back_to_draft_restores_bilibili_submit_block']=True
      lease=await asyncio.to_thread(bridge_api,'/prepare',{'platform':'bilibili','action':'publish'})
      permit={'lease':lease['lease'],'platform':'bilibili'}
      assert (await asyncio.to_thread(bridge_api,'/publish-permit',permit))['ok']
      async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/x/vu/web/add')):
       await page.locator('#submit').click()
      await page.locator('#submit').click()
      assert server.publication_requests==2
      await asyncio.to_thread(bridge_api,'/publish-revoke',permit)
      await page.locator('#submit').click();assert server.publication_requests==2
      await asyncio.to_thread(bridge_api,'/finished',{'lease':lease['lease']})
      checks['checks']['task_bound_publication_permit_allows_only_one_submit']=True
      await shell.locator('#repairDisplay').click()
      async def repaired():return (await shell.evaluate('window.draftDesktop.state()')).get('pageOperation',{}).get('status')=='completed'
      await wait(repaired)
      assert await page.evaluate('Number(document.body.dataset.submitted||0)')==2
      checks['checks']['display_repair_preserves_current_page_and_reports_completion']=True
      await shell.locator('#reload').click()
      async def reloaded():
       operation=(await shell.evaluate('window.draftDesktop.state()')).get('pageOperation') or {}
       return operation.get('kind')=='reload' and operation.get('status')=='completed'
      await wait(reloaded)
      assert '加载完成' in await shell.locator('#address').inner_text()
      checks['checks']['reload_waits_for_document_completion_and_shows_result']=True
    checks['checks']['seven_sessions_isolated']=True
    await shell.get_by_role('button',name='素材工作台',exact=False).click()
    await dashboard.get_by_text('粘贴文件夹或视频路径',exact=True).click()
    await dashboard.locator('#path').fill(str(video));await dashboard.locator('#load').click()
    async def imported():return await dashboard.locator('#title').input_value()=='本地验收作品'
    await wait(imported)
    checks['checks']['unicode_video_import_through_actual_ui']=True
    info=api('/api/media-info',{'path':str(video)});assert info['width']==160 and info['height']==90 and info['duration']>1
    checks['checks']['bundled_media_probe']=True
    cover=api('/api/frame',{'path':str(video),'time':.7,'role':'portrait','ratio':'3:4'})
    assert Path(cover['path']).is_file() and abs(cover['width']/cover['height']-.75)<.01
    checks['checks']['bundled_keyframe_portrait_capture']=True
    with urllib.request.urlopen(urllib.request.Request(base+'/media?path='+urllib.parse.quote(str(video)),headers={'Range':'bytes=0-99'}),timeout=5) as response:
     assert response.status==206 and len(response.read())==100
    checks['checks']['video_preview_and_seeking_range']=True
    await dashboard.locator('#description').fill('独立文案')
    await dashboard.locator('input[data-platform="kuaishou"]').check()
    await dashboard.evaluate('window.flushWorkbenchEditor()')
    assert state()['editor']['payload']['description']=='独立文案'
    checks['checks']['unicode_material_state_saved']=True
    original_editor=state()['editor']
    await dashboard.locator('#clearDraft').click()
    async def empty_editor():return state()['editor'].get('payload')=={}
    await wait(empty_editor)
    await dashboard.locator('#undoClearDraft').wait_for(state='visible')
    assert await dashboard.locator('#title').input_value()==''
    assert await dashboard.locator('#description').input_value()==''
    assert await dashboard.locator('#videoPlayer').get_attribute('src') is None
    assert state()['editor']['platforms']==original_editor['platforms']
    assert video.is_file() and (media/'封面.png').is_file()
    checks['checks']['homepage_one_click_clear_preserves_selection_and_material_files']=True
    await dashboard.locator('#undoClearDraft').click()
    async def restored_editor():return state()['editor']['payload']==original_editor['payload']
    await wait(restored_editor)
    await dashboard.locator('#undoClearDraft').wait_for(state='hidden')
    assert await dashboard.locator('#title').input_value()=='本地验收作品'
    checks['checks']['homepage_clear_undo_restores_material']=True
    await dashboard.locator('#clearDraft').click();await wait(empty_editor)
    await dashboard.locator('#undoClearDraft').wait_for(state='visible')
    await dashboard.reload();await dashboard.locator('#clearDraft').wait_for(state='visible')
    assert await dashboard.locator('#title').input_value()==''
    assert await dashboard.locator('#videoPlayer').get_attribute('src') is None
    assert state()['editor']['payload']=={}
    checks['checks']['homepage_cleared_draft_stays_empty_after_reload']=True
    api('/api/editor',original_editor)
    await dashboard.reload();await dashboard.locator('#title').wait_for()
    await wait(restored_editor);await wait(imported)
    quit_app();await asyncio.to_thread(proc.wait,20)
    proc=launch();await wait(ready);browser=await connect(p)
    shell=next(page for context in browser.contexts for page in context.pages if page.url.endswith('/shell.html'))
    for platform in PLATFORMS:
     await shell.locator(f'.nav[data-platform="{platform}"]').click()
     async def native_page():return next((page for context in browser.contexts for page in context.pages if page.url.endswith('/'+platform)),None)
     page=await wait(native_page)
     assert await page.evaluate("localStorage.getItem('package_session')")==platform
     assert await page.evaluate("document.cookie.includes('package_session='+localStorage.getItem('package_session'))")
    checks['checks']['seven_sessions_persist_after_actual_restart']=True
    assert state()['editor']['payload']['title']=='本地验收作品'
    checks['checks']['material_edits_persist_after_restart']=True
    quit_app();await asyncio.to_thread(proc.wait,20)
   final_files={str(p.relative_to(resources)) for p in resources.rglob('*') if p.is_file() or p.is_symlink()}
   assert final_files==initial_files, {'files_added_to_application': sorted(final_files-initial_files)}
   checks['checks']['runtime_does_not_write_inside_application']=True
   if sys.platform=='darwin':
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
    checks['checks']['mac_signature_remains_valid_after_runtime']=True
   checks['passed']=True
  except BaseException:
   checks['passed']=False;checks['log']=(folder/'app.log').read_text(encoding='utf-8',errors='replace')[-12000:]
   raise
  finally:
   if proc is not None and proc.poll() is None:proc.terminate();proc.wait(timeout=10)
   if backend_pid:
    from runtime_support import process_alive
    if process_alive(backend_pid):
     if os.name=='nt':subprocess.run(['taskkill','/PID',str(backend_pid),'/T','/F'],capture_output=True)
     else:os.kill(backend_pid,15)
     await asyncio.sleep(.4)
   log.close();server.shutdown();server.server_close()
   Path(output).parent.mkdir(parents=True,exist_ok=True)
   # Report contains only fixture results, not temporary profile paths or logs on success.
   Path(output).write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
 return checks
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--app',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
 print(json.dumps(asyncio.run(run(args.app,args.output)),ensure_ascii=False))
