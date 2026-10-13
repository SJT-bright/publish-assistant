"""Bounded local text imports and shared hashtag normalization.

Tag limits belong to the platform preflight. This module never truncates tags.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

MAX_TEXT_BYTES = 256 * 1024
TEXT_EXTENSIONS = frozenset({'.txt', '.md', '.text'})
_FIELDS = frozenset({'title', 'description', 'tags'})
_TAG_SEPARATOR = re.compile(r'[#＃,，、;；\s]+')


def _trim_punctuation(value: str) -> str:
    # Underscores are valid hashtag characters; internal punctuation is retained.
    def edge(char: str) -> bool:
        return char != '_' and unicodedata.category(char).startswith('P')
    start, end = 0, len(value)
    while start < end and edge(value[start]):
        start += 1
    while end > start and edge(value[end - 1]):
        end -= 1
    return value[start:end]


def normalize_tags(value: str | list) -> list[str]:
    """Parse plain/hashed tags, preserving the order of unique NFC strings."""
    values = value if isinstance(value, list) else [value]
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        for token in _TAG_SEPARATOR.split(unicodedata.normalize('NFC', item)):
            token = _trim_punctuation(token)
            if token and token not in seen:
                seen.add(token)
                result.append(token)
    return result


def caption_with_topics(title: str, description: str, tags: list[str]) -> str:
    """Preserve the user's text and append only topics it does not contain.

    URL fragments and programming tokens such as C# are not topic markers.
    This is shared by UI limits and the finalized upload payload.
    """
    body = description.strip()
    text = unicodedata.normalize('NFC', '\n'.join(x for x in (title, body) if x))
    urls = [(m.start(), m.end()) for m in re.finditer(
        r'(?:[a-z][a-z\d+.-]*://|www\.|mailto:)[^\s，,、；;。！!（）()\[\]{}“”‘’]*', text, re.I)]
    existing, previous_end = set(), -1
    for match in re.finditer(r'[#＃]([^\W]+)', text):
        if any(start <= match.start() < end for start, end in urls):
            continue
        previous = text[match.start()-1:match.start()] if match.start() else ''
        if previous and not previous.isspace() and not unicodedata.category(previous).startswith('P') and match.start() != previous_end:
            continue
        existing.add(match.group(1))
        previous_end = match.end()
    suffix = ' '.join('#' + tag for tag in tags if tag not in existing)
    return '\n'.join(x for x in (title, body, suffix) if x)


def read_text_field(path: str | Path, field: str) -> dict:
    """Read a small user-selected text file for one material field."""
    if field not in _FIELDS:
        raise ValueError('不支持这个文字字段。')
    source = Path(path).expanduser()
    if source.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError('请拖入 TXT、MD 或 TEXT 文字文件。')
    try:
        if not source.is_file():
            raise ValueError('找不到这个文字文件。')
        # Read a bounded amount even if a file grows between selection and import.
        with source.open('rb') as stream:
            raw = stream.read(MAX_TEXT_BYTES + 1)
    except OSError as exc:
        raise ValueError('无法读取这个文字文件，请检查文件权限。') from exc
    if len(raw) > MAX_TEXT_BYTES:
        raise ValueError('文字文件不能超过 256 KiB。')
    if b'\x00' in raw:
        raise ValueError('这个文件含有二进制内容，请使用文字文件。')
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            content = raw.decode('gb18030')
        except UnicodeDecodeError as exc:
            raise ValueError('无法识别文字编码，请另存为 UTF-8 文字文件。') from exc
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    if field == 'title':
        value = next((line.strip() for line in content.splitlines() if line.strip()), '')
    elif field == 'description':
        value = content.strip()
    else:
        value = normalize_tags(content)
    return {'value': value, 'filename': source.name}
