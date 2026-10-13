'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const model=require('../ui-assets/progress-model.js');
test('completed operations and observed upload percentage update a stage and total without a timer',()=>{
  const p={stage:'upload',completed:['session','video'],skipped:[],operations:{upload:{plan:['transfer','processing'],completed:[],skipped:[],current:'transfer',state:'running',percent:48}}};
  assert.equal(model.measure(p).percent,32);
  assert.equal(model.measure(p).rows[2].percent,24);
  p.operations.upload.completed=['transfer'];delete p.operations.upload.percent;p.operations.upload.current='processing';
  assert.equal(model.measure(p).percent,35.7);
  p.operations.upload.completed.push('processing');p.completed.push('upload');
  assert.equal(model.measure(p).percent,42.9);
});
test('no records stays unknown; historical records use stage completion; unknown verification never reads 100%',()=>{
  assert.equal(model.measure(null).percent,null);
  assert.equal(model.measure({completed:['session','video','upload']}).percent,42.9);
  const p={stage:'verify',completed:model.stages,operations:{verify:{plan:['response'],completed:['response']}}};
  assert.ok(model.measure(p).percent<100);
  assert.equal(model.measure(p,true).percent,100);
});
test('each platform snapshot owns independent values and corrupt/private fields are filtered',()=>{
  const p={stage:'content',completed:[],operations:{content:{plan:['title','description'],completed:['title'],current:'description',state:'failed',path:'/private',percent:NaN}}};
  assert.equal(model.measure(p).rows[3].percent,50);
  assert.equal(model.measure({stage:'session',completed:[]}).percent,0);
  assert.doesNotMatch(JSON.stringify(model.clean(p.operations)),/private|path|NaN/);
  assert.deepEqual(model.clean({content:{plan:['title','title']}}),{});
});
