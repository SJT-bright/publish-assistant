'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
function harness(){const handlers={},listeners={},notices=[];class Element{constructor(text,modal=false){this.innerText=text;this.modal=modal;}closest(){return this.modal?{}:null;}getAttribute(){return null;}}
 const sandbox={Element,require:()=>({ipcRenderer:{on:(name,fn)=>handlers[name]=fn,send:name=>notices.push(name)}}),document:{addEventListener:(name,fn)=>listeners[name]=fn}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../desktop/platform-preload.cjs'),'utf8'),sandbox);
 return {mode:value=>handlers['publication-mode']({},value),click:(text,modal=false)=>{let blocked=false;const target=new Element(text,modal);listeners.click({type:'click',composedPath:()=>[target],preventDefault:()=>blocked=true,stopImmediatePropagation(){}});return blocked;},notices};}
test('scoped permission permits one publish action and its exact native confirmation',()=>{const h=harness();assert.equal(h.click('发布'),true);
 h.mode({once:true,lease:'fixture'});assert.equal(h.click('发布'),false);assert.equal(h.click('发布'),true);
 assert.equal(h.click('确认发布'),true);assert.equal(h.click('确认发布',true),false);assert.equal(h.click('确认发布',true),true);
 h.mode({once:true,lease:'fixture'});assert.equal(h.click('发布'),true);h.mode(false);assert.equal(h.click('立即投稿'),true);
});
