(function (root) {
  'use strict';

  const FIELDS = { title: '标题', description: '文案', tags: '标签' };
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const safeKey = key => !['__proto__', 'prototype', 'constructor'].includes(key);
  let sequence = 0;

  function copy(value) {
    if (Array.isArray(value)) return value.map(copy);
    if (value && typeof value === 'object') {
      const result = {};
      for (const key of Object.keys(value)) if (safeKey(key)) result[key] = copy(value[key]);
      return result;
    }
    return value;
  }

  function createStore(options = {}) {
    const getGlobal = options.getGlobal || (() => ({}));
    const parseTags = options.parseTags || (value => {
      if (!root.TagEditor) throw new TypeError('请先加载话题标签编辑器。');
      return root.TagEditor.parse(value);
    });
    let values = {};

    function normalize(field, value) {
      if (field === 'tags') return parseTags(value);
      if (own(FIELDS, field)) return String(value ?? '');
      return copy(value);
    }
    function set(next = {}) {
      values = {};
      if (!next || typeof next !== 'object' || Array.isArray(next)) return;
      for (const platform of Object.keys(next)) {
        if (!safeKey(platform)) continue;
        const fields = next[platform];
        if (!fields || typeof fields !== 'object' || Array.isArray(fields)) continue;
        const preserved = {};
        for (const field of Object.keys(fields)) {
          if (safeKey(field)) preserved[field] = normalize(field, fields[field]);
        }
        if (Object.keys(preserved).length) values[platform] = preserved;
      }
    }
    function has(platform, field) { return own(values[platform], field); }
    function resolve(platform, field) {
      return copy(has(platform, field) ? values[platform][field] : normalize(field, getGlobal()[field]));
    }
    function setField(platform, field, value) {
      if (!safeKey(platform) || !safeKey(field)) throw new TypeError('无效的平台或字段。');
      if (!own(values, platform)) values[platform] = {};
      values[platform][field] = normalize(field, value);
    }
    function unsetField(platform, field) {
      if (!own(values, platform)) return;
      delete values[platform][field];
      if (!Object.keys(values[platform]).length) delete values[platform];
    }
    return { set, get: () => copy(values), has, resolve, setField, unsetField };
  }

  function mount(options = {}) {
    const { platforms = [], getGlobal = () => ({}), onChange = () => {} } = options;
    const list = platforms.map(platform => typeof platform === 'string'
      ? { id: platform, name: platform }
      : { id: platform.id, name: platform.name || platform.id });
    const store = createStore({ getGlobal, parseTags: options.parseTags });
    const widgets = new Map();
    let feedback = {};

    function notify(platform, field) {
      for (const widget of widgets.values()) widget.update();
      onChange(store.get(), { platform, field, override: store.has(platform, field) });
    }
    function describe(platform, field) {
      if (feedback[platform] && own(feedback[platform], field)) return feedback[platform][field];
      const value = store.resolve(platform, field);
      if (options.describeValue) return options.describeValue({ platform, field, value, isOverride: store.has(platform, field) }) || {};
      return { text: field === 'tags' ? (value || []).length + ' 个话题' : Array.from(value || '').length + ' 字' };
    }

    function add(field, anchor) {
      if (!own(FIELDS, field)) throw new TypeError('此入口仅支持标题、文案与标签。');
      if (widgets.has(field)) return widgets.get(field);
      if (typeof anchor === 'string') anchor = root.document.querySelector(anchor);
      if (!anchor) throw new TypeError('平台独立设置缺少插入位置。');
      const document = anchor.ownerDocument;
      const element = document.createElement('div');
      element.className = 'field-override';
      element.dataset.overrideField = field;
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'field-override-toggle small ghost';
      toggle.setAttribute('aria-expanded', 'false');
      const panel = document.createElement('div');
      panel.className = 'field-override-panel';
      panel.hidden = true;
      panel.id = 'field-override-panel-' + ++sequence;
      toggle.setAttribute('aria-controls', panel.id);
      const tabs = document.createElement('div');
      tabs.className = 'field-override-platforms';
      tabs.setAttribute('aria-label', '选择独立' + FIELDS[field] + '的平台');
      const row = document.createElement('div');
      row.className = 'field-override-mode';
      const mode = document.createElement('span');
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'small ghost';
      row.append(mode, action);
      const editing = document.createElement('div');
      editing.className = 'field-override-editing';
      const inherited = document.createElement('div');
      inherited.className = 'field-override-inherited';
      const status = document.createElement('div');
      status.className = 'field-override-feedback';
      status.setAttribute('aria-live', 'polite');
      const controls = new Map();
      let active = list.find(platform => store.has(platform.id, field))?.id || list[0]?.id;
      let input, tagController;
      if (field === 'tags') {
        editing.classList.add('field-override-tags');
        const chips = document.createElement('div');
        chips.className = 'field-override-chips';
        chips.setAttribute('aria-label', '平台独立话题');
        input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '#输入独立话题';
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        editing.append(chips, input, hidden);
        tagController = root.TagEditor.attach({ input, chips, hidden, onChange(value) {
          if (!active || !store.has(active, field)) return;
          store.setField(active, field, value); notify(active, field);
        } });
      } else {
        input = document.createElement(field === 'description' ? 'textarea' : 'input');
        if (field === 'title') input.type = 'text';
        input.placeholder = '填写此平台的独立' + FIELDS[field];
        editing.append(input);
        input.addEventListener('input', () => {
          if (!active || !store.has(active, field)) return;
          store.setField(active, field, input.value); notify(active, field);
        });
      }
      input.setAttribute('aria-label', '平台独立' + FIELDS[field]);

      function update() {
        const independent = list.filter(platform => store.has(platform.id, field));
        toggle.textContent = '独立' + FIELDS[field] + (independent.length ? ' · ' + independent.length : '');
        toggle.classList.toggle('has-overrides', !!independent.length);
        toggle.title = independent.length ? independent.map(platform => platform.name).join('、') + '已设置独立' + FIELDS[field] : '为个别平台设置独立' + FIELDS[field];
        for (const platform of list) {
          const button = controls.get(platform.id);
          button.setAttribute('aria-pressed', String(platform.id === active));
          button.classList.toggle('has-override', store.has(platform.id, field));
          button.title = platform.name + (store.has(platform.id, field) ? ' · 使用独立' : ' · 使用统一') + FIELDS[field];
        }
        if (!active) {
          mode.textContent = '暂无可设置的平台'; action.hidden = true; editing.hidden = true; inherited.hidden = true; return;
        }
        const enabled = store.has(active, field), platform = list.find(item => item.id === active);
        const value = store.resolve(active, field);
        mode.textContent = (platform?.name || active) + (enabled ? ' · 使用独立' : ' · 使用统一') + FIELDS[field];
        action.textContent = enabled ? '恢复统一' + FIELDS[field] : '单独设置';
        action.setAttribute('aria-label', (platform?.name || active) + ' · ' + action.textContent);
        editing.hidden = !enabled;
        inherited.hidden = enabled;
        inherited.textContent = field === 'tags' ? (value || []).map(tag => '#' + tag).join(' ') || '统一话题为空' : value || '统一' + FIELDS[field] + '为空';
        inherited.title = '继承统一' + FIELDS[field] + '，修改上方统一内容后会一起更新';
        // Updating counters must not commit the current tag draft or move a text cursor.
        const keepDraft = tagController && tagController.isComposing() && input.ownerDocument.activeElement === input;
        if (tagController && !keepDraft) {
          const current = tagController.get(), next = value || [];
          if (current.length !== next.length || current.some((tag, index) => tag !== next[index])) tagController.set(next);
        } else if (!tagController && input.value !== value) input.value = value;
        const detail = describe(active, field);
        const text = typeof detail === 'string' ? detail : detail?.text || '';
        status.textContent = text;
        status.classList.toggle('has-error', !!detail?.error);
        input.setAttribute('aria-invalid', String(!!detail?.error));
      }
      for (const platform of list) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'field-override-platform';
        button.textContent = platform.name;
        button.setAttribute('aria-label', platform.name);
        button.dataset.overridePlatform = platform.id;
        button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => { active = platform.id; update(); });
        controls.set(platform.id, button); tabs.append(button);
      }
      action.addEventListener('click', () => {
        if (!active) return;
        if (store.has(active, field)) store.unsetField(active, field);
        else store.setField(active, field, store.resolve(active, field));
        notify(active, field);
        if (store.has(active, field)) input.focus();
      });
      function open(platform) {
        if (platform && controls.has(platform)) active = platform;
        panel.hidden = false; toggle.setAttribute('aria-expanded', 'true'); update();
      }
      function close() { panel.hidden = true; toggle.setAttribute('aria-expanded', 'false'); }
      toggle.addEventListener('click', () => panel.hidden ? open() : close());
      panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close(); toggle.focus(); }
      });
      panel.append(tabs, row, inherited, editing, status);
      element.append(toggle, panel); anchor.append(element);
      const widget = { element, toggle, panel, update, open, close };
      widgets.set(field, widget); update();
      return widget;
    }
    const refresh = () => { for (const widget of widgets.values()) widget.update(); };
    return {
      add,
      set(values) { store.set(values); refresh(); },
      get: store.get,
      resolve: store.resolve,
      has: store.has,
      setField(platform, field, value) { store.setField(platform, field, value); notify(platform, field); },
      unsetField(platform, field) { store.unsetField(platform, field); notify(platform, field); },
      refresh,
      close() { for (const widget of widgets.values()) widget.close(); },
      open(field, platform) {
        const widget = widgets.get(field);
        if (!widget) return;
        widget.open(platform);
        widget.element.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
        widget.toggle.focus();
      },
      setFeedback(value = {}) { feedback = value || {}; refresh(); }
    };
  }

  const api = { createStore, mount };
  root.FieldOverrides = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
