"""Passive proof of this native TC File's normal SDK completion.

841.d575b04c.js:1735991 publishes the newly constructed SDK via updateFile
before startUpload; 876055 accepts quick-chunk replies without ordinary ETag;
879303 confirms server parts and 881390 uploads missing parts. 882692 assigns
the observed finish response before updateSuccessResult; handleComplete then
fulfills and ends the upload. No SDK/business method is called by this module.
"""
import asyncio
import copy
import json
import secrets
import time

from uploader.tc_pipeline import _OWNER, _READ
from uploader.tc_resource import tc_resource_identity


# Official Vue2 defineReactive: 894.7c50ab1f.js:8649 (SHA256
# deca776d40156cfb709c4130cd88768ad5cf74bf2897f708cc0dd6245cc26533).
# The installed framework uses the second exact pair with renamed minified
# identifiers. SDK constructor data fields are declared in 841.d575b04c.js;
# its prototype business/computed getters are deliberately excluded.
# Only this observer-owned, recognized data wrapper may be read. Function
# names and generic get/set descriptors are never accepted as evidence.
_VUE_DATA_PAIRS = [['function(){var e=c?c.call(t):n;return tS.target&&(a.depend(),l&&(l.dep.depend(),m(e)&&function t(e){for(var n=void 0,r=0,o=e.length;r<o;r++)(n=e[r])&&n.__ob__&&n.__ob__.dep.depend(),m(n)&&t(n)}(e))),tX(e)&&!o?e.value:e}', 'function(e){var r=c?c.call(t):n;if(J(r,e)){if(u)u.call(t,e);else if(c)return;else if(!o&&tX(r)&&!tX(e)){r.value=e;return}else n=e;l=!o&&tR(e,!1,i),a.notify()}}'], ['function(){var t=c?c.call(e):n;return eE.target&&(a.depend(),l&&(l.dep.depend(),y(t)&&function e(t){for(var n=void 0,r=0,i=t.length;r<i;r++)(n=t[r])&&n.__ob__&&n.__ob__.dep.depend(),y(n)&&e(n)}(t))),eQ(t)&&!i?t.value:t}', 'function(t){var r=c?c.call(e):n;if(Y(r,t)){if(u)u.call(e,t);else if(c)return;else if(!i&&eQ(r)&&!eQ(t)){r.value=t;return}else n=t;l=!i&&eR(t,!1,o),a.notify()}}']]

_READ_OWN_DATA = r"""
 const dataKeys=new Set(['file','uuid','chunkSize','chunkCount','uploadTaskId','partInfo','uploadSuccessResp','transFlag',
                         'originFileObj','uploader','response','data','DownloadURL','httpsUrl','PartNumber','ETag']);
 const dataPairs=VUE_DATA_PAIRS;
 const raw=(o,k)=>{if(!o||typeof o!=='object')return undefined;const d=Object.getOwnPropertyDescriptor(o,k);return d&&'value'in d?d.value:undefined;};
 const source=fn=>typeof fn==='function'?Reflect.apply(Function.prototype.toString,fn,[]):null;
 const own=(o,k)=>{
  if(!o||typeof o!=='object')return undefined;
  const d=Object.getOwnPropertyDescriptor(o,k);
  if(!d)return undefined;
  if('value'in d)return d.value;
  if(!dataKeys.has(k)||d.enumerable!==true||d.configurable!==true)return undefined;
  const pair=dataPairs.find(p=>source(d.get)===p[0]&&source(d.set)===p[1]);
  if(!pair)return undefined;
  const marker=Object.getOwnPropertyDescriptor(o,'__ob__'),ob=marker&&'value'in marker?marker.value:null;
  const dep=raw(ob,'dep');
  if(!marker||marker.enumerable!==false||marker.configurable!==true||marker.writable!==true||
     raw(ob,'value')!==o||!dep||!Number.isSafeInteger(raw(dep,'id'))||!Array.isArray(raw(dep,'subs')))return undefined;
  const value=Reflect.apply(d.get,o,[]);
  const after=Object.getOwnPropertyDescriptor(o,k);
  if(raw(ob,'value')!==o||raw(ob,'dep')!==dep||Object.getOwnPropertyDescriptor(o,'__ob__')?.value!==ob||
     after?.get!==d.get||after?.set!==d.set)return undefined;
  return value;
 };
""".replace('VUE_DATA_PAIRS', json.dumps(_VUE_DATA_PAIRS, separators=(',', ':')))


_INSTALL = '(binding,scope)=>{' + _OWNER + _READ_OWN_DATA + r"""
 try{
  const ownerState=owner();
  if(!binding||binding.selections!==0||!ownerState||ownerState.material!==binding.material||ownerState.upload!==binding.upload||binding.__tcSDK)return false;
  const original=Object.getOwnPropertyDescriptor(binding.upload,'updateFile');
  if(!original||!('value'in original)||typeof original.value!=='function'||!original.configurable)return false;
  const root={scope,closed:false,invalid:false,sdk:null,wraps:[],entered:0,fulfilled:0,rejected:0,successes:0,enteredAt:0,fulfilledAt:0};
  binding.__tcSDK=root;
  function method(o,k){for(let depth=0;o&&depth++<8;o=Object.getPrototypeOf(o)){const d=Object.getOwnPropertyDescriptor(o,k);if(d)return 'value'in d&&typeof d.value==='function'?d.value:null;}return null;}
  function current(){const s=owner();return !root.closed&&!root.invalid&&binding.__tcSDK===root&&binding.selections===1&&
    s&&s.material===binding.material&&s.upload===binding.upload&&s.main===binding.main&&s.documents.has(binding.document);}
  function sameNative(file){return file===binding.file||own(file,'originFileObj')===binding.file;}
  function wrap(sdk,k,after){
   const fn=method(sdk,k),descriptor=Object.getOwnPropertyDescriptor(sdk,k);
   if(!fn||descriptor&&!descriptor.configurable)return false;
   const replacement=function(...args){
    if(this!==sdk||!current()||root.sdk!==sdk){root.invalid=true;return Reflect.apply(fn,this,args);}
    if(k==='handleComplete'){root.entered++;root.enteredAt=Date.now()/1000;}
    let value;
    try{value=Reflect.apply(fn,this,args);}catch(error){root.rejected++;throw error;}
    if(k==='handleComplete'){
     try{Reflect.apply(Promise.prototype.then,value,[()=>{if(current()&&root.sdk===sdk){root.fulfilled++;root.fulfilledAt=Date.now()/1000;}},()=>{root.rejected++;}]);}
     catch(_){root.invalid=true;}
    }else after();
    return value;
   };
   Object.defineProperty(sdk,k,{configurable:true,writable:true,enumerable:descriptor?.enumerable??false,value:replacement});
   root.wraps.push({object:sdk,key:k,descriptor,replacement});return true;
  }
  function install(sdk,file){
   if(!current()||!sameNative(file)||!sdk||typeof sdk!=='object'||!sameNative(own(sdk,'file')))return;
   if(root.sdk){if(root.sdk!==sdk)root.invalid=true;return;}
   if(!['startUpload','reuseUpload','initTaskId','handleComplete','updateSuccessResult','getAvailableUploadedInfo'].every(k=>method(sdk,k)))return;
   root.sdk=sdk;
   if(!wrap(sdk,'handleComplete',()=>{})||!wrap(sdk,'updateSuccessResult',()=>{root.successes++;}))root.invalid=true;
   clearInterval(root.interval);
  }
  const update=function(...args){
   const event=args[0];
   try{if(this===binding.upload&&event?.parcials?.uploader)install(event.parcials.uploader,event.file);}catch(_){root.invalid=true;}
   return Reflect.apply(original.value,this,args);
  };
  Object.defineProperty(binding.upload,'updateFile',{...original,value:update});
  root.wraps.push({object:binding.upload,key:'updateFile',descriptor:original,replacement:update});
  // Vue can retain the original bound updateFile in an existing VNode
  // invoker. Its normal assignment is then discovered without calling it.
  // A late discovery never manufactures a missed completion invocation.
  root.interval=setInterval(()=>{try{
    if(root.closed||root.invalid){clearInterval(root.interval);return;}
    if(binding.selections!==1)return;
    const file=binding.material.fileList[0],sdk=own(file,'uploader');if(sdk)install(sdk,file);
   }catch(_){root.invalid=true;clearInterval(root.interval);}},10);
  root.close=()=>{if(root.closed)return;root.closed=true;clearInterval(root.interval);for(const w of root.wraps.reverse()){
    if(Object.getOwnPropertyDescriptor(w.object,w.key)?.value!==w.replacement)continue;
    if(w.descriptor)Object.defineProperty(w.object,w.key,w.descriptor);else delete w.object[w.key];
   }};
  return true;
 }catch(_){return false;}
} """

_SNAPSHOT = '(binding,scope)=>{' + _READ_OWN_DATA + r"""
 const readBinding=READ_BINDING;
 try{
  const root=binding?.__tcSDK,state=readBinding(binding);
  if(!root||root.closed||root.invalid||JSON.stringify(root.scope)!==JSON.stringify(scope)||
     !['done','uploading'].includes(state)||root.entered!==1||root.fulfilled!==1||root.rejected!==0||root.successes!==1)return null;
  const sdk=root.sdk;
  const current=binding.material.fileList[0],sdkFile=own(sdk,'file');
  if(own(current,'uploader')!==sdk||sdkFile!==binding.file&&own(sdkFile,'originFileObj')!==binding.file)return null;
  const name=Reflect.apply(Object.getOwnPropertyDescriptor(File.prototype,'name').get,binding.file,[]),
        size=Reflect.apply(Object.getOwnPropertyDescriptor(Blob.prototype,'size').get,binding.file,[]),
        count=own(sdk,'chunkCount'),chunkSize=own(sdk,'chunkSize'),parts=own(sdk,'partInfo'),task=own(sdk,'uuid'),upload=own(sdk,'uploadTaskId'),flag=own(sdk,'transFlag'),
        result=own(own(sdk,'uploadSuccessResp'),'data'),response=own(current,'response');
  const download=own(result,'DownloadURL'),url=own(result,'httpsUrl');
  if(!Number.isSafeInteger(size)||size<=0||!Number.isSafeInteger(chunkSize)||chunkSize<=0||
     !Number.isInteger(count)||count<1||count>65536||count!==Math.ceil(size/chunkSize)||!Array.isArray(parts)||
     !result||!response||download!==own(response,'DownloadURL')||url!==own(response,'httpsUrl')||
     typeof download!=='string'||typeof url!=='string'||
     ![task,upload,flag].every(v=>typeof v==='string'&&v.length>0&&v.length<=2048)||parts.length!==count)return null;
  const rows=parts.map(p=>p&&{number:own(p,'PartNumber'),etag:own(p,'ETag')});
  if(rows.some((p,i)=>!p||p.number!==i+1||typeof p.etag!=='string'||!p.etag||p.etag.length>2048))return null;
  if(readBinding(binding)!==state||binding.material.fileList[0]!==current||own(current,'uploader')!==sdk||root.sdk!==sdk)return null;
  return {name,size,chunk_size:chunkSize,task_id:task,upload_id:upload,trans_flag:flag,parts:rows,
    download_url:download,source_url:url,
    entered_at:root.enteredAt,fulfilled_at:root.fulfilledAt};
 }catch(_){return null;}
} """.replace('READ_BINDING', '(' + _READ + ')')


class TCSDKObserver:
    def __init__(self, session):
        self.session, self.page = session, session.page
        self.scope = {k: session.expected.get(k) for k in ('job_id', 'receipt_nonce')}
        self.scope['observer_nonce'] = secrets.token_hex(16)
        self.account = copy.deepcopy(session.account)
        self.binding, self.native, self.closed = None, None, False
        self._proof, self._summary = None, None

    def current(self):
        s = self.session
        wanted = s.expected.get('account', {})
        return (not self.closed and not s.closed and s.platform == 'tencent' and
                getattr(self.page, '_sau_draft_evidence', None) is s and s.account == self.account and
                self.account.get('verified') is True and isinstance(wanted, dict) and
                self.account.get('uid') == wanted.get('uid') and bool(wanted.get('uid')) and
                self.account.get('identity') == wanted.get('identity') and bool(wanted.get('identity')) and
                all(self.scope.get(k) and self.scope[k] == s.expected.get(k) for k in ('job_id', 'receipt_nonce')))

    async def bind(self, binding):
        if not self.current() or self.binding is not None:
            return False
        result = await asyncio.wait_for(binding.evaluate(_INSTALL, self.scope), 5)
        if result is not True or not self.current():
            return False
        self.binding = binding
        return True

    async def drain(self):
        self.native = None
        if self.current() and self.binding is getattr(self.page, '_sau_tc_native_import', None):
            try:
                self.native = await asyncio.wait_for(self.binding.evaluate(_SNAPSHOT, self.scope), 5)
            except Exception:
                pass
        proof = self._live_proof()
        if proof is not None:
            self._proof = proof
        summary = {'event': 'tencent_native_sdk', 'bound': self.binding is not None,
                   'fulfilled': isinstance(self.native, dict), 'upload_link_verified': proof is not None}
        if self.binding is not None:
            try:
                trace = await asyncio.wait_for(self.binding.evaluate('b=>{const r=b?.__tcSDK;return r?{sdk_seen:!!r.sdk,invalid:r.invalid,entered:r.entered,successes:r.successes,fulfilled_count:r.fulfilled,rejected:r.rejected}:null;}'), 5)
                if (isinstance(trace, dict) and set(trace) == {'sdk_seen','invalid','entered','successes','fulfilled_count','rejected'} and
                        all(type(trace[k]) is bool for k in ('sdk_seen','invalid')) and
                        all(type(trace[k]) is int and 0 <= trace[k] < 100 for k in ('entered','successes','fulfilled_count','rejected'))):
                    summary.update(trace)
            except Exception:
                pass
        if summary != self._summary:
            self._summary = summary
            try: print('DRAFT_DIAGNOSTIC=' + json.dumps(summary, sort_keys=True), flush=True)
            except Exception: pass

    def _live_proof(self):
        s, n = self.session, self.native
        current = getattr(s.tc_processing_observer, 'native', None)
        if not self.current() or not isinstance(n, dict) or not isinstance(current, dict):
            return None
        asset = s.assets.get('video', {})
        if (current.get('sha256') != s.expected.get('video_sha256') or current.get('sha256') != asset.get('sha256') or
                (n.get('name'), n.get('size')) != (asset.get('name'), asset.get('size')) or
                n.get('source_url') != current.get('source_url') or n.get('download_url') != current.get('download_url')):
            return None
        from uploader.draft_evidence import _tc_confirmed_parts
        dfs = [x for x in s.exchanges if x.get('tc_protocol') == 'dfs' and x.get('metadata') == (n['name'], n['size']) and
               x.get('task_id') == n.get('task_id') and x.get('upload_id') == n.get('upload_id')]
        allocations = [x for x in dfs if 'plan' in x]
        if not allocations or len({(tuple(x['plan']), x['observed_at']) for x in allocations}) != 1:
            return None
        allocation = allocations[0]
        if not s._tc_selected_asset(allocation) or not s.started_at <= allocation['observed_at'] <= n.get('entered_at', 0) <= n.get('fulfilled_at', 0):
            return None
        chunk_size = n.get('chunk_size')
        if (type(chunk_size) is not int or chunk_size <= 0 or
                allocation['plan'] != [min((index+1)*chunk_size, n['size']) for index in range(len(allocation['plan']))]):
            return None
        parts = n.get('parts')
        if (not isinstance(parts, list) or len(parts) != len(allocation['plan']) or
                any(not isinstance(p, dict) or p.get('number') != i + 1 or not isinstance(p.get('etag'), str) for i, p in enumerate(parts))):
            return None
        expected = {p['number']: {p['etag']} for p in parts}
        resources = set()
        for finish in dfs:
            if (finish.get('stage') != 'complete' or finish.get('trans_flag') != n.get('trans_flag') or
                    not n['entered_at'] <= finish['observed_at'] <= n['fulfilled_at'] + .5 or
                    {number: {etag} for number, etag in finish['parts']} != expected or
                    tc_resource_identity(n.get('source_url')) not in finish['ids']):
                continue
            chunks = [x for x in dfs if 'part_number' in x and allocation['observed_at'] <= x['observed_at'] <= finish['observed_at']]
            observed = {}
            for x in chunks: observed.setdefault(x['part_number'], set()).add(x['etag'])
            present, confirmed = _tc_confirmed_parts(allocation, finish, dfs, chunks, sdk_completed=True)
            if not present or confirmed is None or any(number not in expected or values != expected[number] for number, values in confirmed.items()):
                continue
            if any(number not in expected or len(values) != 1 or (number not in confirmed and values != expected[number]) for number, values in observed.items()):
                continue
            if set(observed) | set(confirmed) != set(expected) or not any(x.get('trans_flag') == n['trans_flag'] for x in chunks):
                continue
            resources.add(tuple(finish['ids']))
        if len(resources) != 1:
            return None
        return {'kind': 'video', 'sha256': asset['sha256'], 'ids': list(resources.pop()), 'complete': True}

    def upload_links(self):
        proof = self._live_proof()
        if proof is not None:
            return [proof]
        processing = getattr(self.session, 'tc_processing_observer', None)
        frozen = getattr(processing, '_frozen_reopen', None)
        binding = getattr(self.session, 'normal_ui_binding', None)
        if (self.current() and self._proof and frozen and processing._frozen_events_current() and
                binding == {'id': frozen['candidate']['id'], 'owner': frozen['candidate']['owner'], 'control': 'post_draft_item_edit'} and
                type(getattr(self.session, 'reopen_at', None)) in (int, float) and self.session.reopen_at >= frozen['at']):
            return [copy.deepcopy(self._proof)]
        return []

    async def close(self):
        self.closed, self.native, self._proof = True, None, None
        if self.binding is not None:
            try: await asyncio.wait_for(self.binding.evaluate('b=>b.__tcSDK?.close()'), 5)
            except Exception: pass


async def install_tc_sdk_observer(session):
    session.tc_sdk_observer = TCSDKObserver(session)
    return session.tc_sdk_observer
