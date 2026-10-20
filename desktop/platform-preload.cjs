// No privileged bridge is exposed to the page. The click guard applies to manual and automated clicks.
const {ipcRenderer}=require('electron');
let manualPublishAllowed=false, scopedLease=null, scopedClickConsumed=false, scopedSubmitConsumed=false, scopedConfirmConsumed=false;
ipcRenderer.on('publication-mode',(_event,value)=>{
  manualPublishAllowed=value===true;
  const lease=value?.once===true && typeof value.lease==='string'?value.lease:null;
  if(lease!==scopedLease){scopedLease=lease;scopedClickConsumed=scopedSubmitConsumed=scopedConfirmConsumed=false;}
});
// "发表视频"/"发布视频"/"发布笔记" are also creator-home navigation labels.
// The uploader installs the full guard on the editor after entering it.
const forbidden=/^(发布|发表|投稿|提交投稿|立即投稿|立即发布|定时发布|确认发布|确定发布|继续发布|继续发表|发布作品|提交审核|保存并发布|发布并保存)$/;
function blockPublication(event){
  if(manualPublishAllowed)return;
  const candidates=event.type==='submit'?[event.submitter]:event.composedPath();
  if(candidates.some(node=>node instanceof Element && forbidden.test((node.innerText || node.value || node.getAttribute('aria-label') || '').trim()))){
    if(scopedLease){
      if(event.type==='click'&&!scopedClickConsumed){scopedClickConsumed=true;return;}
      if(event.type==='click'&&scopedClickConsumed&&!scopedConfirmConsumed && candidates.some(node=>node instanceof Element && (node.innerText||'').trim()==='确认发布' && node.closest('.ant-modal-confirm-centered'))){scopedConfirmConsumed=true;return;}
      if(event.type==='submit'&&!scopedSubmitConsumed){scopedSubmitConsumed=true;return;}
    }
    event.preventDefault();event.stopImmediatePropagation();
    // A fixed notification carries no page content and cannot invoke an action.
    ipcRenderer.send('draft-publish-blocked');
  }
}
document.addEventListener('click',blockPublication,true);
document.addEventListener('submit',blockPublication,true);
