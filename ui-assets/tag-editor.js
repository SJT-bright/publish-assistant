(function (root) {
  'use strict';

  const separators = /[#＃,，、;；\s]+/u;
  const edgePunctuation = char => char !== '_' && /\p{P}/u.test(char);
  const same = (a, b) => a.length === b.length && a.every((item, index) => item === b[index]);
  const format = tags => tags.map(tag => '#' + tag).join(' ');

  function parse(value) {
    const result = [], seen = new Set();
    for (const item of Array.isArray(value) ? value : [value]) {
      if (typeof item !== 'string') continue;
      for (const part of item.normalize('NFC').split(separators)) {
        const chars = Array.from(part);
        while (chars.length && edgePunctuation(chars[0])) chars.shift();
        while (chars.length && edgePunctuation(chars[chars.length - 1])) chars.pop();
        const tag = chars.join('');
        if (tag && !seen.has(tag)) { seen.add(tag); result.push(tag); }
      }
    }
    return result;
  }

  function extract(value) {
    const text = String(value || '').normalize('NFC');
    const pattern = /[#＃]([\p{L}\p{N}_][\p{L}\p{N}\p{M}_]*)/gu;
    // Pre-index URL ranges once rather than rescanning a growing prefix per tag.
    const urls = /(?:[a-z][a-z\d+.-]*:\/\/|www\.|mailto:)[^\s，,、；;。！!（）()\[\]{}“”‘’]*/giu;
    const ranges = [];
    let url;
    while ((url = urls.exec(text))) ranges.push([url.index, urls.lastIndex]);
    const result = [];
    let match, previousHashtagEnd = -1, range = 0;
    while ((match = pattern.exec(text))) {
      while (range < ranges.length && match.index >= ranges[range][1]) range++;
      if (range < ranges.length && match.index >= ranges[range][0]) continue;
      const previousUnit = text.charCodeAt(match.index - 1);
      const previousWidth = previousUnit >= 0xDC00 && previousUnit <= 0xDFFF ? 2 : 1;
      const previous = match.index ? text.slice(Math.max(0, match.index - previousWidth), match.index) : '';
      if (previous && !/\s/u.test(previous) && !edgePunctuation(previous)
          && match.index !== previousHashtagEnd) continue;
      result.push(match[1]);
      previousHashtagEnd = pattern.lastIndex;
    }
    return parse(result);
  }

  function attach(options) {
    const { input, chips, hidden, suggestions, onChange = () => {} } = options;
    if (!input || !chips || !hidden) throw new TypeError('标签编辑器缺少输入或标签容器。');
    const document = input.ownerDocument;
    let committed = [], composing = false, last = [], suggestionText = '';
    const listeners = [];
    const get = () => parse(committed.concat([input.value || '']));
    function listen(target, name, handler) {
      target.addEventListener(name, handler);
      listeners.push(() => target.removeEventListener(name, handler));
    }
    function sync(notify) {
      const current = get();
      hidden.value = format(current);
      const changed = !same(last, current);
      last = current.slice();
      if (notify && changed) onChange(current.slice());
    }
    function render() {
      chips.replaceChildren();
      for (const tag of committed) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'tag-chip';
        chip.setAttribute('aria-label', '删除标签 #' + tag);
        chip.title = '删除标签 #' + tag;
        const label = document.createElement('span');
        label.textContent = '#' + tag;
        const remove = document.createElement('span');
        remove.className = 'tag-remove';
        remove.textContent = '×';
        remove.setAttribute('aria-hidden', 'true');
        chip.append(label, remove);
        chip.addEventListener('click', () => {
          committed = committed.filter(item => item !== tag);
          render(); sync(true); renderSuggestions();
        });
        chips.append(chip);
      }
    }
    function renderSuggestions() {
      if (!suggestions) return;
      suggestions.replaceChildren();
      const current = new Set(get());
      const candidates = extract(suggestionText).filter(tag => !current.has(tag));
      if (!candidates.length) {
        const hint = document.createElement('span');
        hint.className = 'tag-suggestion-hint';
        hint.textContent = extract(suggestionText).length
          ? '文案里的话题已加入标签。'
          : '在文案里添加 #话题，这里会显示可加入的标签。';
        suggestions.append(hint);
        return;
      }
      for (const tag of candidates) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tag-suggestion';
        button.textContent = '+ #' + tag;
        button.setAttribute('aria-label', '添加标签 #' + tag);
        button.addEventListener('click', () => {
          committed = parse(get().concat([tag]));
          input.value = '';
          render(); sync(true); renderSuggestions();
        });
        suggestions.append(button);
      }
    }
    function commit() {
      committed = get();
      input.value = '';
      render(); sync(true); renderSuggestions();
    }
    function handleInput() {
      if (composing) return;
      // Commit finished tokens, keeping the final token editable while typing.
      const value = input.value || '';
      const boundaries = Array.from(value.matchAll(/[#＃,，、;；\s]/gu));
      const boundary = boundaries[boundaries.length - 1];
      if (boundary && boundary.index > 0) {
        const finished = value.slice(0, boundary.index);
        const remainder = value.slice(boundary.index + boundary[0].length);
        committed = parse(committed.concat([finished]));
        input.value = /[#＃]/u.test(boundary[0]) ? '#' + remainder : remainder;
        render();
      }
      sync(true); renderSuggestions();
    }
    listen(input, 'compositionstart', () => { composing = true; });
    listen(input, 'compositionend', () => { composing = false; handleInput(); });
    listen(input, 'input', handleInput);
    listen(input, 'keydown', event => {
      if (composing || event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Enter' || /^[,，、;；]$/u.test(event.key)) {
        event.preventDefault(); commit();
      } else if (event.key === 'Backspace' && !input.value && committed.length) {
        event.preventDefault();
        committed.pop(); render(); sync(true); renderSuggestions();
      }
    });
    listen(input, 'blur', () => { if (!composing) commit(); });
    listen(input, 'paste', event => {
      if (composing || !event.clipboardData) return;
      const pasted = event.clipboardData.getData('text/plain');
      if (!pasted) return;
      event.preventDefault();
      const value = input.value || '';
      const start = Number.isInteger(input.selectionStart) ? input.selectionStart : value.length;
      const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : start;
      input.value = value.slice(0, start) + pasted + value.slice(end);
      commit();
    });
    function set(value) {
      committed = parse(value);
      input.value = '';
      render(); sync(false); renderSuggestions();
    }
    function suggest(text) {
      suggestionText = String(text || '');
      renderSuggestions();
    }
    set(hidden.value || '');
    return { set, get, suggest, isComposing: () => composing, destroy() { listeners.splice(0).forEach(remove => remove()); } };
  }

  root.TagEditor = { parse, extract, attach };
})(typeof window === 'undefined' ? globalThis : window);
