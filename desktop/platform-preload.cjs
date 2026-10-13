// No privileged bridge is exposed to the page. The click guard applies to manual and automated clicks.
const {ipcRenderer}=require('electron');
let manualPublishAllowed=false;
ipcRenderer.on('publication-mode',(_event,value)=>{manualPublishAllowed=value===true;});
// "发表视频"/"发布视频"/"发布笔记" are also creator-home navigation labels.
// The uploader installs the full guard on the editor after entering it.
const forbidden=/^(发布|发表|投稿|提交投稿|立即发布|定时发布|确认发布|确定发布|继续发布|继续发表|发布作品|提交审核|保存并发布|发布并保存)$/;
function blockPublication(event){
  if(manualPublishAllowed)return;
  const candidates=event.type==='submit'?[event.submitter]:event.composedPath();
  if(candidates.some(node=>node instanceof Element && forbidden.test((node.innerText || node.value || node.getAttribute('aria-label') || '').trim()))){
    event.preventDefault();event.stopImmediatePropagation();
    // A fixed notification carries no page content and cannot invoke an action.
    ipcRenderer.send('draft-publish-blocked');
  }
}
document.addEventListener('click',blockPublication,true);
document.addEventListener('submit',blockPublication,true);
