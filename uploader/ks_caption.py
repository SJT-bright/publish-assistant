"""Decode the paragraph markup saved by the normal KS caption editor.

Official ks_publish_page.js:79009 saves contenteditable.innerHTML to caption.
This accepts only the observed div/br paragraph format, never arbitrary HTML.
Unknown rich topic/mention shapes remain unverified until separately observed.
"""
from html.parser import HTMLParser


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = []
        self.stack = [self.root]
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if attrs or tag not in ('div', 'br') or len(self.stack) > 64:
            self.invalid = True
            return
        if tag == 'br':
            self.stack[-1].append(('text', '\n'))
        else:
            children = []
            self.stack[-1].append(('div', children))
            self.stack.append(children)

    def handle_startendtag(self, tag, attrs):
        if tag != 'br':
            self.invalid = True
        else:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag != 'div' or len(self.stack) == 1:
            self.invalid = True
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].append(('text', data))

    def handle_comment(self, data):
        self.invalid = True

    def handle_decl(self, decl):
        self.invalid = True


def _render(nodes):
    output = ''
    previous_block = False
    for kind, value in nodes:
        block = kind == 'div'
        value = _render(value) if block else value
        if output and (block or previous_block) and not output.endswith('\n'):
            output += '\n'
        output += value
        previous_block = block
    return output


def decode_ks_caption(value):
    if not isinstance(value, str) or len(value) > 20000:
        return None
    parser = _Paragraphs()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError):
        return None
    if parser.invalid or len(parser.stack) != 1:
        return None
    return _render(parser.root)
