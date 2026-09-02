from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class TimelineSegment:
    path: str
    start: float
    end: float
    speed: float = 1.0
    audio_volume: float = 1.0
    audio_path: str = ""
    audio_start: Optional[float] = None
    navigation_group: str = ""
    source_file_id: str = ""
    source_file_name: str = ""
    transition: str = ""
    transition_duration: float = 1.0
    audio_fade_in: float = 0.0
    audio_fade_out: float = 0.0

    @property
    def has_transition(self):
        return bool(getattr(self, "transition", "") or "")

    def transition_key(self):
        return str(getattr(self, "transition", "") or "")

    def transition_dur(self):
        return max(0.0, float(getattr(self, "transition_duration", 1.0) or 1.0))

    @property
    def source_duration(self):
        return max(0, self.end - self.start)

    @property
    def duration(self):
        speed = max(0.05, float(self.speed or 1.0))
        return self.source_duration / speed


def with_transition(segment, transition_key="", transition_duration=1.0):
    return TimelineSegment(
        segment.path,
        segment.start,
        segment.end,
        float(getattr(segment, "speed", 1.0) or 1.0),
        float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
        str(getattr(segment, "audio_path", "") or ""),
        getattr(segment, "audio_start", None),
        str(getattr(segment, "navigation_group", "") or ""),
        str(getattr(segment, "source_file_id", "") or ""),
        str(getattr(segment, "source_file_name", "") or ""),
        str(transition_key or ""),
        max(0.0, float(transition_duration or 1.0)),
        max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
        max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
    )


def normalized_audio_fade(value, duration=None):
    try:
        fade = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        fade = 0.0
    if duration is not None:
        fade = min(fade, max(0.0, float(duration or 0.0)) / 2.0)
    return fade


def with_audio_fades(segment, fade_in=None, fade_out=None):
    duration = float(getattr(segment, "duration", 0.0) or 0.0)
    return replace(
        segment,
        audio_fade_in=normalized_audio_fade(
            getattr(segment, "audio_fade_in", 0.0) if fade_in is None else fade_in,
            duration,
        ),
        audio_fade_out=normalized_audio_fade(
            getattr(segment, "audio_fade_out", 0.0) if fade_out is None else fade_out,
            duration,
        ),
    )


def total_duration(segments):
    return sum(segment.duration for segment in segments)


def locate_segment(segments, time):
    position = 0
    for index, segment in enumerate(segments):
        next_position = position + segment.duration
        if time < next_position or index == len(segments) - 1:
            return index, segment, position
        position = next_position
    return None, None, 0


def boundary_index_at_time(segments, time, tolerance=0.08):
    """فهرس المقطع الأيمن للحد الفاصل الأقرب إلى time، ومكان الحد.

    يعيد (None, None) إن لم يوجد حد قريب. الانتقال الموجود على المقطع الأيمن
    هو الذي يطبق على الحد الفاصل.
    """
    position = 0.0
    for index, segment in enumerate(segments):
        boundary_time = position + max(0.0, float(segment.duration))
        if index < len(segments) - 1 and abs(boundary_time - float(time)) <= float(tolerance):
            return index + 1, boundary_time
        position = boundary_time
    return None, None


def slice_segments(segments, start_time, end_time):
    result = []
    position = 0
    for segment in segments:
        next_position = position + segment.duration
        if next_position > start_time and position < end_time:
            speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
            audio_volume = max(0.0, min(1.0, float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0)))
            local_start = max(segment.start, segment.start + (start_time - position) * speed)
            local_end = min(segment.end, segment.start + (end_time - position) * speed)
            if local_start < local_end:
                audio_start = getattr(segment, "audio_start", None)
                if audio_start is not None:
                    audio_start = float(audio_start) + max(0.0, local_start - segment.start)
                source_fade_in = normalized_audio_fade(getattr(segment, "audio_fade_in", 0.0), segment.duration)
                source_fade_out = normalized_audio_fade(getattr(segment, "audio_fade_out", 0.0), segment.duration)
                fade_in = source_fade_in if abs(local_start - segment.start) <= 1e-6 else 0.0
                fade_out = source_fade_out if abs(local_end - segment.end) <= 1e-6 else 0.0
                result.append(TimelineSegment(
                    segment.path, local_start, local_end, speed, audio_volume,
                    str(getattr(segment, "audio_path", "") or ""), audio_start,
                    str(getattr(segment, "navigation_group", "") or ""),
                    str(getattr(segment, "source_file_id", "") or ""),
                    str(getattr(segment, "source_file_name", "") or ""),
                    str(getattr(segment, "transition", "") or ""),
                    max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
                    fade_in,
                    fade_out,
                ))
        position = next_position
    return result


def delete_range(segments, start_time, end_time):
    before = slice_segments(segments, 0, start_time)
    after = slice_segments(segments, end_time, total_duration(segments))
    return before + after


def insert_segments(segments, time, inserted_segments):
    before = slice_segments(segments, 0, time)
    after = slice_segments(segments, time, total_duration(segments))
    return before + list(inserted_segments) + after


def apply_audio_cut_fade_at_boundary(segments, boundary_time, fade_duration=0.008, tolerance=1e-6):
    boundary_time = max(0.0, float(boundary_time or 0.0))
    fade_duration = max(0.0, float(fade_duration or 0.0))
    if fade_duration <= 0.0:
        return list(segments or [])
    result = []
    position = 0.0
    for segment in segments or []:
        start_pos = position
        end_pos = position + float(getattr(segment, "duration", 0.0) or 0.0)
        updated = segment
        if abs(end_pos - boundary_time) <= tolerance:
            updated = with_audio_fades(
                updated,
                fade_out=max(
                    normalized_audio_fade(getattr(updated, "audio_fade_out", 0.0), updated.duration),
                    normalized_audio_fade(fade_duration, updated.duration),
                ),
            )
        if abs(start_pos - boundary_time) <= tolerance:
            updated = with_audio_fades(
                updated,
                fade_in=max(
                    normalized_audio_fade(getattr(updated, "audio_fade_in", 0.0), updated.duration),
                    normalized_audio_fade(fade_duration, updated.duration),
                ),
            )
        result.append(updated)
        position = end_pos
    return result
