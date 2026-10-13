"""Web video editor limits, verified from current official clients on 2026-10-03.

Sources and competing contexts: research/平台视频字段限制-20261003.md.
Never silently truncate. Count the text actually sent, including topic syntax.
"""
import math
import html
import re
from text_import import normalize_tags, caption_with_topics


TEXT_LIMITS = {
    'douyin': {'title_max':30, 'title_count':'utf16', 'caption_max':1000, 'caption_count':'utf16', 'tags_max':5},
    'xiaohongshu': {'title_max':20, 'title_count':'xhs_title', 'description_max':1000,
                   'description_count':'xhs_body', 'tags_max':10, 'new_tag_max':30, 'new_tag_count':'xhs_topic'},
    'tencent': {'caption_max':2000, 'caption_count':'latin1_width', 'short_title_min':6,
               'short_title_max':16, 'short_title_count':'utf16'},
    'bilibili': {'title_max':80, 'title_count':'utf16', 'description_max':2000,
                 'description_count':'utf16', 'tags_max':10, 'tag_max':20, 'tag_count':'utf16'},
    'kuaishou': {'caption_max':500, 'caption_count':'utf16', 'tags_max':4},
    # The new video portals are not yet bound to authenticated editors. Do not
    # substitute article limits or old third-party numbers for current video rules.
    'toutiao': {},
    'xigua': {},
}


def utf16_units(value):
    data = str(value).encode('utf-16-le', errors='surrogatepass')
    return [int.from_bytes(data[i:i+2], 'little') for i in range(0,len(data),2)]


def measure(value, mode='utf16'):
    text = str(value or '')
    if mode == 'xhs_body':
        # Plain-text input: the editor removes line breaks and BOM for its
        # counter. Literal <...> in user text remains literal, not HTML.
        text = html.escape(re.sub(r'[\r\n\ufeff]', '', text), quote=False)
    units = utf16_units(text)
    if mode == 'xhs_title':
        return math.ceil(sum(2 if n>127 or n==94 else 1 for n in units)/2)
    if mode == 'latin1_width':
        return sum(1 if n<=255 else 2 for n in units)
    if mode == 'xhs_topic':
        return sum(1 if n<=127 else 2 if 0x4e00<=n<=0x9fff else 4 for n in units)
    return len(units)


def text_fields(platform, payload):
    override = payload.get('platform_overrides', {}).get(platform, {})
    if not isinstance(override, dict):
        raise ValueError('平台单独设置需要是对象。')
    fields = {k:override[k] if k in override else payload.get(k, [] if k=='tags' else '')
              for k in ('title','description','tags','short_title')}
    if not isinstance(fields['tags'], list) or any(not isinstance(x,str) for x in fields['tags']):
        raise ValueError('标签需要是文字数组。')
    fields.update(title=str(fields['title'] or '').strip(), description=str(fields['description'] or ''),
                  short_title=str(fields['short_title'] or '').strip(), tags=normalize_tags(fields['tags']))
    if platform in ('douyin','kuaishou','tencent'):
        fields['caption'] = caption_with_topics(fields['title'] if platform != 'douyin' else '',
                                                fields['description'], fields['tags'])
    return fields


def check_fields(platform, fields, capabilities=None):
    limits={**TEXT_LIMITS[platform]}
    # Only a current page's *stricter* dynamic rule can reduce a verified default.
    for key in ('tags_max','caption_max','title_max','description_max'):
        value=(capabilities or {}).get(key)
        if isinstance(value,int) and not isinstance(value,bool) and 0<value<limits.get(key,float('inf')):
            limits[key]=value
            if key != 'tags_max':
                limits.setdefault(key.replace('_max','_count'),'utf16')
    errors=[];metrics={}
    def error(field, message):
        errors.append({'platform':platform,'field':field,'message':message})
    if not fields['title']:
        error('title','标题不能为空。')
    if limits.get('title_max'):
        n=measure(fields['title'],limits['title_count']);maximum=limits['title_max']
        metrics['title']={'used':n,'max':maximum,'text':f'{n}/{maximum} 字（平台计数）'}
        if n>maximum:error('title',f'标题 {n}/{maximum} 字（平台计数），请改写此平台标题。')
    else:
        metrics['title']={'used':measure(fields['title']), 'max':None,
            'text':'视频标题额度待现场确认'} if platform in ('toutiao','xigua') else {
            'text':'标题将合并到作品描述，计入下方总额度'}
    if limits.get('caption_max'):
        content=fields.get('caption','');n=measure(content,limits['caption_count']);maximum=limits['caption_max']
        suffix='加权单位，含标题和话题' if platform=='tencent' else '字符，含话题'+('与标题' if platform=='kuaishou' else '')
        metrics['description']={'used':n,'max':maximum,'text':f'{n}/{maximum} {suffix}'}
        if n>maximum:error('description',f'最终作品描述 {n}/{maximum} {suffix}，请减少文案或话题。')
    elif limits.get('description_max'):
        content=fields['description'];mode=limits['description_count'];maximum=limits['description_max']
        if platform=='xiaohongshu':
            # Topic nodes count in the body too. Spaces between them remain;
            # the first separator is a line break when body is non-empty.
            content += ('\n' if content else ' ')+' '.join('#'+t for t in fields['tags']) if fields['tags'] else ''
        n=measure(content,mode)
        metrics['description']={'used':n,'max':maximum,'text':f'{n}/{maximum} 字符'+('（含话题）' if platform=='xiaohongshu' else '')}
        if n>maximum:error('description',f'正文及话题 {n}/{maximum} 字符，请使用独立文案或减少话题。')
    else:
        metrics['description']={'used':measure(fields['description']), 'max':None,
                                'text':'视频文案额度待现场确认'}
    tags=fields['tags'];maximum=limits.get('tags_max')
    metrics['tags']={'used':len(tags),'max':maximum,'text':f'{len(tags)}/{maximum} 个话题' if maximum else f'{len(tags)} 个话题（计入描述额度；未确认独立数量硬上限）'}
    if platform in ('toutiao','xigua') and maximum is None:
        metrics['tags']['text']=f'{len(tags)} 个话题（视频话题规则待现场确认）'
    if maximum and len(tags)>maximum:error('tags',f'话题 {len(tags)}/{maximum} 个，请单独设置此平台话题。')
    if limits.get('tag_max'):
        for tag in tags:
            if measure(tag,limits.get('tag_count','utf16'))>limits['tag_max']:
                error('tags',f'话题「{tag}」超过 {limits["tag_max"]} 个平台计数单位，请缩短。')
    if platform=='tencent' and fields['short_title']:
        short=fields['short_title'];n=measure(short)
        if not 6<=n<=16:error('short_title','视频号短标题非空时须为 6–16 个字符（UTF-16），不会补字或截断。')
        if '\u200b' in short or not re.fullmatch(r'[℃\u4e00-\u9fa5A-Za-z0-9《》"“”:：+?？%\s]+',short):
            error('short_title','视频号短标题含网页不支持的字符，请按平台规则改写。')
    return {'errors':errors,'metrics':metrics,'limits':limits}


CAPTION_PLATFORMS = ('douyin', 'kuaishou', 'tencent')


def assert_draft_fields(platform, source, capabilities=None, caption=None):
    """Reject over-limit substituted text before any upload. Never truncate."""
    if platform not in TEXT_LIMITS:
        raise ValueError('未知平台。')
    if not isinstance(source, dict):
        raise ValueError('文案字段需要是对象。')
    tags = source.get('tags', [])
    fields = text_fields(platform, {
        'title': source.get('title', ''),
        'description': source.get('description', '') or '',
        'tags': tags if tags is not None else [],
        'short_title': source.get('short_title', '') or '',
    })
    # Douyin, Kuaishou and Tencent send one caption. When the runner already
    # substituted it, that exact string is what the editor receives.
    if platform in CAPTION_PLATFORMS and caption is not None:
        if not isinstance(caption, str):
            raise ValueError('caption 必须是完整文本。')
        fields['caption'] = caption
    if not isinstance(capabilities, dict):
        capabilities = None
    checked = check_fields(platform, fields, capabilities)
    if checked['errors']:
        raise ValueError('当前平台配置预检未通过；尚未上传：' + '；'.join(e['message'] for e in checked['errors']))
    return fields


def assert_adapter_draft_text(platform, uploader):
    """Validate the text a draft adapter will send. Public uploads are unchanged."""
    if not getattr(uploader, 'draft_only', False):
        return None
    caption = None
    if platform in CAPTION_PLATFORMS:
        expected = getattr(uploader, 'draft_expected', None)
        sent = expected.get('platform_fields') if isinstance(expected, dict) else None
        if isinstance(sent, dict) and 'caption' in sent:
            caption = sent['caption']
    return assert_draft_fields(platform, {
        'title': getattr(uploader, 'title', ''),
        'description': getattr(uploader, 'desc', '') or '',
        'tags': getattr(uploader, 'tags', None) if getattr(uploader, 'tags', None) is not None else [],
        'short_title': getattr(uploader, 'short_title', '') or '',
    }, getattr(uploader, 'draft_capabilities', None), caption)


def check_text(payload, platforms, capabilities=None):
    if not isinstance(payload,dict) or not isinstance(platforms,list) or any(p not in TEXT_LIMITS for p in platforms):
        raise ValueError('请选择有效平台并填写素材。')
    feedback={};errors=[];summaries={k:[] for k in ('title','description','tags')}
    names={'douyin':'抖音','xiaohongshu':'小红书','tencent':'视频号','bilibili':'B站','kuaishou':'快手'}
    for p in dict.fromkeys(platforms):
        checked=check_fields(p,text_fields(p,payload),(capabilities or {}).get(p))
        errors.extend(checked['errors']);feedback[p]={}
        for k,m in checked['metrics'].items():
            feedback[p][k]={'text':m['text'],'used':m.get('used'),'max':m.get('max'),
                            'error':any(e['field']==k for e in checked['errors'])}
            if k in summaries:summaries[k].append(names[p]+' '+m['text'])
    return {'errors':errors,'feedback':feedback,'summaries':{k:'；'.join(v) for k,v in summaries.items()}}
