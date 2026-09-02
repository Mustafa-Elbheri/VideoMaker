import os
import struct
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

from video_maker import localization
from video_maker.app_paths import ffmpeg_binary
from video_maker.player import VideoPlayer
from video_maker.timeline import TimelineSegment
from video_maker.timeline_audio_insert import video_save_block_message_for_timeline
from video_maker.timeline_silence_insert import (
    create_silence_audio_file,
    inserted_silence_timeline,
    parse_silence_duration,
)
from video_maker.video_editing import get_media_duration, has_audio_stream, has_video_stream, write_timeline_audio, write_timeline_video


def _run_ffmpeg(args):
    result = subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
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


def _average_abs_pcm(path, start, end):
    with wave.open(path, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        wav_file.setpos(int(start * sample_rate))
        frames = wav_file.readframes(max(1, int((end - start) * sample_rate)))
    if sample_width != 2:
        raise AssertionError(f"expected 16-bit PCM, got sample width {sample_width}")
    values = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    first_channel = values[::channels]
    return sum(abs(value) for value in first_channel) / max(1, len(first_channel))


def _frame(media_kind):
    frame = VideoPlayer.__new__(VideoPlayer)
    frame.media_kind = media_kind
    frame.timeline = [TimelineSegment("main.mp4" if media_kind == "video" else "main.wav", 0.0, 10.0)]
    frame.visual_items = []
    frame.background_audio_items = []
    frame.b_roll_items = []
    frame.sound_effects_items = []
    frame.current_time = 4.0
    frame.start_time = None
    frame.end_time = None
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


class TimelineSilenceInsertTest(unittest.TestCase):
    def test_insert_silence_text_is_translated(self):
        from video_maker.localization import tr

        with patch.object(localization, "get_language", lambda default="ar", language="ar": "en"):
            self.assertEqual(tr("إدراج صمت"), "Insert silence")
            self.assertEqual(tr("مدة الصمت بالثواني"), "Silence duration in seconds")
            self.assertEqual(tr("تم إدراج الصمت"), "Silence inserted")
        with patch.object(localization, "get_language", lambda default="ar", language="ar": "fr"):
            self.assertEqual(tr("إدراج صمت"), "Insérer un silence")
            self.assertEqual(tr("مدة الصمت بالثواني"), "Durée du silence en secondes")
            self.assertEqual(tr("تم إدراج الصمت"), "Silence inséré")

    def test_parse_silence_duration_accepts_decimal_and_rejects_invalid_values(self):
        self.assertAlmostEqual(parse_silence_duration("2.5"), 2.5)
        self.assertAlmostEqual(parse_silence_duration("2,5"), 2.5)
        for value in ("", "0", "-1", "text"):
            with self.assertRaises(ValueError):
                parse_silence_duration(value)

    def test_create_silence_audio_file_practically_creates_silent_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            silence_path = create_silence_audio_file(0.8, folder=temp_dir)

            self.assertTrue(os.path.exists(silence_path))
            self.assertTrue(has_audio_stream(silence_path))
            self.assertAlmostEqual(get_media_duration(silence_path), 0.8, delta=0.08)
            self.assertLess(_average_abs_pcm(silence_path, 0.1, 0.7), 1.0)

    def test_inserted_silence_timeline_uses_start_point_only(self):
        timeline = [TimelineSegment("main.wav", 0.0, 10.0)]

        updated = inserted_silence_timeline(timeline, "silence.wav", 3.0, 2.0)

        self.assertEqual(len(updated), 3)
        self.assertEqual((updated[0].path, updated[0].start, updated[0].end), ("main.wav", 0.0, 3.0))
        self.assertEqual((updated[1].path, updated[1].start, updated[1].end), ("silence.wav", 0.0, 2.0))
        self.assertEqual((updated[2].path, updated[2].start, updated[2].end), ("main.wav", 3.0, 10.0))

    def test_insert_silence_in_audio_project_adds_to_main_timeline_only(self):
        frame = _frame("audio")
        frame.start_time = 1.0
        frame.end_time = 2.0

        with patch("video_maker.player_modules.media_insert.choose_silence_duration", return_value=2.5), \
             patch("video_maker.player_modules.media_insert.create_silence_audio_file", return_value="silence.wav"), \
             patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
            frame.OnInsertTimelineSilence()

        self.assertEqual([segment.path for segment in frame.timeline], ["main.wav", "silence.wav", "main.wav"])
        self.assertEqual(frame.background_audio_items, [])
        self.assertEqual(frame.sound_effects_items, [])
        self.assertEqual(frame.shift_calls, [(4.0, 2.5)])
        self.assertEqual(frame.edit_points, [("silence", 4.0, 6.5, "timeline", "insert")])
        self.assertTrue(frame.is_dirty)
        self.assertEqual(frame.spoken[-1], "تم إدراج الصمت")

    def test_insert_silence_in_video_project_adds_to_main_timeline_only(self):
        frame = _frame("video")

        with patch("video_maker.player_modules.media_insert.choose_silence_duration", return_value=3.0), \
             patch("video_maker.player_modules.media_insert.create_silence_audio_file", return_value="silence.wav"), \
             patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
            frame.OnInsertTimelineSilence()

        self.assertEqual([segment.path for segment in frame.timeline], ["main.mp4", "silence.wav", "main.mp4"])
        self.assertEqual(frame.background_audio_items, [])
        self.assertEqual(frame.sound_effects_items, [])
        self.assertEqual(frame.edit_points, [("silence", 4.0, 7.0, "timeline", "insert")])

    def test_video_save_rejects_uncovered_inserted_silence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            silence_path = create_silence_audio_file(1.0, folder=temp_dir)
            timeline = [TimelineSegment(silence_path, 0.0, 1.0)]

            message = video_save_block_message_for_timeline(
                timeline,
                [],
                [],
                has_audio_stream=has_audio_stream,
                has_video_stream=has_video_stream,
            )

        self.assertEqual(message, "لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو.")

    def test_video_save_allows_inserted_silence_when_image_covers_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            silence_path = create_silence_audio_file(1.0, folder=temp_dir)
            timeline = [TimelineSegment(silence_path, 0.0, 1.0)]

            message = video_save_block_message_for_timeline(
                timeline,
                [{"type": "image", "path": "cover.png", "start": 0.0, "end": 1.0}],
                [],
                has_audio_stream=has_audio_stream,
                has_video_stream=has_video_stream,
            )

        self.assertEqual(message, "")

    def test_practical_audio_project_save_after_inserted_silence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first_audio = os.path.join(temp_dir, "first.wav")
            silence_audio = create_silence_audio_file(0.75, folder=temp_dir)
            output_audio = os.path.join(temp_dir, "out.wav")
            _write_tone(first_audio, 1.0, 330)

            timeline = inserted_silence_timeline([TimelineSegment(first_audio, 0.0, 1.0)], silence_audio, 0.5, 0.75)
            write_timeline_audio(timeline, output_audio)

            self.assertTrue(has_audio_stream(output_audio))
            self.assertAlmostEqual(get_media_duration(output_audio), 1.75, delta=0.12)
            self.assertGreater(_average_abs_pcm(output_audio, 0.1, 0.4), 50.0)
            self.assertLess(_average_abs_pcm(output_audio, 0.65, 1.10), 1.0)

    def test_practical_video_project_saves_after_inserted_silence_is_covered_by_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "main.mp4")
            silence_audio = create_silence_audio_file(1.0, folder=temp_dir)
            cover_image = os.path.join(temp_dir, "cover.png")
            output_video = os.path.join(temp_dir, "out.mp4")
            _write_test_video(video_path, 1.0)
            _write_test_image(cover_image)

            timeline = inserted_silence_timeline([TimelineSegment(video_path, 0.0, 1.0)], silence_audio, 1.0, 1.0)
            write_timeline_video(
                timeline,
                output_video,
                save_options={"video_quality": "medium"},
                visual_items=[{"id": "cover", "type": "image", "path": cover_image, "start": 1.0, "end": 2.0}],
            )

            self.assertTrue(has_video_stream(output_video))
            self.assertTrue(has_audio_stream(output_video))
            self.assertAlmostEqual(get_media_duration(output_video), 2.0, delta=0.20)

    def test_insert_menu_contains_insert_silence_for_audio_and_video_projects(self):
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
            self.assertIn("إدراج صمت", labels)

    def test_insert_silence_shortcut_is_registered_and_shown_in_menu(self):
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
        self.assertIn((wx.ACCEL_CTRL, ord("D"), frame.shortcut_ids["insert_timeline_silence"]), accelerator_entries)

        with patch("video_maker.menus.wx.Menu", FakeMenu), \
             patch("video_maker.menus.wx.MenuBar", FakeMenuBar), \
             patch("video_maker.menus.list_recent_files", lambda: []), \
             patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
            install_menu_bar(frame, command_target=frame, include_shortcuts=True)
        self.assertIn("إدراج صمت\tCtrl+D", labels)

    def test_on_save_video_speaks_uncovered_inserted_silence_without_opening_dialog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            silence_path = create_silence_audio_file(1.0, folder=temp_dir)
            frame = _frame("video")
            frame.timeline = [TimelineSegment(silence_path, 0.0, 1.0)]
            frame.timeline_transform_progress_dialog = None
            frame.video_path = "project.mp4"

            with patch("video_maker.player_modules.save.has_audio_stream", return_value=True), \
                 patch("video_maker.player_modules.save.has_video_stream", return_value=False), \
                 patch("video_maker.player_modules.save.ask_video_save_path", side_effect=AssertionError("save dialog should not open")), \
                 patch.object(localization, "get_language", lambda default="ar", language="ar": "ar"):
                frame.OnSaveVideo()

        self.assertEqual(frame.spoken, ["لا يمكن حفظ الفيديو لأن هناك مقطع صوت في الخط الزمني بدون صورة أو فيديو."])


if __name__ == "__main__":
    unittest.main()
