import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from video_maker.app_paths import ffmpeg_binary
from video_maker.logical_files import new_file_segment
from video_maker.player import VideoPlayer
from video_maker.timeline import TimelineSegment, total_duration
from video_maker.timeline_transforms import mute_timeline_audio_ranges
from video_maker.video_editing import write_timeline_audio


class MuteTimelineAudioTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_maker_mute_timeline_audio_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_ffmpeg(self, args):
        command = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", *args]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise unittest.SkipTest(f"FFmpeg is unavailable for mute audio tests: {error}") from error

    def make_player(self, timeline, media_kind):
        player = VideoPlayer.__new__(VideoPlayer)
        player.timeline = list(timeline)
        player.media_kind = media_kind
        player.current_time = 0.25
        player.start_time = None
        player.end_time = None
        player.edit_points = []
        player.is_dirty = False
        player.require_open_file = lambda: True
        player.capture_edit_state = lambda: {"timeline": list(player.timeline)}
        player.record_edit = lambda *_args, **_kwargs: True
        player.refresh_menu_bar = lambda: None
        player.reload_current_position = lambda: None
        player.say = lambda *_args, **_kwargs: None
        player.timeline_duration = lambda: total_duration(player.timeline)
        player.timeline_boundaries_cache_signature = None
        player.timeline_positions_cache = []
        player.timeline_boundaries_cache = []
        player.timeline_duration_cache = 0.0
        return player

    def test_mute_ranges_sets_only_selected_audio_volume_to_zero(self):
        timeline = [
            TimelineSegment("a.mp4", 0.0, 1.0, audio_volume=1.0),
            TimelineSegment("b.mp4", 0.0, 1.0, audio_volume=0.75),
            TimelineSegment("c.mp4", 0.0, 1.0, audio_volume=1.0),
        ]

        updated, changed = mute_timeline_audio_ranges(timeline, [(1.0, 2.0)])

        self.assertTrue(changed)
        self.assertEqual([segment.audio_volume for segment in updated], [1.0, 0.0, 1.0])

    def test_mute_timeline_audio_mutes_entire_video_timeline(self):
        timeline = [
            new_file_segment("first.mp4", 0.0, 1.0),
            new_file_segment("second.mp4", 0.0, 1.0),
        ]
        first_file_id = timeline[0].source_file_id
        timeline.append(new_file_segment("first.mp4", 2.0, 3.0, source_file_id=first_file_id, source_file_name="first.mp4"))
        player = self.make_player(timeline, "video")

        player.OnMuteTimelineVideos()

        self.assertEqual([segment.audio_volume for segment in player.timeline], [0.0, 0.0, 0.0])
        self.assertEqual(len(player.edit_points), 1)
        self.assertEqual(player.current_time, 0.0)
        self.assertTrue(player.is_dirty)

    def test_mute_timeline_audio_mutes_entire_audio_timeline(self):
        player = self.make_player(
            [
                new_file_segment("first.wav", 0.0, 1.0),
                new_file_segment("second.wav", 0.0, 1.0),
            ],
            "audio",
        )

        player.OnMuteTimelineVideos()

        self.assertEqual([segment.audio_volume for segment in player.timeline], [0.0, 0.0])
        self.assertEqual(len(player.edit_points), 1)
        self.assertEqual(player.edit_points[0]["kind"], "mute_timeline_audio")
        self.assertEqual(player.current_time, 0.0)
        self.assertTrue(player.is_dirty)

    def test_mute_selected_part_mutes_only_selected_audio_range(self):
        player = self.make_player([TimelineSegment("voice.wav", 0.0, 2.0)], "audio")
        player.start_time = 0.5
        player.end_time = 1.5

        player.OnMuteOriginalAudio()

        self.assertEqual(
            [(segment.start, segment.end, segment.audio_volume) for segment in player.timeline],
            [(0.0, 0.5, 1.0), (0.5, 1.5, 0.0), (1.5, 2.0, 1.0)],
        )
        self.assertEqual(len(player.edit_points), 1)
        self.assertEqual(player.edit_points[0]["kind"], "mute_original_audio")
        self.assertEqual(player.current_time, 0.5)
        self.assertTrue(player.is_dirty)

    def test_muted_video_timeline_writes_silent_audio(self):
        source = self.temp_dir / "source.mp4"
        output = self.temp_dir / "muted.wav"
        self.run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=12:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=700:sample_rate=48000:duration=1",
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

        write_timeline_audio([TimelineSegment(str(source), 0.0, 1.0, audio_volume=0.0)], str(output))

        self.assertTrue(output.exists())
        with wave.open(str(output), "rb") as audio_file:
            frames = audio_file.readframes(audio_file.getnframes())
            self.assertEqual(audio_file.getsampwidth(), 2)
        self.assertFalse(any(frames))

    def test_muted_multi_video_timeline_writes_silent_audio(self):
        first = self.temp_dir / "1.mp4"
        second = self.temp_dir / "2.mp4"
        output = self.temp_dir / "muted_multi.wav"
        for path, color, tone in ((first, "red", "500"), (second, "blue", "800")):
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
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(path),
                ]
            )
        timeline = [
            TimelineSegment(str(first), 0.0, 0.8, audio_volume=0.0),
            TimelineSegment(str(second), 0.0, 0.8, audio_volume=0.0),
        ]

        write_timeline_audio(timeline, str(output))

        self.assertTrue(output.exists())
        with wave.open(str(output), "rb") as audio_file:
            frames = audio_file.readframes(audio_file.getnframes())
            self.assertEqual(audio_file.getsampwidth(), 2)
        self.assertFalse(any(frames))


if __name__ == "__main__":
    unittest.main()
