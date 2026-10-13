'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui-assets/tag-editor.js'), 'utf8'), context);
const TagEditor = context.window.TagEditor;
const plain = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(document, tag = 'input') {
    this.ownerDocument = document; this.tagName = tag;
    this.children = []; this.handlers = {}; this.attrs = {};
    this.value = ''; this.textContent = ''; this.selectionStart = null; this.selectionEnd = null;
  }
  addEventListener(type, handler) { (this.handlers[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.handlers[type] = (this.handlers[type] || []).filter(item => item !== handler); }
  setAttribute(key, value) { this.attrs[key] = value; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  fire(type, details = {}) {
    const event = { prevented: false, preventDefault() { this.prevented = true; }, ...details };
    for (const handler of this.handlers[type] || []) handler(event);
    return event;
  }
}
function editor() {
  const document = { createElement: tag => new Element(document, tag) };
  const input = new Element(document), chips = new Element(document, 'div');
  const hidden = new Element(document), suggestions = new Element(document, 'div');
  const changes = [];
  const api = TagEditor.attach({ input, chips, hidden, suggestions, onChange: value => changes.push(plain(value)) });
  return { api, input, chips, hidden, suggestions, changes };
}

test('normalization accepts old lists and concatenated/fullwidth hashtags without truncation', () => {
  assert.deepEqual(plain(TagEditor.parse('#生活#日常，＃日常 #旅行；（咖啡）')), ['生活', '日常', '旅行', '咖啡']);
  assert.deepEqual(plain(TagEditor.parse([' #生活#日常', '生活', 'cafe\u0301', 'café', '_日常_', 'C++'])),
    ['生活', '日常', 'café', '_日常_', 'C++']);
  assert.equal(TagEditor.parse(Array.from({ length: 15 }, (_, i) => '#标签' + i)).length, 15);
});

test('suggestions extract actual hashtags, including adjacent ones, and reject URL fragments', () => {
  assert.deepEqual(plain(TagEditor.extract('今天出游 ＃生活#日常，#cafe\u0301。正文不会变标签。')),
    ['生活', '日常', 'café']);
  assert.deepEqual(plain(TagEditor.extract('https://example.com/#anchor www.test/#skip C#开发 foo#word，#保留')),
    ['保留']);
});

test('restoration is quiet; typing pending tags reaches hidden field and get before blur', () => {
  const { api, input, hidden, changes, chips } = editor();
  api.set(['旧标签']);
  assert.equal(changes.length, 0);
  input.value = '#生活#日常，＃日常 #旅行'; input.fire('input');
  assert.deepEqual(plain(api.get()), ['旧标签', '生活', '日常', '旅行']);
  assert.equal(hidden.value, '#旧标签 #生活 #日常 #旅行');
  assert.equal(input.value, '#旅行');
  assert.equal(chips.children.length, 3);
  const event = input.fire('keydown', { key: 'Enter' });
  assert.equal(event.prevented, true);
  assert.equal(input.value, '');
  assert.equal(chips.children.length, 4);
  assert.equal(changes.length, 1, 'committing an already saved pending token should not emit duplicate changes');
});

test('Chinese IME does not commit Enter or comma during composition', () => {
  const { api, input, hidden, changes } = editor();
  input.fire('compositionstart');
  input.value = '生'; input.fire('input');
  assert.equal(hidden.value, '');
  const event = input.fire('keydown', { key: 'Enter', isComposing: true });
  assert.equal(event.prevented, false);
  assert.equal(input.value, '生');
  input.value = '生活'; input.fire('compositionend');
  assert.deepEqual(plain(api.get()), ['生活']);
  assert.equal(changes.length, 1);
  input.fire('blur');
  assert.equal(input.value, '');
  assert.equal(hidden.value, '#生活');
});

test('paste splits and deduplicates, empty Backspace and chip buttons delete accessible tags', () => {
  const { api, input, chips, hidden, changes } = editor();
  const event = input.fire('paste', { clipboardData: { getData: () => '#生活#日常，＃日常 #旅行' } });
  assert.equal(event.prevented, true);
  assert.deepEqual(plain(api.get()), ['生活', '日常', '旅行']);
  assert.equal(input.value, '');
  assert.equal(chips.children[0].attrs['aria-label'], '删除标签 #生活');
  input.fire('keydown', { key: 'Backspace' });
  assert.deepEqual(plain(api.get()), ['生活', '日常']);
  chips.children[0].fire('click');
  assert.deepEqual(plain(api.get()), ['日常']);
  assert.equal(hidden.value, '#日常');
  assert.equal(changes.length, 3);
});

test('suggestions are local, clickable and do not turn ordinary prose into tags', () => {
  const { api, suggestions, changes, input, hidden } = editor();
  api.suggest('这是普通文案');
  assert.match(suggestions.children[0].textContent, /#话题/);
  assert.equal(changes.length, 0);
  api.suggest('这次聊 #旅行 与 ＃生活#日常');
  assert.equal(suggestions.children.length, 3);
  input.value = '#已有'; input.fire('input');
  suggestions.children[0].fire('click');
  assert.deepEqual(plain(api.get()), ['已有', '旅行']);
  assert.equal(hidden.value, '#已有 #旅行');
  assert.equal(input.value, '');
  assert.equal(suggestions.children.length, 2);
});

test('chip content is inert text and destroy removes input listeners', () => {
  const { api, chips, input, changes } = editor();
  api.set(['<img/onerror=alert(1)>']);
  assert.equal(chips.children[0].children[0].textContent, '#<img/onerror=alert(1)>');
  assert.equal(chips.children[0].children[0].children.length, 0);
  api.destroy();
  input.value = '#新标签'; input.fire('input');
  assert.equal(changes.length, 0);
});


test('long pasted descriptions keep every hashtag without quadratic prefix scanning', () => {
  const tags = Array.from({ length: 5000 }, (_, index) => '话题' + index);
  assert.deepEqual(plain(TagEditor.extract(tags.map(tag => '#' + tag).join(''))), tags);
});
