"""Install a verified local build, preserving browser profiles and an app rollback.

Stops only this project's idle backend and an installed application whose bundle
identity matches this checkout. The legacy /Applications name remains a migration
entry and is moved into the backup, never deleted. No platform is opened, logged
out, uploaded to or published to by this installer.
"""
import json
import os
import plistlib
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / '.draft-workbench'
LEGACY_APP = Path('/Applications/视频草稿工作台.app')
APP = Path('/Applications/发布助手.app')
LEGACY_STAGED = Path('/Applications/视频草稿工作台-更新中.app')
STAGED = Path('/Applications/发布助手-更新中.app')
CANDIDATE = ROOT / 'dist/embedded/发布助手-darwin-arm64/发布助手.app'
PROFILE = Path.home() / 'Library/Application Support/视频草稿工作台'
URL = 'http://127.0.0.1:18761/api/state'
VERSION = json.loads((ROOT / 'desktop/package.json').read_text())['version']
BUNDLE_ID = 'local.sijunting.social-auto-upload.drafts'
EXECUTABLE_NAME = 'DraftWorkbench'
BUILD_VERSION = '27'


def bundle_executable(app):
    return Path(app) / 'Contents/MacOS' / EXECUTABLE_NAME


def bundle_identity(app):
    plist_path = Path(app) / 'Contents/Info.plist'
    if not plist_path.is_file():
        return None
    try:
        info = plistlib.loads(plist_path.read_bytes())
    except (plistlib.InvalidFileException, OSError):
        return None
    return {
        'bundle_id': info.get('CFBundleIdentifier'),
        'executable': info.get('CFBundleExecutable'),
        'workbench_root': info.get('WorkbenchRoot'),
        'name': info.get('CFBundleName'),
        'display_name': info.get('CFBundleDisplayName'),
        'version': info.get('CFBundleShortVersionString'),
        'build': str(info.get('CFBundleVersion')) if info.get('CFBundleVersion') is not None else None,
    }


def is_project_bundle(app, project_root):
    identity = bundle_identity(app)
    return bool(
        identity
        and identity['bundle_id'] == BUNDLE_ID
        and identity['executable'] == EXECUTABLE_NAME
        and identity['workbench_root'] == str(project_root)
        and bundle_executable(app).is_file()
    )


def classify_installed_app(app, project_root):
    if not Path(app).exists():
        return 'missing'
    if is_project_bundle(app, project_root):
        return 'project'
    return 'foreign'


def read_process_rows():
    result = subprocess.run(['/bin/ps', '-axww', '-o', 'pid=', '-o', 'command='], capture_output=True, text=True)
    rows = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, separator, command = stripped.partition(' ')
        if not separator:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        rows.append((pid, command.strip()))
    return rows


def main_pids(executable, rows):
    needle = str(executable)
    return [pid for pid, command in rows if command == needle or command.startswith(needle + ' ')]


def installation_targets(project_root, legacy_app, app, rows):
    """Return {bundle: pid} for the unique main processes of this project's apps.

    A foreign bundle or more than one main process raises before any process
    would be stopped. Missing bundles and installed apps that are not running
    are omitted.
    """
    located = {}
    for bundle in (Path(legacy_app), Path(app)):
        kind = classify_installed_app(bundle, project_root)
        if kind == 'missing':
            continue
        if kind != 'project':
            raise RuntimeError(f'{bundle} 不是本项目应用，未停止任何进程。')
        pids = main_pids(bundle_executable(bundle), rows)
        if len(pids) > 1:
            raise RuntimeError(f'{bundle} 主进程无法唯一确认，未停止任何进程。')
        if len(pids) == 1:
            located[bundle] = pids[0]
    return located


def state():
    try:
        with urllib.request.urlopen(URL, timeout=10) as response:
            value = json.load(response)
    except (ConnectionError, OSError):
        return None
    if value.get('app') != 'sau-draft-workbench':
        raise RuntimeError('本机端口由其他服务占用，未更新任何应用。')
    if (value.get('job') or {}).get('active'):
        raise RuntimeError('当前有任务执行中，已保留应用与会话，未安装。')
    return value


def backend_pid():
    result = subprocess.run(['/usr/sbin/lsof', '-t', '-iTCP:18761', '-sTCP:LISTEN'], capture_output=True, text=True)
    pids = list(dict.fromkeys(result.stdout.split()))
    if not pids:
        return None
    if len(pids) != 1:
        raise RuntimeError('本机后台进程无法唯一确认，未安装。')
    pid = int(pids[0])
    command = subprocess.check_output(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='], text=True).strip()
    expected = str(ROOT / '.venv/bin/python') + ' -u ' + str(ROOT / 'draft_workbench.py')
    if command != expected:
        raise RuntimeError('端口进程不属于本项目，未安装。')
    return pid


def wait_exit(pid, timeout=20):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
        if not result.stdout.strip() or result.stdout.strip().startswith('Z'):
            return
        time.sleep(.2)
    raise RuntimeError('旧进程尚未退出，已停止更新。')


def process_alive(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
    text = result.stdout.strip()
    return bool(text) and not text.startswith('Z')


def read_status():
    status_file = PRIVATE / 'embedded-app-status.json'
    if not status_file.exists():
        return {}
    return json.loads(status_file.read_text())


def assert_idle():
    state()
    if read_status().get('active'):
        raise RuntimeError('浏览器正在执行操作，未更新。')


def quit_app(app, pid):
    subprocess.run(
        ['/usr/bin/osascript', '-e', f'tell application "{app}" to quit'],
        check=True, capture_output=True)
    wait_exit(pid)


def quit_located_apps(located, idle_check, process_rows, quitter):
    """Recheck the backend job and this bundle's main pid before every quit.

    idle_check must include the backend job.active probe. A change stops the
    remaining quits and any later file move; nothing is quit when the first
    recheck fails.
    """
    for app, app_pid in located.items():
        idle_check()
        if main_pids(bundle_executable(app), process_rows()) != [app_pid]:
            raise RuntimeError(f'{app} 主进程已变化，已停止后续退出和移动。')
        quitter(app, app_pid)


def restore_moved_bundles(moved):
    """Put back every bundle whose backup exists and whose original path is empty.

    Another bundle still sitting at its original path must not suppress this.
    """
    restored = []
    for original, backed in reversed(list(moved)):
        original = Path(original)
        backed = Path(backed)
        if backed.exists() and not original.exists():
            backed.rename(original)
            restored.append(original)
    return restored


def move_into_backup(app, backup):
    destination = backup / app.name
    if destination.exists():
        raise RuntimeError(f'备份里已有 {app.name}，已停止。')
    app.rename(destination)
    return destination


def assert_candidate():
    if not CANDIDATE.is_dir():
        raise RuntimeError('候选 App 不存在，请先构建。')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(CANDIDATE)], check=True)
    if not is_project_bundle(CANDIDATE, ROOT):
        raise RuntimeError('候选 App 不是本项目的发布助手包，未安装。')
    identity = bundle_identity(CANDIDATE)
    if identity['name'] != '发布助手' or identity['display_name'] != '发布助手':
        raise RuntimeError('候选 App 名称不符，未安装。')
    if identity['version'] != VERSION or identity['build'] != BUILD_VERSION:
        raise RuntimeError('候选版本不符，未安装。')


def install():
    if os.environ.get('SAU_INSTALL_AUTHORIZED') != '1':
        raise RuntimeError('未收到安装授权，未修改 /Applications 或任何正式资料。')
    assert_candidate()
    if LEGACY_STAGED.exists() or STAGED.exists():
        raise RuntimeError('存在另一份安装暂存，已停止且保留现场。')
    located = installation_targets(ROOT, LEGACY_APP, APP, read_process_rows())
    assert_idle()
    status_pid = read_status().get('pid')
    if status_pid and process_alive(int(status_pid)) and int(status_pid) not in located.values():
        raise RuntimeError('状态文件中的进程不是已定位的本项目安装包，未停止任何进程。')
    pid = backend_pid()
    quit_located_apps(located, assert_idle, read_process_rows, quit_app)
    if pid:
        assert_idle()
        current = backend_pid()
        if current is None:
            pass
        elif current != pid:
            raise RuntimeError('后台进程在检查后变化，未更新。')
        else:
            os.kill(pid, signal.SIGTERM)
            wait_exit(pid)
    assert_idle()
    backup = PRIVATE / 'backups' / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    if PROFILE.exists():
        shutil.copytree(PROFILE, backup / 'browser-profile', symlinks=True)
    for name in ('accounts', 'jobs', 'workbench-state.json'):
        source = PRIVATE / name
        if source.is_dir():
            shutil.copytree(source, backup / name, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, backup / name)
    moved = []
    try:
        shutil.copytree(CANDIDATE, STAGED, symlinks=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(STAGED)], check=True)
        for app in (LEGACY_APP, APP):
            if app.exists():
                if not is_project_bundle(app, ROOT):
                    raise RuntimeError(f'{app} 不是本项目应用，未替换任何应用。')
                moved.append((app, move_into_backup(app, backup)))
        STAGED.rename(APP)
    except Exception:
        restore_moved_bundles(moved)
        if STAGED.exists():
            shutil.rmtree(STAGED)
        raise
    if os.environ.get('SAU_INSTALL_LAUNCH') == '0':
        print(json.dumps({'status': 'installed_not_started', 'version': VERSION,
                          'build': BUILD_VERSION, 'app': str(APP), 'backup': str(backup),
                          'profile_preserved': True}, ensure_ascii=False, indent=2))
        return
    subprocess.run(['/usr/bin/open', str(APP)], check=True)
    until = time.monotonic() + 30
    while time.monotonic() < until:
        current = state()
        if current and current.get('api_version') == 2:
            installed = read_status()
            if installed.get('version') == VERSION and installed.get('bundlePath') == str(bundle_executable(APP)):
                result = {
                    'status': 'installed', 'version': VERSION, 'build': BUILD_VERSION,
                    'app': str(APP), 'backend_api_version': 2, 'backup': str(backup),
                    'profile': str(PROFILE), 'profile_preserved': True,
                    'legacy_app_backup': str(backup / LEGACY_APP.name) if (backup / LEGACY_APP.name).exists() else None,
                    'real_account_login': 'not_tested', 'real_platform_draft': 'not_tested',
                }
                report = 'installed-app-' + VERSION + '.json'
                (ROOT / 'research' / report).write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
        time.sleep(.25)
    raise RuntimeError('安装已完成，但新版启动尚未确认；备份与浏览器资料已保留。')


if __name__ == '__main__':
    install()
