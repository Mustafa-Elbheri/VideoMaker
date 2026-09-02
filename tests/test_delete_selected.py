import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath("."))

from video_maker.timeline import TimelineSegment
from video_maker.tracks import SECONDARY_VIDEO_TRACK, MAIN_VIDEO_TRACK
from video_maker.program_modes import PROFESSIONAL_MODE


def make_item(item_id, start, end):
    return {
        "id": item_id,
        "type": "video",
        "path": "{id}.mp4".format(id=item_id),
        "start": float(start),
        "end": float(end),
        "speed": 1.0,
        "source_offset": 0.0,
    }


class DeleteSelectedTest(unittest.TestCase):
    def _make_frame(self, items, ripple_mode="off"):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = SECONDARY_VIDEO_TRACK
        frame.timeline = []
        frame.b_roll_items = items
        frame.sound_effects_items = []
        frame.background_audio_items = []
        frame.visual_items = []
        frame.ripple_mode = ripple_mode
        frame.selected_element_ids = {item["id"] for item in items}
        frame.focused_element = dict(items[0]) if items else None
        frame.current_time = 0.0
        frame.is_dirty = False
        frame.last_insert_end = 0.0
        frame.start_time = None
        frame.end_time = None
        frame.say = lambda *args, **kwargs: None
        frame.require_open_file = lambda: True
        frame.stop_playback_for_timeline_edit = lambda *args, **kwargs: False
        frame.record_edit = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.capture_edit_state = mock.Mock(return_value={})
        frame.timeline_boundaries_cache_signature = None
        frame.timeline_positions_cache = [0.0]
        frame.timeline_boundaries_cache = []
        frame.timeline_duration_cache = 0.0
        frame.edit_points = []
        frame.playback_requested = False
        frame.has_video = lambda: True
        frame.current_background_audio_match = lambda: (None, None)
        frame.stop_background_audio_player = lambda *args, **kwargs: None
        frame.refresh_menu_bar = lambda: None
        frame.reload_current_position = lambda: None
        frame.OnCopyElements = lambda event=None: None
        frame.element_clipboard = [{}]
        frame.load_timeline_time = lambda time_value, *args, **kwargs: setattr(
            frame, "current_time", min(max(float(time_value), 0), frame.timeline_duration())
        )
        frame.sync_background_audio_at = lambda *args, **kwargs: None
        return frame

    def test_deletes_all_selected_items_at_once(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_frame(items)
        frame.OnDeleteSelectedElement()
        self.assertEqual(len(frame.b_roll_items), 0)
        self.assertEqual(frame.selected_element_ids, set())

    def test_deletes_focused_when_no_multi_selection(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9)]
        frame = self._make_frame(items)
        frame.selected_element_ids = set()
        frame.OnDeleteSelectedElement()
        self.assertEqual(len(frame.b_roll_items), 1)
        self.assertEqual(frame.b_roll_items[0]["id"], "b")

    def test_announces_missing_selection(self):
        frame = self._make_frame([])
        frame.say = mock.Mock()
        frame.OnDeleteSelectedElement()
        frame.say.assert_called_once()

    def test_refocuses_element_at_delete_position(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_frame(items)
        frame.timeline = [TimelineSegment("main.mp4", 0.0, 14.0)]
        frame.focused_element = dict(items[1])
        frame.selected_element_ids = set()
        frame.current_time = 9.0
        frame.OnDeleteSelectedElement()
        self.assertEqual([item["id"] for item in frame.b_roll_items], ["a", "c"])
        self.assertEqual(frame.focused_element["id"], "c")

    def test_refocuses_next_element_when_playhead_in_gap(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_frame(items)
        frame.timeline = [TimelineSegment("main.mp4", 0.0, 14.0)]
        frame.focused_element = dict(items[1])
        frame.selected_element_ids = set()
        frame.current_time = 7.0
        frame.OnDeleteSelectedElement()
        self.assertEqual(frame.focused_element["id"], "c")

    def test_refocuses_previous_element_when_playhead_past_all(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_frame(items)
        frame.timeline = [TimelineSegment("main.mp4", 0.0, 14.0)]
        frame.focused_element = dict(items[2])
        frame.selected_element_ids = set()
        frame.current_time = 20.0
        frame.OnDeleteSelectedElement()
        self.assertEqual(frame.focused_element["id"], "b")

    def test_clears_focus_when_track_becomes_empty(self):
        items = [make_item("a", 0, 5)]
        frame = self._make_frame(items)
        frame.timeline = [TimelineSegment("main.mp4", 0.0, 5.0)]
        frame.focused_element = dict(items[0])
        frame.selected_element_ids = set()
        frame.current_time = 2.0
        frame.OnDeleteSelectedElement()
        self.assertEqual(len(frame.b_roll_items), 0)
        self.assertIsNone(frame.focused_element)

    def _make_cut_frame(self, items, ripple_mode="per_track"):
        frame = self._make_frame(items, ripple_mode=ripple_mode)
        frame.timeline = [TimelineSegment("main.mp4", 0.0, 14.0)]
        frame.focused_element = dict(items[1])
        frame.selected_element_ids = set()
        return frame

    def test_cut_ripples_and_refocuses_element_at_position(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_cut_frame(items, ripple_mode="per_track")
        frame.current_time = 6.0
        with mock.patch("video_maker.player.get_program_mode", return_value=PROFESSIONAL_MODE):
            frame.OnCutElements()
        self.assertEqual([item["id"] for item in frame.b_roll_items], ["a", "c"])
        self.assertEqual(float(frame.b_roll_items[1]["start"]), 5.0)
        self.assertEqual(frame.focused_element["id"], "c")
        self.assertEqual(float(frame.focused_element["start"]), 5.0)

    def test_cut_without_ripple_keeps_focus_on_element_at_position(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_cut_frame(items, ripple_mode="off")
        frame.current_time = 9.0
        with mock.patch("video_maker.player.get_program_mode", return_value=PROFESSIONAL_MODE):
            frame.OnCutElements()
        self.assertEqual([item["id"] for item in frame.b_roll_items], ["a", "c"])
        self.assertEqual(float(frame.b_roll_items[1]["start"]), 9.0)
        self.assertEqual(frame.focused_element["id"], "c")

    def _make_background_audio_frame(self, items, ripple_mode="per_track"):
        frame = self._make_frame([], ripple_mode=ripple_mode)
        frame.timeline = [TimelineSegment("main.mp4", 0.0, 14.0)]
        frame.background_audio_items = list(items)
        frame.current_background_audio_match = lambda: ("bg", items[1])
        frame.current_time = 0.0
        frame.focused_element = dict(items[1])
        return frame

    def test_delete_background_audio_ripples_and_refocuses(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_background_audio_frame(items, ripple_mode="per_track")
        frame.OnDeleteCurrentBackgroundAudio()
        self.assertEqual([item["id"] for item in frame.background_audio_items], ["a", "c"])
        self.assertEqual(float(frame.background_audio_items[1]["start"]), 5.0)
        self.assertEqual(frame.focused_element["id"], "c")
        self.assertEqual(frame.start_time, 5.0)
        self.assertEqual(frame.end_time, 9.0)

    def test_delete_background_audio_without_ripple_keeps_edit_points(self):
        items = [make_item("a", 0, 5), make_item("b", 5, 9), make_item("c", 9, 14)]
        frame = self._make_background_audio_frame(items, ripple_mode="off")
        frame.OnDeleteCurrentBackgroundAudio()
        self.assertEqual([item["id"] for item in frame.background_audio_items], ["a", "c"])
        self.assertEqual(float(frame.background_audio_items[1]["start"]), 9.0)
        self.assertEqual(frame.focused_element["id"], "c")
        self.assertEqual(frame.start_time, 5.0)
        self.assertEqual(frame.end_time, 9.0)

    def test_main_timeline_delete_navigates_to_new_position(self):
        frame = self._make_frame([], ripple_mode="per_track")
        frame.current_track = MAIN_VIDEO_TRACK
        frame.timeline = [
            TimelineSegment("a.mp4", 0.0, 5.0),
            TimelineSegment("b.mp4", 5.0, 9.0),
            TimelineSegment("c.mp4", 9.0, 14.0),
        ]
        frame.focused_element = {"path": "b.mp4", "start": 5.0, "end": 9.0, "speed": 1.0}
        frame.selected_element_ids = set()
        frame.current_time = 6.0
        frame.OnDeleteSelectedElement()
        self.assertEqual(
            [(segment.path, segment.start, segment.end) for segment in frame.timeline],
            [("a.mp4", 0.0, 5.0), ("c.mp4", 9.0, 14.0)],
        )
        self.assertEqual(frame.focused_element["path"], "c.mp4")
        frame.OnNextElementOnTrack()
        self.assertEqual(frame.focused_element["path"], "c.mp4")
        frame.OnPreviousElementOnTrack()
        self.assertEqual(frame.focused_element["path"], "a.mp4")


def _professional():
    return mock.patch(
        "video_maker.player_modules.professional.get_program_mode",
        return_value=PROFESSIONAL_MODE,
    )


class NavigationSilenceTest(unittest.TestCase):
    def _make_frame(self):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = SECONDARY_VIDEO_TRACK
        frame.b_roll_items = []
        frame.timeline = []
        frame.sound_effects_items = []
        frame.background_audio_items = []
        frame.visual_items = []
        frame.ripple_mode = "per_track"
        frame.selected_element_ids = set()
        frame.focused_element = None
        frame.current_time = 0.0
        frame.is_dirty = False
        frame.start_time = None
        frame.end_time = None
        frame.playback_requested = True
        frame.timeline_duration_cache = 10.0
        frame.timeline_positions_cache = [0.0]
        frame.timeline_boundaries_cache = []
        frame.timeline_boundaries_cache_signature = None
        frame.say = mock.Mock()
        frame.require_open_file = lambda: True
        frame.record_edit = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.capture_edit_state = mock.Mock(return_value={})
        frame.load_timeline_time = mock.Mock()
        frame.sync_background_audio_at = mock.Mock()
        frame.muted_tracks = set()
        frame.solo_tracks = set()
        return frame

    def test_element_navigation_does_not_request_playback(self):
        frame = self._make_frame()
        frame.b_roll_items = [make_item("a", 0, 5), make_item("b", 5, 9)]
        with _professional():
            frame.OnNextElementOnTrack()
        load_args, load_kwargs = frame.load_timeline_time.call_args
        self.assertFalse(load_args[1])
        bg_args, bg_kwargs = frame.sync_background_audio_at.call_args
        self.assertFalse(bg_kwargs.get("should_play", True))


if __name__ == "__main__":
    unittest.main()
