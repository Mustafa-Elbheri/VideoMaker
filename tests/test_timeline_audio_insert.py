import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageStat

from video_maker import localization
from video_maker.player import VideoPlayer
from video_maker.app_paths import bundled_path, ffmpeg_binary
from video_maker.text_overlay import TextOverlayOptions, render_text_image
from video_maker.timeline import TimelineSegment
from video_maker.timeline_audio_insert import inserted_audio_timeline, video_save_block_message_for_timeline
from video_maker.video_editing import get_media_duration, has_audio_stream, has_video_stream, write_audio_visual_video, write_timeline_audio, write_timeline_video


def _run_ffmpeg(args):
    result = subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="ignore"))


def _write_tone(path, duration=1.0, frequency=440):
    _run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}", "-c:a", "pcm_s16le", path])


def _write_test_video(path, duration=1.0):
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s=320x240:r=25:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
        "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", path,
    ])


def _write_test_image(path):
    _run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=320x240", "-frames:v", "1", path])


def _extract_frame(video_path, image_path, time_value):
    _run_ffmpeg(["-ss", f"{time_value:.3f}", "-i", video_path, "-frames:v", "1", image_path])


def _average_rgb(image_path, box):
    with Image.open(image_path).convert("RGB") as image:
        return tuple(ImageStat.Stat(image.crop(box)).mean)


def _assert_dominant_color(testcase, image_path, channel, box=(480, 220, 800, 500)):
    average = _average_rgb(image_path, box)
    target = average[channel]
    others = [value for index, value in enumerate(average) if index != channel]
    testcase.assertGreater(target, 110, average)
    for value in others:
        testcase.assertGreater(target, value + 35, average)


def _text_font_path():
    candidates = [
        bundled_path("assets", "fonts", "arabic", "NotoSansArabic.ttf"),
        bundled_path("assets", "fonts", "arabic", "Cairo.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"),
    ]
    for path in candidates:
        if os.path.exists(str(path)):
            return str(path)
    raise AssertionError("No usable font found for text overlay test")


def _write_text_overlay(path, text="نص على الصورة"):
    options = TextOverlayOptions(
        text=text,
        font_path=_text_font_path(),
        font_name="Test font",
        font_size=52,
        color=(255, 255, 255, 255),
        background="black",
        background_opacity=65,
        position="center_bottom",
        box_width_percent=80,
    )
    render_text_image(options, path, canvas_size=(1280, 720))


def _frame(media_kind):
    frame = VideoPlayer.__new__(VideoPlayer)
    frame.media_kind = media_kind
    frame.timeline = [TimelineSegment("main.mp4" if media_kind == "video" else "main.wav", 0.0, 10.0)]
    frame.visual_items = []
    frame.background_audio_items = []
    frame.b_roll_items = []
    frame.sound_effects_items = []
    frame.chroma_render_state = {}
    frame.current_time = 4.0
    frame.require_open_file = lambda: True
    frame.capture_edit_state = lambda: {"timeline": list(frame.timeline)}
    frame.shift_calls = []
    frame.shift_timed_items_after_insert = lambda start, duration: frame.shift_calls.append((start, duration))
    frame.edit_points = []
    frame.add_edit_point = lambda kind, start, end, target, mode="": frame.edit_points.append((kind, start, end, target, mode))
    frame.recorded_edits = []
    frame.record_edit = lambda label, before: frame.recorded_edits.append((label, before))
    frame.reload_current_position = lambda: None
    frame.spoken = []
    frame.say = lambda text, **kwargs: frame.spoken.append(text)
    frame.is_dirty = False
    return frame


class TimelineAudioInsertTest(unittest.TestCase):
    def test_insert_audio_text_is_translated(self):
        from video_maker.localization import tr

        with patch.object(localization, "get_language", lambda default="ar", language="ar": "en"):
            self.assertEqual(tr("إدراج صوت"), "Insert audio")
            self.assertEqual(tr("تم إدراج الصوت"), "Audio inserted")
        with patch.object(localization, "get_language", lambda default="ar", language="ar": "fr"):
            self.assertEqual(tr("إدراج صوت"), "Insérer un audio")
            self.assertEqual(tr("تم إدراج الصوت"), "Audio inséré")

    def test_insert_menu_contains_insert_audio_for_audio_and_video_projects(self):
        from video_maker.menus import install_menu_bar
        from video_maker.shortcuts import install_shortcuts

        labels = []

        class FakeMenuItem:
            def __init__(self, item_id):
                self._id = item_id

            def Enable(self, _enabled):
                pass

            def Check(self, _checked):
                pass

            def GetId(self):
                return self._id

        class FakeMenu:
            def Append(self, item_id, label="", *args, **kwargs):
                labels.append(label)
                return FakeMenuItem(item_id)

            def AppendSubMenu(self, _menu, label):
                labels.append(label)
                return FakeMenuItem(0)

            def AppendSeparator(self):
                pass

            def AppendRadioItem(self, item_id, label="", *args, **kwargs):
                labels.append(label)
                return FakeMenuItem(item_id)

            def AppendCheckItem(self, item_id, label="", *args, **kwargs):
                labels.append(label)
                return FakeMenuItem(item_id)

        class FakeMenuBar:
            def __init__(self):
                self.menus = []

            def Append(self, menu, title):
                self.menus.append((menu, title))

            def GetMenuCount(self):
                return len(self.menus)

            def Remove(self, index):
                self.menus.pop(index)

        class FakeFrame:
            def __init__(self, media_kind="video"):
                self.handlers = {}
                self.menu_bar = None
                self.media_kind = media_kind
                self.visual_items = []

            def Bind(self, _event_type, handler, id=None):
                if id is not None:
                    self.handlers[int(id)] = handler

            def SetAcceleratorTable(self, _table):
                pass

            def GetMenuBar(self):
                return self.menu_bar

            def SetMenuBar(self, menu_bar):
                self.menu_bar = menu_bar

            def Refresh(self):
                pass

            def Update(self):
                pass

            def __getattr__(self, name):
                if name.startswith("On"):
                    return lambda event=None: None
                raise AttributeError(name)

        for media_kind in ("audio", "video"):
            labels.clear()
            frame = FakeFrame(media_kind)
            with patch("video_maker.shortcuts.wx.AcceleratorTable", lambda entries: entries):
                frame.shortcut_ids = install_shortcuts(FakeFrame(media_kind))
            with patch("video_maker.menus.wx.Menu", FakeMenu), \
                 patch("video_maker.menus.wx.MenuBar", FakeMenuBar), \
                 patch("video_maker.menus.list_recent_files", lambda: []), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                install_menu_bar(frame, command_target=frame, include_shortcuts=False)
            self.assertIn("إدراج صوت", labels)

    def test_insert_audio_shortcut_is_registered_and_shown_in_menu(self):
        import wx

        from video_maker.menus import install_menu_bar
        from video_maker.shortcuts import install_shortcuts

        labels = []
        accelerator_entries = []

        class FakeMenuItem:
            def __init__(self, item_id):
                self._id = item_id

            def Enable(self, _enabled):
                pass

            def Check(self, _checked):
                pass

            def GetId(self):
                return self._id

        class FakeMenu:
            def Append(self, item_id, label="", *args, **kwargs):
                labels.append(label)
                return FakeMenuItem(item_id)

            def AppendSubMenu(self, _menu, label):
                labels.append(label)
                return FakeMenuItem(0)

            def AppendSeparator(self):
                pass

            def AppendRadioItem(self, item_id, label="", *args, **kwargs):
                labels.append(label)
                return FakeMenuItem(item_id)

            def AppendCheckItem(self, item_id, label="", *args, **kwargs):
                labels.append(label)
                return FakeMenuItem(item_id)

        class FakeMenuBar:
            def __init__(self):
                self.menus = []

            def Append(self, menu, title):
                self.menus.append((menu, title))

            def GetMenuCount(self):
                return len(self.menus)

            def Remove(self, index):
                self.menus.pop(index)

        class FakeFrame:
            media_kind = "video"
            visual_items = []

            def __init__(self):
                self.handlers = {}
                self.menu_bar = None

            def Bind(self, _event_type, handler, id=None):
                if id is not None:
                    self.handlers[int(id)] = handler

            def SetAcceleratorTable(self, entries):
                self.accelerator_entries = entries

            def GetMenuBar(self):
                return self.menu_bar

            def SetMenuBar(self, menu_bar):
                self.menu_bar = menu_bar

            def Refresh(self):
                pass

            def Update(self):
                pass

            def __getattr__(self, name):
                if name.startswith("On"):
                    return lambda event=None: None
                raise AttributeError(name)

        frame = FakeFrame()

        def capture_accelerators(entries):
            accelerator_entries.extend(entries)
            return list(entries)

        with patch("video_maker.shortcuts.wx.AcceleratorTable", capture_accelerators):
            frame.shortcut_ids = install_shortcuts(frame)
        self.assertIn((wx.ACCEL_CTRL, ord("B"), frame.shortcut_ids["insert_timeline_audio"]), accelerator_entries)

        with patch("video_maker.menus.wx.Menu", FakeMenu), \
             patch("video_maker.menus.wx.MenuBar", FakeMenuBar), \
             patch("video_maker.menus.list_recent_files", lambda: []), \
             patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
            install_menu_bar(frame, command_target=frame, include_shortcuts=True)
        self.assertIn("إدراج صوت\tCtrl+B", labels)

    def test_inserted_audio_timeline_uses_start_point_only(self):
        timeline = [TimelineSegment("main.wav", 0.0, 10.0)]

        updated = inserted_audio_timeline(timeline, "voice.wav", 3.0, 2.0)

        self.assertEqual(len(updated), 3)
        self.assertEqual((updated[0].path, updated[0].start, updated[0].end), ("main.wav", 0.0, 3.0))
        self.assertEqual((updated[1].path, updated[1].start, updated[1].end), ("voice.wav", 0.0, 2.0))
        self.assertEqual((updated[2].path, updated[2].start, updated[2].end), ("main.wav", 3.0, 10.0))

    def test_practical_audio_project_save_after_inserted_timeline_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first_audio = os.path.join(temp_dir, "first.wav")
            inserted_audio = os.path.join(temp_dir, "inserted.wav")
            output_audio = os.path.join(temp_dir, "out.wav")
            _write_tone(first_audio, 1.0, 330)
            _write_tone(inserted_audio, 1.0, 660)

            timeline = inserted_audio_timeline([TimelineSegment(first_audio, 0.0, 1.0)], inserted_audio, 1.0, 1.0)
            write_timeline_audio(timeline, output_audio)

            self.assertTrue(has_audio_stream(output_audio))
            self.assertAlmostEqual(get_media_duration(output_audio), 2.0, delta=0.12)

    def test_practical_audio_first_then_image_saves_as_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, "voice.wav")
            cover_image = os.path.join(temp_dir, "cover.png")
            output_video = os.path.join(temp_dir, "audio_first_image.mp4")
            frame_image = os.path.join(temp_dir, "audio_first_image_frame.png")
            _write_tone(audio_path, 1.5, 440)
            _write_test_image(cover_image)

            write_audio_visual_video(
                [TimelineSegment(audio_path, 0.0, 1.5)],
                [{"id": "cover", "type": "image", "path": cover_image, "start": 0.0, "end": 1.5}],
                output_video,
            )

            self.assertTrue(has_video_stream(output_video))
            self.assertTrue(has_audio_stream(output_video))
            self.assertAlmostEqual(get_media_duration(output_video), 1.5, delta=0.20)
            _extract_frame(output_video, frame_image, 0.75)
            _assert_dominant_color(self, frame_image, 2)

    def test_practical_audio_project_image_then_text_overlay_saves_as_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, "voice.wav")
            cover_image = os.path.join(temp_dir, "cover.png")
            text_image = os.path.join(temp_dir, "text.png")
            output_video = os.path.join(temp_dir, "audio_image_text.mp4")
            frame_image = os.path.join(temp_dir, "audio_image_text_frame.png")
            _write_tone(audio_path, 2.0, 440)
            _write_test_image(cover_image)
            _write_text_overlay(text_image)

            write_audio_visual_video(
                [TimelineSegment(audio_path, 0.0, 2.0)],
                [
                    {"id": "cover", "type": "image", "path": cover_image, "start": 0.0, "end": 2.0},
                    {"id": "text", "type": "text", "path": text_image, "start": 0.25, "end": 1.75},
                ],
                output_video,
            )

            self.assertTrue(has_video_stream(output_video))
            self.assertTrue(has_audio_stream(output_video))
            self.assertAlmostEqual(get_media_duration(output_video), 2.0, delta=0.20)
            _extract_frame(output_video, frame_image, 1.0)
            top_average = _average_rgb(frame_image, (500, 100, 780, 260))
            text_area_average = _average_rgb(frame_image, (320, 520, 960, 690))
            self.assertGreater(top_average[2], top_average[0] + 35, top_average)
            self.assertLess(text_area_average[2], top_average[2] - 15, (top_average, text_area_average))

    def test_practical_audio_project_image_and_partial_video_overlay_saves_as_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, "voice.wav")
            cover_image = os.path.join(temp_dir, "cover.png")
            overlay_video = os.path.join(temp_dir, "overlay.mp4")
            output_video = os.path.join(temp_dir, "audio_image_video_overlay.mp4")
            before_overlay_frame = os.path.join(temp_dir, "before_overlay.png")
            during_overlay_frame = os.path.join(temp_dir, "during_overlay.png")
            _write_tone(audio_path, 3.0, 440)
            _write_test_image(cover_image)
            _write_test_video(overlay_video, 1.2)

            write_audio_visual_video(
                [TimelineSegment(audio_path, 0.0, 3.0)],
                [
                    {"id": "cover", "type": "image", "path": cover_image, "start": 0.0, "end": 3.0},
                    {"id": "clip", "type": "video", "path": overlay_video, "start": 1.0, "end": 2.2, "source_offset": 0.0},
                ],
                output_video,
            )

            self.assertTrue(has_video_stream(output_video))
            self.assertTrue(has_audio_stream(output_video))
            self.assertAlmostEqual(get_media_duration(output_video), 3.0, delta=0.25)
            _extract_frame(output_video, before_overlay_frame, 0.5)
            _extract_frame(output_video, during_overlay_frame, 1.5)
            _assert_dominant_color(self, before_overlay_frame, 2)
            _assert_dominant_color(self, during_overlay_frame, 0)

    def test_practical_video_project_saves_after_inserted_audio_is_covered_by_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "main.mp4")
            inserted_audio = os.path.join(temp_dir, "inserted.wav")
            cover_image = os.path.join(temp_dir, "cover.png")
            output_video = os.path.join(temp_dir, "out.mp4")
            _write_test_video(video_path, 1.0)
            _write_tone(inserted_audio, 1.0, 660)
            _write_test_image(cover_image)

            timeline = inserted_audio_timeline([TimelineSegment(video_path, 0.0, 1.0)], inserted_audio, 1.0, 1.0)
            write_timeline_video(
                timeline,
                output_video,
                save_options={"video_quality": "medium"},
                visual_items=[{"id": "cover", "type": "image", "path": cover_image, "start": 1.0, "end": 2.0}],
            )

            self.assertTrue(has_video_stream(output_video))
            self.assertTrue(has_audio_stream(output_video))
            self.assertAlmostEqual(get_media_duration(output_video), 2.0, delta=0.20)

    def test_insert_audio_in_audio_project_adds_to_main_timeline_only(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            frame = _frame("audio")
            frame.start_time = 1.0
            frame.end_time = 2.0

            with patch("video_maker.player_modules.media_insert.choose_timeline_audio_path", return_value=audio_file.name), \
                 patch("video_maker.player_modules.media_insert.has_audio_stream", return_value=True), \
                 patch("video_maker.player_modules.media_insert.get_media_duration", return_value=2.5), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                frame.OnInsertTimelineAudio()

        self.assertEqual([segment.path for segment in frame.timeline], ["main.wav", audio_file.name, "main.wav"])
        self.assertEqual(frame.background_audio_items, [])
        self.assertEqual(frame.sound_effects_items, [])
        self.assertEqual(frame.shift_calls, [(4.0, 2.5)])
        self.assertEqual(frame.edit_points, [("audio", 4.0, 6.5, "timeline", "insert")])
        self.assertTrue(frame.is_dirty)
        self.assertEqual(frame.spoken[-1], "تم إدراج الصوت")

    def test_insert_audio_in_video_project_adds_to_main_timeline_only(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            frame = _frame("video")

            with patch("video_maker.player_modules.media_insert.choose_timeline_audio_path", return_value=audio_file.name), \
                 patch("video_maker.player_modules.media_insert.has_audio_stream", return_value=True), \
                 patch("video_maker.player_modules.media_insert.get_media_duration", return_value=3.0), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                frame.OnInsertTimelineAudio()

        self.assertEqual([segment.path for segment in frame.timeline], ["main.mp4", audio_file.name, "main.mp4"])
        self.assertEqual(frame.background_audio_items, [])
        self.assertEqual(frame.sound_effects_items, [])
        self.assertEqual(frame.edit_points, [("audio", 4.0, 7.0, "timeline", "insert")])

    def test_video_save_rejects_uncovered_timeline_audio_segment(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            timeline = [TimelineSegment(audio_file.name, 0.0, 2.0)]
            message = video_save_block_message_for_timeline(
                timeline,
                [],
                [],
                has_audio_stream=lambda _path: True,
                has_video_stream=lambda _path: False,
            )

        self.assertEqual(message, "لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو.")

    def test_video_save_allows_timeline_audio_when_visual_covers_it(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            timeline = [TimelineSegment(audio_file.name, 0.0, 2.0)]
            message = video_save_block_message_for_timeline(
                timeline,
                [{"type": "image", "path": "cover.png", "start": 0.0, "end": 2.0}],
                [],
                has_audio_stream=lambda _path: True,
                has_video_stream=lambda _path: False,
            )

        self.assertEqual(message, "")

    def test_video_save_allows_timeline_audio_when_b_roll_covers_it(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            timeline = [TimelineSegment(audio_file.name, 0.0, 2.0)]
            message = video_save_block_message_for_timeline(
                timeline,
                [],
                [{"type": "video", "path": "cover.mp4", "start": 0.0, "end": 2.0}],
                has_audio_stream=lambda _path: True,
                has_video_stream=lambda _path: False,
            )

        self.assertEqual(message, "")

    def test_on_save_video_speaks_uncovered_timeline_audio_without_opening_dialog(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            frame = _frame("video")
            frame.timeline = [TimelineSegment(audio_file.name, 0.0, 2.0)]
            frame.timeline_transform_progress_dialog = None
            frame.video_path = "project.mp4"

            with patch("video_maker.player_modules.save.has_audio_stream", return_value=True), \
                 patch("video_maker.player_modules.save.has_video_stream", return_value=False), \
                 patch("video_maker.player_modules.save.ask_video_save_path", side_effect=AssertionError("save dialog should not open")), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                frame.OnSaveVideo()

        self.assertEqual(frame.spoken, ["لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو."])

    def test_on_save_selected_video_speaks_uncovered_timeline_audio_without_opening_dialog(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            frame = _frame("video")
            frame.timeline = [TimelineSegment(audio_file.name, 0.0, 2.0)]
            frame.timeline_transform_progress_dialog = None
            frame.video_path = "project.mp4"
            frame.has_video = lambda: True
            frame.selected_effect_range = lambda: (0.0, 2.0)

            with patch("video_maker.player_modules.save.has_audio_stream", return_value=True), \
                 patch("video_maker.player_modules.save.has_video_stream", return_value=False), \
                 patch("video_maker.player_modules.save.ask_video_save_path", side_effect=AssertionError("save dialog should not open")), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                frame.OnSaveSelectedVideo()

        self.assertEqual(frame.spoken, ["لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو."])

    def test_save_video_worker_speaks_uncovered_timeline_audio_without_error_dialog(self):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            frame = VideoPlayer.__new__(VideoPlayer)
            frame.window_number = 1
            frame.save_cancelled = False
            frame.CreateProgressDialog = lambda *args, **kwargs: None
            frame.DestroyProgressDialog = lambda: None
            spoken = []
            frame.say = lambda message, **kwargs: spoken.append(message)
            frame.OnSaveError = lambda message, kind: self.fail("error dialog path should not be used")
            frame.OnSaveCancelled = lambda _path: None

            with patch("video_maker.player_modules.save.wx.CallAfter", lambda func, *args, **kwargs: func(*args, **kwargs)), \
                 patch("video_maker.player_modules.save.has_audio_stream", return_value=True), \
                 patch("video_maker.player_modules.save.has_video_stream", return_value=False), \
                 patch("video_maker.player_modules.save.write_timeline_video", side_effect=AssertionError("render should not start")), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                frame.SaveVideo(
                    "out.mp4",
                    [TimelineSegment(audio_file.name, 0.0, 2.0)],
                    {},
                    True,
                    [],
                    "video",
                    {},
                    [],
                    "",
                    0.0,
                    [],
                    [],
                    set(),
                    set(),
                )

        self.assertEqual(spoken, ["لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو."])


if __name__ == "__main__":
    unittest.main()
