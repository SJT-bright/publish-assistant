'use strict';
const fs = require('node:fs');
const path = require('node:path');

function resolveRuntime({packaged, config, resourcesPath, sourceRoot, appData, downloads, platform, env}) {
  const bundled = packaged && config.bundled === true;
  const backend = bundled ? path.join(resourcesPath, 'backend') : null;
  const root = bundled ? path.join(backend, '_internal') : config.root || sourceRoot;
  let defaultState = path.join(appData, '视频草稿工作台', 'workbench');
  // Keep existing Mac material edits/history, as well as the unchanged legacy
  // browser profile. Do not copy credentials into the application package.
  const legacyState = path.join(downloads, 'social-auto-upload', '.draft-workbench');
  if (platform === 'darwin' && fs.existsSync(path.join(legacyState, 'workbench-state.json'))) {
    defaultState = legacyState;
  }
  const privateDir = env.SAU_DESKTOP_STATE_DIR || env.SAU_WORKBENCH_PRIVATE ||
    (bundled ? defaultState : path.join(root, '.draft-workbench'));
  const command = bundled
    ? [path.join(backend, platform === 'win32' ? 'PublishAssistantBackend.exe' : 'PublishAssistantBackend'), 'server']
    : [path.join(root, platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'), '-u', path.join(root, 'draft_workbench.py')];
  const tools = bundled ? path.join(resourcesPath, 'tools') : env.SAU_MEDIA_TOOLS;
  return {bundled, root, privateDir, command, tools};
}

module.exports = {resolveRuntime};
