'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {REPOSITORY_URL,releaseAsset,newerVersion,downloadPackage,createGitHubUpdater} = require('../desktop/github-updater.cjs');
const {validArchiveEntries,macInstallScript,windowsInstallScript} = require('../desktop/update-installer.cjs');
const body = Buffer.from('a complete package\n');
const sha = crypto.createHash('sha256').update(body).digest('hex');
function release(platform='darwin', arch='arm64',version='1.8.0') {
  const name=platform==='darwin'?`Publish-Assistant-${version}-Mac-${arch}.zip`:`Publish-Assistant-${version}-Windows-${arch}-Setup.exe`;
  return {tag_name:'v'+version,draft:false,prerelease:false,assets:[{name,state:'uploaded',digest:'sha256:'+sha,size:body.length,browser_download_url:`${REPOSITORY_URL}/releases/download/v${version}/${name}`}]};
}
async function temporary(fn) {const tmp=await fs.promises.mkdtemp(path.join(os.tmpdir(),'publish-update-test-'));try{await fn(tmp);}finally{await fs.promises.rm(tmp,{recursive:true,force:true});}}
test('numeric versions do not downgrade or compare lexically',()=>{
  assert.equal(newerVersion('1.10.0','1.9.9'),true);
  for(const v of ['1.7.13','1.7.12','1.6.1'])assert.equal(newerVersion(v,'1.7.13'),false);
  assert.throws(()=>newerVersion('1.8.0-beta','1.7.13'));
});
test('selects exact platform and CPU installer',()=>{
  for(const [platform,arch] of [['darwin','arm64'],['darwin','x64'],['win32','x64']])assert.ok(releaseAsset(release(platform,arch),'1.7.13',platform,arch));
  assert.throws(()=>releaseAsset(release(),'1.7.13','darwin','x64'),/完整安装包/);
});
test('older, draft, and pre-release versions never update',()=>{
  assert.equal(releaseAsset(release('darwin','arm64','1.6.1'),'1.7.13','darwin','arm64'),null);
  for(const flag of ['draft','prerelease'])assert.equal(releaseAsset({...release(),[flag]:true},'1.7.13','darwin','arm64'),null);
});
test('rejects missing checksum, incomplete asset, duplicate or foreign URL',()=>{
  for(const patch of [{digest:null},{size:0},{size:3*1024**3},{browser_download_url:'https://example.com/update.zip'},{state:'new'}]){
    const r=release();Object.assign(r.assets[0],patch);assert.throws(()=>releaseAsset(r,'1.7.13','darwin','arm64'));
  }
  const r=release();r.assets.push(r.assets[0]);assert.throws(()=>releaseAsset(r,'1.7.13','darwin','arm64'));
});
test('streams complete package, checks SHA and reuses only verified cache',async()=>temporary(async tmp=>{
  const asset=releaseAsset(release(),'1.7.13','darwin','arm64'),file=path.join(tmp,asset.name);let calls=0;
  const fetchImpl=async()=>{calls++;return new Response(body);};
  await downloadPackage(asset,file,{fetchImpl});assert.deepEqual(await fs.promises.readFile(file),body);
  await downloadPackage(asset,file,{fetchImpl});assert.equal(calls,1);
  await fs.promises.writeFile(file,Buffer.alloc(body.length));await downloadPackage(asset,file,{fetchImpl});assert.equal(calls,2);
}));
test('corrupt, truncated, oversized and network-failed packages cannot install',async()=>temporary(async tmp=>{
  const asset=releaseAsset(release(),'1.7.13','darwin','arm64'),file=path.join(tmp,asset.name);
  for(const payload of [Buffer.alloc(body.length),body.subarray(0,3),Buffer.concat([body,body])]){
    await assert.rejects(downloadPackage(asset,file,{fetchImpl:async()=>new Response(payload)}));
    assert.equal(fs.existsSync(file),false);assert.equal(fs.existsSync(file+'.part'),false);
  }
  await assert.rejects(downloadPackage(asset,file,{fetchImpl:async()=>{throw new Error('offline');}}));
  assert.equal(fs.existsSync(file+'.part'),false);
}));
test('one check and download serves concurrent callers and produces installable state',async()=>temporary(async tmp=>{
  let calls=0;const states=[];
  const updater=createGitHubUpdater({version:'1.7.13',platform:'darwin',arch:'arm64',cacheDir:tmp,onState:s=>states.push(s.status),fetchImpl:async url=>{calls++;return url.includes('/repos/')?Response.json(release()):new Response(body);}});
  await Promise.all([updater.check(),updater.check(),updater.check()]);
  assert.equal(calls,2);assert.equal(updater.state().status,'ready');assert.equal((await updater.downloaded()).asset.version,'1.8.0');
  assert.deepEqual(states.filter(s=>s!=='downloading'),['checking','ready']);
  await fs.promises.writeFile((await updater.downloaded()).file,'damaged');await assert.rejects(updater.downloaded());updater.stop();
}));
test('offline and rate limiting remain retryable without affecting app',async()=>temporary(async tmp=>{
  let calls=0;const updater=createGitHubUpdater({version:'1.7.13',cacheDir:tmp,fetchImpl:async()=>{calls++;return new Response('',{status:429});}});
  await updater.check();assert.equal(updater.state().status,'error');await updater.check();assert.equal(calls,2);await assert.rejects(updater.downloaded());updater.stop();
}));
test('older public GitHub Release leaves newer local app unchanged',async()=>temporary(async tmp=>{
  const updater=createGitHubUpdater({version:'1.7.13',cacheDir:tmp,fetchImpl:async()=>Response.json(release('darwin','arm64','1.6.1'))});
  await updater.check();assert.equal(updater.state().status,'current');await assert.rejects(updater.downloaded());updater.stop();
}));
test('development and isolated QA never contact GitHub',async()=>{
  let calls=0;const updater=createGitHubUpdater({version:'1.7.13',enabled:false,fetchImpl:async()=>{calls++;}});
  updater.start();await updater.check();assert.equal(calls,0);assert.equal(updater.state().status,'disabled');updater.stop();
});
test('scheduled checks retry automatically',async()=>temporary(async tmp=>{
  let calls=0;const updater=createGitHubUpdater({version:'1.7.13',cacheDir:tmp,intervalMs:10,fetchImpl:async()=>{calls++;return new Response('',{status:503});}});
  updater.start();await new Promise(resolve=>setTimeout(resolve,45));updater.stop();assert.ok(calls>=2);
}));
test('Mac archive rejects traversal, foreign roots, and Windows paths',()=>{
  assert.ok(validArchiveEntries('发布助手.app/\n发布助手.app/Contents/Info.plist\n__MACOSX/发布助手.app/._Contents\n'));
  for(const input of ['','../outside','/tmp/outside','发布助手.app/../../outside','Other.app/Contents','发布助手.app/..\\outside'])assert.equal(validArchiveEntries(input),false);
});
test('installer scripts quote paths and wait before replacing or opening installer',()=>{
  const script=macInstallScript({pid:123,backend:{pid:456,executable:"/App's/backend"},target:"/App's/发布助手.app",staged:'/tmp/new.app',backup:'/tmp/previous.app',log:'/tmp/update.log'});
  assert.ok(script.indexOf('while /bin/kill -0 123')<script.indexOf('/bin/mv'));
  assert.ok(script.includes('backend_identity_changed'));assert.ok(script.includes('rolled_back'));assert.ok(script.includes("'/App'\\''s/发布助手.app'"));
  assert.ok(!script.includes('Application Support'));assert.ok(!script.includes('workbench-state'));
  const win=windowsInstallScript({pid:123,backend:{pid:456,executable:"C:\\App's\\backend.exe"},file:"C:\\Downloads\\update.exe",log:'C:\\tmp\\update.log'});
  assert.ok(win.indexOf('Wait-Process -Id 123')<win.indexOf('Start-Process'));
  assert.ok(win.includes('ExecutablePath'));assert.ok(win.includes("C:\\App''s\\backend.exe"));
});
test('CPU validation reads Mach-O headers without requiring developer tools',async()=>temporary(async tmp=>{
  const {supportsArchitecture}=require('../desktop/update-installer.cjs');
  const thin=Buffer.alloc(32);thin.writeUInt32LE(0xfeedfacf);thin.writeUInt32LE(0x0100000c,4);
  const file=path.join(tmp,'binary');await fs.promises.writeFile(file,thin);
  assert.equal(await supportsArchitecture(file,'arm64'),true);assert.equal(await supportsArchitecture(file,'x64'),false);
  const fat=Buffer.alloc(48);fat.writeUInt32BE(0xcafebabe);fat.writeUInt32BE(2,4);fat.writeUInt32BE(0x01000007,8);fat.writeUInt32BE(0x0100000c,28);
  await fs.promises.writeFile(file,fat);assert.equal(await supportsArchitecture(file,'x64'),true);assert.equal(await supportsArchitecture(file,'arm64'),true);
  await fs.promises.writeFile(file,'broken');assert.equal(await supportsArchitecture(file,'arm64'),false);
}));
