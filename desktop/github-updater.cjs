'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {Readable, Transform} = require('node:stream');
const {pipeline} = require('node:stream/promises');

const REPOSITORY = 'SJT-bright/publish-assistant';
const REPOSITORY_URL = `https://github.com/${REPOSITORY}`;
const RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const CHECK_INTERVAL = 4 * 60 * 60 * 1000;
const MAX_PACKAGE_SIZE = 2 * 1024 * 1024 * 1024;

function versionParts(version) {
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+$/.test(version)) throw new Error('版本号格式无效。');
  const parts = version.split('.').map(Number);
  if (!parts.every(Number.isSafeInteger)) throw new Error('版本号格式无效。');
  return parts;
}
function newerVersion(candidate, current) {
  const a = versionParts(candidate), b = versionParts(current);
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}
function releaseAsset(release, current, platform, arch) {
  if (!release || release.draft || release.prerelease) return null;
  const version = String(release.tag_name || '').replace(/^v/, '');
  if (!newerVersion(version, current)) return null;
  if (!['arm64', 'x64'].includes(arch) || !['darwin', 'win32'].includes(platform)) throw new Error('当前系统暂未提供自动更新安装包。');
  const name = platform === 'darwin' ? `Publish-Assistant-${version}-Mac-${arch}.zip`
    : `Publish-Assistant-${version}-Windows-${arch}-Setup.exe`;
  const matches = (release.assets || []).filter(asset => asset.name === name && asset.state === 'uploaded');
  if (matches.length !== 1) throw new Error(`v${version} 尚未提供当前系统的完整安装包，请稍后重试。`);
  const asset = matches[0];
  const expectedURL = `${REPOSITORY_URL}/releases/download/${release.tag_name}/${name}`;
  if (asset.browser_download_url !== expectedURL || !/^sha256:[a-f0-9]{64}$/.test(asset.digest || '') ||
      !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > MAX_PACKAGE_SIZE) {
    throw new Error('更新包地址或 SHA-256 校验信息无效，已停止下载。');
  }
  return {version, name, url: expectedURL, size: asset.size, sha256: asset.digest.slice(7)};
}
async function fileDigest(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
async function verifiedFile(file, asset) {
  try {return (await fs.promises.stat(file)).size === asset.size && await fileDigest(file) === asset.sha256;}
  catch {return false;}
}
async function downloadPackage(asset, file, {fetchImpl = globalThis.fetch, onProgress = () => {}, signal} = {}) {
  await fs.promises.mkdir(path.dirname(file), {recursive: true, mode: 0o700});
  if (await verifiedFile(file, asset)) {onProgress(100); return file;}
  const partial = file + '.part';
  try {
    const response = await fetchImpl(asset.url, {signal: AbortSignal.any([signal || new AbortController().signal, AbortSignal.timeout(60 * 60 * 1000)])});
    if (!response.ok || !response.body) throw new Error(`下载更新失败（HTTP ${response.status}）。`);
    let bytes = 0, percent = -1;
    const hash = crypto.createHash('sha256');
    const meter = new Transform({transform(chunk, _encoding, callback) {
      bytes += chunk.length;
      if (bytes > asset.size) return callback(new Error('更新包大小超出预期。'));
      hash.update(chunk);
      const next = Math.floor(bytes * 100 / asset.size);
      if (next !== percent) {percent = next; onProgress(next);}
      callback(null, chunk);
    }});
    await pipeline(Readable.fromWeb(response.body), meter, fs.createWriteStream(partial, {mode: 0o600}), {signal});
    if (bytes !== asset.size || hash.digest('hex') !== asset.sha256) throw new Error('更新包 SHA-256 或大小校验失败，请重新检查更新。');
    await fs.promises.rm(file, {force: true});
    await fs.promises.rename(partial, file);
    return file;
  } finally {await fs.promises.rm(partial, {force: true});}
}
function createGitHubUpdater({version, platform = process.platform, arch = process.arch, cacheDir,
  fetchImpl = globalThis.fetch, onState = () => {}, enabled = true, intervalMs = CHECK_INTERVAL}) {
  let state = {status: enabled ? 'idle' : 'disabled', currentVersion: version}, pending, timer, startup;
  let stopped = false, ready = null, controller;
  const publish = patch => {state = {...state, ...patch}; if (!stopped) onState({...state});};
  async function performCheck() {
    controller = new AbortController();
    publish({status: 'checking', message: '正在检查 GitHub 新版本…', percent: null});
    try {
      const response = await fetchImpl(RELEASE_API, {headers: {Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'},
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)])});
      if (!response.ok) throw new Error(response.status === 403 || response.status === 429 ? 'GitHub 请求暂时受限，稍后会自动重试。' : `检查更新失败（HTTP ${response.status}）。`);
      const asset = releaseAsset(await response.json(), version, platform, arch);
      if (!asset) {publish({status: 'current', message: '当前已是最新版本', checkedAt: Date.now()}); return;}
      const file = path.join(cacheDir, asset.name);
      publish({status: 'downloading', version: asset.version, percent: 0, message: `正在下载 v${asset.version}`});
      await downloadPackage(asset, file, {fetchImpl, signal: controller.signal, onProgress: percent => publish({percent})});
      ready = {asset, file};
      publish({status: 'ready', version: asset.version, percent: 100, message: `v${asset.version} 已下载，可安装更新`, checkedAt: Date.now()});
    } catch (error) {
      if (!stopped) publish({status: 'error', message: error.name === 'TimeoutError' ? '连接 GitHub 超时，稍后会自动重试。' : error.message});
    }
    return {...state};
  }
  return {
    state: () => ({...state}),
    check() {
      if (!enabled || stopped || ready) return Promise.resolve({...state});
      if (!pending) pending = performCheck().finally(() => {pending = null;});
      return pending;
    },
    start() {
      if (!enabled || stopped || timer) return;
      startup = setTimeout(() => {void this.check();}, 10000); startup.unref?.();
      timer = setInterval(() => {void this.check();}, intervalMs); timer.unref?.();
    },
    async downloaded() {
      if (!ready || !await verifiedFile(ready.file, ready.asset)) throw new Error('更新包尚未准备好或校验失败，请重新启动后检查更新。');
      return {...ready, asset: {...ready.asset}};
    },
    stop() {stopped = true; clearTimeout(startup); clearInterval(timer); controller?.abort();},
  };
}
module.exports = {REPOSITORY_URL, RELEASE_API, CHECK_INTERVAL, newerVersion, releaseAsset, fileDigest, verifiedFile, downloadPackage, createGitHubUpdater};
