"""Observe a Douyin editor's existing video SDK upload without starting it.

The observer is deliberately tied to the retained editor contract: one current
React class instance in the committed host tree, its existing video uploader,
and the native File selected by the current video input. It does not construct
an uploader or issue a platform request. Both wrapped methods return the exact
original value and preserve synchronous exceptions.
"""
import asyncio
import json
import math
import re
import secrets
import time


DY_UPLOAD_OBSERVER_SCRIPT = r"""(() => {
 'use strict';
 const previous=window.__sauDYUploadObserver;
 if(previous){
   if(previous.api_version===1&&typeof previous.configure==='function')return;
   if(previous.active||previous.timer||previous.pending?.size)return;
 }
 const nativeThen=Promise.prototype.then;
 const sliceNative=Blob.prototype.slice, arrayBufferNative=Blob.prototype.arrayBuffer;
 const sizeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
 const typeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get;
 const nameGetter=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
 const filesGetter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').get;
 const fileListLength=Object.getOwnPropertyDescriptor(FileList.prototype,'length').get;
 const fileListItem=FileList.prototype.item;
 const componentProtoKeys=['componentDidMount','componentDidUpdate','componentWillUnmount',
   'initUploader','initUploaderLibra','render'];
 const stateKeys=['file','metaInfo','uploadPercent','uploadProgressDetail','uploadStatus'];
 const uploaderProtoKeys=['_handleCompleteReject','_handleCompleteResolve','_intiEvent','_resetCompleteInfo',
   'cancel','offProcess','onProcess','pause','resume','resetVideoUploadConfig','setVideoUploadConfig',
   'updateUploadOption','uploadFile','uploadImgArr','uploadSrc'];
 const root={api_version:1,scope:null,active:null,timer:null,selectionListener:null,
   current:null,componentPatch:null,uploaderPatch:null,sequence:0,pending:new Set(),selection:null,
   metrics:{inspect_count:0,root_count:0,root_valid_count:0,nodes_visited:0,tree_overflow:false,
     candidate_count:0,component_wrapped:false,uploader_wrapped:false,outer_invoked_count:0,
     native_input_match_count:0,sdk_invoked_count:0,sdk_promise_count:0,sdk_fulfilled_count:0,
     sdk_rejected_count:0,outer_promise_count:0,outer_fulfilled_count:0,outer_rejected_count:0,
     result_file_match_count:0,sha_ready_count:0,sha_failed_count:0,report_count:0,
     report_accepted_count:0,invalidated_count:0,observer_error_count:0}};
 const videoAccept=/video\/|\.(?:mp4|mov|m4v|mkv|webm|avi|flv|mpeg|mpg)(?:[,;\s]|$)/i;
 function safeCall(fn){try{return fn()}catch(_){return null}}
 function freshId(){return String(++root.sequence)}
 function nativeFileInfo(file){
   try{
     const name=Reflect.apply(nameGetter,file,[]),size=Reflect.apply(sizeGetter,file,[]),type=Reflect.apply(typeGetter,file,[]);
     if(typeof name!=='string'||!name||!Number.isSafeInteger(size)||size<=0||typeof type!=='string')return null;
     return {name,size,type};
   }catch(_){return null}
 }
 function findNativeInput(file){
   const matches=[];
   for(const input of document.querySelectorAll('input[type="file"]')){
     if(!input.isConnected||!document.contains(input))continue;
     let files=null;try{files=input.files}catch(_){}
     if(!files)continue;
     for(let i=0;i<files.length;i++)if(files.item(i)===file)matches.push(input);
   }
   return matches.length===1?matches[0]:null;
 }
 function nativeListFile(value){try{
   return Reflect.apply(fileListLength,value,[])===1?Reflect.apply(fileListItem,value,[0]):null;
  }catch(_){return null}}
 function currentSelection(file,raw){
   const s=root.selection;
   if(!s||s.file!==file||s.scope!==root.scope||s.used||
      (root.selectionRevisions.get(s.input)||0)!==s.revision)return null;
   return raw===s.list||raw===s.file||(Array.isArray(raw)&&raw.length===1&&raw[0]===s.file)?s:null;
 }
 function exactKeys(value,keys){
   if(!value||typeof value!=='object')return false;
   const actual=Object.keys(value).sort(),expected=keys.slice().sort();
   return actual.length===expected.length&&actual.every((x,i)=>x===expected[i]);
 }
 function componentShape(instance){
   if(!instance||typeof instance!=='object'||typeof instance.uploadFile!=='function'||
      typeof instance.changeOption!=='function'||!exactKeys(instance.state,stateKeys))return false;
   const proto=Object.getPrototypeOf(instance);
   if(!proto)return false;
   const names=Object.getOwnPropertyNames(proto).filter(x=>x!=='constructor').sort();
   return names.length===componentProtoKeys.length&&names.every((x,i)=>x===componentProtoKeys.slice().sort()[i]);
 }
 function uploaderShape(uploader){
   if(!uploader||typeof uploader!=='object'||typeof uploader.uploadFile!=='function'||
      !uploader.byteVideoUploader||typeof uploader.byteVideoUploader!=='object'||
      !uploader.uploaderOptions||uploader.uploaderOptions.type!=='video')return false;
   const proto=Object.getPrototypeOf(uploader);
   if(!proto)return false;
   const names=Object.getOwnPropertyNames(proto).filter(x=>x!=='constructor').sort();
   const expected=uploaderProtoKeys.slice().sort();
   return names.length===expected.length&&names.every((x,i)=>x===expected[i]);
 }
 function locate(){
   root.metrics.inspect_count++;
   root.metrics.root_count=0;root.metrics.root_valid_count=0;root.metrics.nodes_visited=0;
   root.metrics.tree_overflow=false;root.metrics.candidate_count=0;
   if(window!==top||location.origin!=='https://creator.douyin.com'||
      !['/creator-micro/content/upload','/creator-micro/content/post/video'].includes(location.pathname))
     return {ok:false,reason:'route'};
   const containers=[...document.querySelectorAll('[id="root"]')];root.metrics.root_count=containers.length;
   const candidates=[],seenRoots=new Set();let invalidTree=false;
   for(const container of containers){
     const keys=Object.getOwnPropertyNames(container).filter(key=>key.startsWith('__reactContainer$'));
     for(const key of keys){
       const host=container[key],fiberRoot=host?.stateNode;
       if(!host||host.tag!==3||!fiberRoot||seenRoots.has(fiberRoot))continue;
       seenRoots.add(fiberRoot);
       const current=fiberRoot.current;
       if(!current||current.tag!==3||current.stateNode!==fiberRoot){invalidTree=true;continue;}
       root.metrics.root_valid_count++;
       const stack=[current],seen=new Set();
       while(stack.length){
         const node=stack.pop();if(!node||seen.has(node))continue;seen.add(node);
         root.metrics.nodes_visited++;
         if(root.metrics.nodes_visited>4096){root.metrics.tree_overflow=true;invalidTree=true;stack.length=0;break;}
         if(node.tag===1&&componentShape(node.stateNode)){
           const instance=node.stateNode;
           candidates.push({fiber:node,instance,uploader:instance.uploader||null,container});
         }
         if(node.sibling)stack.push(node.sibling);
         if(node.child)stack.push(node.child);
       }
     }
   }
   root.metrics.candidate_count=candidates.length;
   if(invalidTree||root.metrics.tree_overflow||root.metrics.root_valid_count<1||candidates.length!==1)
     return {ok:false,reason:'tree',candidates};
   return {ok:true,...candidates[0]};
 }
 function restore(patch,key,object){
   if(!patch)return;
   try{if(object[key]===patch.wrapper){
     if(patch.descriptor)Object.defineProperty(object,key,patch.descriptor);else delete object[key];
   }}catch(_){}
 }
 function send(kind,report){
   try{
     const bridge=window[root.scope?.binding_name];
     if(typeof bridge?.[kind]!=='function')return Promise.resolve(false);
     return bridge[kind](report);
   }catch(_){return Promise.resolve(false);}
 }
 function track(promise){
   const p=Promise.resolve(promise);root.pending.add(p);
   Reflect.apply(nativeThen,p,[()=>root.pending.delete(p),()=>root.pending.delete(p)]);
   return p;
 }
 function scopeFields(){return {job_id:root.scope.job_id,receipt_nonce:root.scope.receipt_nonce,
   observer_nonce:root.scope.observer_nonce};}
 function invalidate(attempt){
   if(!attempt||attempt.invalid)return;
   attempt.invalid=true;root.metrics.invalidated_count++;
   if(root.active===attempt){
     track(send('invalidate',{v:1,...scopeFields(),invocation_id:attempt.invocation_id}));
   }
 }
 function selectionChanged(event){
   const input=event?.target;
   if(!input||input.tagName!=='INPUT'||input.type!=='file')return;
   root.selectionRevisions.set(input,(root.selectionRevisions.get(input)||0)+1);
   const attempt=root.active;
   let accept='';try{accept=String(input.accept||'')}catch(_){}
   const list=safeCall(()=>Reflect.apply(filesGetter,input,[]));
   const selected=nativeListFile(list);
   const selectedInfo=selected?nativeFileInfo(selected):null;
   const isVideo=input===attempt?.input||videoAccept.test(accept)||
     (typeof selected?.type==='string'&&selected.type.startsWith('video/'))||
     !!(selectedInfo&&videoAccept.test(selectedInfo.name));
   if(isVideo){
     invalidate(attempt);root.selection=null;
     const inputs=[...document.querySelectorAll('input[type="file"]')].filter(i=>
       i.isConnected&&i.ownerDocument===document&&videoAccept.test(String(i.accept||'')));
     const found=locate();
     if(root.scope&&window===top&&found.ok&&root.current?.instance===found.instance&&
        selectedInfo&&inputs.length===1&&inputs[0]===input&&
        document.contains(input)&&selectedInfo.name===root.scope.expected_name&&
        selectedInfo.size===root.scope.expected_size){
       root.selection={input,list,file:selected,scope:root.scope,
         revision:root.selectionRevisions.get(input)||0,used:false};
     }
   }
 }
 function sha256(file,attempt){
   const K=new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
   const h=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
   const w=new Uint32Array(64),R=(v,n)=>(v>>>n)|(v<<(32-n));
   function block(bytes,off){
     for(let i=0;i<16;i++){const p=off+i*4;w[i]=((bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3])>>>0;}
     for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(R(a,7)^R(a,18)^(a>>>3))+w[i-7]+(R(b,17)^R(b,19)^(b>>>10)))>>>0;}
     let [a,b,c,d,e,f,g,j]=h;
     for(let i=0;i<64;i++){const t1=(j+(R(e,6)^R(e,11)^R(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0;
       const t2=((R(a,2)^R(a,13)^R(a,22))+((a&b)^(a&c)^(b&c)))>>>0;j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
     [a,b,c,d,e,f,g,j].forEach((v,i)=>h[i]=(h[i]+v)>>>0);
   }
   return (async()=>{
     const size=Reflect.apply(sizeGetter,file,[]);let tail=new Uint8Array(0);
     for(let off=0;off<size;off+=1048576){
       if(root.active!==attempt||attempt.invalid)throw Error('obsolete observer');
       const chunk=Reflect.apply(sliceNative,file,[off,off+1048576]);
       const bytes=new Uint8Array(await Reflect.apply(arrayBufferNative,chunk,[]));
       const merged=new Uint8Array(tail.length+bytes.length);merged.set(tail);merged.set(bytes,tail.length);
       const whole=merged.length-(merged.length%64);for(let p=0;p<whole;p+=64)block(merged,p);
       tail=merged.slice(whole);await new Promise(resolve=>setTimeout(resolve,0));
     }
     const last=new Uint8Array(tail.length<56?64:128);last.set(tail);last[tail.length]=128;
     const view=new DataView(last.buffer);view.setUint32(last.length-8,Math.floor(size/536870912));
     view.setUint32(last.length-4,(size*8)>>>0);for(let p=0;p<last.length;p+=64)block(last,p);
     return Array.from(h,x=>x.toString(16).padStart(8,'0')).join('');
   })();
 }
 function stillScoped(attempt){return root.active===attempt&&!attempt.invalid&&
   root.scope?.observer_nonce===attempt.observer_nonce&&attempt.input&&
   root.selection===attempt.selection&&attempt.selection?.file===attempt.file&&
   (root.selectionRevisions.get(attempt.input)||0)===attempt.input_revision;}
 function maybeReport(attempt){
   if(!stillScoped(attempt)||!attempt.sdk_fulfilled||!attempt.digest||attempt.reported)return;
   const result=attempt.result,info=attempt.info,sha=attempt.digest;
   if(!info||sha!==root.scope.expected_sha256||info.size!==root.scope.expected_size||
      info.name!==root.scope.expected_name||result?.file!==attempt.file||
      typeof result.vid!=='string'||!/^[A-Za-z0-9_-]{1,256}$/.test(result.vid)||
      !(result.md5==null||result.md5===''||
        typeof result.md5==='string'&&/^[a-fA-F0-9]{32}$/.test(result.md5)))return;
   attempt.reported=true;root.metrics.report_count++;
   const report={v:1,...scopeFields(),invocation_id:attempt.invocation_id,name:info.name,size:info.size,
     type:info.type,sha256:sha,vid:result.vid,md5:result.md5||null,started_at:attempt.started_at,completed_at:Date.now()/1000};
   track(send('video',report).then(accepted=>{if(accepted===true)root.metrics.report_accepted_count++;else attempt.invalid=true;},()=>{attempt.invalid=true;}));
 }
 function beginAttempt(component,args){
   const found=locate();
   if(!found.ok||found.instance!==component||!root.current||root.current.instance!==component)return null;
   if(root.active)invalidate(root.active);
   const invocation_id=freshId(),now=Date.now()/1000;
   const raw=args.length===1?args[0]:null;
   const file=raw instanceof File?raw:(nativeListFile(raw)||
     (Array.isArray(raw)&&raw.length===1&&raw[0] instanceof File?raw[0]:null));
   const info=file?nativeFileInfo(file):null,selection=info?currentSelection(file,raw):null;
   const input=selection?.input||null;
   const uploader=component.uploader;
   const attempt={invocation_id,observer_nonce:root.scope.observer_nonce,component,uploader,file,input,info,selection,
     started_at:now,input_revision:0,selection_revision:0,invalid:false,sdk_calls:0,sdk_fulfilled:false,
     result:null,digest:null,reported:false};
   attempt.input_revision=attempt.input?root.selectionRevisions.get(attempt.input)||0:0;
   attempt.selection_revision=attempt.input_revision;
   if(!file||!info||!input||!uploaderShape(uploader))attempt.invalid=true;
   else {selection.used=true;root.metrics.native_input_match_count++;}
   root.active=attempt;root.metrics.outer_invoked_count++;
   track(send('start',{v:1,...scopeFields(),invocation_id,started_at:now}).then(accepted=>{
     if(accepted!==true)attempt.invalid=true;
     else if(!attempt.invalid){
       const promise=sha256(file,attempt);
       track(promise.then(value=>{attempt.digest=value;root.metrics.sha_ready_count++;maybeReport(attempt);},()=>{
         attempt.invalid=true;root.metrics.sha_failed_count++;}));
     }
   },()=>{attempt.invalid=true;}));
   return attempt;
 }
 function wrapComponent(found){
   if(root.componentPatch?.object===found.instance&&found.instance.uploadFile===root.componentPatch.wrapper)return true;
   restore(root.componentPatch,'uploadFile',root.componentPatch?.object);root.componentPatch=null;
   const object=found.instance,descriptor=Object.getOwnPropertyDescriptor(object,'uploadFile');
   if(!descriptor||!('value'in descriptor)||typeof descriptor.value!=='function'||!descriptor.writable)return false;
   const original=descriptor.value;
   function observedOuter(...args){
     let attempt=null;
     try{if(this===object)attempt=beginAttempt(object,args);}catch(_){try{root.metrics.observer_error_count++;}catch(__){}}
     let returned;
     try{returned=Reflect.apply(original,this,args);}catch(error){
       try{if(attempt){attempt.outer_sync_throw=true;invalidate(attempt);}}catch(_){}
       throw error;
     }
     if(attempt){
       try{Reflect.apply(nativeThen,returned,[()=>{root.metrics.outer_fulfilled_count++;attempt.outer_fulfilled=true;},
         ()=>{root.metrics.outer_rejected_count++;attempt.outer_rejected=true;}]);
         root.metrics.outer_promise_count++;}catch(_){try{attempt.outer_non_promise=true;}catch(__){}}
     }
     return returned;
   }
   try{Object.defineProperty(object,'uploadFile',{...descriptor,value:observedOuter});}
   catch(_){return false;}
   root.componentPatch={object,descriptor,original,wrapper:observedOuter};root.metrics.component_wrapped=true;return true;
 }
 function wrapUploader(uploader){
   if(root.uploaderPatch?.object===uploader&&uploader.uploadFile===root.uploaderPatch.wrapper)return true;
   restore(root.uploaderPatch,'uploadFile',root.uploaderPatch?.object);root.uploaderPatch=null;
   if(!uploaderShape(uploader))return false;
   const object=uploader,descriptor=Object.getOwnPropertyDescriptor(object,'uploadFile');
   if(descriptor&&(!('value'in descriptor)||typeof descriptor.value!=='function'||!descriptor.writable))return false;
   const original=descriptor?descriptor.value:Object.getPrototypeOf(object).uploadFile;
   if(typeof original!=='function')return false;
   function observedInner(...args){
     let attempt=null,match=false;
     try{
       attempt=root.active;
       const found=locate();
       match=!!(attempt&&!attempt.invalid&&attempt.component===root.current?.instance&&
         attempt.uploader===object&&this===object&&args.length>=1&&args[0]===attempt.file&&
         found.ok&&found.instance===attempt.component&&found.uploader===object&&
         attempt.input&&root.selection===attempt.selection&&attempt.selection?.file===attempt.file&&
         (root.selectionRevisions.get(attempt.input)||0)===attempt.input_revision);
       if(attempt&&attempt===root.active){
         attempt.sdk_calls++;root.metrics.sdk_invoked_count++;
         if(!match||attempt.sdk_calls!==1)attempt.invalid=true;
       }
     }catch(_){
       try{root.metrics.observer_error_count++;if(attempt)invalidate(attempt);}catch(__){}
       match=false;
     }
     let returned;
     try{returned=Reflect.apply(original,this,args);}catch(error){
       try{if(match){attempt.sdk_rejected=true;invalidate(attempt);}}catch(_){}
       throw error;
     }
     if(!match)return returned;
     try{
       Reflect.apply(nativeThen,returned,[result=>{
         try{root.metrics.sdk_fulfilled_count++;attempt.sdk_fulfilled=true;attempt.result=result;
           if(result&&typeof result==='object'&&result.file===attempt.file)root.metrics.result_file_match_count++;
           else attempt.invalid=true;
           maybeReport(attempt);
         }catch(_){attempt.invalid=true;root.metrics.observer_error_count++;}
       },()=>{root.metrics.sdk_rejected_count++;attempt.invalid=true;}]);
       root.metrics.sdk_promise_count++;
     }catch(_){try{attempt.invalid=true;}catch(__){}}
     return returned;
   }
   try{Object.defineProperty(object,'uploadFile',descriptor?{...descriptor,value:observedInner}:
     {value:observedInner,writable:true,configurable:true,enumerable:false});}
   catch(_){return false;}
   root.uploaderPatch={object,descriptor,original,wrapper:observedInner};root.metrics.uploader_wrapped=true;return true;
 }
 function refresh(){
   const found=locate();
   if(!found.ok){
     if(root.active&&!root.active.sdk_fulfilled)invalidate(root.active);
     return false;
   }
   if(root.current&&root.current.instance!==found.instance&&root.active&&!root.active.sdk_fulfilled)invalidate(root.active);
   root.current={fiber:found.fiber,instance:found.instance,uploader:found.uploader,container:found.container};
   const outer=wrapComponent(found);
   const inner=found.uploader&&wrapUploader(found.uploader);
   root.metrics.component_wrapped=outer;root.metrics.uploader_wrapped=!!inner;
   return outer&&!!inner;
 }
 root.configure=scope=>{
   if(!scope||typeof scope.job_id!=='string'||!scope.job_id||typeof scope.receipt_nonce!=='string'||!scope.receipt_nonce||
      !/^[0-9a-f]{48}$/.test(scope.observer_nonce||'')||
      typeof scope.binding_name!=='string'||scope.binding_name!=='__sauDYUploaded_'+scope.observer_nonce||
      !/^[0-9a-f]{64}$/.test(scope.expected_sha256||'')||!Number.isSafeInteger(scope.expected_size)||scope.expected_size<=0||
      typeof scope.expected_name!=='string'||!scope.expected_name)return false;
   if(root.scope&&root.scope.observer_nonce!==scope.observer_nonce&&root.active&&!root.active.invalid)return false;
   root.scope=Object.freeze({...scope});
   if(!root.selectionListener){root.selectionListener=selectionChanged;document.addEventListener('change',root.selectionListener,true);}
   if(!root.timer)root.timer=setInterval(()=>{try{refresh();}catch(_){root.metrics.observer_error_count++;}},100);
   try{return refresh();}catch(_){root.metrics.observer_error_count++;return false;}
 };
 root.status=nonce=>{
   if(root.scope?.observer_nonce!==nonce)return null;
   let ready=false;try{ready=refresh();}catch(_){}
   return {api_version:1,scope_match:true,route:window===top&&location.origin==='https://creator.douyin.com'&&
     ['/creator-micro/content/upload','/creator-micro/content/post/video'].includes(location.pathname),
     ready,root_count:root.metrics.root_count,root_valid_count:root.metrics.root_valid_count,
     nodes_visited:root.metrics.nodes_visited,tree_overflow:root.metrics.tree_overflow,
     candidate_count:root.metrics.candidate_count,component_wrapped:root.metrics.component_wrapped,
     uploader_wrapped:root.metrics.uploader_wrapped,active:!!root.active,active_invalid:!!root.active?.invalid,
     pending_count:root.pending.size,...root.metrics};
 };
 root.ensureReady=nonce=>root.scope?.observer_nonce===nonce&&refresh();
 root.flush=async(nonce,timeout)=>{
   if(root.scope?.observer_nonce!==nonce)return false;
   const deadline=Date.now()+Math.max(0,Math.min(10000,Number(timeout)||0));
   while(root.pending.size&&Date.now()<deadline){
     await Promise.race([Promise.allSettled([...root.pending]),new Promise(resolve=>setTimeout(resolve,10))]);
   }
   return root.pending.size===0;
 };
 root.close=nonce=>{
   if(root.scope?.observer_nonce!==nonce)return false;
   if(root.timer){clearInterval(root.timer);root.timer=null;}
   if(root.selectionListener)document.removeEventListener('change',root.selectionListener,true);
   root.selectionListener=null;invalidate(root.active);root.active=null;
   restore(root.uploaderPatch,'uploadFile',root.uploaderPatch?.object);
   restore(root.componentPatch,'uploadFile',root.componentPatch?.object);
   root.uploaderPatch=null;root.componentPatch=null;root.current=null;root.selection=null;root.scope=null;return true;
 };
 root.selectionRevisions=new WeakMap();
 window.__sauDYUploadObserver=root;
})()"""


class _DYMainFrameTransport:
    """Deliver reports only from Chrome's current top-frame default context."""

    def __init__(self, observer):
        self.observer, self.page = observer, observer.page
        self.client = None
        self.root_frame = None
        self.contexts = {}
        self.context_id = None
        self.bridge_id = None
        self.channel_nonce = None
        self.closed = False
        self.tasks = set()
        self.dispatch_lock = asyncio.Lock()
        self.raw_name = '__sauDYTransport_' + observer.observer_nonce
        self.handlers = {
            'Runtime.executionContextCreated': self._created,
            'Runtime.executionContextDestroyed': self._destroyed,
            'Runtime.executionContextsCleared': self._cleared,
            'Runtime.bindingCalled': self._called,
            'Page.frameNavigated': self._frame_navigated,
        }

    def _created(self, event):
        context = event.get('context', {})
        if type(context.get('id')) is int:
            self.contexts[context['id']] = {**dict(context.get('auxData') or {}),
                                            'origin': context.get('origin')}

    def _destroyed(self, event):
        ident = event.get('executionContextId')
        self.contexts.pop(ident, None)
        if ident == self.context_id:
            self.context_id, self.bridge_id, self.channel_nonce = None, None, None

    def _cleared(self, _event):
        self.contexts.clear()
        self.context_id, self.bridge_id, self.channel_nonce = None, None, None

    def _frame_navigated(self, event):
        frame = event.get('frame', {})
        if frame.get('id') and not frame.get('parentId'):
            self.root_frame = frame['id']

    def _canonical(self, ident):
        native = self.contexts.get(ident, {})
        candidates = [key for key, value in self.contexts.items()
                      if value.get('isDefault') is True and value.get('frameId') == self.root_frame]
        return (not self.closed and ident == self.context_id and self.bridge_id is not None and
                native.get('isDefault') is True and native.get('frameId') == self.root_frame and
                native.get('origin') == 'https://creator.douyin.com' and
                candidates == [ident])

    async def prepare(self):
        async with self.observer.transport_lock:
            if self.closed or not self.observer._is_current():
                return False
            if self.client is None:
                self.client = await self.page.context.new_cdp_session(self.page)
                await self.client.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
                for event, handler in self.handlers.items():
                    self.client.on(event, handler)
                await self.client.send('Page.enable')
                tree = await self.client.send('Page.getFrameTree')
                self.root_frame = tree['frameTree']['frame']['id']
                await self.client.send('Runtime.enable')
            tree = await self.client.send('Page.getFrameTree')
            self.root_frame = tree['frameTree']['frame']['id']
            candidates = [key for key, value in self.contexts.items()
                          if value.get('isDefault') is True and value.get('frameId') == self.root_frame]
            if len(candidates) != 1:
                return False
            ident = candidates[0]
            if self._canonical(ident):
                return True
            self.context_id = self.bridge_id = self.channel_nonce = None
            channel_nonce = secrets.token_hex(24)
            await self.client.send('Runtime.addBinding', {'name': self.raw_name, 'executionContextId': ident})
            expression = r"""(() => {
              if(window!==top)return null;const raw=window[RAW],nonce=NONCE,channel=CHANNEL,pending=new Map();
              if(typeof raw!=='function')return null;let closed=false,sequence=0;
              function send(kind,report){if(closed)return Promise.resolve(false);return new Promise(resolve=>{
                const key=String(++sequence);pending.set(key,resolve);
                try{raw(JSON.stringify({v:1,kind,sequence:key,observer_nonce:nonce,channel_nonce:channel,report}));}
                catch(_){pending.delete(key);resolve(false);}});}
              const bridge={probe:report=>send('probe',report),start:report=>send('start',report),
                invalidate:report=>send('invalidate',report),video:report=>send('video',report),
                cover:report=>send('cover',report)};
              window[PUBLIC]=bridge;
              return {deliver(key,accepted){const resolve=pending.get(key);if(!resolve)return false;
                pending.delete(key);resolve(accepted===true);return true;},close(){closed=true;
                for(const resolve of pending.values())resolve(false);pending.clear();}};
            })()""".replace('RAW', json.dumps(self.raw_name)).replace('NONCE', json.dumps(self.observer.observer_nonce)).replace('CHANNEL', json.dumps(channel_nonce)).replace('PUBLIC', json.dumps(self.observer.binding_name))
            result = await self.client.send('Runtime.evaluate', {'expression': expression,
                'contextId': ident, 'returnByValue': False})
            if result.get('exceptionDetails') or result.get('result', {}).get('type') != 'object':
                return False
            self.context_id = ident
            self.bridge_id = result.get('result', {}).get('objectId')
            self.channel_nonce = channel_nonce
            return self._canonical(ident)

    async def evaluate(self, expression):
        if not self._canonical(self.context_id):
            raise RuntimeError('dy_observer_main_context_unavailable')
        ident = self.context_id
        result = await self.client.send('Runtime.evaluate', {'expression': expression,
            'contextId': ident, 'awaitPromise': True, 'returnByValue': True})
        if result.get('exceptionDetails') or not self._canonical(ident):
            raise RuntimeError('dy_observer_main_context_changed')
        return result.get('result', {}).get('value')

    def _called(self, event):
        if event.get('name') != self.raw_name:
            return
        task = asyncio.create_task(self._dispatch(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _dispatch(self, event):
        async with self.dispatch_lock:
            ident = event.get('executionContextId')
            if not self._canonical(ident) or event.get('name') != self.raw_name:
                return False
            payload = event.get('payload')
            if not isinstance(payload, str) or len(payload) > 32768:
                return False
            try:
                message = json.loads(payload)
            except (ValueError, TypeError):
                return False
            if (not isinstance(message, dict) or set(message) != {'v', 'kind', 'sequence', 'observer_nonce', 'channel_nonce', 'report'} or
                    type(message['v']) is not int or message['v'] != 1 or
                    message['observer_nonce'] != self.observer.observer_nonce or
                    message['channel_nonce'] != self.channel_nonce or
                    message['kind'] not in ('probe', 'start', 'invalidate', 'video', 'cover') or
                    not isinstance(message['sequence'], str) or not re.fullmatch(r'[1-9][0-9]{0,15}', message['sequence'])):
                return False
            source = {'page': self.page, 'frame': self.page.main_frame}
            accepted = self.observer._receive(source, message['kind'], message['report'])
            if not self._canonical(ident):
                return False
            try:
                await self.client.send('Runtime.callFunctionOn', {'objectId': self.bridge_id,
                    'functionDeclaration': 'function(key,accepted){return this.deliver(key,accepted);}',
                    'arguments': [{'value': message['sequence']}, {'value': bool(accepted)}], 'returnByValue': True})
            except Exception:
                return False
            return bool(accepted)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.client is None:
            return
        for event, handler in self.handlers.items():
            self.client.remove_listener(event, handler)
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        try:
            if self.bridge_id:
                await self.client.send('Runtime.callFunctionOn', {'objectId': self.bridge_id,
                    'functionDeclaration': 'function(){this.close();}', 'returnByValue': True})
                await self.client.send('Runtime.releaseObject', {'objectId': self.bridge_id})
            await self.client.send('Runtime.removeBinding', {'name': self.raw_name})
        except Exception:
            pass
        finally:
            try:
                await self.client.detach()
            except Exception:
                pass


class DYUploadObserver:
    """Current-job, exact-native-File correlations for DraftEvidenceSession."""

    def __init__(self, session):
        self.session, self.page = session, session.page
        self.job_id = session.expected.get('job_id')
        self.receipt_nonce = session.expected.get('receipt_nonce')
        self.video = session.assets.get('video', {})
        self.video_sha256 = session.expected.get('video_sha256') or self.video.get('sha256')
        self.video_name = self.video.get('name')
        self.video_size = self.video.get('size')
        self.video_type = self.video.get('type') or self.video.get('mime_type') or ''
        self.observer_nonce = secrets.token_hex(24)
        self.binding_name = '__sauDYUploaded_' + self.observer_nonce
        self.started_at = time.time()
        self.closed = False
        self.available = False
        self.transport_ready = False
        self.transport_lock = asyncio.Lock()
        self.transport = _DYMainFrameTransport(self)
        self.records = {}
        self.invalid_invocations = set()
        self.latest_invocation = None
        self.latest_invocation_number = 0
        self.received_count = 0
        self.accepted_count = 0
        self.tasks = set()
        self.navigation_handler = self._navigated
        self._last_status = None

    @property
    def scope(self):
        return {'job_id': self.job_id, 'receipt_nonce': self.receipt_nonce,
                'observer_nonce': self.observer_nonce}

    @property
    def browser_scope(self):
        return {**self.scope, 'binding_name': self.binding_name,
                'expected_sha256': self.video_sha256, 'expected_name': self.video_name,
                'expected_size': self.video_size}

    def _is_current(self):
        return (not self.closed and not self.session.closed and
                getattr(self.page, '_sau_draft_evidence', None) is self.session and
                getattr(self.session, 'dy_observer', None) is self)

    async def initialize(self):
        if (not isinstance(self.job_id, str) or not self.job_id or
                not isinstance(self.receipt_nonce, str) or not self.receipt_nonce or
                not isinstance(self.video_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', self.video_sha256) or
                not isinstance(self.video_name, str) or not self.video_name or
                type(self.video_size) is not int or self.video_size <= 0):
            return
        if not getattr(self.page, '_sau_dy_upload_script', False):
            await self.page.add_init_script(DY_UPLOAD_OBSERVER_SCRIPT)
            self.page._sau_dy_upload_script = True
        self.page.on('framenavigated', self.navigation_handler)
        await self._configure(self.page.main_frame)

    def _navigated(self, frame):
        if not self._is_current() or frame is not self.page.main_frame:
            return
        task = asyncio.create_task(self._configure(frame))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _configure(self, frame):
        if not self._is_current() or frame is not self.page.main_frame:
            return
        try:
            if not await self.transport.prepare():
                self.transport_ready = False
                return
            await self.transport.evaluate(DY_UPLOAD_OBSERVER_SCRIPT)
            self.transport_ready = bool(await asyncio.wait_for(self.transport.evaluate(
                '(async()=>{const b=window[' + json.dumps(self.binding_name) + '];return !!b&&await b.probe(' +
                json.dumps({'v': 1, 'probe': True, **self.scope}) + ')===true})()'), timeout=2))
            self.available = bool(await self.transport.evaluate(
                'window.__sauDYUploadObserver?.configure(' + json.dumps(self.browser_scope) + ')'))
            self._last_status = await self.transport.evaluate(
                'window.__sauDYUploadObserver?.status(' + json.dumps(self.observer_nonce) + ')')
        except Exception:
            self.transport_ready = False
            self.available = False

    def _receive(self, source, kind, report):
        if (not self._is_current() or not isinstance(source, dict) or
                source.get('page') is not self.page or source.get('frame') is not self.page.main_frame or
                not isinstance(report, dict)):
            return False
        if kind == 'probe':
            return (set(report) == {'v', 'probe', *self.scope} and type(report.get('v')) is int and
                    report.get('v') == 1 and report.get('probe') is True and
                    all(report.get(key) == value for key, value in self.scope.items()))
        if kind == 'cover':
            observer = getattr(self.session, 'dy_cover_observer', None)
            return bool(observer and observer.accept(source, report))
        base = {'v', 'job_id', 'receipt_nonce', 'observer_nonce'}
        if (type(report.get('v')) is not int or report['v'] != 1 or
                any(report.get(key) != value for key, value in self.scope.items())):
            return False
        if kind == 'start':
            if set(report) != base | {'invocation_id', 'started_at'}:
                return False
            invocation = report.get('invocation_id')
            started = report.get('started_at')
            if (not isinstance(invocation, str) or not re.fullmatch(r'[1-9][0-9]{0,15}', invocation) or
                    int(invocation) <= self.latest_invocation_number or type(started) not in (int, float) or
                    not math.isfinite(started) or not self.started_at <= started <= time.time() + 5):
                return False
            self.latest_invocation_number = int(invocation)
            self.latest_invocation = invocation
            self.records.clear()
            self.invalid_invocations.clear()
            self.received_count += 1
            return True
        if kind == 'invalidate':
            if set(report) != base | {'invocation_id'} or report.get('invocation_id') != self.latest_invocation:
                return False
            self.records.clear()
            self.invalid_invocations.add(report['invocation_id'])
            self.received_count += 1
            return True
        if kind != 'video':
            return False
        allowed = base | {'invocation_id', 'name', 'size', 'type', 'sha256', 'vid', 'md5', 'started_at', 'completed_at'}
        if set(report) != allowed:
            return False
        invocation = report.get('invocation_id')
        if (not isinstance(invocation, str) or invocation != self.latest_invocation or
                invocation in self.invalid_invocations):
            return False
        if (not isinstance(report.get('sha256'), str) or report['sha256'] != self.video_sha256 or
                type(report.get('size')) is not int or report['size'] != self.video_size or
                not isinstance(report.get('name'), str) or report['name'] != self.video_name or
                not isinstance(report.get('type'), str) or len(report['type']) > 128 or
                re.search(r'[\x00-\x1f\x7f]', report['type'])):
            return False
        if self.video_type and report['type'] != self.video_type:
            return False
        if (not isinstance(report.get('vid'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', report['vid']) or
                (report.get('md5') is not None and
                 (not isinstance(report['md5'], str) or not re.fullmatch(r'[a-fA-F0-9]{32}', report['md5'])))):
            return False
        times = [report.get('started_at'), report.get('completed_at')]
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in times) or
                not self.started_at <= times[0] <= times[1] <= time.time() + 5):
            return False
        self.received_count += 1
        prior = self.records.get(invocation)
        safe = dict(report)
        if prior and prior != safe:
            self.records.pop(invocation, None)
            self.invalid_invocations.add(invocation)
            return False
        self.records[invocation] = safe
        self.accepted_count += 1
        return True

    def _receive_video(self, source, report):
        return self._receive(source, 'video', report)

    async def ensure_ready(self, timeout=5):
        if (not self._is_current() or type(timeout) not in (int, float) or
                not math.isfinite(timeout) or not 0 < timeout <= 30):
            return False
        await self._configure(self.page.main_frame)
        if not self.transport_ready:
            return False
        deadline = asyncio.get_running_loop().time() + timeout
        status = None
        while self._is_current():
            try:
                status = await self.transport.evaluate(
                    'window.__sauDYUploadObserver?.ensureReady(' + json.dumps(self.observer_nonce) + ')')
                if status is True:
                    self.available = True
                    return True
            except Exception:
                pass
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(min(.05, max(0, deadline - asyncio.get_running_loop().time())))
        if isinstance(status, dict):
            self._last_status = status
        return False

    def upload_links(self):
        if not self._is_current() or self.latest_invocation is None or self.latest_invocation in self.invalid_invocations:
            return []
        record = self.records.get(self.latest_invocation)
        if not record:
            return []
        return [{'kind': 'video', 'sha256': record['sha256'], 'ids': [record['vid']], 'complete': True}]

    async def drain(self, timeout=5):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if not self._is_current() or not self.transport_ready:
            return
        try:
            await self.transport.evaluate(
                'window.__sauDYUploadObserver?.flush(' + json.dumps(self.observer_nonce) + ',' +
                str(max(0, min(10, float(timeout))) * 1000) + ')')
            if self.transport.tasks:
                await asyncio.gather(*tuple(self.transport.tasks), return_exceptions=True)
            self._last_status = await self.transport.evaluate(
                'window.__sauDYUploadObserver?.status(' + json.dumps(self.observer_nonce) + ')')
        except Exception:
            pass

    async def close(self):
        if self.closed:
            return
        self.page.remove_listener('framenavigated', self.navigation_handler)
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        try:
            await self.transport.evaluate(
                'window.__sauDYUploadObserver?.close(' + json.dumps(self.observer_nonce) + ')')
        except Exception:
            pass
        finally:
            self.closed = True
            self.transport_ready = False
            self.available = False
            await self.transport.close()


async def install_dy_observer(session):
    """Observe only Douyin sessions; call after assets are hashed, before video selection."""
    if getattr(session, 'platform', None) != 'douyin':
        return None
    observer = DYUploadObserver(session)
    session.dy_observer = observer
    await observer.initialize()
    return observer
