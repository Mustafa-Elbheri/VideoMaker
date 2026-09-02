from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *
from video_maker.auto_subtitles_module import _debug


@publish_player_methods
class PlayerLogicalMediaMixin:
    def ensure_timeline_logical_files(self):
        """ترقية/إكمال هوية الملفات دون اعتبار القصاصات ملفات مستقلة."""
        updated = ensure_logical_file_metadata(
            self.timeline,
            getattr(self, "video_path", ""),
            getattr(self, "edit_points", []),
        )
        if updated != self.timeline:
            self.timeline = updated
        return self.timeline

    def timeline_file_ranges(self):
        # الاسم بقي للتوافق مع القوائم والاختصارات، لكن الناتج الآن ملفات منطقية
        # فريدة، ولكل ملف أكثر من فترة إذا أُدرج ملف آخر في منتصفه.
        self.ensure_timeline_logical_files()
        return logical_file_entries(self.timeline)

    def timeline_file_at_current_time(self):
        self.ensure_timeline_logical_files()
        return logical_file_at_time(self.timeline, self.current_time)

    def announce_timeline_file(self, item, ranges):
        if not item:
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self.say(tr("أنت في الملف رقم {number} من {count}: {name}").format(
            number=item.index + 1, count=len(ranges), name=item.name
        ))

    def OnNextTimelineFile(self, event=None):
        if not self.require_open_file():
            return
        current, ranges = self.timeline_file_at_current_time()
        if not current:
            return
        index = min(current.index + 1, len(ranges) - 1)
        target = ranges[index]
        self.current_time = target.first_start
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.announce_timeline_file(target, ranges)

    def OnPreviousTimelineFile(self, event=None):
        if not self.require_open_file():
            return
        current, ranges = self.timeline_file_at_current_time()
        if not current:
            return
        index = max(current.index - 1, 0)
        target = ranges[index]
        self.current_time = target.first_start
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.announce_timeline_file(target, ranges)

    def timeline_file_reorder_actions(self):
        if not getattr(self, "timeline", None):
            return []
        current, ranges = self.timeline_file_at_current_time()
        if not current or len(ranges) <= 1:
            return []
        actions = []
        if current.index > 0:
            actions.append(("move_up", tr("رفع للأعلى")))
        if current.index < len(ranges) - 1:
            actions.append(("move_down", tr("خفض للأسفل")))
        return actions

    def _map_reordered_timeline_time(self, time_value, mapping, prefer_previous=False):
        if not mapping:
            return float(time_value or 0.0)
        old_total = max(old_end for old_start, old_end, _new_start, _new_end in mapping)
        new_total = max(new_end for _old_start, _old_end, new_start, new_end in mapping)
        value = max(0.0, min(float(time_value or 0.0), old_total))
        if abs(value - old_total) <= 1e-6:
            return new_total
        for old_start, old_end, new_start, new_end in mapping:
            if prefer_previous:
                in_range = old_start < value <= old_end or (old_start == 0 and value == 0)
            else:
                in_range = old_start <= value < old_end
            if in_range:
                return max(0.0, min(new_end, new_start + (value - old_start)))
        closest = min(mapping, key=lambda item: min(abs(value - item[0]), abs(value - item[1])))
        old_start, old_end, new_start, new_end = closest
        if abs(value - old_end) < abs(value - old_start):
            return new_end
        return new_start

    def _remap_reordered_timed_items(self, items, mapping):
        remapped = []
        new_total = max((new_end for _old_start, _old_end, _new_start, new_end in mapping), default=0.0)
        for item in items or []:
            updated = dict(item)
            start = self._map_reordered_timeline_time(updated.get("start", 0.0), mapping)
            end = self._map_reordered_timeline_time(updated.get("end", start), mapping, prefer_previous=True)
            if end < start:
                start, end = end, start
            updated["start"] = max(0.0, min(start, new_total))
            updated["end"] = max(updated["start"], min(end, new_total))
            remapped.append(updated)
        return remapped

    def move_current_timeline_file(self, direction):
        if not self.require_open_file():
            return
        current, ranges = self.timeline_file_at_current_time()
        if not current or len(ranges) <= 1:
            return
        target_index = current.index + direction
        if target_index < 0 or target_index >= len(ranges):
            self.announce_timeline_file(current, ranges)
            return
        before_state = self.capture_edit_state()
        ordered = list(ranges)
        ordered[current.index], ordered[target_index] = ordered[target_index], ordered[current.index]
        mapping = []
        reordered_timeline = []
        cursor = 0.0
        new_current_start = 0.0
        for entry in ordered:
            if entry.file_id == current.file_id:
                new_current_start = cursor
            for segment, (old_start, old_end) in zip(entry.segments, entry.intervals):
                duration = segment.duration
                new_start = cursor
                new_end = cursor + duration
                mapping.append((old_start, old_end, new_start, new_end))
                reordered_timeline.append(segment)
                cursor = new_end
        self.timeline = reordered_timeline
        self.visual_items = self._remap_reordered_timed_items(self.visual_items, mapping)
        self.background_audio_items = self._remap_reordered_timed_items(self.background_audio_items, mapping)
        self.edit_points = self._remap_reordered_timed_items(self.edit_points, mapping)
        self.current_time = new_current_start
        self.start_time = None
        self.end_time = None
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.is_dirty = True
        self.record_edit("ترتيب الملف الحالي في الخط الزمني", before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        updated_current, updated_ranges = self.timeline_file_at_current_time()
        self.announce_timeline_file(updated_current, updated_ranges)

    def OnMoveCurrentTimelineFileUp(self, event=None):
        self.move_current_timeline_file(-1)

    def OnMoveCurrentTimelineFileDown(self, event=None):
        self.move_current_timeline_file(1)

    def OnDeleteCurrentTimelineFile(self, event=None):
        if not self.require_open_file():
            return
        current, ranges = self.timeline_file_at_current_time()
        if not current:
            return
        before_state = self.capture_edit_state()
        first_start = current.first_start
        # قد تكون أجزاء الملف متفرقة بسبب إدراج ملف ثانٍ في منتصفه؛ نحذف من
        # النهاية للبداية كي تظل الأزمنة السابقة صحيحة.
        for start_time, end_time in file_intervals_descending(current):
            self.timeline = delete_range(self.timeline, start_time, end_time)
            self.adjust_timed_items_after_delete(start_time, end_time)
        self.current_time = min(first_start, self.timeline_duration())
        self.start_time = None
        self.end_time = None
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.is_dirty = True
        if not self.timeline:
            self.video_path = ""
            self.media_kind = "none"
            self.reset_main_audio_override_state()
            self.playback_requested = False
            self.pending_play = False
            self.current_segment_index = None
            self.active_media_path = ""
            self.invalidate_pending_media_load()
            try:
                self.media_ctrl.Stop()
            except Exception:
                pass
            self.stop_original_audio_playback()
            self.stop_background_audio_playback()
        else:
            self.reload_current_position()
        self.record_edit("حذف الملف الحالي من الخط الزمني", before_state)
        self.refresh_menu_bar()
        self.say(tr("تم حذف الملف الحالي من الخط الزمني"))

    def OnAddVideo(self, event=None):
        if not self.require_open_file():
            return
        if not self.track_accepts_media("video"):
            return
        if self.media_kind == "audio":
            self.OnInsertWorkVideo()
            return
        if get_program_mode() == PROFESSIONAL_MODE and self.current_track == SECONDARY_VIDEO_TRACK:
            self.insert_secondary_video()
            return
        new_video_path = ask_video_path()
        if new_video_path:
            before_state = self.capture_edit_state()
            insert_time = self.current_time
            duration = get_video_duration(new_video_path)
            self.timeline = insert_segments(self.timeline, insert_time, [new_file_segment(new_video_path, 0, duration)])
            self.shift_timed_items_after_insert(insert_time, duration)
            self.add_edit_point("video", insert_time, insert_time + duration, "timeline", mode="insert")
            self.is_dirty = True
            self.record_edit("إضافة فيديو", before_state)
            self.reload_current_position()
            print(f"Added video: {new_video_path} at {self.current_time} seconds")
            self.say(speech_messages.VIDEO_INSERTED)

    def insert_secondary_video(self):
        new_video_path = ask_video_path()
        if not new_video_path:
            return
        before_state = self.capture_edit_state()
        insert_time = self.current_time
        duration = get_video_duration(new_video_path)
        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "type": "video",
            "path": new_video_path,
            "name": os.path.splitext(os.path.basename(new_video_path))[0],
            "start": insert_time,
            "end": insert_time + duration,
            "transition": self.transition_name,
        }
        self.b_roll_items.append(item)
        self.add_edit_point("b_roll", insert_time, insert_time + duration, "b_roll", item_id=item_id)
        self.last_insert_end = insert_time + duration
        self.current_time = insert_time
        self.is_dirty = True
        self.record_edit("إدراج فيديو ثانوي", before_state)
        self.reload_current_position()
        self.say("تم إدراج المقطع الثانوي")

    def choose_files(self, title, wildcard, kind="", dialog_key=""):
        with wx.FileDialog(self, tr(title), wildcard=wildcard, style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, kind, dialog_key)
            if dialog.ShowModal() == wx.ID_CANCEL:
                return []
            paths = sorted(dialog.GetPaths(), key=natural_sort_key)
            remember_media_paths(paths, kind, dialog_key)
            return paths

    def OnChooseWorkImages(self, event=None):
        paths = self.choose_files("اختيار صور للعمل", IMAGE_WILDCARD, "image", "work_images")
        if paths:
            self.work_images = paths
            self.say(tr("تم اختيار {count} صورة للعمل").format(count=len(paths)))

    def OnChooseWorkVideos(self, event=None):
        paths = self.choose_files("اختيار فيديوهات للعمل", VIDEO_WILDCARD, "video", "work_videos")
        if paths:
            self.work_videos = paths
            self.say(tr("تم اختيار {count} فيديو للعمل").format(count=len(paths)))

    def OnSetImageDuration(self, event=None):
        dialog = wx.TextEntryDialog(self, "اكتب مدة كل صورة بالثواني", "اختيار مدة كل صورة", str(self.default_image_duration))
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
        self.default_image_duration = duration
        self.say(f"مدة كل صورة {duration:g} ثانية")

    def OnSetTransition(self, event=None):
        dialog = wx.SingleChoiceDialog(self, "اختر تأثير الانتقال", "تأثيرات الانتقالات", TRANSITIONS)
        current = TRANSITIONS.index(self.transition_name) if self.transition_name in TRANSITIONS else 0
        dialog.SetSelection(current)
        if dialog.ShowModal() == wx.ID_OK:
            self.transition_name = dialog.GetStringSelection()
            self.say(f"تأثير الانتقال {self.transition_name}")
        dialog.Destroy()

    def OnStopAtInsertEdge(self, event=None):
        if not self.require_open_file():
            return
        if self.last_insert_end is None:
            self.say("لا توجد حافة إضافة محفوظة")
            return
        self.current_time = min(self.last_insert_end, self.timeline_duration())
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.say("تم الوقوف عند حافة ما أضفت")

    def require_audio_project(self):
        if not self.require_open_file():
            return False
        if self.media_kind != "audio":
            self.say("هذا الخيار متاح مع الملف الصوتي")
            return False
        return True

    def selected_or_image_duration(self):
        selected = self.selected_effect_range()
        if selected:
            return selected
        start = self.current_time
        end = min(self.timeline_duration(), start + self.default_image_duration)
        return start, end

    def OnDistributeWorkImages(self, event=None):
        if not self.require_audio_project():
            return
        if not self.work_images:
            self.OnChooseWorkImages()
            if not self.work_images:
                return
        duration = self.timeline_duration()
        count = len(self.work_images)
        if count <= 0 or duration <= 0:
            return
        before_state = self.capture_edit_state()
        each = duration / count
        self.visual_items = []
        self.edit_points = [point for point in normalize_edit_points(self.edit_points) if point.get("target") != "visual"]
        for index, path in enumerate(self.work_images):
            start = index * each
            end = duration if index == count - 1 else (index + 1) * each
            item_id = uuid.uuid4().hex
            self.visual_items.append({"id": item_id, "type": "image", "path": path, "start": start, "end": end, "transition": self.transition_name})
            self.add_edit_point("image", start, end, "visual", item_id=item_id)
        self.last_insert_end = duration
        self.is_dirty = True
        self.record_edit("توزيع الصور", before_state)
        self.refresh_menu_bar()
        self.say("تم توزيع الصور على الملف الصوتي")

    def OnDistributeWorkVideos(self, event=None):
        if not self.require_audio_project():
            return
        if not self.work_videos:
            self.OnChooseWorkVideos()
            if not self.work_videos:
                return
        duration = self.timeline_duration()
        count = len(self.work_videos)
        if count <= 0 or duration <= 0:
            return
        before_state = self.capture_edit_state()
        each = duration / count
        self.visual_items = []
        self.edit_points = [point for point in normalize_edit_points(self.edit_points) if point.get("target") != "visual"]
        for index, path in enumerate(self.work_videos):
            start = index * each
            end = duration if index == count - 1 else (index + 1) * each
            item_id = uuid.uuid4().hex
            self.visual_items.append({"id": item_id, "type": "video", "path": path, "start": start, "end": end, "transition": self.transition_name})
            self.add_edit_point("video", start, end, "visual", item_id=item_id)
        self.last_insert_end = duration
        self.is_dirty = True
        self.record_edit("توزيع الفيديوهات", before_state)
        self.refresh_menu_bar()
        self.say("تم توزيع الفيديوهات على الملف الصوتي")

    def choose_work_video(self):
        if not self.work_videos:
            self.OnChooseWorkVideos()
        if not self.work_videos:
            return ""
        choices = [os.path.basename(path) for path in self.work_videos]
        dialog = wx.SingleChoiceDialog(self, "اختر الفيديو", "إدراج فيديو", choices)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return ""
        index = dialog.GetSelection()
        dialog.Destroy()
        if index == wx.NOT_FOUND or index >= len(self.work_videos):
            return ""
        return self.work_videos[index]

    def OnInsertWorkVideo(self, event=None):
        if not self.require_audio_project():
            return
        selected = self.selected_effect_range()
        if not selected:
            wx.CallLater(180, self.say, "قم بتحديد نقطة بداية ونقطة نهاية", True, False)
            return
        path = self.choose_work_video()
        if not path:
            return
        start_time, end_time = selected
        video_duration = get_video_duration(path)
        if video_duration > end_time - start_time:
            result = wx.MessageBox("الفيديو أكبر من التحديد. هل تريد توسيع نقطة النهاية؟", "توسيع التحديد", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
            if result == wx.YES:
                end_time = min(self.timeline_duration(), start_time + video_duration)
            else:
                return
        before_state = self.capture_edit_state()
        item_id = uuid.uuid4().hex
        self.visual_items.append({"id": item_id, "type": "video", "path": path, "start": start_time, "end": end_time, "transition": self.transition_name})
        self.add_edit_point("video", start_time, end_time, "visual", item_id=item_id)
        self.last_insert_end = end_time
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("إدراج فيديو", before_state)
        self.refresh_menu_bar()
        self.say("تم إدراج الفيديو")

    def OnReplaceChromaBackground(self, event=None):
        if not self.has_video() or self.media_kind == "audio":
            self.say(tr("افتح فيديو يحتوي على كرومة خضراء أولا"))
            return
        timeline_snapshot = list(self.timeline)
        duration = total_duration(timeline_snapshot)
        if duration <= 0:
            self.say(tr("تعذر تحديد مدة الفيديو"))
            return
        background_snapshot = [dict(item) for item in self.background_audio_items]
        dialog = ChromaBackgroundDialog(
            self,
            analyze_callback=lambda: analyze_timeline_chroma(timeline_snapshot),
            preview_callback=lambda options: build_chroma_preview(
                timeline_snapshot,
                options,
                self.current_time,
                background_snapshot,
            ),
            speech_callback=self.say,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            options = dialog.options
        finally:
            dialog.Destroy()
        if not options:
            return
        self.start_timeline_transform(
            "chroma_background",
            tr("جاري استبدال خلفية الفيديو"),
            tr("نسبة استبدال خلفية الفيديو {percent} بالمئة"),
            tr("حالة استبدال خلفية الفيديو"),
            tr("شريط تقدم استبدال خلفية الفيديو"),
            tr("إلغاء استبدال خلفية الفيديو"),
            tr("جاري إلغاء استبدال خلفية الفيديو"),
            lambda progress, cancelled: build_chroma_background_segment(
                timeline_snapshot,
                options,
                progress,
                cancelled,
            ),
            (0.0, duration),
            tr("استبدال خلفية الفيديو إذا كانت كرومة"),
            tr("تم استبدال خلفية الفيديو"),
            scale_timed_items=False,
            preserve_continuous_audio=True,
        )

    def OnAddWatermark(self, event=None):
        if not self.has_video() or self.media_kind == "audio":
            self.say("افتح فيديو أولا لإضافة علامة مائية")
            return
        dialog = AddWatermarkDialog(self)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            options = dialog.options
        finally:
            dialog.Destroy()
        if not options:
            return
        timeline_snapshot = list(self.timeline)
        duration = total_duration(timeline_snapshot)
        if duration <= 0:
            self.say("تعذر تحديد مدة الفيديو")
            return
        self.start_timeline_transform(
            "watermark",
            tr("جاري إضافة العلامة المائية"),
            tr("نسبة إضافة العلامة المائية {percent} بالمئة"),
            tr("حالة إضافة العلامة المائية"),
            tr("شريط تقدم إضافة العلامة المائية"),
            tr("إلغاء إضافة العلامة المائية"),
            tr("جاري إلغاء إضافة العلامة المائية"),
            lambda progress, cancelled: build_watermarked_segment(timeline_snapshot, options, progress, cancelled),
            (0.0, duration),
            "إضافة علامة مائية",
            "تمت إضافة العلامة المائية على كامل الفيديو",
            scale_timed_items=False,
            preserve_continuous_audio=True,
        )

    def OnRemoveWatermark(self, event=None):
        if not self.has_video() or self.media_kind == "audio":
            self.say("افتح الفيديو الذي يحتوي على العلامة المائية أولا")
            return
        dialog = RemoveWatermarkDialog(self)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            options = dialog.options
        finally:
            dialog.Destroy()
        if not options:
            return
        timeline_snapshot = list(self.timeline)
        duration = total_duration(timeline_snapshot)
        if duration <= 0:
            self.say("تعذر تحديد مدة الفيديو")
            return
        self.start_timeline_transform(
            "watermark",
            tr("جاري إزالة العلامة المائية"),
            tr("نسبة إزالة العلامة المائية {percent} بالمئة"),
            tr("حالة إزالة العلامة المائية"),
            tr("شريط تقدم إزالة العلامة المائية"),
            tr("إلغاء إزالة العلامة المائية"),
            tr("جاري إلغاء إزالة العلامة المائية"),
            lambda progress, cancelled: build_watermark_removed_segment(timeline_snapshot, options, progress, cancelled),
            (0.0, duration),
            "إزالة علامة مائية",
            "تمت معالجة العلامة المائية على كامل الفيديو",
            scale_timed_items=False,
            preserve_continuous_audio=True,
        )

    def OnInsertImage(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        selected = self.selected_effect_range()
        if not selected:
            wx.CallLater(180, self.say, "قم بتحديد نقطة بداية ونقطة نهاية", True, False)
            return
        dialog = ImageOverlayDialog(self)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        options = dialog.options
        dialog.Destroy()
        if not options:
            return
        if self.media_kind == "audio":
            before_state = self.capture_edit_state()
            start_time, end_time = selected
            try:
                item_id = uuid.uuid4().hex
                self.visual_items.append({"id": item_id, "type": "image", "path": options.image_path, "start": start_time, "end": end_time, "transition": self.transition_name})
                self.add_edit_point("image", start_time, end_time, "visual", item_id=item_id)
                self.last_insert_end = end_time
                self.current_time = start_time
                self.start_time = None
                self.end_time = None
                self.is_dirty = True
                self.record_edit("إدراج صورة", before_state)
                self.refresh_menu_bar()
                self.say("تم إدراج الصورة")
            except Exception as error:
                self.apply_edit_state(before_state)
                self.notify_failed_edit_restored("إدراج صورة", error, "audio_project_image_insert")
                wx.MessageBox(f"{tr('تعذر إدراج الصورة')}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        start_time, end_time = selected
        timeline_snapshot = list(self.timeline)
        self.start_timeline_transform(
            "image",
            tr("جاري إدراج الصورة"),
            tr("نسبة إدراج الصورة {percent} بالمئة"),
            tr("حالة إدراج الصورة"),
            tr("شريط تقدم إدراج الصورة"),
            tr("إلغاء إدراج الصورة"),
            tr("جاري إلغاء إدراج الصورة"),
            lambda progress, cancelled: self.build_image_overlay_transform(timeline_snapshot, start_time, end_time, options, progress, cancelled),
            selected,
            "إدراج صورة",
            "تم إدراج الصورة",
            scale_timed_items=False,
            preserve_continuous_audio=True,
        )

    def build_image_overlay_transform(self, timeline, start_time, end_time, options, progress_callback=None, cancelled_callback=None):
        if cancelled_callback and cancelled_callback():
            raise AudioEffectPreparationCancelled()
        overlay_path, temp_dir = build_image_overlay_segment(
            timeline,
            start_time,
            end_time,
            options,
            progress_callback=progress_callback,
            cancelled_callback=cancelled_callback,
        )
        if cancelled_callback and cancelled_callback():
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise AudioEffectPreparationCancelled()
        return overlay_path, temp_dir, max(0.0, end_time - start_time)

    def OnInsertText(self, event=None):
        if get_program_mode() == PROFESSIONAL_MODE:
            return self._insert_dynamic_text()
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if not self.track_accepts_media("text"):
            return
        selected = self.selected_effect_range()
        if not selected:
            wx.CallLater(180, self.say, "قم بتحديد نقطة بداية ونقطة نهاية", True, False)
            return
        part_duration = max(0.0, float(selected[1]) - float(selected[0]))
        canvas_size = None
        if self.media_kind != "audio" and self.video_path and os.path.exists(self.video_path):
            try:
                from video_maker.text_overlay import probe_video_size_fps
                probe_width, probe_height, _probe_fps = probe_video_size_fps(self.video_path)
                canvas_size = (probe_width, probe_height)
            except Exception:
                canvas_size = None
        dialog = TextOverlayDialog(self, part_duration=part_duration, canvas_size=canvas_size)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        options = dialog.options
        dialog.Destroy()
        if not options:
            return
        is_subtitles = options.mode == "subtitles"
        self._last_overlay_options = options
        if self.media_kind == "audio":
            temp_dir = tempfile.mkdtemp(prefix="text_overlay_audio_")
            before_state = self.capture_edit_state()
            try:
                start_time, end_time = selected
                typing_mode = getattr(options, "mode", "") == "typing"
                if typing_mode:
                    text_path = os.path.join(temp_dir, "typing.mp4")
                    render_typing_video(None, text_path, options, end_time - start_time)
                    visual_type = "video"
                else:
                    text_path = os.path.join(temp_dir, "text.png")
                    render_text_image(options, text_path)
                    visual_type = "text"
                self.generated_temp_dirs.append(temp_dir)
                self.generated_temp_files.append(text_path)
                item_id = uuid.uuid4().hex
                visual_item = {"id": item_id, "type": visual_type, "path": text_path, "start": start_time, "end": end_time, "transition": self.transition_name}
                if typing_mode:
                    visual_item["is_typing"] = True
                self.visual_items.append(visual_item)
                self.add_edit_point("text", start_time, end_time, "visual", item_id=item_id)
                self.last_insert_end = end_time
                self.current_time = start_time
                self.start_time = None
                self.end_time = None
                self.is_dirty = True
                self.record_edit("إدراج نص", before_state)
                self.refresh_menu_bar()
                self.say(tr("تم إدراج الترجمة") if is_subtitles else tr("تم إدراج النص"))
            except Exception as error:
                shutil.rmtree(temp_dir, ignore_errors=True)
                self.apply_edit_state(before_state)
                self.notify_failed_edit_restored(tr("إدراج نص"), error, "audio_project_text_insert")
                self.say(tr("تعذر إدراج الترجمة") if is_subtitles else tr("تعذر إدراج النص"))
                wx.MessageBox(f"{tr('تعذر إدراج الترجمة') if is_subtitles else tr('تعذر إدراج النص')}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        start_time, end_time = selected
        typing_mode = getattr(options, "mode", "") == "typing"
        self._typing_text_transform_pending = typing_mode
        self._typing_text_audio_override_result = None
        timeline_snapshot = list(self.timeline)
        self.start_timeline_transform(
            "text",
            tr("جاري إدراج الترجمة") if is_subtitles else tr("جاري إدراج النص"),
            tr("نسبة إدراج الترجمة {percent} بالمئة") if is_subtitles else tr("نسبة إدراج النص {percent} بالمئة"),
            tr("حالة إدراج الترجمة") if is_subtitles else tr("حالة إدراج النص"),
            tr("شريط تقدم إدراج الترجمة") if is_subtitles else tr("شريط تقدم إدراج النص"),
            tr("إلغاء إدراج الترجمة") if is_subtitles else tr("إلغاء إدراج النص"),
            tr("جاري إلغاء إدراج الترجمة") if is_subtitles else tr("جاري إلغاء إدراج النص"),
            lambda progress, cancelled: self.build_text_overlay_transform(
                timeline_snapshot, start_time, end_time, options, progress, cancelled
            ),
            selected,
            tr("إدراج ترجمة") if is_subtitles else tr("إدراج نص"),
            tr("تم إدراج الترجمة") if is_subtitles else tr("تم إدراج النص"),
            scale_timed_items=False,
            preserve_continuous_audio=not typing_mode,
        )

    def _insert_dynamic_text(self):
        """إدراج عنصر نص ديناميكي على التراك النصي (الوضع الاحترافي، الخطوة 05)."""
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if not self.track_accepts_media("text"):
            return
        selected = self.selected_effect_range()
        if not selected:
            wx.CallLater(180, self.say, "قم بتحديد نقطة بداية ونقطة نهاية", True, False)
            return
        default_start = max(0.0, float(selected[0]))
        default_end = max(default_start, float(selected[1]))
        part_duration = max(0.0, default_end - default_start)
        canvas_size = None
        if self.media_kind != "audio" and self.video_path and os.path.exists(self.video_path):
            try:
                from video_maker.text_overlay import probe_video_size_fps
                probe_width, probe_height, _probe_fps = probe_video_size_fps(self.video_path)
                canvas_size = (probe_width, probe_height)
            except Exception:
                canvas_size = None
        dialog = TextOverlayDialog(
            self,
            part_duration=part_duration,
            canvas_size=canvas_size,
            range_start=default_start,
            range_end=default_end,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        options = dialog.options
        range_start = dialog.range_start
        range_end = dialog.range_end
        dialog.Destroy()
        if not options:
            return
        if range_start is None or range_end is None or range_end <= range_start:
            self.say(tr("نهاية النص يجب أن تكون بعد بدايته"), wait_for_ui=False)
            return
        is_subtitles = options.mode == "subtitles"
        self._last_overlay_options = options
        before_state = self.capture_edit_state()
        item = new_dynamic_text_item(options, range_start, range_end)
        self.visual_items.append(item)
        self.focused_element = dict(item)
        self.current_time = range_start
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("إدراج نص", before_state)
        self.refresh_menu_bar()
        self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
        self.say(tr("تم إدراج الترجمة") if is_subtitles else tr("تم إدراج النص"))

    def OnEditFocusedElement(self, event=None):
        """تعديل خيارات العنصر النصي المحدد على التراك النصي (الوضع الاحترافي، الخطوة 05)."""
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        element = self.focused_element
        if not isinstance(element, dict) or not element.get("is_dynamic"):
            self.say(tr("لا يوجد عنصر نصي محدد للتعديل"), wait_for_ui=False)
            return
        options = from_text_item(element)
        if options is None:
            self.say(tr("لا يوجد عنصر نصي محدد للتعديل"), wait_for_ui=False)
            return
        start = max(0.0, float(element.get("start", 0.0) or 0.0))
        end = max(start, float(element.get("end", 0.0) or 0.0))
        part_duration = max(0.0, end - start)
        canvas_size = None
        if self.media_kind != "audio" and self.video_path and os.path.exists(self.video_path):
            try:
                from video_maker.text_overlay import probe_video_size_fps
                probe_width, probe_height, _probe_fps = probe_video_size_fps(self.video_path)
                canvas_size = (probe_width, probe_height)
            except Exception:
                canvas_size = None
        dialog = TextOverlayDialog(
            self,
            title=tr("تعديل النص"),
            apply_label=tr("حفظ"),
            apply_name=tr("حفظ تعديل النص"),
            part_duration=part_duration,
            canvas_size=canvas_size,
            initial_options=options,
            range_start=start,
            range_end=end,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        new_options = dialog.options
        range_start = dialog.range_start
        range_end = dialog.range_end
        dialog.Destroy()
        if not new_options:
            return
        if range_start is None or range_end is None or range_end <= range_start:
            self.say(tr("نهاية النص يجب أن تكون بعد بدايته"), wait_for_ui=False)
            return
        item_id = element.get("id")
        before_state = self.capture_edit_state()
        for index, existing in enumerate(self.visual_items):
            if existing.get("id") == item_id:
                updated = new_dynamic_text_item(new_options, range_start, range_end)
                updated["id"] = item_id
                self.visual_items[index] = updated
                self.focused_element = dict(updated)
                self.current_time = range_start
                self.start_time = None
                self.end_time = None
                self.is_dirty = True
                self.record_edit("تعديل النص", before_state)
                self.on_text_options_changed(updated, new_options)
                self.refresh_menu_bar()
                self.apply_edit_state(self.capture_edit_state(), focus_timeline=False)
                self.say(tr("تم تعديل الترجمة") if new_options.mode == "subtitles" else tr("تم تعديل النص"))
                return
        self.say(tr("العنصر المحدد غير موجود على التراك النصي"), wait_for_ui=False)

    def on_text_options_changed(self, item, options):
        """تُستدعى بعد تعديل خيارات عنصر نصي ديناميكي: تُعيد بناء المعاينة الحية (الخطوة 08)."""
        self._text_preview_fingerprint = ""
        self.request_preview_rebuild()

    def build_text_overlay_transform(self, timeline, start_time, end_time, options, progress_callback=None, cancelled_callback=None):
        typing_mode = getattr(options, "mode", "") == "typing" and self.media_kind == "video"
        overlay_progress = progress_callback
        if typing_mode and progress_callback:
            overlay_progress = lambda value: progress_callback(float(value) * 0.80)
        overlay_path, temp_dir = build_text_overlay_segment(
            timeline,
            start_time,
            end_time,
            options,
            progress_callback=overlay_progress,
            cancelled_callback=cancelled_callback,
        )
        if cancelled_callback and cancelled_callback():
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise OperationCancelled()
        if typing_mode:
            audio_source_timeline = self.audio_effect_preparation_timeline()
            self._typing_text_audio_override_result = self.build_typing_text_audio_override(
                audio_source_timeline,
                start_time,
                end_time,
                overlay_path,
                temp_dir,
                progress_callback=(lambda value: progress_callback(80.0 + float(value) * 0.20)) if progress_callback else None,
                cancelled_callback=cancelled_callback,
            )
            if cancelled_callback and cancelled_callback():
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise OperationCancelled()
        return overlay_path, temp_dir, max(0.0, end_time - start_time)

    def OnCutSegment(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if self.start_time is None or self.end_time is None or self.start_time >= self.end_time:
            self.say("حدد بداية ونهاية المقطع أولا")
            return
        resume_playback = self.stop_playback_for_timeline_edit("cut_segment")
        before_state = self.capture_edit_state()
        if self.media_kind == "video":
            cut_start, cut_end = self.start_time, self.end_time
        else:
            cut_start, cut_end = clean_delete_range(self.timeline, self.start_time, self.end_time)
        self.clipboard = slice_segments(self.timeline, cut_start, cut_end)
        self.set_shared_timeline_clipboard(self.clipboard, operation="cut")
        if not self.clipboard:
            self.playback_requested = resume_playback
            self.reload_current_position()
            self.say("حدد بداية ونهاية المقطع أولا")
            return
        self.timeline = delete_range(self.timeline, cut_start, cut_end)
        if self.media_kind == "video":
            self.timeline = apply_audio_cut_fade_at_boundary(self.timeline, cut_start)
        self.adjust_timed_items_after_delete(cut_start, cut_end)
        self.add_edit_point("cut", cut_start, cut_end, "timeline", restore_segments=self.clipboard, mode="restore")
        self.current_time = min(cut_start, self.timeline_duration())
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("قص المقطع", before_state)
        self.playback_requested = resume_playback
        self.reload_current_position()
        trace_event(
            "timeline_edit",
            "cut_segment.complete",
            window=getattr(self, "window_number", None),
            media_kind=getattr(self, "media_kind", ""),
            start=cut_start,
            end=cut_end,
            resume_playback=resume_playback,
        )
        print(f"Cut segment from {cut_start} to {cut_end} seconds")
        self.say(speech_messages.CUT_SEGMENT_DONE, wait_for_ui=False)

    def OnCopySegment(self, event=None):
        if not self.require_open_file():
            return
        if self.start_time is not None and self.end_time is not None and self.start_time < self.end_time:
            self.clipboard = slice_segments(self.timeline, self.start_time, self.end_time)
            self.set_shared_timeline_clipboard(self.clipboard, operation="copy")
            print(f"Copied segment from {self.start_time} to {self.end_time} seconds")
            self.say(speech_messages.COPY_DONE, wait_for_ui=False)
            return
        self.say("حدد بداية ونهاية المقطع أولا")

    def set_shared_timeline_clipboard(self, segments, operation="copy"):
        return set_internal_timeline_clipboard(self, segments, operation=operation)

    def timeline_clipboard_for_paste(self):
        clipboard = internal_timeline_clipboard_segments()
        if clipboard:
            return clipboard
        return list(getattr(self, "clipboard", []) or [])

    def timeline_clipboard_kind_for_paste(self):
        clipboard = internal_timeline_clipboard_segments()
        if clipboard:
            return internal_timeline_clipboard_media_kind()
        media_kind = str(getattr(self, "media_kind", "none") or "none")
        return media_kind if media_kind in ("audio", "video") else "none"

    def _start_project_from_timeline_clipboard(self, clipboard, source_kind):
        """Create an empty window's project from a shared segment snapshot."""
        before_state = self.capture_edit_state()
        self.stop_background_audio_playback()
        self.clear_audio_visual_preview()
        self.timeline = list(clipboard)
        self.media_kind = source_kind if source_kind in ("audio", "video") else "video"
        self.video_path = self.timeline[0].path if self.timeline else ""
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
        self.current_time = 0
        self.current_segment_index = None
        self.active_media_path = ""
        self.invalidate_pending_media_load()
        self.playback_requested = True
        self.selected_playback_range = None
        self.skipped_playback_range = None
        self.start_time = None
        self.end_time = None
        clear_clipboard_paste_marker_state(self)
        self.file_metadata = {}
        self.is_dirty = True
        self.refresh_menu_bar()
        self.record_edit("لصق المقطع", before_state)
        self.load_timeline_time(0, True)

    def OnPasteSegment(self, event=None):
        if focused_control_owns_paste(self, perform=True):
            return
        if not can_start_paste(self):
            return
        # Inspect the native file clipboard before requiring an existing
        # timeline. A copied media file can create the first project, and a
        # copied .elbheri file can restore a project into an empty workspace.
        if paste_media_from_clipboard(self):
            return

        clipboard = self.timeline_clipboard_for_paste()
        if not clipboard:
            self.say("لا يوجد مقطع في الحافظة", wait_for_ui=False)
            return
        source_kind = self.timeline_clipboard_kind_for_paste()
        if paste_timeline_audio_clipboard_as_background(self, clipboard, source_kind):
            return
        if not begin_paste_operation(self):
            return
        before_state = self.capture_edit_state()
        try:
            for segment in clipboard:
                if not os.path.isfile(segment.path):
                    raise FileNotFoundError(segment.path)
                audio_path = str(getattr(segment, "audio_path", "") or "")
                if audio_path and not os.path.isfile(audio_path):
                    raise FileNotFoundError(audio_path)
            if not self.has_video():
                self._start_project_from_timeline_clipboard(clipboard, source_kind)
            else:
                placement = resolve_placement(self)
                inserted_duration = total_duration(clipboard)
                if placement.mode == "range":
                    restore_segments = slice_segments(self.timeline, placement.start, placement.end)
                    self.timeline = insert_segments(
                        delete_range(self.timeline, placement.start, placement.end),
                        placement.start,
                        clipboard,
                    )
                    edit_mode = "replace"
                else:
                    restore_segments = None
                    self.timeline = insert_segments(self.timeline, placement.start, clipboard)
                    self.shift_timed_items_after_insert(placement.start, inserted_duration)
                    edit_mode = "insert"
                self.add_edit_point(
                    "paste",
                    placement.start,
                    placement.start + inserted_duration,
                    "timeline",
                    restore_segments=restore_segments,
                    mode=edit_mode,
                    label="لصق المقطع",
                )
                self.current_time = placement.start
                self.start_time = None
                self.end_time = None
                clear_clipboard_paste_marker_state(self)
                self.last_insert_end = placement.start + inserted_duration
                self.is_dirty = True
                self.record_edit("لصق المقطع", before_state)
                self.reload_current_position()
            print(f"Pasted shared segment at {self.current_time} seconds")
            self.say(speech_messages.PASTE_DONE, wait_for_ui=False)
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored("لصق المقطع", error, "timeline_paste_commit")
        finally:
            end_paste_operation(self)

    def OnTakeSnapshot(self, event=None):
        try:
            msg_code = copy_video_snapshot_to_clipboard(self)
            self.say(tr(msg_code), wait_for_ui=False)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.say(f"حدث خطأ في اخذ الصورة: {e}", wait_for_ui=False)

    def OnCaptionsSettings(self, event=None):
        dlg = captionsSettingsDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def OnStartCaptionsExtraction(self, event=None):
        _debug("OnStartCaptionsExtraction called")
        if self._captions_running:
            _debug("EXIT: _captions_running is True")
            return
        if not self.has_video():
            self.say("لا يوجد أي ملف مفتوح")
            _debug("EXIT: no video open")
            return
        try:
            keys = GroqKeyManager.get_keys()
            _debug(f"get_keys returned {len(keys)} keys")
        except Exception as exc:
            self.say("تعذر قراءة مفاتيح Groq")
            _debug(f"EXIT: get_keys exception: {exc}")
            return
        if not keys:
            result = wx.MessageBox(
                "لا توجد مفاتيح Groq API مخزنة.\nهل ترغب في فتح إعدادات الميزة لإضافة مفتاح؟",
                "مفاتيح مفقودة",
                wx.YES_NO | wx.ICON_QUESTION
            )
            if result == wx.YES:
                self.OnCaptionsSettings()
            _debug("EXIT: no keys")
            return
        selected = self.selected_effect_range()
        if not selected:
            self.say("قم بتحديد نقطة بداية ونقطة نهاية للترجمة")
            wx.MessageBox("قم بتحديد نقطة بداية ونقطة نهاية للترجمة أولاً (مثلاً H للبداية و; للنهاية)", "تنبيه", wx.OK | wx.ICON_INFORMATION)
            _debug("EXIT: no selection range")
            return
        start_time, end_time = selected
        _debug(f"range: {start_time}-{end_time}")
        timeline_snapshot = list(self.timeline)
        try:
            progress_dlg = CaptionsProgressDialog(self, "استخراج النطق على الشاشة")
            progress_dlg.Show()
            progress_dlg.Raise()
            _debug("CaptionsProgressDialog shown and raised")
        except Exception as error:
            self.say("تعذر فتح محاورة التقدم")
            _debug(f"EXIT: dialog creation failed: {error}")
            wx.MessageBox(f"خطأ في فتح محاورة التقدم: {str(error)}", "خطأ", wx.OK | wx.ICON_ERROR)
            return
        self._captions_running = True
        self.say("جاري استخراج الترجمة...")
        _debug("starting pipeline thread")
        pipeline = CaptionsPipeline(self)
        threading.Thread(
            target=pipeline.run,
            args=(start_time, end_time, timeline_snapshot, progress_dlg),
            daemon=True
        ).start()
        _debug("pipeline thread started")

    def _run_ffmpeg_pipe(self, cmd, progress_dlg, timeout=120):
        startupinfo = ffmpeg_startupinfo()
        proc = subprocess.Popen(
            cmd,
            startupinfo=startupinfo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        progress_dlg.set_process(proc)

        output = {"stdout": b"", "stderr": b""}

        def reader_thread(stream, target_list, index):
            try:
                data = stream.read()
                target_list[index] = data
            except Exception:
                pass

        threads = [
            threading.Thread(target=reader_thread, args=(proc.stdout, output, 0), daemon=True),
            threading.Thread(target=reader_thread, args=(proc.stderr, output, 1), daemon=True),
        ]
        for t in threads:
            t.start()

        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if progress_dlg.is_cancelled():
                    proc.kill()
                    raise RuntimeError("ألغى المستخدم العملية")
                if proc.poll() is not None:
                    break
                time.sleep(0.1)

            for t in threads:
                t.join(timeout=2)

            if proc.poll() is None:
                proc.kill()
                proc.communicate()
                raise subprocess.TimeoutExpired(cmd, timeout)

            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd)

            return output["stdout"], output["stderr"]
        finally:
            if not progress_dlg.is_cancelled():
                progress_dlg.set_process(None)
            for t in threads:
                if t.is_alive():
                    t.join(timeout=1)
