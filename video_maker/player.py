from video_maker.player_modules.shared import *
from video_maker.player_modules.state import PlayerStateMixin
from video_maker.player_modules.preview import PlayerPreviewMixin
from video_maker.player_modules.navigation_audio import PlayerNavigationAudioMixin
from video_maker.player_modules.timeline_edit import PlayerTimelineEditMixin
from video_maker.player_modules.save import PlayerSaveMixin
from video_maker.player_modules.project import PlayerProjectMixin
from video_maker.player_modules.media_insert import PlayerMediaInsertMixin
from video_maker.player_modules.audio_effects import PlayerAudioEffectMixin
from video_maker.player_modules.progress_context import PlayerProgressContextMixin
from video_maker.player_modules.professional import PlayerProfessionalMixin
from video_maker.player_modules.update_recording import PlayerUpdateRecordingMixin
from video_maker.player_modules.logical_media import PlayerLogicalMediaMixin


class VideoPlayer(
    PlayerStateMixin,
    PlayerPreviewMixin,
    PlayerNavigationAudioMixin,
    PlayerTimelineEditMixin,
    PlayerSaveMixin,
    PlayerProjectMixin,
    PlayerMediaInsertMixin,
    PlayerAudioEffectMixin,
    PlayerProgressContextMixin,
    PlayerProfessionalMixin,
    PlayerUpdateRecordingMixin,
    PlayerLogicalMediaMixin,
    wx.Frame,
):
    @property
    def is_dirty(self):
        return bool(getattr(self, "_is_dirty", False))

    @is_dirty.setter
    def is_dirty(self, value):
        new_value = bool(value)
        old_value = getattr(self, "_is_dirty", None)
        self._is_dirty = new_value
        if old_value is not None and old_value != new_value:
            log_project_state_change(self, "is_dirty", old_value, new_value)

    def __init__(self, *args, **kw):
        # Keep the normal caption, resize, minimize, maximize and close styles,
        # but do not expose the native Windows "System" menu as an extra menu
        # to screen-reader users.
        args = list(args)
        trace_event("startup", "frame_construction_start", immediate=True, arguments=args, keyword_arguments=kw)
        if len(args) >= 6:
            args[5] = args[5] & ~wx.SYSTEM_MENU
        else:
            style = kw.get("style", wx.DEFAULT_FRAME_STYLE)
            kw["style"] = style & ~wx.SYSTEM_MENU
        super(VideoPlayer, self).__init__(*args, **kw)
        self.Freeze()
        self.Maximize(True)
        from video_maker.ui_sounds import play_startup_sound
        wx.CallAfter(play_startup_sound)
        trace_event("startup", "native_frame_created")

        global PROGRAM_WINDOW_SEQUENCE
        PROGRAM_WINDOW_SEQUENCE += 1
        self.window_number = PROGRAM_WINDOW_SEQUENCE
        trace_event("window", "created", window=self.window_number, arguments=args, keyword_arguments=kw)
        self.window_name = ""
        self.video_path = ""
        self.media_kind = "none"
        self.chroma_render_state = None
        self.timeline = []
        self.visual_items = []
        self.background_audio_items = []
        self.b_roll_items = []
        self.sound_effects_items = []
        self.current_track = DEFAULT_TRACK
        self.focused_element = None
        self.selected_element_ids = set()
        self.ripple_mode = get_ripple_mode("per_track")
        self.element_clipboard = None
        self.muted_tracks = set()
        self.solo_tracks = set()
        self.track_volumes_db = {}
        self.main_audio_override_path = ""
        self.main_audio_override_duration = 0.0
        self.main_audio_override_timeline_duration = 0.0
        self.main_audio_effect_chain = []
        self.main_audio_revision = 0
        self.main_audio_source_revision = 0
        self.timeline_revision = 0
        self.main_audio_format_version = MainAudioOverrideManager.FORMAT_VERSION
        self.main_audio_override_operation_running = False
        self.edit_points = []
        self.current_edit_point_id = None
        self.work_images = []
        self.work_videos = []
        self.default_image_duration = 5
        self.transition_name = TRANSITIONS[0]
        self.last_insert_end = None
        self.current_time = 0
        self.current_segment_index = None
        self.active_media_path = ""
        self.pending_seek_ms = None
        self.pending_play = False
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.media_ctrl_volume_cache = None
        self.media_ctrl_rate_cache = None
        self.media_load_generation = 0
        self.pending_media_load_checks = 0
        self.pending_seamless_media_switch = False
        self.pending_continuous_audio_preserved = False
        self.use_reliable_audio = reliable_audio_available()
        self.original_audio_player = ReliableAudioPlayer() if self.use_reliable_audio else None
        self.scrub_player = ScrubPlayer() if reliable_audio_available() else None
        self.pending_main_audio_override_effect_paths = set()
        self.pending_audio_override_transform_metadata = {}
        self.background_audio_players = {}
        self.background_audio_durations = {}
        self.background_audio_duration_probes = set()
        self.audio_stream_cache = {}
        self.audio_effect_background_preview_state = None
        self.audio_effect_background_preview_timer = None
        self.playback_requested = False
        self.playback_return_position = None
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.is_dirty = False
        self.paused = False
        self.start_time = None
        self.end_time = None
        self.clipboard = []
        self.seek_step = read_seek_step()
        self.cached_seek_step = self.seek_step
        self.normal_seek_step = read_normal_seek_step()
        self.pixels_per_second = read_pixels_per_second()
        self.volume = normalized_program_volume(get_volume())
        self.master_volume_db = persisted_master_volume_db(get_master_volume_db())
        self.master_volume_save_call = None
        self.volume_save_call = None
        self.progress_dialog = None
        self.save_operation_running = False
        self.save_cancelled = False
        self.last_spoken_save_percent = -10
        self.project_progress_dialog = None
        self.project_operation_cancel_event = None
        self.project_operation_running = False
        self.last_spoken_project_percent = -10
        self.merge_progress_dialog = None
        self.merge_cancelled = False
        self.generated_temp_dirs = []
        self.generated_temp_files = []
        self.file_metadata = {}
        self.allow_crash_session_clear = True
        self.last_crash_save_time = 0
        self.crash_save_running = False
        self._captions_running = False
        self.closing = False
        self.speech = ScreenReaderSpeech()
        self.edit_history = EditHistory(100)
        self.audio_override_manager = MainAudioOverrideManager(
            self,
            duration_reader=get_media_duration,
            audio_stream_checker=has_audio_stream,
            video_stream_checker=has_video_stream,
            timeline_audio_writer=write_timeline_audio,
        )
        self.audio_override_manager.initialize_player_state()
        self.undo_menu_item = None
        self.restore_menu_item = None
        self.update_progress_dialog = None
        self.update_cancel_requested = False
        self.last_spoken_update_percent = -10
        self.update_check_running = False
        self.update_check_manual_requested = False
        self.timeline_transform_progress_dialog = None
        self.timeline_transform_cancelled = False
        self.last_spoken_transform_percent = -10
        self.audio_visual_preview_path = ""
        self.audio_visual_preview_temp_dir = ""
        self.audio_visual_preview_signature = None
        self.audio_visual_preview_rendering_signature = None
        self.audio_visual_preview_generation = 0
        self._text_preview_fingerprint = ""
        self._text_preview_items = None
        self._text_preview_active_id = ""
        self._preview_bitmap = None
        self._text_preview_base_path = ""
        self._text_preview_rebuild_call = None
        # A save requested while a timeline transform is still rendering must
        # never capture the old timeline. It is queued and opened only after
        # the transformed media has been committed to the timeline.
        self.pending_save_after_transform = False
        self.speed_preview_state = None
        self.last_playback_sync_time = 0.0
        self.timeline_boundaries_cache_signature = None
        self.timeline_boundaries_cache = []
        self.timeline_positions_cache = [0.0]
        self.timeline_duration_cache = 0.0
        self.recording_session = None
        self.recording_mode = ""
        self.recording_finalizing = False
        self.recording_start_pending = False
        self.recording_start_timer = None
        # إغلاق البرنامج أثناء التسجيل يتم على مرحلتين: حفظ التسجيل ثم إكمال الإغلاق.
        self.close_after_recording = False
        self.recording_saved_during_close = None
        self.text_overlay_running = False
        self._clipboard_paste_running = False
        self.prepared_screen_recording_options = None
        self.global_recording_hotkeys = {}
        self.single_instance_guard = None
        self.single_instance_poll_call = None
        self.close_requested_by_new_instance = False
        self.single_instance_takeover_closing = False
        self.startup_name_announced = False

        trace_event("startup", "init_ui_start")
        try:
            self.InitUI()
        finally:
            self.Thaw()
        trace_event("startup", "init_ui_finished")
        self.register_program_window()
        trace_event("startup", "window_registered", window=self.window_number)
        self._runtime_diagnostics_started = False
        self.Bind(wx.EVT_SHOW, self._on_first_show_for_diagnostics)

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnTimer, self.timer)
        self.timer.Start(25)
        self.crash_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnCrashSaveTimer, self.crash_timer)
        self.crash_timer.Start(3000)
        threading.Thread(target=ensure_user_effects, daemon=True).start()
        # Shell integration must never participate in constructing the main
        # window.  Open command-line files shortly after the event loop starts,
        # then register Explorer integration only after the application is
        # already usable.  Both paths are isolated so a Windows/installer issue
        # cannot terminate startup.
        wx.CallLater(250, self._open_startup_file_arguments)
        wx.CallLater(300, self._sync_track_list_visibility)
        wx.CallLater(3000, self._start_windows_shell_integration)
        wx.CallLater(1500, self.startup_check_for_updates)
        trace_event("startup", "frame_construction_finished", window=self.window_number)

    def _on_first_show_for_diagnostics(self, event):
        event.Skip()
        try:
            shown = bool(event.IsShown())
        except Exception:
            shown = bool(self.IsShown()) if hasattr(self, "IsShown") else False
        if not shown or self.closing or self._runtime_diagnostics_started:
            return
        self._runtime_diagnostics_started = True
        wx.CallAfter(self.announce_startup_name)
        # EVT_SHOW proves that wx created and exposed the frame.  Only now may
        # diagnostics install global hooks that could otherwise obstruct startup.
        wx.CallAfter(self._enable_runtime_diagnostics_after_startup)

    def announce_startup_name(self):
        if self.closing or self.startup_name_announced or self.window_number != 1:
            return
        self.startup_name_announced = True
        try:
            self.Raise()
            self.SetFocus()
        except Exception:
            pass
        self.say(self.application_display_name(), wait_for_ui=False)

    def _enable_runtime_diagnostics_after_startup(self):
        if self.closing:
            return
        trace_event(
            "startup",
            "main_window_first_show",
            window=self.window_number,
            shown=bool(self.IsShown()) if hasattr(self, "IsShown") else None,
        )
        enable_runtime_diagnostics()
        trace_event("startup", "main_window_event_loop_ready", window=self.window_number)

    def _start_windows_shell_integration(self):
        global SHELL_INTEGRATION_STARTED
        if SHELL_INTEGRATION_STARTED or self.closing:
            return
        SHELL_INTEGRATION_STARTED = True

        # Resolve localized labels on the UI thread.  The worker imports the
        # optional Windows-only module lazily, after the main window is alive.
        app_name = self.application_display_name()
        project_type_name = tr("مشروع صانع الفيديو")
        try:
            threading.Thread(
                target=self._ensure_windows_shell_integration,
                args=(app_name, project_type_name),
                daemon=True,
            ).start()
        except Exception as error:
            self._log_windows_shell_integration_error(error)

    def _ensure_windows_shell_integration(self, app_name, project_type_name):
        try:
            from video_maker.windows_shell_integration import ensure_windows_shell_integration

            ensure_windows_shell_integration(
                app_name=app_name,
                project_type_name=project_type_name,
            )
        except Exception as error:
            self._log_windows_shell_integration_error(error)

    @staticmethod
    def _log_windows_shell_integration_error(error):
        try:
            append_problem(
                "windows_shell_integration",
                "تعذر تسجيل صانع الفيديو في Send to أو ربط ملفات المشروع",
                exception=error,
            )
        except Exception:
            # Explorer integration is optional.  Even logging its failure must
            # never be able to affect the running application.
            pass

    def _open_startup_file_arguments(self):
        global STARTUP_FILE_ARGUMENTS_HANDLED
        trace_event(
            "windows_shell",
            "startup_arguments.check",
            window=self.window_number,
            already_handled=STARTUP_FILE_ARGUMENTS_HANDLED,
            closing=self.closing,
        )
        if STARTUP_FILE_ARGUMENTS_HANDLED or self.closing:
            return
        STARTUP_FILE_ARGUMENTS_HANDLED = True

        try:
            from video_maker.windows_shell_integration import startup_file_arguments

            paths = startup_file_arguments()
            trace_event("windows_shell", "startup_arguments.found", window=self.window_number, count=len(paths), paths=paths)
        except Exception as error:
            self._log_windows_shell_integration_error(error)
            return
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

        try:
            self.Show()
            self.Raise()
            self.SetFocus()
        except Exception:
            pass
        for index, path in enumerate(paths):
            if self.closing:
                trace_event("windows_shell", "startup_file.skipped", level="WARNING", window=self.window_number, path=path, reason="window_closing")
                break
            trace_event("windows_shell", "startup_file.open.start", window=self.window_number, index=index, path=path, timeline_items=len(self.timeline))
            try:
                # A media file sent to a newly opened window is an opened
                # source, not an edit.  Opening it through the clipboard paste
                # path marked the project dirty and caused a false unsaved-
                # changes warning on exit.  Additional startup files still use
                # paste semantics so they are appended as real edits.
                if (
                    not self.timeline
                    and not path.lower().endswith(PROJECT_EXTENSION)
                    and not self.is_image_file(path)
                ):
                    trace_event("windows_shell", "startup_file.mode", window=self.window_number, path=path, mode="clean_open")
                    self.OnOpenMedia(path)
                else:
                    trace_event("windows_shell", "startup_file.mode", window=self.window_number, path=path, mode="paste_or_restore")
                    paste_file_path(self, path)
                trace_event("windows_shell", "startup_file.open.complete", window=self.window_number, path=path, dirty=self.is_dirty, media_kind=self.media_kind, timeline_items=len(self.timeline))
            except Exception as error:
                trace_event(
                    "windows_shell",
                    "startup_file.open.error",
                    level="ERROR",
                    immediate=True,
                    window=self.window_number,
                    path=path,
                    error_type=type(error).__name__,
                    error=str(error),
                )
                try:
                    append_problem(
                        "startup_file_open",
                        "تعذر فتح الملف المرسل إلى صانع الفيديو",
                        exception=error,
                        details=str(path),
                    )
                except Exception:
                    pass

    def say(self, text, interrupt=True, wait_for_ui=True):
        from video_maker.app_state import get_speech_mode, get_speech_custom_settings
        from video_maker.speech_messages import categorize_speech

        mode = get_speech_mode()
        if mode == "disable":
            return
        
        if mode == "custom":
            category = categorize_speech(text)
            if category:
                settings = get_speech_custom_settings()
                if not settings.get(category, True):
                    return

        translated_text = tr(text)
        trace_event(
            "speech",
            "request",
            window=self.window_number,
            text=translated_text,
            interrupt=interrupt,
            wait_for_ui=wait_for_ui,
        )
        self.speech.say(translated_text, interrupt, wait_for_ui)


    def OnStartBroadcast(self, event):
        from video_maker.broadcasting_dialog import BroadcastSettingsDialog
        from video_maker.broadcasting.broadcast_manager import BroadcastManager
        from video_maker.localization import tr
        import wx
        
        dlg = BroadcastSettingsDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            options = dlg.get_options()
            dlg.Destroy()
            
            if not hasattr(self, "broadcast_manager"):
                self.broadcast_manager = BroadcastManager()
            
            if self.broadcast_manager.is_broadcasting:
                wx.MessageBox(tr("البث قيد التشغيل بالفعل."), tr("تنبيه"), wx.OK | wx.ICON_INFORMATION)
                return
                
            success = self.broadcast_manager.start_broadcast(
                source_type=options["source_type"],
                file_path=options.get("file_path"),
                window_title=options.get("window_title"),
                window_pid=options.get("window_pid"),
                audio_source=options.get("audio_source"),
                selected_apps=options.get("selected_apps", []),
                external_mic_name=options.get("external_mic_name")
            )
            
            if success:
                self.say(tr("بدأ البث بنجاح! يرجى التأكد من إضافة CABLE Output كمصدر صوت في برنامج التسجيل الخاص بك كـ OBS لتتمكن من سماع صوت التطبيقات."))
            else:
                wx.MessageBox(tr("خطأ في بدء البث"), tr("خطأ"), wx.OK | wx.ICON_ERROR)
        else:
            dlg.Destroy()

    def OnStopBroadcast(self, event):
        import wx
        from video_maker.localization import tr
        if hasattr(self, "broadcast_manager") and self.broadcast_manager.is_broadcasting:
            self.broadcast_manager.stop_broadcast()
            self.say(tr("تم إيقاف البث بنجاح."))
        else:
            self.say(tr("لا يوجد بث قيد التشغيل حالياً."))
