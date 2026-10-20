"""Finish an authorized prepared upload with one guarded native submission.

No file selection, editor write, save, retry, or API replay occurs here. Upload
provenance and current UI/account are checked independently before releasing
this client's guard for the single native publish action.
"""
import asyncio
import copy
import json
import math
import time
from pathlib import Path
from urllib.parse import urlsplit

from uploader.draft_evidence import IDENTITY_KEYS, _checkpoint_links, evaluate_main
from uploader.draft_progress import emit as emit_progress
from uploader.draft_session import CREATOR_HOSTS
from uploader import publish_action


PREPARED_CHECKS = ('upload_complete', 'fields', 'cover', 'media', 'account')


class PublicationBlocked(RuntimeError):
    """An explicit, content-free preparation failure code."""


async def _wait_dy_publication_preview(page, expected, session, *, timeout=60, poll_interval=.25):
    """Wait for this attested vid's decoded HTTPS preview, without reuploading.

    The creator requests its preview/transcode after SDK completion. The
    existing reader may select the unique video-preview presentation once;
    all subsequent polls are pure reads with the same account, File and vid.
    """
    from uploader.draft_only import assert_draft_safe
    from uploader.dy_upload_ready import DY_VIDEO_UPLOAD_STATE, wait_dy_video_uploaded
    from uploader.dy_reopen import DY_VISIBLE_VIDEO_SCRIPT, read_dy_visible_video
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (timeout, poll_interval)):
        raise PublicationBlocked('douyin_preview_wait_invalid')
    timeout = min(timeout, 60)
    deadline = time.monotonic() + timeout
    restored_at = getattr(session, '_checkpoint_restored_at', None)
    observer = getattr(session, 'dy_observer', None)
    bound_scope = copy.deepcopy(getattr(observer, 'scope', None))
    bound_invocation = getattr(observer, 'latest_invocation', None)
    original_assets = copy.deepcopy(session.assets)
    restored_links = copy.deepcopy(getattr(session, '_checkpoint_links', []))
    paths = [expected.get('video') if role == 'video' else expected.get('platform_fields', {}).get(role)
             for role in original_assets]

    def signatures():
        try:
            stats = [Path(path).stat() for path in paths]
            return [tuple(getattr(stat, key) for key in
                          ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')) for stat in stats]
        except (OSError, TypeError, ValueError):
            raise PublicationBlocked('douyin_readiness_source_changed') from None

    sdk_checked = False

    async def bound_ids(original_files):
        nonlocal sdk_checked
        if (not _current(page, session, expected, 'douyin') or session.issues or
                session.assets != original_assets or signatures() != original_files or
                getattr(session, '_checkpoint_restored_at', None) != restored_at):
            raise PublicationBlocked('douyin_readiness_scope_or_source_changed')
        await assert_draft_safe(page, 'douyin')
        account = await session.read_account()
        if (account != session.account or account.get('verified') is not True or
                account.get('identity') != expected.get('account', {}).get('identity')):
            raise PublicationBlocked('douyin_readiness_account_changed')
        if restored_at is None:
            if (getattr(session, 'dy_observer', None) is not observer or observer is None or observer.closed or
                    observer.scope != bound_scope or observer.latest_invocation != bound_invocation):
                raise PublicationBlocked('douyin_readiness_observer_changed')
            if not sdk_checked:
                await wait_dy_video_uploaded(page, timeout=min(1, max(.01, deadline-time.monotonic())))
                sdk_checked = True
            # Reuse the current-File native reader without restarting the
            # completed upload stage's progress event on every preview poll.
            scope = {**bound_scope, 'invocation_id': bound_invocation}
            native = await observer.transport.evaluate('(' + DY_VIDEO_UPLOAD_STATE + ')(' + json.dumps(scope) + ')')
            if (not isinstance(native, dict) or native.get('scoped') is not True or
                    native.get('invalid') is True or native.get('ready') is not True):
                raise PublicationBlocked('douyin_readiness_upload_no_longer_complete')
            raw = observer.upload_links()
        else:
            expires = getattr(session, '_checkpoint_expires_at', None)
            if type(expires) not in (int, float) or not math.isfinite(expires) or time.time() >= expires:
                raise PublicationBlocked('douyin_readiness_checkpoint_expired')
            if getattr(session, '_checkpoint_links', []) != restored_links:
                raise PublicationBlocked('douyin_readiness_checkpoint_changed')
            asset = original_assets.get('video', {})
            if any(row.get('selected_at', 0) >= restored_at and (
                    str(row.get('type', '')).startswith('video/') or
                    (row.get('name'), row.get('size')) == (asset.get('name'), asset.get('size')) or
                    str(row.get('name', '')).lower().endswith(('.mp4','.mov','.mkv','.webm','.avi','.m4v')))
                   for row in session.files):
                raise PublicationBlocked('douyin_readiness_video_reselected')
            raw = restored_links
        videos = [link for link in _checkpoint_links(raw, filter_resources=True) if link['kind'] == 'video' and
                  link['sha256'] == expected['video_sha256']]
        current = _checkpoint_links(session.upload_links(), filter_resources=True)
        if (len(videos) != 1 or len(videos[0]['ids']) != 1 or videos[0] not in current or
                (restored_at is None and native.get('vid') != videos[0]['ids'][0])):
            raise PublicationBlocked('douyin_readiness_current_vid_unverified')
        return videos[0]['ids']

    def matches(snapshot, ids):
        return (isinstance(snapshot, dict) and snapshot.get('source') == 'normal_visible_video_component' and
                snapshot.get('video_count') == 1 and snapshot.get('video_ids') == ids)

    presentation = None
    try:
        async with asyncio.timeout(timeout):
            await session._checkpoint_assets()
            original_files = signatures()
            ids = await bound_ids(original_files)
            presentation = asyncio.create_task(read_dy_visible_video(page, ids))
            while time.monotonic() < deadline:
                if await bound_ids(original_files) != ids:
                    raise PublicationBlocked('douyin_readiness_current_vid_changed')
                if presentation is not None and presentation.done():
                    first = presentation.result()
                    presentation = None
                    if matches(first, ids):
                        if await bound_ids(original_files) != ids:
                            raise PublicationBlocked('douyin_readiness_current_vid_changed')
                        return True
                if presentation is None:
                    snapshot = await evaluate_main(page.main_frame, DY_VISIBLE_VIDEO_SCRIPT.replace('WANTED', json.dumps(ids)))
                    if matches(snapshot, ids):
                        if await bound_ids(original_files) != ids:
                            raise PublicationBlocked('douyin_readiness_current_vid_changed')
                        return True
                await asyncio.sleep(min(poll_interval, max(0, deadline-time.monotonic())))
    except TimeoutError:
        pass
    finally:
        if presentation is not None:
            presentation.cancel()
            await asyncio.gather(presentation, return_exceptions=True)
    raise PublicationBlocked('douyin_current_preview_not_ready')


def _current(page, session, expected, platform):
    return (session is not None and getattr(page, '_sau_draft_evidence', None) is session and
            getattr(session, 'page', None) is page and not session.closed and
            session.platform == platform and expected.get('workflow_action') == 'publish' and
            session.expected.get('workflow_action') == 'publish' and
            all(key in expected and key in session.expected and
                expected[key] == session.expected[key] and (key == 'cover_sha256' or bool(expected[key]))
                for key in IDENTITY_KEYS))


def _candidate(platform, expected, session, links, raw_links):
    native_names = {'douyin':'dy_observer', 'xiaohongshu':'xhs_observer',
                    'bilibili':'bili_observer', 'kuaishou':'ks_observer'}
    native = getattr(session, native_names.get(platform, ''), None)
    source_links = links
    if platform in native_names:
        if native is None:
            raise PublicationBlocked('native_upload_observer_missing')
        observed = _checkpoint_links(native.upload_links(), filter_resources=True)
        native_videos = [link for link in observed if link['kind'] == 'video']
        if native_videos:
            source_links = native_videos
        elif getattr(session, '_checkpoint_restored_at', None) is None:
            raise PublicationBlocked('current_native_upload_unverified')
    videos = [link for link in source_links if link['kind'] == 'video' and link['sha256'] == expected.get('video_sha256')]
    if len(videos) != 1:
        raise PublicationBlocked('current_upload_unverified')
    video_ids = videos[0]['ids'][:]
    if platform == 'tencent':
        # TC's official asyncClip consumes the uploaded original plus its
        # current clip ticket. Synchronous editors consume the finished output.
        observer = getattr(session, 'tc_processing_observer', None)
        resource = observer.current_publication_resource(links) if observer is not None else None
        if resource not in video_ids:
            raise PublicationBlocked('current_processed_video_unverified')
        video_ids = [resource]
    covers, slots = set(), {}
    hashes = expected.get('cover_sha256')
    if not isinstance(hashes, dict):
        raise PublicationBlocked('cover_fingerprints_missing')
    for slot, digest in hashes.items():
        records = []
        for link in raw_links:
            if link.get('kind') == 'cover' and link.get('sha256') == digest and link.get('slot') in (None, slot):
                records.extend(_checkpoint_links([link], filter_resources=True))
        ids = sorted({value for link in records for value in link['ids']})
        if not ids:
            raise PublicationBlocked('current_cover_upload_unverified')
        slots[slot] = ids
        covers.update(ids)
    return {'video_ids': video_ids, 'video_count': 1,
            'cover_ids': sorted(covers), 'cover_slots': slots}


_BILI_CURRENT_COVERS = r"""() => {try{
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const roots=[...document.querySelectorAll('.cover-module-main')].filter(visible),seen=new Set(),owners=new Set();
 if(roots.length!==1)return null;
 for(let node=roots[0];node;node=node.parentElement)for(let vm=node.__vue__,depth=0;vm&&!seen.has(vm)&&depth++<32;vm=vm.$parent){
  seen.add(vm);if(typeof vm.openCoverEditor==='function'&&typeof vm.submitCoverEditor==='function')owners.add(vm);
 }
 if(owners.size!==1)return null;const vm=[...owners][0];
 if(vm.defaultCoverIndex!==0||!Array.isArray(vm.coverList)||vm.coverList.length!==1)return null;
 const row=vm.coverList[0];return typeof row?.main?.edited==='string'&&typeof row?.extra?.edited==='string'?
  {cover:row.main.edited,cover43:row.extra.edited}:null;
}catch(_){return null;}}"""


async def _bili_current_media(page, expected, candidate):
    """Bind this fresh File/SDK task and committed crops without a reopen gate."""
    from uploader import bili_controls
    from uploader.bili_evidence import bili_cover_identity
    from uploader.draft_only import read_checkpoint_composer_media
    session=getattr(page,'_sau_draft_evidence',None)
    observer=getattr(session,'bili_observer',None)
    asset=getattr(session,'assets',{}).get('video')
    binding=None
    async def current_task():
        keys=json.dumps(bili_controls._EDITOR_REFS)
        return await asyncio.wait_for(binding.evaluate('(value,scope)=>{'+bili_controls._EDITOR_OBJECT_JS+
            'const live=read("uploaded"),root=window.__sauBiliEvidenceObserver,sel=root?.video;'
            'return !!live&&'+keys+'.every(key=>live[key]===value[key])&&root?.version===2&&'
            'root.active?.job_id===scope.job_id&&root.active?.receipt_nonce===scope.receipt_nonce&&'
            'root.active?.observer_nonce===scope.observer_nonce&&sel?.scope===root.active&&'
            'sel.kind==="video"&&sel.manager===value.manager&&sel.parentTask===value.parent&&'
            'sel.file===value.file&&sel.input===value.input&&value.parent.videoTasks[0]===value.task;}',observer.scope),5) is True
    try:
        if observer is None or not isinstance(asset,dict):
            return False
        links=[link for link in observer.upload_links() if link.get('kind')=='video']
        if len(links)!=1 or set(links[0].get('ids',[]))!=set(candidate['video_ids']):
            return False
        frame=await bili_controls._frame(page,timeout=.3)
        binding=await bili_controls._editor_object(frame,'uploaded')
        if not await current_task():
            return False
        proof={'identity':{'platform_id':'bilibili','video_sha256':expected['video_sha256']},
               'assets':{'video':asset},'upload_links':links}
        # This existing reader hashes the actual native File in its canonical
        # default realm and checks Publisher/manager/task/record/SDK ownership.
        # The old video entrance may be hidden after a successful import.
        media=await read_checkpoint_composer_media(page,proof)
        if (not media or media.get('video_count')!=1 or set(media.get('video_ids',[]))!=set(candidate['video_ids'])):
            return False
        if expected['cover_sha256']:
            before=await bili_controls._committed_cover(frame)
            if not all(before.get(key) is True for key in ('uploaded','server_images','same_origin')):
                return False
            covers=await asyncio.wait_for(frame.evaluate(_BILI_CURRENT_COVERS,isolated_context=False),5)
            if not isinstance(covers,dict):
                return False
            ids=[bili_cover_identity(covers.get('cover')),bili_cover_identity(covers.get('cover43'))]
            if None in ids or set(ids)!=set(candidate['cover_ids']) or await bili_controls._committed_cover(frame)!=before:
                return False
        return await current_task()
    except Exception:
        return False
    finally:
        if binding is not None:
            await bili_controls._dispose_editor(binding)


_TC_CURRENT_COVERS = r"""scope => {
 const root=window.__sauTCCoverObserver;
 if(!root||root.scope?.job_id!==scope.job_id||root.scope?.receipt_nonce!==scope.receipt_nonce||
   root.scope?.observer_nonce!==scope.observer_nonce||!(root.wrappers instanceof Map))return null;
 const previews=new Set();
 for(const object of root.wrappers.keys()){
   const selected=root.parents.get(object),preview=selected?.previewInstance,el=preview?.$el;
   if(selected?.scope!==root.scope||!selected.invocation?.applied||!el?.isConnected)continue;
   const box=el.getBoundingClientRect(),style=el.ownerDocument.defaultView.getComputedStyle(el);
   if(box.width>0&&box.height>0&&style.display!=='none'&&style.visibility!=='hidden')previews.add(preview);
 }
 if(previews.size!==1)return null;
 const video=previews.values().next().value.video;
 return video&&typeof video==='object'?{cover_portrait:video.coverUrl,cover_landscape:video.shareCoverUrl}:null;
}"""


async def _tc_current_media(page, expected, session, links, candidate):
    from uploader.tc_editor import read_tc_composer_media
    from uploader.tc_resource import tc_resource_identity
    media = await read_tc_composer_media(page)
    videos = [link for link in links if link['kind'] == 'video' and link['sha256'] == expected['video_sha256']]
    if (not media or media.get('video_count') != 1 or len(videos) != 1 or
            not set(media.get('video_ids', [])).issubset(videos[0]['ids']) or not media.get('video_ids')):
        return False
    if not expected['cover_sha256']:
        return True
    observer = getattr(session, 'tc_cover_observer', None)
    if observer is None:
        return False
    # Cover controls can be owned by Wujie's same-origin micro frame. The
    # existing observer configures and drains each frame separately and can
    # delegate a projected input event to that owning realm. Read each current
    # official realm without calling any Vue method; equal signed URL aliases
    # agree, while conflicting current previews fail closed.
    projections=set()
    for frame in tuple(getattr(observer,'frames',())):
        before=getattr(frame,'url',None)
        try:
            parsed=urlsplit(before)
            official=(parsed.scheme=='https' and parsed.netloc=='channels.weixin.qq.com' and
                      parsed.path.rstrip('/') in ('/platform/post/create','/micro/content/post/create'))
            if not official or frame not in page.frames or frame.page is not page:
                continue
            current=await evaluate_main(frame,'(' + _TC_CURRENT_COVERS + ')(' + json.dumps(observer.scope) + ')')
            if frame.url!=before or frame not in page.frames or frame.page is not page:
                return False
        except Exception:
            return False
        if current is None:
            continue
        if (not isinstance(current,dict) or set(current)!= {'cover_portrait','cover_landscape'} or
                any(value is not None and not isinstance(value,str) for value in current.values())):
            return False
        projections.add(tuple(tc_resource_identity(current[slot]) for slot in ('cover_portrait','cover_landscape')))
    if len(projections)!=1:
        return False
    current=dict(zip(('cover_portrait','cover_landscape'),next(iter(projections))))
    for slot, digest in expected['cover_sha256'].items():
        resource = current.get(slot)
        records = [record for record in observer.records.values() if record.get('slot') == slot and
                   record.get('origin_sha256') == digest and tc_resource_identity(record.get('applied_id')) == resource]
        if not resource or len(records) != 1 or resource not in candidate['cover_slots'].get(slot, []):
            return False
    return True


async def _verify_prepared(page, platform, expected, session, checks):
    from uploader.draft_only import assert_draft_safe, _ui_fields, _ui_assets
    if not _current(page, session, expected, platform) or session.issues:
        raise PublicationBlocked('evidence_session_unverified')
    await assert_draft_safe(page, platform)
    await session._checkpoint_assets()  # Rehash actual source files against the task's expected assets.
    await session.drain()
    raw_links = session.upload_links()
    links = _checkpoint_links(raw_links, filter_resources=True)
    if platform == 'tencent' and len([link for link in links if link['kind'] == 'video' and
                                     link['sha256'] == expected['video_sha256']]) != 1:
        from uploader.tc_reuse import tc_unverified_upload_reason
        raise PublicationBlocked(await tc_unverified_upload_reason(page, session, expected))
    candidate = _candidate(platform, expected, session, links, raw_links)
    checks['upload_complete'] = True
    checks['cover'] = not bool(expected['cover_sha256'])
    if platform == 'xiaohongshu':
        from uploader.xhs_publication_readback import read_xhs_fresh_publication
        fresh = await read_xhs_fresh_publication(page, expected, candidate)
        fields_ok, media_ok = fresh['fields_ok'], fresh['media_ok']
    else:
        fields_ok = await _ui_fields(page, platform, expected)
        media_ok = await _ui_assets(page, candidate, video_only=not bool(expected['cover_sha256']))
    if platform == 'bilibili':
        media_ok = await _bili_current_media(page, expected, candidate)
    elif platform == 'tencent' and media_ok is not True:
        media_ok = await _tc_current_media(page, expected, session, links, candidate)
    checks['fields'] = fields_ok is True
    checks['media'] = media_ok is True
    if expected['cover_sha256']:
        checks['cover'] = media_ok is True
    account = await session.read_account()
    wanted = expected.get('account') or {}
    checks['account'] = bool(account.get('verified') is True and account.get('platform') == platform and
                             account.get('identity') and account.get('identity') == wanted.get('identity') and
                             account == session.account and
                             (not wanted.get('uid') or str(account.get('uid')) == str(wanted['uid'])))
    if not _current(page, session, expected, platform) or session.issues:
        raise PublicationBlocked('evidence_session_changed')
    # A field/media scan cannot preserve a receipt invalidated while awaiting.
    if links != _checkpoint_links(session.upload_links(), filter_resources=True):
        raise PublicationBlocked('current_upload_changed')
    if any(checks[key] is not True for key in PREPARED_CHECKS):
        raise PublicationBlocked('prepared_fields_media_or_account_unverified')
    return account


class _OneSubmitPermit:
    def __init__(self, page, platform, session, expected, *, admission_timeout=3):
        self.page, self.platform, self.session, self.expected = page, platform, session, expected
        self.admission_timeout = min(max(float(admission_timeout), 0), 3)
        self.admitted = asyncio.Event()
        self.armed, self.installed, self.released, self.restored = False, False, False, False
        self.request_count = 0
        self.guard = getattr(page, '_sau_guard_route_handler', None)
        self.handle = getattr(page, '_sau_embedded_handle', None)
        self.route_handler = self._route

    def _allowed_request(self, request):
        try:
            parsed = urlsplit(request.url)
            official = publish_action._DOCUMENTED_API_HOSTS.get((parsed.hostname, parsed.path.rstrip('/')))
            host_ok = parsed.hostname == CREATOR_HOSTS[self.platform] or official == self.platform
            if self.platform == 'xiaohongshu':
                host_ok = host_ok or publish_action._xhs_native_submit_url(request.url)
            return (request.method == 'POST' and parsed.scheme == 'https' and not parsed.username and
                    not parsed.password and parsed.port in (None, 443) and host_ok and
                    request.frame in self.page.frames and
                    publish_action._official_editor_route(request.frame.url, self.platform))
        except (AttributeError, TypeError, ValueError):
            return False

    async def _route(self, route):
        request = route.request
        if not publish_action._is_observed_publish_request(request.url, request.method):
            await route.fallback()
            return
        if (not self.armed or self.request_count or not self._allowed_request(request) or
                not _current(self.page, self.session, self.expected, self.platform)):
            await route.abort('blockedbyclient')
            return
        # Consume the admission synchronously before awaiting any network step.
        self.request_count = 1
        self.armed = False
        await route.fallback()
        self.admitted.set()

    async def _set_click_guard(self, enabled):
        script = '(async()=>{const c=window.__sauDraftGuardControl;if(!c)return false;c.setEnabled(' + (
            'true' if enabled else 'false') + ');return window.__sauDraftGuard===' + (
            'true' if enabled else 'false') + '})()'
        for frame in self.page.frames:
            if await evaluate_main(frame, script) is not True:
                raise PublicationBlocked('owned_click_guard_unavailable')

    async def release(self):
        if self.guard is None or getattr(self.page, '_sau_guard_route_handler', None) is not self.guard:
            raise PublicationBlocked('owned_request_guard_unavailable')
        if self.handle is not None and (self.handle.page is not self.page or self.handle.platform != self.platform or
                                        self.handle.action != 'publish' or not self.handle.lease):
            raise PublicationBlocked('embedded_publish_lease_mismatch')
        try:
            await self.page.route('**/*', self.route_handler)
            self.installed = True
            await self.page.unroute('**/*', self.guard)
            self.released = True
            self.page._sau_guard_route_handler = None
            self.page._sau_guard_route = False
            if self.handle is not None:
                await self.handle.allow_publication()
            await self._set_click_guard(False)
            self.armed = True
            return self.restore
        except BaseException:
            await self.restore(wait=False)
            raise

    async def restore(self, *, wait=True):
        if self.restored:
            return
        self.restored = True
        waiting_error = None
        if wait and (self.armed or self.request_count) and not self.admitted.is_set():
            try:
                await asyncio.wait_for(self.admitted.wait(), timeout=self.admission_timeout)
            except TimeoutError:
                pass
            except BaseException as exc:
                waiting_error = exc
        self.armed = False
        failed = False
        # Each cleanup is attempted independently, including after cancellation
        # or an uncertain permit response. Other route handlers remain intact.
        try:
            await self._set_click_guard(True)
        except BaseException:
            failed = True
        if self.released:
            try:
                current = getattr(self.page, '_sau_guard_route_handler', None)
                if current not in (None, self.guard):
                    raise PublicationBlocked('owned_request_guard_changed')
                await self.page.route('**/*', self.guard)
                self.page._sau_guard_route_handler = self.guard
                self.page._sau_guard_route = True
            except BaseException:
                failed = True
        if self.handle is not None:
            try:
                await self.handle.revoke_publication()
            except BaseException:
                failed = True
        if self.installed:
            try:
                await self.page.unroute('**/*', self.route_handler)
            except BaseException:
                failed = True
        if waiting_error is not None:
            raise waiting_error
        if failed:
            raise PublicationBlocked('publication_guard_restore_failed')


async def publish_prepared(page, platform, *, expected, receipt_path=None, receipt_timeout=8):
    """Require current task proof, then use publish_current's one native click."""
    from uploader.draft_only import _write_receipt, _checkpoint_before_save
    expected = copy.deepcopy(expected) if isinstance(expected, dict) else {}
    checks = dict.fromkeys(PREPARED_CHECKS + ('submission',), False)
    session = getattr(page, '_sau_draft_evidence', None)
    account, permit = {}, None
    emit_progress('save', 'running')
    try:
        if platform not in publish_action.PLATFORMS or not _current(page, session, expected, platform):
            raise PublicationBlocked('publication_action_not_authorized')
        if getattr(session, '_publication_started', False) or session.action_at is not None:
            raise PublicationBlocked('publication_already_attempted')
        if platform == 'douyin':
            await _wait_dy_publication_preview(page, expected, session)
        account = await _verify_prepared(page, platform, expected, session, checks)

        async def before_click(current_page, current_platform):
            nonlocal account, permit
            if current_page is not page or current_platform != platform:
                raise PublicationBlocked('publication_target_changed')
            account = await _verify_prepared(page, platform, expected, session, checks)
            if getattr(session, '_publication_started', False) or session.action_at is not None:
                raise PublicationBlocked('publication_already_attempted')
            # Reuse the runner's atomic final-action marker. A crash after the
            # native click but before a receipt must never permit resume/send.
            await _checkpoint_before_save(page, session)
            session._publication_started = True
            session.action_at = time.time()
            permit = _OneSubmitPermit(page, platform, session, expected)
            return await permit.release()

        emit_progress('verify', 'running')
        publication = await publish_action.publish_current(page, platform, expected,
            before_click=before_click, receipt_timeout=receipt_timeout)
    except Exception as exc:
        code = str(exc) if isinstance(exc, PublicationBlocked) else 'prepared_verification_failed'
        publication = {'platform': platform, 'status': publish_action.PUBLICATION_BLOCKED, 'code': code,
                       'clicked': False, 'evidence': {}, 'observation': {'publish_requests': 0, 'publish_responses': 0}}
    finally:
        if permit is not None and not permit.restored:
            await permit.restore(wait=False)
    checks['submission'] = publication.get('status') == publish_action.PUBLISHED_SUBMISSION_VERIFIED
    verified = checks['submission'] and all(checks[key] is True for key in PREPARED_CHECKS)
    emit_progress('save', 'completed' if publication.get('clicked') else 'failed')
    emit_progress('verify', 'completed' if verified else 'failed')
    result = {key: expected.get(key) for key in IDENTITY_KEYS}
    messages = {publish_action.PUBLISHED_SUBMISSION_VERIFIED: '平台已接受本次投稿。',
                publish_action.PUBLISHED_UNVERIFIED: '已执行投稿操作，回执尚未核验；请检查平台当前结果。',
                publish_action.PUBLICATION_BLOCKED: '提交条件未通过，已保留素材和页面，未点击投稿。'}
    if publication.get('code') == 'tencent_cached_upload_chain_unverified':
        messages[publish_action.PUBLICATION_BLOCKED] = '视频号复用了平台旧上传记录，本次完整上传链尚未核验；已保留素材和页面，未点击投稿。'
    result.update(platform=platform, workflow_action='publish', status=publication['status'],
                  clicked=publication.get('clicked') is True, code=publication.get('code'),
                  evidence=publication.get('evidence', {}), observation=publication.get('observation', {}),
                  publication=publication, verification=checks, account=account,
                  submitted_at=time.time() if publication.get('clicked') else None,
                  verified_at=time.time(), evidence_version=3,
                  message=messages.get(publication['status']), partial=not verified)
    return _write_receipt(result, receipt_path)
