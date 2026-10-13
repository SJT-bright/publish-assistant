"""Observe existing XHS SDK uploads without starting any platform operation.

Official cached source, zero-based decoded character offsets, 2026-10-03:
  limits-login/fresh-xhs-project-publish-vue.474cd180.js:326668 module 67490
  exports t$; 331091 assigns that same instance to window._publisher.
  limits-login/fresh-xhs-index.7cd017a9.js:23450 upload(File, options) resolves
  only after its underlying post result has code === 0 and data. At 669244,
  COS.uploadFile's successful Promise is mapped to the allocated fileId.
  project-publish-vue:1371927 copies that resolved fileId to video.fileid.

Only the existing window._publisher reference is read. Never require a webpack
module, create an SDK instance, call createUploader ourselves, or send a request.
The normal UI may lazily create an uploader; its original call is observed.
Every wrapped upload returns exactly the original Promise. Its arguments,
receiver, resolved object and rejection remain unchanged.

Cover editors have separate, operation-bound native selection chains. The old
editor observes Cropper/canvas; Mojito observes the existing Zeus local File/FS
resource and its actual final exported File. Unsupported transformations or a
missing link leave the cover unverified.
"""
import asyncio
import json
import math
import re
import secrets
import time


SDK_OBSERVER_SCRIPT = r"""(() => {
 'use strict';
 const previous=window.__sauXHSSDKObserver;
 if(previous){
   if(previous.api_version===4)return;
   // An old worker leaves its closed observation object on the existing page.
   // Replace only a completely idle legacy observer, never a live invocation.
   if(previous.active!==null||previous.timer!==null||!(previous.pending instanceof Set)||previous.pending.size||
      !(previous.managers instanceof Map)||previous.managers.size||!(previous.uploaders instanceof Map)||previous.uploaders.size||
      typeof previous.configure!=='function'||typeof previous.close!=='function'||typeof previous.flush!=='function')return;
 }
 const videoNames = new Set(['video-uploader', 'not-transcode-video-uploader']);
 const nativeThen = Promise.prototype.then;
 const nativeSlice=Blob.prototype.slice, nativeArrayBuffer=Blob.prototype.arrayBuffer;
 const sizeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
 const typeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get;
 const nameGetter=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
 function freshMetrics(){return {invoked_count:0,native_file:false,promise:false,
   handler_attached_count:0,handler_failed_count:0,fulfilled_count:0,rejected_count:0,
   fulfilled_scope_match:false,rejected_scope_match:false,result_object:false,
   file_id_present:false,file_id_string:false,file_id_valid:false,
   digest_ready_count:0,digest_failed_count:0,digest_scope_match:false,
   binding_present:false,binding_scope_match:false,binding_called_count:0,
   binding_fulfilled_count:0,binding_rejected_count:0,binding_accepted_count:0,
   binding_not_accepted_count:0,observer_error_count:0,closed:false};}
 const root = {api_version:4,active:null, manager:null, timer:null, sequence:0,
               pending:new Set(), managers:new Map(), uploaders:new Map(),
               metrics:freshMetrics()};

 // File bytes are immutable. Hash in bounded chunks; never export the bytes.
 async function digest(file,scope) {
   const K = new Uint32Array([
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
   const h = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                             0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
   const w = new Uint32Array(64), R=(v,n)=>(v>>>n)|(v<<(32-n));
   function block(bytes,off) {
     for(let i=0;i<16;i++){const p=off+i*4;w[i]=((bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3])>>>0;}
     for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(w[i-16]+(R(a,7)^R(a,18)^(a>>>3))+w[i-7]+(R(b,17)^R(b,19)^(b>>>10)))>>>0;}
     let [a,b,c,d,e,f,g,j]=h;
     for(let i=0;i<64;i++){
       const t1=(j+(R(e,6)^R(e,11)^R(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0;
       const t2=((R(a,2)^R(a,13)^R(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
       j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;
     }
     [a,b,c,d,e,f,g,j].forEach((v,i)=>h[i]=(h[i]+v)>>>0);
   }
   let tail=new Uint8Array(0);
   const size=Reflect.apply(sizeGetter,file,[]);
   for(let off=0;off<size;off+=1048576){
     if(root.active!==scope)throw Error('obsolete observer');
     const chunk=Reflect.apply(nativeSlice,file,[off,off+1048576]);
     const bytes=new Uint8Array(await Reflect.apply(nativeArrayBuffer,chunk,[]));
     const merged=new Uint8Array(tail.length+bytes.length);merged.set(tail);merged.set(bytes,tail.length);
     const whole=merged.length-(merged.length%64);
     for(let p=0;p<whole;p+=64)block(merged,p);
     tail=merged.slice(whole);
     await new Promise(resolve=>setTimeout(resolve,0));
   }
   const last=new Uint8Array(tail.length<56?64:128);last.set(tail);last[tail.length]=128;
   const view=new DataView(last.buffer);
   view.setUint32(last.length-8,Math.floor(size/536870912));
   view.setUint32(last.length-4,(size*8)>>>0);
   for(let p=0;p<last.length;p+=64)block(last,p);
   return Array.from(h,x=>x.toString(16).padStart(8,'0')).join('');
 }
 function track(promise,scope) {
   const entry={promise,scope};root.pending.add(entry);
   Reflect.apply(nativeThen,promise,[()=>root.pending.delete(entry),()=>root.pending.delete(entry)]);
   return promise;
 }
 function ownScope(scope,manager) {
   return root.active===scope && root.manager===manager && window._publisher?.uploader===manager;
 }
 function wrapUploader(uploader,name,manager) {
   if((!videoNames.has(name)&&name!=='image-uploader')||!uploader||root.uploaders.has(uploader))return;
   const original=uploader.upload;
   if(typeof original!=='function')return;
   const descriptor=Object.getOwnPropertyDescriptor(uploader,'upload');
   if(descriptor && (!('value' in descriptor)||!descriptor.writable))return;
   function observedUpload(...args) {
     if(name==='image-uploader') {
       let cover=null;
       try {if(this===uploader&&root.active&&ownScope(root.active,manager))
         cover=window.__sauXHSCoverObserver?.captureUpload(args[0],root.active);}catch(_){}
       const returned=Reflect.apply(original,this,args);
       try {if(cover)window.__sauXHSCoverObserver?.observeUpload(cover,returned);}catch(_){}
       return returned;
     }
     const scope=root.active, file=args[0];
     let measured=false;try{measured=!!(scope&&ownScope(scope,manager));}catch(_){}
     if(measured){root.metrics.invoked_count++;root.metrics.native_file=false;root.metrics.promise=false;}
     let capture=null;
     try {
       if(this===uploader&&scope&&ownScope(scope,manager)&&file instanceof File) {
         const size=Reflect.apply(sizeGetter,file,[]),name=Reflect.apply(nameGetter,file,[]);
         if(Number.isSafeInteger(size)&&size>0&&typeof name==='string')
         capture={scope, file, name, size, type:Reflect.apply(typeGetter,file,[]),
                  started_at:Date.now()/1000, invocation_id:String(++root.sequence),metrics:root.metrics};
       }
     } catch (_) { /* Observation must not change the SDK call. */ }
     if(measured)root.metrics.native_file=!!capture;
     // Execute first. Preserve synchronous throws and the exact return object.
     const returned=Reflect.apply(original,this,args);
     try{if(measured)root.metrics.promise=returned instanceof Promise;}catch(_){}
     if(!capture||!(returned instanceof Promise))return returned;
     try {
       const metrics=capture.metrics;
       const fingerprint=track(Promise.resolve().then(()=>digest(capture.file,capture.scope)).then(sha256=>{
         metrics.digest_ready_count++;metrics.digest_scope_match=ownScope(capture.scope,manager);return sha256;
       },error=>{metrics.digest_failed_count++;metrics.digest_scope_match=ownScope(capture.scope,manager);throw error;}),capture.scope);
       // Attach only a side observer, returning the SDK's original Promise.
       Reflect.apply(nativeThen,returned,[result=>{
         try {
           metrics.fulfilled_count++;metrics.fulfilled_scope_match=ownScope(capture.scope,manager);
           metrics.result_object=!!result&&typeof result==='object';
           metrics.file_id_present=!!result&&'fileId' in Object(result);
           metrics.file_id_string=!!result&&typeof result.fileId==='string';
           metrics.file_id_valid=!!(metrics.file_id_string&&result.fileId&&result.fileId.length<=2048&&!/[\x00-\x1f\x7f]/.test(result.fileId));
           if(!metrics.fulfilled_scope_match||!metrics.file_id_valid)return;
           const file_id=result.fileId, completed_at=Date.now()/1000;
           track(fingerprint.then(sha256=>{
             metrics.binding_scope_match=ownScope(capture.scope,manager);
             const binding=window[capture.scope.binding_name];
             metrics.binding_present=typeof binding==='function';
             if(!metrics.binding_scope_match||!metrics.binding_present)return;
             metrics.binding_called_count++;
             let delivery;
             try{delivery=binding({v:1,job_id:capture.scope.job_id,
               receipt_nonce:capture.scope.receipt_nonce,observer_nonce:capture.scope.observer_nonce,
               invocation_id:capture.invocation_id,uploader:name,name:capture.name,size:capture.size,
               type:capture.type,sha256,file_id,started_at:capture.started_at,completed_at});}
             catch(error){metrics.binding_rejected_count++;throw error;}
             return Promise.resolve(delivery).then(value=>{metrics.binding_fulfilled_count++;
               if(value===true)metrics.binding_accepted_count++;else metrics.binding_not_accepted_count++;
               return value;},
               error=>{metrics.binding_rejected_count++;throw error;});
           }).catch(()=>{}),capture.scope);
         } catch (_) {metrics.observer_error_count++; /* Never alter the resolved object. */ }
       },()=>{try{metrics.rejected_count++;metrics.rejected_scope_match=ownScope(capture.scope,manager);}catch(_){} }]);
       metrics.handler_attached_count++;
     } catch (_) {
       try{capture.metrics.handler_failed_count++;capture.metrics.promise=false;}catch(_){}
       /* Never alter the original Promise or rejection. */
     }
     return returned;
   }
   try {
     Object.defineProperty(uploader,'upload',descriptor?{...descriptor,value:observedUpload}:
                           {value:observedUpload,writable:true,configurable:true,enumerable:true});
     root.uploaders.set(uploader,{original,descriptor,wrapper:observedUpload,name});
   } catch (_) { /* Unsupported descriptors remain unobserved. */ }
 }
 function inspectExistingPublisher() {
   if(!root.active)return false;
   const manager=window._publisher?.uploader;
   if(!manager||typeof manager.getUploader!=='function'||typeof manager.createUploader!=='function')return false;
   root.manager=manager;
   if(!root.managers.has(manager)) {
     const original=manager.createUploader,descriptor=Object.getOwnPropertyDescriptor(manager,'createUploader');
     if(!descriptor||('value' in descriptor&&descriptor.writable)) {
       function observedCreate(...args) {
         const result=Reflect.apply(original,this,args);
         try {if(this===manager&&root.active&&window._publisher?.uploader===manager)
                wrapUploader(result,args[0],manager);}catch(_){}
         return result;
       }
       try {
         Object.defineProperty(manager,'createUploader',descriptor?{...descriptor,value:observedCreate}:
                               {value:observedCreate,writable:true,configurable:true,enumerable:true});
         root.managers.set(manager,{original,descriptor,wrapper:observedCreate});
       } catch (_) {}
     }
   }
   for(const name of [...videoNames,'image-uploader']) {
     try {wrapUploader(Reflect.apply(manager.getUploader,manager,[name]),name,manager);}catch(_){}
   }
   return true;
 }
 function restore() {
   for(const [object,entry] of [...root.uploaders,...root.managers]) {
     const key=root.uploaders.has(object)?'upload':'createUploader';
     try {
       if(object[key]===entry.wrapper) {
         if(entry.descriptor)Object.defineProperty(object,key,entry.descriptor);
         else delete object[key];
       }
     } catch (_) {}
   }
   root.uploaders.clear();root.managers.clear();root.manager=null;
 }
 root.configure=scope=>{
   if(!scope||!scope.job_id||!scope.receipt_nonce||!scope.observer_nonce||
      typeof scope.binding_name!=='string'||!/^__sauXHSSDKUploaded_[0-9a-f]{48}$/.test(scope.binding_name))return false;
   if(root.active?.observer_nonce!==scope.observer_nonce){root.active=Object.freeze({...scope});
     root.metrics=freshMetrics();}
   if(!root.timer)root.timer=setInterval(()=>{try{inspectExistingPublisher();}catch(_){}},200);
   try{return inspectExistingPublisher();}catch(_){return false;}
 };
 root.status=observer_nonce=>{
   if(root.active?.observer_nonce!==observer_nonce)return null;
   const manager=window._publisher?.uploader;
   const manager_available=!!(manager&&root.manager===manager&&typeof manager.getUploader==='function'&&typeof manager.createUploader==='function');
   let existing_video_count=0,wrapped_video_count=0;
   if(manager_available)for(const name of videoNames){try{
     const uploader=Reflect.apply(manager.getUploader,manager,[name]);
     if(uploader){existing_video_count++;if(root.uploaders.get(uploader)?.wrapper===uploader.upload)wrapped_video_count++;}
   }catch(_){}}
   const sdkCreate=manager_available?root.managers.get(manager):null;
   const cover=window.__sauXHSCoverObserver,coverCreate=cover?.managers?.get(manager);
   // The cover observer legitimately wraps our creator to observe only image
   // uploaders. Accept that exact same-task chain, never an unknown wrapper.
   const sameCoverScope=!!(root.active&&cover?.active?.cover&&
     ['job_id','receipt_nonce','observer_nonce'].every(key=>cover.active[key]===root.active[key]));
   const create_wrapped=!!(sdkCreate&&(sdkCreate.wrapper===manager.createUploader||
     (sameCoverScope&&coverCreate?.wrapper===manager.createUploader&&coverCreate.original===sdkCreate.wrapper)));
   // A wrapped normal create call synchronously wraps the SDK's lazily created
   // uploader before the UI can invoke upload. Never create it ourselves.
   const ready=manager_available&&existing_video_count===wrapped_video_count&&
     (create_wrapped||existing_video_count===videoNames.size);
   return {manager_available,create_wrapped,existing_video_count,wrapped_video_count,ready,...root.metrics};
 };
 root.ensureReady=observer_nonce=>{
   if(root.active?.observer_nonce!==observer_nonce)return null;
   try{inspectExistingPublisher();}catch(_){}
   return root.status(observer_nonce);
 };
 root.flush=async observer_nonce=>{
   if(root.active?.observer_nonce!==observer_nonce)return false;
   inspectExistingPublisher();
   // Never await an unresolved SDK upload. Only fingerprints and deliveries.
   for(let round=0;round<4;round++) {
     await Promise.resolve();
     const pending=Array.from(root.pending).filter(entry=>entry.scope===root.active).map(entry=>entry.promise);
     if(!pending.length)break;
     await Promise.allSettled(pending);
   }
   return true;
 };
 root.close=observer_nonce=>{
   if(root.active?.observer_nonce!==observer_nonce)return false;
   root.metrics.closed=true;
   root.active=null;
   if(root.timer){clearInterval(root.timer);root.timer=null;}
   restore();return true;
 };
 window.__sauXHSSDKObserver=root;
})()"""


# Existing old-editor Cropper 92060.getCroppedCanvas, official decoded chars
# 35876..38456 in library-cropper.18277c8a.js. Pin the actual function body;
# a plain object that imitates img.cropper is not a supported editor instance.
COVER_OBSERVER_SCRIPT = r"""(() => {
 'use strict';
 const previous=window.__sauXHSCoverObserver;
 if(previous){
   if(previous.api_version===2)return;
   // Upgrade only a fully closed, idle old worker's retained observer.
   if(previous.active!==null||previous.timer!==null||!(previous.pending instanceof Set)||previous.pending.size||
      !(previous.uploaders instanceof Map)||previous.uploaders.size||!(previous.managers instanceof Map)||previous.managers.size)return;
 }
 const nativeThen=Promise.prototype.then,nativeFetch=window.fetch;
 const blobBytes=Blob.prototype.arrayBuffer,blobSize=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
 const blobType=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get;
 const fileName=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
 const inputFiles=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files').get;
 const inputType=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'type').get;
 const nativePath=Event.prototype.composedPath,fnSource=Function.prototype.toString;
 const canvasWidth=Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,'width').get;
 const canvasEncode=HTMLCanvasElement.prototype.toDataURL;
 const shadowGetter=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get;
 const queries=[Element.prototype.querySelectorAll,Document.prototype.querySelectorAll,DocumentFragment.prototype.querySelectorAll];
 const addListener=EventTarget.prototype.addEventListener;
 const methodSHA='ad0070513e8f8ee1a161bd86f3168c36967787653ea09192671a7463e6f4145b';
 const state={api_version:2,active:null,timer:null,revision:0,sequence:0,selection:null,ticket:null,
   transform:null,editors:new Map(),canvases:new Map(),managers:new Map(),uploaders:new Map(),
   pending:new Set(),roots:new WeakSet(),events:new WeakSet(),engines:new Map(),
   localPaths:new Map(),invalidPaths:new Set(),exportedFiles:new WeakMap()};
 // Official Zeus getExportImage, cached library-zeus-engine.1cc8bc98.js
 // chars 323719..324413. Also verified against the actual mounted SDK method.
 const exportMethodSHA='d2d474c418bd55bed04596ac55373d3991db05e9db730bb018cd8dbb038efa0a';
 const mojitoInput='.cover-choose-container .upload-slot > label.upload-btn > input.upload-input[type="file"][accept*="image"]';
 const unref=v=>v&&v.__v_isRef===true?v.value:v;
 function current(scope,selection){return state.active===scope&&state.selection===selection&&selection.revision===state.revision;}
 function track(promise,scope){const entry={promise,scope};state.pending.add(entry);
   Reflect.apply(nativeThen,promise,[()=>state.pending.delete(entry),()=>state.pending.delete(entry)]);return promise;}
 async function sha(buffer){const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',buffer));
   return Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');}
 async function fingerprint(blob){const size=Reflect.apply(blobSize,blob,[]);
   if(!Number.isSafeInteger(size)||size<=0||size>67108864)throw Error('unsupported cover');
   return {size,sha256:await sha(await Reflect.apply(blobBytes,blob,[]))};}
 function elements(root,selector){for(const query of queries){try{return Array.from(Reflect.apply(query,root,[selector]));}catch(_){}}return [];}
 function visible(el){return !!el?.isConnected&&el.getClientRects().length>0&&getComputedStyle(el).visibility!=='hidden';}
 function roots(){const found=[],seen=new Set();function scan(root){if(seen.has(root))return;seen.add(root);found.push(root);
   for(const el of elements(root,'*')){let child;try{child=Reflect.apply(shadowGetter,el,[]);}catch(_){}
     if(child?.mode==='open')scan(child);}}scan(document);return found;}
 function mojito(m){return !!m?.matches('.main-cover-editor-modal.cover-modal')&&
   elements(m,':scope > .d-modal-header > h6').length===1&&
   elements(m,':scope > .d-modal-header > h6')[0].textContent.trim()==='设置封面';}
 function modal(){const found=roots().flatMap(r=>elements(r,'#cover-modal,.main-cover-editor-modal.cover-modal')).filter(visible);
   return found.length===1?found[0]:null;}
 function editor(){const m=modal();if(!m)return null;
   const images=elements(m,'#upload-cover-containner img.cropper');if(images.length!==1)return null;
   const img=images[0],cropper=img.cropper;
   if(!img.isConnected||!cropper||cropper.element!==img||cropper.isImg!==true||cropper.ready!==true||
      typeof cropper.originalUrl!=='string'||!cropper.originalUrl.startsWith('blob:'))return null;
   try {if(new URL(cropper.originalUrl).origin!==location.origin)return null;}catch(_){return null;}
   return {modal:m,img,cropper,url:cropper.originalUrl};}
 function invalidate(){state.revision++;state.selection=null;state.ticket=null;state.transform=null;
   state.localPaths.clear();state.invalidPaths.clear();state.exportedFiles=new WeakMap();
   restore(state.editors,'getCroppedCanvas');restore(state.canvases,'toDataURL');}
 function restore(entries,key){for(const [object,entry] of entries){try{if(object[key]===entry.wrapper){
     if(entry.descriptor)Object.defineProperty(object,key,entry.descriptor);else delete object[key];}}catch(_){}}
   entries.clear();}
 function chosen(event){if(state.events.has(event))return;state.events.add(event);
   const scope=state.active,m=modal();if(!scope?.cover||!m)return;
   let input,files;try{for(const node of Reflect.apply(nativePath,event,[])){try{
     if(Reflect.apply(inputType,node,[])==='file'&&m.contains(node)&&
       (mojito(m)?elements(m,mojitoInput).length===1&&elements(m,mojitoInput)[0]===node:node.closest('.upload-wrapper'))){
       input=node;files=Array.from(Reflect.apply(inputFiles,node,[])||[]);break;}}catch(_){}}}catch(_){return;}
   if(!input)return;invalidate();if(files.length!==1)return;
   const file=files[0];let name,size,type;try{name=Reflect.apply(fileName,file,[]);size=Reflect.apply(blobSize,file,[]);type=Reflect.apply(blobType,file,[]);}catch(_){return;}
   if(name!==scope.cover.name||size!==scope.cover.size||!type.startsWith('image/'))return;
   const selection={scope,modal:m,revision:state.revision,name,size,type,file,selected_at:Date.now()/1000};
   state.selection=selection;
   selection.proof=track(fingerprint(file).then(value=>{
     if(!current(scope,selection)||value.sha256!==scope.cover.sha256)throw Error('wrong selection');
     return value;
   }).catch(()=>null),scope);
   if(mojito(m))inspectMojito();
 }
 function imagePath(engine){try{
   const canvas=engine.getCanvas(),imageType=engine.zeusModule?.LayerType?.LT_IMAGE?.value;
   if(!canvas||!Number.isFinite(imageType)||typeof canvas.getLayerCount!=='function'||
      typeof canvas.getLayerByIndex!=='function'||canvas.getLayerCount()!==1)return null;
   const layer=canvas.getLayerByIndex(0);
   if(!layer||typeof layer.getType!=='function'||layer.getType()?.value!==imageType||
      typeof layer.getMainResource!=='function')return null;
   const path=layer.getMainResource()?.path;
   return typeof path==='string'&&path&&path.length<=4096?{canvas,path}:null;
 }catch(_){return null;}}
 function restoreEngines(){for(const [engine,entry] of state.engines){for(const key of ['uploadLocalFile','getExportImage']){
   const saved=entry[key];try{if(saved&&engine[key]===saved.wrapper){if(saved.descriptor)Object.defineProperty(engine,key,saved.descriptor);else delete engine[key];}}catch(_){}}
 }state.engines.clear();}
 function inspectMojito(){const scope=state.active,m=modal(),engine=window.zeusEngine;
   if(!scope?.cover||!mojito(m)||!engine||Object.getPrototypeOf(engine)===Object.prototype||
      typeof engine.getCanvas!=='function'||typeof engine.getZeusModule!=='function'||
      typeof engine.uploadLocalFile!=='function'||typeof engine.getExportImage!=='function')return null;
   const prior=state.engines.get(engine);if(prior)return prior;
   const originalLocal=engine.uploadLocalFile,originalExport=engine.getExportImage;
   const localDescriptor=Object.getOwnPropertyDescriptor(engine,'uploadLocalFile');
   const exportDescriptor=Object.getOwnPropertyDescriptor(engine,'getExportImage');
   if([localDescriptor,exportDescriptor].some(d=>d&&(!('value'in d)||!d.writable)))return null;
   const entry={engine,scope,ready:false,uploadLocalFile:{original:originalLocal,descriptor:localDescriptor},
                getExportImage:{original:originalExport,descriptor:exportDescriptor}};
   entry.proof=track(sha(new TextEncoder().encode(Reflect.apply(fnSource,originalExport,[]))).then(signature=>{
     entry.ready=signature===exportMethodSHA&&state.active===scope&&window.zeusEngine===engine;
     return entry.ready;
   }).catch(()=>false),scope);
   function observedLocal(...args){const selection=state.selection,file=args[0];let capture=null;
     try{if(this===engine&&entry.ready&&window.zeusEngine===engine&&selection?.file===file&&
         selection.modal===modal()&&mojito(selection.modal)&&current(scope,selection))
       capture={scope,selection,engine,started_at:Date.now()/1000};}catch(_){}
     const returned=Reflect.apply(originalLocal,this,args);
     try{if(capture&&returned instanceof Promise)Reflect.apply(nativeThen,returned,[result=>{
       if(!current(scope,selection)||window.zeusEngine!==engine||result?.success!==true||
          typeof result.path!=='string'||!result.path||result.path.length>4096||state.invalidPaths.has(result.path))return;
       const previous=state.localPaths.get(result.path);
       if(previous&&previous.selection!==selection){state.localPaths.delete(result.path);state.invalidPaths.add(result.path);return;}
       state.localPaths.set(result.path,{...capture,path:result.path,proof:selection.proof});
     },()=>{}]);}catch(_){}return returned;
   }
   function observedExport(...args){let capture=null;
     try{const selection=state.selection,ticket=state.ticket,source=imagePath(engine);
       const local=source&&state.localPaths.get(source.path);
       if(this===engine&&entry.ready&&window.zeusEngine===engine&&args.length===7&&args[0]===false&&
          args.slice(1).every(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0)&&
          args.slice(3).every(v=>v>0)&&selection&&current(scope,selection)&&ticket?.kind==='mojito'&&
          ticket.selection===selection&&ticket.modal===modal()&&mojito(ticket.modal)&&
          ticket.open&&Date.now()/1000-ticket.transform_at<=30&&local?.selection===selection&&
          local.scope===scope&&local.engine===engine&&!state.invalidPaths.has(source.path))
         capture={scope,selection,ticket,source,local,engine};
     }catch(_){}
     const returned=Reflect.apply(originalExport,this,args);
     try{if(capture){const size=Reflect.apply(blobSize,returned,[]),type=Reflect.apply(blobType,returned,[]);
       const name=Reflect.apply(fileName,returned,[]);
       if(name!=='saveImag.jpg'||!['image/jpg','image/jpeg'].includes(type)||!Number.isSafeInteger(size)||size<=0||size>67108864)return returned;
       const sourceNow=imagePath(engine);
       if(sourceNow?.canvas!==capture.source.canvas||sourceNow.path!==capture.source.path)return returned;
       const selection=capture.selection;
       const transform={kind:'mojito',entry:{scope,selection},ticket:capture.ticket,engine,file:returned,
          size,upload:null,reported:false};
       transform.proof=track(Promise.all([capture.local.proof,fingerprint(returned)]).then(([origin,derived])=>{
         if(!origin||!current(scope,selection))return null;
         return {origin_sha256:origin.sha256,derived_sha256:derived.sha256};
       }).catch(()=>null),scope);
       state.exportedFiles.set(returned,transform);
     }}catch(_){}return returned;
   }
   entry.uploadLocalFile.wrapper=observedLocal;entry.getExportImage.wrapper=observedExport;
   try{Object.defineProperty(engine,'uploadLocalFile',localDescriptor?{...localDescriptor,value:observedLocal}:
     {value:observedLocal,writable:true,configurable:true});
     Object.defineProperty(engine,'getExportImage',exportDescriptor?{...exportDescriptor,value:observedExport}:
     {value:observedExport,writable:true,configurable:true});state.engines.set(engine,entry);
   }catch(_){if(engine.uploadLocalFile===observedLocal){if(localDescriptor)Object.defineProperty(engine,'uploadLocalFile',localDescriptor);else delete engine.uploadLocalFile;}return null;}
   return entry;
 }
 function inspectEditor(){if(!state.active?.cover||!state.selection)return;
   const item=editor(),selection=state.selection,scope=state.active;
   if(!item||!scope||!selection||selection.modal!==item.modal||!current(scope,selection))return;
   const cropper=item.cropper;if(state.editors.has(cropper))return;
   const original=cropper.getCroppedCanvas,descriptor=Object.getOwnPropertyDescriptor(cropper,'getCroppedCanvas');
   if(typeof original!=='function'||(descriptor&&(!('value'in descriptor)||!descriptor.writable)))return;
   const entry={...item,scope,selection,original,descriptor};
   entry.proof=track(Promise.all([selection.proof,
     sha(new TextEncoder().encode(Reflect.apply(fnSource,original,[]))),
     Reflect.apply(nativeFetch,window,[item.url]).then(response=>{if(!response.ok)throw Error('blob unavailable');return response.blob();}).then(fingerprint)
   ]).then(([source,signature,origin])=>{
     if(!source||signature!==methodSHA||origin.sha256!==scope.cover.sha256||origin.size!==selection.size||
        !current(scope,selection)||cropper.originalUrl!==item.url)throw Error('unbound source');return origin;
   }).catch(()=>null),scope);
   function observedCanvas(...args){
     const returned=Reflect.apply(original,this,args);
     try{const ticket=state.ticket,itemNow=editor();
       if(this===cropper&&ticket?.open&&ticket.selection===selection&&current(scope,selection)&&
          itemNow?.cropper===cropper&&itemNow.url===entry.url&&ticket.modal===itemNow.modal)
         wrapCanvas(returned,entry,ticket);
     }catch(_){}return returned;
   }
   entry.wrapper=observedCanvas;
   try{Object.defineProperty(cropper,'getCroppedCanvas',descriptor?{...descriptor,value:observedCanvas}:
       {value:observedCanvas,writable:true,configurable:true});state.editors.set(cropper,entry);}catch(_){}
 }
 function clicked(event){if(state.events.has(event))return;state.events.add(event);
   const m=modal(),selection=state.selection,scope=state.active;if(!m||!selection||!current(scope,selection))return;
   let confirm=false,cancel=false;try{for(const node of Reflect.apply(nativePath,event,[])){
     if(!(node instanceof Element)||!m.contains(node))continue;
     if(node.matches('.cancelBtn,.d-modal-close'))cancel=true;
     if(mojito(m)?node.matches('.editor-panel .bottom .bottom-content > button.mojito-button')&&node.textContent.trim()==='完成'&&!node.disabled:
       node.matches('.btn-confirm')&&!node.disabled)confirm=true;
   }}catch(_){return;}
   if(cancel){invalidate();return;}if(!confirm)return;
   if(mojito(m)){const entry=inspectMojito();if(!entry?.ready||selection.modal!==m)return;
     state.ticket={kind:'mojito',id:String(++state.sequence),scope,selection,modal:m,open:true,
       transform_at:Date.now()/1000,used:false};return;}
   inspectEditor();const item=editor();if(!item||item.modal!==selection.modal)return;
   const ticket={id:String(++state.sequence),scope,selection,modal:m,open:true,transform_at:Date.now()/1000,used:false};
   state.ticket=ticket;queueMicrotask(()=>{ticket.open=false;});
 }
 function jpeg(data){if(typeof data!=='string'||!/^data:image\/jpeg;base64,/.test(data))throw Error('not JPEG');
   const raw=atob(data.slice(data.indexOf(',')+1));if(!raw.length||raw.length>67108864)throw Error('unsupported JPEG');
   const bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
   return {bytes,size:bytes.length};}
 function wrapCanvas(canvas,entry,ticket){try{Reflect.apply(canvasWidth,canvas,[]);}catch(_){return;}
   if(state.canvases.has(canvas)||ticket.used)return;
   const original=canvas.toDataURL,descriptor=Object.getOwnPropertyDescriptor(canvas,'toDataURL');
   if(original!==canvasEncode||(descriptor&&(!('value'in descriptor)||!descriptor.writable)))return;
   function observedEncode(...args){
     const returned=Reflect.apply(original,this,args);
     try{if(this===canvas&&ticket.open&&!ticket.used&&args[0]==='image/jpeg'&&current(entry.scope,entry.selection)){
       const encoded=jpeg(returned);ticket.used=true;
       const transform={entry,ticket,size:encoded.size,upload:null,reported:false};state.transform=transform;
       transform.proof=track(Promise.all([entry.proof,sha(encoded.bytes)]).then(([origin,derived_sha256])=>{
         if(!origin||!current(entry.scope,entry.selection))throw Error('obsolete transformation');
         return {origin_sha256:origin.sha256,derived_sha256};
       }).catch(()=>null),entry.scope);
     }}catch(_){}return returned;
   }
   const saved={descriptor,original,wrapper:observedEncode};
   try{Object.defineProperty(canvas,'toDataURL',descriptor?{...descriptor,value:observedEncode}:
     {value:observedEncode,writable:true,configurable:true});state.canvases.set(canvas,saved);}catch(_){}
 }
 function bind(){for(const root of roots()){if(state.roots.has(root))continue;state.roots.add(root);
   Reflect.apply(addListener,root,['change',chosen,true]);Reflect.apply(addListener,root,['click',clicked,true]);}}
 // A currently open creator page may retain the previous video-only observer.
 // Observe the same existing image SDK independently; never recreate its SDK.
 function inspectImageSDK(){const manager=window._publisher?.uploader;
   if(!state.active?.cover||!manager||typeof manager.getUploader!=='function'||typeof manager.createUploader!=='function')return;
   function wrap(uploader){if(!uploader||state.uploaders.has(uploader))return;
     const original=uploader.upload,descriptor=Object.getOwnPropertyDescriptor(uploader,'upload');
     if(typeof original!=='function'||(descriptor&&(!('value'in descriptor)||!descriptor.writable)))return;
     function observedImage(...args){let capture=null;
       try{if(this===uploader&&window._publisher?.uploader===manager&&state.active)
         capture=state.captureUpload(args[0],state.active);}catch(_){}
       const returned=Reflect.apply(original,this,args);
       try{if(capture)state.observeUpload(capture,returned);}catch(_){}return returned;
     }
     try{Object.defineProperty(uploader,'upload',descriptor?{...descriptor,value:observedImage}:
       {value:observedImage,writable:true,configurable:true});state.uploaders.set(uploader,{original,descriptor,wrapper:observedImage});}catch(_){}
   }
   if(!state.managers.has(manager)){
     const original=manager.createUploader,descriptor=Object.getOwnPropertyDescriptor(manager,'createUploader');
     if(typeof original==='function'&&(!descriptor||('value'in descriptor&&descriptor.writable))){
       function observedCreate(...args){const returned=Reflect.apply(original,this,args);
         try{if(this===manager&&args[0]==='image-uploader'&&window._publisher?.uploader===manager)wrap(returned);}catch(_){}
         return returned;
       }
       try{Object.defineProperty(manager,'createUploader',descriptor?{...descriptor,value:observedCreate}:
         {value:observedCreate,writable:true,configurable:true});state.managers.set(manager,{original,descriptor,wrapper:observedCreate});}catch(_){}
     }
   }
   try{wrap(Reflect.apply(manager.getUploader,manager,['image-uploader']));}catch(_){}
 }
 function appliedId(){try{const p=window.publish,rs=roots().flatMap(r=>elements(r,'#creator-publish-dom'));
   if(!p?.app||rs.length!==1||p.el!==rs[0]||!p.el.isConnected||typeof p.getDraftState!=='function')return null;
   const draftCover=unref(p.getDraftState()?.cover);
   // Official effective-cover resolver (publish-vue char 293949) prefers the
   // context cover when it has a fileid. Read the already mounted facade only.
   const context=typeof p.getContextState==='function'?unref(p.getContextState()?.coverContext):null;
   const contextCover=unref(context?.cover);
   const cover=unref(contextCover?.fileid)?contextCover:draftCover;
   return cover?.selectionSource==='upload'?unref(cover.fileid):null;
 }catch(_){return null;}}
 async function deliver(){const transform=state.transform,scope=state.active;if(!transform||transform.reported||!transform.upload)return;
   const {entry,ticket,upload}=transform;
   if(!current(scope,entry.selection)||entry.scope!==scope||!upload.result||appliedId()!==upload.result.fileId)return;
   const proof=await transform.proof,file=await upload.proof;if(!proof||!file||!current(scope,entry.selection)||
      state.transform!==transform||file.sha256!==proof.derived_sha256||file.size!==transform.size||
      appliedId()!==upload.result.fileId)return;
   const applied_at=Date.now()/1000;
   if(typeof window.__sauXHSCoverUploaded!=='function')return;transform.reported=true;
   await window.__sauXHSCoverUploaded({v:1,job_id:scope.job_id,receipt_nonce:scope.receipt_nonce,
     observer_nonce:scope.observer_nonce,slot:scope.cover.slot,revision:String(entry.selection.revision),
     invocation_id:ticket.id,uploader:'image-uploader',origin_name:entry.selection.name,origin_size:entry.selection.size,
     origin_type:entry.selection.type,origin_sha256:proof.origin_sha256,derived_sha256:proof.derived_sha256,
     derived_size:transform.size,derived_type:'image/jpeg',file_id:upload.result.fileId,
     selected_at:entry.selection.selected_at,transform_at:ticket.transform_at,
     started_at:upload.started_at,completed_at:upload.completed_at,applied_at});
 }
 state.captureUpload=(file,sdkScope)=>{let transform=state.exportedFiles.get(file)||state.transform;const scope=state.active;
   if(!scope||!transform||transform.upload||sdkScope.job_id!==scope.job_id||sdkScope.receipt_nonce!==scope.receipt_nonce||
      sdkScope.observer_nonce!==scope.observer_nonce||!current(scope,transform.entry.selection))return null;
   try{if(transform.kind==='mojito'){
       if(file!==transform.file||window.zeusEngine!==transform.engine||transform.ticket.kind!=='mojito'||
          !transform.ticket.open)return null;
     }else if(Reflect.apply(fileName,file,[])!=='cover.jpeg')return null;
     const size=Reflect.apply(blobSize,file,[]);if(size!==transform.size)return null;
     state.transform=transform;
     const upload={scope,transform,started_at:Date.now()/1000,result:null};transform.upload=upload;
     upload.proof=track(fingerprint(file).catch(()=>null),scope);return upload;
   }catch(_){return null;}
 };
 state.observeUpload=(upload,returned)=>{if(!(returned instanceof Promise))return;
   try{Reflect.apply(nativeThen,returned,[result=>{if(!current(upload.scope,upload.transform.entry.selection)||
       !result||typeof result.fileId!=='string'||!result.fileId||result.fileId.length>2048||/[\x00-\x1f\x7f]/.test(result.fileId)||
       typeof result.previewUrl!=='string'||!result.previewUrl)return;
     upload.result={fileId:result.fileId};upload.completed_at=Date.now()/1000;
     track(deliver().catch(()=>{}),upload.scope);
   },()=>{}]);}catch(_){}
 };
 state.configure=scope=>{if(!scope?.job_id||!scope.receipt_nonce||!scope.observer_nonce)return false;
   if(state.active?.observer_nonce!==scope.observer_nonce){invalidate();restore(state.editors,'getCroppedCanvas');restore(state.canvases,'toDataURL');restoreEngines();
     state.active=Object.freeze({...scope});}
   if(!scope.cover){if(state.timer){clearInterval(state.timer);state.timer=null;}return true;}
   bind();inspectImageSDK();inspectMojito();if(!state.timer)state.timer=setInterval(()=>{try{bind();inspectEditor();inspectMojito();inspectImageSDK();track(deliver().catch(()=>{}),state.active);}catch(_){}},100);
   return true;
 };
 state.ensureReady=async nonce=>{if(state.active?.observer_nonce!==nonce||!state.active.cover)return false;
   bind();inspectImageSDK();const m=modal();if(!m)return false;
   if(mojito(m)){const entry=inspectMojito();return !!entry&&await entry.proof===true&&entry.ready&&window.zeusEngine===entry.engine;}
   return elements(m,'.upload-wrapper input[type="file"]').length===1;
 };
 state.selectionReady=async nonce=>{const scope=state.active,selection=state.selection,m=modal();
   if(scope?.observer_nonce!==nonce||!selection||!current(scope,selection)||selection.modal!==m)return false;
   if(!mojito(m))return false;
   const entry=inspectMojito(),source=entry?.ready&&imagePath(entry.engine);
   const local=source&&state.localPaths.get(source.path);
   if(!local||local.selection!==selection||local.scope!==scope||state.invalidPaths.has(source.path))return false;
   const proof=await local.proof;
   return !!proof&&current(scope,selection)&&imagePath(entry.engine)?.path===source.path;
 };
 state.flush=async nonce=>{if(state.active?.observer_nonce!==nonce)return false;bind();inspectEditor();inspectMojito();inspectImageSDK();
   for(let round=0;round<4;round++){const pending=[...state.pending].filter(e=>e.scope===state.active).map(e=>e.promise);
     if(!pending.length)break;await Promise.allSettled(pending);}await deliver();return true;
 };
 state.status=nonce=>state.active?.observer_nonce===nonce?
   {revision:String(state.revision),selection:!!state.selection,applied_id:appliedId()}:null;
 state.close=nonce=>{if(state.active?.observer_nonce!==nonce)return false;invalidate();state.active=null;
   if(state.timer){clearInterval(state.timer);state.timer=null;}restore(state.editors,'getCroppedCanvas');restore(state.canvases,'toDataURL');
   restoreEngines();restore(state.uploaders,'upload');restore(state.managers,'createUploader');return true;};
 window.__sauXHSCoverObserver=state;
})()"""


async def _evaluate_main(frame, script):
    try:
        return await frame.evaluate(script, isolated_context=False)
    except TypeError:
        return await frame.evaluate(script)


XHS_COMPOSER_MEDIA_SCRIPT = r"""() => {
  try {
    // Official facade is mounted by project-publish-vue at char 1280399.
    // These existing getters (1276082/1276255) only read their Pinia state.
    const publisher = window.publish;
    const roots = document.querySelectorAll('#creator-publish-dom');
    if (roots.length !== 1 || !publisher || !publisher.app ||
        publisher.el !== roots[0] || !roots[0].isConnected ||
        typeof publisher.getDraftState !== 'function' ||
        typeof publisher.getPublishState !== 'function') return null;
    const unref = value => value && value.__v_isRef === true ? value.value : value;
    const publish = publisher.getPublishState();
    // Video = 0 in official module 79037; image/audio state is not resumable.
    if (!publish || unref(publish.publishType) !== 0) return null;
    const draft = publisher.getDraftState();
    const video = draft && unref(draft.video);
    if (!video || typeof video !== 'object' || Array.isArray(video)) return null;
    const fileId = unref(video.fileid);
    const transcodeValue = unref(video.transcodeVideoFileId);
    const transcodeId = transcodeValue == null ? '' : transcodeValue;
    const validId = value => typeof value === 'string' &&
      value.length <= 512 && !/[\s\x00-\x1f\x7f]/.test(value);
    if (!validId(fileId) || !validId(transcodeId)) return null;
    // The official draft has a single video object. Its transcoded rendition
    // belongs to that video; it is not a second selected video.
    if (!fileId) return transcodeId ? null : {
      source: 'platform_composer', video_count: 0, video_ids: []
    };
    return {
      source: 'platform_composer', video_count: 1,
      video_ids: [...new Set([fileId, transcodeId].filter(Boolean))]
    };
  } catch (_) { return null; }
}"""


async def read_xhs_composer_media(page):
    """Read IDs from the sole already-mounted video composer, or return None.

    Never initializes an app/store/SDK, reads File bytes, or returns the rest of
    the draft. This getter is for current-media checkpoint identity only; it
    does not establish upload completion or bind an unobserved resource to a
    local file's SHA256.
    """
    try:
        result = await _evaluate_main(page.main_frame, XHS_COMPOSER_MEDIA_SCRIPT)
    except Exception:
        return None
    if not isinstance(result, dict) or set(result) != {'source', 'video_count', 'video_ids'}:
        return None
    count, ids = result.get('video_count'), result.get('video_ids')
    if (result.get('source') != 'platform_composer' or type(count) is not int or
            count not in (0, 1) or not isinstance(ids, list) or len(ids) > 2):
        return None
    if any(not isinstance(value, str) or not value or len(value) > 512 or
           re.search(r'[\s\x00-\x1f\x7f]', value) for value in ids):
        return None
    if len(set(ids)) != len(ids) or bool(count) != bool(ids):
        return None
    return {'source': 'platform_composer', 'video_count': count, 'video_ids': list(ids)}


def _bounded_token(value, maximum=128):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,' + str(maximum) + '}', value))


class _MainFrameTransport:
    """Bind observations to Chrome's actual top-frame default context.

    Patchright 1.58.2 synthesizes execution-context/frame associations while
    evaluating iframe defaults (frames.js _context). On a reused CDP target,
    its Page binding source can consequently name a child for a top-window
    call. This private CDP session uses native contextCreated auxData instead;
    an unknown, destroyed, isolated or child context cannot become evidence.
    """
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
        self.raw_name = '__sauXHSSDKTransport_' + observer.observer_nonce
        self.lock = asyncio.Lock()
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
            self.contexts[context['id']] = dict(context.get('auxData') or {})

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
        return (not self.closed and
                ident == self.context_id and self.bridge_id is not None and
                native.get('isDefault') is True and native.get('frameId') == self.root_frame and
                candidates == [ident])

    async def prepare(self):
        async with self.lock:
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
            self.context_id, self.bridge_id, self.channel_nonce = None, None, None
            channel_nonce = secrets.token_hex(24)
            await self.client.send('Runtime.addBinding', {'name': self.raw_name, 'executionContextId': ident})
            # The return object, containing delivery callbacks, stays private
            # in the CDP session. Public functions transmit metadata only and
            # return an acknowledgement Promise; SDK Promises are unchanged.
            expression = r"""(() => {
                if(window!==top)return null;
                const raw=window[RAW], nonce=NONCE, channel=CHANNEL, pending=new Map();
                if(typeof raw!=='function')return null;
                let closed=false, sequence=0;
                function send(kind,report){
                    if(closed)return Promise.resolve(false);
                    return new Promise(resolve=>{
                        const key=String(++sequence);pending.set(key,resolve);
                        try {raw(JSON.stringify({v:1,kind,sequence:key,observer_nonce:nonce,channel_nonce:channel,report}));}
                        catch(_){pending.delete(key);resolve(false);}
                    });
                }
                const video=report=>send('video',report), cover=report=>send('cover',report);
                window[PUBLIC]=video;window.__sauXHSCoverUploaded=cover;
                return {
                    deliver(key,accepted){const resolve=pending.get(key);if(!resolve)return false;
                        pending.delete(key);resolve(accepted===true);return true;},
                    close(){closed=true;for(const resolve of pending.values())resolve(false);pending.clear();}
                };
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
            raise RuntimeError('xhs_observer_main_context_unavailable')
        ident = self.context_id
        result = await self.client.send('Runtime.evaluate', {'expression': expression,
            'contextId': ident, 'awaitPromise': True, 'returnByValue': True})
        if result.get('exceptionDetails') or not self._canonical(ident):
            raise RuntimeError('xhs_observer_main_context_changed')
        return result.get('result', {}).get('value')

    def _called(self, event):
        if event.get('name') != self.raw_name:
            return
        task = asyncio.create_task(self._dispatch(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _dispatch(self, event):
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
                message['kind'] not in ('video', 'cover') or
                not isinstance(message['sequence'], str) or not re.fullmatch(r'[1-9][0-9]{0,15}', message['sequence'])):
            return False
        source = {'page': self.page, 'frame': self.page.main_frame}
        accepted = (self.observer._receive_video(source, message['report']) if message['kind'] == 'video'
                    else self.observer.accept_cover(source, message['report']))
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


class XHSSDKObserver:
    """Current-session, private video correlations for DraftEvidenceSession."""
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.job_id = session.expected.get('job_id')
        self.receipt_nonce = session.expected.get('receipt_nonce')
        self.observer_nonce = secrets.token_hex(24)
        self.binding_name = '__sauXHSSDKUploaded_' + self.observer_nonce
        self.binding_installed, self.transport_ready = False, False
        self.started_at = time.time()
        self.closed, self.available = False, False
        self.records, self.invalid_invocations, self.tasks = {}, set(), set()
        self.cover_records, self.cover_pending, self.cover_invalid = {}, {}, set()
        self.cover_status = None
        self.received_count, self.accepted_count = 0, 0
        self._last_diagnostics = {}
        self.navigation_handler = self._navigated
        self.transport = _MainFrameTransport(self)

    @property
    def scope(self):
        return {'job_id': self.job_id, 'receipt_nonce': self.receipt_nonce,
                'observer_nonce': self.observer_nonce}

    @property
    def browser_scope(self):
        return {**self.scope, 'binding_name': self.binding_name}

    def _is_current(self):
        return (not self.closed and not self.session.closed and
                getattr(self.page, '_sau_draft_evidence', None) is self.session and
                getattr(self.session, 'xhs_observer', None) is self)

    @property
    def cover_scope(self):
        # XHS old editor has one main cover. Its mapped `cover` is authoritative;
        # portrait/landscape aliases do not make several independent uploads.
        covers = {key: value for key, value in self.session.assets.items() if value.get('kind') == 'cover'}
        if 'cover' in covers:
            slot, asset = 'cover', covers['cover']
        elif len(covers) == 1:
            slot, asset = next(iter(covers.items()))
        else:
            return {**self.scope, 'cover': None}
        if self.session.expected.get('cover_sha256', {}).get(slot) != asset.get('sha256'):
            return {**self.scope, 'cover': None}
        return {**self.scope, 'cover': {'slot': slot, **{key: asset.get(key) for key in ('name', 'size', 'sha256')}}}

    async def initialize(self):
        if not _bounded_token(self.job_id, 80) or not _bounded_token(self.receipt_nonce):
            return
        if not getattr(self.page, '_sau_xhs_sdk_script', False):
            await self.page.add_init_script(SDK_OBSERVER_SCRIPT)
            self.page._sau_xhs_sdk_script = True
        if not getattr(self.page, '_sau_xhs_cover_binding', False):
            await self.page.add_init_script(COVER_OBSERVER_SCRIPT)
            self.page._sau_xhs_cover_binding = True
        self.page.on('framenavigated', self.navigation_handler)
        await self._configure(self.page.main_frame)

    def _receive_video(self, source, report):
        # Even a correctly attributed transport cannot cross task boundaries.
        if isinstance(report, dict) and report.get('probe') is True:
            accepted = (set(report) == {'v', 'probe', *self.scope} and
                type(report.get('v')) is int and report['v'] == 1 and
                self._is_current() and isinstance(source, dict) and
                source.get('page') is self.page and source.get('frame') is self.page.main_frame and
                all(report.get(key) == value for key, value in self.scope.items()))
            self._emit_diagnostic('xhs_sdk_callback', probe=True,
                observer_current=self._is_current(), source_mapping=isinstance(source, dict),
                source_page_match=isinstance(source, dict) and source.get('page') is self.page,
                source_frame_match=isinstance(source, dict) and source.get('frame') is self.page.main_frame,
                scope_job_match=report.get('job_id') == self.job_id,
                scope_receipt_match=report.get('receipt_nonce') == self.receipt_nonce,
                scope_observer_match=report.get('observer_nonce') == self.observer_nonce,
                accepted=accepted)
            return bool(accepted)
        return bool(self.accept(source, report))

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
            self.binding_installed = True
            await self.transport.evaluate(SDK_OBSERVER_SCRIPT)
            await self.transport.evaluate(COVER_OBSERVER_SCRIPT)
            if self._is_current():
                # This is a read-only transport handshake, never media proof.
                # Do not import a video while its report channel cannot reach
                # the exact current Python page/frame/nonce.
                probe = {'v': 1, 'probe': True, **self.scope}
                self.transport_ready = bool(await asyncio.wait_for(self.transport.evaluate(
                    '(async()=>typeof window[' + json.dumps(self.binding_name) + "]==='function' && await window[" +
                    json.dumps(self.binding_name) + '](' + json.dumps(probe) + ')===true)()'), timeout=2))
                self.available = bool(await self.transport.evaluate(
                    'window.__sauXHSSDKObserver?.api_version===4 && window.__sauXHSSDKObserver.configure(' + json.dumps(self.browser_scope) + ')'))
                await self.transport.evaluate(
                    'window.__sauXHSCoverObserver.configure(' + json.dumps(self.cover_scope) + ')')
                await self._sdk_diagnostic('xhs_sdk_configured')
        except Exception:
            self.transport_ready = False
            # Navigation or unsupported runtime leaves no positive evidence.
            pass

    def accept(self, source, report):
        accepted = self._accept(source, report)
        # Print only current-task counts and equality booleans. No report value,
        # filename, nonce, account, resource ID or content enters stdout.
        try:
            self.received_count += 1
            self.accepted_count += int(accepted)
            asset = self.session.assets.get('video', {})
            values = report if isinstance(report, dict) else {}
            self._emit_diagnostic('xhs_sdk_received', received_count=self.received_count,
                    accepted_count=self.accepted_count, accepted=accepted,
                    observer_current=self._is_current(), observer_closed=self.closed,
                    session_closed=bool(self.session.closed),
                    page_session_match=getattr(self.page, '_sau_draft_evidence', None) is self.session,
                    session_observer_match=getattr(self.session, 'xhs_observer', None) is self,
                    source_mapping=isinstance(source, dict), report_mapping=isinstance(report, dict),
                    source_page_match=isinstance(source, dict) and source.get('page') is self.page,
                    source_frame_match=isinstance(source, dict) and source.get('frame') is self.page.main_frame,
                    scope_job_match=values.get('job_id') == self.job_id,
                    scope_receipt_match=values.get('receipt_nonce') == self.receipt_nonce,
                    scope_observer_match=values.get('observer_nonce') == self.observer_nonce,
                    asset_name_match=values.get('name') == asset.get('name'),
                    asset_size_match=type(values.get('size')) is int and values.get('size') == asset.get('size'),
                    asset_hash_match=values.get('sha256') == asset.get('sha256') == self.session.expected.get('video_sha256'))
        except Exception:
            pass
        return accepted

    def _emit_diagnostic(self, event, **values):
        events = {'xhs_sdk_configured', 'xhs_sdk_ready', 'xhs_sdk_invoked', 'xhs_sdk_received', 'xhs_sdk_callback'}
        allowed = {'manager_available', 'create_wrapped', 'existing_video_count', 'wrapped_video_count', 'ready',
                   'invoked_count', 'native_file', 'promise', 'received_count', 'accepted_count', 'accepted',
                   'asset_name_match', 'asset_size_match', 'asset_hash_match',
                   'handler_attached_count', 'handler_failed_count', 'fulfilled_count', 'rejected_count',
                   'fulfilled_scope_match', 'rejected_scope_match', 'result_object',
                   'file_id_present', 'file_id_string', 'file_id_valid',
                   'digest_ready_count', 'digest_failed_count', 'digest_scope_match',
                   'binding_present', 'binding_scope_match', 'binding_called_count',
                   'binding_fulfilled_count', 'binding_rejected_count', 'binding_accepted_count',
                   'binding_not_accepted_count', 'observer_error_count', 'closed', 'binding_probe_ready',
                   'probe', 'observer_current', 'observer_closed', 'session_closed', 'page_session_match',
                   'session_observer_match', 'source_mapping', 'report_mapping', 'source_page_match',
                   'source_frame_match', 'scope_job_match', 'scope_receipt_match', 'scope_observer_match'}
        if event not in events:
            return
        safe = {key: value for key, value in values.items() if key in allowed and
                (type(value) is bool or type(value) is int and 0 <= value <= 2**53 - 1)}
        if self._last_diagnostics.get(event) == safe:
            return
        self._last_diagnostics[event] = safe
        try:
            print('DRAFT_DIAGNOSTIC=' + json.dumps({'event': event, **safe}), flush=True)
        except Exception:
            pass

    async def _sdk_diagnostic(self, event):
        if not self._is_current():
            return None
        try:
            status = await self.transport.evaluate(
                'window.__sauXHSSDKObserver?.status(' + json.dumps(self.observer_nonce) + ')')
            if isinstance(status, dict):
                status['binding_probe_ready'] = self.transport_ready
                self._emit_diagnostic(event, **status)
                return status
        except Exception:
            pass
        return None

    async def ensure_ready(self, timeout=5):
        """Inspect and wrap the existing manager before native video selection.

        Returns False on an unavailable/unsupported SDK. It never creates an
        uploader, starts an upload, invokes content methods or changes evidence
        acceptance. The adapter must stop before importing on False.
        """
        if not self._is_current() or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            return False
        await self._configure(self.page.main_frame)
        if not self.transport_ready:
            self.available = False
            self._emit_diagnostic('xhs_sdk_ready', ready=False, binding_probe_ready=False)
            return False
        deadline = asyncio.get_running_loop().time() + timeout
        status = None
        while self._is_current():
            try:
                status = await self.transport.evaluate(
                    'window.__sauXHSSDKObserver?.ensureReady(' + json.dumps(self.observer_nonce) + ')')
                if isinstance(status, dict) and status.get('ready') is True:
                    self.available = True
                    self._emit_diagnostic('xhs_sdk_ready', **status)
                    return True
            except Exception:
                pass
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(min(.05, max(0, deadline - asyncio.get_running_loop().time())))
        if isinstance(status, dict):
            self._emit_diagnostic('xhs_sdk_ready', **status)
        else:
            self._emit_diagnostic('xhs_sdk_ready', ready=False, manager_available=False)
        return False

    async def ensure_cover_ready(self, timeout=5):
        """Require the existing supported cover editor before native selection.

        This inspects/wraps its already mounted engine and verifies the pinned
        official export method. It never constructs an editor, invokes an
        export, changes a ratio, selects a file or starts an SDK upload.
        """
        if (not self._is_current() or not self.cover_scope.get('cover') or
                type(timeout) not in (int, float) or not math.isfinite(timeout) or
                not 0 < timeout <= 30):
            return False
        if not self.transport_ready:
            await self._configure(self.page.main_frame)
        if not self.transport_ready:
            return False
        deadline = asyncio.get_running_loop().time() + timeout
        while self._is_current():
            try:
                ready = await asyncio.wait_for(self.transport.evaluate(
                    'window.__sauXHSCoverObserver?.ensureReady(' + json.dumps(self.observer_nonce) + ')'),
                    timeout=max(.05, deadline - asyncio.get_running_loop().time()))
                if ready is True:
                    return True
            except Exception:
                pass
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(.05, remaining))
        return False

    async def ensure_cover_selection_ready(self, timeout=15):
        """Wait for this native source to be the one current Mojito image layer."""
        if (not self._is_current() or not self.transport_ready or
                type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30):
            return False
        deadline = asyncio.get_running_loop().time() + timeout
        while self._is_current():
            try:
                if await asyncio.wait_for(self.transport.evaluate(
                    'window.__sauXHSCoverObserver?.selectionReady(' + json.dumps(self.observer_nonce) + ')'),
                    timeout=max(.05, deadline - asyncio.get_running_loop().time())) is True:
                    return True
            except Exception:
                pass
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(.05, remaining))
        return False

    def _accept(self, source, report):
        if (not self._is_current() or not isinstance(source, dict) or
                source.get('page', self.page) is not self.page or
                source.get('frame') is not self.page.main_frame or not isinstance(report, dict)):
            return False
        allowed = {'v', 'job_id', 'receipt_nonce', 'observer_nonce', 'invocation_id', 'uploader',
                   'name', 'size', 'type', 'sha256', 'file_id', 'started_at', 'completed_at'}
        if set(report) != allowed or type(report.get('v')) is not int or report['v'] != 1:
            return False
        if any(report.get(key) != value for key, value in self.scope.items()):
            return False
        if report.get('uploader') not in ('video-uploader', 'not-transcode-video-uploader'):
            return False
        if not isinstance(report.get('invocation_id'), str) or not re.fullmatch(r'[1-9][0-9]{0,15}', report['invocation_id']):
            return False
        if not isinstance(report.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', report['sha256']):
            return False
        if type(report.get('size')) is not int or not 0 < report['size'] <= 2**53 - 1:
            return False
        for key, maximum in (('name', 1024), ('type', 128), ('file_id', 2048)):
            value = report.get(key)
            if not isinstance(value, str) or len(value) > maximum or re.search(r'[\x00-\x1f\x7f]', value):
                return False
        if not report['name'] or not report['file_id'] or report['file_id'] != report['file_id'].strip():
            return False
        for key in ('started_at', 'completed_at'):
            value = report.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                return False
        if not self.started_at <= report['started_at'] <= report['completed_at'] <= time.time() + 5:
            return False
        asset = self.session.assets.get('video', {})
        if (asset.get('kind') != 'video' or
                (report['name'], report['size'], report['sha256']) !=
                (asset.get('name'), asset.get('size'), asset.get('sha256')) or
                report['sha256'] != self.session.expected.get('video_sha256')):
            return False
        key = report['invocation_id']
        if key in self.invalid_invocations:
            return False
        if key in self.records and self.records[key] != report:
            self.records.pop(key, None)
            self.invalid_invocations.add(key)
            return False
        self.records[key] = dict(report)
        return True

    def _selected_cover(self, report):
        images = [item for item in getattr(self.session, 'files', []) if isinstance(item, dict) and
                  isinstance(item.get('type'), str) and item['type'].startswith('image/') and
                  type(item.get('selected_at')) in (int, float) and math.isfinite(item['selected_at'])]
        if not images:
            return False
        # Hash deliveries can finish out of order. Selection time, rather than
        # callback append order, determines the current native source event.
        latest = max(images, key=lambda item: item['selected_at'])
        stamp = latest.get('selected_at')
        return (type(stamp) in (int, float) and math.isfinite(stamp) and
                abs(stamp - report['selected_at']) <= .1 and
                (latest.get('name'), latest.get('size'), latest.get('type'), latest.get('sha256')) ==
                (report['origin_name'], report['origin_size'], report['origin_type'], report['origin_sha256']))

    def accept_cover(self, source, report):
        if (not self._is_current() or not isinstance(source, dict) or
                source.get('page', self.page) is not self.page or
                source.get('frame') is not self.page.main_frame or not isinstance(report, dict)):
            return False
        allowed = {'v', 'job_id', 'receipt_nonce', 'observer_nonce', 'slot', 'revision', 'invocation_id',
                   'uploader', 'origin_name', 'origin_size', 'origin_type', 'origin_sha256',
                   'derived_sha256', 'derived_size', 'derived_type', 'file_id', 'selected_at',
                   'transform_at', 'started_at', 'completed_at', 'applied_at'}
        if set(report) != allowed or type(report.get('v')) is not int or report['v'] != 1:
            return False
        if any(report.get(key) != value for key, value in self.scope.items()):
            return False
        cover = self.cover_scope.get('cover')
        if (not cover or report.get('slot') != cover['slot'] or report.get('uploader') != 'image-uploader' or
                (report.get('origin_name'), report.get('origin_size'), report.get('origin_sha256')) !=
                (cover['name'], cover['size'], cover['sha256']) or report.get('derived_type') != 'image/jpeg'):
            return False
        for key in ('revision', 'invocation_id'):
            if not isinstance(report.get(key), str) or not re.fullmatch(r'[1-9][0-9]{0,15}', report[key]):
                return False
        for key in ('origin_sha256', 'derived_sha256'):
            if not isinstance(report.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', report[key]):
                return False
        for key in ('origin_size', 'derived_size'):
            if type(report.get(key)) is not int or not 0 < report[key] <= 67108864:
                return False
        for key, maximum in (('origin_name', 1024), ('origin_type', 128), ('file_id', 2048)):
            value = report.get(key)
            if not isinstance(value, str) or not value or len(value) > maximum or re.search(r'[\x00-\x1f\x7f]', value):
                return False
        if not report['origin_type'].startswith('image/') or report['file_id'] != report['file_id'].strip():
            return False
        times = [report.get(key) for key in ('selected_at', 'transform_at', 'started_at', 'completed_at', 'applied_at')]
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in times) or
                not self.started_at <= times[0] <= times[1] <= times[2] <= times[3] <= times[4] <= time.time() + 5):
            return False
        key = report['invocation_id']
        if key in self.cover_invalid:
            return False
        prior = self.cover_pending.get(key) or self.cover_records.get(key)
        if prior and prior != report:
            self.cover_records.pop(key, None)
            self.cover_pending.pop(key, None)
            self.cover_invalid.add(key)
            return False
        self.cover_pending[key] = dict(report)
        if not self._selected_cover(report):
            return False
        self.cover_records[key] = dict(report)
        self.cover_pending.pop(key, None)
        return True

    def cover_upload_links(self):
        if not self._is_current() or not isinstance(self.cover_status, dict):
            return []
        return [{'kind': 'cover', 'sha256': record['origin_sha256'], 'ids': [record['file_id']], 'complete': True}
                for record in self.cover_records.values()
                if self.cover_status.get('selection') is True and
                self.cover_status.get('revision') == record['revision'] and
                self.cover_status.get('applied_id') == record['file_id'] and self._selected_cover(record)]

    def cover_transforms(self):
        valid = {link['ids'][0] for link in self.cover_upload_links()}
        return [{key: record[key] for key in ('slot', 'origin_sha256', 'derived_sha256', 'derived_size', 'revision', 'invocation_id')}
                for record in self.cover_records.values() if record['file_id'] in valid]

    def upload_links(self):
        if not self._is_current():
            return []
        return ([{'kind': 'video', 'sha256': record['sha256'], 'ids': [record['file_id']], 'complete': True}
                 for record in self.records.values()] + self.cover_upload_links())

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if not self._is_current():
            return
        try:
            await self.transport.evaluate(
                'window.__sauXHSSDKObserver?.flush(' + json.dumps(self.observer_nonce) + ')')
            await self._sdk_diagnostic('xhs_sdk_invoked')
            await self.transport.evaluate(
                'window.__sauXHSCoverObserver?.flush(' + json.dumps(self.observer_nonce) + ')')
            self.cover_status = await self.transport.evaluate(
                'window.__sauXHSCoverObserver?.status(' + json.dumps(self.observer_nonce) + ')')
            for report in tuple(self.cover_pending.values()):
                self.accept_cover({'frame': self.page.main_frame}, report)
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
                'window.__sauXHSCoverObserver?.close(' + json.dumps(self.observer_nonce) + ')')
            await self.transport.evaluate(
                'window.__sauXHSSDKObserver?.close(' + json.dumps(self.observer_nonce) + ')')
        except Exception:
            pass
        finally:
            self.closed = True
            self.transport_ready = False
            await self.transport.close()


async def install_xhs_observer(session):
    """Call after assets are hashed, before the creator UI selects the video.

    Integration: keep session.xhs_observer; drain it from session.drain(), append
    its upload_links() from session.upload_links(), and close it from close().
    Checkpoints must copy the combined upload_links(), including these SDK links.
    """
    if session.platform != 'xiaohongshu':
        return None
    observer = XHSSDKObserver(session)
    session.xhs_observer = observer
    await observer.initialize()
    return observer
