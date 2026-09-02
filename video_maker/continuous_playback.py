# -*- coding: utf-8 -*-
"""قرارات تشغيل الصوت المتصل أثناء تبديل مقاطع الفيديو.

هذا الملف مستقل عن wxPython حتى يمكن اختباره بالكامل. الفكرة الأساسية أن
مسار الصوت البديل الكامل يكون ساعة التشغيل الرئيسية، بينما يمكن لمشغل
الفيديو تبديل الملفات المرئية دون إيقاف الصوت أو إعادته إلى بداية الحد.
"""
from __future__ import annotations

import os


DEFAULT_BOUNDARY_TOLERANCE_MS = 900
LIVE_GAP_SKIP_MARGIN_SECONDS = 0.045
LIVE_GAP_SKIP_MIN_GAP_SECONDS = 0.010


def clamp_time(value: float, duration: float) -> float:
    """حصر الزمن داخل مدة الخط الزمني."""
    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    try:
        duration = max(0.0, float(duration or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return max(0.0, min(value, duration))


def audio_clock_time(audio_tell_ms: float, duration: float) -> float:
    """تحويل موضع مشغل الصوت بالمللي ثانية إلى زمن المشروع."""
    try:
        seconds = max(0.0, float(audio_tell_ms or 0.0) / 1000.0)
    except (TypeError, ValueError):
        seconds = 0.0
    return clamp_time(seconds, duration)


def should_preserve_override_audio(
    *,
    seamless: bool,
    play: bool,
    has_override: bool,
    player_is_playing: bool,
    player_path: str,
    override_path: str,
    player_tell_ms: float,
    target_time: float,
    tolerance_ms: int = DEFAULT_BOUNDARY_TOLERANCE_MS,
) -> bool:
    """هل نستمر في تشغيل الصوت أثناء تبديل الملف المرئي؟

    لا نحافظ عليه عند القفز اليدوي أو عند اختلاف الملف. السماح الزمني هنا
    مخصص لعبور حافة مقطعين متجاورين، ويمنع اعتبار قفزة المستخدم انتقالًا
    تلقائيًا.
    """
    if not seamless or not play or not has_override or not player_is_playing:
        return False
    if not player_path or not override_path or player_path != override_path:
        return False
    try:
        drift_ms = abs(float(player_tell_ms or 0.0) - float(target_time or 0.0) * 1000.0)
    except (TypeError, ValueError):
        return False
    return drift_ms <= max(50, int(tolerance_ms or DEFAULT_BOUNDARY_TOLERANCE_MS))


def media_seek_ms(
    *,
    timeline_time: float,
    segment_position: float,
    segment_start: float,
    segment_end: float,
    segment_speed: float,
    preview_path: bool = False,
) -> int:
    """حساب موضع الفيديو المحلي من زمن المشروع."""
    timeline_time = max(0.0, float(timeline_time or 0.0))
    if preview_path:
        return max(0, int(round(timeline_time * 1000.0)))
    segment_position = max(0.0, float(segment_position or 0.0))
    segment_start = max(0.0, float(segment_start or 0.0))
    segment_end = max(segment_start, float(segment_end or segment_start))
    segment_speed = max(0.05, float(segment_speed or 1.0))
    local_time = segment_start + max(0.0, timeline_time - segment_position) * segment_speed
    local_time = min(max(segment_start, local_time), max(segment_start, segment_end - 0.001))
    return max(0, int(round(local_time * 1000.0)))


def _same_path(first: str, second: str) -> bool:
    if not first or not second:
        return False
    try:
        return os.path.normcase(os.path.abspath(str(first))) == os.path.normcase(os.path.abspath(str(second)))
    except Exception:
        return str(first) == str(second)


def _segment_speed(segment) -> float:
    try:
        return max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
    except (TypeError, ValueError):
        return 1.0


def _segment_volume(segment) -> float:
    try:
        value = getattr(segment, "audio_volume", 1.0)
        if value is None:
            value = 1.0
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _segment_audio_path(segment) -> str:
    return str(getattr(segment, "audio_path", "") or getattr(segment, "path", "") or "")


def _segment_audio_fade(segment) -> float:
    try:
        return max(
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        )
    except (TypeError, ValueError):
        return 0.0


def can_live_skip_deleted_gap(current_segment, next_segment) -> bool:
    if current_segment is None or next_segment is None:
        return False
    if not _same_path(getattr(current_segment, "path", ""), getattr(next_segment, "path", "")):
        return False
    if not _same_path(_segment_audio_path(current_segment), _segment_audio_path(next_segment)):
        return False
    if abs(_segment_speed(current_segment) - _segment_speed(next_segment)) > 0.001:
        return False
    if abs(_segment_volume(current_segment) - _segment_volume(next_segment)) > 0.001:
        return False
    if _segment_audio_fade(current_segment) > 0.001 or _segment_audio_fade(next_segment) > 0.001:
        return False
    try:
        gap = float(getattr(next_segment, "start", 0.0)) - float(getattr(current_segment, "end", 0.0))
    except (TypeError, ValueError):
        return False
    return gap >= LIVE_GAP_SKIP_MIN_GAP_SECONDS


def should_live_skip_deleted_gap(media_time: float, current_segment, next_segment, margin_seconds: float = LIVE_GAP_SKIP_MARGIN_SECONDS) -> bool:
    if not can_live_skip_deleted_gap(current_segment, next_segment):
        return False
    try:
        media_time = float(media_time or 0.0)
        segment_end = float(getattr(current_segment, "end", 0.0) or 0.0)
        margin_seconds = max(0.001, float(margin_seconds or LIVE_GAP_SKIP_MARGIN_SECONDS))
    except (TypeError, ValueError):
        return False
    return media_time >= segment_end - margin_seconds
