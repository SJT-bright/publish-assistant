'use strict';

// Refresh the native compositor surface without navigating the renderer. In a
// stale macOS child surface the DOM and media can be live behind a white view.
// A real bounds change invalidates that surface; restore the latest layout on
// the next frame. Never change zoom preferences, focus, cookies or page state.
function createNativeViewPainter({canPaint, bounds, settled=()=>{}, delay=32}) {
  const pending=new Map();
  const alive=view=>view?.webContents && !view.webContents.isDestroyed();
  function cancel(view) {
    const item=pending.get(view);
    if(!item)return;
    clearTimeout(item.timer);pending.delete(view);
    if(item.nudged && alive(view))view.setBounds(bounds());
    item.resolve(false);
  }
  function repair(view) {
    cancel(view);
    if(!alive(view) || !canPaint(view))return Promise.resolve(false);
    const target=bounds();
    if(!Number.isInteger(target.width) || target.width<2 || target.height<1)return Promise.resolve(false);
    return new Promise(resolve=>{
      const item={resolve,nudged:true,timer:null};pending.set(view,item);
      view.setBounds({...target,width:target.width-1});
      item.timer=setTimeout(()=>{
        pending.delete(view);
        if(!alive(view)){resolve(false);return;}
        // A resize or progress-bar transition can occur during the frame.
        // Restore that current layout, never the original stale rectangle.
        view.setBounds(bounds());
        const displayed=canPaint(view);if(displayed)settled();resolve(displayed);
      },delay);
      item.timer.unref?.();
    });
  }
  function queue(view) {
    cancel(view);
    const item={nudged:false,resolve:()=>{},timer:null};pending.set(view,item);
    item.timer=setTimeout(()=>{pending.delete(view);void repair(view);},0);
    item.timer.unref?.();
  }
  function stop(){for(const view of [...pending.keys()])cancel(view);}
  return {repair,queue,cancel,stop};
}
module.exports={createNativeViewPainter};
