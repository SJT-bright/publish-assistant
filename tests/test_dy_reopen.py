"""Isolated visible-component readback; no platform request or account."""
import unittest
from patchright.async_api import async_playwright
from uploader.dy_reopen import read_dy_visible_video
from uploader.draft_evidence import parse_record


FIXTURE = r'''<body><div id="root"><video src="https://fixture.invalid/signed-preview.mp4"></video></div>
<script>
const video=document.querySelector('video');video.style='width:320px;height:180px';
for(const [key,value] of Object.entries({currentSrc:video.src,readyState:4,videoWidth:320,videoHeight:180,duration:6}))
 Object.defineProperty(video,key,{value,writable:true});
const fiberRoot={containerInfo:document.querySelector('#root')},host={tag:5,stateNode:video},player={tag:0,child:host,
 memoizedProps:{currentVideo:{vid:'current-vid'},width:320,height:180,playUrl:video.src}};
const current={tag:3,stateNode:fiberRoot,child:player};fiberRoot.current=current;
Object.defineProperty(document.querySelector('#root'),'__reactContainer$fixture',
 {value:{tag:3,stateNode:fiberRoot,alternate:{tag:3,child:{tag:0}}}});
window.fixture={video,player,host,current,fiberRoot};
</script></body>'''


class DYReopenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, channel='chromium')
        self.page = await self.browser.new_page()
        await self.page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=FIXTURE))
        await self.page.goto('https://creator.douyin.com/creator-micro/content/post/video?enter_from=draft')

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def read(self):
        return await read_dy_visible_video(self.page, ['current-vid'])

    async def test_current_component_binds_signed_url_without_id(self):
        self.assertEqual((await self.read())['video_ids'], ['current-vid'])

    async def test_wrong_id_url_or_unloaded_preview_are_rejected(self):
        for mutation in ("fixture.player.memoizedProps.currentVideo.vid='wrong'",
                         "fixture.player.memoizedProps.playUrl='https://fixture.invalid/other.mp4'",
                         "fixture.video.readyState=1"):
            await self.page.reload()
            await self.page.evaluate(mutation, isolated_context=False)
            self.assertIsNone(await self.read())

    async def test_only_committed_tree_is_accepted(self):
        await self.page.evaluate('fixture.fiberRoot.current={tag:3,stateNode:fixture.fiberRoot,child:null}', isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_editor_nested_root_is_distinct_from_creator_shell(self):
        await self.page.evaluate('''() => {
          const editor=document.querySelector('#root'),shell=document.createElement('div');shell.id='root';
          editor.before(shell);shell.append(editor);const root={containerInfo:shell};
          root.current={tag:3,stateNode:root,child:{tag:5,stateNode:editor}};
          shell.__reactContainer$header={tag:3,stateNode:root};
        }''', isolated_context=False)
        self.assertEqual((await self.read())['video_ids'], ['current-vid'])

    async def test_multiple_visible_previews_or_duplicate_player_contract_fail(self):
        await self.page.evaluate('fixture.video.after(fixture.video.cloneNode())', isolated_context=False)
        self.assertIsNone(await self.read())
        await self.page.reload()
        await self.page.evaluate('fixture.current.child={tag:0,memoizedProps:fixture.player.memoizedProps,child:fixture.player}', isolated_context=False)
        self.assertIsNone(await self.read())

    async def test_wrong_component_shape_or_route_is_rejected(self):
        await self.page.evaluate('fixture.player.memoizedProps.other=true', isolated_context=False)
        self.assertIsNone(await self.read())
        await self.page.goto('https://creator.douyin.com/unsupported')
        self.assertIsNone(await self.read())


class DYCoverIdentityTests(unittest.TestCase):
    def test_signed_display_url_changes_do_not_change_stored_uri_identity(self):
        raw = {'cache': {'longFileInfo': {'vid': 'current-vid'},
                        'coverInfo': {'uri': 'tos://current-cover', 'url': 'https://fixture.invalid/a?signature=first'}}}
        first = parse_record('douyin', raw, 'fixture-id', 'fixture-owner')
        raw['cache']['coverInfo']['url'] = 'https://fixture.invalid/a?signature=second'
        second = parse_record('douyin', raw, 'fixture-id', 'fixture-owner')
        self.assertEqual(first['cover_ids'], ['tos://current-cover'])
        self.assertEqual(first['cover_slots'], second['cover_slots'])
