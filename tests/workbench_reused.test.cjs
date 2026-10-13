'use strict';
// Reuse the fixture-only controller harness; do not open any platform or App.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const setup=fs.readFileSync(path.join(__dirname,'workbench_actions.test.cjs'),'utf8').split('\ntest(')[0];
const harness=new Function('require','__dirname',setup+'\nreturn harness;')(require,__dirname);
const job=(status='result_unknown',extra={})=>({id:'fixture-current',browser:'embedded',action:'draft',active:false,
  created_at:100,logs:[],results:[{platform:'xiaohongshu',name:'小红书',status,reused:true,
    previous_job_id:'fixture-original',message:'前次任务未完成核验；未自动重复上传。\n原任务原因：fixture cover timeout',
    progress:{stage:'cover',label:'设置封面',state:'unknown',completed:['session','video','upload','content'],skipped:[]},...extra}]});
const actions=h=>h.elements.results.children[0].children.find(el=>el.classList?.contains('actions'));
test('reused resume displays original steps and submits both current and original task references',async()=>{
  const h=await harness();
  h.api.desktop({select:async()=>{}});
  h.api.renderResults(job('result_unknown',{resumable:true,resume_previous_job_id:'fixture-original',retryable:true}));
  const row=h.elements.results.children[0];
  assert.ok(row.children.some(el=>el.textContent.includes('原记录步骤：设置封面 · 已完成 4/7 步')));
  const resume=actions(h).children.find(el=>el.textContent==='继续已上传素材');
  assert.ok(resume);assert.equal(actions(h).children.some(el=>el.textContent.startsWith('重新上传')),false);
  resume.onclick();
  assert.match(h.elements.retryMessage.textContent,/继续原任务 fixture-original/);
  await h.elements.retryConfirm.onclick();
  const call=h.calls.find(call=>call.url==='/api/resume');
  assert.equal(call.body.expected_job_id,'fixture-current');
  assert.equal(call.body.previous_job_id,'fixture-original');
  assert.equal(call.body.platform,'xiaohongshu');assert.equal(call.body.browser,'embedded');
  assert.equal(h.calls.some(call=>call.url==='/api/retry'||call.url==='/api/start'),false);
});
test('risk and verified reused records offer inspection without an automatic retry or resume',async()=>{
  const h=await harness();h.api.desktop({select:async()=>{}});
  for(const status of ['risk_paused','platform_restricted','local_draft_verified']){
    h.api.renderResults(job(status,{resumable:false,retryable:false,progress:null}));
    assert.deepEqual(actions(h).children.map(el=>el.textContent),['打开小红书页面']);
  }
});
test('explicit reupload is labelled as a new upload and requires separate confirmation',async()=>{
  const h=await harness();h.api.desktop({select:async()=>{}});
  h.api.renderResults(job('result_unknown',{resumable:false,retryable:true,retry_previous_job_id:'fixture-original'}));
  const retry=actions(h).children.find(el=>el.textContent==='重新上传小红书');
  assert.ok(retry);retry.onclick();
  assert.match(h.elements.retryMessage.textContent,/确认平台没有需要保留的同素材草稿/);
  assert.equal(h.calls.some(call=>call.url==='/api/retry'),false);
});
