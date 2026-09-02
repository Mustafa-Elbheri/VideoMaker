import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.timeline import TimelineSegment
from video_maker.track_items import (
    item_at_time,
    ripple_shift,
    ripple_shift_segments,
    should_ripple,
    split_item,
)


def make_item(item_id, start, end, **kwargs):
    item = {"id": item_id, "type": "video", "path": f"{item_id}.mp4", "start": float(start), "end": float(end)}
    item.update(kwargs)
    return item


class RippleShiftTest(unittest.TestCase):
    def test_moves_items_after_from_time_on_multiple_tracks(self):
        panels = {
            "a": [make_item("a1", 0.0, 5.0), make_item("a2", 10.0, 15.0)],
            "b": [make_item("b1", 3.0, 8.0), make_item("b2", 20.0, 25.0)],
        }
        ripple_shift(panels, 9.0, -5.0, "per_track")
        self.assertEqual((panels["a"][0]["start"], panels["a"][0]["end"]), (0.0, 5.0))
        self.assertEqual((panels["a"][1]["start"], panels["a"][1]["end"]), (5.0, 10.0))
        self.assertEqual((panels["b"][0]["start"], panels["b"][0]["end"]), (3.0, 8.0))
        self.assertEqual((panels["b"][1]["start"], panels["b"][1]["end"]), (15.0, 20.0))

    def test_positive_delta_shifts_right(self):
        panels = {"a": [make_item("a1", 0.0, 5.0), make_item("a2", 5.0, 9.0)]}
        ripple_shift(panels, 5.0, 3.0, "all_tracks")
        self.assertEqual((panels["a"][0]["start"], panels["a"][0]["end"]), (0.0, 5.0))
        self.assertEqual((panels["a"][1]["start"], panels["a"][1]["end"]), (8.0, 12.0))

    def test_off_mode_does_not_move_anything(self):
        panels = {"a": [make_item("a1", 0.0, 5.0), make_item("a2", 10.0, 15.0)]}
        original = copy.deepcopy(panels)
        result = ripple_shift(panels, 6.0, -5.0, "off")
        self.assertEqual(panels, original)
        self.assertEqual(result, [])


class RippleShiftSegmentsTest(unittest.TestCase):
    def test_shifts_segments_starting_at_or_after_from_time(self):
        segments = [
            TimelineSegment("a.mp4", 0.0, 10.0),
            TimelineSegment("b.mp4", 10.0, 20.0),
            TimelineSegment("c.mp4", 20.0, 30.0),
        ]
        shifted = ripple_shift_segments(segments, 10.0, -5.0, "per_track")
        self.assertEqual((shifted[0].start, shifted[0].end), (0.0, 10.0))
        self.assertEqual((shifted[1].start, shifted[1].end), (5.0, 15.0))
        self.assertEqual((shifted[2].start, shifted[2].end), (15.0, 25.0))

    def test_off_mode_returns_unchanged_list(self):
        segments = [TimelineSegment("a.mp4", 0.0, 10.0)]
        shifted = ripple_shift_segments(segments, 0.0, 5.0, "off")
        self.assertEqual(shifted, segments)


class RippleDeleteJoinTest(unittest.TestCase):
    def test_split_and_delete_middle_joins_ends(self):
        items = [make_item("a", 0.0, 10.0), make_item("b", 10.0, 20.0), make_item("c", 20.0, 30.0)]
        left, right = split_item(items[1], 15.0)
        items[1:2] = [left, right]
        self.assertEqual((items[1]["start"], items[1]["end"]), (10.0, 15.0))
        self.assertEqual((items[2]["start"], items[2]["end"]), (15.0, 20.0))
        middle = items[2]
        del items[2]
        ripple_shift({"main": items}, middle["end"], -(middle["end"] - middle["start"]), "per_track")
        self.assertEqual(len(items), 3)
        self.assertEqual((items[0]["start"], items[0]["end"]), (0.0, 10.0))
        self.assertEqual((items[1]["start"], items[1]["end"]), (10.0, 15.0))
        self.assertEqual((items[2]["start"], items[2]["end"]), (15.0, 25.0))
        self.assertEqual(items[2]["start"], items[1]["end"])


class SplitItemSourceOffsetWithSpeedTest(unittest.TestCase):
    def test_split_corrects_source_offset_for_non_one_speed(self):
        item = make_item("x", 10.0, 20.0, speed=2.0, source_offset=3.0)
        _left, right = split_item(item, 14.0)
        self.assertEqual(right["source_offset"], 3.0 + (14.0 - 10.0) * 2.0)


class RippleInsertWeldTest(unittest.TestCase):
    def _weld(self, ripple_mode, at_time=6.0, insert_duration=4.0):
        storage = [make_item("a", 0.0, 10.0), make_item("b", 10.0, 20.0)]
        conflict = item_at_time(storage, at_time)
        index = storage.index(conflict)
        left, right = split_item(storage[index], at_time)
        storage[index:index + 1] = [left, right]
        ripple_shift({"main": storage}, at_time, insert_duration, ripple_mode)
        new_item = make_item("new", at_time, at_time + insert_duration)
        insert_pos = next(
            (i for i, existing in enumerate(storage) if float(existing.get("start", 0.0) or 0.0) >= at_time),
            len(storage),
        )
        storage.insert(insert_pos, new_item)
        return storage

    def test_split_conflict_shift_then_insert_avoids_overlap(self):
        storage = self._weld("per_track")
        self.assertEqual([(item["start"], item["end"]) for item in storage], [
            (0.0, 6.0),
            (6.0, 10.0),
            (10.0, 14.0),
            (14.0, 24.0),
        ])

    def test_off_mode_welds_without_shifting(self):
        storage = self._weld("off")
        self.assertEqual([(item["start"], item["end"]) for item in storage], [
            (0.0, 6.0),
            (6.0, 10.0),
            (6.0, 10.0),
            (10.0, 20.0),
        ])


class ShouldRippleTest(unittest.TestCase):
    def test_ripple_flags(self):
        self.assertTrue(should_ripple("per_track"))
        self.assertTrue(should_ripple("all_tracks"))
        self.assertFalse(should_ripple("off"))


if __name__ == "__main__":
    unittest.main()
