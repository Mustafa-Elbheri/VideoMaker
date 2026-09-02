from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *
from video_maker.timeline_audio_insert import choose_timeline_audio_path, inserted_audio_timeline
from video_maker.timeline_silence_insert import choose_silence_duration, create_silence_audio_file, inserted_silence_timeline


@publish_player_methods
class PlayerMediaInsertMixin:
    def OnRepeatSelection(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        selected = self.selected_effect_range()
        if not selected:
            # self.say("حدد بداية ونهاية المقطع أولا")
            wx.MessageBox("حدد بداية ونهاية المقطع المطلوب تكراره أولا.", "تحديد مطلوب", wx.OK | wx.ICON_INFORMATION)
            return
        dialog = wx.TextEntryDialog(self, "اكتب عدد التكرارات", "تكرار المقطع المحدد", "2")
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        value = dialog.GetValue().strip()
        dialog.Destroy()
        try:
            count = int(value)
        except ValueError:
            wx.MessageBox("اكتب رقما صحيحا أكبر من صفر.", "قيمة غير صحيحة", wx.OK | wx.ICON_ERROR)
            return
        if count <= 0:
            wx.MessageBox("اكتب رقما صحيحا أكبر من صفر.", "قيمة غير صحيحة", wx.OK | wx.ICON_ERROR)
            return
        before_state = self.capture_edit_state()
        start_time, end_time = selected
        selected_segments = slice_segments(self.timeline, start_time, end_time)
        repeated = []
        for _ in range(count):
            repeated.extend(selected_segments)
        remaining = delete_range(self.timeline, start_time, end_time)
        self.timeline = insert_segments(remaining, start_time, repeated)
        self.repeat_timed_items_for_selection(start_time, end_time, count)
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("تكرار المقطع", before_state)
        self.reload_current_position()
        self.say("تم تكرار المقطع المحدد")

    def OnMergeAudioWithImages(self, event=None):
        merge_window = AudioImageMergeDialog(self, self.StartAudioImageMerge)
        merge_window.Show()

    def OnMergeAudioWithVideo(self, event=None):
        merge_window = AudioVideoMergeDialog(self, self.StartAudioVideoMerge)
        merge_window.Show()

    def OnMergeAudioFiles(self, event=None):
        merge_window = AudioClipMergeWindow(self, self.StartAudioClipMerge)
        merge_window.Show()

    def OnMergeVideoClips(self, event=None):
        merge_window = VideoClipMergeWindow(self, self.StartVideoClipMerge)
        merge_window.Show()

    def OnVisualEffects(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        dialog = VisualEffectsDialog(self, self.InsertVisualEffect, self.InsertVisualEffect)
        dialog.ShowModal()

    def OnInsertBackgroundAudio(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if not self.track_accepts_media("audio"):
            return
        if get_program_mode() == PROFESSIONAL_MODE and self.current_track == SOUND_EFFECTS_TRACK:
            self.insert_sound_effect()
            return
        self.insert_background_audio()

    def OnInsertTimelineAudio(self, event=None):
        if not self.require_open_file():
            return
        audio_path = choose_timeline_audio_path(self)
        if not audio_path:
            return
        if not os.path.exists(audio_path):
            self.say(tr("ملف الصوت غير موجود"))
            return
        try:
            if not has_audio_stream(audio_path):
                self.say(tr("الملف المختار لا يحتوي على صوت."))
                return
        except Exception:
            self.say(tr("تعذر قراءة ملف الصوت"))
            return
        try:
            duration = get_media_duration(audio_path)
        except Exception:
            self.say(tr("تعذر قراءة مدة ملف الصوت"))
            return
        before_state = self.capture_edit_state()
        insert_time = max(0.0, float(getattr(self, "current_time", 0.0) or 0.0))
        self.timeline = inserted_audio_timeline(self.timeline, audio_path, insert_time, duration)
        self.shift_timed_items_after_insert(insert_time, duration)
        self.current_time = insert_time
        self.add_edit_point("audio", insert_time, insert_time + duration, "timeline", mode="insert")
        self.is_dirty = True
        self.record_edit("إدراج صوت", before_state)
        self.reload_current_position()
        self.say(tr("تم إدراج الصوت"))

    def OnInsertTimelineSilence(self, event=None):
        if not self.require_open_file():
            return
        duration = choose_silence_duration(self)
        if duration is None:
            return
        try:
            silence_path = create_silence_audio_file(duration)
        except Exception:
            self.say(tr("تعذر إنشاء الصمت"))
            return
        before_state = self.capture_edit_state()
        insert_time = max(0.0, float(getattr(self, "current_time", 0.0) or 0.0))
        self.timeline = inserted_silence_timeline(self.timeline, silence_path, insert_time, duration)
        self.shift_timed_items_after_insert(insert_time, duration)
        self.current_time = insert_time
        self.add_edit_point("silence", insert_time, insert_time + duration, "timeline", mode="insert")
        self.is_dirty = True
        self.record_edit(tr("إدراج صمت"), before_state)
        self.reload_current_position()
        self.say(tr("تم إدراج الصمت"))

    def insert_background_audio(self):
        options = self._prompt_audio_insert(tr("إدراج خلفية صوتية"))
        if options is None:
            return
        self.InsertBackgroundAudio(options)

    def insert_sound_effect(self):
        options = self._prompt_audio_insert(tr("إدراج مؤثر صوتي"))
        if options is None:
            return
        self.InsertSoundEffect(options)

    def _prompt_audio_insert(self, title):
        selected = self.selected_effect_range()
        if not selected:
            wx.MessageBox(tr("حدد بداية ونهاية الجزء المطلوب أولا."), tr("تحديد مطلوب"), wx.OK | wx.ICON_INFORMATION)
            return None
        dialog = BackgroundAudioDialog(self, title=title)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return None
        options = dialog.selection_options
        dialog.Destroy()
        if not options:
            return None
        return options

    def InsertBackgroundAudio(self, options):
        self._insert_audio_item(
            self.background_audio_items,
            "background_audio",
            options,
            tr("تعذر قص الصمت من الخلفية الصوتية"),
            "إدراج خلفية صوتية",
            "تم إدراج الخلفية الصوتية",
        )

    def InsertSoundEffect(self, options):
        self._insert_audio_item(
            self.sound_effects_items,
            "sound_effect",
            options,
            tr("تعذر قص الصمت من المؤثر الصوتي"),
            "إدراج مؤثر صوتي",
            "تم إدراج المؤثر الصوتي",
        )

    def _insert_audio_item(self, storage, item_type, options, trim_error_message, operation_label, done_message):
        start_time, end_time = self.selected_effect_range()
        path = options["path"]
        temp_dir = ""
        if options.get("trim_silence"):
            try:
                path, temp_dir = trim_background_audio_silence(path)
            except Exception as error:
                wx.MessageBox(f"{trim_error_message}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
                return
        before_state = self.capture_edit_state()
        if temp_dir:
            self.generated_temp_dirs.append(temp_dir)
            self.generated_temp_files.append(path)
        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "type": item_type,
            "path": path,
            "original_path": options["path"],
            "name": options.get("name") or os.path.splitext(os.path.basename(options["path"]))[0],
            "start": start_time,
            "end": end_time,
            "volume": options.get("volume", 0.4),
            "trim_silence": bool(options.get("trim_silence")),
            "speed": 1.0,
            "source_offset": 0.0,
        }
        storage.append(item)
        self.add_edit_point(item_type, start_time, end_time, item_type, item_id=item_id)
        self.last_insert_end = end_time
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(operation_label, before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(done_message)

    def selected_effect_range(self):
        duration = self.timeline_duration() if self.timeline else 0.0
        if self.start_time is not None and self.end_time is None:
            start = max(0.0, min(float(self.start_time), duration))
            return (start, duration) if start < duration else None
        if self.end_time is not None and self.start_time is None:
            end = max(0.0, min(float(self.end_time), duration))
            return (0.0, end) if end > 0 else None
        if self.start_time is None or self.end_time is None or self.start_time >= self.end_time:
            return None
        start = max(0.0, min(float(self.start_time), duration))
        end = max(0.0, min(float(self.end_time), duration))
        return (start, end) if start < end else None

    def selected_transform_range(self):
        selected = self.selected_effect_range()
        if selected:
            return selected
        self.say(tr("حدد كامل الخط الزمني أو بداية ونهاية أولا"))
        return None

    def OnChangeSpeed(self, event=None):
        if not self.require_open_file():
            return
        selected = self.selected_transform_range()
        if not selected:
            return
        dialog = SpeedDialog(self)
        if dialog.ShowModal() != wx.ID_OK:
            self.stop_speed_preview()
            dialog.Destroy()
            return
        speed = dialog.speed()
        self._speed_step_index = dialog.choice.GetSelection()
        self.stop_speed_preview()
        dialog.Destroy()
        if abs(speed - 1.0) < 0.001:
            self.say(tr("سرعة التشغيل الافتراضية واحد اكس"))
            return
        start_time, end_time = selected
        before_state = self.capture_edit_state()
        restore_segments = slice_segments(before_state["timeline"], start_time, end_time)
        old_duration = max(0.0, end_time - start_time)
        self.timeline, new_duration = speed_timeline_range(self.timeline, start_time, end_time, speed)
        self.speed_timed_items_for_range(start_time, end_time, speed, new_duration)
        self.edit_points = adjust_points_after_delete(self.edit_points, start_time, end_time)
        self.edit_points = adjust_points_after_insert(self.edit_points, start_time, new_duration)
        self.add_edit_point("speed", start_time, start_time + new_duration, "timeline", restore_segments=restore_segments, mode="replace", label=tr("تسريع وإبطاء"))
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(tr("تسريع وإبطاء"), before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        if old_duration > 0:
            self.say(tr("تم تطبيق تغيير السرعة {speed}").format(speed=f"{speed:g}x"))

    def _get_speed_step_index(self):
        """إرجاع مؤشر السرعة الحالية أو الافتراضية."""
        return getattr(self, "_speed_step_index", next(
            (i for i, (_l, v) in enumerate(SPEED_CHOICES) if abs(v - 1.0) < 0.001), 0
        ))

    def _apply_speed_step(self, new_index, announce_text=None):
        """تطبيق السرعة الجديدة على التحديد الحالي أو كامل الملف وحفظ نقطة التعديل."""
        if not self.require_open_file():
            return
        # إذا كان هناك تحديد استخدمه، وإلا طبّق على كامل الملف
        selected = self.selected_effect_range()
        if selected is None:
            duration = self.timeline_duration() if self.timeline else 0.0
            if duration <= 0:
                return
            selected = (0.0, duration)
        label, speed = SPEED_CHOICES[new_index]
        self._speed_step_index = new_index
        if abs(speed - 1.0) < 0.001:
            self.say(tr("إعادة السرعة للوضع الافتراضي"))
            return
        start_time, end_time = selected
        before_state = self.capture_edit_state()
        restore_segments = slice_segments(before_state["timeline"], start_time, end_time)
        old_duration = max(0.0, end_time - start_time)
        self.timeline, new_duration = speed_timeline_range(self.timeline, start_time, end_time, speed)
        self.speed_timed_items_for_range(start_time, end_time, speed, new_duration)
        self.edit_points = adjust_points_after_delete(self.edit_points, start_time, end_time)
        self.edit_points = adjust_points_after_insert(self.edit_points, start_time, new_duration)
        self.add_edit_point("speed", start_time, start_time + new_duration, "timeline", restore_segments=restore_segments, mode="replace", label=tr("تسريع وإبطاء"))
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(tr("تسريع وإبطاء"), before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        if old_duration > 0 and announce_text:
            self.say(announce_text.format(speed=label))

    def OnSpeedUpStep(self, event=None):
        """تسريع خطوة واحدة باستخدام Alt+سهم يمين من النافذة الرئيسية."""
        current = self._get_speed_step_index()
        if current >= len(SPEED_CHOICES) - 1:
            return
        self._apply_speed_step(current + 1, tr("تسريع خطوة واحدة سرعة {speed}"))

    def OnSpeedDownStep(self, event=None):
        """تبطيئ خطوة واحدة باستخدام Alt+سهم يسار من النافذة الرئيسية."""
        current = self._get_speed_step_index()
        if current <= 0:
            return
        self._apply_speed_step(current - 1, tr("تبطيئ خطوة واحدة سرعة {speed}"))

    def OnSpeedReset(self, event=None):
        """إعادة السرعة للوضع الافتراضي 1x باستخدام Alt+0."""
        default_index = next(
            (i for i, (_l, v) in enumerate(SPEED_CHOICES) if abs(v - 1.0) < 0.001), 0
        )
        self._apply_speed_step(default_index)


    def OnMuteOriginalAudio(self, event=None):
        if not self.require_open_file():
            return
        selected = self.selected_transform_range()
        if not selected:
            return
        start_time, end_time = selected
        before_state = self.capture_edit_state()
        restore_segments = slice_segments(before_state["timeline"], start_time, end_time)
        self.timeline = mute_original_audio_range(self.timeline, start_time, end_time)
        self.add_edit_point(
            "mute_original_audio",
            start_time,
            end_time,
            "timeline",
            restore_segments=restore_segments,
            mode="replace",
            label=tr("كتم الجزء المحدد"),
        )
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(tr("كتم الجزء المحدد"), before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(tr("تم كتم الجزء المحدد"))

    def timeline_video_mute_ranges(self):
        duration = self.timeline_duration()
        if duration <= 0:
            return []
        return [(0.0, duration)]

    def OnMuteTimelineVideos(self, event=None):
        if not self.require_open_file():
            return
        ranges = self.timeline_video_mute_ranges()
        if not ranges:
            self.say(speech_messages.NO_OPEN_FILE)
            return
        before_state = self.capture_edit_state()
        self.timeline, changed = mute_timeline_audio_ranges(self.timeline, ranges)
        if not changed:
            self.say(tr("صوت الخط الزمني مكتوم بالفعل"))
            return
        start_time = min(start for start, _end in ranges)
        for range_start, range_end in ranges:
            self.add_edit_point(
                "mute_timeline_audio",
                range_start,
                range_end,
                "timeline",
                restore_segments=slice_segments(before_state["timeline"], range_start, range_end),
                mode="replace",
                label=tr("كتم صوت الخط الزمني كامل"),
            )
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(tr("كتم صوت الخط الزمني كامل"), before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(tr("تم كتم صوت الخط الزمني كامل"))

    def OnRotateVideo(self, event=None):
        if not self.require_open_file():
            return
        if not has_video_stream(self.timeline[0].path):
            self.say(tr("تدوير الفيديو يحتاج إلى ملف فيديو"))
            return
        selected = self.selected_transform_range()
        if not selected:
            return
        dialog = VideoRotationDialog(self)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        rotation_key = dialog.rotation_key()
        dialog.Destroy()
        self.start_timeline_transform(
            "rotate_video",
            tr("جاري تدوير الفيديو"),
            tr("نسبة تدوير الفيديو {percent} بالمئة"),
            tr("حالة تدوير الفيديو"),
            tr("شريط تقدم تدوير الفيديو"),
            tr("إلغاء تدوير الفيديو"),
            tr("جاري إلغاء تدوير الفيديو"),
            lambda progress, cancelled: build_rotated_video_segment(self.timeline, selected[0], selected[1], rotation_key, progress, cancelled),
            selected,
            tr("تدوير الفيديو"),
            tr("تم تدوير الفيديو"),
            scale_timed_items=False,
            preserve_continuous_audio=True,
        )

    def start_speed_preview(self, speed, offset=0, silent=False):
        selected = self.selected_effect_range()
        if not selected:
            return 0
        self.stop_speed_preview()
        start_time, end_time = selected
        timeline_snapshot = list(self.timeline)
        visual_snapshot = [dict(item) for item in self.visual_items]
        background_snapshot = [dict(item) for item in self.background_audio_items]
        original_state = {
            "timeline": timeline_snapshot,
            "visual_items": visual_snapshot,
            "background_audio_items": background_snapshot,
            "current_time": self.current_time,
            "playback_requested": self.playback_requested,
            "current_segment_index": self.current_segment_index,
            "active_media_path": self.active_media_path,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }
        self.timeline, new_duration = speed_timeline_range(self.timeline, start_time, end_time, speed)
        self.speed_timed_items_for_range(start_time, end_time, speed, new_duration)
        preview_end = start_time + new_duration
        offset = max(0.0, min(float(offset or 0.0), new_duration))
        self.speed_preview_state = {
            **original_state,
            "preview_start": start_time,
            "preview_end": preview_end,
        }
        self.playback_requested = True
        self.load_timeline_time(start_time + offset, True)
        if not silent:
            self.say(tr("تشغيل معاينة السرعة"))
        return new_duration

    def pause_speed_preview(self):
        if not self.speed_preview_state:
            return
        self.playback_requested = False
        try:
            if self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
                self.media_ctrl.Pause()
        except Exception:
            pass
        self.stop_original_audio_playback()
        for state in self.background_audio_players.values():
            ctrl = state.get("ctrl")
            try:
                if ctrl and ctrl.GetState() == MEDIASTATE_PLAYING:
                    ctrl.Pause()
            except Exception:
                pass
        self.say(tr("إيقاف مؤقت لمعاينة السرعة"))

    def seek_speed_preview(self, delta):
        if not self.speed_preview_state:
            return 0
        start_time = float(self.speed_preview_state.get("preview_start", 0) or 0)
        end_time = float(self.speed_preview_state.get("preview_end", start_time) or start_time)
        target = max(start_time, min(end_time, self.current_time + float(delta or 0)))
        self.load_timeline_time(target, self.playback_requested)
        return max(0.0, target - start_time)

    def speed_preview_offset(self):
        if not self.speed_preview_state:
            return 0
        start_time = float(self.speed_preview_state.get("preview_start", 0) or 0)
        end_time = float(self.speed_preview_state.get("preview_end", start_time) or start_time)
        return max(0.0, min(end_time - start_time, self.current_time - start_time))

    def stop_speed_preview(self):
        state = self.speed_preview_state
        if not state:
            return
        self.speed_preview_state = None
        try:
            if self.media_ctrl.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
                self.media_ctrl.Pause()
        except Exception:
            pass
        self.stop_original_audio_playback()
        self.stop_background_audio_playback()
        self.timeline = list(state["timeline"])
        self.visual_items = [dict(item) for item in state["visual_items"]]
        self.background_audio_items = [dict(item) for item in state["background_audio_items"]]
        self.current_time = float(state.get("current_time", 0) or 0)
        self.playback_requested = bool(state.get("playback_requested", False))
        self.current_segment_index = state.get("current_segment_index")
        self.active_media_path = state.get("active_media_path", "")
        self.start_time = state.get("start_time")
        self.end_time = state.get("end_time")
        self.load_timeline_time(self.current_time, self.playback_requested)

    def OnCensorBleep(self, event=None):
        if not self.require_open_file():
            return
        selected = self.selected_transform_range()
        if not selected:
            return
        dialog = CensorBleepDialog(self)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        sound_key = dialog.sound_key()
        dialog.Destroy()
        if self.media_kind == "video":
            transform_kind = "audio_override_censor_bleep"
            self.pending_audio_override_transform_metadata[transform_kind] = {
                "effect_key": "censor_bleep",
                "parameters": {"sound_key": sound_key},
            }
            worker = lambda progress, cancelled: self.prepare_main_audio_censor_effect(
                sound_key, selected[0], selected[1], progress, cancelled
            )
        else:
            transform_kind = "censor_bleep"
            worker = lambda progress, cancelled: build_censor_segment(
                self.timeline, selected[0], selected[1], sound_key, progress, cancelled
            )
        self.start_timeline_transform(
            transform_kind,
            tr("جاري تطبيق كتم الكلمة"),
            tr("نسبة تطبيق كتم الكلمة {percent} بالمئة"),
            tr("حالة تطبيق كتم الكلمة"),
            tr("شريط تقدم كتم الكلمة"),
            tr("إلغاء كتم الكلمة"),
            tr("جاري إلغاء كتم الكلمة"),
            worker,
            selected,
            tr("كتم كلمة بصوت تغطية"),
            tr("تم تطبيق كتم الكلمة"),
            scale_timed_items=False,
        )

    def prepare_main_audio_censor_effect(self, sound_key, start_time, end_time, progress_callback=None, cancelled_callback=None):
        """تجهيز صوت تغطية داخل المسار البديل الكامل دون إنتاج فيديو وسيط."""
        source_temp_dir = ""
        effect_temp_dir = ""
        try:
            source = self.audio_override_manager.ensure_effect_source(
                (lambda value, message="": progress_callback(min(20, value * 0.20))) if progress_callback else None,
                cancelled_callback,
            )
            source_temp_dir = source.temp_dir
            censor_path, effect_temp_dir, _duration = build_censor_segment(
                [TimelineSegment(source.path, 0.0, source.duration)],
                start_time,
                end_time,
                sound_key,
                (lambda value: progress_callback(20 + value * 0.55)) if progress_callback else None,
                cancelled_callback,
            )
            composed = self.audio_override_manager.compose_audio_replacement(
                source.path,
                censor_path,
                start_time,
                end_time,
                target_duration=self.timeline_duration(),
                temp_dir=effect_temp_dir,
                progress_callback=(lambda value, message="": progress_callback(75 + value * 0.25)) if progress_callback else None,
                cancelled_callback=cancelled_callback,
            )
            return composed.path, effect_temp_dir, composed.duration
        except Exception:
            if effect_temp_dir:
                shutil.rmtree(effect_temp_dir, ignore_errors=True)
            raise
        finally:
            if source_temp_dir:
                shutil.rmtree(source_temp_dir, ignore_errors=True)

    def start_timeline_transform(
        self,
        kind,
        title,
        progress_template,
        status_name,
        gauge_name,
        cancel_name,
        cancelling_message,
        worker,
        selected,
        operation,
        success_message,
        scale_timed_items=False,
        preserve_continuous_audio=False,
    ):
        trace_event(
            "timeline_transform",
            "start.request",
            window=self.window_number,
            kind=kind,
            operation=operation,
            selected=selected,
            timeline_items=len(self.timeline),
        )
        if self.timeline_transform_progress_dialog:
            self.say(tr("انتظر حتى ينتهي العمل الحالي"))
            return
        self._diagnostic_active_operation = f"timeline_transform:{operation or kind}"
        self._diagnostic_operation_started = time.monotonic()
        self.playback_requested = False
        try:
            self.media_ctrl.Pause()
        except Exception:
            pass
        self.stop_original_audio_playback()
        self.stop_background_audio_playback()
        self.timeline_transform_cancelled = False
        self.last_spoken_transform_percent = -10
        # A save requested while a timeline transform is still rendering must
        # never capture the old timeline. It is queued and opened only after
        # the transformed media has been committed to the timeline.
        self.pending_save_after_transform = False
        self.timeline_transform_progress_dialog = SaveProgressDialog(
            self,
            self.cancel_timeline_transform,
            title=title,
            progress_template=progress_template,
            status_name=status_name,
            gauge_name=gauge_name,
            cancel_name=cancel_name,
            cancelling_message=cancelling_message,
        )
        self.timeline_transform_progress_dialog.Show()
        self.say(title)
        threading.Thread(
            target=self.timeline_transform_worker,
            args=(worker, selected, kind, operation, success_message, scale_timed_items, preserve_continuous_audio),
            daemon=True,
        ).start()

    def cancel_timeline_transform(self):
        trace_event(
            "timeline_transform",
            "cancel.request",
            level="WARNING",
            immediate=True,
            window=self.window_number,
            active=bool(self.timeline_transform_progress_dialog),
            current_percent=self.last_spoken_transform_percent,
        )
        self.timeline_transform_cancelled = True
        self.say(tr("جاري إلغاء العمل"))

    def update_timeline_transform_progress(self, value):
        if not self.timeline_transform_progress_dialog:
            trace_event("timeline_transform", "progress.ignored", window=self.window_number, value=value, reason="dialog_missing")
            return
        value = max(0, min(100, int(value)))
        self.timeline_transform_progress_dialog.update_progress(value)
        if value >= self.last_spoken_transform_percent + 10 or value >= 100:
            self.last_spoken_transform_percent = value
            trace_event(
                "timeline_transform",
                "progress",
                window=self.window_number,
                value=value,
                cancelled=self.timeline_transform_cancelled,
                operation=getattr(self, "_diagnostic_active_operation", ""),
            )
            self.say(tr("نسبة العمل {percent} بالمئة").format(percent=value), interrupt=False)

    def timeline_transform_worker(self, worker, selected, kind, operation, success_message, scale_timed_items, preserve_continuous_audio=False):
        trace_event(
            "timeline_transform",
            "worker.start",
            window=self.window_number,
            kind=kind,
            operation=operation,
            selected=selected,
            preserve_continuous_audio=bool(preserve_continuous_audio),
        )
        prepared_continuous_audio = None
        try:
            # التعديلات البصرية التي لا تغيّر المدة تستخدم صوتًا واحدًا متصلًا
            # للمشروع كله. هذا يمنع تمهيد AAC/MP3 من تكرار أو قطع الكلمات عند
            # حدود الجزء المرئي المعاد ترميزه.
            if preserve_continuous_audio and self.media_kind == "video":
                prepared_continuous_audio = self.audio_override_manager.ensure_effect_source(
                    lambda value, message="": wx.CallAfter(
                        self.update_timeline_transform_progress,
                        max(0, min(20, int(float(value) * 0.20))),
                    ),
                    lambda: self.timeline_transform_cancelled,
                )

            def transform_progress(percent):
                value = max(0.0, min(100.0, float(percent)))
                if prepared_continuous_audio is not None:
                    value = 20.0 + value * 0.80
                wx.CallAfter(self.update_timeline_transform_progress, value)

            path, temp_dir, new_duration = worker(
                transform_progress,
                lambda: self.timeline_transform_cancelled,
            )
            if self.timeline_transform_cancelled:
                raise OperationCancelled()
        except OperationCancelled:
            if prepared_continuous_audio is not None and prepared_continuous_audio.temp_dir:
                shutil.rmtree(prepared_continuous_audio.temp_dir, ignore_errors=True)
            wx.CallAfter(
                self.finish_timeline_transform, None, "", 0, selected, kind, operation,
                success_message, scale_timed_items, True, None
            )
            return
        except Exception as error:
            if is_operation_cancelled(error, lambda: self.timeline_transform_cancelled):
                if prepared_continuous_audio is not None and prepared_continuous_audio.temp_dir:
                    shutil.rmtree(prepared_continuous_audio.temp_dir, ignore_errors=True)
                wx.CallAfter(
                    self.finish_timeline_transform, None, "", 0, selected, kind, operation,
                    success_message, scale_timed_items, True, None
                )
                return
            if prepared_continuous_audio is not None and prepared_continuous_audio.temp_dir:
                shutil.rmtree(prepared_continuous_audio.temp_dir, ignore_errors=True)
            wx.CallAfter(
                self.finish_timeline_transform, None, str(error), 0, selected, kind, operation,
                success_message, scale_timed_items, False, None
            )
            return
        wx.CallAfter(
            self.finish_timeline_transform, (path, temp_dir), "", new_duration, selected, kind,
            operation, success_message, scale_timed_items, False, prepared_continuous_audio
        )

    def finish_timeline_transform(self, result, error_message, new_duration, selected, kind, operation, success_message, scale_timed_items, cancelled, prepared_continuous_audio=None):
        trace_event(
            "timeline_transform",
            "finish",
            level="ERROR" if error_message else ("WARNING" if cancelled else "INFO"),
            immediate=bool(error_message or cancelled),
            window=self.window_number,
            kind=kind,
            operation=operation,
            selected=selected,
            cancelled=cancelled,
            error=error_message,
            new_duration=new_duration,
            result=result,
        )
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        save_was_queued = bool(self.pending_save_after_transform)
        self.pending_save_after_transform = False
        if self.timeline_transform_progress_dialog:
            self.timeline_transform_progress_dialog.Destroy()
            self.timeline_transform_progress_dialog = None
        typing_text_transform = bool(getattr(self, "_typing_text_transform_pending", False))
        self._typing_text_transform_pending = False
        if cancelled:
            self.pending_audio_override_transform_metadata.pop(kind, None)
            if prepared_continuous_audio is not None and prepared_continuous_audio.temp_dir:
                shutil.rmtree(prepared_continuous_audio.temp_dir, ignore_errors=True)
            self.say(tr("تم إلغاء العمل"))
            if save_was_queued:
                self.say(tr("لم يبدأ الحفظ لأن التعديل الحالي أُلغي"), False)
            return
        if error_message:
            self.pending_audio_override_transform_metadata.pop(kind, None)
            if prepared_continuous_audio is not None and prepared_continuous_audio.temp_dir:
                shutil.rmtree(prepared_continuous_audio.temp_dir, ignore_errors=True)
            # self.say(tr("تعذر تطبيق التعديل"))
            wx.MessageBox(tr(error_message), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            if save_was_queued:
                self.say(tr("لم يبدأ الحفظ لأن التعديل الحالي لم يكتمل"), False)
            return
        path, temp_dir = result
        start_time, end_time = selected
        before_state = self.capture_edit_state()
        prepared_audio_adopted = False
        try:
            if prepared_continuous_audio is not None:
                if not self.audio_override_manager.valid_audio_file(prepared_continuous_audio.path):
                    raise RuntimeError(tr("تعذر تجهيز الصوت المتصل للتعديل البصري"))
                self.main_audio_override_path = prepared_continuous_audio.path
                self.main_audio_override_duration = prepared_continuous_audio.duration
                self.main_audio_override_timeline_duration = self.timeline_duration()
                prepared_audio_adopted = True
            if not path or not os.path.exists(path):
                raise RuntimeError(tr("ملف التعديل الناتج غير موجود"))
            if str(kind or "").startswith("audio_override_"):
                self.generated_temp_dirs.append(temp_dir)
                self.generated_temp_files.append(path)
                self.main_audio_override_path = path
                self.main_audio_override_duration = self.audio_override_manager.exact_duration(path)
                self.main_audio_override_timeline_duration = self.timeline_duration()
                metadata = self.pending_audio_override_transform_metadata.pop(kind, {})
                self.audio_override_manager.register_effect(
                    metadata.get("effect_key", kind),
                    operation,
                    start_time,
                    end_time,
                    metadata.get("parameters", {}),
                )
                self.current_time = start_time
                self.start_time = None
                self.end_time = None
                self.is_dirty = True
                self.record_edit(operation, before_state, audio_policy="already_updated")
                self.reload_current_position()
                self.say(success_message)
                if save_was_queued:
                    wx.CallAfter(self.OnSaveVideo)
                return
            restore_segments = slice_segments(before_state["timeline"], start_time, end_time)
            navigation_group = self.common_navigation_group(restore_segments)
            old_duration = max(0.0, end_time - start_time)
            self.timeline, actual_duration = replace_timeline_range(self.timeline, start_time, end_time, path)
            if navigation_group:
                self.mark_timeline_range_navigation_group(start_time, actual_duration, navigation_group)
            # Chroma replacement always targets the complete timeline. Commit the
            # rendered asset explicitly so a later save can never fall back to the
            # original green-screen source because of a boundary rounding issue.
            if kind == "chroma_background" and start_time <= 0.001 and end_time >= old_duration - 0.001:
                # replace_timeline_range أبقى هوية كل ملف حتى لو كان الرندر النهائي
                # ملفًا ماديًا واحدًا؛ لا نضغطه إلى TimelineSegment واحد.
                self.chroma_render_state = {
                    "render_path": path,
                    "source_paths": sorted({os.path.abspath(segment.path) for segment in restore_segments}),
                    "duration": actual_duration,
                }
            self.generated_temp_dirs.append(temp_dir)
            if scale_timed_items and old_duration > 0:
                self.scale_timed_items_after_transform(start_time, end_time, actual_duration)
                self.edit_points = adjust_points_after_delete(self.edit_points, start_time, end_time)
                self.edit_points = adjust_points_after_insert(self.edit_points, start_time, actual_duration)
            self.add_edit_point(kind, start_time, start_time + actual_duration, "timeline", restore_segments=restore_segments, mode="replace", label=operation)
            self.current_time = start_time
            self.start_time = None
            self.end_time = None
            self.is_dirty = True
            if typing_text_transform:
                typing_audio = getattr(self, "_typing_text_audio_override_result", None) or {}
                typing_audio_path = str(typing_audio.get("path", "") or "")
                if not typing_audio_path or not self.audio_override_manager.valid_audio_file(typing_audio_path):
                    raise RuntimeError(tr("تعذر تجهيز صوت متصل لنص الكتابة"))
                self.main_audio_override_path = typing_audio_path
                self.main_audio_override_duration = float(typing_audio.get("duration", 0.0) or self.audio_override_manager.exact_duration(typing_audio_path))
                self.main_audio_override_timeline_duration = self.timeline_duration()
                for extra_dir in typing_audio.get("temp_dirs", []) or []:
                    if extra_dir and extra_dir not in self.generated_temp_dirs:
                        self.generated_temp_dirs.append(extra_dir)
                if typing_audio_path not in self.generated_temp_files:
                    self.generated_temp_files.append(typing_audio_path)
            self.record_edit(
                operation,
                before_state,
                audio_policy="already_updated" if typing_text_transform else ("preserve" if prepared_audio_adopted else "auto"),
            )
            if prepared_audio_adopted:
                if prepared_continuous_audio.temp_dir and prepared_continuous_audio.temp_dir not in self.generated_temp_dirs:
                    self.generated_temp_dirs.append(prepared_continuous_audio.temp_dir)
                if prepared_continuous_audio.path and prepared_continuous_audio.path not in self.generated_temp_files:
                    self.generated_temp_files.append(prepared_continuous_audio.path)
            self.refresh_menu_bar()
            self.reload_current_position()
            self.say(success_message)
            if save_was_queued:
                # Run on the next UI turn, after the transformed timeline and media
                # path have been fully committed. The save dialog therefore captures
                # the new rendered asset, never the pre-transform source.
                wx.CallAfter(self.OnSaveVideo)
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored(operation, error, "timeline_transform_commit")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            if prepared_continuous_audio is not None and prepared_continuous_audio.temp_dir:
                shutil.rmtree(prepared_continuous_audio.temp_dir, ignore_errors=True)
            show_error(
                f"{tr('تعذر تثبيت التعديل في الخط الزمني')}. {tr('لم يثبت التعديل وتمت استعادة مساحة العمل كما كانت')}: {error}",
                "خطأ",
                self,
                exception=error,
                context="timeline_transform_commit",
            )
            if save_was_queued:
                self.say(tr("لم يبدأ الحفظ لأن التعديل الحالي لم يكتمل"), False)

    def speed_timed_items_for_range(self, start_time, end_time, speed, new_duration):
        self.visual_items = self.speed_items_for_range(self.visual_items, start_time, end_time, speed, new_duration)
        self.background_audio_items = self.speed_items_for_range(self.background_audio_items, start_time, end_time, speed, new_duration)

    def speed_items_for_range(self, items, start_time, end_time, speed, new_duration):
        old_duration = max(0.0, end_time - start_time)
        if old_duration <= 0:
            return list(items)
        speed = max(0.05, float(speed))
        scale = max(0.0, float(new_duration)) / old_duration
        shift = float(new_duration) - old_duration
        adjusted = []
        for item in items:
            item_start = float(item.get("start", 0) or 0)
            item_end = float(item.get("end", item_start) or item_start)
            if item_end <= start_time:
                adjusted.append(dict(item))
            elif item_start >= end_time:
                updated = dict(item)
                updated["start"] = max(0.0, item_start + shift)
                updated["end"] = max(updated["start"], item_end + shift)
                adjusted.append(updated)
            else:
                item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
                if item_start < start_time:
                    before = dict(item)
                    before["start"] = item_start
                    before["end"] = start_time
                    if before["end"] > before["start"]:
                        adjusted.append(before)
                overlap_start = max(item_start, start_time)
                overlap_end = min(item_end, end_time)
                sped = dict(item)
                sped["start"] = start_time + (overlap_start - start_time) * scale
                sped["end"] = start_time + (overlap_end - start_time) * scale
                sped["speed"] = max(0.05, item_speed * speed)
                sped["source_offset"] = source_offset + max(0.0, overlap_start - item_start) * item_speed
                if sped["end"] > sped["start"]:
                    adjusted.append(sped)
                if item_end > end_time:
                    after = dict(item)
                    after["start"] = start_time + new_duration
                    after["end"] = item_end + shift
                    after["source_offset"] = source_offset + max(0.0, end_time - item_start) * item_speed
                    if after["end"] > after["start"]:
                        adjusted.append(after)
        return adjusted

    def scale_timed_items_after_transform(self, start_time, end_time, new_duration):
        self.visual_items = self.scale_items_after_transform(self.visual_items, start_time, end_time, new_duration)
        self.background_audio_items = self.scale_items_after_transform(self.background_audio_items, start_time, end_time, new_duration)

    def scale_items_after_transform(self, items, start_time, end_time, new_duration):
        old_duration = max(0.0, end_time - start_time)
        if old_duration <= 0:
            return list(items)
        scale = max(0.0, float(new_duration)) / old_duration
        shift = float(new_duration) - old_duration
        adjusted = []
        for item in items:
            item_start = float(item.get("start", 0) or 0)
            item_end = float(item.get("end", item_start) or item_start)
            updated = dict(item)
            if item_end <= start_time:
                adjusted.append(updated)
            elif item_start >= end_time:
                updated["start"] = max(0.0, item_start + shift)
                updated["end"] = max(updated["start"], item_end + shift)
                adjusted.append(updated)
            else:
                overlap_start = max(item_start, start_time)
                overlap_end = min(item_end, end_time)
                updated["start"] = start_time + (overlap_start - start_time) * scale
                updated["end"] = start_time + (overlap_end - start_time) * scale
                if updated["end"] > updated["start"]:
                    adjusted.append(updated)
        return adjusted

