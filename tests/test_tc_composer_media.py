"""Isolated live-composer correlations; routes never reach a platform."""
import unittest
from patchright.async_api import async_playwright
from uploader.tc_editor import read_tc_composer_media
from tests.test_tc_editor import FIXTURE
from tests.test_tc_resource import resource
from uploader.tc_resource import tc_resource_identity


class TCComposerMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p=await async_playwright().start()
        self.b=await self.p.chromium.launch(headless=True,channel='chrome')
        self.c=await self.b.new_context()
        await self.c.route('**/*',lambda route:route.fulfill(content_type='text/html',body=FIXTURE))
        self.page=await self.c.new_page()
        await self.page.goto('https://channels.weixin.qq.com/platform/post/create')

    async def asyncTearDown(self):
        await self.c.close();await self.b.close();await self.p.stop()

    async def setup_entry(self):
        https=resource();http=resource(origin='http://wxapp.tc.qq.com')
        await self.page.evaluate('''async urls=>{
          const vm=window.makeMaterial(['done']);
          const previous=vm.fileList[0],file={isVideo:true,isImage:false,isValid:true,
            status:'done',thumbUrl:previous.thumbUrl,originFileObj:previous.originFileObj,
            response:{DownloadURL:urls[0],httpsUrl:urls[1]},httpsUrl:urls[1]};
          file.url=URL.createObjectURL(new Blob(['invalid video fixture'],{type:'video/mp4'}));
          vm.fileList=[file];vm.$refs.postUpload.value=vm.fileList.slice();
          window.material=vm;document.querySelector('#main').__vue__=vm;
          const video=document.createElement('video');video.width=320;video.height=180;
          window.preview=video;document.body.append(video);
          await new Promise(resolve=>{video.onerror=resolve;video.onloadedmetadata=resolve;video.src=file.url;});
        }''',[http,https],isolated_context=False)

    async def test_blob_preview_is_bound_to_the_native_entry_and_sealed_resource(self):
        await self.setup_entry()
        result=await read_tc_composer_media(self.page)
        self.assertEqual(result,{'source':'platform_composer','video_count':1,
                                'video_ids':[tc_resource_identity(resource())]})
        self.assertEqual(await self.page.evaluate('window.calls',isolated_context=False),
                         {'business':0,'mutable':0,'private':0})
        self.assertNotIn('http',str(result))

    async def test_ambiguous_pending_mismatched_forged_or_hidden_composer_is_rejected(self):
        cases={
          'second_video':'document.body.append(preview.cloneNode());',
          'second_material':'document.querySelector("#shadow-host").__vue__=window.makeMaterial(["done"]);',
          'pending':'material.fileList[0].status="croping";',
          'missing_crop':'material.fileList[0].thumbUrl="";',
          'mismatched_response':'material.fileList[0].response.httpsUrl+="&unknown=1";',
          'mismatched_download':'material.fileList[0].response.DownloadURL=material.fileList[0].response.DownloadURL.replace("idx=1","idx=2");',
          'forged_file':'material.fileList[0].originFileObj={name:"private-name.mp4",size:21};',
          'wrong_upload':'material.$refs.postUpload.value=[{...material.fileList[0],originFileObj:new File(["other"],"other.mp4")}];',
          'missing_native':'material.fileList[0].originFileObj=null;',
          'wrong_preview':'material.fileList[0].url="blob:another";',
          'hidden_preview':'preview.style.display="none";',
        }
        for name,script in cases.items():
            with self.subTest(name=name):
                await self.page.reload()
                await self.setup_entry()
                await self.page.evaluate('()=>{'+script+'}',isolated_context=False)
                self.assertIsNone(await read_tc_composer_media(self.page))
