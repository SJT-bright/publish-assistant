const {app, BrowserWindow, WebContentsView, ipcMain, dialog, Menu, session, shell, net, clipboard, ClipboardItem, nativeImage} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {createBackendManager, waitForDebuggingEndpoint, withTimeout, recoverConnection} = require(path.join(__dirname, 'backend-health.cjs'));
const {recoverOfficialMigration}=require(path.join(__dirname,'platform-migration.cjs'));
const {runPageOperation,notifyPageUnload}=require(path.join(__dirname,'page-operations.cjs'));
const {canPublishManually,validatePublicationMode}=require(path.join(__dirname,'publication-mode.cjs'));
const {createCoverClipboard}=require(path.join(__dirname,'cover-clipboard.cjs'));
const {createNativeViewPainter} = require(path.join(__dirname, 'native-view-paint.cjs'));
const {createPrivateIO} = require(path.join(__dirname, 'private-io.cjs'));
const {REPOSITORY_URL,createGitHubUpdater} = require(path.join(__dirname,'github-updater.cjs'));
const {prepareInstallation} = require(path.join(__dirname,'update-installer.cjs'));

const config = app.isPackaged ? require(path.join(__dirname, 'runtime.json')) : {root: path.resolve(__dirname, '..')};
const {resolveRuntime} = require(path.join(__dirname,'runtime-paths.cjs'));
const runtime = resolveRuntime({packaged:app.isPackaged, config, resourcesPath:process.resourcesPath,
  sourceRoot:path.resolve(__dirname, '..'), appData:app.getPath('appData'), downloads:app.getPath('downloads'),
  platform:process.platform, env:process.env});
const root = runtime.root;
function assertIsolatedCandidate() {
  if (!String(root).endsWith('/发布助手-Grok并行-20261003')) return;
  const realProfile = path.resolve(path.join(app.getPath('appData'), '视频草稿工作台'));
  const profile = process.env.SAU_DESKTOP_PROFILE || '';
  const stateDir = process.env.SAU_DESKTOP_STATE_DIR || '';
  const privateDirEnv = process.env.SAU_WORKBENCH_PRIVATE || '';
  const port = process.env.SAU_WORKBENCH_PORT || '';
  const descriptorEnv = process.env.SAU_EMBEDDED_DESCRIPTOR || '';
  const touchesReal = [profile, stateDir, privateDirEnv, descriptorEnv].some(value => {
    if (!value) return false;
    const resolved = path.resolve(value);
    return resolved === realProfile || resolved.startsWith(realProfile + path.sep);
  });
  if (!profile || !stateDir || !privateDirEnv || !port || !descriptorEnv || String(port) === '18761' || touchesReal) {
    console.error('隔离候选不会启动：需要独立的 SAU_DESKTOP_PROFILE、SAU_DESKTOP_STATE_DIR、SAU_WORKBENCH_PRIVATE、SAU_WORKBENCH_PORT（不能是 18761）和 SAU_EMBEDDED_DESCRIPTOR，且不能落在正式浏览器资料目录。');
    process.exit(1);
  }
}
assertIsolatedCandidate();
const privateDir = runtime.privateDir;
const descriptor = path.join(privateDir, 'embedded-browser.json');
const progressModel = require(path.join(app.isPackaged ? __dirname : root, 'ui-assets/progress-model.js'));
const ownerStateScript = fs.readFileSync(path.join(app.isPackaged ? __dirname : root, 'ui-assets/session-owner.js'), 'utf8').trim();
const backendPort = Number(process.env.SAU_WORKBENCH_PORT || 18761);
const backendURL = `http://127.0.0.1:${backendPort}`;
let coverClipboard;
function copyCover(path){coverClipboard ||= createCoverClipboard({clipboard,ClipboardItem,nativeImage,fetch:globalThis.fetch,baseURL:backendURL});return coverClipboard.copy(path);}
const PRODUCT_NAME = '发布助手';
const LEGACY_PROFILE_NAME = '视频草稿工作台';
const profileDir = process.env.SAU_DESKTOP_PROFILE || path.join(app.getPath('appData'), LEGACY_PROFILE_NAME);
fs.mkdirSync(profileDir,{recursive:true,mode:0o700});
// 内部进程名保留为存储身份兼容约束，不只是 userData 路径。Electron v44.5.1 的
// shell/browser/electron_browser_main_parts.cc 在 PostCreateMainMessageLoop
// 用 Browser::GetName() 作为 macOS 安全存储的服务名和账户名。旧正式包这里是
// 「视频草稿工作台」。启动早期设为 LEGACY_PROFILE_NAME，之后不再调用 setName，
// 也不再改 app.name。当前正式包的 CookieEncryption fuse 为关闭，这次不改该配置。
// 关于、隐藏、退出保留 role，显式 label 和关于面板使用 PRODUCT_NAME。
// 窗口标题、页标题和系统 Bundle 显示名仍是发布助手。
app.setPath('userData', profileDir);
app.setName(LEGACY_PROFILE_NAME);
app.setPath('userData', profileDir);
app.setAboutPanelOptions({applicationName: PRODUCT_NAME, applicationVersion:app.getVersion(), version:config.build || '开发版'});
app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
// Chromium chooses a free local port and writes DevToolsActivePort into userData.
app.commandLine.appendSwitch('remote-debugging-port', '0');

const platforms = {
  douyin: {name: '抖音', url: 'https://creator.douyin.com/creator-micro/content/upload'},
  xiaohongshu: {name: '小红书', url: 'https://creator.xiaohongshu.com/publish/publish?target=video'},
  tencent: {name: '视频号', url: 'https://channels.weixin.qq.com/platform/post/create'},
  bilibili: {name: 'B站', url: 'https://member.bilibili.com/platform/upload/video/frame'},
  kuaishou: {name: '快手', url: 'https://cp.kuaishou.com/article/publish/video'},
  // Use only the verified creator entry pages; do not guess editor deep links.
  toutiao: {name: '今日头条', url: 'https://mp.toutiao.com/', automation_ready: false,
    readiness_message: '视频草稿适配待完成，可打开平台手动登录。'},
  xigua: {name: '西瓜视频', url: 'https://studio.ixigua.com/', automation_ready: false,
    readiness_message: '视频草稿适配待完成，可打开平台手动登录。'},
};
if(process.env.SAU_DESKTOP_TEST==='1' && process.env.SAU_DESKTOP_TEST_URL){
  for(const [id,p] of Object.entries(platforms))p.url=`${process.env.SAU_DESKTOP_TEST_URL}/${id}`;
}
const views = new Map();
const popupViews = [];
// Only platform WebContents are zoomed; the shell and material editor retain
// their normal size. A popup shares the preference of its parent platform.
const pageZoomPreferences = new Map();
const PAGE_ZOOM_MIN = 0.65, PAGE_ZOOM_MAX = 1;
let win, dashboard, bridge, endpoint, activeLease = null, selected = 'workbench', notice = '', quitting = false, flushing = false, storageFlushed = false;
let connectionStatus = 'connecting', connectionErrorCode = null, connectionPromise = null, bridgePromise = null;
let selectionRevision = 0;
let githubUpdater, updateInstallBusy = false;
let sessionMonitorTimer, sessionMonitorBusy = false;
let publicationMode='draft', publicationPermit=null;
let pageOperation=null, pageOperationController=null;
let progressMonitorTimer, progressMonitorBusy = false, taskProgress = null;
const PROGRESS_VIEW_TOP = 216;
const progressStages = ['session','video','upload','content','cover','save','verify'];
const progressStatuses = new Set(['pending','running','login_verified','local_draft_verified','platform_draft_verified',
  'platform_snapshot_verified','saved_unverified','result_unknown','risk_paused','platform_restricted','adapter_mismatch',
  'needs_reauthentication','account_mismatch','failed','cancelled','published_submission_verified','published_unverified','publication_blocked']);
const bridgeToken = crypto.randomBytes(32).toString('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function privateAccessNotice(waiting) {
  if(quitting || !win || win.isDestroyed() || activeLease) return;
  notice=waiting ? '正在访问草稿资料目录，可稍后重新连接工作台。' :
    '无法写入工作台运行记录，请检查资料目录是否可访问后重新连接工作台。';
  // Do not call update(): reporting a write error must not enqueue another write.
  win.webContents.send('browser-state',shellState());
}
const privateIO=createPrivateIO({dir:privateDir,io:fs.promises,pid:process.pid,shouldContinue:()=>!quitting,
  onWait:()=>privateAccessNotice(true),onStatusError:()=>privateAccessNotice(false)});

function writeStatus() {
  if (!win || win.isDestroyed()) return;
  privateIO.status({
    status:connectionStatus==='ready'?'ready':connectionStatus==='connecting'?'connecting':'connection_failed',
    connection:{status:connectionStatus,errorCode:connectionErrorCode}, pid:process.pid, version:app.getVersion(), build:config.build || null, bundlePath:app.getPath('exe'),
    selected, windowVisible:win.isVisible(), windowFocused:win.isFocused(), windowMinimized:win.isMinimized(),
    platformCount:Object.keys(platforms).length,
    browser:'embedded-chromium', notice, active:!!activeLease,
    activePlatform:activeLease?.platform || null, activeAction:activeLease?.action || null,
    platformPages:[...views].map(([id,v])=>({id, url:v.webContents.getURL(), loading:v.webContents.isLoading(),
      visible:v.getVisible(), attached:win.contentView.children.includes(v), bounds:v.getBounds(),
      focused:v.webContents.isFocused(), backgroundThrottling:v.webContents.getBackgroundThrottling()})),
    date:new Date().toISOString(),
  });
}
function creatorPageMatches(id, url) {
  try {
    const target = new URL(url), creator = new URL(platforms[id].url);
    return target.origin === creator.origin;
  } catch { return false; }
}
async function monitorSessions() {
  if (quitting || sessionMonitorBusy || ['draft','publish'].includes(activeLease?.action)) return;
  sessionMonitorBusy = true;
  try {
    const checkedAt = Date.now() / 1000;
    const statuses = {};
    for (const id of Object.keys(platforms)) {
      if (quitting || ['draft','publish'].includes(activeLease?.action)) return;
      const wc = views.get(id)?.webContents;
      const status = { status: 'needs_confirmation', identity: null, checked_at: checkedAt };
      statuses[id] = status;
      // A profile/session record does not prove that the current owner is signed
      // in. Only inspect a page that already exists on this creator's origin.
      if (!wc || wc.isDestroyed() || wc.isLoading() || !creatorPageMatches(id, wc.getURL())) continue;
      const urlBefore = wc.getURL();
      let owner;
      try {
        let timeout;
        try {
          owner = await Promise.race([
            wc.executeJavaScript(ownerStateScript + '(' + JSON.stringify(id) + ')'),
            new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('Owner state read timed out')), 1500); })
          ]);
        } finally { clearTimeout(timeout); }
      } catch { continue; }
      if (quitting || ['draft','publish'].includes(activeLease?.action)) return;
      if (wc.isDestroyed() || wc.isLoading() || wc.getURL() !== urlBefore) continue;
      if (owner?.loggedOut === true) status.status = 'needs_reauthentication';
      else if (typeof owner?.owner === 'string' && owner.owner.trim() && owner.owner.length <= 512) {
        status.status = 'login_verified';
        status.identity = crypto.createHash('sha256').update(id + ':' + owner.owner).digest('hex');
        const capabilities = {};
        for (const field of ['tags_max', 'caption_max']) {
          const value = owner.capabilities?.[field];
          if (Number.isSafeInteger(value) && value > 0) capabilities[field] = value;
        }
        if (Object.keys(capabilities).length) status.capabilities = capabilities;
      }
    }
    if (quitting || ['draft','publish'].includes(activeLease?.action)) return;
    await privateIO.session({pid:process.pid,checked_at:checkedAt,platforms:statuses},
      ()=>!quitting && !['draft','publish'].includes(activeLease?.action));
  } catch {
    // A failed read leaves the last observation to expire; it never changes
    // account markers, the user's current page, or a pending draft operation.
  } finally { sessionMonitorBusy = false; }
}
function startSessionMonitor() {
  if(sessionMonitorTimer) return;
  void monitorSessions();
  sessionMonitorTimer = setInterval(() => { void monitorSessions(); }, 5000);
  sessionMonitorTimer.unref();
}
// Only this deliberately small, read-only response reaches the shell. Never
// forward /api/state, its token, expected payloads, accounts, or asset paths.
function safeProgressMessage(value) {
  if (typeof value !== 'string') return '';
  if (/\b(?:token|uid|cookie|authorization|password|payload|account_identity)\b/i.test(value) || /[A-Za-z0-9_-]{24,}/.test(value)) return '';
  return value.replace(/[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s，。；]+|(?:\/|[A-Za-z]:\\)[^\s，。；]+/g,'[详情见运行记录]')
    .replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,160);
}
function progressTime(value) {return typeof value==='number' && Number.isFinite(value) && value>0 ? value : null;}
function publicTaskProgress(data) {
  if (!data || data.available!==true) throw new Error('Progress response unavailable');
  if (data.job===null) return {available:true,connected:true,job:null,observed_at:Date.now()/1000};
  const job=data.job;
  if (!job || !platforms[job.platform] || !['login','draft','publish'].includes(job.action) ||
      typeof job.id!=='string' || !/^[A-Za-z0-9_-]{1,80}$/.test(job.id)) throw new Error('Invalid progress response');
  const count=Number.isSafeInteger(job.platform_count) && job.platform_count>=1 && job.platform_count<=7 ? job.platform_count : 1;
  const index=Number.isSafeInteger(job.platform_index) && job.platform_index>=1 && job.platform_index<=count ? job.platform_index : 1;
  const current=job.progress;
  const stepStates=new Set(['pending','running','waiting','completed','skipped','failed','cancelled','unknown','unverified']);
  const progress=current && progressStages.includes(current.stage) ? {
    stage:current.stage,state:stepStates.has(current.state)?current.state:'unknown',
    started_at:progressTime(current.started_at),updated_at:progressTime(current.updated_at),
    completed:progressStages.filter(stage=>Array.isArray(current.completed) && current.completed.includes(stage)),
    skipped:progressStages.filter(stage=>Array.isArray(current.skipped) && current.skipped.includes(stage)),
    operations:progressModel.clean(current.operations),
    ...(Array.isArray(current.active_stages) && current.active_stages.length>1 ? {active_stages:progressStages.filter(stage=>current.active_stages.includes(stage))} : {}),
  } : null;
  return {available:true,connected:true,observed_at:Date.now()/1000,job:{
    id:job.id,action:job.action,active:job.active===true,platform:job.platform,
    platform_name:platforms[job.platform].name,platform_index:index,platform_count:count,
    status:progressStatuses.has(job.status)?job.status:'result_unknown',message:safeProgressMessage(job.message),progress,
  }};
}
function legacyTaskProgress() {
  if (!activeLease) return null;
  return {id:'legacy-'+activeLease.id,action:activeLease.action,active:true,platform:activeLease.platform,
    platform_name:platforms[activeLease.platform].name,platform_index:1,platform_count:1,
    status:'running',message:'当前任务尚未上报具体步骤',progress:null};
}
function visibleTaskProgress() {
  if (activeLease && (!taskProgress?.job || taskProgress.job.platform!==activeLease.platform ||
      taskProgress.job.action!==activeLease.action || !taskProgress.job.active)) {
    return {available:taskProgress?.available===true,connected:taskProgress?.connected!==false,
      observed_at:taskProgress?.observed_at || Date.now()/1000,job:legacyTaskProgress()};
  }
  return taskProgress;
}
async function monitorProgress() {
  if (quitting || progressMonitorBusy) return;
  progressMonitorBusy=true;
  try {
    const response=await fetch(`${backendURL}/api/progress`,{signal:AbortSignal.timeout(1800),cache:'no-store'});
    if (quitting) return;
    if (response.status===404) taskProgress={available:false,connected:true,job:null,observed_at:Date.now()/1000};
    else {
      if (!response.ok) throw new Error('Progress request failed');
      const data=await response.json();
      if (quitting) return;
      taskProgress=publicTaskProgress(data);
    }
  } catch {
    if (quitting) return;
    taskProgress={available:taskProgress?.available===true,connected:false,
      job:taskProgress?.job || legacyTaskProgress(),observed_at:taskProgress?.observed_at || Date.now()/1000};
  } finally {progressMonitorBusy=false;}
  update();
}
function startProgressMonitor() {
  if(progressMonitorTimer) return;
  void monitorProgress();
  progressMonitorTimer=setInterval(()=>{void monitorProgress();},1000);
  progressMonitorTimer.unref();
}
function selectedPopup() {return popupViews.filter(p=>p.platform===selected).at(-1);}
function navigationLocked() {return ['draft','publish'].includes(activeLease?.action);}
function platformZoomFactor(id,bounds) {
  const preference=pageZoomPreferences.get(id);
  if(preference?.fit===false) return preference.factor;
  const referenceHeight=id==='tencent'?960:850;
  // Round down so CSS viewport rounding cannot cut off a reference-size footer.
  const fitted=Math.floor(Math.min(1,bounds.width/1280,bounds.height/referenceHeight)*1000)/1000;
  return Math.max(PAGE_ZOOM_MIN,Math.min(PAGE_ZOOM_MAX,fitted));
}
function platformBounds() {
  const [width,height]=win.getContentSize();
  const job=visibleTaskProgress()?.job;
  const shown=!!job && (job.active===true || selected==='workbench' || selected===job.platform);
  const top=shown ? PROGRESS_VIEW_TOP : 76;
  return {x:224,y:top,width:Math.max(1,width-224),height:Math.max(1,height-top)};
}
const nativeViewPainter=createNativeViewPainter({bounds:platformBounds,settled:update,
  canPaint:view=>!quitting && win && !win.isDestroyed() && win.isVisible() && !win.isMinimized() &&
    currentView()===view && view.getVisible() && win.contentView.children.includes(view)});
function applyPlatformZoom(view,id,bounds) {
  const wc=view.webContents;
  if(!wc || wc.isDestroyed()) return;
  const factor=platformZoomFactor(id,bounds);
  if(Math.abs(wc.getZoomFactor()-factor)>0.0001) wc.setZoomFactor(factor);
}
function pageZoomState() {
  const view=selectedPopup()?.view || views.get(selected);
  const available=!!platforms[selected] && !!view && !view.webContents.isDestroyed();
  return {available,factor:available?platformZoomFactor(selected,platformBounds()):1,
    fit:available && pageZoomPreferences.get(selected)?.fit!==false,min:PAGE_ZOOM_MIN,max:PAGE_ZOOM_MAX};
}
function setPageZoom(factor=null) {
  if(!pageZoomState().available) throw new Error('请先打开一个平台页面。');
  if(factor!==null && (typeof factor!=='number' || !Number.isFinite(factor) || factor<PAGE_ZOOM_MIN || factor>PAGE_ZOOM_MAX)) {
    throw new Error('页面缩放需在 65% 至 100% 之间。');
  }
  if(factor===null) pageZoomPreferences.delete(selected);
  else pageZoomPreferences.set(selected,{fit:false,factor});
  // This changes presentation only, so an active draft lease remains untouched.
  update();
  return shellState();
}
function shellState() {
  const popup=selectedPopup();
  const v = popup?.view || views.get(selected);
  return {selected, version:app.getVersion(), build:config.build || null,
    softwareUpdate:{...githubUpdater?.state(),installing:updateInstallBusy}, repositoryURL:REPOSITORY_URL,
    platforms:Object.entries(platforms).map(([id,p])=>({id,name:p.name,
      ...(p.automation_ready===false?{automation_ready:false,readiness_message:p.readiness_message}: {})})),
    active:!!activeLease, activePlatform:activeLease?.platform || null,
    activeAction:activeLease?.action || null, navigationLocked:navigationLocked(),
    publicationMode, manualPublishAllowed:canPublishManually(publicationMode,activeLease,visibleTaskProgress()?.job?.active),
    pageOperation, notice, connection:{status:connectionStatus,errorCode:connectionErrorCode}, popup:!!popup,taskProgress:visibleTaskProgress(),zoom:pageZoomState(),
    ...(process.env.SAU_DESKTOP_TEST==='1' ? {viewVisibility:{workbench:dashboard.getVisible(),
      platforms:Object.fromEntries([...views].map(([id,view])=>[id,view.getVisible()])),
      popups:popupViews.map(p=>({platform:p.platform,visible:p.view.getVisible()}))}} : {}),
    url:v?.webContents.getURL() || '', loading:v?.webContents.isLoading() || false,
    canGoBack:v?.webContents.navigationHistory.canGoBack() || false};
}
function update() {
  for(const view of [...views.values(),...popupViews.map(p=>p.view)])void syncPublicationMode(view);
  if (win && !win.isDestroyed()) {layout();win.webContents.send('browser-state', shellState());}
  writeStatus();
}
function layout() {
  if (!win || win.isDestroyed()) return;
  const bounds=platformBounds();
  dashboard.setBounds(bounds);
  for (const [id,view] of views) {view.setBounds(bounds);applyPlatformZoom(view,id,bounds);}
  for (const popup of popupViews) {popup.view.setBounds(bounds);applyPlatformZoom(popup.view,popup.platform,bounds);}
}
function setViewVisibility(view,visible) {
  // A hidden but still attached WebContentsView can remain in macOS's first
  // responder/AX hierarchy. Keep its live WebContents and session, but only
  // mount the page currently displayed in the native window.
  const attached=win.contentView.children.includes(view);
  if(visible && !attached) win.contentView.addChildView(view);
  view.setVisible(visible);
  if(!visible && attached) win.contentView.removeChildView(view);
  if(!visible)nativeViewPainter.cancel(view);
  // Defer one frame until showView has installed final bounds and zoom. Only
  // a mount transition schedules this; progress polls never keep repainting.
  if(visible && !attached)nativeViewPainter.queue(view);
}
function currentView() {
  return selected==='workbench'?dashboard:selectedPopup()?.view || views.get(selected);
}
function showView(id,focus=false) {
  if(focus) {selectionRevision++;pageOperationController?.abort();pageOperation=null;}
  selected = id;
  setViewVisibility(dashboard,id==='workbench');
  const popup=selectedPopup();
  for(const [key,view] of views) setViewVisibility(view,id===key && popup?.platform!==id);
  for(const p of popupViews)setViewVisibility(p.view,p===popup && p.platform===id);
  layout(); update();
  // Only an explicit page switch/acquire/close moves keyboard focus. Polls,
  // resize and background load events must not steal it from the user's editor.
  if(focus) {
    const wc=currentView()?.webContents;
    if(wc && !wc.isDestroyed()) wc.focus();
  }
}
async function settleSelectedFocus(view,revision) {
  const current=()=>!quitting && revision===selectionRevision && currentView()===view && view.getVisible() && !view.webContents.isDestroyed();
  if(!current()) return false;
  view.webContents.focus();
  const deadline=Date.now()+1200;
  do {
    if(!current()) return false;
    // Do not activate the application while the user is in another app. The
    // selected native child is already mounted for the next window activation.
    if(!win.isFocused()) return true;
    const wc=view.webContents;
    // Native focus is synchronous, while renderer focus delivery is
    // asynchronous. CDP clients may emulate document focus on hidden pages,
    // so their renderer booleans cannot override macOS first-responder state.
    const dom=await Promise.race([wc.executeJavaScript('document.hasFocus()'),sleep(50).then(()=>null)]).catch(()=>null);
    const all=[...new Set([dashboard,...views.values(),...popupViews.map(p=>p.view)])];
    if(!current()) return false;
    if(wc.isFocused() && dom===true && all.every(candidate=>candidate===view || candidate.webContents.isDestroyed() || !candidate.webContents.isFocused())) return true;
    await sleep(10);
  } while(Date.now()<deadline);
  return false;
}
function validRemoteURL(url) {
  try { return new URL(url).protocol==='https:' || (process.env.SAU_DESKTOP_TEST==='1' && new URL(url).hostname==='127.0.0.1'); }
  catch { return false; }
}
function browserUserAgent(ua) {
  // Keep platform pages free of the desktop shell token for both the old and new product names.
  return ua.replace(/\sElectron\/\S+/g,'').replace(/\s(?:视频草稿工作台|发布助手)\/\S+/g,'').replace(/[ \t]{2,}/g,' ').trim();
}
const publicationPermissions=new WeakMap();
async function syncPublicationMode(view,force=false){
  if(!view||view.webContents.isDestroyed())return;
  const wc=view.webContents;
  const scoped=publicationPermit && publicationPermit.lease===activeLease?.id && activeLease?.action==='publish' && publicationPermit.view===view;
  const allowed=scoped || canPublishManually(publicationMode,activeLease,visibleTaskProgress()?.job?.active);
  const key=scoped?'scoped:'+publicationPermit.lease:allowed?'manual':'draft';
  if(!force&&publicationPermissions.get(view)===key)return;
  publicationPermissions.set(view,key);
  try{
    wc.send('publication-mode',scoped?{once:true,lease:publicationPermit.lease}:allowed);
    for(const frame of wc.mainFrame.framesInSubtree || [wc.mainFrame]){
      try{await frame.executeJavaScript('window.__sauDraftGuardControl?.setEnabled('+String(!allowed)+')');}catch(_){/* Detached or not yet loaded. */}
    }
  }catch(_){publicationPermissions.delete(view);/* Page may close between the state check and send. */}
}
async function setPublicationMode(mode){
  publicationMode=validatePublicationMode(mode,activeLease,visibleTaskProgress()?.job?.active);
  notice=publicationMode==='manual_publish'?'网页发表已开启：在平台页面确认内容后，点击「发表 / 发布」。':'已切回草稿模式。';
  await Promise.all([...views.values(),...popupViews.map(p=>p.view)].map(syncPublicationMode));
  update();return shellState();
}
function configureRemote(view, platform) {
  const wc = view.webContents;
  wc.setUserAgent(browserUserAgent(wc.getUserAgent()));
  wc.on('dom-ready',()=>{void syncPublicationMode(view,true);});
  wc.on('did-frame-finish-load',()=>{void syncPublicationMode(view,true);});
  wc.ipc.on('draft-publish-blocked',event=>{
    // Only the displayed page's main frame may show this fixed mode notice.
    // No renderer-supplied text or command crosses the platform boundary.
    if(quitting || !win || win.isDestroyed() || wc.isDestroyed()
      || event.sender!==wc || event.senderFrame!==wc.mainFrame
      || currentView()!==view || !view.getVisible())return;
    notice='当前仅保存草稿，已拦截公开投稿。要在'+platforms[platform].name+'发表，请点击上方「开启网页发表」，再点击平台的投稿或发布按钮。';
    update();
  });
  wc.on('will-navigate', (event,url)=>{if(!validRemoteURL(url)){event.preventDefault();notice='这个链接暂时无法在应用内打开。';update();}});
  wc.on('will-prevent-unload',event=>{
    // This event's preventDefault permits leaving. Preserve the page unless
    // the user explicitly approves, and never interrupt a draft/background page.
    const canConfirm=()=>!quitting && !navigationLocked() && win && !win.isDestroyed()
      && !wc.isDestroyed() && currentView()===view && view.getVisible();
    if(!canConfirm()){notifyPageUnload(wc,'cancel');return;}
    notifyPageUnload(wc,'waiting');
    let choice;
    try {
      choice=dialog.showMessageBoxSync(win,{
        type:'question',title:'离开平台页面',
        message:'平台页面有未保存的素材，是否离开？',
        buttons:['继续离开','保留页面'],defaultId:1,cancelId:1,noLink:true
      });
    } catch {notifyPageUnload(wc,'cancel');return;}
    // The native dialog can run a nested event loop: recheck the task and view.
    if(choice===0 && canConfirm()) {notifyPageUnload(wc,'continue');event.preventDefault();}
    else notifyPageUnload(wc,'cancel');
  });
  // Keep window.open/OAuth relationships intact while putting its WebContents in this window.
  wc.setWindowOpenHandler(({url}) => {
    if(!validRemoteURL(url)) return {action:'deny'};
    return {action:'allow',overrideBrowserWindowOptions:{webPreferences:{show:true,focusOnNavigation:false}}, createWindow:(options)=>{
      const child = new WebContentsView({webContents:options.webContents,webPreferences:{...options.webPreferences,
        session:wc.session, nodeIntegration:false, contextIsolation:true, sandbox:true,show:true,focusOnNavigation:false,
        preload:path.join(__dirname,'platform-preload.cjs')}});
      configureRemote(child,platform);
      popupViews.push({view:child, platform});
      child.setVisible(false);
      showView(selected,selected===platform);
      child.webContents.on('destroyed',()=>{
        const wasCurrent=currentView()===child;
        const i=popupViews.findIndex(p=>p.view===child);if(i>=0)popupViews.splice(i,1);
        if(!quitting){setViewVisibility(child,false);showView(selected,wasCurrent);}
      });
      child.webContents.once('did-finish-load',()=>{showView(selected);});
      return child.webContents;
    }};
  });
  wc.on('did-start-loading',update);
  wc.on('did-stop-loading',update);
  wc.on('did-navigate',()=>{notice='';update();});
  wc.on('did-fail-load',(_e,code,description,url,isMainFrame)=>{
    if(code!==-3 && isMainFrame){notice=`页面未能加载：${description}。可点击重新加载。`;update();}
  });
  wc.on('render-process-gone',()=>{notice='平台页面已停止，请重新加载。';update();});
}
function getPlatformView(id) {
  if(!platforms[id])throw new Error('未知平台');
  if(views.has(id))return views.get(id);
  const ses=session.fromPartition(`persist:platform-${id}`);
  ses.setPermissionRequestHandler((_wc,permission,callback)=>callback(permission==='clipboard-sanitized-write'));
  // Electron 44.5.1 creates WebContentsView with show:false by default. On
  // macOS that can leave its renderer clock and native input frozen despite
  // setVisible(true). Initialize the renderer shown, then use View visibility
  // to control which persistent platform is actually attached to the window.
  const view=new WebContentsView({webPreferences:{show:true,session:ses,nodeIntegration:false,contextIsolation:true,
    sandbox:true,backgroundThrottling:false,focusOnNavigation:false,preload:path.join(__dirname,'platform-preload.cjs')}});
  configureRemote(view,id);
  views.set(id,view);view.setVisible(false);layout();
  return view;
}
async function selectPlatform(id, navigate=false, deferNavigation=false) {
  if(id==='workbench'){showView(id,true);await settleSelectedFocus(dashboard,selectionRevision);return;}
  if(navigationLocked() && activeLease.platform!==id)throw new Error('正在处理草稿，请先等待或在素材工作台停止任务。');
  const view=getPlatformView(id);showView(id,true);notice='';update();
  const revision=selectionRevision,displayed=currentView();
  if(!deferNavigation && (navigate || !view.webContents.getURL())) {
    try { await view.webContents.loadURL(platforms[id].url); }
    catch(error) {
      // The official Xigua portal replaces its navigation with the Douyin
      // creator portal. Electron rejects the replaced load with ERR_ABORTED
      // even when the replacement document has loaded. Accept only this known
      // migration with a real document; genuine load failures still surface.
      if(!await recoverOfficialMigration(id,platforms[id].url,error,view.webContents)) throw error;
    }
  }
  if(!deferNavigation) await settleSelectedFocus(displayed,revision);
  return view;
}
const backendManager=createBackendManager({baseURL:backendURL,port:backendPort,shouldContinue:()=>!quitting,
  start:async()=>{
    // Existing listeners are never stopped. The manager only reaches this
    // after an explicit connection refusal and permits one spawn per app run.
    const log=await privateIO.openLog();
    let child;
    try {
      if(quitting)throw Object.assign(new Error('工作台已关闭。'),{code:'CONNECTION_CLOSED',backendNotStarted:true});
      child=spawn(runtime.command[0],runtime.command.slice(1),{
        cwd:root,detached:true,windowsHide:true,stdio:['ignore',log.fd,log.fd],env:{...process.env,
          PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',SAU_MEDIA_TOOLS:runtime.tools||'',
          NO_PROXY:[process.env.NO_PROXY||'', '127.0.0.1','localhost','::1'].join(','),
          no_proxy:[process.env.no_proxy||'', '127.0.0.1','localhost','::1'].join(','),
          SAU_WORKBENCH_PORT:String(backendPort),SAU_WORKBENCH_PRIVATE:privateDir,SAU_EMBEDDED_DESCRIPTOR:descriptor}});
    } finally {void privateIO.close(log);}
    const spawned=new Promise((resolve,reject)=>{
      child.once('error',reject);
      child.once('spawn',()=>{child.unref();resolve();});
    });
    await withTimeout(()=>spawned,2000);
  }});
function startBackend() {return backendManager.ensureReady();}
function debuggingEndpoint() {
  return waitForDebuggingEndpoint({readActivePort:()=>fs.readFileSync(path.join(profileDir,'DevToolsActivePort'),'utf8'),
    shouldContinue:()=>!quitting});
}
function startBridge() {
  if(bridgePromise) return bridgePromise;
  if(bridge?.listening) return Promise.resolve();
  if(!bridgePromise) bridgePromise=createBridge().finally(()=>{bridgePromise=null;});
  return bridgePromise;
}
async function createBridge() {
  endpoint=await debuggingEndpoint();
  bridge=http.createServer(async(req,res)=>{
    const respond=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
    if(req.method!=='POST' || req.headers['x-embedded-token']!==bridgeToken || req.headers.origin) return respond({error:'Forbidden'},403);
    try {
      let body='';for await(const chunk of req){body+=chunk;if(body.length>8192)throw new Error('请求过大');}
      const data=JSON.parse(body);
      if(req.url==='/prepare') {
        if(updateInstallBusy || quitting)throw new Error('正在安装软件更新，请稍后再开始任务。');
        if(!platforms[data.platform] || !['login','draft','publish'].includes(data.action))throw new Error('操作无效');
        if(activeLease)throw new Error('内嵌浏览器正在处理另一项任务。');
        const lease={platform:data.platform, action:data.action,id:crypto.randomUUID()};
        activeLease=lease;update();
        await Promise.all([...views.values()].map(syncPublicationMode));
        try {
          // Attach the execution observer before creator navigation. Keep the existing page.
          const view=await selectPlatform(data.platform,false,true);
          const revision=selectionRevision;
          if(currentView()!==view) throw new Error('请先关闭当前平台的登录子页，再继续任务。');
          // A newly created WebContents must have a document before CDP can attach.
          if(!view.webContents.getURL())await view.webContents.loadURL('about:blank');
          const debug=view.webContents.debugger;
          debug.attach('1.3');
          let target;
          try {target=await debug.sendCommand('Target.getTargetInfo');}finally{debug.detach();}
          if(activeLease!==lease || !await settleSelectedFocus(view,revision)) throw new Error('当前平台页面已切换或未获得焦点，请回到该平台后继续任务。');
          lease.view=view;
          respond({endpoint,targetId:target.targetInfo.targetId,lease:lease.id});
        } catch(e){if(activeLease===lease)activeLease=null;update();throw e;}
      } else if(req.url==='/publish-permit') {
        if(activeLease?.id!==data.lease || activeLease?.action!=='publish' || activeLease?.platform!==data.platform
          || !activeLease.view || activeLease.view!==currentView() || activeLease.permitGrantedOnce || activeLease.view.webContents.isDestroyed())throw new Error('投稿许可与当前任务页面不匹配');
        activeLease.permitGrantedOnce=true;
        publicationPermit={lease:activeLease.id,view:activeLease.view};
        await syncPublicationMode(activeLease.view,true);update();respond({ok:true});
      } else if(req.url==='/publish-revoke') {
        if(activeLease?.id!==data.lease || activeLease?.platform!==data.platform)throw new Error('任务标识已失效');
        publicationPermit=null;
        await syncPublicationMode(activeLease.view,true);update();respond({ok:true});
      } else if(req.url==='/cancel') {
        if(activeLease && activeLease.platform!==data.platform)throw new Error('平台与当前任务不匹配');
        if(activeLease){const ses=views.get(activeLease.platform).webContents.session;ses.flushStorageData();await ses.cookies.flushStore();}
        publicationPermit=null;activeLease=null;update();
        await Promise.all([...views.values()].map(syncPublicationMode));respond({ok:true});
      } else if(req.url==='/finished') {
        if(activeLease?.id!==data.lease)throw new Error('任务标识已失效');
        const view=views.get(activeLease.platform);
        view.webContents.session.flushStorageData();await view.webContents.session.cookies.flushStore();
        publicationPermit=null;activeLease=null;update();
        await Promise.all([...views.values()].map(syncPublicationMode));respond({ok:true});
      } else if(req.url==='/test-quit' && process.env.SAU_DESKTOP_TEST==='1') {
        respond({ok:true});setImmediate(()=>app.quit());
      } else return respond({error:'Not found'},404);
    } catch(e) {respond({error:e.message},400);}
  });
  const server=bridge;
  try {
    await withTimeout(()=>new Promise((resolve,reject)=>{
      server.once('error',reject);server.listen(0,'127.0.0.1',resolve);
    }),1500,'BRIDGE_START_TIMEOUT');
    await privateIO.descriptor({pid:process.pid,url:`http://127.0.0.1:${server.address().port}`,token:bridgeToken},
      ()=>bridge===server && server.listening && !quitting);
  } catch(error) {
    server.close();if(bridge===server)bridge=null;throw error;
  }
}
function connectWorkbench(forceReload=false) {
  if(connectionStatus==='ready' && !forceReload) return Promise.resolve();
  if(connectionPromise) return connectionPromise;
  if(activeLease || visibleTaskProgress()?.job?.active) {
    return Promise.reject(new Error('当前任务正在使用工作台，请等待任务结束后重新连接。'));
  }
  const refreshConnectedDashboard=forceReload && connectionStatus==='ready';
  connectionStatus='connecting';connectionErrorCode=null;notice='正在连接本机工作台…';update();
  connectionPromise=recoverConnection(async()=>{
    const assertIdle=()=>{
      if(activeLease || visibleTaskProgress()?.job?.active) throw new Error('当前任务正在使用工作台，请等待任务结束后重新连接。');
    };
    assertIdle();
    if(refreshConnectedDashboard && !dashboard.webContents.isDestroyed()) {
      // Save the current editor before replacing only the dashboard document.
      // Other creator WebContents and their unfinished forms stay attached.
      await withTimeout(()=>dashboard.webContents.executeJavaScript('window.flushWorkbenchEditor?.()'),5000,'EDITOR_FLUSH_TIMEOUT');
      assertIdle();
    }
    await startBackend();
    assertIdle();
    await startBridge();
    assertIdle();
    if(quitting) throw Object.assign(new Error('工作台已关闭。'),{code:'CONNECTION_CLOSED'});
    try {
      await withTimeout(()=>dashboard.webContents.loadURL(`${backendURL}/?embedded=1`),15000,'WORKBENCH_LOAD_TIMEOUT');
    } catch(error) {
      if(error.code==='WORKBENCH_LOAD_TIMEOUT' && !dashboard.webContents.isDestroyed()) dashboard.webContents.stop();
      throw error;
    }
    if(quitting) throw Object.assign(new Error('工作台已关闭。'),{code:'CONNECTION_CLOSED'});
    connectionStatus='ready';connectionErrorCode=null;notice='';showView(selected);
    startSessionMonitor();startProgressMonitor();
  },{shouldContinue:()=>!quitting}).catch(error=>{
    if(!quitting){connectionStatus='disconnected';connectionErrorCode=error.code || 'CONNECTION_FAILED';notice=error.message;update();}
    throw error;
  }).finally(()=>{connectionPromise=null;});
  return connectionPromise;
}
function trusted(event) {
  return event.senderFrame===event.sender.mainFrame && (event.sender===win.webContents || event.sender===dashboard.webContents);
}
function ipc(name,handler) {ipcMain.handle(name,async(event,...args)=>{if(!trusted(event))throw new Error('无效的页面来源');return handler(...args);});}
async function performPageOperation(kind) {
  if(selected==='workbench')throw new Error('请先打开一个平台页面。');
  if(kind==='reload' && (navigationLocked() || visibleTaskProgress()?.job?.active))throw new Error('视频正在处理，请先在素材工作台停止任务再重新加载。');
  const view=currentView(),revision=selectionRevision;
  const isCurrent=()=>!quitting && revision===selectionRevision && currentView()===view;
  pageOperationController?.abort();
  const controller=new AbortController();pageOperationController=controller;
  notice='';
  try {
    const receipt=await runPageOperation({kind,view,isCurrent,
      onState:state=>{pageOperation=state;update();},
      repair:(target,options)=>nativeViewPainter.repair(target,options),signal:controller.signal});
    if(receipt.status==='failed' && isCurrent())throw new Error(receipt.detail);
    return shellState();
  } finally {if(pageOperationController===controller)pageOperationController=null;}
}
function configureIPC() {
  ipc('browser-state',()=>shellState());
  ipc('github-open',()=>shell.openExternal(REPOSITORY_URL));
  ipc('update-check',()=>githubUpdater.check());
  ipc('update-install',installSoftwareUpdate);
  ipc('cover-copy',path=>copyCover(path));
  ipc('publication-mode',setPublicationMode);
  ipc('browser-reconnect',async()=>{await connectWorkbench(true);return shellState();});
  ipc('browser-select',async id=>{await selectPlatform(id);return shellState();});
  ipc('browser-fit',()=>setPageZoom());
  ipc('browser-zoom',factor=>setPageZoom(factor));
  ipc('browser-repair-display',()=>performPageOperation('repair'));
  ipc('browser-reload',()=>performPageOperation('reload'));
  ipc('browser-back',()=>{if(navigationLocked())throw new Error('请先停止任务再返回。');const wc=(selectedPopup()?.view || views.get(selected))?.webContents;if(wc?.navigationHistory.canGoBack())wc.navigationHistory.goBack();});
  ipc('browser-close-popup',async()=>{const child=selectedPopup();if(child){popupViews.splice(popupViews.indexOf(child),1);setViewVisibility(child.view,false);child.view.webContents.close();showView(selected,true);await settleSelectedFocus(currentView(),selectionRevision);}});
  ipc('browser-stop-login',async()=>{
    if(navigationLocked())throw new Error('当前正在处理上传任务，请在素材工作台停止任务。');
    const response=await fetch(`${backendURL}/api/state`,{signal:AbortSignal.timeout(5000)});
    if(!response.ok)throw new Error('未能读取登录等待状态。');
    const state=await response.json();
    if(!state.job?.active)return;
    if(state.job.action!=='login')throw new Error('当前正在处理上传任务，请在素材工作台停止任务。');
    const cancelled=await fetch(`${backendURL}/api/cancel`,{method:'POST',
      headers:{'Content-Type':'application/json','X-Workbench-Token':state.token},
      body:JSON.stringify({expected_job_id:state.job.id,expected_action:'login'}),signal:AbortSignal.timeout(5000)});
    const result=await cancelled.json();if(!cancelled.ok)throw new Error(result.error || '登录等待未能结束。');
  });
  ipc('native-picker',async kind=>{
    if(!['folder','video','cover','text'].includes(kind))throw new Error('选择类型无效');
    const options=kind==='folder'?{properties:['openDirectory'],title:'选择素材文件夹'}:
      {properties:['openFile'],title:kind==='video'?'选择视频':kind==='text'?'选择标题或文案文件':'选择封面',filters:[{name:kind==='video'?'视频':kind==='text'?'文字':'封面',extensions:kind==='video'?['mp4','mov','m4v','webm','avi','mkv']:kind==='text'?['txt','md','text']:['jpg','jpeg','png','webp']}]};
    const result=await dialog.showOpenDialog(win,options);return result.canceled?null:result.filePaths[0];
  });
}
async function assertUpdateIdle() {
  if (activeLease || visibleTaskProgress()?.job?.active || pageOperation?.status==='running') throw new Error('请等待当前任务完成，再安装更新。');
  const response=await fetch(`${backendURL}/api/state`,{signal:AbortSignal.timeout(5000)});
  if(!response.ok)throw new Error('未能确认后台空闲，请重新连接工作台后再安装更新。');
  const state=await response.json();
  if(state.app!=='sau-draft-workbench' || state.job?.active)throw new Error('后台仍在执行任务，暂不能安装更新。');
}
async function ownedUpdateBackend() {
  const owner=JSON.parse(await fs.promises.readFile(path.join(privateDir,'backend-owner.json'),'utf8'));
  if(!Number.isSafeInteger(owner.pid) || owner.pid<=0 || path.resolve(owner.executable || '')!==path.resolve(runtime.command[0]))throw new Error('后台进程身份未确认，已停止安装。');
  const execute=require('node:util').promisify(require('node:child_process').execFile);
  if(process.platform==='darwin') {
    const result=await execute('/bin/ps',['-ww','-p',String(owner.pid),'-o','command=']);
    if(result.stdout.trim()!==owner.executable+' server')throw new Error('后台进程已变化，已停止安装。');
  } else {
    const result=await execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',`(Get-CimInstance Win32_Process -Filter "ProcessId = ${owner.pid}").ExecutablePath`]);
    if(result.stdout.trim().toLowerCase()!==owner.executable.toLowerCase())throw new Error('后台进程已变化，已停止安装。');
  }
  return owner;
}
async function installSoftwareUpdate() {
  if(updateInstallBusy)throw new Error('正在准备安装更新。');
  if(!app.isPackaged || process.env.SAU_DESKTOP_TEST==='1')throw new Error('安装更新仅在正式安装的应用中启用。');
  updateInstallBusy=true;update();
  let prepared;
  try {
    await assertUpdateIdle();
    const {asset,file}=await githubUpdater.downloaded();
    const answer=await dialog.showMessageBox(win,{type:'info',title:'安装发布助手更新',
      message:`v${asset.version} 已下载并通过 SHA-256 校验`,
      detail:process.platform==='darwin'?'安装将退出并重新打开发布助手。素材、任务记录和登录资料会保留，旧版应用会保留为回滚副本。':'安装将退出发布助手并打开新版安装向导。请选择现有安装位置；素材、任务记录和登录资料会保留。',
      buttons:['安装更新','稍后'],defaultId:1,cancelId:1});
    if(answer.response!==0)return;
    await assertUpdateIdle();
    const backend=await ownedUpdateBackend();
    prepared=await prepareInstallation({file,version:asset.version,executable:app.getPath('exe'),cacheDir:path.dirname(file),backend});
    await assertUpdateIdle();
    await prepared.launch();
    quitting=true;app.quit();
  } catch(error) {
    if(prepared)await prepared.cancel();
    throw error;
  } finally {updateInstallBusy=false;if(!quitting)update();}
}
async function boot() {
  // Render the local shell before touching the protected legacy data folder.
  win=new BrowserWindow({width:1320,height:900,minWidth:1000,minHeight:640,title:PRODUCT_NAME,
    titleBarStyle:process.platform==='darwin'?'hiddenInset':'default',trafficLightPosition:{x:28,y:28},
    backgroundColor:'#b3c6e4',webPreferences:{preload:path.join(__dirname,'preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  dashboard=new WebContentsView({webPreferences:{show:true,preload:path.join(__dirname,'preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,focusOnNavigation:false}});
  dashboard.webContents.on('will-navigate',(event,url)=>{if(new URL(url).origin!==backendURL)event.preventDefault();});
  dashboard.webContents.on('did-fail-load',(_event,code,_description,_url,isMainFrame)=>{
    if(!isMainFrame || code===-3 || quitting || connectionPromise) return;
    connectionStatus='disconnected';connectionErrorCode='WORKBENCH_LOAD_FAILED';
    notice='工作台页面连接中断，可重新连接工作台。';update();
  });
  dashboard.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  win.webContents.on('will-navigate',event=>event.preventDefault());
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  win.contentView.addChildView(dashboard);layout();dashboard.setVisible(false);
  githubUpdater=createGitHubUpdater({version:app.getVersion(),cacheDir:path.join(app.getPath('userData'),'software-updates'),
    fetchImpl:(...args)=>net.fetch(...args),enabled:app.isPackaged && process.env.SAU_DESKTOP_TEST!=='1',onState:()=>update()});
  configureIPC();
  Menu.setApplicationMenu(Menu.buildFromTemplate([{label:PRODUCT_NAME,submenu:[{role:'about',label:'关于'+PRODUCT_NAME},
    {label:'检查软件更新',click:()=>{void githubUpdater.check();}},
    {label:'在 GitHub 上支持发布助手',click:()=>{void shell.openExternal(REPOSITORY_URL);}},
    {label:'重新连接工作台',click:()=>{void connectWorkbench(true).catch(error=>{if(!quitting){notice=error.message;update();}});}},
    {type:'separator'},{role:'hide',label:'隐藏'+PRODUCT_NAME},{role:'quit',label:'退出'+PRODUCT_NAME}]},
    {label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
    {label:'窗口',submenu:[{role:'minimize'},{role:'zoom'},{role:'front'}]}]));
  for(const event of ['resize','focus','blur','show','hide','minimize','restore'])win.on(event,update);
  for(const event of ['show','restore'])win.on(event,()=>nativeViewPainter.queue(currentView()));
  win.on('close',event=>{
    if(quitting)return;
    event.preventDefault();
    fetch(`${backendURL}/api/state`,{signal:AbortSignal.timeout(1000)}).then(r=>r.json()).catch(()=>({})).then(state=>{
      if(activeLease || state.job?.active){win.hide();}
      else{quitting=true;app.quit();}
    });
  });
  await win.loadFile(path.join(__dirname,'shell.html'));
  githubUpdater.start();
  await connectWorkbench();
}
if(!app.requestSingleInstanceLock()){app.quit();}else{
  app.on('second-instance',()=>{win?.show();win?.focus();});
  app.on('activate',()=>{win?.show();win?.focus();});
  app.whenReady().then(boot).catch(error=>{if(!quitting){notice=error.message;update();dialog.showErrorBox('工作台未能启动',error.message);}});
}
app.on('before-quit',event=>{quitting=true;
  githubUpdater?.stop();
  privateIO.stop();
  nativeViewPainter.stop();
  clearInterval(sessionMonitorTimer);
  clearInterval(progressMonitorTimer);
  if(!storageFlushed){
    event.preventDefault();
    if(flushing)return;
    flushing=true;
    const sessions=new Set([...views.values(),...popupViews.map(p=>p.view)].map(v=>v.webContents.session));
    const pending=[...sessions].map(async ses=>{ses.flushStorageData();await ses.cookies.flushStore();});
    if(dashboard && !dashboard.webContents.isDestroyed())pending.push(dashboard.webContents.executeJavaScript('window.flushWorkbenchEditor?.()'));
    pending.push(privateIO.removeDescriptor());
    Promise.allSettled(pending).then(()=>{
      storageFlushed=true;app.quit();
    });
    return;
  }
  if(bridge)bridge.close();
  for(const v of views.values()){v.webContents.session.flushStorageData();v.webContents.close();}
  for(const p of popupViews)p.view.webContents.close();dashboard?.webContents.close();
});
