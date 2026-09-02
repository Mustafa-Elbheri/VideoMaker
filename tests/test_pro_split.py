import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath("."))

from video_maker.track_items import element_display_name, element_identifier, item_bounds, next_item_on_track
from video_maker.tracks import SECONDARY_VIDEO_TRACK


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


class ProSplitTest(unittest.TestCase):
    def _make_frame(self, track, items):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = track
        frame.timeline = []
        frame.b_roll_items = items if track == SECONDARY_VIDEO_TRACK else []
        frame.sound_effects_items = []
        frame.background_audio_items = []
        frame.visual_items = []
        frame.current_time = 0.0
        frame.focused_element = None
        frame.is_dirty = False
        frame.last_insert_end = 0.0
        frame.start_time = None
        frame.end_time = None
        frame.say = lambda *args, **kwargs: None
        frame.require_open_file = lambda: True
        frame.capture_edit_state = mock.Mock(return_value={})
        frame.record_edit = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        return frame

    def test_split_produces_real_second_piece(self):
        frame = self._make_frame(SECONDARY_VIDEO_TRACK, [make_item("x", 0.0, 10.0)])
        frame.current_time = 3.0
        frame.OnSplitAtPlayhead()
        self.assertEqual(len(frame.b_roll_items), 2)
        self.assertEqual(item_bounds(frame.b_roll_items[0]), (0.0, 3.0))
        self.assertEqual(item_bounds(frame.b_roll_items[1]), (3.0, 10.0))
        ids = {element_identifier(item) for item in frame.b_roll_items}
        self.assertEqual(len(ids), 2)
        self.assertNotIn("x", ids)

    def test_double_split_yields_three_navigable_pieces(self):
        frame = self._make_frame(SECONDARY_VIDEO_TRACK, [make_item("x", 0.0, 10.0)])
        frame.current_time = 3.0
        frame.OnSplitAtPlayhead()
        frame.current_time = 7.0
        frame.OnSplitAtPlayhead()
        self.assertEqual(len(frame.b_roll_items), 3)
        bounds = [item_bounds(item) for item in frame.b_roll_items]
        self.assertEqual(bounds, [(0.0, 3.0), (3.0, 7.0), (7.0, 10.0)])
        ids = [element_identifier(item) for item in frame.b_roll_items]
        self.assertEqual(len(set(ids)), 3)
        order = []
        current_id = ids[0]
        for _ in range(3):
            order.append(current_id)
            target = next_item_on_track(frame.b_roll_items, current_id, 1)
            if target is None:
                break
            current_id = element_identifier(target)
        self.assertEqual(len(set(order)), 3)

    def test_double_split_pieces_are_numbered_with_original_name(self):
        frame = self._make_frame(SECONDARY_VIDEO_TRACK, [make_item("x", 0.0, 10.0)])
        frame.current_time = 3.0
        frame.OnSplitAtPlayhead()
        frame.current_time = 7.0
        frame.OnSplitAtPlayhead()
        names = [element_display_name(item, frame.b_roll_items) for item in frame.b_roll_items]
        self.assertEqual(names, ["1 - x", "2 - x", "3 - x"])


if __name__ == "__main__":
    unittest.main()
