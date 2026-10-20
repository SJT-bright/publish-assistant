'use strict';
// Execute the actual inline controller in a small DOM with fixture-only fetch.
// No browser, platform, backend, filesystem state or account is opened.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../draft_workbench.html'),'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
class Element {
  constructor(){this.children=[];this.attributes={};this.style={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.dataset={};this.classes=new Set();
    this.classList={contains:key=>this.classes.has(key),add:key=>this.classes.add(key),remove:key=>this.classes.delete(key),
      toggle:(key,force)=>{if(force)this.classes.add(key);else this.classes.delete(key);}};}
  set className(value){this.classes=new Set(value.split(/\s+/));}
  get className(){return [...this.classes].join(' ');}
  append(...values){this.children.push(...values);}
  replaceChildren(...values){this.children=[...values];}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(){}
  querySelectorAll(){return this.children.flatMap(child=>[...(child.type==='checkbox'&&child.checked?[child]:[]),...(child.querySelectorAll?.()||[])]);}
  focus(){this.focused=true;}
  scrollIntoView(){this.scrolled=true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
const plain=value=>JSON.parse(JSON.stringify(value));
const response=value=>({ok:true,json:async()=>value});
const preflight=errors=>({errors:errors||[],warnings:[],mappings:{},policies:{},fingerprints:{}});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
async function flush(){for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));}
async function harness(){
  const elements=Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
  const checkbox={checked:true,dataset:{platform:'tencent'}},login=new Element(),calls=[];
  let checkboxes=[checkbox],commonTags=[];
  let state={token:'fixture-token',job:null,platforms:[],history:[],editor:{}},handler=null;
  const document={documentElement:new Element(),body:new Element(),getElementById:id=>elements[id],createElement:()=>new Element(),
    createTextNode:text=>({textContent:text}),addEventListener:()=>{},
    querySelector:()=>null,querySelectorAll:selector=>selector==='[data-platform]:checked'?checkboxes.filter(c=>c.checked):selector==='.login'?[login]:[]};
  const external=async(url,options)=>{
    calls.push({url,body:options?.body?JSON.parse(options.body):null});
    if(handler){const result=handler(url,options);if(result!==undefined)return result;}
    if(url.startsWith('/api/state'))return response(state);
    if(url==='/api/editor/clear')return response({result:{payload:{},platforms:checkboxes.filter(c=>c.checked).map(c=>c.dataset.platform)}});
    if(url==='/api/preflight')return response({result:preflight()});
    if(url==='/api/text-check')return response({result:{errors:[],feedback:{},summaries:{title:'fixture',description:'fixture',tags:'fixture'}}});
    return response({result:{}});
  };
  const store=require('../ui-assets/field-overrides.js').createStore({getGlobal:()=>({tags:commonTags,title:elements.title.value,description:elements.description.value}),parseTags:value=>Array.isArray(value)?value:[]});
  const opened=[];
  const overrides={...store,refresh:()=>{},setFeedback:()=>{},add:()=>{},open:(...args)=>opened.push(args),
    setField:(...args)=>{store.setField(...args);sandbox.controller?.dirty();}};
  const sandbox={document,window:{addEventListener:()=>{},TagEditor:{parse:()=>[],attach:()=>({get:()=>commonTags.slice(),set:values=>{commonTags=values.slice();},suggest:()=>{}})},
    FieldOverrides:{mount:()=>overrides},MaterialEditor:{mount:()=>({update:()=>{}})}},fetch:external,console,Date,Map,
    setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1};
  vm.createContext(sandbox);
  vm.runInContext(script+`
    globalThis.controller={start,refresh,updateTextCheck,renderResults,dirty,updateActions,dateText,openTagChoices,updateTagChoiceCount,loadVideo,payload,clearCurrentDraft,undoClearDraft,
      flushEditor:()=>window.flushWorkbenchEditor(),
      platformName:(id,name)=>{platformNames[id]=name;},
      status:()=>({busy,submitting,checking}),retry:(mode,target)=>{retryMode=mode;retryTarget=target;},
      desktop:value=>{window.draftDesktop=value;}};`,sandbox,{filename:'draft_workbench.html'});
  await flush();
  return {api:sandbox.controller,elements,calls,login,handler:fn=>{handler=fn;},state:value=>{state={...state,...value};},checkbox,overrides,opened,
    commonTags:values=>{commonTags=values.slice();},select:ids=>{checkboxes=ids.map(platform=>({checked:true,dataset:{platform}}));}};
}
test('one click clears homepage material and overrides, preserves platform choice and supports undo',async()=>{
  const h=await harness();
  h.elements.title.value='旧标题';h.elements.description.value='完整文案';h.commonTags(['科普']);
  h.elements.coverLandscape.value='/fixture/横版.png';h.elements.coverPortrait.value='/fixture/竖版.png';
  h.overrides.set({tencent:{description:'平台文案',tags:['独立话题'],cover:'/fixture/专用.png'}});
  const previous=plain(h.api.payload());
  await h.elements.clearDraft.onclick();
  assert.equal(h.elements.title.value,'');assert.equal(h.elements.description.value,'');
  assert.equal(h.elements.coverLandscape.value,'');assert.equal(h.elements.coverPortrait.value,'');
  assert.deepEqual(plain(h.api.payload().tags),[]);assert.deepEqual(plain(h.overrides.get()),{});
  assert.equal(h.checkbox.checked,true);assert.equal(h.elements.undoClearDraft.hidden,false);
  assert.deepEqual(h.calls.find(call=>call.url==='/api/editor/clear').body,{platforms:['tencent']});
  await h.elements.undoClearDraft.onclick();
  assert.deepEqual(plain(h.api.payload()),previous);assert.equal(h.elements.undoClearDraft.hidden,true);
  assert.deepEqual(h.calls.filter(call=>call.url==='/api/editor').at(-1).body.payload,previous);
});
test('clear waits for an in-flight save, discards stale video imports, and flush cannot restore old content',async()=>{
  const h=await harness(),saved=deferred(),loaded=deferred(),cleared=deferred();
  h.elements.title.value='迟到的旧草稿';
  h.handler(url=>url==='/api/editor'?saved.promise:url==='/api/load'?loaded.promise:url==='/api/editor/clear'?cleared.promise:undefined);
  const writing=h.api.flushEditor();await flush();
  const importing=h.api.loadVideo('/fixture/old.mp4');await flush();
  const clearing=h.elements.clearDraft.onclick();await flush();
  assert.equal(h.calls.filter(call=>call.url==='/api/editor/clear').length,0);
  assert.equal(h.elements.save.disabled,true);assert.equal(h.elements.clearDraft.disabled,true);
  loaded.resolve(response({result:{video:'/fixture/old.mp4',title:'不应恢复'}}));await importing;
  saved.resolve(response({result:{}}));await writing;await flush();
  const closing=h.api.flushEditor();
  assert.equal(h.calls.filter(call=>call.url==='/api/editor').length,1);
  cleared.resolve(response({result:{payload:{},platforms:['tencent']}}));await clearing;await closing;
  assert.equal(h.elements.title.value,'');assert.equal(h.api.payload().video,'');
  assert.equal(h.elements.videoDrop.classList.contains('importing'),false);
  assert.equal(h.elements.clearDraft.disabled,false);
});
test('clearing is blocked during active work and a rejected clear preserves material',async()=>{
  const h=await harness();h.elements.title.value='需要保留';
  h.state({job:{id:'active',active:true,action:'draft',results:[],logs:[]}});await h.api.refresh();
  await h.elements.clearDraft.onclick();assert.equal(h.calls.filter(call=>call.url==='/api/editor/clear').length,0);
  assert.equal(h.elements.clearDraft.disabled,true);assert.equal(h.elements.title.value,'需要保留');
  h.state({job:null});await h.api.refresh();
  h.handler(url=>url==='/api/editor/clear'?Promise.reject(new Error('保存失败')):undefined);
  await h.elements.clearDraft.onclick();assert.equal(h.elements.title.value,'需要保留');
  assert.match(h.elements.notice.textContent,/草稿未清空/);assert.equal(h.elements.clearDraft.disabled,false);
});
test('preflight and start phases stay locked across idle polls, text checks and duplicate start calls',async()=>{
  const h=await harness(),checked=deferred(),accepted=deferred();
  h.handler(url=>url==='/api/preflight'?checked.promise:url==='/api/start'?accepted.promise:undefined);
  const starting=h.api.start('draft',['tencent']);await flush();
  assert.equal(h.elements.saveLabel.textContent,'正在检查素材…');
  assert.equal(h.elements.save.disabled,true);assert.equal(h.login.disabled,true);
  await h.api.refresh();await h.api.updateTextCheck();
  assert.equal(h.elements.save.disabled,true);assert.equal(h.elements.precheck.disabled,true);
  await h.api.start('draft',['tencent']);
  assert.equal(h.calls.filter(call=>call.url==='/api/preflight').length,1);
  checked.resolve(response({result:preflight()}));await flush();
  assert.equal(h.elements.saveLabel.textContent,'正在启动上传…');
  await h.api.refresh();assert.equal(h.elements.save.disabled,true);
  assert.equal(h.elements.save.getAttribute('aria-busy'),'true');
  h.state({job:{id:'fixture-job',action:'draft',active:true,created_at:100,results:[],logs:[]}});
  accepted.resolve(response({result:{id:'fixture-job'}}));await starting;
  assert.deepEqual(plain(h.api.status()),{busy:true,submitting:false,checking:false});
  assert.equal(h.elements.saveLabel.textContent,'正在上传并保存草稿…');
  assert.equal(h.elements.save.disabled,true);assert.equal(h.elements.cancel.disabled,false);
  assert.equal(h.calls.filter(call=>call.url==='/api/start').length,1);
});
test('rejected preflight and failed starts release the lock without clearing an existing text limit',async()=>{
  const h=await harness();
  h.handler(url=>url==='/api/preflight'?response({result:preflight([{message:'fixture limit'}])}):undefined);
  h.elements.textLimitIssues.classList.add('limit-error');
  await h.api.start('draft',['tencent']);
  assert.equal(h.calls.filter(call=>call.url==='/api/start').length,0);
  assert.equal(h.api.status().submitting,false);assert.equal(h.elements.save.disabled,false);
  assert.match(h.elements.notice.textContent,/预检未通过/);
  h.elements.textLimitIssues.classList.remove('limit-error');
  h.handler(url=>url==='/api/start'?Promise.reject(new Error('fixture start rejected')):undefined);
  await h.api.start('draft',['tencent']);
  assert.equal(h.elements.saveLabel.textContent,'开始上传并保存草稿');
  assert.equal(h.elements.save.disabled,false);assert.equal(h.elements.save.getAttribute('aria-busy'),'false');
  assert.equal(h.elements.notice.textContent,'fixture start rejected');
});
test('standalone material checking cannot be undone by a poll or start a competing task',async()=>{
  const h=await harness(),checked=deferred();
  h.handler(url=>url==='/api/preflight'?checked.promise:undefined);
  const checking=h.elements.precheck.onclick();await flush();
  assert.equal(h.elements.precheck.textContent,'正在检查素材…');
  await h.api.refresh();await h.api.updateTextCheck();await h.api.start('draft',['tencent']);
  assert.equal(h.elements.save.disabled,true);assert.equal(h.elements.precheck.disabled,true);
  assert.equal(h.calls.filter(call=>call.url==='/api/start').length,0);
  checked.resolve(response({result:preflight()}));await checking;
  assert.equal(h.elements.precheck.textContent,'检查素材');
  assert.equal(h.elements.save.disabled,false);assert.equal(h.api.status().checking,false);
});
const ksTopics=['vibe知识大赏','热力学','claude','科普','熵'];
function ksLimit(){return {errors:[{platform:'kuaishou',field:'tags',message:'话题 5/4 个，请单独设置此平台话题。'}],
  feedback:{kuaishou:{tags:{used:5,max:4,text:'5/4 个话题',error:true}}},summaries:{title:'',description:'389/500',tags:'快手 5/4 个话题'}};}
test('selecting Kuaishou exposes an actionable limit beside an enabled upload entry; invalid content cannot start a job',async()=>{
  const h=await harness();h.select(['tencent','kuaishou']);h.api.platformName('kuaishou','快手');h.commonTags(ksTopics);
  h.handler(url=>url==='/api/text-check'?response({result:ksLimit()}):url==='/api/preflight'?response({result:preflight(ksLimit().errors)}):undefined);
  await h.api.updateTextCheck();
  assert.equal(h.elements.save.disabled,false);assert.equal(h.elements.uploadIssues.hidden,false);
  const row=h.elements.uploadIssueList.children[0];assert.match(row.children[0].textContent,/快手.*5\/4/);
  assert.equal(row.children[1].textContent,'设置快手独立话题');
  await h.api.start('draft',['tencent','kuaishou']);
  assert.equal(h.calls.filter(call=>call.url==='/api/start').length,0);
  assert.equal(h.elements.tagChoiceDialog.open,true);assert.equal(h.elements.tagChoiceApply.disabled,true);
  assert.match(h.elements.notice.textContent,/预检未通过.*快手.*5\/4/);
  assert.equal(h.elements.save.disabled,false);
});
test('Kuaishou topic choices require explicit confirmation and preserve all common tags and other platform overrides',async()=>{
  const h=await harness();h.select(['kuaishou','tencent']);h.commonTags(ksTopics);
  h.overrides.set({tencent:{description:'保留视频号独立文案'},kuaishou:{cover_portrait:'保留竖版封面'}});
  const before=plain(h.overrides.get());
  h.handler(url=>url==='/api/text-check'?response({result:ksLimit()}):undefined);await h.api.updateTextCheck();
  h.elements.uploadIssueList.children[0].children[1].onclick();
  h.elements.tagChoiceApply.onclick();assert.deepEqual(plain(h.overrides.get()),before);
  h.elements.tagChoiceCancel.onclick();assert.deepEqual(plain(h.overrides.get()),before);
  h.api.openTagChoices('kuaishou');
  h.elements.tagChoiceOptions.children[2].children[0].checked=false;h.api.updateTagChoiceCount();
  assert.equal(h.elements.tagChoiceApply.disabled,false);assert.match(h.elements.tagChoiceCount.textContent,/4 \/ 4/);
  h.elements.tagChoiceApply.onclick();
  assert.deepEqual(plain(h.overrides.resolve('kuaishou','tags')),ksTopics.filter(t=>t!=='claude'));
  assert.deepEqual(plain(h.overrides.resolve('tencent','tags')),ksTopics);
  assert.deepEqual(plain(h.overrides.get().tencent),before.tencent);
  assert.equal(h.overrides.get().kuaishou.cover_portrait,before.kuaishou.cover_portrait);
  assert.equal(h.elements.tagChoiceDialog.open,false);
  h.handler(url=>url==='/api/text-check'?response({result:{errors:[],feedback:{},summaries:{title:'',description:'',tags:''}}}):undefined);
  await h.api.updateTextCheck();assert.equal(h.elements.uploadIssues.hidden,true);assert.equal(h.elements.save.disabled,false);
});
test('a stale text-limit response cannot restore Kuaishou errors after deselection',async()=>{
  const h=await harness(),pending=deferred();h.select(['kuaishou']);
  h.handler(url=>url==='/api/text-check'?pending.promise:undefined);
  const old=h.api.updateTextCheck();h.select(['tencent']);h.api.dirty();
  h.handler(url=>url==='/api/text-check'?response({result:{errors:[],feedback:{},summaries:{title:'',description:'',tags:''}}}):undefined);
  await h.api.updateTextCheck();pending.resolve(response({result:ksLimit()}));await old;
  assert.equal(h.elements.uploadIssues.hidden,true);assert.equal(h.elements.save.disabled,false);
});
test('failed text-limit refresh leaves an accessible preflight entry, while zero selections stay disabled',async()=>{
  const h=await harness();h.select(['kuaishou']);
  h.handler(url=>url==='/api/text-check'?response({result:ksLimit()}):undefined);await h.api.updateTextCheck();
  h.handler(url=>url==='/api/text-check'?Promise.reject(new Error('fixture timeout')):undefined);await h.api.updateTextCheck();
  assert.equal(h.elements.save.disabled,false);assert.match(h.elements.textLimitIssues.textContent,/先重新检查/);
  h.select([]);await h.api.updateTextCheck();assert.equal(h.elements.save.disabled,true);assert.equal(h.elements.uploadIssues.hidden,true);
});
test('resume submission uses the same lock and cannot race a new full upload',async()=>{
  const h=await harness(),resumed=deferred();
  h.api.retry('resume',{expected_job_id:'fixture-old',platform:'tencent',browser:'embedded'});
  h.handler(url=>url==='/api/resume'?resumed.promise:undefined);
  const submitting=h.elements.retryConfirm.onclick();await flush();
  assert.equal(h.elements.saveLabel.textContent,'正在继续已上传素材…');
  await h.api.refresh();await h.api.updateTextCheck();await h.api.start('draft',['tencent']);
  assert.equal(h.elements.save.disabled,true);
  assert.equal(h.calls.filter(call=>call.url==='/api/start').length,0);
  resumed.reject(new Error('fixture resume rejected'));await submitting;
  assert.equal(h.elements.retryMessage.textContent,'fixture resume rejected');
  assert.equal(h.elements.retryConfirm.disabled,false);
  assert.equal(h.elements.save.disabled,false);assert.equal(h.api.status().submitting,false);
});
test('old receipts retain their data but visibly identify the previous task time and submitted material',async()=>{
  const h=await harness();
  const job={id:'fixture-old',action:'draft',active:false,created_at:1700000000,logs:['fixture log'],
    results:[{name:'视频号',platform:'tencent',status:'platform_draft_verified',message:'fixture verified'}]};
  const before=plain(job);h.api.renderResults(job);
  assert.equal(h.elements.resultContext.hidden,false);
  assert.match(h.elements.resultContext.textContent,/^最近任务 · /);
  assert.ok(h.elements.resultContext.textContent.includes(h.api.dateText(job.created_at)));
  assert.match(h.elements.resultContext.textContent,/回执对应上次任务提交的素材/);
  const receipt=h.elements.results.children[0];
  h.elements.video.value='another-fixture.mp4';h.api.dirty();h.api.renderResults(job);
  assert.equal(h.elements.results.children[0],receipt);
  assert.equal(receipt.classList.contains('good'),true);
  assert.match(h.elements.resultContext.textContent,/最近任务.*上次任务提交的素材/);
  assert.deepEqual(plain(job),before);
  h.api.renderResults({...job,active:true});
  assert.match(h.elements.resultContext.textContent,/^执行中 · .*本次任务提交的素材/);
  h.api.renderResults({...job,created_at:null});
  assert.match(h.elements.resultContext.textContent,/最近任务 · 时间未记录/);
  h.api.renderResults(null);assert.equal(h.elements.resultContext.hidden,true);
});
