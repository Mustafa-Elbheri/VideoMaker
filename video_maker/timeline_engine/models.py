from dataclasses import dataclass, field

from video_maker.timeline_engine.constants import (
    MAIN_VIDEO_TRACK,
    SAMPLE_RATE,
)


def to_seconds(value):
    return max(0.0, float(value or 0.0))


def to_samples(value):
    return max(0, int(round(float(value or 0.0) * SAMPLE_RATE)))


def _segment_duration(segment):
    speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
    source = max(0.0, float(getattr(segment, "end", 0.0) or 0.0) - float(getattr(segment, "start", 0.0) or 0.0))
    return source / speed


def _main_segment_identifier(segment):
    return "main:{path}:{start}:{end}:{speed}".format(
        path=str(getattr(segment, "path", "") or ""),
        start=float(getattr(segment, "start", 0.0) or 0.0),
        end=float(getattr(segment, "end", 0.0) or 0.0),
        speed=float(getattr(segment, "speed", 1.0) or 1.0),
    )


@dataclass
class MediaItem:
    id: str
    media_type: str
    payload: dict
    track_key: str = ""

    @property
    def timeline_start(self):
        return float(self.payload.get("start", 0.0) or 0.0)

    @property
    def timeline_end(self):
        return float(self.payload.get("end", 0.0) or 0.0)

    @property
    def timeline_length(self):
        return max(0.0, self.timeline_end - self.timeline_start)

    def set_start(self, value):
        length = self.timeline_length
        value = max(0.0, float(value or 0.0))
        self.payload["start"] = value
        self.payload["end"] = value + length

    def shift(self, delta):
        delta = float(delta or 0.0)
        if delta == 0:
            return
        self.set_start(self.timeline_start + delta)


@dataclass
class Track:
    key: str
    media_types: tuple = field(default_factory=tuple)
    items: list = field(default_factory=list)

    def find(self, item_id):
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def remove(self, item_id):
        for index, item in enumerate(self.items):
            if item.id == item_id:
                return self.items.pop(index)
        return None

    def insert_sorted(self, item):
        at_time = item.timeline_start
        for index, existing in enumerate(self.items):
            if existing.timeline_start >= at_time:
                self.items.insert(index, item)
                return
        self.items.append(item)

    def shift_after(self, from_time, delta):
        from_time = float(from_time)
        delta = float(delta or 0.0)
        if delta == 0:
            return
        for item in self.items:
            if item.timeline_start >= from_time or item.timeline_end > from_time:
                item.shift(delta)

    def overlaps_span(self, start_time, length, exclude_item_id=None):
        start_time = float(start_time)
        end_time = start_time + max(0.0, float(length or 0.0))
        for item in self.items:
            if exclude_item_id is not None and item.id == exclude_item_id:
                continue
            if item.timeline_start < end_time and item.timeline_end > start_time:
                return True
        return False

    def straddles(self, time_value, exclude_item_id=None):
        time_value = float(time_value)
        for item in self.items:
            if exclude_item_id is not None and item.id == exclude_item_id:
                continue
            if item.timeline_start < time_value < item.timeline_end:
                return True
        return False

    def overlaps(self, item, exclude_item_id=None):
        return self.overlaps_span(
            item.timeline_start, item.timeline_length, exclude_item_id=exclude_item_id
        )


@dataclass
class Timeline:
    main_segments: list = field(default_factory=list)
    main_media_type: str = "video"

    def main_total_seconds(self):
        return sum(_segment_duration(segment) for segment in self.main_segments)

    def main_position_seconds(self, index):
        return sum(_segment_duration(segment) for segment in self.main_segments[:index])

    def main_duration_seconds(self, index):
        if index is None or not (0 <= index < len(self.main_segments)):
            return 0.0
        return _segment_duration(self.main_segments[index])

    def main_segment_index_by_id(self, identifier):
        for index, segment in enumerate(self.main_segments):
            if _main_segment_identifier(segment) == identifier:
                return index
        return None


@dataclass
class Engine:
    tracks: dict = field(default_factory=dict)
    timeline: Timeline = field(default_factory=Timeline)

    def track(self, key):
        return self.tracks.get(key)

    def find_item(self, item_id):
        for key, track in self.tracks.items():
            if track.find(item_id) is not None:
                return key, item_id
        if self.timeline.main_segment_index_by_id(item_id) is not None:
            return MAIN_VIDEO_TRACK, item_id
        return None, None
