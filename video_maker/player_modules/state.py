from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerStateMixin:
    def require_open_file(self):
        if self.has_video():
            return True
        self.say(speech_messages.NO_OPEN_FILE, wait_for_ui=False)
        return False

    def notify_failed_edit_restored(self, operation, error=None, context="edit_rollback"):
        message = tr("لم يثبت التعديل وتمت استعادة مساحة العمل كما كانت")
        self.say(message)
        append_problem(
            context,
            f"{tr(operation)}. {message}" if operation else message,
            exception=error if isinstance(error, BaseException) else None,
            details=str(error or ""),
        )

    def capture_edit_state(self):
        return {
            "timeline": list(self.timeline),
            "media_kind": self.media_kind,
            "video_path": self.video_path,
            "visual_items": list(self.visual_items),
            "background_audio_items": list(self.background_audio_items),
            "b_roll_items": list(self.b_roll_items),
            "sound_effects_items": list(self.sound_effects_items),
            "main_audio_override_path": self.main_audio_override_path,
            "main_audio_override_duration": self.main_audio_override_duration,
            "main_audio_override_timeline_duration": self.main_audio_override_timeline_duration,
            "main_audio_effect_chain": list(self.main_audio_effect_chain),
            "main_audio_revision": self.main_audio_revision,
            "main_audio_source_revision": self.main_audio_source_revision,
            "timeline_revision": self.timeline_revision,
            "main_audio_format_version": self.main_audio_format_version,
            "edit_points": list(self.edit_points),
            "current_edit_point_id": self.current_edit_point_id,
            "current_time": self.current_time,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "last_insert_end": self.last_insert_end,
            "file_metadata": dict(self.file_metadata),
            "is_dirty": self.is_dirty,
            "chroma_render_state": dict(self.chroma_render_state) if self.chroma_render_state else None,
            "focused_element": dict(self.focused_element) if self.focused_element else None,
            "selected_element_ids": set(self.selected_element_ids),
            "element_clipboard": copy.deepcopy(self.element_clipboard),
            "muted_tracks": set(getattr(self, "muted_tracks", set())),
            "solo_tracks": set(getattr(self, "solo_tracks", set())),
            "track_volumes_db": dict(getattr(self, "track_volumes_db", {}) or {}),
            "ripple_mode": getattr(self, "ripple_mode", "per_track"),
        }

    def record_edit(self, operation, before_state, audio_policy="auto"):
        """تسجيل تعديل واحد مضمون في سجل التراجع، مع تحديث صوتي غير إتلافي.

        يُسجَّل الإدخال في سجل التراجع دائماً بصرف النظر عن نجاح تحديث الصوت
        البديل، بحيث يبقى "تراجع" متاحاً بعد أي تعديل (قصّ، حذف، لصق...). فشل
        تحديث الصوت لا يلغي التعديل ولا يمنع تسجيله؛ يُكتفى بإشعار غير قطعي.
        """
        reconcile_error = None
        try:
            reconcile = self.audio_override_manager.reconcile_after_timeline_edit(
                before_state,
                operation,
                audio_policy=audio_policy,
            )
            if reconcile.changed:
                if reconcile.temp_dir and reconcile.temp_dir not in self.generated_temp_dirs:
                    self.generated_temp_dirs.append(reconcile.temp_dir)
                if reconcile.path and reconcile.path not in self.generated_temp_files:
                    self.generated_temp_files.append(reconcile.path)
        except Exception as error:
            reconcile_error = error
        self.is_dirty = True
        if self.edit_history.record(operation, before_state, self.capture_edit_state()):
            if wx.IsMainThread():
                wx.CallLater(1500, self.update_edit_history_menu)
            else:
                wx.CallAfter(self.update_edit_history_menu)
        if reconcile_error is not None:
            self._report_reconcile_failure(operation, reconcile_error)
        # Persist the completed edit before control returns to the user. The
        # periodic timer remains a fallback for cursor/selection-only changes.
        self.save_crash_session_now()
        return True

    def _report_reconcile_failure(self, operation, error=None):
        """يُخطِر بأن التعديل ثُبّت لكن تعذّر تحديث الصوت البديل (بدون إلغاء)."""
        message = tr("تم تنفيذ التعديل، لكن تعذر تحديث الصوت البديل لهذا التعديل")
        self.say(message, wait_for_ui=False)
        append_problem(
            "main_audio_override_reconcile",
            f"{tr(operation)}. {message}",
            exception=error if isinstance(error, BaseException) else None,
            details=str(error or ""),
        )

    def clear_edit_history(self):
        self.edit_history.clear()
        self.update_edit_history_menu()

    def history_item_label(self, action):
        if action == "undo":
            operation = self.edit_history.next_undo_operation()
            label = tr("تراجع")
            shortcut = "Ctrl+Z"
        else:
            operation = self.edit_history.next_restore_operation()
            label = tr("استعادة")
            shortcut = "Ctrl+Y"
        if operation:
            label = f"{label}: {tr(operation)}"
        return f"{label}\t{shortcut}"

    def update_edit_history_menu(self):
        if self.undo_menu_item:
            self.undo_menu_item.SetItemLabel(self.history_item_label("undo"))
            self.undo_menu_item.Enable(self.edit_history.can_undo())
        if self.restore_menu_item:
            self.restore_menu_item.SetItemLabel(self.history_item_label("restore"))
            self.restore_menu_item.Enable(self.edit_history.can_restore())

    def apply_edit_state(self, state, focus_timeline=True):
        if not wx.IsMainThread():
            return call_on_wx_main_thread(self.apply_edit_state, state)
        was_playing = self.playback_requested
        self.timeline = list(state["timeline"])
        self.media_kind = str(state.get("media_kind", self.media_kind if self.timeline else "none") or "none")
        self.video_path = str(state.get("video_path", self.timeline[0].path if self.timeline else "") or "")
        self.visual_items = [dict(item) for item in state["visual_items"]]
        self.background_audio_items = [dict(item) for item in state.get("background_audio_items", [])]
        self.b_roll_items = [dict(item) for item in state.get("b_roll_items", [])]
        self.sound_effects_items = [dict(item) for item in state.get("sound_effects_items", [])]
        self.main_audio_override_path = str(state.get("main_audio_override_path", "") or "")
        self.main_audio_override_duration = float(state.get("main_audio_override_duration", 0.0) or 0.0)
        self.main_audio_override_timeline_duration = float(state.get("main_audio_override_timeline_duration", 0.0) or 0.0)
        self.audio_override_manager.restore_state_payload(state)
        self.edit_points = normalize_edit_points(state.get("edit_points", []))
        self.timeline = ensure_logical_file_metadata(self.timeline, self.video_path, self.edit_points)
        self.current_edit_point_id = state.get("current_edit_point_id")
        if self.timeline:
            self.current_time = min(float(state["current_time"]), self.timeline_duration())
        elif get_program_mode() == PROFESSIONAL_MODE:
            bg_items = getattr(self, "background_audio_items", None) or []
            self.current_time = min(float(state["current_time"]),
                                    max(0.0, *(float(it.get("end", 0) or 0) for it in bg_items))) if bg_items else 0
        else:
            self.current_time = 0
        self.start_time = state["start_time"]
        self.end_time = state["end_time"]
        self.last_insert_end = state["last_insert_end"]
        self.file_metadata = dict(state["file_metadata"])
        self.is_dirty = bool(state["is_dirty"])
        saved_chroma_state = state.get("chroma_render_state")
        self.chroma_render_state = dict(saved_chroma_state) if saved_chroma_state else None
        saved_focused = state.get("focused_element")
        self.focused_element = dict(saved_focused) if saved_focused else None
        self.selected_element_ids = set(state.get("selected_element_ids") or ())
        self.element_clipboard = copy.deepcopy(state.get("element_clipboard"))
        self.muted_tracks = set(state.get("muted_tracks") or ())
        self.solo_tracks = set(state.get("solo_tracks") or ())
        self.track_volumes_db = dict(state.get("track_volumes_db") or {})
        self.ripple_mode = state.get("ripple_mode", self.ripple_mode)
        self.current_segment_index = None
        self.active_media_path = ""
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.invalidate_pending_media_load()
        self.refresh_menu_bar()
        self._update_track_list()
        self.ensure_audio_visual_preview()
        self.request_preview_rebuild()
        if self.timeline:
            self.load_timeline_time(self.current_time, was_playing)
        else:
            self.reload_current_position()
        if not focus_timeline:
            wx.CallAfter(self.SetFocus)

    def announce_history_change(self, restored, operation):
        message = history_feedback_message(
            restored,
            operation,
            self.edit_history.undo_count(),
            self.edit_history.restore_count(),
        )
        self.speech.say(message, interrupt=True, wait_for_ui=False)

    def OnUndoEdit(self, event=None):
        if not self.require_open_file():
            return
        result = self.edit_history.undo()
        if not result:
            self.say("لا توجد عملية للتراجع عنها", wait_for_ui=False)
            return
        operation, state = result
        self.apply_edit_state(state)
        self.announce_history_change(False, operation)

    def OnRestoreEdit(self, event=None):
        if not self.require_open_file():
            return
        result = self.edit_history.restore()
        if not result:
            self.say("لا توجد عملية لاستعادتها", wait_for_ui=False)
            return
        operation, state = result
        self.apply_edit_state(state)
        self.announce_history_change(True, operation)

    def InitUI(self):
        panel = wx.Panel(self)
        set_accessible_label(panel, "مساحة العمل")
        self.main_panel = panel
        hbox = wx.BoxSizer(wx.HORIZONTAL)

        media_vbox = wx.BoxSizer(wx.VERTICAL)
        self.media_ctrl = MPVMediaCtrl(panel, style=wx.SIMPLE_BORDER)
        silence_media_control_accessibility(self.media_ctrl)
        media_vbox.Add(self.media_ctrl, 1, flag=wx.EXPAND | wx.ALL, border=5)

        self.track_list = wx.ListBox(panel, style=wx.LB_SINGLE | wx.BORDER_SIMPLE)
        set_accessible_label(self.track_list, "التراكات")
        self.track_list.SetMinSize((200, -1))
        self.track_list.Hide()

        hbox.Add(media_vbox, 1, flag=wx.EXPAND)
        hbox.Add(self.track_list, 0, flag=wx.EXPAND | wx.ALL, border=5)

        panel.SetSizer(hbox)

        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(wx.EVT_CONTEXT_MENU, self.OnContextMenu)
        self.media_ctrl.Bind(wx.EVT_CONTEXT_MENU, self.OnContextMenu)
        self.media_ctrl.Bind(wx.EVT_SET_FOCUS, self.OnMediaCtrlFocus)
        self.Bind(EVT_MEDIA_LOADED, self.OnMediaLoaded, self.media_ctrl)
        self.Bind(EVT_MEDIA_FINISHED, self.OnMediaFinished, self.media_ctrl)
        self.SetSize((800, 600))
        self.update_window_title()
        self.Centre()
        self.shortcut_ids = install_shortcuts(self)
        install_menu_bar(self)
        self.register_recording_hotkeys()
        self.apply_current_theme()
        wx.CallAfter(self.SetFocus)

    def OnMediaCtrlFocus(self, event):
        wx.CallAfter(self.SetFocus)

    def open_program_windows(self):
        result = []
        for window in list(OPEN_PLAYER_WINDOWS):
            if window is None:
                continue
            try:
                if window.IsBeingDeleted():
                    continue
            except Exception:
                pass
            result.append(window)
        return result

    def register_program_window(self):
        if self not in OPEN_PLAYER_WINDOWS:
            OPEN_PLAYER_WINDOWS.append(self)
        self.update_all_window_titles()

    def unregister_program_window(self):
        if self in OPEN_PLAYER_WINDOWS:
            OPEN_PLAYER_WINDOWS.remove(self)
        self.update_all_window_titles()

    def attach_single_instance_guard(self, guard):
        if self.single_instance_poll_call:
            try:
                self.single_instance_poll_call.Stop()
            except Exception:
                pass
        self.single_instance_guard = guard
        try:
            hwnd = self.GetHandle()
        except Exception:
            hwnd = 0
        try:
            guard.write_owner_state(hwnd)
        except Exception:
            pass
        self.schedule_single_instance_poll()

    def detach_single_instance_guard(self):
        if self.single_instance_poll_call:
            try:
                self.single_instance_poll_call.Stop()
            except Exception:
                pass
        self.single_instance_poll_call = None
        guard = self.single_instance_guard
        self.single_instance_guard = None
        return guard

    def schedule_single_instance_poll(self):
        if not self.single_instance_guard or self.closing:
            return
        self.single_instance_poll_call = wx.CallLater(500, self.poll_single_instance_request)

    def poll_single_instance_request(self):
        self.single_instance_poll_call = None
        guard = self.single_instance_guard
        if not guard or self.closing:
            return
        try:
            request = guard.close_request_for_this_instance()
        except Exception as error:
            trace_event("application", "single_instance.poll_error", level="WARNING", window=self.window_number, error=str(error))
            request = {}
        if request:
            trace_event(
                "application",
                "single_instance.replace_requested",
                immediate=True,
                window=self.window_number,
                requester_pid=request.get("requester_pid"),
            )
            self.close_all_windows_for_single_instance_takeover()
            return
        self.schedule_single_instance_poll()

    def close_all_windows_for_single_instance_takeover(self):
        if self.single_instance_takeover_closing:
            return
        self.single_instance_takeover_closing = True
        windows = list(reversed(self.open_program_windows()))
        for window in windows:
            window.close_requested_by_new_instance = True
            window.single_instance_takeover_closing = True
        for window in windows:
            try:
                if not window.closing:
                    window.Close()
            except Exception as error:
                trace_event(
                    "application",
                    "single_instance.window_close_failed",
                    level="WARNING",
                    window=getattr(window, "window_number", ""),
                    error=str(error),
                )

    def other_open_program_windows(self):
        return [window for window in self.open_program_windows() if window is not self]

    def update_all_window_titles(self):
        for window in self.open_program_windows():
            try:
                window.update_window_title()
            except Exception:
                pass

    def display_window_name(self):
        return self.window_name.strip() or tr("النافذة رقم {number}").format(number=self.window_number)

    def application_display_name(self):
        return get_custom_app_name() or tr(APP_TITLE)

    def update_window_title(self):
        title = self.application_display_name()
        if self.window_name.strip() or len(self.open_program_windows()) > 1:
            title = f"{title} - {self.display_window_name()}"
        # Updating a wx frame with the same title can still emit an
        # accessibility name-change event. During startup this method is called
        # once while building the UI and again when registering the window,
        # which makes NVDA announce the unchanged application title twice.
        # Only write properties whose value actually changed.
        try:
            current_title = self.GetTitle()
        except Exception:
            current_title = None
        if current_title != title:
            self.SetTitle(title)

        try:
            current_name = self.GetName()
        except Exception:
            current_name = None
        if current_name != title:
            self.SetName(title)

    def OnOpen(self, event=None):
        media_paths = ask_media_paths()
        if media_paths:
            print("Loading video, please wait...")
            self.OnOpenMediaPaths(media_paths)

    def OnOpenMediaPaths(self, media_paths):
        paths = [path for path in media_paths or [] if path]
        if not paths:
            try:
                from video_maker.app_state import get_startup_action, get_last_project_path
                action = get_startup_action()
                if action == 1:
                    if crash_session_exists():
                        try:
                            payload = read_crash_session()
                            self.restore_session_payload(payload)
                            self.say(tr("تمت استعادة جلسة العمل السابقة تلقائياً"))
                        except Exception:
                            pass
                elif action == 2:
                    last_proj = get_last_project_path()
                    if last_proj and os.path.exists(last_proj):
                        wx.CallAfter(self.restore_project_from_path, last_proj, confirm_unsaved=False)
            except Exception:
                pass
            return
        if len(paths) == 1:
            self.OnOpenMedia(paths[0])
            return
        kinds = [self.open_media_kind(path) for path in paths]
        if all(kind == "video" for kind in kinds):
            self.StartVideoClipMerge(paths)
            return
        if all(kind == "audio" for kind in kinds):
            self.StartAudioClipMerge(paths)
            return
        wx.MessageBox(
            tr("اختيار ملفات متعددة يعمل مع ملفات من النوع نفسه: فيديو فقط أو صوت فقط."),
            tr("اختيار غير متوافق"),
            wx.OK | wx.ICON_ERROR,
        )

    def open_media_kind(self, media_path):
        if self.is_image_file(media_path):
            return "image"
        if has_video_stream(media_path):
            return "video"
        return "audio"

    def OnOpenMedia(self, media_path):
        trace_event("media", "open.request", window=self.window_number, path=media_path, source="application")
        if self.is_image_file(media_path):
            self.OnOpenImage(media_path)
            return
        if has_video_stream(media_path):
            self.OnOpenVideo(media_path)
        else:
            self.OnOpenAudio(media_path)

    def is_image_file(self, path):
        return os.path.splitext(path)[1].lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

    def OnOpenImage(self, image_path):
        dialog = wx.TextEntryDialog(self, "اكتب مدة عرض الصورة بالثواني", "فتح صورة", str(self.default_image_duration))
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        value = dialog.GetValue().strip()
        dialog.Destroy()
        try:
            duration = float(value)
        except ValueError:
            wx.MessageBox("اكتب رقما صحيحا أو عشريا أكبر من صفر.", "قيمة غير صحيحة", wx.OK | wx.ICON_ERROR)
            return
        if duration <= 0:
            wx.MessageBox("اكتب رقما أكبر من صفر.", "قيمة غير صحيحة", wx.OK | wx.ICON_ERROR)
            return
        temp_dir = tempfile.mkdtemp(prefix="opened_image_")
        output_file = os.path.join(temp_dir, "image.mp4")
        try:
            create_video_from_image(image_path, duration, output_file)
            self.OnOpenGeneratedVideo(output_file, temp_dir)
            self.video_path = image_path
            image_file_id = new_logical_file_id()
            self.timeline = [segment_with_file_identity(segment, image_file_id, display_file_name(image_path)) for segment in self.timeline]
            remember_recent_file(image_path)
            self.say("تم فتح الصورة")
        except Exception as error:
            shutil.rmtree(temp_dir, ignore_errors=True)
            # self.say("تعذر فتح الصورة")
            wx.MessageBox(f"تعذر فتح الصورة: {error}", "خطأ", wx.OK | wx.ICON_ERROR)

    def OnOpenVideo(self, video_path):
        trace_event("media", "video_open.start", window=self.window_number, path=video_path)
        self.stop_background_audio_playback()
        self.clear_audio_visual_preview()
        self.video_path = video_path
        self.media_kind = "video"
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
        duration = get_video_duration(video_path)
        self.timeline = [new_file_segment(video_path, 0, duration)]
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
        self.is_dirty = False
        self.edit_history.clear()
        self.refresh_menu_bar()
        # Create the first recovery point immediately. Previously the first
        # snapshot waited for the three-second timer and could be lost if the
        # process was terminated before its daemon writer committed the file.
        self.save_crash_session_now()
        self.load_timeline_time(0, True)
        print(f"Opened video: {self.video_path}")
        trace_event(
            "media",
            "video_open.complete",
            window=self.window_number,
            path=self.video_path,
            duration=duration,
            timeline_items=len(self.timeline),
            dirty=self.is_dirty,
        )
        if not getattr(self, "_suppress_recent_file_once", False):
            remember_recent_file(video_path)
        self.say(speech_messages.OPENED_VIDEO)

    def OnOpenAudio(self, audio_path):
        trace_event("media", "audio_open.start", window=self.window_number, path=audio_path)
        self.stop_background_audio_playback()
        self.clear_audio_visual_preview()
        self.video_path = audio_path
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
        duration = get_media_duration(audio_path)
        self.timeline = [new_file_segment(audio_path, 0, duration)]
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
        self.is_dirty = False
        self.edit_history.clear()
        self.refresh_menu_bar()
        self.save_crash_session_now()
        self.load_timeline_time(0, True)
        trace_event(
            "media",
            "audio_open.complete",
            window=self.window_number,
            path=self.video_path,
            duration=duration,
            timeline_items=len(self.timeline),
            dirty=self.is_dirty,
        )
        remember_recent_file(audio_path)
        self.say("تم فتح الصوت")

    def OnOpenRecentFile(self, path):
        open_recent_file(self, path)

    def OnClearRecentFiles(self, event=None):
        clear_recent_files()
        self.refresh_menu_bar()
        self.say(tr("تم تفريغ قائمة الملفات الأخيرة"))

    def refresh_menu_bar(self):
        self.update_window_title()
        install_menu_bar(self)
        self.apply_current_theme()
        wx.CallAfter(self.ensure_audio_visual_preview)

    def apply_current_theme(self):
        apply_theme(self)

    def OnNewProgramWindow(self, event=None):
        window = self.__class__(None)
        window.Show()
        window.Raise()
        window.SetFocus()
        self.say(tr("تم فتح {name}").format(name=window.display_window_name()))

    def OnRenameProgramWindow(self, event=None):
        dialog = wx.TextEntryDialog(
            self,
            tr("اكتب اسم النافذة"),
            tr("تسمية النافذة"),
            self.display_window_name(),
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            name = dialog.GetValue().strip()
        finally:
            dialog.Destroy()
        if not name:
            self.say(tr("اكتب اسم النافذة أولا"))
            return
        self.window_name = name
        self.update_window_title()
        self.say(tr("تمت تسمية النافذة {name}").format(name=self.display_window_name()))

    def OnNextProgramWindow(self, event=None, reverse=False):
        windows = self.open_program_windows()
        if len(windows) <= 1:
            self.say(tr("لا توجد نافذة أخرى"))
            return
        try:
            current_index = windows.index(self)
        except ValueError:
            current_index = 0
        step = -1 if reverse else 1
        target = windows[(current_index + step) % len(windows)]
        target.Show()
        target.Raise()
        target.SetFocus()
        target.say(tr("أنت الآن في {name}").format(name=target.display_window_name()))

    def OnPreviousProgramWindow(self, event=None):
        self.OnNextProgramWindow(event, True)

    def refresh_crash_session_from_open_window(self):
        for window in reversed(self.open_program_windows()):
            if window is self:
                continue
            try:
                if window.has_video() and window.save_crash_session_now():
                    return True
            except Exception:
                pass
        clear_crash_session()
        return False

    def OnOpenGeneratedVideo(self, video_path, temp_dir):
        self._suppress_recent_file_once = True
        try:
            self.OnOpenVideo(video_path)
        finally:
            self._suppress_recent_file_once = False
        self.generated_temp_dirs.append(temp_dir)
        self.generated_temp_files.append(video_path)
        self.is_dirty = True
        self.save_crash_session_now()

    def OnClearWorkspace(self, event=None):
        if not self.require_open_file():
            return
        self.playback_requested = False
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.invalidate_pending_media_load()
        try:
            if self.media_ctrl.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
                self.media_ctrl.Stop()
        except Exception:
            pass
        self.stop_original_audio_playback()
        self.stop_background_audio_playback()
        self.video_path = ""
        self.media_kind = "none"
        self.chroma_render_state = None
        self.timeline = []
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
        self.current_time = 0
        self.current_segment_index = None
        self.active_media_path = ""
        self.start_time = None
        self.end_time = None
        self.clipboard = []
        self.file_metadata = {}
        self.is_dirty = False
        self.edit_history.clear()
        self.save_cancelled = False
        self.merge_cancelled = False
        self.clear_audio_visual_preview()
        self.cleanup_generated_files()
        self.refresh_crash_session_from_open_window()
        self.refresh_menu_bar()
        self.media_ctrl.Refresh()
        self.main_panel.Refresh()
        self.say(speech_messages.WORKSPACE_CLEARED, wait_for_ui=False)

