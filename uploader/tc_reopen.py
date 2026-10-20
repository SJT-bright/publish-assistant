"""Reopen a saved video-channel draft using its ordinary visible UI.

Cached official 755.151a1d93.js (chars 29700/31200) binds each
.post-draft-item to a Vue post.objectId and routes its icon-edit operation to
PostCreate. The official 772 leave dialog uses an explicit 不保存 button.
No URL, API call, Vue business method, or synthetic DOM click is generated.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
import time
from urllib.parse import urlsplit


def _diagnostic(stage, reason, **values):
    """Only fixed stage/reason names, booleans, counts, and type names leave here."""
    print('TC_REOPEN_DIAGNOSTIC=' + json.dumps({'stage': stage, 'reason': reason, **values},
                                              ensure_ascii=True, sort_keys=True), flush=True)


def _failed(stage, reason, **values):
    _diagnostic(stage, reason, **values)
    return False


def _entry_diagnostics(candidate, session):
    from uploader.draft_evidence import field_match
    record = candidate if isinstance(candidate, dict) else {}
    account = getattr(session, 'account', {})
    expected = getattr(session, 'expected', {})
    saves = getattr(session, 'saves', [])
    same_id = [item for item in saves if isinstance(item, dict) and item.get('id') == record.get('id')]
    return {
        'candidate_dict': isinstance(candidate, dict),
        'candidate_id_type': type(record.get('id')).__name__,
        'candidate_id_present': bool(record.get('id')),
        'candidate_fresh': record.get('fresh') is True,
        'session_present': session is not None,
        'session_platform_matches': getattr(session, 'platform', None) == 'tencent',
        'session_open': not getattr(session, 'closed', True),
        'candidate_owner_type': type(record.get('owner')).__name__,
        'account_uid_type': type(account.get('uid')).__name__,
        'owner_exact': record.get('owner') == account.get('uid'),
        'candidate_fields_match': field_match('tencent', record, expected),
        'save_count': len(saves), 'same_id_count': len(same_id),
        'same_id_fresh_count': sum(item.get('fresh') is True for item in same_id),
        'same_id_owner_exact_count': sum(item.get('owner') == record.get('owner') for item in same_id),
        'same_id_fields_match_count': sum(field_match('tencent', item, expected) for item in same_id),
        'same_id_full_match_count': sum(item.get('fresh') is True and item.get('owner') == record.get('owner')
                                       and field_match('tencent', item, expected) for item in same_id),
    }


def _platform_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and parsed.netloc == 'channels.weixin.qq.com' and parsed.path.startswith('/platform/')
    except (TypeError, ValueError):
        return False


_LOOKUP = r"""({mode,id,caption,shortTitle}) => {
 let phase='document_scope';
 try {
  if(location.origin!=='https://channels.weixin.qq.com' || !location.pathname.startsWith('/platform/'))return {
   status:'invalid',diagnostic:{reason:'document_scope_invalid',origin_matches:location.origin==='https://channels.weixin.qq.com',
    platform_path:location.pathname.startsWith('/platform/'),micro_path:location.pathname.startsWith('/micro/content/')}};
  const queue=[document],roots=new Set(),nodes=new Set();
  while(queue.length){
   phase='root_scan';
   const root=queue.shift();if(roots.has(root))continue;roots.add(root);
   if(roots.size>64)return {status:'invalid',diagnostic:{reason:'root_limit',count:roots.size}};
   for(const el of root.querySelectorAll('*')){
    nodes.add(el);if(nodes.size>20000)return {status:'invalid',diagnostic:{reason:'node_limit',count:nodes.size}};
    if(el.shadowRoot)queue.push(el.shadowRoot);
    if(el.tagName==='IFRAME'){
     let owner;
     try{owner=el.contentDocument;}
     catch(error){return {status:'invalid',diagnostic:{reason:'iframe_scope_unavailable',
      error_type:['TypeError','SecurityError','InvalidStateError'].includes(error.name)?error.name:'OtherError'}};}
     if(owner){
      phase='iframe_scope';
      let origin,path;
      try{origin=owner.location.origin;path=owner.location.pathname;}
      catch(error){return {status:'invalid',diagnostic:{reason:'iframe_scope_unavailable',
       error_type:['TypeError','SecurityError','InvalidStateError'].includes(error.name)?error.name:'OtherError'}};}
      if(origin==='https://channels.weixin.qq.com' && typeof path==='string' && path.startsWith('/micro/content/'))queue.push(owner);
     }
    }
   }
  }
  phase='control_scan';
  const text=el=>(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
  const visible=el=>{
   // Vue's asynchronously mounted component can still own a Comment $el.
   // It is not a visible editor and must not turn normal mounting into an error.
   if(!el || el.nodeType!==1 || !el.isConnected || typeof el.getBoundingClientRect!=='function')return false;
   const view=el.ownerDocument?.defaultView;if(!view || typeof view.getComputedStyle!=='function')return false;
   const box=el.getBoundingClientRect(),style=view.getComputedStyle(el);
   return box.width>0 && box.height>0 && style.display!=='none' && style.visibility!=='hidden' && style.visibility!=='collapse';
  };
  const enabled=el=>visible(el) && !el.disabled && el.getAttribute('aria-disabled')!=='true' &&
   !el.closest('[inert]') && !el.classList.contains('disabled');
  const one=items=>items.length===1?{status:'unique',control:items[0]}:
   {status:items.length?'ambiguous':'none'};
  if(mode==='content' || mode==='draft'){
   // In the normal folded sidebar the label is hidden while its two official
   // outline/filled icons remain. Read its label from textContent, then bind
   // the submenu to this exact content group rather than another menu's text.
   const label=el=>(el.textContent||'').replace(/\s+/g,' ').trim();
   const content=el=>el.matches('a.finder-ui-desktop-menu__link.finder-ui-desktop-menu__sub__link') &&
    label(el)==='内容管理' && [...el.querySelectorAll('svg use')].some(use=>
     ['#icon-content-manager-outlined','#icon-content-manager-filled'].includes(
      use.getAttribute('href')||use.getAttribute('xlink:href')||use.getAttributeNS('http://www.w3.org/1999/xlink','href')));
   if(mode==='content')return one([...nodes].filter(el=>content(el) && enabled(el)));
   return one([...nodes].filter(el=>{
    if(!el.matches('a.finder-ui-desktop-menu__link') || label(el)!=='草稿箱' || !enabled(el))return false;
    const group=el.closest('.finder-ui-desktop-menu__sub__wrp');
    return !!group && [...group.querySelectorAll('a.finder-ui-desktop-menu__sub__link')].filter(content).length===1;
   }));
  }
  if(mode==='leave'){
   const physical=new Set();
   for(const el of nodes)if(el.tagName==='H3' && text(el)==='将此次编辑保留?' && visible(el)){
    const dialog=el.closest('.weui-desktop-dialog')||el.closest('.common-dialog');
    if(dialog && visible(dialog))physical.add(dialog);
   }
   const dialogs=[...physical];
   if(dialogs.length!==1)return {status:dialogs.length?'ambiguous':'none'};
   return one([...dialogs[0].querySelectorAll('button,a[role="button"],[role="button"]')]
    .filter(el=>text(el)==='不保存' && enabled(el)));
  }
  if(mode==='fields'){
   const editors=new Set();
   for(const el of nodes)if(el.matches('.input-editor[contenteditable]:not([contenteditable="false"]),.input-editor [contenteditable]:not([contenteditable="false"]),div[contenteditable="true"]') &&
    el.isContentEditable && visible(el))editors.add(el);
   const normalized=value=>String(value||'').replace(/\r\n/g,'\n').trim();
   const value=el=>{
    const children=[...el.children];
    return children.length && children.every(x=>x.tagName==='P')?
     children.map(x=>x.innerText.replace(/\n$/,'')).join('\n'):el.innerText;
   };
   if(editors.size!==1 || normalized(value([...editors][0]))!==normalized(caption))return {status:'none'};
   if(shortTitle){
    const titles=[...nodes].filter(el=>el.matches('input[placeholder*="短标题"]') && visible(el));
    if(titles.length!==1 || normalized(titles[0].value)!==normalized(shortTitle))return {status:'none'};
   }
   return {status:'unique'};
  }
  if(mode==='editor'){
   phase='editor_scan';
   if(location.pathname!=='/platform/post/create')return {status:'none'};
   const vms=new Set(),matches=new Set();
   for(const el of nodes){
    const chain=new Set();let depth=0;
    for(let vm=el.__vue__;vm;vm=vm.$parent){
     if(chain.has(vm))return {status:'invalid',diagnostic:{reason:'vue_parent_cycle',count:depth}};
     if(++depth>32)return {status:'invalid',diagnostic:{reason:'vue_depth_limit',count:depth}};chain.add(vm);
     if(vms.has(vm))break;vms.add(vm);if(vms.size>2048)return {status:'invalid',diagnostic:{reason:'vue_limit',count:vms.size}};
     const component=vm.$refs?.postCreate;
     if(component && !Array.isArray(component) && visible(component.$el) && vm.draftStore?.objectId===id)matches.add(vm);
    }
   }
   return {status:matches.size===1?'unique':matches.size?'ambiguous':'none'};
  }
  if(mode!=='row')return {status:'invalid'};
  phase='row_scan';
  const matches=[],visibleRows=[];
  for(const row of nodes){
   if(!row.matches('.post-draft-item') || !visible(row))continue;
   visibleRows.push(row);
   const vm=row.__vue__;
   if(!vm || !vm.post || vm.post.objectId!==id)continue;
   if(vm.$el!==row)return {status:'invalid'};
   matches.push(row);
  }
  if(matches.length!==1)return {status:matches.length?'ambiguous':visibleRows.length?'invalid':'none'};
  const row=matches[0],edits=[];
  for(const item of row.querySelectorAll('.opr > .opr-item')){
   const uses=[...item.querySelectorAll('svg.icon-edit use')].filter(use=>
    (use.getAttribute('href')||use.getAttribute('xlink:href')||use.getAttributeNS('http://www.w3.org/1999/xlink','href'))==='#icon-edit');
   if(uses.length===1 && !item.querySelector('.weui-icon-outlined-delete') && enabled(item))edits.push(item);
  }
  return edits.length===1?{status:'unique',control:edits[0]}:{status:edits.length?'ambiguous':'invalid'};
 }catch(error){return {status:'invalid',diagnostic:{reason:'lookup_exception',phase,
  error_type:['Error','TypeError','SecurityError','InvalidStateError','NotFoundError'].includes(error.name)?error.name:'OtherError'}};}
}"""


async def _lookup(page, mode, candidate, fields):
    handle = None
    try:
        if not _platform_url(page.url) or not _platform_url(page.main_frame.url):
            _diagnostic('lookup_' + mode, 'page_frame_scope_invalid',
                        page_platform=_platform_url(page.url), frame_platform=_platform_url(page.main_frame.url))
            return 'invalid', None
        handle = await asyncio.wait_for(page.main_frame.evaluate_handle(_LOOKUP, {
            'mode': mode, 'id': candidate['id'], 'caption': fields.get('caption', ''),
            'shortTitle': fields.get('short_title', '')}, isolated_context=False), 5)
        status_handle = await handle.get_property('status')
        try:
            state = await status_handle.json_value()
        finally:
            await status_handle.dispose()
        if state not in ('unique', 'none', 'ambiguous', 'invalid'):
            _diagnostic('lookup_' + mode, 'unexpected_verdict', verdict_type=type(state).__name__)
            return 'invalid', None
        if state in ('invalid', 'none'):
            detail = await handle.get_property('diagnostic')
            try:
                value = await detail.json_value()
            finally:
                await detail.dispose()
            value = value if isinstance(value, dict) else {}
            safe = {key: item for key, item in value.items()
                    if key in ('origin_matches', 'platform_path', 'micro_path', 'count') and type(item) in (bool, int)}
            if value.get('error_type') in ('Error', 'TypeError', 'SecurityError', 'InvalidStateError', 'NotFoundError', 'OtherError'):
                safe['error_type'] = value['error_type']
            if value.get('phase') in ('document_scope', 'root_scan', 'iframe_scope', 'control_scan', 'editor_scan', 'row_scan'):
                safe['phase'] = value['phase']
            reason = value.get('reason')
            if state == 'invalid' or reason is not None:
                _diagnostic('lookup_' + mode, reason if reason in ('document_scope_invalid', 'root_limit', 'node_limit',
                            'iframe_scope_unavailable', 'vue_parent_cycle', 'vue_depth_limit', 'vue_limit', 'lookup_exception')
                            else 'dom_scope_invalid', **safe)
        control = await handle.get_property('control')
        element = control.as_element()
        if element is None:
            await control.dispose()
        return state, element
    except Exception as error:
        # Only a destroyed navigation context is an editor-mounting retry.
        # Scope violations, malformed DOM, ambiguous editors and timeouts fail.
        if mode == 'editor' and any(message in str(error) for message in (
                'Execution context was destroyed', 'Cannot find context with specified id',
                'Cannot find context with specified ID', 'No execution context with given id')):
            _diagnostic('lookup_editor', 'navigation_context_unavailable', error_type=type(error).__name__)
            return 'none', None
        _diagnostic('lookup_' + mode, 'handle_read_failed', error_type=type(error).__name__)
        return 'invalid', None
    finally:
        if handle is not None:
            try:
                await asyncio.wait_for(handle.dispose(), 1)
            except Exception:
                pass


async def _click(control, candidate=None):
    try:
        if candidate is not None:
            valid = await asyncio.wait_for(control.evaluate("""(el,id)=>{
              const row=el.closest('.post-draft-item');
              return !!row && row.__vue__?.$el===row && row.__vue__?.post?.objectId===id &&
               el.matches('.opr > .opr-item') && !el.querySelector('.weui-icon-outlined-delete') &&
               [...row.querySelectorAll('.opr > .opr-item svg.icon-edit use')].filter(use=>
                (use.getAttribute('href')||use.getAttribute('xlink:href'))==='#icon-edit').length===1;
            }""", candidate['id'], isolated_context=False), 3)
            if valid is not True:
                return _failed('row_click', 'exact_row_revalidation_failed')
        await control.click(timeout=5000)
        return True
    except Exception as error:
        return _failed('row_click' if candidate is not None else 'normal_click', 'pointer_click_failed',
                       error_type=type(error).__name__)
    finally:
        try:
            await asyncio.wait_for(control.dispose(), 1)
        except Exception:
            pass


async def _hover(control):
    """Open the official content submenu through a real pointer hover."""
    try:
        await control.hover(timeout=5000)
        return True
    except Exception as error:
        return _failed('content_hover', 'pointer_hover_failed', error_type=type(error).__name__)
    finally:
        try:
            await asyncio.wait_for(control.dispose(), 1)
        except Exception:
            pass


async def _processing_account_verdict(session, expected):
    """Keep inspect_session's explicit bootstrap/logout distinction locally.

    DraftEvidenceSession.read_account intentionally projects four identity
    fields. During a normal page reload that projection loses the distinction
    between inspect_session's pending owner and visible login/logout/risk.
    Read the same trusted reader here; no old identity authorizes advancing.
    """
    try:
        reader = getattr(session, 'account_reader', None)
        if callable(reader):
            value = reader() if len(inspect.signature(reader).parameters) == 0 else reader(session.page, session.platform)
            raw = await asyncio.wait_for(value, 5) if inspect.isawaitable(value) else value
        else:
            raw = await asyncio.wait_for(session.read_account(), 5)
    except asyncio.TimeoutError:
        return 'rejected', 'account_reader_timeout'
    except Exception as error:
        reason = 'account_risk_rejected' if getattr(error, 'status', None) == 'risk_paused' else 'account_reader_failed'
        return 'rejected', reason
    if not isinstance(raw, dict):
        return 'rejected', 'account_result_invalid'
    if raw.get('account_conflict') is True:
        return 'changed', 'account_identity_changed'
    if raw.get('status') == 'risk_paused' or raw.get('diagnostic') in ('visible_login_marker', 'login_route', 'owner_logged_out', 'response_rejected', 'owner_not_unique', 'owner_response_invalid'):
        return 'rejected', 'account_not_verified'
    identity = {k: raw[k] for k in ('verified', 'identity', 'uid', 'platform') if k in raw}
    if identity == expected:
        return 'same', 'account_verified_same'
    if (raw.get('account_conflict') is True or raw.get('verified') is True or
            any(raw.get(k) is not None and raw[k] != expected.get(k) for k in ('uid', 'identity', 'platform'))):
        return 'changed', 'account_identity_changed'
    # Exact existing inspect_session contract: no owner yet, not a visible
    # login, invalid response, logout, risk pause or ambiguous owner. The
    # caller permits only a finite wait in the normal creator-list document.
    if (expected.get('verified') is True and raw.get('verified') is False and
            raw.get('platform') == 'tencent' and raw.get('status') == 'needs_reauthentication' and
            raw.get('reason') == '创作者身份尚未确认' and raw.get('account_conflict') is False and
            not raw.get('diagnostic') and not raw.get('uid') and not raw.get('identity')):
        return 'pending', 'account_initializing'
    return 'rejected', 'account_not_verified'


def _processing_read_budget(session):
    configured = getattr(session, 'tc_processing_read_timeout', None)
    value = float(configured if configured is not None else 90)
    if not math.isfinite(value):
        raise ValueError('Invalid TC processing wait budget')
    return min(900, max(.01, value)), configured is not None


def _processing_frozen_proof(session, candidate):
    from uploader.tc_processing import TCProcessingObserver
    observer = getattr(session, 'tc_processing_observer', None)
    frozen = getattr(observer, '_frozen_reopen', None)
    try:
        if (type(observer) is TCProcessingObserver and isinstance(frozen, dict) and
                frozen.get('candidate') == candidate and observer._frozen_events_current()):
            return observer, frozen
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return None


def _reopen_time_budget(session, candidate):
    budget, explicit = _processing_read_budget(session)
    # This is the enclosing ceiling. The inner wait grants 900 only after
    # normal same-ID handleStatus=1; unknown/missing rows still get 90.
    if not explicit and _processing_frozen_proof(session, candidate):
        budget = 900
    settle = min(15, max(.01, float(getattr(session, 'tc_account_reload_timeout', 8))))
    return 35 + settle + budget


async def wait_tc_processing_list(page, candidate, session, fields, edit):
    """Normal list reloads; wait for current identity, never inherit old auth."""
    if not hasattr(session, 'reads'):
        return edit  # Ordinary UI-only fixtures have no network observer.
    timeout, explicit_timeout = _processing_read_budget(session)
    interval = min(15, max(.05, float(getattr(session, 'tc_processing_reload_interval', 5))))
    account_timeout = min(15, max(.01, float(getattr(session, 'tc_account_reload_timeout', 8))))
    started = time.monotonic()
    deadline, next_reload = started + timeout, started + interval
    account_deadline = min(deadline, time.monotonic() + account_timeout)
    expected_account = copy.deepcopy(session.account)
    expected_task = copy.deepcopy(getattr(session, 'expected', None))
    last_account = None
    row_deadline = None
    list_mount_deadline = None
    reload_after = reload_response_deadline = None
    read_floor = session.reopen_at
    response_timeout = min(30, max(.01, float(getattr(session, 'tc_list_response_timeout', 20))))
    row_timeout = min(12, max(.01, float(getattr(session, 'tc_row_mount_timeout', 12))))
    proof = _processing_frozen_proof(session, candidate)
    frozen_snapshot = copy.deepcopy(proof[1]) if proof else None
    extended = False
    from uploader.tc_processing import TCProcessingObserver
    original_observer = getattr(session, 'tc_processing_observer', None)
    invalid_frozen = (type(original_observer) is TCProcessingObserver and
        getattr(original_observer, '_frozen_reopen', None) is not None and proof is None)

    def frozen_changed():
        if proof is None:
            return False
        current = _processing_frozen_proof(session, candidate)
        return (current is None or current[0] is not proof[0] or current[1] is not proof[1] or
                current[1] != frozen_snapshot)

    async def dispose(control):
        if control is not None:
            try:
                await asyncio.wait_for(control.dispose(), 1)
            except Exception:
                pass

    def scope_error():
        if getattr(page, '_sau_draft_evidence', None) is not session or session.closed:
            return 'session_changed_or_closed'
        if session.issues:
            return 'session_issues_present'
        if session.account != expected_account:
            return 'bound_account_changed'
        if getattr(session, 'expected', None) != expected_task:
            return 'processing_expected_changed'
        if invalid_frozen or frozen_changed():
            return 'processing_frozen_proof_changed'
        frame = getattr(page, 'main_frame', None)
        if (not _platform_url(page.url) or frame is not None and not _platform_url(frame.url) or
                urlsplit(page.url).path == '/platform/post/create'):
            return 'normal_list_scope_unavailable'
        return None

    try:
        while time.monotonic() < deadline:
            reason = scope_error()
            if reason:
                _diagnostic('processing_wait', reason, issue_count=len(session.issues))
                return None
            verdict, reason = await _processing_account_verdict(session, expected_account)
            changed = scope_error()
            if changed:
                _diagnostic('processing_wait', changed, issue_count=len(session.issues))
                return None
            if verdict != last_account:
                _diagnostic('processing_wait', reason)
                if verdict == 'pending':
                    from uploader.draft_progress import emit as emit_progress
                    emit_progress('verify', 'running', detail='草稿已保存，列表重新加载后正在确认当前账号')
                last_account = verdict
            if verdict == 'pending':
                await dispose(edit); edit = None
                if time.monotonic() >= account_deadline:
                    _diagnostic('processing_wait', 'account_initialization_deadline_expired')
                    return None
                await asyncio.sleep(.1)
                continue
            if verdict != 'same':
                return None
            await session.drain()
            changed = scope_error()
            if changed:
                _diagnostic('processing_wait', changed, issue_count=len(session.issues))
                return None
            rows = [x for x in session.reads if x.get('id') == candidate['id'] and x.get('owner') == candidate['owner'] and
                    x.get('tc_normal_read') == 'get_draft_list' and x.get('request_method') == 'POST' and
                    all(type(x.get(k)) in (int, float) and math.isfinite(x[k]) for k in ('request_at', 'observed_at')) and
                    read_floor <= x['request_at'] <= x['observed_at'] <= time.time()]
            if reload_after is not None:
                # drain() cannot wait for a POST whose response headers have
                # not arrived yet. Only this reload's normal same-row read
                # releases the barrier; a late read from an older request does not.
                current_reads = [x for x in rows if x['request_at'] >= reload_after]
                if not current_reads:
                    state, current = await _lookup(page, 'row', candidate, fields)
                    unexpected_control = state == 'none' and current is not None
                    await dispose(current)
                    current_fields, unused = await _lookup(page, 'fields', candidate, fields)
                    await dispose(unused)
                    if scope_error() or state not in ('unique', 'none') or unexpected_control or current_fields != 'none':
                        _diagnostic('processing_wait', scope_error() or 'normal_list_scope_unavailable')
                        return None
                    if time.monotonic() >= reload_response_deadline:
                        _diagnostic('processing_wait', 'normal_list_response_deadline_expired')
                        return None
                    await asyncio.sleep(.1)
                    continue
                reload_after = reload_response_deadline = None
                next_reload = time.monotonic() + interval
            row = max(rows, key=lambda x: (x['request_at'], x['observed_at'])) if rows else None
            status = row.get('tc_handle_status') if row else None
            if type(status) is int and status == 1 and not explicit_timeout and proof is not None:
                from uploader.draft_evidence import field_match
                if field_match('tencent', row, session.expected):
                    deadline = started + 900
                    if not extended:
                        extended = True
                        _diagnostic('processing_wait', 'saved_platform_processing', max_wait_seconds=900)
                        from uploader.draft_progress import emit as emit_progress
                        emit_progress('verify', 'running', detail='保存已完成，平台正在加工本次视频，等待草稿可重开核验，最多15分钟')
            if type(status) is int and status == 2:
                if edit is None:
                    state, edit = await _lookup(page, 'row', candidate, fields)
                    if state == 'none' and edit is None:
                        if row_deadline is None:
                            row_deadline = min(deadline, time.monotonic() + row_timeout)
                            _diagnostic('processing_wait', 'successful_row_mount_pending')
                            from uploader.draft_progress import emit as emit_progress
                            emit_progress('verify', 'running', detail='平台已处理完成，正在加载本次草稿的编辑入口')
                        if time.monotonic() >= row_deadline:
                            _diagnostic('processing_wait', 'successful_row_mount_deadline_expired')
                            return None
                        await asyncio.sleep(.1)
                        continue
                    if state != 'unique' or edit is None:
                        _diagnostic('processing_wait', 'successful_row_control_unavailable', verdict=state)
                        return None
                final, reason = await _processing_account_verdict(session, expected_account)
                if scope_error() or final != 'same':
                    _diagnostic('processing_wait', scope_error() or reason)
                    return None
                current, edit = edit, None
                return current
            row_deadline = None
            if status in (3, 4, 5):
                _diagnostic('processing_wait', 'server_processing_failed', handle_status=status)
                return None
            await dispose(edit); edit = None
            if time.monotonic() >= next_reload:
                state, current = await _lookup(page, 'row', candidate, fields)
                unexpected_control = state == 'none' and current is not None
                await dispose(current)
                current_fields, unused = await _lookup(page, 'fields', candidate, fields)
                await dispose(unused)
                if scope_error() or state not in ('unique', 'none') or unexpected_control or current_fields != 'none':
                    _diagnostic('processing_wait', scope_error() or 'normal_list_scope_unavailable')
                    return None
                if state == 'none':
                    # The normal POST may outlast the five-second reload cadence.
                    # Wait for its row before considering another reload; do not
                    # treat an empty mounting list as a different record.
                    if list_mount_deadline is None:
                        list_mount_deadline = min(deadline, time.monotonic() + row_timeout)
                        _diagnostic('processing_wait', 'normal_row_mount_pending')
                        from uploader.draft_progress import emit as emit_progress
                        emit_progress('verify', 'running', detail='平台草稿列表仍在加载，正在等待本次记录显示')
                    if time.monotonic() >= list_mount_deadline:
                        _diagnostic('processing_wait', 'normal_row_mount_deadline_expired')
                        return None
                    await asyncio.sleep(.1)
                    continue
                list_mount_deadline = None
                _diagnostic('processing_wait', 'normal_list_reload', handle_status=status if status in (1, 2, 3, 4, 5) else None)
                from uploader.draft_progress import emit as emit_progress
                emit_progress('verify', 'running', detail='草稿已保存，等待平台完成媒体处理，正在重新读取草稿列表')
                reload_after = read_floor = time.time()
                await page.reload(wait_until='domcontentloaded', timeout=20000)
                reload_response_deadline = min(deadline, time.monotonic() + response_timeout)
                next_reload = time.monotonic() + interval
                account_deadline = min(deadline, time.monotonic() + account_timeout)
                last_account = None
            await asyncio.sleep(.05)
        _diagnostic('processing_wait', 'processing_deadline_expired')
        return None
    finally:
        await dispose(edit)


async def reopen_tc_draft(page, candidate) -> bool:
    """Click the unique exact-ID draft's edit operation and attest UI binding.

    A leave dialog's 不保存 action is permitted only for an already observed
    fresh save and an unchanged live description/short title. No save is retried.
    The caller still verifies server readback, account, all media and fields.
    """
    from uploader.draft_evidence import field_match
    session = getattr(page, '_sau_draft_evidence', None)
    if (not isinstance(candidate, dict) or not isinstance(candidate.get('id'), str) or
            not candidate['id'] or session is None or getattr(session, 'platform', None) != 'tencent' or
            getattr(session, 'closed', True) or candidate.get('fresh') is not True or
            candidate.get('owner') != session.account.get('uid') or
            not field_match('tencent', candidate, session.expected) or
            not any(item.get('id') == candidate['id'] and item.get('fresh') is True and
                    item.get('owner') == candidate['owner'] and field_match('tencent', item, session.expected)
                    for item in session.saves)):
        return _failed('entry', 'attestation_failed', **_entry_diagnostics(candidate, session))
    fields = session.expected.get('platform_fields', {})
    _diagnostic('entry', 'fresh_saved_record_attested')
    stage = 'initial_lookup'
    try:
        async with asyncio.timeout(_reopen_time_budget(session, candidate)):
            unchanged, _ = await _lookup(page, 'fields', candidate, fields)
            state, edit = await _lookup(page, 'row', candidate, fields)
            _diagnostic('initial_lookup', 'verdicts', fields=unchanged, row=state)
            if state in ('invalid', 'ambiguous'):
                return _failed('initial_row', 'row_rejected', verdict=state)
            if state == 'none':
                state, navigation = await _lookup(page, 'draft', candidate, fields)
                if state == 'none':
                    state, menu = await _lookup(page, 'content', candidate, fields)
                    if state != 'unique' or menu is None:
                        return _failed('content_lookup', 'unique_control_unavailable', verdict=state,
                                       has_control=menu is not None)
                    stage = 'content_hover'
                    if not await _hover(menu):
                        return False
                    stage = 'draft_menu_wait'
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        state, navigation = await _lookup(page, 'draft', candidate, fields)
                        if state != 'none':
                            break
                        await asyncio.sleep(.1)
                if state != 'unique' or navigation is None:
                    return _failed('draft_lookup', 'unique_control_unavailable', verdict=state,
                                   has_control=navigation is not None)
                stage = 'draft_click'
                if not await _click(navigation):
                    return False
                stage = 'draft_rows_wait'
                deadline, discarded = time.monotonic() + 10, False
                while time.monotonic() < deadline:
                    state, edit = await _lookup(page, 'row', candidate, fields)
                    if state != 'none':
                        break
                    leave, button = await _lookup(page, 'leave', candidate, fields)
                    if leave in ('invalid', 'ambiguous'):
                        return _failed('leave_lookup', 'dialog_rejected', verdict=leave)
                    if leave == 'unique':
                        # The user may edit while the hover/menu navigation is
                        # pending. Attest the live fields again at the dialog.
                        current, _ = await _lookup(page, 'fields', candidate, fields)
                        if (discarded or unchanged != 'unique' or current != 'unique' or button is None or
                                getattr(page, '_sau_draft_evidence', None) is not session or session.closed):
                            if button is not None:
                                await button.dispose()
                            return _failed('leave_guard', 'unchanged_saved_editor_not_attested',
                                           initial_fields=unchanged, current_fields=current, already_discarded=discarded,
                                           session_same=getattr(page, '_sau_draft_evidence', None) is session,
                                           session_open=not session.closed)
                        if not await _click(button):
                            return False
                        discarded = True
                    await asyncio.sleep(.1)
            if state != 'unique' or edit is None:
                return _failed('saved_row_lookup', 'unique_control_unavailable', verdict=state,
                               has_control=edit is not None)
            stage = 'processing_list_wait'
            edit = await wait_tc_processing_list(page, candidate, session, fields, edit)
            if edit is None:
                return _failed(stage, 'successful_normal_read_unavailable')
            stage = 'saved_row_click'
            if not await _click(edit, candidate):
                return False
            stage = 'editor_binding_wait'
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                state, _ = await _lookup(page, 'editor', candidate, fields)
                if state == 'unique':
                    if getattr(page, '_sau_draft_evidence', None) is not session or session.closed:
                        return _failed('editor_binding', 'session_changed_or_closed')
                    session.normal_ui_binding = {'id': candidate['id'], 'owner': candidate['owner'],
                                                 'control': 'post_draft_item_edit'}
                    _diagnostic('editor_binding', 'normal_edit_attested')
                    return True
                if state in ('ambiguous', 'invalid'):
                    return _failed('editor_binding', 'editor_rejected', verdict=state)
                await asyncio.sleep(.1)
    except Exception as error:
        return _failed(stage, 'operation_failed', error_type=type(error).__name__)
    return _failed('editor_binding_wait', 'editor_deadline_expired')
