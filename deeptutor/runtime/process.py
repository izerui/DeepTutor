"""Cross-platform process liveness checks with no process-side effects."""

from __future__ import annotations

import os
from typing import Literal


def _is_windows_process_alive(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return False
        kernel32 = win_dll("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return False


def is_process_alive(pid: int | None) -> bool:
    """Return whether ``pid`` is alive without signalling or terminating it."""

    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        # CPython implements ordinary os.kill signals with TerminateProcess on
        # Windows, so os.kill(pid, 0) is destructive rather than a POSIX probe.
        return _is_windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_probe_liveness(pid: int) -> Literal["alive", "dead", "unknown"]:
    try:
        import ctypes
        from ctypes import wintypes

        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            return "unknown"
        kernel32 = win_dll("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            # psutil treats OpenProcess error 87 (ERROR_INVALID_PARAMETER)
            # for a validated DWORD pid as "no such process"; every other
            # failure (including access denied) stays unknown.
            return "dead" if ctypes.get_last_error() == 87 else "unknown"
        state: Literal["alive", "dead", "unknown"] = "unknown"
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                state = "alive" if exit_code.value == 259 else "dead"
        finally:
            # A failed close means the handle state is untrustworthy; the
            # computed state may only be reported when the close succeeded.
            closed = kernel32.CloseHandle(handle)
        return state if closed else "unknown"
    except (AttributeError, ImportError, OSError):
        return "unknown"


def probe_process_liveness(pid: int) -> Literal["alive", "dead", "unknown"]:
    """Classify ``pid`` as alive, dead or unknown without signalling it.

    ``unknown`` is returned for invalid pids and for any probe failure so
    callers can stay conservative; only confirmed liveness or exit is
    reported. Windows never uses ``os.kill``, which terminates processes.
    """

    if type(pid) is not int or pid <= 0:
        return "unknown"
    if os.name == "nt":
        # CPython implements ordinary os.kill signals with TerminateProcess
        # on Windows, so os.kill(pid, 0) is destructive rather than a probe.
        if pid > 0xFFFFFFFF:
            return "unknown"
        return _windows_probe_liveness(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except (OverflowError, OSError):
        # Permission errors and overflow cannot confirm either state.
        return "unknown"
    return "alive"


__all__ = ["is_process_alive", "probe_process_liveness"]
