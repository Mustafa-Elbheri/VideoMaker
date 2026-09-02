"""نظام تخزين مؤقت مركزي لنتائج ffprobe/ffmpeg.

بدلاً من تشغيل subprocess في كل استدعاء لـ get_media_duration /
has_video_stream / media_info_text / ffmpeg_parse_infos، نشغّل ffprobe مرة
واحدة فقط لكل ملف ونخزّن كل النتائج في cache واحد.

المفتاح: (المسار_المعتدل, الحجم, mtime) — لو الملف تغير يُعاد الفحص.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading

from video_maker.app_paths import ffmpeg_binary, ffprobe_binary


_AUDIO_ONLY_EXTENSIONS = frozenset({
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".ac3", ".amr", ".ape", ".mka",
})


def _startupinfo():
    si = None
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return si


def _cache_key(path):
    try:
        s = os.stat(path)
        return (os.path.normcase(os.path.abspath(path)), s.st_size, s.st_mtime)
    except OSError:
        return None


def _is_attached_picture_stream(line):
    normalized = str(line or "").lower().replace("_", " ")
    return "attached pic" in normalized


def _probe(path):
    """شغّل ffprobe مرة واحدة فقط وأعد كل النتائج المطلوبة."""
    key = _cache_key(path)
    if key is None:
        return None

    # --- ffprobe: duration + stream types ---
    raw_info = {}
    duration = 0.0
    try:
        cmd = [
            ffprobe_binary(), "-v", "error",
            "-print_format", "json",
            "-show_streams", "-show_format",
            path,
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, startupinfo=_startupinfo(),
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout.decode("utf-8", errors="ignore") or "{}")
            streams = data.get("streams") or []
            video = next(
                (s for s in streams
                 if s.get("codec_type") == "video"
                 and not s.get("disposition", {}).get("attached_pic")),
                None,
            )
            audio = next(
                (s for s in streams if s.get("codec_type") == "audio"),
                None,
            )
            if video:
                w = int(video.get("width") or 0)
                h = int(video.get("height") or 0)
                if w and h:
                    raw_info["video_size"] = [w, h]
                rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
                if rate and rate != "0/0":
                    from fractions import Fraction
                    raw_info["video_fps"] = float(Fraction(rate))
            dur_val = (video or audio or {}).get("duration") or (
                data.get("format") or {}
            ).get("duration")
            if dur_val not in (None, "", "N/A"):
                duration = float(dur_val)
                raw_info["duration"] = duration
    except Exception:
        pass

    # --- ffmpeg -i: info_text (للاستعلام عن codec/completeness) ---
    info_text = ""
    try:
        cmd = [ffmpeg_binary(), "-i", path]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, startupinfo=_startupinfo(),
        )
        info_text = proc.stderr.decode("utf-8", errors="ignore")
    except Exception:
        pass

    # --- تحديد الأنواع من info_text ---
    has_video = False
    has_audio = False
    if info_text:
        video_lines = [
            l for l in info_text.splitlines() if " Video: " in l
        ]
        if video_lines:
            ext = os.path.splitext(path or "")[1].lower()
            if ext not in _AUDIO_ONLY_EXTENSIONS:
                has_video = any(
                    not _is_attached_picture_stream(l) for l in video_lines
                )
        has_audio = any(
            " Audio: " in l for l in info_text.splitlines()
        )

    # --- fallback parsing من ffmpeg stderr إذا ffprobe فشل ---
    if not raw_info.get("duration") and info_text:
        m = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info_text
        )
        if m:
            duration = (
                int(m.group(1)) * 3600
                + int(m.group(2)) * 60
                + float(m.group(3))
            )
            raw_info["duration"] = duration

    return {
        "key": key,
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "info_text": info_text,
        "raw_info": raw_info,
    }


# ── Cache store ──────────────────────────────────────────────────────────────

_cache: dict = {}
_lock = threading.Lock()


def cached_media_info(path):
    """يعيد dict فيه duration, has_video, has_audio, info_text, raw_info.

    تُشغّل subprocess مرة واحدة فقط لكل ملف. النتائج تُخزّن في cache
    وتُلاحَظ إذا تغير الملف (mtime/size).
    """
    key = _cache_key(path)
    if key is None:
        return None
    with _lock:
        entry = _cache.get(key)
        if entry is not None:
            return entry
    # خارج الـ lock: تشغيل ffprobe (أبطأ من الانتظار على lock)
    result = _probe(path)
    if result is not None:
        with _lock:
            _cache[key] = result
    return result


def invalidate_media_cache(path=None):
    """حذف cache لملف محدد أو كل الـ cache."""
    with _lock:
        if path is None:
            _cache.clear()
        else:
            key = _cache_key(path)
            if key is not None and key in _cache:
                del _cache[key]


def clear_media_cache():
    """مسح كل الـ cache."""
    with _lock:
        _cache.clear()
