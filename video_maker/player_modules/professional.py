from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *
import json

from video_maker.timeline_engine import (
    NUDGE_STEP_SAMPLES,
    build_engine_from_player,
    commit_engine_to_player,
    move_to_track,
    nudge_item,
)


@publish_player_methods
class PlayerProfessionalMixin:
    def InsertAudioVisualItem(self, item_type, path):
        if self.media_kind != "audio":
            return
        selected = self.selected_effect_range()
        if selected:
            start_time, end_time = selected
            if end_time <= start_time:
                selected = None
        if not selected:
            start_time = self.current_time
            end_time = start_time
        if not path or not os.path.exists(path):
            # self.say("تعذر إدراج العنصر")
            wx.MessageBox("تعذر إدراج العنصر لأن الملف غير موجود.", "خطأ", wx.OK | wx.ICON_ERROR)
            return
        if item_type == "video":
            try:
                video_duration = get_video_duration(path)
            except Exception as error:
                # self.say("تعذر إدراج الفيديو")
                wx.MessageBox(f"تعذر إدراج الفيديو: {error}", "خطأ", wx.OK | wx.ICON_ERROR)
                return
            if end_time <= start_time:
                end_time = start_time + video_duration
            elif video_duration > end_time - start_time:
                result = wx.MessageBox("الفيديو أكبر من التحديد. هل تريد توسيع نقطة النهاية؟", "توسيع التحديد", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
                if result == wx.YES:
                    end_time = min(self.timeline_duration(), start_time + video_duration)
                else:
                    return
        if item_type == "image":
            if end_time <= start_time:
                end_time = start_time + 5.0
        before_state = self.capture_edit_state()
        try:
            item_id = uuid.uuid4().hex
            self.visual_items.append({"id": item_id, "type": item_type, "path": path, "start": start_time, "end": end_time, "transition": self.transition_name})
            self.add_edit_point(item_type, start_time, end_time, "visual", item_id=item_id)
            self.last_insert_end = end_time
            self.current_time = start_time
            self.start_time = None
            self.end_time = None
            self.is_dirty = True
            self.record_edit("إدراج فيديو" if item_type == "video" else "إدراج صورة", before_state)
            self.refresh_menu_bar()
            self.say("تم إدراج الفيديو" if item_type == "video" else "تم إدراج الصورة")
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored("إدراج فيديو" if item_type == "video" else "إدراج صورة", error, "audio_visual_item_insert")
            wx.MessageBox(f"تعذر إدراج العنصر: {error}", "خطأ", wx.OK | wx.ICON_ERROR)

    def is_within_deleted_segments(self, time):
        return False

    def OnHome(self, event=None):
        if self.has_video():
            if get_program_mode() == PROFESSIONAL_MODE:
                self._clear_element_selection()
            selected = self.selected_effect_range()
            self.current_time = selected[0] if selected else 0
            self.load_timeline_time(self.current_time, self.playback_requested)
            print(f"Moved to start: {self.current_time} seconds")
            self.say(tr("بداية التحديد") if selected else speech_messages.FILE_START, wait_for_ui=False)
        else:
            self.say(speech_messages.NO_OPEN_FILE, wait_for_ui=False)

    def OnEnd(self, event=None):
        if self.has_video():
            if get_program_mode() == PROFESSIONAL_MODE:
                self._clear_element_selection()
            selected = self.selected_effect_range()
            self.current_time = selected[1] if selected else self.timeline_duration()
            self.playback_requested = False
            self.load_timeline_time(self.current_time, False)
            print(f"Moved to end: {self.current_time} seconds")
            self.say(tr("نهاية التحديد") if selected else speech_messages.FILE_END, wait_for_ui=False)
        else:
            self.say(speech_messages.NO_OPEN_FILE, wait_for_ui=False)

    def OnPageUp(self, event=None):
        if not self.require_open_file():
            return
        self.seek_timeline_by(-20)
        print(f"Moved 20 seconds back: {self.current_time} seconds")

    def OnPageDown(self, event=None):
        if not self.require_open_file():
            return
        self.seek_timeline_by(20)
        print(f"Moved 20 seconds forward: {self.current_time} seconds")

    def OnCtrl1(self, event=None):
        if get_program_mode() == PROFESSIONAL_MODE:
            # In professional mode the number keys 1/2 are no longer used to
            # change the seek step. They are ignored here (the numpad 2 keeps
            # its element-move role elsewhere).
            return
        if not self.require_open_file():
            return
        if not hasattr(self, "normal_seek_step"):
            self.normal_seek_step = read_normal_seek_step()
        self.normal_seek_step = decrease_normal_seek_step(self.normal_seek_step)
        write_normal_seek_step(self.normal_seek_step)
        print(f"Decreased normal seek step: {self.normal_seek_step} milliseconds")
        self.say(speech_messages.SEEK_STEP_DECREASED.format(step=format_seek_step_ms(self.normal_seek_step)), wait_for_ui=False)

    def OnCtrl2(self, event=None):
        if get_program_mode() == PROFESSIONAL_MODE:
            # In professional mode the number keys 1/2 are no longer used to
            # change the seek step. They are ignored here (the numpad 2 keeps
            # its element-move role elsewhere).
            return
        if not self.require_open_file():
            return
        if not hasattr(self, "normal_seek_step"):
            self.normal_seek_step = read_normal_seek_step()
        self.normal_seek_step = increase_normal_seek_step(self.normal_seek_step)
        write_normal_seek_step(self.normal_seek_step)
        print(f"Increased normal seek step: {self.normal_seek_step} milliseconds")
        self.say(speech_messages.SEEK_STEP_INCREASED.format(step=format_seek_step_ms(self.normal_seek_step)), wait_for_ui=False)

    def OnCtrl3(self, event=None):
        if get_program_mode() == PROFESSIONAL_MODE:
            self.OnResetZoom()
            return
        if not self.require_open_file():
            return
        self.normal_seek_step = DEFAULT_NORMAL_SEEK_STEP
        write_normal_seek_step(self.normal_seek_step)
        print(f"Reset normal seek step to default: {self.normal_seek_step} milliseconds")
        self.say(speech_messages.SEEK_STEP_RESET.format(step=format_seek_step_ms(self.normal_seek_step)), wait_for_ui=False)

    def _base_pixels_per_second(self):
        value = getattr(self, "pixels_per_second", None)
        if value is None:
            value = read_pixels_per_second()
        return normalize_pixels_per_second(value)

    def _announce_zoom_step(self, message_key):
        if not self.require_open_file():
            return
        self.say(message_key.format(step=format_seek_step_ms(self.current_seek_step_ms())), wait_for_ui=False)

    def OnZoomIn(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        current = self._base_pixels_per_second()
        self.pixels_per_second = min(
            MAX_PIXELS_PER_SECOND,
            current + pixels_per_second_increment(current),
        )
        write_pixels_per_second(self.pixels_per_second)
        print(f"Zoomed in: {self.pixels_per_second} pixels per second")
        self._announce_zoom_step(speech_messages.SEEK_STEP_DECREASED)

    def OnZoomOut(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        current = self._base_pixels_per_second()
        self.pixels_per_second = max(
            MIN_PIXELS_PER_SECOND,
            current - pixels_per_second_increment(current),
        )
        write_pixels_per_second(self.pixels_per_second)
        print(f"Zoomed out: {self.pixels_per_second} pixels per second")
        self._announce_zoom_step(speech_messages.SEEK_STEP_INCREASED)

    def OnResetZoom(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        self.pixels_per_second = DEFAULT_PIXELS_PER_SECOND
        write_pixels_per_second(self.pixels_per_second)
        print(f"Reset zoom to default: {self.pixels_per_second} pixels per second")
        self._announce_zoom_step(speech_messages.SEEK_STEP_RESET)

    def OnSetLanguage(self, language):
        set_language(language)
        self.update_all_window_titles()
        self.refresh_menu_bar()
        self.say(tr("تم تغيير اللغة"))

    def OnSetTheme(self, theme):
        set_theme(theme)
        for window in self.open_program_windows():
            window.apply_current_theme()
            window.refresh_menu_bar()
        self.say(tr("تم تغيير مظهر البرنامج"))

    def open_external_target(self, target, spoken_text):
        try:
            webbrowser.open(target)
            self.say(tr(spoken_text))
        except Exception as error:
            # self.say(tr("تعذر فتح الرابط"))
            wx.MessageBox(tr("تعذر فتح الرابط: {error}").format(error=error), tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnFacebookContact(self, event=None):
        self.open_external_target("https://www.facebook.com/mustafaalbehairy2020/", "تم فتح صفحة فيس بوك")

    def OnTelegramContact(self, event=None):
        self.open_external_target("https://t.me/elbheri", "تم فتح صفحة تلجرام")

    def OnTelegramApps(self, event=None):
        self.open_external_target("https://t.me/elbheri100", "تم فتح مجموعة تلجرام")

    def OnOpenSourceContribution(self, event=None):
        self.open_external_target("https://www.facebook.com/mustafaalbehairy2020/", "تم فتح صفحة المساهمة")

    def OnKeyboardShortcutsHelp(self, event=None):
        help_path = bundled_path("keyboard_shortcuts.html").as_uri() + f"?lang={get_language()}"
        self.open_external_target(help_path, "تم فتح صفحة اختصارات لوحة المفاتيح")

    def OnProgramSettings(self, event=None):
        dialog = ProgramSettingsDialog(self)
        try:
            result = dialog.ShowModal()
        finally:
            dialog.Destroy()
        if result == wx.ID_OK:
            self.refresh_menu_bar()

    def OnToggleProgramMode(self, event=None):
        mode = toggle_program_mode()
        self.current_track = DEFAULT_TRACK
        self.refresh_menu_bar()
        self._sync_track_list_visibility()
        label = tr("الوضع العادي") if mode == NORMAL_MODE else tr("الوضع الاحترافي")
        self.say(f"{tr('تم التبديل إلى')} {label}")

    def _sync_track_list_visibility(self):
        track_list = getattr(self, "track_list", None)
        if track_list is None:
            return
        if get_program_mode() == PROFESSIONAL_MODE:
            self._update_track_list()
            track_list.Show()
        else:
            track_list.Hide()
        try:
            self.main_panel.Layout()
        except Exception:
            pass

    def _update_track_list(self):
        track_list = getattr(self, "track_list", None)
        if track_list is None or not track_list.IsShown():
            return
        from video_maker.tracks import (
            BACKGROUND_AUDIO_TRACK, MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK,
            SOUND_EFFECTS_TRACK, TEXT_TRACK,
        )
        tracks = [
            (MAIN_VIDEO_TRACK, "المقطع الرئيسي", self.timeline),
            (SECONDARY_VIDEO_TRACK, "الفيديو الثانوي", self.b_roll_items),
            (SOUND_EFFECTS_TRACK, "المؤثرات الصوتية", self.sound_effects_items),
            (BACKGROUND_AUDIO_TRACK, "الخلفية الصوتية", self.background_audio_items),
            (TEXT_TRACK, "النصوص", [v for v in self.visual_items if v.get("type") == "text"]),
        ]
        track_list.Clear()
        for track_key, label, items in tracks:
            count = len(items)
            marker = " ←" if track_key == self.current_track else ""
            if count:
                entry = "{label}: {count}{marker}".format(label=label, count=count, marker=marker)
            else:
                entry = "{label}: فارغ{marker}".format(label=label, marker=marker)
            track_list.Append(entry)
        track_list.SetSelection(min(len(tracks) - 1, max(0, self._current_track_index())))

    def _current_track_index(self):
        from video_maker.tracks import track_index
        return track_index(self.current_track)

    def OnNextTrack(self, event=None):
        if not self.require_open_file():
            return
        self.current_track = next_track(self.current_track)
        self.say(self.track_announcement(), wait_for_ui=False)
        self._update_track_list()

    def OnPreviousTrack(self, event=None):
        if not self.require_open_file():
            return
        self.current_track = previous_track(self.current_track)
        self.say(self.track_announcement(), wait_for_ui=False)
        self._update_track_list()

    def OnInsertTrackItem(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        return self.OnInsertItemAtPlayhead(event)

    def OnDeleteElement(self, event=None):
        if get_program_mode() == PROFESSIONAL_MODE:
            return self.OnDeleteSelectedElement(event)
        return self.OnDeleteSegment(event)

    def _find_item_index(self, items, element):
        element_id = element.get("id") if isinstance(element, dict) else None
        if element_id:
            for index, item in enumerate(items):
                if isinstance(item, dict) and item.get("id") == element_id:
                    return index
        estart, eend = item_bounds(element)
        for index, item in enumerate(items):
            start, end = item_bounds(item)
            if abs(start - estart) < 1e-6 and abs(end - eend) < 1e-6:
                return index
        return None

    def _find_segment_index(self, element):
        if not isinstance(element, dict):
            element = element_to_dict(element)
        element_path = str(element.get("path", "") or "")
        for index, segment in enumerate(self.timeline):
            if (
                str(getattr(segment, "path", "") or "") == element_path
                and abs(float(segment.start) - float(element.get("start", 0.0) or 0.0)) < 1e-6
                and abs(float(segment.end) - float(element.get("end", 0.0) or 0.0)) < 1e-6
                and abs(float(segment.speed) - float(element.get("speed", 1.0) or 1.0)) < 1e-6
            ):
                return index
        return None

    def _segment_position(self, index):
        return sum(float(segment.duration) for segment in self.timeline[:index])

    def _dict_track_panels(self):
        return {
            SECONDARY_VIDEO_TRACK: self.b_roll_items,
            SOUND_EFFECTS_TRACK: self.sound_effects_items,
            BACKGROUND_AUDIO_TRACK: self.background_audio_items,
            TEXT_TRACK: self.visual_items,
        }

    def _make_gap_segment(self, duration):
        try:
            from video_maker.clipboard_media_paste import create_black_video_proxy, create_silent_audio_proxy

            black_path, black_dir = create_black_video_proxy("", duration)
            silence_path, silence_dir = create_silent_audio_proxy(black_path, duration)
            self.generated_temp_dirs.append(black_dir)
            self.generated_temp_dirs.append(silence_dir)
            self.generated_temp_files.append(black_path)
            self.generated_temp_files.append(silence_path)
            return TimelineSegment(
                black_path, 0.0, max(0.05, float(duration)), speed=1.0,
                audio_path=silence_path, audio_start=0.0,
            )
        except Exception as error:
            append_problem("gap_segment", str(error), exception=error)
            return None

    def OnDeleteSelectedElement(self, event=None):
        if event is not None and get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        elements = []
        if self.selected_element_ids:
            elements = [item for item in self._all_selectable_items() if self._element_id(item) in self.selected_element_ids]
        elif self.focused_element:
            elements = [self.focused_element]
        if not elements:
            self.say(tr("لا يوجد عنصر محدد للحذف"), wait_for_ui=False)
            return
        self._remove_elements_from_project(elements)
        self.say(tr("تم حذف العناصر"), wait_for_ui=False)

    def _remove_elements_from_project(self, elements):
        resume_playback = self.stop_playback_for_timeline_edit("delete_elements")
        before_state = self.capture_edit_state()
        main_indexes = []
        for element in sorted(elements, key=lambda item: item_bounds(item)[0]):
            track = self._locate_element_track(element)
            if track is None:
                continue
            if track == MAIN_VIDEO_TRACK:
                index = self._find_segment_index(element)
                if index is not None:
                    main_indexes.append(index)
                continue
            storage = self._track_storage_for(track)
            index = self._find_item_index(storage, element)
            if index is None:
                continue
            start, end = item_bounds(storage[index])
            removed = max(0.0, end - start)
            del storage[index]
            if self.ripple_mode == "per_track":
                ripple_shift({track: storage}, end, -removed, self.ripple_mode)
            elif self.ripple_mode == "all_tracks":
                ripple_shift(self._dict_track_panels(), end, -removed, self.ripple_mode)
                if self.media_kind == "video":
                    range_start, range_end = start, end
                else:
                    range_start, range_end = clean_delete_range(self.timeline, start, end)
                if range_end > range_start + 1e-9:
                    self.timeline = delete_range(self.timeline, range_start, range_end)
                    if self.media_kind == "video":
                        self.timeline = apply_audio_cut_fade_at_boundary(self.timeline, range_start)
                    self.edit_points = adjust_points_after_delete(self.edit_points, range_start, range_end)
        for index in sorted(main_indexes, reverse=True):
            position = self._segment_position(index)
            duration = float(self.timeline[index].duration)
            if self.ripple_mode == "off":
                gap = self._make_gap_segment(duration)
                if gap is not None:
                    self.timeline = self.timeline[:index] + [gap] + self.timeline[index + 1:]
                    continue
            self.timeline = self.timeline[:index] + self.timeline[index + 1:]
        self.selected_element_ids = set()
        self.current_time = min(float(self.current_time), self.timeline_duration()) if self.timeline else 0.0
        self.focused_element = self._refocus_element_after_delete()
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("حذف العناصر", before_state)
        self.playback_requested = resume_playback
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)

    def OnSplitAtPlayhead(self, event=None):
        if event is not None and get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        at_time = float(self.current_time)
        track = self.current_track
        if track == MAIN_VIDEO_TRACK:
            if not self.timeline:
                self.say(tr("لا يوجد عنصر للقص على المقطع الرئيسي"), wait_for_ui=False)
                return
            index, segment, position = self.locate_timeline_segment(at_time)
            local = max(0.0, at_time - position)
            if local <= 0.05 or local >= float(segment.duration) - 0.05:
                self.say(tr("لا يوجد عنصر يقبل القص عند المؤشر"), wait_for_ui=False)
                return
            left, right = split_timeline_segment(self.timeline, index, at_time)
            before_state = self.capture_edit_state()
            self.timeline = self.timeline[:index] + left + right + self.timeline[index + 1:]
            if right:
                self.focused_element = dict(right[0])
            self.is_dirty = True
            self.record_edit("قص العنصر", before_state)
            self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
            self.say(tr("تم قص العنصر"), wait_for_ui=False)
            return
        items, _kind = self._current_track_items()
        element = item_at_time(items, at_time)
        if element is None:
            self.say(tr("لا يوجد عنصر عند المؤشر للقص"), wait_for_ui=False)
            return
        start, end = item_bounds(element)
        if at_time - start <= 0.05 or end - at_time <= 0.05:
            self.say(tr("المؤشر على حافة العنصر"), wait_for_ui=False)
            return
        storage = self._track_storage_for(track)
        index = self._find_item_index(storage, element)
        if index is None:
            self.say(tr("العنصر غير موجود على التراك الحالي"), wait_for_ui=False)
            return
        left, right = split_item(storage[index], at_time)
        before_state = self.capture_edit_state()
        storage[index:index + 1] = [left, right]
        self.focused_element = right
        self.is_dirty = True
        self.record_edit("قص العنصر", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم قص العنصر"), wait_for_ui=False)

    def OnInsertItemAtPlayhead(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        at_time = max(0.0, float(self.current_time))
        track = self.current_track
        if track == TEXT_TRACK:
            return self.OnInsertText()
        if track in (SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK):
            return self._insert_audio_at_playhead(at_time)
        if track == MAIN_VIDEO_TRACK:
            return self._insert_main_video_at_playhead(at_time)
        if track == SECONDARY_VIDEO_TRACK:
            return self._insert_secondary_at_playhead(at_time)
        self.say(tr("التراك الحالي لا يدعم الإدراج عند المؤشر"), wait_for_ui=False)

    def _import_media_into_program(self, path):
        if not path:
            return None
        source = os.path.abspath(path)
        if not os.path.exists(source):
            return None
        root = os.path.abspath(app_data_root())
        if source.startswith(root + os.sep):
            return source
        try:
            extension = os.path.splitext(source)[1] or ""
            if not extension:
                kind = media_kind_for_path(source)
                extension = ".png" if kind == "image" else ".mp4"
            destination = os.path.join(str(imported_media_root()), f"{uuid.uuid4().hex}{extension}")
            shutil.copy2(source, destination)
            return destination
        except Exception as error:
            append_problem("import_media", str(error), exception=error)
            return None

    def pick_file_for_track(self, track, multiple=False):
        types = track_media_types(track)
        if "audio" in types:
            wildcard = AUDIO_WILDCARD
            kind = "audio"
        elif "image" in types and "video" not in types:
            wildcard = IMAGE_WILDCARD
            kind = "image"
        else:
            wildcard = GENERAL_WILDCARD
            kind = "video"
        style = wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        if multiple:
            style |= wx.FD_MULTIPLE
        with wx.FileDialog(self, tr("اختيار ملف"), wildcard=wildcard, style=style) as dialog:
            prepare_media_file_dialog(dialog, kind, "add_track_item")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return []
            paths = sorted(list(dialog.GetPaths()), key=natural_sort_key)
            remember_media_paths(paths, kind, "add_track_item")
            return paths

    def OnMuteToggleCurrentTrack(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        track = self.current_track
        before_state = self.capture_edit_state()
        if track in self.muted_tracks:
            self.muted_tracks.discard(track)
            muted = False
        else:
            self.muted_tracks.add(track)
            muted = True
        self.is_dirty = True
        self.record_edit("تبديل كتم التراك", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        label = tr("التراك {number} {label}").format(number=track_index(track) + 1, label=tr(track_label(track)))
        if muted:
            self.say(tr("تم كتم {label}").format(label=label), wait_for_ui=False)
        else:
            self.say(tr("تم رفع الكتم عن {label}").format(label=label), wait_for_ui=False)

    def OnSoloToggleCurrentTrack(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        track = self.current_track
        before_state = self.capture_edit_state()
        if track in self.solo_tracks:
            self.solo_tracks.discard(track)
            solo = False
        else:
            self.solo_tracks.add(track)
            solo = True
        self.is_dirty = True
        self.record_edit("تبديل عزل التراك", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        label = tr("التراك {number} {label}").format(number=track_index(track) + 1, label=tr(track_label(track)))
        if solo:
            self.say(tr("تم عزل {label}").format(label=label), wait_for_ui=False)
        else:
            self.say(tr("تم رفع العزل عن {label}").format(label=label), wait_for_ui=False)

    def _resolve_insert_overlap(self, storage, at_time):
        conflict = item_at_time(storage, at_time)
        if conflict is None:
            return
        start, end = item_bounds(conflict)
        if at_time - start > 0.05 and end - at_time > 0.05:
            index = self._find_item_index(storage, conflict)
            if index is not None:
                left, right = split_item(storage[index], at_time)
                storage[index:index + 1] = [left, right]

    def _insert_sorted(self, storage, item):
        at_time = float(item.get("start", 0.0) or 0.0)
        for index, existing in enumerate(storage):
            if float(existing.get("start", 0.0) or 0.0) >= at_time:
                storage.insert(index, item)
                return
        storage.append(item)

    def _insert_main_video_at_playhead(self, at_time):
        paths = self.pick_file_for_track(MAIN_VIDEO_TRACK)
        if not paths:
            return
        original = os.path.abspath(paths[0])
        path = self._import_media_into_program(paths[0])
        if not path:
            self.say(tr("تعذر تحميل الملف داخل البرنامج"), wait_for_ui=False)
            return
        duration = natural_span(path)
        if duration <= 0:
            self.say(tr("تعذر تحديد مدة الملف"), wait_for_ui=False)
            return
        new_segment = new_file_segment(path, 0.0, duration, source_file_name=os.path.basename(original))
        before_state = self.capture_edit_state()
        if not self.timeline:
            self.timeline = [new_segment]
        else:
            index, _segment, _position = self.locate_timeline_segment(at_time)
            left, right = split_timeline_segment(self.timeline, index, at_time)
            self.timeline = self.timeline[:index] + left + [new_segment] + right + self.timeline[index + 1:]
        self.current_time = at_time
        self.focused_element = dict(new_segment)
        self.is_dirty = True
        self.record_edit("إدراج عنصر", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم إدراج العنصر"), wait_for_ui=False)

    def _insert_audio_at_playhead(self, at_time):
        is_effects = self.current_track == SOUND_EFFECTS_TRACK
        paths = self.pick_file_for_track(self.current_track, multiple=is_effects)
        if not paths:
            return
        if is_effects:
            storage = self.sound_effects_items
            item_type = "sound_effect"
            operation = "إدراج مؤثر صوتي"
        else:
            storage = self.background_audio_items
            item_type = "background_audio"
            operation = "إدراج خلفية صوتية"
        before_state = self.capture_edit_state()
        insert_at = max(0.0, float(at_time))
        for path in paths:
            original = os.path.abspath(path)
            path = self._import_media_into_program(path)
            if not path:
                self.say(tr("تعذر تحميل الملف داخل البرنامج"), wait_for_ui=False)
                continue
            duration = natural_span(path)
            if duration <= 0:
                self.say(tr("تعذر تحديد مدة الملف"), wait_for_ui=False)
                continue
            item = {
                "id": uuid.uuid4().hex,
                "type": item_type,
                "path": path,
                "original_path": original,
                "name": os.path.basename(original),
                "start": insert_at,
                "end": insert_at + duration,
                "volume": 0.4,
                "trim_silence": False,
                "speed": 1.0,
                "source_offset": 0.0,
            }
            self._resolve_insert_overlap(storage, insert_at)
            if should_ripple(self.ripple_mode):
                ripple_shift({self.current_track: storage}, insert_at, duration, self.ripple_mode)
            self._insert_sorted(storage, item)
            self.last_insert_end = item["end"]
            self.focused_element = dict(item)
            insert_at = item["end"]
        self.current_time = max(0.0, float(at_time)) if not is_effects else max(0.0, float(insert_at))
        self.is_dirty = True
        self.record_edit(operation, before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم إدراج العنصر"), wait_for_ui=False)

    def _insert_secondary_at_playhead(self, at_time):
        paths = self.pick_file_for_track(SECONDARY_VIDEO_TRACK)
        if not paths:
            return
        original = os.path.abspath(paths[0])
        path = self._import_media_into_program(paths[0])
        if not path:
            self.say(tr("تعذر تحميل الملف داخل البرنامج"), wait_for_ui=False)
            return
        duration = natural_span(path)
        media_kind = media_kind_for_path(path)
        is_image = duration <= 0 or media_kind == "image"
        if duration <= 0:
            duration = default_duration_for("image", 0, self.default_image_duration)
        item = {
            "id": uuid.uuid4().hex,
            "type": "image" if is_image else "video",
            "path": path,
            "name": os.path.basename(original),
            "start": at_time,
            "end": at_time + duration,
            "transition": self.transition_name,
            "speed": 1.0,
            "source_offset": 0.0,
        }
        before_state = self.capture_edit_state()
        self._resolve_insert_overlap(self.b_roll_items, at_time)
        if should_ripple(self.ripple_mode):
            ripple_shift({SECONDARY_VIDEO_TRACK: self.b_roll_items}, at_time, duration, self.ripple_mode)
        self._insert_sorted(self.b_roll_items, item)
        self.last_insert_end = item["end"]
        self.current_time = at_time
        self.focused_element = dict(item)
        self.is_dirty = True
        self.record_edit("إدراج فيديو ثانوي", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم إدراج العنصر"), wait_for_ui=False)

    def track_announcement(self):
        content = self.track_content_text()
        message = tr("التراك {number} {label}، {content}").format(
            number=track_index(self.current_track) + 1,
            label=tr(track_label(self.current_track)),
            content=content,
        )
        if self.current_track in getattr(self, "muted_tracks", set()):
            message += "، " + tr("مكتوم")
        if self.current_track in getattr(self, "solo_tracks", set()):
            message += "، " + tr("معزول")
        return message

    def track_content_text(self):
        track = self.current_track
        if track == MAIN_VIDEO_TRACK:
            return self._track_count_text(len(self.timeline), "لا يحتوي على مقاطع", "عدد المقاطع {count}")
        if track == SECONDARY_VIDEO_TRACK:
            return self._track_count_text(
                len(getattr(self, "b_roll_items", [])), "لا يحتوي على مقاطع", "عدد المقاطع {count}"
            )
        if track == SOUND_EFFECTS_TRACK:
            return self._track_count_text(
                len(getattr(self, "sound_effects_items", [])), "لا يحتوي على أصوات", "عدد الأصوات {count}"
            )
        if track == BACKGROUND_AUDIO_TRACK:
            return self._track_count_text(
                len(self.background_audio_items), "لا يحتوي على أصوات", "عدد الأصوات {count}"
            )
        if track == TEXT_TRACK:
            items = [item for item in getattr(self, "visual_items", []) if item.get("type") == "text"]
            return self._track_count_text(len(items), "لا يحتوي على نصوص", "عدد النصوص {count}")
        return tr("لا يحتوي على مقاطع")

    def _track_count_text(self, count, empty_message, count_template):
        if not count:
            return tr(empty_message)
        return tr(count_template).format(count=count)

    def _current_track_items(self):
        track = self.current_track
        if track == MAIN_VIDEO_TRACK:
            return self.timeline, "timeline"
        if track == SECONDARY_VIDEO_TRACK:
            return self.b_roll_items, "b_roll"
        if track == SOUND_EFFECTS_TRACK:
            return self.sound_effects_items, "sound_effect"
        if track == BACKGROUND_AUDIO_TRACK:
            return self.background_audio_items, "background_audio"
        if track == TEXT_TRACK:
            items = [item for item in getattr(self, "visual_items", []) if item.get("type") == "text"]
            return items, "text"
        return self.timeline, "timeline"

    def _track_storage_for(self, track):
        if track == MAIN_VIDEO_TRACK:
            return self.timeline
        if track == SECONDARY_VIDEO_TRACK:
            return self.b_roll_items
        if track == SOUND_EFFECTS_TRACK:
            return self.sound_effects_items
        if track == BACKGROUND_AUDIO_TRACK:
            return self.background_audio_items
        if track == TEXT_TRACK:
            return self.visual_items
        return self.timeline

    def _all_track_storages(self):
        return {
            MAIN_VIDEO_TRACK: self.timeline,
            SECONDARY_VIDEO_TRACK: self.b_roll_items,
            SOUND_EFFECTS_TRACK: self.sound_effects_items,
            BACKGROUND_AUDIO_TRACK: self.background_audio_items,
            TEXT_TRACK: self.visual_items,
        }

    def _all_selectable_items(self):
        pool = list(self.timeline)
        pool.extend(self.b_roll_items)
        pool.extend(self.sound_effects_items)
        pool.extend(self.background_audio_items)
        pool.extend(item for item in self.visual_items if isinstance(item, dict) and item.get("type") == "text")
        return pool

    def _element_dict(self, item):
        return element_to_dict(item)

    def _element_id(self, item):
        return element_identifier(item)

    def _current_track_filtered(self):
        items, _kind = self._current_track_items()
        return items

    def _element_bounds_for_navigation(self, item):
        if isinstance(item, TimelineSegment):
            for index, segment in enumerate(self.timeline):
                if segment is item:
                    position = self._segment_position(index)
                    return position, position + float(segment.duration)
        return item_bounds(item)

    def _element_timeline_position(self, item):
        start, _end = self._element_bounds_for_navigation(item)
        return float(start)

    def _element_near_time(self, items, at_time):
        at_time = float(at_time)

        def bounds(item):
            return self._element_bounds_for_navigation(item)

        for item in items or ():
            start, end = bounds(item)
            if start <= at_time < end:
                return item
        after = [item for item in items or () if bounds(item)[0] >= at_time - 1e-9]
        if after:
            return min(after, key=lambda item: bounds(item)[0])
        before = [item for item in items or () if bounds(item)[1] <= at_time + 1e-9]
        if before:
            return max(before, key=lambda item: bounds(item)[0])
        return None

    def _refocus_element_after_delete(self, track=None):
        if track is None:
            items = self._current_track_filtered()
        else:
            items = self._track_storage_for(track) or []
        element = self._element_near_time(items, self.current_time)
        return self._element_dict(element) if element is not None else None

    def _resolve_restored_focused_element(self, saved):
        """يعيد العنصر المُركز المحفوظ إن كان ما زال موجوداً بعد فتح المشروع.

        الـ id للمقاطع الرئيسية مشتق من (path, start, end, speed) فلا يُحفظ في
        الملف، ولذلك لا نستخدم العنصر المحفوظ كما هو بل نتأكد أنه يطابق عنصراً
        حياً في التراكات قبل اعتماده (الاستئناف في نفس الموضع عند الفتح).
        """
        if not isinstance(saved, dict):
            return None
        saved_id = element_identifier(saved)
        for item in self._all_selectable_items():
            if element_identifier(item) == saved_id:
                return dict(saved) if isinstance(saved, dict) else saved
        return None

    def _element_announcement(self, element):
        name = element_display_name(element, self._current_track_filtered()) or base_element_name(element) or tr("عنصر")
        position = self._element_timeline_position(element)
        return tr("العنصر {name} يبدأ عند {time}").format(name=name, time=self.spoken_time(position))

    def _focus_element_on_track(self, element, announce=True):
        self.focused_element = self._element_dict(element)
        position = self._element_timeline_position(element)
        self.current_time = min(position + 0.05, self.timeline_duration())
        self.load_timeline_time(self.current_time, False)
        self.sync_background_audio_at(self.current_time, should_play=False)
        if announce:
            self.say(self._element_announcement(element), wait_for_ui=False)

    def _nav_item_bounds(self, item):
        return self._element_bounds_for_navigation(item)

    def OnNextElementOnTrack(self, event=None):
        if event is not None and get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        items = self._current_track_filtered()
        current_id = self._element_id(self.focused_element) if self.focused_element else ""
        target = next_item_on_track(items, current_id, 1, bounds_fn=self._nav_item_bounds)
        if target is None:
            if self.focused_element:
                self._focus_element_on_track(self.focused_element)
            return
        self._focus_element_on_track(target)

    def OnPreviousElementOnTrack(self, event=None):
        if event is not None and get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        items = self._current_track_filtered()
        current_id = self._element_id(self.focused_element) if self.focused_element else ""
        target = previous_item_on_track(items, current_id, -1, bounds_fn=self._nav_item_bounds)
        if target is None:
            if self.focused_element:
                self._focus_element_on_track(self.focused_element)
            return
        self._focus_element_on_track(target)

    def _selection_reference_element(self, direction=1):
        items = self._current_track_filtered()
        if self.selected_element_ids:
            selected_items = [item for item in items if self._element_id(item) in self.selected_element_ids]
            if selected_items:
                if direction >= 0:
                    return max(selected_items, key=lambda item: item_bounds(item)[0])
                return min(selected_items, key=lambda item: item_bounds(item)[0])
        if self.focused_element:
            return self.focused_element
        return item_at_time(items, self.current_time)

    def _add_selected_element(self, element):
        self.selected_element_ids.add(self._element_id(element))

    def _selection_announcement(self):
        return tr("تم تحديد {count} عناصر").format(count=len(self.selected_element_ids))

    def OnExtendSelectionRight(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        items = self._current_track_filtered()
        if not items:
            self.say(tr("لا توجد عناصر على هذا التراك"), wait_for_ui=False)
            return
        reference = self._selection_reference_element(1)
        current_id = self._element_id(reference) if reference else ""
        target = next_item_on_track(items, current_id, 1)
        if target is None:
            self.say(tr("لا توجد عناصر إضافية للاختيار"), wait_for_ui=False)
            return
        self._add_selected_element(target)
        self._focus_element_on_track(target, announce=False)
        self.say(self._selection_announcement(), wait_for_ui=False)

    def OnExtendSelectionLeft(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        items = self._current_track_filtered()
        if not items:
            self.say(tr("لا توجد عناصر على هذا التراك"), wait_for_ui=False)
            return
        reference = self._selection_reference_element(-1)
        current_id = self._element_id(reference) if reference else ""
        target = previous_item_on_track(items, current_id, -1)
        if target is None:
            self.say(tr("لا توجد عناصر إضافية للاختيار"), wait_for_ui=False)
            return
        self._add_selected_element(target)
        self._focus_element_on_track(target, announce=False)
        self.say(self._selection_announcement(), wait_for_ui=False)

    def OnSelectAllTimelinePro(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        all_items = self._all_selectable_items()
        self.selected_element_ids = {self._element_id(item) for item in all_items}
        self.say(tr("تم تحديد جميع العناصر ({count})").format(count=len(self.selected_element_ids)), wait_for_ui=False)

    def _clipboard_elements(self):
        elements = []
        if self.selected_element_ids:
            elements.extend(item for item in self._all_selectable_items() if self._element_id(item) in self.selected_element_ids)
        elif self.focused_element:
            elements.append(self.focused_element)
        return elements

    def OnCopyElements(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        elements = self._clipboard_elements()
        if not elements:
            self.say(tr("لا يوجد عنصر محدد للنسخ"), wait_for_ui=False)
            return
        ordered = sorted(elements, key=lambda item: item_bounds(item)[0])
        first_start = item_bounds(ordered[0])[0]
        items = []
        for element in ordered:
            entry = self._element_dict(element)
            entry["start"] = float(entry["start"]) - first_start
            entry["end"] = max(float(entry["end"]) - first_start, float(entry["start"]))
            items.append(entry)
        self.element_clipboard = {"track": self.current_track, "items": items}
        self._write_clipboard_to_system(items)
        self.say(tr("تم نسخ {count} عناصر").format(count=len(items)), wait_for_ui=False)

    def _write_clipboard_to_system(self, items):
        try:
            data = json.dumps(items, ensure_ascii=False)
            if wx.TheClipboard.Open():
                try:
                    wx.TheClipboard.SetData(wx.TextDataObject(data))
                    wx.TheClipboard.Flush()
                finally:
                    wx.TheClipboard.Close()
        except Exception:
            pass

    def _read_clipboard_from_system(self):
        try:
            if wx.TheClipboard.Open():
                try:
                    data_obj = wx.TextDataObject()
                    if wx.TheClipboard.GetData(data_obj):
                        text = data_obj.GetText()
                        if text:
                            items = json.loads(text)
                            if isinstance(items, list) and items:
                                return items
                finally:
                    wx.TheClipboard.Close()
        except Exception:
            pass
        return None

    def _locate_element_track(self, element):
        element_key = self._element_id(element)
        if element_key:
            for track, storage in self._all_track_storages().items():
                for item in storage:
                    if self._element_id(item) == element_key:
                        return track
        estart, eend = item_bounds(element)
        for track, storage in self._all_track_storages().items():
            for item in storage:
                start, end = item_bounds(item)
                if abs(start - estart) < 1e-6 and abs(end - eend) < 1e-6:
                    return track
        return None

    def OnCutElements(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        self.OnCopyElements(event)
        if not self.element_clipboard:
            return
        elements = self._clipboard_elements()
        if not elements:
            return
        resume_playback = self.stop_playback_for_timeline_edit("cut_elements")
        before_state = self.capture_edit_state()
        main_indexes = []
        cut_sub_tracks = []
        for element in sorted(elements, key=lambda item: item_bounds(item)[0]):
            track = self._locate_element_track(element)
            if track is None:
                continue
            if track == MAIN_VIDEO_TRACK:
                index = self._find_segment_index(element)
                if index is not None:
                    main_indexes.append(index)
                continue
            storage = self._track_storage_for(track)
            index = self._find_item_index(storage, element)
            if index is None:
                continue
            start, end = item_bounds(storage[index])
            removed = max(0.0, end - start)
            del storage[index]
            if track not in cut_sub_tracks:
                cut_sub_tracks.append(track)
            if self.ripple_mode == "per_track":
                ripple_shift({track: storage}, end, -removed, self.ripple_mode)
            elif self.ripple_mode == "all_tracks":
                ripple_shift(self._dict_track_panels(), end, -removed, self.ripple_mode)
                if self.media_kind == "video":
                    range_start, range_end = start, end
                else:
                    range_start, range_end = clean_delete_range(self.timeline, start, end)
                if range_end > range_start + 1e-9:
                    self.timeline = delete_range(self.timeline, range_start, range_end)
                    if self.media_kind == "video":
                        self.timeline = apply_audio_cut_fade_at_boundary(self.timeline, range_start)
                    self.edit_points = adjust_points_after_delete(self.edit_points, range_start, range_end)
        for index in sorted(main_indexes, reverse=True):
            position = self._segment_position(index)
            duration = float(self.timeline[index].duration)
            if self.ripple_mode == "off":
                gap = self._make_gap_segment(duration)
                if gap is not None:
                    self.timeline = self.timeline[:index] + [gap] + self.timeline[index + 1:]
                    continue
            self.timeline = self.timeline[:index] + self.timeline[index + 1:]
        self.selected_element_ids = set()
        if cut_sub_tracks:
            focus_track = self.current_track if self.current_track in cut_sub_tracks else cut_sub_tracks[0]
            self.focused_element = self._refocus_element_after_delete(focus_track)
        else:
            self.focused_element = None
        self.current_time = min(float(self.current_time), self.timeline_duration()) if self.timeline else 0.0
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("قص العناصر", before_state)
        self.playback_requested = resume_playback
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم قص العناصر"), wait_for_ui=False)

    def _element_media_class(self, entry):
        item_type = str(entry.get("type", "") or "")
        if item_type in ("sound_effect", "background_audio"):
            return "audio"
        if item_type in ("image", "video"):
            return "video"
        if item_type == "text":
            return "text"
        if item_type:
            return item_type
        return "video"

    def _target_item_type(self, track, media_class, entry):
        if track == SOUND_EFFECTS_TRACK:
            return "sound_effect"
        if track == BACKGROUND_AUDIO_TRACK:
            return "background_audio"
        if track == TEXT_TRACK:
            return "text"
        if track == SECONDARY_VIDEO_TRACK:
            if media_class == "image" or str(entry.get("type", "") or "") == "image":
                return "image"
            return "video"
        return str(entry.get("type", "") or "") or media_class

    def _segment_from_entry(self, entry, at_time):
        start = max(0.0, at_time + float(entry.get("start", 0.0) or 0.0))
        end = max(start, at_time + float(entry.get("end", 0.0) or 0.0))
        return TimelineSegment(
            str(entry.get("path", "") or ""),
            start,
            end,
            speed=float(entry.get("speed", 1.0) or 1.0),
            audio_volume=float(entry.get("audio_volume", 1.0) if entry.get("audio_volume", 1.0) is not None else 1.0),
            audio_path=str(entry.get("audio_path", "") or ""),
            audio_start=entry.get("audio_start"),
            navigation_group=str(entry.get("navigation_group", "") or ""),
            source_file_id=str(entry.get("source_file_id", "") or ""),
            source_file_name=str(entry.get("source_file_name", "") or ""),
            transition=str(entry.get("transition", "") or ""),
            transition_duration=float(entry.get("transition_duration", 1.0) or 1.0),
        )

    def OnPasteElements(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        clipboard = self.element_clipboard
        if not clipboard or not clipboard.get("items"):
            system_items = self._read_clipboard_from_system()
            if system_items:
                clipboard = {"track": self.current_track, "items": system_items}
            else:
                self.say(tr("لا توجد عناصر في حافظة العناصر"), wait_for_ui=False)
                return
        track = self.current_track
        at_time = max(0.0, float(self.current_time))
        accepted = track_media_types(track)
        rejected = 0
        pasted = []
        for entry in clipboard.get("items", []):
            media_class = self._element_media_class(entry)
            if media_class not in accepted:
                rejected += 1
                continue
            copy_item = copy.deepcopy(entry)
            copy_item["id"] = uuid.uuid4().hex
            copy_item["start"] = at_time + float(entry.get("start", 0.0) or 0.0)
            copy_item["end"] = max(copy_item["start"], at_time + float(entry.get("end", 0.0) or 0.0))
            copy_item["type"] = self._target_item_type(track, media_class, entry)
            pasted.append(copy_item)
        if rejected:
            self.say(tr("تم تجاهل {count} عناصر لا يقبلها التراك الحالي").format(count=rejected), wait_for_ui=False)
        if not pasted:
            return
        resume_playback = self.stop_playback_for_timeline_edit("paste_elements")
        before_state = self.capture_edit_state()
        if track == MAIN_VIDEO_TRACK:
            new_segments = [self._segment_from_entry(entry, at_time) for entry in pasted]
            if not self.timeline:
                self.timeline = new_segments
            else:
                index, _segment, _position = self.locate_timeline_segment(at_time)
                left, right = split_timeline_segment(self.timeline, index, at_time)
                self.timeline = self.timeline[:index] + left + new_segments + right + self.timeline[index + 1:]
            self.last_insert_end = at_time + max(float(entry.get("end", 0.0) or 0.0) for entry in pasted)
            self.current_time = at_time
            self.focused_element = self._element_dict(new_segments[0])
        else:
            storage = self._track_storage_for(track)
            for copy_item in pasted:
                self._resolve_insert_overlap(storage, copy_item["start"])
                if should_ripple(self.ripple_mode):
                    ripple_shift(
                        {track: storage},
                        copy_item["start"],
                        float(copy_item["end"]) - float(copy_item["start"]),
                        self.ripple_mode,
                    )
                self._insert_sorted(storage, copy_item)
            self.last_insert_end = at_time + max(float(entry.get("end", 0.0) or 0.0) for entry in pasted)
            self.current_time = at_time
            self.focused_element = dict(pasted[0])
        self.is_dirty = True
        self.record_edit("لصق العناصر", before_state)
        self.playback_requested = resume_playback
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم لصق {count} عناصر").format(count=len(pasted)), wait_for_ui=False)

    def OnToggleRippleMode(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        order = ("per_track", "all_tracks", "off")
        current = self.ripple_mode
        index = order.index(current) if current in order else 0
        next_mode = order[(index + 1) % len(order)]
        self.ripple_mode = next_mode
        set_ripple_mode(next_mode)
        self.refresh_menu_bar()
        self.say(tr("وضع Ripple: {mode}").format(mode=next_mode), wait_for_ui=False)

    def OnSetRippleModeValue(self, mode):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        mode = normalize_ripple_mode(mode)
        self.ripple_mode = mode
        set_ripple_mode(mode)
        self.refresh_menu_bar()
        self.say(tr("وضع Ripple: {mode}").format(mode=mode), wait_for_ui=False)

    def OnNudgeElementLeft(self, event=None):
        self._nudge_focused_element(-NUDGE_STEP_SAMPLES)

    def OnNudgeElementRight(self, event=None):
        self._nudge_focused_element(NUDGE_STEP_SAMPLES)

    def OnMoveElementToPreviousTrack(self, event=None):
        self._move_focused_element(-1)

    def OnMoveElementToNextTrack(self, event=None):
        self._move_focused_element(1)

    def _nudge_focused_element(self, step_samples):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        element = self.focused_element
        if not element:
            self.say(tr("لا يوجد عنصر مركّز للإزاحة"), wait_for_ui=False)
            return
        engine = build_engine_from_player(self)
        track_key, item_id = self._find_engine_item(engine, element)
        if track_key is None:
            self.say(tr("العنصر المركّز غير موجود على الخط الزمني"), wait_for_ui=False)
            return
        result = nudge_item(engine, track_key, item_id, step_samples, self.ripple_mode)
        if not result.ok:
            self.say(tr(result.announcement), wait_for_ui=False)
            return
        self._finish_engine_result(result, "إزاحة العنصر")

    def _move_focused_element(self, direction):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        element = self.focused_element
        if not element:
            self.say(tr("لا يوجد عنصر مركّز للنقل"), wait_for_ui=False)
            return
        engine = build_engine_from_player(self)
        track_key, item_id = self._find_engine_item(engine, element)
        if track_key is None:
            self.say(tr("العنصر المركّز غير موجود على الخط الزمني"), wait_for_ui=False)
            return
        target_key = next_track(track_key) if direction > 0 else previous_track(track_key)
        result = move_to_track(engine, track_key, target_key, item_id, self.ripple_mode)
        if not result.ok:
            self.say(tr(result.announcement), wait_for_ui=False)
            return
        self._finish_engine_result(result, "نقل العنصر")

    def _find_engine_item(self, engine, element):
        item_id = element_identifier(element)
        track_key, _found = engine.find_item(item_id)
        if track_key is not None:
            return track_key, item_id
        estart, eend = item_bounds(element)
        for key, track in engine.tracks.items():
            if key == MAIN_VIDEO_TRACK:
                continue
            for item in track.items:
                if abs(item.timeline_start - estart) < 1e-6 and abs(item.timeline_end - eend) < 1e-6:
                    return key, item.id
        return None, None

    def _storage_index_of(self, storage, item_id):
        for index, item in enumerate(storage):
            if isinstance(item, dict) and str(item.get("id", "") or "") == item_id:
                return index
        for index, item in enumerate(storage):
            if element_identifier(item) == item_id:
                return index
        return None

    def _engine_result_focused_element(self, result):
        target = result.track_key
        item_id = result.item_id
        storage = self._track_storage_for(target)
        found = self._storage_index_of(storage, item_id)
        if found is not None:
            self.focused_element = dict(storage[found])
            return
        if target == MAIN_VIDEO_TRACK:
            payload = result.segment_payload
            if isinstance(payload, dict):
                self.focused_element = dict(payload)
            return
        start = float(result.new_start_seconds or 0.0)
        length = float(result.length_seconds or 0.0)
        for item in storage:
            s, e = item_bounds(item)
            if abs(s - start) < 1e-6 and abs(e - (start + length)) < 1e-6:
                self.focused_element = dict(item)
                return
        self.focused_element = self._element_near_time(storage, start)

    def _finish_engine_result(self, result, operation):
        resume_playback = self.stop_playback_for_timeline_edit("nudge_element")
        before_state = self.capture_edit_state()
        try:
            commit_engine_to_player(self, result.ops)
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored(operation, error, "nudge_element")
            return
        self._engine_result_focused_element(result)
        self.current_time = min(float(self.current_time), self.timeline_duration())
        self.is_dirty = True
        self.record_edit(operation, before_state)
        self.playback_requested = resume_playback
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self._announce_engine_result(result)

    def _announce_engine_result(self, result):
        message = result.announcement
        if message == "nudge_success":
            message = tr("تم إزاحة العنصر، يبدأ عند {time}").format(
                time=self.spoken_time(result.new_start_seconds)
            )
        elif message == "move_success":
            message = tr("تم نقل العنصر إلى التراك {number} {label}").format(
                number=track_index(result.track_key) + 1,
                label=tr(track_label(result.track_key)),
            )
        else:
            message = tr(message)
        self.say(message, wait_for_ui=False)

    def _numpad_key_owned_by_focus(self):
        try:
            focused = wx.Window.FindFocus()
        except Exception:
            return False
        if focused is None:
            return False
        if isinstance(focused, (wx.TextCtrl, wx.ComboBox, wx.SearchCtrl, wx.SpinCtrl, wx.SpinCtrlDouble)):
            return True
        try:
            top_level = focused.GetTopLevelParent()
        except AttributeError:
            top_level = wx.GetTopLevelParent(focused)
        return top_level is not self

    def OnToggleTrackMuteValue(self, track):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        before_state = self.capture_edit_state()
        if track in self.muted_tracks:
            self.muted_tracks.discard(track)
            muted = False
        else:
            self.muted_tracks.add(track)
            muted = True
        self.is_dirty = True
        self.record_edit("تبديل كتم التراك", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        label = tr("التراك {number} {label}").format(number=track_index(track) + 1, label=tr(track_label(track)))
        if muted:
            self.say(tr("تم كتم {label}").format(label=label), wait_for_ui=False)
        else:
            self.say(tr("تم رفع الكتم عن {label}").format(label=label), wait_for_ui=False)

    def OnToggleTrackSoloValue(self, track):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.require_open_file():
            return
        before_state = self.capture_edit_state()
        if track in self.solo_tracks:
            self.solo_tracks.discard(track)
            solo = False
        else:
            self.solo_tracks.add(track)
            solo = True
        self.is_dirty = True
        self.record_edit("تبديل عزل التراك", before_state)
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        label = tr("التراك {number} {label}").format(number=track_index(track) + 1, label=tr(track_label(track)))
        if solo:
            self.say(tr("تم عزل {label}").format(label=label), wait_for_ui=False)
        else:
            self.say(tr("تم رفع العزل عن {label}").format(label=label), wait_for_ui=False)

    def OnSpeakEditorStatus(self, event=None):
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        ripple_labels = {"per_track": "لكل تراك", "all_tracks": "كل التراكات", "off": "مطفأ"}
        mode_label = ripple_labels.get(normalize_ripple_mode(self.ripple_mode), self.ripple_mode)
        parts = [tr("المحرر الاحترافي"), tr("وضع Ripple: {mode}").format(mode=mode_label)]
        muted = set(getattr(self, "muted_tracks", ()) or ())
        if muted:
            names = "، ".join(
                tr("التراك {number} {label}").format(number=track_index(key) + 1, label=tr(track_label(key)))
                for key in sorted(muted, key=track_index)
            )
            parts.append(tr("الأصوات المكتومة: {names}").format(names=names))
        else:
            parts.append(tr("لا توجد تراكات مكتومة"))
        solo = set(getattr(self, "solo_tracks", ()) or ())
        if solo:
            names = "، ".join(
                tr("التراك {number} {label}").format(number=track_index(key) + 1, label=tr(track_label(key)))
                for key in sorted(solo, key=track_index)
            )
            parts.append(tr("الأصوات المعزولة: {names}").format(names=names))
        parts.append(self.track_announcement())
        self.say("، ".join(parts), wait_for_ui=False)

    def _clear_element_selection(self):
        if getattr(self, "selected_element_ids", None):
            self.selected_element_ids = set()

    def track_accepts_media(self, media_type):
        if get_program_mode() != PROFESSIONAL_MODE:
            return True
        if media_type in track_media_types(self.current_track):
            return True
        self.say(self.track_rejection_message(media_type))
        return False

    def track_rejection_message(self, media_type):
        type_label = {
            "video": "الفيديو",
            "audio": "الصوت",
            "text": "النصوص",
        }.get(media_type, media_type)
        accepted_keys = tracks_accepting(media_type)
        if not accepted_keys:
            return tr("هذا التراك لا يقبل {type}").format(type=tr(type_label))
        numbers = " أو ".join(str(track_index(key) + 1) for key in accepted_keys)
        return tr("هذا التراك لا يقبل {type}، انتقل إلى التراك {numbers} لإدراج {type}").format(
            type=tr(type_label),
            numbers=numbers,
        )

    def OnGrokKeysSettings(self, event=None):
        self.OnCaptionsSettings()

    def OnChangeApplicationName(self, event=None):
        previous_name = self.application_display_name()
        dialog = ApplicationNameDialog(self, get_custom_app_name())
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            requested_name = dialog.result_name or ""
        finally:
            dialog.Destroy()

        set_custom_app_name(requested_name)
        new_name = self.application_display_name()
        self.update_all_window_titles()
        self.say(tr("تم حفظ اسم التطبيق"))
        try:
            threading.Thread(
                target=self._ensure_application_name_shell_integration,
                args=(new_name, previous_name, tr("مشروع صانع الفيديو")),
                daemon=True,
            ).start()
        except Exception as error:
            append_problem("change_application_name", str(error), exception=error)

    def _ensure_application_name_shell_integration(self, app_name, previous_app_name, project_type_name):
        try:
            from video_maker.windows_shell_integration import ensure_windows_shell_integration

            desktop_updated = ensure_windows_shell_integration(
                app_name=app_name,
                project_type_name=project_type_name,
                create_desktop_shortcut=True,
                previous_app_name=previous_app_name,
            )
        except Exception as error:
            append_problem("change_application_name", str(error), exception=error)
            return
        if desktop_updated:
            wx.CallAfter(self.say, "تم تحديث اختصار سطح المكتب")

    def OnCopyProblemLog(self, event=None):
        trace_event("diagnostic_log", "copy_menu_selected", immediate=True)
        if copy_problem_log_to_clipboard():
            self.say(tr("تم نسخ سجل الأخطاء"))
        else:
            # self.say(tr("تعذر نسخ سجل الأخطاء"))
            wx.MessageBox(tr("تعذر نسخ سجل الأخطاء."), tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnExportProblemLog(self, event=None):
        trace_event("diagnostic_log", "export_menu_selected", immediate=True)
        with wx.FileDialog(
            self,
            tr("تصدير سجل الأخطاء كملف txt"),
            wildcard="Text files (*.txt)|*.txt",
            defaultFile=tr("سجل الأخطاء") + ".txt",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dialog:
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            path = dialog.GetPath()
        try:
            if export_problem_log(path):
                self.say(tr("تم تصدير سجل الأخطاء"))
            else:
                self.say(tr("سجل الأخطاء فارغ"))
        except Exception as error:
            # self.say(tr("تعذر تصدير سجل الأخطاء"))
            wx.MessageBox(tr("تعذر تصدير سجل الأخطاء: {error}").format(error=error), tr("خطأ"), wx.OK | wx.ICON_ERROR)

    def OnClearProblemLog(self, event=None):
        clear_problem_log()
        self.say(tr("تم حذف سجل الأخطاء"))

    def OnAbout(self, event=None):
        message = (
            f"{tr('صانع الفيديو')}\n"
            f"{tr('تصميم وتطوير مصطفى البحيري')}\n"
            f"{tr('برنامج بسيط لتحرير الفيديو والصوت وإضافة الصور والنصوص والمؤثرات')}\n"
            f"{tr('المساهمون والمجربون والمتعاونون معنا في النشر')}\n"
            f"{tr('حمزة أبو سليمة: إضافة ميزات')}\n"
            f"{tr('حسن ماهر: تجربة متعمقة ومتواصلة للتطبيق واكتشاف الأخطاء')}\n"
            f"{tr('حسام أسامة: تجربة متواصلة واقتراح ميزات')}\n"
            f"{tr('أحمد مختار: تجربة متعمقة للتطبيق واقتراح أفكار وشروحات للتطبيق')}\n"
            f"{tr('كمال مرعي: تجربة مستمرة وتقييم للميزات واقتراح أفكار')}\n"
            f"{tr('محمود حسن كمال: تجربة متواصلة واقتراح ميزات')}\n"
            f"{tr('رحاب عبد العاطي: 10 سنوات من العطاء، نشر التطبيق باستمرار ودعم المحتوى الذي يخصنا')}\n"
            f"{tr('سالي من سوريا: تجربة التطبيق واقتراح ميزات')}\n"
            f"{tr('صالح أبو سلامة: تجربة التطبيق واقتراح ميزات')}\n"
            f"{tr('حسن البرشومي: تجربة معمقة للتطبيق واقتراح ميزات')}\n"
            f"{tr('سيد السعيد الأزهري: تجربة معمقة للتطبيق')}\n"
            f"{tr('حيدر محمد: تجربة معمقة للتطبيق واقتراح ميزات')}\n"
            f"{tr('رجب إبراهيم: اقتراح ميزات ومساهمة في نشر التطبيق')}\n"
            f"{tr('خالد الشرقاوي: مساهمة بشرح البرنامج ونشره على قناته على اليوتيوب')}\n"
            f"{tr('محمد فتحي شعراوي: تجربة معمقة للتطبيق واقتراح ميزات')}\n"
            f"{tr('بليغ عبد النبي زيدان: تجربة معمقة للتطبيق واقتراح ميزات')}\n"
            f"{tr('ياسر أحمد: نشر متواصل لكل ما أقوم به')}"
        )
        # self.say(tr("حول"))
        dialog = ReadOnlyTextDialog(self, "حول", message)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

