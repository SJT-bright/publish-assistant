"""Current owner-realm cover projections; every request is fulfilled locally."""
import asyncio
import json
import unittest
from types import SimpleNamespace

from patchright.async_api import async_playwright
from tests.test_tc_editor import FIXTURE
from tests.test_tc_assets import resource
from uploader.tc_resource import tc_resource_identity
from uploader.publication_workflow import _tc_current_media

MAIN='https://channels.weixin.qq.com/platform/post/create'
OWNER='https://channels.weixin.qq.com/micro/content/post/create'


class TCPreparedMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context()
        await self.context.route('**/*',lambda r:r.fulfill(content_type='text/html',body=FIXTURE if r.request.url==MAIN else '<div id="preview" style="width:100px;height:80px"></div>'))
        self.page=await self.context.new_page();await self.page.goto(MAIN)
        self.owner=next(f for f in self.page.frames if f is not self.page.main_frame)
        await self.owner.locator('#preview').wait_for(state='attached')
        video=resource('video');self.ids={slot:tc_resource_identity(resource(slot)) for slot in ('cover_portrait','cover_landscape')}
        self.expected={'video_sha256':'a'*64,'cover_sha256':{'cover_portrait':'b'*64,'cover_landscape':'c'*64}}
        self.links=[{'kind':'video','sha256':'a'*64,'complete':True,'ids':[tc_resource_identity(video)]}]
        self.candidate={'video_ids':self.links[0]['ids'],'cover_slots':{k:[v] for k,v in self.ids.items()}}
        self.scope={'job_id':'local-fixture','receipt_nonce':'local-nonce','observer_nonce':'local-observer'}
        records={slot:{'slot':slot,'origin_sha256':self.expected['cover_sha256'][slot],'applied_id':resource(slot)} for slot in self.ids}
        self.observer=SimpleNamespace(scope=self.scope,frames=set(self.page.frames),records=records)
        self.session=SimpleNamespace(tc_cover_observer=self.observer)
        await self.page.evaluate('''async urls=>{
          const vm=makeMaterial(['done']),old=vm.fileList[0],file={originFileObj:old.originFileObj,isVideo:true,isImage:false,isValid:true,
            status:'done',thumbUrl:old.thumbUrl,response:{DownloadURL:urls[0],httpsUrl:urls[1]},httpsUrl:urls[1]};
          file.url=URL.createObjectURL(new Blob(['local video'],{type:'video/mp4'}));vm.fileList=[file];vm.$refs.postUpload.value=[file];
          window.material=vm;document.querySelector('#main').__vue__=vm;const video=document.createElement('video');video.width=320;video.height=180;
          document.body.append(video);await new Promise(r=>{video.onerror=r;video.onloadedmetadata=r;video.src=file.url;});
        }''',[video.replace('https://finder.video.qq.com','http://wxapp.tc.qq.com'),video],isolated_context=False)
        await self.project(self.page.main_frame,active=False)

    async def asyncTearDown(self):
        await self.context.close();await self.browser.close();await self.pw.stop()

    async def project(self,frame,*,active=True,wrong=False,alias=False,nonce=None):
        payload={'scope':{**self.scope,'observer_nonce':nonce or self.scope['observer_nonce']},'active':active,
                 'portrait':resource('wrong' if wrong else 'cover_portrait',token='other-signature' if alias else 'synthetic-A'),
                 'landscape':resource('cover_landscape')}
        await frame.evaluate('''cfg=>{
          let el=document.querySelector('#preview');if(!el){el=document.createElement('div');el.id='preview';el.style='width:100px;height:80px';document.body.append(el);}
          const root={scope:cfg.scope,wrappers:new Map(),parents:new WeakMap()};
          if(cfg.active){const parent={},preview={$el:el,video:{coverUrl:cfg.portrait,shareCoverUrl:cfg.landscape}};
            root.wrappers.set(parent,new Map());root.parents.set(parent,{scope:root.scope,previewInstance:preview,invocation:{applied:true}});}
          window.projectionReads=0;Object.defineProperty(window,'__sauTCCoverObserver',{configurable:true,get(){projectionReads++;return root;}});
        }''',payload,isolated_context=False)

    async def read(self):
        result=await _tc_current_media(self.page,self.expected,self.session,self.links,self.candidate)
        self.assertEqual(await self.page.evaluate('calls',isolated_context=False),{'business':0,'mutable':0,'private':0})
        return result

    async def test_owner_only_current_projection_releases_fresh_media_gate(self):
        await self.project(self.owner);self.assertTrue(await self.read())

    async def test_equal_main_and_owner_signed_aliases_agree(self):
        await self.project(self.page.main_frame);await self.project(self.owner,alias=True)
        self.assertTrue(await self.read())

    async def test_conflicting_current_projections_fail_closed(self):
        await self.project(self.page.main_frame);await self.project(self.owner,wrong=True)
        self.assertFalse(await self.read())

    async def test_foreign_non_editor_and_aboutblank_realms_are_not_read(self):
        for url in ('https://foreign.invalid/micro/content/post/create','https://channels.weixin.qq.com/micro/other','about:blank'):
            await self.page.locator('iframe').evaluate('(el,url)=>el.src=url',url)
            for _ in range(100):
                candidates=[f for f in self.page.frames if f is not self.page.main_frame and f.url==url]
                if len(candidates)==1:
                    self.owner=candidates[0];break
                await asyncio.sleep(.01)
            else:self.fail('fixture owner navigation did not settle')
            self.observer.frames=set(self.page.frames)
            if url=='about:blank':await self.owner.set_content('<div id="preview" style="width:100px;height:80px"></div>')
            await self.project(self.owner);self.assertFalse(await self.read())
            self.assertEqual(await self.owner.evaluate('projectionReads',isolated_context=False),0)

    async def test_wrong_scope_or_hidden_preview_cannot_release(self):
        await self.project(self.owner,nonce='foreign-nonce');self.assertFalse(await self.read())
        await self.project(self.owner);await self.owner.locator('#preview').evaluate("e=>e.style.display='none'")
        self.assertFalse(await self.read())

    async def test_changed_applied_resource_and_original_digest_fail_closed(self):
        await self.project(self.owner)
        self.observer.records['cover_portrait']['origin_sha256']='d'*64;self.assertFalse(await self.read())
        self.observer.records['cover_portrait']['origin_sha256']='b'*64
        self.observer.records['cover_portrait']['applied_id']=resource('foreign');self.assertFalse(await self.read())

    async def test_other_page_frame_cannot_release_current_page(self):
        other=await self.context.new_page();await other.goto(OWNER);await self.project(other.main_frame)
        self.observer.frames.add(other.main_frame);self.assertFalse(await self.read())
        self.assertEqual(await other.evaluate('projectionReads',isolated_context=False),0)
        await other.close()


if __name__=='__main__':unittest.main(verbosity=2)
