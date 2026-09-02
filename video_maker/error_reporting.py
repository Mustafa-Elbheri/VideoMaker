"""Central, privacy-safe error reporting for visible and unhandled errors.

The module keeps at most one report on disk.  Every newly displayed error
replaces the previous report.  A successful copy removes the stored report so
users do not accumulate diagnostic logs.
"""

from __future__ import annotations

import os
import platform
import re
import socket
import sys
import threading
import traceback
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import wx

from video_maker.app_info import APP_VERSION
from video_maker.app_paths import user_data_path
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.operation_control import is_operation_cancelled


_REPORT_LOCK = threading.RLock()
_ORIGINAL_MESSAGE_BOX = None
_INSTALLED = False
_LAST_REPORT = ""


def _set_accessible_name(control, name):
    text = str(name or "")
    for method_name in ("SetName", "SetAccessibleName"):
        method = getattr(control, method_name, None)
        if method is None:
            continue
        try:
            method(text)
        except Exception:
            pass


def error_log_path():
    return user_data_path("logs", "error.log")


def _legacy_log_paths():
    # Previous updater versions used a separate accumulating log.  Removing it
    # after a successful copy honours the same no-accumulation rule.
    return (
        user_data_path("logs", "update.log"),
        user_data_path("logs", "update.log.old"),
    )


def _known_private_values():
    values = set()
    for name in (
        "USERPROFILE",
        "HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "USERNAME",
        "USER",
        "COMPUTERNAME",
    ):
        value = os.environ.get(name)
        if value:
            values.add(value)
    try:
        values.add(str(Path.home()))
    except Exception:
        pass
    try:
        values.add(socket.gethostname())
    except Exception:
        pass
    return sorted((value for value in values if value), key=len, reverse=True)


def _redact_url(match):
    text = match.group(0)
    try:
        parsed = urllib.parse.urlsplit(text)
        host = str(parsed.hostname or "").lower()
        safe_hosts = {
            "github.com",
            "api.github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
        if host not in safe_hosts:
            return "<URL>"
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        return "<URL>"


def sanitize_details(value):
    """Remove user-identifying data while retaining technical diagnostics."""
    text = str(value or "")
    if not text:
        return ""

    for private_value in _known_private_values():
        text = re.sub(re.escape(private_value), "<PRIVATE>", text, flags=re.IGNORECASE)

    # URLs remain useful, but query strings and fragments may contain tokens.
    text = re.sub(r"https?://[^\s<>]+", _redact_url, text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "<EMAIL>", text)

    def redact_ip_line(line):
        # Protect version fields before redacting IPv4 addresses.  This keeps
        # values such as app_version=1.3.4.0 while still removing a real IP if
        # it appears elsewhere on the same line.
        protected = {}

        def protect_version(match):
            token = f"<VERSION_{len(protected)}>"
            protected[token] = match.group(0)
            return token

        version_pattern = (
            r"(?i)\b(?:Application version|Python|app_version|python_version|python)"
            r"\s*[:=]\s*\d+(?:\.\d+){1,3}"
        )
        line = re.sub(version_pattern, protect_version, line)
        line = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", line)
        for token, value in protected.items():
            line = line.replace(token, value)
        return line

    text = "\n".join(redact_ip_line(line) for line in text.splitlines())

    # Remove quoted paths first so spaces inside a path do not leak.
    text = re.sub(r"[\"](?:[A-Za-z]:\\|\\\\)[^\"]+[\"]", "<PATH>", text)
    text = re.sub(r"['](?:[A-Za-z]:\\|\\\\)[^']+[']", "<PATH>", text)
    # Remove unquoted UNC, Windows, private-root, and common Unix paths.
    text = re.sub(r"\\\\[^\s\\/:*?\"<>|]+\\[^\s;,)\]}>]+", "<PATH>", text)
    text = re.sub(r"\b[A-Za-z]:\\[^\s;,)\]}>]+", "<PATH>", text)
    text = re.sub(r"<PRIVATE>(?:\\[^\s;,)\]}>]+)+", "<PATH>", text)
    text = re.sub(r"(?<![A-Za-z0-9_])/(?:home|Users|mnt|tmp|var/tmp)/[^\s;,)\]}>]+", "<PATH>", text)

    private_extensions = (
        "mp4|mkv|mov|m4v|webm|avi|wmv|mpg|mpeg|mp3|m4a|aac|wav|flac|ogg|opus|wma|aiff|"
        "jpg|jpeg|png|bmp|webp|gif|srt|vtt|ass|ssa|txt|json|zip|rar|7z"
    )
    safe_names = {"version.json", "accessible_manifest.json"}

    def redact_filename(match):
        name = match.group(0)
        return name if name.lower() in safe_names else "<FILE>"

    text = re.sub(
        rf"(?i)\b[^\s\\/:*?\"'<>|]+\.(?:{private_extensions})\b",
        redact_filename,
        text,
    )

    # Remove common explicit identity fields if they reach a third-party error.
    text = re.sub(
        r"(?im)^(\s*(?:user(?:name)?|computer(?:name)?|host(?:name)?)\s*[:=]\s*).+$",
        r"\1<PRIVATE>",
        text,
    )
    return text.strip()


def _current_language():
    try:
        from video_maker.app_state import get_language

        return str(get_language() or "ar")
    except Exception:
        return "ar"


def _exception_details(exception=None):
    if exception is None:
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_value is not None:
            exception = exc_value
            return (
                f"{exc_type.__name__}: {exc_value}",
                "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            )
        return "", ""

    return (
        f"{type(exception).__name__}: {exception}",
        "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)),
    )


def build_error_report(
    message,
    title="",
    *,
    exception=None,
    context="",
    technical_details="",
):
    error_id = uuid.uuid4().hex[:12]
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    exception_summary, exception_traceback = _exception_details(exception)
    if not exception_traceback and not technical_details:
        # A validation or worker-result error may have no active exception.  A
        # short call stack still identifies the code path that displayed it.
        exception_traceback = "".join(traceback.format_stack(limit=18)[:-2])

    sections = [
        "Video Maker error report",
        f"Error ID: {error_id}",
        f"UTC time: {timestamp}",
        f"Application version: {APP_VERSION}",
        f"Language: {_current_language()}",
        f"Operating system: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
        f"Context: {context or 'visible_error'}",
        f"Title: {title or tr('خطأ')}",
        f"Message: {message}",
    ]
    if exception_summary:
        sections.append(f"Exception: {exception_summary}")
    if technical_details:
        sections.extend(("Technical details:", str(technical_details)))
    if exception_traceback:
        sections.extend(("Traceback or call path:", exception_traceback))
    return sanitize_details("\n".join(sections))


def store_error_report(report):
    """Overwrite the report file so diagnostics never accumulate."""
    global _LAST_REPORT
    text = sanitize_details(report)
    if not text:
        return ""
    with _REPORT_LOCK:
        _LAST_REPORT = text
        try:
            path = error_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception:
            pass
    return text


def store_error_details(details, context="internal"):
    text = str(details or "").strip()
    if not text:
        return ""
    # Already formatted reports should not be wrapped repeatedly.  Update
    # diagnostics are intentionally wrapped with app/version metadata once.
    if text.startswith("Video Maker error report"):
        report = text
    else:
        report = build_error_report(
            tr("تعذر إكمال العملية"),
            tr("خطأ"),
            context=context,
            technical_details=text,
        )
    return store_error_report(report)


def latest_error_report(fallback=""):
    with _REPORT_LOCK:
        if _LAST_REPORT:
            return _LAST_REPORT
        try:
            return error_log_path().read_text(encoding="utf-8").strip()
        except OSError:
            return sanitize_details(fallback)


def clear_error_report():
    """Delete all current/legacy diagnostic logs after a successful copy."""
    global _LAST_REPORT
    with _REPORT_LOCK:
        _LAST_REPORT = ""
        paths = (error_log_path(),) + _legacy_log_paths()
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass


def _find_speech_callback(parent):
    current = parent
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        callback = getattr(current, "say", None)
        if callable(callback):
            return callback
        getter = getattr(current, "GetParent", None)
        if not callable(getter):
            break
        try:
            current = getter()
        except Exception:
            break
    return None


class ErrorReportDialog(wx.Dialog):
    """Visible error with one copy button and no exposed technical jargon."""

    def __init__(
        self,
        parent,
        message,
        details,
        speech_callback=None,
        title=None,
        close_accessible_name=None,
    ):
        super().__init__(
            parent,
            title=str(title or tr("خطأ")),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        self.details = sanitize_details(details)
        self.speech_callback = speech_callback or _find_speech_callback(parent)

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        message_label = wx.StaticText(panel, label=str(message or ""))
        message_label.Wrap(540)
        _set_accessible_name(message_label, str(message or ""))
        main_sizer.Add(message_label, 0, wx.ALL | wx.EXPAND, 12)

        help_label = wx.StaticText(
            panel,
            label=tr("يمكنك نسخ تفاصيل الخطأ وإرسالها إلى المطور."),
        )
        help_label.Wrap(540)
        _set_accessible_name(help_label, tr("يمكنك نسخ تفاصيل الخطأ وإرسالها إلى المطور."))
        main_sizer.Add(help_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.copy_button = wx.Button(panel, label=tr("نسخ التفاصيل"))
        _set_accessible_name(self.copy_button, tr("نسخ تفاصيل الخطأ"))
        self.copy_button.Enable(bool(self.details))
        self.copy_button.Bind(wx.EVT_BUTTON, self.on_copy_details)
        button_sizer.Add(self.copy_button, 0, wx.RIGHT, 8)

        self.close_button = wx.Button(panel, wx.ID_CLOSE, label=tr("إغلاق"))
        _set_accessible_name(
            self.close_button,
            close_accessible_name or tr("إغلاق نافذة الخطأ"),
        )
        self.close_button.Bind(wx.EVT_BUTTON, self.on_close)
        self.close_button.SetDefault()
        button_sizer.Add(self.close_button, 0)
        main_sizer.Add(button_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 12)

        panel.SetSizer(main_sizer)
        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        outer_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(outer_sizer)
        self.SetMinSize((540, self.GetSize().GetHeight()))
        self.CentreOnParent()

        bind_dialog_keys(self, self.on_char_hook)
        wx.CallAfter(self.copy_button.SetFocus if self.details else self.close_button.SetFocus)

    def _speak(self, text):
        if not self.speech_callback:
            return
        try:
            self.speech_callback(text)
        except TypeError:
            try:
                self.speech_callback(text, False)
            except Exception:
                pass
        except Exception:
            pass

    def on_copy_details(self, event=None):
        details = self.details or latest_error_report()
        if not details:
            return
        copied = False
        try:
            if wx.TheClipboard.Open():
                try:
                    wx.TheClipboard.SetData(wx.TextDataObject(details))
                    wx.TheClipboard.Flush()
                    copied = True
                finally:
                    wx.TheClipboard.Close()
        except Exception:
            copied = False

        if copied:
            clear_error_report()
            self.details = ""
            self.copy_button.Enable(False)
            self._speak(tr("تم نسخ تفاصيل الخطأ"))
        else:
            self._speak(tr("تعذر نسخ تفاصيل الخطأ"))

    def on_close(self, event=None):
        self.EndModal(wx.ID_CLOSE)

    def on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_close()
            return
        event.Skip()


def show_error(
    message,
    title=None,
    parent=None,
    *,
    exception=None,
    context="",
    technical_details="",
    speech_callback=None,
):
    # الإلغاء قرار طبيعي من المستخدم. هذه الحماية المركزية تمنع أي مسار قديم
    # أو مؤثر يضاف مستقبلًا من تحويله إلى نافذة/تقرير خطأ حتى لو لم يلتقطه
    # العامل الخاص به محليًا.
    if is_operation_cancelled(exception) or is_operation_cancelled(message):
        if callable(speech_callback):
            try:
                speech_callback(tr("تم إلغاء العمل"))
            except Exception:
                pass
        return getattr(wx, "ID_CANCEL", getattr(wx, "CANCEL", 0))
    report = build_error_report(
        message,
        title or tr("خطأ"),
        exception=exception,
        context=context,
        technical_details=technical_details,
    )
    store_error_report(report)
    dialog = ErrorReportDialog(
        parent,
        message,
        report,
        speech_callback=speech_callback,
        title=title or tr("خطأ"),
    )
    try:
        dialog.ShowModal()
    finally:
        dialog.Destroy()
    return getattr(wx, "ID_OK", getattr(wx, "OK", 0))


def show_prepared_error(
    parent,
    message,
    details,
    *,
    title=None,
    speech_callback=None,
    context="prepared_error",
    close_accessible_name=None,
):
    report = str(details or "").strip()
    if not report.startswith("Video Maker error report"):
        report = build_error_report(
            message,
            title or tr("خطأ"),
            context=context,
            technical_details=report,
        )
    report = store_error_report(report)
    dialog = ErrorReportDialog(
        parent,
        message,
        report,
        speech_callback=speech_callback,
        title=title or tr("خطأ"),
        close_accessible_name=close_accessible_name,
    )
    try:
        return dialog.ShowModal()
    finally:
        dialog.Destroy()


def _message_box_proxy(message, caption="", style=0, parent=None, *args, **kwargs):
    if style & getattr(wx, "ICON_ERROR", 0):
        exception = sys.exc_info()[1]
        return show_error(
            message,
            caption or tr("خطأ"),
            parent,
            exception=exception,
            context="wx.MessageBox",
        )
    return _ORIGINAL_MESSAGE_BOX(message, caption, style, parent, *args, **kwargs)


def _install_exception_hooks():
    previous_sys_hook = sys.excepthook

    def system_hook(exc_type, exc_value, exc_traceback):
        if is_operation_cancelled(exc_value):
            return
        try:
            report = build_error_report(
                str(exc_value) or exc_type.__name__,
                tr("خطأ"),
                exception=exc_value,
                context="unhandled_main_thread",
            )
            store_error_report(report)
        finally:
            previous_sys_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = system_hook

    previous_thread_hook = getattr(threading, "excepthook", None)
    if previous_thread_hook is not None:
        def thread_hook(args):
            if is_operation_cancelled(getattr(args, "exc_value", None)):
                return
            try:
                report = build_error_report(
                    str(args.exc_value) or args.exc_type.__name__,
                    tr("خطأ"),
                    exception=args.exc_value,
                    context=f"unhandled_thread:{getattr(args.thread, 'name', '')}",
                )
                store_error_report(report)
            finally:
                previous_thread_hook(args)

        threading.excepthook = thread_hook


def install_error_reporting():
    """Install once; information/warning/question dialogs remain untouched."""
    global _INSTALLED, _ORIGINAL_MESSAGE_BOX
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_MESSAGE_BOX = wx.MessageBox
    wx.MessageBox = _message_box_proxy
    _install_exception_hooks()
