import os

import wx

from video_maker.dialogs import AUDIO_WILDCARD, prepare_media_file_dialog, remember_media_paths
from video_maker.localization import tr
from video_maker.logical_files import new_file_segment
from video_maker.timeline import insert_segments


class TimelineAudioVideoSaveBlocked(RuntimeError):
    pass


def choose_timeline_audio_path(parent=None):
    with wx.FileDialog(parent, tr("إدراج صوت"), wildcard=AUDIO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
        prepare_media_file_dialog(dialog, "audio", "insert_timeline_audio")
        if dialog.ShowModal() == wx.ID_CANCEL:
            return ""
        path = dialog.GetPath()
        remember_media_paths([path], "audio", "insert_timeline_audio")
        return path


def inserted_audio_timeline(timeline, audio_path, insert_time, duration):
    insert_time = max(0.0, float(insert_time or 0.0))
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0.0:
        return list(timeline or [])
    return insert_segments(list(timeline or []), insert_time, [new_file_segment(audio_path, 0.0, duration)])


def _covered_by_visuals(start, end, visual_ranges):
    cursor = float(start)
    target_end = float(end)
    for visual_start, visual_end in sorted(visual_ranges):
        visual_start = float(visual_start)
        visual_end = float(visual_end)
        if visual_end <= cursor:
            continue
        if visual_start > cursor + 0.001:
            return False
        cursor = max(cursor, visual_end)
        if cursor >= target_end - 0.001:
            return True
    return cursor >= target_end - 0.001


def _item_range(item):
    try:
        start = float(item.get("start", 0.0) or 0.0)
        end = float(item.get("end", start) or start)
    except (AttributeError, TypeError, ValueError):
        return None
    if end <= start:
        return None
    return start, end


def visual_coverage_ranges(visual_items=None, b_roll_items=None):
    ranges = []
    for item in list(visual_items or []) + list(b_roll_items or []):
        item_range = _item_range(item)
        if item_range:
            ranges.append(item_range)
    return ranges


def video_save_block_message_for_timeline(timeline, visual_items, b_roll_items, has_audio_stream, has_video_stream):
    visual_ranges = visual_coverage_ranges(visual_items, b_roll_items)
    position = 0.0
    for segment in timeline or []:
        duration = max(0.0, float(getattr(segment, "duration", 0.0) or 0.0))
        end_time = position + duration
        path = str(getattr(segment, "path", "") or "")
        has_audio = False
        has_video = False
        if path and os.path.exists(path):
            try:
                has_audio = bool(has_audio_stream(path))
            except Exception:
                has_audio = False
            try:
                has_video = bool(has_video_stream(path))
            except Exception:
                has_video = False
        if has_audio and not has_video and not _covered_by_visuals(position, end_time, visual_ranges):
            return tr("لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو.")
        position = end_time
    return ""
