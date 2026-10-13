"""Best-effort, content-free progress for one explicitly configured draft task.

No timer estimates or user/account fields enter this stdout protocol. Ordinary
uploader and login invocations are silent unless the draft runner configures it.
"""
import json
import os
from contextvars import ContextVar
from uploader.progress_contract import OPERATIONS, valid_plan, valid_percent

PLATFORMS = frozenset(('douyin', 'xiaohongshu', 'tencent', 'bilibili', 'kuaishou'))
STAGES = ('session', 'video', 'upload', 'content', 'cover', 'save', 'verify')
STATES = frozenset(('running', 'completed', 'skipped', 'failed'))
_configuration = ContextVar('draft_progress_configuration', default=None)
_active = ContextVar('draft_progress_active', default=())
_LABELS = {
    'session': '连接浏览器并确认当前账号',
    'video': '将视频导入平台编辑器',
    'upload': '等待平台完成视频上传和处理',
    'content': '填写标题、描述和话题',
    'cover': '导入并确认自定义封面',
    'save': '执行草稿保存',
    'verify': '核验保存响应、重新打开后的内容与账号',
}


def configure(platform):
    """Enable only a task with an exact, trusted platform/environment binding."""
    job_id = os.environ.get('SAU_PROGRESS_JOB_ID', '')
    configured_platform = os.environ.get('SAU_PROGRESS_PLATFORM', '')
    # IDs are opaque identifiers, never payload text or arbitrary paths.
    valid_id = isinstance(job_id, str) and 0 < len(job_id) <= 80 and all(
        character.isascii() and (character.isalnum() or character in '-_') for character in job_id)
    configuration = (job_id, platform) if isinstance(platform, str) and platform in PLATFORMS and configured_platform == platform and valid_id else None
    _configuration.set(configuration)
    _active.set(())
    return configuration is not None


def emit(stage, state, **fields):
    """Write one allowed event; diagnostic output must never break the task."""
    configuration = _configuration.get()
    if configuration is None or not isinstance(stage, str) or not isinstance(state, str) or stage not in STAGES or state not in STATES:
        return False
    job_id, platform = configuration
    detail = _LABELS[stage]
    if state == 'completed':
        detail += '已完成'
    elif state == 'failed':
        detail += '未完成，已保留现场'
    elif state == 'skipped':
        detail = '未提供自定义封面，使用平台默认封面' if stage == 'cover' else '此步骤不适用'
    event = dict(v=1, job_id=job_id, platform=platform, stage=stage, state=state, detail=detail)
    event.update(fields)
    try:
        print('DRAFT_PROGRESS=' + json.dumps(event, ensure_ascii=False, separators=(',', ':')), flush=True)
    except Exception:
        return False
    return True


def set_plan(stage, operations):
    if not valid_plan(stage, operations):
        return False
    return emit(stage, 'running', plan=list(operations))


def operation(stage, name, state='running', percent=None):
    if (not isinstance(stage, str) or stage not in OPERATIONS or not isinstance(name, str)
            or name not in OPERATIONS[stage] or not isinstance(state, str) or state not in STATES
            or (percent is not None and (state != 'running' or not valid_percent(percent)))):
        return False
    fields = dict(operation=name, operation_state=state)
    if percent is not None:
        fields['operation_percent'] = round(percent, 2)
    return emit(stage, 'running', **fields)


class ProgressPart:
    def __init__(self, stage, name):
        self.stage, self.name = stage, name

    def __enter__(self):
        operation(self.stage, self.name)
        return self

    def __exit__(self, exception_type, exception, traceback):
        operation(self.stage, self.name, 'failed' if exception_type else 'completed')
        return False


def part(stage, name):
    return ProgressPart(stage, name)


class ProgressStep:
    """Complete only after the operation exits; failures preserve the exception.

    An early ``complete`` can close a connection/authentication scope before the
    borrowed browser is released. Nested instances of the same stage are silent
    so an inner readiness check cannot finish its outer cover/content operation.
    """
    def __init__(self, stage, *, auto_complete=True, operations=None):
        self.stage = stage
        self.operations = operations
        self.auto_complete = auto_complete
        self.closed = False
        self.owns_event = False
        self.token = None

    def __enter__(self):
        active = _active.get()
        self.owns_event = self.stage not in active
        if self.owns_event:
            self.token = _active.set(active + (self.stage,))
            emit(self.stage, 'running')
            if self.operations is not None:
                set_plan(self.stage, self.operations)
        return self

    def finish(self, state):
        if self.closed:
            return
        self.closed = True
        if self.owns_event:
            if state is not None:
                emit(self.stage, state)
            if self.token is not None:
                _active.reset(self.token)

    def complete(self):
        self.finish('completed')

    def fail(self):
        self.finish('failed')

    def pause(self):
        """Leave the stage unfinished between two real operations, without nesting."""
        self.finish(None)

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is not None:
            self.fail()
        elif self.auto_complete:
            self.complete()
        elif not self.closed:
            self.fail()
        return False


def step(stage, *, auto_complete=True, operations=None):
    return ProgressStep(stage, auto_complete=auto_complete, operations=operations)
