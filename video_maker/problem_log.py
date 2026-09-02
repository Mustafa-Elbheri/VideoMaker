from __future__ import annotations

import atexit
import faulthandler
import os
import platform
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import wx

from video_maker.app_info import APP_VERSION
from video_maker.app_paths import user_data_path


# One active circular file.  The writer keeps the newest records and never
# allows the active file to grow beyond one MiB.
MAX_LOG_BYTES = 1024 * 1024
ROTATION_RETAIN_BYTES = 768 * 1024
PENDING_MEMORY_BYTES = 1024 * 1024
FLUSH_INTERVAL_SECONDS = 1.0
FLUSH_SIGNAL_BYTES = 64 * 1024
HEARTBEAT_INTERVAL_SECONDS = 0.75
WATCHDOG_INTERVAL_SECONDS = 2.0
WATCHDOG_STALL_SECONDS = 4.0
WATCHDOG_REPEAT_SECONDS = 10.0
MAX_FIELD_CHARS = 700
MAX_COMMAND_CHARS = 2200

_SESSION_ID = uuid.uuid4().hex[:10]
_SEQUENCE = 0
_SEQUENCE_LOCK = threading.Lock()

_QUEUE = deque()  # (sequence, encoded_record)
_QUEUE_BYTES = 0
_QUEUE_LOCK = threading.RLock()
_QUEUE_EVENT = threading.Event()
_WRITER_THREAD = None
_WRITER_STOP = False

_FILE_LOCK = threading.RLock()
_ACTIVE_LOG_PATH = None
_LAST_WRITE_ERROR = ""
_LAST_WRITE_ERROR_TIME = ""
_WRITE_FAILURE_COUNT = 0
_DROPPED_PENDING_RECORDS = 0

_INSTALLED = False
_RUNTIME_ENABLED = False
_ORIGINAL_SYS_HOOK = None
_ORIGINAL_THREAD_HOOK = None
_ORIGINAL_STORE_ERROR_REPORT = None
_ORIGINAL_MESSAGE_BOX = None
_FATAL_LOG_FILE = None

_UI_HEARTBEATS = {}
_UI_HEARTBEAT_LOCK = threading.RLock()
_WATCHDOG_THREAD = None
_WATCHDOG_STOP = False

_THREAD_STATE = threading.local()


def _utc_timestamp(milliseconds=True):
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds" if milliseconds else "seconds")


def _next_sequence():
    global _SEQUENCE
    with _SEQUENCE_LOCK:
        _SEQUENCE += 1
        return _SEQUENCE


def _primary_problem_log_path():
    return Path(user_data_path("logs", "problem_history.log"))


def _candidate_log_paths():
    candidates = [
        _primary_problem_log_path(),
        Path(tempfile.gettempdir()) / "VideoMaker" / "problem_history.log",
        Path.home() / ".video_maker" / "problem_history.log",
    ]
    result = []
    seen = set()
    for path in candidates:
        try:
            key = os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(Path(path))
    return result


def problem_log_path():
    return Path(_ACTIVE_LOG_PATH) if _ACTIVE_LOG_PATH is not None else _primary_problem_log_path()


def _thread_label():
    thread = threading.current_thread()
    return f"{thread.name}:{thread.ident}" if thread.ident is not None else thread.name


def _compact(value, limit=MAX_FIELD_CHARS):
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = f"<bytes:{len(value)}>"
    elif isinstance(value, bytearray):
        text = f"<bytearray:{len(value)}>"
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        text = "[" + ", ".join(_compact(item, 140) for item in items[:12])
        if len(items) > 12:
            text += f", … +{len(items) - 12}"
        text += "]"
    elif isinstance(value, dict):
        parts = []
        for index, (key, item) in enumerate(value.items()):
            if index >= 12:
                parts.append(f"… +{len(value) - 12}")
                break
            key_text = str(key)
            if any(secret in key_text.lower() for secret in ("password", "passwd", "secret", "token", "api_key", "license_key")):
                item = "<REDACTED>"
            parts.append(f"{key_text}={_compact(item, 140)}")
        text = "{" + ", ".join(parts) + "}"
    else:
        try:
            text = str(value)
        except Exception:
            text = f"<{type(value).__name__}>"
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return text


def _format_fields(fields):
    parts = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if any(secret in str(key).lower() for secret in ("password", "passwd", "secret", "token", "api_key", "license_key")):
            value = "<REDACTED>"
        parts.append(f"{key}={_compact(value)}")
    return " | ".join(parts)


def _trim_bytes(data, limit=MAX_LOG_BYTES):
    limit = max(1, min(int(limit), MAX_LOG_BYTES))
    if len(data) <= limit:
        return data
    data = data[-limit:]
    newline = data.find(b"\n")
    if 0 <= newline < len(data) - 1:
        data = data[newline + 1 :]
    return data[-limit:]


def _set_write_failure(error, path=None):
    global _LAST_WRITE_ERROR, _LAST_WRITE_ERROR_TIME, _WRITE_FAILURE_COUNT
    _WRITE_FAILURE_COUNT += 1
    _LAST_WRITE_ERROR_TIME = _utc_timestamp()
    location = f" at {path}" if path else ""
    _LAST_WRITE_ERROR = f"{type(error).__name__}{location}: {error}"
    try:
        print(f"Video Maker diagnostic log write failure{location}: {error}", file=sys.stderr)
    except Exception:
        pass


def _atomic_replace(path, data, durable=False):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            if durable:
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _write_bounded(path, encoded, durable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _trim_bytes(encoded)
    try:
        current_size = path.stat().st_size
    except OSError:
        current_size = 0

    if current_size + len(encoded) <= MAX_LOG_BYTES:
        with path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            if durable:
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
        return

    try:
        current = path.read_bytes()
    except OSError:
        current = b""
    combined = _trim_bytes(current + encoded, ROTATION_RETAIN_BYTES)
    _atomic_replace(path, combined, durable=durable)


def _write_batch(encoded, durable=False):
    global _ACTIVE_LOG_PATH, _LAST_WRITE_ERROR
    if not encoded:
        return True
    with _FILE_LOCK:
        candidates = []
        if _ACTIVE_LOG_PATH is not None:
            candidates.append(Path(_ACTIVE_LOG_PATH))
        candidates.extend(_candidate_log_paths())
        seen = set()
        for path in candidates:
            try:
                key = os.path.normcase(os.path.abspath(str(path)))
            except Exception:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                _write_bounded(path, encoded, durable=durable)
            except BaseException as error:
                _set_write_failure(error, path)
                continue
            _ACTIVE_LOG_PATH = Path(path)
            _LAST_WRITE_ERROR = ""
            return True
    return False


def _queue_record(sequence, encoded, wake=False):
    global _QUEUE_BYTES, _DROPPED_PENDING_RECORDS
    if len(encoded) > MAX_LOG_BYTES:
        encoded = _trim_bytes(encoded)
    with _QUEUE_LOCK:
        _QUEUE.append((sequence, encoded))
        _QUEUE_BYTES += len(encoded)
        while _QUEUE_BYTES > PENDING_MEMORY_BYTES and len(_QUEUE) > 1:
            _old_sequence, old_encoded = _QUEUE.popleft()
            _QUEUE_BYTES -= len(old_encoded)
            _DROPPED_PENDING_RECORDS += 1
        should_wake = wake or _QUEUE_BYTES >= FLUSH_SIGNAL_BYTES
    _ensure_writer()
    if should_wake:
        _QUEUE_EVENT.set()


def _pending_snapshot():
    with _QUEUE_LOCK:
        return list(_QUEUE)


def _commit_snapshot(snapshot):
    global _QUEUE_BYTES
    if not snapshot:
        return
    last_sequence = snapshot[-1][0]
    with _QUEUE_LOCK:
        while _QUEUE and _QUEUE[0][0] <= last_sequence:
            _sequence, encoded = _QUEUE.popleft()
            _QUEUE_BYTES -= len(encoded)
        _QUEUE_BYTES = max(0, _QUEUE_BYTES)


def _pending_bytes():
    with _QUEUE_LOCK:
        return b"".join(encoded for _sequence, encoded in _QUEUE)


def flush_problem_log(*, durable=False):
    snapshot = _pending_snapshot()
    if not snapshot:
        return True
    encoded = b"".join(item[1] for item in snapshot)
    try:
        written = _write_batch(encoded, durable=durable)
    except BaseException as error:
        _set_write_failure(error, problem_log_path())
        written = False
    if written:
        _commit_snapshot(snapshot)
    return written


def _writer_loop():
    while True:
        _QUEUE_EVENT.wait(FLUSH_INTERVAL_SECONDS)
        _QUEUE_EVENT.clear()
        try:
            flush_problem_log(durable=False)
        except BaseException as error:
            _set_write_failure(error, problem_log_path())
        if _WRITER_STOP:
            try:
                flush_problem_log(durable=True)
            except BaseException as error:
                _set_write_failure(error, problem_log_path())
            return


def _ensure_writer():
    global _WRITER_THREAD
    if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
        return
    _WRITER_THREAD = threading.Thread(target=_writer_loop, name="diagnostic-log-writer", daemon=True)
    _WRITER_THREAD.start()


def trace_event(category, action="", *, level="INFO", immediate=False, **fields):
    """Queue one compact breadcrumb without doing disk I/O on the caller."""
    if getattr(_THREAD_STATE, "inside_trace", False):
        return ""
    _THREAD_STATE.inside_trace = True
    try:
        sequence = _next_sequence()
        line = (
            f"{_utc_timestamp()} #{sequence} session={_SESSION_ID} level={level} "
            f"thread={_thread_label()} category={_compact(category, 120)}"
        )
        if action:
            line += f" action={_compact(action, 160)}"
        field_text = _format_fields(fields)
        if field_text:
            line += " | " + field_text
        line += "\n"
        _queue_record(sequence, line.encode("utf-8", errors="replace"), wake=bool(immediate))
        return line.rstrip()
    except BaseException as error:
        try:
            sequence = _next_sequence()
            fallback = (
                f"{_utc_timestamp()} #{sequence} session={_SESSION_ID} level=CRITICAL "
                f"category=diagnostic_log action=format_failed | error={type(error).__name__}: {error}\n"
            ).encode("utf-8", errors="replace")
            _queue_record(sequence, fallback, wake=True)
        except BaseException:
            pass
        return ""
    finally:
        _THREAD_STATE.inside_trace = False


def _exception_text(exception=None):
    if exception is None:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        if exc_value is None:
            return ""
        return "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    return "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))


def append_problem(context, message="", *, exception=None, details=""):
    message = _compact(message, 4000)
    details = str(details or "").strip()
    exception_details = _exception_text(exception).strip()
    if not message and not details and not exception_details:
        return ""
    parts = [
        "Video Maker problem log entry",
        f"UTC time: {_utc_timestamp(milliseconds=False)}",
        f"Application version: {APP_VERSION}",
        f"Session: {_SESSION_ID}",
        f"Thread: {_thread_label()}",
        f"Context: {_compact(context, 500) or 'unknown'}",
    ]
    if message:
        parts.append(f"Message: {message}")
    if details and details != message:
        parts.extend(("Details:", details))
    if exception_details:
        parts.extend(("Traceback:", exception_details))
    entry = "\n".join(parts).strip()
    sequence = _next_sequence()
    _queue_record(sequence, (entry + "\n\n").encode("utf-8", errors="replace"), wake=True)
    return entry


@contextmanager
def trace_operation(category, action, **fields):
    started = time.perf_counter()
    trace_event(category, f"{action}.start", **fields)
    try:
        yield
    except BaseException as error:
        trace_event(
            category,
            f"{action}.error",
            level="ERROR",
            immediate=True,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            error_type=type(error).__name__,
            error=str(error),
            **fields,
        )
        raise
    else:
        trace_event(
            category,
            f"{action}.end",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            **fields,
        )


def _read_disk_log():
    candidates = []
    if _ACTIVE_LOG_PATH is not None:
        candidates.append(Path(_ACTIVE_LOG_PATH))
    candidates.extend(_candidate_log_paths())
    seen = set()
    for path in candidates:
        try:
            key = os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                data = path.read_bytes()
                if data:
                    return _trim_bytes(data)
        except BaseException as error:
            _set_write_failure(error, path)
    return b""


def _status_record():
    if not _LAST_WRITE_ERROR and not _DROPPED_PENDING_RECORDS:
        return b""
    line = (
        f"{_utc_timestamp()} session={_SESSION_ID} level=CRITICAL category=diagnostic_log "
        f"action=storage_status | write_failures={_WRITE_FAILURE_COUNT} | "
        f"last_write_error={_compact(_LAST_WRITE_ERROR, 1200)} | "
        f"last_write_error_time={_LAST_WRITE_ERROR_TIME} | pending_bytes={_QUEUE_BYTES} | "
        f"dropped_pending_records={_DROPPED_PENDING_RECORDS} | active_path={problem_log_path()}\n"
    )
    return line.encode("utf-8", errors="replace")


def read_problem_log():
    # Never block the wx thread waiting for disk.  The copy contains both the
    # latest file contents and every event still waiting in memory.
    combined = _trim_bytes(_read_disk_log() + _pending_bytes() + _status_record())
    return combined.decode("utf-8", errors="replace").strip()


def copy_problem_log_to_clipboard():
    trace_event("diagnostic_log", "copy_requested", immediate=True)
    text = read_problem_log()
    if not text:
        text = (
            f"{_utc_timestamp()} session={_SESSION_ID} level=CRITICAL "
            "category=diagnostic_log action=empty_snapshot_unexpected"
        )
    if not wx.TheClipboard.Open():
        trace_event("diagnostic_log", "clipboard_open_failed", level="WARNING", immediate=True)
        return False
    try:
        wx.TheClipboard.SetData(wx.TextDataObject(text))
        wx.TheClipboard.Flush()
        trace_event("diagnostic_log", "copied_to_clipboard", bytes=len(text.encode("utf-8")))
        return True
    except BaseException as error:
        trace_event(
            "diagnostic_log",
            "clipboard_copy_failed",
            level="ERROR",
            immediate=True,
            error_type=type(error).__name__,
            error=str(error),
        )
        return False
    finally:
        wx.TheClipboard.Close()


def export_problem_log(destination):
    trace_event("diagnostic_log", "export_requested", destination=destination)
    text = read_problem_log()
    if not text:
        return False
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text + "\n")
            stream.flush()
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    trace_event("diagnostic_log", "exported", destination=target, bytes=len(text.encode("utf-8")))
    return True


def clear_problem_log():
    global _QUEUE_BYTES, _ACTIVE_LOG_PATH, _LAST_WRITE_ERROR
    global _LAST_WRITE_ERROR_TIME, _WRITE_FAILURE_COUNT, _DROPPED_PENDING_RECORDS
    with _QUEUE_LOCK:
        _QUEUE.clear()
        _QUEUE_BYTES = 0
    paths = _candidate_log_paths()
    if _ACTIVE_LOG_PATH is not None:
        paths.insert(0, Path(_ACTIVE_LOG_PATH))
    seen = set()
    for path in paths:
        try:
            key = os.path.normcase(os.path.abspath(str(path)))
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            _set_write_failure(error, path)
    _ACTIVE_LOG_PATH = None
    _LAST_WRITE_ERROR = ""
    _LAST_WRITE_ERROR_TIME = ""
    _WRITE_FAILURE_COUNT = 0
    _DROPPED_PENDING_RECORDS = 0
    trace_event("diagnostic_log", "cleared", immediate=True)


def log_project_state_change(player, attribute, old_value, new_value):
    if old_value == new_value:
        return
    trace_event(
        "project_state",
        "changed",
        attribute=attribute,
        old=old_value,
        new=new_value,
        window=getattr(player, "window_number", None),
        media_kind=getattr(player, "media_kind", None),
        timeline_items=len(getattr(player, "timeline", []) or []),
    )


def note_ui_heartbeat(player, **state):
    """Record liveness at a low rate; this is called by a 15-ms timer."""
    try:
        now = time.monotonic()
        last_seen = float(getattr(player, "_diagnostic_heartbeat_time", 0.0) or 0.0)
        signature = (
            bool(getattr(player, "closing", False)),
            bool(state.get("transform_active")),
            state.get("transform_percent"),
            bool(state.get("save_running")),
            bool(state.get("project_operation_running")),
            bool(state.get("recording_active")),
            bool(state.get("transform_cancelled")),
        )
        previous_signature = getattr(player, "_diagnostic_heartbeat_signature", None)
        important_change = signature != previous_signature and any(bool(value) for value in signature[1:])
        if now - last_seen < HEARTBEAT_INTERVAL_SECONDS and not important_change:
            return
        player._diagnostic_heartbeat_time = now
        player._diagnostic_heartbeat_signature = signature
        key = int(getattr(player, "window_number", id(player)))
        with _UI_HEARTBEAT_LOCK:
            previous = _UI_HEARTBEATS.get(key, {})
            _UI_HEARTBEATS[key] = {
                "last_seen": now,
                "last_report": previous.get("last_report", 0.0),
                "window": key,
                "closing": bool(getattr(player, "closing", False)),
                "dirty": bool(getattr(player, "is_dirty", False)),
                "media_kind": getattr(player, "media_kind", ""),
                "current_time": getattr(player, "current_time", None),
                "timeline_items": len(getattr(player, "timeline", []) or []),
                **state,
            }
    except Exception:
        pass


def remove_ui_heartbeat(player):
    try:
        key = int(getattr(player, "window_number", id(player)))
        with _UI_HEARTBEAT_LOCK:
            _UI_HEARTBEATS.pop(key, None)
    except Exception:
        pass


def _watchdog_loop():
    while not _WATCHDOG_STOP:
        time.sleep(WATCHDOG_INTERVAL_SECONDS)
        now = time.monotonic()
        reports = []
        with _UI_HEARTBEAT_LOCK:
            for state in _UI_HEARTBEATS.values():
                if state.get("closing"):
                    continue
                stale = now - float(state.get("last_seen", now))
                if stale >= WATCHDOG_STALL_SECONDS and now - float(state.get("last_report", 0.0)) >= WATCHDOG_REPEAT_SECONDS:
                    state["last_report"] = now
                    reports.append((dict(state), stale))
        for state, stale in reports:
            state.pop("last_seen", None)
            state.pop("last_report", None)
            trace_event(
                "watchdog",
                "ui_event_loop_stalled",
                level="ERROR",
                immediate=True,
                stale_seconds=round(stale, 3),
                **state,
            )


def _ensure_watchdog():
    global _WATCHDOG_THREAD
    if _WATCHDOG_THREAD is not None and _WATCHDOG_THREAD.is_alive():
        return
    _WATCHDOG_THREAD = threading.Thread(target=_watchdog_loop, name="diagnostic-watchdog", daemon=True)
    _WATCHDOG_THREAD.start()


def _install_error_report_bridge():
    global _ORIGINAL_STORE_ERROR_REPORT
    if _ORIGINAL_STORE_ERROR_REPORT is not None:
        return
    try:
        from video_maker import error_reporting
    except Exception as error:
        trace_event("diagnostic_install", "error_report_bridge_unavailable", level="WARNING", error=str(error))
        return
    _ORIGINAL_STORE_ERROR_REPORT = error_reporting.store_error_report

    def store_error_report_with_history(report):
        append_problem("visible_error_report", details=report)
        return _ORIGINAL_STORE_ERROR_REPORT(report)

    error_reporting.store_error_report = store_error_report_with_history


def _install_unhandled_hooks():
    global _ORIGINAL_SYS_HOOK, _ORIGINAL_THREAD_HOOK
    if _ORIGINAL_SYS_HOOK is None:
        _ORIGINAL_SYS_HOOK = sys.excepthook

        def system_hook(exc_type, exc_value, exc_traceback):
            try:
                details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                append_problem("unhandled_main_thread", str(exc_value), details=details)
                flush_problem_log(durable=True)
            finally:
                _ORIGINAL_SYS_HOOK(exc_type, exc_value, exc_traceback)

        sys.excepthook = system_hook

    if getattr(threading, "excepthook", None) is not None and _ORIGINAL_THREAD_HOOK is None:
        _ORIGINAL_THREAD_HOOK = threading.excepthook

        def thread_hook(args):
            try:
                details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
                append_problem(f"unhandled_thread:{getattr(args.thread, 'name', '')}", str(args.exc_value), details=details)
                flush_problem_log(durable=True)
            finally:
                _ORIGINAL_THREAD_HOOK(args)

        threading.excepthook = thread_hook


def _install_fatal_crash_logging():
    global _FATAL_LOG_FILE
    if _FATAL_LOG_FILE is not None:
        return
    try:
        path = user_data_path("logs", "fatal_crash.log")
        _FATAL_LOG_FILE = open(path, "a", encoding="utf-8", errors="ignore")
        faulthandler.enable(file=_FATAL_LOG_FILE, all_threads=True)
        trace_event("diagnostic_install", "fatal_crash_logging_enabled", path=path)
    except Exception as error:
        _FATAL_LOG_FILE = None
        trace_event("diagnostic_install", "fatal_crash_logging_failed", level="WARNING", error=str(error))



def _install_error_message_bridge():
    """Capture only visible error/warning boxes; normal UI events are untouched."""
    global _ORIGINAL_MESSAGE_BOX
    if _ORIGINAL_MESSAGE_BOX is not None:
        return
    original = wx.MessageBox
    _ORIGINAL_MESSAGE_BOX = original

    def message_box_with_history(message, caption="", style=0, parent=None, *args, **kwargs):
        is_problem = bool(style & (getattr(wx, "ICON_ERROR", 0) | getattr(wx, "ICON_WARNING", 0)))
        if is_problem:
            append_problem(
                "visible_message_box",
                f"{caption}: {message}" if caption else str(message),
            )
        return original(message, caption, style, parent, *args, **kwargs)

    wx.MessageBox = message_box_with_history

def enable_runtime_diagnostics():
    """Start only the cheap stall watchdog after the first frame is visible."""
    global _RUNTIME_ENABLED
    if _RUNTIME_ENABLED:
        return False
    _RUNTIME_ENABLED = True
    _install_error_message_bridge()
    _ensure_watchdog()
    trace_event(
        "diagnostic_install",
        "lightweight_runtime_enabled",
        coverage="explicit_operations,visible_errors,unhandled_exceptions,ui_stalls",
    )
    return True


def enable_deep_diagnostics():
    # Compatibility for older callers.  Global tracing is intentionally gone.
    return False


def install_wx_event_logging():
    # Compatibility for older callers.  Blanket wx filtering is intentionally gone.
    return False


def install_problem_logging():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _ensure_writer()
    _install_error_report_bridge()
    _install_unhandled_hooks()
    _install_fatal_crash_logging()
    trace_event(
        "application",
        "diagnostic_session_start",
        version=APP_VERSION,
        python=sys.version.split()[0],
        operating_system=f"{platform.system()} {platform.release()} ({platform.machine()})",
        process_id=os.getpid(),
        frozen=bool(getattr(sys, "frozen", False)),
        executable=sys.executable,
        working_directory=os.getcwd(),
        argv=sys.argv,
        max_log_bytes=MAX_LOG_BYTES,
        mode="lightweight",
    )
    atexit.register(_shutdown_logging)


def _shutdown_logging():
    global _WRITER_STOP, _WATCHDOG_STOP, _FATAL_LOG_FILE
    try:
        trace_event("application", "logging_shutdown", immediate=True)
    except Exception:
        pass
    _WATCHDOG_STOP = True
    _WRITER_STOP = True
    _QUEUE_EVENT.set()
    try:
        flush_problem_log(durable=True)
    except Exception:
        pass
    if _FATAL_LOG_FILE is not None:
        try:
            faulthandler.disable()
        except Exception:
            pass
        try:
            _FATAL_LOG_FILE.close()
        except Exception:
            pass
        _FATAL_LOG_FILE = None
