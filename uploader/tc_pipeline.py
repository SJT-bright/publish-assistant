"""Read-only native File binding for this video-channel upload.

The bound object belongs to our isolated call, never to a Vue business method.
No account fields, mediaList or disablePostTip mutating getters are read.
"""
import asyncio
import time


_OWNER = r"""
function owner(){try{
 const main=window.top.document;
 if(main.location.origin!=='https://channels.weixin.qq.com'||!main.location.pathname.startsWith('/platform/'))return null;
 const roots=[main],seenRoots=new Set(),seenVM=new Set(),materials=new Set();let nodes=0;
 while(roots.length){const root=roots.shift();if(seenRoots.has(root))continue;seenRoots.add(root);
  if(seenRoots.size>64)return null;
  for(const el of root.querySelectorAll('*')){
   if(++nodes>20000)return null;if(el.shadowRoot)roots.push(el.shadowRoot);
   if(el.tagName==='IFRAME'){let doc;try{doc=el.contentDocument;}catch(_){}
    if(doc&&doc.location.origin===main.location.origin&&doc.location.pathname.startsWith('/micro/content/'))roots.push(doc);}
   const chain=new Set();let depth=0;
   for(let vm=el.__vue__;vm;vm=vm.$parent){if(chain.has(vm)||++depth>32)return null;chain.add(vm);
    if(seenVM.has(vm))break;seenVM.add(vm);if(seenVM.size>2048)return null;
    if(Array.isArray(vm.fileList)&&['handleFileListChange','handleSetFileList','handleDeleteFile','updateFile'].every(k=>typeof vm[k]==='function')&&
      ['allUploaded','allCoverCroped','canSaveDraft','disablePostTip'].every(k=>k in vm))materials.add(vm);
   }
  }
 }
 if(materials.size!==1)return null;const material=[...materials][0],upload=material.$refs?.postUpload;
 if(!upload||Array.isArray(upload)||!Array.isArray(upload.value)||
    !['handleCropVideo','updateVideo','updateFile','setFileList'].every(k=>typeof upload[k]==='function'))return null;
 return {main,material,upload,documents:seenRoots};
}catch(_){return null;}}
"""

_CAPTURE = 'input=>{'+_OWNER+r"""
 const state=owner();
 if(!state||!state.documents.has(input.ownerDocument)||!input.isConnected||input.tagName!=='INPUT'||input.type!=='file'||
    input.files.length!==0||state.material.fileList.length!==0||state.upload.value.length!==0)return null;
 const binding={main:state.main,document:input.ownerDocument,material:state.material,upload:state.upload,input,file:null,selections:0};
 // Capture before the site's handler may reset/remount its native input.
 // The observed File is never passed back into an application method.
 binding.observe=()=>{binding.selections++;if(input.files.length===1)binding.file=input.files[0];};
 input.addEventListener('change',binding.observe,true);
 return binding;
} """

_READ = 'binding=>{'+_OWNER+r"""
 try{
  const state=owner(),input=binding?.input;
  if(!state||!binding||state.main!==binding.main||state.material!==binding.material||state.upload!==binding.upload||
     !state.documents.has(binding.document)||input.ownerDocument!==binding.document||binding.selections>1)return 'changed';
  if(binding.selections===0||binding.file===null)return 'pending';
  const native=binding.file,name=Object.getOwnPropertyDescriptor(File.prototype,'name').get.call(native),
   size=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get.call(native),type=Object.getOwnPropertyDescriptor(Blob.prototype,'type').get.call(native);
  if(!name||!Number.isSafeInteger(size)||size<=0||!type.startsWith('video/'))return 'changed';
  if(input.isConnected&&(input.files.length>1||input.files.length===1&&input.files[0]!==native))return 'changed';
  const list=state.material.fileList,value=state.upload.value;
  if(list.length===0&&value.length===0)return 'pending';
  if(list.length!==1||value.length!==1)return 'changed';
  const file=list[0],other=value[0];
  if(!file||!other||file.originFileObj!==native||other.originFileObj!==native||
     file.isVideo!==true||file.isImage!==false||file.isValid!==true)return 'changed';
  if(file.status==='error'||file.status==='removed')return 'failed';
  if(!['uploading','success','done','croping'].includes(file.status))return 'changed';
  return file.status==='done'?'done':'uploading';
 }catch(_){return 'changed';}
}"""


async def capture_tc_upload(page, native_input):
    """Capture the actual empty MaterialEdit/input before the one selection."""
    handle=await asyncio.wait_for(native_input.evaluate_handle(_CAPTURE,isolated_context=False),5)
    if not await asyncio.wait_for(handle.evaluate('value=>value!==null'),5):
        await handle.dispose()
        raise RuntimeError('视频号空白素材与原生上传控件无法绑定，未导入视频。')
    observer = getattr(getattr(page, '_sau_draft_evidence', None), 'tc_sdk_observer', None)
    if observer is not None and not await observer.bind(handle):
        await handle.dispose()
        raise RuntimeError('视频号本次原生SDK上传观察无法绑定，未导入视频。')
    return handle


async def read_tc_upload_binding(binding):
    try:
        stage=getattr(binding,'_sau_tc_native_stage',None)
        if stage is not None:
            stage.assert_current()
        result=await asyncio.wait_for(binding.evaluate(_READ),5)
        if stage is not None:
            stage.assert_current()
        return result if result in ('pending','uploading','done','failed','changed') else 'changed'
    except Exception:
        return 'changed'


async def require_tc_upload_context(page, timeout=30):
    binding=getattr(page,'_sau_tc_native_import',None)
    if binding is None:
        return
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        state=await read_tc_upload_binding(binding)
        if state in ('uploading','done'):
            return
        if state=='failed':
            raise RuntimeError('视频号本次上传已失败，未继续填写或提交。')
        if state=='changed':
            raise RuntimeError('视频号当前素材与本次导入不一致，未继续填写或提交。')
        await asyncio.sleep(.1)
    raise RuntimeError('视频号本次素材尚未进入编辑器，未填写内容。')


async def release_tc_upload(binding, *, preserve_stage=False):
    if binding is not None:
        try:
            await asyncio.wait_for(binding.evaluate("value=>{value?.__tcSDK?.close();if(value?.input&&value.observe)value.input.removeEventListener('change',value.observe,true);}"),5)
        except Exception:
            pass
        try:
            await asyncio.wait_for(binding.dispose(),5)
        except Exception:
            pass
        from uploader.tc_stage import release_tc_native_stage
        if not preserve_stage:
            await release_tc_native_stage(getattr(binding,'_sau_tc_native_stage',None))
