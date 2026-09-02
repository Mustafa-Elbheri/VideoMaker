from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerNavigationAudioMixin:
    def invalidate_pending_media_load(self):
        self.media_load_generation += 1
        self.pending_seek_ms = None
        self.pending_play = False
        self.pending_media_load_checks = 0
        self.pending_seamless_media_switch = False
        self.pending_continuous_audio_preserved = False

    def timeline_segment_boundaries(self):
        return self.timeline_metrics()[1]

    def locate_timeline_segment(self, time_value):
        if not self.timeline:
            return None, None, 0.0
        positions = self.timeline_metrics()[0]
        index = bisect.bisect_right(positions, float(time_value)) - 1
        index = max(0, min(index, len(self.timeline) - 1))
        return index, self.timeline[index], positions[index]

    def timeline_time_from_media_ms(self, media_ms, segment, segment_position, preview_path=""):
        if media_ms is None or isinstance(media_ms, bool):
            return None
        try:
            media_ms = float(media_ms)
        except (TypeError, ValueError):
            return None
        if media_ms < 0:
            return None
        if preview_path:
            return max(0.0, min(media_ms / 1000.0, self.timeline_duration()))
        segment_speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
        media_seconds = media_ms / 1000.0
        local_seconds = max(float(segment.start), min(media_seconds, float(segment.end)))
        return segment_position + max(0.0, local_seconds - float(segment.start)) / segment_speed

    def smooth_seek_target(self, target, direction):
        duration = self.timeline_duration()
        target = max(0.0, min(duration, float(target)))
        if not self.timeline or direction == 0:
            return target
        boundaries = self.timeline_segment_boundaries()
        boundary_index = bisect.bisect_left(boundaries, target)
        nearby_boundaries = []
        if boundary_index < len(boundaries):
            nearby_boundaries.append(boundaries[boundary_index])
        if boundary_index > 0:
            nearby_boundaries.append(boundaries[boundary_index - 1])
        for boundary in nearby_boundaries:
            if abs(target - boundary) <= SEEK_BOUNDARY_NUDGE:
                if direction > 0:
                    return min(duration, boundary + SEEK_BOUNDARY_NUDGE)
                return max(0.0, boundary - SEEK_BOUNDARY_NUDGE)
        return target

    def seek_timeline_by(self, delta, fine=False):
        import time
        now = time.monotonic()
        last_seek = getattr(self, "_last_audio_tick_time", 0)
        self._last_audio_tick_time = now
        if get_program_mode() == PROFESSIONAL_MODE:
            self._clear_element_selection()
        
        target = self.smooth_seek_target(self.current_time + delta, 1 if delta > 0 else -1 if delta < 0 else 0)

        # As in REAPER, pressing a scrub key while playing stops transport first,
        # then scrubs the new position. This happens before the slice so the
        # playing audio cannot overlap with it.
        if self.playback_requested and self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
            try:
                self.media_ctrl.Pause()
            except Exception:
                pass
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()

        is_normal_mode = get_program_mode() == NORMAL_MODE
        is_long_press = (now - last_seek < 0.15)

        if is_normal_mode:
            self.stop_scrub_playback()
            if is_long_press:
                self.play_normal_tape_scrub_sound(now)
        else:
            # Audio scrubbing: play a real micro slice of the audio at the new position.
            self.scrub_preview_slice(target, delta, fine)


        self.current_time = target

        # Apply the visual seek immediately without resuming playback
        self.load_timeline_time(self.current_time, play=False)

        # Cancel any existing debounce timer
        if getattr(self, "seek_debounce_call", None):
            self.seek_debounce_call.Stop()
            self.seek_debounce_call = None

        # Schedule playback to resume 150ms after the last key repeat
        if self.playback_requested:
            import wx
            self.seek_debounce_call = wx.CallLater(150, self._apply_debounced_seek)

    def scrub_preview_slice(self, target_time, delta, fine=False):
        """تشغيل شريحة صوتية (20-50ms) من الموقع الجديد.

        سرعة الشريحة تتبع مقدار الحركة (نمط REAPER): كلما كبرت خطوة
        التحرك كبرت السرعة، وكلما صغرت هبطت مع انخفاض الدرجة. لا نستدعي
        القارئ الصوتي هنا حتى لا تتعارض الشريحة مع نطق موقع المؤشر.
        يُرجع True إذا أُنتجت الشريحة فعليًا.
        """
        if not self.timeline:
            return False
        try:
            from video_maker.scrub_audio import (
                ScrubPlayer,
                scrub_rate_for_step,
                scrub_request_for_timeline_point,
            )
            if not getattr(self, "scrub_player", None) and reliable_audio_available():
                self.scrub_player = ScrubPlayer()
            if not self.scrub_player or not self.scrub_player.available:
                return False
            try:
                delta_abs = abs(float(delta or 0.0))
            except (TypeError, ValueError):
                delta_abs = 0.0
            rate = scrub_rate_for_step(delta_abs, fine)
            request = scrub_request_for_timeline_point(
                timeline=self.timeline,
                timeline_time=target_time,
                has_override=self.has_main_audio_override(),
                override_path=str(self.main_audio_override_path or ""),
                output_volume=self.effective_output_volume(),
                rate=rate,
                step_seconds=delta_abs,
            )
            if not request:
                return False
            return self.scrub_player.play_request(request)
        except Exception:
            return False

    def play_normal_tape_scrub_sound(self, now=None):
        last_scrub_sound = getattr(self, "_last_scrub_sound_time", 0)
        if now is None:
            now = time.monotonic()
        if now - last_scrub_sound <= 0.1:
            return False
        self._last_scrub_sound_time = now
        try:
            from video_maker.ui_sounds import play_ui_sound
            return bool(play_ui_sound("tape_scrub.wav"))
        except Exception:
            return False

    def stop_scrub_playback(self):
        """إيقاف شريحة الـ Scrubbing عند بدء التشغيل العادي حتى لا يتزاحم
        الدفق الصوتي مع الصوت الأساسي."""
        scrub_player = getattr(self, "scrub_player", None)
        if scrub_player is not None:
            try:
                scrub_player.stop()
            except Exception:
                pass
    def _apply_debounced_seek(self):
        self.seek_debounce_call = None
        if self.playback_requested:
            self.load_timeline_time(self.current_time, play=True)

    def OnNextItemEdge(self, event=None):
        if not self.require_open_file():
            return
        self.jump_to_added_edge(True)

    def OnPreviousItemEdge(self, event=None):
        if not self.require_open_file():
            return
        self.jump_to_added_edge(False)

    def move_one_second(self, direction, fine=False):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self.seek_timeline_by(direction, fine)
        self.say(tr("الوقت الحالي {current}").format(current=self.spoken_time(self.current_time)))

    def OnFineForward(self, event=None):
        if not self.require_open_file():
            return
        self.move_one_second(1.0, fine=True)

    def OnFineRewind(self, event=None):
        if not self.require_open_file():
            return
        self.move_one_second(-1.0, fine=True)

    def OnSelectFromStartToCurrent(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self.start_time = 0
        self.end_time = max(0.0, min(self.current_time, self.timeline_duration()))
        self.say(tr("تم تحديد من البداية إلى الموضع الحالي"))

    def OnSelectFromCurrentToEnd(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self.start_time = max(0.0, min(self.current_time, self.timeline_duration()))
        self.end_time = self.timeline_duration()
        self.say(tr("تم تحديد من الموضع الحالي إلى النهاية"))

    def current_background_audio_match(self):
        active = self.active_background_audio_items(self.current_time)
        if not active:
            return None, None
        return active[-1]

    def OnAdjustCurrentBackgroundVolume(self, delta):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        key, item = self.current_background_audio_match()
        if not item:
            self.say(tr("لا توجد خلفية صوتية عند الموضع الحالي"))
            return
        item["volume"] = max(0.0, min(1.0, normalized_volume(item.get("volume", 1.0)) + delta))
        self.is_dirty = True
        self.save_crash_session_now()
        self.sync_background_audio_playback(self.playback_requested, False)
        self.say(tr("صوت الخلفية {percent} بالمئة").format(percent=int(round(item["volume"] * 100))), wait_for_ui=False)

    def OnIncreaseCurrentBackgroundVolume(self, event=None):
        if not self.require_open_file():
            return
        self.OnAdjustCurrentBackgroundVolume(0.1)

    def OnDecreaseCurrentBackgroundVolume(self, event=None):
        if not self.require_open_file():
            return
        self.OnAdjustCurrentBackgroundVolume(-0.1)

    def OnMuteBackgroundAudioSelection(self, event=None):
        if not self.require_open_file():
            return
        selected = self.selected_transform_range()
        if not selected:
            return
        start_time, end_time = selected
        before_state = self.capture_edit_state()
        updated_items, changed, touched = mute_timed_audio_items_range(
            self.background_audio_items,
            start_time,
            end_time,
        )
        if not touched:
            self.say(tr("لا توجد خلفية صوتية في الجزء المحدد"), wait_for_ui=False)
            return
        if not changed:
            self.say(tr("صوت الخلفية مكتوم بالفعل في الجزء المحدد"), wait_for_ui=False)
            return
        self.background_audio_items = updated_items
        self.current_time = start_time
        self.is_dirty = True
        self.record_edit(tr("كتم صوت الخلفية في الجزء المحدد"), before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم كتم صوت الخلفية في الجزء المحدد"), wait_for_ui=False)

    def sorted_background_audio_items(self):
        return sorted(
            [item for item in self.background_audio_items if item.get("path")],
            key=lambda item: (float(item.get("start", 0) or 0), float(item.get("end", 0) or 0), item.get("id", "")),
        )

    def jump_to_background_audio(self, forward=True):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        items = self.sorted_background_audio_items()
        if not items:
            self.say(tr("لا توجد خلفيات صوتية"))
            return
        if forward:
            index = next((i for i, item in enumerate(items) if float(item.get("start", 0) or 0) > self.current_time + 0.03), 0)
        else:
            index = next((len(items) - 1 - offset for offset, item in enumerate(reversed(items)) if float(item.get("start", 0) or 0) < self.current_time - 0.03), len(items) - 1)
        item = items[index]
        self.current_time = max(0.0, min(float(item.get("start", 0) or 0), self.timeline_duration()))
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.say(tr("الخلفية الصوتية رقم {number} {name}").format(number=index + 1, name=item.get("name", "")))

    def OnNextBackgroundAudio(self, event=None):
        if not self.require_open_file():
            return
        self.jump_to_background_audio(True)

    def OnPreviousBackgroundAudio(self, event=None):
        if not self.require_open_file():
            return
        self.jump_to_background_audio(False)

    def OnDeleteCurrentBackgroundAudio(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        key, item = self.current_background_audio_match()
        if not item:
            self.say(tr("لا توجد خلفية صوتية عند الموضع الحالي"))
            return
        before_state = self.capture_edit_state()
        item_id = item.get("id", "")
        start_time = float(item.get("start", 0) or 0)
        end_time = float(item.get("end", start_time) or start_time)
        # Release the exact player before removing the item.  Normal Pause keeps
        # players warm for responsiveness, but a deleted item must not keep its
        # source file locked or retain a stale controller.
        self.stop_background_audio_player(key)
        if item_id:
            self.background_audio_items = [
                current for current in self.background_audio_items
                if current is not item and current.get("id") != item_id
            ]
        else:
            self.background_audio_items = [current for current in self.background_audio_items if current is not item]
        if item_id:
            self.edit_points = [point for point in normalize_edit_points(self.edit_points) if point.get("item_id") != item_id]
        removed = max(0.0, end_time - start_time)
        if self.ripple_mode == "per_track":
            ripple_shift({BACKGROUND_AUDIO_TRACK: self.background_audio_items}, end_time, -removed, self.ripple_mode)
        elif self.ripple_mode == "all_tracks":
            ripple_shift(self._dict_track_panels(), end_time, -removed, self.ripple_mode)
            range_start, range_end = clean_delete_range(self.timeline, start_time, end_time)
            if range_end > range_start + 1e-9:
                self.timeline = delete_range(self.timeline, range_start, range_end)
                self.edit_points = adjust_points_after_delete(self.edit_points, range_start, range_end)
        self.current_time = min(start_time, self.timeline_duration())
        self.focused_element = self._refocus_element_after_delete(BACKGROUND_AUDIO_TRACK)
        if self.ripple_mode == "all_tracks":
            self.start_time = None
            self.end_time = None
        else:
            self.start_time = start_time
            self.end_time = min(end_time, self.timeline_duration())
        self.is_dirty = True
        self.record_edit("حذف خلفية صوتية", before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(tr("تم حذف الخلفية الصوتية وتم تحديد النقاط"))

    def effective_output_volume(self):
        return master_linear_into_volume(self.volume, getattr(self, "master_volume_db", 0.0))

    def segment_audio_volume(self, segment=None):
        if segment is None and self.current_segment_index is not None and self.current_segment_index < len(self.timeline):
            segment = self.timeline[self.current_segment_index]
        value = getattr(segment, "audio_volume", 1.0) if segment is not None else 1.0
        if value is None:
            value = 1.0
        return max(0.0, min(1.0, float(value)))

    def effective_original_audio_volume(self, segment=None):
        return self.effective_output_volume() * self.segment_audio_volume(segment) * track_volume_gain(MAIN_VIDEO_TRACK, getattr(self, "track_volumes_db", {}) or {})

    def reset_main_audio_override_state(self):
        """مسح صوت الفيديو البديل وسجله عند بدء مشروع مستقل جديد."""
        self.main_audio_override_path = ""
        self.main_audio_override_duration = 0.0
        self.main_audio_override_timeline_duration = 0.0
        self.main_audio_effect_chain = []
        self.main_audio_revision = 0
        self.main_audio_source_revision = 0
        self.timeline_revision = 0
        self.main_audio_format_version = MainAudioOverrideManager.FORMAT_VERSION
        self.main_audio_override_operation_running = False
        if hasattr(self, "pending_main_audio_override_effect_paths"):
            self.pending_main_audio_override_effect_paths.clear()
        if hasattr(self, "pending_audio_override_transform_metadata"):
            self.pending_audio_override_transform_metadata.clear()

    def normalize_restored_main_audio_override(self):
        """فحص صوت الجلسة المستعادة وتسويته دون ترك المعاينة أو الحفظ في حالة صامتة."""
        if not self.main_audio_override_configured():
            return False
        path = str(self.main_audio_override_path or "")
        if not self.audio_override_manager.valid_audio_file(path):
            self.reset_main_audio_override_state()
            self.say(tr("تعذر استعادة ملف الصوت المعالج وسيستخدم المشروع صوت الخط الزمني الحالي"))
            return True
        expected = max(0.001, self.timeline_duration())
        try:
            actual = self.audio_override_manager.exact_duration(path)
            if abs(actual - expected) <= 0.03:
                self.main_audio_override_duration = actual
                self.main_audio_override_timeline_duration = expected
                self.main_audio_source_revision = int(self.timeline_revision or 0)
                return False
            prepared = self.audio_override_manager.fit_audio_to_duration(path, expected)
            self.main_audio_override_path = prepared.path
            self.main_audio_override_duration = prepared.duration
            self.main_audio_override_timeline_duration = expected
            self.main_audio_source_revision = int(self.timeline_revision or 0)
            self.main_audio_revision = int(self.main_audio_revision or 0) + 1
            if prepared.temp_dir and prepared.temp_dir not in self.generated_temp_dirs:
                self.generated_temp_dirs.append(prepared.temp_dir)
            if prepared.path and prepared.path not in self.generated_temp_files:
                self.generated_temp_files.append(prepared.path)
            self.say(tr("تمت مطابقة مدة صوت المشروع المستعاد مع مدة الفيديو"))
            return True
        except Exception as error:
            self.reset_main_audio_override_state()
            self.say(tr("تعذر تجهيز صوت المشروع المستعاد وسيستخدم المشروع صوت الخط الزمني الحالي"))
            append_problem(
                "restore_main_audio_override",
                "تعذر تجهيز صوت المشروع المستعاد",
                exception=error,
                details=str(error),
            )
            return True

    def main_audio_override_configured(self):
        return (
            self.media_kind == "video"
            and bool(self.main_audio_override_path)
        )

    def has_main_audio_override(self):
        return self.main_audio_override_configured() and os.path.exists(self.main_audio_override_path)

    def main_audio_override_valid(self):
        if not self.main_audio_override_configured() or not os.path.exists(self.main_audio_override_path):
            return False
        expected = self.timeline_duration()
        duration = self.main_audio_override_duration
        if duration <= 0:
            try:
                duration = get_media_duration(self.main_audio_override_path)
                self.main_audio_override_duration = duration
            except Exception:
                return False
        compatible, _tolerance = main_audio_duration_is_compatible(expected, duration)
        return compatible

    def main_audio_override_save_error(self):
        """إرجاع أخطاء الملف الحقيقية فقط؛ اختلاف المدة يُعالج تلقائيًا عند الحفظ."""
        if not self.main_audio_override_configured():
            return ""
        if not os.path.exists(self.main_audio_override_path):
            return tr("ملف صوت المشروع غير موجود")
        if not self.audio_override_manager.valid_audio_file(self.main_audio_override_path):
            return tr("ملف صوت المشروع لا يحتوي على صوت صالح")
        return ""

    def media_control_volume(self, segment=None):
        if not is_track_audible(MAIN_VIDEO_TRACK, getattr(self, "muted_tracks", ()), getattr(self, "solo_tracks", ())):
            return 0.0
        if self.has_main_audio_override():
            return 0.0
        return 0.0 if self.use_reliable_audio else device_volume(self.effective_original_audio_volume(segment))

    def reset_media_control_cached_settings(self):
        self.media_ctrl_volume_cache = None
        self.media_ctrl_rate_cache = None

    def set_media_control_volume(self, segment=None):
        volume = self.media_control_volume(segment)
        if self.media_ctrl_volume_cache is None or abs(float(self.media_ctrl_volume_cache) - float(volume)) > 0.001:
            self.media_ctrl.SetVolume(volume)
            self.media_ctrl_volume_cache = volume

    def set_media_control_playback_rate(self, rate):
        if not hasattr(self.media_ctrl, "SetPlaybackRate"):
            return
        rate = max(0.05, float(rate or 1.0))
        if self.media_ctrl_rate_cache is not None and abs(float(self.media_ctrl_rate_cache) - rate) <= 0.001:
            return
        try:
            self.media_ctrl.SetPlaybackRate(rate)
            self.media_ctrl_rate_cache = rate
        except Exception:
            pass

    def pause_original_audio_playback(self):
        if self.original_audio_player:
            self.original_audio_player.Pause()

    def stop_original_audio_playback(self, wait=False):
        if self.original_audio_player:
            try:
                self.original_audio_player.Stop(wait=wait)
            except TypeError:
                self.original_audio_player.Stop()

    def media_has_audio_stream(self, path):
        key = os.path.abspath(path).lower()
        if key not in self.audio_stream_cache:
            self.audio_stream_cache[key] = True
            threading.Thread(target=self.probe_audio_stream, args=(path, key), daemon=True).start()
        return self.audio_stream_cache[key]

    def probe_audio_stream(self, path, key):
        try:
            self.audio_stream_cache[key] = bool(has_audio_stream(path))
        except Exception:
            self.audio_stream_cache[key] = True

    def disable_reliable_audio_playback(self):
        if not self.use_reliable_audio:
            return
        if self.original_audio_player:
            self.original_audio_player.Stop()
        self.original_audio_player = None
        self.use_reliable_audio = False
        self.stop_background_audio_playback()
        try:
            self.media_ctrl.SetVolume(self.effective_original_audio_volume())
        except Exception:
            pass

    def sync_original_audio_playback(self, play=None, force=False):
        if not self.use_reliable_audio or not self.original_audio_player:
            return
        should_play = self.playback_requested if play is None else bool(play)
        if should_play:
            self.stop_scrub_playback()
        if not should_play or not self.timeline or self.current_segment_index is None or self.pending_seek_ms is not None:
            self.stop_original_audio_playback()
            return
        if not is_track_audible(MAIN_VIDEO_TRACK, getattr(self, "muted_tracks", ()), getattr(self, "solo_tracks", ())):
            self.stop_original_audio_playback()
            return
        if self.has_main_audio_override():
            if not self.main_audio_override_valid():
                self.stop_original_audio_playback()
                return
            duration = max(0.0, float(self.main_audio_override_duration or self.timeline_duration()))
            local_time = min(max(0.0, self.current_time), max(0.0, duration - 0.001))
            segment = self.timeline[self.current_segment_index] if self.current_segment_index < len(self.timeline) else None
            volume = self.effective_original_audio_volume(segment)
            if local_time >= duration:
                self.stop_original_audio_playback()
                return
            if volume <= 0.001:
                player = self.original_audio_player
                if player.GetState() == MEDIASTATE_PLAYING:
                    player.SetVolume(0.0)
                return
            seek_ms = int(local_time * 1000)
            end_ms = int(min(duration, self.timeline_duration()) * 1000)
            player = self.original_audio_player
            same_clip = (
                player.path == self.main_audio_override_path
                and player.GetState() == MEDIASTATE_PLAYING
                and abs(float(player.rate) - 1.0) <= 0.001
                and player.limit_ms == end_ms
            )
            if same_clip:
                if abs(player.Tell() - seek_ms) > 250:
                    player.Configure(self.main_audio_override_path, seek_ms, end_ms, 1.0, volume, duration=duration)
                    if not player.Play() or getattr(player, "last_error", ""):
                        self.disable_reliable_audio_playback()
                    return
                player.SetVolume(volume)
                if getattr(player, "last_error", ""):
                    self.disable_reliable_audio_playback()
                return
            player.Configure(self.main_audio_override_path, seek_ms, end_ms, 1.0, volume, duration=duration)
            if not player.Play() or getattr(player, "last_error", ""):
                self.disable_reliable_audio_playback()
            return
        if self.current_segment_index >= len(self.timeline):
            self.stop_original_audio_playback()
            return
        segment = self.timeline[self.current_segment_index]
        segment_position = self.segment_position(self.current_segment_index)
        segment_speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
        local_time = segment.start + (self.current_time - segment_position) * segment_speed
        local_time = min(max(segment.start, local_time), max(segment.start, segment.end - 0.001))
        volume = self.effective_original_audio_volume(segment)
        audio_path = str(getattr(segment, "audio_path", "") or segment.path)
        if not self.media_has_audio_stream(audio_path):
            self.stop_original_audio_playback()
            return
        if local_time >= segment.end:
            self.stop_original_audio_playback()
            return
        if volume <= 0.001:
            player = self.original_audio_player
            if player.GetState() == MEDIASTATE_PLAYING:
                player.SetVolume(0.0)
            return
        audio_local_time = segment_audio_start(segment) + max(0.0, local_time - segment.start)
        audio_end_time = segment_audio_start(segment) + max(0.0, segment.end - segment.start)
        seek_ms = int(audio_local_time * 1000)
        end_ms = int(audio_end_time * 1000)
        player = self.original_audio_player
        same_clip = (
            player.path == audio_path
            and player.GetState() == MEDIASTATE_PLAYING
            and abs(float(player.rate) - segment_speed) <= 0.001
            and player.limit_ms == end_ms
        )
        if same_clip and not force:
            if abs(player.Tell() - seek_ms) > 250:
                player.Configure(audio_path, seek_ms, end_ms, segment_speed, volume, duration=audio_end_time)
                if not player.Play() or getattr(player, "last_error", ""):
                    self.disable_reliable_audio_playback()
                return
            player.SetVolume(volume)
            if getattr(player, "last_error", ""):
                self.disable_reliable_audio_playback()
            return
        player.Configure(audio_path, seek_ms, end_ms, segment_speed, volume, duration=audio_end_time)
        if not player.Play() or getattr(player, "last_error", ""):
            self.disable_reliable_audio_playback()

    def segment_position(self, index):
        positions = self.timeline_metrics()[0]
        if not positions:
            return 0.0
        index = max(0, min(int(index or 0), len(positions) - 1))
        return positions[index]

    def background_audio_key(self, item):
        return (
            item.get("id", ""),
            os.path.abspath(item.get("path", "")).lower(),
            float(item.get("start", 0) or 0),
            float(item.get("end", 0) or 0),
            float(item.get("volume", 0) or 0),
            float(item.get("speed", 1.0) or 1.0),
            float(item.get("source_offset", 0.0) or 0.0),
        )

    def active_background_audio_items(self, time_value):
        active_items = []
        for index, item in enumerate(getattr(self, "background_audio_items", [])):
            start = float(item.get("start", 0) or 0)
            end = float(item.get("end", 0) or 0)
            path = item.get("path", "")
            if start <= time_value < end and path and os.path.exists(path):
                key = self.background_audio_key(item)
                if not key[0]:
                    key = (f"legacy_{index}",) + key[1:]
                active_items.append((key, item))
        return active_items

    def background_audio_duration(self, path, default=None):
        key = os.path.abspath(path).lower()
        if key not in self.background_audio_durations:
            try:
                default_duration = max(0.0, float(default or 0))
            except (TypeError, ValueError):
                default_duration = 0.0
            if default_duration > 0:
                self.background_audio_durations[key] = default_duration
            else:
                if key not in self.background_audio_duration_probes:
                    self.background_audio_duration_probes.add(key)
                    threading.Thread(target=self.probe_background_audio_duration, args=(path, key), daemon=True).start()
                return 0.0
        return self.background_audio_durations[key]

    def probe_background_audio_duration(self, path, key):
        try:
            duration = max(0.0, float(get_media_duration(path) or 0))
        except Exception:
            duration = 0.0
        self.background_audio_durations[key] = duration
        self.background_audio_duration_probes.discard(key)

    def pause_background_audio_playback(self):
        if self.audio_effect_background_preview_timer:
            try:
                self.audio_effect_background_preview_timer.Stop()
            except Exception:
                pass
            self.audio_effect_background_preview_timer = None
        self.audio_effect_background_preview_state = None
        for state in list(self.background_audio_players.values()):
            # A media load can finish after the user has already pressed Pause.
            # Keep the requested seek position, but never allow that late load to
            # start playing until an explicit resume updates pending_play again.
            state["pending_play"] = False
            ctrl = state.get("ctrl")
            if ctrl is None:
                continue
            try:
                if ctrl.GetState() == MEDIASTATE_PLAYING:
                    ctrl.Pause()
            except Exception:
                pass

    def stop_background_audio_playback(self, wait=False):
        if self.audio_effect_background_preview_timer:
            try:
                self.audio_effect_background_preview_timer.Stop()
            except Exception:
                pass
            self.audio_effect_background_preview_timer = None
        self.audio_effect_background_preview_state = None
        self.stop_all_background_audio_players(wait=wait)

    def stop_all_background_audio_players(self, wait=False):
        for key in list(self.background_audio_players):
            self.stop_background_audio_player(key, wait=wait)

    def stop_background_audio_player(self, key, wait=False):
        state = self.background_audio_players.pop(key, None)
        if not state:
            return
        ctrl = state.get("ctrl")
        try:
            if ctrl.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
                try:
                    ctrl.Stop(wait=wait)
                except TypeError:
                    ctrl.Stop()
            ctrl.Destroy()
        except Exception:
            pass

    def background_audio_player(self, key):
        state = self.background_audio_players.get(key)
        if state:
            return state
        if self.use_reliable_audio:
            ctrl = ReliableAudioPlayer()
            state = {"ctrl": ctrl, "pending_seek_ms": None, "pending_play": False, "pending_checks": 0, "reliable_audio": True}
        else:
            ctrl = MPVMediaCtrl(self.main_panel)
            silence_media_control_accessibility(ctrl)
            ctrl.Hide()
            state = {"ctrl": ctrl, "pending_seek_ms": None, "pending_play": False, "pending_checks": 0, "reliable_audio": False}
        self.background_audio_players[key] = state
        if not state["reliable_audio"]:
            self.Bind(EVT_MEDIA_LOADED, self.OnBackgroundAudioLoaded, ctrl)
            self.Bind(EVT_MEDIA_FINISHED, self.OnBackgroundAudioFinished, ctrl)
        return state

    def background_audio_player_key(self, ctrl):
        for key, state in self.background_audio_players.items():
            if state.get("ctrl") is ctrl:
                return key
        return None

    def _audio_channel_items(self, channels, sfx=False, broll=False):
        """يحول قنوات build_preview_audio_mix إلى (key, item) لمجمع المشغلات."""
        result = []
        for index, channel in enumerate(channels):
            item = channel["item"]
            key = self.background_audio_key(item)
            if not key[0]:
                key = (f"legacy_{index}",) + key[1:]
            if sfx:
                key = ("sfx",) + key
            if broll:
                key = ("broll",) + key
            result.append((key, item))
        return result

    def _sync_active_audio_players(self, active_items, time_value, should_play=True, force=False, keep_preview_state=False, looping=True, track_gain=1.0):
        """يشغّل القنوات النشطة داخل ملفاتها (loop للخلفية، لمرة واحدة للمؤثرات)."""
        for key, item in active_items:
            path = item["path"]
            try:
                source_duration = self.background_audio_duration(path, item.get("source_duration"))
            except Exception:
                self.stop_background_audio_player(key)
                continue
            if source_duration <= 0:
                self.stop_background_audio_player(key)
                continue
            item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
            source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
            local_time = source_offset + max(0.0, time_value - float(item.get("start", 0) or 0)) * item_speed
            if looping:
                local_time = local_time % source_duration
            elif local_time >= source_duration:
                self.stop_background_audio_player(key)
                continue
            seek_ms = int(local_time * 1000)
            item_volume = normalized_volume(item.get("volume", 1.0))
            volume = max(0.0, min(BOOSTED_OUTPUT_VOLUME, self.effective_output_volume() * item_volume * track_gain))
            state = self.background_audio_player(key)
            ctrl = state["ctrl"]
            state["item"] = item
            state["source_duration"] = source_duration
            state["volume"] = volume
            state["speed"] = item_speed
            if self.use_reliable_audio and getattr(ctrl, "last_error", ""):
                self.disable_reliable_audio_playback()
                self.sync_background_audio_at(time_value, should_play, force, keep_preview_state)
                return
            ctrl.SetVolume(volume)
            if hasattr(ctrl, "SetPlaybackRate"):
                try:
                    ctrl.SetPlaybackRate(item_speed)
                except Exception:
                    pass
            if state.get("path") != path:
                state["path"] = path
                state["pending_seek_ms"] = seek_ms
                state["pending_play"] = True
                state["pending_checks"] = 0
                if state.get("reliable_audio"):
                    loaded = ctrl.Load(path, source_duration)
                else:
                    loaded = ctrl.Load(path)
                if not loaded:
                    self.stop_background_audio_player(key)
                else:
                    wx.CallLater(5, self.finish_pending_background_audio_load, key)
                continue
            if state.get("pending_seek_ms") is not None:
                # Pause/resume or seeking may happen while wx is still loading
                # the file.  Update the pending request instead of trying to
                # Play an incompletely loaded control.
                state["pending_seek_ms"] = seek_ms
                state["pending_play"] = True
                continue
            if force:
                ctrl.Seek(seek_ms)
            elif state.get("reliable_audio") and ctrl.GetState() == MEDIASTATE_PLAYING and abs(ctrl.Tell() - seek_ms) > 250:
                ctrl.Seek(seek_ms)
            if ctrl.GetState() != MEDIASTATE_PLAYING:
                try:
                    if ctrl.Length() > 0 and ctrl.Tell() >= ctrl.Length() - 200:
                        ctrl.Seek(seek_ms)
                except Exception:
                    pass
                ctrl.SetVolume(volume)
                if not ctrl.Play() and self.use_reliable_audio and getattr(ctrl, "last_error", ""):
                    self.disable_reliable_audio_playback()
                    self.sync_background_audio_at(time_value, should_play, force, keep_preview_state)
                    return

    def sync_background_audio_at(self, time_value, should_play=True, force=False, keep_preview_state=False):
        if not should_play:
            self.pause_background_audio_playback()
            return
        mix = build_preview_audio_mix(
            getattr(self, "background_audio_items", []),
            getattr(self, "sound_effects_items", []),
            getattr(self, "muted_tracks", ()),
            time_value,
            getattr(self, "solo_tracks", ()),
            b_roll_items=getattr(self, "b_roll_items", []),
        )
        active_items = self._audio_channel_items(
            [channel for channel in mix["channels"] if channel["track"] == BACKGROUND_AUDIO_TRACK]
        )
        sfx_items = self._audio_channel_items(
            [channel for channel in mix["channels"] if channel["track"] == SOUND_EFFECTS_TRACK],
            sfx=True,
        )
        broll_items = self._audio_channel_items(
            [channel for channel in mix["channels"] if channel["track"] == SECONDARY_VIDEO_TRACK],
            broll=True,
        )
        active_keys = {key for key, _item in active_items}
        active_keys.update(key for key, _item in sfx_items)
        active_keys.update(key for key, _item in broll_items)
        for key in list(self.background_audio_players):
            if key not in active_keys:
                self.stop_background_audio_player(key)
        if not active_items and not sfx_items and not broll_items:
            if keep_preview_state:
                self.stop_all_background_audio_players()
            else:
                self.stop_background_audio_playback()
            return
        self._sync_active_audio_players(active_items, time_value, should_play, force, keep_preview_state, looping=True, track_gain=track_volume_gain(BACKGROUND_AUDIO_TRACK, getattr(self, "track_volumes_db", {}) or {}))
        self._sync_active_audio_players(sfx_items, time_value, should_play, force, keep_preview_state, looping=False, track_gain=track_volume_gain(SOUND_EFFECTS_TRACK, getattr(self, "track_volumes_db", {}) or {}))
        self._sync_active_audio_players(broll_items, time_value, should_play, force, keep_preview_state, looping=False, track_gain=track_volume_gain(SECONDARY_VIDEO_TRACK, getattr(self, "track_volumes_db", {}) or {}))

    def sync_background_audio_playback(self, play=None, force=False):
        should_play = self.playback_requested if play is None else bool(play)
        self.audio_effect_background_preview_state = None
        if self.audio_effect_background_preview_timer:
            try:
                self.audio_effect_background_preview_timer.Stop()
            except Exception:
                pass
            self.audio_effect_background_preview_timer = None
        self.sync_background_audio_at(self.current_time, should_play, force)

    def finish_pending_background_audio_load(self, key):
        state = self.background_audio_players.get(key)
        if not state or state.get("pending_seek_ms") is None:
            return
        ctrl = state["ctrl"]
        if ctrl.Length() <= 0:
            state["pending_checks"] = int(state.get("pending_checks", 0) or 0) + 1
            if state["pending_checks"] >= 25:
                self.stop_background_audio_player(key)
                return
            wx.CallLater(5, self.finish_pending_background_audio_load, key)
            return
        seek_ms = state["pending_seek_ms"]
        play = state["pending_play"]
        state["pending_seek_ms"] = None
        state["pending_play"] = False
        state["pending_checks"] = 0
        ctrl.SetVolume(max(0.0, float(state.get("volume", self.effective_output_volume()))))
        if hasattr(ctrl, "SetPlaybackRate"):
            try:
                ctrl.SetPlaybackRate(max(0.05, float(state.get("speed", 1.0) or 1.0)))
            except Exception:
                pass
        ctrl.Seek(seek_ms)
        if play:
            ctrl.Play()

    def OnBackgroundAudioLoaded(self, event):
        key = self.background_audio_player_key(event.GetEventObject())
        if key is not None:
            self.finish_pending_background_audio_load(key)
        event.Skip()

    def OnBackgroundAudioFinished(self, event):
        ctrl = event.GetEventObject()
        key = self.background_audio_player_key(ctrl)
        if key is None:
            event.Skip()
            return
        state = self.background_audio_players.get(key, {})
        if self.audio_effect_background_preview_state:
            preview_time = self.audio_effect_background_preview_time()
            if preview_time is None:
                self.stop_background_audio_player(key)
            else:
                self.sync_background_audio_at(preview_time, True, True, True)
            event.Skip()
            return
        if self.playback_requested and state.get("item"):
            self.sync_background_audio_playback(True, True)
        else:
            self.stop_background_audio_player(key)
        event.Skip()

    def load_timeline_time(self, time, play=False, seamless=False):
        """تحميل الصورة المطلوبة مع إبقاء الصوت البديل متصلًا عند الحواف.

        ``seamless`` لا يُستخدم إلا في الانتقال التلقائي بين مقطعين متجاورين.
        القفز اليدوي يظل يعيد ضبط الصوت والفيديو معًا بصورة صريحة.
        """
        if not self.timeline:
            if get_program_mode() == PROFESSIONAL_MODE:
                effective = max(0.0, *(float(it.get("end", 0) or 0)
                                       for it in (getattr(self, "background_audio_items", None) or [])))
                if effective <= 0:
                    return
                self.current_time = min(max(time, 0), effective)
                self.current_segment_index = None
                self.active_media_path = ""
                if play:
                    self.sync_background_audio_at(self.current_time, True)
                return
            return
        duration = self.timeline_duration()
        self.current_time = min(max(time, 0), duration)
        index, segment, segment_position = self.locate_timeline_segment(self.current_time)
        if segment is None:
            return
        segment_speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
        preview_path = self.audio_visual_preview_playback_path()
        seek_ms = media_seek_ms(
            timeline_time=self.current_time,
            segment_position=segment_position,
            segment_start=segment.start,
            segment_end=segment.end,
            segment_speed=segment_speed,
            preview_path=bool(preview_path),
        )
        self.current_segment_index = index
        media_path = preview_path or segment.path
        if self.active_media_path != media_path:
            player = self.original_audio_player
            preserve_continuous_audio = should_preserve_override_audio(
                seamless=bool(seamless),
                play=bool(play),
                has_override=bool(self.has_main_audio_override()),
                player_is_playing=bool(player and player.GetState() == MEDIASTATE_PLAYING),
                player_path=str(getattr(player, "path", "") or ""),
                override_path=str(self.main_audio_override_path or ""),
                player_tell_ms=float(player.Tell() if player else 0),
                target_time=self.current_time,
            )
            self.pending_seek_ms = seek_ms
            self.pending_play = bool(play)
            self.pending_media_load_checks = 0
            self.pending_seamless_media_switch = bool(seamless and play)
            self.pending_continuous_audio_preserved = preserve_continuous_audio
            self.active_media_path = media_path
            self.media_load_generation += 1
            generation = self.media_load_generation
            # لا نوقف الخلفيات ولا الصوت البديل عند عبور حافة مرئية متجاورة.
            if not self.pending_seamless_media_switch:
                self.stop_background_audio_playback()
            if not preserve_continuous_audio:
                self.stop_original_audio_playback()
            self.reset_media_control_cached_settings()
            self.media_ctrl.Load(media_path)
            self.set_media_control_volume(segment)
            wx.CallLater(5, self.finish_pending_media_load, generation)
            self.request_preview_rebuild()
            return
        if self.pending_seek_ms is not None:
            self.pending_seek_ms = seek_ms
            self.pending_play = bool(play)
            self.pending_seamless_media_switch = bool(self.pending_seamless_media_switch or (seamless and play))
            if not play:
                self.pending_continuous_audio_preserved = False
                self.pause_original_audio_playback()
            self.sync_background_audio_playback(play, not self.pending_seamless_media_switch)
            self.request_preview_rebuild()
            return
        self.media_ctrl.Seek(seek_ms)
        self.set_media_control_volume(segment)
        self.set_media_control_playback_rate(1.0 if preview_path else segment_speed)
        if play:
            self.playback_requested = True
            self.stop_scrub_playback()
            self.media_ctrl.Play()
            self.sync_original_audio_playback(True, not seamless)
        else:
            self.pause_original_audio_playback()
        if play or self.background_audio_players or self.audio_effect_background_preview_state:
            self.sync_background_audio_playback(play, not seamless)
        self.request_preview_rebuild()

    def OnMediaLoaded(self, event):
        wx.CallAfter(self.finish_pending_media_load, self.media_load_generation)
        event.Skip()

    def OnMediaFinished(self, event):
        # عند وجود صوت بديل متصل لا نجعل نهاية ملف الفيديو الجزئي هي التي
        # تحرك الزمن؛ مشغل الصوت الكامل هو الساعة الرئيسية ويتولى OnTimer
        # الانتقال عند العينة الصحيحة. هذا يمنع التقديم المبكر والتكرار.
        if self.has_main_audio_override() and self.original_audio_player:
            if self.original_audio_player.GetState() == MEDIASTATE_PLAYING:
                event.Skip()
                return
        self.advance_after_segment_end()
        event.Skip()

    def finish_pending_media_load(self, generation=None):
        if generation is not None and generation != self.media_load_generation:
            return
        if self.pending_seek_ms is None:
            return
        if self.media_ctrl.Length() <= 0:
            self.pending_media_load_checks += 1
            if self.use_reliable_audio and self.pending_media_load_checks >= 10:
                play = self.pending_play
                preserve_audio = self.pending_continuous_audio_preserved
                seamless = self.pending_seamless_media_switch
                self.pending_seek_ms = None
                self.pending_play = False
                self.pending_media_load_checks = 0
                self.pending_seamless_media_switch = False
                self.pending_continuous_audio_preserved = False
                if play:
                    self.playback_requested = True
                    if not preserve_audio:
                        self.sync_original_audio_playback(True, True)
                    self.sync_background_audio_playback(True, not seamless)
                return
            wx.CallLater(5, self.finish_pending_media_load, self.media_load_generation)
            return

        seek_ms = self.pending_seek_ms
        play = self.pending_play
        seamless = self.pending_seamless_media_switch
        preserve_audio = self.pending_continuous_audio_preserved

        # استغرق تحميل الملف المرئي وقتًا بينما ظل الصوت يعمل. نأخذ الزمن
        # الحالي من الصوت ونلحق الفيديو به بدل إعادة الصوت إلى موضع قديم.
        if play and preserve_audio and self.original_audio_player:
            audio_time = audio_clock_time(self.original_audio_player.Tell(), self.timeline_duration())
            index, segment, segment_position = self.locate_timeline_segment(audio_time)
            preview_path = self.audio_visual_preview_playback_path()
            expected_media_path = preview_path or (segment.path if segment is not None else "")
            if segment is not None and (index != self.current_segment_index or expected_media_path != self.active_media_path):
                self.pending_seek_ms = None
                self.pending_play = False
                self.pending_media_load_checks = 0
                self.pending_seamless_media_switch = False
                self.pending_continuous_audio_preserved = False
                self.current_time = audio_time
                self.load_timeline_time(audio_time, True, seamless=True)
                return
            if segment is not None:
                self.current_time = audio_time
                segment_speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
                seek_ms = media_seek_ms(
                    timeline_time=audio_time,
                    segment_position=segment_position,
                    segment_start=segment.start,
                    segment_end=segment.end,
                    segment_speed=segment_speed,
                    preview_path=bool(preview_path),
                )

        self.pending_seek_ms = None
        self.pending_play = False
        self.pending_media_load_checks = 0
        self.pending_seamless_media_switch = False
        self.pending_continuous_audio_preserved = False
        actual_seek_ms = self.media_ctrl.Seek(seek_ms)
        if not play and self.current_segment_index is not None and self.current_segment_index < len(self.timeline):
            segment = self.timeline[self.current_segment_index]
            segment_position = self.segment_position(self.current_segment_index)
            actual_time = self.timeline_time_from_media_ms(actual_seek_ms, segment, segment_position, self.audio_visual_preview_playback_path())
            if actual_time is not None and abs(actual_time - self.current_time) > 0.20:
                self.current_time = max(0.0, min(actual_time, self.timeline_duration()))
        self.set_media_control_volume()
        if self.current_segment_index is not None and self.current_segment_index < len(self.timeline):
            rate = 1.0 if self.active_media_is_audio_visual_preview() else max(0.05, float(getattr(self.timeline[self.current_segment_index], "speed", 1.0) or 1.0))
            self.set_media_control_playback_rate(rate)
        if play:
            self.playback_requested = True
            self.media_ctrl.Play()
            self.sync_original_audio_playback(True, not preserve_audio)
        else:
            self.pause_original_audio_playback()
            self.refresh_paused_video_frame(seek_ms)
        if play or self.background_audio_players or self.audio_effect_background_preview_state:
            self.sync_background_audio_playback(play, not seamless)

    def refresh_paused_video_frame(self, seek_ms=None):
        if self.media_kind != "video" and not self.active_media_is_audio_visual_preview():
            return
        try:
            if self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
                return
            length = int(self.media_ctrl.Length() or 0)
            if length <= 0:
                return
            target = int(self.media_ctrl.Tell() if seek_ms is None else seek_ms)
            target = max(0, min(target, max(0, length - 1)))
            # mpv يعرض الإطار الصحيح بعد بحث واحد مباشرة؛ لا حاجة لبحث
            # مزدوج (هدف + إزاحة) كان ضروريًا مع wxMediaCtrl فقط وكان يسبب
            # وميضًا وتقطيعًا عند التوقف.
            self.media_ctrl.Seek(target)
            self.media_ctrl.Refresh()
            self.media_ctrl.Update()
            self.main_panel.Refresh()
            self.main_panel.Update()
        except Exception:
            pass

    def advance_after_segment_end(self):
        if self.current_segment_index is None or not self.timeline:
            return
        segment = self.timeline[self.current_segment_index]
        next_time = self.segment_position(self.current_segment_index) + segment.duration
        if self.selected_playback_range:
            _selected_start, selected_end = self.selected_playback_range
            if next_time >= selected_end - PLAYBACK_EDGE_GUARD:
                self.current_time = selected_end
                self.playback_requested = False
                self.selected_playback_range = None
                self.media_ctrl.Pause()
                self.stop_original_audio_playback()
                self.stop_background_audio_playback()
                self.load_timeline_time(self.current_time, False)
                return
        if self.should_skip_playback_range(next_time):
            self.skip_playback_range()
            return
        if self.speed_preview_state and next_time >= float(self.speed_preview_state.get("preview_end", 0) or 0) - PLAYBACK_EDGE_GUARD:
            self.stop_speed_preview()
            return
        if next_time >= self.timeline_duration():
            self.playback_requested = False
            self.pending_play = False
            self.skipped_playback_range = None
            self.media_ctrl.Pause()
            self.stop_original_audio_playback()
            self.stop_background_audio_playback()
            return_position = getattr(self, "playback_return_position", None)
            self.playback_return_position = None
            if return_position is not None and return_position < self.timeline_duration() - PLAYBACK_EDGE_GUARD:
                self.current_time = return_position
                self.load_timeline_time(self.current_time, False)
                self.refresh_paused_video_frame()
            else:
                self.current_time = self.timeline_duration()
            return
        self.current_time = next_time
        self.load_timeline_time(next_time, self.playback_requested, seamless=True)

    def should_advance_current_segment(self, media_time, media_end, segment):
        if self.current_segment_index is None:
            return False
        guarded_end = max(segment.start, segment.end - PLAYBACK_EDGE_GUARD)
        if media_time < guarded_end:
            return False
        if self.current_segment_index < len(self.timeline) - 1:
            return True
        return media_end <= 0 or segment.end < media_end - 0.05

    def live_skip_deleted_gap(self, media_time):
        if (
            self.current_segment_index is None
            or not self.timeline
            or self.current_segment_index >= len(self.timeline) - 1
            or self.pending_seek_ms is not None
            or self.selected_playback_range
            or self.skipped_playback_range
            or self.speed_preview_state
            or self.active_media_is_audio_visual_preview()
            or self.has_main_audio_override()
        ):
            return False
        current_segment = self.timeline[self.current_segment_index]
        next_index = self.current_segment_index + 1
        next_segment = self.timeline[next_index]
        if not should_live_skip_deleted_gap(media_time, current_segment, next_segment):
            return False
        positions = self.timeline_metrics()[0]
        if next_index >= len(positions):
            return False
        next_time = positions[next_index]
        self.current_time = next_time
        self.current_segment_index = next_index
        seek_ms = media_seek_ms(
            timeline_time=next_time,
            segment_position=next_time,
            segment_start=next_segment.start,
            segment_end=next_segment.end,
            segment_speed=max(0.05, float(getattr(next_segment, "speed", 1.0) or 1.0)),
            preview_path=False,
        )
        self.media_ctrl.Seek(seek_ms, mode="exact")
        self.set_media_control_volume(next_segment)
        self.set_media_control_playback_rate(max(0.05, float(getattr(next_segment, "speed", 1.0) or 1.0)))
        if self.playback_requested and self.media_ctrl.GetState() != MEDIASTATE_PLAYING:
            self.media_ctrl.Play()
        if self.use_reliable_audio and self.original_audio_player:
            self.sync_original_audio_playback(True, True)
        if self.background_audio_players:
            self.sync_background_audio_playback(True, False)
        return True

    def reload_current_position(self):
        if not self.timeline:
            self.current_segment_index = None
            self.active_media_path = ""
            self.invalidate_pending_media_load()
            if get_program_mode() == PROFESSIONAL_MODE:
                bg_items = getattr(self, "background_audio_items", None) or []
                if bg_items:
                    self.current_time = min(self.current_time,
                                            max(0.0, *(float(it.get("end", 0) or 0) for it in bg_items)))
                    self.sync_background_audio_at(self.current_time, self.playback_requested)
                    return
            self.current_time = 0
            self.playback_requested = False
            try:
                self.media_ctrl.Stop()
            except Exception:
                pass
            self.stop_original_audio_playback()
            self.stop_background_audio_playback()
            return
        self.current_time = min(self.current_time, self.timeline_duration())
        self.load_timeline_time(self.current_time, self.playback_requested)

    def stop_playback_for_timeline_edit(self, action):
        was_playing = bool(getattr(self, "playback_requested", False))
        media_ctrl = getattr(self, "media_ctrl", None)
        if media_ctrl is not None:
            try:
                was_playing = was_playing or media_ctrl.GetState() == MEDIASTATE_PLAYING
            except Exception:
                pass
        original_audio_player = getattr(self, "original_audio_player", None)
        if original_audio_player is not None:
            try:
                was_playing = was_playing or original_audio_player.GetState() == MEDIASTATE_PLAYING
            except Exception:
                pass
        trace_event(
            "timeline_edit",
            f"{action}.stop_playback",
            window=getattr(self, "window_number", None),
            media_kind=getattr(self, "media_kind", ""),
            current_time=getattr(self, "current_time", 0.0),
            was_playing=was_playing,
        )
        self.playback_requested = False
        self.pending_play = False
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.playback_return_position = None
        try:
            self.stop_original_audio_playback(wait=True)
        except Exception:
            pass
        try:
            self.stop_background_audio_playback(wait=True)
        except Exception:
            pass
        if media_ctrl is not None:
            try:
                media_ctrl.Pause()
            except Exception:
                try:
                    media_ctrl.Stop()
                except Exception:
                    pass
        return was_playing

    def OnPlayPause(self, event=None):
        if get_program_mode() == NORMAL_MODE:
            self.OnPause(event, announce_pause=False)
            return
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if self.playback_requested or self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
            self.playback_requested = False
            self.pending_play = False
            # Cut custom audio first so the wx media control does not continue
            # under a separate decoded audio stream.
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()
            self.media_ctrl.Pause()
            self.selected_playback_range = None
            self.skipped_playback_range = None
            # المسافة تبدأ التشغيل من مكان المؤشر وعند الضغط عليها مرة أخرى
            # يعود المؤشر إلى الموضع الذي بدأ منه التشغيل كما في برامج المونتاج.
            return_position = getattr(self, "playback_return_position", None)
            self.playback_return_position = None
            if return_position is not None:
                self.load_timeline_time(return_position, False)
                self.refresh_paused_video_frame()
        else:
            self.selected_playback_range = None
            self.skipped_playback_range = None
            if self.current_time >= self.timeline_duration() - PLAYBACK_EDGE_GUARD:
                self.say(tr("المؤشر عند نهاية المحتوى، لا يوجد شيء للتشغيل"), wait_for_ui=False)
                return
            self.playback_return_position = self.current_time
            self.playback_requested = True
            self.load_timeline_time(self.current_time, True)

    def refresh_current_time_from_playback_clock(self):
        if not self.timeline or self.current_segment_index is None:
            return
        try:
            if (
                self.has_main_audio_override()
                and self.use_reliable_audio
                and self.original_audio_player
                and self.original_audio_player.GetState() == MEDIASTATE_PLAYING
                and self.original_audio_player.path == self.main_audio_override_path
            ):
                self.current_time = audio_clock_time(self.original_audio_player.Tell(), self.timeline_duration())
                return
            segment = self.timeline[self.current_segment_index]
            segment_position = self.segment_position(self.current_segment_index)
            actual_time = self.timeline_time_from_media_ms(
                self.media_ctrl.Tell(),
                segment,
                segment_position,
                self.audio_visual_preview_playback_path(),
            )
            if actual_time is not None:
                self.current_time = max(0.0, min(actual_time, self.timeline_duration()))
        except Exception:
            pass

    def OnPause(self, event=None, announce_pause=True):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if self.playback_requested or self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
            self.refresh_current_time_from_playback_clock()
            self.playback_return_position = None
            self.playback_requested = False
            self.pending_play = False
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()
            self.media_ctrl.Pause()
            self.selected_playback_range = None
            self.skipped_playback_range = None
            if announce_pause:
                self.say(speech_messages.PAUSED, wait_for_ui=False)
        else:
            self.selected_playback_range = None
            self.skipped_playback_range = None
            if self.current_time >= self.timeline_duration() - PLAYBACK_EDGE_GUARD:
                self.current_time = 0.0
                self.current_segment_index = None
                self.active_media_path = ""
                self.invalidate_pending_media_load()
            self.playback_requested = True
            self.load_timeline_time(self.current_time, True)

    def OnPlaySelectedRange(self, event=None):
        if not self.require_open_file():
            return
        selected = self.selected_effect_range()
        if not selected:
            self.say(tr("حدد بداية ونهاية المقطع أولا"), wait_for_ui=False)
            return
        start_time, end_time = selected
        self.selected_playback_range = (start_time, end_time)
        self.skipped_playback_range = None
        self.current_time = start_time
        self.playback_requested = True
        self.load_timeline_time(start_time, True)

    def OnPlayTimelineExceptSelection(self, event=None):
        if not self.require_open_file():
            return
        selected = self.selected_effect_range()
        if not selected:
            self.say(tr("حدد بداية ونهاية المقطع أولا"), wait_for_ui=False)
            return
        start_time, end_time = selected
        duration = self.timeline_duration()
        if start_time <= PLAYBACK_EDGE_GUARD and end_time >= duration - PLAYBACK_EDGE_GUARD:
            self.say(tr("لا يوجد جزء خارج التحديد"), wait_for_ui=False)
            return
        self.selected_playback_range = None
        self.skipped_playback_range = (start_time, end_time)
        self.current_time = end_time if start_time <= PLAYBACK_EDGE_GUARD else 0.0
        self.playback_requested = True
        self.load_timeline_time(self.current_time, True)
        self.say(tr("تشغيل الخط الزمني فيما عدا الجزء المحدد"), wait_for_ui=False)

    def should_skip_playback_range(self, time_value=None):
        if not self.skipped_playback_range:
            return False
        start_time, end_time = self.skipped_playback_range
        current = self.current_time if time_value is None else float(time_value)
        return start_time - PLAYBACK_EDGE_GUARD <= current < end_time - PLAYBACK_EDGE_GUARD

    def skip_playback_range(self):
        if not self.skipped_playback_range:
            return False
        start_time, end_time = self.skipped_playback_range
        duration = self.timeline_duration()
        if end_time >= duration - PLAYBACK_EDGE_GUARD:
            self.current_time = start_time
            self.playback_requested = False
            self.pending_play = False
            self.skipped_playback_range = None
            try:
                self.media_ctrl.Pause()
            except Exception:
                pass
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()
            self.load_timeline_time(self.current_time, False)
            self.say(speech_messages.PAUSED, wait_for_ui=False)
            return True
        self.current_time = end_time
        self.load_timeline_time(self.current_time, True)
        return True

    def current_seek_step_ms(self):
        if get_program_mode() == NORMAL_MODE:
            if not hasattr(self, "normal_seek_step"):
                self.normal_seek_step = read_normal_seek_step()
            return max(0, int(self.normal_seek_step))
        pixels_per_second = getattr(self, "pixels_per_second", None)
        if pixels_per_second is None:
            pixels_per_second = read_pixels_per_second()
        return max(MIN_SEEK_STEP, int(round(seek_seconds_for_pixels(SEEK_PIXELS, pixels_per_second) * 1000)))

    def OnForward(self, event=None):
        if not self.require_open_file():
            return
        self.seek_timeline_by(self.current_seek_step_ms() / 1000.0)

    def OnRewind(self, event=None):
        if not self.require_open_file():
            return
        self.seek_timeline_by(-self.current_seek_step_ms() / 1000.0)

    def schedule_volume_save(self):
        if self.volume_save_call:
            try:
                self.volume_save_call.Stop()
            except Exception:
                pass
        self.volume_save_call = wx.CallLater(250, self.persist_volume_setting)

    def persist_volume_setting(self):
        self.volume_save_call = None
        set_volume(self.volume)

    def _apply_program_volume_change(self, new_volume, debug_label):
        """تطبيق تغيير حقيقي فقط؛ يمنع تسرب الأسهم بين النطاق العادي والمضخم."""
        old_volume = self.volume
        if not volume_changed(old_volume, new_volume):
            return False
        self.volume = normalized_program_volume(new_volume)
        self.schedule_volume_save()
        self.set_media_control_volume()
        self.sync_original_audio_playback(self.playback_requested, False)
        self.sync_background_audio_playback(self.playback_requested, False)
        percent = volume_percent(self.volume)
        print(f"{debug_label}: {self.volume}")
        self.say(speech_messages.VOLUME.format(percent=percent), wait_for_ui=False)
        return True

    def OnIncreaseVolume(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_program_volume_change(normal_volume_up(self.volume), "Increased normal volume")

    def OnDecreaseVolume(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_program_volume_change(volume_down(self.volume), "Decreased normal volume")

    def OnIncreaseVolumeBoost(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_program_volume_change(boosted_volume_up(self.volume), "Boosted volume")

    def OnDecreaseVolumeBoost(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_program_volume_change(boosted_volume_down(self.volume), "Reduced boosted volume")

    def schedule_master_volume_save(self):
        if self.master_volume_save_call:
            try:
                self.master_volume_save_call.Stop()
            except Exception:
                pass
        self.master_volume_save_call = wx.CallLater(250, self.persist_master_volume_setting)

    def persist_master_volume_setting(self):
        self.master_volume_save_call = None
        set_master_volume_db(self.master_volume_db)

    def _apply_master_volume_change(self, new_db, debug_label):
        """تطبيق تغيير مستوى الماستر (dB) على كل الأصوات."""
        old_db = normalized_master_volume_db(self.master_volume_db)
        new_db = normalized_master_volume_db(new_db)
        if not master_volume_db_changed(old_db, new_db):
            return False
        self.master_volume_db = new_db
        self.schedule_master_volume_save()
        self.set_media_control_volume()
        self.sync_original_audio_playback(self.playback_requested, False)
        self.sync_background_audio_playback(self.playback_requested, False)
        print(f"{debug_label}: {self.master_volume_db} dB")
        self.say(speech_messages.MASTER_VOLUME.format(db=format_master_db(self.master_volume_db)), wait_for_ui=False)
        return True

    def OnIncreaseMasterVolume(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_master_volume_change(master_volume_up_db(self.master_volume_db), "Increased master volume")

    def OnDecreaseMasterVolume(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_master_volume_change(master_volume_down_db(self.master_volume_db), "Decreased master volume")

    def current_track_volume_db(self, track=None):
        """مستوى التراك الحالي بالديسيبل (الافتراضي 0dB)."""
        track = track or self.current_track
        return normalized_track_volume_db(self.track_volumes_db.get(track, 0.0))

    def _apply_current_track_volume_change(self, new_db, debug_label):
        """تطبيق تغيير مستوى التراك (dB) على صوت التراك الحالي وإعادة المزامنة."""
        track = self.current_track
        if not track_has_volume(track):
            return False
        old_db = self.current_track_volume_db(track)
        new_db = normalized_track_volume_db(new_db)
        if not track_volume_db_changed(old_db, new_db):
            return False
        before_state = self.capture_edit_state()
        self.track_volumes_db[track] = new_db
        self.is_dirty = True
        self.record_edit("تغيير مستوى التراك", before_state)
        # لا نعيد تحميل الوسائط هنا (apply_edit_state) لأن ذلك يسبب تعطلًا
        # أصليًا عند تعارض إعادة التحميل مع تحديث إطار الفيديو المتوقف.
        # التغيير صوتي فقط، فتكفي إعادة مزامنة المشغلات بنسبة الكسب الجديدة.
        self.set_media_control_volume()
        self.sync_original_audio_playback(self.playback_requested, False)
        self.sync_background_audio_playback(self.playback_requested, False)
        label = tr("التراك {number} {label}").format(number=track_index(track) + 1, label=tr(track_label(track)))
        db_value = self.current_track_volume_db(track)
        db_text = format_track_db(db_value)
        if db_value > 0:
            db_text = "+" + db_text
        print(f"{debug_label}: {label} = {db_value} dB")
        self.say(speech_messages.TRACK_VOLUME.format(db=db_text), wait_for_ui=False)
        return True

    def OnIncreaseTrackVolume(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_current_track_volume_change(track_volume_up_db(self.current_track_volume_db()), "Increased track volume")

    def OnDecreaseTrackVolume(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self._apply_current_track_volume_change(track_volume_down_db(self.current_track_volume_db()), "Decreased track volume")

    def OnCancelCurrentSelection(self, event=None):
        """Cancel only an active timeline selection; otherwise preserve Escape."""
        has_start = self.start_time is not None
        has_end = self.end_time is not None
        if not has_start and not has_end:
            return False

        selected_start = self.start_time
        selected_end = self.end_time
        duration = self.timeline_duration() if self.timeline else 0.0
        full_timeline = (
            has_start
            and has_end
            and abs(float(selected_start)) <= 0.03
            and abs(float(selected_end) - duration) <= 0.03
        )
        has_two_points = has_start and has_end and not full_timeline

        self.start_time = None
        self.end_time = None
        clear_clipboard_paste_marker_state(self)

        if has_two_points and self.timeline:
            target = max(0.0, min(float(selected_start), duration))
            self.playback_requested = False
            self.pending_play = False
            try:
                self.media_ctrl.Pause()
            except Exception:
                pass
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()
            self.current_time = target
            self.load_timeline_time(target, False)
            self.say(tr("تم إلغاء التحديد والانتقال إلى نقطة البداية"), wait_for_ui=False)
        elif full_timeline:
            self.say(tr("تم إلغاء تحديد كامل الخط الزمني"), wait_for_ui=False)
        else:
            self.say(tr("تم إلغاء التحديد"), wait_for_ui=False)
        return True

    def OnSetStart(self, event=None):
        if not self.require_open_file():
            return
        duration = self.timeline_duration()
        current_time = max(0.0, min(float(self.current_time), duration))
        if self.end_time is None:
            self.start_time = current_time
            self.end_time = duration
        elif self.start_time is None:
            self.start_time = 0.0
            self.end_time = current_time
        else:
            self.start_time = current_time
        note_clipboard_paste_start_marker(self)
        print(f"Start time set to: {self.start_time} seconds")
        self.say(speech_messages.START_MARK_SET, wait_for_ui=False)

    def OnSetEnd(self, event=None):
        if not self.require_open_file():
            return
        duration = self.timeline_duration()
        current_time = max(0.0, min(float(self.current_time), duration))
        if self.start_time is None:
            self.start_time = 0.0
        self.end_time = current_time
        if self.start_time is not None and self.end_time > self.start_time:
            self.playback_requested = False
            self.pending_play = False
            self.selected_playback_range = None
            self.skipped_playback_range = None
            try:
                self.media_ctrl.Pause()
            except Exception:
                pass
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()
        note_clipboard_paste_end_marker(self)
        print(f"End time set to: {self.end_time} seconds")
        self.say(speech_messages.END_MARK_SET, wait_for_ui=False)

    def OnSelectAllTimeline(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self.start_time = 0
        self.end_time = self.timeline_duration()
        note_clipboard_paste_full_selection(self)
        print(f"Selected full timeline from {self.start_time} to {self.end_time} seconds")
        self.say(f"{tr(speech_messages.FULL_TIMELINE_SELECTED)}. {tr('مدته {duration}').format(duration=self.spoken_time(self.end_time))}", wait_for_ui=False)

