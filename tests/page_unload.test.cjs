'use strict';
// Event mocks only: never boot Electron, open a platform, or mutate real state.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {EventEmitter}=require('node:events');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'desktop/main.cjs'),'utf8');

function harness(){
  let response=1,onPrompt=null,destroyed=false,viewDestroyed=false,visible=true;
  const prompts=[],writes=[];
  const window={isDestroyed:()=>destroyed};
  const wc=new EventEmitter();wc.ipc=new EventEmitter();
  Object.assign(wc,{isDestroyed:()=>viewDestroyed,getUserAgent:()=> 'Fixture Electron/44.5.1',setUserAgent:()=>{},setWindowOpenHandler:()=>{}});
  const view={webContents:wc,getVisible:()=>visible};
  const dialog={showMessageBoxSync:(parent,options)=>{
    prompts.push({parent,options});if(onPrompt)onPrompt();return response;
  }};
  const app={isPackaged:false,getPath:()=>'/isolated-unload-profile',setPath:()=>{},setName:()=>{},setAboutPanelOptions:()=>{},
    commandLine:{appendSwitch:()=>{}},requestSingleInstanceLock:()=>false,quit:()=>{},on:()=>{}};
  const fakeFs={...fs,mkdirSync:()=>{},writeFileSync:(...args)=>writes.push(args)};
  const sandbox={require:name=>name==='electron'?{app,dialog}:name==='node:fs'?fakeFs:require(name),
    __dirname:path.join(root,'desktop'),process:{env:{},pid:100,exit:()=>{throw new Error('Unexpected exit');}},console,
    setTimeout,clearTimeout,setInterval:()=>({unref:()=>{}}),clearInterval};
  vm.createContext(sandbox);
  vm.runInContext(source+`\nglobalThis.unloadFixture={
    attach:(window,view)=>{win=window;views.set('tencent',view);selected='tencent';configureRemote(view,'tencent');},
    lease:value=>{activeLease=value;},selected:id=>{selected=id;},quit:value=>{quitting=value;},
    popup:view=>{popupViews.push({view,platform:'tencent'});},window:value=>{win=value;}
  };`,sandbox,{filename:'desktop/main.cjs'});
  const api=sandbox.unloadFixture;api.attach(window,view);
  const emit=()=>{
    let permitted=0;wc.emit('will-prevent-unload',{preventDefault:()=>{permitted++;}});return permitted;
  };
  return {api,wc,view,window,prompts,writes,emit,choice:value=>{response=value;},duringPrompt:fn=>{onPrompt=fn;},
    destroyWindow:()=>{destroyed=true;},destroyView:()=>{viewDestroyed=true;},hideView:()=>{visible=false;}};
}

test('confirmation only appears when the platform actually prevents unloading and defaults to preserving material',()=>{
  const h=harness();assert.equal(h.prompts.length,0);assert.equal(h.writes.length,0);
  assert.equal(h.wc.listenerCount('will-prevent-unload'),1);
  assert.equal(h.emit(),0);assert.equal(h.prompts.length,1);
  const {parent,options}=h.prompts[0];
  assert.equal(parent,h.window);
  assert.equal(options.message,'平台页面有未保存的素材，是否离开？');
  assert.deepEqual(Array.from(options.buttons),['继续离开','保留页面']);
  assert.equal(options.defaultId,1);assert.equal(options.cancelId,1);
});
test('only the explicit leave choice overrides beforeunload; stay and cancel do not',()=>{
  const h=harness();
  for(const choice of [1,-1,undefined,null,'0']){h.choice(choice);assert.equal(h.emit(),0);}
  h.choice(0);assert.equal(h.emit(),1);
});
test('an active draft lease blocks unloading without prompting, including a background platform',()=>{
  const h=harness();h.choice(0);h.api.lease({action:'draft',platform:'tencent'});
  assert.equal(h.emit(),0);assert.equal(h.prompts.length,0);
  h.api.selected('workbench');assert.equal(h.emit(),0);assert.equal(h.prompts.length,0);
});
test('hidden, replaced, quitting, closed, or absent native windows never display a prompt or permit leaving',()=>{
  const changes=[h=>h.hideView(),h=>h.api.selected('workbench'),h=>h.api.popup({webContents:{}}),
    h=>h.api.quit(true),h=>h.destroyWindow(),h=>h.destroyView(),h=>h.api.window(null)];
  for(const change of changes){const h=harness();h.choice(0);change(h);assert.equal(h.emit(),0);assert.equal(h.prompts.length,0);}
});
test('changes during the native modal cannot authorize a newly active task or a replaced/closed view',()=>{
  const changes=[h=>h.api.lease({action:'draft',platform:'tencent'}),h=>h.api.selected('workbench'),
    h=>h.destroyWindow(),h=>h.destroyView(),h=>h.hideView(),h=>h.api.quit(true)];
  for(const change of changes){const h=harness();h.choice(0);h.duringPrompt(()=>change(h));
    assert.equal(h.emit(),0);assert.equal(h.prompts.length,1);}
});
test('a native dialog failure preserves the platform instead of overriding beforeunload',()=>{
  const h=harness();h.choice(0);h.duringPrompt(()=>{throw new Error('Closed window');});
  assert.equal(h.emit(),0);assert.equal(h.prompts.length,1);
});
