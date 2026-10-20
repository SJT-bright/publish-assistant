'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {createNativeViewPainter}=require('../desktop/native-view-paint.cjs');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function fixture() {
  let visible=true,destroyed=false,target={x:224,y:76,width:1100,height:750},settles=0;
  const view={webContents:{isDestroyed:()=>destroyed},calls:[],setBounds(bounds){this.calls.push({...bounds});}};
  const painter=createNativeViewPainter({canPaint:()=>visible,bounds:()=>target,settled:()=>settles++,delay:10});
  return {view,painter,hide(){visible=false;},destroy(){destroyed=true;},resize(){target={x:224,y:240,width:1000,height:600};},
    get target(){return target;},get settles(){return settles;}};
}

test('repair restores current layout after resize and reports one real settle',async()=>{
  const f=fixture();const pending=f.painter.repair(f.view);f.resize();assert.equal(await pending,true);
  assert.deepEqual(f.view.calls.at(-1),f.target);assert.equal(f.settles,1);
});

test('hidden or destroyed view never emits a late successful paint',async()=>{
  for(const action of ['hide','destroy']) {
    const f=fixture();const pending=f.painter.repair(f.view);f[action]();assert.equal(await pending,false);assert.equal(f.settles,0);
  }
});

test('abort cancels pending frame, restores bounds and permits a new repair',async()=>{
  const f=fixture();const controller=new AbortController();const pending=f.painter.repair(f.view,{signal:controller.signal});
  controller.abort();assert.equal(await pending,false);assert.deepEqual(f.view.calls.at(-1),f.target);
  const count=f.view.calls.length;await pause(20);assert.equal(f.view.calls.length,count);
  assert.equal(await f.painter.repair(f.view),true);assert.equal(f.settles,1);
});

test('stop cancels queued and active work; a subsequent explicit repair recovers',async()=>{
  const f=fixture();const pending=f.painter.repair(f.view);f.painter.stop();assert.equal(await pending,false);
  f.painter.queue(f.view);f.painter.stop();const count=f.view.calls.length;await pause(20);
  assert.equal(f.view.calls.length,count);assert.equal(f.settles,0);assert.equal(await f.painter.repair(f.view),true);
});

test('bounds errors, unavailable layout and throwing visibility checks give false receipts',async()=>{
  for(const failure of ['setBounds','bounds','canPaint']) {
    const view={webContents:{isDestroyed:()=>false},setBounds(){if(failure==='setBounds')throw Error('fixture native error');}};
    const painter=createNativeViewPainter({delay:1,
      bounds:()=>{if(failure==='bounds')throw Error('fixture bounds error');return {x:0,y:0,width:800,height:600};},
      canPaint:()=>{if(failure==='canPaint')throw Error('fixture hidden');return true;}});
    assert.equal(await painter.repair(view),false,failure);
  }
});

test('restore-frame error and feedback callback error do not leave pending promises',async()=>{
  let count=0;const view={webContents:{isDestroyed:()=>false},setBounds(){if(++count===2)throw Error('fixture restore failure');}};
  const painter=createNativeViewPainter({delay:1,canPaint:()=>true,bounds:()=>({x:0,y:0,width:800,height:600})});
  assert.equal(await painter.repair(view),false);
  const f=fixture();const other=createNativeViewPainter({delay:1,canPaint:()=>true,bounds:()=>f.target,settled:()=>{throw Error('fixture UI error');}});
  assert.equal(await other.repair(f.view),true);
});

test('abort reentrantly triggered by native setBounds restores the same surface',async()=>{
  const controller=new AbortController();const calls=[];const target={x:0,y:0,width:800,height:600};
  const view={webContents:{isDestroyed:()=>false},setBounds(value){calls.push({...value});if(calls.length===1)controller.abort();}};
  const painter=createNativeViewPainter({canPaint:()=>true,bounds:()=>target,delay:1});
  assert.equal(await painter.repair(view,{signal:controller.signal}),false);assert.deepEqual(calls.at(-1),target);
});

test('layout shrinking to one pixel still restores the actual current bounds',async()=>{
  let target={x:0,y:0,width:800,height:600};const calls=[];
  const view={webContents:{isDestroyed:()=>false},setBounds(value){calls.push({...value});}};
  const painter=createNativeViewPainter({canPaint:()=>true,bounds:()=>target,delay:5});
  const pending=painter.repair(view);target={x:0,y:0,width:1,height:1};
  assert.equal(await pending,true);assert.deepEqual(calls.at(-1),target);
});
