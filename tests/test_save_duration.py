import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from video_maker.app_paths import ffmpeg_binary
from video_maker.audio_override_manager import MainAudioOverrideManager
from video_maker.timeline import TimelineSegment, total_duration
from video_maker.video_editing import (
    get_media_duration,
    has_audio_stream,
    has_video_stream,
    preferred_audio_bitrate_for_codec,
    video_output_settings,
    write_timeline_audio,
    write_timeline_video,
)


class SaveDurationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_maker_save_duration_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_ffmpeg(self, args):
        command = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", *args]
        try:
            subprocess.run(command, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise unittest.SkipTest(f"FFmpeg is unavailable for duration export tests: {error}") from error

    def test_video_export_keeps_video_duration_when_source_audio_is_shorter(self):
        source = self.temp_dir / "source_video_4_audio_3_5.mp4"
        output = self.temp_dir / "export.mp4"
        self.run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=24:duration=4",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=3.5",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ]
        )

        segment = TimelineSegment(str(source), 0.0, 4.0)
        write_timeline_video([segment], str(output), save_options={"video_quality": "medium"})

        expected_duration = total_duration([segment])
        manager = MainAudioOverrideManager(
            None,
            duration_reader=get_media_duration,
            audio_stream_checker=has_audio_stream,
            video_stream_checker=has_video_stream,
            timeline_audio_writer=write_timeline_audio,
        )
        manager.validate_exported_video(str(output), expected_duration, require_audio=True)
        self.assertGreaterEqual(get_media_duration(str(output)), expected_duration - 0.10)

    def test_audio_export_keeps_exact_duration_after_speed_change(self):
        source = self.temp_dir / "source.wav"
        output = self.temp_dir / "export.wav"
        self.run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=3.5",
                "-c:a",
                "pcm_s16le",
                str(source),
            ]
        )

        segment = TimelineSegment(str(source), 0.0, 3.5, speed=2.0)
        write_timeline_audio([segment], str(output))

        self.assertAlmostEqual(get_media_duration(str(output)), total_duration([segment]), places=2)

    def test_muted_multi_video_export_progress_reaches_completion(self):
        first = self.temp_dir / "1.mp4"
        second = self.temp_dir / "2.mp4"
        output = self.temp_dir / "muted_multi.mp4"
        for path, color, tone in ((first, "red", "440"), (second, "blue", "660")):
            self.run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:size=160x90:rate=12:duration=0.8",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={tone}:sample_rate=48000:duration=0.8",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-pix_fmt",
                    "yuv420p",
                    "-shortest",
                    str(path),
                ]
            )
        timeline = [
            TimelineSegment(str(first), 0.0, get_media_duration(str(first)), audio_volume=0.0),
            TimelineSegment(str(second), 0.0, get_media_duration(str(second)), audio_volume=0.0),
        ]
        progress_values = []

        write_timeline_video(timeline, str(output), progress_callback=progress_values.append)

        self.assertTrue(output.exists())
        self.assertGreaterEqual(max(progress_values), 99)
        self.assertEqual(int(progress_values[-1]), 100)

    def test_video_export_filter_pads_fractional_tail_to_timeline_duration(self):
        source = self.temp_dir / "source.mp4"
        output = self.temp_dir / "export.mp4"
        source.write_bytes(b"placeholder")
        timeline = [
            TimelineSegment(str(source), 0.0, 5.142),
            TimelineSegment(str(source), 5.142, 7.0),
        ]
        captured = {}

        def capture_command(command, *_args, **_kwargs):
            captured["command"] = command

        with patch("video_maker.video_editing.has_audio_stream", return_value=True), \
             patch("video_maker.video_editing.prepare_timeline_boundary_safe_audio_proxies", return_value=({}, [])), \
             patch("video_maker.watermark.run_ffmpeg_with_progress", side_effect=capture_command):
            write_timeline_video(timeline, str(output), save_options={"video_quality": "medium"})

        command = captured["command"]
        script_path = command[command.index("-filter_complex_script") + 1]
        script_text = Path(script_path).read_text(encoding="utf-8")
        self.assertIn("tpad=stop_mode=clone:stop_duration=1.000000", script_text)
        self.assertIn("trim=duration=7.000000", script_text)

    def test_fractional_boundary_video_export_validates_duration(self):
        source = self.temp_dir / "fractional_source.mp4"
        output = self.temp_dir / "fractional_export.mp4"
        self.run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=24:duration=7",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=550:sample_rate=48000:duration=7",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(source),
            ]
        )
        timeline = [
            TimelineSegment(str(source), 0.0, 5.142),
            TimelineSegment(str(source), 5.142, 7.0),
        ]

        write_timeline_video(timeline, str(output), save_options={"video_quality": "medium"})

        expected_duration = total_duration(timeline)
        manager = MainAudioOverrideManager(
            None,
            duration_reader=get_media_duration,
            audio_stream_checker=has_audio_stream,
            video_stream_checker=has_video_stream,
            timeline_audio_writer=write_timeline_audio,
        )
        manager.validate_exported_video(str(output), expected_duration, require_audio=True)
        self.assertGreaterEqual(get_media_duration(str(output)), expected_duration - 0.10)

    def test_video_quality_preset_does_not_lower_audio_bitrate(self):
        settings = video_output_settings(
            "output.mp4",
            {"format": "mp4", "video_codec": "libx264", "audio_codec": "aac", "video_quality": "compact", "video_bitrate": 1500},
            source_bitrate=600,
            source_audio_bitrate=320,
        )

        self.assertEqual(settings["bitrate"], "1500k")
        self.assertEqual(settings["audio_bitrate"], "320k")
        self.assertEqual(preferred_audio_bitrate_for_codec("aac", 96), 320)

    def test_video_export_copies_simple_main_audio_override(self):
        captured = {}

        def capture_command(command, *_args, **_kwargs):
            captured["command"] = command

        with patch("video_maker.video_editing.parse_bitrates_from_ffmpeg", return_value=(600, 64)), \
             patch("video_maker.video_editing.ffmpeg_parse_infos", return_value={"video_size": [160, 90], "video_fps": 24}), \
             patch("video_maker.watermark.run_ffmpeg_with_progress", side_effect=capture_command), \
             patch("video_maker.video_editing.apply_metadata"):
            write_timeline_video(
                [TimelineSegment("low_video.mp4", 0.0, 2.0)],
                "output.mp4",
                save_options={"format": "mp4", "video_quality": "compact", "video_bitrate": 1500},
                main_audio_override_path="voice.m4a",
            )

        command = captured["command"]
        audio_codec_index = command.index("-c:a") + 1
        self.assertEqual(command[audio_codec_index], "copy")
        self.assertNotIn("-b:a", command)

    def test_video_export_uses_high_bitrate_when_main_audio_override_must_be_mixed(self):
        captured = {}

        def fake_bitrates(path):
            if str(path).endswith("voice.m4a"):
                return None, 320
            return 600, 64

        def capture_command(command, *_args, **_kwargs):
            captured["command"] = command

        with patch("video_maker.video_editing.parse_bitrates_from_ffmpeg", side_effect=fake_bitrates), \
             patch("video_maker.video_editing.ffmpeg_parse_infos", return_value={"video_size": [160, 90], "video_fps": 24}), \
             patch("video_maker.watermark.run_ffmpeg_with_progress", side_effect=capture_command), \
             patch("video_maker.video_editing.apply_metadata"):
            write_timeline_video(
                [TimelineSegment("low_video.mp4", 0.0, 2.0)],
                "output.mp4",
                save_options={"format": "mp4", "video_quality": "compact", "video_bitrate": 1500},
                main_audio_override_path="voice.m4a",
                background_audio_items=[{"path": "bed.wav", "start": 0.0, "end": 2.0, "volume": 0.2}],
            )

        command = captured["command"]
        audio_bitrate_index = command.index("-b:a") + 1
        self.assertEqual(command[audio_bitrate_index], "320k")


if __name__ == "__main__":
    unittest.main()
