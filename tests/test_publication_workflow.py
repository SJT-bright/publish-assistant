"""Prepared publication and scoped guard tests; no platform/account/network."""
import asyncio
import copy
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_publish_action import FakePage, FakeFrame, FakeButton, FakeRequest, FakeResponse
from uploader import publication_workflow as workflow, publish_action
from uploader.draft_evidence import IDENTITY_KEYS
from uploader.draft_only import save_draft
from uploader.embedded_browser import EmbeddedBrowser


class GuardFrame(FakeFrame):
    def __init__(self, url):
        super().__init__(url)
        self.enabled = True

    async def evaluate(self, script, **_kwargs):
        if 'setEnabled(false)' in script:
            self.enabled = False
        if 'setEnabled(true)' in script:
            self.enabled = True
        return True


class GuardPage(FakePage):
    def __init__(self, frame):
        super().__init__(frame)
        self.main_frame = frame
        self.routes = []
        self.context = object()
        self.passed, self.aborted = 0, 0
        self.admission_gate = None
        self.guard_states_at_submission = []
        async def guard(route):
            await route.abort('blockedbyclient')
        self._sau_guard_route_handler = guard
        self._sau_guard_route = True
        self.routes.append(guard)

    async def route(self, pattern, handler):
        self.routes.append(handler)

    async def unroute(self, pattern, handler):
        if handler in self.routes:
            self.routes.remove(handler)

    async def submit(self, url, body, frame=None, method='POST'):
        request = FakeRequest(url, frame or self.main_frame, method)
        self.emit('request', request)
        handlers = list(reversed(self.routes))
        owner = self
        class Route:
            def __init__(self):
                self.request, self.position = request, 0
            async def fallback(self):
                if self.position < len(handlers):
                    current = handlers[self.position]
                    self.position += 1
                    await current(self)
                else:
                    if owner.admission_gate is not None:
                        await owner.admission_gate.wait()
                    owner.passed += 1
                    owner.guard_states_at_submission.append(owner.main_frame.enabled)
                    owner.emit('response', FakeResponse(request, body))
            async def abort(self, reason):
                owner.aborted += 1
        await Route().fallback()


class PreparedSession:
    def __init__(self, page, expected):
        self.page, self.expected = page, copy.deepcopy(expected)
        self.platform = 'douyin'
        self.closed, self.issues, self.action_at = False, [], None
        self.account = {'verified': True, 'identity':'fixture-owner', 'uid':'fixture-uid', 'platform':'douyin'}
        self._checkpoint_assets = AsyncMock(return_value={'video': {}})
        self.read_account = AsyncMock(side_effect=lambda: dict(self.account))
        self.drain = AsyncMock()
        self.links = [{'kind':'video', 'sha256':expected['video_sha256'], 'ids':['fixture-vid'], 'complete':True}]
        self.dy_observer = SimpleNamespace(upload_links=self.upload_links)
    def upload_links(self):
        return copy.deepcopy(self.links)


class PublicationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.frame = GuardFrame('https://creator.douyin.com/creator-micro/content/post/video')
        self.page = GuardPage(self.frame)
        self.expected = {key:'fixture-'+key for key in IDENTITY_KEYS}
        self.expected.update(platform_id='douyin', video_sha256='a'*64, cover_sha256={}, title='fixture-title',
                             workflow_action='publish', platform_fields={'title':'fixture-title','description':'fixture-body','tags':[]},
                             account={'verified':True,'identity':'fixture-owner','uid':'fixture-uid','platform':'douyin'})
        self.session = PreparedSession(self.page, self.expected)
        self.page._sau_draft_evidence = self.session
        self.button = FakeButton()
        self.background = []
        self.original_guard = self.page._sau_guard_route_handler
        self.patchers = [
            patch.object(workflow, '_wait_dy_publication_preview', AsyncMock(return_value=True)),
            patch('uploader.draft_only.assert_draft_safe', AsyncMock()),
            patch('uploader.draft_only._ui_fields', AsyncMock(return_value=True)),
            patch('uploader.draft_only._ui_assets', AsyncMock(return_value=True)),
            patch('uploader.draft_only._write_receipt', side_effect=lambda result, _path: result),
            patch.object(publish_action, 'inspect_session', AsyncMock(side_effect=lambda *_: dict(self.session.account))),
            patch.object(publish_action, '_locate_current_editor', AsyncMock(
                side_effect=lambda *_: (publish_action._Candidate(self.frame,self.button),None))),
        ]
        for patcher in self.patchers:
            patcher.start()

    async def asyncTearDown(self):
        if self.background:
            await asyncio.gather(*self.background, return_exceptions=True)
        for patcher in reversed(self.patchers):
            patcher.stop()

    async def run_workflow(self, *, receipt_timeout=.03):
        return await workflow.publish_prepared(self.page, 'douyin', expected=self.expected, receipt_timeout=receipt_timeout)

    async def test_xhs_official_native_host_receives_one_scoped_submit_only(self):
        self.frame.url='https://creator.xiaohongshu.com/publish/publish?target=video'
        self.page.url=self.frame.url
        self.expected['platform_id']='xiaohongshu'
        self.session.platform='xiaohongshu';self.session.expected=copy.deepcopy(self.expected)
        permit=workflow._OneSubmitPermit(self.page,'xiaohongshu',self.session,self.expected)
        await permit.release()
        await self.page.submit('https://edith.xiaohongshu.com/web_api/sns/v2/note',{})
        await self.page.submit('https://edith.xiaohongshu.com/web_api/sns/v2/note',{})
        await permit.restore()
        self.assertEqual((self.page.passed,self.page.aborted,permit.request_count),(1,1,1))
        self.assertIn(self.original_guard,self.page.routes)

    def submit_on_click(self, *, delay=.01, url='https://open.douyin.com/video/create', body=None):
        body = body if body is not None else {'extra':{'error_code':0},'data':{'error_code':0,'item_id':'fixture-submission'}}
        def click():
            async def later():
                await asyncio.sleep(delay)
                await self.page.submit(url,body)
            self.background.append(asyncio.create_task(later()))
        self.button.callback = click

    def assert_restored(self):
        self.assertTrue(self.frame.enabled)
        self.assertIs(self.page._sau_guard_route_handler, self.original_guard)
        self.assertIn(self.original_guard, self.page.routes)
        self.assertEqual(len(self.page.routes),1)

    async def test_one_native_click_delayed_post_and_independent_receipt(self):
        self.submit_on_click(delay=.03)
        result = await self.run_workflow()
        self.assertEqual(result['status'], publish_action.PUBLISHED_SUBMISSION_VERIFIED)
        self.assertTrue(all(result['verification'].values()))
        self.assertEqual(result['publication']['evidence']['submission_id'],'fixture-submission')
        self.assertEqual(self.button.clicks,1)
        self.assertEqual(self.page.passed,1)
        self.assertEqual(self.page.guard_states_at_submission,[False])
        self.assertFalse(result['partial'])
        self.assert_restored()

    async def test_private_creator_response_is_submitted_but_not_verified(self):
        self.submit_on_click(url='https://creator.douyin.com/web/api/media/aweme/create_v2',
                             body={'status_code':0,'data':{'aweme_id':'untrusted-web-id'}})
        result = await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLISHED_UNVERIFIED)
        self.assertFalse(result['verification']['submission'])
        self.assertTrue(result['clicked'])
        self.assertEqual(self.button.clicks,1)
        self.assert_restored()

    async def test_repeat_workflow_never_permits_a_second_click(self):
        self.submit_on_click()
        await self.run_workflow()
        repeated = await self.run_workflow()
        self.assertEqual(repeated['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(repeated['code'],'publication_already_attempted')
        self.assertEqual(self.button.clicks,1)
        self.assert_restored()

    async def test_atomic_final_action_marker_failure_never_releases_permission(self):
        marker=AsyncMock(side_effect=RuntimeError('fixture marker failure'))
        self.page._sau_checkpoint_runtime=SimpleNamespace(before_save=marker)
        result=await self.run_workflow()
        marker.assert_awaited_once_with(self.session)
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        self.assertFalse(getattr(self.session,'_publication_started',False))
        self.assert_restored()

    async def test_atomic_final_action_marker_precedes_the_single_native_click(self):
        marker=AsyncMock()
        self.page._sau_checkpoint_runtime=SimpleNamespace(before_save=marker)
        self.submit_on_click()
        original_click=self.button.callback
        def click():
            marker.assert_awaited_once_with(self.session)
            return original_click()
        self.button.callback=click
        result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLISHED_SUBMISSION_VERIFIED)
        self.assertEqual(self.button.clicks,1)
        self.assert_restored()

    async def test_missing_upload_fingerprint_stops_before_permission(self):
        self.session.links[0]['sha256']='b'*64
        result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        self.assert_restored()

    async def test_changed_account_and_changed_fields_stop_before_click(self):
        self.session.read_account=AsyncMock(return_value={**self.session.account,'identity':'another-owner'})
        result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        self.assertFalse(result['verification']['account'])
        self.assert_restored()

    async def test_final_preclick_fields_are_checked_again(self):
        with patch('uploader.draft_only._ui_fields', AsyncMock(side_effect=[True,False])):
            result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        self.assertFalse(result['verification']['fields'])
        self.assert_restored()

    async def test_original_files_are_rehashed_before_any_final_action(self):
        from uploader.draft_evidence import DraftEvidenceSession, _sha_file
        with tempfile.TemporaryDirectory(prefix='publication-source-fixture-') as folder:
            video=Path(folder)/'fixture.mp4';video.write_bytes(b'original fixture')
            asset=_sha_file(video);asset['kind']='video'
            self.expected['video_sha256']=asset['sha256'];self.expected['video']=str(video)
            self.session.expected=copy.deepcopy(self.expected)
            self.session.assets={'video':asset}
            self.session._checkpoint_assets=lambda:DraftEvidenceSession._checkpoint_assets(self.session)
            self.session.links[0]['sha256']=asset['sha256']
            video.write_bytes(b'changed fixture')
            result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        self.assert_restored()

    async def test_missing_requested_custom_cover_stops_before_click(self):
        self.expected['cover_sha256']={'cover_portrait':'c'*64}
        self.session.expected=copy.deepcopy(self.expected)
        result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        self.assert_restored()

    async def test_custom_covers_keep_each_native_slot_binding(self):
        hashes={'cover_portrait':'c'*64,'cover_landscape':'c'*64}
        raw=[*self.session.links,
             {'kind':'cover','slot':'cover_portrait','sha256':'c'*64,'ids':['portrait-01'],'complete':True},
             {'kind':'cover','slot':'cover_landscape','sha256':'c'*64,'ids':['landscape-01'],'complete':True}]
        normalized=workflow._checkpoint_links(raw,filter_resources=True)
        candidate=workflow._candidate('douyin',{**self.expected,'cover_sha256':hashes},self.session,normalized,raw)
        self.assertEqual(candidate['cover_slots'],{'cover_portrait':['portrait-01'],'cover_landscape':['landscape-01']})

    async def test_ambiguous_generic_proof_cannot_override_current_native_attempt(self):
        raw=[*self.session.links,{'kind':'video','sha256':'a'*64,'ids':['generic-old-vid'],'complete':True}]
        candidate=workflow._candidate('douyin',self.expected,self.session,raw,raw)
        self.assertEqual(candidate['video_ids'],['fixture-vid'])
        self.session.dy_observer=SimpleNamespace(upload_links=lambda:[])
        with self.assertRaisesRegex(workflow.PublicationBlocked,'current_native_upload_unverified'):
            workflow._candidate('douyin',self.expected,self.session,raw,raw)

    async def test_bili_projection_without_current_sdk_binding_cannot_pass(self):
        from uploader.bili_evidence import bili_cover_identity
        first='https://archive.biliimg.com/bfs/archive/fixture-main.jpg'
        second='https://archive.biliimg.com/bfs/archive/fixture-extra.jpg'
        media={'video_count':1,'video_ids':['123','fixture-name'], 'cover':first, 'cover43':second}
        candidate={'video_ids':['123','fixture-name'], 'cover_ids':[bili_cover_identity(first),bili_cover_identity(second)]}
        frame=SimpleNamespace(evaluate=AsyncMock(side_effect=lambda *_args,**_kwargs:copy.deepcopy(media)))
        with patch('uploader.bili_controls._frame',AsyncMock(return_value=frame)):
            self.assertFalse(await workflow._bili_current_media(self.page,{'cover_sha256':{'cover':'c'*64}},candidate))
            media['video_ids']=['456','another-video']
            self.assertFalse(await workflow._bili_current_media(self.page,{'cover_sha256':{'cover':'c'*64}},candidate))

    async def test_tc_processed_candidate_uses_only_an_attested_processed_id(self):
        from tests.test_tc_resource import resource
        from uploader.tc_resource import tc_resource_identity
        processed=resource();identity=tc_resource_identity(processed)
        links=[{'kind':'video','sha256':'a'*64,'ids':['tc-resource:'+'d'*64,identity],'complete':True}]
        session=SimpleNamespace(tc_processing_observer=SimpleNamespace(native={'processed_url':processed}))
        # Candidate selection consumes the observer's verified resource contract.
        # Full synchronous/async provenance is exercised by TC observer tests.
        session.tc_processing_observer.current_publication_resource=lambda _links: tc_resource_identity(
            session.tc_processing_observer.native['processed_url'])
        candidate=workflow._candidate('tencent',self.expected,session,links,links)
        self.assertEqual(candidate['video_ids'],[identity])
        session.tc_processing_observer.native['processed_url']=resource(origin='https://untrusted.example')
        with self.assertRaisesRegex(workflow.PublicationBlocked,'current_processed_video_unverified'):
            workflow._candidate('tencent',self.expected,session,links,links)

    async def test_restore_waits_for_actual_admission_when_route_is_in_flight(self):
        permit=workflow._OneSubmitPermit(self.page,'douyin',self.session,self.expected,admission_timeout=.2)
        await permit.release()
        self.page.admission_gate=asyncio.Event()
        submit=asyncio.create_task(self.page.submit('https://open.douyin.com/video/create',{}))
        self.background.append(submit)
        await asyncio.sleep(.01)
        restore=asyncio.create_task(permit.restore())
        await asyncio.sleep(.01)
        self.assertFalse(restore.done())
        self.assertFalse(self.frame.enabled)
        self.page.admission_gate.set()
        await restore
        self.assertEqual(self.page.passed,1)
        self.assert_restored()

    async def test_final_route_allows_one_post_and_blocks_duplicate_foreign_post(self):
        permit=workflow._OneSubmitPermit(self.page,'douyin',self.session,self.expected,admission_timeout=.02)
        await permit.release()
        await self.page.submit('https://creator.douyin.com/web/api/media/aweme/create_v2',{})
        await self.page.submit('https://creator.douyin.com/web/api/media/aweme/create_v2',{})
        await self.page.submit('https://untrusted.example/web/api/media/aweme/create_v2',{})
        await permit.restore()
        self.assertEqual(self.page.passed,1)
        self.assertEqual(self.page.aborted,2)
        self.assert_restored()

    async def test_cancelled_admission_wait_still_restores_all_guards(self):
        handle=SimpleNamespace(page=self.page,platform='douyin',action='publish',lease='fixture-lease',
                               allow_publication=AsyncMock(),revoke_publication=AsyncMock())
        self.page._sau_embedded_handle=handle
        permit=workflow._OneSubmitPermit(self.page,'douyin',self.session,self.expected,admission_timeout=.2)
        await permit.release()
        task=asyncio.create_task(permit.restore())
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        handle.revoke_publication.assert_awaited_once()
        self.assert_restored()

    async def test_uncertain_embedded_permit_is_revoked_without_a_click(self):
        handle=SimpleNamespace(page=self.page,platform='douyin',action='publish',lease='fixture-lease',
                               allow_publication=AsyncMock(side_effect=RuntimeError('fixture bridge error')),
                               revoke_publication=AsyncMock())
        self.page._sau_embedded_handle=handle
        result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLICATION_BLOCKED)
        self.assertEqual(self.button.clicks,0)
        handle.revoke_publication.assert_awaited_once()
        self.assert_restored()

    async def test_revoke_failure_keeps_success_response_unverified(self):
        handle=SimpleNamespace(page=self.page,platform='douyin',action='publish',lease='fixture-lease',
                               allow_publication=AsyncMock(),revoke_publication=AsyncMock(side_effect=RuntimeError('revoke fixture')))
        self.page._sau_embedded_handle=handle
        self.submit_on_click()
        result=await self.run_workflow()
        self.assertEqual(result['status'],publish_action.PUBLISHED_UNVERIFIED)
        self.assertEqual(result['code'],'guard_restore_failed')
        self.assertFalse(result['verification']['submission'])
        self.assertEqual(self.button.clicks,1)
        self.assert_restored()

    async def test_save_draft_dispatches_publish_without_a_draft_click(self):
        with patch.object(workflow,'publish_prepared',AsyncMock(return_value={'status':'fixture-dispatch'})) as publish:
            result=await save_draft(self.page,'douyin','fixture-title',expected=self.expected)
        self.assertEqual(result['status'],'fixture-dispatch')
        publish.assert_awaited_once()
        self.assertEqual(self.button.clicks,0)


class EmbeddedPublicationPermitTests(unittest.IsolatedAsyncioTestCase):
    def handle(self,action='publish'):
        handle=EmbeddedBrowser('douyin',action)
        handle.lease='fixture-lease'
        handle.page=SimpleNamespace(_sau_embedded_handle=handle)
        return handle

    async def test_bridge_uses_exact_platform_lease_and_restores_on_release(self):
        handle=self.handle()
        with patch('uploader.embedded_browser.bridge_request',return_value={'ok':True}) as bridge:
            await handle.allow_publication()
            await handle.release()
        self.assertEqual([call.args for call in bridge.call_args_list],[
            ('/publish-permit',{'lease':'fixture-lease','platform':'douyin'}),
            ('/publish-revoke',{'lease':'fixture-lease','platform':'douyin'}),
            ('/finished',{'lease':'fixture-lease'})])
        self.assertIsNone(handle.lease)
        self.assertIsNone(handle.page._sau_embedded_handle)

    async def test_draft_lease_or_wrong_page_can_never_request_a_permit(self):
        for reason in ('draft','page'):
            handle=self.handle('draft' if reason=='draft' else 'publish')
            if reason=='page':handle.page._sau_embedded_handle=object()
            with patch('uploader.embedded_browser.bridge_request') as bridge:
                with self.assertRaises(RuntimeError):await handle.allow_publication()
            bridge.assert_not_called()

    async def test_bad_permit_response_retains_revoke_requirement(self):
        handle=self.handle()
        with patch('uploader.embedded_browser.bridge_request',side_effect=[{'ok':False},{'ok':True}]) as bridge:
            with self.assertRaises(RuntimeError):await handle.allow_publication()
            self.assertTrue(handle._publication_permit_requested)
            await handle.revoke_publication()
        self.assertFalse(handle._publication_permit_requested)
        self.assertEqual(bridge.call_count,2)


if __name__=='__main__':unittest.main()
