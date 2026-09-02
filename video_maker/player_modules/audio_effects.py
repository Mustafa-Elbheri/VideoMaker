from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerAudioEffectMixin:
    def OnAudioDucking(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if not self.selected_effect_range():
            wx.MessageBox("حدد بداية ونهاية الجزء المطلوب أولا.", "تحديد مطلوب", wx.OK | wx.ICON_INFORMATION)
            return
        from video_maker.audio_ducking import AudioDuckingDialog

        dialog = AudioDuckingDialog(self)
        dialog.ShowModal()
        dialog.Destroy()

    def OnAudioEffect(self, effect_key):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        effects = {effect["key"]: effect for effect in get_audio_effect_definitions()}
        effect = effects.get(effect_key)
        if not effect:
            return
        if effect.get("special_action") == "remove_silence":
            self.OnRemoveSilence()
            return
        if effect.get("special_action") == "voice_over_ducking":
            self.OnAudioDucking()
            return
        if not self.selected_effect_range():
            wx.MessageBox("حدد بداية ونهاية الجزء المطلوب أولا.", "تحديد مطلوب", wx.OK | wx.ICON_INFORMATION)
            return
        dialog = AudioEffectDialog(self, effect)
        dialog.ShowModal()

    def audio_effect_preparation_timeline(self):
        """مصدر المعاينة مع احترام كتم المقاطع دون خبزه في ملف المؤثرات."""
        if self.media_kind == "video" and self.has_main_audio_override():
            position = 0.0
            result = []
            for segment in self.timeline:
                duration = max(0.0, float(segment.duration))
                if duration <= 0.0005:
                    continue
                result.append(TimelineSegment(
                    self.main_audio_override_path,
                    position,
                    position + duration,
                    1.0,
                    self.segment_audio_volume(segment),
                ))
                position += duration
            return result
        return list(self.timeline)

    def boundary_safe_audio_timeline_for_range(self, timeline, start_time, end_time):
        selected_segments = slice_segments(timeline, start_time, end_time)
        source_paths = []
        for segment in selected_segments:
            if str(getattr(segment, "audio_path", "") or ""):
                continue
            path = str(getattr(segment, "path", "") or "")
            if path and path not in source_paths:
                source_paths.append(path)
        proxy_paths = {}
        proxy_dirs = []
        for path in source_paths:
            try:
                proxy_path, proxy_dir = prepare_boundary_safe_audio_proxy(path)
            except Exception:
                proxy_path, proxy_dir = "", ""
            if proxy_path:
                proxy_paths[os.path.abspath(path)] = proxy_path
            if proxy_dir:
                proxy_dirs.append(proxy_dir)
        if not proxy_paths:
            return list(timeline), proxy_dirs
        updated = []
        for segment in timeline:
            path = str(getattr(segment, "path", "") or "")
            proxy_path = proxy_paths.get(os.path.abspath(path)) if path else ""
            if proxy_path and not str(getattr(segment, "audio_path", "") or ""):
                updated.append(TimelineSegment(
                    segment.path,
                    segment.start,
                    segment.end,
                    float(getattr(segment, "speed", 1.0) or 1.0),
                    float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
                    proxy_path,
                    float(getattr(segment, "start", 0.0) or 0.0),
                    str(getattr(segment, "navigation_group", "") or ""),
                    str(getattr(segment, "source_file_id", "") or ""),
                    str(getattr(segment, "source_file_name", "") or ""),
                    str(getattr(segment, "transition", "") or ""),
                    max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
                ))
            else:
                updated.append(segment)
        return updated, proxy_dirs

    def build_typing_text_audio_override(self, source_timeline, start_time, end_time, overlay_path, temp_dir, progress_callback=None, cancelled_callback=None):
        audio_timeline = list(source_timeline or [])
        proxy_dirs = []
        if self.media_kind == "video" and not self.has_main_audio_override():
            audio_timeline, proxy_dirs = self.boundary_safe_audio_timeline_for_range(audio_timeline, start_time, end_time)
        replacement_timeline = replace_audio_effect_range(audio_timeline, start_time, end_time, overlay_path)
        full_audio_path = os.path.join(temp_dir, f"typing_text_audio_{uuid.uuid4().hex}.wav")
        write_timeline_audio(
            replacement_timeline,
            full_audio_path,
            progress_callback,
            cancelled_callback,
        )
        fitted_path = os.path.join(temp_dir, f"typing_text_audio_fit_{uuid.uuid4().hex}.wav")
        fitted = self.audio_override_manager.fit_audio_to_duration(
            full_audio_path,
            timeline_export_duration(source_timeline),
            output_path=fitted_path,
            temp_dir=temp_dir,
            cancelled_callback=cancelled_callback,
        )
        return {
            "path": fitted.path,
            "duration": fitted.duration,
            "temp_dirs": proxy_dirs,
        }

    def create_selected_audio_effect_source(self):
        selected_range = self.selected_effect_range()
        if not selected_range:
            raise RuntimeError("حدد بداية ونهاية الجزء المطلوب أولا")
        start_time, end_time = selected_range
        temp_dir = tempfile.mkdtemp(prefix="audio_effect_preview_")
        selected_segments = slice_segments(self.audio_effect_preparation_timeline(), start_time, end_time)
        if self.media_kind == "audio" or self.has_main_audio_override() or (selected_segments and not has_video_stream(selected_segments[0].path)):
            selected_path = os.path.join(temp_dir, "selected.wav")
            write_timeline_audio(selected_segments, selected_path)
        else:
            selected_path = os.path.join(temp_dir, "selected.mp4")
            write_timeline_video(selected_segments, selected_path)
        return selected_path, temp_dir

    def create_realtime_audio_effect_preview_source(self):
        direct_source = self.create_direct_audio_effect_preview_source()
        if direct_source:
            return direct_source
        selected_range = self.selected_effect_range()
        if not selected_range:
            raise RuntimeError("حدد بداية ونهاية الجزء المطلوب أولا")
        start_time, end_time = selected_range
        selected_segments = slice_segments(self.audio_effect_preparation_timeline(), start_time, end_time)
        temp_dir = tempfile.mkdtemp(prefix="audio_effect_realtime_")
        selected_path = os.path.join(temp_dir, "selected.wav")
        write_timeline_audio(selected_segments, selected_path)
        return selected_path, 0, end_time - start_time, temp_dir

    def can_create_direct_audio_effect_preview_source(self):
        return self.direct_audio_effect_preview_segments() is not None

    def create_direct_audio_effect_preview_source(self):
        selected_segments = self.direct_audio_effect_preview_segments()
        if not selected_segments:
            return None
        if len(selected_segments) == 1:
            segment = selected_segments[0]
            preview_path = str(getattr(segment, "audio_path", "") or segment.path)
            return preview_path, segment_audio_start(segment), segment.duration, ""
        temp_dir = tempfile.mkdtemp(prefix="audio_effect_concat_")
        concat_path = os.path.join(temp_dir, "preview.ffconcat")
        with open(concat_path, "w", encoding="utf-8") as concat_file:
            concat_file.write("ffconcat version 1.0\n")
            for segment in selected_segments:
                source_path = str(getattr(segment, "audio_path", "") or segment.path)
                source_start = segment_audio_start(segment)
                source_end = source_start + segment.duration
                concat_file.write(f"file {self.ffconcat_path_literal(source_path)}\n")
                concat_file.write(f"inpoint {source_start:.6f}\n")
                concat_file.write(f"outpoint {source_end:.6f}\n")
        duration = sum(segment.duration for segment in selected_segments)
        return {"path": concat_path, "format": "concat"}, 0, duration, temp_dir

    @staticmethod
    def ffconcat_path_literal(path):
        normalized = os.path.abspath(str(path)).replace("\\", "/")
        escaped = normalized.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    def direct_audio_effect_preview_segments(self):
        selected_range = self.selected_effect_range()
        if not selected_range:
            raise RuntimeError("حدد بداية ونهاية الجزء المطلوب أولا")
        start_time, end_time = selected_range
        selected_segments = slice_segments(self.audio_effect_preparation_timeline(), start_time, end_time)
        if not selected_segments:
            return None
        direct_segments = []
        for segment in selected_segments:
            segment_speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
            if abs(segment_speed - 1.0) > 0.001:
                return None
            direct_segments.append(segment)
        return direct_segments

    def start_background_audio_live_preview(self):
        selected_range = self.selected_effect_range()
        if not selected_range:
            return 0
        start_time, end_time = selected_range
        if not hasattr(self, "background_audio_live_preview_state"):
            self.background_audio_live_preview_state = None
        if self.background_audio_live_preview_state is None:
            self.background_audio_live_preview_state = {
                "current_time": self.current_time,
                "playback_requested": self.playback_requested,
            }
        self.playback_requested = True
        self.load_timeline_time(start_time, True)
        return max(0, end_time - start_time)

    def pause_background_audio_live_preview(self):
        self.playback_requested = False
        if self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
            self.media_ctrl.Pause()
        self.stop_original_audio_playback()

    def seek_background_audio_live_preview(self, delta):
        selected_range = self.selected_effect_range()
        if not selected_range:
            return
        start_time, end_time = selected_range
        self.current_time = min(max(start_time, self.current_time + delta), end_time)
        self.load_timeline_time(self.current_time, self.playback_requested)

    def stop_background_audio_live_preview(self):
        if self.media_ctrl.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            self.media_ctrl.Pause()
        self.stop_original_audio_playback()
        state = getattr(self, "background_audio_live_preview_state", None)
        self.background_audio_live_preview_state = None
        if state:
            self.current_time = min(float(state["current_time"]), self.timeline_duration()) if self.timeline else 0
            self.playback_requested = bool(state["playback_requested"])
            self.load_timeline_time(self.current_time, self.playback_requested)

    def start_audio_effect_background_preview(self, offset=0):
        selected_range = self.selected_effect_range()
        if not selected_range:
            return
        start_time, end_time = selected_range
        duration = max(0, end_time - start_time)
        offset = max(0, min(duration, float(offset or 0)))
        if duration <= 0:
            return
        if self.audio_effect_background_preview_timer:
            try:
                self.audio_effect_background_preview_timer.Stop()
            except Exception:
                pass
        self.playback_requested = False
        self.audio_effect_background_preview_state = {
            "start": start_time,
            "end": end_time,
            "offset": offset,
            "started_at": time.monotonic(),
            "paused": False,
        }
        self.sync_background_audio_at(start_time + offset, True, True, True)
        remaining = max(0.05, duration - offset)
        self.audio_effect_background_preview_timer = wx.CallLater(int(remaining * 1000), self.stop_audio_effect_background_preview)

    def audio_effect_background_preview_time(self):
        state = self.audio_effect_background_preview_state
        if not state or state.get("paused"):
            return None
        start_time = float(state.get("start", 0) or 0)
        end_time = float(state.get("end", start_time) or start_time)
        offset = float(state.get("offset", 0) or 0)
        started_at = float(state.get("started_at", time.monotonic()) or time.monotonic())
        preview_time = start_time + offset + max(0.0, time.monotonic() - started_at)
        if preview_time >= end_time:
            self.stop_audio_effect_background_preview()
            return None
        return preview_time

    def sync_audio_effect_background_preview(self):
        preview_time = self.audio_effect_background_preview_time()
        if preview_time is not None:
            self.sync_background_audio_at(preview_time, True, False, True)

    def pause_audio_effect_background_preview(self):
        if self.audio_effect_background_preview_timer:
            try:
                self.audio_effect_background_preview_timer.Stop()
            except Exception:
                pass
            self.audio_effect_background_preview_timer = None
        if self.audio_effect_background_preview_state:
            self.audio_effect_background_preview_state["paused"] = True
        for state in self.background_audio_players.values():
            ctrl = state.get("ctrl")
            if ctrl and ctrl.GetState() == MEDIASTATE_PLAYING:
                ctrl.Pause()

    def stop_audio_effect_background_preview(self):
        self.stop_background_audio_playback()

    def seek_audio_effect_background_preview(self, offset):
        selected_range = self.selected_effect_range()
        if not selected_range:
            return
        start_time, end_time = selected_range
        duration = max(0, end_time - start_time)
        offset = max(0, min(duration, float(offset or 0)))
        if self.audio_effect_background_preview_state:
            self.start_audio_effect_background_preview(offset)

    def apply_audio_effect_to_selection(self, audio_filter):
        selected_range = self.selected_effect_range()
        if not selected_range:
            raise RuntimeError("حدد بداية ونهاية الجزء المطلوب أولا")
        start_time, end_time = selected_range
        if self.media_kind == "video":
            effect_result, temp_dir, start_time, end_time = self.prepare_main_audio_override_effect(audio_filter, start_time, end_time)
            self.commit_audio_effect_to_selection(effect_result, temp_dir, start_time, end_time)
            return
        effect_result, temp_dir = build_audio_effect_segment(self.timeline, start_time, end_time, audio_filter)
        before_state = self.capture_edit_state()
        try:
            self.validate_audio_effect_output(effect_result)
            self.remember_audio_effect_output(effect_result, temp_dir)
            self.timeline = replace_audio_effect_range(self.timeline, start_time, end_time, effect_result)
            self.add_edit_point("audio_effect", start_time, end_time, "timeline", restore_segments=slice_segments(before_state["timeline"], start_time, end_time), mode="replace")
            self.current_time = start_time
            self.start_time = None
            self.end_time = None
            self.is_dirty = True
            self.record_edit("تطبيق المؤثر الصوتي", before_state)
            self.reload_current_position()
            self.say(speech_messages.AUDIO_EFFECT_APPLIED)
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored("تطبيق المؤثر الصوتي", error, "audio_effect_apply")
            raise

    def prepare_audio_effect_to_selection(self, audio_filter, progress_callback=None, cancelled_callback=None):
        selected_range = self.selected_effect_range()
        if not selected_range:
            raise RuntimeError("حدد بداية ونهاية الجزء المطلوب أولا")
        start_time, end_time = selected_range
        if self.media_kind == "video":
            return self.prepare_main_audio_override_effect(audio_filter, start_time, end_time, progress_callback, cancelled_callback)
        timeline_snapshot = list(self.timeline)
        effect_result, temp_dir = build_audio_effect_segment_with_progress(timeline_snapshot, start_time, end_time, audio_filter, progress_callback, cancelled_callback)
        return effect_result, temp_dir, start_time, end_time

    def prepare_main_audio_override_effect(self, audio_filter, start_time, end_time, progress_callback=None, cancelled_callback=None):
        """تطبيق المؤثر على صوت الفيديو الكامل دون إنشاء فيديو جديد."""
        source_temp_dir = ""
        try:
            source = self.audio_override_manager.ensure_effect_source(
                (lambda value, message="": progress_callback(min(5, value * 0.05), message)) if progress_callback else None,
                cancelled_callback,
            )
            source_temp_dir = source.temp_dir
            override_timeline = [TimelineSegment(source.path, 0.0, source.duration)]

            def effect_progress(value, message):
                if progress_callback:
                    progress_callback(min(90, 5 + value * 0.85), message)

            effect_path, temp_dir = build_audio_effect_segment_with_progress(
                override_timeline,
                start_time,
                end_time,
                audio_filter,
                effect_progress,
                cancelled_callback,
            )
            full_audio_path = os.path.join(temp_dir, f"main_audio_override_effect_{uuid.uuid4().hex}.wav")
            replacement_timeline = replace_audio_effect_range(override_timeline, start_time, end_time, effect_path)

            def render_progress(percent):
                if progress_callback:
                    progress_callback(min(98, 90 + percent * 0.08), "جاري المعالجة")

            write_timeline_audio(replacement_timeline, full_audio_path, render_progress, cancelled_callback)
            fitted = self.audio_override_manager.fit_audio_to_duration(
                full_audio_path,
                self.timeline_duration(),
                temp_dir=temp_dir,
                progress_callback=(lambda value, message="": progress_callback(98 + value * 0.02, message)) if progress_callback else None,
                cancelled_callback=cancelled_callback,
            )
            full_audio_path = fitted.path
            self.pending_main_audio_override_effect_paths.add(os.path.abspath(full_audio_path))
            if progress_callback:
                progress_callback(100, "تم تطبيق المؤثر الصوتي")
            return full_audio_path, temp_dir, start_time, end_time
        finally:
            if source_temp_dir:
                shutil.rmtree(source_temp_dir, ignore_errors=True)

    def remember_audio_effect_output(self, effect_result, temp_dir):
        self.generated_temp_dirs.append(temp_dir)
        if isinstance(effect_result, (list, tuple)):
            for segment in effect_result:
                audio_path = str(getattr(segment, "audio_path", "") or "")
                if audio_path:
                    self.generated_temp_files.append(audio_path)
            return
        self.generated_temp_files.append(effect_result)

    def validate_audio_effect_output(self, effect_result):
        missing_paths = []
        if isinstance(effect_result, (list, tuple)):
            for segment in effect_result:
                audio_path = str(getattr(segment, "audio_path", "") or "")
                if audio_path and not os.path.exists(audio_path):
                    missing_paths.append(audio_path)
                video_path = str(getattr(segment, "path", "") or "")
                if video_path and not os.path.exists(video_path):
                    missing_paths.append(video_path)
        else:
            effect_path = str(effect_result or "")
            if not effect_path or not os.path.exists(effect_path):
                missing_paths.append(effect_path or "audio_effect_output")
        if missing_paths:
            raise RuntimeError("ملف المؤثر الصوتي الناتج غير موجود")

    def commit_audio_effect_to_selection(self, effect_path, temp_dir, start_time, end_time, announce=True, operation_name="تطبيق المؤثر الصوتي", effect_key="audio_effect", effect_parameters=None):
        before_state = self.capture_edit_state()
        try:
            self.validate_audio_effect_output(effect_path)
            self.remember_audio_effect_output(effect_path, temp_dir)
            if (
                not isinstance(effect_path, (list, tuple))
                and os.path.abspath(str(effect_path or "")) in self.pending_main_audio_override_effect_paths
            ):
                self.pending_main_audio_override_effect_paths.discard(os.path.abspath(str(effect_path or "")))
                self.main_audio_override_path = effect_path
                self.main_audio_override_duration = get_media_duration(effect_path)
                self.main_audio_override_timeline_duration = self.timeline_duration()
                self.current_time = start_time
                self.start_time = None
                self.end_time = None
                self.is_dirty = True
                self.audio_override_manager.register_effect(
                    effect_key,
                    operation_name,
                    start_time,
                    end_time,
                    effect_parameters,
                )
                self.record_edit(operation_name, before_state, audio_policy="already_updated")
                self.reload_current_position()
                if announce:
                    self.say(speech_messages.AUDIO_EFFECT_APPLIED)
                return
            self.timeline = replace_audio_effect_range(self.timeline, start_time, end_time, effect_path)
            self.add_edit_point("audio_effect", start_time, end_time, "timeline", restore_segments=slice_segments(before_state["timeline"], start_time, end_time), mode="replace")
            self.current_time = start_time
            self.start_time = None
            self.end_time = None
            self.is_dirty = True
            self.record_edit(operation_name, before_state)
            self.reload_current_position()
            if announce:
                self.say(speech_messages.AUDIO_EFFECT_APPLIED)
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored("تطبيق المؤثر الصوتي", error, "audio_effect_commit")
            raise

    def InsertVisualEffect(self, effect_path, description):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        duration = get_video_duration(effect_path)
        insert_time = min(max(self.current_time, 0), self.timeline_duration())
        before_state = self.capture_edit_state()
        previous_timeline = list(self.timeline)
        previous_duration = total_duration(previous_timeline)
        created_proxy_path = ""
        created_proxy_dir = ""

        # A visual effect inserted inside a compressed source creates two new
        # decoder boundaries.  AAC/MP3 priming at those boundaries can soften
        # the last word before the effect and the first word after it.  Prepare
        # one lossless audio proxy for that source and keep the visual timeline
        # exactly as it was.  Preview and export then seek the same sample-accurate
        # audio without changing the effect's duration, position, or own sound.
        source_index, source_segment, source_position = locate_segment(previous_timeline, insert_time)
        if source_segment is not None:
            segment_end_position = source_position + source_segment.duration
            split_inside_segment = source_position + 0.001 < insert_time < segment_end_position - 0.001
            if split_inside_segment and not getattr(source_segment, "audio_path", ""):
                try:
                    created_proxy_path, created_proxy_dir = prepare_boundary_safe_audio_proxy(source_segment.path)
                except Exception as error:
                    self.say("تعذر إدراج المؤثر المرئي")
                    show_error(
                        f"{tr('تعذر تجهيز الصوت المحيط بالمؤثر المرئي')}: {error}",
                        tr("خطأ"),
                        self,
                        exception=error,
                        context="visual_effect_audio_boundary",
                    )
                    return
                if created_proxy_path:
                    previous_timeline[source_index] = TimelineSegment(
                        source_segment.path,
                        source_segment.start,
                        source_segment.end,
                        float(getattr(source_segment, "speed", 1.0) or 1.0),
                        float(getattr(source_segment, "audio_volume", 1.0) if getattr(source_segment, "audio_volume", 1.0) is not None else 1.0),
                        created_proxy_path,
                        getattr(source_segment, "audio_start", None),
                        str(getattr(source_segment, "navigation_group", "") or ""),
                        segment_file_id(source_segment),
                        segment_file_name(source_segment),
                    )

        before_segments = slice_segments(previous_timeline, 0, insert_time)
        after_segments = slice_segments(previous_timeline, insert_time, previous_duration)
        current_file, _files = logical_file_at_time(previous_timeline, insert_time)
        effect_segment = TimelineSegment(
            effect_path, 0, duration,
            source_file_id=current_file.file_id if current_file else new_logical_file_id(),
            source_file_name=current_file.name if current_file else display_file_name(self.video_path or effect_path),
        )
        self.timeline = before_segments + [effect_segment] + after_segments
        if total_duration(self.timeline) < previous_duration:
            self.timeline = list(before_state["timeline"])
            if created_proxy_dir:
                shutil.rmtree(created_proxy_dir, ignore_errors=True)
            self.notify_failed_edit_restored("إدراج المؤثر المرئي", context="visual_effect_insert_duration_guard")
            show_error(
                f"{tr('تعذر إدراج المؤثر المرئي بدون الحفاظ على الفيديو الأصلي.')} {tr('لم يثبت التعديل وتمت استعادة مساحة العمل كما كانت')}",
                tr("خطأ"),
                self,
                context="visual_effect_insert",
            )
            return
        if created_proxy_dir:
            self.generated_temp_dirs.append(created_proxy_dir)
            self.generated_temp_files.append(created_proxy_path)
        self.shift_timed_items_after_insert(insert_time, duration)
        self.add_edit_point("visual_effect", insert_time, insert_time + duration, "timeline", mode="insert")
        self.current_time = insert_time
        self.is_dirty = True
        self.record_edit("إدراج المؤثر المرئي", before_state)
        self.reload_current_position()
        print(f"Inserted visual effect: {effect_path} at {insert_time} seconds")
        self.say(speech_messages.VISUAL_EFFECT_INSERTED)

    def StartVideoClipMerge(self, video_paths):
        timeline = []
        for video_path in video_paths:
            duration = get_video_duration(video_path)
            timeline.append(new_file_segment(video_path, 0, duration))
        self.stop_background_audio_playback()
        self.clear_audio_visual_preview()
        self.video_path = video_paths[0]
        self.media_kind = "video"
        self.chroma_render_state = None
        self.visual_items = []
        self.background_audio_items = []
        self.b_roll_items = []
        self.sound_effects_items = []
        self.reset_main_audio_override_state()
        self.timeline = timeline
        self.edit_points = []
        self.current_edit_point_id = None
        self.work_images = []
        self.work_videos = []
        self.last_insert_end = None
        self.current_time = 0
        self.current_segment_index = None
        self.active_media_path = ""
        self.invalidate_pending_media_load()
        self.playback_requested = True
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.start_time = None
        self.end_time = None
        self.clipboard = []
        self.file_metadata = {}
        self.is_dirty = True
        self.edit_history.clear()
        self.refresh_menu_bar()
        self.load_timeline_time(0, True)
        remember_recent_files(video_paths)
        self.say(speech_messages.VIDEO_CLIPS_MERGED)

    def StartAudioClipMerge(self, audio_paths):
        timeline = []
        for audio_path in audio_paths:
            duration = get_media_duration(audio_path)
            timeline.append(new_file_segment(audio_path, 0, duration))
        self.stop_background_audio_playback()
        self.video_path = audio_paths[0]
        self.media_kind = "audio"
        self.chroma_render_state = None
        self.visual_items = []
        self.background_audio_items = []
        self.b_roll_items = []
        self.sound_effects_items = []
        self.reset_main_audio_override_state()
        self.edit_points = []
        self.current_edit_point_id = None
        self.work_images = []
        self.work_videos = []
        self.last_insert_end = None
        self.timeline = timeline
        self.current_time = 0
        self.current_segment_index = None
        self.active_media_path = ""
        self.invalidate_pending_media_load()
        self.playback_requested = True
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.start_time = None
        self.end_time = None
        self.clipboard = []
        self.file_metadata = {}
        self.is_dirty = True
        self.edit_history.clear()
        self.refresh_menu_bar()
        self.load_timeline_time(0, True)
        remember_recent_files(audio_paths)
        self.say(tr("تم دمج الملفات الصوتية"))

    def StartAudioImageMerge(self, options):
        temp_dir = tempfile.mkdtemp(prefix="audio_image_merge_")
        output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4", dir=temp_dir).name
        self.merge_cancelled = False
        self.merge_progress_dialog = MergeProgressDialog(self, self.cancel_merge)
        self.merge_progress_dialog.Show()
        self.say(speech_messages.MERGE_STARTED)
        threading.Thread(target=self.MergeAudioWithImages, args=(options, output_file, temp_dir)).start()

    def cancel_merge(self):
        self.merge_cancelled = True

    def MergeAudioWithImages(self, options, output_file, temp_dir):
        try:
            completed = create_audio_image_video(
                options,
                output_file,
                temp_dir,
                lambda progress: wx.CallAfter(self.UpdateMergeProgress, progress),
                lambda: self.merge_cancelled,
            )
            if self.merge_cancelled or not completed:
                wx.CallAfter(self.OnMergeCancelled, temp_dir)
                return
            wx.CallAfter(self.OnMergeComplete, output_file, temp_dir)
        except Exception as error:
            if is_operation_cancelled(error, lambda: self.merge_cancelled):
                wx.CallAfter(self.OnMergeCancelled, temp_dir)
            else:
                wx.CallAfter(self.OnMergeError, str(error), temp_dir)

    def StartAudioVideoMerge(self, options):
        temp_dir = tempfile.mkdtemp(prefix="audio_video_merge_")
        output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4", dir=temp_dir).name
        self.merge_cancelled = False
        self.merge_progress_dialog = AudioVideoMergeProgressDialog(self, self.cancel_merge)
        self.merge_progress_dialog.Show()
        self.say(tr("بدأ دمج الصوت مع الفيديو"))
        threading.Thread(target=self.MergeAudioWithVideo, args=(options, output_file, temp_dir)).start()

    def MergeAudioWithVideo(self, options, output_file, temp_dir):
        try:
            completed = create_audio_video_merge(
                options,
                output_file,
                temp_dir,
                lambda progress: wx.CallAfter(self.UpdateMergeProgress, progress),
                lambda: self.merge_cancelled,
            )
            if self.merge_cancelled or not completed:
                wx.CallAfter(self.OnMergeCancelled, temp_dir)
                return
            wx.CallAfter(self.OnAudioVideoMergeComplete, output_file, temp_dir)
        except Exception as error:
            if is_operation_cancelled(error, lambda: self.merge_cancelled):
                wx.CallAfter(self.OnMergeCancelled, temp_dir)
            else:
                wx.CallAfter(self.OnAudioVideoMergeError, str(error), temp_dir)

    def UpdateMergeProgress(self, progress):
        if self.merge_progress_dialog:
            self.merge_progress_dialog.update_progress(progress)

    def DestroyMergeProgressDialog(self):
        if self.merge_progress_dialog:
            self.merge_progress_dialog.Destroy()
            self.merge_progress_dialog = None

    def OnMergeComplete(self, output_file, temp_dir):
        self.DestroyMergeProgressDialog()
        self.OnOpenGeneratedVideo(output_file, temp_dir)
        # self.say(speech_messages.MERGE_DONE)
        wx.MessageBox(tr("تم دمج الصوت مع الصور وفتح الناتج داخل المشغل. استخدم حفظ الفيديو من قائمة ملف لحفظه نهائيًا."), tr("تم الدمج"), wx.OK | wx.ICON_INFORMATION)

    def OnAudioVideoMergeComplete(self, output_file, temp_dir):
        self.DestroyMergeProgressDialog()
        self.OnOpenGeneratedVideo(output_file, temp_dir)
        # self.say(tr("تم دمج الصوت مع الفيديو وفتح الناتج"))
        wx.MessageBox(tr("تم دمج الصوت مع الفيديو وفتح الناتج داخل المشغل. استخدم حفظ الفيديو من قائمة ملف لحفظه نهائيا."), tr("تم الدمج"), wx.OK | wx.ICON_INFORMATION)

    def OnMergeCancelled(self, temp_dir):
        self.DestroyMergeProgressDialog()
        self.cleanup_temp_dir(temp_dir)
        self.say(speech_messages.MERGE_CANCELLED)

    def OnMergeError(self, error_message, temp_dir):
        self.DestroyMergeProgressDialog()
        self.cleanup_temp_dir(temp_dir)
        # self.say(speech_messages.MERGE_FAILED)
        wx.MessageBox(tr_format("تعذر دمج الصوت مع الصور: {error}", error=error_message), tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnAudioVideoMergeError(self, error_message, temp_dir):
        self.DestroyMergeProgressDialog()
        self.cleanup_temp_dir(temp_dir)
        # self.say(tr("تعذر دمج الصوت مع الفيديو"))
        wx.MessageBox(tr_format("تعذر دمج الصوت مع الفيديو: {error}", error=error_message), tr("خطأ"), wx.OK | wx.ICON_ERROR)

