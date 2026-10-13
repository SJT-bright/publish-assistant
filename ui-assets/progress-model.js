/* Workflow completion, not a byte estimate or publication receipt. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.DraftProgress=api;})(typeof window==='object'?window:globalThis,function(){
'use strict';
const operations={"session":{"preflight":"检查素材与文字限制","media_probe":"读取视频与图片","browser_connect":"连接内嵌浏览器","creator_open":"打开创作页面","account_check":"验证当前登录","capability_check":"检查账号与页面权限","evidence_init":"准备草稿核验"},"video":{"locate":"定位视频导入位置","select":"选择并导入视频","bind":"确认视频已进入编辑器"},"upload":{"transfer":"等待视频上传","processing":"等待平台处理并确认视频"},"content":{"title":"填写标题","description":"填写文案","topics":"填写话题","readback":"核对填写内容"},"cover":{"editor":"打开封面编辑器","landscape":"设置横版封面","portrait":"设置竖版封面","confirm":"确认封面","readback":"核对封面"},"save":{"guard":"检查保存条件","action":"执行保存操作","response":"等待保存记录"},"verify":{"response":"核对草稿记录","reopen":"重新打开草稿","content":"核对视频、封面与文字","owner":"核对所属账号"}};
const stages=Object.keys(operations);
function clean(value){
  const result={};if(!value||typeof value!=='object'||Array.isArray(value))return result;
  for(const stage of stages){
    const row=value[stage];if(!row||!Array.isArray(row.plan)||!row.plan.length||row.plan.length>Object.keys(operations[stage]).length||new Set(row.plan).size!==row.plan.length||!row.plan.every(id=>Object.hasOwn(operations[stage],id)))continue;
    const plan=[...row.plan],completed=plan.filter(id=>Array.isArray(row.completed)&&row.completed.includes(id));
    const skipped=plan.filter(id=>!completed.includes(id)&&Array.isArray(row.skipped)&&row.skipped.includes(id));
    const current=plan.includes(row.current)?row.current:null;
    const state=['running','completed','skipped','failed'].includes(row.state)?row.state:null;
    result[stage]={plan,completed,skipped,current,state};
    if(current&&!completed.includes(current)&&!skipped.includes(current)&&typeof row.percent==='number'&&Number.isFinite(row.percent)&&row.percent>=0&&row.percent<=100)result[stage].percent=row.percent;
  }return result;
}
function measure(progress,verified=false){
  const details=clean(progress?.operations),completed=new Set((progress?.completed||[]).filter(id=>id!=='verify'||verified)),skipped=new Set((progress?.skipped||[]).filter(id=>id!=='verify'));
  if(progress&&verified)completed.add('verify');
  const rows=stages.map(stage=>{
    const row=details[stage];let percent=null;
    if(completed.has(stage)||skipped.has(stage))percent=100;
    else if(row)percent=(row.completed.length+row.skipped.length+(typeof row.percent==='number'?row.percent/100:0))/row.plan.length*100;
    else if(progress)percent=0;
    return {stage,percent:percent===null?null:Math.round(percent*10)/10,detail:row};
  });
  let percent=progress?rows.reduce((sum,row)=>sum+(row.percent||0),0)/7:null;
  if(percent!==null)percent=verified?100:Math.min(99,Math.round(percent*10)/10);
  const current=details[progress?.stage];
  return {percent,rows,current,operations};
}
return {operations,stages,clean,measure};
});
