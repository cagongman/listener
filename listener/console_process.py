"""Create a real Windows console without inheriting the tray's null/pipe stdio."""
import os
import subprocess


class ConsoleProcess:
    def __init__(self, handle, pid):
        self.handle = handle
        self.pid = pid

    def poll(self):
        import _winapi
        if _winapi.WaitForSingleObject(self.handle, 0) == _winapi.WAIT_TIMEOUT:
            return None
        return _winapi.GetExitCodeProcess(self.handle)

    def wait(self, timeout=None):
        import _winapi
        milliseconds = _winapi.INFINITE if timeout is None else int(timeout * 1000)
        if _winapi.WaitForSingleObject(self.handle, milliseconds) == _winapi.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        return _winapi.GetExitCodeProcess(self.handle)

    def __del__(self):
        try:
            import _winapi
            _winapi.CloseHandle(self.handle)
        except (ImportError, OSError, AttributeError):
            pass


def spawn_console(command, *, cwd, env=None, hidden=False):
    if os.name != "nt":
        return subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    # Popen would copy the parent's redirected/null handles into STARTUPINFO.
    # Use CPython's Win32 binding directly with STARTF_USESTDHANDLES unset.
    import _winapi
    info = subprocess.STARTUPINFO()
    if hidden:
        info.dwFlags = subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
    handle, thread, pid, _ = _winapi.CreateProcess(
        command[0], subprocess.list2cmdline(command), None, None, False,
        subprocess.CREATE_NEW_CONSOLE | 0x00000400,  # CREATE_UNICODE_ENVIRONMENT
        env, str(cwd), info)
    _winapi.CloseHandle(thread)
    return ConsoleProcess(handle, pid)
