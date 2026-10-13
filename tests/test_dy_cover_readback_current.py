"""Synthetic current React cover-readback fixtures; all requests stay local."""
import asyncio
import json
import unittest
from pathlib import Path

from patchright.async_api import async_playwright

from uploader.dy_cover_readback import read_dy_visible_cover_cards


PAGE_URL = "https://creator.douyin.com/creator-micro/content/post/video?fixture=1"
VERTICAL_URI = "tos://fixture-cover-vertical"
HORIZONTAL_URI = "tos://fixture-cover-horizontal"
VERTICAL_URL = "https://img.fixture/vertical.svg"
HORIZONTAL_URL = "https://img.fixture/horizontal.svg"


FIXTURE = r'''<!doctype html><html><head><meta charset="utf-8"><style>
html,body,#root{margin:0;min-height:100%;font:14px sans-serif}
.coverControl-v,.coverControl-h{position:relative;display:inline-block;width:180px;height:120px;margin:8px}
.coverControl-v img,.coverControl-h img{display:block;width:100%;height:100%;object-fit:cover}
.coverControl-v span,.coverControl-h span{position:absolute;left:2px;bottom:2px;background:white;color:black}
</style></head><body><div id="root"></div><script>
(() => {
  const V_URI='tos://fixture-cover-vertical', H_URI='tos://fixture-cover-horizontal';
  const V_URL='https://img.fixture/vertical.svg', H_URL='https://img.fixture/horizontal.svg';
  const UPDATED_URL='https://img.fixture/updated.svg';
  const storeKeys=['addTransactionMetadata','getGraph','getState','replaceState','subscribeToTransactions'];
  window.fixture={calls:0};
  const called=()=>{window.fixture.calls++;};
  const makeStore=()=>Object.fromEntries(storeKeys.map(key=>[key,called]));
  const store=makeStore(),otherStore=makeStore();
  const container=document.querySelector('#root');
  const renderKey={id:'fixture-render'};
  const noop=called;

  function atom(key,contents,ownerStore,state='hasValue'){
    const effectHook={memoizedState:{deps:[renderKey,{key},{current:ownerStore}],create:noop},next:null};
    const loadable={state,contents};
    const refHook={memoizedState:{current:loadable},next:null};
    effectHook.next=refHook;
    return {effectHook,loadable,refHook};
  }
  function chain(hooks){for(let i=0;i<hooks.length-1;i++)hooks[i].next=hooks[i+1];return hooks[0]||null;}
  function card(direction,label,url){
    const control=document.createElement('div');
    control.className='coverControl-'+direction;
    const image=document.createElement('img');
    image.alt='fixture cover';image.src=url;
    const text=document.createElement('span');text.textContent=label;
    control.append(image,text);container.append(control);
    const imageProps={src:url};
    const imageHost={tag:5,stateNode:image,memoizedProps:{src:url},child:null,sibling:null};
    const imageComponent={tag:11,memoizedProps:imageProps,child:imageHost,sibling:null};
    const cardProps={canEdit:true,coverUrl:url,customStyle:{},direction,
      disabled:false,height:120,isLoading:false,onClick:noop,showSelect:false,tip:label,tooltipOptions:{}};
    const fiber={tag:0,memoizedProps:cardProps,memoizedState:{},child:imageComponent,sibling:null};
    return {fiber,props:cardProps,imageProps,image};
  }

  const vertical=card('v','竖封面3:4',V_URL),horizontal=card('h','横封面4:3',H_URL);
  const verticalInfo={uri:V_URI,url:V_URL},horizontalInfo={uri:H_URI,url:H_URL};
  const verticalUrlAtom=atom('coverUrl',V_URL,store),horizontalUrlAtom=atom('coverHorizontalUrl',H_URL,store);
  const verticalInfoAtom=atom('coverInfo',verticalInfo,store),horizontalInfoAtom=atom('coverHorizontalInfo',horizontalInfo,store);
  const widgetProps={algorithmStrategy:'fixture',coverHorizontalUri:H_URI,coverStatus:'ready',coverUri:V_URI,
    coverVideoName:'fixture',defaultUri:'',from:'publish',isKeyAuthor:false,isMainReady:true,isModify:false,
    isReedit:true,isSelect:false,longFileInfo:{name:'fixture.mp4',size:1234},onCoverEditFinish:noop,onDouyinCoverEdit:noop,
    setLog:noop,setRecommendCoverInfo:noop,videoEventParams:{}};
  const supplierProps={abTestVal:{},algorithmStrategy:'fixture',coverEdited:false,coverRequired:false,
    coverUrl:V_URL,defaultUri:'',editorsetLog:noop,from:'publish',isKeyAuthor:false,isModify:false,
    isReedit:true,longFileInfo:{name:'fixture.mp4',size:1234},onCoverEditFinish:noop,onDouyinCoverEdit:noop,
    setRecommendCoverInfo:noop};

  const verticalState={memoizedState:V_URL,queue:{lastRenderedState:V_URL,dispatch:noop},next:null};
  const horizontalState={memoizedState:H_URL,queue:{lastRenderedState:H_URL,dispatch:noop},next:null};
  const widgetHooks=[verticalUrlAtom.effectHook,verticalUrlAtom.refHook,
    horizontalUrlAtom.effectHook,horizontalUrlAtom.refHook,verticalState,horizontalState];
  const widget={tag:0,memoizedProps:widgetProps,memoizedState:chain(widgetHooks),child:vertical.fiber,sibling:null};
  vertical.fiber.sibling=horizontal.fiber;

  const supplierHooks=[verticalInfoAtom.effectHook,verticalInfoAtom.refHook,
    horizontalInfoAtom.effectHook,horizontalInfoAtom.refHook];
  const supplier={tag:0,memoizedProps:supplierProps,memoizedState:chain(supplierHooks),child:widget,sibling:null};
  const fiberRoot={containerInfo:container,current:null};
  const current={tag:3,stateNode:fiberRoot,child:supplier,sibling:null};
  fiberRoot.current=current;
  const anchor={tag:3,stateNode:fiberRoot};
  Object.defineProperty(container,'__reactContainer$fixture',{value:anchor,configurable:true});

  Object.assign(window.fixture,{store,otherStore,vertical,horizontal,verticalUrlAtom,horizontalUrlAtom,
    verticalInfoAtom,horizontalInfoAtom,widgetProps,supplierProps,widget,supplier,fiberRoot,current,
    verticalState,horizontalState});
  window.fixture.mutate=async mode=>{
    const f=window.fixture;
    switch(mode){
      case 'router_supplier':
        Object.assign(f.supplierProps,{history:{},location:{},match:{},staticContext:null});break;
      case 'url_atom_updated_card_old':
        f.verticalUrlAtom.loadable.contents=UPDATED_URL;
        f.verticalInfoAtom.loadable.contents.url=UPDATED_URL;
        f.verticalState.memoizedState=UPDATED_URL;
        f.verticalState.queue.lastRenderedState=UPDATED_URL;
        break;
      case 'image_source_old':
        f.verticalUrlAtom.loadable.contents=UPDATED_URL;
        f.verticalInfoAtom.loadable.contents.url=UPDATED_URL;
        f.verticalState.memoizedState=UPDATED_URL;
        f.verticalState.queue.lastRenderedState=UPDATED_URL;
        f.vertical.props.coverUrl=UPDATED_URL;
        break;
      case 'state_not_caught_up':
        f.verticalUrlAtom.loadable.contents=UPDATED_URL;
        f.verticalInfoAtom.loadable.contents.url=UPDATED_URL;
        f.vertical.props.coverUrl=UPDATED_URL;
        f.vertical.imageProps.src=UPDATED_URL;
        f.vertical.image.src=UPDATED_URL;
        await f.vertical.image.decode();
        break;
      case 'url_not_https':
        f.verticalUrlAtom.loadable.contents='http://img.fixture/insecure.svg';
        f.verticalInfoAtom.loadable.contents.url='http://img.fixture/insecure.svg';
        f.verticalState.memoizedState='http://img.fixture/insecure.svg';
        f.verticalState.queue.lastRenderedState='http://img.fixture/insecure.svg';
        f.vertical.props.coverUrl='http://img.fixture/insecure.svg';
        f.vertical.imageProps.src='http://img.fixture/insecure.svg';
        f.vertical.image.src='http://img.fixture/insecure.svg';
        await f.vertical.image.decode();
        break;
      case 'same_urls':
        f.horizontalUrlAtom.loadable.contents=V_URL;
        f.horizontalInfoAtom.loadable.contents.url=V_URL;
        f.horizontalState.memoizedState=V_URL;
        f.horizontalState.queue.lastRenderedState=V_URL;
        f.horizontal.props.coverUrl=V_URL;
        f.horizontal.imageProps.src=V_URL;
        f.horizontal.image.src=V_URL;
        await f.horizontal.image.decode();
        break;
      case 'info_uri_mismatch':f.verticalInfoAtom.loadable.contents.uri='tos://other-cover';break;
      case 'info_url_mismatch':f.verticalInfoAtom.loadable.contents.url='https://img.fixture/other.svg';break;
      case 'loading':f.verticalUrlAtom.loadable.state='loading';break;
      case 'error':f.horizontalInfoAtom.loadable.state='error';break;
      case 'different_store':
        f.verticalInfoAtom.effectHook.memoizedState.deps[2].current=f.otherStore;
        f.horizontalInfoAtom.effectHook.memoizedState.deps[2].current=f.otherStore;
        break;
      case 'swapped_directions':
        f.widgetProps.coverUri=H_URI;f.widgetProps.coverHorizontalUri=V_URI;break;
      case 'duplicate_vertical':
        f.duplicate={tag:0,memoizedProps:{...f.vertical.props},memoizedState:{},child:null,sibling:null};
        f.horizontal.fiber.sibling=f.duplicate;break;
      case 'stale_alternate':
        f.emptyCurrent={tag:3,stateNode:f.fiberRoot,child:null,sibling:null,alternate:f.current};
        f.current.alternate=f.emptyCurrent;f.fiberRoot.current=f.emptyCurrent;break;
      case 'wrong_supplier_schema':f.supplierProps.unexpectedField=true;break;
      case 'widget_modify_true':f.widgetProps.isModify=true;break;
      case 'supplier_modify_true':f.supplierProps.isModify=true;break;
      case 'reedit_false':
        f.widgetProps.isReedit=false;f.supplierProps.isReedit=false;break;
      case 'reedit_false_no_file':
        f.widgetProps.isReedit=false;f.supplierProps.isReedit=false;
        f.widgetProps.longFileInfo=null;f.supplierProps.longFileInfo=null;break;
      case 'reedit_false_no_file_name':
        f.widgetProps.isReedit=false;f.supplierProps.isReedit=false;
        f.widgetProps.longFileInfo={name:'没有文件',size:1234};
        f.supplierProps.longFileInfo={name:'没有文件',size:1234};break;
      case 'reedit_false_zero_size':
        f.widgetProps.isReedit=false;f.supplierProps.isReedit=false;
        f.widgetProps.longFileInfo={name:'fixture.mp4',size:0};
        f.supplierProps.longFileInfo={name:'fixture.mp4',size:0};break;
      case 'transition_uri_spelling':
        f.widgetProps.coverUri='fixture-cover-vertical';
        f.verticalInfoAtom.loadable.contents.uri='fixture-cover-vertical';
        break;
      default:throw new Error('unknown fixture mode');
    }
  };
})();
</script></body></html>'''


class DYCoverReadbackCurrentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.playwright = await async_playwright().start()
        shell = Path.home() / 'Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell'
        self.browser = await self.playwright.chromium.launch(
            headless=True, executable_path=str(shell) if shell.is_file() else None, timeout=30000)
        self.context = await self.browser.new_context(ignore_https_errors=True)

        async def fulfill(route):
            if route.request.url.startswith('https://img.fixture/'):
                svg = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="6"><rect width="8" height="6" fill="#4a8"/></svg>'
                await route.fulfill(status=200, content_type='image/svg+xml', body=svg)
            else:
                await route.fulfill(status=200, content_type='text/html', body=FIXTURE)

        await self.context.route('**/*', fulfill)
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def read_fixture(self, mode=None):
        await self.page.goto(PAGE_URL, wait_until='load')
        if mode:
            await self.page.evaluate('fixture.mutate(' + json.dumps(mode) + ')', isolated_context=False)
        result = await read_dy_visible_cover_cards(self.page, [VERTICAL_URI, HORIZONTAL_URI])
        calls = await self.page.evaluate('fixture.calls', isolated_context=False)
        self.assertEqual(calls, 0, 'readback must not call store or UI functions')
        return result

    def assert_dual_roles(self, result):
        self.assertIsNotNone(result)
        self.assertEqual(result['source'], 'normal_visible_cover_cards')
        self.assertEqual(result['cover_count'], 2)
        self.assertEqual({cover['uri'] for cover in result['covers']}, {VERTICAL_URI, HORIZONTAL_URI})
        self.assertEqual({cover['slot'] for cover in result['covers']}, {'cover_portrait', 'cover_landscape'})
        self.assertTrue(all(cover['image_match'] and cover['image_count'] == 1 for cover in result['covers']))

    async def assert_rejected(self, mode):
        self.assertIsNone(await self.read_fixture(mode), mode)

    async def test_current_dual_role_cards_and_router_supplier_schema(self):
        self.assert_dual_roles(await self.read_fixture())
        # The independently documented supplier may also carry the four router props.
        self.assert_dual_roles(await self.read_fixture('router_supplier'))

    async def test_updated_atom_rejects_stale_card_and_stale_state_cell(self):
        await self.assert_rejected('url_atom_updated_card_old')
        await self.assert_rejected('state_not_caught_up')

    async def test_updated_card_rejects_stale_image_source(self):
        await self.assert_rejected('image_source_old')

    async def test_uri_url_and_direction_pairs_must_match(self):
        for mode in ('info_uri_mismatch', 'info_url_mismatch', 'swapped_directions'):
            with self.subTest(mode=mode):
                await self.assert_rejected(mode)

    async def test_loadables_and_recoil_store_identity_fail_closed(self):
        for mode in ('loading', 'error', 'different_store'):
            with self.subTest(mode=mode):
                await self.assert_rejected(mode)

    async def test_widget_url_state_pair_requires_https_and_distinct_values(self):
        for mode in ('url_not_https', 'same_urls'):
            with self.subTest(mode=mode):
                await self.assert_rejected(mode)

    async def test_duplicate_same_role_card_is_rejected(self):
        await self.assert_rejected('duplicate_vertical')

    async def test_only_current_committed_tree_and_exact_supplier_schema_are_used(self):
        await self.assert_rejected('stale_alternate')
        await self.assert_rejected('wrong_supplier_schema')

    async def test_publish_selector_accepts_reedit_or_real_file_only(self):
        self.assert_dual_roles(await self.read_fixture())
        # The primary 2209 selector sends Publish + isReedit=false through the
        # same cover widget when longFileInfo has a real name and positive size.
        self.assert_dual_roles(await self.read_fixture('reedit_false'))
        for mode in ('widget_modify_true', 'supplier_modify_true', 'reedit_false_no_file',
                     'reedit_false_no_file_name', 'reedit_false_zero_size'):
            with self.subTest(mode=mode):
                await self.assert_rejected(mode)

    async def test_two_committed_reads_must_have_identical_structure(self):
        from unittest.mock import patch
        import uploader.draft_evidence as draft_evidence

        await self.page.goto(PAGE_URL, wait_until='load')
        original = draft_evidence.evaluate_main
        observed = []

        async def transition_between_reads(frame, script):
            result = await original(frame, script)
            observed.append(result)
            if len(observed) == 1:
                await frame.evaluate('fixture.mutate("transition_uri_spelling")', isolated_context=False)
            return result

        with patch('uploader.draft_evidence.evaluate_main', new=transition_between_reads):
            result = await read_dy_visible_cover_cards(self.page, [VERTICAL_URI, HORIZONTAL_URI])
        self.assertEqual(len(observed), 2)
        self.assertIsNotNone(observed[0])
        self.assertIsNotNone(observed[1])
        self.assertNotEqual(observed[0], observed[1])
        self.assertIsNone(result)
        calls = await self.page.evaluate('fixture.calls', isolated_context=False)
        self.assertEqual(calls, 0)


if __name__ == '__main__':
    unittest.main()
