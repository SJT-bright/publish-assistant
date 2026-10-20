'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {runPageOperation,notifyPageUnload}=require('../desktop/page-operations.cjs');
const {createNativeViewPainter}=require('../desktop/native-view-paint.cjs');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function fixture() {
  const wc=new EventEmitter();
  Object.assign(wc,{url:'https://fixture.invalid/creator',destroyed:false,reloads:0,stops:0,
    getURL(){return this.url;},isDestroyed(){return this.destroyed;},
    reload(){this.reloads++;},stop(){this.stops++;}});
  const view={webContents:wc,bounds:[],setBounds(value){this.bounds.push({...value});}};
  const states=[];
  let selected=view;
  const options={view,isCurrent:target=>selected===target,onState:state=>states.push(state),
    timeoutMs:300,pollMs:5};
  const start=url=>{wc.url=url||wc.url;wc.emit('did-start-navigation',{},wc.url,false,true);};
  const done=()=>{wc.emit('dom-ready');wc.emit('did-finish-load');};
  return {wc,view,states,options,start,done,select(value){selected=value;}};
}
function assertClean(wc) {
  for(const event of ['destroyed','did-start-navigation','dom-ready','did-finish-load','did-fail-load']) {
    assert.equal(wc.listenerCount(event),0,event+' listener leaked');
  }
}

test('waiting for the real unload choice pauses the load timeout, then preserving cancels cleanly',async()=>{
  const f=fixture();let ended=false;
  const pending=runPageOperation({...f.options,kind:'reload',timeoutMs:20}).then(r=>{ended=true;return r;});
  notifyPageUnload(f.wc,'waiting');await pause(40);
  assert.equal(ended,false);assert.equal(f.states.at(-1).phase,'awaiting_choice');
  notifyPageUnload(f.wc,'cancel');const result=await pending;
  assert.equal(result.status,'cancelled');assert.equal(result.errorCode,'PAGE_UNLOAD_CANCELLED');
  assert.match(result.detail,/保留/);assertClean(f.wc);
  const count=f.states.length;notifyPageUnload(f.wc,'continue');f.done();assert.equal(f.states.length,count);
});

test('confirming leave restarts bounded loading and still requires the actual document',async()=>{
  const f=fixture();let ended=false;
  const pending=runPageOperation({...f.options,kind:'reload',timeoutMs:25}).then(r=>{ended=true;return r;});
  notifyPageUnload(f.wc,'waiting');await pause(40);notifyPageUnload(f.wc,'continue');
  await pause(5);assert.equal(ended,false);f.start();f.done();
  assert.equal((await pending).status,'completed');assertClean(f.wc);
});

test('reload stays loading until fresh main navigation, DOM and final load both arrive',async()=>{
  const f=fixture();let finished=false;
  const pending=runPageOperation({...f.options,kind:'reload'}).then(result=>{finished=true;return result;});
  assert.equal(f.wc.reloads,1);assert.equal(f.states[0].status,'running');
  assert.equal(f.states[0].phase,'loading');assert.equal(f.states[0].finishedTime,null);
  f.wc.emit('dom-ready');f.wc.emit('did-finish-load');await pause(10);assert.equal(finished,false);
  f.start();f.wc.emit('dom-ready');await pause(10);assert.equal(finished,false);
  f.wc.emit('did-finish-load');const receipt=await pending;
  assert.equal(receipt.status,'completed');assert.equal(receipt.domReady,true);assert.equal(receipt.loadFinished,true);
  assert.ok(receipt.finishedTime>=receipt.startTime);assert.equal(f.states.at(-1).status,'completed');assertClean(f.wc);
});

test('same-origin aborted old navigation waits for actual successor load',async()=>{
  const f=fixture();const pending=runPageOperation({...f.options,kind:'reload'});
  f.start();f.wc.emit('did-fail-load',{},-3,'ERR_ABORTED',f.wc.url,true);
  assert.equal(f.states.at(-1).status,'running');assert.match(f.states.at(-1).detail,/跳转/);
  f.start('https://fixture.invalid/creator/editor');f.done();
  assert.equal((await pending).status,'completed');assertClean(f.wc);
});

test('late old-request abort does not discard actual successor DOM readiness',async()=>{
  const f=fixture();const original=f.wc.url;const pending=runPageOperation({...f.options,kind:'reload'});
  f.start('https://fixture.invalid/creator/editor');f.wc.emit('dom-ready');
  f.wc.emit('did-fail-load',{},-3,'ERR_ABORTED',original,true);f.wc.emit('did-finish-load');
  assert.equal((await pending).status,'completed');assertClean(f.wc);
});

test('ERR_ABORTED alone times out and never reports successful reload',async()=>{
  const f=fixture();const pending=runPageOperation({...f.options,kind:'reload',timeoutMs:20});
  f.start();f.wc.emit('did-fail-load',{},-3,'ERR_ABORTED',f.wc.url,true);
  const result=await pending;assert.equal(result.status,'failed');assert.equal(result.errorCode,'PAGE_LOAD_TIMEOUT');
  assert.equal(f.wc.stops,1);assertClean(f.wc);
});

test('subframe ready/failure does not prove or fail main page loading',async()=>{
  const f=fixture();const pending=runPageOperation({...f.options,kind:'reload'});
  f.wc.emit('did-start-navigation',{},f.wc.url,false,false);
  f.wc.emit('did-fail-load',{},-105,'ERR_NAME_NOT_RESOLVED','https://frame.invalid/',false);
  f.start();f.done();assert.equal((await pending).status,'completed');assertClean(f.wc);
});

test('network error, destroyed page and asynchronous reload rejection give terminal failure receipts',async()=>{
  for(const kind of ['network','destroyed','rejected']) {
    const f=fixture();
    if(kind==='rejected')f.wc.reload=function(){this.reloads++;return Promise.reject(new Error('fixture rejection'));};
    const pending=runPageOperation({...f.options,kind:'reload'});
    if(kind==='network'){f.start();f.wc.emit('did-fail-load',{},-105,'ERR_NAME_NOT_RESOLVED',f.wc.url,true);}
    if(kind==='destroyed'){f.wc.destroyed=true;f.wc.emit('destroyed');}
    const result=await pending;assert.equal(result.status,'failed',kind);
    assert.equal(f.states.at(-1).status,'failed',kind);assertClean(f.wc);
  }
});

test('page switch cancels old operation without painting new page state or stopping its renderer',async()=>{
  const f=fixture();const pending=runPageOperation({...f.options,kind:'reload'});
  f.start();const count=f.states.length;f.select({});await pause(15);
  const result=await pending;assert.equal(result.status,'cancelled');assert.equal(result.errorCode,'PAGE_CHANGED');
  f.done();assert.equal(f.states.length,count);assert.equal(f.wc.stops,0);assertClean(f.wc);
});

test('explicit stop cleans up before synchronous stop failure and a later reload can recover',async()=>{
  const f=fixture();const controller=new AbortController();
  f.wc.stop=function(){this.stops++;this.emit('did-fail-load',{},-7,'fixture stopped',this.url,true);};
  const pending=runPageOperation({...f.options,kind:'reload',signal:controller.signal});
  f.start();controller.abort();const result=await pending;
  assert.equal(result.status,'cancelled');assert.equal(f.wc.stops,1);assertClean(f.wc);
  const resumed=runPageOperation({...f.options,kind:'reload'});f.start();f.done();
  assert.equal((await resumed).status,'completed');assert.equal(f.wc.reloads,2);assertClean(f.wc);
});

test('new operation cancels prior listener ownership and receives only its own completion',async()=>{
  const f=fixture();const first=runPageOperation({...f.options,kind:'reload'});f.start();
  const second=runPageOperation({...f.options,kind:'reload'});f.start();f.done();
  assert.equal((await first).errorCode,'PAGE_SUPERSEDED');assert.equal((await second).status,'completed');
  assert.equal(f.wc.listenerCount('dom-ready'),0);assertClean(f.wc);
});

test('cross-origin navigation cannot become successful platform reload',async()=>{
  const f=fixture();const pending=runPageOperation({...f.options,kind:'reload'});
  f.start('https://other.invalid/login');f.done();const result=await pending;
  assert.equal(result.status,'failed');assert.equal(result.errorCode,'PAGE_ORIGIN_CHANGED');assertClean(f.wc);
});

test('repair runs native bounds only and its loading feedback spans the actual painter frame',async()=>{
  const f=fixture();const target={x:224,y:76,width:1000,height:700};
  const painter=createNativeViewPainter({bounds:()=>target,canPaint:()=>true,delay:15});
  const result=await runPageOperation({...f.options,kind:'repair',repair:(view,options)=>painter.repair(view,options)});
  assert.equal(result.status,'completed');assert.equal(f.states[0].phase,'painting');
  assert.deepEqual(f.view.bounds,[{...target,width:999},target]);
  assert.equal(f.wc.reloads,0);assert.equal(f.wc.stops,0);assert.ok(result.elapsedMs>=10);assertClean(f.wc);
});

test('repair abort restores bounds, gives cancelled receipt and does not reload',async()=>{
  const f=fixture();const controller=new AbortController();const target={x:0,y:0,width:900,height:600};
  const painter=createNativeViewPainter({bounds:()=>target,canPaint:()=>true,delay:40});
  const pending=runPageOperation({...f.options,kind:'repair',signal:controller.signal,
    repair:(view,options)=>painter.repair(view,options)});
  await pause(5);controller.abort();assert.equal((await pending).status,'cancelled');await pause(50);
  assert.deepEqual(f.view.bounds.at(-1),target);assert.equal(f.wc.reloads,0);assert.equal(f.wc.stops,0);assertClean(f.wc);
});

test('repair failure and timeout are explicit and do not claim completed',async()=>{
  for(const repair of [()=>false,()=>Promise.reject(new Error('fixture paint failure')),()=>new Promise(()=>{})]) {
    const f=fixture();const result=await runPageOperation({...f.options,kind:'repair',repair,timeoutMs:20});
    assert.equal(result.status,'failed');assert.ok(['PAGE_REPAIR_FAILED','PAGE_REPAIR_TIMEOUT'].includes(result.errorCode));
    assert.equal(f.wc.reloads,0);assertClean(f.wc);
  }
});

test('navigation during repair cancels the old-document feedback without reloading or stopping the page',async()=>{
  const f=fixture();const target={x:0,y:0,width:900,height:600};
  const painter=createNativeViewPainter({bounds:()=>target,canPaint:()=>true,delay:40});
  const pending=runPageOperation({...f.options,kind:'repair',repair:(view,options)=>painter.repair(view,options)});
  await pause(5);f.start('https://fixture.invalid/creator/other');
  const result=await pending;assert.equal(result.status,'cancelled');assert.equal(result.errorCode,'PAGE_NAVIGATED');
  assert.equal(f.wc.reloads,0);assert.equal(f.wc.stops,0);assert.deepEqual(f.view.bounds.at(-1),target);assertClean(f.wc);
});

test('feedback callback errors and already-aborted calls cannot leak operations',async()=>{
  const f=fixture();const pending=runPageOperation({...f.options,kind:'reload',onState:()=>{throw Error('fixture UI failure');}});
  f.start();f.done();assert.equal((await pending).status,'completed');assertClean(f.wc);
  const controller=new AbortController();controller.abort();const reloads=f.wc.reloads,stops=f.wc.stops;
  const result=await runPageOperation({...f.options,kind:'reload',signal:controller.signal});
  assert.equal(result.status,'cancelled');assert.equal(f.wc.reloads,reloads);assert.equal(f.wc.stops,stops);assertClean(f.wc);
});
