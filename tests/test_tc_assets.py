"""Isolated official-origin DOM fixtures; no external site or account calls."""

import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from patchright.async_api import async_playwright

from uploader.tc_assets import tc_reopened_assets_match
from uploader.tc_resource import tc_resource_identity


MAIN = "https://channels.weixin.qq.com/platform/post/create"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)


def resource(name, *, token="synthetic-A"):
    return "https://finder.video.qq.com/251/20302/stodownload?" + urlencode({
        "encfilekey": "synthetic-opaque-" + name, "m": "", "uzid": "synthetic-id-" + name,
        "bizid": "1023", "hy": "SZ", "idx": "1", "scene": "1", "dotrans": "0", "token": token,
    })


def candidate(*, custom=True):
    result = {
        "video_ids": [tc_resource_identity(resource("video"))], "video_count": 1,
        "cover_ids": [tc_resource_identity(resource("thumb"))],
        "cover_slots": {"cover_portrait": [], "cover_landscape": []},
    }
    if custom:
        result["cover_slots"] = {
            "cover_portrait": [tc_resource_identity(resource("portrait"))],
            "cover_landscape": [tc_resource_identity(resource("landscape"))],
        }
        result["cover_ids"] += result["cover_slots"]["cover_portrait"] + result["cover_slots"]["cover_landscape"]
    return result


FIXTURE = """<!doctype html><style>
body {margin:0;} video {display:block;width:320px;height:180px;}
img,.preview {display:block;width:160px;height:120px;}
iframe {width:400px;height:220px;border:0;} #shadow-host {display:block;}
</style><main id="main"></main><div id="shadow-host"></div>
<iframe id="owner-frame" src="/micro/content/post/create"></iframe>
<script>window.calls={business:0,private:0,mutations:0};
window.forbidden=()=>{calls.business++;throw Error('business_call');};
window.trap=node=>Object.defineProperty(node,'__vue__',{get(){calls.private++;throw Error('VM_read');}});
</script>"""
OWNER = """<!doctype html><style>video{display:block;width:320px;height:180px;}
img,.preview{display:block;width:160px;height:120px;}</style><main id="owner"></main>"""


class TCReopenedAssetBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel="chrome")
        self.context = await self.browser.new_context()
        self.requests = []

        async def route(request):
            self.requests.append(request.request.url)
            if request.request.url.startswith("https://finder.video.qq.com/"):
                await request.fulfill(content_type="image/png", body=PNG)
            elif "/micro/content/" in request.request.url or "/other/" in request.request.url:
                await request.fulfill(content_type="text/html", body=OWNER)
            else:
                await request.fulfill(content_type="text/html", body=FIXTURE)

        await self.context.route("**/*", route)
        self.page = await self.context.new_page()
        await self.page.goto(MAIN)
        await self.page.frames[1].locator("#owner").wait_for(state="attached")

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def put(self, *, custom=True, shadow=False, owner=False, background=False):
        await self.page.evaluate("""({video,thumb,portrait,landscape,custom,shadow,owner,background})=>{
            const main=document.querySelector('#main'),host=document.querySelector('#shadow-host');
            main.replaceChildren();if(host.shadowRoot)host.shadowRoot.replaceChildren();
            const ownerDoc=document.querySelector('#owner-frame').contentDocument;
            ownerDoc.querySelector('#owner').replaceChildren();
            const root=owner?ownerDoc.querySelector('#owner'):
                shadow?(host.shadowRoot||host.attachShadow({mode:'open'})):main;
            const doc=root.ownerDocument;
            const v=doc.createElement('video');v.id='video';v.src=video;v.poster=thumb;
            v.style.cssText='display:block;width:320px;height:180px';root.append(v);window.trap(v);
            window.currentVideo=v;
            if(custom)for(const [id,url] of [['portrait',portrait],['landscape',landscape]]){
                const preview=doc.createElement(background?'div':'img');preview.id=id;
                preview.style.cssText='display:block;width:160px;height:120px';
                if(background)preview.style.backgroundImage='url("'+url+'")';else preview.src=url;
                root.append(preview);window.trap(preview);
            }
            window.mediaRoot=root;
        }""", dict(video=resource("video", token="rotated-B"), thumb=resource("thumb"),
                   portrait=resource("portrait"), landscape=resource("landscape"),
                   custom=custom, shadow=shadow, owner=owner, background=background), isolated_context=False)

    async def match_readonly(self, expected=True, record=None):
        await self.page.evaluate("""()=>{
            calls.mutations=0;window.observer?.disconnect();
            window.observer=new MutationObserver(entries=>calls.mutations+=entries.length);
            observer.observe(document,{subtree:true,childList:true,attributes:true,characterData:true});
            if(mediaRoot!==document && mediaRoot!==document.querySelector('#main'))
                observer.observe(mediaRoot,{subtree:true,childList:true,attributes:true,characterData:true});
        }""", isolated_context=False)
        before_requests = len(self.requests)
        result = await tc_reopened_assets_match(self.page, candidate() if record is None else record)
        self.assertIs(result, expected)
        self.assertEqual(await self.page.evaluate("window.calls", isolated_context=False),
                         {"business": 0, "private": 0, "mutations": 0})
        # Background/video/image requests originated from fixture setup. The
        # read-only evaluator performs no fetch, SDK call or navigation.
        await self.page.evaluate("observer.disconnect()", isolated_context=False)
        self.assertEqual(self.page.url, MAIN)
        return before_requests

    async def put_wujie_projection(self):
        # Logical Wujie owner/root overrides cannot erase the native physical
        # main shadow tree. The same saved editor/producer is independently bound.
        await self.page.evaluate('window.trap=()=>{}', isolated_context=False)
        await self.put(shadow=True)
        record = {**candidate(), 'id': 'saved-fixture', 'owner': 'fixture-owner', 'fresh': True}
        self.session = SimpleNamespace(platform='tencent', closed=False, account={'uid':'fixture-owner'},
            saves=[copy.deepcopy(record)], normal_ui_binding={'id':record['id'], 'owner':record['owner'],
                                                            'control':'post_draft_item_edit'})
        self.page._sau_draft_evidence = self.session
        await self.page.evaluate("""id=>{
          const owner=document.querySelector('#owner-frame').contentDocument,root=mediaRoot;
          document.querySelector('#owner-frame').style.display='none';
          const editor=document.createElement('section'),material=document.createElement('section');
          for(const el of [...root.children])material.append(el);editor.append(material);root.append(editor);
          window.materialVM={$el:material,fileList:[{isVideo:true,isImage:false,isValid:true,status:'done'}],
            handleFileListChange:forbidden,handleSetFileList:forbidden,handleDeleteFile:forbidden,updateFile:forbidden,
            postStore:{postObjDesc:{media:[{url:currentVideo.src}]}}};material.__vue__=materialVM;
          window.editorVM={$el:editor,draftStore:{objectId:id},$refs:{postCreate:{$el:editor}}};
          editor.__vue__=editorVM;
          for(const el of [editor,...editor.querySelectorAll('*')]){
            Object.defineProperty(el,'ownerDocument',{configurable:true,get:()=>owner});
            el.getRootNode=()=>owner;
          }
          Object.defineProperty(editor,'parentNode',{configurable:true,get:()=>owner});
        }""", record['id'], isolated_context=False)
        return record

    async def test_hidden_wujie_owner_requires_native_main_projection_and_same_saved_producer(self):
        record = await self.put_wujie_projection()
        await self.match_readonly(record=record)
        self.assertTrue(await self.page.evaluate("""()=>{
          const actual=Reflect.apply(Object.getOwnPropertyDescriptor(Node.prototype,'ownerDocument').get,currentVideo,[]);
          return actual===document&&currentVideo.ownerDocument!==actual&&
            Reflect.apply(Node.prototype.getRootNode,currentVideo,[]).host===document.querySelector('#shadow-host')&&
            currentVideo.getRootNode()===document.querySelector('#owner-frame').contentDocument;
        }""", isolated_context=False))

    async def test_projected_resource_cannot_accept_unbound_wrong_editor_or_foreign_producer(self):
        for case in ('no-session','wrong-binding','wrong-editor','duplicate-editor','foreign-producer','not-done','two-files'):
            with self.subTest(case=case):
                await self.page.reload()
                await self.page.frames[1].locator('#owner').wait_for(state='attached')
                record = await self.put_wujie_projection()
                if case == 'no-session': self.page._sau_draft_evidence = None
                if case == 'wrong-binding': self.session.normal_ui_binding['id']='foreign'
                if case == 'wrong-editor': await self.page.evaluate("editorVM.draftStore.objectId='foreign'", isolated_context=False)
                if case == 'duplicate-editor': await self.page.evaluate("const el=document.createElement('div');el.style.cssText='width:100px;height:100px';document.body.append(el);el.__vue__={draftStore:{objectId:editorVM.draftStore.objectId},$refs:{postCreate:{$el:el}}}", isolated_context=False)
                if case == 'foreign-producer': await self.page.evaluate('url=>materialVM.postStore.postObjDesc.media[0].url=url',resource('foreign'),isolated_context=False)
                if case == 'not-done': await self.page.evaluate("materialVM.fileList[0].status='uploading'",isolated_context=False)
                if case == 'two-files': await self.page.evaluate('materialVM.fileList.push(materialVM.fileList[0])',isolated_context=False)
                await self.match_readonly(False,record)

    async def test_projection_must_still_be_physically_visible_and_hidden_owner_is_not_main(self):
        record = await self.put_wujie_projection()
        await self.page.evaluate("document.querySelector('#shadow-host').style.display='none'",isolated_context=False)
        await self.match_readonly(False,record)
        await self.page.evaluate("document.querySelector('#shadow-host').style.display=''",isolated_context=False)
        await self.page.evaluate("""()=>{
          const owner=document.querySelector('#owner-frame').contentDocument;
          owner.querySelector('#owner').append(currentVideo);
          // Ordinary owner nodes have their own distinct physical root and
          // remain rejected even when their box is forced positive.
        }""",isolated_context=False)
        await self.match_readonly(False,record)

    async def test_overridden_main_native_ancestry_method_fails_closed(self):
        record = await self.put_wujie_projection()
        await self.page.evaluate('Node.prototype.getRootNode=function(){return document}',isolated_context=False)
        await self.match_readonly(False,record)

    async def test_both_custom_covers_in_images_and_css_backgrounds(self):
        for background in (False, True):
            with self.subTest(background=background):
                await self.put(background=background)
                await self.match_readonly()

    async def test_hidden_missing_and_wrong_custom_covers_do_not_use_poster(self):
        for variant in ("hidden", "hidden_ancestor", "missing", "wrong", "poster_only"):
            with self.subTest(variant=variant):
                await self.put()
                await self.page.evaluate("""({variant,wrong,portrait})=>{
                    const image=mediaRoot.querySelector('#portrait');
                    if(variant==='hidden')image.style.visibility='hidden';
                    if(variant==='hidden_ancestor'){
                        const wrap=document.createElement('div');wrap.style.opacity='0';
                        image.before(wrap);wrap.append(image);
                    }
                    if(variant==='missing')image.remove();
                    if(variant==='wrong')image.src=wrong;
                    if(variant==='poster_only'){image.remove();currentVideo.poster=portrait;}
                }""", dict(variant=variant, wrong=resource("wrong"), portrait=resource("portrait")),
                                         isolated_context=False)
                await self.match_readonly(False)

    async def test_wrong_blob_multiple_and_hidden_video_fail(self):
        for variant in ("wrong", "blob", "http", "two", "hidden"):
            with self.subTest(variant=variant):
                await self.put()
                await self.page.evaluate("""({variant,wrong})=>{
                    if(variant==='wrong')currentVideo.src=wrong;
                    if(variant==='blob')currentVideo.src=URL.createObjectURL(new Blob(['synthetic-video']));
                    if(variant==='http')currentVideo.src=currentVideo.src.replace('https://finder.video.qq.com','http://wxapp.tc.qq.com');
                    if(variant==='two'){const other=currentVideo.cloneNode();other.id='second';mediaRoot.append(other);}
                    if(variant==='hidden')currentVideo.style.display='none';
                }""", dict(variant=variant, wrong=resource("wrong")), isolated_context=False)
                await self.match_readonly(False)

    async def test_no_custom_slots_can_use_only_attested_automatic_poster(self):
        await self.put(custom=False)
        await self.match_readonly(record=candidate(custom=False))
        await self.page.evaluate("currentVideo.poster=''", isolated_context=False)
        await self.match_readonly(False, candidate(custom=False))
        await self.page.evaluate("currentVideo.poster=URL.createObjectURL(new Blob(['synthetic-cover']))",
                                 isolated_context=False)
        await self.match_readonly(False, candidate(custom=False))

    async def test_open_shadow_and_same_physical_owner_projection_are_deduplicated(self):
        await self.put(shadow=True)
        await self.match_readonly()
        await self.page.evaluate("""()=>{
            const frame=document.querySelector('#owner-frame'),owner=frame.contentDocument;
            frame.style.display='none';
            const original=owner.querySelectorAll.bind(owner);
            owner.querySelectorAll=selector=>selector==='*'?
                [...original(selector),...mediaRoot.querySelectorAll('*')]:original(selector);
        }""", isolated_context=False)
        await self.match_readonly()
        # A genuinely distinct physical video in the main realm is not
        # collapsed merely because it has the same src/currentSrc/poster.
        await self.page.evaluate("document.querySelector('#main').append(currentVideo.cloneNode())",
                                 isolated_context=False)
        await self.match_readonly(False)

    async def test_visible_owner_frame_is_read_but_hidden_or_non_content_frame_is_not(self):
        await self.put(owner=True)
        await self.match_readonly()
        await self.page.evaluate("document.querySelector('#owner-frame').style.display='none'",
                                 isolated_context=False)
        await self.match_readonly(False)
        await self.page.evaluate("document.querySelector('#owner-frame').style.display=''",
                                 isolated_context=False)
        await self.page.frames[1].goto("https://channels.weixin.qq.com/other/editor")
        await self.put(owner=True)
        await self.match_readonly(False)

    async def test_node_and_root_limits_fail_closed(self):
        for limit in ("nodes", "roots"):
            with self.subTest(limit=limit):
                await self.put()
                await self.page.evaluate("""limit=>{
                    const fragment=document.createDocumentFragment();
                    const count=limit==='nodes'?20001:64;
                    for(let i=0;i<count;i++){
                        const node=document.createElement('div');
                        if(limit==='roots')node.attachShadow({mode:'open'});fragment.append(node);
                    }
                    document.body.append(fragment);window.extra=fragment;
                }""", limit, isolated_context=False)
                await self.match_readonly(False)
                # New document between limit cases, all requests remain local.
                await self.page.reload()
                await self.page.frames[1].locator("#owner").wait_for(state="attached")


class TCReopenedAssetContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from uploader.tc_assets import _READ
        import json
        async def native_fixture(page, expression, **kwargs):
            prefix = '(' + _READ + ')('
            self.assertTrue(expression.startswith(prefix))
            scope = json.loads(expression[len(prefix):-1])
            return await page.main_frame.evaluate(_READ, scope, isolated_context=False)
        self.native_patch = patch('uploader.tc_assets.canonical_root_evaluate', side_effect=native_fixture)
        self.native_patch.start()

    async def asyncTearDown(self):
        self.native_patch.stop()

    def page(self, snapshot=None):
        frame = SimpleNamespace(url=MAIN, evaluate=AsyncMock(return_value=snapshot))
        return SimpleNamespace(url=MAIN, main_frame=frame)

    async def test_malformed_candidate_does_not_read_page(self):
        cases = [None, [], {"video_ids": []}]
        for field, value in (
                ("video_ids", "tc-resource:" + "a" * 64),
                ("video_ids", ["raw-server-id"]),
                ("video_ids", [tc_resource_identity(resource("video"))] * 2),
                ("video_ids", [tc_resource_identity(resource("video")), tc_resource_identity(resource("wrong"))]),
                ("cover_ids", None), ("cover_ids", [resource("portrait")]),
                ("cover_slots", []), ("cover_slots", {"unknown": []}),
                ("cover_slots", {"cover_portrait": [tc_resource_identity(resource("portrait"))]}),
                ("cover_slots", {"cover_portrait": [tc_resource_identity(resource("portrait"))], "cover_landscape": []}),
                ("cover_slots", {"cover_portrait": [tc_resource_identity(resource("portrait"))] * 2,
                                 "cover_landscape": [tc_resource_identity(resource("landscape"))]}),
                ("video_count", True), ("video_count", 2)):
            cases.append({**candidate(), field: value})
        for record in cases:
            with self.subTest(index=cases.index(record)):
                page = self.page()
                self.assertFalse(await tc_reopened_assets_match(page, record))
                page.main_frame.evaluate.assert_not_awaited()

    async def test_missing_wrong_origin_navigation_timeout_and_bad_projection_fail(self):
        good = {"videos": [{"sources": [resource("video")], "poster": resource("thumb")}],
                "images": [resource("portrait"), resource("landscape")]}
        for snapshot in (None, [], {"videos": [], "images": []},
                         {**good, "private": "not_allowed"}, {**good, "images": "not-an-array"},
                         {**good, "videos": [{"sources": [resource("video"), resource("wrong")], "poster": None}]}):
            page = self.page(snapshot)
            self.assertFalse(await tc_reopened_assets_match(page, candidate()))
        page = self.page(good)
        page.url = "https://not-channels.invalid/platform/post/create"
        self.assertFalse(await tc_reopened_assets_match(page, candidate()))
        page.main_frame.evaluate.assert_not_awaited()
        page = self.page(good)

        async def changed(*args, **kwargs):
            page.url = MAIN + "?changed=1"
            return good

        page.main_frame.evaluate.side_effect = changed
        self.assertFalse(await tc_reopened_assets_match(page, candidate()))
        page = self.page(good)
        async def stalled(*args, **kwargs):
            raise TimeoutError()
        with patch('uploader.tc_assets.canonical_root_evaluate', side_effect=stalled):
            self.assertFalse(await tc_reopened_assets_match(page, candidate()))

    async def test_scoped_projection_binding_cannot_survive_session_change_during_read(self):
        good = {'videos':[{'sources':[resource('video')],'poster':resource('thumb')}],
                'images':[resource('portrait'),resource('landscape')]}
        for case in ('closed','foreign-session','binding','saved-record','account'):
            with self.subTest(case=case):
                record = {**candidate(), 'id':'saved-fixture','owner':'fixture-owner','fresh':True}
                page = self.page(good)
                session = SimpleNamespace(platform='tencent',closed=False,account={'uid':record['owner']},
                    saves=[copy.deepcopy(record)],normal_ui_binding={'id':record['id'],'owner':record['owner'],
                                                                   'control':'post_draft_item_edit'})
                page._sau_draft_evidence = session
                async def changed(*args, **kwargs):
                    self.assertEqual(args[1], {'id':record['id']})
                    if case == 'closed':session.closed=True
                    elif case == 'foreign-session':page._sau_draft_evidence=SimpleNamespace()
                    elif case == 'binding':session.normal_ui_binding['id']='foreign'
                    elif case == 'saved-record':session.saves.clear()
                    elif case == 'account':session.account['uid']='foreign'
                    return good
                page.main_frame.evaluate.side_effect = changed
                self.assertFalse(await tc_reopened_assets_match(page,record))

    async def test_candidate_is_detached_before_await_and_read_is_single_real_realm(self):
        record = candidate()
        good = {"videos": [{"sources": [resource("video")], "poster": resource("thumb")}],
                "images": [resource("portrait"), resource("landscape")]}
        page = self.page(good)
        original = copy.deepcopy(record)

        async def mutated(*args, **kwargs):
            record["video_ids"][0] = tc_resource_identity(resource("wrong"))
            record["cover_slots"]["cover_portrait"][0] = tc_resource_identity(resource("wrong"))
            return good

        page.main_frame.evaluate.side_effect = mutated
        self.assertTrue(await tc_reopened_assets_match(page, record))
        page.main_frame.evaluate.assert_awaited_once()
        self.assertFalse(page.main_frame.evaluate.call_args.kwargs["isolated_context"])
        self.assertNotEqual(record, original)


if __name__ == "__main__":
    unittest.main()
