"""Closed names for observational workflow progress, without user content."""
OPERATIONS = {
    'session': {'preflight': '检查素材与文字限制', 'media_probe': '读取视频与图片',
                'browser_connect': '连接内嵌浏览器', 'creator_open': '打开创作页面',
                'account_check': '验证当前登录', 'capability_check': '检查账号与页面权限',
                'evidence_init': '准备草稿核验'},
    'video': {'locate': '定位视频导入位置', 'select': '选择并导入视频', 'bind': '确认视频已进入编辑器'},
    'upload': {'transfer': '等待视频上传', 'processing': '等待平台处理并确认视频'},
    'content': {'title': '填写标题', 'description': '填写文案', 'topics': '填写话题', 'readback': '核对填写内容'},
    'cover': {'editor': '打开封面编辑器', 'landscape': '设置横版封面', 'portrait': '设置竖版封面',
              'confirm': '确认封面', 'readback': '核对封面'},
    'save': {'guard': '检查保存条件', 'action': '执行保存操作', 'response': '等待保存记录'},
    'verify': {'response': '核对草稿记录', 'reopen': '重新打开草稿', 'content': '核对视频、封面与文字', 'owner': '核对所属账号'},
}


def valid_plan(stage, value):
    return (isinstance(value, list) and 0 < len(value) <= len(OPERATIONS.get(stage, {}))
            and all(isinstance(name, str) and name in OPERATIONS[stage] for name in value)
            and len(set(value)) == len(value))


def valid_percent(value):
    import math
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def public_operations(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for stage, row in value.items():
        if not isinstance(stage, str) or stage not in OPERATIONS or not isinstance(row, dict) or not valid_plan(stage, row.get('plan')):
            continue
        plan = row['plan'][:]
        completed = [name for name in plan if isinstance(row.get('completed'), list) and name in row['completed']]
        skipped = [name for name in plan if name not in completed and isinstance(row.get('skipped'), list) and name in row['skipped']]
        current = row.get('current') if row.get('current') in plan else None
        state = row.get('state') if row.get('state') in ('running', 'completed', 'skipped', 'failed') else None
        clean = dict(plan=plan, completed=completed, skipped=skipped, current=current, state=state)
        if current and current not in completed + skipped and valid_percent(row.get('percent')):
            clean['percent'] = row['percent']
        result[stage] = clean
    return result
