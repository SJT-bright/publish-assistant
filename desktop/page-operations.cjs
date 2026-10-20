'use strict';

// One visible page operation owns its lifecycle callbacks. Navigation events
// prove reload completion; resolving reload() or a timer never proves it.
const active = new WeakMap();
let sequence = 0;
const ALLOWED_KINDS = new Set(['reload', 'repair']);

function originOf(url) {
  try { return new URL(url).origin; } catch { return null; }
}

function runPageOperation({kind, view, isCurrent, onState=()=>{}, repair,
    signal, timeoutMs=30000, pollMs=50, now=Date.now}={}) {
  if (!ALLOWED_KINDS.has(kind) || typeof isCurrent !== 'function'
      || typeof onState !== 'function' || typeof now !== 'function'
      || !Number.isFinite(timeoutMs) || timeoutMs<=0
      || !Number.isFinite(pollMs) || pollMs<=0) {
    throw new TypeError('Page operation requires a kind, current-view guard and bounded timeout.');
  }
  const wc=view?.webContents;
  const startTime=now(), operationId=++sequence;
  const base={kind,operationId,startTime,finishedTime:null};
  const current=()=>{try{return isCurrent(view)===true;}catch{return false;}};
  const alive=()=>{try{return !!wc && !wc.isDestroyed();}catch{return false;}};
  const unavailable=(detail,code)=>Promise.resolve({...base,phase:'failed',status:'failed',
    finishedTime:now(),detail,errorCode:code});
  if (!wc || !alive()) return unavailable('平台页面已关闭，请重新打开平台页面。','PAGE_UNAVAILABLE');
  if (!current()) return Promise.resolve({...base,phase:'cancelled',status:'cancelled',
    finishedTime:now(),detail:'已切换平台页面，本次操作已取消。',errorCode:'PAGE_CHANGED'});
  if (kind==='repair' && typeof repair !== 'function') {
    throw new TypeError('Display repair requires the native painter callback.');
  }

  // Superseding a pending action must not leave listeners or a late UI update.
  active.get(wc)?.cancel('操作已被新的页面操作替代。','PAGE_SUPERSEDED');
  return new Promise(resolve=>{
    const controller=new AbortController();
    let finished=false, timeoutTimer, guardTimer;
    let navigationSeen=false, domReady=false, loadFinished=false, loadStarted=false;
    const listeners=[];
    const url=()=>{try{return wc.getURL?.();}catch{return null;}};
    const initialOrigin=originOf(url());
    const entry={cancel,unloadDecision};
    active.set(wc,entry);

    function notify(state) {
      if (active.get(wc)!==entry || !current()) return;
      try {
        const result=onState({...base,...state});
        if (result && typeof result.catch==='function') result.catch(()=>{});
      } catch { /* Feedback failure cannot trigger another page operation. */ }
    }
    function cleanup() {
      clearTimeout(timeoutTimer); clearInterval(guardTimer);
      for (const [name,handler] of listeners) wc.removeListener(name,handler);
      signal?.removeEventListener('abort',aborted);
      if (active.get(wc)===entry) active.delete(wc);
    }
    function finish(status,detail,errorCode,extra={},stopLoading=false) {
      if (finished) return;
      finished=true;
      // Set the terminal guard before stop(): Electron can synchronously emit
      // a failed navigation while stopping. It must not replace cancellation.
      if (stopLoading) stopOwnLoad();
      const finishedTime=now();
      const state={phase:status==='completed'?'finished':status,status,
        finishedTime,elapsedMs:Math.max(0,finishedTime-startTime),detail,...extra};
      if (errorCode) state.errorCode=errorCode;
      notify(state);
      cleanup();
      resolve({...base,...state});
    }
    function stopOwnLoad() {
      if (kind==='reload' && loadStarted && current() && alive() && active.get(wc)===entry) {
        try { wc.stop(); } catch { /* The page may be closing. */ }
      }
    }
    function cancel(detail='本次页面操作已停止。',code='PAGE_CANCELLED') {
      if (finished) return;
      controller.abort();
      finish('cancelled',detail,code,{},true);
    }
    function aborted() {cancel();}
    function armTimeout() {
      clearTimeout(timeoutTimer);
      timeoutTimer=setTimeout(()=>{
        controller.abort();
        finish('failed',kind==='reload'?'页面加载超时，未收到完整加载回执。':'显示修复超时，请再次尝试。',
          kind==='reload'?'PAGE_LOAD_TIMEOUT':'PAGE_REPAIR_TIMEOUT',{},true);
      },timeoutMs);
    }
    function unloadDecision(decision) {
      if(finished || kind!=='reload')return;
      if(decision==='waiting') {
        clearTimeout(timeoutTimer);
        notify({phase:'awaiting_choice',status:'running',detail:'页面有未保存内容，等待是否保留当前页面…'});
      } else if(decision==='continue') {
        armTimeout();
        notify({phase:'loading',status:'running',detail:'正在重新加载平台页面，等待页面内容加载完成…'});
      } else if(decision==='cancel') {
        cancel('已保留当前页面，视频和填写内容未清空。','PAGE_UNLOAD_CANCELLED');
      }
    }
    function listen(name,handler) {wc.on(name,handler);listeners.push([name,handler]);}
    function succeeded() {
      if (!finished && navigationSeen && domReady && loadFinished) {
        if (!current()) {cancel('已切换平台页面，本次操作已取消。','PAGE_CHANGED');return;}
        const finalOrigin=originOf(url());
        if (!initialOrigin || finalOrigin!==initialOrigin) {
          finish('failed','平台跳转到了其他页面，请确认当前页面后重试。','PAGE_ORIGIN_CHANGED');return;
        }
        finish('completed','平台页面已重新加载完成。',undefined,
          {domReady:true,loadFinished:true});
      }
    }
    listen('destroyed',()=>finish('failed','平台页面已关闭，本次操作未完成。','PAGE_DESTROYED'));
    signal?.addEventListener('abort',aborted,{once:true});
    guardTimer=setInterval(()=>{
      if (!current()) cancel('已切换平台页面，本次操作已取消。','PAGE_CHANGED');
      else if (!alive()) finish('failed','平台页面已关闭，本次操作未完成。','PAGE_DESTROYED');
    },pollMs);
    armTimeout();

    if (signal?.aborted) {cancel();return;}
    notify({phase:kind==='reload'?'loading':'painting',status:'running',
      detail:kind==='reload'?'正在重新加载平台页面，等待页面内容加载完成…':'正在修复显示，刷新页面绘制…'});
    if (finished) return;
    if (kind==='repair') {
      listen('did-start-navigation',(_event,_url,isInPlace,isMainFrame)=>{
        if (isMainFrame===true && isInPlace!==true)
          cancel('页面正在切换，本次显示修复已取消。','PAGE_NAVIGATED');
      });
      // The callback uses only native bounds changes; it never reloads or
      // executes page JavaScript. The abort signal cancels its pending frame.
      Promise.resolve().then(()=>finished?false:repair(view,{signal:controller.signal})).then(ok=>{
        if (finished) return;
        if (!current()) {cancel('已切换平台页面，本次操作已取消。','PAGE_CHANGED');return;}
        if (ok===true) finish('completed','显示已修复，当前视频和填写内容已保留。');
        else finish('failed','当前页面无法完成显示修复，请打开平台页面后重试。','PAGE_REPAIR_FAILED');
      },()=>finish('failed','显示修复失败，请再次尝试。','PAGE_REPAIR_FAILED'));
      return;
    }

    listen('did-start-navigation',(_event,url,isInPlace,isMainFrame)=>{
      if (finished || isMainFrame!==true || isInPlace===true) return;
      if (!initialOrigin || originOf(url)!==initialOrigin) {
        finish('failed','平台跳转到了其他页面，请确认当前页面后重试。','PAGE_ORIGIN_CHANGED');return;
      }
      navigationSeen=true;domReady=false;loadFinished=false;
    });
    listen('dom-ready',()=>{if(navigationSeen){domReady=true;succeeded();}});
    listen('did-finish-load',()=>{if(navigationSeen){loadFinished=true;succeeded();}});
    listen('did-fail-load',(_event,errorCode,_description,validatedURL,isMainFrame)=>{
      if (finished || isMainFrame!==true) return;
      if (errorCode===-3 && initialOrigin && originOf(validatedURL)===initialOrigin) {
        // ERR_ABORTED can be the old request cancelled by the platform's own
        // same-origin navigation. Keep loading until the successor actually
        // produces both terminal events, or the bounded timeout expires.
        notify({phase:'loading',status:'running',detail:'平台正在跳转，等待页面完成加载…'});
        return;
      }
      finish('failed','平台页面加载失败，请检查页面后重试。','PAGE_LOAD_FAILED',
        {nativeErrorCode:Number.isInteger(errorCode)?errorCode:null});
    });
    try {
      loadStarted=true;
      const result=wc.reload();
      if (result && typeof result.catch==='function') result.catch(()=>
        finish('failed','重新加载未能启动，请再次尝试。','PAGE_RELOAD_FAILED'));
    } catch {
      finish('failed','重新加载未能启动，请再次尝试。','PAGE_RELOAD_FAILED');
    }
  });
}

function notifyPageUnload(wc,decision) {
  if(!['waiting','continue','cancel'].includes(decision))return;
  active.get(wc)?.unloadDecision(decision);
}

module.exports={runPageOperation,notifyPageUnload};
