// A replaced navigation is healthy only when the known replacement document
// actually commits. This is a page-load check, never an account/draft verdict.
const migratedDocument='location.origin==="https://creator.douyin.com" && document.readyState!=="loading" && !!document.body?.childElementCount';

async function recoverOfficialMigration(id, requestedURL, error, wc, timeoutMs=10000) {
  let origin;
  try { origin=new URL(requestedURL).origin; } catch { return false; }
  const aborted=error?.code==='ERR_ABORTED' || error?.errno===-3
    || /^ERR_ABORTED \(-3\)/.test(error?.message || '');
  if(id!=='xigua' || origin!=='https://studio.ixigua.com' || !aborted) return false;
  const deadline=Date.now()+timeoutMs;
  while(Date.now()<deadline){
    if(wc.isDestroyed()) return false;
    // executeJavaScript may wait for a pending document. Bound that wait too,
    // and clear our timer when it resolves; a late read has no side effects.
    const remaining=deadline-Date.now();
    const loaded=await new Promise(resolve=>{
      const timer=setTimeout(()=>resolve(false),Math.max(1,remaining));
      Promise.resolve().then(()=>wc.executeJavaScript(migratedDocument)).then(
        value=>{clearTimeout(timer);resolve(value===true);},
        ()=>{clearTimeout(timer);resolve(false);}
      );
    });
    if(loaded) return true;
    if(Date.now()>=deadline || wc.isDestroyed()) return false;
    await new Promise(resolve=>setTimeout(resolve,Math.min(150,Math.max(1,deadline-Date.now()))));
  }
  return false;
}

module.exports={recoverOfficialMigration};
