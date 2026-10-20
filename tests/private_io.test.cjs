'use strict';
// Delayed syscall and Electron event mocks only; no platform, backend or profile.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {createPrivateIO}=require('../desktop/private-io.cjs');
const health=require('../desktop/backend-health.cjs');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function memoryIO(){
  const files=new Map(),ops=[],hooks={},handles=[];
  async function before(kind,name){ops.push([kind,name]);if(hooks[kind])await hooks[kind](name);}
  const io={
    mkdir:async p=>{await before('mkdir',p);},
    writeFile:async(p,value,options)=>{await before('write',p);files.set(p,value);assert.equal(options.mode,0o600);},
    chmod:async(p,mode)=>{await before('chmod',p);assert.equal(mode,0o600);},
    rename:async(a,b)=>{await before('rename',b);files.set(b,files.get(a));files.delete(a);},
    unlink:async p=>{await before('unlink',p);files.delete(p);},
    readFile:async p=>{await before('read',p);if(!files.has(p))throw Object.assign(new Error('missing'),{code:'ENOENT'});return files.get(p);},
    open:async(p,flag,mode)=>{await before('open',p);assert.equal(flag,'a');assert.equal(mode,0o600);
      const h={fd:99+handles.length,closes:0,async close(){this.closes++;}};handles.push(h);return h;},
  };
  return {io,files,ops,hooks,handles};
}
function privateFixture(memory=memoryIO(),extra={}){
  const notices=[],errors=[];
  const priv=createPrivateIO({dir:'/isolated/private',io:memory.io,pid:123,timeoutMs:25,
    onWait:x=>notices.push(x),onStatusError:e=>errors.push(e),...extra});
  return {...memory,priv,notices,errors};
}
test('normal completed IO never flashes a permission wait notice',async()=>{
  const h=privateFixture();const handle=await h.priv.openLog();await h.priv.close(handle);
  await h.priv.descriptor({pid:123});await h.priv.session({checked_at:1});
  h.priv.status({version:1});await h.priv.idle();assert.deepEqual(h.notices,[]);assert.deepEqual(h.errors,[]);
});

test('pending directory access keeps the event loop responsive and merges status to the latest snapshot',async()=>{
  const h=privateFixture(),gate=deferred();h.hooks.mkdir=()=>gate.promise;
  h.priv.status({version:1});await pause(2);
  for(let version=2;version<=50;version++)h.priv.status({version});
  let heartbeat=false;await new Promise(r=>setImmediate(()=>{heartbeat=true;r();}));
  assert.equal(heartbeat,true);await pause(30);assert.deepEqual(h.notices,['status']);
  assert.equal(h.ops.filter(x=>x[0]==='write').length,0);
  gate.resolve();await h.priv.idle();
  assert.deepEqual(JSON.parse(h.files.get('/isolated/private/embedded-app-status.json')),{version:50});
  assert.equal(h.ops.filter(x=>x[0]==='write').length,1);assert.equal(h.errors.length,0);
});
test('status writes are serial under delayed write and recover from errors without recursive writes',async()=>{
  const h=privateFixture(),gate=deferred();let active=0,max=0,first=true;
  h.hooks.write=async()=>{active++;max=Math.max(max,active);if(first){first=false;await gate.promise;}active--;};
  h.priv.status({version:1});await pause(2);h.priv.status({version:2});h.priv.status({version:3});
  gate.resolve();await h.priv.idle();assert.equal(max,1);
  assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-app-status.json')).version,3);
  h.hooks.write=async()=>{throw Object.assign(new Error('/private/path'),{code:'EACCES'});};
  h.priv.status({version:4});await h.priv.idle();assert.equal(h.errors.length,1);
  assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-app-status.json')).version,3);
  delete h.hooks.write;h.priv.status({version:5});await h.priv.idle();
  assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-app-status.json')).version,5);
});
test('a status arriving at each drain completion microtask position is never stranded',async()=>{
  for(let hops=0;hops<10;hops++){
    const h=privateFixture();let once=true;
    h.hooks.unlink=()=>{if(!once)return;once=false;
      let left=hops;const enqueue=()=>left--?queueMicrotask(enqueue):h.priv.status({version:2});queueMicrotask(enqueue);};
    h.priv.status({version:1});await new Promise(r=>setImmediate(r));await h.priv.idle();
    assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-app-status.json')).version,2,'microtask offset '+hops);
  }
});
test('log open timeout closes its late FD once and permits one explicit safe backend retry',async()=>{
  const h=privateFixture(),gate=deferred();let spawns=0;h.hooks.open=()=>gate.promise;
  const manager=health.createBackendManager({baseURL:'unused',port:1,timeoutMs:200,retryDelayMs:1,
    probe:async()=>({status:spawns?'compatible':'unavailable'}),portOpen:async()=>false,
    start:async()=>{const fd=await h.priv.openLog();try{spawns++;}finally{await h.priv.close(fd);}}});
  await assert.rejects(manager.ensureReady(),{code:'PRIVATE_DATA_TIMEOUT'});assert.equal(spawns,0);
  gate.resolve();await pause(3);assert.equal(h.handles[0].closes,1);assert.equal(spawns,0);
  delete h.hooks.open;await manager.ensureReady();assert.equal(spawns,1);assert.equal(h.handles[1].closes,1);
  await manager.ensureReady();assert.equal(spawns,1);
});
test('a timed-out directory creation never starts a later log open, and failed opens never spawn',async()=>{
  const h=privateFixture(),gate=deferred();h.hooks.mkdir=()=>gate.promise;
  await assert.rejects(h.priv.openLog(),e=>e.code==='PRIVATE_DATA_TIMEOUT'&&e.backendNotStarted===true);
  gate.resolve();await pause(3);assert.equal(h.ops.some(x=>x[0]==='open'),false);
  h.hooks.open=async()=>{throw Object.assign(new Error('fixture denied'),{code:'EPERM'});};
  await assert.rejects(h.priv.openLog(),e=>e.code==='PRIVATE_DATA_UNAVAILABLE'&&e.backendNotStarted===true);
});
test('descriptor is not ready before its atomic commit and a failure retains no new descriptor',async()=>{
  const h=privateFixture(),gate=deferred();h.hooks.write=()=>gate.promise;
  let ready=false;const pending=h.priv.descriptor({pid:123,token:'fixture'}).then(()=>{ready=true;});
  await pause(2);assert.equal(ready,false);assert.equal(h.files.has('/isolated/private/embedded-browser.json'),false);
  gate.resolve();await pending;assert.equal(ready,true);
  h.hooks.write=async()=>{throw Object.assign(new Error('/secret/private/path'),{code:'EACCES'});};
  await assert.rejects(h.priv.descriptor({pid:123,token:'other'}),e=>e.code==='PRIVATE_DATA_UNAVAILABLE'&&!e.message.includes('/secret'));
  assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-browser.json')).token,'fixture');
});
test('a late descriptor rename is removed before another request commits and cannot overwrite the new owner',async()=>{
  const h=privateFixture(),gate=deferred();let calls=0;
  h.hooks.rename=async()=>{if(++calls===1)await gate.promise;};
  await assert.rejects(h.priv.descriptor({pid:123,token:'old'}),{code:'PRIVATE_DATA_TIMEOUT'});
  const newer=h.priv.descriptor({pid:123,token:'new'});await pause(2);
  gate.resolve();await newer;
  assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-browser.json')).token,'new');
  await h.priv.removeDescriptor();assert.equal(h.files.has('/isolated/private/embedded-browser.json'),false);
});
test('stopping pending IO prevents file publication and closes a late FD without any startup side effect',async()=>{
  const h=privateFixture(),gate=deferred();h.hooks.open=()=>gate.promise;
  const pending=h.priv.openLog();await pause(2);h.priv.stop();gate.resolve();
  await assert.rejects(pending,{code:'PRIVATE_IO_CANCELLED'});assert.equal(h.handles[0].closes,1);
  const other=privateFixture(),directory=deferred();other.hooks.mkdir=()=>directory.promise;
  other.priv.status({pid:123});other.priv.stop();directory.resolve();await other.priv.idle();
  assert.equal(other.ops.some(x=>x[0]==='write'),false);
});
test('a session whose account scope changes during IO cannot publish that observation',async()=>{
  const h=privateFixture(),gate=deferred();let current=true;h.hooks.write=()=>gate.promise;
  const pending=h.priv.session({account:'synthetic'},()=>current);await pause(2);current=false;gate.resolve();
  await assert.rejects(pending,{code:'PRIVATE_IO_CANCELLED'});
  assert.equal(h.files.has('/isolated/private/embedded-session-status.json'),false);
});
test('a session rename already in flight is cleaned if its account scope expires before completion',async()=>{
  const h=privateFixture(),gate=deferred();let current=true;h.hooks.rename=()=>gate.promise;
  const pending=h.priv.session({account:'synthetic'},()=>current);await pause(2);current=false;gate.resolve();
  await assert.rejects(pending,{code:'PRIVATE_IO_CANCELLED'});
  assert.equal(h.files.has('/isolated/private/embedded-session-status.json'),false);
});
test('descriptor cleanup never deletes another process descriptor',async()=>{
  const h=privateFixture();await h.priv.descriptor({pid:123,token:'own'});
  h.files.set('/isolated/private/embedded-browser.json',JSON.stringify({pid:321,token:'foreign'}));
  await h.priv.removeDescriptor();assert.equal(JSON.parse(h.files.get('/isolated/private/embedded-browser.json')).pid,321);
});
test('real temporary-file FD inheritance survives asynchronous parent handle close and atomic files retain private modes',async()=>{
  const dir=await fs.promises.mkdtemp(path.join(os.tmpdir(),'publish-private-io-'));
  const priv=createPrivateIO({dir,io:fs.promises,pid:process.pid});
  try{
    const handle=await priv.openLog();
    const child=spawn(process.execPath,['-e','setTimeout(()=>process.stdout.write("fixture-output"),10)'],{stdio:['ignore',handle.fd,handle.fd]});
    const done=new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',code=>code===0?resolve():reject(Error('child '+code)));});
    await priv.close(handle);await done;
    assert.equal(await fs.promises.readFile(path.join(dir,'workbench.log'),'utf8'),'fixture-output');
    priv.status({version:1});priv.status({version:2});await priv.idle();
    await priv.descriptor({pid:process.pid,token:'synthetic'});await priv.session({checked_at:1});
    for(const name of ['embedded-app-status.json','embedded-browser.json','embedded-session-status.json'])
      assert.equal((await fs.promises.stat(path.join(dir,name))).mode&0o777,0o600);
    await priv.removeDescriptor();assert.equal(fs.existsSync(path.join(dir,'embedded-browser.json')),false);
  }finally{priv.stop();await fs.promises.rm(dir,{recursive:true,force:true});}
});

function desktop(memory){
  const root=path.resolve(__dirname,'..'),events=[],observed={spawns:0,loads:0,closed:0};
  class WC extends EventEmitter{
    constructor(){super();this.mainFrame={};this.url='';}
    isDestroyed(){return false;}isLoading(){return false;}getURL(){return this.url;}
    setWindowOpenHandler(){}send(_name,state){observed.state=state;}
    async loadURL(url){this.url=url;observed.loads++;}stop(){}executeJavaScript(){return Promise.resolve();}
    focus(){}getBackgroundThrottling(){return false;}
  }
  class View{constructor(){this.webContents=new WC();this.visible=false;}setVisible(x){this.visible=x;}getVisible(){return this.visible;}
    setBounds(b){this.bounds=b;}getBounds(){return this.bounds;}}
  class Window extends EventEmitter{
    constructor(){super();events.push('window');this.webContents=new WC();this.contentView={children:[],addChildView:v=>this.contentView.children.push(v),removeChildView:v=>{this.contentView.children=this.contentView.children.filter(x=>x!==v);}};}
    isDestroyed(){return false;}isVisible(){return true;}isFocused(){return false;}isMinimized(){return false;}getContentSize(){return [1320,900];}
    async loadFile(){events.push('shell');}show(){}focus(){}
  }
  const app={isPackaged:false,getVersion:()=> 'isolated-test',getPath:()=>'/isolated-profile',setPath(){},setName(){},setAboutPanelOptions(){},
    commandLine:{appendSwitch(){}},requestSingleInstanceLock:()=>false,quit(){},on(){}};
  const fakeHttp={createServer:()=>Object.assign(new EventEmitter(),{listening:false,
    listen(_p,_h,callback){this.listening=true;queueMicrotask(callback);},address:()=>({port:12346}),close(){this.listening=false;observed.closed++;}})};
  const sandbox={require:name=>{
    if(name==='electron')return {app,BrowserWindow:Window,WebContentsView:View,ipcMain:{handle(){}},Menu:{buildFromTemplate:x=>x,setApplicationMenu(){}}};
    if(name==='node:fs')return {...fs,mkdirSync(){},promises:memory.io};
    if(name==='node:http')return fakeHttp;
    if(name==='node:child_process')return {spawn:()=>{observed.spawns++;const c=new EventEmitter();c.unref=()=>{};queueMicrotask(()=>c.emit('spawn'));return c;}};
    if(name===path.join(root,'desktop/private-io.cjs'))return {createPrivateIO:o=>createPrivateIO({...o,timeoutMs:memory.timeoutMs || 25,onWait:value=>{o.onWait(value);memory.waiting?.resolve();}})};
    if(name===path.join(root,'desktop/backend-health.cjs'))return {...health,
      createBackendManager:o=>({ensureReady:()=>o.start()}),waitForDebuggingEndpoint:async()=> 'ws://127.0.0.1:12345/devtools/browser/fixture'};
    if(name===path.join(root,'desktop/native-view-paint.cjs'))return {createNativeViewPainter:()=>({queue(){},cancel(){},stop(){}})};
    return require(name);
  },__dirname:path.join(root,'desktop'),process:{env:{},pid:123,exit(){throw Error('Unexpected exit');}},
    console,AbortSignal,fetch:async()=>({ok:true,json:async()=>({available:true,job:null})}),setTimeout,clearTimeout,
    setInterval:()=>({unref(){}}),clearInterval};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(root,'desktop/main.cjs'),'utf8')+
    '\nglobalThis.api={boot,connectWorkbench,state:shellState,stop:()=>{quitting=true;privateIO.stop();}};',sandbox);
  return {api:sandbox.api,events,observed};
}
test('actual desktop boot renders its shell before pending private mkdir, stays callable, and never starts late backend',async()=>{
  const mem=memoryIO(),gate=deferred();mem.hooks.mkdir=()=>gate.promise;
  mem.timeoutMs=200;mem.waiting=deferred();
  const h=desktop(mem),pending=h.api.boot();await mem.waiting.promise;
  assert.deepEqual(h.events,['window','shell']);assert.equal(h.api.state().connection.status,'connecting');
  assert.equal(h.observed.spawns,0);assert.equal(h.observed.loads,0);
  assert.match(h.observed.state.notice,/资料目录.*重新连接/);
  await assert.rejects(pending,{code:'PRIVATE_DATA_TIMEOUT'});
  assert.equal(h.api.state().connection.status,'disconnected');assert.match(h.api.state().notice,/访问超时.*重新连接/);
  gate.resolve();await pause(3);assert.equal(h.observed.spawns,0);
  await h.api.connectWorkbench();assert.equal(h.api.state().connection.status,'ready');assert.equal(h.observed.spawns,1);
  assert.equal(h.observed.loads,1);assert.equal(mem.handles[0].closes,1);h.api.stop();
});
test('actual desktop cannot load dashboard or become ready before descriptor, and a late failed write cannot revive bridge',async()=>{
  const mem=memoryIO(),gate=deferred();mem.hooks.write=p=>p.includes('embedded-browser.json')?gate.promise:undefined;
  const h=desktop(mem),pending=h.api.boot();await pause(3);
  assert.equal(h.observed.spawns,1);assert.equal(h.observed.loads,0);assert.equal(h.api.state().connection.status,'connecting');
  await assert.rejects(pending,{code:'PRIVATE_DATA_TIMEOUT'});assert.equal(h.observed.closed,1);
  gate.resolve();await pause(3);assert.equal(h.observed.loads,0);assert.equal(h.api.state().connection.status,'disconnected');
  assert.equal([...mem.files.keys()].some(p=>p.endsWith('embedded-browser.json')),false);h.api.stop();
});
