"""No real login/upload: current File, SDK fields and denied-publication fixtures."""
import asyncio
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright
from tests.test_tc_bili_parallel_pipeline import TC_HTML, TC_URL
from uploader import publication_workflow
from uploader.tc_pipeline import capture_tc_upload, release_tc_upload
from uploader.tc_reuse import read_tc_upload_reuse, tc_unverified_upload_reason


class TCCachedUploadDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.temp=tempfile.TemporaryDirectory(prefix='sau-tc-reuse-')
        self.video=Path(self.temp.name)/'private-local-fixture.mp4';self.data=b'original native bytes'
        self.video.write_bytes(self.data);self.sha=hashlib.sha256(self.data).hexdigest()
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context()
        cfg={'delay':100000,'failure':False,'resetContent':False,'resetInput':False,'removeInput':False}
        await self.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body=TC_HTML.replace('CONFIG',json.dumps(cfg))))
        self.page=await self.context.new_page();await self.page.goto(TC_URL)
        self.binding=await capture_tc_upload(self.page,self.page.locator('#native'));self.page._sau_tc_native_import=self.binding
        await self.page.locator('#native').set_input_files(str(self.video))
        await self.page.evaluate('''()=>{const file=material.fileList[0];file.status='done';file.thumbUrl='fixture-thumb';
          const business=()=>{businessCalls++;throw Error('SDK business method forbidden')};
          window.sdk={file:file.originFileObj,startUpload:business,reuseUpload:business,initTaskId:business,handleComplete:business,
            getAvailableUploadedInfo:business,reuseUploadType:5,isUseSnapshotUpload:true,isTerminalUpload:true,
            chunkCount:1,partInfo:[{PartNumber:1,ETag:'private-fixture-etag'}],uuid:'private-fixture-uuid',uploadTaskId:'private-fixture-upload'};
          file.uploader=sdk;
        }''',isolated_context=False)
        self.expected={'workflow_action':'publish','job_id':'fixture-job','receipt_nonce':'fixture-nonce','video_sha256':self.sha,'cover_sha256':{}}
        self.asset={'name':self.video.name,'size':len(self.data),'sha256':self.sha,'kind':'video'}
        self.finish={'tc_protocol':'dfs','metadata':(self.asset['name'],self.asset['size']),
                     'task_id':'private-fixture-uuid','upload_id':'private-fixture-upload','stage':'complete'}
        self.session=SimpleNamespace(page=self.page,platform='tencent',closed=False,expected=self.expected,assets={'video':self.asset},
          exchanges=[self.finish],issues=[],action_at=None,_checkpoint_assets=AsyncMock(),drain=AsyncMock(),upload_links=lambda:[])
        self.page._sau_draft_evidence=self.session

    async def asyncTearDown(self):
        await release_tc_upload(self.binding);await self.context.close();await self.browser.close();await self.pw.stop();self.temp.cleanup()

    async def read(self):
        result=await read_tc_upload_reuse(self.page,self.session,self.expected)
        self.assertEqual(await self.page.evaluate('businessCalls',isolated_context=False),0)
        return result

    async def reason(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):reason=await tc_unverified_upload_reason(self.page,self.session,self.expected)
        text=output.getvalue()
        for private in (self.video.name,str(self.video),self.sha,'private-fixture-uuid','private-fixture-upload','private-fixture-etag'):
            self.assertNotIn(private,text)
        self.assertEqual(await self.page.evaluate('businessCalls',isolated_context=False),0)
        return reason,json.loads(text.split('DRAFT_DIAGNOSTIC=',1)[1])

    async def test_bound_old_id_finish_has_read_only_content_free_reuse_diagnostic(self):
        result=await self.read()
        self.assertEqual(result,{'recognized':True,'state':'done','reuse_mode':'reused_url','snapshot_reused':True,
          'terminal':True,'has_upload_id':True,'planned_parts':1,'stored_parts':1,'current_allocations':0,'current_parts':0,'current_finishes':1})
        reason,diag=await self.reason();self.assertEqual(reason,'tencent_cached_upload_chain_unverified')
        self.assertEqual(diag,{'event':'tencent_upload_reuse',**result})

    async def test_none_expired_and_no_snapshot_cannot_claim_cache_reuse(self):
        for mode,snapshot in ((1,False),(2,False),(5,False)):
            await self.page.evaluate('(cfg)=>{sdk.reuseUploadType=cfg[0];sdk.isUseSnapshotUpload=cfg[1]}',[mode,snapshot],isolated_context=False)
            self.assertEqual((await self.reason())[0],'current_upload_unverified')

    async def test_fresh_allocation_or_foreign_id_is_not_finish_only_cache_diagnosis(self):
        self.session.exchanges.append({**self.finish,'stage':'allocated','plan':[len(self.data)]})
        self.assertEqual((await self.read())['current_allocations'],1)
        self.assertEqual((await self.reason())[0],'current_upload_unverified')
        self.session.exchanges=[{**self.finish,'upload_id':'foreign-id'}]
        self.assertEqual((await self.read())['current_finishes'],0)

    async def test_original_native_file_and_owner_are_required_even_with_done_status(self):
        await self.page.evaluate("sdk.file=new File(['original native bytes'],'private-local-fixture.mp4',{type:'video/mp4'})",isolated_context=False)
        self.assertIsNone(await self.read())
        self.assertEqual((await self.reason())[0],'current_upload_unverified')
        await self.page.evaluate('sdk.file=material.fileList[0].originFileObj;material.fileList=[{...material.fileList[0],originFileObj:new File(["foreign"],"foreign.mp4")}]',isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_missing_sdk_poisoned_getter_and_invalid_modes_are_never_called(self):
        await self.page.evaluate("()=>{Object.defineProperty(sdk,'reuseUploadType',{configurable:true,get(){businessCalls++;throw Error('forbidden getter')}});}",isolated_context=False)
        self.assertIsNone(await self.read())
        await self.page.evaluate("()=>{Object.defineProperty(sdk,'reuseUploadType',{configurable:true,value:6,writable:true});}",isolated_context=False)
        self.assertIsNone(await self.read())
        await self.page.evaluate("()=>{Object.defineProperty(sdk,'startUpload',{configurable:true,get(){businessCalls++;throw Error('forbidden method getter')}});}",isolated_context=False)
        self.assertIsNone(await self.read())
        await self.page.evaluate('delete material.fileList[0].uploader',isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_scope_or_selection_replacement_cannot_create_diagnostic(self):
        self.session.expected={**self.expected,'receipt_nonce':'foreign'};self.assertIsNone(await self.read())
        self.session.expected=self.expected
        await self.page.locator('#native').set_input_files(str(self.video));self.assertIsNone(await self.read())

    async def test_editor_route_change_cannot_use_an_old_connected_material(self):
        await self.page.evaluate("history.replaceState(null,'','/platform/post/list')",isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_reading_reuse_never_accepts_done_or_clicks_publish(self):
        output=io.StringIO();submit=AsyncMock();safe=AsyncMock()
        with contextlib.redirect_stdout(output),patch.object(publication_workflow,'_current',return_value=True),\
             patch('uploader.draft_only.assert_draft_safe',safe),patch.object(publication_workflow.publish_action,'publish_current',submit):
            result=await publication_workflow.publish_prepared(self.page,'tencent',expected=self.expected)
        self.assertEqual(result['code'],'tencent_cached_upload_chain_unverified')
        self.assertEqual(result['status'],'publication_blocked');self.assertFalse(result['clicked'])
        self.assertFalse(result['verification']['upload_complete']);self.assertFalse(result['verification']['submission'])
        self.assertEqual(result['observation'],{'publish_requests':0,'publish_responses':0})
        self.assertIn('复用了平台旧上传记录',result['message']);submit.assert_not_awaited()
        self.assertEqual(await self.page.evaluate('businessCalls',isolated_context=False),0)


if __name__=='__main__':unittest.main(verbosity=2)
