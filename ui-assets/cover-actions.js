/* Local-only actions for already imported cover images. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CoverActions = api;
})(globalThis, function () {
  'use strict';

  const TOKEN_TYPE = 'application/x-draft-cover-token';
  const MAX_PATH_LENGTH = 8192;
  const MAX_DRAG_DATA_URL_LENGTH = 16 * 1024 * 1024;
  const attached = new WeakMap();
  let activeDrag = null;
  let closeActiveMenu = null;
  let tokenSequence = 0;

  function localPath(value) {
    if (typeof value !== 'string') return '';
    const path = value;
    if (!path.trim() || path.length > MAX_PATH_LENGTH || /[\0\r\n]/.test(path)) return '';
    if (/^(?:https?|file|data|blob):/i.test(path)) return '';
    return path;
  }

  function hasType(dataTransfer, type) {
    return !!dataTransfer && Array.from(dataTransfer.types || []).includes(type);
  }

  function targetElement(target) {
    if (!target) return null;
    if (target.nodeType === 1) return target;
    return target.parentElement || null;
  }

  function imageFor(host, target) {
    const node = targetElement(target);
    const image = node?.closest?.('img');
    if (image && host.contains(image) && !image.hidden) return image;
    return null;
  }

  function readPath(getPath, host) {
    try { return localPath(getPath(host)); }
    catch (_) { return ''; }
  }

  function escapeAttribute(value) {
    return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function dragDataUrl(image, host, path, doc) {
    try {
      const view = doc.defaultView || globalThis;
      const pageURL = view.location?.href;
      if (!pageURL) return '';
      const imageURL = new URL(image.currentSrc || image.src, pageURL);
      const pageOrigin = new URL(pageURL).origin;
      if (imageURL.origin !== pageOrigin || imageURL.pathname !== '/asset' || imageURL.searchParams.get('path') !== path) return '';
      const width = Number(image.naturalWidth), height = Number(image.naturalHeight);
      if (!(width > 0 && height > 0) || width * height > 12_000_000) return '';
      const canvas = doc.createElement('canvas');
      canvas.width = width; canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) return '';
      context.drawImage(image, 0, 0, width, height);
      const dataURL = canvas.toDataURL('image/png');
      if (!dataURL.startsWith('data:image/png;base64,') || dataURL.length > MAX_DRAG_DATA_URL_LENGTH) return '';
      return dataURL;
    } catch (_) { return ''; }
  }

  function token() {
    try {
      if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    } catch (_) { /* Use a short-lived opaque fallback below. */ }
    tokenSequence += 1;
    return `cover-${Date.now().toString(36)}-${tokenSequence.toString(36)}-${Math.random().toString(36).slice(2)}`;
  }

  function report(notice, message) {
    try { notice?.(message); } catch (_) { /* Notifications must not break cover actions. */ }
  }

  function attach({ element, getPath, copy, notice, onDropPath } = {}) {
    if (!element || typeof getPath !== 'function') throw new TypeError('CoverActions.attach requires element and getPath.');
    if (attached.has(element)) return attached.get(element);

    const doc = element.nodeType === 9 ? element : element.ownerDocument;
    if (!doc) throw new TypeError('CoverActions.attach requires an element in a document.');
    const host = element;
    const listeners = [];
    let observer = null;
    let destroyed = false;
    const listen = (target, type, callback, options) => {
      target.addEventListener(type, callback, options);
      listeners.push(() => target.removeEventListener(type, callback, options));
    };
    const dismiss = () => {
      if (closeActiveMenu) closeActiveMenu();
    };
    const configureImages = () => {
      if (destroyed) return;
      const images = host.matches?.('img') ? [host] : [...(host.querySelectorAll?.('img') || [])];
      for (const image of images) image.draggable = true;
    };

    function showMenu(event, path) {
      dismiss();
      const view = doc.defaultView || globalThis;
      const openedAt = { x: Number(view.scrollX ?? view.pageXOffset ?? 0), y: Number(view.scrollY ?? view.pageYOffset ?? 0) };
      const menu = doc.createElement('div');
      menu.className = 'cover-actions-menu';
      menu.setAttribute('role', 'menu');
      Object.assign(menu.style, {
        position: 'fixed', zIndex: '2147483647', display: 'grid', gap: '4px',
        minWidth: '148px', padding: '6px', borderRadius: '9px',
        background: '#20242c', color: '#fff', boxShadow: '0 8px 28px #0006',
        border: '1px solid #ffffff2b', font: '13px system-ui, sans-serif',
      });
      const makeButton = (label, action, muted = false) => {
        const button = doc.createElement('button');
        button.type = 'button'; button.textContent = label;
        button.setAttribute('role', 'menuitem');
        Object.assign(button.style, {
          border: '0', borderRadius: '6px', padding: '8px 10px', textAlign: 'left',
          color: muted ? '#d1d5dc' : '#fff', background: muted ? 'transparent' : '#315fcb',
          font: 'inherit', cursor: 'pointer',
        });
        button.addEventListener('click', action);
        menu.append(button);
        return button;
      };
      const close = () => {
        if (menu.parentNode) menu.parentNode.removeChild(menu);
        if (closeActiveMenu === close) closeActiveMenu = null;
        doc.removeEventListener('pointerdown', outside);
        doc.removeEventListener('keydown', keydown);
        doc.defaultView?.removeEventListener('resize', close);
        doc.defaultView?.removeEventListener('scroll', onScroll, true);
      };
      const outside = e => { if (!menu.contains(e.target)) close(); };
      const keydown = e => {
        if (e.key === 'Escape') { e.preventDefault(); close(); }
      };
      const onScroll = event => {
        const x = Number(view.scrollX ?? view.pageXOffset ?? 0), y = Number(view.scrollY ?? view.pageYOffset ?? 0);
        // Browsers can emit a document scroll notification while focusing a newly
        // opened fixed menu even when the viewport did not move. Keep that menu;
        // dismiss it only for a real viewport move or a nested scroller move.
        if (x !== openedAt.x || y !== openedAt.y || (event.target !== doc && event.target !== view)) close();
      };
      const copyButton = makeButton('复制封面图片', async () => {
        close();
        if (readPath(getPath, host) !== path) {
          report(notice, '封面已更改，请重新打开菜单。');
          return;
        }
        if (typeof copy !== 'function') {
          report(notice, '当前环境不支持复制封面图片。');
          return;
        }
        try {
          const result = await copy(path);
          if (result === false || result?.ok === false) throw new Error(result?.message || '复制封面图片失败。');
          report(notice, '封面图片已复制。');
        } catch (error) {
          report(notice, error?.message || '复制封面图片失败。');
        }
      });
      makeButton('取消', close, true);
      (doc.body || doc.documentElement).append(menu);
      const bounds = menu.getBoundingClientRect?.() || { width: 170, height: 76 };
      const maxX = Math.max(0, Number(view.innerWidth) - (bounds.width || 170) - 8);
      const maxY = Math.max(0, Number(view.innerHeight) - (bounds.height || 76) - 8);
      menu.style.left = `${Math.max(8, Math.min(Number(event.clientX) || 0, maxX))}px`;
      menu.style.top = `${Math.max(8, Math.min(Number(event.clientY) || 0, maxY))}px`;
      closeActiveMenu = close;
      doc.addEventListener('pointerdown', outside);
      doc.addEventListener('keydown', keydown);
      view.addEventListener?.('resize', close);
      view.addEventListener?.('scroll', onScroll, true);
      copyButton.focus?.();
    }

    const onContextMenu = event => {
      const path = readPath(getPath, host);
      if (!path || !imageFor(host, event.target) || typeof copy !== 'function') return;
      event.preventDefault();
      showMenu(event, path);
    };
    const onDragStart = event => {
      const path = readPath(getPath, host);
      const image = imageFor(host, event.target);
      const data = event.dataTransfer;
      if (!path || !image || !data) return;
      const dragToken = token();
      activeDrag = { token: dragToken, path, host, getPath };
      try { data.effectAllowed = 'copy'; } catch (_) { /* Browser may expose a read-only facade. */ }
      try { data.setData(TOKEN_TYPE, dragToken); } catch (_) { /* Internal drop remains unavailable. */ }
      const dataURL = dragDataUrl(image, host, path, doc);
      if (dataURL) {
        try { data.setData('text/uri-list', dataURL); } catch (_) { /* Some targets reject data URLs. */ }
        try { data.setData('text/html', `<img alt="封面图片" src="${escapeAttribute(dataURL)}">`); } catch (_) { /* Optional drag representation. */ }
      }
    };
    const onDragEnd = () => { activeDrag = null; };
    const onDragOver = event => {
      if (!activeDrag || !hasType(event.dataTransfer, TOKEN_TYPE)) return;
      event.preventDefault();
      try { event.dataTransfer.dropEffect = 'copy'; } catch (_) { /* Read-only facade. */ }
    };
    const onDrop = event => {
      if (!activeDrag || !hasType(event.dataTransfer, TOKEN_TYPE) || typeof onDropPath !== 'function') return;
      let transferredToken = '';
      try { transferredToken = event.dataTransfer.getData(TOKEN_TYPE); } catch (_) { return; }
      if (!transferredToken || transferredToken !== activeDrag.token) return;
      const path = localPath(activeDrag.path);
      if (!path) return;
      event.preventDefault(); event.stopPropagation();
      if (readPath(activeDrag.getPath, activeDrag.host) !== path) {
        report(notice, '源封面已更改，拖动已取消。');
        return;
      }
      Promise.resolve().then(() => onDropPath(host, path)).catch(error => report(notice, error?.message || String(error)));
    };

    configureImages();
    const Observer = doc.defaultView?.MutationObserver || globalThis.MutationObserver;
    if (typeof Observer === 'function') {
      observer = new Observer(configureImages);
      observer.observe(host, { childList: true, subtree: true });
    }
    listen(host, 'contextmenu', onContextMenu);
    listen(host, 'dragstart', onDragStart);
    listen(host, 'dragend', onDragEnd);
    listen(host, 'dragover', onDragOver);
    listen(host, 'drop', onDrop);

    const handle = {
      destroy() {
        if (destroyed) return;
        destroyed = true;
        if (observer) observer.disconnect();
        dismiss();
        for (const remove of listeners.splice(0)) remove();
        if (activeDrag) activeDrag = null;
        attached.delete(element);
      },
    };
    attached.set(element, handle);
    return handle;
  }

  return { attach };
});
