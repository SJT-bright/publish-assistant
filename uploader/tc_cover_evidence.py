"""Observe the current official TC cover pipeline, without starting operations.

Source: content-841.d575b04c.js, 2026-10-03, decoded character offsets:
SingleCoverUploader 1864330 / display watcher 1859154,
EditCoverDialog 1889847/1893987,
horizon bind 1817500, actual Canvas -> Blob upload 884550,
CoverPreview applied role 1915436. Never hook a global canvas prototype.
"""
import asyncio
import json
import math
import re
import secrets
import time

MAX_COVER_BYTES = 32 * 1024 * 1024
COVER_DIAGNOSTIC_SLOTS = ('cover_landscape', 'cover_portrait')
COVER_DIAGNOSTIC_KEYS = ('records_count', 'native_match_count', 'links_count',
    'metadata_match_count', 'time_window_match_count', 'complete_candidate_count',
    'body_sha_match_count', 'applied_resource_match_count', 'resource_chain_count')

# A separate, fixed diagnostic schema. Never add application values, names,
# resource identities, hashes or exception text to these counters.
COVER_TRACE_EVENTS = (
    'configured', 'selection_accepted', 'reader_called', 'reader_returned', 'reader_accepted',
    'single_change_called', 'preview_encoding_called', 'preview_encoding_captured',
    'single_canvas_accepted', 'crop_install_attempted', 'crop_result_called',
    'crop_invocation_started', 'crop_result_returned', 'crop_result_canvas_accepted',
    'derived_encoding_called', 'derived_blob_captured', 'update_cover_called',
    'applied_candidate_accepted', 'applied_report_delivered',
    'preprocess_called', 'preprocess_returned', 'preprocess_bound_accepted',
    'display_watcher_called', 'display_source_attested',
    'live_trace_error', 'ready_trace_error', 'single_trace_error', 'preprocess_trace_error',
)
COVER_TRACE_GATES = (
    'reader_receiver', 'reader_selection', 'reader_args_match', 'reader_scope_current',
    'reader_selection_current', 'reader_dataurl_valid',
    'change_receiver', 'change_selection', 'change_canvas_native', 'change_dataurl_match',
    'preview_encoding_receiver', 'preview_encoding_selection_current',
    'single_preview_present', 'single_parent_canvas_match', 'single_parent_preview_match', 'single_parent_custom',
    'crop_ref_present', 'crop_ref_owned', 'crop_result_hook', 'crop_original_hook', 'preprocess_hook',
    'crop_result_hook_owner', 'single_reader_hook_owner', 'single_emit_hook_owner', 'parent_emit_hook_owner',
    'crop_receiver', 'crop_selection', 'crop_setting_cover',
    'live_scope', 'live_single_owned', 'live_parent_owned', 'live_canvas_match', 'live_dataurl_match',
    'live_custom_preview_match', 'live_current_preview_match', 'live_selection_current',
    'ready_live', 'ready_crop_owned', 'ready_crop_ref_match', 'ready_parent_open',
    'ready_not_loading', 'ready_crop_ready', 'ready_custom', 'ready_imageurl_match',
    'ready_last_image_match', 'ready_bound_image_match', 'ready_image_loaded',
    'preprocess_receiver', 'preprocess_selection', 'preprocess_live', 'preprocess_args_match',
    'preprocess_selection_current', 'preprocess_crop_ref_match', 'preprocess_returned_live', 'preprocess_result_valid',
    'display_watcher_receiver', 'display_selection_current', 'display_native_current',
    'display_canvas_match', 'display_preview_match', 'display_before_match', 'display_after_match',
)
COVER_TRACE_KEYS = COVER_TRACE_EVENTS + tuple(
    f'{gate}_{verdict}' for gate in COVER_TRACE_GATES for verdict in ('true', 'false')
)


def _empty_trace():
    return {slot: dict.fromkeys(COVER_TRACE_KEYS, 0) for slot in COVER_DIAGNOSTIC_SLOTS}

TC_COVER_OBSERVER_SCRIPT = r"""(() => {
 'use strict';
 const existing=window.__sauTCCoverObserver;
 if(existing){
  if(existing.trace_version===3)return;
  // Upgrade only a fully closed older observer. Never replace a live scope,
  // pending operation or existing hook, and never call a business method.
  if(existing.scope!==null || existing.roots?.size!==0 || existing.wrappers?.size!==0 || existing.pending?.size!==0)return;
 }
 const traceEvents=__TRACE_EVENTS__,traceGates=__TRACE_GATES__;
 const traceKeys=[...traceEvents,...traceGates.flatMap(k=>[k+'_true',k+'_false'])];
 const freshTrace=()=>Object.fromEntries(['cover_landscape','cover_portrait'].map(slot=>[slot,Object.fromEntries(traceKeys.map(k=>[k,0]))]));
 const root={scope:null,roots:new Map(),wrappers:new Map(),selections:new WeakMap(),parents:new WeakMap(),
   seenEvents:new WeakSet(),pending:new Set(),revision:0,sequence:0,available:false,
   trace_version:3,trace_nonce:null,trace:freshTrace()};
 const count=(role,key)=>{const target=root.trace[role];if(target&&traceKeys.includes(key))target[key]=Math.min(1000000000,target[key]+1);};
 const gate=(role,key,value)=>count(role,key+(value?'_true':'_false'));
 const then=Promise.prototype.then;
 const track=p=>{root.pending.add(p);Reflect.apply(then,p,[()=>root.pending.delete(p),()=>root.pending.delete(p)]);return p;};
 const current=s=>root.scope===s;
 const own=v=>v?.$el?.isConnected&&!!v.$el.ownerDocument?.defaultView;
 const methods=v=>v?.$options?.methods||{};
 const key=v=>methods(v).getFileImageDataURL?'single':methods(v).handleChooseCover?'parent':methods(v).handleUpdateCover?'preview':null;
 const ancestors=el=>{const seen=new Set(),out=[];for(let n=el;n;n=n.parentElement||n.getRootNode()?.host){let v=n.__vue__;while(v&&!seen.has(v)){seen.add(v);out.push(v);v=v.$parent}}return out;};
 const canv=v=>v&&v.tagName==='CANVAS'&&v instanceof v.ownerDocument?.defaultView?.HTMLCanvasElement;
 const nativeBlob=v=>{try{return Number.isSafeInteger(Reflect.apply(Object.getOwnPropertyDescriptor(Blob.prototype,'size').get,v,[]));}catch(_){return false;}};
 const nativeFile=v=>{try{return typeof Reflect.apply(Object.getOwnPropertyDescriptor(File.prototype,'name').get,v,[])==='string';}catch(_){return false;}};
 async function hashBlob(blob){
   const getter=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
   const size=Reflect.apply(getter,blob,[]);
   if(!Number.isSafeInteger(size)||size<=0||size>33554432)throw Error('cover size');
   const bytes=await Reflect.apply(Blob.prototype.arrayBuffer,blob,[]);
   const hash=await crypto.subtle.digest('SHA-256',bytes);
   return {sha256:Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join(''),size};
 }
 async function hashDataURL(url){
   if(typeof url!=='string'||!/^data:image\/[a-z0-9.+-]+;base64,/i.test(url))throw Error('cover data');
   const encoded=url.slice(url.indexOf(',')+1);
   if(encoded.length>44739244)throw Error('cover size');
   const bytes=atob(encoded),array=new Uint8Array(bytes.length);
   for(let i=0;i<bytes.length;i++)array[i]=bytes.charCodeAt(i);
   return hashBlob(new Blob([array]));
 }
 function side(p,yes){try{Reflect.apply(then,p,[value=>{try{yes(value);}catch(_){}},()=>{}]);}catch(_){} }
 function wrap(object,name,factory){
   if(!object||typeof object[name]!=='function')return false;
   let map=root.wrappers.get(object);if(map?.has(name))return true;
   if(object[name].__sauTCCoverOwner&&object[name].__sauTCCoverOwner!==root)return true;
   const descriptor=Object.getOwnPropertyDescriptor(object,name);
   if(descriptor&&(!('value'in descriptor)||!descriptor.writable||!descriptor.configurable))return false;
   const original=object[name],wrapper=factory(original);
   Object.defineProperty(wrapper,'__sauTCCoverOwner',{value:root});
   try{Object.defineProperty(object,name,descriptor?{...descriptor,value:wrapper}:{value:wrapper,writable:true,configurable:true,enumerable:true});
     if(!map){map=new Map();root.wrappers.set(object,map);}map.set(name,{original,wrapper,descriptor});return true;
   }catch(_){return false;}
 }
 function deliver(report,s){if(!current(s)||typeof window.__sauTCCoverObserved!=='function')return;
   track(Promise.resolve(window.__sauTCCoverObserved({v:1,...s,...report})).catch(()=>{}));}
 function slot(parent){return parent.previewMode==='landscape-share'?'cover_landscape':
   ['portrait','landscape-profile'].includes(parent.previewMode)?'cover_portrait':null;}
 function live(sel){return current(sel.scope)&&own(sel.single)&&own(sel.parent)&&sel.parent.customCover===sel.canvas&&
   typeof sel.displaySource==='string'&&sel.single.imgSrc===sel.displaySource&&sel.parent.customCoverPreviewUrl===sel.preview&&sel.parent.currentCoverImageUrl===sel.preview&&
   root.selections.get(sel.single)===sel;}
 function ready(sel,crop){const croppie=crop?.$refs?.croppieRefHorizon?.croppie||crop?.$refs?.croppieRefVertical?.croppie;
   const image=croppie?.elements?.img;
   return live(sel)&&own(crop)&&sel.parent.$refs.cropRef===crop&&sel.parent.value===true&&
     sel.parent.isCroppieLoading===false&&crop.isCroppieReady===true&&crop.useCustomCover===true&&
     crop.imageUrl===sel.preview&&crop.lastImageUrl===crop.imageUrl&&image?.src===(crop.$refs?.croppieRefVertical?sel.boundSource:crop.imageUrl)&&
     image.naturalWidth>0&&image.naturalHeight>0;}
 // Evaluate known pure fields for diagnostics only. The live/ready predicates
 // still decide success, including the precisely attested display source.
 function traceLive(sel,role){try{
   gate(role,'live_scope',!!sel&&current(sel.scope));gate(role,'live_single_owned',!!sel&&own(sel.single));
   gate(role,'live_parent_owned',!!sel&&own(sel.parent));gate(role,'live_canvas_match',!!sel&&sel.parent.customCover===sel.canvas);
   gate(role,'live_dataurl_match',!!sel&&typeof sel.displaySource==='string'&&sel.single.imgSrc===sel.displaySource);
   gate(role,'live_custom_preview_match',!!sel&&sel.parent.customCoverPreviewUrl===sel.preview);
   gate(role,'live_current_preview_match',!!sel&&sel.parent.currentCoverImageUrl===sel.preview);
   gate(role,'live_selection_current',!!sel&&root.selections.get(sel.single)===sel);
  }catch(_){count(role,'live_trace_error');}}
 function traceReady(sel,crop,role){traceLive(sel,role);try{
   const croppie=crop?.$refs?.croppieRefHorizon?.croppie||crop?.$refs?.croppieRefVertical?.croppie,image=croppie?.elements?.img;
   gate(role,'ready_live',!!sel&&live(sel));gate(role,'ready_crop_owned',own(crop));
   gate(role,'ready_crop_ref_match',!!sel&&sel.parent.$refs.cropRef===crop);
   gate(role,'ready_parent_open',sel?.parent.value===true);gate(role,'ready_not_loading',sel?.parent.isCroppieLoading===false);
   gate(role,'ready_crop_ready',crop?.isCroppieReady===true);gate(role,'ready_custom',crop?.useCustomCover===true);
   gate(role,'ready_imageurl_match',!!sel&&crop?.imageUrl===sel.preview);
   gate(role,'ready_last_image_match',!!crop&&crop.lastImageUrl===crop.imageUrl);
   gate(role,'ready_bound_image_match',!!sel&&image?.src===(crop?.$refs?.croppieRefVertical?sel.boundSource:crop?.imageUrl));
   gate(role,'ready_image_loaded',!!image&&image.naturalWidth>0&&image.naturalHeight>0);
  }catch(_){count(role,'ready_trace_error');}}
 function installCrop(sel){const crop=sel.parent.$refs?.cropRef,role=sel.traceSlot||slot(sel.parent);
   count(role,'crop_install_attempted');gate(role,'crop_ref_present',!!crop);gate(role,'crop_ref_owned',own(crop));
   if(!crop||!own(crop))return;
   if(crop.$refs?.croppieRefVertical)gate(role,'preprocess_hook',wrap(crop,'preprocessImage',original=>function(...args){
     const selection=root.parents.get(sel.parent),eligible=selection&&live(selection)&&args[0]===selection.preview;
     const traceSlot=selection?.traceSlot||slot(sel.parent);count(traceSlot,'preprocess_called');
     gate(traceSlot,'preprocess_receiver',this===crop);gate(traceSlot,'preprocess_selection',!!selection);
     traceLive(selection,traceSlot);gate(traceSlot,'preprocess_live',!!selection&&live(selection));
     gate(traceSlot,'preprocess_args_match',!!selection&&args[0]===selection.preview);
     const returned=Reflect.apply(original,this,args);
     if(eligible&&this===crop)side(returned,result=>{count(traceSlot,'preprocess_returned');
       try{gate(traceSlot,'preprocess_selection_current',root.selections.get(selection.single)===selection);
        gate(traceSlot,'preprocess_crop_ref_match',selection.parent.$refs.cropRef===crop);
        gate(traceSlot,'preprocess_returned_live',live(selection));
        gate(traceSlot,'preprocess_result_valid',!!result&&typeof result.url==='string'&&!!result.url&&result.rawWidth>0&&result.rawHeight>0);
       }catch(_){count(traceSlot,'preprocess_trace_error');}
       if(root.selections.get(selection.single)===selection&&
       selection.parent.$refs.cropRef===crop&&live(selection)&&
       result&&typeof result.url==='string'&&result.url&&result.rawWidth>0&&result.rawHeight>0){
         selection.boundSource=result.url;count(traceSlot,'preprocess_bound_accepted');}});
     return returned;
   }));
   gate(role,'crop_result_hook',wrap(crop,'getCropResult',original=>function(...args){
     const selection=root.parents.get(sel.parent);let invocation=null;
     const traceSlot=selection?.traceSlot||slot(sel.parent);count(traceSlot,'crop_result_called');
     gate(traceSlot,'crop_receiver',this===crop);gate(traceSlot,'crop_selection',!!selection);
     traceReady(selection,crop,traceSlot);gate(traceSlot,'crop_setting_cover',selection?.parent.isSettingCover===true);
     try{if(this===crop&&selection&&ready(selection,crop)&&selection.parent.isSettingCover===true)
       invocation={selection,crop,id:String(++root.sequence),started_at:Date.now()/1000,valid:true,blob:null,applied:false};}catch(_){}
     if(invocation)count(traceSlot,'crop_invocation_started');
     const returned=Reflect.apply(original,this,args);
     if(invocation){selection.invocation=invocation;side(returned,canvas=>{count(traceSlot,'crop_result_returned');
       if(!invocation.valid||!ready(selection,crop)||!canv(canvas))return;
       count(traceSlot,'crop_result_canvas_accepted');
       invocation.canvas=canvas;
       wrap(canvas,'toBlob',encode=>function(...encodingArgs){
         count(traceSlot,'derived_encoding_called');
         const cb=encodingArgs[0];
         if(this!==canvas||typeof cb!=='function'||selection.invocation!==invocation||!invocation.valid||!ready(selection,crop))
           return Reflect.apply(encode,this,encodingArgs);
         const observed=[...encodingArgs];
         observed[0]=function(blob){
           // Read this actual encoding only; preserve callback receiver, args,
           // return and throws. Do not replace or re-encode its Blob.
           if(invocation.valid&&selection.invocation===invocation&&live(selection)&&nativeBlob(blob)){
             invocation.encoded_at=Date.now()/1000;
             invocation.blob=blob;
             invocation.fingerprint=track(hashBlob(blob).catch(()=>null));
             count(traceSlot,'derived_blob_captured');
           }
           return Reflect.apply(cb,this,arguments);
         };
         return Reflect.apply(encode,this,observed);
       });
     });}
     return returned;
   }));
   gate(role,'crop_original_hook',wrap(crop,'getOriginalCanvas',original=>function(...args){
     const returned=Reflect.apply(original,this,args),s=root.parents.get(sel.parent),inv=s?.invocation;
     if(inv)side(returned,canvas=>{if(canvas===inv.canvas)inv.valid=false;});return returned;
   }));
   gate(role,'crop_result_hook_owner',crop.getCropResult?.__sauTCCoverOwner===root);
 }
 function installSingle(single,parent,preview){
   if(!own(single)||!own(parent)||!own(preview)||single.$el.ownerDocument!==parent.$el.ownerDocument||
     single.$el.ownerDocument!==preview.$el.ownerDocument)return;
   wrap(single,'getFileImageDataURL',original=>function(...args){
     const sel=root.selections.get(single),role=sel?.traceSlot||slot(parent);count(role,'reader_called');
     gate(role,'reader_receiver',this===single);gate(role,'reader_selection',!!sel);
     gate(role,'reader_args_match',!!sel&&args[0]===sel.file);
     const returned=Reflect.apply(original,this,args);
     if(this===single&&sel&&args[0]===sel.file)side(returned,url=>{count(role,'reader_returned');
       gate(role,'reader_scope_current',current(sel.scope));gate(role,'reader_selection_current',root.selections.get(single)===sel);
       gate(role,'reader_dataurl_valid',typeof url==='string'&&/^data:image\/[a-z0-9.+-]+;base64,/i.test(url));
       if(current(sel.scope)&&root.selections.get(single)===sel){sel.dataURL=url;sel.displaySource=url;count(role,'reader_accepted');}});
     return returned;
   });
   // Official SingleCoverUploader watcher (1859154) changes imgSrc from the
   // file-reader URL to this exact current canvas preview. Observe that real
   // method call; neither a matching preview alone nor an unknown setter is
   // an attestation. Keep the original dataURL/native fingerprint unchanged.
   const displayOriginal=root.wrappers.get(single)?.get('handleCustomCoverPreviewUrlChange')?.original||single.handleCustomCoverPreviewUrlChange;
   if(typeof methods(single).handleCustomCoverPreviewUrlChange==='function')
   wrap(single,'handleCustomCoverPreviewUrlChange',original=>function(...args){
     const sel=root.selections.get(single),role=sel?.traceSlot||slot(parent);
     count(role,'display_watcher_called');
     const selected=()=>!!sel&&current(sel.scope)&&root.selections.get(single)===sel&&root.parents.get(parent)===sel&&
       sel.single===single&&sel.parent===parent&&own(single)&&own(parent);
     // onInputChange case25 clears input.value before Image.onload/change;
     // the immutable File was already captured and hashed at native change.
     // Bind the still-current physical ref, not the subsequently empty list.
     const native=()=>!!sel?.input?.isConnected&&single.$refs?.singleCoverUploader===sel.input&&
       single.$el.contains(sel.input)&&nativeFile(sel.file);
     const exactPreview=()=>!!sel?.preview&&parent.customCover===sel.canvas&&canv(sel.canvas)&&
       parent.customCoverPreviewUrl===sel.preview&&parent.currentCoverImageUrl===sel.preview&&single.customCoverPreviewUrl===sel.preview;
     let eligible=false;
     try{
       gate(role,'display_watcher_receiver',this===single);gate(role,'display_selection_current',selected());
       gate(role,'display_native_current',native());gate(role,'display_canvas_match',!!sel&&parent.customCover===sel.canvas);
       gate(role,'display_preview_match',exactPreview());
       gate(role,'display_before_match',!!sel&&typeof sel.displaySource==='string'&&single.imgSrc===sel.displaySource);
       eligible=this===single&&selected()&&native()&&exactPreview()&&typeof sel.displaySource==='string'&&single.imgSrc===sel.displaySource;
     }catch(_){}
     const returned=Reflect.apply(original,this,args);
     try{
       gate(role,'display_after_match',!!sel&&single.imgSrc===sel.preview);
       if(eligible&&selected()&&native()&&exactPreview()&&single.imgSrc===sel.preview){
         sel.displaySource=sel.preview;count(role,'display_source_attested');
       }
     }catch(_){}
     return returned;
   });
   // Vue 2.7 registers the bound callback before this observer mounts. A
   // later replacement of vm.method does not replace that existing callback.
   // Observe only the sole active user Watcher already bound to this exact
   // component and original method; no watcher is created or run here.
   const displayObserved=single.handleCustomCoverPreviewUrlChange;
   if(displayObserved?.__sauTCCoverOwner===root){
     const effects=new Set([...(Array.isArray(single._watchers)?single._watchers:[]),
       ...(Array.isArray(single._scope?.effects)?single._scope.effects:[])]);
     const watchers=[...effects].filter(w=>w?.vm===single&&w.active===true&&w.user===true&&
       (w.cb===displayOriginal||root.wrappers.get(w)?.get('cb')?.original===displayOriginal));
     if(watchers.length===1){
       const watcher=watchers[0];
       wrap(watcher,'cb',original=>function(...args){
         const currentEffects=new Set([...(Array.isArray(single._watchers)?single._watchers:[]),
           ...(Array.isArray(single._scope?.effects)?single._scope.effects:[])]);
         const currentWatchers=[...currentEffects].filter(w=>w?.vm===single&&w.active===true&&w.user===true&&
           (w.cb===displayOriginal||w.cb===root.wrappers.get(w)?.get('cb')?.wrapper&&
            root.wrappers.get(w)?.get('cb')?.original===displayOriginal));
         if(currentWatchers.length===1&&currentWatchers[0]===watcher&&
             watcher.cb===root.wrappers.get(watcher)?.get('cb')?.wrapper&&
             single.handleCustomCoverPreviewUrlChange===displayObserved)
           return Reflect.apply(displayObserved,this,args);
         // Preserve normal Vue behavior on stale/ambiguous registrations,
         // but never attest that unbound display transition as this source.
         return Reflect.apply(original,this,args);
       });
     }
   }
   wrap(single,'$emit',original=>function(...args){
     const sel=root.selections.get(single),canvas=args[0]==='change'?args[1]:null;
     const role=sel?.traceSlot||slot(parent);
     if(args[0]==='change')try{count(role,'single_change_called');gate(role,'change_receiver',this===single);
       gate(role,'change_selection',!!sel);gate(role,'change_canvas_native',canv(canvas));
       gate(role,'change_dataurl_match',!!sel&&sel.dataURL===single.imgSrc);
     }catch(_){count(role,'single_trace_error');}
     let previewURL=null;
     if(this===single&&sel&&canv(canvas)&&sel.dataURL===single.imgSrc){
       wrap(canvas,'toDataURL',encode=>function(...a){const result=Reflect.apply(encode,this,a);
         count(role,'preview_encoding_called');gate(role,'preview_encoding_receiver',this===canvas);
         gate(role,'preview_encoding_selection_current',root.selections.get(single)===sel);
         if(this===canvas&&root.selections.get(single)===sel){previewURL=result;count(role,'preview_encoding_captured');}return result;});
     }
     const returned=Reflect.apply(original,this,args);
     if(args[0]==='change')try{gate(role,'single_preview_present',!!previewURL);
       gate(role,'single_parent_canvas_match',parent.customCover===canvas);
       gate(role,'single_parent_preview_match',parent.customCoverPreviewUrl===previewURL);
       gate(role,'single_parent_custom',parent.useCustomCover===true);
     }catch(_){count(role,'single_trace_error');}
     try{if(sel&&canv(canvas)&&previewURL&&parent.customCover===canvas&&parent.customCoverPreviewUrl===previewURL&&parent.useCustomCover===true){
       count(role,'single_canvas_accepted');
       sel.canvas=canvas;sel.preview=previewURL;sel.parent=parent;sel.previewInstance=preview;
       sel.originalFingerprint=track(hashDataURL(sel.dataURL).catch(()=>null));installCrop(sel);
     }}catch(_){}return returned;
   });
   wrap(parent,'$emit',original=>function(...args){
     const sel=root.parents.get(parent),inv=sel?.invocation,role=slot(parent);
     if(args[0]==='updateCover')count(role,'update_cover_called');
     let payload=null;
     try{if(this===parent&&args[0]==='updateCover'&&args[3]===true&&args[4]===parent.previewMode&&role&&
       inv?.valid&&!inv.applied&&inv.blob&&ready(sel,inv.crop)&&parent.isSettingCover===true&&
       args[1]?.coverImgs?.length===1)payload=args[1].coverImgs[0];}catch(_){}
     const returned=Reflect.apply(original,this,args);
     try{if(payload&&typeof payload.httpsUrl==='string'&&payload.httpsUrl){
       count(role,'applied_candidate_accepted');
       inv.applied=true;const applied_at=Date.now()/1000;
       const applied_id=payload.httpsUrl,blob=inv.blob;
       track(Promise.all([sel.nativeFingerprint,sel.originalFingerprint,inv.fingerprint]).then(([native,origin,derived])=>{
         if(!inv.valid||!current(sel.scope)||!native||!origin||!derived||native.sha256!==origin.sha256||native.size!==origin.size||
           native.sha256!==sel.scope.covers[role]?.sha256||!live(sel)||
           preview.video?.[role==='cover_landscape'?'shareCoverUrl':'coverUrl']!==applied_id)return;
         const name=blob.name,type=Reflect.apply(Object.getOwnPropertyDescriptor(Blob.prototype,'type').get,blob,[]);
         if(name!=='finder_video_img.jpeg'||type!=='image/jpeg')return;
         count(role,'applied_report_delivered');
         deliver({event:'applied',invocation_id:inv.id,slot:role,origin_name:sel.name,origin_size:native.size,
           origin_sha256:native.sha256,selected_at:sel.selected_at,derived_name:name,derived_size:derived.size,
           derived_sha256:derived.sha256,derived_type:type,encoded_at:inv.encoded_at,applied_at,applied_id},sel.scope);
       }).catch(()=>{}));
     }else if(this===parent&&args[0]==='input'&&args[1]===false&&inv&&!inv.applied)inv.valid=false;
     }catch(_){}return returned;
   });
   const traceSlot=slot(parent);
   gate(traceSlot,'single_reader_hook_owner',single.getFileImageDataURL?.__sauTCCoverOwner===root);
   gate(traceSlot,'single_emit_hook_owner',single.$emit?.__sauTCCoverOwner===root);
   gate(traceSlot,'parent_emit_hook_owner',parent.$emit?.__sauTCCoverOwner===root);
   return true;
 }
 function inspect(){
   if(!root.scope)return;
   for(const doc of root.roots.keys())for(const el of doc.querySelectorAll('.single-cover-uploader-wrap')){
     const chain=ancestors(el),singles=chain.filter(v=>key(v)==='single'&&own(v)),parents=chain.filter(v=>key(v)==='parent'&&own(v)),previews=chain.filter(v=>key(v)==='preview'&&own(v));
     if(singles.length===1&&parents.length===1&&previews.length===1){
       root.available=!!installSingle(singles[0],parents[0],previews[0]);
       const sel=root.selections.get(singles[0]);if(sel?.canvas)installCrop(sel);
     }
   }
 }
 function selected(e){
   if(root.seenEvents.has(e))return;root.seenEvents.add(e);
   try{const input=e.composedPath().find(n=>n?.tagName==='INPUT'&&n.type==='file');
     if(!input||!input.closest('.single-cover-uploader-wrap')||input.files.length!==1)return;
     inspect();const chain=ancestors(input),ss=chain.filter(v=>key(v)==='single'&&own(v)),ps=chain.filter(v=>key(v)==='parent'&&own(v));
     if(ss.length!==1||ps.length!==1)return;const single=ss[0],parent=ps[0],role=slot(parent),s=root.scope,file=input.files[0];
     const owner=single.getFileImageDataURL?.__sauTCCoverOwner;
     if(owner&&owner!==root){if(owner.scope?.observer_nonce===s?.observer_nonce)owner.capture(e);return;}
     if(!role||!s?.covers[role]||!nativeFile(file))return;
     const selected_at=Date.now()/1000;deliver({event:'invalidate',slot:role,selected_at},s);
     const previous=root.selections.get(single);if(previous?.invocation)previous.invocation.valid=false;
     root.selections.delete(single);root.parents.delete(parent);
     if(file.name!==s.covers[role].name||file.size!==s.covers[role].size)return;
     const sel={scope:s,single,parent,file,input,name:file.name,selected_at,revision:++root.revision,traceSlot:role};
     count(role,'selection_accepted');
     sel.nativeFingerprint=track(hashBlob(file).catch(()=>null));root.selections.set(single,sel);root.parents.set(parent,sel);
     
   }catch(_){}
 }
 function scan(node){if(!node?.querySelectorAll)return;
   if(!root.roots.has(node)){node.addEventListener('change',selected,true);const mo=new MutationObserver(()=>{try{scan(node);inspect();}catch(_){}});
     mo.observe(node,{childList:true,subtree:true});root.roots.set(node,mo);}
   for(const e of node.querySelectorAll('*'))if(e.shadowRoot)scan(e.shadowRoot);
 }
 root.capture=selected;
 root.configure=s=>{if(!s?.job_id||!s.receipt_nonce||!s.observer_nonce||!s.covers)return false;
   if(root.trace_nonce!==s.observer_nonce){root.trace=freshTrace();root.trace_nonce=s.observer_nonce;}
   for(const role of ['cover_landscape','cover_portrait'])if(s.covers[role])count(role,'configured');
   root.scope=s;scan(document);inspect();return root.available;};
 root.flush=async nonce=>{if(root.scope?.observer_nonce!==nonce)return false;scan(document);inspect();
   for(let n=0;n<5;n++){await Promise.resolve();const pending=[...root.pending];if(!pending.length)break;await Promise.allSettled(pending);}return true;};
 root.close=nonce=>{if(root.scope?.observer_nonce!==nonce)return false;root.scope=null;
   for(const [node,mo]of root.roots){node.removeEventListener('change',selected,true);mo.disconnect();}root.roots.clear();
   for(const [obj,map]of root.wrappers)for(const [name,x]of map)try{if(obj[name]===x.wrapper){if(x.descriptor)Object.defineProperty(obj,name,x.descriptor);else delete obj[name];}}catch(_){}
   root.wrappers.clear();root.selections=new WeakMap();root.parents=new WeakMap();return true;};
 window.__sauTCCoverObserver=root;
})()""".replace('__TRACE_EVENTS__', json.dumps(COVER_TRACE_EVENTS)).replace('__TRACE_GATES__', json.dumps(COVER_TRACE_GATES))


async def _evaluate(frame, script):
    try:
        return await frame.evaluate(script, isolated_context=False)
    except TypeError:
        return await frame.evaluate(script)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


class TCCoverObserver:
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.observer_nonce = secrets.token_hex(24)
        self.started_at = time.time()
        self.closed = False
        self.available = False
        self.records = {}
        self.invalid = set()
        self.frames = set()
        self.tasks = set()
        self.invalidated = {}

    def _current(self):
        return (not self.closed and not self.session.closed and
                getattr(self.page, '_sau_draft_evidence', None) is self.session and
                getattr(self.session, 'tc_cover_observer', None) is self)

    @property
    def scope(self):
        covers = {slot: {key: asset[key] for key in ('name', 'size', 'sha256')}
                  for slot, asset in self.session.assets.items()
                  if slot in ('cover_portrait', 'cover_landscape') and asset.get('kind') == 'cover' and
                  asset.get('sha256') == self.session.expected.get('cover_sha256', {}).get(slot)}
        return {'job_id': self.session.expected.get('job_id'),
                'receipt_nonce': self.session.expected.get('receipt_nonce'),
                'observer_nonce': self.observer_nonce, 'covers': covers}

    async def initialize(self):
        scope = self.scope
        if (not scope['covers'] or not all(isinstance(scope[k], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', scope[k])
                                         for k in ('job_id', 'receipt_nonce', 'observer_nonce'))):
            return
        page = self.page
        if not getattr(page, '_sau_tc_cover_binding', False):
            async def observed(source, report):
                observer = getattr(getattr(page, '_sau_draft_evidence', None), 'tc_cover_observer', None)
                if observer:
                    observer.accept(source, report)
            await page.expose_binding('__sauTCCoverObserved', observed)
            await page.add_init_script(TC_COVER_OBSERVER_SCRIPT)
            page._sau_tc_cover_binding = True
        page.on('framenavigated', self._navigated)
        for frame in page.frames:
            await self._configure(frame)

    def _navigated(self, frame):
        if self._current():
            task = asyncio.create_task(self._configure(frame))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _configure(self, frame):
        if not self._current():
            return
        try:
            await _evaluate(frame, TC_COVER_OBSERVER_SCRIPT)
            if self._current():
                self.frames.add(frame)
                self.available |= bool(await _evaluate(frame, 'window.__sauTCCoverObserver.configure(' + json.dumps(self.scope) + ')'))
        except Exception:
            pass  # Unsupported or navigating frame has no positive evidence.

    def accepts_upload_metadata(self, metadata):
        return (self._current() and bool(self.scope['covers']) and isinstance(metadata, (tuple, list)) and
                len(metadata) == 2 and metadata[0] == 'finder_video_img.jpeg' and
                type(metadata[1]) is int and 0 < metadata[1] <= MAX_COVER_BYTES)

    def accept(self, source, report):
        if (not self._current() or not isinstance(source, dict) or source.get('frame') not in self.frames or
                not isinstance(report, dict) or type(report.get('v')) is not int or report['v'] != 1 or
                any(report.get(k) != self.scope[k] for k in ('job_id', 'receipt_nonce', 'observer_nonce')) or
                report.get('covers') != self.scope['covers']):
            return False
        slot, event = report.get('slot'), report.get('event')
        if slot not in self.scope['covers']:
            return False
        selected = report.get('selected_at')
        if type(selected) not in (int, float) or not math.isfinite(selected) or not self.started_at <= selected <= time.time()+5:
            return False
        if event == 'invalidate':
            if set(report) != {'v', 'job_id', 'receipt_nonce', 'observer_nonce', 'covers', 'event', 'slot', 'selected_at'}:
                return False
            self.invalidated[slot] = max(selected, self.invalidated.get(slot, 0))
            self.records = {k: v for k, v in self.records.items() if v['slot'] != slot or v['selected_at'] >= selected}
            return True
        wanted = {'v','job_id','receipt_nonce','observer_nonce','covers','event','slot','invocation_id','origin_name',
                  'origin_size','origin_sha256','selected_at','derived_name','derived_size','derived_sha256',
                  'derived_type','encoded_at','applied_at','applied_id'}
        if event != 'applied' or set(report) != wanted or not isinstance(report.get('invocation_id'), str) or not re.fullmatch(r'[1-9][0-9]{0,15}', report['invocation_id']):
            return False
        asset = self.scope['covers'][slot]
        if ((report.get('origin_name'), report.get('origin_size'), report.get('origin_sha256')) !=
                (asset['name'], asset['size'], asset['sha256']) or
                not _digest(report.get('origin_sha256')) or not _digest(report.get('derived_sha256')) or
                report.get('derived_type') != 'image/jpeg' or
                not self.accepts_upload_metadata((report.get('derived_name'), report.get('derived_size')))):
            return False
        for key in ('encoded_at', 'applied_at'):
            if type(report.get(key)) not in (int, float) or not math.isfinite(report[key]):
                return False
        if not selected <= report['encoded_at'] <= report['applied_at'] <= time.time()+5 or selected < self.invalidated.get(slot, 0):
            return False
        value = report.get('applied_id')
        if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r'[\x00-\x20\x7f]', value):
            return False
        key = report['invocation_id']
        if key in self.invalid:
            return False
        if key in self.records and self.records[key] != report:
            self.records.pop(key, None)
            self.invalid.add(key)
            return False
        self.records[key] = dict(report)
        return True

    def _resources(self, record, counts=None):
        from uploader.draft_evidence import _tc_resource_aliases
        matching_ids = set(_tc_resource_aliases(record['applied_id']))
        if counts is not None:
            metadata = (record['derived_name'], record['derived_size'])
            start, end = record['encoded_at'], record['applied_at']
            matching = [x for x in self.session.exchanges if x.get('tc_protocol') in ('sns', 'dfs') and x.get('metadata') == metadata]
            within = [x for x in matching if start <= x.get('observed_at', -1) <= end]
            complete = [x for x in within if x.get('stage') == 'complete']
            counts.update(metadata_match_count=len(matching), time_window_match_count=len(within),
                complete_candidate_count=len(complete),
                body_sha_match_count=sum(x.get('body_sha256') == record['derived_sha256'] for x in within),
                applied_resource_match_count=sum(bool(matching_ids.intersection(x.get('ids', []))) for x in complete))
        if not matching_ids:
            return []
        metadata = (record['derived_name'], record['derived_size'])
        start, end = record['encoded_at'], record['applied_at']
        exchanges = list(self.session.exchanges)
        resources = {}
        sns = {}
        for x in exchanges:
            if x.get('tc_protocol') == 'sns':
                sns.setdefault(x.get('task_id'), []).append(x)
        for task, group in sns.items():
            if not task or any(x.get('metadata') != metadata or not start <= x.get('observed_at', -1) <= end for x in group):
                continue
            cursor = 0
            for left, right in sorted({tuple(x.get('range', ())) for x in group}):
                if left != cursor:
                    break
                cursor = right
            else:
                terminals = [x for x in group if x.get('stage') == 'complete']
                sets = {tuple(x.get('ids', [])) for x in terminals}
                if cursor == metadata[1] and len(sets) == 1 and any(x.get('whole_body') and x.get('body_sha256') == record['derived_sha256'] for x in terminals):
                    ids = sets.pop()
                    if matching_ids.intersection(ids):
                        resources[('sns', task)] = tuple(sorted(ids))
        dfs = [x for x in exchanges if x.get('tc_protocol') == 'dfs']
        allocations = [x for x in dfs if 'plan' in x]
        for allocation in allocations:
            if allocation.get('metadata') != metadata or allocation.get('plan') != [metadata[1]] or not start <= allocation.get('observed_at', -1) <= end:
                continue  # No concatenation-by-per-part-hash claim.
            task, upload = allocation.get('task_id'), allocation.get('upload_id')
            same = [x for x in allocations if x.get('task_id') == task]
            if not task or not upload or any(not start <= x.get('observed_at', -1) <= end for x in same) or len({(x.get('upload_id'), x.get('metadata'), tuple(x.get('plan', []))) for x in same}) != 1:
                continue
            finishes = [x for x in dfs if x.get('stage') == 'complete' and x.get('task_id') == task and x.get('upload_id') == upload and x.get('metadata') == metadata and allocation['observed_at'] <= x.get('observed_at', 0) <= end]
            for finish in finishes:
                parts = [x for x in dfs if 'part_number' in x and x.get('task_id') == task and x.get('upload_id') == upload and x.get('metadata') == metadata and allocation['observed_at'] <= x.get('observed_at', 0) <= finish['observed_at']]
                if (finish.get('parts') != [(1, next(iter({x.get('etag') for x in parts}), None))] or
                        {x.get('part_number') for x in parts} != {1} or len({x.get('etag') for x in parts}) != 1 or
                        not any(x.get('trans_flag') == finish.get('trans_flag') and x.get('body_sha256') == record['derived_sha256'] for x in parts)):
                    continue
                ids = finish.get('ids', [])
                if matching_ids.intersection(ids):
                    identity = ('dfs', task, upload)
                    value = tuple(sorted(ids))
                    resources[identity] = value if identity not in resources or resources[identity] == value else None
        if counts is not None:
            counts['resource_chain_count'] = len(resources)
        value = next(iter(resources.values()), None) if len(resources) == 1 else None
        return list(value) if value else []

    def freeze_records(self, candidate, links):
        """Preserve only verified original→crop→upload records for saved roles."""
        import copy
        if not self._current():
            return None
        output = {}
        for slot, asset in self.scope['covers'].items():
            ids = candidate.get('cover_slots', {}).get(slot, [])
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
                return None
            matches = [record for record in self.records.values() if record['slot'] == slot and
                record['origin_sha256'] == asset['sha256'] and ids[0] in self._resources(record) and
                record['selected_at'] >= self.invalidated.get(slot, 0)]
            if len(matches) != 1:
                return None
            record = matches[0]
            chain_ids = self._resources(record)
            valid = [link for link in self.upload_links() if link['sha256'] == asset['sha256'] and link['ids'] == chain_ids and link in links]
            if len(valid) != 1:
                return None
            output[slot] = {'record': copy.deepcopy(record), 'ids': list(ids), 'chain_ids': list(chain_ids), 'link': copy.deepcopy(valid[0])}
        return output

    def frozen_current(self, frozen):
        try:
            current = self.upload_links()
            return (self._current() and isinstance(frozen, dict) and set(frozen) == set(self.scope['covers']) and
                all(value['record'] == self.records.get(value['record']['invocation_id']) and
                    value['record']['selected_at'] >= self.invalidated.get(slot, 0) and
                    len(value['ids']) == 1 and value['ids'][0] in value['chain_ids'] and
                    self._resources(value['record']) == value['chain_ids'] and
                    value['link']['ids'] == value['chain_ids'] and value['link'] in current
                    for slot, value in frozen.items()))
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def upload_links(self):
        if not self._current():
            return []
        links = []
        for record in self.records.values():
            matching = [x for x in self.session.files if
                        (x.get('name'), x.get('size'), x.get('sha256')) ==
                        (record['origin_name'], record['origin_size'], record['origin_sha256']) and
                        abs(x.get('selected_at', -100)-record['selected_at']) <= .5 and
                        self.session.started_at <= x.get('selected_at', -1) <= record['encoded_at']]
            if len(matching) != 1:
                continue
            try:
                ids = self._resources(record)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if ids:
                links.append({'kind': 'cover', 'sha256': record['origin_sha256'], 'ids': ids, 'complete': True})
        return links

    def diagnostic_counts(self):
        """Content-free counts from existing evidence; never mutate or probe UI.

        Native and resource counts are summed over accepted applied records per
        slot. Resource chains use the exact success gates in _resources; two
        eligible chains remain ambiguous and do not contribute a link. Empty,
        unrequested and noncurrent slots always have the same zero-only schema.
        """
        result = {slot: dict.fromkeys(COVER_DIAGNOSTIC_KEYS, 0) for slot in COVER_DIAGNOSTIC_SLOTS}
        if not self._current():
            return result
        wanted = self.scope['covers']
        for record in self.records.values():
            if (not isinstance(record, dict) or record.get('slot') not in COVER_DIAGNOSTIC_SLOTS
                    or record['slot'] not in wanted):
                continue
            counts = result[record['slot']]
            counts['records_count'] += 1
            resource_counts = dict.fromkeys(COVER_DIAGNOSTIC_KEYS, 0)
            try:
                matching = [x for x in self.session.files if
                    (x.get('name'), x.get('size'), x.get('sha256')) ==
                    (record['origin_name'], record['origin_size'], record['origin_sha256']) and
                    abs(x.get('selected_at', -100)-record['selected_at']) <= .5 and
                    self.session.started_at <= x.get('selected_at', -1) <= record['encoded_at']]
                counts['native_match_count'] += len(matching)
                ids = self._resources(record, counts=resource_counts)
                counts['links_count'] += int(len(matching) == 1 and bool(ids))
            except (ValueError, TypeError, KeyError, AttributeError):
                pass  # Diagnostics never echo raw exceptions or stored values.
            for key in COVER_DIAGNOSTIC_KEYS[3:]:
                counts[key] += resource_counts[key]
        return result

    async def diagnostic_trace(self):
        """Read fixed gate counters, separately per main/owner realm.

        This never flushes, installs hooks, invokes Vue methods or creates
        evidence. Closed observers remain readable for their original nonce;
        a later execution's counters cannot be reported as this execution's.
        Missing/unsupported/malformed roots produce no positive diagnostics.
        Counts are deliberately not summed across projected frame realms.
        """
        result = {'main': _empty_trace(), 'owner': []}
        script = """(() => {
          const root=window.__sauTCCoverObserver,slots=SLOTS,keys=KEYS;
          if(!root || root.trace_version!==3 || root.trace_nonce!==NONCE ||
             !root.trace || Object.keys(root.trace).length!==slots.length)return null;
          const output={};
          for(const slot of slots){
            const value=root.trace[slot];
            if(!value || Object.keys(value).length!==keys.length)return null;
            output[slot]={};
            for(const key of keys){
              const count=value[key];
              if(!Number.isSafeInteger(count)||count<0||count>1000000000)return null;
              output[slot][key]=count;
            }
          }
          return output;
        })()""".replace('SLOTS', json.dumps(COVER_DIAGNOSTIC_SLOTS)).replace(
            'KEYS', json.dumps(COVER_TRACE_KEYS)).replace('NONCE', json.dumps(self.observer_nonce))
        try:
            frames = tuple(self.frames)
            main = self.page.main_frame
        except (AttributeError, TypeError):
            return result
        for frame in frames:
            try:
                trace = await asyncio.wait_for(_evaluate(frame, script), timeout=5)
                if type(trace) is not dict or set(trace) != set(COVER_DIAGNOSTIC_SLOTS):
                    continue
                if any(type(values) is not dict or set(values) != set(COVER_TRACE_KEYS) or
                       any(type(value) is not int or not 0 <= value <= 1000000000
                           for value in values.values()) for values in trace.values()):
                    continue
                detached = {slot: {key: trace[slot][key] for key in COVER_TRACE_KEYS}
                            for slot in COVER_DIAGNOSTIC_SLOTS}
                if frame is main:
                    result['main'] = detached
                else:
                    result['owner'].append(detached)
            except Exception:
                continue
        return result

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self._current():
            for frame in tuple(self.frames):
                try:
                    await _evaluate(frame, 'window.__sauTCCoverObserver?.flush(' + json.dumps(self.observer_nonce) + ')')
                except Exception:
                    pass

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.scope['covers']:
            try:
                self.page.remove_listener('framenavigated', self._navigated)
            except Exception:
                pass
        for frame in tuple(self.frames):
            try:
                await _evaluate(frame, 'window.__sauTCCoverObserver?.close(' + json.dumps(self.observer_nonce) + ')')
            except Exception:
                pass
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


async def install_tc_cover_observer(session):
    observer = TCCoverObserver(session)
    session.tc_cover_observer = observer
    if session.platform == 'tencent' and observer.scope['covers']:
        await observer.initialize()
    return observer
