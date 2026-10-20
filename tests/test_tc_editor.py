"""Official MaterialEdit/PostUpload-shaped isolated DOM fixtures; no site calls.

Existing handlers throw if invoked; mediaList/disablePostTip and private
getters also throw. Native File identity is exercised in real browser realms.
"""
import unittest
import os
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from patchright.async_api import async_playwright

from uploader.tc_editor import read_tc_material_state
from uploader.tencent_uploader.main import TencentVideo


UNKNOWN = dict(recognized=False, count=0, video_count=0, done_count=0,
               all_uploaded=False, all_cover_cropped=False, can_save_draft=False)

FIXTURE = r'''<!doctype html><div id="main"></div><div id="shadow-host"></div>
<iframe id="owner-frame" src="/micro/content/post/create"></iframe><script>
window.calls={business:0,mutable:0,private:0};
window.makeMaterial=statuses=>{
 const business=()=>{window.calls.business++;throw Error('business_handler_called');};
 const files=statuses.map(status=>{
  const native=new File(['private fixture bytes'],'private-name.mp4',{type:'video/mp4'});
  const item={isVideo:true,isImage:false,isValid:true,status,
   thumbUrl:status==='done'?'fixture:private-thumb':undefined,originFileObj:native};
  for(const key of ['name','response','uuid'])Object.defineProperty(item,key,
   {get(){window.calls.private++;throw Error('private_field_read');}});
  return item;
 });
 const upload={value:files.slice(),handleCropVideo:business,updateVideo:business,
  updateFile:business,setFileList:business};
 const vm={fileList:files,$refs:{postUpload:upload},handleFileListChange:business,
  handleSetFileList:business,handleDeleteFile:business,updateFile:business,
  postStore:{createPostStatus:{hasExtLinkError:false,isShowDescLengthError:false,isShowMpTitleLengthError:false}}};
 // These are the official pure getters at cached 841 chars 1939734 onward.
 Object.defineProperties(vm,{
  allUploaded:{configurable:true,get(){return this.fileList.length&&this.fileList.every(e=>e.status==='done');}},
  allCoverCroped:{get(){return this.fileList.length&&this.fileList.every(e=>e.thumbUrl);}},
  canSaveDraft:{get(){return this.allUploaded&&!this.postStore.createPostStatus.hasExtLinkError&&
    !this.postStore.createPostStatus.isShowDescLengthError&&!this.postStore.createPostStatus.isShowMpTitleLengthError;}},
  mediaList:{get(){window.calls.mutable++;throw Error('mutating_mediaList_getter');}},
  disablePostTip:{get(){window.calls.mutable++;throw Error('mutating_disablePostTip_getter');}},
  userStore:{get(){window.calls.private++;throw Error('account_read');}}
 });
 return vm;
};
</script>'''


class TcMaterialSourceTests(unittest.TestCase):
    def test_fixture_contract_matches_current_official_cached_component(self):
        cached=Path(os.environ.get('SAU_TC_CACHED_COMPONENT',Path(__file__).resolve().parents[1]/
                'research/raw/2026-10-03/limits-login/tc-content-841.d575b04c.js'))
        if not cached.is_file():
            self.skipTest('Private official source cache is optional; set SAU_TC_CACHED_COMPONENT to verify it.')
        content=cached.read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(),
                         '022cd6da6b8f3afed8aded29b19aed5c360ea504784cd69c969c6e3578d7198b')
        source=content.decode()
        for contract in ('"fileList",[]', '{key:"handleSetFileList",value:function(e){this.fileList=e}',
                         '{key:"handleDeleteFile",value:function(e)', '{key:"handleFileListChange",value:function(e)',
                         '{key:"updateVideo",value:function(e){this.$emit("updateFile",{file:this.value[0],parcials:e})}',
                         'n("PostUpload",{ref:"postUpload",attrs:{value:e.mediaList',
                         'return this.fileList.length&&this.fileList.every(function(e){return e.status===q.done})',
                         'return this.fileList.length&&this.fileList.every(function(e){return e.thumbUrl})',
                         '.success="success",l.uploading="uploading",l.done="done",l.error="error",l.removed="removed",l.croping="croping"'):
            self.assertTrue(contract in source,'cached official contract missing: '+contract)

class TcMaterialBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright=await async_playwright().start()
        self.browser=await self.playwright.chromium.launch(headless=True,channel='chrome')
        self.context=await self.browser.new_context()
        async def route(request):
            body='<div id="owner"></div>' if '/micro/content/' in request.request.url else FIXTURE
            await request.fulfill(content_type='text/html',body=body)
        await self.context.route('**/*',route)
        self.page=await self.context.new_page()
        await self.page.goto('https://channels.weixin.qq.com/platform/post/create')
        # The Wujie owner document can be hidden; its Vue instance remains
        # readable. Requiring a visible empty div would fabricate readiness.
        await self.page.frames[1].locator('#owner').wait_for(state='attached')

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def put(self,statuses):
        await self.page.evaluate('''statuses=>{
            window.material=window.makeMaterial(statuses);
            document.querySelector('#main').__vue__=window.material;
        }''',statuses,isolated_context=False)

    async def assert_read_only(self):
        self.assertEqual(await self.page.evaluate('window.calls',isolated_context=False),
                         {'business':0,'mutable':0,'private':0})

    async def test_empty_duplicate_croping_and_single_done_are_distinct(self):
        cases=[([],0,False,False,False),(['done','croping','croping','croping'],1,False,False,False),
               (['croping'],0,False,False,False),(['done'],1,True,True,True)]
        for statuses,done,uploaded,cropped,save in cases:
            with self.subTest(statuses=statuses):
                await self.put(statuses)
                result=await read_tc_material_state(self.page)
                self.assertEqual(result,dict(recognized=True,count=len(statuses),video_count=len(statuses),
                    done_count=done,all_uploaded=uploaded,all_cover_cropped=cropped,can_save_draft=save))
                self.assertTrue(all(type(value) in (bool,int) for value in result.values()))
                await self.assert_read_only()
        # Official canSaveDraft and upload completion do not test automatic
        # cover readiness. Custom covers wait for their own native edit controls.
        await self.page.evaluate("material.fileList[0].thumbUrl='';",isolated_context=False)
        result=await read_tc_material_state(self.page)
        self.assertTrue(result['all_uploaded'])
        self.assertFalse(result['all_cover_cropped'])
        self.assertTrue(result['can_save_draft'])
        await self.assert_read_only()

    async def test_same_instance_across_shadow_and_owner_is_deduplicated_but_two_instances_are_not(self):
        await self.put(['done'])
        await self.page.evaluate('''()=>{
            const host=document.querySelector('#shadow-host'),shadow=host.attachShadow({mode:'open'});
            const projected=document.createElement('div');projected.__vue__={$parent:window.material};shadow.append(projected);
            document.querySelector('#owner-frame').contentDocument.querySelector('#owner').__vue__=window.material;
        }''',isolated_context=False)
        result=await read_tc_material_state(self.page)
        self.assertTrue(result['recognized'])
        self.assertEqual(result['count'],1)
        # It is still found when only the existing owner document projects it.
        await self.page.evaluate('''()=>{
            delete document.querySelector('#main').__vue__;
            document.querySelector('#shadow-host').shadowRoot.replaceChildren();
        }''',isolated_context=False)
        self.assertEqual(await read_tc_material_state(self.page),result)
        # A separate VM with identical state and native File pointers is still
        # a second composer. Schema equality must never merge its identity.
        await self.page.evaluate('''()=>{
            const other=window.makeMaterial(['done']);other.fileList=window.material.fileList;
            other.$refs.postUpload.value=window.material.fileList.slice();
            document.querySelector('#main').__vue__=other;
        }''',isolated_context=False)
        self.assertEqual(await read_tc_material_state(self.page),UNKNOWN)
        await self.assert_read_only()

    async def test_native_file_link_and_unknown_shapes_fail_closed(self):
        for case in ('native_clone','foreign_native','same_name_other_file','forged_file','blob','wrong_value',
                     'unknown_status','bad_getter','missing_upload','unknown_material'):
            with self.subTest(case=case):
                await self.put(['done'])
                await self.page.evaluate('''variant=>{
                    const vm=window.material,upload=vm.$refs.postUpload;
                    if(variant==='native_clone')upload.value=vm.fileList.map(e=>({...e}));
                    if(variant==='foreign_native'){
                        const owner=document.querySelector('#owner-frame').contentWindow;
                        const file=new owner.File(['private fixture bytes'],'private-name.mp4',{type:'video/mp4'});
                        vm.fileList[0].originFileObj=file;upload.value=[{...vm.fileList[0]}];
                    }
                    if(variant==='same_name_other_file')upload.value=[{...vm.fileList[0],originFileObj:
                        new File(['private fixture bytes'],'private-name.mp4',{type:'video/mp4'})}];
                    if(variant==='forged_file')vm.fileList[0].originFileObj={name:'private-name.mp4',size:21,type:'video/mp4'};
                    if(variant==='blob')vm.fileList[0].originFileObj=new Blob(['private fixture bytes'],{type:'video/mp4'});
                    if(variant==='wrong_value')upload.value=[];
                    if(variant==='unknown_status')vm.fileList[0].status='unrecognized';
                    if(variant==='bad_getter')Object.defineProperty(vm,'allUploaded',{get(){return 'true';}});
                    if(variant==='missing_upload')delete upload.updateVideo;
                    if(variant==='unknown_material')delete vm.handleSetFileList;
                }''',case,isolated_context=False)
                result=await read_tc_material_state(self.page)
                if case in ('native_clone','foreign_native'):
                    self.assertTrue(result['recognized'])
                    self.assertEqual(result['count'],1)
                    self.assertTrue(result['all_uploaded'])
                else:
                    self.assertEqual(result,UNKNOWN)
                await self.assert_read_only()


class TcMaterialProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_origin_and_non_boolean_output_cannot_be_ready(self):
        frame=SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/create',
                              evaluate=AsyncMock(return_value={**UNKNOWN,'recognized':True,'all_uploaded':'true'}))
        page=SimpleNamespace(url=frame.url,main_frame=frame)
        self.assertEqual(await read_tc_material_state(page),UNKNOWN)
        page.url='https://fixture.invalid/platform/post/create'
        frame.evaluate.reset_mock()
        self.assertEqual(await read_tc_material_state(page),UNKNOWN)
        frame.evaluate.assert_not_awaited()


class TcMaterialAdapterTests(unittest.IsolatedAsyncioTestCase):
    def uploader(self):
        uploader=TencentVideo('完整标题','fixture.mp4',[],0,'unused.json',desc='完整正文')
        uploader.draft_only=True
        uploader.draft_upload_timeout=2
        return uploader

    def page_with_one_video_input(self):
        native_input=object()
        locator=SimpleNamespace(all=AsyncMock(return_value=[native_input]),count=AsyncMock(return_value=1))
        frame=SimpleNamespace(locator=lambda _selector:locator)
        page=SimpleNamespace(frames=[frame],_sau_draft_evidence=SimpleNamespace(
            capture_tc_cdn_snapshot=AsyncMock(return_value=True),
            expected={'job_id':'fixture-job','receipt_nonce':'fixture-nonce'},
            assets={'video':{'kind':'video','name':'fixture.mp4','size':1,'sha256':'a'*64}}))
        return page,native_input

    async def test_nonempty_or_unknown_materials_never_reach_native_file_selection(self):
        for case,count,recognized in [('empty',0,True),('existing',1,True),('duplicate',4,True),('unknown',0,False)]:
            with self.subTest(case=case):
                page,native_input=self.page_with_one_video_input()
                state={**UNKNOWN,'recognized':recognized,'count':count,'video_count':count}
                reader=AsyncMock(return_value=state)
                stage=SimpleNamespace(path=Path('fixture-stage.mp4'),assert_current=Mock())
                with patch('uploader.tc_editor.read_tc_material_state',reader), \
                     patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()), \
                     patch('uploader.tc_pipeline.capture_tc_upload',new=AsyncMock(return_value=SimpleNamespace())), \
                     patch('uploader.tc_stage.create_tc_native_stage',new=AsyncMock(return_value=stage)) as prepare, \
                     patch('uploader.tencent_uploader.main.set_local_files',new=AsyncMock()) as files:
                    if case=='empty':
                        await self.uploader().upload_video_file(page,'fixture.mp4')
                        files.assert_awaited_once_with(page,native_input,stage.path)
                        prepare.assert_awaited_once_with('fixture.mp4',page._sau_draft_evidence.assets['video'],
                            recovery_root=None,scope={'job_id':'fixture-job','receipt_nonce':'fixture-nonce'})
                        self.assertEqual(stage.assert_current.call_count,2)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'尚未确认|已有素材'):
                            await self.uploader().upload_video_file(page,'fixture.mp4')
                        files.assert_not_awaited()
                        prepare.assert_not_awaited()
                    reader.assert_awaited_once_with(page)

    async def test_upload_wait_requires_single_done_without_waiting_for_automatic_cover(self):
        pending={**UNKNOWN,'recognized':True,'count':1,'video_count':1}
        no_cover={**pending,'done_count':1,'all_uploaded':True,'can_save_draft':True}
        page=object()
        reader=AsyncMock(side_effect=[UNKNOWN,pending,no_cover])
        with patch('uploader.tc_editor.read_tc_material_state',reader), \
             patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()), \
             patch('uploader.tencent_uploader.main.set_local_files',new=AsyncMock()) as files:
            await self.uploader().wait_for_upload_complete(page)
            self.assertEqual(reader.await_count,3)
            files.assert_not_awaited()

    async def test_upload_wait_rejects_duplicate_materials_without_reimport(self):
        reader=AsyncMock(return_value={**UNKNOWN,'recognized':True,'count':4,'video_count':4,'done_count':1})
        with patch('uploader.tc_editor.read_tc_material_state',reader), \
             patch('uploader.draft_only.assert_draft_safe',new=AsyncMock()), \
             patch('uploader.tencent_uploader.main.set_local_files',new=AsyncMock()) as files:
            with self.assertRaisesRegex(RuntimeError,'重复素材'):
                await self.uploader().wait_for_upload_complete(object())
            reader.assert_awaited_once()
            files.assert_not_awaited()


if __name__=='__main__':
    unittest.main()
