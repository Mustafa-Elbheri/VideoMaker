import copy
from dataclasses import dataclass, field

from video_maker.timeline import TimelineSegment
from video_maker.timeline_engine.constants import (
    MAIN_ACCEPTED_MEDIA_TYPES,
    MAIN_VIDEO_TRACK,
    RIPPLE_MODE_ALL_TRACKS,
    RIPPLE_MODE_PER_TRACK,
    SAMPLE_RATE,
)
from video_maker.timeline_engine.models import MediaItem, _segment_duration, to_seconds


@dataclass
class OperationResult:
    ok: bool
    announcement: str = ""
    ops: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def track_key(self):
        return self.meta.get("track_key")

    @property
    def item_id(self):
        return self.meta.get("item_id")

    @property
    def new_start_seconds(self):
        return self.meta.get("new_start_seconds")

    @property
    def length_seconds(self):
        return self.meta.get("length_seconds")

    @property
    def segment_payload(self):
        return self.meta.get("segment_payload")


def _success(track_key, item_id, new_start_seconds, length_seconds, ops, announcement, segment_payload=None):
    meta = {
        "track_key": track_key,
        "item_id": item_id,
        "new_start_seconds": new_start_seconds,
        "length_seconds": length_seconds,
    }
    if segment_payload is not None:
        meta["segment_payload"] = segment_payload
    return OperationResult(True, announcement, ops, meta)


def _restore_tracks(engine, snapshot):
    for key, items in snapshot.items():
        engine.tracks[key].items = items


def nudge_item(engine, track_key, item_id, step_samples, ripple_mode):
    """يزيح عنصراً على تراك فرعي خطوة واحدة وينتج عمليات الالتزام.

    - الخطوة بالمقاسات (عينة). الإزاحة الفعلية تحسب من جديد الموضع المثبت.
    - في per_track يُزيح تراك العنصر فقط بعد موضعه القديم، وفي all_tracks كل
      التراكات، وفي off لا يُزيح شيئاً (ويمنع التداخل الناتج).
    - إزاحة المقطع الرئيسي ممنوعة دائماً.
    """
    step_seconds = (step_samples or 0) / float(SAMPLE_RATE)
    if track_key == MAIN_VIDEO_TRACK:
        return OperationResult(False, "لا يمكن إزاحة عنصر على المقطع الرئيسي")
    track = engine.track(track_key)
    if track is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    item = track.find(item_id)
    if item is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    old_start = item.timeline_start
    old_end = item.timeline_end
    length = item.timeline_length
    new_start = to_seconds(old_start + step_seconds)
    effective_delta = new_start - old_start
    if effective_delta == 0:
        return OperationResult(False, "لا يمكن إزاحة العنصر أبعد من بداية الخط الزمني")
    snapshot = {key: copy.deepcopy(track.items) for key, track in engine.tracks.items()}
    item = track.remove(item_id)
    if ripple_mode == RIPPLE_MODE_ALL_TRACKS:
        for other in engine.tracks.values():
            other.shift_after(old_start, effective_delta)
    elif ripple_mode == RIPPLE_MODE_PER_TRACK:
        track.shift_after(old_start, effective_delta)
    item.set_start(new_start)
    track.insert_sorted(item)
    if track.overlaps(item, exclude_item_id=item.id):
        _restore_tracks(engine, snapshot)
        return OperationResult(False, "لا يمكن إزاحة العنصر لأنه سيتداخل مع عنصر آخر")
    ops = [("nudge_item", track_key, item.id, new_start, effective_delta, old_start)]
    if ripple_mode == RIPPLE_MODE_ALL_TRACKS and engine.timeline.main_segments:
        if effective_delta > 0:
            ops.append(("ripple_main_gap", old_start, effective_delta))
        else:
            ops.append(("ripple_main_range", new_start, old_start))
    return _success(track_key, item.id, new_start, length, ops, "nudge_success")


def move_to_track(engine, source_key, target_key, item_id, ripple_mode):
    if source_key == target_key:
        return OperationResult(False, "لا يمكن نقل العنصر إلى التراك نفسه")
    if source_key == MAIN_VIDEO_TRACK:
        return move_from_main(engine, target_key, item_id, ripple_mode)
    if target_key == MAIN_VIDEO_TRACK:
        return move_to_main(engine, source_key, item_id, ripple_mode)
    return move_between_overlays(engine, source_key, target_key, item_id, ripple_mode)


def move_between_overlays(engine, source_key, target_key, item_id, ripple_mode):
    source = engine.track(source_key)
    target = engine.track(target_key)
    if source is None or target is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    item = source.find(item_id)
    if item is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    if item.media_type not in target.media_types:
        return OperationResult(False, "لا يمكن نقل العنصر إلى هذا التراك")
    position = item.timeline_start
    old_end = item.timeline_end
    length = item.timeline_length
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        if target.straddles(position, exclude_item_id=item.id):
            return OperationResult(False, "لا يمكن نقل العنصر لأنه سيتداخل مع عنصر آخر على التراك الهدف")
        ops = [
            ("remove_item", source_key, item.id),
            ("shift_track", target_key, position, length),
            ("shift_track", source_key, old_end, -length),
            ("insert_item", target_key, item.payload),
        ]
    else:
        if target.overlaps_span(position, length, exclude_item_id=item.id):
            return OperationResult(False, "لا يمكن نقل العنصر لأنه سيتداخل مع عنصر آخر على التراك الهدف")
        ops = [
            ("move_item", source_key, target_key, item.id, position, old_end, position),
        ]
    source.remove(item.id)
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        target.shift_after(position, length)
        source.shift_after(old_end, -length)
    item.track_key = target_key
    target.insert_sorted(item)
    return _success(target_key, item.id, position, length, ops, "move_success")


def _insert_engine_main_segment(timeline, at_time, payload):
    source_offset = max(0.0, float(payload.get("source_offset", 0.0) or 0.0))
    speed = max(0.05, float(payload.get("speed", 1.0) or 1.0))
    timeline_length = max(
        0.0, float(payload.get("end", 0.0) or 0.0) - float(payload.get("start", 0.0) or 0.0)
    )
    segment = TimelineSegment(
        str(payload.get("path", "") or ""),
        source_offset,
        max(source_offset, source_offset + timeline_length * speed),
        speed=speed,
    )
    at_time = max(0.0, float(at_time or 0.0))
    index = len(timeline.main_segments)
    position = 0.0
    for i, existing in enumerate(timeline.main_segments):
        if position >= at_time:
            index = i
            break
        position += _segment_duration(existing)
    timeline.main_segments.insert(index, segment)


def move_to_main(engine, source_key, item_id, ripple_mode):
    source = engine.track(source_key)
    if source is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    item = source.find(item_id)
    if item is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    if item.media_type not in MAIN_ACCEPTED_MEDIA_TYPES:
        return OperationResult(False, "لا يمكن نقل العنصر إلى هذا التراك")
    position = item.timeline_start
    old_end = item.timeline_end
    length = item.timeline_length
    main_total = engine.timeline.main_total_seconds()
    at_time = min(max(0.0, position), main_total) if engine.timeline.main_segments else position
    speed = float(item.payload.get("speed", 1.0) or 1.0)
    source_offset = float(item.payload.get("source_offset", 0.0) or 0.0)
    source_start = source_offset
    source_end = max(source_start, source_start + length * speed)
    segment_payload = {
        "path": str(item.payload.get("path", "") or ""),
        "start": source_start,
        "end": source_end,
        "speed": speed,
        "type": "audio" if item.media_type == "audio" else "video",
    }
    ops = [
        ("remove_item", source_key, item.id),
    ]
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        ops.append(("shift_track", source_key, old_end, -length))
    ops.append(("insert_main_segment", at_time, item.payload))
    source.remove(item.id)
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        source.shift_after(old_end, -length)
    _insert_engine_main_segment(engine.timeline, at_time, item.payload)
    return _success(
        MAIN_VIDEO_TRACK,
        item.id,
        at_time,
        length,
        ops,
        "move_success",
        segment_payload=segment_payload,
    )


def move_from_main(engine, target_key, item_id, ripple_mode):
    target = engine.track(target_key)
    if target is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    index = engine.timeline.main_segment_index_by_id(item_id)
    if index is None:
        return OperationResult(False, "العنصر المركّز غير موجود على الخط الزمني")
    media_class = "audio" if engine.timeline.main_media_type == "audio" else "video"
    if media_class not in target.media_types:
        return OperationResult(False, "لا يمكن نقل العنصر إلى هذا التراك")
    segment = engine.timeline.main_segments[index]
    position = engine.timeline.main_position_seconds(index)
    length = engine.timeline.main_duration_seconds(index)
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        if target.straddles(position):
            return OperationResult(False, "لا يمكن نقل العنصر لأنه سيتداخل مع عنصر آخر على التراك الهدف")
    elif target.overlaps_span(position, length, exclude_item_id=item_id):
        return OperationResult(False, "move_overlap")
    main_payload = {
        "path": str(getattr(segment, "path", "") or ""),
        "start": float(getattr(segment, "start", 0.0) or 0.0),
        "end": float(getattr(segment, "end", 0.0) or 0.0),
        "speed": float(getattr(segment, "speed", 1.0) or 1.0),
    }
    speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
    payload = {
        "id": item_id,
        "type": media_class,
        "path": str(getattr(segment, "path", "") or ""),
        "start": position,
        "end": position + length,
        "speed": speed,
        "source_offset": float(getattr(segment, "start", 0.0) or 0.0),
    }
    ops = [
        ("remove_main_segment", main_payload),
    ]
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        ops.append(("shift_track", target_key, position, length))
    ops.append(("insert_item", target_key, payload))
    engine.timeline.main_segments.pop(index)
    if ripple_mode == RIPPLE_MODE_PER_TRACK:
        target.shift_after(position, length)
    target.insert_sorted(MediaItem(id=item_id, media_type=media_class, payload=payload, track_key=target_key))
    return _success(target_key, item_id, position, length, ops, "move_success")
