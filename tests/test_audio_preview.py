import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.track_items import build_preview_audio_mix
from video_maker.tracks import BACKGROUND_AUDIO_TRACK, SOUND_EFFECTS_TRACK


def make_item(**overrides):
    item = {
        "id": "x",
        "type": "background_audio",
        "path": "",
        "start": 0.0,
        "end": 10.0,
        "volume": 0.5,
        "speed": 1.0,
        "source_offset": 0.0,
    }
    item.update(overrides)
    return item


class BuildPreviewAudioMixTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_preview_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def make_file(self, name="sound.wav"):
        path = os.path.join(self.temp_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def bg_item(self, **overrides):
        item = make_item(type="background_audio", path=self.make_file("bg.wav"), **overrides)
        return item

    def sfx_item(self, **overrides):
        item = make_item(type="sound_effect", path=self.make_file("sfx.wav"), **overrides)
        return item

    def test_returns_active_channels_from_both_tracks(self):
        background = [self.bg_item(id="bg", start=0.0, end=10.0, volume=0.4)]
        effects = [self.sfx_item(id="sfx", start=5.0, end=15.0, volume=0.7)]
        result = build_preview_audio_mix(background, effects, (), 6.0)
        self.assertEqual(result["playhead"], 6.0)
        channels = result["channels"]
        self.assertEqual(len(channels), 2)
        by_track = {channel["track"]: channel for channel in channels}
        self.assertIn(BACKGROUND_AUDIO_TRACK, by_track)
        self.assertIn(SOUND_EFFECTS_TRACK, by_track)
        self.assertEqual(by_track[BACKGROUND_AUDIO_TRACK]["volume"], 0.4)
        self.assertEqual(by_track[SOUND_EFFECTS_TRACK]["volume"], 0.7)

    def test_channels_contain_volume_path_and_time_map(self):
        background = [self.bg_item(id="bg", start=0.0, end=10.0, volume=0.4)]
        result = build_preview_audio_mix(background, [], (), 2.0)
        channel = result["channels"][0]
        self.assertEqual(channel["volume"], 0.4)
        self.assertEqual(channel["speed"], 1.0)
        self.assertEqual(channel["start"], 0.0)
        self.assertEqual(channel["end"], 10.0)
        self.assertEqual(channel["channel_duration"], 8.0)
        self.assertEqual(channel["local_offset"], 2.0)

    def test_local_offset_respects_source_offset_and_speed(self):
        background = [self.bg_item(id="bg", start=2.0, end=10.0, source_offset=1.0, speed=2.0)]
        channel = build_preview_audio_mix(background, [], (), 5.0)["channels"][0]
        self.assertEqual(channel["local_offset"], 1.0 + (5.0 - 2.0) * 2.0)
        self.assertEqual(channel["channel_duration"], 10.0 - 5.0)

    def test_volume_defaults_to_one_when_missing(self):
        background = [self.bg_item(id="bg", start=0.0, end=10.0)]
        del background[0]["volume"]
        channel = build_preview_audio_mix(background, [], (), 1.0)["channels"][0]
        self.assertEqual(channel["volume"], 1.0)

    def test_playhead_at_item_start_included(self):
        background = [self.bg_item(id="bg", start=3.0, end=8.0)]
        channels = build_preview_audio_mix(background, [], (), 3.0)["channels"]
        self.assertEqual(len(channels), 1)

    def test_playhead_at_item_end_excluded(self):
        background = [self.bg_item(id="bg", start=0.0, end=8.0)]
        channels = build_preview_audio_mix(background, [], (), 8.0)["channels"]
        self.assertEqual(channels, [])

    def test_excludes_items_starting_after_or_ending_before_playhead(self):
        background = [
            self.bg_item(id="before", start=0.0, end=3.0),
            self.bg_item(id="after", start=5.0, end=8.0),
            self.bg_item(id="active", start=3.0, end=5.0),
        ]
        channels = build_preview_audio_mix(background, [], (), 4.0)["channels"]
        self.assertEqual([channel["item"]["id"] for channel in channels], ["active"])

    def test_excludes_items_with_missing_files(self):
        background = [make_item(type="background_audio", id="gone", path=os.path.join(self.temp_dir, "nope.wav"), start=0.0, end=10.0)]
        channels = build_preview_audio_mix(background, [], (), 1.0)["channels"]
        self.assertEqual(channels, [])


class BuildPreviewAudioMixMutedTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="audio_preview_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def make_file(self, name):
        path = os.path.join(self.temp_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def mix(self, muted):
        background = [
            {
                "id": "bg",
                "type": "background_audio",
                "path": self.make_file("bg.wav"),
                "start": 0.0,
                "end": 10.0,
                "volume": 0.4,
                "speed": 1.0,
                "source_offset": 0.0,
            }
        ]
        effects = [
            {
                "id": "sfx",
                "type": "sound_effect",
                "path": self.make_file("sfx.wav"),
                "start": 0.0,
                "end": 10.0,
                "volume": 0.7,
                "speed": 1.0,
                "source_offset": 0.0,
            }
        ]
        return build_preview_audio_mix(background, effects, muted, 5.0)

    def test_unmuted_includes_both_tracks(self):
        result = self.mix(())
        self.assertEqual(len(result["channels"]), 2)

    def test_muted_background_excluded(self):
        channels = self.mix((BACKGROUND_AUDIO_TRACK,))["channels"]
        self.assertEqual([channel["track"] for channel in channels], [SOUND_EFFECTS_TRACK])

    def test_muted_sound_effects_excluded(self):
        channels = self.mix((SOUND_EFFECTS_TRACK,))["channels"]
        self.assertEqual([channel["track"] for channel in channels], [BACKGROUND_AUDIO_TRACK])

    def test_both_tracks_muted_return_empty(self):
        result = self.mix((BACKGROUND_AUDIO_TRACK, SOUND_EFFECTS_TRACK))
        self.assertEqual(result["channels"], [])


if __name__ == "__main__":
    unittest.main()
