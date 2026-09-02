from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import uuid
from pathlib import Path
from ctypes import wintypes

from video_maker.app_paths import user_data_path


ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010

MUTEX_NAME = "Local\\AfaqMakkah.VideoMaker.SingleInstance"
STATE_FILE = "single_instance_state.json"
REQUEST_FILE = "single_instance_request.json"
TAKEOVER_WAIT_SECONDS = 90.0
TAKEOVER_POLL_GRACE_SECONDS = 3.0
TAKEOVER_WAIT_SLICE_SECONDS = 0.25


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL
    return user32


def state_path():
    return Path(user_data_path(STATE_FILE))


def request_path():
    return Path(user_data_path(REQUEST_FILE))


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(dict(data), file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _pid_is_running(pid):
    if not pid or os.name != "nt":
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _post_close_to_process_windows(pid):
    if not pid or os.name != "nt":
        return 0
    user32 = _user32()
    sent = 0

    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_windows_proc
    def callback(hwnd, _lparam):
        nonlocal sent
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) == int(pid):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            sent += 1
        return True

    user32.EnumWindows.argtypes = (enum_windows_proc, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return sent


def _wait_for_mutex(handle, timeout_seconds):
    timeout_ms = max(0, int(float(timeout_seconds) * 1000))
    result = _kernel32().WaitForSingleObject(handle, timeout_ms)
    return result in (WAIT_OBJECT_0, WAIT_ABANDONED)


class SingleInstanceGuard:
    def __init__(self, mutex_name=MUTEX_NAME):
        self.mutex_name = mutex_name
        self.handle = None
        self.owned = False
        self.instance_token = uuid.uuid4().hex
        self._handled_request_ids = set()

    def acquire_or_replace_existing(self, wait_seconds=TAKEOVER_WAIT_SECONDS):
        if os.name != "nt":
            self.owned = True
            self.write_owner_state()
            return True

        kernel32 = _kernel32()
        self.handle = kernel32.CreateMutexW(None, True, self.mutex_name)
        if not self.handle:
            return True

        already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if not already_exists:
            self.owned = True
            self.write_owner_state()
            return True

        previous_state = _read_json(state_path())
        target_pid = int(previous_state.get("pid") or 0)
        target_token = str(previous_state.get("token") or "")
        request_sent = False
        if target_pid and target_pid != os.getpid():
            self.request_existing_instance_close(target_pid, target_token)
            request_sent = True

        deadline = time.monotonic() + float(wait_seconds)
        request_started = time.monotonic()
        fallback_close_sent = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if _wait_for_mutex(self.handle, min(TAKEOVER_WAIT_SLICE_SECONDS, remaining)):
                self.owned = True
                self.write_owner_state()
                return True
            if not request_sent:
                previous_state = _read_json(state_path())
                target_pid = int(previous_state.get("pid") or 0)
                target_token = str(previous_state.get("token") or "")
                if target_pid and target_pid != os.getpid():
                    self.request_existing_instance_close(target_pid, target_token)
                    request_started = time.monotonic()
                    request_sent = True
            if (
                target_pid
                and not fallback_close_sent
                and (time.monotonic() - request_started) >= TAKEOVER_POLL_GRACE_SECONDS
            ):
                try:
                    _post_close_to_process_windows(target_pid)
                except Exception:
                    pass
                fallback_close_sent = True

        return False

    def request_existing_instance_close(self, target_pid, target_token):
        request = {
            "action": "replace_instance",
            "request_id": uuid.uuid4().hex,
            "requester_pid": os.getpid(),
            "target_pid": int(target_pid or 0),
            "target_token": str(target_token or ""),
            "created_at": time.time(),
        }
        try:
            _write_json(request_path(), request)
        except Exception:
            pass
        return request

    def write_owner_state(self, hwnd=0):
        if not self.owned:
            return
        state = {
            "pid": os.getpid(),
            "token": self.instance_token,
            "hwnd": int(hwnd or 0),
            "executable": sys.executable,
            "updated_at": time.time(),
        }
        try:
            _write_json(state_path(), state)
        except Exception:
            pass

    def close_request_for_this_instance(self):
        request = _read_json(request_path())
        if request.get("action") != "replace_instance":
            return {}
        request_id = str(request.get("request_id") or "")
        if not request_id or request_id in self._handled_request_ids:
            return {}
        if int(request.get("target_pid") or 0) != os.getpid():
            return {}
        target_token = str(request.get("target_token") or "")
        if target_token and target_token != self.instance_token:
            return {}
        self._handled_request_ids.add(request_id)
        return request

    def release(self):
        if os.name == "nt" and self.handle:
            kernel32 = _kernel32()
            if self.owned:
                try:
                    kernel32.ReleaseMutex(self.handle)
                except Exception:
                    pass
            try:
                kernel32.CloseHandle(self.handle)
            except Exception:
                pass
        self.handle = None
        self.owned = False


def ensure_single_instance():
    guard = SingleInstanceGuard()
    return guard if guard.acquire_or_replace_existing() else None
