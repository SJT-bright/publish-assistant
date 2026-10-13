const $=id=>document.getElementById(id);
const names={};
const platformIcon=id=>window.DraftVisual.icon(id);
const progressLabels={session:'验证登录',video:'导入视频',upload:'等待上传完成',content:'填写标题、文案和话题',cover:'设置封面',save:'保存草稿',verify:'重开核验'};
const verifiedStatuses=new Set(['local_draft_verified','platform_draft_verified','platform_snapshot_verified']);
const terminalLabels={cancelled:'已停止',failed:'执行失败',saved_unverified:'保存尚未核验',result_unknown:'结果待确认',
  risk_paused:'平台提示风险，已暂停',platform_restricted:'平台权限受限',adapter_mismatch:'页面控件待适配',
  needs_reauthentication:'需要重新登录',account_mismatch:'账号不一致，已停止'};
let currentState=null;
function formatWait(seconds){const total=Math.max(0,Math.floor(seconds));return String(Math.floor(total/60)).padStart(2,'0')+':'+String(total%60).padStart(2,'0');}
function renderProgress(state){
  const envelope=state.taskProgress,job=envelope?.job;
  const container=$('taskProgress');container.hidden=!job;document.body.classList.toggle('has-task-progress',!!job);
  if(!job)return;
  const progress=job.progress,connected=envelope.connected!==false,login=job.action==='login';
  const verified=login ? job.status==='login_verified' : verifiedStatuses.has(job.status);
  const stepFailed=progress?.state==='failed';
  const active=job.active===true && !verified && !stepFailed;
  const stepWaiting=active && ['running','waiting'].includes(progress?.state);
  container.dataset.tone=!connected?'offline':verified?'verified':active?'running':job.status==='cancelled'?'stopped':'attention';
  container.dataset.job=job.id;container.dataset.platform=job.platform;
  const platformName=job.platform_name || names[job.platform] || '平台';
  const browsingName=names[state.selected] || '素材工作台';
  const otherPage=state.selected!==job.platform;
  $('progressScope').textContent=!connected?'最后记录':active?'执行中':'最近任务';
  $('progressPlatform').textContent='平台 '+job.platform_index+'/'+job.platform_count+' · '+platformName;
  const stage=progressLabels[progress?.stage] || '';
  let label;
  if(!connected)label='进度连接中断'+(stage?' · 最后停在'+stage:'');
  else if(verified)label=login?'登录已验证':'草稿已重开核验';
  else if(stepFailed)label='此步骤失败'+(stage?' · 停在'+stage:'');
  else if(!active)label=(terminalLabels[job.status] || '任务已结束，结果待确认')+(stage?' · 停在'+stage:'');
  else if(login)label='等待扫码或完成登录验证';
  else if(progress?.state==='completed')label='本步骤已完成 · '+stage+'；等待下一步骤';
  else if(progress?.state==='skipped')label='本步骤已跳过 · '+stage+'；等待下一步骤';
  else if(stage)label=stage;
  else label='当前任务尚未上报具体步骤';
  if($('progressStage').textContent!==label)$('progressStage').textContent=label;
  const started=progress?.started_at;
  const observed=envelope.observed_at || 0;
  // The timer describes a real step's elapsed wait; it never advances steps.
  // Freeze it on disconnect/terminal state instead of implying continued work.
  const end=connected && stepWaiting ? Date.now()/1000 : !connected ? observed : progress?.updated_at || observed;
  const elapsed=typeof started==='number' && started>0 ? Math.max(0,end-started) : null;
  $('progressWait').textContent=elapsed===null ? '' : (stepWaiting && connected?'已等待 ':'停留 ')+formatWait(elapsed);
  const bar=$('progressSteps');bar.hidden=login;
  const completed=new Set((progress?.completed || []).filter(id=>id!=='verify' || verified));
  const skipped=new Set((progress?.skipped || []).filter(id=>id!=='verify'));
  if(progress && verified && !login)completed.add('verify');
  const metrics=window.DraftProgress.measure(progress,verified);
  let completeCount=0,skipCount=0;
  for(const node of bar.children){
    const id=node.dataset.stage;
    const done=completed.has(id),skip=!done && skipped.has(id);
    let kind=!progress?'unrecorded':done?'completed':skip?'skipped':'pending';
    if(!done && !skip && id===progress?.stage){
      kind=!connected?'offline':active?'running':verified?'completed':job.status==='cancelled'?'cancelled':'unconfirmed';
      if(progress.state==='failed')kind='failed';
    }
    node.dataset.state=kind;
    const measured=metrics.rows.find(row=>row.stage===id);
    const pct=measured?.percent;
    node.textContent=progressLabels[id]+' '+(pct===null?'—':pct+'%');
    node.style.setProperty('--stage-percent',(pct||0)+'%');
    node.setAttribute('aria-current',kind==='running'?'step':'false');
    node.title=progressLabels[id]+'：'+({completed:'已完成',skipped:'已跳过',pending:'未开始',running:'进行中',offline:'连接中断',cancelled:'已停止',failed:'失败',unconfirmed:'待确认',unrecorded:'无步骤记录'}[kind]);
    if(done)completeCount++;if(skip)skipCount++;
  }
  if(progress)bar.setAttribute('aria-valuenow',String(completeCount));else bar.removeAttribute('aria-valuenow');
  const scope=$('progressScope').textContent;
  const count=$('progressCount');count.hidden=login;
  count.textContent=progress?'已完成 '+completeCount+'/7 步'+(skipCount?' · 跳过 '+skipCount+' 步':''):active?'无步骤记录':'历史任务 · 无步骤记录';
  count.title=progress?'已完成 '+completeCount+' / 7 步；当前阶段见上方。':'此任务没有记录各步骤状态，不能推算完成数。';
  count.setAttribute('aria-label',progress?'已完成 '+completeCount+' / 7 步':'无步骤记录');
  bar.setAttribute('aria-valuetext',scope+'；'+$('progressPlatform').textContent+'；'+label+(progress?'；已完成 '+completeCount+' / 7 步'+(skipCount?'，跳过 '+skipCount+' 步':''):'；无步骤记录'));
  let detail;
  if(!connected)detail='无法取得最新进度，以上为最后记录；恢复连接后更新。';
  else if(!progress && !login)detail=verified?'草稿已核验；此任务未记录各步骤进度。':(active?'':'任务已结束；')+'具体步骤尚未上报，不能据此判断上传或保存成功。'+(job.message?' · '+job.message:'');
  else if(stepWaiting && elapsed!==null && elapsed>=45)detail='此步骤仍在等待平台响应'+(job.message?' · '+job.message:'');
  else if(login)detail=job.message || (active?'登录状态会保存在本机。':'');
  else detail='已完成 '+completeCount+' 步'+(skipCount?' · 跳过 '+skipCount+' 步':'')+(job.message?' · '+job.message:'');
  if(otherPage)detail=(connected && active?'正在处理 '+platformName+'；当前浏览 '+browsingName+'。':
    '当前浏览 '+browsingName+'；以上是'+(!connected?'最后的':'最近的')+platformName+'任务记录。')+' '+detail;
  renderPercent(metrics,{login,active,connected,progress,verified});
  $('progressDetail').textContent=detail;
  $('progressDetail').title=detail;
}
function renderPercent(metrics,{login,active,connected,progress,verified}){
  const percent=$('progressPercent'),meter=$('progressMeter'),fill=$('progressFill'),parts=$('progressOperations');
  percent.hidden=meter.hidden=parts.hidden=login;
  const value=metrics.percent;
  percent.textContent=value===null?'流程进度 —':'流程 '+value+'%';
  percent.title='按已完成操作计算；上传字节百分比仅在平台提供时显示。';
  fill.style.width=(value||0)+'%';
  if(value===null)meter.removeAttribute('aria-valuenow');else meter.setAttribute('aria-valuenow',String(value));
  meter.setAttribute('aria-valuetext',value===null?'未上报步骤':value+'%，'+(!connected?'最后记录':verified?'已核验':active?'处理中':'任务已停止'));
  const row=metrics.current;
  if(row){
    const signature=JSON.stringify([progress.stage,row,active,connected]);
    if(parts.dataset.signature!==signature){
      parts.dataset.signature=signature;parts.textContent='';
      for(const id of row.plan){
        const node=document.createElement('span');
        const done=row.completed.includes(id),skipped=row.skipped.includes(id),current=row.current===id;
        const pct=done||skipped?100:current&&typeof row.percent==='number'?row.percent:0;
        node.className='progress-operation';node.dataset.state=done?'completed':skipped?'skipped':current?row.state:'pending';
        node.textContent=metrics.operations[progress.stage][id]+' · '+(skipped?'跳过':pct+'%'+(current&&row.state==='running'&&!Object.hasOwn(row,'percent')?(active&&connected?' · 待回执':' · 未完成'):''));
        node.title=node.textContent;
        node.style.setProperty('--stage-percent',pct+'%');
        if(current)node.setAttribute('aria-current','step');
        parts.append(node);
      }
    }
  }else{
    parts.dataset.signature='';
    parts.textContent=progress?(active?'等待平台报告细分操作；流程百分比保持在已完成的位置。':'此历史任务只记录了大阶段；按已完成阶段显示百分比。'):'未上报具体步骤，无法推算百分比。';
  }
  if(progress?.stage==='upload'&&active&&connected&&typeof row?.percent!=='number')parts.title='平台尚未提供可读取的上传百分比；等待实际上传和处理结果，不按时间增加进度。';else parts.title='';
}
async function run(action){try{$('error').textContent='';await action();}catch(e){$('error').textContent=e.message;}}
function renderZoom(state){
  const zoom=state.zoom,available=state.selected!=='workbench' && zoom?.available===true;
  $('pageZoom').hidden=state.selected==='workbench';
  $('fitPage').disabled=!available;
  $('fitPage').setAttribute('aria-pressed',available && zoom.fit?'true':'false');
  $('zoomValue').textContent=available?Math.round(zoom.factor*100)+'%':'—';
  $('zoomValue').setAttribute('aria-label',available?'平台页面缩放 '+Math.round(zoom.factor*100)+'%，'+(zoom.fit?'自动适配':'手动设置'):'平台页面尚未准备好');
  $('pageZoom').title=available?(zoom.fit?'自动适配 · 窗口改变时同步调整':'手动缩放 · 点击适配页面恢复自动调整'):'';
  $('zoomOut').disabled=!available || zoom.factor<=zoom.min+0.0001;
  $('zoomIn').disabled=!available || zoom.factor>=zoom.max-0.0001;
}
function adjustZoom(delta){
  const zoom=currentState?.zoom;
  if(!zoom?.available) return;
  const factor=Math.max(zoom.min,Math.min(zoom.max,Math.round((zoom.factor+delta)*1000)/1000));
  return window.draftDesktop.zoom(factor);
}
function render(state){
  currentState=state;
  const connection=state.connection?.status || 'ready';
  if($('connectionPanel'))$('connectionPanel').hidden=connection==='ready';
  if($('connectionDetail'))$('connectionDetail').textContent=connection==='connecting'?'正在连接本机工作台…':'工作台连接未完成，素材和登录会话已保留。';
  if($('reconnect')){
    $('reconnect').hidden=connection!=='disconnected';
    $('reconnect').disabled=state.active===true || state.taskProgress?.job?.active===true;
  }
  if(!$('platforms').children.length){for(const p of state.platforms){names[p.id]=p.name;const button=document.createElement('button');button.className='nav';button.dataset.platform=p.id;const icon=document.createElement('span');icon.className='badge tile';icon.dataset.brand=p.id;icon.innerHTML=platformIcon(p.id);button.append(icon,document.createTextNode(p.name));button.onclick=()=>run(()=>window.draftDesktop.select(p.id));$('platforms').append(button);}}
  for(const b of document.querySelectorAll('.nav')){const id=b.id==='workbench'?'workbench':b.dataset.platform;b.classList.toggle('active',id===state.selected);b.disabled=state.navigationLocked && id!=='workbench' && id!==state.activePlatform;}
  const platform=state.selected!=='workbench';$('heading').textContent=names[state.selected]||'素材工作台';$('address').textContent=state.notice || (state.activeAction==='login'?'等待'+names[state.activePlatform]+'登录 · 可自由切换页面':state.active?(progressLabels[state.taskProgress?.job?.progress?.stage] || '正在准备上传')+' · 可返回素材工作台':state.loading?'正在加载…':state.url||'选择视频、封面和文案');
  $('back').hidden=!platform;$('reload').hidden=!platform;$('back').disabled=state.navigationLocked || !state.canGoBack;$('reload').disabled=state.navigationLocked;$('closePopup').hidden=!state.popup;
  $('repairDisplay').hidden=!platform;$('repairDisplay').disabled=!state.zoom?.available;
  $('stopLogin').hidden=state.activeAction!=='login';
  const manual=state.publicationMode==='manual_publish';
  $('publicationMode').textContent=manual?'切回草稿模式':'开启网页发表';
  $('publicationMode').setAttribute('aria-pressed',String(manual));
  $('publicationMode').disabled=state.active===true || state.taskProgress?.job?.active===true;
  $('publicationLabel').textContent=manual?(state.manualPublishAllowed?'可在平台发表':'任务中 · 仅保存草稿'):'仅保存草稿';
  renderZoom(state);
  renderProgress(state);
}
$('publicationMode').onclick=()=>run(()=>window.draftDesktop.publicationMode(currentState?.publicationMode==='manual_publish'?'draft':'manual_publish'));
$('workbench').onclick=()=>run(()=>window.draftDesktop.select('workbench'));
$('reload').onclick=()=>run(()=>window.draftDesktop.reload());$('back').onclick=()=>run(()=>window.draftDesktop.back());$('closePopup').onclick=()=>run(()=>window.draftDesktop.closePopup());
$('repairDisplay').onclick=()=>run(()=>window.draftDesktop.repairDisplay());
$('stopLogin').onclick=()=>run(()=>window.draftDesktop.stopLogin());
if($('reconnect'))$('reconnect').onclick=()=>run(()=>window.draftDesktop.reconnect());
$('fitPage').onclick=()=>run(()=>window.draftDesktop.fit());
$('zoomOut').onclick=()=>run(()=>adjustZoom(-0.05));$('zoomIn').onclick=()=>run(()=>adjustZoom(0.05));
window.draftDesktop.onState(render);window.draftDesktop.state().then(render);
setInterval(()=>{if(currentState)renderProgress(currentState);},1000);
