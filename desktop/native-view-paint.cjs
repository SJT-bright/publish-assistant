'use strict';

// Refresh the native compositor surface without navigating the renderer. In a
// stale macOS child surface the DOM and media can be live behind a white view.
// A real bounds change invalidates that surface; restore the latest layout on
// the next frame. Never change zoom preferences, focus, cookies or page state.
function createNativeViewPainter({canPaint, bounds, settled=()=>{}, delay=32}) {
  const pending=new Map();
  let stopping=false;
  const alive=view=>{try{return !!view?.webContents && !view.webContents.isDestroyed();}catch{return false;}};
  const displayed=view=>{try{return alive(view) && canPaint(view)===true;}catch{return false;}};
  const currentBounds=(minimumWidth=1)=>{
    try {
      const value=bounds();
      return value && ['x','y','width','height'].every(key=>Number.isInteger(value[key]))
        && value.width>=minimumWidth && value.height>=1 ? {...value}:null;
    } catch {return null;}
  };
  function finish(view,item,result) {
    if(pending.get(view)!==item)return;
    clearTimeout(item.timer);pending.delete(view);
    item.signal?.removeEventListener('abort',item.aborted);
    item.resolve(result);
  }
  function restore(view,item) {
    if(!item.nudged || !alive(view))return true;
    const target=currentBounds();
    if(!target)return false;
    try{view.setBounds(target);return true;}catch{return false;}
  }
  function cancel(view) {
    const item=pending.get(view);
    if(!item)return;
    finish(view,item,false);
    restore(view,item);
  }
  function repair(view,{signal}={}) {
    if(stopping)return Promise.resolve(false);
    cancel(view);
    if(signal?.aborted || !displayed(view))return Promise.resolve(false);
    const target=currentBounds(2);
    if(!target)return Promise.resolve(false);
    return new Promise(resolve=>{
      const item={resolve,nudged:false,timer:null,signal,aborted:()=>cancel(view)};
      pending.set(view,item);
      signal?.addEventListener('abort',item.aborted,{once:true});
      if(signal?.aborted){cancel(view);return;}
      item.nudged=true;
      try{view.setBounds({...target,width:target.width-1});}
      catch{restore(view,item);finish(view,item,false);return;}
      if(pending.get(view)!==item)return;
      item.timer=setTimeout(()=>{
        if(pending.get(view)!==item)return;
        if(!alive(view)){finish(view,item,false);return;}
        // A resize or progress-bar transition can occur during the frame.
        // Restore that current layout, never the original stale rectangle.
        const restored=restore(view,item);
        if(pending.get(view)!==item)return;
        const visible=restored && displayed(view) && !signal?.aborted;
        finish(view,item,visible);
        if(visible){try{settled();}catch{/* Painting has already completed. */}}
      },delay);
      // Awaited repairs keep their frame timer referenced until a receipt.
    });
  }
  function queue(view) {
    if(stopping)return;
    cancel(view);
    if(!displayed(view))return;
    const item={nudged:false,resolve:()=>{},timer:null};pending.set(view,item);
    item.timer=setTimeout(()=>{finish(view,item,false);void repair(view);},0);
    item.timer.unref?.();
  }
  function stop(){stopping=true;try{for(const view of [...pending.keys()])cancel(view);}finally{stopping=false;}}
  return {repair,queue,cancel,stop};
}
module.exports={createNativeViewPainter};
