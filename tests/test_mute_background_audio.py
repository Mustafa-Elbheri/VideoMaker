import unittest

from video_maker.player import VideoPlayer
from video_maker.track_items import mute_timed_audio_items_range


class MuteBackgroundAudioTest(unittest.TestCase):
    def test_mute_range_splits_background_audio_and_mutes_only_selection(self):
        items = [
            {
                "id": "bg",
                "type": "background_audio",
                "path": "music.wav",
                "start": 0.0,
                "end": 10.0,
                "volume": 0.8,
                "speed": 1.0,
                "source_offset": 2.0,
            }
        ]

        updated, changed, touched = mute_timed_audio_items_range(items, 2.0, 5.0)

        self.assertTrue(changed)
        self.assertEqual(touched, 1)
        self.assertEqual(
            [(item["start"], item["end"], item["volume"], item["source_offset"]) for item in updated],
            [(0.0, 2.0, 0.8, 2.0), (2.0, 5.0, 0.0, 4.0), (5.0, 10.0, 0.8, 7.0)],
        )
        self.assertEqual({item["source_id"] for item in updated}, {"bg"})

    def test_player_command_updates_background_audio_immediately(self):
        player = VideoPlayer.__new__(VideoPlayer)
        player.background_audio_items = [
            {
                "id": "bg",
                "type": "background_audio",
                "path": "music.wav",
                "start": 0.0,
                "end": 10.0,
                "volume": 0.8,
                "speed": 1.0,
                "source_offset": 0.0,
            }
        ]
        player.current_time = 6.0
        player.is_dirty = False
        player.require_open_file = lambda: True
        player.selected_transform_range = lambda: (2.0, 5.0)
        player.capture_edit_state = lambda: {"background_audio_items": [dict(item) for item in player.background_audio_items]}
        recorded = []
        applied = []
        spoken = []
        player.record_edit = lambda label, before: recorded.append((label, before))
        player.apply_edit_state = lambda state, **kwargs: applied.append((state, kwargs))
        player.say = lambda message, **kwargs: spoken.append(message)

        player.OnMuteBackgroundAudioSelection()

        self.assertEqual([item["volume"] for item in player.background_audio_items], [0.8, 0.0, 0.8])
        self.assertEqual([(item["start"], item["end"]) for item in player.background_audio_items], [(0.0, 2.0), (2.0, 5.0), (5.0, 10.0)])
        self.assertEqual(player.current_time, 2.0)
        self.assertTrue(player.is_dirty)
        self.assertEqual(recorded[0][0], "كتم صوت الخلفية في الجزء المحدد")
        self.assertEqual(len(applied), 1)
        self.assertEqual(spoken, ["تم كتم صوت الخلفية في الجزء المحدد"])

    def test_player_command_does_not_split_when_selection_is_already_muted(self):
        player = VideoPlayer.__new__(VideoPlayer)
        player.background_audio_items = [
            {
                "id": "bg",
                "type": "background_audio",
                "path": "music.wav",
                "start": 0.0,
                "end": 10.0,
                "volume": 0.0,
                "speed": 1.0,
                "source_offset": 0.0,
            }
        ]
        player.current_time = 6.0
        player.is_dirty = False
        player.require_open_file = lambda: True
        player.selected_transform_range = lambda: (2.0, 5.0)
        player.capture_edit_state = lambda: {}
        recorded = []
        applied = []
        spoken = []
        player.record_edit = lambda *args, **kwargs: recorded.append(args)
        player.apply_edit_state = lambda *args, **kwargs: applied.append(args)
        player.say = lambda message, **kwargs: spoken.append(message)

        player.OnMuteBackgroundAudioSelection()

        self.assertEqual(len(player.background_audio_items), 1)
        self.assertEqual(player.background_audio_items[0]["volume"], 0.0)
        self.assertFalse(player.is_dirty)
        self.assertEqual(recorded, [])
        self.assertEqual(applied, [])
        self.assertEqual(spoken, ["صوت الخلفية مكتوم بالفعل في الجزء المحدد"])


if __name__ == "__main__":
    unittest.main()
