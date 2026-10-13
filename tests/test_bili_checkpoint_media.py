"""Native Bili resume consumer on isolated pages; no account or upload calls."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from patchright.async_api import async_playwright
from uploader.bili_evidence import BiliEvidenceObserver
from uploader.draft_only import read_checkpoint_composer_media

URL = 'https://member.bilibili.com/platform/upload/video/frame'
HTML = r'''<!doctype html><style>.upload{width:200px;height:40px}</style>
<div class="video-entrance"><div class="upload-wrp"><div class="upload bcc-upload"><input id="native" type="file" accept=".mp4"></div></div></div>
<div class="video-basic-wrp"></div><script>
window.businessCalls=0;const business=()=>{businessCalls++;throw Error('business forbidden')};
window.manager={tasks:[],currentTaskId:null,createTask:business};
window.recordConfig={data:[]};
const entrance=document.querySelector('.video-entrance');
entrance.__vue__={$el:entrance,$taskConfig:manager,$recordConfig:recordConfig,record:[],uploadDisabled:false,
 handleChange:business,initRecord:business,recovery:business};
window.publisher={$el:document.querySelector('.video-basic-wrp'),id:'fixture-task',$taskConfig:manager,$recordConfig:recordConfig,
 videoTasks:[],checkSubTask:business,handleSubmitDraft:business};publisher.$el.__vue__=publisher;
document.querySelector('#native').onchange=e=>{
 const file=e.target.files[0],inner={file,completed:true};
 const sdk={file,uploader:inner,cid:77,filename:'native-resource',uploaderType:'stdSlice',emit:business};
 window.task={file,uploader:sdk,cid:77,filename:'native-resource',status:3};publisher.videoTasks=[task];
 manager.tasks=[{id:publisher.id,videoTasks:publisher.videoTasks}];manager.currentTaskId=publisher.id;
 recordConfig.data=[{id:publisher.id,videos:[{cid:77,filename:'native-resource'}]}];
};</script>'''


class BiliCheckpointMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sau-bili-checkpoint-')
        self.bytes = b'native-full-content-' * 70000
        self.video = Path(self.temp.name) / 'sealed.mp4'
        self.video.write_bytes(self.bytes)
        self.digest = hashlib.sha256(self.bytes).hexdigest()
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, channel='chromium')
        self.context = await self.browser.new_context()
        await self.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=HTML))
        self.page = await self.context.new_page()
        await self.page.goto(URL)
        await self.page.locator('#native').set_input_files(str(self.video))
        self.session = SimpleNamespace(page=self.page, platform='bilibili', closed=False,
                                       expected={'job_id':'fixture-job','receipt_nonce':'fixture-nonce','video_sha256':self.digest})
        self.page._sau_draft_evidence = self.session
        self.observer = BiliEvidenceObserver(self.session)
        self.session.bili_observer = self.observer
        self.assertTrue(await self.observer.transport.prepare())
        self.proof = {'identity':{'platform_id':'bilibili','video_sha256':self.digest},
                      'assets':{'video':{'kind':'video','name':'sealed.mp4','size':len(self.bytes),'sha256':self.digest}},
                      'upload_links':[{'kind':'video','complete':True,'sha256':self.digest,'ids':['77','native-resource']}]}

    async def asyncTearDown(self):
        await self.observer.transport.close()
        await self.context.close()
        await self.browser.close()
        await self.pw.stop()
        self.temp.cleanup()

    async def read(self):
        result = await read_checkpoint_composer_media(self.page, self.proof)
        self.assertEqual(await self.page.evaluate('businessCalls', isolated_context=False), 0)
        self.assertEqual(self.observer.records, {})
        return result

    async def test_fresh_observer_without_network_reads_actual_native_consumer_and_full_sha(self):
        self.assertEqual(await self.page.locator('video,[data-cid],[data-file-id]').count(), 0)
        self.assertEqual(await self.read(), {'source':'platform_composer','video_count':1,
                                          'video_ids':['77','native-resource']})

    async def test_resource_equality_requires_both_cid_and_filename_not_partial_match(self):
        for ids in (['77','foreign-resource'], ['88','native-resource'], ['77'], ['77','77']):
            with self.subTest(ids=ids):
                self.proof['upload_links'][0]['ids'] = ids
                self.assertIsNone(await self.read())

    async def test_same_name_size_and_ids_with_foreign_full_bytes_reject(self):
        await self.page.evaluate("""()=>{const input=document.querySelector('#native'),foreign=new File([new Uint8Array(task.file.size)],task.file.name,{type:task.file.type}),transfer=new DataTransfer();
          transfer.items.add(foreign);input.files=transfer.files;task.file=foreign;task.uploader.file=foreign;task.uploader.uploader.file=foreign;}
        """, isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_incomplete_or_foreign_sdk_or_record_rejects(self):
        scripts = ["task.status=1", "task.uploader.uploader.completed=false", "task.uploader.file=new File(['x'],'sealed.mp4')",
                   "task.uploader.uploaderType='unknown'", "task.uploader.cid=88", "recordConfig.data[0].videos[0].filename='foreign'",
                   "recordConfig.data[0].id='foreign'", "recordConfig.data.push(recordConfig.data[0])"]
        for script in scripts:
            with self.subTest(script=script):
                await self.page.locator('#native').set_input_files(str(self.video))
                await self.page.evaluate('()=>{'+script+'}', isolated_context=False)
                self.assertIsNone(await self.read())

    async def test_ambiguous_publisher_manager_and_task_binding_rejects(self):
        scripts = ["const el=document.createElement('div');document.body.append(el);el.__vue__={...publisher,$el:el}",
                   "manager.currentTaskId='foreign'", "manager.tasks[0].videoTasks=publisher.videoTasks.slice()",
                   "manager.tasks.push({...manager.tasks[0],id:'second'})"]
        for script in scripts:
            with self.subTest(script=script):
                await self.page.evaluate("()=>{for(const el of document.querySelectorAll('body>div'))if(el.__vue__&&el!==publisher.$el&&el.className!=='video-entrance')el.remove()}", isolated_context=False)
                await self.page.locator('#native').set_input_files(str(self.video))
                await self.page.evaluate('()=>{'+script+'}', isolated_context=False)
                self.assertIsNone(await self.read())

    async def test_component_swap_during_multi_chunk_hash_rejects(self):
        await self.page.evaluate("""()=>{const original=Blob.prototype.slice;let count=0;
          Blob.prototype.slice=function(...args){const part=Reflect.apply(original,this,args);if(++count===2)recordConfig.data[0]={...recordConfig.data[0]};return part;};
        }""", isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_closed_or_noncanonical_native_context_rejects(self):
        self.observer.closed = True
        self.assertIsNone(await self.read())
        self.observer.closed = False
        self.observer.transport.contexts[self.observer.transport.context_id]['isDefault'] = False
        self.assertIsNone(await self.read())

    async def test_malformed_or_unverified_proof_cannot_supply_current_resources(self):
        original = copy.deepcopy(self.proof)
        for mutation in ('hash', 'incomplete', 'missing_asset', 'multiple_video'):
            with self.subTest(mutation=mutation):
                self.proof = copy.deepcopy(original)
                if mutation == 'hash':self.proof['identity']['video_sha256'] = 'f'*64
                elif mutation == 'incomplete':self.proof['upload_links'][0]['complete'] = False
                elif mutation == 'missing_asset':self.proof['assets']['video'] = {}
                else:self.proof['upload_links'].append(copy.deepcopy(self.proof['upload_links'][0]))
                self.assertIsNone(await self.read())


if __name__ == '__main__':
    unittest.main()
