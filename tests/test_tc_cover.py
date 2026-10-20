"""Current TC EditCoverDialog fixtures; no real accounts, video or upload.

Run only these meaningful cover regressions:
  .venv/bin/python -m unittest tests.test_tc_cover -v
"""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from patchright.async_api import async_playwright

from uploader.tencent_uploader.main import TencentVideo


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aOuoAAAAASUVORK5CYII=')


def cover_fixture(mode='auto', crop=False, opened='', duplicate=False):
    dialogs = []
    for orientation, title in [('landscape', '编辑分享卡片'), ('portrait', '编辑个人主页卡片')]:
        extra = '<button onclick="record(\'wrong-extra-confirm\')">确认</button>' if duplicate and orientation == opened else ''
        dialogs.append(f'''<div class="weui-desktop-dialog" id="{orientation}"
          data-orientation="{orientation}" style="display:{'block' if orientation == opened else 'none'}">
          <h2>{title}</h2>
          <div class="single-cover-uploader-wrap"><input type="file" accept="image/png" onchange="fixtureUpload(this)">
            <div class="finish-wrap" style="display:none"><img width="160" height="120"></div></div>
          <div class="setting-cover-mask" style="display:none">图片处理中</div>
          <div class="weui-desktop-dialog__ft"></div>
          <div class="cover-set-footer"><button onclick="fixtureConfirm(this)">确认</button>{extra}</div>
        </div>''')
    return '''<!doctype html><meta charset="utf-8"><style>
      body{font:16px sans-serif}.weui-desktop-dialog{border:1px solid #aaa;padding:16px;margin:16px;width:440px}
      input,button{margin:8px}.setting-cover-mask{background:#eee}.finish-wrap{width:160px;height:120px}
      </style>
      <input type="file" id="wrong-file" accept="image/png" onchange="record('wrong-file')">
      <button id="wrong-confirm" onclick="record('wrong-confirm')">确认</button>
      <button onclick="record('wrong-crop-confirm')">确定裁剪</button>
      <div class="horizontal-cover-wrap" onclick="openCover('landscape')">分享卡片 4:3<div class="edit-btn">编辑</div></div>
      <div class="vertical-cover-wrap" onclick="openCover('portrait')">个人主页卡片 3:4<div class="edit-btn edit-btn-zIndex">编辑</div></div>
      ''' + ''.join(dialogs) + '''
      <div class="weui-desktop-dialog" id="crop" style="display:none"><h2>裁剪封面图</h2>
        <div class="weui-desktop-dialog__ft"></div>
        <div class="crop-set-footer"><button onclick="cropDone()">确定</button></div>
      </div>
      <button id="preview-ready" onclick="previewReady()">完成预览</button>
      <button id="mask-ready" onclick="finishMask(window.pendingDialog)">完成图片处理</button>
      <button id="cropper-ready" onclick="cropperReady(window.pendingDialog)">裁剪器完成</button>
      <script>
      window.fixtureMode=__MODE__;window.fixtureCrop=__CROP__;window.pendingDialog=null;
      function record(type,data={}){const events=JSON.parse(document.body.dataset.events||'[]');
        events.push({type,...data});document.body.dataset.events=JSON.stringify(events);}
      function openCover(orientation){record('open',{orientation});document.getElementById(orientation).style.display='block';}
      function showPreview(dialog){dialog.querySelector('.finish-wrap img').src=dialog.dataset.customSrc;
        dialog.querySelector('.finish-wrap').style.display='block';record('preview',{orientation:dialog.dataset.orientation});}
      function finishMask(dialog){dialog.querySelector('.setting-cover-mask').style.display='none';record('mask-hidden',{orientation:dialog.dataset.orientation});}
      for(const dialog of document.querySelectorAll('[data-orientation]')) {
        const crop={isCroppieReady:false,imageUrl:'',lastImageUrl:''};
        dialog.__vue__={handleChooseCover(){},$refs:{cropRef:crop},useCustomCover:false,
          isSettingCover:false,isCroppieLoading:true,customCoverPreviewUrl:'',currentCoverImageUrl:'',customCover:null};
      }
      function cropperReady(dialog){const v=dialog.__vue__,url=dialog.dataset.customSrc;
        v.useCustomCover=true;v.customCover={width:1,height:1};v.customCoverPreviewUrl=url;v.currentCoverImageUrl=url;
        Object.assign(v.$refs.cropRef,{isCroppieReady:true,imageUrl:url,lastImageUrl:url});
        v.isCroppieLoading=false;record('cropper-ready',{orientation:dialog.dataset.orientation});}
      function completeImage(dialog){showPreview(dialog);finishMask(dialog);cropperReady(dialog);}
      function previewReady(){const dialog=window.pendingDialog;
        dialog.querySelector('.setting-cover-mask').style.display='block';
        record('mask-active',{orientation:dialog.dataset.orientation});showPreview(dialog);}
      function fixtureUpload(input){const dialog=input.closest('.weui-desktop-dialog');
        dialog.dataset.file=input.files[0].name;window.pendingDialog=dialog;
        dialog.querySelector('.setting-cover-mask').style.display='block';
        record('file',{orientation:dialog.dataset.orientation,name:input.files[0].name});
        const reader=new FileReader();reader.onload=()=>{dialog.dataset.customSrc=reader.result;
          record('reader-ready',{orientation:dialog.dataset.orientation});
          if(window.fixtureMode==='auto'&&(!window.fixtureCrop||dialog.dataset.cropped==='yes'))completeImage(dialog);};
        reader.readAsDataURL(input.files[0]);
        if(window.fixtureCrop)document.getElementById('crop').style.display='block';}
      function cropDone(){record('crop-confirm',{orientation:window.pendingDialog.dataset.orientation});
        window.pendingDialog.dataset.cropped='yes';document.getElementById('crop').style.display='none';
        if(window.pendingDialog.dataset.customSrc)completeImage(window.pendingDialog);}
      function fixtureConfirm(button){const dialog=button.closest('.weui-desktop-dialog');
        const image=dialog.querySelector('.finish-wrap img');record('confirm',{orientation:dialog.dataset.orientation,
          name:dialog.dataset.file,customSrc:image.getAttribute('src'),
          previewShown:dialog.querySelector('.finish-wrap').style.display!=='none',
          maskShown:dialog.querySelector('.setting-cover-mask').style.display!=='none'});
        dialog.style.display='none';}
      </script>'''.replace('__MODE__', json.dumps(mode)).replace('__CROP__', 'true' if crop else 'false')


def recommendation_fixture(mode='single', modal_delay=400):
    """Official CoverPreview shapes; the recommended source reverses the target.

    Main/profile uses recommend-img-horizontal and openEditDialogDirectly(false).
    Share uses recommend-img-vertical and openEditDialogDirectly(true). Popovers
    are children of cover-preview-wrap via the official getPopupContainer.
    """
    original = '''<div class="horizontal-cover-wrap" onclick="openCover('landscape')">分享卡片 4:3<div class="edit-btn">编辑</div></div>
      <div class="vertical-cover-wrap" onclick="openCover('portrait')">个人主页卡片 3:4<div class="edit-btn edit-btn-zIndex">编辑</div></div>'''
    cards = '''<div class="cover-preview-wrap">
      <div class="horizon-cover-wrap" onclick="recommendCover('landscape',event)">分享卡片 4:3<div class="edit-btn">编辑</div></div>
      <div class="vertical-cover-wrap" onclick="recommendCover('portrait',event)">个人主页卡片 3:4<div class="edit-btn edit-btn-zIndex">编辑</div></div>
      <div id="recommend-portrait" class="img-recommend-wrap" data-target="portrait" style="display:none">
        <div class="title">使用此素材作为封面？</div><img class="recommend-img-horizontal">
        <div class="btn-wrap"><button class="btn-directly-edit" onclick="directCover('portrait',event)">直接编辑</button>
          <button class="btn-use" onclick="record('wrong-use',{orientation:'portrait'})">使用素材</button></div></div>
      <div id="recommend-landscape" class="img-recommend-wrap" data-target="landscape" style="display:none">
        <div class="title">使用此素材作为封面？</div><img class="recommend-img-vertical">
        <div class="btn-wrap"><button class="btn-directly-edit" onclick="directCover('landscape',event)">直接编辑</button>
          <button class="btn-use" onclick="record('wrong-use',{orientation:'landscape'})">使用素材</button></div></div>
      </div>'''
    html = cover_fixture()
    if original not in html:
        raise AssertionError('Cover fixture card shape changed')
    script = '''<script>
      window.recommendMode=__RECOMMEND_MODE__;window.modalDelay=__MODAL_DELAY__;
      function recommendCover(orientation,event){
        record('slot',{orientation,trusted:event.isTrusted});
        const popup=document.getElementById('recommend-'+orientation);popup.style.display='block';
        record('recommend',{orientation});
        if(window.recommendMode==='duplicate'){
          const copy=popup.cloneNode(true);copy.id='recommend-copy';popup.parentElement.append(copy);
        } else if(window.recommendMode==='both'){
          document.getElementById('recommend-'+(orientation==='portrait'?'landscape':'portrait')).style.display='block';
        }
      }
      function directCover(orientation,event){
        record('direct',{orientation,trusted:event.isTrusted});
        for(const popup of document.querySelectorAll('.img-recommend-wrap'))popup.style.display='none';
        setTimeout(()=>openCover(orientation),window.modalDelay);
      }
      </script>'''.replace('__RECOMMEND_MODE__', json.dumps(mode)).replace('__MODAL_DELAY__', str(modal_delay))
    return html.replace(original, cards) + script


class TencentCurrentCoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sau-tc-cover-')
        self.landscape = Path(self.temp.name)/'landscape.png'
        self.portrait = Path(self.temp.name)/'portrait.png'
        self.landscape.write_bytes(PNG)
        self.portrait.write_bytes(PNG)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel='chrome')
        self.context = await self.browser.new_context()
        # No fixture can accidentally reach an external URL.
        await self.context.route('**/*', lambda route: route.abort())
        self.page = await self.context.new_page()
        # Cover tests isolate modal selection/normal input. Trusted CDN capture
        # is mocked explicitly; its actual origin/owner/configuration gate is
        # independently tested by tests.test_tc_cdn, including a real browser.
        self.page._sau_draft_evidence = SimpleNamespace(
            capture_tc_cdn_snapshot=AsyncMock(return_value=True))
        self.uploader = TencentVideo('本机测试', 'unused.mp4', [], 0, 'unused.json',
            thumbnail_landscape_path=str(self.landscape), thumbnail_portrait_path=str(self.portrait))
        self.uploader.draft_only = True

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.temp.cleanup()

    async def events(self, scope=None):
        return await (scope or self.page).evaluate("JSON.parse(document.body.dataset.events||'[]')")

    async def test_current_cover_footer_confirms_when_legacy_footer_has_no_buttons(self):
        await self.page.set_content(cover_fixture(opened='portrait'))
        dialog = self.page.locator('#portrait')
        self.assertEqual(await dialog.locator('.weui-desktop-dialog__ft button').count(), 0)
        self.assertEqual(await dialog.locator('.cover-set-footer button').count(), 1)
        await self.uploader.upload_thumbnail_in_dialog(self.page, dialog, str(self.portrait))
        events = await self.events()
        confirmations = [event for event in events if event['type'] == 'confirm']
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(confirmations[0]['name'], self.portrait.name)
        self.assertTrue(confirmations[0]['customSrc'].startswith('data:image/png;base64,'))
        self.assertTrue(confirmations[0]['previewShown'])
        self.assertFalse(confirmations[0]['maskShown'])
        self.assertFalse(any(event['type'].startswith('wrong') for event in events))
        self.assertFalse(await dialog.is_visible())

    async def test_existing_requested_landscape_modal_reused_without_clicking_thumbnail_behind_overlay(self):
        await self.page.set_content(cover_fixture(opened='landscape'))
        # A real full-screen modal overlay makes the thumbnail underneath
        # unclickable. Reusing the exact requested modal must not open it again.
        await self.page.add_style_tag(content='#landscape{position:fixed;inset:0;width:auto;margin:0;z-index:100;background:white}')
        self.uploader.thumbnail_portrait_path = None
        await self.uploader.set_thumbnail(self.page)
        events = await self.events()
        self.assertEqual([e for e in events if e['type'] == 'open'], [])
        self.assertEqual([(e['orientation'], e['name']) for e in events if e['type'] == 'file'],
                         [('landscape', self.landscape.name)])
        self.assertEqual([e['orientation'] for e in events if e['type'] == 'confirm'], ['landscape'])
        self.assertFalse(any(e['type'].startswith('wrong') for e in events))
        self.assertFalse(await self.page.locator('#landscape').is_visible())
        self.page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()

    async def test_portrait_entry_waits_for_delayed_exact_modal_before_image_import(self):
        await self.page.set_content(cover_fixture())
        await self.page.evaluate('''()=>{
            window.openCover=orientation=>{
                record('open-requested',{orientation,at:performance.now()});
                // Vue mounts the requested EditCoverDialog asynchronously
                // after its normal entry click; no matching modal exists yet.
                setTimeout(()=>{
                    record('open',{orientation,at:performance.now()});
                    document.getElementById(orientation).style.display='block';
                },300);
            };
        }''', isolated_context=False)
        self.uploader.thumbnail_landscape_path=None
        self.assertFalse(await self.page.locator('#portrait').is_visible())
        await self.uploader.set_thumbnail(self.page)
        events=await self.events()
        requested=[e for e in events if e['type']=='open-requested']
        opened=[e for e in events if e['type']=='open']
        self.assertEqual([e['orientation'] for e in requested], ['portrait'])
        self.assertEqual([e['orientation'] for e in opened], ['portrait'])
        self.assertGreaterEqual(opened[0]['at']-requested[0]['at'], 250)
        self.assertEqual([(e['orientation'],e['name']) for e in events if e['type']=='file'],
                         [('portrait',self.portrait.name)])
        self.assertEqual([e['orientation'] for e in events if e['type']=='confirm'], ['portrait'])
        types=[e['type'] for e in events]
        self.assertLess(types.index('open-requested'),types.index('open'))
        self.assertLess(types.index('open'),types.index('file'))
        self.assertFalse(any(e['type'].startswith('wrong') for e in events))
        self.assertFalse(await self.page.locator('#portrait').is_visible())
        self.assertFalse(await self.page.locator('#landscape').is_visible())
        self.page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()

    async def test_multiple_matching_existing_modals_stop_before_any_image_import(self):
        await self.page.set_content(cover_fixture(opened='landscape'))
        await self.page.evaluate('''()=>{
            const copy=document.querySelector('#landscape').cloneNode(true);
            copy.id='landscape-copy';document.body.append(copy);
        }''', isolated_context=False)
        self.uploader.thumbnail_portrait_path = None
        with self.assertRaisesRegex(RuntimeError, '多个封面编辑器'):
            await self.uploader.set_thumbnail(self.page)
        events = await self.events()
        self.assertFalse(any(e['type'] in ('open', 'file', 'confirm') or e['type'].startswith('wrong') for e in events))
        self.assertEqual(await self.page.locator('input[type="file"]').evaluate_all(
            'inputs=>inputs.reduce((count,input)=>count+input.files.length,0)'), 0)
        self.assertTrue(await self.page.locator('#landscape').is_visible())
        self.assertTrue(await self.page.locator('#landscape-copy').is_visible())
        self.page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()

    async def test_visible_parent_with_hidden_nested_crop_confirms_only_its_own_editor(self):
        await self.page.set_content(cover_fixture(opened='landscape'))
        await self.page.evaluate('''()=>{
            const parent=document.querySelector('#landscape'),crop=document.querySelector('#crop');
            for (const dialog of [parent,crop]) {
                const heading=dialog.querySelector(':scope > h2');
                const header=document.createElement('div');header.className='weui-desktop-dialog__hd';
                const title=document.createElement('span');title.className='weui-desktop-dialog__title';
                title.textContent=heading.textContent;header.append(title);heading.replaceWith(header);
            }
            // Current CommonDialog keeps its hidden CropCoverDialog nested in
            // the visible EditCoverDialog. Descendant text matches both titles.
            crop.querySelector('button').onclick=()=>record('wrong-hidden-crop-confirm');
            parent.append(crop);
        }''', isolated_context=False)
        parent=self.page.locator('#landscape')
        self.assertEqual(await self.page.locator('div.weui-desktop-dialog:visible').filter(
            has_text='裁剪封面图').count(), 1)
        self.assertFalse(await parent.locator('#crop').is_visible())
        self.assertEqual(await parent.locator('#crop button').count(), 1)
        self.uploader.thumbnail_portrait_path=None
        await self.uploader.set_thumbnail(self.page)
        events=await self.events()
        self.assertEqual([(e['orientation'],e['name']) for e in events if e['type']=='file'],
                         [('landscape',self.landscape.name)])
        self.assertEqual([e['orientation'] for e in events if e['type']=='confirm'], ['landscape'])
        self.assertFalse(any(e['type']=='crop-confirm' or e['type'].startswith('wrong') for e in events))
        self.assertFalse(await parent.is_visible())
        self.assertFalse(await self.page.locator('#crop').is_visible())
        self.page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()

    async def test_waits_for_custom_image_and_then_for_processing_mask_before_confirm(self):
        await self.page.set_content(cover_fixture(mode='controlled', opened='portrait'))
        # The other cases use the real safety gate. Skip that unrelated scan
        # here so a slow risk check cannot masquerade as an image/mask wait.
        with patch('uploader.draft_only.assert_draft_safe', AsyncMock()):
            task = asyncio.create_task(self.uploader.upload_thumbnail_in_dialog(
                self.page, self.page.locator('#portrait'), str(self.portrait)))
            try:
                await self.page.wait_for_function("document.querySelector('#portrait').dataset.customSrc")
                # Hide the mask first: it cannot conceal a missing preview wait.
                await self.page.locator('#mask-ready').click()
                self.assertFalse(await self.page.locator('#portrait .finish-wrap img[src]').is_visible())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=.5)
                self.assertFalse(any(event['type'] == 'confirm' for event in await self.events()))
                # This fixture action mounts the preview and restarts the mask
                # together, before the adapter can continue past the preview.
                await self.page.locator('#preview-ready').click()
                self.assertTrue(await self.page.locator('#portrait .finish-wrap img[src]').is_visible())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=.5)
                self.assertFalse(any(event['type'] == 'confirm' for event in await self.events()))
                await self.page.locator('#mask-ready').click()
                # Official visible/enabled Confirm silently returns while the
                # cropper is still binding, independently of image/mask ready.
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=.5)
                self.assertFalse(any(event['type'] == 'confirm' for event in await self.events()))
                await self.page.locator('#cropper-ready').click()
                await asyncio.wait_for(task, timeout=5)
                events = await self.events()
                types = [event['type'] for event in events]
                self.assertEqual(types.count('file'), 1)
                self.assertEqual(types.count('confirm'), 1)
                final_mask = max(i for i, event in enumerate(events) if event['type'] == 'mask-hidden')
                self.assertLess(types.index('preview'), final_mask)
                self.assertLess(final_mask, types.index('confirm'))
                self.assertTrue(events[-1]['previewShown'])
                self.assertFalse(events[-1]['maskShown'])
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_correct_landscape_and_portrait_modals_in_child_frame_with_current_crop_footer(self):
        child = cover_fixture(crop=True)
        async def route(request):
            body = child if request.request.url.endswith('/editor-frame') else '''<input type="file" id="parent-wrong-file" accept="image/png"
              onchange="document.body.dataset.wrongFile='yes'"><button id="parent-wrong-confirm" onclick="document.body.dataset.wrongConfirm='yes'">确认</button>
              <iframe src="https://fixture.invalid/editor-frame" width="800" height="1000"></iframe>'''
            await request.fulfill(content_type='text/html', body=body)
        await self.context.unroute('**/*')
        await self.context.route('**/*', route)
        await self.page.goto('https://fixture.invalid/creator')
        frame = self.page.frames[1]
        await frame.locator('.horizontal-cover-wrap').wait_for()
        await self.uploader.set_thumbnail(self.page)
        events = await self.events(frame)
        files = [(event['orientation'], event['name']) for event in events if event['type'] == 'file']
        self.assertEqual(files, [('landscape', self.landscape.name), ('portrait', self.portrait.name)])
        self.assertEqual([event['orientation'] for event in events if event['type'] == 'open'], ['landscape', 'portrait'])
        self.assertEqual([event['orientation'] for event in events if event['type'] == 'crop-confirm'], ['landscape', 'portrait'])
        self.assertEqual([event['orientation'] for event in events if event['type'] == 'confirm'], ['landscape', 'portrait'])
        self.assertFalse(any(event['type'].startswith('wrong') for event in events))
        self.assertIsNone(await self.page.locator('body').get_attribute('data-wrong-file'))
        self.assertIsNone(await self.page.locator('body').get_attribute('data-wrong-confirm'))
        for orientation in ['landscape', 'portrait']:
            self.assertFalse(await frame.locator('#'+orientation).is_visible())
        self.assertFalse(await frame.locator('#crop').is_visible())

    async def test_ambiguous_current_confirm_buttons_stop_without_guessing(self):
        await self.page.set_content(cover_fixture(opened='portrait', duplicate=True))
        with self.assertRaisesRegex(RuntimeError, '确认按钮不唯一'):
            await self.uploader.upload_thumbnail_in_dialog(self.page, self.page.locator('#portrait'), str(self.portrait))
        events = await self.events()
        self.assertEqual(sum(event['type'] == 'file' for event in events), 1)
        self.assertFalse(any(event['type'] == 'confirm' or event['type'].startswith('wrong') for event in events))
        self.assertTrue(await self.page.locator('#portrait').is_visible())


class TencentRecommendationPopoverTests(unittest.IsolatedAsyncioTestCase):
    # Reuse browser/media setup, but do not inherit and rerun unrelated tests.
    asyncSetUp = TencentCurrentCoverTests.asyncSetUp
    asyncTearDown = TencentCurrentCoverTests.asyncTearDown
    events = TencentCurrentCoverTests.events

    @staticmethod
    def ready_state(**overrides):
        # No caption is filled in this modal fixture. can_save_draft=False
        # must not conceal readiness for independently editing its covers.
        return dict(recognized=True, count=1, video_count=1, done_count=1,
                    all_uploaded=True, all_cover_cropped=True, can_save_draft=False) | overrides

    async def open_requested(self, orientation):
        selector = '.cover-preview-wrap .' + ('vertical-cover-wrap' if orientation == 'portrait' else 'horizon-cover-wrap')
        title = '编辑个人主页卡片' if orientation == 'portrait' else '编辑分享卡片'
        return await self.uploader.open_thumbnail_dialog(self.page, [selector], [title])

    async def assert_successful_direct_editor(self, orientation):
        await self.page.set_content(recommendation_fixture(modal_delay=500))
        read = AsyncMock(return_value=self.ready_state())
        with patch('uploader.tc_editor.read_tc_material_state', read):
            dialog = await self.open_requested(orientation)
        self.assertEqual(await dialog.get_attribute('id'), orientation)
        title = '编辑个人主页卡片' if orientation == 'portrait' else '编辑分享卡片'
        self.assertEqual(await dialog.locator(':scope > h2').inner_text(), title)
        self.assertTrue(await dialog.is_visible())
        opposite = 'landscape' if orientation == 'portrait' else 'portrait'
        self.assertFalse(await self.page.locator('#' + opposite).is_visible())
        events = await self.events()
        self.assertEqual([(e['type'], e['orientation']) for e in events],
                         [('slot', orientation), ('recommend', orientation), ('direct', orientation), ('open', orientation)])
        self.assertTrue(all(e['trusted'] for e in events if e['type'] in ('slot', 'direct')))
        self.assertEqual(sum(e['type'] == 'direct' for e in events), 1)
        self.assertFalse(any(e['type'].startswith('wrong') or e['type'] in ('file', 'confirm') for e in events))
        read.assert_awaited_once_with(self.page)
        self.page._sau_draft_evidence.capture_tc_cdn_snapshot.assert_awaited_once()

    async def test_profile_recommendation_enters_profile_editor_once_without_reusing_share_cover(self):
        await self.assert_successful_direct_editor('portrait')

    async def test_share_recommendation_enters_share_editor_once_without_reusing_profile_cover(self):
        await self.assert_successful_direct_editor('landscape')

    async def test_native_edit_controls_accept_done_video_without_automatic_thumb(self):
        await self.page.set_content(recommendation_fixture())
        read=AsyncMock(return_value=self.ready_state(all_cover_cropped=False))
        with patch('uploader.tc_editor.read_tc_material_state',read):
            dialog=await self.open_requested('landscape')
        self.assertTrue(await dialog.is_visible())
        self.assertEqual([event['type'] for event in await self.events()], ['slot','recommend','direct','open'])
        read.assert_awaited_once_with(self.page)

    async def test_duplicate_requested_recommendation_and_both_directions_stop_before_direct_edit(self):
        for mode in ('duplicate', 'both'):
            for orientation in ('portrait', 'landscape'):
                with self.subTest(mode=mode, orientation=orientation):
                    await self.page.set_content(recommendation_fixture(mode=mode))
                    read = AsyncMock(return_value=self.ready_state())
                    with patch('uploader.tc_editor.read_tc_material_state', read):
                        with self.assertRaisesRegex(RuntimeError, '封面推荐窗口不唯一'):
                            await self.open_requested(orientation)
                    events = await self.events()
                    self.assertEqual([(e['type'], e['orientation']) for e in events],
                                     [('slot', orientation), ('recommend', orientation)])
                    self.assertFalse(any(e['type'] in ('direct', 'open', 'file', 'confirm') or e['type'].startswith('wrong') for e in events))
                    self.assertFalse(await self.page.locator('#portrait').is_visible())
                    self.assertFalse(await self.page.locator('#landscape').is_visible())
                    read.assert_not_awaited()

    async def test_pending_or_unrecognized_material_stops_before_direct_edit_for_each_direction(self):
        states = [self.ready_state(recognized=False), self.ready_state(done_count=0, all_uploaded=False),
                  self.ready_state(count=2, video_count=2, done_count=2)]
        for state in states:
            for orientation in ('portrait', 'landscape'):
                with self.subTest(state=state, orientation=orientation):
                    await self.page.set_content(recommendation_fixture())
                    read = AsyncMock(return_value=state)
                    with patch('uploader.tc_editor.read_tc_material_state', read):
                        with self.assertRaisesRegex(RuntimeError, '素材尚未完成处理'):
                            await self.open_requested(orientation)
                    events = await self.events()
                    self.assertEqual([(e['type'], e['orientation']) for e in events],
                                     [('slot', orientation), ('recommend', orientation)])
                    self.assertFalse(any(e['type'] in ('direct', 'open', 'file', 'confirm') or e['type'].startswith('wrong') for e in events))
                    self.assertFalse(await self.page.locator('#portrait').is_visible())
                    self.assertFalse(await self.page.locator('#landscape').is_visible())
                    read.assert_awaited_once_with(self.page)


class TencentCoverReadinessTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp= TencentCurrentCoverTests.asyncSetUp
    asyncTearDown= TencentCurrentCoverTests.asyncTearDown
    events= TencentCurrentCoverTests.events

    async def test_loading_card_waits_then_uses_one_normal_native_click_and_import(self):
        await self.page.set_content(cover_fixture())
        self.uploader.thumbnail_portrait_path=None
        await self.page.evaluate("()=>{const card=document.querySelector('.horizontal-cover-wrap');card.querySelector('.edit-btn').style.display='none';const loading=document.createElement('div');loading.className='loading-wrap';loading.textContent='生成中';card.append(loading);Object.defineProperty(card,'__vue__',{get(){throw Error('business getter read');}});}",isolated_context=False)
        task=asyncio.create_task(self.uploader.set_thumbnail(self.page))
        try:
            await asyncio.sleep(.25)
            self.assertEqual(await self.events(),[])
            self.assertEqual(self.uploader._draft_cover_phase,'等待平台封面控件准备')
            await self.page.evaluate("()=>{const card=document.querySelector('.horizontal-cover-wrap');card.querySelector('.loading-wrap').remove();card.querySelector('.edit-btn').style.display='';}",isolated_context=False)
            await asyncio.wait_for(task,10)
            kinds=[event['type'] for event in await self.events()]
            self.assertEqual(kinds.count('open'),1)
            self.assertEqual(kinds.count('file'),1)
            self.assertEqual(kinds.count('confirm'),1)
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_native_mask_disabled_or_duplicate_edit_marker_times_out_without_action(self):
        for mode in ('loading','mask','disabled','pointer','duplicate-edit'):
            with self.subTest(mode=mode):
                await self.page.set_content(cover_fixture())
                self.uploader.thumbnail_portrait_path=None
                self.uploader.draft_cover_ready_timeout=.2
                await self.page.evaluate("mode=>{const card=document.querySelector('.horizontal-cover-wrap');if(mode==='disabled')card.setAttribute('aria-disabled','true');else if(mode==='pointer')card.style.pointerEvents='none';else if(mode==='duplicate-edit')card.append(card.querySelector('.edit-btn').cloneNode(true));else {const mask=document.createElement('div');mask.className=mode==='mask'?'horizon-mask-layer':'loading-wrap';mask.textContent='生成中';card.append(mask);}}",mode,isolated_context=False)
                with self.assertRaisesRegex(RuntimeError,'封面控件仍在生成或不可编辑'):
                    await self.uploader.set_thumbnail(self.page)
                self.assertEqual(await self.events(),[])

    async def test_failed_changed_and_pending_binding_never_open_ready_cover_controls(self):
        for state in ('failed','changed','pending','uploading'):
            with self.subTest(state=state):
                await self.page.set_content(cover_fixture())
                self.uploader.thumbnail_portrait_path=None
                self.uploader.draft_cover_ready_timeout=.2
                self.page._sau_tc_native_import=object()
                read=AsyncMock(return_value=state)
                with patch('uploader.tc_pipeline.read_tc_upload_binding',read):
                    with self.assertRaises(RuntimeError):
                        await self.uploader.set_thumbnail(self.page)
                self.assertEqual(await self.events(),[])
        del self.page._sau_tc_native_import


if __name__ == '__main__':
    unittest.main()
