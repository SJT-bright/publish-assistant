'use strict';
// No real app, backend, account, media, or task is opened by these checks.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const shellSource=fs.readFileSync(path.join(root,'desktop/shell.js'),'utf8');
const html=fs.readFileSync(path.join(root,'desktop/shell.html'),'utf8');
const mainSource=fs.readFileSync(path.join(root,'desktop/main.cjs'),'utf8');
const stageIDs=['session','video','upload','content','cover','save','verify'];
const platforms=[{id:'douyin',name:'抖音'},{id:'xiaohongshu',name:'小红书'},{id:'tencent',name:'视频号'},{id:'bilibili',name:'B站'},{id:'kuaishou',name:'快手'}];
const plain=value=>JSON.parse(JSON.stringify(value));

class Element {
  constructor(id=''){this.style={setProperty:(name,value)=>{this.attributes[name]=value;}};this.id=id;this.children=[];this.dataset={};this.attributes={};this.hidden=false;this.textContent='';this.classes=new Set();this.classList={
    toggle:(name,force)=>{if(force)this.classes.add(name);else this.classes.delete(name);},contains:name=>this.classes.has(name)};}
  set className(value){this.classes=new Set(value.split(/\s+/));}
  get className(){return [...this.classes].join(' ');}
  append(...nodes){this.children.push(...nodes);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  removeAttribute(name){delete this.attributes[name];}
}
function shellHarness(initialNow=100){
  let now=initialNow,callback,tick;const calls=[];
  const elements=Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element(match[1])]));
  for(const match of html.matchAll(/data-stage="([^"]+)"/g)){const node=new Element();node.dataset.stage=match[1];elements.progressSteps.append(node);}
  elements.workbench.className='nav';
  const document={body:new Element('body'),getElementById:id=>elements[id],createElement:()=>new Element(),
    createTextNode:text=>({textContent:text}),querySelectorAll:()=>[elements.workbench,...elements.platforms.children]};
  const empty={selected:'workbench',platforms,active:false,navigationLocked:false};
  const api={onState:fn=>{callback=fn;},state:()=>({then:fn=>fn(empty)}),select:async()=>{},reload:async()=>{},reconnect:async()=>{calls.push(['reconnect']);},publicationMode:async mode=>{calls.push(['publicationMode',mode]);},back:async()=>{},closePopup:async()=>{},stopLogin:async()=>{},fit:async()=>{calls.push(['fit']);},zoom:async factor=>{calls.push(['zoom',factor]);}};
  const sandbox={document,window:{DraftProgress:require('../ui-assets/progress-model.js'),draftDesktop:api,DraftVisual:{icon:()=>'<svg></svg>'}},
    Date:{now:()=>now*1000},Set,setInterval:fn=>{tick=fn;return 1;}};
  vm.createContext(sandbox);vm.runInContext(shellSource,sandbox,{filename:'desktop/shell.js'});
  return {elements,document,calls,state:overrides=>callback({...empty,...overrides}),render:(taskProgress,selected='workbench')=>callback({...empty,selected,taskProgress}),
    advance:seconds=>{now+=seconds;tick();},node:stage=>elements.progressSteps.children.find(node=>node.dataset.stage===stage)};
}
function snapshot(overrides={},progressOverrides={}){
  return {available:true,connected:true,observed_at:100,job:{id:'job-1',action:'draft',active:true,platform:'tencent',platform_name:'视频号',platform_index:2,platform_count:4,status:'running',message:'',
    progress:{stage:'video',state:'running',started_at:100,updated_at:100,completed:['session'],skipped:[],...progressOverrides},...overrides}};
}

test('connection failure offers recovery and a healthy connection removes the loader',async()=>{
  const h=shellHarness();
  h.state({connection:{status:'disconnected'}});
  assert.equal(h.elements.connectionPanel.hidden,false);
  assert.equal(h.elements.reconnect.hidden,false);
  await h.elements.reconnect.onclick();
  assert.deepEqual(h.calls,[['reconnect']]);
  h.state({connection:{status:'connecting'}});
  assert.equal(h.elements.reconnect.hidden,true);
  h.state({connection:{status:'ready'}});
  assert.equal(h.elements.connectionPanel.hidden,true);
});

test('web publication is explicit, disabled during jobs, and switches back to draft',async()=>{
  const h=shellHarness();
  assert.equal(h.elements.publicationLabel.textContent,'仅保存草稿');
  await h.elements.publicationMode.onclick();
  assert.deepEqual(h.calls,[['publicationMode','manual_publish']]);
  h.state({publicationMode:'manual_publish',manualPublishAllowed:true});
  assert.equal(h.elements.publicationLabel.textContent,'可在平台发表');
  await h.elements.publicationMode.onclick();
  assert.deepEqual(h.calls.at(-1),['publicationMode','draft']);
  h.state({publicationMode:'manual_publish',manualPublishAllowed:false,taskProgress:snapshot()});
  assert.equal(h.elements.publicationMode.disabled,true);
});

test('connection recovery cannot invite an interruption of an active upload',()=>{
  const h=shellHarness();
  h.state({connection:{status:'disconnected'},taskProgress:snapshot()});
  assert.equal(h.elements.reconnect.disabled,true);
});

test('progress is hidden and original layout class is restored when no task exists',()=>{
  const h=shellHarness();
  assert.equal(h.elements.taskProgress.hidden,true);
  h.render(snapshot());assert.equal(h.elements.taskProgress.hidden,false);
  h.render({available:true,connected:true,job:null});
  assert.equal(h.elements.taskProgress.hidden,true);
  assert.equal(h.document.body.classList.contains('has-task-progress'),false);
});
test('active task platform owns the strip even while the user looks at another platform or workbench',()=>{
  const h=shellHarness();h.render(snapshot(),'xiaohongshu');
  assert.equal(h.elements.heading.textContent,'小红书');
  assert.equal(h.elements.progressPlatform.textContent,'平台 2/4 · 视频号');
  h.render(snapshot(),'workbench');assert.equal(h.elements.progressPlatform.textContent,'平台 2/4 · 视频号');
});
test('finished upload from another platform is hidden on the current native page',()=>{
  const h=shellHarness(),record=snapshot({active:false,status:'result_unknown'},{stage:'upload',state:'failed',completed:['session','video','content','cover']});
  h.render(record,'kuaishou');
  assert.equal(h.elements.taskProgress.hidden,true);
  assert.equal(h.document.body.classList.contains('has-task-progress'),false);
  h.render(record,'tencent');assert.equal(h.elements.taskProgress.hidden,false);
  h.render(record,'workbench');assert.equal(h.elements.taskProgress.hidden,false);
});
test('new platform and new task reset completed segments and the step wait',()=>{
  const h=shellHarness();h.render(snapshot({}, {stage:'save',completed:['session','video','upload','content','cover']}));
  h.advance(20);assert.equal(h.elements.progressWait.textContent,'已等待 00:20');
  h.render(snapshot({id:'job-1',platform:'bilibili',platform_name:'B站',platform_index:3},{stage:'session',started_at:120,updated_at:120,completed:[]}));
  assert.equal(h.elements.progressPlatform.textContent,'平台 3/4 · B站');
  assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),'0');
  assert.equal(h.node('save').dataset.state,'pending');assert.equal(h.elements.progressWait.textContent,'已等待 00:00');
  h.render(snapshot({id:'job-2'},{started_at:120,updated_at:120,completed:[]}));
  assert.equal(h.elements.taskProgress.dataset.job,'job-2');assert.equal(h.node('session').dataset.state,'pending');
});
test('elapsed time does not invent progress and the long wait wording does not claim a deadlock',()=>{
  const h=shellHarness();h.render(snapshot());h.advance(44);
  assert.equal(h.elements.progressWait.textContent,'已等待 00:44');
  assert.doesNotMatch(h.elements.progressDetail.textContent,/仍在等待/);
  h.advance(1);assert.match(h.elements.progressDetail.textContent,/此步骤仍在等待平台响应/);
  assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),'1');
  assert.equal(h.node('video').dataset.state,'running');assert.equal(h.node('upload').dataset.state,'pending');
  assert.doesNotMatch(h.elements.progressDetail.textContent,/卡死|自动重试|%/);
});
test('fine operation events update percentages immediately and waiting never advances them',()=>{
  const h=shellHarness();
  const row={plan:['transfer','processing'],completed:[],skipped:[],current:'transfer',state:'running',percent:48};
  const data=snapshot({}, {stage:'upload',completed:['session','video'],operations:{upload:row}});
  h.render(data);
  assert.equal(h.elements.progressPercent.textContent,'流程 32%');
  assert.equal(h.elements.progressFill.style.width,'32%');
  assert.equal(h.elements.progressMeter.getAttribute('aria-valuenow'),'32');
  assert.match(h.node('upload').textContent,/24%/);
  assert.match(h.elements.progressOperations.children[0].textContent,/48%/);
  h.advance(90);assert.equal(h.elements.progressPercent.textContent,'流程 32%');
  data.job.active=false;data.job.status='result_unknown';data.job.progress.state='unknown';
  h.render(data);assert.equal(h.elements.progressPercent.textContent,'流程 32%');
  h.render(snapshot({platform:'bilibili',platform_index:3},{stage:'session',completed:[]}));
  assert.equal(h.elements.progressPercent.textContent,'流程 0%');
});
test('completed and skipped stages stop their wait while the job awaits its next real event',()=>{
  const h=shellHarness();
  h.render(snapshot({}, {stage:'upload',state:'completed',started_at:50,updated_at:100,completed:['session','video','upload']}));
  assert.equal(h.elements.progressScope.textContent,'执行中');
  assert.match(h.elements.progressStage.textContent,/本步骤已完成.*等待下一步骤/);
  assert.equal(h.elements.progressCount.textContent,'已完成 3/7 步');
  assert.equal(h.elements.progressWait.textContent,'停留 00:50');
  h.advance(90);
  assert.equal(h.elements.progressWait.textContent,'停留 00:50');
  assert.doesNotMatch(h.elements.progressDetail.textContent,/仍在等待平台响应/);
  assert.equal(h.node('upload').dataset.state,'completed');
  h.render(snapshot({}, {stage:'cover',state:'skipped',started_at:175,updated_at:190,completed:['session','video','upload','content'],skipped:['cover']}));
  assert.match(h.elements.progressStage.textContent,/本步骤已跳过.*等待下一步骤/);
  assert.equal(h.elements.progressCount.textContent,'已完成 4/7 步 · 跳过 1 步');
  h.advance(60);assert.equal(h.elements.progressWait.textContent,'停留 00:15');
  assert.doesNotMatch(h.elements.progressDetail.textContent,/仍在等待平台响应/);
  assert.equal(h.node('cover').dataset.state,'skipped');
  h.render(snapshot({}, {stage:'save',state:'waiting',started_at:250,updated_at:250,completed:['session','video','upload','content'],skipped:['cover']}));
  h.advance(45);assert.equal(h.elements.progressWait.textContent,'已等待 00:45');
  assert.match(h.elements.progressDetail.textContent,/此步骤仍在等待平台响应/);
});
test('terminal unknown results keep the stopping stage and cannot display seven completed steps',()=>{
  const h=shellHarness();h.render(snapshot({active:false,status:'saved_unverified'}, {stage:'verify',state:'unknown',updated_at:131,completed:stageIDs}));
  assert.match(h.elements.progressStage.textContent,/保存尚未核验.*重开核验/);
  assert.equal(h.elements.taskProgress.dataset.tone,'attention');
  assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),'6');
  assert.equal(h.node('verify').dataset.state,'unconfirmed');
  h.advance(100);assert.equal(h.elements.progressWait.textContent,'停留 00:31');
  h.render(snapshot({active:false,status:'result_unknown'},{stage:'cover',state:'unknown'}));
  assert.match(h.elements.progressStage.textContent,/结果待确认.*设置封面/);
});
test('all three verified draft statuses including snapshot can complete the verification segment',()=>{
  for(const status of ['local_draft_verified','platform_draft_verified','platform_snapshot_verified']){
    const h=shellHarness();h.render(snapshot({active:false,status},{stage:'verify',state:'completed',completed:stageIDs}));
    assert.equal(h.elements.taskProgress.dataset.tone,'verified');
    assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),'7');
    assert.equal(h.node('verify').dataset.state,'completed');
    assert.equal(h.elements.progressStage.textContent,'草稿已重开核验');
  }
});
test('a reported failed step and a cancelled task retain the failure or stopping point',()=>{
  const h=shellHarness();h.render(snapshot({}, {stage:'content',state:'failed',updated_at:112}));
  assert.match(h.elements.progressStage.textContent,/此步骤失败.*填写标题、文案和话题/);
  assert.equal(h.node('content').dataset.state,'failed');
  h.render(snapshot({active:false,status:'cancelled'},{stage:'upload',state:'cancelled',updated_at:115}));
  assert.match(h.elements.progressStage.textContent,/已停止.*等待上传完成/);
  assert.equal(h.node('upload').dataset.state,'cancelled');
});
test('connection failure freezes the last observation and shows it as stale',()=>{
  const h=shellHarness();const data=snapshot();data.connected=false;data.observed_at=121;
  h.render(data);assert.match(h.elements.progressStage.textContent,/进度连接中断.*导入视频/);
  assert.equal(h.node('video').dataset.state,'offline');
  assert.equal(h.elements.progressWait.textContent,'停留 00:21');
  h.advance(90);assert.equal(h.elements.progressWait.textContent,'停留 00:21');
  assert.match(h.elements.progressDetail.textContent,/最后记录/);
  h.render(snapshot());assert.equal(h.node('video').dataset.state,'running');
});
test('old task without stage reports is indeterminate and never guessed from elapsed time',()=>{
  const h=shellHarness();h.render(snapshot({progress:null}));h.advance(60);
  assert.equal(h.elements.progressStage.textContent,'当前任务尚未上报具体步骤');
  assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),null);
  assert.equal(h.elements.progressWait.textContent,'');
  assert.equal(h.elements.progressSteps.getAttribute('aria-valuetext'),'执行中；平台 2/4 · 视频号；当前任务尚未上报具体步骤；无步骤记录');
  h.render(snapshot({active:false,status:'result_unknown',progress:null}));
  assert.match(h.elements.progressStage.textContent,/任务|结果待确认/);
  assert.doesNotMatch(h.elements.progressStage.textContent,/正在/);
});
test('login uses a simple authentication status and hides the seven draft steps',()=>{
  const h=shellHarness();h.render(snapshot({action:'login',progress:null}));
  assert.equal(h.elements.progressSteps.hidden,true);
  assert.equal(h.elements.progressStage.textContent,'等待扫码或完成登录验证');
  h.render(snapshot({action:'login',active:false,status:'login_verified',progress:null}));
  assert.equal(h.elements.taskProgress.dataset.tone,'verified');assert.equal(h.elements.progressStage.textContent,'登录已验证');
});
test('step count is explicit, skipped stages are separate, and all remote text is assigned as text',()=>{
  const h=shellHarness();h.render(snapshot({message:'<img src=x onerror=alert(1)>'},{stage:'save',completed:['session','video','upload','content'],skipped:['cover']}));
  assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),'4');
  assert.match(h.elements.progressSteps.getAttribute('aria-valuetext'),/已完成 4 \/ 7 步，跳过 1 步/);
  assert.equal(h.node('cover').dataset.state,'skipped');assert.equal(h.node('save').getAttribute('aria-current'),'step');
  assert.match(h.elements.progressDetail.textContent,/<img/);assert.equal(h.elements.progressDetail.innerHTML,undefined);
  assert.match(html,/role="progressbar"[^>]*aria-valuemin="0"[^>]*aria-valuemax="7"/);
  assert.match(html,/id="progressStage"[^>]*aria-live="polite"/);
});
test('platform sequence and completed step count have distinct labels and honest numbers',()=>{
  const h=shellHarness();
  h.render(snapshot({platform:'xiaohongshu',platform_name:'小红书',platform_index:1,platform_count:3}), 'xiaohongshu');
  assert.equal(h.elements.progressScope.textContent,'执行中');
  assert.equal(h.elements.progressPlatform.textContent,'平台 1/3 · 小红书');
  assert.equal(h.elements.progressCount.textContent,'已完成 1/7 步');
  assert.equal(h.elements.progressCount.getAttribute('aria-label'),'已完成 1 / 7 步');
  h.render(snapshot({}, {stage:'save',completed:['session','video','upload','content'],skipped:['cover']}));
  assert.equal(h.elements.progressCount.textContent,'已完成 4/7 步 · 跳过 1 步');
  assert.match(h.elements.progressDetail.textContent,/已完成 4 步 · 跳过 1 步/);
});
test('recent task on a different browsed page is hidden instead of implying that page is executing',()=>{
  const h=shellHarness();h.render(snapshot({active:false,status:'result_unknown',platform:'xiaohongshu',platform_name:'小红书',platform_index:1,platform_count:3}), 'bilibili');
  assert.equal(h.elements.heading.textContent,'B站');
  assert.equal(h.elements.taskProgress.hidden,true);
  h.render(snapshot({active:false,status:'result_unknown'}), 'tencent');
  assert.equal(h.elements.progressScope.textContent,'最近任务');
  assert.doesNotMatch(h.elements.progressDetail.textContent,/当前浏览/);
});
test('legacy history explicitly says no step records and cannot infer zero or full completion even from a verified result',()=>{
  const h=shellHarness();
  for(const status of ['result_unknown','platform_draft_verified']){
    h.render(snapshot({active:false,status,progress:null}), 'tencent');
    assert.equal(h.elements.progressScope.textContent,'最近任务');
    assert.equal(h.elements.progressCount.textContent,'历史任务 · 无步骤记录');
    assert.equal(h.elements.progressSteps.getAttribute('aria-valuenow'),null);
    assert.ok(h.elements.progressSteps.children.every(node=>node.dataset.state==='unrecorded'));
    assert.doesNotMatch(h.elements.progressCount.textContent,/0\/7|7\/7|100%/);
    assert.doesNotMatch(h.elements.progressDetail.textContent,/当前浏览/);
  }
});
test('a live legacy task and a disconnected last observation keep their distinct context labels',()=>{
  const h=shellHarness();h.render(snapshot({progress:null}), 'workbench');
  assert.equal(h.elements.progressScope.textContent,'执行中');
  assert.equal(h.elements.progressCount.textContent,'无步骤记录');
  assert.match(h.elements.progressDetail.textContent,/正在处理 视频号；当前浏览 素材工作台/);
  const offline=snapshot();offline.connected=false;h.render(offline,'xiaohongshu');
  assert.equal(h.elements.progressScope.textContent,'最后记录');
  assert.match(h.elements.progressDetail.textContent,/当前浏览 小红书；以上是最后的视频号任务记录/);
});
test('unverified terminal result never receives a full-completion step badge',()=>{
  const h=shellHarness();h.render(snapshot({active:false,status:'saved_unverified'}, {stage:'verify',state:'unknown',completed:stageIDs}), 'tencent');
  assert.equal(h.elements.progressCount.textContent,'已完成 6/7 步');
  assert.equal(h.elements.progressScope.textContent,'最近任务');
  assert.match(h.elements.progressStage.textContent,/保存尚未核验/);
});

function mainHarness(fetchImpl=async()=>({ok:true,status:200,json:async()=>({available:true,job:null})})){
  // app's ready path never runs; filesystem writes are disabled by the stub.
  const handlers=new Map(),writes=[];
  const app={isPackaged:false,getVersion:()=> 'isolated-test',getPath:()=>'/isolated-test-profile',setPath:()=>{},setName:()=>{},setAboutPanelOptions:()=>{},
    commandLine:{appendSwitch:()=>{}},requestSingleInstanceLock:()=>false,quit:()=>{},on:()=>{}};
  const fakeFs={...fs,mkdirSync:()=>{},promises:{mkdir:async()=>{},writeFile:async(...args)=>{writes.push(args);},chmod:async()=>{},rename:async()=>{},unlink:async()=>{}},writeFileSync:(...args)=>{writes.push(args);}};
  const sandbox={require:name=>name==='electron'?{app,ipcMain:{handle:(name,handler)=>handlers.set(name,handler)}}:name==='node:fs'?fakeFs:require(name),__dirname:path.join(root,'desktop'),
    process:{env:{},pid:123,exit:()=>{throw new Error('Unexpected exit');}},console,fetch:fetchImpl,AbortSignal,
    setTimeout,clearTimeout,setInterval:()=>({unref:()=>{}}),clearInterval};
  vm.createContext(sandbox);
  vm.runInContext(mainSource+`\nglobalThis.api={publicTaskProgress,monitorProgress,visibleTaskProgress,layout,showView,selectPlatform,settleSelectedFocus,shellState,platformZoomFactor,pageZoomState,setPageZoom,configureIPC,setPublicationMode,syncPublicationMode,
    get:()=>taskProgress,set:value=>{taskProgress=value;},lease:value=>{activeLease=value;},
    selected:id=>{selected=id;},platform:(id,view)=>{views.set(id,view);},
    layoutMocks:(window,view,platformView,popup)=>{win=window;dashboard=view;views.set('tencent',platformView);popupViews.push({view:popup,platform:'tencent'});}};`,sandbox,{filename:'desktop/main.cjs'});
  return Object.assign(sandbox.api,{handlers,writes});
}
test('main only forwards whitelisted fields and sanitizes paths, identifiers, and secret-bearing messages',()=>{
  const api=mainHarness();const data=snapshot();
  Object.assign(data,{token:'topsecret'});Object.assign(data.job,{token:'jobsecret',expected:{video:'/Users/real/private.mp4'},uid:'secretuid',platform_name:'attacker supplied'});
  data.job.progress.label='<img>';data.job.progress.completed.push('nonsense');
  data.job.message='封面未匹配 /Users/real/private.jpg https://example.com/user 0123456789abcdef0123456789abcdef';
  const result=plain(api.publicTaskProgress(data));
  assert.equal(result.job.platform_name,'视频号');assert.equal(result.job.progress.label,undefined);
  assert.equal(result.job.expected,undefined);assert.equal(result.token,undefined);assert.equal(result.job.uid,undefined);
  assert.doesNotMatch(result.job.message,/\/Users|https?:|0123456789abcdef/);
  assert.deepEqual(result.job.progress.completed,['session']);
  data.job.message='uid secret';assert.equal(api.publicTaskProgress(data).job.message,'');
  data.job.message='封面文件 /Applications/封面.jpg /private/素材.mp4 C:\\Assets\\封面.jpg';
  assert.doesNotMatch(api.publicTaskProgress(data).job.message,/\/Applications|\/private|C:\\/);
  data.job.platform='evil';assert.throws(()=>api.publicTaskProgress(data),/Invalid progress/);
});
test('main caps poll concurrency, marks network failure without refreshing old observations, and hides on no task',async()=>{
  let resolve,calls=0;
  const api=mainHarness(async()=>{calls++;return new Promise(done=>{resolve=done;});});
  const first=api.monitorProgress();await api.monitorProgress();assert.equal(calls,1);
  resolve({status:200,ok:true,json:async()=>snapshot()});await first;
  const observed=api.get().observed_at;
  const failed=api.monitorProgress();resolve({status:500,ok:false});await failed;
  assert.equal(api.get().connected,false);assert.equal(api.get().observed_at,observed);assert.equal(api.get().job.id,'job-1');
  const idle=api.monitorProgress();resolve({status:200,ok:true,json:async()=>({available:true,job:null})});await idle;
  assert.equal(api.visibleTaskProgress().job,null);
});
test('404 legacy backend displays only the currently leased platform with no invented stage',async()=>{
  const api=mainHarness(async()=>({status:404,ok:false}));
  api.lease({id:'lease-id',platform:'kuaishou',action:'draft'});await api.monitorProgress();
  const visible=plain(api.visibleTaskProgress());
  assert.equal(visible.available,false);assert.equal(visible.job.platform,'kuaishou');assert.equal(visible.job.progress,null);
  api.lease(null);assert.equal(api.visibleTaskProgress().job,null);
});
test('workbench, platform, and login popup bounds all reserve the same dynamic strip height',()=>{
  const api=mainHarness();const records=[];
  const makeView=kind=>({setBounds:bounds=>records.push({kind,...plain(bounds)}),webContents:{isDestroyed:()=>false,getZoomFactor:()=>1,setZoomFactor:()=>{}}});
  api.layoutMocks({isDestroyed:()=>false,getContentSize:()=>[1100,700]},makeView('dashboard'),makeView('platform'),makeView('popup'));
  api.layout();assert.equal(records.length,3);assert.ok(records.every(entry=>entry.y===76 && entry.height===624));
  records.length=0;api.set(snapshot());api.layout();
  assert.equal(records.length,3);assert.ok(records.every(entry=>entry.x===224 && entry.y===216 && entry.width===876 && entry.height===484));
  api.set({available:true,connected:true,job:null});records.length=0;api.layout();assert.ok(records.every(entry=>entry.y===76));
  api.set(snapshot({active:false,status:'result_unknown'}));api.selected('bilibili');records.length=0;api.layout();
  assert.ok(records.every(entry=>entry.y===76 && entry.height===624));
  api.selected('tencent');records.length=0;api.layout();assert.ok(records.every(entry=>entry.y===216));
});

function zoomMocks(api,width=1320,height=868){
  const focused=[],zoomed=[],sent=[];let dimensions=[width,height],focusedKind=null;
  const view=kind=>{let factor=1,visible=false,bounds={};return {setBounds:value=>{bounds=value;},getBounds:()=>bounds,setVisible:value=>{visible=value;},getVisible:()=>visible,webContents:{
    isDestroyed:()=>false,send:(name,value)=>sent.push([kind,name,value]),mainFrame:{framesInSubtree:[]},getZoomFactor:()=>factor,setZoomFactor:value=>{factor=value;zoomed.push([kind,value]);},
    focus:()=>{focused.push(kind);focusedKind=kind;},isFocused:()=>focusedKind===kind,getBackgroundThrottling:()=>false,
    executeJavaScript:async source=>{assert.equal(source,'document.hasFocus()');return focusedKind===kind;},
    getURL:()=>kind==='dashboard'?'http://127.0.0.1/editor':'https://channels.weixin.qq.com/platform/post/create',isLoading:()=>false,
    navigationHistory:{canGoBack:()=>false},loadURL:async()=>{throw new Error('Unexpected navigation');}}};};
  const dashboard=view('dashboard'),platform=view('tencent'),popup=view('popup');
  const wc={mainFrame:{},send:(_name,state)=>sent.push(plain(state))};
  const children=[],mutations=[];
  const contentView={children,addChildView:view=>{assert.ok(!children.includes(view));children.push(view);mutations.push('attach');},
    removeChildView:view=>{assert.ok(children.includes(view));children.splice(children.indexOf(view),1);mutations.push('detach');}};
  const window={isDestroyed:()=>false,isVisible:()=>true,isFocused:()=>true,isMinimized:()=>false,getContentSize:()=>dimensions,webContents:wc,contentView};
  api.layoutMocks(window,dashboard,platform,popup);
  return {focused,zoomed,sent,dashboard,platform,popup,window,mutations,resize:(w,h)=>{dimensions=[w,h];api.layout();}};
}
test('manual publication re-enables after the finished job snapshot without refreshing the editor',async()=>{
  const api=mainHarness(),h=zoomMocks(api);
  await api.setPublicationMode('manual_publish');
  assert.ok(h.sent.some(row=>Array.isArray(row)&&row[1]==='publication-mode'&&row[2]===true));
  api.set(snapshot());await api.syncPublicationMode(h.platform);
  assert.deepEqual(h.sent.at(-1),['tencent','publication-mode',false]);
  api.set(snapshot({active:false}));await api.syncPublicationMode(h.platform);
  assert.deepEqual(h.sent.at(-1),['tencent','publication-mode',true]);
  const before=h.sent.length;await api.syncPublicationMode(h.platform);
  assert.equal(h.sent.length,before);
  await api.syncPublicationMode(h.platform,true);assert.equal(h.sent.length,before+1);
});
test('automatic fit preserves native bounds but gives TC 960px height and other platforms 1280px width',()=>{
  const api=mainHarness(),bounds={width:1096,height:706};
  assert.equal(api.platformZoomFactor('tencent',bounds),.735);
  assert.ok(bounds.height/api.platformZoomFactor('tencent',bounds)>=960);
  assert.equal(api.platformZoomFactor('bilibili',bounds),.83);
  assert.ok(bounds.width/api.platformZoomFactor('bilibili',bounds)>=1280);
  assert.ok(bounds.height/api.platformZoomFactor('bilibili',bounds)>=850);
  assert.equal(api.platformZoomFactor('tencent',{width:2400,height:1800}),1);
  assert.equal(api.platformZoomFactor('tencent',{width:500,height:300}),.65);
});
test('manual zoom survives resize per platform and fit restores automatic sizing without zooming the editor',()=>{
  const api=mainHarness(),h=zoomMocks(api);api.set(snapshot());api.selected('tencent');api.layout();
  assert.equal(api.pageZoomState().factor,.679);assert.equal(api.pageZoomState().fit,true);
  api.setPageZoom(.9);assert.equal(api.pageZoomState().fit,false);
  h.resize(1100,700);assert.equal(api.pageZoomState().factor,.9);
  assert.equal(h.platform.webContents.getZoomFactor(),.9);assert.equal(h.popup.webContents.getZoomFactor(),.9);
  assert.ok(h.zoomed.every(([kind])=>kind!=='dashboard'));
  api.setPageZoom();assert.equal(api.pageZoomState().factor,.65);assert.equal(api.pageZoomState().fit,true);
  api.platform('bilibili',h.platform);api.selected('bilibili');assert.equal(api.pageZoomState().fit,true);
  api.setPageZoom(.8);api.selected('tencent');assert.equal(api.pageZoomState().fit,true);
  api.selected('workbench');assert.equal(api.pageZoomState().available,false);
  assert.throws(()=>api.setPageZoom(.7),/先打开/);
});
test('zoom validation and trusted IPC retain draft lease and do not enable remote navigation',async()=>{
  const api=mainHarness(),h=zoomMocks(api);api.selected('tencent');const lease={id:'lease',platform:'tencent',action:'draft'};api.lease(lease);
  api.configureIPC();const event={sender:h.window.webContents,senderFrame:h.window.webContents.mainFrame};
  for(const value of ['.8',NaN,Infinity,.64,1.01]) assert.throws(()=>api.setPageZoom(value),/65%/);
  const state=await api.handlers.get('browser-zoom')(event,.8);
  assert.equal(state.zoom.factor,.8);assert.equal(state.navigationLocked,true);assert.equal(state.activePlatform,'tencent');
  assert.equal(h.focused.length,0);
  await assert.rejects(api.handlers.get('browser-zoom')({sender:{},senderFrame:{}},.75),/来源/);
  await assert.rejects(api.handlers.get('browser-reload')(event),/停止任务/);
  await api.handlers.get('browser-fit')(event);assert.equal(api.pageZoomState().fit,true);
});
test('explicit platform and editor switching focuses the visible view while resize and progress updates do not',async()=>{
  const api=mainHarness(),h=zoomMocks(api);api.set(snapshot());
  api.platform('xiaohongshu',h.platform);
  await api.selectPlatform('tencent');assert.deepEqual(h.focused,['popup','popup']);
  assert.deepEqual(h.window.contentView.children,[h.popup]);
  await api.selectPlatform('workbench');assert.deepEqual(h.focused,['popup','popup','dashboard','dashboard']);
  assert.deepEqual(h.window.contentView.children,[h.dashboard]);
  h.resize(1200,800);api.layout();api.showView('tencent');assert.deepEqual(h.focused,['popup','popup','dashboard','dashboard']);
  api.lease({id:'lease',platform:'xiaohongshu',action:'draft'});
  await api.selectPlatform('xiaohongshu',false,true);assert.deepEqual(h.focused,['popup','popup','dashboard','dashboard','tencent']);
  await assert.rejects(api.selectPlatform('tencent'),/正在处理草稿/);assert.equal(h.focused.length,5);
});
test('native mounting excludes hidden pages, repeated rendering does not remount, and a stale focus attempt cannot steal selection',async()=>{
  const api=mainHarness(),h=zoomMocks(api);
  await api.selectPlatform('tencent');
  assert.deepEqual(h.window.contentView.children,[h.popup]);
  assert.equal(h.platform.getVisible(),false);assert.equal(h.dashboard.getVisible(),false);
  const mutations=h.mutations.length,focused=h.focused.length;
  api.showView('tencent');api.layout();api.showView('tencent');
  assert.equal(h.mutations.length,mutations);assert.equal(h.focused.length,focused);
  await api.selectPlatform('workbench');
  const currentFocus=h.focused.length;
  assert.equal(await api.settleSelectedFocus(h.platform,0),false);
  assert.equal(h.focused.length,currentFocus);
  assert.deepEqual(h.window.contentView.children,[h.dashboard]);
  assert.equal(h.popup.getVisible(),false);assert.equal(h.platform.getVisible(),false);
  assert.equal(h.platform.webContents.isDestroyed(),false);
});
test('shell zoom controls expose fit and manual modes, retain accessibility, and stay enabled during draft navigation lock',async()=>{
  const h=shellHarness();assert.equal(h.elements.pageZoom.hidden,true);
  h.state({selected:'tencent',navigationLocked:true,active:true,activeAction:'draft',activePlatform:'tencent',zoom:{available:true,factor:.735,fit:true,min:.65,max:1}});
  assert.equal(h.elements.pageZoom.hidden,false);assert.equal(h.elements.zoomValue.textContent,'74%');
  assert.equal(h.elements.fitPage.getAttribute('aria-pressed'),'true');assert.equal(h.elements.zoomOut.disabled,false);assert.equal(h.elements.reload.disabled,true);
  await h.elements.zoomIn.onclick();await h.elements.zoomOut.onclick();await h.elements.fitPage.onclick();
  assert.deepEqual(h.calls,[['zoom',.785],['zoom',.685],['fit']]);
  h.state({selected:'bilibili',zoom:{available:true,factor:.65,fit:false,min:.65,max:1}});
  assert.equal(h.elements.zoomOut.disabled,true);assert.equal(h.elements.fitPage.getAttribute('aria-pressed'),'false');
  assert.match(h.elements.zoomValue.getAttribute('aria-label'),/手动设置/);
  h.state({selected:'bilibili',zoom:{available:true,factor:1,fit:false,min:.65,max:1}});assert.equal(h.elements.zoomIn.disabled,true);
  h.state({selected:'bilibili'});assert.equal(h.elements.fitPage.disabled,true);assert.equal(h.elements.zoomValue.textContent,'—');
  assert.match(html,/id="pageZoom"[^>]*role="group"[^>]*aria-label=/);
  assert.match(html,/id="zoomValue"[^>]*aria-live="polite"/);
});

test('overlapping upload and content remain visible with both operation rows',()=>{
  const h=shellHarness();
  h.render(snapshot({}, {stage:'content',active_stages:['upload','content'],operations:{
    upload:{plan:['transfer'],completed:[],skipped:[],current:'transfer',state:'running',percent:82},
    content:{plan:['title','description'],completed:['title'],skipped:[],current:'description',state:'running'}}}));
  assert.equal(h.node('upload').dataset.state,'running');assert.equal(h.node('content').dataset.state,'running');
  assert.match(h.elements.progressStage.textContent,/上传.* ＋ .*填写/);
  assert.ok(h.elements.progressOperations.children.some(node=>node.textContent.includes('82%')));
  assert.ok(h.elements.progressOperations.children.some(node=>node.textContent.includes('填写文案')));
});
test('publication submission shows review state without claiming public visibility',()=>{
  const h=shellHarness();h.render(snapshot({action:'publish',active:false,status:'published_submission_verified'},{stage:'verify',state:'completed'}));
  assert.match(h.elements.progressStage.textContent,/投稿已提交.*等待平台审核/);
  assert.match(h.node('save').textContent,/提交投稿/);assert.match(h.node('verify').textContent,/核验投稿回执/);
});
test('page-operation loading remains visible until a real terminal state',()=>{
  const h=shellHarness();h.state({selected:'tencent',zoom:{available:true},pageOperation:{kind:'reload',status:'running',startTime:100000,detail:'等待页面内容'}});
  assert.equal(h.elements.reload.disabled,true);assert.equal(h.elements.reload.getAttribute('aria-busy'),'true');
  h.advance(4);assert.match(h.elements.address.textContent,/00:04/);
  h.state({selected:'tencent',zoom:{available:true},pageOperation:{kind:'reload',status:'completed',startTime:100000,finishedTime:104000,detail:'页面加载完成'}});
  assert.equal(h.elements.reload.getAttribute('aria-busy'),'false');assert.match(h.elements.address.textContent,/页面加载完成/);
});
