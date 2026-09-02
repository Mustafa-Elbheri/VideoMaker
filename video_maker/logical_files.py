"""هوية الملفات المنطقية داخل الخط الزمني.

المقطع TimelineSegment وحدة تشغيل/ترميز، أما الملف المنطقي فهو الوسيط الذي فتحه
المستخدم. قد يتحول ملف واحد إلى عشرات المقاطع بسبب القص أو الصور أو النصوص، لكنه
يبقى ملفًا واحدًا عند التنقل بـ Tab وShift+Tab.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import os
import uuid
from typing import Iterable, Sequence

from video_maker.timeline import TimelineSegment, total_duration


@dataclass
class LogicalFileEntry:
    file_id: str
    name: str
    index: int
    first_start: float
    last_end: float
    intervals: list[tuple[float, float]] = field(default_factory=list)
    segments: list[TimelineSegment] = field(default_factory=list)


def new_logical_file_id() -> str:
    return f"file:{uuid.uuid4().hex}"


def display_file_name(path: str, fallback: str = "") -> str:
    name = os.path.basename(str(path or "").strip())
    return name or str(fallback or "").strip() or "ملف"


def segment_file_id(segment: TimelineSegment) -> str:
    return str(getattr(segment, "source_file_id", "") or "")


def segment_file_name(segment: TimelineSegment) -> str:
    return str(getattr(segment, "source_file_name", "") or "") or display_file_name(segment.path)


def segment_with_file_identity(segment: TimelineSegment, file_id: str, name: str = "") -> TimelineSegment:
    return TimelineSegment(
        segment.path,
        segment.start,
        segment.end,
        float(getattr(segment, "speed", 1.0) or 1.0),
        float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
        str(getattr(segment, "audio_path", "") or ""),
        getattr(segment, "audio_start", None),
        str(getattr(segment, "navigation_group", "") or ""),
        str(file_id or ""),
        str(name or "") or segment_file_name(segment),
        str(getattr(segment, "transition", "") or ""),
        max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
        max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
        max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
    )


def new_file_segment(path: str, start: float, end: float, **kwargs) -> TimelineSegment:
    file_id = str(kwargs.pop("source_file_id", "") or new_logical_file_id())
    file_name = str(kwargs.pop("source_file_name", "") or display_file_name(path))
    return TimelineSegment(path, start, end, source_file_id=file_id, source_file_name=file_name, **kwargs)


def _split_and_mark_range(
    timeline: Sequence[TimelineSegment],
    start_time: float,
    end_time: float,
    file_id: str,
    file_name: str,
) -> list[TimelineSegment]:
    """ضع هوية ملف على نطاق مع الحفاظ على الأجزاء الواقعة قبله وبعده."""
    from video_maker.timeline import delete_range, insert_segments, slice_segments

    duration = total_duration(timeline)
    start_time = max(0.0, min(float(start_time or 0.0), duration))
    end_time = max(start_time, min(float(end_time or 0.0), duration))
    if end_time <= start_time:
        return list(timeline)
    selected = [segment_with_file_identity(s, file_id, file_name) for s in slice_segments(timeline, start_time, end_time)]
    return insert_segments(delete_range(timeline, start_time, end_time), start_time, selected)


def ensure_logical_file_metadata(
    timeline: Sequence[TimelineSegment],
    primary_path: str = "",
    edit_points: Iterable[dict] | None = None,
) -> list[TimelineSegment]:
    """ترقية خط زمني قديم لا يحتوي على هوية الملفات.

    عند غياب الهوية تمامًا نعتبر المشروع المفتوح ملفًا واحدًا، ثم نفصل فقط نطاقات
    إدراج ملفات فيديو/صوت المعروفة في edit_points. التعديلات البصرية لا تنشئ ملفًا.
    """
    items = list(timeline or [])
    if not items:
        return []
    if all(segment_file_id(s) for s in items):
        return items

    existing_ids = [segment_file_id(s) for s in items]
    if any(existing_ids):
        # املأ المقاطع القديمة الواقعة بين أجزاء الملف نفسه بأقرب هوية، ولا تنشئ
        # أرقامًا جديدة لمجرد أن مسار الملف المؤقت مختلف.
        result: list[TimelineSegment] = []
        last_id = ""
        last_name = ""
        for index, segment in enumerate(items):
            file_id = segment_file_id(segment)
            name = segment_file_name(segment)
            if not file_id:
                next_identity = next(
                    ((segment_file_id(s), segment_file_name(s)) for s in items[index + 1:] if segment_file_id(s)),
                    ("", ""),
                )
                file_id, name = (last_id, last_name) if last_id else next_identity
            if not file_id:
                file_id = new_logical_file_id()
            if not name:
                name = display_file_name(primary_path or segment.path)
            result.append(segment_with_file_identity(segment, file_id, name))
            last_id, last_name = file_id, name
        return result

    primary_id = new_logical_file_id()
    primary_name = display_file_name(primary_path or items[0].path)
    result = [segment_with_file_identity(s, primary_id, primary_name) for s in items]

    # ترقية المشاريع القديمة التي أضيفت إليها ملفات مستقلة قبل وجود هذا النظام.
    # لا نعتبر الصور والنصوص والمؤثرات البصرية ملفات جديدة.
    file_point_kinds = {"video", "audio", "recording", "screen_recording", "audio_recording"}
    for point in sorted(list(edit_points or []), key=lambda p: float(p.get("start", 0.0) or 0.0)):
        if str(point.get("target", "") or "") != "timeline":
            continue
        if str(point.get("mode", "") or "") not in {"insert", "replace"}:
            continue
        if str(point.get("kind", "") or "") not in file_point_kinds:
            continue
        start = float(point.get("start", 0.0) or 0.0)
        end = float(point.get("end", start) or start)
        if end <= start:
            continue
        # الاسم الحقيقي من أول مقطع في النطاق بعد التقسيم.
        selected = _segments_in_range(result, start, end)
        source_path = selected[0].path if selected else ""
        result = _split_and_mark_range(result, start, end, new_logical_file_id(), display_file_name(source_path, "ملف مضاف"))
    return result


def _segments_in_range(timeline: Sequence[TimelineSegment], start: float, end: float) -> list[TimelineSegment]:
    from video_maker.timeline import slice_segments
    return slice_segments(timeline, start, end)


def replacement_segments_preserving_files(
    timeline: Sequence[TimelineSegment],
    start_time: float,
    end_time: float,
    replacement_path: str,
    replacement_duration: float,
    *,
    audio_path: str = "",
    audio_start: float | None = None,
) -> list[TimelineSegment]:
    """قسّم ملف الناتج المؤقت وفق هويات الملفات التي غطاها النطاق الأصلي.

    إذا امتد تأثير واحد عبر ملفين، يبقى الناتج مقطعين منطقيين يشيران إلى أجزاء
    مختلفة من ملف الرندر نفسه، وبذلك لا يندمجان في رقم ملف واحد.
    """
    from video_maker.timeline import slice_segments

    selected = slice_segments(timeline, start_time, end_time)
    selected_duration = sum(s.duration for s in selected)
    replacement_duration = max(0.0, float(replacement_duration or 0.0))
    if not selected or selected_duration <= 0 or replacement_duration <= 0:
        return [TimelineSegment(replacement_path, 0.0, replacement_duration)] if replacement_duration > 0 else []

    scale = replacement_duration / selected_duration
    output: list[TimelineSegment] = []
    cursor = 0.0
    for index, source in enumerate(selected):
        part = source.duration * scale
        end = replacement_duration if index == len(selected) - 1 else min(replacement_duration, cursor + part)
        if end <= cursor:
            continue
        output.append(TimelineSegment(
            replacement_path,
            cursor,
            end,
            1.0,
            float(getattr(source, "audio_volume", 1.0) if getattr(source, "audio_volume", 1.0) is not None else 1.0),
            audio_path,
            (float(audio_start) + cursor) if audio_start is not None else None,
            str(getattr(source, "navigation_group", "") or ""),
            segment_file_id(source),
            segment_file_name(source),
            str(getattr(source, "transition", "") or "") if index == 0 else "",
            max(0.0, float(getattr(source, "transition_duration", 1.0) or 1.0)) if index == 0 else 1.0,
            max(0.0, float(getattr(source, "audio_fade_in", 0.0) or 0.0)) if index == 0 else 0.0,
            max(0.0, float(getattr(source, "audio_fade_out", 0.0) or 0.0)) if index == len(selected) - 1 else 0.0,
        ))
        cursor = end
    return output


def logical_file_entries(timeline: Sequence[TimelineSegment]) -> list[LogicalFileEntry]:
    entries: "OrderedDict[str, LogicalFileEntry]" = OrderedDict()
    position = 0.0
    for segment in timeline or []:
        start = position
        end = position + segment.duration
        file_id = segment_file_id(segment) or f"legacy:{id(segment)}"
        name = segment_file_name(segment)
        entry = entries.get(file_id)
        if entry is None:
            entry = LogicalFileEntry(file_id, name, len(entries), start, end)
            entries[file_id] = entry
        entry.last_end = max(entry.last_end, end)
        entry.intervals.append((start, end))
        entry.segments.append(segment)
        position = end
    return list(entries.values())


def logical_file_at_time(timeline: Sequence[TimelineSegment], time_value: float):
    entries = logical_file_entries(timeline)
    if not entries:
        return None, []
    duration = total_duration(timeline)
    current = max(0.0, min(float(time_value or 0.0), duration))
    for entry in entries:
        for start, end in entry.intervals:
            if start <= current < end or (current >= duration and end >= duration):
                return entry, entries
    return entries[-1], entries


def file_intervals_descending(entry: LogicalFileEntry) -> list[tuple[float, float]]:
    """ادمج الفترات المتجاورة ثم أعدها من النهاية للبداية للحذف الآمن."""
    merged: list[list[float]] = []
    for start, end in sorted(entry.intervals):
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in reversed(merged)]
