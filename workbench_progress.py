"""Bound, controlled worker progress; never a draft-success receipt.

Percentages describe completed workflow operations. A separately observed
upload percentage never proves persistence or a successful draft receipt.
"""
import json
import math
from uploader.progress_contract import OPERATIONS, valid_plan, valid_percent, public_operations


PREFIX = 'DRAFT_PROGRESS='
PLATFORMS = frozenset(('douyin', 'xiaohongshu', 'tencent', 'bilibili', 'kuaishou'))
STAGES = ('session', 'video', 'upload', 'content', 'cover', 'save', 'verify')
LABELS = {'session': '确认登录', 'video': '导入视频', 'upload': '等待视频处理',
          'content': '填写标题、文案和话题', 'cover': '设置封面',
          'save': '保存草稿', 'verify': '核验草稿'}
STATES = frozenset(('running', 'completed', 'skipped', 'failed'))
PUBLIC_STATES = STATES | {'cancelled', 'unknown', 'unverified'}
DETAILS = frozenset(('正在检查登录状态', '等待扫码登录', '正在选择视频',
                    '等待平台处理视频', '正在填写标题、描述和话题', '正在设置封面',
                    '等待平台保存草稿', '正在重开核验草稿', '该步骤无需执行',
                    '该步骤未完成'))
# Exact content-free phrases emitted by uploader.draft_progress. Keep a closed
# set rather than accepting uploader exception text or arbitrary JSON content.
_EMITTER_LABELS = ('连接浏览器并确认当前账号', '将视频导入平台编辑器',
                   '等待平台完成视频上传和处理', '填写标题、描述和话题',
                   '导入并确认自定义封面', '执行草稿保存',
                   '核验保存响应、重新打开后的内容与账号')
DETAILS |= frozenset(label + suffix for label in _EMITTER_LABELS
                     for suffix in ('', '已完成', '未完成，已保留现场'))
DETAILS |= {'未提供自定义封面，使用平台默认封面', '此步骤不适用'}
STATUS_MESSAGES = {
    'published_submission_verified': '投稿已提交，等待平台审核',
    'published_unverified': '已尝试投稿，回执待确认；不会自动再次提交',
    'publication_blocked': '提交前核验未通过，页面已保留',
    'pending': '等待执行', 'running': '正在处理当前平台',
    'login_verified': '登录已在现场验证',
    'local_draft_verified': '本地草稿已重开核验，保存在当前浏览器',
    'platform_draft_verified': '平台草稿已重开核验',
    'platform_snapshot_verified': '平台暂存快照已重开核验',
    'saved_unverified': '已尝试保存，核验证据不足；不会自动重传',
    'result_unknown': '结果待确认；不会自动重传',
    'risk_paused': '平台出现风险提示，已暂停此账号的自动处理',
    'platform_restricted': '平台权限受限，已停止自动处理',
    'adapter_mismatch': '页面控件待适配，现场已保留',
    'needs_reauthentication': '登录未通过验证，请在平台页面确认',
    'account_mismatch': '当前账号与登录记录不同，已停止',
    'failed': '执行失败，当前步骤已保留', 'cancelled': '已停止，当前步骤已保留',
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate progress field')
        result[key] = value
    return result


def valid_job_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 80 and all(
        ch.isascii() and (ch.isalnum() or ch in '_-') for ch in value)


def parse_progress(line, job_id, platform):
    """Return only an exact, bounded event belonging to this worker."""
    if not isinstance(line, str) or not line.startswith(PREFIX) or len(line) > 2048:
        return None
    try:
        event = json.loads(line[len(PREFIX):], object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict):
        return None
    required = {'v', 'job_id', 'platform', 'stage', 'state'}
    if not required <= event.keys() or not event.keys() <= required | {'detail', 'plan', 'operation', 'operation_state', 'operation_percent'}:
        return None
    if type(event['v']) is not int or event['v'] != 1:
        return None
    if not valid_job_id(event['job_id']) or not isinstance(event['platform'], str) or event['platform'] not in PLATFORMS:
        return None
    if event['job_id'] != job_id or event['platform'] != platform:
        return None
    if not isinstance(event['stage'], str) or event['stage'] not in LABELS:
        return None
    if not isinstance(event['state'], str) or event['state'] not in STATES:
        return None
    if 'detail' in event and (not isinstance(event['detail'], str) or event['detail'] not in DETAILS):
        return None
    stage = event['stage']
    if 'plan' in event and (event['state'] != 'running' or not valid_plan(stage, event['plan'])):
        return None
    if 'operation' in event:
        if (event['state'] != 'running' or not isinstance(event['operation'], str)
                or event['operation'] not in OPERATIONS[stage] or not isinstance(event.get('operation_state'), str) or event['operation_state'] not in STATES):
            return None
    elif 'operation_state' in event or 'operation_percent' in event:
        return None
    if 'operation_percent' in event and (event.get('operation_state') != 'running' or not valid_percent(event['operation_percent'])):
        return None
    if 'plan' in event and 'operation' in event:
        return None
    return event


def _stage_list(value):
    if not isinstance(value, list):
        return []
    return [stage for stage in STAGES if stage in value]


def _timestamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def public_progress(value):
    """Filter both newly written and legacy/corrupt values before the UI."""
    if not isinstance(value, dict) or not isinstance(value.get('stage'), str) or value['stage'] not in LABELS:
        return None
    if not isinstance(value.get('state'), str) or value['state'] not in PUBLIC_STATES:
        return None
    started, updated = _timestamp(value.get('started_at')), _timestamp(value.get('updated_at'))
    if started is None or updated is None:
        return None
    result = {'stage': value['stage'], 'label': LABELS[value['stage']], 'state': value['state'],
            'started_at': started, 'updated_at': updated,
            'completed': _stage_list(value.get('completed')),
            'skipped': _stage_list(value.get('skipped'))}
    operations = public_operations(value.get('operations'))
    if operations:
        result['operations'] = operations
    active = value.get('active_stages')
    if isinstance(active, list) and len(active) > 1:
        result['active_stages'] = [stage for stage in STAGES if stage in active]
    return result


def apply_progress(item, event, now):
    """Update the current running item's steps without touching its status.

    Events reopening an already completed/skipped phase are stale. A repeated
    running phase keeps its original start time so a long wait remains visible.
    """
    if item.get('status') != 'running':
        return False
    stage, state = event['stage'], event['state']
    previous = public_progress(item.get('progress'))
    completed = previous['completed'][:] if previous else []
    skipped = previous['skipped'][:] if previous else []
    if stage in completed + skipped:
        return False
    if previous and previous['state'] == 'failed':
        return False
    operations = public_operations(previous.get('operations')) if previous else {}
    raw = item.get('progress', {})
    stored_activity = raw.get('stage_activity', {})
    if not isinstance(stored_activity, dict): stored_activity = {}
    activity = {key: value for key, value in stored_activity.items()
                if key in STAGES and isinstance(value, dict) and value.get('state') == 'running'
                and _timestamp(value.get('started_at')) is not None}
    if not activity and previous and previous['state'] == 'running':
        activity[previous['stage']] = {'state': 'running', 'started_at': previous['started_at']}
    if 'plan' in event:
        if stage in operations and operations[stage]['plan'] != event['plan']:
            return False
        operations.setdefault(stage, dict(plan=event['plan'][:], completed=[], skipped=[], current=None, state=None))
    if 'operation' in event:
        row = operations.setdefault(stage, dict(plan=list(OPERATIONS[stage]), completed=[], skipped=[], current=None, state=None))
        name, op_state = event['operation'], event['operation_state']
        if name not in row['plan'] or name in row['completed'] + row['skipped']:
            return False
        if row['current'] == name and row['state'] == 'failed':
            return False
        percent = event.get('operation_percent')
        if percent is not None and row['current'] == name and percent < row.get('percent', 0):
            return False
        if row['current'] != name:
            row.pop('percent', None)
        row.update(current=name, state=op_state)
        if percent is not None:
            row['percent'] = percent
        if op_state in ('completed', 'skipped'):
            row[op_state].append(name)
            row.pop('percent', None)
    started = activity.get(stage, {}).get('started_at', previous['started_at'] if previous and previous['stage'] == stage else now)
    if state == 'running':
        activity[stage] = {'state': 'running', 'started_at': started}
    else:
        activity.pop(stage, None)
    if state == 'completed':
        completed.append(stage)
    elif state == 'skipped':
        skipped.append(stage)
    foreground = max(activity, key=STAGES.index) if activity and state != 'failed' else stage
    foreground_state = 'running' if foreground in activity else state
    foreground_started = activity[foreground]['started_at'] if foreground in activity else started
    item['progress'] = {'stage': foreground, 'label': LABELS[foreground], 'state': foreground_state,
                        'started_at': foreground_started, 'updated_at': now,
                        'completed': _stage_list(completed), 'skipped': _stage_list(skipped)}
    if activity:
        item['progress']['stage_activity'] = activity
    if len(activity) > 1:
        item['progress']['active_stages'] = [key for key in STAGES if key in activity]
    if operations:
        item['progress']['operations'] = operations
    if event.get('detail') and foreground == stage:
        item['progress']['detail'] = event['detail']
    elif previous and foreground == previous['stage'] and raw.get('detail') in DETAILS:
        item['progress']['detail'] = raw['detail']
    return True


def finish_progress(item, status, now):
    """Keep the last known phase visible when execution stops without proof."""
    state = {'cancelled': 'cancelled', 'failed': 'failed', 'result_unknown': 'unknown',
             'saved_unverified': 'unverified', 'published_unverified': 'unverified',
             'publication_blocked': 'failed', 'risk_paused': 'failed',
             'platform_restricted': 'failed', 'adapter_mismatch': 'failed',
             'needs_reauthentication': 'failed', 'account_mismatch': 'failed'}.get(status)
    progress = public_progress(item.get('progress'))
    if state is not None and progress is not None:
        progress.pop('active_stages', None)
        progress.update(state=state, updated_at=now)
        item['progress'] = progress


def progress_summary(job, platforms):
    """Read-only endpoint shape, excluding account, media and raw messages."""
    if not isinstance(job, dict) or not job:
        return {'available': True, 'job': None}
    results = job.get('results', [])
    if not isinstance(results, list):
        return {'available': True, 'job': None}
    valid = [(index, item) for index, item in enumerate(results) if isinstance(item, dict) and isinstance(item.get('platform'), str) and item['platform'] in platforms]
    if not valid:
        return {'available': True, 'job': None}
    running = [entry for entry in valid if entry[1].get('status') == 'running']
    started = [entry for entry in valid if _timestamp(entry[1].get('started_at')) is not None]
    index, item = running[0] if running else max(started, key=lambda entry: (entry[1]['started_at'], entry[0])) if started else valid[0]
    status = item.get('status') if isinstance(item.get('status'), str) and item['status'] in STATUS_MESSAGES else 'result_unknown'
    progress = public_progress(item.get('progress'))
    message = STATUS_MESSAGES[status]
    if status == 'running' and progress:
        detail = item.get('progress', {}).get('detail')
        message = detail if isinstance(detail, str) and detail in DETAILS else LABELS[progress['stage']]
    # IDs are internal task identifiers; never expose an arbitrary stored object.
    job_id = job.get('id')
    if not valid_job_id(job_id):
        job_id = None
    return {'available': True, 'job': {
        'id': job_id, 'action': job.get('action') if job.get('action') in ('login', 'draft', 'publish') else None,
        'active': job.get('active') is True, 'platform': item['platform'],
        'platform_name': platforms[item['platform']], 'platform_index': index + 1,
        'platform_count': len(results), 'status': status, 'message': message, 'progress': progress}}
