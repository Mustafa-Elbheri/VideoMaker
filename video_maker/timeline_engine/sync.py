import copy
import uuid
from dataclasses import replace

from video_maker.clean_cut import clean_delete_range
from video_maker.edit_points import adjust_points_after_delete, adjust_points_after_insert
from video_maker.timeline import (
    TimelineSegment,
    apply_audio_cut_fade_at_boundary,
    delete_range,
    insert_segments,
    total_duration,
)
from video_maker.timeline_engine.constants import TEXT_TRACK, TRACK_ACCEPTED_MEDIA_TYPES
from video_maker.timeline_engine.models import Engine, MediaItem, Timeline, Track
from video_maker.track_items import element_identifier, ripple_shift


def _dict_media_type(item):
    item_type = str(item.get("type", "") or "")
    if item_type in ("sound_effect", "background_audio"):
        return "audio"
    if item_type in ("image", "video"):
        return "video"
    if item_type == "text":
        return "text"
    if item_type:
        return item_type
    return "video"


def build_engine_from_player(player):
    engine = Engine()
    for track_key, accepted in TRACK_ACCEPTED_MEDIA_TYPES.items():
        storage = player._track_storage_for(track_key)
        track = Track(key=track_key, media_types=accepted)
        for entry in storage or ():
            if not isinstance(entry, dict):
                continue
            if track_key == TEXT_TRACK and str(entry.get("type", "") or "") != "text":
                continue
            payload = copy.deepcopy(entry)
            track.items.append(
                MediaItem(
                    id=element_identifier(entry),
                    media_type=_dict_media_type(payload),
                    payload=payload,
                    track_key=track_key,
                )
            )
        engine.tracks[track_key] = track
    main_media_type = "audio" if str(getattr(player, "media_kind", "") or "") == "audio" else "video"
    engine.timeline = Timeline(
        main_segments=list(player.timeline or ()),
        main_media_type=main_media_type,
    )
    return engine


def _segment_from_engine_item(entry, at_time):
    if isinstance(entry, TimelineSegment):
        return TimelineSegment(
            entry.path,
            float(entry.start or 0.0),
            float(entry.end or 0.0),
            speed=float(entry.speed or 1.0),
            audio_volume=float(entry.audio_volume if entry.audio_volume is not None else 1.0),
            audio_path=str(entry.audio_path or ""),
            audio_start=entry.audio_start,
            navigation_group=str(entry.navigation_group or ""),
            source_file_id=str(entry.source_file_id or ""),
            source_file_name=str(entry.source_file_name or ""),
            transition=str(entry.transition or ""),
            transition_duration=float(entry.transition_duration or 1.0),
        )
    source_offset = float(entry.get("source_offset", 0.0) or 0.0)
    speed = float(entry.get("speed", 1.0) or 1.0)
    timeline_length = max(
        0.0, float(entry.get("end", 0.0) or 0.0) - float(entry.get("start", 0.0) or 0.0)
    )
    source_start = max(0.0, source_offset)
    source_end = max(source_start, source_start + timeline_length * speed)
    return TimelineSegment(
        str(entry.get("path", "") or ""),
        source_start,
        source_end,
        speed=speed,
        audio_volume=float(entry.get("audio_volume", 1.0) if entry.get("audio_volume", 1.0) is not None else 1.0),
        audio_path=str(entry.get("audio_path", "") or ""),
        audio_start=entry.get("audio_start"),
        navigation_group=str(entry.get("navigation_group", "") or ""),
        source_file_id=str(entry.get("source_file_id", "") or ""),
        source_file_name=str(entry.get("source_file_name", "") or ""),
        transition=str(entry.get("transition", "") or ""),
        transition_duration=float(entry.get("transition_duration", 1.0) or 1.0),
    )


def _shift_main_segments(segments, from_time, delta):
    from_time = float(from_time or 0.0)
    delta = float(delta or 0.0)
    if delta == 0:
        return list(segments or ())
    shifted = []
    position = 0.0
    for segment in segments or ():
        if position >= from_time:
            shifted.append(
                replace(segment, start=float(segment.start) + delta, end=float(segment.end) + delta)
            )
        else:
            shifted.append(segment)
        position += float(segment.duration)
    return shifted


def _commit_nudge_item(player, track_key, item_id, new_start_s, effective_delta_s, old_start_s):
    storage = player._track_storage_for(track_key)
    index = player._storage_index_of(storage, item_id)
    if index is None:
        return
    item = storage.pop(index)
    if player.ripple_mode == "all_tracks":
        for key, panel in player._dict_track_panels().items():
            if key != track_key:
                ripple_shift({key: panel}, old_start_s, effective_delta_s, "per_track")
    if player.ripple_mode != "off":
        ripple_shift({track_key: storage}, old_start_s, effective_delta_s, "per_track")
    length = float(item.get("end", 0.0) or 0.0) - float(item.get("start", 0.0) or 0.0)
    item["start"] = float(new_start_s)
    item["end"] = float(new_start_s) + length
    player._insert_sorted(storage, item)


def _commit_move_item(player, source_key, target_key, item_id, old_start_s, old_end_s, new_start_s):
    source = player._track_storage_for(source_key)
    target = player._track_storage_for(target_key)
    index = player._storage_index_of(source, item_id)
    if index is None:
        return
    item = source.pop(index)
    item["start"] = float(new_start_s)
    item["end"] = float(new_start_s) + (float(old_end_s) - float(old_start_s))
    item["type"] = player._target_item_type(target_key, player._element_media_class(item), item)
    player._insert_sorted(target, item)


def _commit_remove_item(player, track_key, item_id):
    storage = player._track_storage_for(track_key)
    index = player._storage_index_of(storage, item_id)
    if index is not None:
        storage.pop(index)


def _commit_insert_item(player, track_key, payload):
    storage = player._track_storage_for(track_key)
    item = copy.deepcopy(payload)
    item_id = str(item.get("id", "") or "")
    if item_id and player._storage_index_of(storage, item_id) is not None:
        return
    if not item_id:
        item["id"] = uuid.uuid4().hex
    item["type"] = player._target_item_type(track_key, player._element_media_class(item), item)
    player._insert_sorted(storage, item)


def _commit_shift_track(player, track_key, from_s, delta_s):
    storage = player._track_storage_for(track_key)
    ripple_shift({track_key: storage}, float(from_s or 0.0), float(delta_s or 0.0), "per_track")


def _commit_remove_main_segment(player, payload):
    index = player._find_segment_index(payload)
    if index is None:
        return
    duration = float(player.timeline[index].duration)
    gap = player._make_gap_segment(duration)
    if gap is not None:
        player.timeline[index] = gap


def _commit_insert_main_segment(player, position_s, payload):
    if not player.timeline:
        player.timeline = [_segment_from_engine_item(payload, 0.0)]
        return
    at_time = min(max(0.0, float(position_s or 0.0)), total_duration(player.timeline))
    segment = _segment_from_engine_item(payload, at_time)
    player.timeline = insert_segments(player.timeline, at_time, [segment])
    player.edit_points = adjust_points_after_insert(player.edit_points, at_time, float(segment.duration))


def _commit_ripple_main_gap(player, position_s, delta_s):
    if player.ripple_mode != "all_tracks":
        return
    delta = float(delta_s or 0.0)
    if delta <= 0:
        return
    player.timeline = _shift_main_segments(player.timeline, float(position_s or 0.0), delta)
    player.edit_points = adjust_points_after_insert(player.edit_points, float(position_s or 0.0), delta)


def _commit_ripple_main_range(player, start_s, end_s):
    if player.ripple_mode != "all_tracks":
        return
    start_time = max(0.0, float(start_s or 0.0))
    end_time = max(start_time, float(end_s or 0.0))
    if end_time <= start_time:
        return
    if str(getattr(player, "media_kind", "") or "") != "video":
        player.timeline = clean_delete_range(player.timeline, start_time, end_time)
    player.timeline = delete_range(player.timeline, start_time, end_time)
    if str(getattr(player, "media_kind", "") or "") == "video":
        player.timeline = apply_audio_cut_fade_at_boundary(player.timeline, start_time)
    player.edit_points = adjust_points_after_delete(player.edit_points, start_time, end_time)


_COMMIT_HANDLERS = {
    "nudge_item": _commit_nudge_item,
    "move_item": _commit_move_item,
    "remove_item": _commit_remove_item,
    "insert_item": _commit_insert_item,
    "shift_track": _commit_shift_track,
    "remove_main_segment": _commit_remove_main_segment,
    "insert_main_segment": _commit_insert_main_segment,
    "ripple_main_gap": _commit_ripple_main_gap,
    "ripple_main_range": _commit_ripple_main_range,
}


def commit_engine_to_player(player, ops):
    for op in ops or ():
        handler = _COMMIT_HANDLERS.get(op[0]) if isinstance(op, tuple) else None
        if handler is not None:
            handler(player, *op[1:])
