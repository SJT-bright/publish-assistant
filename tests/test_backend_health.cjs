'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const net = require('node:net');
const health = require('../desktop/backend-health.cjs');

const compatible = {app:'sau-draft-workbench', api_version:2, embedded_supported:true};
const response = data => ({ok:true, json:async () => data});
function managerFixture({states = ['unavailable'], ports = [true], timeoutMs = 1000, start} = {}) {
  let elapsed = 0, probes = 0, socketChecks = 0, starts = 0;
  const next = (items, index) => items[Math.min(index, items.length - 1)];
  const manager = health.createBackendManager({
    timeoutMs, retryDelayMs:100, now:() => elapsed, sleep:async ms => {elapsed += ms;},
    probe:async () => ({status:next(states, probes++)}),
    portOpen:async () => next(ports, socketChecks++),
    start:async () => {starts++; if (start) await start();},
  });
  return {manager, counts:() => ({elapsed, probes, socketChecks, starts})};
}

test('only explicit old workbench contracts are incompatible; incomplete responses are unknown', () => {
  assert.equal(health.classifyBackendState(compatible), 'compatible');
  assert.equal(health.classifyBackendState({...compatible, api_version:1}), 'incompatible');
  assert.equal(health.classifyBackendState({...compatible, embedded_supported:false}), 'incompatible');
  assert.equal(health.classifyBackendState({app:'another-service'}), 'occupied');
  for (const data of [null, [], {}, {app:compatible.app}, {...compatible, api_version:'2'}]) {
    assert.equal(health.classifyBackendState(data), 'unknown');
  }
});

test('busy backend recovers without spawning or reporting an update', async () => {
  const fixture = managerFixture({states:['busy','unavailable','compatible']});
  await fixture.manager.ensureReady();
  assert.deepEqual(fixture.counts(), {elapsed:200, probes:3, socketChecks:2, starts:0});
});

test('an explicitly incompatible backend fails immediately and never spawns', async () => {
  const fixture = managerFixture({states:['incompatible']});
  await assert.rejects(fixture.manager.ensureReady(), {code:'BACKEND_INCOMPATIBLE'});
  assert.deepEqual(fixture.counts(), {elapsed:0, probes:1, socketChecks:0, starts:0});
});

test('a recognized foreign service is reported separately and is preserved', async () => {
  const fixture = managerFixture({states:['occupied']});
  await assert.rejects(fixture.manager.ensureReady(), {code:'BACKEND_PORT_OCCUPIED'});
  assert.equal(fixture.counts().starts, 0);
});

test('unreachable or unknown occupied endpoints have a bounded busy result and no spawn', async () => {
  for (const port of [true, null]) {
    const fixture = managerFixture({ports:[port]});
    const error = await fixture.manager.ensureReady().catch(value => value);
    assert.equal(error.code, 'BACKEND_BUSY');
    assert.doesNotMatch(error.message, /更新|旧版/);
    assert.deepEqual(fixture.counts(), {elapsed:1000, probes:10, socketChecks:10, starts:0});
  }
});

test('parallel requests share one connection and a refused port permits one spawn', async () => {
  const fixture = managerFixture({states:['unavailable','unavailable','compatible'], ports:[false]});
  const first = fixture.manager.ensureReady(), second = fixture.manager.ensureReady();
  assert.equal(first, second);
  await Promise.all([first, second]);
  await fixture.manager.ensureReady();
  assert.equal(fixture.counts().starts, 1);
});

test('startup timeout and subsequent retry do not spawn a second process', async () => {
  const fixture = managerFixture({ports:[false]});
  await assert.rejects(fixture.manager.ensureReady(), {code:'BACKEND_START_TIMEOUT'});
  await assert.rejects(fixture.manager.ensureReady(), {code:'BACKEND_START_TIMEOUT'});
  assert.equal(fixture.counts().starts, 1);
});

test('spawn errors are handled and do not trigger repeated process creation', async () => {
  const fixture = managerFixture({ports:[false], start:async () => {throw new Error('ENOENT');}});
  await assert.rejects(fixture.manager.ensureReady(), {code:'BACKEND_START_FAILED'});
  await assert.rejects(fixture.manager.ensureReady(), {code:'BACKEND_START_TIMEOUT'});
  assert.equal(fixture.counts().starts, 1);
});

test('quitting during a probe prevents spawning', async () => {
  let running = true, starts = 0;
  const manager = health.createBackendManager({probe:async () => {running = false; return {status:'unavailable'};},
    portOpen:async () => false, start:async () => {starts++;}, shouldContinue:() => running});
  await assert.rejects(manager.ensureReady(), {code:'CONNECTION_CLOSED'});
  assert.equal(starts, 0);
});

test('real isolated loopback socket probes detect a listener and an explicit refusal', async () => {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {assert.equal(await health.probePort(port, 300), true);}
  finally {await new Promise(resolve => server.close(resolve));}
  assert.equal(await health.probePort(port, 300), false);
});

test('backend request bounds include response parsing, and transient HTTP statuses remain busy', async () => {
  for (const status of [408,429,500,502,503,504]) {
    assert.deepEqual(await health.probeBackend('http://127.0.0.1:1', 30,
      async () => ({ok:false, status})), {status:'busy'});
  }
  const before = Date.now();
  const result = await health.probeBackend('http://127.0.0.1:1', 30,
    async () => ({ok:true, json:() => new Promise(() => {})}));
  assert.equal(result.status, 'unavailable');
  assert.ok(Date.now() - before < 1000);
});

const activePort = '12345\n/devtools/browser/current-id\n';
const endpoint = 'ws://127.0.0.1:12345/devtools/browser/current-id';
test('DevTools endpoints must match loopback host, port, and the exact browser resource', () => {
  const expected = health.parseDevToolsActivePort(activePort);
  assert.deepEqual(expected, {port:12345, resource:'/devtools/browser/current-id'});
  assert.equal(health.validateDevToolsEndpoint(endpoint, expected), true);
  for (const value of [endpoint.replace('12345','54321'), endpoint.replace('127.0.0.1','localhost'),
    endpoint.replace('/devtools/browser/', '/other/devtools/browser/'), endpoint.replace('current-id','old-id'),
    endpoint.replace('ws:', 'wss:'), endpoint + '?stale=1', endpoint + '#suffix', endpoint.replace('ws://','ws://user@')]) {
    assert.equal(health.validateDevToolsEndpoint(value, expected), false, value);
  }
  for (const value of ['', '0\n/devtools/browser/id', '65536\n/devtools/browser/id',
    '12345\n', 'NaN\n/devtools/browser/id', '12345\n/devtools/page/id']) {
    assert.equal(health.parseDevToolsActivePort(value), null);
  }
});

test('a hung debugging endpoint is bounded even when fetch ignores abort', async () => {
  let observedSignal;
  const before = Date.now();
  await assert.rejects(health.waitForDebuggingEndpoint({readActivePort:() => activePort,
    timeoutMs:65, requestTimeoutMs:20, retryDelayMs:5,
    fetchImpl:(_url, options) => {observedSignal = options.signal; return new Promise(() => {});}}),
  {code:'DEVTOOLS_TIMEOUT'});
  assert.ok(Date.now() - before < 1000);
  assert.equal(observedSignal.aborted, true);
});

test('a stale resource is rejected and a refreshed descriptor can recover', async () => {
  let attempts = 0, elapsed = 0;
  const result = await health.waitForDebuggingEndpoint({readActivePort:() => activePort,
    timeoutMs:100, now:() => elapsed, sleep:async ms => {elapsed += ms;}, retryDelayMs:10,
    fetchImpl:async () => response({webSocketDebuggerUrl:attempts++ === 0 ? endpoint.replace('current-id','old-id') : endpoint})});
  assert.equal(result, endpoint);
  assert.equal(attempts, 2);
});

test('a descriptor changed during the request cannot validate the old response', async () => {
  let current = activePort, elapsed = 0;
  await assert.rejects(health.waitForDebuggingEndpoint({readActivePort:() => current,
    timeoutMs:30, now:() => elapsed, sleep:async ms => {elapsed += ms;}, retryDelayMs:10,
    fetchImpl:async () => {current = activePort.replace('current-id','new-id'); return response({webSocketDebuggerUrl:endpoint});}}),
  {code:'DEVTOOLS_TIMEOUT'});
});

test('automatic recovery retries only transient failures, at most twice', async () => {
  let attempts = 0;
  await health.recoverConnection(async () => {if (++attempts === 1) throw Object.assign(new Error('busy'), {code:'BACKEND_BUSY'});},
    {sleep:async () => {}});
  assert.equal(attempts, 2);
  attempts = 0;
  await assert.rejects(health.recoverConnection(async () => {attempts++; throw Object.assign(new Error('old'), {code:'BACKEND_INCOMPATIBLE'});},
    {sleep:async () => {}}), {code:'BACKEND_INCOMPATIBLE'});
  assert.equal(attempts, 1);
});

// Electron event mocks: no app launch, profile writes, real backend, or jobs.
function desktopFixture({backend, loads, flush} = {}) {
  const root = path.resolve(__dirname, '..'), handlers = new Map();
  const observed = {backends:0, bridges:0, loads:0, timers:0, descriptors:0, stops:0};
  const app = {isPackaged:false, getVersion:() => 'isolated-test', getPath:() => '/isolated-connection-profile', setPath:() => {}, setName:() => {},
    setAboutPanelOptions:() => {}, commandLine:{appendSwitch:() => {}}, requestSingleInstanceLock:() => false,
    quit:() => {}, on:() => {}};
  const fakeFs = {...fs, mkdirSync:() => {}, promises:{mkdir:async()=>{}, writeFile:async()=>{observed.descriptors++;}, chmod:async()=>{}, rename:async()=>{}, unlink:async()=>{}}, writeFileSync:() => {observed.descriptors++;}};
  const fakeHttp = {createServer:() => {
    observed.bridges++;
    const server = new EventEmitter();
    Object.assign(server, {listening:false, listen(_port,_host,callback) {this.listening = true; queueMicrotask(callback);},
      address:() => ({port:12346}), close() {this.listening = false;}});
    return server;
  }};
  const ipcMain = {handle:(name,handler) => handlers.set(name,handler)};
  const wc = {mainFrame:{}, isDestroyed:() => false, loadURL:async () => {
    observed.loads++; if (loads) await loads(observed.loads);
  }, executeJavaScript:async code=>{
    assert.equal(code,'window.flushWorkbenchEditor?.()');observed.flushes=(observed.flushes||0)+1;
    if(flush)await flush();
  }, stop:() => {observed.stops++;}, getURL:() => '', isLoading:() => false};
  const sandbox = {require:name => name === 'electron' ? {app, ipcMain} : name === 'node:fs' ? fakeFs :
    name === 'node:http' ? fakeHttp : name === path.join(root,'desktop/backend-health.cjs') ?
      {...health, recoverConnection:(connect,options) => health.recoverConnection(connect,{...options,sleep:async () => {}})} : require(name),
    __dirname:path.join(root,'desktop'), process:{env:{},pid:100,exit:() => {throw new Error('Unexpected exit');}},
    console, setTimeout, clearTimeout, setInterval:() => {observed.timers++; return {unref:() => {}};}, clearInterval,
    fixtureWC:wc, fixtureObserved:observed, fixtureBackend:backend, fixtureEndpoint:endpoint};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(root,'desktop/main.cjs'),'utf8') + `
    win={webContents:{}};dashboard={webContents:fixtureWC};
    update=()=>{};showView=()=>{};monitorSessions=async()=>{};monitorProgress=async()=>{};
    startBackend=async()=>{fixtureObserved.backends++;if(fixtureBackend)await fixtureBackend(fixtureObserved.backends);};
    debuggingEndpoint=async()=>fixtureEndpoint;
    configureIPC();
    globalThis.fixtureAPI={connect:connectWorkbench,state:shellState,
      disconnect:()=>{connectionStatus='disconnected';},lease:value=>{activeLease=value;},
      task:value=>{taskProgress=value;},selected:id=>{selected=id;}};
  `, sandbox, {filename:'desktop/main.cjs'});
  return {api:sandbox.fixtureAPI, observed,
    reconnect:() => handlers.get('browser-reconnect')({sender:wc, senderFrame:wc.mainFrame})};
}

test('desktop concurrent startup preserves singletons and explicit refresh refuses an active creator task', async () => {
  const fixture = desktopFixture();
  const first = fixture.api.connect(), second = fixture.api.connect();
  assert.equal(first, second);
  assert.equal(fixture.api.state().connection.status, 'connecting');
  await Promise.all([first, second]);
  assert.equal(fixture.api.state().connection.status, 'ready');
  fixture.api.selected('tencent');
  fixture.api.lease({platform:'tencent', action:'draft'});
  const before = {...fixture.observed};
  await assert.rejects(fixture.reconnect(),/当前任务正在使用工作台/);
  assert.deepEqual(fixture.observed, before);
  assert.deepEqual(before, {backends:1, bridges:1, loads:1, timers:2, descriptors:1, stops:0});
});

test('explicit ready refresh flushes and reloads only the dashboard, retaining selection and monitor singletons',async()=>{
  const fixture=desktopFixture();await fixture.api.connect();fixture.api.selected('tencent');
  await fixture.api.connect();assert.equal(fixture.observed.loads,1);
  assert.equal((await fixture.reconnect()).connection.status,'ready');
  assert.equal(fixture.api.state().selected,'tencent');
  assert.deepEqual(fixture.observed,{backends:2,bridges:1,loads:2,timers:2,descriptors:1,stops:0,flushes:1});
});

test('failed editor persistence prevents an explicit refresh from replacing the current document',async()=>{
  const fixture=desktopFixture({flush:async()=>{throw new Error('fixture editor save failed');}});await fixture.api.connect();
  await assert.rejects(fixture.reconnect(),/fixture editor save failed/);
  assert.equal(fixture.observed.loads,1);assert.equal(fixture.observed.flushes,1);
  assert.equal(fixture.observed.bridges,1);assert.equal(fixture.observed.timers,2);
});

test('desktop failure can reconnect while preserving the bridge and monitor singleton', async () => {
  const fixture = desktopFixture({loads:async attempt => {
    if (attempt <= 2) throw Object.assign(new Error('refused'), {code:'ERR_CONNECTION_REFUSED'});
  }});
  await assert.rejects(fixture.api.connect(), {code:'ERR_CONNECTION_REFUSED'});
  assert.equal(fixture.api.state().connection.status, 'disconnected');
  assert.equal(fixture.api.state().connection.errorCode, 'ERR_CONNECTION_REFUSED');
  assert.equal((await fixture.reconnect()).connection.status, 'ready');
  assert.deepEqual(fixture.observed, {backends:3, bridges:1, loads:3, timers:2, descriptors:1, stops:0});
  fixture.api.disconnect();
  await fixture.reconnect();
  assert.equal(fixture.observed.bridges, 1);
  assert.equal(fixture.observed.timers, 2);
});

test('desktop reconnect never changes active leases or backend jobs', async () => {
  for (const kind of ['lease','job']) {
    const fixture = desktopFixture();
    fixture.api.disconnect();
    if (kind === 'lease') fixture.api.lease({platform:'tencent', action:'draft'});
    else fixture.api.task({job:{active:true}});
    await assert.rejects(fixture.reconnect(), /当前任务/);
    assert.equal(fixture.observed.backends, 0);
    assert.equal(fixture.observed.loads, 0);
    assert.equal(fixture.observed.bridges, 0);
  }
});

test('a job becoming active during backend recovery blocks bridge creation and page loading', async () => {
  const fixture = desktopFixture({backend:async () => {fixture.api.task({job:{active:true}});}});
  await assert.rejects(fixture.api.connect(), /当前任务/);
  assert.equal(fixture.api.state().taskProgress.job.active, true);
  assert.equal(fixture.api.state().connection.status, 'disconnected');
  assert.equal(fixture.observed.bridges, 0);
  assert.equal(fixture.observed.loads, 0);
});

test('desktop temporary busy recovery succeeds without rebuilding UI or starting duplicate monitors', async () => {
  const fixture = desktopFixture({backend:async attempt => {
    if (attempt === 1) throw Object.assign(new Error('busy'), {code:'BACKEND_BUSY'});
  }});
  await fixture.api.connect();
  assert.equal(fixture.api.state().connection.status, 'ready');
  assert.deepEqual(fixture.observed, {backends:2, bridges:1, loads:1, timers:2, descriptors:1, stops:0});
});
