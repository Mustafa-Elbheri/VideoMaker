import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from video_maker.app_paths import ffmpeg_binary
from video_maker.audio_effects import (
    build_audio_effect_segment_with_progress,
    direct_realtime_audio_filter_supported,
    mosque_reverb_effect,
    run_isolated_pedalboard_filter,
)
from video_maker.timeline import TimelineSegment, total_duration
from video_maker.video_editing import get_media_duration, write_timeline_audio


class PedalboardWorkerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_maker_pedalboard_worker_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def write_wav(self, path, duration=0.25, sample_rate=44100):
        frames = int(duration * sample_rate)
        with wave.open(str(path), "wb") as audio_file:
            audio_file.setnchannels(2)
            audio_file.setsampwidth(2)
            audio_file.setframerate(sample_rate)
            for index in range(frames):
                sample = int(math.sin(index * 2 * math.pi * 440 / sample_rate) * 12000)
                audio_file.writeframes(struct.pack("<hh", sample, sample))

    def run_ffmpeg(self, args):
        command = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", *args]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise unittest.SkipTest(f"FFmpeg is unavailable for pedalboard worker tests: {error}") from error

    def require_pedalboard(self):
        try:
            import pedalboard  # noqa: F401
            import pedalboard.io  # noqa: F401
        except Exception as error:
            raise unittest.SkipTest(f"pedalboard is unavailable: {error}") from error

    def test_worker_process_crash_is_reported_without_exiting_parent(self):
        source = self.temp_dir / "source.wav"
        output = self.temp_dir / "output.wav"
        self.write_wav(source)

        def crash_command(_payload_path):
            return [sys.executable, "-c", "import os; os._exit(77)"]

        with self.assertRaises(RuntimeError) as error:
            run_isolated_pedalboard_filter(
                str(source),
                str(output),
                mosque_reverb_effect({"wet": 24, "clarity": 42, "warmth": 44}),
                command_builder=crash_command,
            )

        self.assertIn("عامل المعالجة توقف فجأة", str(error.exception))
        self.assertFalse(output.exists() and output.stat().st_size > 0)

    def test_convolution_reverb_after_cut_video_audio_uses_worker_and_writes_valid_audio(self):
        self.require_pedalboard()
        source = self.temp_dir / "source.mp4"
        cut_audio = self.temp_dir / "cut_audio.wav"
        self.run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=24:duration=3.4",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=3.4",
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
                "-shortest",
                str(source),
            ]
        )

        cut_timeline = [
            TimelineSegment(str(source), 0.0, 1.0),
            TimelineSegment(str(source), 1.8, 3.4),
        ]
        write_timeline_audio(cut_timeline, str(cut_audio))
        cut_duration = get_media_duration(str(cut_audio))
        self.assertAlmostEqual(cut_duration, total_duration(cut_timeline), delta=0.08)

        progress = []
        effect_result, effect_temp_dir = build_audio_effect_segment_with_progress(
            [TimelineSegment(str(cut_audio), 0.0, cut_duration)],
            0.6,
            1.4,
            mosque_reverb_effect({"wet": 24, "clarity": 42, "warmth": 44}),
            lambda value, _message="": progress.append(float(value)),
            lambda: False,
        )
        try:
            self.assertTrue(os.path.exists(effect_result))
            self.assertGreater(os.path.getsize(effect_result), 0)
            self.assertGreater(get_media_duration(effect_result), 0.5)
            self.assertTrue(progress)
            self.assertGreaterEqual(max(progress), 99)
        finally:
            shutil.rmtree(effect_temp_dir, ignore_errors=True)

    def test_pedalboard_effects_support_direct_realtime_preview(self):
        self.assertTrue(direct_realtime_audio_filter_supported(mosque_reverb_effect({})))


if __name__ == "__main__":
    unittest.main()
