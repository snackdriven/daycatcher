"""Process/lock health checks; no desktop or window APIs."""
from contextlib import contextmanager
import json
import os


@contextmanager
def worker_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    stream = (directory / 'local-worker.lock').open('a+b')
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('A local collector worker is already running for this data directory') from error
        yield
    finally:
        stream.close()


def local_is_locked(directory):
    try:
        with worker_lock(directory):
            return False
    except RuntimeError:
        return True


def process_alive(pid):
    if type(pid) is not int or pid < 1:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def worker_health(directory):
    result = {}
    for name in ('local', 'claude'):
        file = directory / f'{name}-status.json'
        if not file.exists():
            continue
        try:
            value = json.loads(file.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('invalid status')
        except (ValueError, OSError):
            result[name] = {'state': 'unknown', 'reason': 'unreadable_status'}
            continue
        if name == 'local':
            if value.get('state') == 'running' and not local_is_locked(directory):
                value.update(state='stopped', stale=True)
        elif value.get('active'):
            try:
                lock_pid = int((directory / 'claude-bridge.lock').read_text())
            except (OSError, ValueError):
                lock_pid = None
            if lock_pid != value.get('pid') or not process_alive(lock_pid):
                value.update(active=False, stale=True)
        result[name] = value
    return result
