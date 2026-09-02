import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath("."))

from video_maker.app_paths import ffmpeg_binary
from video_maker.audio_override_manager import MainAudioOverrideManager
from video_maker.timeline import TimelineSegment
from video_maker.video_editing import (
    get_media_duration,
    has_audio_stream,
    has_video_stream,
    write_timeline_audio,
    write_timeline_video,
)


def _create_sine_tone(path, duration=30.0, frequency=440):
    import subprocess
    cmd = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={duration}",
        "-c:a",
        "pcm_s16le",
        path,
    ]
    subprocess.run(cmd, check=True)


def _create_test_video(path, duration=20.0):
    import subprocess
    cmd = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=320x240:r=25:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        path,
    ]
    subprocess.run(cmd, check=True)


class DummyPlayer:
    def __init__(self, timeline, audio_path):
        self.timeline = timeline
        self.main_audio_override_path = audio_path
        self.main_audio_override_duration = 0.0
        self.main_audio_override_timeline_duration = 0.0
        self.timeline_revision = 0
        self.main_audio_source_revision = 0


class LongTimelineCommandLengthTest(unittest.TestCase):
    """Test that timelines with hundreds of segments (e.g. from silence removal)
    do not exceed the Windows CreateProcess 32KB command line limit (WinError 206)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_long_tl_")
        self.source_audio = os.path.join(self.temp_dir, "source.wav")
        _create_sine_tone(self.source_audio, duration=20.0)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_write_timeline_audio_with_400_segments(self):
        """Verify write_timeline_audio successfully handles 400 segments without WinError 206."""
        # 400 segments of 0.025s each = 10.0s total duration
        segments = []
        for i in range(400):
            start = (i * 0.04) % 19.0
            end = start + 0.025
            segments.append(TimelineSegment(self.source_audio, start, end))

        output_wav = os.path.join(self.temp_dir, "output_400.wav")
        # This previously failed with FileNotFoundError: [WinError 206]
        write_timeline_audio(segments, output_wav)

        self.assertTrue(os.path.exists(output_wav))
        duration = get_media_duration(output_wav)
        expected_duration = 400 * 0.025
        self.assertAlmostEqual(duration, expected_duration, delta=0.15)

    def test_reconcile_after_silence_removal_with_300_segments(self):
        """Simulate reconcile_after_timeline_edit following silence removal with 300 segments."""
        old_timeline = [TimelineSegment(self.source_audio, 0.0, 20.0)]
        new_timeline = []
        for i in range(300):
            start = (i * 0.05) % 19.0
            end = start + 0.03
            new_timeline.append(TimelineSegment(self.source_audio, start, end))

        player = DummyPlayer(new_timeline, self.source_audio)
        manager = MainAudioOverrideManager(
            player,
            duration_reader=get_media_duration,
            audio_stream_checker=has_audio_stream,
            video_stream_checker=has_video_stream,
            timeline_audio_writer=write_timeline_audio,
        )

        before_state = {
            "main_audio_override_path": self.source_audio,
            "timeline": old_timeline,
        }

        # Must not raise WinError 206
        result = manager.reconcile_after_timeline_edit(before_state, "إزالة الصمت")
        self.assertTrue(bool(result.path))
        self.assertTrue(os.path.exists(result.path))
        self.assertGreater(result.duration, 0.0)

    def test_write_timeline_video_with_100_segments(self):
        """Verify write_timeline_video handles a large number of cuts without command line overflow."""
        video_path = os.path.join(self.temp_dir, "source.mp4")
        _create_test_video(video_path, duration=10.0)

        segments = []
        for i in range(100):
            start = (i * 0.08) % 9.0
            end = start + 0.05
            segments.append(TimelineSegment(video_path, start, end))

        output_mp4 = os.path.join(self.temp_dir, "output_100.mp4")
        write_timeline_video(segments, output_mp4, save_options={"video_quality": "low", "video_preset": "ultrafast"})
        self.assertTrue(os.path.exists(output_mp4))
        duration = get_media_duration(output_mp4)
        expected_duration = 100 * 0.05
        self.assertAlmostEqual(duration, expected_duration, delta=0.25)

    def test_exact_timeline_audio_chain_backward_compatibility(self):
        """Verify exact_timeline_audio_chain retains full backward compatibility."""
        from video_maker.video_editing import exact_timeline_audio_chain
        
        # Call without start_time (legacy)
        legacy_chain = exact_timeline_audio_chain("[0:a]", 2.5, 2.5)
        self.assertIn("atrim=duration=2.500000", legacy_chain)
        self.assertIn("asetpts=PTS-STARTPTS", legacy_chain)

        # Call with start_time (deduplicated)
        new_chain = exact_timeline_audio_chain("[0:a]", 2.5, 2.5, start_time=1.2)
        self.assertIn("atrim=start=1.200000:duration=2.500000", new_chain)
        self.assertIn("asetpts=PTS-STARTPTS", new_chain)


if __name__ == "__main__":
    unittest.main()
