import unittest

from video_maker.continuous_playback import can_live_skip_deleted_gap, should_live_skip_deleted_gap
from video_maker.player import VideoPlayer
from video_maker.timeline import TimelineSegment
from video_maker.mpv_player import MEDIASTATE_PLAYING


class FakeMediaCtrl:
    def __init__(self):
        self.seek_calls = []
        self.load_calls = []
        self.volume_calls = []
        self.rate_calls = []
        self.play_calls = 0

    def Seek(self, value, mode="exact"):
        self.seek_calls.append((value, mode))
        return value

    def Load(self, path):
        self.load_calls.append(path)
        return True

    def SetVolume(self, value):
        self.volume_calls.append(value)
        return True

    def SetPlaybackRate(self, value):
        self.rate_calls.append(value)
        return True

    def GetState(self):
        return MEDIASTATE_PLAYING

    def Play(self):
        self.play_calls += 1
        return True


class ContinuousCutPlaybackTest(unittest.TestCase):
    def test_live_gap_skip_only_allows_same_source_and_same_playback_shape(self):
        current = TimelineSegment("source.mp4", 0.0, 2.0)
        next_segment = TimelineSegment("source.mp4", 5.0, 8.0)
        self.assertTrue(can_live_skip_deleted_gap(current, next_segment))
        self.assertFalse(should_live_skip_deleted_gap(1.90, current, next_segment))
        self.assertTrue(should_live_skip_deleted_gap(1.97, current, next_segment))

        self.assertFalse(can_live_skip_deleted_gap(current, TimelineSegment("other.mp4", 5.0, 8.0)))
        self.assertFalse(can_live_skip_deleted_gap(current, TimelineSegment("source.mp4", 2.0, 5.0)))
        self.assertFalse(can_live_skip_deleted_gap(current, TimelineSegment("source.mp4", 5.0, 8.0, speed=1.5)))
        self.assertFalse(can_live_skip_deleted_gap(current, TimelineSegment("source.mp4", 5.0, 8.0, audio_volume=0.5)))
        self.assertFalse(can_live_skip_deleted_gap(current, TimelineSegment("source.mp4", 5.0, 8.0, audio_path="other.wav")))
        self.assertFalse(can_live_skip_deleted_gap(TimelineSegment("source.mp4", 0.0, 2.0, audio_fade_out=0.008), next_segment))
        self.assertFalse(can_live_skip_deleted_gap(current, TimelineSegment("source.mp4", 5.0, 8.0, audio_fade_in=0.008)))

    def test_player_live_gap_skip_seeks_same_loaded_source_without_loading(self):
        player = VideoPlayer.__new__(VideoPlayer)
        player.timeline = [
            TimelineSegment("source.mp4", 0.0, 2.0),
            TimelineSegment("source.mp4", 5.0, 8.0),
        ]
        player.current_segment_index = 0
        player.current_time = 1.97
        player.pending_seek_ms = None
        player.selected_playback_range = None
        player.skipped_playback_range = None
        player.speed_preview_state = None
        player.playback_requested = True
        player.volume = 1.0
        player.use_reliable_audio = False
        player.original_audio_player = None
        player.background_audio_players = {}
        player.media_ctrl = FakeMediaCtrl()
        player.media_ctrl_volume_cache = None
        player.media_ctrl_rate_cache = None
        player.timeline_boundaries_cache_signature = None
        player.timeline_positions_cache = []
        player.timeline_boundaries_cache = []
        player.timeline_duration_cache = 0.0
        player.active_media_is_audio_visual_preview = lambda: False
        player.has_main_audio_override = lambda: False
        player.sync_original_audio_playback = lambda *_args, **_kwargs: None
        player.sync_background_audio_playback = lambda *_args, **_kwargs: None

        self.assertTrue(player.live_skip_deleted_gap(1.97))
        self.assertEqual(player.current_segment_index, 1)
        self.assertAlmostEqual(player.current_time, 2.0)
        self.assertEqual(player.media_ctrl.seek_calls, [(5000, "exact")])
        self.assertEqual(player.media_ctrl.load_calls, [])


if __name__ == "__main__":
    unittest.main()
