"""Isolated Wujie-style iframe DOM projected into the physical main shadow.

No installed App, real account, platform, material, JS .click() or zoom math.
The small ownerDocument/getRootNode/HTML-parent proxies model the public Wujie
patchElementEffect, patchNodeEffect and renderTemplateToShadowRoot behavior:
https://github.com/Tencent/wujie/blob/master/packages/wujie-core/src/iframe.ts
https://github.com/Tencent/wujie/blob/master/packages/wujie-core/src/shadow.ts
"""
import asyncio
import json
import unittest

from patchright.async_api import async_playwright


FIXTURE = r'''<!doctype html><meta charset="utf-8">
<style>body{margin:0;padding:24px}wujie-app{display:block;width:740px;height:440px}
iframe{display:none}button{padding:12px}</style>
<button id="wrong-main">确认</button><iframe id="sandbox"></iframe>
<wujie-app class="wujie_iframe"></wujie-app>
<script>
window.events=[];
document.getElementById('wrong-main').addEventListener('click', e=>events.push({id:e.currentTarget.id,trusted:e.isTrusted}));
window.makeFixture=function(proxy){
 const iframe=document.getElementById('sandbox'), child=iframe.contentWindow, doc=child.document;
 const root=document.querySelector('wujie-app').attachShadow({mode:'open'});
 const nativeRoot=child.Node.prototype.getRootNode;
 if(proxy){
  child.Node.prototype.getRootNode=function(options){const physical=nativeRoot.call(this,options);return physical===root?doc:physical;};
 }
 const html=doc.createElement('html');
 html.innerHTML='<style>.weui-desktop-dialog{margin:20px;width:650px;height:300px;border:1px solid;padding:20px}footer{margin-top:120px}button{padding:12px}</style>'+
  '<body><div class="weui-desktop-dialog"><h2>编辑分享卡片</h2><div class="cover-set-footer"><button id="exact-confirm">确认</button></div></div></body>';
 const button=html.querySelector('#exact-confirm');
 button.addEventListener('click', e=>{
  window.events.push({id:e.currentTarget.id,trusted:e.isTrusted});
  button.closest('.weui-desktop-dialog').hidden=true;
 });
 if(proxy){
  for(const node of [html,...html.querySelectorAll('*')])Object.defineProperty(node,'ownerDocument',{configurable:true,get:()=>doc});
 }
 root.appendChild(html);
 if(proxy){
  Object.defineProperty(html,'parentNode',{configurable:true,get:()=>doc});
  Object.defineProperty(doc,'elementFromPoint',{configurable:true,value:(x,y)=>root.elementFromPoint(x,y)});
 }
 window.geometry=()=>{const b=button.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;
  return {ownerDocForeign:button.ownerDocument===doc,physicalRootIsMainShadow:nativeRoot.call(button)===root,
   reportedRootType:button.getRootNode().nodeType,outerHit:document.elementFromPoint(x,y)?.tagName,
   ownerHit:button.ownerDocument.elementFromPoint(x,y)===button,shadowHit:root.elementFromPoint(x,y)===button,
   rect:{x:b.x,y:b.y,width:b.width,height:b.height}};};
};
</script>'''


class TencentCoverHitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.driver = await async_playwright().start()
        self.browser = await self.driver.chromium.launch(headless=True, channel='chrome')
        self.context = await self.browser.new_context(viewport={'width': 1100, 'height': 700})
        await self.context.route('**/*', lambda route: route.abort())
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        # Only this fixture's disposable browser is owned by this driver.
        # Bound shutdown so a slow local Chrome cannot hang the test runner.
        try:
            await asyncio.wait_for(self.context.close(), timeout=8)
        finally:
            # Stopping this launch driver closes its disposable browser. Avoid
            # a second browser.close protocol wait after context shutdown.
            await asyncio.wait_for(self.driver.stop(), timeout=8)

    async def prepare(self, proxy, *, css=False):
        await self.page.set_content(FIXTURE)
        await self.page.evaluate('proxy=>makeFixture(proxy)', proxy, isolated_context=False)
        dialogs = self.page.locator('div.weui-desktop-dialog:visible').filter(has_text='编辑分享卡片')
        self.assertEqual(await dialogs.count(), 1)
        dialog = dialogs.first
        confirm = (dialog.locator('.cover-set-footer button') if css else
                   dialog.get_by_role('button', name='确认', exact=True))
        self.assertEqual(await confirm.count(), 1)
        self.assertTrue(await confirm.is_visible())
        self.assertTrue(await confirm.is_enabled())
        geometry = await self.page.evaluate('geometry()', isolated_context=False)
        self.assertTrue(geometry['physicalRootIsMainShadow'])
        self.assertTrue(geometry['shadowHit'])
        self.assertGreater(geometry['rect']['width'], 0)
        self.assertGreater(geometry['rect']['height'], 0)
        return dialog, confirm, geometry

    async def events(self):
        return await self.page.evaluate('events', isolated_context=False)

    async def test_native_adoption_without_document_proxies_uses_normal_trusted_click(self):
        dialog, confirm, geometry = await self.prepare(False)
        self.assertFalse(geometry['ownerDocForeign'])
        await confirm.click(timeout=2000)
        self.assertEqual(await self.events(), [{'id': 'exact-confirm', 'trusted': True}])
        self.assertEqual(await dialog.count(), 0)

    async def test_projected_foreign_document_role_click_uses_physical_utility_root(self):
        dialog, confirm, geometry = await self.prepare(True)
        self.assertTrue(geometry['ownerDocForeign'])
        self.assertEqual(geometry['reportedRootType'], 9)
        self.assertEqual(geometry['outerHit'], 'WUJIE-APP')
        self.assertTrue(geometry['ownerHit'])
        utility_geometry = await confirm.evaluate('''e=>{const b=e.getBoundingClientRect();return {
          ownerSame:e.ownerDocument===document,rootType:e.getRootNode().nodeType,
          hitTag:document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.tagName};}''')
        # Main-world Wujie proxies are not shared with the utility world used
        # by normal actionability checks. Do not manufacture a hit-test failure
        # from the outer document returning its shadow host.
        self.assertTrue(utility_geometry['ownerSame'])
        self.assertEqual(utility_geometry['rootType'], 11)
        self.assertEqual(utility_geometry['hitTag'], 'WUJIE-APP')
        await confirm.click(timeout=2000)
        result = await self.events()
        print(json.dumps({'geometry': geometry, 'utility_geometry': utility_geometry,
                          'normal_role_events': result}, ensure_ascii=False), flush=True)
        self.assertEqual(result, [{'id': 'exact-confirm', 'trusted': True}])
        self.assertEqual(await dialog.count(), 0)

    async def test_projected_foreign_document_scoped_css_click_is_trusted_and_closes_modal(self):
        dialog, confirm, _ = await self.prepare(True, css=True)
        await confirm.click(timeout=2000)
        self.assertEqual(await self.events(), [{'id': 'exact-confirm', 'trusted': True}])
        self.assertEqual(await dialog.count(), 0)

    async def test_projected_foreign_document_guarded_force_click_is_trusted_and_scoped(self):
        dialog, confirm, _ = await self.prepare(True)
        # prepare checks a unique visible, enabled confirmation and a positive
        # physical shadow-root hit. This proves only the isolated click path;
        # it does not prove a forced click is needed on the real platform.
        await confirm.click(force=True, timeout=2000)
        self.assertEqual(await self.events(), [{'id': 'exact-confirm', 'trusted': True}])
        self.assertEqual(await dialog.count(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
