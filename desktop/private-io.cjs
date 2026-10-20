'use strict';
const path = require('node:path');

function accessError(code) {
  const message = code === 'PRIVATE_DATA_TIMEOUT' ?
    '草稿资料目录访问超时；macOS 可能正在等待“下载”文件夹授权，完成系统提示后可重新连接工作台。' :
    '草稿资料目录暂时无法访问，请检查系统提示或目录访问情况后重新连接工作台。';
  return Object.assign(new Error(message), {code});
}

// Filesystem promises may remain pending in a macOS permission prompt. A
// timeout ends the connection attempt, not the syscall: every later stage
// therefore checks its request before publishing a file or returning an FD.
function createPrivateIO({dir, io, pid, shouldContinue = () => true,
  onStatusError = () => {}, onWait = () => {}, timeoutMs = 2000}) {
  let directory, stopped = false, sequence = 0, latestStatus, statusTask;
  let descriptorTail = Promise.resolve(), sessionTail = Promise.resolve(), descriptorContents;
  const active = () => !stopped && shouldContinue();
  const closed = new WeakSet();
  const notify = (callback, value) => { try { callback(value); } catch {} };
  async function close(handle) {
    if (!handle || closed.has(handle)) return;
    closed.add(handle);
    try { await handle.close(); } catch (error) { notify(onStatusError, error); }
  }
  function ensureDirectory() {
    if (!directory) directory = Promise.resolve().then(() => io.mkdir(dir, {recursive:true, mode:0o700}))
      .catch(error => { directory = null; throw error; });
    return directory;
  }
  async function timed(operation, {code = 'PRIVATE_DATA_TIMEOUT', current = active, duration = timeoutMs, waiting} = {}) {
    let expired = false, timer, waitTimer;
    const guard = () => { if (expired || !current()) throw accessError('PRIVATE_IO_CANCELLED'); };
    try {
      if (waiting) waitTimer = setTimeout(() => { if (!expired && current()) notify(onWait, waiting); }, Math.min(500, duration / 2));
      return await Promise.race([
        Promise.resolve().then(() => operation(guard)),
        new Promise((_, reject) => { timer = setTimeout(() => {
          expired = true; reject(accessError(code));
        }, duration); }),
      ]);
    } catch (error) {
      if (['PRIVATE_DATA_TIMEOUT','PRIVATE_IO_CANCELLED'].includes(error.code)) throw error;
      throw accessError('PRIVATE_DATA_UNAVAILABLE');
    } finally { expired = true; clearTimeout(timer); clearTimeout(waitTimer); }
  }
  async function atomic(name, contents, guard) {
    const destination = path.join(dir, name), temporary = destination + '.' + pid + '.' + (++sequence) + '.tmp';
    let committed = false, writeStarted = false;
    try {
      guard(); await ensureDirectory(); guard();
      writeStarted = true; await io.writeFile(temporary, contents, {mode:0o600}); guard();
      await io.chmod(temporary, 0o600); guard();
      await io.rename(temporary, destination); committed = true; guard();
    } catch (error) {
      // Each record type is serialized: clean a stale in-flight rename before
      // the next observation or descriptor can publish its own contents.
      if (committed && ['embedded-browser.json','embedded-session-status.json'].includes(name)) {
        try { if (await io.readFile(destination, 'utf8') === contents) await io.unlink(destination); } catch {}
      }
      throw error;
    } finally { if (writeStarted) { try { await io.unlink(temporary); } catch {} } }
  }
  function status(contents) {
    if (!active()) return;
    try { latestStatus = JSON.stringify(contents, null, 2); }
    catch (error) { notify(onStatusError, error); return; }
    drainStatus();
  }
  function drainStatus() {
    if (statusTask || !active()) return;
    statusTask = (async () => {
      while (latestStatus !== undefined && active()) {
        const value = latestStatus; latestStatus = undefined;
        const guard = () => { if (!active() || latestStatus !== undefined) throw accessError('PRIVATE_IO_CANCELLED'); };
        const waiting = setTimeout(() => { if (active()) notify(onWait, 'status'); }, timeoutMs);
        waiting.unref?.();
        try { await atomic('embedded-app-status.json', value, guard); }
        catch (error) { if (error.code !== 'PRIVATE_IO_CANCELLED') notify(onStatusError, error); }
        finally { clearTimeout(waiting); }
      }
    })().finally(() => {
      statusTask = null;
      if (latestStatus !== undefined && active()) drainStatus();
    });
  }
  async function openLog() {
    try {
      return await timed(async guard => {
        guard(); await ensureDirectory(); guard();
        const handle = await io.open(path.join(dir, 'workbench.log'), 'a', 0o600);
        try { guard(); return handle; } catch (error) { await close(handle); throw error; }
      }, {waiting:'log'});
    } catch (error) {
      // This helper has no spawn operation, so retries after failure are safe.
      error.backendNotStarted = true; throw error;
    }
  }
  function descriptor(contents, current = active) {
    const encoded = JSON.stringify(contents);
    return timed(guard => {
      const request = descriptorTail.then(async () => {
        guard(); await atomic('embedded-browser.json', encoded, guard);
        descriptorContents = encoded;
      });
      descriptorTail = request.catch(() => {});
      return request;
    }, {current:() => active() && current(),waiting:'descriptor'});
  }
  async function session(contents, current = active) {
    return timed(guard => {
      const request = sessionTail.then(() => atomic('embedded-session-status.json', JSON.stringify(contents), guard));
      sessionTail = request.catch(() => {});
      return request;
    }, {current:() => active() && current()});
  }
  async function removeDescriptor() {
    if (descriptorContents === undefined) return;
    const contents = descriptorContents;
    try {
      await timed(async guard => {
        const found = await io.readFile(path.join(dir, 'embedded-browser.json'), 'utf8'); guard();
        if (found === contents) await io.unlink(path.join(dir, 'embedded-browser.json'));
      }, {current:() => true, duration:1000});
    } catch {}
  }
  return {status, openLog, close, descriptor, session, removeDescriptor,
    stop() { stopped = true; latestStatus = undefined; },
    async idle() { while (statusTask) await statusTask; }};
}
module.exports = {createPrivateIO};
