import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageStat

from video_maker.app_paths import ffmpeg_binary
from video_maker.audio_image_merge import AudioImageMergeOptions, TRANSITIONS, create_audio_image_video
from video_maker.audio_video_merge import AudioVideoMergeOptions, create_audio_video_merge
from video_maker.timeline import TimelineSegment
from video_maker.video_editing import get_media_duration, has_audio_stream, has_video_stream, write_audio_visual_video


def _run_ffmpeg(args):
    result = subprocess.run([ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="ignore"))


def _write_tone(path, duration=1.0, frequency=440):
    _run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}", "-c:a", "pcm_s16le", path])


def _write_test_image(path, color="blue"):
    _run_ffmpeg(["-f", "lavfi", "-i", f"color=c={color}:s=320x240", "-frames:v", "1", path])


def _extract_frame(video_path, image_path, time_value):
    _run_ffmpeg(["-ss", f"{time_value:.3f}", "-i", video_path, "-frames:v", "1", image_path])


def _average_rgb(image_path, box=(480, 220, 800, 500)):
    with Image.open(image_path).convert("RGB") as image:
        return tuple(ImageStat.Stat(image.crop(box)).mean)


def _assert_dominant_color(testcase, image_path, channel):
    average = _average_rgb(image_path)
    target = average[channel]
    others = [value for index, value in enumerate(average) if index != channel]
    testcase.assertGreater(target, 80, average)
    for value in others:
        testcase.assertGreater(target, value + 35, average)


class AudioPreservationTest(unittest.TestCase):
    def test_audio_image_merge_copies_source_audio_stream(self):
        commands = []
        options = AudioImageMergeOptions(
            images=["image.jpg"],
            audio="voice.m4a",
            image_duration=5,
            distribute_evenly=True,
            transition="بدون انتقال",
        )

        def capture(command, *_args, **_kwargs):
            commands.append(command)

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("video_maker.audio_image_merge.process_images", return_value=["prepared.jpg"]), \
             patch("video_maker.audio_image_merge.get_media_duration", return_value=5.0), \
             patch("video_maker.audio_image_merge.run_ffmpeg_with_progress", side_effect=capture):
            result = create_audio_image_video(options, str(Path(temp_dir) / "out.mp4"), temp_dir, lambda _p: None, lambda: False)

        self.assertTrue(result)
        self.assertIn("-y", commands[0])
        self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "copy")
        self.assertNotIn("192k", commands[0])
        self.assertEqual(commands[0][commands[0].index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(commands[0][commands[0].index("-movflags") + 1], "+faststart")

    def test_audio_image_merge_fallback_uses_high_audio_bitrate(self):
        commands = []
        options = AudioImageMergeOptions(
            images=["image.jpg"],
            audio="voice.wav",
            image_duration=5,
            distribute_evenly=True,
            transition="بدون انتقال",
        )

        def fail_then_capture(command, *_args, **_kwargs):
            commands.append(command)
            if len(commands) == 1:
                raise RuntimeError("copy not supported by this container")

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("video_maker.audio_image_merge.process_images", return_value=["prepared.jpg"]), \
             patch("video_maker.audio_image_merge.get_media_duration", return_value=5.0), \
             patch("video_maker.audio_image_merge.run_ffmpeg_with_progress", side_effect=fail_then_capture):
            result = create_audio_image_video(options, str(Path(temp_dir) / "out.mp4"), temp_dir, lambda _p: None, lambda: False)

        self.assertTrue(result)
        self.assertIn("-y", commands[0])
        self.assertIn("-y", commands[1])
        self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "copy")
        self.assertEqual(commands[1][commands[1].index("-b:a") + 1], "320k")
        self.assertNotIn("192k", commands[1])
        self.assertEqual(commands[1][commands[1].index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(commands[1][commands[1].index("-movflags") + 1], "+faststart")

    def test_practical_audio_image_merge_single_rotating_image_overwrites_precreated_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = str(Path(temp_dir) / "cover.png")
            audio_path = str(Path(temp_dir) / "voice.wav")
            output_path = str(Path(temp_dir) / "precreated.mp4")
            _write_test_image(image_path)
            _write_tone(audio_path, 1.2, 550)
            Path(output_path).write_bytes(b"pre-existing placeholder")
            options = AudioImageMergeOptions(
                images=[image_path],
                audio=audio_path,
                image_duration=1,
                distribute_evenly=True,
                transition=TRANSITIONS[3],
            )

            result = create_audio_image_video(options, output_path, temp_dir, lambda _p: None, lambda: False)

            self.assertTrue(result)
            self.assertTrue(has_video_stream(output_path))
            self.assertTrue(has_audio_stream(output_path))
            self.assertAlmostEqual(get_media_duration(output_path), 1.2, delta=0.25)

    def test_practical_audio_image_merge_multiple_images_overwrites_precreated_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first_image = str(Path(temp_dir) / "first.png")
            second_image = str(Path(temp_dir) / "second.png")
            audio_path = str(Path(temp_dir) / "voice.wav")
            output_path = str(Path(temp_dir) / "precreated_multi.mp4")
            _write_test_image(first_image, "red")
            _write_test_image(second_image, "green")
            _write_tone(audio_path, 2.0, 660)
            Path(output_path).write_bytes(b"pre-existing placeholder")
            options = AudioImageMergeOptions(
                images=[first_image, second_image],
                audio=audio_path,
                image_duration=1,
                distribute_evenly=True,
                transition=TRANSITIONS[0],
            )

            result = create_audio_image_video(options, output_path, temp_dir, lambda _p: None, lambda: False)

            self.assertTrue(result)
            self.assertTrue(has_video_stream(output_path))
            self.assertTrue(has_audio_stream(output_path))
            self.assertAlmostEqual(get_media_duration(output_path), 2.0, delta=0.25)

    def test_practical_audio_image_merge_multiple_images_shows_each_image_in_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_paths = [
                str(Path(temp_dir) / "first.png"),
                str(Path(temp_dir) / "second.png"),
                str(Path(temp_dir) / "third.png"),
            ]
            for path, color in zip(image_paths, ("red", "green", "blue")):
                _write_test_image(path, color)
            audio_path = str(Path(temp_dir) / "voice.wav")
            output_path = str(Path(temp_dir) / "merge_visible_images.mp4")
            _write_tone(audio_path, 3.0, 520)
            Path(output_path).write_bytes(b"pre-existing placeholder")
            options = AudioImageMergeOptions(
                images=image_paths,
                audio=audio_path,
                image_duration=1,
                distribute_evenly=True,
                transition=TRANSITIONS[0],
            )

            result = create_audio_image_video(options, output_path, temp_dir, lambda _p: None, lambda: False)

            self.assertTrue(result)
            self.assertTrue(has_video_stream(output_path))
            self.assertTrue(has_audio_stream(output_path))
            self.assertAlmostEqual(get_media_duration(output_path), 3.0, delta=0.25)
            expected_frames = [
                (0.35, 0, "first_frame.png"),
                (1.35, 1, "second_frame.png"),
                (2.35, 2, "third_frame.png"),
            ]
            for time_value, channel, file_name in expected_frames:
                with self.subTest(time=time_value):
                    frame_path = str(Path(temp_dir) / file_name)
                    _extract_frame(output_path, frame_path, time_value)
                    _assert_dominant_color(self, frame_path, channel)

    def test_practical_audio_image_merge_multiple_images_all_transition_effects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_paths = [
                str(Path(temp_dir) / "first.png"),
                str(Path(temp_dir) / "second.png"),
                str(Path(temp_dir) / "third.png"),
            ]
            for path, color in zip(image_paths, ("red", "green", "blue")):
                _write_test_image(path, color)
            audio_path = str(Path(temp_dir) / "voice.wav")
            _write_tone(audio_path, 6.0, 520)

            for transition in TRANSITIONS:
                with self.subTest(transition=transition):
                    output_path = str(Path(temp_dir) / f"merge_{TRANSITIONS.index(transition)}.mp4")
                    Path(output_path).write_bytes(b"pre-existing placeholder")
                    options = AudioImageMergeOptions(
                        images=image_paths,
                        audio=audio_path,
                        image_duration=2,
                        distribute_evenly=True,
                        transition=transition,
                    )

                    result = create_audio_image_video(options, output_path, temp_dir, lambda _p: None, lambda: False)

                    self.assertTrue(result)
                    self.assertTrue(has_video_stream(output_path), transition)
                    self.assertTrue(has_audio_stream(output_path), transition)
                    self.assertAlmostEqual(get_media_duration(output_path), 6.0, delta=0.35, msg=transition)

    def test_audio_visual_save_uses_whatsapp_compatible_video_settings(self):
        captured = {}

        def fake_write_audio(*_args, **kwargs):
            save_path = kwargs["save_path"]
            Path(save_path).write_bytes(b"audio")

        def capture(command, *_args, **_kwargs):
            captured["command"] = command

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("video_maker.video_editing.write_timeline_audio", side_effect=fake_write_audio), \
             patch("video_maker.video_editing.get_audio_duration", return_value=5.0), \
             patch("video_maker.video_editing._append_audio_visual_overlay_filters", return_value=("[bg]", 1, [])), \
             patch("video_maker.video_editing.audio_bitrate_from_paths", return_value=128), \
             patch("video_maker.watermark.run_ffmpeg_with_progress", side_effect=capture), \
             patch("video_maker.video_editing.apply_metadata"):
            write_audio_visual_video(
                [TimelineSegment("voice.wav", 0.0, 5.0)],
                [{"type": "image", "path": "cover.jpg", "start": 0.0, "end": 5.0}],
                str(Path(temp_dir) / "out.mp4"),
            )

        command = captured["command"]
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")

    def test_audio_video_merge_copies_source_audio_stream(self):
        commands = []
        options = AudioVideoMergeOptions(
            audio="voice.m4a",
            videos=["clip.mp4"],
            transition="بدون انتقال",
        )

        def fake_duration(path):
            return 5.0

        def capture(command, *_args, **_kwargs):
            commands.append(command)

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("video_maker.audio_video_merge.get_media_duration", side_effect=fake_duration), \
             patch("video_maker.audio_video_merge.ffmpeg_parse_infos", return_value={"video_size": (1280, 720), "video_fps": 24}), \
             patch("video_maker.audio_video_merge.run_ffmpeg_with_progress", side_effect=capture):
            result = create_audio_video_merge(options, str(Path(temp_dir) / "out.mp4"), temp_dir, lambda _p: None, lambda: False)

        self.assertTrue(result)
        self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "copy")
        self.assertNotIn("192k", commands[0])

    def test_audio_video_merge_fallback_uses_high_audio_bitrate(self):
        commands = []
        options = AudioVideoMergeOptions(
            audio="voice.wav",
            videos=["clip.mp4"],
            transition="بدون انتقال",
        )

        def fail_then_capture(command, *_args, **_kwargs):
            commands.append(command)
            if len(commands) == 1:
                raise RuntimeError("copy not supported by this container")

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("video_maker.audio_video_merge.get_media_duration", return_value=5.0), \
             patch("video_maker.audio_video_merge.ffmpeg_parse_infos", return_value={"video_size": (1280, 720), "video_fps": 24}), \
             patch("video_maker.audio_video_merge.run_ffmpeg_with_progress", side_effect=fail_then_capture):
            result = create_audio_video_merge(options, str(Path(temp_dir) / "out.mp4"), temp_dir, lambda _p: None, lambda: False)

        self.assertTrue(result)
        self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "copy")
        self.assertEqual(commands[1][commands[1].index("-b:a") + 1], "320k")
        self.assertNotIn("192k", commands[1])


if __name__ == "__main__":
    unittest.main()
