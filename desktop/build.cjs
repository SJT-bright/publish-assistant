'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');
const pkg = require('./package.json');
const platform = process.env.PUBLISH_ASSISTANT_TARGET || process.platform;
const arch = process.env.PUBLISH_ASSISTANT_ARCH || process.arch;
const targetBackend = path.join(ROOT, 'build/runtime/PublishAssistantBackend');
const targetTools = process.env.PUBLISH_ASSISTANT_TOOLS || path.join(ROOT, 'build/media/tools');

async function build() {
  if (!['darwin','win32'].includes(platform)) throw new Error('Build on Mac or Windows');
  for (const file of [platform === 'win32' ? 'PublishAssistantBackend.exe' : 'PublishAssistantBackend', '_internal/draft_workbench.html']) {
    if (!fs.existsSync(path.join(targetBackend,file))) throw new Error('Build the bundled backend first: '+file);
  }
  for (const tool of ['ffmpeg','ffprobe']) {
    if (!fs.existsSync(path.join(targetTools,tool+(platform==='win32'?'.exe':'')))) throw new Error('Missing bundled '+tool);
  }
  const {packager} = await import('@electron/packager');
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'publish-assistant-build-'));
  try {
    for (const f of fs.readdirSync(__dirname)) {
      if (/\.(cjs|html|js)$/.test(f) && f!=='build.cjs') fs.copyFileSync(path.join(__dirname,f),path.join(stage,f));
    }
    fs.cpSync(path.join(ROOT,'ui-assets'),path.join(stage,'ui-assets'),{recursive:true});
    let revision='development';try{revision=execFileSync('git',['rev-parse','HEAD'],{cwd:ROOT,encoding:'utf8'}).trim();}catch{}
    fs.writeFileSync(path.join(stage,'runtime.json'), JSON.stringify({bundled:true,version:pkg.version,source_revision:revision}));
    fs.writeFileSync(path.join(stage,'package.json'),JSON.stringify({name:pkg.name,productName:pkg.productName,version:pkg.version,main:pkg.main,author:pkg.author,description:pkg.description,license:'MIT'}));
    const output = await packager({dir:stage,name:'发布助手',executableName:'DraftWorkbench',platform,arch,
      electronVersion:pkg.devDependencies.electron,electronZipDir:process.env.PUBLISH_ASSISTANT_ELECTRON_ZIPS,
      out:path.join(ROOT,'dist'),overwrite:true,asar:true,appBundleId:'local.sijunting.social-auto-upload.drafts',
      appVersion:pkg.version,buildVersion:'28',icon:path.join(__dirname,platform==='win32'?'AppIcon.ico':'AppIcon.icns'),
      extendInfo:platform==='darwin'?{CFBundleDisplayName:'发布助手',LSMinimumSystemVersion:'13.0',WorkbenchBundledRuntime:true}:undefined});
    const folder = output[0];
    const resources = platform==='darwin'?path.join(folder,'发布助手.app/Contents/Resources'):path.join(folder,'resources');
    fs.cpSync(targetBackend,path.join(resources,'backend'),{recursive:true,verbatimSymlinks:true});
    fs.cpSync(targetTools,path.join(resources,'tools'),{recursive:true,verbatimSymlinks:true});
    fs.copyFileSync(path.join(ROOT,'LICENSE'),path.join(resources,'Publish-Assistant-LICENSE.txt'));
    fs.copyFileSync(path.join(ROOT,'NOTICE.md'),path.join(resources,'NOTICE.md'));
    if(platform==='darwin') {
      const app = path.join(folder,'发布助手.app');
      execFileSync('/usr/bin/codesign',['--force','--deep','--sign','-',app],{stdio:'inherit'});
      execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',app],{stdio:'inherit'});
    }
    console.log(JSON.stringify({app:folder,version:pkg.version,platform,arch,bundled:true}));
  } finally {fs.rmSync(stage,{recursive:true,force:true});}
}
build().catch(e=>{console.error(e);process.exitCode=1;});
