from pathlib import Path
import shutil
import uuid

from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *
from video_maker.timeline_audio_insert import TimelineAudioVideoSaveBlocked, video_save_block_message_for_timeline


def _unique_locked_output_path(save_path):
    path = Path(save_path)
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        label = "saved copy" if index == 1 else f"saved copy {index}"
        candidate = parent / f"{stem} {label}{suffix}"
        if not candidate.exists():
            return str(candidate)
    return str(parent / f"{stem} saved copy {uuid.uuid4().hex}{suffix}")


def finalize_rendered_output(render_path, save_path):
    if os.path.abspath(render_path) == os.path.abspath(save_path):
        return save_path
    try:
        os.replace(render_path, save_path)
        return save_path
    except OSError as error:
        access_denied = isinstance(error, PermissionError) or getattr(error, "winerror", None) == 5
        if not access_denied:
            raise
        if not os.path.exists(save_path):
            try:
                shutil.copy2(render_path, save_path)
                os.remove(render_path)
                return save_path
            except OSError:
                pass
        fallback_path = _unique_locked_output_path(save_path)
        try:
            os.replace(render_path, fallback_path)
            return fallback_path
        except OSError:
            raise error


@publish_player_methods
class PlayerSaveMixin:
    def timeline_snapshot_for_save(self):
        """Return a timeline that cannot silently fall back to a pre-chroma source.

        Chroma replacement is rendered asynchronously. The normal committed
        timeline points at that rendered file. This guard also covers a stale
        UI/session state that still points only at the original source after a
        successful full-timeline replacement. Other later edits are left alone.
        """
        snapshot = list(self.timeline)
        state = self.chroma_render_state or {}
        render_path = str(state.get("render_path", "") or "")
        if not snapshot or not render_path or not os.path.isfile(render_path):
            return snapshot
        render_abs = os.path.abspath(render_path)
        if any(os.path.abspath(segment.path) == render_abs for segment in snapshot):
            return snapshot
        source_paths = {os.path.abspath(path) for path in state.get("source_paths", []) if path}
        if not source_paths or not all(os.path.abspath(segment.path) in source_paths for segment in snapshot):
            return snapshot
        try:
            render_duration = get_media_duration(render_path)
        except Exception:
            return snapshot
        if abs(total_duration(snapshot) - render_duration) > 0.10:
            return snapshot
        return [TimelineSegment(render_path, 0.0, render_duration)]

    def video_audio_override_snapshot(self, selected_start=0.0):
        if self.media_kind != "video" or not self.main_audio_override_configured():
            return "", 0.0
        error = self.main_audio_override_save_error()
        if error:
            # لا نمنع حفظ الفيديو إذا حُذف الملف المؤقت خارجيًا أو تلف.
            # نعود إلى صوت الخط الزمني الحالي، ونلغي سجل المؤثرات الذي لم يعد
            # له ملف فعلي حتى لا ندعي حفظ مؤثرات غير موجودة.
            self.reset_main_audio_override_state()
            self.is_dirty = True
            self.say(tr("تعذر استخدام ملف الصوت المعالج وسيتم حفظ صوت الخط الزمني الحالي"))
            return "", 0.0
        return self.main_audio_override_path, max(0.0, float(selected_start or 0.0))

    def call_after_or_now(self, callback, *args):
        try:
            if wx.GetApp():
                wx.CallAfter(callback, *args)
                return
        except Exception:
            pass
        callback(*args)

    def timeline_audio_video_save_block_message(self, timeline_snapshot=None, visual_snapshot=None, b_roll_snapshot=None):
        return video_save_block_message_for_timeline(
            list(self.timeline) if timeline_snapshot is None else timeline_snapshot,
            self.visual_items if visual_snapshot is None else visual_snapshot,
            getattr(self, "b_roll_items", []) if b_roll_snapshot is None else b_roll_snapshot,
            has_audio_stream,
            has_video_stream,
        )

    def speak_timeline_audio_video_save_blocked(self, message):
        self.save_operation_running = False
        try:
            self.DestroyProgressDialog()
        except Exception:
            pass
        self.say(message)

    def OnExportVideoAudio(self, event=None):
        if not self.has_video() or self.media_kind != "video":
            self.say(tr("هذا الخيار متاح مع الفيديو فقط"))
            return
        save_path, save_options = ask_audio_save_path(self, self.say, self.video_path, False)
        if not save_path:
            return
        save_options = save_options_with_output_volume(save_options, self.volume)
        save_options = save_options_with_master_volume(save_options, getattr(self, "master_volume_db", 0.0))
        save_options = save_options_with_track_volumes(save_options, getattr(self, "track_volumes_db", {}) or {})
        self.say(tr("جاري تصدير صوت الفيديو"))
        self.save_cancelled = False
        self.reset_save_progress_state()
        self.save_operation_running = True
        self.CreateProgressDialog("audio")
        self.call_after_or_now(self.StartExportVideoAudioAfterDialog, save_path, save_options)

    def StartExportVideoAudioAfterDialog(self, save_path, save_options):
        if self.save_cancelled:
            self.OnSaveCancelled(save_path)
            return
        try:
            override_path, _override_start = self.video_audio_override_snapshot(0.0)
            timeline_snapshot = self.timeline_snapshot_for_save()
            background_audio_snapshot = [dict(item) for item in self.background_audio_items]
            sound_effects_snapshot = [dict(item) for item in self.sound_effects_items]
            muted_tracks_snapshot = set(getattr(self, "muted_tracks", ()) or ())
            solo_tracks_snapshot = set(getattr(self, "solo_tracks", ()) or ())
            metadata_snapshot = dict(self.file_metadata)
            threading.Thread(
                target=self.SaveVideo,
                args=(save_path, timeline_snapshot, metadata_snapshot, False, [], "audio", save_options, background_audio_snapshot, override_path, 0.0, [], sound_effects_snapshot, muted_tracks_snapshot, solo_tracks_snapshot),
            ).start()
        except Exception as error:
            self.OnSaveError(str(error), "audio")

    def OnImportVideoAudio(self, event=None):
        if not self.has_video() or self.media_kind != "video":
            self.say(tr("هذا الخيار متاح مع الفيديو فقط"))
            return
        with wx.FileDialog(self, tr("استيراد الملف الصوتي الخاص بهذا الفيديو"), wildcard=AUDIO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, "audio", "import_video_audio")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            audio_path = dialog.GetPath()
            remember_media_paths([audio_path], "audio", "import_video_audio")
        if not audio_path or not os.path.exists(audio_path):
            self.say(tr("ملف الصوت غير موجود"))
            return
        if not has_audio_stream(audio_path):
            # self.say(tr("الملف المختار لا يحتوي على صوت"))
            wx.MessageBox(tr("الملف المختار لا يحتوي على صوت."), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        try:
            audio_duration = get_media_duration(audio_path)
        except Exception as error:
            # self.say(tr("تعذر قراءة مدة ملف الصوت"))
            wx.MessageBox(tr("تعذر قراءة مدة ملف الصوت: {error}").format(error=error), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        video_duration = self.timeline_duration()
        try:
            fitted_audio = self.audio_override_manager.fit_audio_to_duration(audio_path, video_duration)
            audio_path = fitted_audio.path
            audio_duration = fitted_audio.duration
            if fitted_audio.temp_dir:
                self.generated_temp_dirs.append(fitted_audio.temp_dir)
            self.generated_temp_files.append(audio_path)
        except Exception as error:
            message = tr("تعذر تجهيز ملف الصوت ليتوافق مع مدة الفيديو: {error}").format(error=error)
            # self.say(message)
            wx.MessageBox(message, tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        before_state = self.capture_edit_state()
        if not self.use_reliable_audio and reliable_audio_available():
            self.use_reliable_audio = True
            self.original_audio_player = ReliableAudioPlayer()
        self.main_audio_override_path = audio_path
        self.main_audio_override_duration = audio_duration
        self.main_audio_override_timeline_duration = video_duration
        self.main_audio_effect_chain = []
        self.main_audio_revision = int(self.main_audio_revision or 0) + 1
        self.main_audio_source_revision = int(self.timeline_revision or 0)
        self.is_dirty = True
        self.record_edit("استيراد صوت الفيديو", before_state)
        self.reload_current_position()
        self.refresh_menu_bar()
        self.say(tr("تم استيراد صوت الفيديو وتطبيقه في المشغل"))

    def OnSaveVideo(self, event=None):
        if not self.require_open_file():
            return
        if self.timeline_transform_progress_dialog is not None:
            if not self.pending_save_after_transform:
                self.pending_save_after_transform = True
                self.say(tr("سيتم فتح نافذة الحفظ بعد اكتمال التعديل الحالي"))
            else:
                self.say(tr("طلب الحفظ مسجل وسيبدأ بعد اكتمال التعديل الحالي"))
            return
        save_options = None
        output_kind = project_output_kind(self)
        if output_kind == "video" and self.media_kind == "video":
            block_message = self.timeline_audio_video_save_block_message()
            if block_message:
                self.say(block_message)
                return
        if output_kind == "audio":
            save_path, save_options = ask_audio_save_path(self, self.say, self.video_path, False)
        else:
            save_path, save_options = ask_video_save_path(self, self.say, False, self.video_path)
        if save_path:
            save_options = save_options_with_output_volume(save_options, self.volume)
            save_options = save_options_with_master_volume(save_options, getattr(self, "master_volume_db", 0.0))
            save_options = save_options_with_track_volumes(save_options, getattr(self, "track_volumes_db", {}) or {})
            self.say(speech_messages.SAVING_AUDIO if output_kind == "audio" else speech_messages.SAVING_VIDEO)
            self.save_cancelled = False
            self.reset_save_progress_state()
            self.save_operation_running = True
            self.CreateProgressDialog(output_kind)
            self.call_after_or_now(self.StartSaveVideoAfterDialog, save_path, save_options, output_kind)

    def StartSaveVideoAfterDialog(self, save_path, save_options, output_kind):
        if self.save_cancelled:
            self.OnSaveCancelled(save_path)
            return
        try:
            override_path = ""
            override_start = 0.0
            if output_kind == "video":
                override_path, override_start = self.video_audio_override_snapshot(0.0)
            timeline_snapshot = self.timeline_snapshot_for_save()
            visual_snapshot = [dict(item) for item in self.visual_items]
            background_audio_snapshot = [dict(item) for item in self.background_audio_items]
            b_roll_snapshot = [dict(item) for item in getattr(self, "b_roll_items", [])]
            sound_effects_snapshot = [dict(item) for item in self.sound_effects_items]
            muted_tracks_snapshot = set(getattr(self, "muted_tracks", ()) or ())
            solo_tracks_snapshot = set(getattr(self, "solo_tracks", ()) or ())
            metadata_snapshot = dict(self.file_metadata)
            threading.Thread(
                target=self.SaveVideo,
                args=(save_path, timeline_snapshot, metadata_snapshot, True, visual_snapshot, self.media_kind, save_options, background_audio_snapshot, override_path, override_start, b_roll_snapshot, sound_effects_snapshot, muted_tracks_snapshot, solo_tracks_snapshot),
            ).start()
        except Exception as error:
            self.OnSaveError(str(error), output_kind)

    def OnSaveSelectedVideo(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if self.timeline_transform_progress_dialog is not None:
            self.say(tr("انتظر حتى ينتهي التعديل الحالي ثم احفظ الجزء المحدد"))
            return
        selected = self.selected_effect_range()
        if not selected:
            # self.say("حدد بداية ونهاية المقطع أولا")
            wx.MessageBox("حدد بداية ونهاية المقطع المطلوب حفظه أولا.", "تحديد مطلوب", wx.OK | wx.ICON_INFORMATION)
            return
        start_time, end_time = selected
        selected_has_visuals = any(
            item["end"] > start_time and item["start"] < end_time
            for item in self.visual_items
        )
        if self.media_kind == "video":
            block_message = self.timeline_audio_video_save_block_message(
                slice_segments(list(self.timeline), start_time, end_time),
                timed_items_for_range(self.visual_items, start_time, end_time),
                timed_items_for_range(getattr(self, "b_roll_items", []), start_time, end_time),
            )
            if block_message:
                self.say(block_message)
                return
        save_options = None
        if self.media_kind == "audio" and not selected_has_visuals:
            save_path, save_options = ask_audio_save_path(self, self.say, self.video_path, True)
        else:
            save_path, save_options = ask_video_save_path(self, self.say, True, self.video_path)
        if not save_path:
            return

        save_options = save_options_with_output_volume(save_options, self.volume)
        save_options = save_options_with_master_volume(save_options, getattr(self, "master_volume_db", 0.0))
        save_options = save_options_with_track_volumes(save_options, getattr(self, "track_volumes_db", {}) or {})
        self.say(speech_messages.SAVING_AUDIO if self.media_kind == "audio" and not selected_has_visuals else speech_messages.SAVING_VIDEO)
        self.save_cancelled = False
        self.reset_save_progress_state()
        self.save_operation_running = True
        output_kind = "audio" if self.media_kind == "audio" and not selected_has_visuals else "video"
        self.CreateProgressDialog(output_kind)
        self.call_after_or_now(self.StartSaveSelectedVideoAfterDialog, save_path, save_options, start_time, end_time, output_kind)

    def StartSaveSelectedVideoAfterDialog(self, save_path, save_options, start_time, end_time, output_kind):
        if self.save_cancelled:
            self.OnSaveCancelled(save_path)
            return
        try:
            timeline_snapshot = slice_segments(self.timeline_snapshot_for_save(), start_time, end_time)
            visual_snapshot = []
            background_audio_snapshot = []
            for item in self.visual_items:
                if item["end"] > start_time and item["start"] < end_time:
                    adjusted = dict(item)
                    item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                    source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
                    overlap_start = max(float(item["start"]), start_time)
                    overlap_end = min(end_time, float(item["end"]))
                    adjusted["start"] = overlap_start - start_time
                    adjusted["end"] = overlap_end - start_time
                    adjusted["source_offset"] = source_offset + max(0.0, overlap_start - float(item["start"])) * item_speed
                    visual_snapshot.append(adjusted)
            for item in self.background_audio_items:
                if item["end"] > start_time and item["start"] < end_time:
                    adjusted = dict(item)
                    item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                    source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
                    overlap_start = max(float(item["start"]), start_time)
                    overlap_end = min(end_time, float(item["end"]))
                    adjusted["start"] = overlap_start - start_time
                    adjusted["end"] = overlap_end - start_time
                    adjusted["source_offset"] = source_offset + max(0.0, overlap_start - float(item["start"])) * item_speed
                    background_audio_snapshot.append(adjusted)
            b_roll_snapshot = timed_items_for_range(getattr(self, "b_roll_items", []), start_time, end_time)
            sound_effects_snapshot = timed_items_for_range(getattr(self, "sound_effects_items", []), start_time, end_time)
            muted_tracks_snapshot = set(getattr(self, "muted_tracks", ()) or ())
            solo_tracks_snapshot = set(getattr(self, "solo_tracks", ()) or ())
            metadata_snapshot = dict(self.file_metadata)
            override_path = ""
            override_start = 0.0
            if not (self.media_kind == "audio" and not visual_snapshot):
                override_path, override_start = self.video_audio_override_snapshot(start_time)
            threading.Thread(
                target=self.SaveVideo,
                args=(save_path, timeline_snapshot, metadata_snapshot, False, visual_snapshot, self.media_kind, save_options, background_audio_snapshot, override_path, override_start, b_roll_snapshot, sound_effects_snapshot, muted_tracks_snapshot, solo_tracks_snapshot),
            ).start()
        except Exception as error:
            self.OnSaveError(str(error), output_kind)

    def OnSplitTimeline(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if self.timeline_transform_progress_dialog is not None:
            self.say(tr("انتظر حتى ينتهي التعديل الحالي ثم احفظ التقسيم"))
            return
        selected = self.selected_effect_range()
        if not selected:
            # self.say(tr("حدد بداية ونهاية المقطع أولا"))
            wx.MessageBox(tr("حدد بداية ونهاية المقطع المطلوب تقسيمه أولا."), tr("تحديد مطلوب"), wx.OK | wx.ICON_INFORMATION)
            return
        start_time, end_time = selected
        dialog = TimelineSplitDialog(self, end_time - start_time, self.StartTimelineSplit)
        dialog.Show()

    def split_save_snapshot(self, start_time, end_time, timeline_snapshot=None, visual_items=None, background_audio_items=None, b_roll_items=None, sound_effects_items=None):
        source_timeline = timeline_snapshot if timeline_snapshot is not None else self.timeline_snapshot_for_save()
        source_visuals = visual_items if visual_items is not None else self.visual_items
        source_background = background_audio_items if background_audio_items is not None else self.background_audio_items
        source_b_roll = b_roll_items if b_roll_items is not None else getattr(self, "b_roll_items", [])
        source_sound_effects = sound_effects_items if sound_effects_items is not None else getattr(self, "sound_effects_items", [])
        return {
            "timeline": slice_segments(source_timeline, start_time, end_time),
            "visual_items": timed_items_for_range(source_visuals, start_time, end_time),
            "background_audio_items": timed_items_for_range(source_background, start_time, end_time),
            "b_roll_items": timed_items_for_range(source_b_roll, start_time, end_time),
            "sound_effects_items": timed_items_for_range(source_sound_effects, start_time, end_time),
        }

    def StartTimelineSplit(self, split_options):
        selected = self.selected_effect_range()
        if not selected:
            self.say(tr("حدد بداية ونهاية المقطع أولا"))
            return
        start_time, end_time = selected
        ranges = split_ranges_for_options(start_time, end_time, split_options)
        if not ranges:
            self.say(tr("لا يوجد جزء صالح للحفظ"), wait_for_ui=False)
            return
        output_kind = project_output_kind(self)
        if output_kind == "audio":
            save_path, save_options = ask_audio_save_path(self, self.say, self.video_path, True)
        else:
            save_path, save_options = ask_video_save_path(self, self.say, True, self.video_path)
        if not save_path:
            return
        save_options = save_options_with_output_volume(save_options, self.volume)
        save_options = save_options_with_master_volume(save_options, getattr(self, "master_volume_db", 0.0))
        save_options = save_options_with_track_volumes(save_options, getattr(self, "track_volumes_db", {}) or {})
        self.say(tr("جاري حفظ التقسيم"))
        self.save_cancelled = False
        self.reset_save_progress_state()
        self.save_operation_running = True
        self.CreateSplitProgressDialog(output_kind)
        self.call_after_or_now(self.StartTimelineSplitAfterDialog, ranges, save_path, output_kind, save_options)

    def StartTimelineSplitAfterDialog(self, ranges, save_path, output_kind, save_options):
        if self.save_cancelled:
            self.OnSaveCancelled(save_path)
            return
        try:
            timeline_snapshot = self.timeline_snapshot_for_save()
            visual_snapshot = [dict(item) for item in self.visual_items]
            background_audio_snapshot = [dict(item) for item in self.background_audio_items]
            b_roll_snapshot = [dict(item) for item in getattr(self, "b_roll_items", [])]
            metadata_snapshot = dict(self.file_metadata)
            jobs = []
            muted_tracks_snapshot = set(getattr(self, "muted_tracks", ()) or ())
            solo_tracks_snapshot = set(getattr(self, "solo_tracks", ()) or ())
            for index, split_range in enumerate(ranges, start=1):
                snapshot = self.split_save_snapshot(
                    split_range.start,
                    split_range.end,
                    timeline_snapshot,
                    visual_snapshot,
                    background_audio_snapshot,
                    b_roll_snapshot,
                )
                override_path = ""
                override_start = 0.0
                if output_kind == "video":
                    override_path, override_start = self.video_audio_override_snapshot(split_range.start)
                jobs.append({
                    "path": numbered_output_path(save_path, index, len(ranges)),
                    "timeline": snapshot["timeline"],
                    "visual_items": snapshot["visual_items"],
                    "background_audio_items": snapshot["background_audio_items"],
                    "b_roll_items": snapshot["b_roll_items"],
                    "sound_effects_items": snapshot["sound_effects_items"],
                    "muted_tracks": muted_tracks_snapshot,
                    "solo_tracks": solo_tracks_snapshot,
                    "main_audio_override_path": override_path,
                    "main_audio_override_start": override_start,
                })

            threading.Thread(
                target=self.SaveTimelineSplit,
                args=(jobs, metadata_snapshot, self.media_kind, output_kind, save_options),
            ).start()
        except Exception as error:
            self.OnSplitSaveError(str(error), [], output_kind)

    def SaveTimelineSplit(self, jobs, metadata_snapshot, media_kind, output_kind, save_options=None):
        self.save_operation_running = True
        self._diagnostic_active_operation = f"save_split:{output_kind}"
        self._diagnostic_operation_started = time.monotonic()
        trace_event("save", "split_worker.start", window=self.window_number, output_kind=output_kind, jobs=len(jobs or []))
        wx.CallAfter(self.CreateSplitProgressDialog, output_kind)
        saved_paths = []
        prepared_dirs = []
        partial_paths = []
        try:
            total_jobs = max(1, len(jobs))
            for job_index, job in enumerate(jobs):
                save_path = job["path"]
                saved_paths.append(save_path)
                prepared_audio = None
                render_path = save_path
                override_path = job.get("main_audio_override_path", "")
                override_start = job.get("main_audio_override_start", 0.0)

                def progress_callback(percent, job_index=job_index):
                    overall = ((job_index + max(0.0, min(100.0, float(percent))) / 100.0) / total_jobs) * 100.0
                    wx.CallAfter(self.UpdateProgressDialog, overall)

                if not is_track_audible(MAIN_VIDEO_TRACK, set(job.get("muted_tracks") or ()), set(job.get("solo_tracks") or ())):
                    override_path = ""

                if output_kind == "video":
                    render_path = self.audio_override_manager.partial_output_path(save_path)
                    partial_paths.append(render_path)
                    if override_path:
                        prepared_audio = self.audio_override_manager.prepare_export_audio(
                            source_path=override_path,
                            source_start=override_start,
                            duration=timeline_export_duration(job["timeline"]),
                            timeline=job["timeline"],
                            progress_callback=lambda percent, message="", callback=progress_callback: callback(min(10.0, float(percent) * 0.10)),
                            cancelled_callback=lambda: self.save_cancelled,
                        )
                        override_path = prepared_audio.path
                        override_start = 0.0
                        if prepared_audio.temp_dir:
                            prepared_dirs.append(prepared_audio.temp_dir)

                def render_job_progress(percent, prepared_audio=prepared_audio, callback=progress_callback):
                    percent = max(0.0, min(100.0, float(percent)))
                    if prepared_audio is not None:
                        percent = 10.0 + percent * 0.90
                    callback(percent)

                if media_kind == "audio" and job["visual_items"]:
                    write_audio_visual_video(
                        job["timeline"],
                        job["visual_items"],
                        render_path,
                        render_job_progress,
                        lambda: self.save_cancelled,
                        metadata_snapshot,
                        save_options,
                        job["background_audio_items"],
                        job.get("sound_effects_items"),
                        job.get("muted_tracks"),
                        job.get("solo_tracks"),
                    )
                elif media_kind == "audio":
                    write_timeline_audio(
                        job["timeline"],
                        render_path,
                        render_job_progress,
                        lambda: self.save_cancelled,
                        metadata_snapshot,
                        job["background_audio_items"],
                        save_options,
                        job.get("sound_effects_items"),
                        job.get("muted_tracks"),
                        job.get("solo_tracks"),
                    )
                else:
                    write_timeline_video(
                        job["timeline"],
                        render_path,
                        render_job_progress,
                        lambda: self.save_cancelled,
                        metadata_snapshot,
                        save_options,
                        job["background_audio_items"],
                        override_path,
                        override_start,
                        False,
                        job["b_roll_items"],
                        job.get("sound_effects_items"),
                        job["visual_items"],
                        job.get("muted_tracks"),
                        job.get("solo_tracks"),
                    )
                if self.save_cancelled:
                    raise OperationCancelled()
                if output_kind == "video":
                    source_has_audio = any(
                        self.audio_override_manager.valid_audio_file(str(getattr(segment, "audio_path", "") or segment.path))
                        for segment in job["timeline"] or []
                    )
                    export_background, export_sfx, export_broll = filter_audio_sources_for_export(
                        job["background_audio_items"],
                        job.get("sound_effects_items"),
                        job["b_roll_items"],
                        job.get("muted_tracks"),
                        job.get("solo_tracks"),
                    )
                    main_audible = is_track_audible(
                        MAIN_VIDEO_TRACK,
                        set(job.get("muted_tracks") or ()),
                        set(job.get("solo_tracks") or ()),
                    )
                    require_audio = bool(override_path or export_background or (export_sfx and timed_items_have_audio(export_sfx)) or (export_broll and timed_items_have_audio(export_broll)) or (main_audible and source_has_audio))
                    self.audio_override_manager.validate_exported_video(
                        render_path,
                        timeline_export_duration(job["timeline"]),
                        require_audio=require_audio,
                    )
                    save_path = finalize_rendered_output(render_path, save_path)
                    saved_paths[-1] = save_path
                    partial_paths.remove(render_path)
            wx.CallAfter(self.OnSplitSaveComplete, saved_paths, output_kind)
        except Exception as error:
            for path in list(partial_paths):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if is_operation_cancelled(error, lambda: self.save_cancelled):
                wx.CallAfter(self.OnSplitSaveCancelled, saved_paths)
            else:
                wx.CallAfter(self.OnSplitSaveError, str(error), saved_paths, output_kind)
        finally:
            for directory in prepared_dirs:
                shutil.rmtree(directory, ignore_errors=True)

    def SaveVideo(self, save_path, timeline_snapshot, metadata_snapshot=None, open_after_save=True, visual_snapshot=None, media_kind=None, save_options=None, background_audio_snapshot=None, main_audio_override_path="", main_audio_override_start=0.0, b_roll_snapshot=None, sound_effects_snapshot=None, muted_tracks_snapshot=None, solo_tracks_snapshot=None):
        self.save_operation_running = True
        output_kind = "video" if (media_kind == "audio" and visual_snapshot) else (media_kind or "video")
        self._diagnostic_active_operation = f"save:{output_kind}"
        self._diagnostic_operation_started = time.monotonic()
        trace_event(
            "save",
            "worker.start",
            window=self.window_number,
            output_kind=output_kind,
            destination=save_path,
            timeline_items=len(timeline_snapshot or []),
            visual_items=len(visual_snapshot or []),
        )
        wx.CallAfter(self.CreateProgressDialog, output_kind)
        prepared_audio = None
        partial_path = ""
        render_path = save_path
        try:
            if not is_track_audible(MAIN_VIDEO_TRACK, set(muted_tracks_snapshot or ()), set(solo_tracks_snapshot or ())):
                main_audio_override_path = ""
            if output_kind == "video" and media_kind == "video":
                block_message = video_save_block_message_for_timeline(
                    timeline_snapshot,
                    visual_snapshot,
                    b_roll_snapshot,
                    has_audio_stream,
                    has_video_stream,
                )
                if block_message:
                    raise TimelineAudioVideoSaveBlocked(block_message)
            if output_kind == "audio" and main_audio_override_path:
                prepared_audio = self.audio_override_manager.prepare_export_audio(
                    source_path=main_audio_override_path,
                    source_start=main_audio_override_start,
                    duration=timeline_export_duration(timeline_snapshot),
                    timeline=timeline_snapshot,
                    progress_callback=lambda percent, message="": wx.CallAfter(self.UpdateProgressDialog, min(20, float(percent) * 0.20)),
                    cancelled_callback=lambda: self.save_cancelled,
                )
                timeline_snapshot = [TimelineSegment(prepared_audio.path, 0.0, prepared_audio.duration)]
            if output_kind == "video":
                partial_path = self.audio_override_manager.partial_output_path(save_path)
                render_path = partial_path
                if main_audio_override_path:
                    prepared_audio = self.audio_override_manager.prepare_export_audio(
                        source_path=main_audio_override_path,
                        source_start=main_audio_override_start,
                        duration=timeline_export_duration(timeline_snapshot),
                        timeline=timeline_snapshot,
                        progress_callback=lambda percent, message="": wx.CallAfter(self.UpdateProgressDialog, min(10, float(percent) * 0.10)),
                        cancelled_callback=lambda: self.save_cancelled,
                    )
                    main_audio_override_path = prepared_audio.path
                    main_audio_override_start = 0.0

            def render_progress(percent):
                percent = max(0.0, min(100.0, float(percent)))
                if output_kind == "video" and prepared_audio is not None:
                    percent = 10 + percent * 0.90
                elif output_kind == "audio" and prepared_audio is not None:
                    percent = 20 + percent * 0.80
                wx.CallAfter(self.UpdateProgressDialog, percent)

            if media_kind == "audio" and visual_snapshot:
                write_audio_visual_video(
                    timeline_snapshot,
                    visual_snapshot,
                    render_path,
                    render_progress,
                    lambda: self.save_cancelled,
                    metadata_snapshot,
                    save_options,
                    background_audio_snapshot,
                    sound_effects_snapshot,
                    muted_tracks_snapshot,
                    solo_tracks_snapshot,
                )
            elif media_kind == "audio":
                write_timeline_audio(timeline_snapshot, render_path, render_progress, lambda: self.save_cancelled, metadata_snapshot, background_audio_snapshot, save_options, sound_effects_snapshot, muted_tracks_snapshot, solo_tracks_snapshot)
            else:
                write_timeline_video(
                    timeline_snapshot,
                    render_path,
                    render_progress,
                    lambda: self.save_cancelled,
                    metadata_snapshot,
                    save_options,
                    background_audio_snapshot,
                    main_audio_override_path,
                    main_audio_override_start,
                    False,
                    b_roll_snapshot,
                    sound_effects_snapshot,
                    visual_snapshot,
                    muted_tracks_snapshot,
                    solo_tracks_snapshot,
                )

            if self.save_cancelled:
                raise OperationCancelled()

            if output_kind == "video":
                expected_duration = timeline_export_duration(timeline_snapshot)
                source_has_audio = any(
                    self.audio_override_manager.valid_audio_file(str(getattr(segment, "audio_path", "") or segment.path))
                    for segment in timeline_snapshot or []
                )
                export_background, export_sfx, export_broll = filter_audio_sources_for_export(
                    background_audio_snapshot,
                    sound_effects_snapshot,
                    b_roll_snapshot,
                    muted_tracks_snapshot,
                    solo_tracks_snapshot,
                )
                main_audible = is_track_audible(MAIN_VIDEO_TRACK, set(muted_tracks_snapshot or ()), set(solo_tracks_snapshot or ()))
                require_audio = bool(main_audio_override_path or export_background or (export_sfx and timed_items_have_audio(export_sfx)) or (export_broll and timed_items_have_audio(export_broll)) or (main_audible and source_has_audio))
                self.audio_override_manager.validate_exported_video(render_path, expected_duration, require_audio=require_audio)
                save_path = finalize_rendered_output(render_path, save_path)
                partial_path = ""

            if media_kind != "audio" and len(timeline_snapshot) == 1:
                segment = timeline_snapshot[0]
                try:
                    source_duration = get_media_duration(segment.path)
                    speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
                    audio_volume = float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0)
                    full_source = (
                        abs(float(segment.start)) <= 0.05
                        and abs(float(segment.end) - source_duration) <= 0.08
                        and abs(speed - 1.0) <= 0.001
                        and abs(audio_volume - 1.0) <= 0.001
                    )
                    if full_source:
                        preserve_watermark_restoration_patch(save_path, segment.path)
                except Exception:
                    pass
            wx.CallAfter(self.OnSaveComplete, save_path, open_after_save, metadata_snapshot or {}, output_kind)
        except TimelineAudioVideoSaveBlocked as error:
            if partial_path and os.path.exists(partial_path):
                try:
                    os.remove(partial_path)
                except OSError:
                    pass
            wx.CallAfter(self.speak_timeline_audio_video_save_blocked, str(error))
        except Exception as error:
            if partial_path and os.path.exists(partial_path):
                try:
                    os.remove(partial_path)
                except OSError:
                    pass
            if is_operation_cancelled(error, lambda: self.save_cancelled):
                wx.CallAfter(self.OnSaveCancelled, save_path)
            else:
                wx.CallAfter(self.OnSaveError, str(error), output_kind)
        finally:
            if prepared_audio and prepared_audio.temp_dir:
                shutil.rmtree(prepared_audio.temp_dir, ignore_errors=True)

