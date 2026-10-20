'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync,spawn} = require('node:child_process');
const {prepareInstallation} = require('../desktop/update-installer.cjs');
function bundle(folder,version){
  const app=path.join(folder,'发布助手.app');fs.mkdirSync(path.join(app,'Contents/MacOS'),{recursive:true});
  execFileSync('/usr/bin/clang',['-arch',process.arch==='arm64'?'arm64':'x86_64','-x','c','-o',path.join(app,'Contents/MacOS/DraftWorkbench'),'-'],{input:'int main(void){return 0;}'});
  fs.writeFileSync(path.join(app,'Contents/Info.plist'),`<?xml version="1.0"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>local.sijunting.social-auto-upload.drafts</string><key>CFBundleExecutable</key><string>DraftWorkbench</string><key>CFBundleName</key><string>发布助手</string><key>CFBundlePackageType</key><string>APPL</string><key>CFBundleShortVersionString</key><string>${version}</string></dict></plist>`);
  execFileSync('/usr/bin/codesign',['--force','--sign','-',app],{stdio:'pipe'});return app;
}
test('native Mac ZIP stages, waits, swaps, retains rollback, and leaves user data intact',{skip:process.platform!=='darwin'},async()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),"publish updater's native test "));let prepared,child;
  try{
    const old=bundle(path.join(tmp,'installed'),'1.7.13'),next=bundle(path.join(tmp,'new'),'1.8.0');
    const data=path.join(tmp,'profile.json');fs.writeFileSync(data,'keep-login-and-materials');
    const archive=path.join(tmp,'update.zip');execFileSync('/usr/bin/ditto',['-c','-k','--keepParent',next,archive]);
    child=spawn('/bin/sleep',['120']);await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});
    prepared=await prepareInstallation({file:archive,version:'1.8.0',executable:path.join(old,'Contents/MacOS/DraftWorkbench'),cacheDir:tmp,pid:child.pid});
    await prepared.launch();await new Promise(resolve=>setTimeout(resolve,150));
    assert.match(fs.readFileSync(path.join(old,'Contents/Info.plist'),'utf8'),/1.7.13/);
    child.kill();await new Promise(resolve=>child.once('exit',resolve));
    const deadline=Date.now()+20000;let log='';
    while(Date.now()<deadline){try{log=fs.readFileSync(prepared.log,'utf8');}catch{}if(log.includes('installed'))break;await new Promise(resolve=>setTimeout(resolve,100));}
    assert.ok(log.includes('installed'),log);
    assert.match(fs.readFileSync(path.join(old,'Contents/Info.plist'),'utf8'),/1.8.0/);
    const backup=fs.readdirSync(path.dirname(old)).find(name=>name.startsWith('.发布助手-previous-'));
    assert.ok(backup);assert.match(fs.readFileSync(path.join(path.dirname(old),backup,'Contents/Info.plist'),'utf8'),/1.7.13/);
    assert.equal(fs.readFileSync(data,'utf8'),'keep-login-and-materials');
    execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',old],{stdio:'pipe'});
  }finally{child?.kill();fs.rmSync(tmp,{recursive:true,force:true});}
});
test('native Mac staging rejects a validly signed wrong-version bundle before replacing app',{skip:process.platform!=='darwin'},async()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'publish-updater-rejection-'));
  try{
    const old=bundle(path.join(tmp,'installed'),'1.7.13'),next=bundle(path.join(tmp,'new'),'1.8.0');
    const archive=path.join(tmp,'update.zip');execFileSync('/usr/bin/ditto',['-c','-k','--keepParent',next,archive]);
    await assert.rejects(prepareInstallation({file:archive,version:'1.9.0',executable:path.join(old,'Contents/MacOS/DraftWorkbench'),cacheDir:tmp}),/版本不符/);
    assert.match(fs.readFileSync(path.join(old,'Contents/Info.plist'),'utf8'),/1.7.13/);
    assert.deepEqual(fs.readdirSync(path.dirname(old)),['发布助手.app']);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
