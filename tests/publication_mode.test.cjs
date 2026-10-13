'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {canPublishManually,validatePublicationMode}=require('../desktop/publication-mode.cjs');
test('manual publication requires an explicit idle mode, and tasks always block it',()=>{
  assert.equal(canPublishManually('draft',null,false),false);
  assert.equal(canPublishManually('manual_publish',null,false),true);
  assert.equal(canPublishManually('manual_publish',{action:'draft'},false),false);
  assert.equal(canPublishManually('manual_publish',null,true),false);
  assert.throws(()=>validatePublicationMode('manual_publish',{action:'draft'}));
  assert.throws(()=>validatePublicationMode('manual_publish',null,true));
  assert.throws(()=>validatePublicationMode('publish',null,false));
  assert.equal(validatePublicationMode('manual_publish',null,false),'manual_publish');
});
