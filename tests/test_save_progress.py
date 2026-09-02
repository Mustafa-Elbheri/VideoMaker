import io
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from video_maker.player import VideoPlayer
from video_maker.timeline import TimelineSegment
from video_maker.video_editing import (
    ffmpeg_progress_seconds,
    prepare_timeline_boundary_safe_audio_proxies,
    write_timeline_video,
)


class FakeProgressDialog:
    def __init__(self, *_args, **_kwargs):
        self.values = []
        self.shown = False
        self.focused = False

    def Show(self):
        self.shown = True

    def focus_navigation_controls(self):
        self.focused = True

    def update_progress(self, value):
        self.values.append(int(value))


class SaveProgressTest(unittest.TestCase):
    def make_player(self):
        player = VideoPlayer.__new__(VideoPlayer)
        player.window_number = 1
        player.progress_dialog = FakeProgressDialog()
        player.say_messages = []
        player.say = lambda message, **_kwargs: player.say_messages.append(message)
        player.save_cancelled = False
        player._diagnostic_active_operation = "save:video"
        player.reset_save_progress_state()
        return player

    def test_save_progress_never_moves_backward_or_outside_range(self):
        player = self.make_player()

        player.UpdateProgressDialog(5)
        player.UpdateProgressDialog(4)
        player.UpdateProgressDialog(120)
        player.UpdateProgressDialog(100)

        self.assertEqual(player.save_progress_percent, 100)
        self.assertEqual(player.progress_dialog.values, [5, 5, 100, 100])
        self.assertEqual(sum("100" in message for message in player.say_messages), 1)

    def test_save_progress_dialog_opens_at_latest_known_percent(self):
        player = self.make_player()
        player.progress_dialog = None
        player.cancel_save = lambda: None
        player.save_progress_percent = 37
        fake_dialog = FakeProgressDialog()

        with patch("video_maker.player.SaveProgressDialog", return_value=fake_dialog):
            player.CreateProgressDialog("video")

        self.assertTrue(fake_dialog.shown)
        self.assertEqual(fake_dialog.values, [37])

    def test_update_download_progress_dialog_is_focused_for_keyboard_navigation(self):
        player = VideoPlayer.__new__(VideoPlayer)
        player.say_messages = []
        player.say = lambda message, **_kwargs: player.say_messages.append(message)
        fake_dialog = FakeProgressDialog()
        started_threads = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self):
                started_threads.append((self.target, self.args, self.daemon))

        with patch("video_maker.player_modules.update_recording.SaveProgressDialog", return_value=fake_dialog), patch(
            "video_maker.player_modules.update_recording.threading.Thread", FakeThread
        ):
            player.start_update_download({"asset_url": "https://example.test/update.exe"})

        self.assertTrue(fake_dialog.shown)
        self.assertTrue(fake_dialog.focused)
        self.assertIs(player.update_progress_dialog, fake_dialog)
        self.assertEqual(len(started_threads), 1)

    def test_ffmpeg_progress_accepts_out_time_format(self):
        self.assertAlmostEqual(ffmpeg_progress_seconds("out_time", "00:01:02.500000"), 62.5)
        self.assertAlmostEqual(ffmpeg_progress_seconds("time", "01:02:03.250"), 3723.25)
        self.assertIsNone(ffmpeg_progress_seconds("out_time", "N/A"))

    def test_boundary_proxy_preparation_reports_each_restored_source(self):
        progress_values = []

        def fake_proxy(path, progress_callback=None, cancelled_callback=None):
            if progress_callback:
                progress_callback(0)
                progress_callback(50)
                progress_callback(100)
            return f"{path}.flac", f"{path}.dir"

        timeline = [
            TimelineSegment("first.mp4", 0.0, 1.0),
            TimelineSegment("second.mp4", 0.0, 1.0),
            TimelineSegment("first.mp4", 1.0, 2.0),
        ]

        with patch("video_maker.video_editing.prepare_boundary_safe_audio_proxy", side_effect=fake_proxy):
            proxy_paths, proxy_dirs = prepare_timeline_boundary_safe_audio_proxies(timeline, progress_values.append)

        self.assertEqual(sorted(proxy_paths), sorted([__import__("os").path.abspath("first.mp4"), __import__("os").path.abspath("second.mp4")]))
        self.assertEqual(proxy_dirs, ["first.mp4.dir", "second.mp4.dir"])
        self.assertEqual(progress_values[0], 0)
        self.assertEqual(progress_values[-1], 100)
        self.assertTrue(any(0 < value < 100 for value in progress_values))

    def test_video_save_maps_proxy_stage_before_render_stage(self):
        source = "restored.mp4"
        output = "out.mp4"
        progress_values = []

        def fake_proxy_stage(timeline, progress_callback=None, cancelled_callback=None, extra_paths=None, include_timeline_segments=True):
            if progress_callback:
                progress_callback(0)
                progress_callback(50)
                progress_callback(100)
            return {}, []

        def fake_render(command, *_args, progress_callback=None, **_kwargs):
            if progress_callback:
                progress_callback(0)
                progress_callback(50)
                progress_callback(100)

        with patch("video_maker.video_editing.has_audio_stream", return_value=True), \
             patch("video_maker.video_editing.prepare_timeline_boundary_safe_audio_proxies", side_effect=fake_proxy_stage), \
             patch("video_maker.watermark.run_ffmpeg_with_progress", side_effect=fake_render), \
             patch("video_maker.video_editing.apply_metadata"):
            write_timeline_video(
                [TimelineSegment(source, 0.0, 10.0)],
                output,
                progress_callback=progress_values.append,
                save_options={"video_quality": "medium"},
            )

        self.assertIn(4, [int(value) for value in progress_values])
        self.assertIn(9, [int(value) for value in progress_values])
        self.assertIn(14, [int(value) for value in progress_values])
        self.assertIn(57, [int(value) for value in progress_values])
        self.assertEqual(int(progress_values[-1]), 100)

    def test_final_render_does_not_leave_stderr_pipe_unread(self):
        from video_maker import watermark

        class FakeProcess:
            def __init__(self, _command, stdout=None, stderr=None, **_kwargs):
                self.stdout = io.StringIO("out_time_us=100000\nprogress=end\n")
                self._returncode = 0
                self.stderr_target = stderr
                self.stderr_target.write(b"E" * 200000)

            def poll(self):
                return self._returncode

            def wait(self):
                return self._returncode

        with tempfile.NamedTemporaryFile(delete=False) as output_file:
            output_file.write(b"ok")
            output_path = output_file.name
        try:
            progress_values = []
            with patch("video_maker.watermark.subprocess.Popen", side_effect=FakeProcess) as popen:
                watermark.run_ffmpeg_with_progress(
                    ["ffmpeg", "-i", "input.mp4", output_path],
                    output_path,
                    output_path,
                    "failed",
                    progress_callback=progress_values.append,
                    total_duration=1.0,
                )

            stderr_target = popen.call_args.kwargs["stderr"]
            self.assertIsNot(stderr_target, subprocess.PIPE)
            self.assertTrue(hasattr(stderr_target, "write"))
            self.assertEqual(int(progress_values[-1]), 100)
        finally:
            try:
                os.remove(output_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
