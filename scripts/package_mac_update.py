"""Package a verified Mac application in the exact layout the updater consumes."""
import argparse
import json
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def package(app, arch, output):
    app = Path(app).resolve()
    version = json.loads((ROOT / 'desktop/package.json').read_text())['version']
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (app.name != '发布助手.app' or info.get('CFBundleIdentifier') != 'local.sijunting.social-auto-upload.drafts'
            or info.get('CFBundleShortVersionString') != version):
        raise ValueError('Application identity/version does not match the release source')
    subprocess.run(['/usr/bin/lipo', str(app / 'Contents/MacOS/DraftWorkbench'), '-verify_arch',
                    'arm64' if arch == 'arm64' else 'x86_64'], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    # Frozen runtime content is build output, never formal App profiles/cookies.
    private_directories = []
    for folder in (app / 'Contents/Resources/backend').rglob('*'):
        if folder.is_dir() and folder.name in {'.draft-workbench', 'cookies', 'cookiesFile', 'logs'}:
            if any(item.is_file() or item.is_symlink() for item in folder.rglob('*')):
                raise ValueError('Private runtime content must not be packaged: ' + folder.name)
            private_directories.append(folder)
    # Some imported adapters create empty cookies directories during QA.
    import shutil
    for folder in private_directories:
        if folder.exists():
            shutil.rmtree(folder)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'Publish-Assistant-{version}-Mac-{arch}.zip'
    if archive.exists():
        raise FileExistsError(archive)
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(archive)], check=True)
    subprocess.run(['/usr/bin/unzip', '-tq', str(archive)], check=True)
    import hashlib
    with archive.open('rb') as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        digest = checksum.hexdigest()
    (output / 'SHA256SUMS-Mac.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    return {'version': version, 'arch': arch, 'file': str(archive), 'sha256': digest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', required=True)
    parser.add_argument('--arch', choices=['arm64', 'x64'], required=True)
    parser.add_argument('--output', default=str(ROOT / 'release-assets'))
    args = parser.parse_args()
    print(json.dumps(package(args.app, args.arch, args.output), ensure_ascii=False))
