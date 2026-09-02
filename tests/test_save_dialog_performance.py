import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import wx

from video_maker import dialogs
from video_maker.player import VideoPlayer
from video_maker.timeline import TimelineSegment
from video_maker.timeline_split import SplitRange


class SaveDialogPerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def yield_events(self, milliseconds=100):
        end_time = time.perf_counter() + milliseconds / 1000.0
        while time.perf_counter() < end_time:
            wx.Yield()
            time.sleep(0.01)

    def make_save_frame(self, media_kind="video", visual_items=None):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.media_kind = media_kind
        frame.video_path = "source.mp4" if media_kind == "video" else "source.wav"
        frame.timeline_transform_progress_dialog = None
        frame.pending_save_after_transform = False
        frame.require_open_file = lambda: True
        frame.has_video = lambda: True
        frame.selected_effect_range = lambda: (1.0, 3.0)
        frame.say = lambda *args, **kwargs: None
        frame.timeline = [TimelineSegment(frame.video_path, 0.0, 10.0)]
        frame.chroma_render_state = {}
        frame.visual_items = list(visual_items or [])
        frame.background_audio_items = []
        frame.file_metadata = {}
        frame.volume = 1.0
        frame.save_cancelled = False
        frame.reset_save_progress_state = lambda: None
        frame.SaveVideo = lambda *args: None
        frame.SaveTimelineSplit = lambda *args: None
        return frame

    def assert_dialog_requested_before_save_preparation(self, frame, method_name, ask_patch_target, ask_return, *method_args):
        dialog_requested = threading.Event()

        def fail_snapshot():
            self.fail("save dialog must be requested before timeline snapshot preparation")

        def fail_override(*args, **kwargs):
            self.fail("save dialog must be requested before audio override validation")

        frame.timeline_snapshot_for_save = fail_snapshot
        frame.video_audio_override_snapshot = fail_override

        with patch(ask_patch_target, lambda *args, **kwargs: dialog_requested.set() or ask_return):
            getattr(frame, method_name)(*method_args)

        self.assertTrue(dialog_requested.is_set())

    def assert_progress_created_before_save_preparation(
        self,
        frame,
        method_name,
        ask_patch_target,
        ask_return,
        progress_method_name,
        *method_args,
    ):
        progress_created = threading.Event()
        scheduled_start = []

        def fail_snapshot():
            self.fail("progress dialog must be created before timeline snapshot preparation")

        def fail_override(*args, **kwargs):
            self.fail("progress dialog must be created before audio override validation")

        frame.timeline_snapshot_for_save = fail_snapshot
        frame.video_audio_override_snapshot = fail_override
        setattr(frame, progress_method_name, lambda *args, **kwargs: progress_created.set())
        frame.call_after_or_now = lambda callback, *args: scheduled_start.append((callback, args))

        with patch(ask_patch_target, return_value=ask_return):
            getattr(frame, method_name)(*method_args)

        self.assertTrue(progress_created.is_set())
        self.assertEqual(len(scheduled_start), 1)

    def test_save_dialog_is_shown_before_source_probe_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "slow_source.mp4"
            source_path.write_bytes(b"not a real media file; probing is patched")
            probe_started = threading.Event()
            release_probe = threading.Event()

            def slow_probe(path):
                probe_started.set()
                release_probe.wait(3.0)
                profile = dialogs.quick_source_profile(path)
                profile.update({"width": 1920, "height": 1080, "video_bitrate": 8000})
                return profile

            dialog = None
            with patch.object(dialogs, "probe_source_profile", slow_probe):
                try:
                    started = time.perf_counter()
                    dialog = dialogs.AccessibleMediaSaveDialog(None, None, False, "video", str(source_path))
                    construct_elapsed = time.perf_counter() - started

                    self.assertLess(construct_elapsed, 1.0)
                    self.assertFalse(probe_started.is_set())

                    dialog.Show()
                    self.yield_events(150)
                    self.assertTrue(dialog.IsShown())
                    self.assertTrue(probe_started.wait(0.5))
                    self.assertTrue(dialog.IsShown())

                    release_probe.set()
                    self.yield_events(700)
                    labels = [dialog.dimension_choice.GetString(index) for index in range(dialog.dimension_choice.GetCount())]
                    self.assertTrue(any("1920" in label and "1080" in label for label in labels))
                finally:
                    release_probe.set()
                    if dialog is not None:
                        dialog.Destroy()
                        self.yield_events(100)

    def test_audio_save_dialog_is_shown_before_source_probe_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "slow_source.wav"
            source_path.write_bytes(b"not a real media file; probing is patched")
            probe_started = threading.Event()
            release_probe = threading.Event()

            def slow_probe(path):
                probe_started.set()
                release_probe.wait(3.0)
                profile = dialogs.quick_source_profile(path)
                profile.update({"audio_bitrate": 128, "channels": 2, "sample_rate": 48000})
                return profile

            dialog = None
            with patch.object(dialogs, "probe_source_profile", slow_probe):
                try:
                    started = time.perf_counter()
                    dialog = dialogs.AccessibleMediaSaveDialog(None, None, False, "audio", str(source_path))
                    construct_elapsed = time.perf_counter() - started

                    self.assertLess(construct_elapsed, 1.0)
                    self.assertFalse(probe_started.is_set())

                    dialog.Show()
                    self.yield_events(150)
                    self.assertTrue(dialog.IsShown())
                    self.assertTrue(probe_started.wait(0.5))
                    self.assertTrue(dialog.IsShown())
                finally:
                    release_probe.set()
                    if dialog is not None:
                        dialog.Destroy()
                        self.yield_events(100)

    def test_full_video_save_dialog_is_requested_before_save_preparation(self):
        frame = self.make_save_frame("video")
        self.assert_dialog_requested_before_save_preparation(
            frame,
            "OnSaveVideo",
            "video_maker.player.ask_video_save_path",
            ("", None),
        )

    def test_full_audio_save_dialog_is_requested_before_save_preparation(self):
        frame = self.make_save_frame("audio")
        self.assert_dialog_requested_before_save_preparation(
            frame,
            "OnSaveVideo",
            "video_maker.player.ask_audio_save_path",
            ("", None),
        )

    def test_export_video_audio_dialog_is_requested_before_save_preparation(self):
        frame = self.make_save_frame("video")
        self.assert_dialog_requested_before_save_preparation(
            frame,
            "OnExportVideoAudio",
            "video_maker.player.ask_audio_save_path",
            ("", None),
        )

    def test_selected_audio_save_dialog_is_requested_before_save_preparation(self):
        frame = self.make_save_frame("audio")
        self.assert_dialog_requested_before_save_preparation(
            frame,
            "OnSaveSelectedVideo",
            "video_maker.player.ask_audio_save_path",
            ("", None),
        )

    def test_split_video_save_dialog_is_requested_before_save_preparation(self):
        frame = self.make_save_frame("video")
        frame.selected_effect_range = lambda: (0.0, 4.0)
        with patch("video_maker.player.split_ranges_for_options", return_value=[SplitRange(0.0, 2.0), SplitRange(2.0, 4.0)]):
            self.assert_dialog_requested_before_save_preparation(
                frame,
                "StartTimelineSplit",
                "video_maker.player.ask_video_save_path",
                ("", None),
                {"duration": 2.0},
            )

    def test_split_audio_save_dialog_is_requested_before_save_preparation(self):
        frame = self.make_save_frame("audio")
        frame.selected_effect_range = lambda: (0.0, 4.0)
        with patch("video_maker.player.split_ranges_for_options", return_value=[SplitRange(0.0, 2.0), SplitRange(2.0, 4.0)]):
            self.assert_dialog_requested_before_save_preparation(
                frame,
                "StartTimelineSplit",
                "video_maker.player.ask_audio_save_path",
                ("", None),
                {"duration": 2.0},
            )

    def test_full_video_progress_is_created_before_save_preparation(self):
        frame = self.make_save_frame("video")
        self.assert_progress_created_before_save_preparation(
            frame,
            "OnSaveVideo",
            "video_maker.player.ask_video_save_path",
            ("output.mp4", {"format": "mp4"}),
            "CreateProgressDialog",
        )

    def test_full_audio_progress_is_created_before_save_preparation(self):
        frame = self.make_save_frame("audio")
        self.assert_progress_created_before_save_preparation(
            frame,
            "OnSaveVideo",
            "video_maker.player.ask_audio_save_path",
            ("output.wav", {"format": "wav"}),
            "CreateProgressDialog",
        )

    def test_export_video_audio_progress_is_created_before_save_preparation(self):
        frame = self.make_save_frame("video")
        self.assert_progress_created_before_save_preparation(
            frame,
            "OnExportVideoAudio",
            "video_maker.player.ask_audio_save_path",
            ("output.wav", {"format": "wav"}),
            "CreateProgressDialog",
        )

    def test_selected_audio_progress_is_created_before_save_preparation(self):
        frame = self.make_save_frame("audio")
        self.assert_progress_created_before_save_preparation(
            frame,
            "OnSaveSelectedVideo",
            "video_maker.player.ask_audio_save_path",
            ("selected.wav", {"format": "wav"}),
            "CreateProgressDialog",
        )

    def test_split_video_progress_is_created_before_save_preparation(self):
        frame = self.make_save_frame("video")
        frame.selected_effect_range = lambda: (0.0, 4.0)
        with patch("video_maker.player.split_ranges_for_options", return_value=[SplitRange(0.0, 2.0), SplitRange(2.0, 4.0)]):
            self.assert_progress_created_before_save_preparation(
                frame,
                "StartTimelineSplit",
                "video_maker.player.ask_video_save_path",
                ("split.mp4", {"format": "mp4"}),
                "CreateSplitProgressDialog",
                {"duration": 2.0},
            )

    def test_split_audio_progress_is_created_before_save_preparation(self):
        frame = self.make_save_frame("audio")
        frame.selected_effect_range = lambda: (0.0, 4.0)
        with patch("video_maker.player.split_ranges_for_options", return_value=[SplitRange(0.0, 2.0), SplitRange(2.0, 4.0)]):
            self.assert_progress_created_before_save_preparation(
                frame,
                "StartTimelineSplit",
                "video_maker.player.ask_audio_save_path",
                ("split.wav", {"format": "wav"}),
                "CreateSplitProgressDialog",
                {"duration": 2.0},
            )

    def test_save_selected_dialog_is_requested_before_chroma_snapshot_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "source.mp4"
            render_path = Path(temporary) / "chroma_render.mp4"
            source_path.write_bytes(b"source")
            render_path.write_bytes(b"render")

            frame = VideoPlayer.__new__(VideoPlayer)
            frame.media_kind = "video"
            frame.video_path = str(source_path)
            frame.timeline_transform_progress_dialog = None
            frame.has_video = lambda: True
            frame.selected_effect_range = lambda: (1.0, 2.0)
            frame.say = lambda *args, **kwargs: None
            frame.timeline = [TimelineSegment(str(source_path), 0.0, 10.0)]
            frame.chroma_render_state = {
                "render_path": str(render_path),
                "source_paths": [str(source_path)],
            }
            frame.visual_items = []
            frame.background_audio_items = []
            frame.file_metadata = {}
            frame.volume = 1.0
            frame.video_audio_override_snapshot = lambda start=0.0: ("", 0.0)
            frame.CreateProgressDialog = lambda *args, **kwargs: None
            frame.call_after_or_now = lambda callback, *args: None

            probe_started = threading.Event()
            release_probe = threading.Event()
            dialog_requested = threading.Event()

            def slow_duration(path):
                probe_started.set()
                release_probe.wait(2.0)
                return 10.0

            with patch("video_maker.player.get_media_duration", slow_duration), patch(
                "video_maker.player.ask_video_save_path",
                lambda *args, **kwargs: dialog_requested.set() or ("", None),
            ):
                worker = threading.Thread(target=frame.OnSaveSelectedVideo)
                worker.start()
                try:
                    self.assertTrue(dialog_requested.wait(0.5))
                    self.assertFalse(probe_started.is_set())
                finally:
                    release_probe.set()
                    worker.join(2.0)
            self.assertFalse(worker.is_alive())

    def test_save_selected_builds_snapshot_after_path_is_chosen(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_path = str(Path(temporary) / "selected.mp4")
            frame = VideoPlayer.__new__(VideoPlayer)
            frame.media_kind = "video"
            frame.video_path = "source.mp4"
            frame.timeline_transform_progress_dialog = None
            frame.has_video = lambda: True
            frame.selected_effect_range = lambda: (1.0, 3.0)
            frame.say = lambda *args, **kwargs: None
            frame.timeline = [TimelineSegment("source.mp4", 0.0, 10.0)]
            frame.chroma_render_state = {}
            frame.visual_items = []
            frame.background_audio_items = []
            frame.file_metadata = {}
            frame.volume = 1.0
            frame.video_audio_override_snapshot = lambda start=0.0: ("", 0.0)
            frame.reset_save_progress_state = lambda: None
            frame.CreateProgressDialog = lambda *args, **kwargs: None
            frame.call_after_or_now = lambda callback, *args: callback(*args)
            save_started = threading.Event()
            save_args = {}

            def fake_save_video(*args):
                save_args["args"] = args
                save_started.set()

            frame.SaveVideo = fake_save_video

            with patch("video_maker.player.ask_video_save_path", return_value=(output_path, {"format": "mp4"})):
                frame.OnSaveSelectedVideo()

            self.assertTrue(save_started.wait(1.0))
            args = save_args["args"]
            self.assertEqual(args[0], output_path)
            self.assertEqual(args[1], [TimelineSegment("source.mp4", 1.0, 3.0)])


if __name__ == "__main__":
    unittest.main()
