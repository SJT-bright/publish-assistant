const test=require('node:test');
const assert=require('node:assert/strict');
const {recoverOfficialMigration}=require('../desktop/platform-migration.cjs');

test('an abort before the replacement frame commits waits for the actual document',async()=>{
  let reads=0;
  const wc={isDestroyed:()=>false,executeJavaScript:async script=>{
    assert.match(script,/location.origin===\"https:\/\/creator.douyin.com\"/);
    return ++reads>1;
  }};
  assert.equal(await recoverOfficialMigration('xigua','https://studio.ixigua.com/',
    {code:'ERR_ABORTED',errno:-3},wc,1000),true);
  assert.equal(reads,2);
});

test('serialized Electron error also requires a real replacement document',async()=>{
  const wc={isDestroyed:()=>false,executeJavaScript:async()=>true};
  assert.equal(await recoverOfficialMigration('xigua','https://studio.ixigua.com/',
    new Error("ERR_ABORTED (-3) loading 'https://creator.douyin.com/'"),wc),true);
});

test('network errors, other platforms and other starting origins are never suppressed',async()=>{
  let reads=0;const wc={isDestroyed:()=>false,executeJavaScript:async()=>{reads++;return true;}};
  for(const [id,url,error] of [
    ['xigua','https://studio.ixigua.com/',{code:'ERR_NAME_NOT_RESOLVED',errno:-105}],
    ['douyin','https://studio.ixigua.com/',{code:'ERR_ABORTED',errno:-3}],
    ['xigua','https://unknown.invalid/',{code:'ERR_ABORTED',errno:-3}],
    ['xigua','http://studio.ixigua.com/',{code:'ERR_ABORTED',errno:-3}],
    ['xigua','https://studio.ixigua.com/',new Error('unexpected error mentions ERR_ABORTED')]
  ])assert.equal(await recoverOfficialMigration(id,url,error,wc),false);
  assert.equal(reads,0);
});

test('an empty or different document cannot become success merely because the URL changed',async()=>{
  let reads=0;const wc={isDestroyed:()=>false,executeJavaScript:async()=>{reads++;return false;}};
  assert.equal(await recoverOfficialMigration('xigua','https://studio.ixigua.com/',{errno:-3},wc,20),false);
  const finalReads=reads;await new Promise(resolve=>setTimeout(resolve,25));
  assert.equal(reads,finalReads,'no polling continues after the deadline');
});

test('a hung document read and a destroyed renderer stop within the deadline',async()=>{
  const pending={isDestroyed:()=>false,executeJavaScript:()=>new Promise(()=>{})};
  assert.equal(await recoverOfficialMigration('xigua','https://studio.ixigua.com/',{errno:-3},pending,20),false);
  const destroyed={isDestroyed:()=>true,executeJavaScript:()=>{throw new Error('must not read');}};
  assert.equal(await recoverOfficialMigration('xigua','https://studio.ixigua.com/',{errno:-3},destroyed,20),false);
});
