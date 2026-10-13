'use strict';
function canPublishManually(mode, lease, activeJob=false){
  return mode==='manual_publish' && !lease && activeJob!==true;
}
function validatePublicationMode(mode,lease,activeJob=false){
  if(!['draft','manual_publish'].includes(mode))throw new Error('发表模式无效。');
  if(lease||activeJob===true)throw new Error('任务正在处理，请结束后再切换发表模式。');
  return mode;
}
module.exports={canPublishManually,validatePublicationMode};
