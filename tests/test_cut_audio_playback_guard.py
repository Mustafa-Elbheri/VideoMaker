import unittest
import threading
from unittest.mock import patch

from video_maker.logical_files import new_file_segment
from video_maker.mpv_player import MEDIASTATE_PAUSED, MEDIASTATE_PLAYING, MEDIASTATE_STOPPED
from video_maker.player import VideoPlayer
from video_maker.reliable_playback import ReliableAudioPlayer
from video_maker.timeline import TimelineSegment, apply_audio_cut_fade_at_boundary, total_duration
from video_maker.video_editing import exact_timeline_audio_chain, timeline_needs_render


class FakeReliableAudioPlayer:
    def __init__(self):
        self.state = MEDIASTATE_PLAYING
        self.stop_waits = []

    def GetState(self):
        return self.state

    def Stop(self, wait=False):
        self.stop_waits.append(wait)
        self.state = MEDIASTATE_STOPPED
        return True


class FakeMediaCtrl:
    def __init__(self):
        self.state = MEDIASTATE_PLAYING
        self.pause_count = 0

    def GetState(self):
        return self.state

    def Pause(self):
        self.pause_count += 1
        self.state = MEDIASTATE_PAUSED
        return True

    def Stop(self):
        self.state = MEDIASTATE_STOPPED
        return True


def make_audio_cut_player():
    player = VideoPlayer.__new__(VideoPlayer)
    player.window_number = 3
    player.media_kind = "audio"
    player.timeline = [new_file_segment("voice.mp3", 0.0, 4.0)]
    player.current_time = 0.75
    player.start_time = 1.0
    player.end_time = 2.0
    player.playback_requested = True
    player.pending_play = True
    player.selected_playback_range = (1.0, 2.0)
    player.skipped_playback_range = None
    player.original_audio_player = FakeReliableAudioPlayer()
    player.media_ctrl = FakeMediaCtrl()
    player.audio_effect_background_preview_timer = None
    player.audio_effect_background_preview_state = None
    player.background_audio_players = {}
    player.has_video = lambda: True
    player.require_open_file = lambda: True
    player.capture_edit_state = lambda: {"timeline": list(player.timeline)}
    player.adjust_timed_items_after_delete = lambda *_args, **_kwargs: None
    player.add_edit_point = lambda *_args, **_kwargs: None
    player.record_edit = lambda *_args, **_kwargs: None
    player.say = lambda *_args, **_kwargs: None
    player.set_shared_timeline_clipboard = lambda *_args, **_kwargs: None
    player.timeline_duration = lambda: sum(max(0.0, item.end - item.start) for item in player.timeline)
    player.reload_calls = []
    player.reload_current_position = lambda: player.reload_calls.append(player.playback_requested)
    return player


def make_video_cut_player():
    player = make_audio_cut_player()
    player.media_kind = "video"
    player.timeline = [TimelineSegment("clip.mp4", 0.0, 4.0)]
    return player


class CutAudioPlaybackGuardTest(unittest.TestCase):
    def test_reliable_audio_stop_does_not_abort_stream_from_caller_thread(self):
        player = ReliableAudioPlayer()

        class Stream:
            def __init__(self):
                self.abort_count = 0

            def abort(self, *args, **kwargs):
                self.abort_count += 1

        class Process:
            def __init__(self):
                self.terminate_count = 0

            def poll(self):
                return None

            def terminate(self):
                self.terminate_count += 1

        stop_event = threading.Event()
        stream = Stream()
        process = Process()
        player.stream = stream
        player.process = process
        player.stop_event = stop_event
        player.state = MEDIASTATE_PLAYING

        player.Stop(wait=True)

        self.assertEqual(stream.abort_count, 0)
        self.assertEqual(process.terminate_count, 1)
        self.assertTrue(stop_event.is_set())
        self.assertEqual(player.GetState(), MEDIASTATE_STOPPED)

    def test_cut_segment_stops_reliable_audio_synchronously_before_reload(self):
        player = make_audio_cut_player()

        with patch("video_maker.player.clean_delete_range", return_value=(1.0, 2.0)):
            player.OnCutSegment()

        self.assertEqual(player.original_audio_player.stop_waits, [True])
        self.assertEqual(player.media_ctrl.pause_count, 1)
        self.assertEqual(player.reload_calls, [True])
        self.assertTrue(player.playback_requested)
        self.assertFalse(player.pending_play)
        self.assertEqual(player.start_time, None)
        self.assertEqual(player.end_time, None)

    def test_delete_segment_stops_reliable_audio_synchronously_before_reload(self):
        player = make_audio_cut_player()

        with patch("video_maker.player.clean_delete_range", return_value=(1.0, 2.0)):
            player.OnDeleteSegment()

        self.assertEqual(player.original_audio_player.stop_waits, [True])
        self.assertEqual(player.media_ctrl.pause_count, 1)
        self.assertEqual(player.reload_calls, [True])
        self.assertTrue(player.playback_requested)
        self.assertFalse(player.pending_play)
        self.assertEqual(player.start_time, None)
        self.assertEqual(player.end_time, None)

    def test_cut_in_third_window_does_not_stop_other_windows(self):
        windows = [make_audio_cut_player() for _index in range(3)]
        for index, player in enumerate(windows, 1):
            player.window_number = index

        with patch("video_maker.player.clean_delete_range", return_value=(1.0, 2.0)):
            windows[2].OnCutSegment()

        self.assertEqual(windows[0].original_audio_player.stop_waits, [])
        self.assertEqual(windows[1].original_audio_player.stop_waits, [])
        self.assertEqual(windows[2].original_audio_player.stop_waits, [True])

    def test_video_cut_uses_exact_markers_and_adds_audio_boundary_fade(self):
        player = make_video_cut_player()

        with patch("video_maker.player.clean_delete_range", side_effect=AssertionError("video cut must not move markers")):
            player.OnCutSegment()

        self.assertEqual([(segment.start, segment.end) for segment in player.timeline], [(0.0, 1.0), (2.0, 4.0)])
        self.assertAlmostEqual(total_duration(player.timeline), 3.0)
        self.assertGreater(player.timeline[0].audio_fade_out, 0.0)
        self.assertGreater(player.timeline[1].audio_fade_in, 0.0)

    def test_video_delete_uses_exact_markers_and_adds_audio_boundary_fade(self):
        player = make_video_cut_player()

        with patch("video_maker.player.clean_delete_range", side_effect=AssertionError("video delete must not move markers")):
            player.OnDeleteSegment()

        self.assertEqual([(segment.start, segment.end) for segment in player.timeline], [(0.0, 1.0), (2.0, 4.0)])
        self.assertAlmostEqual(total_duration(player.timeline), 3.0)
        self.assertGreater(player.timeline[0].audio_fade_out, 0.0)
        self.assertGreater(player.timeline[1].audio_fade_in, 0.0)

    def test_audio_boundary_fade_keeps_timeline_duration(self):
        timeline = [TimelineSegment("clip.mp4", 0.0, 1.0), TimelineSegment("clip.mp4", 2.0, 4.0)]

        updated = apply_audio_cut_fade_at_boundary(timeline, 1.0)

        self.assertAlmostEqual(total_duration(updated), total_duration(timeline))
        self.assertAlmostEqual(updated[0].audio_fade_out, 0.008)
        self.assertAlmostEqual(updated[1].audio_fade_in, 0.008)

    def test_audio_boundary_fade_is_exported_without_duration_change(self):
        segment = TimelineSegment("clip.mp4", 0.0, 1.0, audio_fade_in=0.008, audio_fade_out=0.008)

        chain = exact_timeline_audio_chain("", 1.0, 1.0, fade_in=segment.audio_fade_in, fade_out=segment.audio_fade_out)

        self.assertIn("afade=t=in:st=0:d=0.008000", chain)
        self.assertIn("afade=t=out:st=0.992000:d=0.008000", chain)
        self.assertIn("atrim=duration=1.000000", chain)
        self.assertTrue(timeline_needs_render(segment))


if __name__ == "__main__":
    unittest.main()
