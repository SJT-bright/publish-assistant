"""Isolated tests for the read-only Douyin visible cover-card contract."""
import base64
import struct
import unittest
import zlib

from patchright.async_api import async_playwright

from uploader.dy_cover_readback import read_dy_visible_cover_cards


ROOT_URL = 'https://creator.douyin.com/creator-micro/content/post/video?enter_from=draft'


def _png_chunk(kind, data):
    body = kind + data
    return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)


TINY_PNG = (b'\x89PNG\r\n\x1a\n' +
            _png_chunk(b'IHDR', struct.pack('>2I5B', 1, 1, 8, 6, 0, 0, 0)) +
            _png_chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\xff')) +
            _png_chunk(b'IEND', b''))


FIXTURE = r'''<!doctype html><meta charset="utf-8"><style>
  html,body,#root{margin:0;width:800px;height:600px} .cover-card{display:block;width:180px;height:140px}
  .cover-card img{display:block;width:100px;height:100px;object-fit:cover}
</style><div id="root"><div id="root"></div></div><script>
(() => {
 const roots=[...document.querySelectorAll('[id="root"]')],outerRoot=roots[0],root=roots[1];
 const outerFiberRoot={},outerCurrent={tag:3,stateNode:outerFiberRoot};outerFiberRoot.current=outerCurrent;
 Object.defineProperty(outerRoot,'__reactContainer$outer',{value:{tag:3,stateNode:outerFiberRoot,alternate:{tag:3,child:null}}});
 const fiberRoot={},current={tag:3,stateNode:fiberRoot};fiberRoot.current=current;
 Object.defineProperty(root,'__reactContainer$fixture',{value:{tag:3,stateNode:fiberRoot,alternate:{tag:3,child:null}}});
 const cards=[];
 function addImage(card,src){
   const img=document.createElement('img');img.src=src;card.element.append(img);
   const host={tag:5,stateNode:img,memoizedProps:{src}};
   const component={tag:0,memoizedProps:{src,alt:'cover'},child:host};
   if(card.host.child){let last=card.host.child;while(last.sibling)last=last.sibling;last.sibling=component;}
   else card.host.child=component;
   card.images.push({img,host,component});return img;
 }
 function addCard(uri,src,options={}){
   const element=document.createElement('div');element.className='coverControl-fixture cover-card';root.append(element);
   const label=document.createElement('span');label.textContent=options.slot==='landscape'?'横封面4:3':'竖封面3:4';element.append(label);
   const host={tag:5,stateNode:element},props={coverInfo:{uri},disabled:false,handleEditCover(){},
      durationStr:'00:06',showOperation:true,className:'cover-card',...(options.extraProps||{})};
   if(options.omitProp)delete props[options.omitProp];
   const fiber={tag:0,memoizedProps:props,child:host},card={element,host,fiber,images:[]};
   if(cards.length)cards[cards.length-1].fiber.sibling=fiber;else current.child=fiber;
   cards.push(card);addImage(card,src);
   for(const imageSrc of options.extraImages||[])addImage(card,imageSrc);
   return card;
 }
 window.fixture={cards,current,fiberRoot,outerFiberRoot,root,outerRoot,addCard,addImage};
 addCard('tos://cover-one','https://fixture.invalid/cover-one.png');
})();
</script>'''


class DYCoverReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, channel='chromium')
        self.context = await self.browser.new_context()

        async def fulfill_locally(route):
            if route.request.resource_type == 'image':
                await route.fulfill(status=200, content_type='image/png', body=TINY_PNG)
            else:
                await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=FIXTURE)

        # Every top-level and subresource request is fulfilled locally.
        await self.context.route('**/*', fulfill_locally)
        self.page = await self.context.new_page()
        await self.page.goto(ROOT_URL)
        await self.page.wait_for_function("document.querySelector('img')?.complete && document.querySelector('img').naturalWidth===1")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def read(self, candidates=('cover-one',)):
        return await read_dy_visible_cover_cards(self.page, list(candidates))

    async def js(self, script):
        return await self.page.evaluate(script, isolated_context=False)

    async def test_tos_uri_matches_bare_cache_alias_and_returns_current_identity(self):
        result = await self.read(('cover-one',))
        self.assertEqual(result['source'], 'normal_visible_cover_cards')
        self.assertEqual(result['cover_count'], 1)
        self.assertEqual(result['covers'], [{
            'uri': 'tos://cover-one', 'ids': ['tos://cover-one', 'cover-one'],
            'slot': 'cover_portrait', 'image_match': True, 'image_count': 1, 'image_width': 1, 'image_height': 1,
        }])
        self.assertNotIn('fixture.invalid', str(result))

    async def test_two_distinct_portrait_and_landscape_uris_remain_distinct(self):
        await self.js("fixture.addCard('landscape-two','https://fixture.invalid/cover-two.png',{slot:'landscape'})")
        await self.page.wait_for_function("[...document.querySelectorAll('img')].every(i=>i.complete&&i.naturalWidth===1)")
        result = await self.read(('tos://cover-one', 'landscape-two'))
        self.assertEqual(result['cover_count'], 2)
        self.assertEqual([row['uri'] for row in result['covers']], ['tos://cover-one', 'landscape-two'])
        self.assertEqual([row['ids'] for row in result['covers']], [
            ['tos://cover-one', 'cover-one'], ['landscape-two', 'tos://landscape-two'],
        ])
        self.assertEqual([row['slot'] for row in result['covers']], ['cover_portrait','cover_landscape'])

    async def test_nested_semi_image_components_keep_same_src(self):
        await self.js("""const image=fixture.cards[0].images[0];
          image.component.child={tag:11,memoizedProps:{src:image.img.src},child:image.host};""")
        self.assertIsNotNone(await self.read())

    async def test_ambiguous_orientation_labels_are_rejected(self):
        await self.js("fixture.cards[0].element.insertAdjacentHTML('beforeend','<span>横封面4:3</span>')")
        self.assertIsNone(await self.read())

    async def test_duplicate_visible_cards_for_one_cache_identity_are_rejected(self):
        await self.js("fixture.addCard('cover-one','https://fixture.invalid/duplicate.png')")
        await self.page.wait_for_function("[...document.querySelectorAll('img')].length===2 && [...document.querySelectorAll('img')].every(i=>i.complete&&i.naturalWidth===1)")
        self.assertIsNone(await self.read(('tos://cover-one', 'cover-one')))

    async def test_only_exact_official_cover_card_props_and_cache_uri_are_accepted(self):
        await self.js("fixture.cards[0].fiber.memoizedProps.extra=true")
        self.assertIsNone(await self.read())
        await self.page.reload()
        await self.page.wait_for_function("document.querySelector('img')?.complete && document.querySelector('img').naturalWidth===1")
        await self.js("fixture.cards[0].fiber.memoizedProps.coverInfo.uri='https://fixture.invalid/cover-one.png'")
        self.assertIsNone(await self.read(('cover-one',)))

    async def test_current_image_component_prop_must_equal_visible_dom_src(self):
        await self.js("fixture.cards[0].images[0].component.memoizedProps.src='https://fixture.invalid/other.png'")
        self.assertIsNone(await self.read())

    async def test_card_requires_one_visible_fully_loaded_https_image(self):
        await self.js("fixture.addImage(fixture.cards[0],'https://fixture.invalid/second.png')")
        await self.page.wait_for_function("[...document.querySelectorAll('img')].every(i=>i.complete&&i.naturalWidth===1)")
        self.assertIsNone(await self.read())
        await self.page.reload()
        await self.page.wait_for_function("document.querySelector('img')?.complete && document.querySelector('img').naturalWidth===1")
        await self.js("fixture.cards[0].images[0].img.style.display='none'")
        self.assertIsNone(await self.read())

    async def test_wrong_route_duplicate_root_and_noncommitted_tree_fail_closed(self):
        await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')
        self.assertIsNone(await self.read())
        await self.page.goto(ROOT_URL)
        await self.js("document.body.insertAdjacentHTML('beforeend','<div id=\"root\"></div>')")
        self.assertIsNone(await self.read())
        await self.page.reload()
        await self.page.wait_for_function("document.querySelector('img')?.complete && document.querySelector('img').naturalWidth===1")
        await self.js("fixture.fiberRoot.current={tag:3,stateNode:fixture.fiberRoot,child:null}")
        self.assertIsNone(await self.read())

    async def test_candidate_urls_and_empty_or_malformed_candidates_are_rejected(self):
        for candidates in ((), ('https://fixture.invalid/cover-one.png',), ('tos://',), ('tos://bad id',)):
            with self.subTest(candidates=candidates):
                self.assertIsNone(await self.read(candidates))


if __name__ == '__main__':
    unittest.main()
