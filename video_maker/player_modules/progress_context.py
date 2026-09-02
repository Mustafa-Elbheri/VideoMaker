from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerProgressContextMixin:
    def CreateProgressDialog(self, media_kind="video"):
        existing_dialog = getattr(self, "progress_dialog", None)
        if existing_dialog:
            try:
                existing_dialog.Show()
                update = getattr(existing_dialog, "Update", None)
                if callable(update):
                    update()
            except Exception:
                pass
            return
        if media_kind == "audio":
            self.progress_dialog = SaveProgressDialog(
                self,
                self.cancel_save,
                title=tr("جاري حفظ الصوت"),
                status_name=tr("حالة حفظ الصوت"),
                gauge_name=tr("شريط تقدم حفظ الصوت"),
                cancel_name=tr("إلغاء حفظ الصوت"),
            )
        else:
            self.progress_dialog = SaveProgressDialog(self, self.cancel_save)
        self.progress_dialog.Show()
        update = getattr(self.progress_dialog, "Update", None)
        if callable(update):
            update()
        current_percent = getattr(self, "save_progress_percent", 0)
        if current_percent:
            self.progress_dialog.update_progress(current_percent)

    def CreateSplitProgressDialog(self, media_kind="video"):
        existing_dialog = getattr(self, "progress_dialog", None)
        if existing_dialog:
            try:
                existing_dialog.Show()
                update = getattr(existing_dialog, "Update", None)
                if callable(update):
                    update()
            except Exception:
                pass
            return
        self.progress_dialog = SaveProgressDialog(
            self,
            self.cancel_save,
            title=tr("جاري حفظ التقسيم"),
            progress_template=tr("نسبة حفظ التقسيم {percent} بالمئة"),
            status_name=tr("حالة حفظ التقسيم"),
            gauge_name=tr("شريط تقدم حفظ التقسيم"),
            cancel_name=tr("إلغاء حفظ التقسيم"),
            cancelling_message=tr("جاري إلغاء حفظ التقسيم"),
        )
        self.progress_dialog.Show()
        update = getattr(self.progress_dialog, "Update", None)
        if callable(update):
            update()
        current_percent = getattr(self, "save_progress_percent", 0)
        if current_percent:
            self.progress_dialog.update_progress(current_percent)

    def reset_save_progress_state(self):
        self.save_progress_percent = 0.0
        self.last_spoken_save_percent = -10

    def UpdateProgressDialog(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        value = max(0.0, min(100.0, value))
        current_percent = max(0.0, min(100.0, float(getattr(self, "save_progress_percent", 0.0) or 0.0)))
        if value < current_percent:
            value = current_percent
        else:
            self.save_progress_percent = value
        if self.progress_dialog:
            self.progress_dialog.update_progress(value)
        if value >= self.last_spoken_save_percent + 10 or (value >= 100 and self.last_spoken_save_percent < 100):
            self.last_spoken_save_percent = value
            trace_event(
                "save",
                "progress",
                window=self.window_number,
                value=int(value),
                operation=getattr(self, "_diagnostic_active_operation", ""),
                cancelled=self.save_cancelled,
            )
            self.say(speech_messages.SAVE_PROGRESS.format(percent=int(value)), interrupt=False)

    def DestroyProgressDialog(self):
        if self.progress_dialog:
            self.progress_dialog.Destroy()
            self.progress_dialog = None

    def cancel_save(self):
        trace_event(
            "save",
            "cancel.request",
            level="WARNING",
            immediate=True,
            window=self.window_number,
            operation=getattr(self, "_diagnostic_active_operation", ""),
            current_percent=self.last_spoken_save_percent,
        )
        self.save_cancelled = True
        self.say(speech_messages.SAVE_CANCELLING)

    def OnSaveComplete(self, save_path, open_after_save=True, metadata_snapshot=None, media_kind="video"):
        self.save_operation_running = False
        trace_event("save", "complete", window=self.window_number, media_kind=media_kind, destination=save_path)
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        self.UpdateProgressDialog(100)
        self.DestroyProgressDialog()
        if media_kind == "audio":
            self.say(speech_messages.AUDIO_SAVED)
            message = tr("تم حفظ الصوت بنجاح")
        else:
            # self.say(speech_messages.VIDEO_SAVED)
            message = tr("تم حفظ الفيديو بنجاح")
        wx.MessageBox(message, tr("نجاح"), wx.OK | wx.ICON_INFORMATION)
        print(f"Media saved to: {save_path}")
        if open_after_save:
            self.OnOpenMedia(save_path)
            self.file_metadata = dict(metadata_snapshot or {})

    def OnSaveCancelled(self, save_path):
        self.save_operation_running = False
        trace_event("save", "cancelled", level="WARNING", immediate=True, window=self.window_number, destination=save_path)
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        self.DestroyProgressDialog()
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass
        self.say(speech_messages.SAVE_CANCELLED)

    def OnSaveError(self, error_message, media_kind="video"):
        self.save_operation_running = False
        trace_event("save", "error", level="ERROR", immediate=True, window=getattr(self, "window_number", 0), media_kind=media_kind, error=error_message)
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        self.DestroyProgressDialog()
        if media_kind == "audio":
            self.say(speech_messages.AUDIO_SAVE_FAILED)
            message = tr("تعذر حفظ الصوت: {error}").format(error=error_message)
        else:
            # self.say(speech_messages.SAVE_FAILED)
            message = tr("تعذر حفظ الفيديو: {error}").format(error=error_message)
        wx.MessageBox(message, tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnSplitSaveComplete(self, saved_paths, media_kind="video"):
        self.save_operation_running = False
        trace_event("save", "split_complete", window=self.window_number, media_kind=media_kind, outputs=len(saved_paths or []))
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        self.UpdateProgressDialog(100)
        self.DestroyProgressDialog()
        count = len(saved_paths or [])
        message = tr_format("تم حفظ التقسيم في {count} ملف", count=count)
        # self.say(message)
        wx.MessageBox(message, tr("نجاح"), wx.OK | wx.ICON_INFORMATION)

    def OnSplitSaveCancelled(self, saved_paths):
        self.save_operation_running = False
        trace_event("save", "split_cancelled", level="WARNING", immediate=True, window=self.window_number, outputs=len(saved_paths or []))
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        self.DestroyProgressDialog()
        for path in saved_paths or []:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.say(tr("تم إلغاء حفظ التقسيم"))

    def OnSplitSaveError(self, error_message, saved_paths, media_kind="video"):
        self.save_operation_running = False
        trace_event("save", "split_error", level="ERROR", immediate=True, window=self.window_number, media_kind=media_kind, error=error_message, outputs=len(saved_paths or []))
        self._diagnostic_active_operation = ""
        self._diagnostic_operation_started = 0.0
        self.DestroyProgressDialog()
        for path in saved_paths or []:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        # self.say(tr("تعذر حفظ التقسيم"))
        wx.MessageBox(tr_format("تعذر حفظ التقسيم: {error}", error=error_message), tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnTimer(self, event):
        if not hasattr(self, '_timer_tick'):
            self._timer_tick = 0
        self._timer_tick += 1
        if self._timer_tick % 3 == 0:
            note_ui_heartbeat(
                self,
                playback_requested=self.playback_requested,
                transform_active=bool(self.timeline_transform_progress_dialog),
                transform_cancelled=self.timeline_transform_cancelled,
                transform_percent=self.last_spoken_transform_percent,
                save_running=self.save_operation_running,
                project_operation_running=self.project_operation_running,
                recording_active=self.recording_is_active(),
                active_operation=getattr(self, "_diagnostic_active_operation", ""),
                operation_elapsed_seconds=(
                    round(time.monotonic() - getattr(self, "_diagnostic_operation_started", time.monotonic()), 3)
                    if getattr(self, "_diagnostic_operation_started", 0.0)
                    else 0.0
                ),
            )
        if self.audio_effect_background_preview_state:
            self.sync_audio_effect_background_preview()
            
        # Do not update current_time from hardware if we are currently debouncing a seek
        if getattr(self, "seek_debounce_call", None):
            return
            
        if not self.playback_requested:
            return

        override_clock_active = bool(
            self.has_main_audio_override()
            and self.use_reliable_audio
            and self.original_audio_player
            and self.original_audio_player.GetState() == MEDIASTATE_PLAYING
            and self.original_audio_player.path == self.main_audio_override_path
        )

        # أثناء تحميل المقطع المرئي التالي يظل الصوت يعمل. نحافظ على الزمن
        # العام من ساعة الصوت بدل تجميد المؤشر أو إعادته عند اكتمال التحميل.
        if self.pending_seek_ms is not None:
            if override_clock_active and self.pending_continuous_audio_preserved:
                self.current_time = audio_clock_time(self.original_audio_player.Tell(), self.timeline_duration())
                if self.current_time >= self.timeline_duration() - PLAYBACK_EDGE_GUARD:
                    self.playback_requested = False
                    self.pending_play = False
                    self.pause_original_audio_playback()
                    self.pause_background_audio_playback()
            return

        if self.current_segment_index is None:
            if get_program_mode() == PROFESSIONAL_MODE:
                bg_players = getattr(self, "background_audio_players", None) or {}
                if bg_players:
                    for state in bg_players.values():
                        ctrl = state.get("ctrl")
                        if ctrl and ctrl.GetState() == MEDIASTATE_PLAYING:
                            item = state.get("item") or {}
                            source_offset = float(item.get("source_offset", 0) or 0)
                            item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                            item_start = float(item.get("start", 0) or 0)
                            local_time = ctrl.Tell() / 1000.0
                            self.current_time = max(0.0, (local_time - source_offset) / item_speed + item_start)
                            break
                    if self.current_time >= self.timeline_duration() - PLAYBACK_EDGE_GUARD:
                        self.current_time = self.timeline_duration()
                        self.playback_requested = False
                        self.pending_play = False
                        try:
                            self.media_ctrl.Pause()
                        except Exception:
                            pass
                        self.pause_original_audio_playback()
                        self.pause_background_audio_playback()
                        return
                    return
            self.load_timeline_time(self.current_time, True)
            return

        segment = self.timeline[self.current_segment_index]
        media_time = self.media_ctrl.Tell() / 1000.0
        media_end = self.media_ctrl.Length() / 1000.0

        if override_clock_active:
            # الصوت الكامل هو الساعة الرئيسية. لا نعيد تشغيله عند الحواف؛
            # نحدد المقطع المرئي الموافق لموضعه الحالي ونلحق الصورة به فقط.
            self.current_time = audio_clock_time(self.original_audio_player.Tell(), self.timeline_duration())
            expected_index, expected_segment, expected_position = self.locate_timeline_segment(self.current_time)
            if expected_segment is not None and expected_index != self.current_segment_index:
                self.load_timeline_time(self.current_time, True, seamless=True)
                return
            if expected_segment is not None:
                preview_path = self.audio_visual_preview_playback_path()
                expected_seek_ms = media_seek_ms(
                    timeline_time=self.current_time,
                    segment_position=expected_position,
                    segment_start=expected_segment.start,
                    segment_end=expected_segment.end,
                    segment_speed=max(0.05, float(getattr(expected_segment, "speed", 1.0) or 1.0)),
                    preview_path=bool(preview_path),
                )
                # صحح الصورة فقط عند انحراف ملحوظ؛ لا تلمس الصوت المستمر.
                diff = int(self.media_ctrl.Tell() or 0) - expected_seek_ms
                if abs(diff) > 400:
                    now = time.time()
                    if not hasattr(self, "_last_sync_seek") or (now - self._last_sync_seek) > 1.5:
                        self._last_sync_seek = now
                        try:
                            self.media_ctrl.Seek(expected_seek_ms, mode='exact')
                        except Exception:
                            pass
                    # Pause audio until video catches up to avoid perpetual chase
                    if getattr(self, "original_audio_player", None) and self.original_audio_player.IsPlaying():
                        self.pause_original_audio_playback()
                        self.pause_background_audio_playback()
                else:
                    # In sync. Resume audio if it was playing.
                    if self.playback_requested and getattr(self, "original_audio_player", None):
                        if not self.original_audio_player.IsPlaying():
                            self.original_audio_player.Play()
                        for state in self.background_audio_players.values():
                            ctrl = state.get("ctrl")
                            if ctrl and not ctrl.IsPlaying():
                                ctrl.Play()
        else:
            if self.use_reliable_audio and self.original_audio_player:
                audio_time = self.original_audio_player.Tell() / 1000.0
                media_state = self.media_ctrl.GetState()
                media_length = self.media_ctrl.Length()
                if self.media_kind == "audio" or media_state != MEDIASTATE_PLAYING or media_length <= 0:
                    media_time = audio_time
            segment_position = self.segment_position(self.current_segment_index)
            segment_speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
            self.current_time = segment_position + max(0, min(media_time, segment.end) - segment.start) / segment_speed

        if self.selected_playback_range:
            _selected_start, selected_end = self.selected_playback_range
            if self.current_time >= selected_end - PLAYBACK_EDGE_GUARD:
                self.current_time = selected_end
                self.playback_requested = False
                self.pending_play = False
                self.selected_playback_range = None
                try:
                    self.media_ctrl.Pause()
                except Exception:
                    pass
                self.pause_original_audio_playback()
                self.pause_background_audio_playback()
                self.load_timeline_time(self.current_time, False)
                return
        if self.should_skip_playback_range():
            self.skip_playback_range()
            return
        if self.speed_preview_state and self.current_time >= float(self.speed_preview_state.get("preview_end", 0) or 0) - PLAYBACK_EDGE_GUARD:
            self.stop_speed_preview()
            return
        if self.current_time >= self.timeline_duration() - PLAYBACK_EDGE_GUARD:
            self.playback_requested = False
            self.pending_play = False
            try:
                self.media_ctrl.Pause()
            except Exception:
                pass
            self.pause_original_audio_playback()
            self.pause_background_audio_playback()
            return_position = getattr(self, "playback_return_position", None)
            self.playback_return_position = None
            if return_position is not None and return_position < self.timeline_duration() - PLAYBACK_EDGE_GUARD:
                self.current_time = return_position
                self.load_timeline_time(self.current_time, False)
                self.refresh_paused_video_frame()
            else:
                self.current_time = self.timeline_duration()
            return

        if not override_clock_active and self.live_skip_deleted_gap(media_time):
            return

        if not override_clock_active and self.should_advance_current_segment(media_time, media_end, segment):
            self.advance_after_segment_end()
            return

        now = time.monotonic()
        if now - self.last_playback_sync_time >= 0.12:
            self.last_playback_sync_time = now
            if not override_clock_active:
                self.sync_original_audio_playback(True, False)
            else:
                # تحديث المستوى فقط؛ لا Configure ولا Seek للصوت عند الحواف.
                active_segment = self.timeline[self.current_segment_index]
                self.original_audio_player.SetVolume(self.effective_original_audio_volume(active_segment))
            self.sync_background_audio_playback(True, False)

    def OnCrashSaveTimer(self, event):
        if self.has_video():
            self.save_crash_session_now()

    def OnContextMenu(self, event):
        menu = wx.Menu()
        ids = self.shortcut_ids
        if self.media_kind == "audio":
            insert_menu = wx.Menu()
            image_menu = wx.Menu()
            if self.work_images:
                for path in self.work_images:
                    item_id = wx.NewIdRef()
                    image_menu.Append(item_id, os.path.basename(path))
                    self.Bind(wx.EVT_MENU, lambda evt, selected_path=path: self.InsertAudioVisualItem("image", selected_path), id=item_id)
            else:
                image_menu.Append(ids["choose_work_images"], tr("اختيار صور للعمل"))
            insert_menu.AppendSubMenu(image_menu, tr("إدراج صورة"))
            insert_menu.Append(ids["insert_text"], tr("إدراج نص"))
            insert_menu.Append(ids["insert_background_audio"], tr("إدراج خلفية صوتية"))
            video_menu = wx.Menu()
            if self.work_videos:
                for path in self.work_videos:
                    item_id = wx.NewIdRef()
                    video_menu.Append(item_id, os.path.basename(path))
                    self.Bind(wx.EVT_MENU, lambda evt, selected_path=path: self.InsertAudioVisualItem("video", selected_path), id=item_id)
            else:
                video_menu.Append(ids["choose_work_videos"], tr("اختيار فيديوهات للعمل"))
            insert_menu.AppendSubMenu(video_menu, tr("إدراج فيديو هنا"))
            insert_menu.Append(ids["stop_at_insert_edge"], f"{tr('أوقفني عند حافة ما أضفت')}\tShift+Right")
            menu.AppendSubMenu(insert_menu, tr("إدراج هنا"))
            menu.AppendSeparator()
            menu.Append(ids["choose_work_images"], tr("اختيار صور للعمل"))
            menu.Append(ids["choose_work_videos"], tr("اختيار فيديوهات للعمل"))
            menu.Append(ids["distribute_work_images"], tr("دمج الصور مع الصوت بالتوزيع المتساوي"))
            menu.Append(ids["distribute_work_videos"], tr("دمج الفيديوهات مع الصوت بالتوزيع المتساوي"))
            menu.Append(ids["image_duration"], tr("اختيار مدة كل صورة"))
            menu.Append(ids["transition"], tr("تأثيرات الانتقالات"))

    def OnCrashSaveTimer(self, event):
        if self.has_video():
            self.save_crash_session_now()

    def OnContextMenu(self, event):
        menu = wx.Menu()
        ids = self.shortcut_ids
        if self.media_kind == "audio":
            insert_menu = wx.Menu()
            image_menu = wx.Menu()
            if self.work_images:
                for path in self.work_images:
                    item_id = wx.NewIdRef()
                    image_menu.Append(item_id, os.path.basename(path))
                    self.Bind(wx.EVT_MENU, lambda evt, selected_path=path: self.InsertAudioVisualItem("image", selected_path), id=item_id)
            else:
                image_menu.Append(ids["choose_work_images"], tr("اختيار صور للعمل"))
            insert_menu.AppendSubMenu(image_menu, tr("إدراج صورة"))
            insert_menu.Append(ids["insert_text"], tr("إدراج نص"))
            insert_menu.Append(ids["insert_background_audio"], tr("إدراج خلفية صوتية"))
            video_menu = wx.Menu()
            if self.work_videos:
                for path in self.work_videos:
                    item_id = wx.NewIdRef()
                    video_menu.Append(item_id, os.path.basename(path))
                    self.Bind(wx.EVT_MENU, lambda evt, selected_path=path: self.InsertAudioVisualItem("video", selected_path), id=item_id)
            else:
                video_menu.Append(ids["choose_work_videos"], tr("اختيار فيديوهات للعمل"))
            insert_menu.AppendSubMenu(video_menu, tr("إدراج فيديو هنا"))
            insert_menu.Append(ids["stop_at_insert_edge"], f"{tr('أوقفني عند حافة ما أضفت')}\tShift+Right")
            menu.AppendSubMenu(insert_menu, tr("إدراج هنا"))
            menu.AppendSeparator()
            menu.Append(ids["choose_work_images"], tr("اختيار صور للعمل"))
            menu.Append(ids["choose_work_videos"], tr("اختيار فيديوهات للعمل"))
            menu.Append(ids["distribute_work_images"], tr("دمج الصور مع الصوت بالتوزيع المتساوي"))
            menu.Append(ids["distribute_work_videos"], tr("دمج الفيديوهات مع الصوت بالتوزيع المتساوي"))
            menu.Append(ids["image_duration"], tr("اختيار مدة كل صورة"))
            menu.Append(ids["transition"], tr("تأثيرات الانتقالات"))
        else:
            menu.Append(ids["insert_image"], tr("إدراج صورة"))
            menu.Append(ids["insert_text"], tr("إدراج نص"))
            menu.Append(ids["insert_background_audio"], tr("إدراج خلفية صوتية"))
            menu.Append(ids["add_video"], tr("إضافة فيديو عند الموضع الحالي"))
            menu.AppendSeparator()
            menu.Append(ids["start"], tr("تحديد بداية المقطع"))
            menu.Append(ids["end"], tr("تحديد نهاية المقطع"))
            menu.Append(ids["delete"], tr("حذف المقطع المحدد"))
        reorder_actions = self.timeline_file_reorder_actions()
        if reorder_actions:
            menu.AppendSeparator()
            action_handlers = {
                "move_up": self.OnMoveCurrentTimelineFileUp,
                "move_down": self.OnMoveCurrentTimelineFileDown,
            }
            for key, label in reorder_actions:
                item_id = wx.NewIdRef()
                menu.Append(item_id, label)
                self.Bind(wx.EVT_MENU, action_handlers[key], id=item_id)
        if get_program_mode() == PROFESSIONAL_MODE:
            menu.AppendSeparator()
            split_id = wx.NewIdRef()
            menu.Append(split_id, f"{tr('تقسيم عند المؤشر')}\tS")
            self.Bind(wx.EVT_MENU, self.OnSplitAtPlayhead, id=split_id)
            insert_id = wx.NewIdRef()
            menu.Append(insert_id, f"{tr('إدراج عند المؤشر')}\tI")
            self.Bind(wx.EVT_MENU, self.OnInsertItemAtPlayhead, id=insert_id)
            delete_id = wx.NewIdRef()
            menu.Append(delete_id, f"{tr('حذف العنصر المركّز أو المحدد')}\tDelete")
            self.Bind(wx.EVT_MENU, self.OnDeleteElement, id=delete_id)
            mute_id = wx.NewIdRef()
            menu.Append(mute_id, f"{tr('كتم التراك الحالي أو رفع الكتم عنه')}\tCtrl+M")
            self.Bind(wx.EVT_MENU, self.OnMuteToggleCurrentTrack, id=mute_id)
            solo_id = wx.NewIdRef()
            menu.Append(solo_id, f"{tr('عزل التراك الحالي أو رفع العزل عنه')}\tShift+S")
            self.Bind(wx.EVT_MENU, self.OnSoloToggleCurrentTrack, id=solo_id)
        self.PopupMenu(menu)
        menu.Destroy()

