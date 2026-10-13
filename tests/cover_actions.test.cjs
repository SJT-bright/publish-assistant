'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { attach } = require('../ui-assets/cover-actions.js');
const { createCoverClipboard } = require('../desktop/cover-clipboard.cjs');

class FakeElement {
  constructor(tagName = 'div', document = null) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = document;
    this.parentNode = null;
    this.parentElement = null;
    this.children = [];
    this.listeners = new Map();
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.draggable = false;
    this.textContent = '';
    this.className = '';
  }
  append(...children) {
    for (const child of children) {
      child.parentNode = this;
      child.parentElement = this;
      child.ownerDocument ||= this.ownerDocument;
      this.children.push(child);
    }
  }
  removeChild(child) {
    this.children = this.children.filter(item => item !== child);
    child.parentNode = child.parentElement = null;
    return child;
  }
  addEventListener(type, callback) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(callback);
    this.listeners.set(type, callbacks);
  }
  removeEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== callback));
  }
  dispatch(type, values = {}) {
    const event = {
      type, target: this, currentTarget: this, defaultPrevented: false, propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
      ...values,
    };
    for (let node = this; node; node = event.bubbles === false ? null : node.parentElement) {
      event.currentTarget = node;
      for (const callback of [...(node.listeners.get(type) || [])]) callback(event);
      if (event.propagationStopped) break;
    }
    return event;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  matches(selector) { return selector === 'img' && this.tagName === 'IMG'; }
  closest(selector) {
    if (selector === 'img') {
      for (let node = this; node; node = node.parentElement) if (node.tagName === 'IMG') return node;
    }
    if (selector === 'button,input,select,textarea') {
      for (let node = this; node; node = node.parentElement) if (/^(BUTTON|INPUT|SELECT|TEXTAREA)$/.test(node.tagName)) return node;
    }
    return null;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some(child => child.contains(node));
  }
  querySelectorAll(selector) {
    const result = [];
    const visit = node => { for (const child of node.children) { if (selector === 'img' && child.tagName === 'IMG') result.push(child); visit(child); } };
    visit(this);
    return result;
  }
  getBoundingClientRect() { return { width: 170, height: 76 }; }
  focus() { this.focused = true; }
}

class FakeDocument {
  constructor() {
    this.nodeType = 9;
    this.listeners = new Map();
    const viewListeners = new Map();
    this.defaultView = {
      location: { href: 'http://127.0.0.1:18761/' }, innerWidth: 800, innerHeight: 600, scrollX: 0, scrollY: 0,
      addEventListener(type, callback) { const callbacks=viewListeners.get(type)||[];callbacks.push(callback);viewListeners.set(type,callbacks); },
      removeEventListener(type, callback) { viewListeners.set(type,(viewListeners.get(type)||[]).filter(item=>item!==callback)); },
      dispatch(type, values = {}) {
        const event={type,target:this,currentTarget:this,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...values};
        for(const callback of [...(viewListeners.get(type)||[])])callback(event);
        return event;
      },
    };
    this.body = new FakeElement('body', this);
    this.documentElement = new FakeElement('html', this);
    this.documentElement.append(this.body);
  }
  createElement(tagName) {
    const element = new FakeElement(tagName, this);
    if (tagName === 'canvas') {
      element.getContext = () => ({ drawImage() {} });
      element.toDataURL = () => 'data:image/png;base64,YWJj+/==';
    }
    return element;
  }
  addEventListener(type, callback) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(callback); this.listeners.set(type, callbacks);
  }
  removeEventListener(type, callback) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== callback)); }
  dispatch(type, values = {}) {
    const event = { type, target: null, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...values };
    for (const callback of [...(this.listeners.get(type) || [])]) callback(event);
    return event;
  }
}

class FakeDataTransfer {
  constructor(types = []) { this.types = [...types]; this.values = new Map(); this.dropEffect = 'none'; }
  setData(type, value) { this.values.set(type, String(value)); if (!this.types.includes(type)) this.types.push(type); }
  getData(type) { return this.values.get(type) || ''; }
}

function coverSlot(document, name, path) {
  const host = document.createElement('div');
  host.dataset.coverSlot = name;
  const image = document.createElement('img');
  image.hidden = false;
  image.src = `/asset?path=${encodeURIComponent(path)}`;
  image.currentSrc = image.src;
  image.naturalWidth = 320; image.naturalHeight = 180;
  host.append(image);
  document.body.append(host);
  return { host, image };
}

function clipboardResponse({ ok = true, type = 'image/png', bytes = Buffer.from('png'), length } = {}) {
  return {
    ok,
    headers: { get: name => name.toLowerCase() === 'content-type' ? type : name.toLowerCase() === 'content-length' ? String(length ?? bytes.length) : null },
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  };
}

test('all core cover slots open a small menu and write the image only after an explicit copy click', async () => {
  const document = new FakeDocument();
  const notices = [], copied = [];
  const slots = ['common', 'landscape', 'portrait'].map(name => coverSlot(document, name, `/local/${name}.jpg`));
  const detachedOverride = document.createElement('div');
  detachedOverride.dataset.overrideCover = 'kuaishou:cover_portrait';
  const overrideImage = document.createElement('img'); overrideImage.hidden = false; detachedOverride.append(overrideImage); document.body.append(detachedOverride);
  slots.push({ host: detachedOverride, image: overrideImage, path: '/local/kuaishou-portrait.jpg' });

  for (const [index, item] of slots.entries()) {
    const path = item.path || `/local/${item.host.dataset.coverSlot}.jpg`;
    attach({ element: item.host, getPath: () => path, copy: async value => copied.push(value), notice: value => notices.push(value) });
    assert.equal(item.image.draggable, true);
    const event = item.image.dispatch('contextmenu', { clientX: 240, clientY: 180 });
    assert.equal(event.defaultPrevented, true);
    const menu = document.body.children.at(-1);
    assert.deepEqual(menu.children.map(button => button.textContent), ['复制封面图片', '取消']);
    assert.equal(copied.length, index, 'opening the menu must not write the clipboard');
    menu.children[0].dispatch('click');
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.deepEqual(copied, ['/local/common.jpg', '/local/landscape.jpg', '/local/portrait.jpg', '/local/kuaishou-portrait.jpg']);
  assert.equal(notices.filter(value => value === '封面图片已复制。').length, 4);
});

test('Escape, cancel, outside click, changed covers, and copy failures preserve the existing image', async () => {
  const document = new FakeDocument();
  const item = coverSlot(document, 'common', '/local/old.jpg');
  const notices = [], copied = [];
  let path = '/local/old.jpg';
  attach({ element: item.host, getPath: () => path, copy: async value => copied.push(value), notice: value => notices.push(value) });

  item.image.dispatch('contextmenu');
  document.dispatch('keydown', { key: 'Escape' });
  assert.equal(document.body.children.length, 1);
  item.image.dispatch('contextmenu');
  document.body.children.at(-1).children[1].dispatch('click');
  assert.equal(document.body.children.length, 1);
  item.image.dispatch('contextmenu');
  document.dispatch('pointerdown', { target: document.body });
  assert.equal(document.body.children.length, 1);

  item.image.dispatch('contextmenu');
  path = '/local/new.jpg';
  document.body.children.at(-1).children[0].dispatch('click');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(copied, []);
  assert.match(notices.at(-1), /封面已更改/);
  path = '/local/new.jpg';
  const second = coverSlot(document, 'common', path);
  attach({ element: second.host, getPath: () => path, copy: async () => { throw new Error('clipboard fixture failure'); }, notice: value => notices.push(value) });
  second.image.dispatch('contextmenu');
  document.body.children.at(-1).children[0].dispatch('click');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(path, '/local/new.jpg');
  assert.match(notices.at(-1), /clipboard fixture failure/);
});

test('a no-movement focus scroll notification keeps the real menu open, while an actual scroll closes it', () => {
  const document = new FakeDocument();
  const item = coverSlot(document, 'common', '/local/common.jpg');
  attach({ element: item.host, getPath: () => '/local/common.jpg', copy: async () => {}, notice: () => {} });
  item.image.dispatch('contextmenu');
  assert.equal(document.body.children.filter(child => child.className === 'cover-actions-menu').length, 1);
  assert.equal(document.body.children.at(-1).className, 'cover-actions-menu');
  document.defaultView.dispatch('scroll', { target: document });
  assert.equal(document.body.children.at(-1).className, 'cover-actions-menu');
  document.defaultView.scrollY = 20;
  document.defaultView.dispatch('scroll', { target: document });
  assert.notEqual(document.body.children.at(-1)?.className, 'cover-actions-menu');
});

test('internal cover drags use an opaque token, preserve Files drops, and route the registered source path', async () => {
  const document = new FakeDocument();
  const sourcePath = '/private/registered/cover.jpg';
  const source = coverSlot(document, 'common', sourcePath);
  const target = coverSlot(document, 'portrait', '/private/registered/portrait.jpg');
  const drops = [];
  attach({ element: source.host, getPath: () => sourcePath, copy: async () => ({ ok: true }), notice: () => {} });
  attach({ element: target.host, getPath: () => '/private/registered/portrait.jpg', copy: async () => ({ ok: true }),
    notice: () => {}, onDropPath: (host, path) => drops.push([host.dataset.coverSlot, path]) });

  const transfer = new FakeDataTransfer();
  source.image.dispatch('dragstart', { dataTransfer: transfer });
  const opaque = transfer.getData('application/x-draft-cover-token');
  assert.ok(opaque);
  assert.notEqual(opaque, sourcePath);
  assert.match(transfer.getData('text/uri-list'), /^data:image\/png;base64,/);
  assert.match(transfer.getData('text/html'), /data:image\/png;base64,/);
  assert.equal(transfer.getData('text/uri-list').includes(sourcePath), false);
  assert.equal(transfer.getData('text/html').includes(sourcePath), false);
  const over = target.host.dispatch('dragover', { dataTransfer: transfer });
  assert.equal(over.defaultPrevented, true);
  assert.equal(transfer.dropEffect, 'copy');
  const drop = target.host.dispatch('drop', { dataTransfer: transfer });
  assert.equal(drop.defaultPrevented, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(drops, [['portrait', sourcePath]]);

  source.image.dispatch('dragend');
  const files = new FakeDataTransfer(['Files']);
  const fileOver = target.host.dispatch('dragover', { dataTransfer: files });
  const fileDrop = target.host.dispatch('drop', { dataTransfer: files });
  assert.equal(fileOver.defaultPrevented, false);
  assert.equal(fileDrop.defaultPrevented, false);
  assert.equal(drops.length, 1);
});

test('clipboard reads only a bounded PNG from the configured loopback asset route', async () => {
  const writes = [], requests = [], built = [];
  const image = { isEmpty: () => false, getSize: () => ({ width: 10, height: 20 }) };
  class FakeBlob { constructor(parts, options) { this.parts = parts; this.type = options.type; } }
  class FakeClipboardItem { constructor(data) { this.data = data; } }
  const service = createCoverClipboard({
    clipboard: { write: async value => writes.push(value) },
    nativeImage: { createFromBuffer: bytes => { built.push(Buffer.from(bytes)); return image; } },
    ClipboardItem: FakeClipboardItem, Blob: FakeBlob,
    fetch: async (url, options) => { requests.push({ url, options }); return clipboardResponse(); },
    baseURL: 'http://127.0.0.1:18761',
  });
  const result = await service.copy('/private/registered/封面.jpg');
  const requested = new URL(requests[0].url);
  assert.deepEqual(result, { ok: true });
  assert.equal(requested.origin, 'http://127.0.0.1:18761');
  assert.equal(requested.pathname, '/asset');
  assert.equal(requested.searchParams.get('path'), '/private/registered/封面.jpg');
  assert.equal(requested.searchParams.get('clipboard'), '1');
  assert.equal(requests[0].options.redirect, 'error');
  assert.equal(requests[0].options.method, 'GET');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].length, 1);
  assert.equal(writes[0][0].data['image/png'].type, 'image/png');
  assert.deepEqual(writes[0][0].data['image/png'].parts[0], Buffer.from('png'));
  assert.equal(built.length, 1);
});

test('bad paths, remote endpoints, wrong types, oversize data, and invalid images never change the clipboard', async () => {
  let fetchCount = 0, writeCount = 0;
  const make = (response, extra = {}) => createCoverClipboard({
    clipboard: { writeImage: () => { writeCount++; } },
    nativeImage: { createFromBuffer: () => ({ isEmpty: () => false, getSize: () => ({ width: 1, height: 1 }) }) },
    fetch: async () => { fetchCount++; return response; }, baseURL: 'http://127.0.0.1:18761', ...extra,
  });

  await assert.rejects(() => make(clipboardResponse()).copy('https://example.invalid/image.png'), /路径无效/);
  assert.equal(fetchCount, 0);
  assert.throws(() => createCoverClipboard({ clipboard: { writeImage() {} }, nativeImage: { createFromBuffer() {} }, fetch: async () => {}, baseURL: 'http://example.invalid' }), /本机工作台/);
  await assert.rejects(() => make(clipboardResponse({ type: 'image/jpeg' })).copy('/private/cover.jpg'), /没有返回 PNG/);
  await assert.rejects(() => make(clipboardResponse({ bytes: Buffer.alloc(20), length: 20 }), { maxBytes: 10 }).copy('/private/cover.jpg'), /大小限制/);
  await assert.rejects(() => createCoverClipboard({
    clipboard: { writeImage: () => { writeCount++; } }, nativeImage: { createFromBuffer: () => ({ isEmpty: () => true }) },
    fetch: async () => clipboardResponse(), baseURL: 'http://127.0.0.1:18761',
  }).copy('/private/cover.jpg'), /数据无效/);
  assert.equal(writeCount, 0);
});

test('clipboard timeout aborts the loopback request and leaves clipboard untouched', async () => {
  let aborted = false, wrote = false;
  const service = createCoverClipboard({
    clipboard: { writeImage: () => { wrote = true; } },
    nativeImage: { createFromBuffer: () => ({ isEmpty: () => false }) },
    fetch: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => { aborted = true; reject(new Error('aborted')); }, { once: true });
    }),
    baseURL: 'http://127.0.0.1:18761', timeoutMs: 10,
  });
  await assert.rejects(() => service.copy('/private/cover.jpg'), /超时/);
  assert.equal(aborted, true);
  assert.equal(wrote, false);
});

test('an asynchronous native clipboard rejection is propagated instead of reporting success', async () => {
  let writeCalls = 0;
  class FakeBlob { constructor(parts, options) { this.parts = parts; this.type = options.type; } }
  class FakeClipboardItem { constructor(data) { this.data = data; } }
  const service = createCoverClipboard({
    clipboard: { write: async () => { writeCalls++; throw new Error('native clipboard denied'); } },
    nativeImage: { createFromBuffer: () => ({ isEmpty: () => false, getSize: () => ({ width: 1, height: 1 }) }) },
    ClipboardItem: FakeClipboardItem, Blob: FakeBlob,
    fetch: async () => clipboardResponse(), baseURL: 'http://127.0.0.1:18761',
  });
  await assert.rejects(() => service.copy('/private/cover.jpg'), /native clipboard denied/);
  assert.equal(writeCalls, 1);
});
