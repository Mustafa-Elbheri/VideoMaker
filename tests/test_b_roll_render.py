import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from video_maker.app_paths import ffmpeg_binary
from video_maker.timeline import TimelineSegment
from video_maker.video_editing import write_timeline_video


def _dft_magnitude(samples, sample_rate, frequency):
    index = int(round(frequency * len(samples) / sample_rate))
    real = 0.0
    imag = 0.0
    step = 2 * 3.141592653589793 * frequency / sample_rate
    for i, sample in enumerate(samples):
        angle = step * i
        real += sample * _cos(angle)
        imag -= sample * _sin(angle)
    return (real * real + imag * imag) ** 0.5


def _cos(angle):
    n = angle / 6.283185307179586
    n -= round(n)
    x = n * 6.283185307179586
    return 1 - x * x / 2 + x * x * x * x / 24 - x * x * x * x * x * x / 720 + x * x * x * x * x * x * x * x / 40320


def _sin(angle):
    n = angle / 6.283185307179586
    n -= round(n)
    x = n * 6.283185307179586
    return x - x * x * x / 6 + x * x * x * x * x / 120 - x * x * x * x * x * x * x / 5040


class BRollRenderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_maker_b_roll_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_ffmpeg(self, args):
        command = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", *args]
        try:
            subprocess.run(command, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise unittest.SkipTest(f"FFmpeg is unavailable for b-roll render tests: {error}") from error

    def make_media(self):
        main = self.temp_dir / "main.mp4"
        broll = self.temp_dir / "broll.mp4"
        self.run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=24:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(main),
            ]
        )
        self.run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=24:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(broll),
            ]
        )
        return main, broll

    def read_frame(self, path, seconds):
        command = [
            ffmpeg_binary(), "-y", "-loglevel", "error",
            "-ss", str(seconds), "-i", path, "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]
        try:
            data = subprocess.run(command, stdout=subprocess.PIPE, check=True).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise unittest.SkipTest(f"FFmpeg is unavailable for b-roll render tests: {error}") from error
        self.assertGreaterEqual(len(data), 3, "no video frame decoded")
        return data[0], data[1], data[2]

    def read_audio(self, path, seconds):
        command = [
            ffmpeg_binary(), "-y", "-loglevel", "error",
            "-ss", str(seconds), "-t", "0.4", "-i", path,
            "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", "-f", "s16le", "-",
        ]
        try:
            data = subprocess.run(command, stdout=subprocess.PIPE, check=True).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise unittest.SkipTest(f"FFmpeg is unavailable for b-roll render tests: {error}") from error
        samples = []
        for i in range(0, len(data) - 1, 2):
            samples.append(int.from_bytes(data[i:i + 2], "little", signed=True))
        return samples

    def test_b_roll_overlay_appears_only_inside_its_time_window(self):
        main, broll = self.make_media()
        output = self.temp_dir / "overlay.mp4"
        item = {"path": str(broll), "start": 1.0, "end": 3.0, "volume": 1.0}

        write_timeline_video(
            [TimelineSegment(str(main), 0.0, 4.0)],
            str(output),
            save_options={"video_quality": "medium"},
            b_roll_items=[item],
        )

        red, _, _ = self.read_frame(output, 0.5)
        self.assertGreater(red, 200, "main video should still show before the b-roll window")
        blue_at_15 = self.read_frame(output, 1.5)[2]
        self.assertGreater(blue_at_15, 200, "b-roll should cover the frame inside its window")
        blue_at_25 = self.read_frame(output, 2.5)[2]
        self.assertGreater(blue_at_25, 200, "b-roll should still cover the frame inside its window")
        red_after, _, _ = self.read_frame(output, 3.5)
        self.assertGreater(red_after, 200, "main video should return after the b-roll window")

    def test_b_roll_audio_is_mixed_alongside_main_audio(self):
        main, broll = self.make_media()
        output = self.temp_dir / "mixed.mp4"
        item = {"path": str(broll), "start": 1.0, "end": 3.0, "volume": 1.0}

        write_timeline_video(
            [TimelineSegment(str(main), 0.0, 4.0)],
            str(output),
            save_options={"video_quality": "medium"},
            b_roll_items=[item],
        )

        main_tone = _dft_magnitude(self.read_audio(output, 0.5), 8000, 440)
        self.assertGreater(main_tone, 1000, "main track audio should be present")
        broll_tone_outside = _dft_magnitude(self.read_audio(output, 0.5), 8000, 880)
        self.assertLess(broll_tone_outside, 1000, "b-roll audio must not play before its window")
        broll_tone_inside = _dft_magnitude(self.read_audio(output, 2.0), 8000, 880)
        self.assertGreater(broll_tone_inside, 1000, "b-roll audio should be mixed in during its window")
        main_tone_inside = _dft_magnitude(self.read_audio(output, 2.0), 8000, 440)
        self.assertGreater(main_tone_inside, 1000, "main audio must still be present while b-roll plays")
        broll_tone_after = _dft_magnitude(self.read_audio(output, 3.5), 8000, 880)
        self.assertLess(broll_tone_after, 1000, "b-roll audio must stop after its window")

    def test_no_b_roll_renders_without_overlay(self):
        main, _broll = self.make_media()
        output = self.temp_dir / "plain.mp4"

        write_timeline_video(
            [TimelineSegment(str(main), 0.0, 4.0)],
            str(output),
            save_options={"video_quality": "medium"},
        )

        red, _, _ = self.read_frame(output, 2.0)
        self.assertGreater(red, 200, "without b-roll the main video should cover the whole frame")


if __name__ == "__main__":
    unittest.main()
