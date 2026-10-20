'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {execFile, spawn} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);
const BUNDLE_ID = 'local.sijunting.social-auto-upload.drafts';
const quote = value => "'" + String(value).replaceAll("'", "'\\''") + "'";
const psQuote = value => "'" + String(value).replaceAll("'", "''") + "'";

function validArchiveEntries(text) {
  const entries = text.split(/\r?\n/).filter(Boolean);
  return entries.length > 0 && entries.every(entry => !entry.includes('\\') && !entry.split('/').includes('..') &&
    (entry === '发布助手.app/' || entry.startsWith('发布助手.app/') || entry.startsWith('__MACOSX/')));
}
async function zipEntries(file) {
  // macOS unzip rewrites UTF-8 Chinese names under some process locales. Read
  // central-directory names directly so validation sees exactly what ditto uses.
  const handle = await fs.promises.open(file, 'r');
  try {
    const {size} = await handle.stat();
    const tail = Buffer.alloc(Math.min(size, 65557));
    await handle.read(tail, 0, tail.length, size - tail.length);
    let end = -1;
    for (let i = tail.length - 22; i >= 0; i--) {
      if (tail.readUInt32LE(i) === 0x06054b50 && i + 22 + tail.readUInt16LE(i + 20) === tail.length) {end = i; break;}
    }
    if (end < 0 || tail.readUInt16LE(end + 4) || tail.readUInt16LE(end + 6)) throw new Error('更新 ZIP 格式无效。');
    const count = tail.readUInt16LE(end + 10), length = tail.readUInt32LE(end + 12), offset = tail.readUInt32LE(end + 16);
    if (count === 65535 || count !== tail.readUInt16LE(end + 8) || length > 16 * 1024 * 1024 || offset + length > size - tail.length + end) throw new Error('更新 ZIP 目录无效。');
    const directory = Buffer.alloc(length);
    if ((await handle.read(directory, 0, length, offset)).bytesRead !== length) throw new Error('更新 ZIP 目录不完整。');
    const entries = []; let cursor = 0;
    for (let i = 0; i < count; i++) {
      if (cursor + 46 > length || directory.readUInt32LE(cursor) !== 0x02014b50 || directory.readUInt16LE(cursor + 8) & 1) throw new Error('更新 ZIP 目录无效。');
      const nameLength = directory.readUInt16LE(cursor + 28), extra = directory.readUInt16LE(cursor + 30), comment = directory.readUInt16LE(cursor + 32);
      if (cursor + 46 + nameLength + extra + comment > length) throw new Error('更新 ZIP 目录不完整。');
      const name = new TextDecoder('utf-8', {fatal: true}).decode(directory.subarray(cursor + 46, cursor + 46 + nameLength));
      if (/[\r\n\0]/.test(name)) throw new Error('更新 ZIP 文件名无效。');
      entries.push(name); cursor += 46 + nameLength + extra + comment;
    }
    if (cursor !== length) throw new Error('更新 ZIP 目录无效。');
    return entries;
  } finally {await handle.close();}
}
async function supportsArchitecture(file, arch) {
  const expected = {arm64: 0x0100000c, x64: 0x01000007}[arch];
  if (!expected) return false;
  const handle = await fs.promises.open(file, 'r');
  try {
    const data = Buffer.alloc(4096), {bytesRead} = await handle.read(data, 0, data.length, 0);
    if (bytesRead < 8) return false;
    if (data.readUInt32LE(0) === 0xfeedfacf) return data.readUInt32LE(4) === expected;
    if (data.readUInt32BE(0) === 0xfeedfacf) return data.readUInt32BE(4) === expected;
    const magic = data.readUInt32BE(0), big = [0xcafebabe, 0xcafebabf].includes(magic);
    if (!big && ![0xbebafeca, 0xbfbafeca].includes(magic)) return false;
    const read = offset => big ? data.readUInt32BE(offset) : data.readUInt32LE(offset);
    const count = read(4), stride = [0xcafebabf, 0xbfbafeca].includes(magic) ? 32 : 20;
    if (!count || count > 64 || 8 + count * stride > bytesRead) return false;
    for (let i = 0; i < count; i++) if (read(8 + i * stride) === expected) return true;
    return false;
  } finally {await handle.close();}
}
function macInstallScript({pid, backend, target, staged, backup, log}) {
  return `#!/bin/sh
exec >>${quote(log)} 2>&1
set -eu
attempt=0
while /bin/kill -0 ${pid} 2>/dev/null; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 120 ] || { echo 'quit_timeout'; exit 1; }
  /bin/sleep 1
done
${backend ? `if /bin/kill -0 ${backend.pid} 2>/dev/null; then
  actual=$(/bin/ps -ww -p ${backend.pid} -o command= | /usr/bin/sed 's/^ *//')
  [ "$actual" = ${quote(backend.executable + ' server')} ] || { echo 'backend_identity_changed'; exit 1; }
  /bin/kill -TERM ${backend.pid}
  attempt=0
  while /bin/kill -0 ${backend.pid} 2>/dev/null; do
    state=$(/bin/ps -p ${backend.pid} -o stat= || true)
    case "$state" in *Z*) break;; esac
    attempt=$((attempt + 1)); [ "$attempt" -lt 30 ] || { echo 'backend_quit_timeout'; exit 1; }
    /bin/sleep 1
  done
fi` : ''}
/usr/bin/codesign --verify --deep --strict ${quote(staged)}
[ ! -e ${quote(backup)} ] || exit 1
/bin/mv ${quote(target)} ${quote(backup)}
if ! /bin/mv ${quote(staged)} ${quote(target)}; then
  /bin/mv ${quote(backup)} ${quote(target)}
  /usr/bin/open ${quote(target)}
  echo 'rolled_back'; exit 1
fi
if ! /usr/bin/codesign --verify --deep --strict ${quote(target)}; then
  /bin/mv ${quote(target)} ${quote(staged)}
  /bin/mv ${quote(backup)} ${quote(target)}
  /usr/bin/open ${quote(target)}
  echo 'rolled_back'; exit 1
fi
/bin/rmdir ${quote(path.dirname(staged))} || true
/usr/bin/open ${quote(target)}
echo 'installed'
`;
}
function windowsInstallScript({pid, backend, file, log}) {
  return `$ErrorActionPreference = 'Stop'
try {
  Wait-Process -Id ${pid} -Timeout 120 -ErrorAction SilentlyContinue
  if (Get-Process -Id ${pid} -ErrorAction SilentlyContinue) { throw 'quit_timeout' }
  ${backend ? `$owner = Get-CimInstance Win32_Process -Filter "ProcessId = ${backend.pid}"
  if ($owner) {
    if ($owner.ExecutablePath -ne ${psQuote(backend.executable)}) { throw 'backend_identity_changed' }
    Stop-Process -Id ${backend.pid} -ErrorAction Stop
    Wait-Process -Id ${backend.pid} -Timeout 30 -ErrorAction SilentlyContinue
    if (Get-Process -Id ${backend.pid} -ErrorAction SilentlyContinue) { throw 'backend_quit_timeout' }
  }` : ''}
  Start-Process -FilePath ${psQuote(file)} -Wait
  'installer_closed' | Set-Content -LiteralPath ${psQuote(log)}
} catch { $_ | Out-String | Set-Content -LiteralPath ${psQuote(log)}; exit 1 }
`;
}
async function prepareInstallation({platform = process.platform, arch = process.arch, file, version, executable,
  cacheDir, backend = null, pid = process.pid}) {
  if (!Number.isSafeInteger(pid) || pid <= 0 || backend && (!Number.isSafeInteger(backend.pid) || backend.pid <= 0)) throw new Error('更新进程身份无效。');
  const work = await fs.promises.mkdtemp(path.join(cacheDir, 'install-'));
  const log = path.join(work, 'installation.log');
  let staging;
  try {
    let command, args, script;
    if (platform === 'darwin') {
      const target = path.resolve(path.dirname(executable), '../..');
      if (path.extname(target) !== '.app') throw new Error('请从已安装的发布助手应用检查更新。');
      await fs.promises.access(path.dirname(target), fs.constants.W_OK);
      const entries = await zipEntries(file);
      if (!validArchiveEntries(entries.join('\n'))) throw new Error('更新 ZIP 的目录结构无效。');
      staging = await fs.promises.mkdtemp(path.join(path.dirname(target), '.publish-assistant-update-'));
      await execute('/usr/bin/ditto', ['-x', '-k', file, staging]);
      const staged = path.join(staging, '发布助手.app');
      const plist = path.join(staged, 'Contents/Info.plist');
      const readPlist = async key => (await execute('/usr/libexec/PlistBuddy', ['-c', `Print :${key}`, plist])).stdout.trim();
      if (await readPlist('CFBundleIdentifier') !== BUNDLE_ID || await readPlist('CFBundleShortVersionString') !== version ||
          await readPlist('CFBundleExecutable') !== 'DraftWorkbench') throw new Error('更新包的应用身份或版本不符。');
      const installedPlist = path.join(target, 'Contents/Info.plist');
      if ((await execute('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleIdentifier', installedPlist])).stdout.trim() !== BUNDLE_ID) throw new Error('当前应用身份不符。');
      if (!await supportsArchitecture(path.join(staged, 'Contents/MacOS/DraftWorkbench'), arch)) throw new Error('更新包与当前 CPU 不匹配。');
      await execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', staged]);
      // Remove the ZIP metadata directory, never data from the user's profile.
      await fs.promises.rm(path.join(staging, '__MACOSX'), {recursive: true, force: true});
      const backup = path.join(path.dirname(target), `.发布助手-previous-${Date.now()}.app`);
      script = path.join(work, 'install.sh');
      await fs.promises.writeFile(script, macInstallScript({pid, backend, target, staged, backup, log}), {mode: 0o700});
      command = '/bin/sh'; args = [script];
    } else if (platform === 'win32') {
      script = path.join(work, 'install.ps1');
      await fs.promises.writeFile(script, '\ufeff' + windowsInstallScript({pid, backend, file, log}), {mode: 0o600});
      command = 'powershell.exe'; args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script];
    } else {throw new Error('当前系统不支持安装更新。');}
    return {log, async launch() {
      const child = spawn(command, args, {detached: true, stdio: 'ignore', windowsHide: true});
      await new Promise((resolve, reject) => {child.once('error', reject); child.once('spawn', resolve);});
      child.unref();
    }, async cancel() {
      if (staging) await fs.promises.rm(staging, {recursive: true, force: true});
      await fs.promises.rm(work, {recursive: true, force: true});
    }};
  } catch (error) {
    if (staging) await fs.promises.rm(staging, {recursive: true, force: true});
    await fs.promises.rm(work, {recursive: true, force: true});
    throw error;
  }
}
module.exports = {BUNDLE_ID, validArchiveEntries, zipEntries, supportsArchitecture, macInstallScript, windowsInstallScript, prepareInstallation};
