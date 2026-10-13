"""Observe the current Douyin custom-cover upload without starting operations.

The observer is tied to the normal image input, the current uploadCrop React
ref, and the image-mode instance of the already verified video SDK class. It
does not construct an SDK uploader, invoke crop/upload methods, or alter the
value/promise returned by either wrapped method.
"""
import asyncio
import json
import math
import re
import secrets
import time


DY_COVER_SLOTS = ('cover_portrait', 'cover_landscape')
MAX_COVER_BYTES = 32 * 1024 * 1024

DY_COVER_OBSERVER_SCRIPT = r"""(() => {
 'use strict';
 const DY_COVER_SLOTS=['cover_portrait','cover_landscape'];
 const old=window.__sauDYCoverObserver;
 if(old){if(old.api_version===9&&typeof old.configure==='function')return;
   if(old.scope||old.timer||old.patches?.length)return;}
 const nativeThen=Promise.prototype.then;
 const sizeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
 const typeGetter=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get;
 const nameGetter=Object.getOwnPropertyDescriptor(File.prototype,'name').get;
 const arrayBufferNative=Blob.prototype.arrayBuffer;
 const uploadProtoNames=['_handleCompleteReject','_handleCompleteResolve','_intiEvent',
   '_resetCompleteInfo','cancel','offProcess','onProcess','pause','resume',
   'resetVideoUploadConfig','setVideoUploadConfig','updateUploadOption',
   'uploadFile','uploadImgArr','uploadSrc'];
 const root={api_version:9,scope:null,dy_nonce:null,video_uploader:null,upload_proto:null,
   patches:[],selectionListener:null,active:false,sequence:0,selections:new WeakMap(),
   latestSelection:null,roles:new Map(),handleBindings:new WeakMap(),pending:new Set(),closed:false,
   metrics:{selection_count:0,ready_count:0,crop_call_count:0,crop_blob_count:0,
     image_upload_count:0,fulfilled_count:0,report_count:0,invalidated_count:0,
     exact_filechange_count:0,observer_error_count:0}};
 const current=scope=>!!scope&&root.active&&!root.closed&&root.scope===scope&&
   window.__sauDYUploadObserver?.scope?.observer_nonce===root.dy_nonce&&
   window.__sauDYUploadObserver?.scope?.binding_name===root.scope?.binding_name&&
   window.__sauDYUploadObserver?.current?.uploader===root.video_uploader&&
   window===top&&location.origin==='https://creator.douyin.com'&&
   ['/creator-micro/content/upload','/creator-micro/content/post/video'].includes(location.pathname);
 const routeOK=()=>window===top&&location.origin==='https://creator.douyin.com'&&
   ['/creator-micro/content/upload','/creator-micro/content/post/video'].includes(location.pathname);
 function nativeInfo(file){try{
   const name=Reflect.apply(nameGetter,file,[]),size=Reflect.apply(sizeGetter,file,[]),type=Reflect.apply(typeGetter,file,[]);
   if(typeof name!=='string'||!name||!Number.isSafeInteger(size)||size<=0||size>33554432||
      !['image/png','image/jpeg'].includes(type))return null;
   return {name,size,type};
  }catch(_){return null}}
 function nativeBlobInfo(blob){try{
   const size=Reflect.apply(sizeGetter,blob,[]),type=Reflect.apply(typeGetter,blob,[]);
   if(!Number.isSafeInteger(size)||size<=0||size>33554432||typeof type!=='string')return null;
   return {size,type};
  }catch(_){return null}}
 async function sha256(blob){
   const info=nativeBlobInfo(blob);if(!info)throw Error('blob');
   const bytes=await Reflect.apply(arrayBufferNative,blob,[]);
   const digest=await crypto.subtle.digest('SHA-256',bytes);
   return Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
 }
 // Reproduce official eD(YU(File)) for the supported PNG source. The retained
 // source scales the decoded image down to a 2160px longest edge, draws it to
 // a fresh canvas, then calls toBlob('image/jpeg') with browser-default quality.
 // Hash equality with the Blob whose exact object URL is passed as uploadCrop's
 // current `url` prop closes the File -> cropper-image edge without a Canvas
 // prototype hook or a time-window-only association.
 async function expectedPreview(file,role){
   const info=nativeInfo(file);if(!info||info.type!=='image/png')return null;
   const data=await new Promise((resolve,reject)=>{
     const reader=new FileReader();reader.onload=()=>resolve(reader.result);
     reader.onerror=()=>reject(Error('read'));reader.readAsDataURL(file);
   });
   const image=await new Promise((resolve,reject)=>{
     const value=new Image();value.onload=()=>resolve(value);value.onerror=()=>reject(Error('decode'));value.src=data;
   });
   const width=image.naturalWidth,height=image.naturalHeight;
   if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<=0||height<=0)return null;
   const horizontal=width>=height;
   const shrinkWidth=horizontal&&width>2160,shrinkHeight=height>2160;
   const targetWidth=shrinkWidth?2160:shrinkHeight?width/height*2160:width;
   const targetHeight=shrinkWidth?height/width*2160:shrinkHeight?2160:height;
   const canvas=document.createElement('canvas');canvas.width=targetWidth;canvas.height=targetHeight;
   const context=canvas.getContext('2d');if(!context)return null;
   context.drawImage(image,0,0,targetWidth,targetHeight);
   const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg'));
   const blobInfo=blob&&nativeBlobInfo(blob);
   if(!blobInfo||blobInfo.type!=='image/jpeg')return null;
   return {blob,info:blobInfo,sha256:await sha256(blob)};
 }
 function track(p){const value=Promise.resolve(p);root.pending.add(value);
   Reflect.apply(nativeThen,value,[()=>root.pending.delete(value),()=>root.pending.delete(value)]);return value;}
 function side(p,fulfilled,rejected){try{Reflect.apply(nativeThen,p,[v=>{try{fulfilled(v)}catch(_){root.metrics.observer_error_count++}},
   e=>{try{rejected?.(e)}catch(_){root.metrics.observer_error_count++}}]);}catch(_){}}
 function patch(object,key,factory){
   if(!object||typeof object[key]!=='function')return false;
   const prior=root.patches.find(p=>p.object===object&&p.key===key);
   if(prior)return object[key]===prior.wrapper;
   const descriptor=Object.getOwnPropertyDescriptor(object,key);
   if(descriptor&&(!('value'in descriptor)||descriptor.writable!==true||descriptor.configurable!==true))return false;
   const original=object[key],wrapper=factory(original);
   try{Object.defineProperty(object,key,descriptor?{...descriptor,value:wrapper}:
     {value:wrapper,writable:true,configurable:true,enumerable:true});
     root.patches.push({object,key,descriptor,original,wrapper});return true;
   }catch(_){return false}
 }
 function restoreAll(){
   for(const p of root.patches.slice().reverse())try{
     if(p.object[p.key]===p.wrapper){if(p.descriptor)Object.defineProperty(p.object,p.key,p.descriptor);else delete p.object[p.key];}
   }catch(_){}
   root.patches=[];
 }
 function videoUploaderShape(uploader){
   if(!uploader||typeof uploader!=='object'||!uploader.byteVideoUploader||
      uploader.uploaderOptions?.type!=='video'||typeof uploader.uploadFile!=='function')return false;
   const proto=Object.getPrototypeOf(uploader);if(!proto)return false;
   const names=Object.getOwnPropertyNames(proto).filter(x=>x!=='constructor').sort();
   const expected=uploadProtoNames.slice().sort();
   return names.length===expected.length&&names.every((x,i)=>x===expected[i]);
 }
 function normalInputs(){
   return [...document.querySelectorAll('input.semi-upload-hidden-input')].filter(input=>{
     const host=input.closest('.semi-upload');
     return input.type==='file'&&host&&host.querySelector('.semi-upload-drag-area-main-text');
   });
 }
 function nativeNormalInput(input){
   const matches=normalInputs();
   return matches.length===1&&matches[0]===input&&input.isConnected&&input.ownerDocument===document;
 }
 function normalUploadProps(input){
   if(!nativeNormalInput(input))return null;
   const owner=normalUploadComponent(input);
   if(owner)return owner.props;
   const keys=Object.getOwnPropertyNames(input).filter(k=>k.startsWith('__reactFiber$'));
   if(keys.length!==1)return null;
   let fiber=input[keys[0]],matches=[],seenFibers=new Set(),seenProps=new Set();
   while(fiber&&!seenFibers.has(fiber)){
     seenFibers.add(fiber);
     for(const props of [fiber.memoizedProps,fiber.pendingProps]){
       if(!props||seenProps.has(props))continue;seenProps.add(props);
       if(typeof props.onFileChange==='function'&&props.dragable===true&&props.limit===1&&
          props.showUploadList===false&&(Object.prototype.hasOwnProperty.call(props,'dragMainText')||
            (typeof props.beforeUpload==='function'&&props.accept)))
         matches.push(props);
     }
     fiber=fiber.return;
   }
   return matches.length===1?matches[0]:null;
 }
 function normalUploadComponent(input){
   if(!nativeNormalInput(input))return null;
   // DOM Fiber properties can still point to the alternate immediately before
   // a native change. Resolve the current Upload owner from committed roots.
   const queue=[],seen=new Set(),matches=new Set();
   for(const container of document.querySelectorAll('[id="root"]')){
     const keys=Object.getOwnPropertyNames(container).filter(k=>k.startsWith('__reactContainer$'));
     if(keys.length!==1)continue;
     const anchor=container[keys[0]],current=anchor?.stateNode?.current;
     if(anchor?.tag===3&&current?.tag===3&&current.stateNode===anchor.stateNode&&
       current.stateNode.containerInfo===container)queue.push(current);
   }
   while(queue.length){if(seen.size>=4096)return null;
     const fiber=queue.shift();if(!fiber||seen.has(fiber))continue;seen.add(fiber);
     const component=fiber.stateNode,props=component?.props;
     if(fiber.tag===1&&component&&(fiber.memoizedProps===props||fiber.pendingProps===props)&&
       typeof props?.onFileChange==='function'&&props.dragable===true&&props.limit===1&&
       props.showUploadList===false&&component.inputRef?.current===input)matches.add(component);
     if(fiber.child)queue.push(fiber.child);if(fiber.sibling)queue.push(fiber.sibling);
   }
   return matches.size===1?[...matches][0]:null;
 }
 function selectionFor(input){
   const known=root.selections.get(input);if(known)return known;
   const prior=root.latestSelection;
   // Semi remounts its input after file selection. Only the same retained
   // normal Upload class instance can transfer the captured File to that input.
   if(prior?.valid&&prior.uploadComponent&&nativeNormalInput(input)&&
     normalUploadComponent(input)===prior.uploadComponent){
     prior.input=input;root.selections.set(input,prior);return prior;}
   return null;
 }
 function installFileChangeHook(input){
   const props=normalUploadProps(input);if(!props)return false;
   return patch(props,'onFileChange',original=>function(...args){
     try{
       const selection=root.latestSelection,files=args[0];
       // Semi can retain this props callback while remounting a new input
       // for the next cover. Match the newly captured File and the same
       // Upload owner, rather than retaining the first input in the closure.
       const owner=selection?.capturedInput===input||selection?.uploadComponent?.props===props;
       if(root.active&&selection?.valid&&owner&&root.selections.get(selection.capturedInput)===selection&&
          Array.isArray(files)&&files.length===1&&
          files[0]===selection.file){
         selection.file_change_seen=true;root.metrics.exact_filechange_count++;
       }
     }catch(_){root.metrics.observer_error_count++;}
     return Reflect.apply(original,this,args);
   });
 }
 function onSelection(event){
   try{
     const input=event?.target;
     if(!nativeNormalInput(input)||!input.files||input.files.length!==1)return;
     const file=input.files.item(0),info=file&&nativeInfo(file);if(!info)return;
     const selection={id:String(++root.sequence),input,file,info,selected_at:Date.now()/1000,
       uploadComponent:normalUploadComponent(input),capturedInput:input,
       url_records:[],valid:true,overflow:false,prepared:null,file_change_seen:false,file_change_hook_ready:false};
     root.selections.set(input,selection);root.latestSelection=selection;
     selection.file_change_hook_ready=installFileChangeHook(input);
     root.metrics.selection_count++;
   }catch(_){root.metrics.observer_error_count++;}
 }
 function dataBlob(data){
   if(typeof data!=='string'||data.length>45000000)return null;
   const match=/^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/]+={0,2})$/.exec(data);
   if(!match)return null;
   try{const value=atob(match[2]),bytes=new Uint8Array(value.length);
     for(let i=0;i<value.length;i++)bytes[i]=value.charCodeAt(i);
     return new Blob([bytes],{type:match[1]});}catch(_){return null;}
 }
 function fabricFor(role){
   const direction=role==='cover_portrait'?'vertical':'horizontal',id=direction+'_coverCanvas';
   const canvases=[...document.querySelectorAll('canvas[id="'+id+'"]')];
   if(canvases.length!==1||!canvases[0].isConnected)return null;
   const canvas=canvases[0],queue=[],seen=new Set(),roots=new Set(),hosts=[];
   for(const container of document.querySelectorAll('[id="root"]')){
     const keys=Object.getOwnPropertyNames(container).filter(k=>k.startsWith('__reactContainer$'));
     if(keys.length!==1)continue;
     const anchor=container[keys[0]],current=anchor?.stateNode?.current;
     if(anchor?.tag===3&&current?.tag===3&&current.stateNode===anchor.stateNode&&
        current.stateNode.containerInfo===container&&!roots.has(current)){
       roots.add(current);queue.push({node:current,parents:[]});}
   }
   while(queue.length){if(seen.size>=4096)return null;
     const {node,parents}=queue.shift();if(!node||seen.has(node))continue;seen.add(node);
     if(node.tag===5&&node.stateNode===canvas)hosts.push({node,parents});
     if(node.child)queue.push({node:node.child,parents:[node,...parents]});
     if(node.sibling)queue.push({node:node.sibling,parents});}
   if(hosts.length!==1)return null;
   const parents=hosts[0].parents,providers=parents.filter(f=>f.tag===10&&
     f.memoizedProps?.value?.rootProps&&f.memoizedProps.value?.UIStates&&
     typeof f.memoizedProps.value.getEditorPerfTracker==='function');
   if(providers.length!==1||providers[0].memoizedProps.value.rootProps.currentEditStep!==direction)return null;
   const sdkKeys=['uid','canvasW','canvasH','cropW','cropH','initScale','direction','getNewId','canvas',
     '_eventAbility','on','off','offAll','onCurrentLayerChanged','onTextEdit','onError','onLayerChanged',
     '_loadingAbility','_canvasAbility','_cropAbility','compile','getPreviewDataURL','replaceBackground',
     'getRawBackgroundInfo','moveLayerOnTop','moveLayerOnBottom','recoverFromDraft','getCheckDataUrl',
     'deleteObjects','filterAbility','addComposerFilter','removeComposerFilter','removeFilter','addFilter',
     'setFilterProperty','getFilterParams','recoverDraftFilter','imageAbility','addSticker','templateAbility',
     'addTemplate','removeTemplate','recoverDraftTemplate','getTemplateCurrent','textAbility','addText',
     'addTitle','updateTitle','updateTextProperty','aiTextAbility'].sort();
   const handles=new Map();
   for(const fiber of parents){if(fiber.tag!==0||Object.keys(fiber.memoizedProps||{}).length)continue;
     let hook=fiber.memoizedState,count=0;
     while(hook&&count++<128){const ref=hook.memoizedState,handle=ref?.current;
       if(handle&&typeof handle.compile==='function'&&handle.direction===direction&&
         handle.canvas?.lowerCanvasEl===canvas&&handle._canvasAbility?.core===handle&&
         handle._canvasAbility?.canvas===handle.canvas&&
         JSON.stringify(Object.keys(handle).sort())===JSON.stringify(sdkKeys)&&
         typeof handle._canvasAbility.rawBackgroundInfo?.base64==='string')
         handles.set(handle,{kind:'fabric',fiber,ref,handle,canvas,
           sourceData:handle._canvasAbility.rawBackgroundInfo.base64});
       hook=hook.next;}
   }
   return handles.size===1?[...handles.values()][0]:null;
 }
 function cropperFor(input,role){
   const fabric=fabricFor(role);if(fabric)return fabric;
   if(!nativeNormalInput(input))return null;
   const canvases=[...document.querySelectorAll('canvas#uploadCrop')];if(canvases.length!==1)return null;
   const canvas=canvases[0],keys=Object.getOwnPropertyNames(canvas).filter(k=>k.startsWith('__reactFiber$'));
   if(keys.length!==1)return null;
   let fiber=canvas[keys[0]],matches=[];const seen=new Set();
   while(fiber&&!seen.has(fiber)){seen.add(fiber);
     const props=fiber.memoizedProps,ref=fiber.ref,handle=ref&&typeof ref==='object'?ref.current:null;
     if(props?.cropId==='uploadCrop'&&typeof props.url==='string'&&
        handle&&typeof handle.cbCrop==='function'&&
        (handle.direction==='vertical'||handle.direction==='horizontal'))matches.push({fiber,props,ref,handle});
     fiber=fiber.return;
   }
   if(matches.length!==1)return null;
   const found=matches[0],direction=role==='cover_portrait'?'vertical':'horizontal';
   if(found.handle.direction!==direction)return null;
   return {...found,canvas};
 }
 // Official module 64477's image wrapper is a separate class from the
 // current video wrapper. oP is a custom hook in the same committed,
 // empty-props function as the Fabric SDK: horizontal ref precedes vertical
 // ref (6837.cb03acc1.js bytes 415215 and 521223). Bind the exact role receiver,
 // rather than assuming that the video prototype also receives image calls.
 function fabricImageUploader(crop,role){
   const keys=['_completeReject','_completeResolve','_uploadedFile','byteVideoUploader',
     'defaultVideoUploadConfig','disableRouterSelect','isSupportPDF','isTosDirect',
     'isUsingRegionalVideoHostConfig','processListeners','timestamp','uid','uploadTime','uploaderOptions'].sort();
   const methods=['_handleCompleteReject','_handleCompleteResolve','_intiEvent','_resetCompleteInfo',
     'cancel','offProcess','onProcess','resetVideoUploadConfig','updateUploadOption',
     'uploadFile','uploadImgArr','uploadSrc'].sort();
   const found=[];let hook=crop.fiber?.memoizedState,count=0;
   while(hook&&count++<128){const ref=hook.memoizedState,uploader=ref?.current;
     if(uploader?.uploaderOptions?.type==='image'&&uploader.byteVideoUploader&&
       JSON.stringify(Object.keys(uploader).sort())===JSON.stringify(keys)){
       const proto=Object.getPrototypeOf(uploader);
       if(proto&&JSON.stringify(Object.getOwnPropertyNames(proto).filter(k=>k!=='constructor').sort())===JSON.stringify(methods)&&
         typeof uploader.uploadFile==='function'&&!Object.hasOwn(uploader,'uploadFile'))found.push({ref,uploader,proto});
     }hook=hook.next;
   }
   if(found.length!==2||found[0].uploader===found[1].uploader||found[0].proto!==found[1].proto)return null;
   return found[role==='cover_portrait'?1:0];
 }
 function send(event,fields,scope=root.scope){
   if(!current(scope))return Promise.resolve(false);
   try{const bridge=window[scope.binding_name],fn=bridge?.cover;if(typeof fn!=='function')return Promise.resolve(false);
     return track(Promise.resolve(fn.call(bridge,{v:1,job_id:scope.job_id,receipt_nonce:scope.receipt_nonce,
       observer_nonce:scope.observer_nonce,event,...fields})).then(x=>x===true,()=>false));
   }catch(_){return Promise.resolve(false)}
 }
 function invalidateRole(role,selection){
   if(!selection||!selection.valid)return;
   selection.valid=false;root.metrics.invalidated_count++;
   const invocation=selection.invocation;if(invocation)invocation.valid=false;
   const binding=selection.handle&&root.handleBindings.get(selection.handle);
   if(binding?.selection===selection)root.handleBindings.delete(selection.handle);
   if(root.roles.get(role)===selection)root.roles.delete(role);
   return track(send('invalidate',{role,selection_id:selection.id}));
 }
 function currentSelection(selection,role){
   return !!selection&&selection.valid&&root.roles.get(role)===selection&&
     current(selection.scope)&&selection.scope===root.scope&&
     selection.upload_proto===root.upload_proto&&selection.video_uploader===root.video_uploader;
 }
 function installURLObserver(){
   const descriptor=Object.getOwnPropertyDescriptor(URL,'createObjectURL');
   if(descriptor&&(!('value'in descriptor)||descriptor.writable!==true||descriptor.configurable!==true))return false;
   const original=URL.createObjectURL;
   return patch(URL,'createObjectURL',native=>function(...args){
     const returned=Reflect.apply(native,this,args);
     try{
       const selection=root.latestSelection,info=args.length===1&&nativeBlobInfo(args[0]);
       if(root.active&&selection?.valid&&info&&typeof returned==='string'&&returned.startsWith('blob:')){
         if(selection.url_records.length>=16)selection.overflow=true;
         else selection.url_records.push({blob:args[0],url:returned,info,created_at:Date.now()/1000});
       }
     }catch(_){root.metrics.observer_error_count++;}
     return returned;
   });
 }
 async function prepareSelection(scope,role,input){
   if(!current(scope)||!DY_COVER_SLOTS.includes(role)||!nativeNormalInput(input))return null;
   const selection=selectionFor(input),file=selection?.file;
   if(!selection||!selection.valid||selection.input!==input||selection.file!==file||
      !selection.file_change_hook_ready||!selection.file_change_seen)return null;
   const expected=scope.covers[role],info=nativeInfo(file);
   if(!expected||!info||info.name!==expected.name||info.size!==expected.size)return null;
   if(info.type!=='image/png')return null;
   try{if(!selection.prepared)selection.prepared=track(Promise.all([sha256(file),expectedPreview(file,role)]));}
   catch(_){return null;}
   let prepared;try{prepared=await selection.prepared;}catch(_){return null;}
   const originHash=prepared?.[0],preview=prepared?.[1];
   if(originHash!==expected.sha256||!preview||!current(scope)||root.selections.get(input)!==selection)return null;
   const crop=cropperFor(input,role);if(!crop)return null;
   const fabricBlob=crop.kind==='fabric'?dataBlob(crop.sourceData):null;
   const fabricHash=fabricBlob?await sha256(fabricBlob):null;
   const urls=selection.url_records.filter(item=>(crop.kind==='fabric'||item.url===crop.props.url)&&item.info.type==='image/jpeg'&&
      item.created_at>=selection.selected_at&&item.created_at<=Date.now()/1000);
   let verifiedURLs=urls;
   if(crop.kind==='fabric'){verifiedURLs=[];for(const item of urls)if(await sha256(item.blob)===fabricHash)verifiedURLs.push(item);}
   if(selection.overflow||!verifiedURLs.length||(crop.kind!=='fabric'&&verifiedURLs.length!==1))return null;
   // The cloud editor creates a second URL for a fetched copy of the same
   // JPEG. Full byte equality with the selected File's official conversion
   // and the current SDK base64 makes these aliases one material resource.
   const source=verifiedURLs[0];
   const sourceHashPromise=track(sha256(source.blob));
   let sourceHash;try{sourceHash=await sourceHashPromise;}catch(_){return null;}
   if(sourceHash!==preview.sha256||source.info.size!==preview.info.size||source.info.type!==preview.info.type)return null;
   const rolePrior=root.roles.get(role);
   const cropHook=root.patches.find(p=>p.object===crop.handle&&p.key===(crop.kind==='fabric'?'compile':'cbCrop'));
   if(rolePrior===selection&&selection.handle===crop.handle&&cropHook&&crop.handle[crop.kind==='fabric'?'compile':'cbCrop']===cropHook.wrapper)
     return selection;
   if(rolePrior&&rolePrior!==selection)await invalidateRole(role,rolePrior);
   // One selected File and its current uploadCrop URL cannot simultaneously
   // attest two slots. Separate selections are required even when bytes match.
   for(const [otherRole,other] of root.roles){
     if(otherRole!==role&&other===selection)return null;
     if(otherRole!==role&&other.handle===crop.handle&&other.valid&&!other.invocation?.reported)return null;
   }
   const proto=root.upload_proto;
   if(!proto||Object.getPrototypeOf(root.video_uploader)!==proto)return null;
   const image=crop.kind==='fabric'?fabricImageUploader(crop,role):null;
   if(crop.kind==='fabric'&&(!image||!installUploadHook(image.proto)))return null;
   const roleScope=scope;
   // Keep selection identity stable while adding only observer-owned state.
   Object.assign(selection,{scope:roleScope,origin_hash:originHash,source,source_hash_promise:sourceHashPromise,
     kind:crop.kind||'crop',sourceData:crop.sourceData,cropper_fiber:crop.fiber,props:crop.props,handle:crop.handle,ref:crop.ref,canvas:crop.canvas,
     upload_proto:proto,video_uploader:root.video_uploader,invocation:null});
   if(image)Object.assign(selection,{image_uploader:image.uploader,image_ref:image.ref,image_proto:image.proto});
   root.roles.set(role,selection);
   root.handleBindings.set(crop.handle,{selection,role});
   if(!(crop.kind==='fabric'?installFabricHook(crop.handle):installCropHook(crop.handle))){invalidateRole(role,selection);return null;}
   root.metrics.ready_count++;
   const accepted=await send('selection',{role,selection_id:selection.id,name:info.name,size:info.size,type:info.type,
     sha256:originHash,selected_at:selection.selected_at});
   if(!accepted){invalidateRole(role,selection);return null;}
   return selection;
 }
 function activeCropper(selection,role){
   if(!selection||!currentSelection(selection,role)||!selection.ref||
      selection.ref.current!==selection.handle||!selection.handle||
      selection.handle.direction!==(role==='cover_portrait'?'vertical':'horizontal'))return false;
   const crop=cropperFor(selection.input,role);
   return !!crop&&crop.handle===selection.handle&&crop.ref===selection.ref&&
     (crop.kind==='fabric'?crop.sourceData===selection.sourceData&&crop.canvas===selection.canvas:
       crop.props.url===selection.source.url&&crop.fiber===selection.cropper_fiber);
 }
 function installCropHook(handle){
   return patch(handle,'cbCrop',original=>function(...args){
     const binding=root.handleBindings.get(handle),selection=binding?.selection,role=binding?.role;
     let invocation=null;
     try{if(DY_COVER_SLOTS.includes(role)&&activeCropper(selection,role)&&selection.invocation===null){
       invocation={id:String(++root.sequence),role,selection,handle,valid:true,called_at:Date.now()/1000,
         blob:null,blob_info:null,blob_sha256:null,blob_sha256_promise:null,uploaded:false};
       selection.invocation=invocation;root.metrics.crop_call_count++;
     }}catch(_){root.metrics.observer_error_count++;}
     const returned=Reflect.apply(original,this,args);
     if(invocation){
       side(returned,blob=>{
         if(!invocation.valid||selection.invocation!==invocation||!activeCropper(selection,role))return;
         const info=nativeBlobInfo(blob);if(!info||info.type!=='image/jpeg')return;
         invocation.blob=blob;invocation.blob_info=info;
         invocation.blob_sha256_promise=track(sha256(blob).then(hash=>{
           // The normal save closes this modal after SDK fulfillment. The
           // exact crop call already froze the Blob before that unmount.
           if(invocation.valid&&selection.invocation===invocation&&currentSelection(selection,role)){
             invocation.blob_sha256=hash;root.metrics.crop_blob_count++;return hash;
           }else {invocation.valid=false;return null;}
         },()=>{invocation.valid=false;return null;}));
       },()=>{invocation.valid=false;});
     }
     return returned;
   });
 }
 function installFabricHook(handle){
   return patch(handle,'compile',original=>function(...args){
     const binding=root.handleBindings.get(handle),selection=binding?.selection,role=binding?.role;
     let invocation=null;
     if(selection?.kind==='fabric'&&activeCropper(selection,role)&&selection.invocation===null){
       invocation={id:String(++root.sequence),role,selection,handle,valid:true,called_at:Date.now()/1000,
         blob:null,blob_info:null,blob_sha256:null,blob_sha256_promise:null,uploaded:false,fabric:true};
       selection.invocation=invocation;root.metrics.crop_call_count++;
     }
     const returned=Reflect.apply(original,this,args);
     if(invocation)side(returned,result=>{
       const active=activeCropper(selection,role);
       root.metrics.compile_source_same=result?.draftBase64===selection.sourceData;
       root.metrics.compile_active=active;
       const mime=typeof result?.dataURL==='string'?result.dataURL.slice(5,result.dataURL.indexOf(';')):null;
       root.metrics.compile_result_type=['image/jpeg','image/png','image/webp'].includes(mime)?mime:null;
       if(!invocation.valid||selection.invocation!==invocation||!active)return;
       // Official compile returns the exact dataURL converted by module 6681.i
       // into the Blob supplied to the existing normal image uploader.
       const blob=dataBlob(result?.dataURL),info=blob&&nativeBlobInfo(blob);
       invocation.compile_diagnostic={type:info?.type||null,source_same:result?.draftBase64===selection.sourceData,active:activeCropper(selection,role)};
       if(!info||!['image/jpeg','image/png','image/webp'].includes(info.type)||result.draftBase64!==selection.sourceData){invocation.valid=false;return;}
       invocation.expected_crop_blob=blob;
       invocation.expected_crop_hash=track(sha256(blob));
     },()=>{invocation.valid=false;});
     return returned;
   });
 }
 function installUploadHook(proto){
   return patch(proto,'uploadFile',original=>function(...args){
     let selected=null,invocation=null;
     try{
       const validReceiver=Object.getPrototypeOf(this)===proto&&
         this?.uploaderOptions?.type==='image';
       if(validReceiver&&args.length===1){
         const matches=[...root.roles].filter(([role,selection])=>{
           const inv=selection.invocation;
           return inv&&inv.valid&&!inv.uploaded&&(inv.fabric?!!inv.expected_crop_blob:inv.blob===args[0])&&
             currentSelection(selection,role)&&activeCropper(selection,role)&&
             (inv.fabric?selection.image_uploader===this&&selection.image_ref?.current===this&&selection.image_proto===proto:
               root.upload_proto===proto);
         });
         if(matches.length===1){selected=matches[0][1];invocation=selected.invocation;
           invocation.uploaded=true;invocation.upload_started_at=Date.now()/1000;
           invocation.image_uploader=this;root.metrics.image_upload_count++;
           if(invocation.fabric){
             const info=nativeBlobInfo(args[0]);
             if(!info||!['image/jpeg','image/png','image/webp'].includes(info.type)){invocation.valid=false;}
             else {invocation.blob=args[0];invocation.blob_info=info;
               invocation.blob_sha256_promise=track(Promise.all([sha256(args[0]),invocation.expected_crop_hash]).then(([actual,expected])=>{
                 if(!actual||actual!==expected||!invocation.valid||!currentSelection(selected,invocation.role)){
                   invocation.valid=false;return null;}
                 invocation.blob_sha256=actual;root.metrics.crop_blob_count++;return actual;
               },()=>{invocation.valid=false;return null;}));}
           }}
       }
     }catch(_){root.metrics.observer_error_count++;}
     const returned=Reflect.apply(original,this,args);
     if(selected&&invocation){
       side(returned,result=>{
         if(!invocation.valid||!currentSelection(selected,invocation.role)||
            selected.invocation!==invocation||!activeCropper(selected,invocation.role)||
            Object.getPrototypeOf(this)!==proto||this.uploaderOptions?.type!=='image')return;
         const uri=result&&result.uri;
         if(typeof uri!=='string'||!uri||uri.length>2048||/[\x00-\x20\x7f]/.test(uri))return;
         invocation.uri=uri;invocation.completed_at=Date.now()/1000;
         root.metrics.fulfilled_count++;
         track(Promise.all([selected.source_hash_promise,invocation.blob_sha256_promise]).then(async hashes=>{
           const [sourceHash,cropHash]=hashes;
           if(!sourceHash||!cropHash||!invocation.valid||!currentSelection(selected,invocation.role)||
              selected.invocation!==invocation||
              invocation.completed_at<invocation.upload_started_at)return;
           const accepted=await send('uploaded',{role:invocation.role,selection_id:selected.id,
             invocation_id:invocation.id,name:selected.info.name,size:selected.info.size,type:selected.info.type,
             origin_sha256:selected.origin_hash,selected_at:selected.selected_at,
             source_blob_size:selected.source.info.size,source_blob_type:selected.source.info.type,
             source_blob_sha256:sourceHash,source_url_created_at:selected.source.created_at,
             crop_blob_size:invocation.blob_info.size,crop_blob_type:invocation.blob_info.type,
             crop_blob_sha256:cropHash,crop_called_at:invocation.called_at,
             upload_started_at:invocation.upload_started_at,upload_completed_at:invocation.completed_at,
             uri:invocation.uri});
           if(accepted){invocation.reported=true;root.metrics.report_count++;}
           else invocation.valid=false;
         },()=>{invocation.valid=false;}));
       },()=>{invocation.valid=false;});
     }
     return returned;
   });
 }
 root.configure=(scope,dyNonce)=>{
   if(!routeOK()||!scope||!['job_id','receipt_nonce','observer_nonce','binding_name','covers'].every(k=>scope[k])||
      !/^[A-Za-z0-9_-]{1,128}$/.test(scope.job_id)||!/^[A-Za-z0-9_-]{1,128}$/.test(scope.receipt_nonce)||
      !/^[a-f0-9]{48}$/.test(scope.observer_nonce)||typeof scope.binding_name!=='string'||
      !/^__sauDYUploaded_[a-f0-9]{48}$/.test(scope.binding_name)||
      !scope.covers||!Object.keys(scope.covers).length||Object.keys(scope.covers).some(k=>!['cover_portrait','cover_landscape'].includes(k)))return false;
   for(const value of Object.values(scope.covers))if(!value||typeof value.name!=='string'||!value.name||
     !Number.isSafeInteger(value.size)||value.size<=0||value.size>33554432||!/^[a-f0-9]{64}$/.test(value.sha256))return false;
   const dy=window.__sauDYUploadObserver,uploader=dy?.current?.uploader;
   if(!dy||dy.scope?.observer_nonce!==dyNonce||dy.scope?.binding_name!==scope.binding_name||
      typeof window[scope.binding_name]?.cover!=='function'||!videoUploaderShape(uploader))return false;
   if(root.scope&&root.scope.observer_nonce!==scope.observer_nonce&&root.active)return false;
   root.scope=Object.freeze({...scope,covers:Object.freeze({...scope.covers})});root.dy_nonce=dyNonce;
   root.video_uploader=uploader;root.upload_proto=Object.getPrototypeOf(uploader);root.active=true;root.closed=false;
   if(!root.selectionListener){root.selectionListener=onSelection;document.addEventListener('change',root.selectionListener,true);}
   const urlOK=installURLObserver(),uploadOK=installUploadHook(root.upload_proto);
   return urlOK&&uploadOK;
 };
 root.ensureReady=async(nonce,role,input)=>{
   const scope=root.scope;
   if(!current(scope)||scope.observer_nonce!==nonce||!DY_COVER_SLOTS.includes(role))return false;
   if(!nativeNormalInput(input)||!selectionFor(input)?.valid)return false;
   const deadline=Date.now()+6000;
   while(current(scope)&&Date.now()<deadline){
     try{
       if(nativeNormalInput(input)&&selectionFor(input)?.valid){
         const prior=root.roles.get(role),next=selectionFor(input);
         if(prior&&prior!==next)await invalidateRole(role,prior);
         if(next&&(!next.file_change_hook_ready||!next.file_change_seen))return false;
         const selected=await prepareSelection(scope,role,input);
         if(selected)return true;
         if(next&&next.url_records.length&&cropperFor(input,role)?.kind!=='fabric')return false;
       }
     }catch(_){root.metrics.observer_error_count++;}
     await new Promise(resolve=>setTimeout(resolve,40));
   }
   return false;
 };
 root.flush=async(nonce,timeout)=>{
   if(root.scope?.observer_nonce!==nonce)return false;
   const until=Date.now()+Math.max(0,Math.min(10000,Number(timeout)||0));
   while(root.pending.size&&Date.now()<until)
     await Promise.race([Promise.allSettled([...root.pending]),new Promise(resolve=>setTimeout(resolve,10))]);
   return root.pending.size===0;
 };
 root.status=nonce=>root.scope?.observer_nonce===nonce?{
   api_version:9,scope_match:true,active:root.active,closed:root.closed,
   route:routeOK(),selected_count:root.metrics.selection_count,ready_count:root.metrics.ready_count,
   crop_call_count:root.metrics.crop_call_count,crop_blob_count:root.metrics.crop_blob_count,
   image_upload_count:root.metrics.image_upload_count,fulfilled_count:root.metrics.fulfilled_count,
   exact_filechange_count:root.metrics.exact_filechange_count,report_count:root.metrics.report_count,pending_count:root.pending.size,
   observer_error_count:root.metrics.observer_error_count}:null;
 root.close=nonce=>{
   if(root.scope?.observer_nonce!==nonce)return false;
   root.active=false;root.closed=true;
   if(root.selectionListener)document.removeEventListener('change',root.selectionListener,true);
   root.selectionListener=null;
   for(const selection of root.roles.values()){
     selection.valid=false;if(selection.invocation)selection.invocation.valid=false;
   }
   root.roles.clear();restoreAll();root.video_uploader=null;root.upload_proto=null;root.scope=null;root.dy_nonce=null;
   return true;
 };
 window.__sauDYCoverObserver=root;
})()"""


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


class DYCoverObserver:
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.observer_nonce = secrets.token_hex(24)
        self.binding_name = None
        self.started_at = time.time()
        self.closed = False
        self.available = False
        self.records = {}
        self.invalid = set()
        self.selection_ids = {}
        self.selection_reports = {}
        self.tasks = set()
        self.navigation_handler = self._navigated

    def _current(self):
        return (not self.closed and not self.session.closed and
                getattr(self.page, '_sau_draft_evidence', None) is self.session and
                getattr(self.session, 'dy_cover_observer', None) is self)

    @property
    def covers(self):
        wanted = self.session.expected.get('cover_sha256') or {}
        result = {}
        for role in DY_COVER_SLOTS:
            asset = self.session.assets.get(role)
            if (role in wanted and isinstance(asset, dict) and asset.get('kind') == 'cover' and
                    asset.get('sha256') == wanted[role] and _sha(wanted[role]) and
                    isinstance(asset.get('name'), str) and asset['name'] and
                    type(asset.get('size')) is int and 0 < asset['size'] <= MAX_COVER_BYTES):
                result[role] = {key: asset[key] for key in ('name', 'size', 'sha256')}
        return result

    @property
    def scope(self):
        dy = getattr(self.session, 'dy_observer', None)
        return {'job_id': self.session.expected.get('job_id'),
                'receipt_nonce': self.session.expected.get('receipt_nonce'),
                'observer_nonce': self.observer_nonce,
                'binding_name': getattr(dy, 'binding_name', None),
                'covers': self.covers}

    async def initialize(self):
        dy = getattr(self.session, 'dy_observer', None)
        if (not self._current() or not self.covers or dy is None or dy.page is not self.page or
                not isinstance(getattr(dy, 'binding_name', None), str) or
                not await dy.ensure_ready(timeout=5)):
            return
        self.binding_name = dy.binding_name
        self.page.on('framenavigated', self.navigation_handler)
        await self._configure()

    def _navigated(self, frame):
        if not self._current() or frame is not self.page.main_frame:
            return
        task = asyncio.create_task(self._configure())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _configure(self):
        dy = getattr(self.session, 'dy_observer', None)
        if not self._current() or dy is None or not self.covers:
            return False
        try:
            if not await dy.ensure_ready(timeout=5):
                self.available = False
                return False
            await dy.transport.evaluate(DY_COVER_OBSERVER_SCRIPT)
            self.available = bool(await dy.transport.evaluate(
                'window.__sauDYCoverObserver?.configure(' + json.dumps(self.scope) + ',' +
                json.dumps(dy.observer_nonce) + ')'))
            return self.available
        except Exception:
            self.available = False
            return False

    async def ensure_ready(self, role, normal_input):
        if (role not in self.covers or not self._current() or not self.available or
                getattr(self.session, 'dy_observer', None) is None):
            return False
        dy = self.session.dy_observer
        if not await dy.ensure_ready(timeout=5):
            return False
        if normal_input is None:
            return False
        selector = (normal_input if isinstance(normal_input, str) else
                    getattr(getattr(normal_input, '_impl_obj', None), '_selector', None))
        if not isinstance(selector, str) or not selector:
            return False
        try:
            expression = (
                "(async()=>{const o=window.__sauDYCoverObserver,args=" +
                json.dumps({'nonce': self.observer_nonce, 'role': role, 'selector': selector}) +
                ";const xs=[...document.querySelectorAll('input.semi-upload-hidden-input')]" +
                ".filter(e=>e.type==='file'&&e.closest('.semi-upload')&&" +
                "e.closest('.semi-upload').querySelector('.semi-upload-drag-area-main-text'));" +
                "let requested;try{requested=[...document.querySelectorAll(args.selector)]}catch(_){return false}" +
                "if(xs.length!==1||requested.length!==1||requested[0]!==xs[0])return false;" +
                "return !!o&&await o.ensureReady(args.nonce,args.role,xs[0]);})()"
            )
            return bool(await dy.transport.evaluate(expression))
        except Exception:
            return False

    def accept(self, source, report):
        base = {'v', 'job_id', 'receipt_nonce', 'observer_nonce', 'event'}
        if (not self._current() or not isinstance(source, dict) or source.get('page') is not self.page or
                source.get('frame') is not self.page.main_frame or not isinstance(report, dict) or
                type(report.get('v')) is not int or report.get('v') != 1 or
                any(report.get(key) != self.scope[key] for key in ('job_id', 'receipt_nonce', 'observer_nonce'))):
            return False
        role, event = report.get('role'), report.get('event')
        if role not in self.covers:
            return False
        if event == 'invalidate':
            if set(report) != base | {'role', 'selection_id'}:
                return False
            selection_id = report.get('selection_id')
            if not isinstance(selection_id, str) or self.selection_ids.get(role) != selection_id:
                return False
            self.records = {key: value for key, value in self.records.items()
                            if value.get('role') != role or value.get('selection_id') != selection_id}
            self.invalid.add((role, selection_id))
            self.selection_ids.pop(role, None)
            self.selection_reports.pop(role, None)
            return True
        if event == 'selection':
            wanted = base | {'role', 'selection_id', 'name', 'size', 'type', 'sha256', 'selected_at'}
            if set(report) != wanted:
                return False
            selection_id = report.get('selection_id')
            asset = self.covers[role]
            if (not isinstance(selection_id, str) or not re.fullmatch(r'[1-9][0-9]{0,15}', selection_id) or
                    not isinstance(report.get('name'), str) or report['name'] != asset['name'] or
                    type(report.get('size')) is not int or report['size'] != asset['size'] or
                    report.get('type') not in ('image/png', 'image/jpeg') or
                    report.get('sha256') != asset['sha256'] or not _sha(report.get('sha256')) or
                    not _finite(report.get('selected_at')) or
                    not self.started_at <= report['selected_at'] <= time.time()+5 or
                    (role, selection_id) in self.invalid):
                return False
            for other_role, other_id in self.selection_ids.items():
                if other_role != role and other_id == selection_id:
                    return False
            previous = self.selection_ids.get(role)
            if previous and previous != selection_id:
                self.records = {key: value for key, value in self.records.items()
                                if value.get('role') != role or value.get('selection_id') != previous}
                self.invalid.add((role, previous))
            prior = self.selection_reports.get(role)
            safe = dict(report)
            if prior and prior != safe:
                return False
            self.selection_ids[role] = selection_id
            self.selection_reports[role] = safe
            return True
        if event != 'uploaded':
            return False
        wanted = base | {'role', 'selection_id', 'invocation_id', 'name', 'size', 'type',
            'origin_sha256', 'selected_at', 'source_blob_size', 'source_blob_type', 'source_blob_sha256',
            'source_url_created_at', 'crop_blob_size', 'crop_blob_type', 'crop_blob_sha256',
            'crop_called_at', 'upload_started_at', 'upload_completed_at', 'uri'}
        if set(report) != wanted:
            return False
        asset = self.covers[role]
        selection_id = report.get('selection_id')
        if (self.selection_ids.get(role) != selection_id or (role, selection_id) in self.invalid or
                not isinstance(selection_id, str) or not isinstance(report.get('invocation_id'), str) or
                not re.fullmatch(r'[1-9][0-9]{0,15}', report['invocation_id']) or
                report.get('name') != asset['name'] or type(report.get('size')) is not int or
                report['size'] != asset['size'] or report.get('type') not in ('image/png', 'image/jpeg') or
                report.get('origin_sha256') != asset['sha256'] or not _sha(report.get('origin_sha256')) or
                not _sha(report.get('source_blob_sha256')) or not _sha(report.get('crop_blob_sha256')) or
                report.get('source_blob_type') != 'image/jpeg' or report.get('crop_blob_type') not in ('image/jpeg', 'image/png', 'image/webp') or
                any(type(report.get(key)) is not int or not 0 < report[key] <= MAX_COVER_BYTES
                    for key in ('source_blob_size', 'crop_blob_size'))):
            return False
        times = [report.get(key) for key in ('selected_at', 'source_url_created_at', 'crop_called_at',
                                             'upload_started_at', 'upload_completed_at')]
        if (any(not _finite(value) for value in times) or
                not self.started_at <= times[0] <= times[1] <= times[2] <= times[3] <= times[4] <= time.time()+5):
            return False
        uri = report.get('uri')
        if not isinstance(uri, str) or not uri or len(uri) > 2048 or re.search(r'[\x00-\x20\x7f]', uri):
            return False
        key = (role, selection_id, report['invocation_id'])
        safe = dict(report)
        prior = self.records.get(key)
        if prior and prior != safe:
            self.records.pop(key, None)
            self.invalid.add((role, selection_id))
            return False
        self.records[key] = safe
        return True

    async def drain(self, timeout=5):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if not self._current() or not self.available:
            return
        dy = self.session.dy_observer
        try:
            await dy.transport.evaluate('window.__sauDYCoverObserver?.flush(' +
                json.dumps(self.observer_nonce) + ',' + str(max(0, min(10, float(timeout))) * 1000) + ')')
            if dy.transport.tasks:
                await asyncio.gather(*tuple(dy.transport.tasks), return_exceptions=True)
        except Exception:
            pass

    def upload_links(self):
        if not self._current():
            return []
        links = []
        for role, selection_id in self.selection_ids.items():
            records = [record for (record_role, record_selection, _), record in self.records.items()
                       if record_role == role and record_selection == selection_id and
                       record.get('role') == role]
            if len(records) != 1:
                continue
            record = records[0]
            if (record.get('origin_sha256') == self.covers[role]['sha256'] and
                    record.get('uri') and (role, selection_id) not in self.invalid):
                uri = record['uri']
                # The official eY callback stores "tos://" + SDK URI in
                # coverInfo; preserve the SDK identity and that exact variant.
                ids = [uri] if uri.startswith('tos://') else [uri, 'tos://' + uri]
                links.append({'kind': 'cover', 'slot': role, 'sha256': record['origin_sha256'],
                              'ids': ids, 'complete': True})
        return links

    async def close(self):
        if self.closed:
            return
        try:
            self.page.remove_listener('framenavigated', self.navigation_handler)
        except Exception:
            pass
        dy = getattr(self.session, 'dy_observer', None)
        if dy is not None:
            try:
                await dy.transport.evaluate('window.__sauDYCoverObserver?.close(' +
                                            json.dumps(self.observer_nonce) + ')')
            except Exception:
                pass
        self.closed = True
        self.available = False
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


async def install_dy_cover_observer(session):
    """Install after DY video observer; cover selections must still be pending."""
    observer = DYCoverObserver(session)
    session.dy_cover_observer = observer
    if getattr(session, 'platform', None) == 'douyin' and observer.covers:
        await observer.initialize()
    return observer
