from video_maker.player_modules.shared import *
from video_maker.player_modules.runtime_proxy import *


@publish_player_methods
class PlayerPreviewMixin:
    def has_video(self):
        return bool(self.timeline)

    def timeline_cache_signature(self):
        return tuple(segment for segment in self.timeline)

    def timeline_metrics(self):
        signature = self.timeline_cache_signature()
        if signature == self.timeline_boundaries_cache_signature:
            return self.timeline_positions_cache, self.timeline_boundaries_cache, self.timeline_duration_cache
        position = 0.0
        positions = [0.0]
        for segment in self.timeline:
            position += segment.duration
            positions.append(position)
        boundaries = [round(value, 3) for value in positions[1:-1]]
        self.timeline_boundaries_cache_signature = signature
        self.timeline_positions_cache = positions
        self.timeline_boundaries_cache = boundaries
        self.timeline_duration_cache = position
        return positions, boundaries, position

    def _overlay_tracks_max_end(self):
        overlay_max = 0.0
        for storage in (getattr(self, "background_audio_items", None) or [],
                        getattr(self, "sound_effects_items", None) or [],
                        getattr(self, "b_roll_items", None) or []):
            for item in storage:
                end = float(item.get("end", 0) or 0)
                if end > overlay_max:
                    overlay_max = end
        return overlay_max

    def timeline_duration(self):
        main = self.timeline_metrics()[2]
        if get_program_mode() == PROFESSIONAL_MODE:
            return max(main, self._overlay_tracks_max_end())
        return main

    def mark_timeline_range_navigation_group(self, start_time, duration, group_id):
        if not self.timeline or duration <= 0:
            return
        group_id = str(group_id or "")
        if not group_id:
            return
        start_time = max(0.0, float(start_time or 0.0))
        end_time = min(self.timeline_duration(), start_time + max(0.0, float(duration or 0.0)))
        position = 0.0
        updated = []
        changed = False
        for segment in self.timeline:
            segment_start = position
            segment_end = position + segment.duration
            navigation_group = str(getattr(segment, "navigation_group", "") or "")
            if segment_end > start_time + 0.001 and segment_start < end_time - 0.001:
                navigation_group = group_id
                changed = True
            updated.append(TimelineSegment(
                segment.path,
                segment.start,
                segment.end,
                float(getattr(segment, "speed", 1.0) or 1.0),
                float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
                str(getattr(segment, "audio_path", "") or ""),
                getattr(segment, "audio_start", None),
                navigation_group,
                segment_file_id(segment),
                segment_file_name(segment),
                str(getattr(segment, "transition", "") or ""),
                max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            ))
            position = segment_end
        if changed:
            self.timeline = updated

    def common_navigation_group(self, segments):
        groups = {
            str(getattr(segment, "navigation_group", "") or "")
            for segment in segments or []
        }
        groups.discard("")
        return next(iter(groups)) if len(groups) == 1 else ""

    def prepare_remove_silence_audio_file(self, progress_callback=None, cancelled_callback=None):
        if not self.timeline:
            return "", 0.0, ""
        temp_dir = tempfile.mkdtemp(prefix="remove_silence_audio_")
        audio_path = os.path.join(temp_dir, "removed_silence.wav")

        def audio_progress(percent):
            if progress_callback:
                scaled = 86 + int(max(0, min(100, percent)) * 0.13)
                progress_callback(min(99, scaled), "جاري تجهيز صوت إزالة الصمت")

        try:
            write_timeline_audio(self.audio_effect_preparation_timeline(), audio_path, audio_progress, cancelled_callback)
            audio_duration = get_media_duration(audio_path)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        self.generated_temp_dirs.append(temp_dir)
        self.generated_temp_files.append(audio_path)
        return audio_path, audio_duration, temp_dir

    def clear_audio_visual_preview(self):
        self.audio_visual_preview_generation += 1
        self.audio_visual_preview_signature = None
        self.audio_visual_preview_rendering_signature = None
        self.audio_visual_preview_path = ""
        if self.audio_visual_preview_temp_dir:
            shutil.rmtree(self.audio_visual_preview_temp_dir, ignore_errors=True)
            self.audio_visual_preview_temp_dir = ""

    def audio_visual_preview_enabled(self):
        enabled = bool(self.visual_items) or bool(getattr(self, "b_roll_items", []))
        if enabled and not self.use_reliable_audio and reliable_audio_available():
            self.use_reliable_audio = True
            if not getattr(self, "original_audio_player", None):
                self.original_audio_player = ReliableAudioPlayer()
        return enabled and bool(self.use_reliable_audio)

    def audio_visual_preview_playback_path(self):
        if not self.audio_visual_preview_enabled():
            return ""
        path = str(getattr(self, "audio_visual_preview_path", "") or "")
        return path if path and os.path.exists(path) else ""

    def active_media_is_audio_visual_preview(self):
        preview_path = self.audio_visual_preview_playback_path()
        return bool(preview_path) and os.path.abspath(str(self.active_media_path or "")) == os.path.abspath(preview_path)

    def audio_visual_preview_snapshot(self):
        duration = self.timeline_duration()
        visuals = [
            {
                "type": item.get("type", ""),
                "path": os.path.abspath(str(item.get("path", "") or "")),
                "start": round(float(item.get("start", 0) or 0), 3),
                "end": round(float(item.get("end", 0) or 0), 3),
                "transition": item.get("transition", ""),
                "transition_duration": round(float(item.get("transition_duration", 1.0) or 1.0), 3),
                "source_offset": round(float(item.get("source_offset", 0) or 0), 3),
                "speed": round(float(item.get("speed", 1.0) or 1.0), 3),
            }
            for item in self.visual_items
        ]
        return (round(duration, 3), tuple(sorted(visuals, key=lambda item: (item["start"], item["end"], item["path"]))), tuple(sorted(
            (
                os.path.abspath(str(item.get("path", "") or "")),
                round(float(item.get("start", 0) or 0), 3),
                round(float(item.get("end", 0) or 0), 3),
                round(float(item.get("source_offset", 0) or 0), 3),
                round(float(item.get("speed", 1.0) or 1.0), 3),
            )
            for item in getattr(self, "b_roll_items", []) or []
        )))

    def ensure_audio_visual_preview(self):
        if not self.audio_visual_preview_enabled():
            self.clear_audio_visual_preview()
            return
        signature = self.audio_visual_preview_snapshot()
        if signature == self.audio_visual_preview_signature and self.audio_visual_preview_playback_path():
            return
        if signature == self.audio_visual_preview_signature and signature == self.audio_visual_preview_rendering_signature:
            return
        self.audio_visual_preview_generation += 1
        generation = self.audio_visual_preview_generation
        old_temp_dir = self.audio_visual_preview_temp_dir
        self.audio_visual_preview_temp_dir = ""
        self.audio_visual_preview_path = ""
        self.audio_visual_preview_signature = signature
        self.audio_visual_preview_rendering_signature = signature
        if old_temp_dir:
            shutil.rmtree(old_temp_dir, ignore_errors=True)
        visual_snapshot = [dict(item) for item in self.visual_items]
        duration = self.timeline_duration()
        timeline_snapshot = [
            {
                "path": str(getattr(segment, "path", "") or ""),
                "start": float(getattr(segment, "start", 0) or 0),
                "end": float(getattr(segment, "end", 0) or 0),
            }
            for segment in (getattr(self, "timeline", []) or [])
        ]
        b_roll_snapshot = [dict(item) for item in getattr(self, "b_roll_items", []) or []]
        temp_dir = tempfile.mkdtemp(prefix="audio_visual_preview_")
        preview_path = os.path.join(temp_dir, "preview.mp4")
        threading.Thread(
            target=self.audio_visual_preview_worker,
            args=(generation, visual_snapshot, duration, temp_dir, preview_path, signature, timeline_snapshot, b_roll_snapshot),
            daemon=True,
        ).start()

    def audio_visual_preview_worker(self, generation, visual_snapshot, duration, temp_dir, preview_path, signature, timeline_snapshot=None, b_roll_snapshot=None):
        try:
            write_audio_visual_preview_video(visual_snapshot, duration, preview_path, cancelled_callback=lambda: generation != self.audio_visual_preview_generation, timeline=timeline_snapshot, b_roll_items=b_roll_snapshot)
        except Exception as error:
            shutil.rmtree(temp_dir, ignore_errors=True)
            wx.CallAfter(self.finish_audio_visual_preview_failed, generation, signature)
            return
        wx.CallAfter(self.finish_audio_visual_preview, generation, temp_dir, preview_path, signature)

    def finish_audio_visual_preview_failed(self, generation, signature):
        if generation == self.audio_visual_preview_generation and signature == self.audio_visual_preview_signature:
            self.audio_visual_preview_rendering_signature = None

    def finish_audio_visual_preview(self, generation, temp_dir, preview_path, signature):
        if generation != self.audio_visual_preview_generation or signature != self.audio_visual_preview_signature:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return
        self.audio_visual_preview_rendering_signature = None
        if not os.path.exists(preview_path):
            shutil.rmtree(temp_dir, ignore_errors=True)
            return
        self.audio_visual_preview_temp_dir = temp_dir
        self.audio_visual_preview_path = preview_path
        self.active_media_path = ""
        self.reload_current_position()

    def _dynamic_text_preview_items(self):
        return [
            item
            for item in getattr(self, "visual_items", []) or ()
            if isinstance(item, dict) and item.get("is_dynamic")
        ]

    def request_preview_rebuild(self):
        """يعيد بناء المعاينة الحية للنصوص عند تغيّر البصمة أو العنصر النشط.

        المسار السريع: عندما تكون البصمة فارغة (لا عناصر نصية) لا يُبنى شيء
        وتُستخدم المعاينة الحالية عبر mpv كما كانت. تحريك playhead وحده لا يغيّر
        البصمة لكنه قد يغيّر العنصر النشط، فيُعاد التقييم عندها.
        """
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        items = self._dynamic_text_preview_items()
        fingerprint = text_preview_fingerprint(items)
        playhead = float(getattr(self, "current_time", 0.0) or 0.0)
        active = render_preview_layer(items, playhead)
        active_id = str(active.get("id", "") or "") if active else ""
        fingerprint_changed = fingerprint != self._text_preview_fingerprint
        active_changed = active_id != self._text_preview_active_id
        if not fingerprint_changed and not active_changed:
            return
        self._text_preview_fingerprint = fingerprint
        self._text_preview_items = items
        self._text_preview_active_id = active_id
        if getattr(self, "_text_preview_rebuild_call", None):
            try:
                self._text_preview_rebuild_call.Stop()
            except Exception:
                pass
        self._text_preview_rebuild_call = wx.CallLater(250, self._apply_debounced_text_preview_rebuild)

    def _apply_debounced_text_preview_rebuild(self):
        self._text_preview_rebuild_call = None
        if get_program_mode() != PROFESSIONAL_MODE:
            return
        items = self._text_preview_items or ()
        if should_use_fast_path(items):
            self._preview_bitmap = None
            self._text_preview_base_path = ""
            return
        playhead = float(getattr(self, "current_time", 0.0) or 0.0)
        active = render_preview_layer(items, playhead)
        if active is None:
            self._preview_bitmap = None
            self._text_preview_base_path = ""
            return
        try:
            self._compose_and_show_text_preview(active)
        except Exception:
            self._preview_bitmap = None

    def _compose_and_show_text_preview(self, active):
        from PIL import Image

        options = from_text_item(active)
        if options is None or not getattr(options, "text", ""):
            self._preview_bitmap = None
            return
        base_path = self._text_preview_base_path or self.audio_visual_preview_playback_path()
        frame_path = self._capture_current_preview_frame(base_path)
        temp_dir = tempfile.mkdtemp(prefix="text_preview_")
        self.generated_temp_dirs.append(temp_dir)
        layer_path = os.path.join(temp_dir, "text_layer.png")
        composed_path = os.path.join(temp_dir, "composed.png")
        canvas_size = None
        if frame_path:
            try:
                with Image.open(frame_path) as im:
                    canvas_size = im.size
            except Exception:
                canvas_size = None
        if not canvas_size:
            canvas_size = (1280, 720)
        render_text_image(options, layer_path, canvas_size=canvas_size)
        if frame_path:
            with Image.open(frame_path).convert("RGBA") as base, Image.open(layer_path).convert("RGBA") as overlay:
                merged = Image.alpha_composite(base, overlay)
                merged.convert("RGB").save(composed_path)
        else:
            composed_path = layer_path
        self._preview_bitmap = composed_path
        if base_path:
            self._text_preview_base_path = base_path
        if self._preview_playback_is_active():
            return
        try:
            self.media_ctrl.Load(composed_path)
        except Exception:
            pass

    def _capture_current_preview_frame(self, base_path):
        try:
            player = self.media_ctrl._player
            if player is None:
                return ""
            if base_path:
                self.media_ctrl.Load(base_path)
            temp_dir = tempfile.mkdtemp(prefix="text_preview_frame_")
            self.generated_temp_dirs.append(temp_dir)
            frame_path = os.path.join(temp_dir, "frame.png")
            player.command("screenshot-to-file", frame_path, "video")
            if os.path.exists(frame_path):
                return frame_path
        except Exception:
            pass
        return ""

    def _preview_playback_is_active(self):
        if getattr(self, "playback_requested", False):
            return True
        media_ctrl = getattr(self, "media_ctrl", None)
        if media_ctrl is not None:
            try:
                return media_ctrl.GetState() == MEDIASTATE_PLAYING
            except Exception:
                pass
        return False

    def element_manager_items(self):
        if self.media_kind == "audio":
            return sorted(
                [
                    {
                        "id": item.get("id") or "",
                        "source": "visual",
                        "type": item.get("type", ""),
                        "path": item.get("path", ""),
                        "start": float(item.get("start", 0) or 0),
                        "end": float(item.get("end", 0) or 0),
                        "transition": item.get("transition", self.transition_name),
                        "transition_duration": float(item.get("transition_duration", 1.0) or 1.0),
                    }
                    for item in self.visual_items
                    if item.get("type") in ("image", "text", "video")
                ],
                key=lambda item: (item["start"], item["end"], item["type"]),
            )

        items = []
        for point in normalize_edit_points(self.edit_points):
            if point.get("target") != "timeline" or point.get("kind") not in ("image", "text", "video"):
                continue
            path = ""
            index, segment, segment_position = locate_segment(self.timeline, point["start"])
            if segment and abs(segment_position - point["start"]) <= max(0.2, point["end"] - point["start"] + 0.2):
                path = segment.path
            items.append(
                {
                    "id": point["id"],
                    "source": "point",
                    "type": point.get("kind", ""),
                    "path": path,
                    "start": point["start"],
                    "end": point["end"],
                    "transition": point.get("transition", self.transition_name),
                    "transition_duration": float(point.get("transition_duration", 1.0) or 1.0),
                }
            )
        return sorted(items, key=lambda item: (item["start"], item["end"], item["type"]))

    def OnElementManager(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        manager = ElementManagerWindow(self)
        manager.Show()

    def transition_item_from_start_marker(self):
        if self.start_time is None:
            return None
        marker = max(0.0, min(float(self.start_time), self.timeline_duration()))
        items = self.element_manager_items()
        for item in items:
            start = float(item.get("start", 0) or 0)
            end = float(item.get("end", start) or start)
            if start - 0.03 <= marker < end + 0.03:
                return item
        for item in items:
            if float(item.get("start", 0) or 0) >= marker - 0.03:
                return item
        return None

    def transition_range_from_selection(self):
        selected = self.selected_effect_range()
        if selected:
            return selected
        item = self.transition_item_from_start_marker()
        if item:
            return float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)
        point = self.selected_or_current_edit_point()
        if point and point.get("target") == "timeline":
            return point["start"], point["end"]
        return None

    def OnTransitionEffects(self, event=None, manager_item=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        current_key = (manager_item or {}).get("transition", "")
        current_duration = (manager_item or {}).get("transition_duration", 1.0)
        dialog = TransitionEffectsDialog(self, current_key=current_key, current_duration=current_duration)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        selection = dialog.selection
        dialog.Destroy()
        if not selection:
            return
        self.say(selection.description)
        if manager_item:
            self.set_element_manager_transition(manager_item, selection)
            return
        selected = self.selected_effect_range()
        if selected:
            if self.media_kind == "audio":
                self.set_transition_for_visual_range(selected[0], selected[1], selection)
            else:
                self.apply_transition_to_timeline_range(selected[0], selected[1], selection)
            return
        item = self.transition_item_from_start_marker()
        if item:
            self.set_element_manager_transition(item, selection)
            return
        target_range = self.transition_range_from_selection()
        if target_range and self.media_kind != "audio":
            self.apply_transition_to_timeline_range(target_range[0], target_range[1], selection)
            return
        self.say(tr("حدد بداية ونهاية الجزء المطلوب أولا."))

    def audio_visual_transition_groups_for_range(self, start_time, end_time):
        result = []
        for group, transition_key, transition_duration in visual_item_groups(self.visual_items):
            if not transition_key or len(group) < 2:
                continue
            group_start = min(float(item.get("start", 0) or 0) for item in group)
            group_end = max(float(item.get("end", 0) or 0) for item in group)
            if group_end > start_time and group_start < end_time:
                result.append((group, transition_key, transition_duration))
        return result

    def prepare_audio_visual_transition_renders(self, start_time, end_time):
        groups = self.audio_visual_transition_groups_for_range(start_time, end_time)
        if not groups:
            return False
        self.say(tr("جاري تجهيز تأثير الانتقال"))
        item_by_id = {item.get("id"): item for item in self.visual_items if item.get("id")}
        for group, transition_key, transition_duration in groups:
            render_path, temp_dir, signature = render_xfade_visual_overlay_file(group, (1280, 720), transition_key, transition_duration)
            render = {
                "path": render_path,
                "temp_dir": temp_dir,
                "signature": signature,
                "start": min(float(item.get("start", 0) or 0) for item in group),
                "end": max(float(item.get("end", 0) or 0) for item in group),
            }
            self.generated_temp_dirs.append(temp_dir)
            self.generated_temp_files.append(render_path)
            for item in group:
                current = item_by_id.get(item.get("id"))
                if current is not None:
                    current["transition_render"] = dict(render)
        return True

    def set_transition_for_visual_range(self, start_time, end_time, selection):
        before_state = self.capture_edit_state()
        changed = 0
        for item in self.visual_items:
            item_start = float(item.get("start", 0) or 0)
            item_end = float(item.get("end", item_start) or item_start)
            if item_end > start_time and item_start < end_time:
                item["transition"] = selection.key
                item["transition_duration"] = selection.duration
                item.pop("transition_render", None)
                changed += 1
        if not changed:
            self.say(tr("لا توجد عناصر مضافة على الخط الزمني"))
            return
        try:
            if not self.prepare_audio_visual_transition_renders(start_time, end_time):
                self.apply_edit_state(before_state)
                self.notify_failed_edit_restored("إضافة تأثير انتقال", context="transition_effect_no_adjacent_items")
                self.say(tr("حدد عنصرين بصريين متجاورين لتطبيق تأثير الانتقال"))
                return
        except Exception as error:
            self.apply_edit_state(before_state)
            self.notify_failed_edit_restored("إضافة تأثير انتقال", error, "transition_effect_prepare")
            wx.MessageBox(tr("تعذر تطبيق تأثير الانتقال: {error}").format(error=error), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        self.transition_name = selection.key
        self.is_dirty = True
        self.record_edit("إضافة تأثير انتقال من مدير العناصر", before_state)
        self.refresh_menu_bar()
        self.say(tr("تم تطبيق تأثير الانتقال"))

    def apply_transition_to_timeline_range(self, start_time, end_time, selection, kind="visual_transition"):
        start_time = max(0.0, min(float(start_time), self.timeline_duration()))
        end_time = max(start_time, min(float(end_time), self.timeline_duration()))
        if end_time <= start_time:
            self.say(tr("تعذر إضافة تأثير الانتقال"))
            return
        timeline_snapshot = list(self.timeline)
        self.transition_name = selection.key
        self.start_timeline_transform(
            kind,
            tr("جاري تطبيق تأثير الانتقال"),
            tr("نسبة تطبيق تأثير الانتقال {percent} بالمئة"),
            tr("حالة تطبيق تأثير الانتقال"),
            tr("شريط تقدم تطبيق تأثير الانتقال"),
            tr("إلغاء تطبيق تأثير الانتقال"),
            tr("جاري إلغاء تطبيق تأثير الانتقال"),
            lambda progress, cancelled: build_visual_transition_segment(
                timeline_snapshot,
                start_time,
                end_time,
                selection.key,
                progress,
                cancelled,
                selection.duration,
            ),
            (start_time, end_time),
            tr("تطبيق تأثير انتقال"),
            tr("تم تطبيق تأثير الانتقال"),
            scale_timed_items=False,
            preserve_continuous_audio=True,
        )

    def sync_visual_edit_points_from_items(self):
        item_by_id = {item.get("id"): item for item in self.visual_items if item.get("id")}
        updated_points = []
        for point in normalize_edit_points(self.edit_points):
            item = item_by_id.get(point.get("item_id"))
            if item and point.get("target") == "visual":
                updated = dict(point)
                updated["start"] = float(item.get("start", 0) or 0)
                updated["end"] = float(item.get("end", updated["start"]) or updated["start"])
                updated_points.append(updated)
            else:
                updated_points.append(point)
        self.edit_points = normalize_edit_points(updated_points)

    def visual_item_by_id(self, item_id):
        return next((item for item in self.visual_items if item.get("id") == item_id), None)

    def edit_point_for_manager_item(self, item):
        return point_by_id(self.edit_points, item.get("id"))

    def choose_replacement_image(self):
        with wx.FileDialog(self, tr("استبدال الصورة"), wildcard=IMAGE_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, "image", "replace_image")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return ""
            path = dialog.GetPath()
            remember_media_paths([path], "image", "replace_image")
            return path

    def choose_replacement_video(self):
        with wx.FileDialog(self, tr("استبدال الفيديو"), wildcard=VIDEO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, "video", "replace_video")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return ""
            path = dialog.GetPath()
            remember_media_paths([path], "video", "replace_video")
            return path

    def replacement_text_image(self):
        dialog = TextOverlayDialog(
            self,
            title=tr("استبدال النص"),
            apply_label=tr("استبدال"),
            apply_name=tr("استبدال النص"),
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return "", ""
        options = dialog.options
        dialog.Destroy()
        if not options:
            return "", ""
        temp_dir = tempfile.mkdtemp(prefix="element_text_replace_")
        text_path = os.path.join(temp_dir, "text.png")
        try:
            render_text_image(options, text_path)
            self.generated_temp_dirs.append(temp_dir)
            self.generated_temp_files.append(text_path)
            return text_path, temp_dir
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def delete_element_manager_item(self, item, compensation_mode=""):
        if item.get("source") == "visual":
            self.delete_visual_manager_item(item, compensation_mode)
        else:
            self.delete_timeline_manager_item(item)

    def delete_visual_manager_item(self, item, compensation_mode=""):
        before_state = self.capture_edit_state()
        item_id = item.get("id")
        before_count = len(self.visual_items)
        if compensation_mode:
            self.visual_items = compensate_deleted_visual_item(self.visual_items, item_id, compensation_mode)
        else:
            self.visual_items = [visual for visual in self.visual_items if visual.get("id") != item_id]
        if len(self.visual_items) == before_count:
            self.say(tr("تعذر حذف العنصر"))
            return
        self.edit_points = [
            point for point in normalize_edit_points(self.edit_points)
            if point.get("item_id") != item_id
        ]
        self.sync_visual_edit_points_from_items()
        self.current_edit_point_id = None
        self.current_time = min(float(item.get("start", 0) or 0), self.timeline_duration())
        self.is_dirty = True
        self.record_edit("حذف عنصر من مدير العناصر", before_state)
        self.refresh_menu_bar()
        self.say(tr("تم حذف العنصر"))

    def delete_timeline_manager_item(self, item):
        point = self.edit_point_for_manager_item(item)
        if not point:
            self.say(tr("تعذر حذف العنصر"))
            return
        before_state = self.capture_edit_state()
        start_time = max(0.0, min(point["start"], self.timeline_duration()))
        end_time = max(start_time, min(point["end"], self.timeline_duration()))
        restored = dicts_to_segments(point.get("restore_segments"))
        point_kind = str(point.get("kind", "") or "")
        audio_policy = "preserve" if point.get("mode") == "replace" and visual_only_edit_kind(point_kind) and restored and abs(total_duration(restored) - (end_time - start_time)) <= 0.03 else "auto"
        if point.get("mode") == "replace" and restored:
            self.timeline = insert_segments(delete_range(self.timeline, start_time, end_time), start_time, restored)
        elif point.get("mode") == "insert" and end_time > start_time:
            self.timeline = delete_range(self.timeline, start_time, end_time)
            self.adjust_visual_items_after_delete(start_time, end_time)
            self.adjust_background_audio_after_delete(start_time, end_time)
            self.edit_points = adjust_points_after_delete(self.edit_points, start_time, end_time, point["id"])
        else:
            self.say(tr("تعذر حذف العنصر"))
            return
        self.edit_points = remove_point(self.edit_points, point["id"])
        self.current_edit_point_id = None
        self.current_time = min(start_time, self.timeline_duration())
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit("حذف عنصر من مدير العناصر", before_state, audio_policy=audio_policy)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(tr("تم حذف العنصر"))

    def replace_element_manager_item(self, item):
        if item.get("source") == "visual":
            self.replace_visual_manager_item(item)
        else:
            self.replace_timeline_manager_item(item)

    def replace_visual_manager_item(self, item):
        visual = self.visual_item_by_id(item.get("id"))
        if not visual:
            self.say(tr("تعذر استبدال العنصر"))
            return
        item_type = visual.get("type")
        try:
            if item_type == "image":
                replacement_path = self.choose_replacement_image()
                if not replacement_path:
                    return
            elif item_type == "video":
                replacement_path = self.choose_replacement_video()
                if not replacement_path:
                    return
            elif item_type == "text":
                replacement_path, temp_dir = self.replacement_text_image()
                if not replacement_path:
                    return
            else:
                self.say(tr("تعذر استبدال العنصر"))
                return
        except Exception as error:
            # self.say(tr("تعذر استبدال العنصر"))
            wx.MessageBox(tr("تعذر استبدال العنصر: {error}").format(error=error), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        before_state = self.capture_edit_state()
        visual["path"] = replacement_path
        if item_type == "video":
            visual["source_offset"] = 0.0
        self.is_dirty = True
        self.record_edit("استبدال عنصر من مدير العناصر", before_state)
        self.refresh_menu_bar()
        self.say(tr("تم استبدال العنصر"))

    def replacement_base_timeline(self, point, start_time, end_time):
        restored = dicts_to_segments(point.get("restore_segments"))
        if restored:
            return restored, 0.0, total_duration(restored)
        return slice_segments(self.timeline, start_time, end_time), 0.0, end_time - start_time

    def replace_timeline_manager_item(self, item):
        point = self.edit_point_for_manager_item(item)
        if not point:
            self.say(tr("تعذر استبدال العنصر"))
            return
        item_type = point.get("kind")
        start_time = max(0.0, min(point["start"], self.timeline_duration()))
        end_time = max(start_time, min(point["end"], self.timeline_duration()))
        duration = max(0.0, end_time - start_time)
        if duration <= 0:
            self.say(tr("تعذر استبدال العنصر"))
            return
        try:
            if item_type == "video":
                replacement_path = self.choose_replacement_video()
                if not replacement_path:
                    return
                replacement_duration = get_video_duration(replacement_path)
                if replacement_duration + 0.05 < duration:
                    wx.MessageBox(tr("الفيديو الجديد أقصر من مدة العنصر الحالي."), tr("قيمة غير صحيحة"), wx.OK | wx.ICON_ERROR)
                    return
                replacement_timeline = replacement_segments_preserving_files(self.timeline, start_time, end_time, replacement_path, duration)
                temp_dir = ""
            elif item_type == "image":
                image_options = ImageOverlayDialog(
                    self,
                    title=tr("استبدال الصورة"),
                    apply_label=tr("استبدال"),
                    apply_name=tr("استبدال الصورة"),
                )
                if image_options.ShowModal() != wx.ID_OK:
                    image_options.Destroy()
                    return
                options = image_options.options
                image_options.Destroy()
                if not options:
                    return
                base_timeline, base_start, base_end = self.replacement_base_timeline(point, start_time, end_time)
                replacement_path, temp_dir = build_image_overlay_segment(base_timeline, base_start, base_end, options)
                replacement_timeline = replacement_segments_preserving_files(self.timeline, start_time, end_time, replacement_path, duration)
            elif item_type == "text":
                text_dialog = TextOverlayDialog(
                    self,
                    title=tr("استبدال النص"),
                    apply_label=tr("استبدال"),
                    apply_name=tr("استبدال النص"),
                )
                if text_dialog.ShowModal() != wx.ID_OK:
                    text_dialog.Destroy()
                    return
                options = text_dialog.options
                text_dialog.Destroy()
                if not options:
                    return
                base_timeline, base_start, base_end = self.replacement_base_timeline(point, start_time, end_time)
                replacement_path, temp_dir = build_text_overlay_segment(base_timeline, base_start, base_end, options)
                replacement_timeline = replacement_segments_preserving_files(self.timeline, start_time, end_time, replacement_path, duration)
            else:
                self.say(tr("تعذر استبدال العنصر"))
                return
        except Exception as error:
            # self.say(tr("تعذر استبدال العنصر"))
            wx.MessageBox(tr("تعذر استبدال العنصر: {error}").format(error=error), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return

        before_state = self.capture_edit_state()
        if temp_dir:
            self.generated_temp_dirs.append(temp_dir)
            self.generated_temp_files.append(replacement_path)
        self.timeline = insert_segments(delete_range(self.timeline, start_time, end_time), start_time, replacement_timeline)
        self.current_time = start_time
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(
            "استبدال عنصر من مدير العناصر",
            before_state,
            audio_policy="preserve" if item_type in ("image", "text") else "auto",
        )
        self.reload_current_position()
        self.say(tr("تم استبدال العنصر"))

    def set_element_manager_transition(self, item, selection):
        if item.get("source") == "visual":
            visual = self.visual_item_by_id(item.get("id"))
            if not visual:
                self.say(tr("تعذر إضافة تأثير الانتقال"))
                return
            self.set_transition_for_visual_range(
                float(visual.get("start", 0) or 0),
                float(visual.get("end", 0) or 0),
                selection,
            )
            return

        point = self.edit_point_for_manager_item(item)
        if not point:
            self.say(tr("تعذر إضافة تأثير الانتقال"))
            return
        start_time = max(0.0, min(point["start"], self.timeline_duration()))
        end_time = max(start_time, min(point["end"], self.timeline_duration()))
        if end_time <= start_time:
            self.say(tr("تعذر إضافة تأثير الانتقال"))
            return
        self.apply_transition_to_timeline_range(start_time, end_time, selection, point.get("kind") or "visual_transition")

    def OnSetTimelineBoundaryTransition(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if len(self.timeline or []) < 2:
            self.say(tr("لا يوجد حد فاصل بين مقاطع لإضافة انتقال"))
            return
        boundary_index, boundary_time = boundary_index_at_time(self.timeline, self.current_time)
        if boundary_index is None:
            self.say(tr("انقل المؤشر إلى الحد الفاصل بين مقطعين ثم أعد المحاولة"))
            return
        segment = self.timeline[boundary_index]
        dialog = TimelineTransitionDialog(
            self,
            boundary_time=boundary_time,
            current_key=str(getattr(segment, "transition", "") or ""),
            current_duration=float(getattr(segment, "transition_duration", 1.0) or 1.0),
        )
        if dialog.ShowModal() != wx.ID_OK:
            return
        self.apply_timeline_boundary_transition(
            boundary_index,
            boundary_time,
            dialog.selected_key(),
            dialog.selected_duration(),
        )

    def apply_timeline_boundary_transition(self, boundary_index, boundary_time, transition_key, transition_duration):
        if boundary_index < 1 or boundary_index >= len(self.timeline or []):
            return
        before_state = self.capture_edit_state()
        segment = self.timeline[boundary_index]
        updated = list(self.timeline)
        updated[boundary_index] = with_transition(segment, transition_key, transition_duration)
        self.timeline = updated
        end_time = boundary_time + float(segment.duration)
        self.add_edit_point(
            "transition",
            boundary_time,
            end_time,
            "timeline",
            restore_segments=[segment],
            mode="replace",
            label=tr("هنا أضفت انتقالًا بين مقطعين"),
        )
        self.current_time = min(max(boundary_time, 0), self.timeline_duration())
        self.start_time = None
        self.end_time = None
        self.is_dirty = True
        self.record_edit(tr("إضافة انتقال بين مقطعين"), before_state)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(tr("تم تطبيق انتقال الحد"))

    def jump_to_element_manager_item(self, item):
        self.current_time = min(max(float(item.get("start", 0) or 0), 0), self.timeline_duration())
        if item.get("source") == "point":
            self.current_edit_point_id = item.get("id")
        else:
            self.current_edit_point_id = next(
                (
                    point["id"]
                    for point in normalize_edit_points(self.edit_points)
                    if point.get("item_id") == item.get("id")
                ),
                None,
            )
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.say(tr("تم الانتقال إلى العنصر"))

    def add_edit_point(self, kind, start_time, end_time, target="timeline", item_id="", restore_segments=None, mode="", label=""):
        point = make_edit_point(kind, start_time, end_time, target, item_id, restore_segments, mode, label)
        self.edit_points = normalize_edit_points([*self.edit_points, point])
        self.current_edit_point_id = point["id"]
        return point

    def edit_point_navigation_message(self, point, index):
        return tr("أنت في نقطة التعديل رقم {number} {description}").format(
            number=index + 1,
            description=tr(point_description(point)),
        )

    def jump_to_edit_point(self, point, index):
        if get_program_mode() == PROFESSIONAL_MODE:
            self._clear_element_selection()
        self.current_edit_point_id = point["id"]
        self.current_time = min(max(point["start"], 0), self.timeline_duration())
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.say(self.edit_point_navigation_message(point, index))

    def OnNextEditPoint(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        index, point, total = next_point(self.edit_points, self.current_time)
        if not point:
            self.say(tr("لا توجد مواضع تعديل"))
            return
        self.jump_to_edit_point(point, index)

    def OnPreviousEditPoint(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        index, point, total = previous_point(self.edit_points, self.current_time)
        if not point:
            self.say(tr("لا توجد مواضع تعديل"))
            return
        self.jump_to_edit_point(point, index)

    def selected_or_current_edit_point(self):
        point = point_by_id(self.edit_points, self.current_edit_point_id)
        if point and abs(point["start"] - self.current_time) <= 0.2:
            return point
        index, point, total = point_at_time(self.edit_points, self.current_time)
        return point

    def remove_visual_item_for_point(self, point):
        item_id = point.get("item_id")
        before = len(self.visual_items)
        if item_id:
            self.visual_items = [item for item in self.visual_items if item.get("id") != item_id]
        if len(self.visual_items) == before:
            self.visual_items = [
                item for item in self.visual_items
                if not (
                    item.get("type") == point.get("kind")
                    and abs(float(item.get("start", 0) or 0) - point["start"]) <= 0.03
                    and abs(float(item.get("end", 0) or 0) - point["end"]) <= 0.03
                )
            ]
        return len(self.visual_items) != before

    def remove_background_audio_for_point(self, point):
        item_id = point.get("item_id")
        before = len(self.background_audio_items)
        if item_id:
            self.background_audio_items = [item for item in self.background_audio_items if item.get("id") != item_id]
        if len(self.background_audio_items) == before:
            self.background_audio_items = [
                item for item in self.background_audio_items
                if not (
                    item.get("type") == "background_audio"
                    and abs(float(item.get("start", 0) or 0) - point["start"]) <= 0.03
                    and abs(float(item.get("end", 0) or 0) - point["end"]) <= 0.03
                )
            ]
        return len(self.background_audio_items) != before

    def OnDeleteCurrentEditPoint(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        point = self.selected_or_current_edit_point()
        if not point:
            self.say(tr("لا توجد نقطة تعديل عند الموضع الحالي"))
            return
        before_state = self.capture_edit_state()
        start_time = max(0.0, min(point["start"], self.timeline_duration()))
        end_time = max(start_time, min(point["end"], self.timeline_duration()))
        changed = False
        if point.get("target") == "visual":
            changed = self.remove_visual_item_for_point(point)
        elif point.get("target") == "background_audio":
            changed = self.remove_background_audio_for_point(point)
        elif point.get("mode") == "restore":
            restored = dicts_to_segments(point.get("restore_segments"))
            if restored:
                self.timeline = insert_segments(self.timeline, start_time, restored)
                inserted_duration = total_duration(restored)
                self.shift_timed_items_after_insert(start_time, inserted_duration)
                changed = True
        elif point.get("mode") == "replace":
            restored = dicts_to_segments(point.get("restore_segments"))
            if restored:
                self.timeline = insert_segments(delete_range(self.timeline, start_time, end_time), start_time, restored)
                changed = True
        else:
            if end_time > start_time:
                self.timeline = delete_range(self.timeline, start_time, end_time)
                self.adjust_visual_items_after_delete(start_time, end_time)
                self.adjust_background_audio_after_delete(start_time, end_time)
                self.edit_points = adjust_points_after_delete(self.edit_points, start_time, end_time, point["id"])
                changed = True
        if not changed:
            self.say(tr("تعذر حذف نقطة التعديل"))
            return
        self.edit_points = remove_point(self.edit_points, point["id"])
        self.current_edit_point_id = None
        self.current_time = min(start_time, self.timeline_duration())
        self.start_time = start_time
        self.end_time = max(start_time, min(point["end"], self.timeline_duration()))
        self.is_dirty = True
        point_kind = str(point.get("kind", "") or "")
        delete_point_audio_policy = "preserve" if point.get("mode") == "replace" and visual_only_edit_kind(point_kind) and abs(total_duration(dicts_to_segments(point.get("restore_segments"))) - (end_time - start_time)) <= 0.03 else "auto"
        self.record_edit("حذف نقطة تعديل", before_state, audio_policy=delete_point_audio_policy)
        self.refresh_menu_bar()
        self.reload_current_position()
        self.say(tr("تم حذف {name} وتم تحديد النقاط").format(name=tr(delete_name(point))))

    def spoken_seconds(self, seconds):
        seconds = max(0.0, float(seconds or 0))
        rounded = round(seconds)
        if abs(seconds - rounded) < 0.05:
            return str(int(rounded))
        return f"{seconds:.1f}"

    def spoken_time(self, seconds):
        return spoken_duration(seconds)

    def OnChooseAudioEffect(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        effects = get_audio_effect_definitions()
        dialog = AudioEffectChooserDialog(self, effects)
        if dialog.ShowModal() == wx.ID_OK:
            effect = dialog.selected_effect
            dialog.Destroy()
            if effect:
                if effect.get("special_action") == "remove_silence":
                    self.OnRemoveSilence()
                    return
                if effect.get("special_action") == "voice_over_ducking":
                    self.OnAudioDucking()
                    return
                self.OnAudioEffect(effect["key"])
            return
        dialog.Destroy()

    def OnSpeakSelectionLength(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        selected = self.selected_effect_range()
        if not selected:
            self.say(tr("لا يوجد تحديد"), wait_for_ui=False)
            return
        start_time, end_time = selected
        self.say(tr("مدة التحديد {duration}").format(duration=self.spoken_time(end_time - start_time)))

    def OnSpeakCurrentTime(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        self.say(tr("الوقت الحالي {current} من {duration}").format(
            current=self.spoken_time(self.current_time),
            duration=self.spoken_time(self.timeline_duration()),
        ))

    def active_visual_items_at(self, time_value):
        items = []
        for item in getattr(self, "visual_items", []):
            start = float(item.get("start", 0) or 0)
            end = float(item.get("end", 0) or 0)
            if start <= time_value < end:
                items.append(item)
        return items

    def OnSpeakCurrentItems(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        names = []
        for item in self.active_visual_items_at(self.current_time):
            names.append(tr(delete_name({"kind": item.get("type", "")})))
        for key, item in self.active_background_audio_items(self.current_time):
            names.append(tr("الخلفية الصوتية"))
        for point in normalize_edit_points(self.edit_points):
            if point["start"] <= self.current_time < max(point["end"], point["start"] + 0.03):
                names.append(tr(delete_name(point)))
        unique = []
        for name in names:
            if name and name not in unique:
                unique.append(name)
        if not unique:
            self.say(tr("لا توجد عناصر عند الموضع الحالي"))
            return
        self.say(tr("عند الموضع الحالي {items}").format(items=" ".join(unique)))

    def OnSpeakEditPointCount(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        count = len(normalize_edit_points(self.edit_points))
        if count == 0:
            self.say(tr("لا توجد مواضع تعديل"))
        else:
            self.say(tr("عدد مواضع التعديل {count}").format(count=count))

    def OnSpeakCurrentEditPoint(self, event=None):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        point = self.selected_or_current_edit_point()
        if not point:
            self.say(tr("لا توجد نقطة تعديل عند الموضع الحالي"))
            return
        ordered = normalize_edit_points(self.edit_points)
        index = next((i for i, item in enumerate(ordered) if item["id"] == point["id"]), 0)
        self.say(self.edit_point_navigation_message(point, index))

    def added_item_edges(self):
        edges = []
        for collection in (getattr(self, "visual_items", []), getattr(self, "background_audio_items", [])):
            for item in collection:
                edges.extend([float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)])
        for point in normalize_edit_points(self.edit_points):
            edges.extend([point["start"], point["end"]])
        duration = self.timeline_duration()
        return sorted({max(0.0, min(duration, round(edge, 3))) for edge in edges if 0 <= edge <= duration})

    def jump_to_added_edge(self, forward=True):
        if not self.has_video():
            self.say(speech_messages.NO_OPEN_FILE)
            return
        if get_program_mode() == PROFESSIONAL_MODE:
            self._clear_element_selection()
        edges = self.added_item_edges()
        if not edges:
            self.say(tr("لا توجد حواف عناصر"))
            return
        if forward:
            target = next((edge for edge in edges if edge > self.current_time + 0.03), edges[0])
            message = "حافة العنصر التالية {seconds} ثانية"
        else:
            target = next((edge for edge in reversed(edges) if edge < self.current_time - 0.03), edges[-1])
            message = "حافة العنصر السابقة {seconds} ثانية"
        self.current_time = target
        self.load_timeline_time(self.current_time, self.playback_requested)
        self.say(tr(message).format(seconds=self.spoken_seconds(target)))

