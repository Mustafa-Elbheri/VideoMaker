from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerTimelineEditMixin:
    def OnRemoveSilence(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        dialog = RemoveSilenceDialog(self)
        result = wx.ID_CANCEL
        try:
            result = dialog.ShowModal()
        finally:
            dialog.Destroy()
        if result == wx.ID_OK:
            wx.CallAfter(self.Raise)
            wx.CallAfter(self.SetFocus)

    def create_remove_silence_preview(self, threshold, minimum, padding, progress_callback=None):
        start_time, end_time = selected_range(self)
        return preview_silence_removed(self.timeline, start_time, end_time, threshold, minimum, padding, progress_callback)

    def apply_remove_silence(self, threshold, minimum, padding, progress_callback=None, cancelled_callback=None, intervals=None, removed=None):
        start_time, end_time = selected_range(self)
        before_state = self.capture_edit_state()
        try:
            has_cached = intervals is not None
            detection_max = 5 if has_cached else 85
            
            def apply_progress(value, message):
                if progress_callback:
                    progress_callback(min(detection_max, int(max(0, min(100, value)) * (detection_max / 100.0))), message)

            if intervals is not None:
                updated, removed, intervals = apply_silence_intervals(self.timeline, start_time, end_time, intervals, removed, apply_progress, True)
            else:
                updated, removed, intervals = apply_silence_removed(self.timeline, start_time, end_time, threshold, minimum, padding, apply_progress, True, cancelled_callback)
                
            self.timeline = updated
            self.compact_timed_items_for_kept_intervals(start_time, end_time, intervals)
            kept_duration = sum(max(0.0, float(end or 0.0) - float(start or 0.0)) for start, end in intervals)
            self.mark_timeline_range_navigation_group(start_time, kept_duration, f"remove_silence:{uuid.uuid4().hex}")
            had_override = bool(before_state.get("main_audio_override_path")) and self.audio_override_manager.valid_audio_file(before_state.get("main_audio_override_path", ""))
            audio_path = ""
            audio_duration = 0.0
            
            def prepare_progress(value, message):
                if progress_callback:
                    # value is 0 to 100 from prepare_remove_silence_audio_file
                    # But wait, prepare_remove_silence_audio_file internally scales to 86-99!
                    # So we should pass a callback that doesn't scale again if prepare internally scales?
                    # Let's just pass progress_callback directly and modify prepare_remove_silence_audio_file instead!
                    pass
            
            # Since prepare_remove_silence_audio_file internally maps to 86-99, we MUST NOT modify it here.
            # Instead, we will pass a custom progress_callback to prepare_remove_silence_audio_file!
            # Wait, prepare_remove_silence_audio_file is called with `progress_callback` directly.
            
            def mapped_progress_callback(value, message):
                if not progress_callback: return
                # If we have cached intervals, detection was instant (0 to 5%).
                # Then we jump to prepare_remove_silence_audio_file.
                # prepare_remove_silence_audio_file reports values from 86 to 99 internally.
                # If we want smooth progress from 1% to 100%, we need to un-scale it.
                if has_cached and value >= 86:
                    # Un-scale from 86-99 to 5-99
                    original = (value - 86) / 13.0
                    new_val = 5 + int(original * 94)
                    progress_callback(min(99, new_val), message)
                else:
                    progress_callback(value, message)

            if self.media_kind == "video":
                if not self.use_reliable_audio and reliable_audio_available():
                    self.use_reliable_audio = True
                    self.original_audio_player = ReliableAudioPlayer()
                if not had_override:
                    audio_path, audio_duration, _temp_dir = self.prepare_remove_silence_audio_file(mapped_progress_callback, cancelled_callback)
                    self.main_audio_override_path = audio_path
                    self.main_audio_override_duration = audio_duration
                    self.main_audio_override_timeline_duration = self.timeline_duration()
            elif self.media_kind == "audio":
                audio_path, audio_duration, _temp_dir = self.prepare_remove_silence_audio_file(mapped_progress_callback, cancelled_callback)
                if audio_path:
                    self.timeline = replacement_segments_preserving_files(
                        self.timeline, 0.0, self.timeline_duration(), audio_path, audio_duration
                    )
                    self.video_path = audio_path
            self.current_time = min(start_time, self.timeline_duration())
            self.start_time = None
            self.end_time = None
            self.is_dirty = True
            self.record_edit("إزالة الصمت", before_state, audio_policy="already_updated" if self.main_audio_override_path != before_state.get("main_audio_override_path", "") else "auto")
            wx.CallAfter(self.reload_current_position)
            wx.CallAfter(self.say, speech_messages.SILENCE_REMOVED)
            return removed
        except Exception:
            self.apply_edit_state(before_state)
            raise

    def OnDeleteSegment(self, event=None):
        if not self.require_open_file():
            return
        if self.start_time is not None and self.end_time is not None and self.start_time < self.end_time:
            resume_playback = self.stop_playback_for_timeline_edit("delete_segment")
            before_state = self.capture_edit_state()
            print(f"Deleting segment from {self.start_time} to {self.end_time} seconds")
            if self.media_kind == "video":
                delete_start, delete_end = self.start_time, self.end_time
            else:
                delete_start, delete_end = clean_delete_range(self.timeline, self.start_time, self.end_time)
            deleted_segments = slice_segments(self.timeline, delete_start, delete_end)
            self.timeline = delete_range(self.timeline, delete_start, delete_end)
            if self.media_kind == "video":
                self.timeline = apply_audio_cut_fade_at_boundary(self.timeline, delete_start)
            self.adjust_timed_items_after_delete(delete_start, delete_end)
            self.add_edit_point("delete", delete_start, delete_end, "timeline", restore_segments=deleted_segments, mode="restore")
            self.current_time = min(delete_start, self.timeline_duration())
            self.start_time = None
            self.end_time = None
            self.is_dirty = True
            
            self.record_edit("حذف المقطع", before_state)
            self.playback_requested = resume_playback
            self.reload_current_position()
            trace_event(
                "timeline_edit",
                "delete_segment.complete",
                window=getattr(self, "window_number", None),
                media_kind=getattr(self, "media_kind", ""),
                start=delete_start,
                end=delete_end,
                resume_playback=resume_playback,
            )
            import wx
            if wx.GetApp():
                wx.CallLater(50, lambda: self.say(speech_messages.DELETE_SEGMENT_DONE, wait_for_ui=False))
            else:
                self.say(speech_messages.DELETE_SEGMENT_DONE, wait_for_ui=False)

    def adjust_visual_items_after_delete(self, start_time, end_time):
        self.visual_items = self.adjust_items_after_delete(self.visual_items, start_time, end_time)

    def adjust_background_audio_after_delete(self, start_time, end_time):
        self.background_audio_items = self.adjust_items_after_delete(self.background_audio_items, start_time, end_time)

    def adjust_timed_items_after_delete(self, start_time, end_time):
        self.adjust_visual_items_after_delete(start_time, end_time)
        self.adjust_background_audio_after_delete(start_time, end_time)
        self.edit_points = adjust_points_after_delete(self.edit_points, start_time, end_time)

    def adjust_items_after_delete(self, items, start_time, end_time):
        removed = max(0, end_time - start_time)
        if removed <= 0:
            return items
        adjusted_items = []
        for item in items:
            item_start = float(item["start"])
            item_end = float(item["end"])
            if item_end <= start_time:
                adjusted_items.append(dict(item))
            elif item_start >= end_time:
                updated = dict(item)
                updated["start"] = max(0, item_start - removed)
                updated["end"] = max(updated["start"], item_end - removed)
                adjusted_items.append(updated)
            else:
                item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
                if item_start < start_time:
                    before = dict(item)
                    before["start"] = item_start
                    before["end"] = start_time
                    if before["end"] > before["start"]:
                        adjusted_items.append(before)
                if item_end > end_time:
                    after = dict(item)
                    after["start"] = start_time
                    after["end"] = max(start_time, item_end - removed)
                    after["source_offset"] = source_offset + max(0.0, end_time - item_start) * item_speed
                    if after["end"] > after["start"]:
                        adjusted_items.append(after)
        return adjusted_items

    def compact_visual_items_for_kept_intervals(self, start_time, end_time, intervals):
        self.visual_items = self.compact_items_for_kept_intervals(self.visual_items, start_time, end_time, intervals)

    def compact_background_audio_for_kept_intervals(self, start_time, end_time, intervals):
        self.background_audio_items = self.compact_items_for_kept_intervals(self.background_audio_items, start_time, end_time, intervals)

    def compact_timed_items_for_kept_intervals(self, start_time, end_time, intervals):
        self.compact_visual_items_for_kept_intervals(start_time, end_time, intervals)
        self.compact_background_audio_for_kept_intervals(start_time, end_time, intervals)

    def compact_items_for_kept_intervals(self, items, start_time, end_time, intervals):
        selected_duration = max(0, end_time - start_time)
        kept_duration = sum(end - start for start, end in intervals)
        removed = selected_duration - kept_duration
        adjusted_items = []
        cumulative = 0
        for item in items:
            item_start = float(item["start"])
            item_end = float(item["end"])
            if item_end <= start_time:
                adjusted_items.append(dict(item))
            elif item_start >= end_time:
                updated = dict(item)
                updated["start"] = max(0, item_start - removed)
                updated["end"] = max(updated["start"], item_end - removed)
                adjusted_items.append(updated)
            else:
                item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
                cumulative = 0
                for keep_start, keep_end in intervals:
                    absolute_keep_start = start_time + keep_start
                    absolute_keep_end = start_time + keep_end
                    overlap_start = max(item_start, absolute_keep_start)
                    overlap_end = min(item_end, absolute_keep_end)
                    if overlap_end > overlap_start:
                        updated = dict(item)
                        updated["start"] = start_time + cumulative + (overlap_start - absolute_keep_start)
                        updated["end"] = start_time + cumulative + (overlap_end - absolute_keep_start)
                        updated["source_offset"] = source_offset + max(0.0, overlap_start - item_start) * item_speed
                        adjusted_items.append(updated)
                    cumulative += keep_end - keep_start
        return adjusted_items

    def shift_visual_items_after_insert(self, time, duration):
        self.visual_items = self.shift_items_after_insert(self.visual_items, time, duration)

    def shift_background_audio_after_insert(self, time, duration):
        self.background_audio_items = self.shift_items_after_insert(self.background_audio_items, time, duration)

    def shift_timed_items_after_insert(self, time, duration):
        self.shift_visual_items_after_insert(time, duration)
        self.shift_background_audio_after_insert(time, duration)
        self.edit_points = adjust_points_after_insert(self.edit_points, time, duration)

    def shift_items_after_insert(self, items, time, duration):
        if duration <= 0:
            return items
        adjusted_items = []
        for item in items:
            updated = dict(item)
            if updated["start"] >= time:
                updated["start"] += duration
                updated["end"] += duration
            elif updated["end"] > time:
                updated["end"] += duration
            adjusted_items.append(updated)
        return adjusted_items

    def repeat_visual_items_for_selection(self, start_time, end_time, count):
        self.visual_items = self.repeat_items_for_selection(self.visual_items, start_time, end_time, count)

    def repeat_background_audio_for_selection(self, start_time, end_time, count):
        self.background_audio_items = self.repeat_items_for_selection(self.background_audio_items, start_time, end_time, count)

    def repeat_timed_items_for_selection(self, start_time, end_time, count):
        self.repeat_visual_items_for_selection(start_time, end_time, count)
        self.repeat_background_audio_for_selection(start_time, end_time, count)

    def repeat_items_for_selection(self, items, start_time, end_time, count):
        duration = max(0, end_time - start_time)
        if duration <= 0 or count <= 0:
            return items
        added_duration = duration * max(0, count - 1)
        adjusted_items = []
        for item in items:
            item_start = item["start"]
            item_end = item["end"]
            if item_end <= start_time:
                adjusted_items.append(item)
            elif item_start >= end_time:
                updated = dict(item)
                updated["start"] += added_duration
                updated["end"] += added_duration
                adjusted_items.append(updated)
            elif item_start >= start_time and item_end <= end_time:
                relative_start = item_start - start_time
                relative_end = item_end - start_time
                for index in range(count):
                    updated = dict(item)
                    updated["start"] = start_time + index * duration + relative_start
                    updated["end"] = start_time + index * duration + relative_end
                    adjusted_items.append(updated)
            elif item_start < start_time and item_end > end_time:
                updated = dict(item)
                updated["end"] += added_duration
                adjusted_items.append(updated)
            elif item_start < start_time:
                updated = dict(item)
                updated["end"] = start_time
                if updated["end"] > updated["start"]:
                    adjusted_items.append(updated)
            elif item_end > end_time:
                updated = dict(item)
                updated["start"] = start_time + duration * count
                updated["end"] = item_end + added_duration
                if updated["end"] > updated["start"]:
                    adjusted_items.append(updated)
        return adjusted_items

