"""Desktop runtime paths and native process/file handling for Mac and Windows."""
import ctypes
import os
import signal
import stat
import subprocess
import sys
from pathlib import Path


def runner_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable, 'runner']
    return [sys.executable, '-u', str(Path(__file__).parent / 'draft_runner.py')]


def media_tool(name):
    import shutil
    suffix = '.exe' if os.name == 'nt' else ''
    folder = os.environ.get('SAU_MEDIA_TOOLS')
    if folder:
        tool = Path(folder) / (name + suffix)
        if tool.is_file():
            return str(tool)
        raise ValueError('安装包的视频组件不完整，请重新安装发布助手。')
    tool = shutil.which(name)
    if not tool and sys.platform == 'darwin':
        tool = '/opt/homebrew/bin/' + name
    if not tool or not Path(tool).is_file():
        raise ValueError('视频组件不可用，请使用完整的发布助手安装包。')
    return str(tool)


def process_alive(pid):
    if os.name != 'nt':
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def stop_runner(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # Only this workbench's exact Popen PID and its driver children.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        os.killpg(process.pid, signal.SIGTERM)


def private_stat_ok(info, exact_mode=False):
    if not stat.S_ISREG(info.st_mode):
        return False
    if os.name == 'nt':
        return not getattr(info, 'st_file_attributes', 0) & 0x400
    return info.st_uid == os.getuid() and (
        stat.S_IMODE(info.st_mode) == 0o600 if exact_mode else not info.st_mode & 0o077)


def open_private_read(path):
    if os.name != 'nt':
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Open the reparse point itself, never the target. No write/delete sharing
    # while reading a checkpoint/key; fstat then rejects a reparse point.
    handle = kernel.CreateFileW(str(Path(path).absolute()), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def subprocess_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
