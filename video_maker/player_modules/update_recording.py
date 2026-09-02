from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerUpdateRecordingMixin:
    def startup_check_for_updates(self):
        self.start_update_check(manual=False)

    def OnCheckForUpdates(self, event=None):
        self.start_update_check(manual=True)

    def start_update_check(self, manual=True):
        if self.update_check_running:
            if manual:
                self.update_check_manual_requested = True
                self.say(tr("جاري التحقق من وجود تحديثات"))
            return
        if self.show_pending_downloaded_update(manual):
            return
        self.update_check_running = True
        self.update_check_manual_requested = False
        if manual:
            self.say(tr("جاري التحقق من وجود تحديثات"))
        threading.Thread(target=self.check_updates_worker, args=(manual,), daemon=True).start()

    def show_pending_downloaded_update(self, manual=True):
        pending_update = find_pending_downloaded_update()
        if not pending_update:
            return False
        path = pending_update.get("downloaded_path")
        update_id = update_download_id(pending_update)
        local_install_id = update_install_id(path)
        if not manual and update_id and update_id == get_declined_update_install_id():
            delete_downloaded_update(path)
            return True
        if not manual and local_install_id and local_install_id == get_declined_update_install_id():
            delete_downloaded_update(path)
            return True
        self.prompt_install_downloaded_update(path, pending_update)
        return True

    def check_updates_worker(self, manual=True):
        try:
            update_info = check_for_update()
        except UpdateError as error:
            wx.CallAfter(
                self.finish_update_check,
                None,
                error.message,
                error.params,
                error.details,
                manual,
            )
            return
        except Exception as error:
            details = format_unexpected_error("update_check", error)
            wx.CallAfter(
                self.finish_update_check,
                None,
                "تعذر قراءة بيانات التحديث",
                {},
                details,
                manual,
            )
            return
        wx.CallAfter(self.finish_update_check, update_info, "", {}, "", manual)

    def finish_update_check(
        self,
        update_info,
        error_message="",
        error_params=None,
        error_details="",
        manual=True,
    ):
        self.update_check_running = False
        manual = bool(manual or self.update_check_manual_requested)
        self.update_check_manual_requested = False
        if error_message:
            self.show_update_error(error_message, error_params, error_details)
            return
        self.show_update_result(update_info, manual)

    def show_update_error(self, message, params=None, details=""):
        params = params or {}
        display_message = tr(message).format(**params)
        self.say(display_message)
        dialog = UpdateErrorDialog(self, display_message, details, self.say)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def show_update_result(self, update_info, manual=True):
        if not update_info.get("has_update"):
            if not manual:
                return
            message = tr("أنت تستخدم أحدث إصدار").format(version=update_info.get("current_version", ""))
            # self.say(message)
            wx.MessageBox(message, tr("تحديث البرنامج"), wx.OK | wx.ICON_INFORMATION)
            return
        latest = update_info.get("latest_version", "")
        current = update_info.get("current_version", "")
        asset_name = update_info.get("asset_name") or tr("غير متوفر")
        update_id = update_download_id(update_info)
        if not manual and update_id and update_id == get_declined_update_install_id():
            return
        downloaded_path = find_downloaded_update(update_info)
        if downloaded_path:
            self.prompt_install_downloaded_update(downloaded_path, update_info)
            return
        message = tr("يتوفر إصدار جديد {latest} والإصدار الحالي {current}\nملف التحديث {asset}\nهل تريد تنزيل التحديث الآن؟").format(
            latest=latest,
            current=current,
            asset=asset_name,
        )
        if not update_info.get("asset_url"):
            result = wx.MessageBox(
                tr("يتوفر إصدار جديد لكن لا يوجد ملف تحديث مباشر هل تريد فتح صفحة التحديث؟"),
                tr("تحديث البرنامج"),
                wx.YES_NO | wx.ICON_INFORMATION,
            )
            if result == wx.YES:
                self.open_external_target(update_info.get("release_url", ""), "تم فتح صفحة التحديث")
            return
        result = wx.MessageBox(message, tr("تحديث البرنامج"), wx.YES_NO | wx.ICON_INFORMATION)
        if result == wx.YES:
            self.start_update_download(update_info)

    def prompt_install_downloaded_update(self, path, update_info=None):
        update_info = update_info or {}
        latest = update_info.get("latest_version", "")
        asset_name = update_info.get("asset_name") or os.path.basename(str(path))
        message = tr("يوجد تحديث تم تنزيله ولم يتم تثبيته بعد\nالإصدار {latest}\nملف التحديث {asset}\nهل تريد تشغيل المثبت الآن؟ سيتم إغلاق البرنامج").format(
            latest=latest,
            asset=asset_name,
        )
        # self.say(tr("يوجد تحديث تم تنزيله ولم يتم تثبيته بعد"))
        result = wx.MessageBox(message, tr("تحديث البرنامج"), wx.YES_NO | wx.ICON_INFORMATION)
        if result != wx.YES:
            delete_downloaded_update(path)
            set_declined_update_install_id(update_download_id(update_info))
            return
        set_declined_update_install_id("")
        try:
            self.say(tr("جاري تثبيت التحديث"))
            run_update_file(path)
        except UpdateError as error:
            self.show_update_error(error.message, error.params, error.details)
            return
        self.OnClose(None)

    def start_update_download(self, update_info):
        self.update_cancel_requested = False
        self.last_spoken_update_percent = -10
        self.update_progress_dialog = SaveProgressDialog(
            self,
            self.cancel_update_download,
            title=tr("تحديث البرنامج"),
            progress_template=tr("نسبة تنزيل التحديث {percent} بالمئة"),
            status_name=tr("حالة تنزيل التحديث"),
            gauge_name=tr("شريط تقدم تنزيل التحديث"),
            cancel_label=tr("إلغاء"),
            cancel_name=tr("إلغاء تنزيل التحديث"),
            cancelling_message=tr("جاري إلغاء تنزيل التحديث"),
        )
        self.update_progress_dialog.Show()
        self.update_progress_dialog.focus_navigation_controls()
        self.say(tr("جاري تنزيل التحديث"))
        threading.Thread(target=self.download_update_worker, args=(update_info,), daemon=True).start()

    def cancel_update_download(self):
        self.update_cancel_requested = True
        if self.update_progress_dialog:
            try:
                self.update_progress_dialog.status.SetValue(tr("جاري إلغاء تنزيل التحديث"))
            except Exception:
                pass
        self.say(tr("جاري إلغاء تنزيل التحديث"))

    def update_download_progress(self, value):
        if not self.update_progress_dialog:
            return
        value = max(0, min(100, int(value)))
        self.update_progress_dialog.update_progress(value)
        if value >= self.last_spoken_update_percent + 10 or value >= 100:
            self.last_spoken_update_percent = value
            self.say(tr("نسبة تنزيل التحديث {percent} بالمئة").format(percent=value), interrupt=False)

    def download_update_worker(self, update_info):
        try:
            path = download_update(
                update_info,
                progress_callback=lambda value: wx.CallAfter(self.update_download_progress, value),
                cancel_callback=lambda: self.update_cancel_requested,
            )
        except UpdateError as error:
            wx.CallAfter(
                self.finish_update_download,
                None,
                update_info,
                error.message,
                error.params,
                error.details,
            )
            return
        except Exception as error:
            details = format_unexpected_error("update_download", error)
            wx.CallAfter(
                self.finish_update_download,
                None,
                update_info,
                "تعذر تنزيل التحديث. لم يتم إجراء أي تغيير على البرنامج ويمكنك المحاولة مرة أخرى.",
                {},
                details,
            )
            return
        wx.CallAfter(self.finish_update_download, path, update_info, "", {}, "")

    def finish_update_download(
        self,
        path,
        update_info=None,
        error_message="",
        error_params=None,
        error_details="",
    ):
        if self.update_progress_dialog:
            self.update_progress_dialog.Destroy()
            self.update_progress_dialog = None
        if self.update_cancel_requested or error_message == "تم إلغاء تنزيل التحديث":
            self.update_cancel_requested = False
            self.say(tr("تم إلغاء تنزيل التحديث"))
            return
        if error_message:
            self.show_update_error(error_message, error_params, error_details)
            return
        self.say(tr("تم تنزيل التحديث"))
        self.prompt_install_downloaded_update(path, update_info)

    def read_seek_step(self):
        return read_seek_step()

    def write_seek_step(self, seek_step):
        write_seek_step(seek_step)

    def register_recording_hotkeys(self):
        if not hasattr(self, "RegisterHotKey"):
            return
        if self.global_recording_hotkeys:
            return
        shortcuts = [
            ("start_screen_recording", wx.MOD_CONTROL | wx.MOD_ALT, wx.WXK_F9, self.OnStartPreparedScreenRecording),
            ("pause_recording", wx.MOD_CONTROL | wx.MOD_ALT, wx.WXK_F7, self.OnPauseResumeRecording),
            ("stop_recording", wx.MOD_CONTROL | wx.MOD_ALT, wx.WXK_F8, self.OnStopRecording),
            ("pause_recording_plain", wx.MOD_CONTROL, wx.WXK_F7, self.OnPauseResumeRecording),
            ("stop_recording_plain", wx.MOD_CONTROL, wx.WXK_F8, self.OnStopRecording),
            ("toggle_broadcast_global", wx.MOD_CONTROL, wx.WXK_F12, self.OnToggleBroadcast),
        ]
        for name, modifiers, key, handler in shortcuts:
            hotkey_id = wx.NewIdRef()
            try:
                registered = self.RegisterHotKey(int(hotkey_id), modifiers, key)
            except Exception:
                registered = False
            if registered:
                self.global_recording_hotkeys[name] = int(hotkey_id)
                self.Bind(wx.EVT_HOTKEY, handler, id=int(hotkey_id))

    def unregister_recording_hotkeys(self):
        for hotkey_id in list(self.global_recording_hotkeys.values()):
            try:
                self.UnregisterHotKey(hotkey_id)
            except Exception:
                pass
        self.global_recording_hotkeys = {}

    def claim_recording_hotkeys(self):
        for window in self.open_program_windows():
            if window is self:
                continue
            try:
                window.unregister_recording_hotkeys()
            except Exception:
                pass
        self.unregister_recording_hotkeys()
        self.register_recording_hotkeys()

    def recording_is_active(self):
        return bool(self.recording_session and getattr(self.recording_session, "running", False))

    def OnRecordAudio(self, event=None):
        if self.recording_is_active() or self.recording_finalizing or self.recording_start_pending:
            self.say("التسجيل يعمل بالفعل")
            return
        dialog = RecordingSettingsDialog(self, "audio")
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            options = dialog.options()
        finally:
            dialog.Destroy()
        self.queue_recording_start(options, "بدأ التسجيل الصوتي")

    def OnPrepareScreenRecording(self, event=None):
        if self.recording_is_active() or self.recording_finalizing or self.recording_start_pending:
            self.say("التسجيل يعمل بالفعل")
            return
        dialog = RecordingSettingsDialog(self, "screen")
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            self.prepared_screen_recording_options = dialog.options()
        finally:
            dialog.Destroy()
        self.claim_recording_hotkeys()
        self.Hide()
        self.say("تم تجهيز تسجيل الشاشة اضغط كنترول ألت إف تسعة للبدء")

    def OnStartPreparedScreenRecording(self, event=None):
        if self.recording_is_active() or self.recording_finalizing or self.recording_start_pending:
            self.say("التسجيل يعمل بالفعل")
            return
        if not self.prepared_screen_recording_options:
            self.say("جهز تسجيل الشاشة أولا")
            return
        self.queue_recording_start(self.prepared_screen_recording_options, "بدأ تسجيل الشاشة")

    def queue_recording_start(self, options, message):
        if self.recording_is_active() or self.recording_finalizing or self.recording_start_pending:
            self.say("التسجيل يعمل بالفعل")
            return
        self.recording_start_pending = True
        self.say(message)
        self.recording_start_timer = wx.CallLater(RECORDING_ANNOUNCEMENT_DELAY_MS, self.start_recording, options)

    def start_recording(self, options):
        self.recording_start_timer = None
        if not self.recording_start_pending:
            return
        self.recording_start_pending = False
        if self.recording_is_active() or self.recording_finalizing:
            self.say("التسجيل يعمل بالفعل")
            return
        try:
            session = make_recording_session(options)
            session.start()
        except Exception as error:
            if getattr(options, "mode", "") == "screen":
                self.Show()
                self.Raise()
                self.SetFocus()
            # self.say("تعذر بدء التسجيل")
            wx.MessageBox(f"{tr('تعذر بدء التسجيل')}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        self.recording_session = session
        self.recording_mode = options.mode
        self.close_after_recording = False
        self.recording_saved_during_close = None
        wx.CallLater(700, self.check_recording_started, session)

    def check_recording_started(self, session):
        if session is not self.recording_session:
            return
        if getattr(session, "error", ""):
            message = session.error
            self.recording_session = None
            self.recording_mode = ""
            self.recording_start_pending = False
            self.recording_start_timer = None
            if session.options.mode == "screen":
                self.Show()
                self.Raise()
                self.SetFocus()
            # self.say("تعذر بدء التسجيل")
            wx.MessageBox(f"{tr('تعذر بدء التسجيل')}: {message}", tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnPauseResumeRecording(self, event=None):
        session = self.recording_session
        if not self.recording_is_active():
            self.say("لا يوجد تسجيل يعمل", wait_for_ui=False)
            return
        if getattr(session, "paused", False):
            try:
                if session.resume():
                    self.say("تم استئناف التسجيل")
            except Exception as error:
                # self.say("تعذر استئناف التسجيل")
                wx.MessageBox(f"{tr('تعذر استئناف التسجيل')}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        try:
            if session.pause():
                self.say("تم إيقاف التسجيل مؤقتا")
        except Exception as error:
            # self.say("تعذر إيقاف التسجيل مؤقتا")
            wx.MessageBox(f"{tr('تعذر إيقاف التسجيل مؤقتا')}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnStopRecording(self, event=None):
        if self.recording_finalizing:
            return
        if self.recording_start_pending:
            timer = self.recording_start_timer
            self.recording_start_timer = None
            self.recording_start_pending = False
            if timer:
                try:
                    timer.Stop()
                except Exception:
                    pass
            self.say("لا يوجد تسجيل يعمل", wait_for_ui=False)
            return
        session = self.recording_session
        if not self.recording_is_active():
            self.say("لا يوجد تسجيل يعمل", wait_for_ui=False)
            return
        self.recording_finalizing = True
        self.say("جاري إنهاء التسجيل")
        threading.Thread(target=self.finish_recording_worker, args=(session,), daemon=True).start()

    def finish_recording_worker(self, session):
        try:
            path = session.stop()
        except Exception as error:
            wx.CallAfter(self.finish_recording, session, "", str(error))
            return
        wx.CallAfter(self.finish_recording, session, path, "")

    def finish_recording(self, session, path, error_message):
        if session is not self.recording_session:
            return
        mode = self.recording_mode
        close_after = bool(self.close_after_recording)
        self.recording_session = None
        self.recording_mode = ""
        self.recording_finalizing = False
        self.recording_start_pending = False
        self.recording_start_timer = None
        if mode == "screen":
            self.Show()
            self.Raise()
            self.SetFocus()
            self.prepared_screen_recording_options = None
        if error_message:
            self.close_after_recording = False
            if close_after:
                message = tr("تعذر حفظ التسجيل قبل إغلاق البرنامج: {error}").format(error=error_message)
                # self.say(tr("تعذر حفظ التسجيل قبل إغلاق البرنامج"))
                wx.MessageBox(message, tr("خطأ في حفظ التسجيل"), wx.OK | wx.ICON_ERROR)
            else:
                # self.say("تعذر إنهاء التسجيل")
                wx.MessageBox(f"{tr('تعذر إنهاء التسجيل')}: {error_message}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        if close_after:
            self.close_after_recording = False
            self.recording_saved_during_close = (path, mode)
            message = tr("تم حفظ التسجيل وإيقافه لأنك طلبت إغلاق البرنامج.\nمسار الملف: {path}").format(path=path)
            # self.say(tr("تم حفظ التسجيل قبل الخروج"))
            wx.MessageBox(message, tr("تم حفظ التسجيل قبل الخروج"), wx.OK | wx.ICON_INFORMATION)
            wx.CallAfter(self.continue_close_after_recording)
            return
        appended = self.open_or_append_recording(path, mode)
        if mode == "screen":
            self.say("تم تسجيل الشاشة وإضافتها إلى الخط الزمني" if appended else "تم تسجيل الشاشة وفتح الفيديو")
        else:
            self.say("تم تسجيل الصوت وإضافته إلى الخط الزمني" if appended else "تم تسجيل الصوت وفتحه")

    def continue_close_after_recording(self):
        """إكمال طلب الخروج بعد حفظ التسجيل خارج خيط واجهة المستخدم."""
        saved_recording = self.recording_saved_during_close
        self.recording_saved_during_close = None
        self._continue_close(None, saved_recording=saved_recording)

    def open_or_append_recording(self, path, mode):
        recorded_kind = "video" if mode == "screen" or has_video_stream(path) else "audio"
        if not self.timeline or self.media_kind != recorded_kind:
            self.OnOpenMedia(path)
            self.playback_requested = False
            self.pending_play = False
            self.selected_playback_range = None
            self.skipped_playback_range = None
            self.current_time = 0.0
            self.load_timeline_time(0.0, False)
            return False
        duration = get_video_duration(path) if recorded_kind == "video" else get_media_duration(path)
        if duration <= 0:
            self.OnOpenMedia(path)
            self.playback_requested = False
            self.pending_play = False
            self.selected_playback_range = None
            self.skipped_playback_range = None
            self.current_time = 0.0
            self.load_timeline_time(0.0, False)
            return False
        before_state = self.capture_edit_state()
        insert_time = self.timeline_duration()
        self.timeline = insert_segments(self.timeline, insert_time, [new_file_segment(path, 0.0, duration)])
        self.add_edit_point("video" if recorded_kind == "video" else "audio", insert_time, insert_time + duration, "timeline", mode="insert")
        self.current_time = insert_time
        self.start_time = None
        self.end_time = None
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.playback_requested = False
        self.pending_play = False
        self.is_dirty = True
        self.record_edit("إضافة تسجيل إلى الخط الزمني", before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        return True

    def _veto_close_event(self, event):
        if event and hasattr(event, "Veto"):
            try:
                event.Veto()
            except Exception:
                pass

    def _cancel_pending_recording_start_for_close(self):
        if not self.recording_start_pending:
            return False
        timer = self.recording_start_timer
        self.recording_start_timer = None
        self.recording_start_pending = False
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        self.say(tr("تم إلغاء بدء التسجيل لأنك طلبت إغلاق البرنامج"))
        return True

    def _defer_close_for_recording(self, event):
        """حفظ التسجيل فقط عند إغلاق نافذة صانع الفيديو نفسها، وليس عند Alt+F4 في برنامج آخر."""
        self._cancel_pending_recording_start_for_close()
        if self.recording_finalizing:
            self.close_after_recording = True
            self._veto_close_event(event)
            self.say(tr("سيتم إغلاق البرنامج بعد اكتمال حفظ التسجيل"))
            return True
        session = self.recording_session
        if not self.recording_is_active() or session is None:
            return False
            
        if self.recording_mode == "screen" and not self.close_requested_by_new_instance:
            result = wx.MessageBox(
                tr("هناك تسجيل شاشة قيد الإجراء حالياً، هل أنت متأكد أنك تريد إنهاء التسجيل وإغلاق البرنامج؟"),
                tr("تأكيد الخروج"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
            )
            if result != wx.YES:
                self._veto_close_event(event)
                return True
                
        self.close_after_recording = True
        self.recording_finalizing = True
        self._veto_close_event(event)
        self.say(tr("جاري حفظ التسجيل قبل إغلاق البرنامج"))
        threading.Thread(target=self.finish_recording_worker, args=(session,), daemon=True).start()
        return True

    def OnClose(self, event=None):
        # يصل هذا المسار من EVT_CLOSE الخاص بهذه النافذة فقط؛ لا يوجد Alt+F4 عام أثناء تسجيل الشاشة.
        trace_event(
            "window",
            "close.request",
            immediate=True,
            window=self.window_number,
            dirty=self.is_dirty,
            media_kind=self.media_kind,
            media_path=self.video_path,
            timeline_items=len(self.timeline),
            recording_active=self.recording_is_active(),
            recording_finalizing=self.recording_finalizing,
        )
        if self.closing:
            return
        if self._defer_close_for_recording(event):
            return
        self._continue_close(event)

    def _continue_close(self, event=None, saved_recording=None):
        """متابعة الإغلاق العادي بعد ضمان عدم وجود تسجيل غير محفوظ."""
        has_history = hasattr(self, "edit_history") and self.edit_history.can_undo()
        if self.is_dirty or has_history:
            from video_maker.dialogs import confirm_exit_prompt
            if not confirm_exit_prompt(self):
                trace_event("window", "close.cancelled_by_user", immediate=True, window=self.window_number, dirty=self.is_dirty)
                self._veto_close_event(event)
                # إذا تراجع المستخدم عن الخروج بعد حفظ التسجيل، أضفه إلى المشروع المفتوح.
                if saved_recording:
                    path, mode = saved_recording
                    try:
                        self.open_or_append_recording(path, mode)
                    except Exception as error:
                        wx.MessageBox(
                            tr("تم حفظ التسجيل لكن تعذر إضافته إلى المشروع: {error}").format(error=error),
                            tr("تنبيه"),
                            wx.OK | wx.ICON_WARNING,
                        )
                return
        self._perform_close()

    def _perform_close(self):
        last_window = not self.other_open_program_windows()
        self.playback_requested = False
        self.closing = True
        self.crash_timer.Stop()
        if self.volume_save_call:
            try:
                self.volume_save_call.Stop()
            except Exception:
                pass
            self.volume_save_call = None
        if self.master_volume_save_call:
            try:
                self.master_volume_save_call.Stop()
            except Exception:
                pass
            self.master_volume_save_call = None
        set_volume(self.volume)
        set_master_volume_db(getattr(self, "master_volume_db", 0.0))
        if self.allow_crash_session_clear and last_window:
            clear_crash_session()
        if self.media_ctrl.GetState() == MEDIASTATE_PLAYING:
            self.media_ctrl.Stop()
        self.stop_original_audio_playback()
        self.stop_background_audio_playback()
        self.unregister_recording_hotkeys()
        single_instance_guard = self.detach_single_instance_guard()
        remove_ui_heartbeat(self)
        self.unregister_program_window()
        remaining_windows = self.open_program_windows()
        if single_instance_guard and remaining_windows:
            try:
                remaining_windows[-1].attach_single_instance_guard(single_instance_guard)
            except Exception as error:
                trace_event(
                    "application",
                    "single_instance.guard_transfer_failed",
                    level="WARNING",
                    window=getattr(remaining_windows[-1], "window_number", ""),
                    error=str(error),
                )
        if self.allow_crash_session_clear and not last_window:
            self.refresh_crash_session_from_open_window()
        if remaining_windows:
            try:
                remaining_windows[-1].register_recording_hotkeys()
            except Exception:
                pass
        self.Destroy()
        print("Application closed.")

        if last_window:
            deleted_path = delete_seek_step_file()
            if deleted_path:
                print(f"Deleted temporary file: {deleted_path}")
        self.cleanup_generated_files()
        trace_event("window", "close.complete", immediate=True, window=self.window_number, last_window=last_window)
        flush_problem_log()

    def shutdown_playback_controls_for_destroy(self):
        if getattr(self, "_playback_controls_shutdown", False):
            return
        self._playback_controls_shutdown = True
        self.playback_requested = False
        self.pending_play = False
        scrub_player = getattr(self, "scrub_player", None)
        if scrub_player is not None:
            try:
                scrub_player.shutdown()
            except Exception:
                pass
        try:
            self.stop_original_audio_playback(wait=True)
        except Exception:
            pass
        try:
            self.stop_background_audio_playback(wait=True)
        except Exception:
            pass
        media_ctrl = getattr(self, "media_ctrl", None)
        if media_ctrl is not None:
            try:
                if media_ctrl.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
                    media_ctrl.Stop()
            except Exception:
                pass
            destroy_player = getattr(media_ctrl, "_destroy_mpv_player", None)
            if callable(destroy_player):
                try:
                    destroy_player()
                except Exception:
                    pass

    def Destroy(self):
        self.shutdown_playback_controls_for_destroy()
        return super().Destroy()

    def cleanup_temp_dir(self, temp_dir):
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    def cleanup_generated_files(self):
        self.clear_audio_visual_preview()
        for temp_dir in self.generated_temp_dirs:
            self.cleanup_temp_dir(temp_dir)
        self.generated_temp_dirs = []
        self.generated_temp_files = []


    def OnToggleBroadcast(self, event=None):
        if hasattr(self, "broadcast_manager") and self.broadcast_manager.is_broadcasting:
            self.OnStopBroadcast(event)
        else:
            # Force the main window to the front so the dialog can gain focus
            import wx
            if self.IsIconized():
                self.Iconize(False)
            self.Raise()
            
            # Using CallAfter ensures the UI has time to come to the front before blocking with ShowModal
            wx.CallAfter(self.OnStartBroadcast, event)
