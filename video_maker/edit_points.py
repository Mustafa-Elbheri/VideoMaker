import uuid

from video_maker.timeline import TimelineSegment


EPSILON = 0.03


POINT_DESCRIPTIONS = {
    "image": "هنا أضفت صورة",
    "text": "هنا أضفت نص",
    "video": "هنا أضفت فديو",
    "visual_effect": "هنا أضفت مأثر مرئي",
    "audio_effect": "هنا أضفت مأثر صوتي",
    "background_audio": "هنا أضفت خلفية صوتية",
    "b_roll": "هنا أضفت فديو ثانوي",
    "sound_effect": "هنا أضفت مؤثر صوتي",
    "speed": "هنا غيرت السرعة",
    "rotate_video": "هنا قمت بتدوير الفيديو",
    "mute_original_audio": "هنا كتمت الجزء المحدد",
    "censor_bleep": "هنا كتمت كلمة بصوت تغطية",
    "delete": "هنا قمت بحذف جزء",
    "cut": "هنا قمت بقص جزء",
}


DELETE_NAMES = {
    "image": "الصورة",
    "text": "النص",
    "video": "الفديو",
    "visual_effect": "المأثر المرئي",
    "audio_effect": "المأثر الصوتي",
    "background_audio": "الخلفية الصوتية",
    "b_roll": "المقطع الثانوي",
    "sound_effect": "المؤثر الصوتي",
    "speed": "تغيير السرعة",
    "rotate_video": "تدوير الفيديو",
    "mute_timeline_audio": "كتم صوت الخط الزمني كامل",
    "mute_original_audio": "كتم الجزء المحدد",
    "censor_bleep": "كتم الكلمة",
    "delete": "الحذف",
    "cut": "القص",
}


def segments_to_dicts(segments):
    return [
        {
            "path": segment.path,
            "start": segment.start,
            "end": segment.end,
            "speed": float(getattr(segment, "speed", 1.0) or 1.0),
            "audio_volume": float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
            "audio_path": str(getattr(segment, "audio_path", "") or ""),
            "audio_start": getattr(segment, "audio_start", None),
            "navigation_group": str(getattr(segment, "navigation_group", "") or ""),
            "source_file_id": str(getattr(segment, "source_file_id", "") or ""),
            "source_file_name": str(getattr(segment, "source_file_name", "") or ""),
            "transition": str(getattr(segment, "transition", "") or ""),
            "transition_duration": max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            "audio_fade_in": max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            "audio_fade_out": max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        }
        for segment in segments or []
    ]


def dicts_to_segments(items):
    return [
        TimelineSegment(
            item["path"],
            float(item["start"]),
            float(item["end"]),
            float(item.get("speed", 1.0) or 1.0),
            float(item.get("audio_volume", 1.0) if item.get("audio_volume", 1.0) is not None else 1.0),
            str(item.get("audio_path", "") or ""),
            float(item["audio_start"]) if item.get("audio_start") is not None else None,
            str(item.get("navigation_group", "") or ""),
            str(item.get("source_file_id", "") or ""),
            str(item.get("source_file_name", "") or ""),
            str(item.get("transition", "") or ""),
            max(0.0, float(item.get("transition_duration", 1.0) or 1.0)),
            max(0.0, float(item.get("audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(item.get("audio_fade_out", 0.0) or 0.0)),
        )
        for item in items or []
    ]


def make_edit_point(kind, start, end, target="timeline", item_id="", restore_segments=None, mode="", label=""):
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "target": target,
        "item_id": item_id,
        "start": float(start),
        "end": float(end),
        "mode": mode,
        "label": label,
        "restore_segments": segments_to_dicts(restore_segments),
    }


def normalize_edit_point(point):
    if not isinstance(point, dict):
        return None
    try:
        start = float(point.get("start", 0) or 0)
        end = float(point.get("end", start) or start)
    except (TypeError, ValueError):
        return None
    if end < start:
        end = start
    normalized = {
        "id": point.get("id") or uuid.uuid4().hex,
        "kind": point.get("kind") or "edit",
        "target": point.get("target") or "timeline",
        "item_id": point.get("item_id") or "",
        "start": start,
        "end": end,
        "mode": point.get("mode") or "",
        "label": point.get("label") or "",
        "restore_segments": list(point.get("restore_segments") or []),
    }
    if point.get("transition"):
        normalized["transition"] = point.get("transition")
    if point.get("transition_duration"):
        normalized["transition_duration"] = float(point.get("transition_duration") or 1.0)
    return normalized


def normalize_edit_points(points):
    result = []
    for point in points or []:
        normalized = normalize_edit_point(point)
        if normalized:
            result.append(normalized)
    return sorted(result, key=lambda item: (item["start"], item["end"], item["kind"], item["id"]))


def point_description(point):
    label = point.get("label") or ""
    return label or POINT_DESCRIPTIONS.get(point.get("kind"), "هنا نقطة تعديل")


def delete_name(point):
    return DELETE_NAMES.get(point.get("kind"), "نقطة التعديل")


def sorted_points(points):
    return normalize_edit_points(points)


def next_point(points, current_time):
    ordered = sorted_points(points)
    for index, point in enumerate(ordered):
        if point["start"] > current_time + EPSILON:
            return index, point, len(ordered)
    return (0, ordered[0], len(ordered)) if ordered else (None, None, 0)


def previous_point(points, current_time):
    ordered = sorted_points(points)
    for offset, point in enumerate(reversed(ordered)):
        if point["start"] < current_time - EPSILON:
            index = len(ordered) - 1 - offset
            return index, point, len(ordered)
    return (len(ordered) - 1, ordered[-1], len(ordered)) if ordered else (None, None, 0)


def point_by_id(points, point_id):
    for point in normalize_edit_points(points):
        if point["id"] == point_id:
            return point
    return None


def point_at_time(points, current_time):
    ordered = sorted_points(points)
    for index, point in enumerate(ordered):
        if abs(point["start"] - current_time) <= EPSILON:
            return index, point, len(ordered)
    return None, None, len(ordered)


def remove_point(points, point_id):
    return [point for point in normalize_edit_points(points) if point["id"] != point_id]


def adjust_points_after_delete(points, start_time, end_time, skip_id=None):
    removed = max(0.0, float(end_time) - float(start_time))
    if removed <= 0:
        return normalize_edit_points(points)
    adjusted = []
    for point in normalize_edit_points(points):
        if skip_id and point["id"] == skip_id:
            adjusted.append(point)
            continue
        start = point["start"]
        end = point["end"]
        if end <= start_time:
            adjusted.append(point)
        elif start >= end_time:
            updated = dict(point)
            updated["start"] = max(0.0, start - removed)
            updated["end"] = max(updated["start"], end - removed)
            adjusted.append(updated)
        elif start < start_time:
            updated = dict(point)
            updated["end"] = max(start_time, updated["start"])
            if updated["end"] >= updated["start"]:
                adjusted.append(updated)
        elif end > end_time:
            updated = dict(point)
            updated["start"] = start_time
            updated["end"] = max(start_time, end - removed)
            adjusted.append(updated)
    return normalize_edit_points(adjusted)


def adjust_points_after_insert(points, time_value, duration, skip_id=None):
    duration = max(0.0, float(duration))
    if duration <= 0:
        return normalize_edit_points(points)
    adjusted = []
    for point in normalize_edit_points(points):
        if skip_id and point["id"] == skip_id:
            adjusted.append(point)
            continue
        if point["start"] >= time_value:
            updated = dict(point)
            updated["start"] += duration
            updated["end"] += duration
            adjusted.append(updated)
        else:
            adjusted.append(point)
    return normalize_edit_points(adjusted)
