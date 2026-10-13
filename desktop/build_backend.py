"""Create an independent backend with the browser driver and UI resources."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
separator = ';' if os.name == 'nt' else ':'
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
           '--name', 'PublishAssistantBackend', '--distpath', str(ROOT / 'build/runtime'),
           '--workpath', str(ROOT / 'build/pyinstaller'), '--specpath', str(ROOT / 'build'),
           '--paths', str(ROOT), '--collect-all', 'patchright', '--collect-all', 'playwright',
           '--add-data', str(ROOT / 'draft_workbench.html') + separator + '.',
           '--add-data', str(ROOT / 'ui-assets') + separator + 'ui-assets']
for module in ('draft_workbench', 'draft_execution', 'uploader.douyin_uploader.main',
               'uploader.xiaohongshu_uploader.main', 'uploader.ks_uploader.main', 'uploader.tencent_uploader.main'):
    command += ['--hidden-import', module]
command.append(str(ROOT / 'desktop/backend_entry.py'))
subprocess.run(command, cwd=ROOT, check=True)
