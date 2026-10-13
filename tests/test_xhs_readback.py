"""Native-root isolated XHS readback fixtures, never formal pages or media."""
import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader.native_read import NativeReadError, canonical_root_evaluate
from uploader.xhs_readback import read_xhs_reopened_ui, xhs_readback_expression, XHS_VIDEO_OWNER_PROJECTION
from uploader.xiaohongshu_uploader.main import draft_xhs_description_plan

URL='https://creator.xiaohongshu.com/publish/publish?target=video'
HTML='''<!doctype html><meta charset="utf-8"><style>
#editor{width:400px;min-height:120px}video{width:300px;height:170px}
#cover{width:150px;height:120px;background-image:url("https://fixture.test/cover-resource")}
</style><div id="creator-publish-dom"><input placeholder="填写标题" value="隔离标题">
<div id="editor" contenteditable="true"><p>第一段</p><p><br></p><p>第二段
<a contenteditable="false" data-topic='{"id":"synthetic-topic-A","name":"科普"}'>#科普[话题]#</a>
<a contenteditable="false" data-topic='{"id":"synthetic-topic-B","name":"验收"}'>#验收[话题]#</a></p></div>
<video src="https://fixture.test/video-resource"></video><div id="cover" class="cover"></div></div>
<iframe src="/micro/foreign"></iframe><script>
const editor=document.querySelector('#editor');
window._tiptap_editor={view:{dom:editor,docView:{children:[]}},commands:{setTextSelection(){throw Error('selection_forbidden')}}};
window.fixture={mutations:0,business:0};
window.publish={getDraftState(){fixture.business++;throw Error('getter_forbidden')},getPublishState(){fixture.business++;throw Error('getter_forbidden')}};
window.observer=new MutationObserver(e=>fixture.mutations+=e.length);
observer.observe(document,{subtree:true,attributes:true,childList:true,characterData:true});
</script>'''.replace('第二段\n','第二段 ').replace('</a>\n<a','</a> <a')


class XhsReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True,channel='chromium')
        self.context=await self.browser.new_context()
        self.requests=[]
        async def route(r):
            self.requests.append(r.request.url)
            if r.request.url.startswith('https://fixture.test/'):
                await r.fulfill(status=200,content_type='application/octet-stream',body=b'isolated-fixture')
            elif r.request.url.endswith('/micro/foreign'):
                await r.fulfill(content_type='text/html',body='<input placeholder="标题" value="foreign"><div contenteditable="true">foreign</div>')
            else:
                await r.fulfill(content_type='text/html',body=HTML)
        await self.context.route('**/*',route)
        self.page=await self.context.new_page();await self.page.goto(URL)
        await self.page.frames[1].locator('input').wait_for(state='attached')
        self.reset_session()

    def reset_session(self):
        self.fields={'title':'隔离标题','description':'第一段\n\n第二段 #科普 #验收','tags':['科普','验收']}
        self.account={'verified':True,'platform':'xiaohongshu','uid':123,'identity':'isolated-account-hash'}
        self.candidate={'id':'isolated-draft','owner':'123','fresh':True,'title':self.fields['title'],
            'description':draft_xhs_description_plan(self.fields['description'],self.fields['tags'])['plain'],
            'tags':copy.deepcopy(self.fields['tags']),'video_ids':['video-resource'],'cover_ids':['cover-resource'],
            'video_count':1,'cover_slots':{}}
        self.session=SimpleNamespace(page=self.page,platform='xiaohongshu',closed=False,account=copy.deepcopy(self.account),
            expected={'account':copy.deepcopy(self.account),'platform_fields':copy.deepcopy(self.fields)},saves=[],
            normal_ui_binding={'id':self.candidate['id'],'owner':self.candidate['owner'],'control':'draft_item_edit'},
            normal_ui_candidate=copy.deepcopy(self.candidate))
        self.page._sau_draft_evidence=self.session

    async def asyncTearDown(self):
        await self.context.close();await self.browser.close();await self.pw.stop()

    async def js(self,code):
        return await self.page.evaluate(code,isolated_context=False)

    async def test_canonical_current_editor_fields_and_selected_cover_background_match_readonly(self):
        # A broken library main-frame evaluator must never become a fallback.
        with patch.object(self.page,'evaluate',AsyncMock(side_effect=AssertionError('library_realm_forbidden'))):
            result=await read_xhs_reopened_ui(self.page,self.candidate)
        self.assertTrue(result['fields_ok']);self.assertTrue(result['assets_ok'])
        self.assertEqual(result['canonical_topic_count'],2)
        self.assertEqual(result['cover_background_match_count'],1)
        self.assertEqual(result['video_exact_id_match_count'],1)
        self.assertEqual(await self.js('()=>fixture'),{'business':0,'mutations':0})
        self.assertEqual(self.page.url,URL);self.assertEqual(self.session.saves,[])
        self.assertTrue(all(type(v) in (bool,int) for v in result.values()))

    async def test_blob_video_never_matches_saved_ids_or_poster_alone(self):
        await self.js('''()=>{const v=document.querySelector('video');v.src=URL.createObjectURL(new Blob(['synthetic']));
            v.poster='https://fixture.test/cover-resource';}''')
        result=await read_xhs_reopened_ui(self.page,self.candidate)
        self.assertTrue(result['fields_ok']);self.assertFalse(result['assets_ok'])
        self.assertEqual(result['video_blob_count'],1);self.assertEqual(result['video_exact_id_match_count'],0)
        self.assertEqual(result['video_poster_cover_match_count'],1)

    async def test_wrong_title_duplicate_title_or_wrong_topic_fail_fields(self):
        for change in ("document.querySelector('input').value='wrong'",
                       "document.querySelector('input').after(document.querySelector('input').cloneNode())",
                       "document.querySelector('a').setAttribute('data-topic','{}')"):
            with self.subTest(change=change):
                await self.page.reload();await self.js('()=>{'+change+';}')
                result=await read_xhs_reopened_ui(self.page,self.candidate)
                self.assertFalse(result['fields_ok'])

    async def test_generic_editable_cannot_replace_detached_current_view(self):
        await self.js("()=>{_tiptap_editor.view.dom.remove();const e=document.createElement('div');e.contentEditable='true';e.textContent='第一段';document.body.append(e);}")
        result=await read_xhs_reopened_ui(self.page,self.candidate)
        self.assertFalse(result['fields_ok']);self.assertFalse(result['current_editor_owned'])

    async def test_canonical_paragraph_whitespace_is_not_flattened(self):
        await self.js("()=>document.querySelector('#editor p').append(document.createTextNode(' '))")
        result=await read_xhs_reopened_ui(self.page,self.candidate)
        self.assertTrue(result['canonical_read_valid']);self.assertFalse(result['canonical_plain_equal'])
        self.assertFalse(result['fields_ok'])

    async def test_separator_requires_current_prosemirror_ownership(self):
        await self.js('''()=>{const p=document.querySelector('#editor').lastElementChild;
            const img=document.createElement('img');img.className='ProseMirror-separator';img.alt='';
            const br=document.createElement('br');br.className='ProseMirror-trailingBreak';p.append(img,br);
            const desc={dom:img,size:0,children:[],parent:_tiptap_editor.view.docView};img.pmViewDesc=desc;
            _tiptap_editor.view.docView.children=[desc];}''')
        self.assertTrue((await read_xhs_reopened_ui(self.page,self.candidate))['fields_ok'])
        await self.js("()=>document.querySelector('img').pmViewDesc.parent={children:[]}")
        result=await read_xhs_reopened_ui(self.page,self.candidate)
        self.assertFalse(result['canonical_read_valid']);self.assertFalse(result['fields_ok'])

    async def test_stale_wrong_owner_or_unbound_normal_edit_never_calls_native(self):
        for case in ('stale','owner','binding','attestation','closed','account','expected'):
            with self.subTest(case=case):
                self.reset_session()
                if case=='stale':self.candidate['fresh']=False
                elif case=='owner':self.candidate['owner']='other-owner'
                elif case=='binding':self.session.normal_ui_binding['control']='unknown'
                elif case=='attestation':self.session.normal_ui_candidate['video_ids']=['foreign']
                elif case=='closed':self.session.closed=True
                elif case=='account':self.session.account['uid']='different-owner'
                elif case=='expected':self.session.expected['platform_fields']['description']='foreign'
                with patch('uploader.xhs_readback.canonical_root_evaluate',AsyncMock()) as native:
                    result=await read_xhs_reopened_ui(self.page,self.candidate)
                    native.assert_not_called();self.assertFalse(result['valid'])

    async def test_session_candidate_expected_account_binding_changes_during_read_rejected(self):
        unchanged=await read_xhs_reopened_ui(self.page,self.candidate)
        for case in ('session','candidate','expected','account','binding','attestation','closed','platform'):
            with self.subTest(case=case):
                self.reset_session()
                async def mutate(*args,**kwargs):
                    if case=='session':self.page._sau_draft_evidence=copy.copy(self.session)
                    elif case=='candidate':self.candidate['video_ids'].append('foreign')
                    elif case=='expected':self.session.expected['platform_fields']['tags'].reverse()
                    elif case=='account':self.session.account['identity']='changed'
                    elif case=='binding':self.session.normal_ui_binding['id']='foreign'
                    elif case=='attestation':self.session.normal_ui_candidate['cover_ids']=['foreign']
                    elif case=='closed':self.session.closed=True
                    elif case=='platform':self.session.platform='tencent'
                    return copy.deepcopy(unchanged)
                with patch('uploader.xhs_readback.canonical_root_evaluate',side_effect=mutate):
                    result=await read_xhs_reopened_ui(self.page,self.candidate)
                self.assertFalse(result['valid']);self.assertEqual(result['reason'],'session_changed_during_read')

    async def test_unexpected_projection_never_returns_private_values(self):
        with patch('uploader.xhs_readback.canonical_root_evaluate',AsyncMock(return_value={'private':'secret'})):
            result=await read_xhs_reopened_ui(self.page,self.candidate)
        self.assertEqual(result['reason'],'projection_invalid');self.assertNotIn('secret',json.dumps(result))

    async def test_native_timeout_is_fixed_failure_without_library_fallback(self):
        with patch('uploader.xhs_readback.canonical_root_evaluate',AsyncMock(side_effect=NativeReadError('native_read_timeout'))) as native:
            result=await read_xhs_reopened_ui(self.page,self.candidate,timeout=.2)
        self.assertFalse(result['valid']);self.assertEqual(result['reason'],'native_read_timeout')
        self.assertEqual(native.call_args.kwargs,{'timeout':.2})

    async def test_foreign_root_path_is_rejected_before_native_read(self):
        await self.page.goto('https://creator.xiaohongshu.com/other')
        with patch('uploader.xhs_readback.canonical_root_evaluate',AsyncMock()) as native:
            result=await read_xhs_reopened_ui(self.page,self.candidate)
        native.assert_not_called();self.assertEqual(result['reason'],'document_scope_invalid')

    async def owner_fixture(self):
        await self.js('''()=>{
            const root=document.querySelector('#creator-publish-dom'),video=document.querySelector('video');
            const blob=URL.createObjectURL(new Blob(['synthetic']));video.src=blob;
            const container=document.createElement('div');root.append(container);container.append(video);
            const app={_container:container,_instance:null};container.__vue_app__=app;
            const state={video:{__v_isRef:true,value:{fileid:'video-resource',transcodeVideoFileId:''}},
                videoBlobUrl:{__v_isRef:true,value:blob},videoUrl:{__v_isRef:true,value:'https://fixture.test/video-resource'},
                title:{__v_isRef:true,value:'隔离标题'},desc:{__v_isRef:true,value:'第一段\\n\\n第二段 #科普 #验收'}};
            const native={type:'video',el:video,children:null},playerEl=document.createElement('div');
            playerEl.className='player';container.append(playerEl);playerEl.append(video);
            const playerNode={type:{__scopeId:'data-v-286252ff'},el:playerEl,props:{src:blob}};
            const player={vnode:playerNode,subTree:{type:'div',el:playerEl,children:[]},props:{src:blob},
                exposed:{video:{__v_isRef:true,value:video}},appContext:{app},isUnmounted:false};playerNode.component=player;
            const previewNode={type:{},el:container,props:{state}};
            const preview={vnode:previewNode,subTree:{type:'div',el:container,children:[playerNode]},props:{state},appContext:{app},isUnmounted:false};previewNode.component=preview;
            container._vnode=previewNode;window.publish={el:root,app};
            window.ownerFixture={app,state,native,playerNode,player,playerEl,previewNode,preview,root,container,video};
        }''')

    async def owner_projection(self):
        args={'video_ids':self.candidate['video_ids'],'title':self.candidate['title'],
              'description':self.candidate['description']}
        return await canonical_root_evaluate(self.page,'('+XHS_VIDEO_OWNER_PROJECTION+')('+json.dumps(args)+')')

    async def owner_server_source_fixture(self):
        await self.owner_fixture()
        await self.js('''()=>{
            const f=ownerFixture,src=f.state.videoUrl.value;
            f.player.props.src=src;f.playerNode.props.src=src;
            f.sdk={config:{url:src},options:{url:src}};
            f.player.exposed.player={__v_isRef:true,value:f.sdk};
        }''')

    async def test_owner_diagnostic_follows_exact_current_video_render_branch_without_dom_vm_annotations(self):
        await self.owner_fixture()
        result=await self.owner_projection()
        self.assertTrue(result['valid']);self.assertEqual(result['exact_video_vnode_count'],0)
        self.assertEqual(result['abort_reason_code'],'none')
        self.assertEqual(result['source_consumer_count'],1);self.assertEqual(result['state_video_ids_equal_count'],1)
        self.assertEqual(result['fully_bound_blob_video_count'],1)
        self.assertEqual(result['player_src_blob_equal_count'],1)
        # This is a diagnostic contract fixture; it never widens production.
        self.assertFalse((await read_xhs_reopened_ui(self.page,self.candidate))['assets_ok'])

    async def test_repeated_current_vnode_aborts_with_fixed_identity_diagnostics(self):
        await self.owner_server_source_fixture()
        await self.js('''()=>{const f=ownerFixture;f.preview.subTree.children.push(f.playerNode);}''')
        result=await self.owner_projection()
        self.assertFalse(result['valid']);self.assertEqual(result['abort_reason_code'],'duplicate_vnode')
        self.assertEqual(result['duplicate_vnode_count'],1)
        self.assertEqual(result['duplicate_vnode_shared_reference_count'],1)
        self.assertEqual(result['duplicate_vnode_cycle_count'],0)
        self.assertEqual(result['duplicate_component_vnode_count'],1)
        self.assertEqual(result['duplicate_player_scope_vnode_count'],1)
        self.assertEqual(result['source_consumer_count'],1)
        self.assertEqual(result['fully_bound_blob_video_count'],0)

    async def test_invalid_current_component_link_has_fixed_subtree_diagnostic(self):
        await self.owner_server_source_fixture()
        await self.js('''()=>{ownerFixture.player.subTree=null;}''')
        result=await self.owner_projection()
        self.assertFalse(result['valid']);self.assertEqual(result['abort_reason_code'],'component_link_invalid')
        self.assertEqual(result['component_link_invalid_count'],1)
        self.assertEqual(result['component_subtree_missing_count'],1)
        self.assertEqual(result['component_unmounted_count'],0)
        self.assertEqual(result['component_vnode_link_mismatch_count'],0)
        self.assertEqual(result['fully_bound_blob_video_count'],0)

    async def test_current_suspense_branch_does_not_walk_pending_cache_reference(self):
        await self.owner_server_source_fixture()
        await self.js('''()=>{const f=ownerFixture,boundary={type:{__isSuspense:true},el:f.container,
            children:{default:()=>f.playerNode,fallback:()=>null},
            suspense:{activeBranch:f.playerNode,pendingBranch:f.playerNode}};f.preview.subTree=boundary;}''')
        result=await self.owner_projection()
        self.assertTrue(result['valid']);self.assertEqual(result['abort_reason_code'],'none')
        self.assertEqual(result['suspense_vnode_count'],1)
        self.assertEqual(result['suspense_active_branch_count'],1)
        self.assertEqual(result['duplicate_vnode_count'],0)
        self.assertEqual(result['source_consumer_count'],1)
        self.assertEqual(result['fully_bound_blob_video_count'],1)

    async def test_server_url_handoff_binds_sdk_url_to_its_exposed_blob_video(self):
        await self.owner_server_source_fixture()
        result=await self.owner_projection()
        self.assertTrue(result['valid'])
        self.assertEqual(result['player_scope_component_count'],1)
        self.assertEqual(result['player_exposed_video_equal_count'],1)
        self.assertEqual(result['player_src_equal_count'],1)
        self.assertEqual(result['player_src_blob_equal_count'],0)
        self.assertEqual(result['player_src_candidate_id_equal_count'],1)
        self.assertEqual(result['player_sdk_config_url_equal_count'],1)
        self.assertEqual(result['player_sdk_options_url_equal_count'],1)
        self.assertEqual(result['player_sdk_url_equal_count'],1)
        self.assertEqual(result['state_server_prop_src_equal_count'],1)
        self.assertEqual(result['state_server_resource_id_equal_count'],1)
        self.assertEqual(result['state_blob_src_equal_count'],1)
        self.assertEqual(result['fully_bound_blob_video_count'],1)

    async def test_same_id_in_unrelated_vnode_branch_does_not_bind_video(self):
        await self.owner_server_source_fixture()
        await self.js('''()=>{
            const f=ownerFixture,siblingNode={type:{}},other=document.createElement('div');f.root.append(other);
            siblingNode.component={vnode:siblingNode,props:{state:f.state},appContext:{app:f.app},isUnmounted:false,
                subTree:{type:'div',el:other,children:[]}};
            f.preview.subTree.children.push(siblingNode);f.preview.props={};
        }''')
        result=await self.owner_projection()
        self.assertEqual(result['source_consumer_count'],1)
        self.assertEqual(result['state_component_count'],0);self.assertEqual(result['fully_bound_blob_video_count'],0)

    async def test_server_handoff_rejects_stale_or_unmatched_video_state(self):
        cases=(
            "ownerFixture.state.video.value.fileid='foreign'",
            "ownerFixture.state.title.value='stale'",
            "ownerFixture.state.desc.value='stale'",
            "ownerFixture.state.videoBlobUrl.value='blob:https://fixture.test/foreign'",
            "ownerFixture.player.exposed.video.value=document.createElement('video')",
            "ownerFixture.player.appContext.app={}",
            "ownerFixture.sdk.config.url='https://fixture.test/foreign'",
            "ownerFixture.sdk.options.url='https://fixture.test/foreign'",
            "ownerFixture.player.props.src='https://fixture.test/foreign';ownerFixture.playerNode.props.src='https://fixture.test/foreign'",
            "ownerFixture.player.props.src='https://fixture.test/video-resource-extra';ownerFixture.playerNode.props.src='https://fixture.test/video-resource-extra';ownerFixture.sdk.config.url='https://fixture.test/video-resource-extra';ownerFixture.sdk.options.url='https://fixture.test/video-resource-extra'",
            "ownerFixture.video.src=URL.createObjectURL(new Blob(['different']))",
        )
        for change in cases:
            with self.subTest(change=change):
                await self.owner_server_source_fixture()
                await self.js('()=>{'+change+';}')
                result=await self.owner_projection()
                self.assertEqual(result['fully_bound_blob_video_count'],0)

    async def test_blob_id_consumer_url_app_and_unique_vnode_are_all_required(self):
        for change in ("ownerFixture.state.video.value.fileid='foreign'",
                       "ownerFixture.state.videoBlobUrl.value='blob:https://fixture.test/foreign'",
                       "ownerFixture.player.props.src='blob:https://fixture.test/foreign'",
                       "ownerFixture.player.appContext.app={}",
                       "ownerFixture.player.exposed.video.value=document.createElement('video')",
                       "ownerFixture.playerNode.type.__scopeId='foreign-scope'",
                       "ownerFixture.preview.subTree.children.push({type:'video',el:ownerFixture.video,children:null})"):
            with self.subTest(change=change):
                await self.owner_fixture();await self.js('()=>{'+change+';}')
                self.assertEqual((await self.owner_projection())['fully_bound_blob_video_count'],0)

    async def test_mounted_container_shape_is_diagnostic_and_unknown_container_getter_not_called(self):
        await self.owner_fixture()
        result=await self.owner_projection()
        self.assertFalse(result['publisher_container_owned']);self.assertTrue(result['publisher_contains_container'])
        self.assertTrue(result['container_parent_is_publisher']);self.assertTrue(result['container_vue_app_equal'])
        self.assertTrue(result['root_vnode_present']);self.assertFalse(result['app_instance_present'])
        self.assertTrue(result['container_scope_valid']);self.assertEqual(result['source_consumer_count'],1)
        await self.js("()=>{Object.defineProperty(ownerFixture.app,'_container',{get(){fixture.business++;throw Error('unknown_getter')}});}")
        result=await self.owner_projection()
        self.assertTrue(result['container_getter_property']);self.assertFalse(result['container_data_property'])
        self.assertEqual((await self.js('()=>fixture'))['business'],0)


class XhsExpressionShapeTests(unittest.TestCase):
    def test_invalid_ids_fields_are_rejected_without_building_expression(self):
        fields={'title':'fixture','description':'body','tags':[]}
        base={'video_ids':['video'],'cover_ids':['cover']}
        for ids in ([],['video','video'],[{}],['video\nprivate'],['x'*2049]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                xhs_readback_expression(fields,{**base,'video_ids':ids})
        with self.assertRaises(ValueError):xhs_readback_expression({**fields,'tags':'topic'},base)


if __name__=='__main__':unittest.main()
