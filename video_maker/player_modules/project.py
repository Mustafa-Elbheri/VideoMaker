from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerProjectMixin:
    def _project_wildcard(self):
        return f"{tr('ملفات مشروع البحيري')} (*{PROJECT_EXTENSION})|*{PROJECT_EXTENSION}"

    def _project_default_filename(self):
        base = os.path.splitext(os.path.basename(self.video_path or tr("مشروع جديد")))[0]
        return f"{base or tr('مشروع جديد')}{PROJECT_EXTENSION}"

    def _cancel_project_operation(self):
        if self.project_operation_cancel_event is None:
            return
        self.project_operation_cancel_event.set()
        operation = getattr(self, "_active_project_operation", "save")
        if operation == "save":
            self.say(tr("جاري إلغاء حفظ المشروع"))
        else:
            self.say(tr("جاري إلغاء استعادة المشروع"))

    def _update_project_progress(self, percent, operation):
        if not self.project_progress_dialog:
            return
        percent = max(0, min(100, int(percent)))
        self.project_progress_dialog.update_progress(percent)
        if percent >= self.last_spoken_project_percent + 10 or percent >= 100:
            self.last_spoken_project_percent = percent
            template = "نسبة حفظ المشروع {percent} بالمئة" if operation == "save" else "نسبة استعادة المشروع {percent} بالمئة"
            self.say(tr(template).format(percent=percent), interrupt=False)

    def _finish_project_operation(self, result_holder, status, value=None, error=None):
        result_holder["status"] = status
        result_holder["value"] = value
        result_holder["error"] = error
        dialog = self.project_progress_dialog
        if dialog:
            try:
                if dialog.IsModal():
                    dialog.EndModal(wx.ID_OK)
                else:
                    dialog.Hide()
            except Exception:
                pass

    def _run_project_operation(self, operation, worker):
        if self.project_operation_running:
            self.say(tr("انتظر حتى تنتهي عملية المشروع الحالية"))
            return "busy", None, None
        self.project_operation_running = True
        self._active_project_operation = operation
        self.project_operation_cancel_event = threading.Event()
        self.last_spoken_project_percent = 0
        result_holder = {"status": "error", "value": None, "error": ProjectError("unknown")}
        if operation == "save":
            title = tr("جاري حفظ المشروع")
            progress_template = tr("نسبة حفظ المشروع {percent} بالمئة")
            status_name = tr("حالة حفظ المشروع")
            gauge_name = tr("شريط تقدم حفظ المشروع")
            cancel_name = tr("إلغاء حفظ المشروع")
            cancelling_message = tr("جاري إلغاء حفظ المشروع")
        else:
            title = tr("جاري استعادة المشروع")
            progress_template = tr("نسبة استعادة المشروع {percent} بالمئة")
            status_name = tr("حالة استعادة المشروع")
            gauge_name = tr("شريط تقدم استعادة المشروع")
            cancel_name = tr("إلغاء استعادة المشروع")
            cancelling_message = tr("جاري إلغاء استعادة المشروع")
        self.project_progress_dialog = SaveProgressDialog(
            self,
            self._cancel_project_operation,
            title=title,
            progress_template=progress_template,
            status_name=status_name,
            gauge_name=gauge_name,
            cancel_name=cancel_name,
            cancelling_message=cancelling_message,
        )

        def progress(percent):
            wx.CallAfter(self._update_project_progress, percent, operation)

        def run_worker():
            try:
                value = worker(progress, self.project_operation_cancel_event)
                wx.CallAfter(self._finish_project_operation, result_holder, "success", value, None)
            except ProjectCancelled as error:
                wx.CallAfter(self._finish_project_operation, result_holder, "cancelled", None, error)
            except Exception as error:
                wx.CallAfter(self._finish_project_operation, result_holder, "error", None, error)

        thread = threading.Thread(target=run_worker, daemon=True)
        try:
            wx.CallAfter(thread.start)
            self.project_progress_dialog.ShowModal()
        finally:
            if self.project_progress_dialog:
                self.project_progress_dialog.Destroy()
            self.project_progress_dialog = None
            self.project_operation_cancel_event = None
            self.project_operation_running = False
            self._active_project_operation = ""
        return result_holder["status"], result_holder["value"], result_holder["error"]

    def _show_project_error(self, error, operation):
        key = project_error_text_key(error, operation)
        message = tr(key)
        detail = str(getattr(error, "detail", "") or "").strip()
        if detail and getattr(error, "code", "") in ("missing_asset", "asset_changed"):
            message = f"{message}: {detail}"
        # self.say(message)
        title = tr("تعذر حفظ المشروع") if operation == "save" else tr("تعذر استعادة المشروع")
        wx.MessageBox(message, title, wx.OK | wx.ICON_ERROR)

    def OnSaveProject(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if self.timeline_transform_progress_dialog is not None:
            self.say(tr("انتظر حتى ينتهي العمل الحالي"))
            return
        dialog = wx.FileDialog(
            self,
            tr("اختر مكان حفظ المشروع"),
            defaultFile=self._project_default_filename(),
            wildcard=self._project_wildcard(),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            project_path = ensure_project_extension(dialog.GetPath())
        finally:
            dialog.Destroy()
        try:
            snapshot = capture_project_snapshot(self)
        except Exception as error:
            self._show_project_error(error, "save")
            return
        status, value, error = self._run_project_operation(
            "save",
            lambda progress, cancel: save_project_file(project_path, snapshot, progress, cancel),
        )
        if status == "success":
            set_last_project_path(project_path)
            self.is_dirty = False
            self.say(tr("تم حفظ المشروع بنجاح"))
        elif status == "cancelled":
            self.say(tr("تم إلغاء حفظ المشروع"))
        elif status != "busy":
            self._show_project_error(error, "save")

    def _confirm_project_restore(self):
        has_history = hasattr(self, "edit_history") and self.edit_history.can_undo()
        if not self.is_dirty and not has_history:
            return True
        from video_maker.dialogs import confirm_exit_prompt
        return confirm_exit_prompt(self, message=tr("هناك تعديلات لم يتم حفظها. هل تريد استعادة مشروع آخر؟"))

    def restore_project_from_path(self, project_path, confirm_unsaved=True):
        """Restore one project path selected by a dialog or copied in Explorer."""
        if self.timeline_transform_progress_dialog is not None:
            self.say(tr("انتظر حتى ينتهي العمل الحالي"))
            return False
        if self.project_operation_running:
            self.say(tr("انتظر حتى تنتهي عملية المشروع الحالية"))
            return False
        if confirm_unsaved and not self._confirm_project_restore():
            return False
        project_path = os.path.abspath(str(project_path or ""))
        status, value, error = self._run_project_operation(
            "restore",
            lambda progress, cancel: restore_project_file(project_path, progress, cancel),
        )
        if status == "cancelled":
            self.say(tr("تم إلغاء استعادة المشروع"))
            return False
        if status != "success":
            if status != "busy":
                self._show_project_error(error, "restore")
            return False
        payload, extraction_root = value
        rollback_payload = capture_runtime_payload(self)
        previous_dirty = self.is_dirty
        old_temp_dirs = list(self.generated_temp_dirs)
        old_temp_files = list(self.generated_temp_files)
        try:
            self.generated_temp_dirs.append(extraction_root)
            self.restore_session_payload(payload)
            for temp_dir in old_temp_dirs:
                if temp_dir and os.path.abspath(temp_dir) != os.path.abspath(extraction_root):
                    self.cleanup_temp_dir(temp_dir)
            self.generated_temp_dirs = [extraction_root]
            self.generated_temp_files = []
            set_last_project_path(project_path)
            remember_recent_file(project_path)
            self.say(tr("تمت استعادة المشروع بنجاح"))
            return True
        except Exception as restore_error:
            try:
                self.restore_session_payload(rollback_payload)
                self.is_dirty = previous_dirty
                self.generated_temp_dirs = old_temp_dirs
                self.generated_temp_files = old_temp_files
            except Exception:
                pass
            if extraction_root in self.generated_temp_dirs:
                self.generated_temp_dirs.remove(extraction_root)
            shutil.rmtree(extraction_root, ignore_errors=True)
            self._show_project_error(restore_error, "restore")
            return False

    def OnRestoreProject(self, event=None):
        if self.timeline_transform_progress_dialog is not None:
            self.say(tr("انتظر حتى ينتهي العمل الحالي"))
            return
        if self.project_operation_running:
            self.say(tr("انتظر حتى تنتهي عملية المشروع الحالية"))
            return
        # Keep the existing menu behavior: ask before opening the file chooser,
        # while direct clipboard restore uses the same confirmation in the
        # path-based helper.
        if not self._confirm_project_restore():
            return
        dialog = wx.FileDialog(
            self,
            tr("اختر ملف المشروع"),
            wildcard=self._project_wildcard(),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            project_path = dialog.GetPath()
        finally:
            dialog.Destroy()
        self.restore_project_from_path(project_path, confirm_unsaved=False)

    def OnSaveSession(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        default_name = os.path.splitext(os.path.basename(self.video_path or "جلسة"))[0] or "جلسة"
        dialog = wx.TextEntryDialog(self, "اكتب اسم الجلسة الحالية", "حفظ جلسة العمل الحالية", default_name)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        name = dialog.GetValue().strip()
        dialog.Destroy()
        if not name:
            wx.MessageBox("اكتب اسم الجلسة أولا.", "اسم مطلوب", wx.OK | wx.ICON_INFORMATION)
            return
        if os.path.exists(session_dir_for_name(name)):
            result = wx.MessageBox("توجد جلسة بهذا الاسم. هل تريد استبدالها؟", "تأكيد الاستبدال", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            if result != wx.YES:
                return
        try:
            write_session(name, self)
            self.is_dirty = False
            # self.say("تم حفظ جلسة العمل")
            wx.MessageBox("تم حفظ جلسة العمل الحالية.", "تم الحفظ", wx.OK | wx.ICON_INFORMATION)
        except Exception as error:
            # self.say("تعذر حفظ جلسة العمل")
            wx.MessageBox(f"تعذر حفظ جلسة العمل: {error}", "خطأ", wx.OK | wx.ICON_ERROR)

    def OnRestoreSession(self, event=None):
        if self.is_dirty:
            result = wx.MessageBox("هناك تعديلات لم يتم حفظها. هل تريد استعادة جلسة أخرى؟", "تعديلات غير محفوظة", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            if result != wx.YES:
                return
        dialog = RestoreSessionDialog(self)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        session_path = dialog.selected_session
        dialog.Destroy()
        try:
            payload = read_session(session_path)
            self.restore_session_payload(payload)
            self.say("تمت استعادة جلسة العمل")
        except Exception as error:
            # self.say("تعذر استعادة جلسة العمل")
            wx.MessageBox(f"تعذر استعادة جلسة العمل: {error}", "خطأ", wx.OK | wx.ICON_ERROR)

    def OnRestoreCrashSession(self, event=None):
        if not crash_session_exists():
            wx.MessageBox("لا توجد جلسة إغلاق مفاجئ محفوظة.", "استعادة جلسة الإغلاق المفاجئ", wx.OK | wx.ICON_INFORMATION)
            return
        if self.is_dirty:
            result = wx.MessageBox("هناك تعديلات لم يتم حفظها. هل تريد استعادة جلسة الإغلاق المفاجئ؟", "تعديلات غير محفوظة", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            if result != wx.YES:
                return
        try:
            payload = read_crash_session()
            self.restore_session_payload(payload)
            self.say("تمت استعادة جلسة الإغلاق المفاجئ")
        except Exception as error:
            # self.say("تعذر استعادة جلسة الإغلاق المفاجئ")
            wx.MessageBox(f"تعذر استعادة جلسة الإغلاق المفاجئ: {error}", "خطأ", wx.OK | wx.ICON_ERROR)

    def restore_session_payload(self, payload):
        self.video_path = payload.get("video_path", "")
        self.media_kind = payload.get("media_kind", "video")
        saved_chroma_state = payload.get("chroma_render_state")
        self.chroma_render_state = dict(saved_chroma_state) if saved_chroma_state else None
        self.timeline = list(payload.get("timeline", []))
        if not self.timeline:
            raise RuntimeError("الجلسة لا تحتوي على خط زمني")
        self.current_time = min(float(payload.get("current_time", 0) or 0), total_duration(self.timeline))
        self.start_time = payload.get("start_time")
        self.end_time = payload.get("end_time")
        self.volume = persisted_program_volume(payload.get("volume", self.volume), self.volume)
        self.master_volume_db = persisted_master_volume_db(
            payload.get("master_volume_db", getattr(self, "master_volume_db", 0.0)),
            getattr(self, "master_volume_db", 0.0),
        )
        self.track_volumes_db = {
            str(key): persisted_track_volume_db(db)
            for key, db in (payload.get("track_volumes_db") or {}).items()
        }
        stored = int(payload.get("seek_step", self.seek_step) or self.seek_step)
        self.seek_step = max(MIN_SEEK_STEP, stored)
        self.cached_seek_step = self.seek_step
        self.file_metadata = dict(payload.get("metadata", {}))
        self.visual_items = [dict(item) for item in payload.get("visual_items", [])]
        self.background_audio_items = [dict(item) for item in payload.get("background_audio_items", [])]
        self.b_roll_items = [dict(item) for item in payload.get("b_roll_items", [])]
        self.sound_effects_items = [dict(item) for item in payload.get("sound_effects_items", [])]
        self.main_audio_override_path = str(payload.get("main_audio_override_path", "") or "")
        self.main_audio_override_duration = float(payload.get("main_audio_override_duration", 0.0) or 0.0)
        self.main_audio_override_timeline_duration = float(payload.get("main_audio_override_timeline_duration", 0.0) or 0.0)
        self.audio_override_manager.restore_state_payload(payload)
        restored_audio_changed = self.normalize_restored_main_audio_override()
        self.edit_points = normalize_edit_points(payload.get("edit_points", []))
        self.timeline = ensure_logical_file_metadata(self.timeline, self.video_path, self.edit_points)
        self.muted_tracks = set(payload.get("muted_tracks") or ())
        self.solo_tracks = set(payload.get("solo_tracks") or ())
        self.ripple_mode = normalize_ripple_mode(payload.get("ripple_mode", self.ripple_mode))
        self.selected_element_ids = set(payload.get("selected_element_ids") or ())
        self.focused_element = self._resolve_restored_focused_element(payload.get("focused_element"))
        if self.focused_element:
            element_start = float(item_bounds(self.focused_element)[0])
            self.current_time = min(max(0.0, element_start + 0.05), total_duration(self.timeline))
        # أعد حساب بصمة النصوص من العناصر المحمّلة عند الفتح (لا تُخزَّن مسبقاً).
        self._text_preview_fingerprint = ""
        self._text_preview_items = None
        self._text_preview_active_id = ""
        self.current_edit_point_id = payload.get("current_edit_point_id")
        self.work_images = list(payload.get("work_images", []))
        self.work_videos = list(payload.get("work_videos", []))
        self.default_image_duration = float(payload.get("default_image_duration", self.default_image_duration) or self.default_image_duration)
        self.transition_name = payload.get("transition_name", self.transition_name) or self.transition_name
        self.last_insert_end = payload.get("last_insert_end")
        if "window_name" in payload:
            self.window_name = str(payload.get("window_name", "") or "")
        self.current_segment_index = None
        self.active_media_path = ""
        self.invalidate_pending_media_load()
        self.playback_requested = True
        self.clipboard = []
        self.is_dirty = bool(restored_audio_changed)
        self.edit_history.clear()
        self.refresh_menu_bar()
        self.save_crash_session_now()
        self.load_timeline_time(self.current_time, True)

    def save_crash_session_now(self):
        """Synchronously commit a small, atomic recovery snapshot.

        The snapshot contains JSON references only, so completing the write on
        the UI thread is fast and removes the daemon-thread race that allowed a
        forced termination to happen before the recovery file existed.
        """
        if not self.has_video() or self.crash_save_running or self.closing:
            return False
        self.crash_save_running = True
        try:
            payload = build_crash_session_payload(self)
            if not payload:
                return False
            write_crash_session_payload(payload)
            self.last_crash_save_time = time.monotonic()
            return True
        except Exception:
            # Recovery must never interrupt editing. The next completed edit or
            # periodic timer attempts another atomic snapshot.
            return False
        finally:
            self.crash_save_running = False

    def OnMetadata(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        dialog = MetadataDialog(self, self.file_metadata)
        if dialog.ShowModal() == wx.ID_OK:
            before_state = self.capture_edit_state()
            self.file_metadata = dict(dialog.metadata)
            self.is_dirty = True
            self.record_edit("تعديل المعلومات", before_state)
            self.say("تم تحديث المعلومات")
        dialog.Destroy()

