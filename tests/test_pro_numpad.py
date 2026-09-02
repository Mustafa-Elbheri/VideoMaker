import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath("."))

from video_maker.timeline import TimelineSegment
from video_maker.tracks import (
    BACKGROUND_AUDIO_TRACK,
    MAIN_VIDEO_TRACK,
    SECONDARY_VIDEO_TRACK,
    SOUND_EFFECTS_TRACK,
)
from video_maker.program_modes import NORMAL_MODE, PROFESSIONAL_MODE
from video_maker.timeline_engine import NUDGE_STEP_SAMPLES


def make_item(item_id, start, end, item_type="video"):
    return {
        "id": item_id,
        "type": item_type,
        "path": "{id}.mp4".format(id=item_id),
        "start": float(start),
        "end": float(end),
        "speed": 1.0,
        "source_offset": 0.0,
    }


class ProNumpadTest(unittest.TestCase):
    def _make_frame(self, items=None, sfx=None, background_audio=None, timeline=None, ripple_mode="per_track"):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = SECONDARY_VIDEO_TRACK
        frame.timeline = list(timeline or ())
        frame.b_roll_items = list(items or ())
        frame.sound_effects_items = list(sfx or ())
        frame.background_audio_items = list(background_audio or ())
        frame.visual_items = []
        frame.ripple_mode = ripple_mode
        frame.media_kind = "video"
        frame.selected_element_ids = set()
        frame.focused_element = None
        frame.current_time = 0.0
        frame.is_dirty = False
        frame.last_insert_end = 0.0
        frame.start_time = None
        frame.end_time = None
        frame.edit_points = []
        frame.playback_requested = False
        frame.say = mock.Mock()
        frame.require_open_file = lambda: True
        frame.stop_playback_for_timeline_edit = lambda *args, **kwargs: False
        frame.record_edit = mock.Mock()
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.capture_edit_state = mock.Mock(return_value={})
        frame.notify_failed_edit_restored = lambda *args, **kwargs: None
        frame.timeline_boundaries_cache_signature = None
        frame.timeline_positions_cache = [0.0]
        frame.timeline_boundaries_cache = []
        frame.timeline_duration_cache = 0.0
        return frame

    def _professional(self):
        return mock.patch(
            "video_maker.player_modules.professional.get_program_mode",
            return_value=PROFESSIONAL_MODE,
        )

    def _normal(self):
        return mock.patch(
            "video_maker.player_modules.professional.get_program_mode",
            return_value=NORMAL_MODE,
        )

    def test_nudge_right_ripples_track_and_refocuses(self):
        frame = self._make_frame(
            items=[make_item("a", 0, 5), make_item("b", 5, 9)],
            ripple_mode="per_track",
        )
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._professional():
            frame.OnNudgeElementRight()
        self.assertEqual(float(frame.b_roll_items[0]["start"]), 0.05)
        self.assertEqual(float(frame.b_roll_items[0]["end"]), 5.05)
        self.assertEqual(float(frame.b_roll_items[1]["start"]), 5.05)
        self.assertEqual(float(frame.b_roll_items[1]["end"]), 9.05)
        self.assertEqual(frame.focused_element["id"], "a")
        self.assertEqual(float(frame.focused_element["start"]), 0.05)
        self.assertTrue(frame.is_dirty)
        frame.record_edit.assert_called_once()
        frame.say.assert_called_once()

    def test_nudge_left_at_timeline_start_blocked(self):
        frame = self._make_frame(items=[make_item("a", 0, 5)], ripple_mode="per_track")
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._professional():
            frame.OnNudgeElementLeft()
        self.assertEqual(float(frame.b_roll_items[0]["start"]), 0.0)
        self.assertFalse(frame.is_dirty)
        frame.say.assert_called_once()
        frame.record_edit.assert_not_called()

    def test_nudge_left_into_previous_item_blocked(self):
        frame = self._make_frame(
            items=[make_item("a", 0, 5), make_item("b", 5, 9)],
            ripple_mode="per_track",
        )
        frame.focused_element = dict(frame.b_roll_items[1])
        with self._professional():
            frame._nudge_focused_element(-3 * NUDGE_STEP_SAMPLES)
        self.assertEqual(float(frame.b_roll_items[0]["start"]), 0.0)
        self.assertEqual(float(frame.b_roll_items[1]["start"]), 5.0)
        self.assertFalse(frame.is_dirty)
        frame.say.assert_called_once()

    def test_nudge_all_tracks_opens_gap_in_main(self):
        frame = self._make_frame(
            items=[make_item("a", 0, 5), make_item("b", 5, 10)],
            sfx=[make_item("s", 4, 6, item_type="sound_effect")],
            timeline=[TimelineSegment("main.mp4", 0.0, 10.0)],
            ripple_mode="all_tracks",
        )
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._professional():
            frame.OnNudgeElementRight()
        self.assertEqual(float(frame.b_roll_items[0]["start"]), 0.05)
        self.assertEqual(float(frame.b_roll_items[1]["start"]), 5.05)
        self.assertEqual(float(frame.sound_effects_items[0]["start"]), 4.05)
        self.assertEqual(float(frame.timeline[0].start), 0.05)
        self.assertEqual(float(frame.timeline[0].end), 10.05)
        self.assertEqual(frame.focused_element["id"], "a")

    def test_nudge_all_tracks_left_ripples_main_range(self):
        frame = self._make_frame(
            items=[make_item("a", 0.1, 5.1), make_item("b", 5.1, 10.1)],
            timeline=[TimelineSegment("main.mp4", 0.0, 10.0)],
            ripple_mode="all_tracks",
        )
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._professional():
            frame.OnNudgeElementLeft()
        self.assertEqual(float(frame.b_roll_items[0]["start"]), 0.05)
        self.assertEqual(float(frame.b_roll_items[1]["start"]), 5.05)
        self.assertEqual(float(frame.timeline[0].start), 0.0)
        self.assertLess(float(frame.timeline[0].end), 10.0)

    def test_move_sound_effect_to_background_audio_track(self):
        frame = self._make_frame(
            sfx=[make_item("s", 2, 5, item_type="sound_effect")],
            ripple_mode="per_track",
        )
        frame.current_track = SOUND_EFFECTS_TRACK
        frame.focused_element = dict(frame.sound_effects_items[0])
        with self._professional():
            frame.OnMoveElementToNextTrack()
        self.assertEqual(frame.sound_effects_items, [])
        self.assertEqual(len(frame.background_audio_items), 1)
        moved = frame.background_audio_items[0]
        self.assertEqual(moved["id"], "s")
        self.assertEqual(moved["type"], "background_audio")
        self.assertEqual(float(moved["start"]), 2.0)
        self.assertEqual(float(moved["end"]), 5.0)
        self.assertEqual(frame.focused_element["id"], "s")
        self.assertTrue(frame.is_dirty)
        frame.record_edit.assert_called_once()

    def test_move_from_main_to_secondary_replaces_with_gap(self):
        frame = self._make_frame(
            items=[],
            timeline=[
                TimelineSegment("b.mp4", 0.0, 5.0),
                TimelineSegment("c.mp4", 5.0, 10.0),
            ],
            ripple_mode="per_track",
        )
        frame.current_track = MAIN_VIDEO_TRACK
        frame.focused_element = {"path": "c.mp4", "start": 5.0, "end": 10.0, "speed": 1.0}
        frame._make_gap_segment = mock.Mock(
            return_value=TimelineSegment("gap.mp4", 0.0, 5.0)
        )
        with self._professional():
            frame.OnMoveElementToNextTrack()
        self.assertEqual(len(frame.timeline), 2)
        self.assertEqual(frame.timeline[0].path, "b.mp4")
        self.assertEqual(frame.timeline[1].path, "gap.mp4")
        self.assertEqual(len(frame.b_roll_items), 1)
        moved = frame.b_roll_items[0]
        self.assertEqual(moved["id"], "main:c.mp4:5.0:10.0:1.0")
        self.assertEqual(float(moved["start"]), 5.0)
        self.assertEqual(float(moved["end"]), 10.0)
        self.assertEqual(frame.focused_element["id"], "main:c.mp4:5.0:10.0:1.0")
        self.assertTrue(frame.is_dirty)

    def test_move_secondary_to_main_inserts_segment(self):
        frame = self._make_frame(
            items=[make_item("a", 0, 5)],
            timeline=[TimelineSegment("main.mp4", 0.0, 10.0)],
            ripple_mode="per_track",
        )
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._professional():
            frame.OnMoveElementToPreviousTrack()
        self.assertEqual(frame.b_roll_items, [])
        self.assertGreaterEqual(len(frame.timeline), 2)
        self.assertEqual(frame.focused_element["path"], "a.mp4")
        self.assertTrue(frame.is_dirty)

    def test_move_off_mode_does_not_shift_other_items(self):
        frame = self._make_frame(
            sfx=[
                make_item("s", 2, 5, item_type="sound_effect"),
                make_item("u", 7, 10, item_type="sound_effect"),
            ],
            ripple_mode="off",
        )
        frame.current_track = SOUND_EFFECTS_TRACK
        frame.focused_element = dict(frame.sound_effects_items[0])
        with self._professional():
            frame.OnMoveElementToNextTrack()
        self.assertEqual(len(frame.sound_effects_items), 1)
        self.assertEqual(frame.sound_effects_items[0]["id"], "u")
        self.assertEqual(float(frame.sound_effects_items[0]["start"]), 7.0)
        self.assertEqual(len(frame.background_audio_items), 1)
        self.assertEqual(frame.background_audio_items[0]["id"], "s")

    def test_move_rejected_media_type_announces(self):
        frame = self._make_frame(items=[make_item("a", 0, 5)], ripple_mode="per_track")
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._professional():
            frame.OnMoveElementToNextTrack()
        self.assertEqual(len(frame.b_roll_items), 1)
        self.assertFalse(frame.is_dirty)
        frame.say.assert_called_once()

    def test_normal_mode_ignored(self):
        frame = self._make_frame(items=[make_item("a", 0, 5)], ripple_mode="per_track")
        frame.focused_element = dict(frame.b_roll_items[0])
        with self._normal():
            frame.OnNudgeElementRight()
            frame.OnNudgeElementLeft()
            frame.OnMoveElementToNextTrack()
            frame.OnMoveElementToPreviousTrack()
        self.assertEqual(float(frame.b_roll_items[0]["start"]), 0.0)
        self.assertFalse(frame.is_dirty)
        frame.say.assert_not_called()

    def test_no_focused_element_announces(self):
        frame = self._make_frame(items=[make_item("a", 0, 5)], ripple_mode="per_track")
        with self._professional():
            frame.OnNudgeElementRight()
        frame.say.assert_called_once()
        self.assertFalse(frame.is_dirty)


if __name__ == "__main__":
    unittest.main()
