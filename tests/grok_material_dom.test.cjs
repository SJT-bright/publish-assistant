'use strict';
// Isolated DOM checks for covers, text import, frame capture, and per-field overrides.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const sources = ['ui-assets/tag-editor.js', 'ui-assets/field-overrides.js', 'ui-assets/material-editor.js']
  .map(file => ({ file, code: fs.readFileSync(path.join(root, file), 'utf8') }));

function dataName(attribute) {
  return attribute.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}
class ClassList {
  constructor(owner) { this.owner = owner; }
  add(...names) { for (const name of names) this.owner._classes.add(name); }
  remove(...names) { for (const name of names) this.owner._classes.delete(name); }
  toggle(name, force) {
    const next = force === undefined ? !this.owner._classes.has(name) : !!force;
    if (next) this.owner._classes.add(name); else this.owner._classes.delete(name);
    return next;
  }
  contains(name) { return this.owner._classes.has(name); }
}
class Element {
  constructor(document, tag = 'div') {
    this.ownerDocument = document;
    this.tagName = tag;
    this.parent = null;
    this.children = [];
    this.handlers = {};
    this.attrs = {};
    this.dataset = {};
    this._classes = new Set();
    this.classList = new ClassList(this);
    this.id = '';
    this.value = '';
    this.textContent = '';
    this.disabled = false;
    this.hidden = false;
    this.type = '';
    this.placeholder = '';
    this.title = '';
    this._src = '';
    this.options = [];
  }
  get className() { return [...this._classes].join(' '); }
  set className(value) { this._classes = new Set(String(value || '').split(/\s+/).filter(Boolean)); }
  get src() { return this._src; }
  set src(value) { this._src = String(value); this.attrs.src = this._src; }
  setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'id') this.id = String(value); }
  getAttribute(name) {
    if (name === 'src') return this.attrs.src ?? null;
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }
  removeAttribute(name) { delete this.attrs[name]; if (name === 'src') this._src = ''; }
  addEventListener(type, handler) { (this.handlers[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.handlers[type] = (this.handlers[type] || []).filter(item => item !== handler); }
  focus() { this.ownerDocument.activeElement = this; }
  append(...nodes) {
    for (const node of nodes) {
      if (!node || typeof node !== 'object') continue;
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  fire(type, details = {}) {
    const event = {
      type, bubbles: true, prevented: false, stopped: false, target: this,
      preventDefault() { this.prevented = true; },
      stopPropagation() { this.stopped = true; },
      ...details
    };
    for (let node = this; node && !event.stopped; node = node.parent) {
      for (const handler of node.handlers[type] || []) handler(event);
    }
    return event;
  }
}
function matches(element, selector) {
  if (selector.startsWith('#')) return element.id === selector.slice(1);
  if (selector.startsWith('.')) return element._classes.has(selector.slice(1));
  const attribute = selector.match(/^\[([^\]=]+)(?:="([^"]*)")?\]$/);
  if (attribute) {
    const [, name, value] = attribute;
    if (!name.startsWith('data-')) return false;
    const actual = element.dataset[dataName(name)];
    return value === undefined ? actual !== undefined : String(actual) === value;
  }
  return element.tagName === selector;
}
function queryAll(rootNode, selector) {
  const found = [];
  (function visit(node) {
    for (const child of node.children || []) {
      if (matches(child, selector)) found.push(child);
      visit(child);
    }
  })(rootNode);
  return found;
}
function createDocument() {
  const document = new Element(null, 'document');
  document.ownerDocument = document;
  document.activeElement = null;
  document.createElement = tag => new Element(document, tag);
  document.body = document.createElement('body');
  document.append(document.body);
  document.getElementById = id => queryAll(document, '#' + id)[0] || null;
  document.querySelector = selector => queryAll(document, selector)[0] || null;
  return document;
}
function element(document, tag, properties = {}) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(properties)) {
    if (key === 'dataset') Object.assign(node.dataset, value);
    else node[key] = value;
  }
  return node;
}
function buttonByText(rootNode, text) {
  return queryAll(rootNode, 'button').find(node => node.textContent === text);
}
function loadPage() {
  const document = createDocument();
  const sandbox = {
    window: { document }, document, console, TextDecoder, TextEncoder, Uint8Array, setTimeout, clearTimeout
  };
  vm.createContext(sandbox);
  for (const source of sources) vm.runInContext(source.code, sandbox, { filename: source.file });
  return { document, window: sandbox.window };
}
async function flush() {
  for (let step = 0; step < 8; step += 1) await new Promise(resolve => setImmediate(resolve));
}
const plain = value => JSON.parse(JSON.stringify(value));
function deferredBytes(text) {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, finish: () => resolve(new TextEncoder().encode(text).buffer) };
}

function fieldHarness() {
  const { document, window } = loadPage();
  const anchor = element(document, 'div');
  document.body.append(anchor);
  let global = { title: '统一标题', description: '统一文案', tags: ['生活', '日常'] };
  const api = window.FieldOverrides.mount({
    platforms: [{ id: 'douyin', name: '抖音' }, { id: 'xiaohongshu', name: '小红书' }, { id: 'bilibili', name: 'B站' }],
    getGlobal: () => global
  });
  return {
    api,
    title: api.add('title', anchor),
    description: api.add('description', anchor),
    tags: api.add('tags', anchor),
    setGlobal(next) { global = next; }
  };
}

function materialHarness() {
  const { document, window } = loadPage();
  const jobs = [];
  const notices = [];
  const loaded = [];
  const covers = [];
  const material = {
    video: '', title: '', description: '', cover: '', cover_landscape: '', cover_portrait: '',
    cover_frames: {}, cover_generated: false
  };
  const videoPlayer = element(document, 'video', { id: 'videoPlayer', hidden: true });
  let currentTime = 0;
  videoPlayer.blockSeek = false;
  videoPlayer.error = null;
  videoPlayer.duration = NaN;
  videoPlayer.pause = () => {};
  videoPlayer.load = () => {};
  Object.defineProperty(videoPlayer, 'currentTime', {
    configurable: true,
    get() { return currentTime; },
    set(value) { if (videoPlayer.blockSeek) throw new Error('not seekable'); currentTime = Number(value); }
  });
  const seek = element(document, 'input', { id: 'seek', value: '0' });
  const time = element(document, 'input', { id: 'frameTime', value: '0' });
  const ratio = element(document, 'select', { id: 'captureRatio', value: '16:9' });
  for (const value of ['16:9', '4:3', '3:4', '9:16', 'original']) {
    const item = element(document, 'option', { value });
    item.disabled = false;
    ratio.options.push(item);
  }
  const role = element(document, 'select', { id: 'captureRole', value: 'landscape' });
  for (const value of ['landscape', 'portrait', 'common']) {
    const item = element(document, 'option', { value });
    item.disabled = false;
    role.options.push(item);
  }
  const nodes = [
    videoPlayer, seek, time,
    element(document, 'span', { id: 'videoDuration' }),
    element(document, 'p', { id: 'frameStatus' }),
    element(document, 'button', { id: 'captureFrame' }),
    role, ratio,
    element(document, 'div', { id: 'videoDrop' }),
    element(document, 'input', { id: 'videoFileInput' }),
    element(document, 'button', { id: 'file' }),
    element(document, 'div', { id: 'videoEmpty' }),
    element(document, 'input', { id: 'title' }),
    element(document, 'textarea', { id: 'description' })
  ];
  for (const field of ['title', 'description']) {
    const zone = element(document, 'div', { dataset: { textDrop: field } });
    zone.append(element(document, 'button', { dataset: { textPick: field } }));
    nodes.push(zone);
  }
  for (const which of ['common', 'landscape', 'portrait']) {
    const slot = element(document, 'div', { dataset: { coverSlot: which } });
    slot.append(
      element(document, 'img'),
      element(document, 'div', { className: 'cover-caption', textContent: '未选择' }),
      element(document, 'button', { dataset: { coverPick: which } }),
      element(document, 'button', { dataset: { coverClear: which } })
    );
    nodes.push(slot);
  }
  for (const node of nodes) document.body.append(node);
  window.draftDesktop = { filePath: file => file.path || '' };
  const editor = window.MaterialEditor.mount({
    request(url, body) {
      const job = { url, body };
      job.done = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
      jobs.push(job);
      return job.done;
    },
    notice(message) { notices.push(message); },
    dirty() {},
    getMaterial: () => material,
    loadVideo(filePath) { loaded.push(filePath); return Promise.resolve(filePath); },
    setCover(which, coverPath, options = {}) {
      const key = { common: 'cover', landscape: 'cover_landscape', portrait: 'cover_portrait' }[which];
      material[key] = coverPath;
      material.cover_frames = { ...material.cover_frames };
      if (options.frame) material.cover_frames[which] = options.frame;
      else delete material.cover_frames[which];
      if (which === 'common') material.cover_generated = !!options.generated;
      covers.push({ which, coverPath });
      editor.update({ ...material });
    }
  });
  return { document, window, editor, jobs, notices, loaded, covers, material, videoPlayer, seek, time, role, ratio };
}
function readyVideo(harness, filePath = '/tmp/素材/拖入测试.mp4') {
  harness.editor.update({ ...harness.material, video: filePath });
  harness.videoPlayer.duration = 10;
  harness.videoPlayer.fire('loadedmetadata');
}

test('independent fields start collapsed and only the edited field stops inheriting', () => {
  const { api, title, description, tags, setGlobal } = fieldHarness();
  for (const widget of [title, description, tags]) {
    assert.equal(widget.panel.hidden, true);
    assert.equal(widget.toggle.getAttribute('aria-expanded'), 'false');
  }
  tags.open('douyin');
  assert.equal(tags.panel.hidden, false);
  buttonByText(tags.panel, '单独设置').fire('click');
  const input = tags.panel.querySelector('input');
  input.value = '#独特主题';
  input.fire('keydown', { key: 'Enter' });
  setGlobal({ title: '新的统一标题', description: '更新统一文案', tags: ['旅行'] });
  api.refresh();
  assert.equal(api.resolve('douyin', 'title'), '新的统一标题');
  assert.equal(api.resolve('douyin', 'description'), '更新统一文案');
  assert.deepEqual(plain(api.resolve('douyin', 'tags')), ['生活', '日常', '独特主题']);
  assert.equal(api.has('douyin', 'title'), false);
  assert.deepEqual(plain(api.resolve('xiaohongshu', 'tags')), ['旅行']);
  assert.equal(api.resolve('bilibili', 'description'), '更新统一文案');
  assert.equal(api.get().xiaohongshu, undefined);
});

test('explicit empty copy and tags, restore, and old dedicated cover keys survive', () => {
  const { api } = fieldHarness();
  api.set({
    douyin: {
      title: '旧完整标题', description: '', tags: [], short_title: '六个字短标题',
      cover: '/tmp/old-common.jpg', cover_landscape: '/tmp/old-landscape.jpg',
      cover_portrait: '/tmp/old-portrait.jpg', legacy_note: '旧字段保留'
    },
    xiaohongshu: { tags: '#生活#日常，＃日常 #旅行' }
  });
  let saved = api.get();
  assert.equal(saved.douyin.description, '');
  assert.deepEqual(plain(saved.douyin.tags), []);
  assert.equal(saved.douyin.cover, '/tmp/old-common.jpg');
  assert.equal(saved.douyin.cover_landscape, '/tmp/old-landscape.jpg');
  assert.equal(saved.douyin.cover_portrait, '/tmp/old-portrait.jpg');
  assert.equal(saved.douyin.short_title, '六个字短标题');
  assert.equal(saved.douyin.legacy_note, '旧字段保留');
  assert.deepEqual(plain(saved.xiaohongshu.tags), ['生活', '日常', '旅行']);
  api.setField('douyin', 'title', '只改标题');
  saved = api.get();
  assert.equal(saved.douyin.title, '只改标题');
  assert.equal(saved.douyin.description, '');
  assert.equal(saved.douyin.cover_portrait, '/tmp/old-portrait.jpg');
  api.unsetField('douyin', 'description');
  api.unsetField('douyin', 'tags');
  assert.equal(api.has('douyin', 'description'), false);
  assert.equal(api.resolve('douyin', 'description'), '统一文案');
  assert.deepEqual(plain(api.resolve('douyin', 'tags')), ['生活', '日常']);
  assert.equal(api.get().douyin.cover, '/tmp/old-common.jpg');
  assert.equal(api.get().douyin.legacy_note, '旧字段保留');
});

test('refresh during platform-tag composition keeps the IME buffer', () => {
  const { api, tags } = fieldHarness();
  tags.open('douyin');
  buttonByText(tags.panel, '单独设置').fire('click');
  const input = tags.panel.querySelector('input');
  input.focus();
  input.fire('compositionstart');
  input.value = '独特';
  input.fire('input');
  api.setFeedback({ douyin: { tags: { text: '2 个话题' } } });
  assert.equal(input.value, '独特');
  assert.deepEqual(plain(api.resolve('douyin', 'tags')), ['生活', '日常']);
  input.fire('compositionend');
  assert.deepEqual(plain(api.resolve('douyin', 'tags')), ['生活', '日常', '独特']);
  assert.equal(tags.panel.querySelector('.field-override-feedback').textContent, '2 个话题');
});

test('disk drops use the native path, and a browser text file keeps the first title line', async () => {
  const harness = materialHarness();
  const { document, window } = harness;
  const videoDrop = document.getElementById('videoDrop');
  const blocked = document.fire('dragover', { dataTransfer: { types: ['Files'] } });
  assert.equal(blocked.prevented, true);
  videoDrop.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '拖入测试.mp4', type: 'video/mp4', path: '/tmp/素材/拖入测试.mp4' }] } });
  videoDrop.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '素材文件夹', type: '', path: '/tmp/素材' }] } });
  videoDrop.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '笔记.txt', type: 'text/plain', path: '/tmp/笔记.txt' }] } });
  await flush();
  assert.deepEqual(harness.loaded, ['/tmp/素材/拖入测试.mp4', '/tmp/素材']);
  assert.match(harness.notices.at(-1), /视频文件或素材文件夹/);

  const titleZone = document.querySelector('[data-text-drop="title"]');
  titleZone.fire('drop', { dataTransfer: { types: ['text/plain'], files: [], getData: () => '拖动纯文字标题\n第二行' } });
  await flush();
  assert.equal(document.getElementById('title').value, '拖动纯文字标题');

  titleZone.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '标题.txt', size: 12, type: 'text/plain', path: '/tmp/标题.txt' }] } });
  await flush();
  const imported = harness.jobs.find(job => job.url === '/api/import-text');
  assert.equal(imported.body.path, '/tmp/标题.txt');
  assert.equal(imported.body.field, 'title');
  imported.resolve({ value: '拖入的标题' });
  await flush();
  assert.equal(document.getElementById('title').value, '拖入的标题');

  window.draftDesktop = null;
  const encoded = new TextEncoder().encode('\n拖入的标题\n第二行不是标题');
  const bytes = new Uint8Array(encoded.length + 3);
  bytes.set([0xEF, 0xBB, 0xBF]);
  bytes.set(encoded, 3);
  titleZone.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '标题.txt', size: bytes.length, type: 'text/plain', arrayBuffer: async () => bytes.buffer }] } });
  await flush();
  assert.equal(document.getElementById('title').value, '拖入的标题');

  window.draftDesktop = { filePath: file => file.path || '' };
  document.querySelector('[data-cover-slot="portrait"]').fire('drop', {
    dataTransfer: { types: ['Files'], files: [{ name: '手工竖版.png', type: 'image/png', path: '/tmp/手工竖版.png' }] }
  });
  await flush();
  const coverJob = harness.jobs.find(job => job.url === '/api/cover');
  assert.equal(coverJob.body.path, '/tmp/手工竖版.png');
  coverJob.resolve('/tmp/手工竖版.png');
  await flush();
  assert.equal(harness.material.cover_portrait, '/tmp/手工竖版.png');
  const preview = document.querySelector('[data-cover-slot="portrait"]').querySelector('img');
  assert.equal(preview.hidden, false);
  assert.equal(preview.getAttribute('src'), '/asset?path=' + encodeURIComponent('/tmp/手工竖版.png'));
});

test('an older text import does not overwrite a newer import or a newer external edit', async () => {
  const harness = materialHarness();
  const { document } = harness;
  harness.window.draftDesktop = null;
  const title = document.getElementById('title');
  const zone = document.querySelector('[data-text-drop="title"]');
  const older = deferredBytes('旧导入');
  const newer = deferredBytes('新导入');
  zone.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '旧.txt', size: 6, type: 'text/plain', arrayBuffer: () => older.promise }] } });
  zone.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '新.txt', size: 6, type: 'text/plain', arrayBuffer: () => newer.promise }] } });
  newer.finish();
  await flush();
  assert.equal(title.value, '新导入');
  older.finish();
  await flush();
  assert.equal(title.value, '新导入');

  const late = deferredBytes('过期导入');
  title.value = '正在导入前';
  zone.fire('drop', { dataTransfer: { types: ['Files'], files: [{ name: '晚到.txt', size: 4, type: 'text/plain', arrayBuffer: () => late.promise }] } });
  title.value = '加载后的标题';
  late.finish();
  await flush();
  assert.equal(title.value, '加载后的标题');
  assert.match(harness.notices.at(-1), /保留了你的最新编辑/);
});

test('a stale frame does not replace a newer cover, and an unapplied seek captures the typed time', async () => {
  const harness = materialHarness();
  const { document } = harness;
  readyVideo(harness);
  harness.role.value = 'portrait';
  harness.role.fire('change');
  assert.deepEqual(harness.ratio.options.filter(item => !item.disabled).map(item => item.value), ['3:4', '9:16', 'original']);
  harness.ratio.value = '9:16';
  harness.videoPlayer.blockSeek = true;
  harness.time.value = '1.50';
  harness.time.fire('change');
  harness.videoPlayer.fire('timeupdate');
  assert.equal(harness.time.value, '1.50');
  document.getElementById('captureFrame').fire('click');
  await flush();
  const frame = harness.jobs.filter(job => job.url === '/api/frame').at(-1);
  assert.equal(frame.body.role, 'portrait');
  assert.equal(frame.body.ratio, '9:16');
  assert.ok(Math.abs(frame.body.time - 1.5) < 0.001, `captured ${frame.body.time} instead of the selected 1.50s`);
  harness.editor.update({ ...harness.material, video: '/tmp/素材/拖入测试.mp4', cover_portrait: '/tmp/新竖版.png' });
  frame.resolve({
    path: '/tmp/frame-covers/frame-portrait-' + 'a'.repeat(32) + '.jpg',
    time: 1.5, width: 720, height: 1280, role: 'portrait', ratio: '9:16'
  });
  await flush();
  assert.equal(harness.covers.length, 0);
  assert.equal(harness.material.cover_portrait, '');
  assert.match(document.getElementById('frameStatus').textContent, /保留了当前选择/);
  assert.match(document.querySelector('[data-cover-slot="portrait"]').querySelector('.cover-caption').textContent, /新竖版\.png/);

  harness.videoPlayer.blockSeek = false;
  harness.videoPlayer.currentTime = 4;
  harness.videoPlayer.fire('seeked');
  harness.videoPlayer.fire('timeupdate');
  document.getElementById('captureFrame').fire('click');
  await flush();
  const live = harness.jobs.filter(job => job.url === '/api/frame').at(-1);
  assert.ok(Math.abs(live.body.time - 4) < 0.001);
  live.resolve({
    path: '/tmp/frame-covers/frame-portrait-' + 'b'.repeat(32) + '.jpg',
    time: 4, width: 720, height: 1280, role: 'portrait', ratio: '9:16'
  });
  await flush();
  assert.match(harness.material.cover_portrait, /frame-portrait-b/);
  assert.equal(harness.material.cover_frames.portrait.width, 720);
  assert.equal(harness.material.cover_generated, false);
});

test('reset cancels pending covers, text and frames even when the video was already empty', async () => {
  const harness=materialHarness(),{document}=harness;
  const bytes=deferredBytes('迟到的标题');harness.window.draftDesktop=null;
  document.querySelector('[data-text-drop="title"]').fire('drop',{dataTransfer:{types:['Files'],files:[{name:'旧.txt',size:12,type:'text/plain',arrayBuffer:()=>bytes.promise}]}});
  harness.window.draftDesktop={filePath:file=>file.path};
  document.querySelector('[data-cover-slot="common"]').fire('drop',{dataTransfer:{types:['Files'],files:[{name:'旧.png',path:'/fixture/old.png',type:'image/png'}]}});
  await flush();const cover=harness.jobs.find(job=>job.url==='/api/cover');
  harness.editor.reset();document.getElementById('title').value='';
  bytes.finish();cover.resolve('/fixture/old.png');await flush();
  assert.equal(document.getElementById('title').value,'');assert.equal(harness.covers.length,0);
  readyVideo(harness);document.getElementById('captureFrame').fire('click');await flush();
  const frame=harness.jobs.find(job=>job.url==='/api/frame');harness.editor.reset();
  frame.resolve({path:'/fixture/stale.jpg',time:1,width:160,height:90});await flush();
  assert.equal(harness.covers.length,0);assert.equal(harness.videoPlayer.hidden,true);
  assert.equal(document.getElementById('captureFrame').disabled,true);
  assert.equal(harness.videoPlayer.getAttribute('src'),null);
});

test('an unfinished seek does not stick after the video changes', async () => {
  const harness = materialHarness();
  const { document } = harness;
  readyVideo(harness, '/tmp/素材/旧视频.mp4');
  harness.videoPlayer.blockSeek = true;
  harness.time.value = '1.50';
  harness.time.fire('change');
  harness.videoPlayer.fire('timeupdate');
  assert.equal(harness.time.value, '1.50');
  document.getElementById('captureFrame').fire('click');
  await flush();
  const pending = harness.jobs.filter(job => job.url === '/api/frame').at(-1);
  assert.equal(pending.body.path, '/tmp/素材/旧视频.mp4');
  assert.ok(Math.abs(pending.body.time - 1.5) < 0.001);

  harness.material.video = '/tmp/素材/新视频.mp4';
  harness.editor.update({ ...harness.material });
  harness.videoPlayer.duration = 8;
  harness.videoPlayer.blockSeek = false;
  harness.videoPlayer.currentTime = 3.25;
  harness.videoPlayer.fire('loadedmetadata');
  harness.videoPlayer.fire('timeupdate');
  assert.equal(harness.time.value, '3.25');
  const framesBeforeSecondClick = harness.jobs.filter(job => job.url === '/api/frame').length;
  document.getElementById('captureFrame').fire('click');
  assert.equal(harness.jobs.filter(job => job.url === '/api/frame').length, framesBeforeSecondClick);

  pending.resolve({
    path: '/tmp/frame-covers/frame-landscape-' + 'c'.repeat(32) + '.jpg',
    time: 1.5, width: 1280, height: 720, role: 'landscape', ratio: '16:9'
  });
  await flush();
  assert.equal(harness.covers.length, 0);
  assert.equal(harness.material.cover_landscape, '');
  document.getElementById('captureFrame').fire('click');
  await flush();
  const frame = harness.jobs.filter(job => job.url === '/api/frame').at(-1);
  assert.equal(frame.body.path, '/tmp/素材/新视频.mp4');
  assert.ok(Math.abs(frame.body.time - 3.25) < 0.001, `captured ${frame.body.time} instead of the new video time 3.25`);
});
