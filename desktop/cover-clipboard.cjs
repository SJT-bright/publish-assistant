'use strict';

const DEFAULT_TIMEOUT_MS = 7000;
const DEFAULT_MAX_BYTES = 16 * 1024 * 1024;
const DEFAULT_MAX_PATH_LENGTH = 8192;

function loopbackBaseURL(value) {
  let url;
  try { url = new URL(value); }
  catch (_) { throw new TypeError('封面服务地址无效。'); }
  const host = url.hostname.toLowerCase();
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]', '::1'].includes(host) ||
      url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new TypeError('封面图片只允许从本机工作台读取。');
  }
  return url;
}

function validAssetPath(value, maxLength) {
  if (typeof value !== 'string') return '';
  const path = value;
  if (!path.trim() || path.length > maxLength || /[\0\r\n]/.test(path) || /^(?:https?|file|data|blob):/i.test(path)) return '';
  return path;
}

async function readBounded(response, maxBytes) {
  const contentLength = Number(response.headers?.get?.('content-length'));
  if (Number.isFinite(contentLength) && contentLength > maxBytes) throw new Error('封面图片超过复制大小限制。');
  const reader = response.body?.getReader?.();
  if (reader) {
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = Buffer.from(value);
        total += chunk.length;
        if (total > maxBytes) {
          await reader.cancel().catch(() => {});
          throw new Error('封面图片超过复制大小限制。');
        }
        chunks.push(chunk);
      }
    } catch (error) {
      try { await reader.cancel(); } catch (_) { /* The stream may already be closed. */ }
      throw error;
    }
    return Buffer.concat(chunks, total);
  }
  if (typeof response.arrayBuffer !== 'function') throw new Error('无法读取封面图片数据。');
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > maxBytes) throw new Error('封面图片超过复制大小限制。');
  return bytes;
}

function createCoverClipboard({
  clipboard,
  nativeImage,
  ClipboardItem: ClipboardItemClass = globalThis.ClipboardItem,
  Blob: BlobClass = globalThis.Blob,
  fetch: fetchImpl = globalThis.fetch,
  baseURL,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  maxBytes = DEFAULT_MAX_BYTES,
  maxPathLength = DEFAULT_MAX_PATH_LENGTH,
}) {
  if (!clipboard || (typeof clipboard.writeImage !== 'function' && typeof clipboard.write !== 'function')) {
    throw new TypeError('clipboard.writeImage or clipboard.write is required.');
  }
  if (!nativeImage || typeof nativeImage.createFromBuffer !== 'function') throw new TypeError('nativeImage.createFromBuffer is required.');
  if (typeof fetchImpl !== 'function') throw new TypeError('fetch is required.');
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new TypeError('timeoutMs must be a positive integer.');
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new TypeError('maxBytes must be a positive integer.');
  if (!Number.isSafeInteger(maxPathLength) || maxPathLength < 1) throw new TypeError('maxPathLength must be a positive integer.');
  const origin = loopbackBaseURL(baseURL);

  async function copy(pathValue) {
    const path = validAssetPath(pathValue, maxPathLength);
    if (!path) throw new TypeError('封面路径无效。');

    const url = new URL('/asset', origin);
    url.searchParams.set('path', path);
    url.searchParams.set('clipboard', '1');

    const controller = new AbortController();
    let timeoutId;
    const timeout = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        reject(new Error('读取封面图片超时。'));
        controller.abort();
      }, timeoutMs);
    });
    try {
      const response = await Promise.race([
        fetchImpl(url.href, { method: 'GET', redirect: 'error', signal: controller.signal }),
        timeout,
      ]);
      if (!response?.ok) throw new Error('封面图片读取失败。');
      const contentType = String(response.headers?.get?.('content-type') || '').split(';', 1)[0].trim().toLowerCase();
      if (contentType !== 'image/png') throw new Error('封面服务没有返回 PNG 图片。');
      const bytes = await Promise.race([readBounded(response, maxBytes), timeout]);
      if (!bytes.length) throw new Error('封面图片为空。');
      const image = nativeImage.createFromBuffer(bytes);
      if (!image || (typeof image.isEmpty === 'function' && image.isEmpty())) throw new Error('封面图片数据无效。');
      if (typeof image.getSize === 'function') {
        const size = image.getSize();
        if (!size || !(Number(size.width) > 0 && Number(size.height) > 0)) throw new Error('封面图片数据无效。');
      }
      if (typeof clipboard.writeImage === 'function') clipboard.writeImage(image);
      else {
        if (typeof ClipboardItemClass !== 'function' || typeof BlobClass !== 'function') {
          throw new Error('当前剪贴板 API 缺少图片写入支持。');
        }
        await clipboard.write([new ClipboardItemClass({ 'image/png': new BlobClass([bytes], { type: 'image/png' }) })]);
      }
      return { ok: true };
    } finally {
      clearTimeout(timeoutId);
    }
  }

  return { copy };
}

module.exports = { createCoverClipboard };
