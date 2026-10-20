'use strict';

const net = require('node:net');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function connectionError(code, message) {
  return Object.assign(new Error(message), {code});
}

// Bound both the request and its response body, even if an injected transport
// ignores AbortSignal. The losing promise is still handled by Promise.race.
async function withTimeout(operation, timeoutMs, code = 'REQUEST_TIMEOUT') {
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(() => operation(controller.signal)),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(connectionError(code, '连接探测超时。'));
        }, Math.max(1, timeoutMs));
      }),
    ]);
  } finally { clearTimeout(timer); }
}

function requestJSON(url, timeoutMs, fetchImpl = globalThis.fetch) {
  return withTimeout(async signal => {
    const response = await fetchImpl(url, {signal, cache:'no-store', redirect:'error'});
    if (!response.ok) return {ok:false, status:response.status};
    return {ok:true, data:await response.json()};
  }, timeoutMs);
}

function classifyBackendState(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return 'unknown';
  if (typeof data.app === 'string' && data.app !== 'sau-draft-workbench') return 'occupied';
  if (data.app !== 'sau-draft-workbench') return 'unknown';
  if (data.embedded_supported === false || (Number.isSafeInteger(data.api_version) && data.api_version < 2)) return 'incompatible';
  if (data.embedded_supported === true && Number.isSafeInteger(data.api_version) && data.api_version >= 2) return 'compatible';
  return 'unknown';
}

async function probeBackend(baseURL, timeoutMs, fetchImpl = globalThis.fetch) {
  try {
    const response = await requestJSON(`${baseURL}/api/state`, timeoutMs, fetchImpl);
    if (!response.ok) return {status:[408,429,500,502,503,504].includes(response.status) ? 'busy' : 'unknown'};
    return {status:classifyBackendState(response.data)};
  } catch { return {status:'unavailable'}; }
}

function probePort(port, timeoutMs = 500) {
  return new Promise(resolve => {
    let socket, timer, settled = false;
    const finish = result => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket?.destroy();
      resolve(result);
    };
    try {
      socket = net.connect({port, host:'127.0.0.1'});
      socket.once('connect', () => finish(true));
      socket.once('error', error => finish(error.code === 'ECONNREFUSED' ? false : null));
      timer = setTimeout(() => finish(null), Math.max(1, timeoutMs));
    } catch { finish(null); }
  });
}

function createBackendManager({baseURL, port, start, probe = ms => probeBackend(baseURL, ms),
  portOpen = ms => probePort(port, ms), timeoutMs = 12000, requestTimeoutMs = 1200,
  portTimeoutMs = 500, retryDelayMs = 250, now = Date.now, sleep = pause,
  shouldContinue = () => true}) {
  let pending = null, started = false;
  async function connect() {
    const deadline = now() + timeoutMs;
    let listening = null;
    const checkActive = () => {
      if (!shouldContinue()) throw connectionError('CONNECTION_CLOSED', '工作台已关闭。');
    };
    while (now() < deadline) {
      checkActive();
      const result = await probe(Math.max(1, Math.min(requestTimeoutMs, deadline - now())));
      checkActive();
      if (result.status === 'compatible') return;
      if (result.status === 'incompatible') throw connectionError('BACKEND_INCOMPATIBLE', '本机后台版本不兼容，请更新后台后重新连接。');
      if (result.status === 'occupied') throw connectionError('BACKEND_PORT_OCCUPIED', '工作台端口正在被其他服务使用。');
      if (now() >= deadline) break;
      listening = await portOpen(Math.max(1, Math.min(portTimeoutMs, deadline - now())));
      checkActive();
      // A timeout or unknown socket error never proves a free port. Preserve
      // any running service; only an explicit refusal permits one spawn.
      if (listening === false && !started) {
        started = true;
        try { await start(); }
        catch (error) {
          // A timed-out protected-folder open never called spawn; its late FD
          // is closed by the I/O helper. Only that explicit case can retry.
          if(error?.backendNotStarted === true) {
            started=false;
            if(error.code==='CONNECTION_CLOSED')throw error;
            throw connectionError(error.code==='PRIVATE_DATA_TIMEOUT'?'PRIVATE_DATA_TIMEOUT':'PRIVATE_DATA_UNAVAILABLE',
              error.code==='PRIVATE_DATA_TIMEOUT' ? '草稿资料目录访问超时；macOS 可能正在等待“下载”文件夹授权，完成系统提示后可重新连接工作台。' :
              '草稿资料目录暂时无法访问，请检查系统提示或目录访问情况后重新连接工作台。');
          }
          throw connectionError('BACKEND_START_FAILED', '后台未能启动，请查看运行记录。');
        }
      }
      if (now() < deadline) await sleep(Math.min(retryDelayMs, deadline - now()));
    }
    checkActive();
    if (listening !== false) throw connectionError('BACKEND_BUSY', '本机后台暂未响应，可能正在处理任务；可稍后重新连接。');
    throw connectionError(started ? 'BACKEND_START_TIMEOUT' : 'BACKEND_UNAVAILABLE', '后台尚未准备好，请查看运行记录或重新连接。');
  }
  return {
    ensureReady() {
      if (!pending) pending = connect().finally(() => { pending = null; });
      return pending;
    },
  };
}

function parseDevToolsActivePort(text) {
  const lines = String(text).trim().split(/\r?\n/);
  if (lines.length !== 2 || !/^\d{1,5}$/.test(lines[0])) return null;
  const port = Number(lines[0]), resource = lines[1];
  if (port < 1 || port > 65535 || !/^\/devtools\/browser\/[A-Za-z0-9-]+$/.test(resource)) return null;
  return {port, resource};
}

function validateDevToolsEndpoint(value, expected) {
  if (typeof value !== 'string' || !expected) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'ws:' && url.hostname === '127.0.0.1' && Number(url.port) === expected.port
      && url.pathname === expected.resource && !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

async function waitForDebuggingEndpoint({readActivePort, fetchImpl = globalThis.fetch, timeoutMs = 8000,
  requestTimeoutMs = 700, retryDelayMs = 100, now = Date.now, sleep = pause, shouldContinue = () => true}) {
  const deadline = now() + timeoutMs;
  while (now() < deadline && shouldContinue()) {
    try {
      const expected = parseDevToolsActivePort(readActivePort());
      if (expected) {
        const response = await requestJSON(`http://127.0.0.1:${expected.port}/json/version`,
          Math.max(1, Math.min(requestTimeoutMs, deadline - now())), fetchImpl);
        // Also reject a descriptor that changed while this request was pending.
        const current = parseDevToolsActivePort(readActivePort());
        if (shouldContinue() && current?.port === expected.port && current?.resource === expected.resource
          && response.ok && validateDevToolsEndpoint(response.data?.webSocketDebuggerUrl, expected)) {
          return response.data.webSocketDebuggerUrl;
        }
      }
    } catch {}
    if (now() < deadline && shouldContinue()) await sleep(Math.min(retryDelayMs, deadline - now()));
  }
  throw connectionError(shouldContinue() ? 'DEVTOOLS_TIMEOUT' : 'CONNECTION_CLOSED', '内嵌浏览器连接未准备好，可重新连接工作台。');
}

const recoverableCodes = new Set(['BACKEND_BUSY','BACKEND_UNAVAILABLE','BACKEND_START_TIMEOUT',
  'DEVTOOLS_TIMEOUT','WORKBENCH_LOAD_TIMEOUT','ERR_CONNECTION_REFUSED','ERR_CONNECTION_RESET',
  'ERR_CONNECTION_CLOSED','ERR_EMPTY_RESPONSE','ERR_TIMED_OUT']);
async function recoverConnection(connect, {attempts = 2, sleep = pause, shouldContinue = () => true} = {}) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (!shouldContinue()) throw connectionError('CONNECTION_CLOSED', '工作台已关闭。');
    try { return await connect(); }
    catch (error) {
      if (!recoverableCodes.has(error.code) || attempt + 1 === attempts || !shouldContinue()) throw error;
      await sleep(500);
    }
  }
}

module.exports = {withTimeout, requestJSON, classifyBackendState, probeBackend, probePort,
  createBackendManager, parseDevToolsActivePort, validateDevToolsEndpoint, waitForDebuggingEndpoint, recoverConnection};
