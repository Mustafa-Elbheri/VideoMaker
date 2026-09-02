import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.app_state import normalize_ripple_mode
from video_maker.timeline import TimelineSegment
from video_maker.track_items import (
    apply_selection_to,
    element_identifier,
    element_to_dict,
    items_in_range,
    next_item_on_track,
    previous_item_on_track,
    split_item,
)


def make_item(item_id, start, end, **kwargs):
    item = {"id": item_id, "type": "video", "path": f"{item_id}.mp4", "start": float(start), "end": float(end)}
    item.update(kwargs)
    return item


class NextItemOnTrackTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            make_item("a", 0.0, 5.0),
            make_item("b", 10.0, 15.0),
            make_item("c", 20.0, 25.0),
        ]

    def test_next_returns_first_item_after_current_end(self):
        target = next_item_on_track(self.items, "a", 1)
        self.assertEqual(element_identifier(target), "b")

    def test_next_returns_none_at_end_of_line(self):
        target = next_item_on_track(self.items, "c", 1)
        self.assertIsNone(target)

    def test_next_with_no_focus_returns_first(self):
        target = next_item_on_track(self.items, "", 1)
        self.assertEqual(element_identifier(target), "a")

    def test_next_with_unknown_id_returns_first(self):
        target = next_item_on_track(self.items, "missing", 1)
        self.assertEqual(element_identifier(target), "a")

    def test_previous_returns_last_item_before_current_start(self):
        target = previous_item_on_track(self.items, "b", -1)
        self.assertEqual(element_identifier(target), "a")

    def test_previous_returns_none_at_start_of_line(self):
        target = previous_item_on_track(self.items, "a", -1)
        self.assertIsNone(target)

    def test_empty_list_returns_none(self):
        self.assertIsNone(next_item_on_track([], "a", 1))
        self.assertIsNone(previous_item_on_track([], "a", -1))


class ElementIdentifierTest(unittest.TestCase):
    def test_dict_uses_its_id(self):
        item = make_item("x", 0.0, 5.0)
        self.assertEqual(element_identifier(item), "x")

    def test_segment_gets_stable_field_key(self):
        segment = TimelineSegment("a.mp4", 1.0, 9.0, speed=2.0)
        self.assertEqual(
            element_identifier(segment),
            "main:a.mp4:1.0:9.0:2.0",
        )


class ElementToDictTest(unittest.TestCase):
    def test_segment_converts_to_dict(self):
        segment = TimelineSegment("a.mp4", 1.0, 9.0, speed=2.0, transition="fade")
        entry = element_to_dict(segment)
        self.assertEqual(entry["path"], "a.mp4")
        self.assertEqual(entry["start"], 1.0)
        self.assertEqual(entry["end"], 9.0)
        self.assertEqual(entry["transition"], "fade")

    def test_deep_copy_does_not_share_objects(self):
        item = make_item("x", 0.0, 5.0, options={"label": "first"})
        entry = element_to_dict(item)
        clone = copy.deepcopy(entry)
        clone["options"]["label"] = "changed"
        clone["start"] = 99.0
        self.assertEqual(entry["options"]["label"], "first")
        self.assertEqual(entry["start"], 0.0)


class ItemsInRangeTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            make_item("a", 0.0, 5.0),
            make_item("b", 10.0, 15.0),
            make_item("c", 20.0, 25.0),
        ]

    def test_returns_intersecting_items(self):
        result = items_in_range(self.items, 4.0, 22.0)
        self.assertEqual([element_identifier(item) for item in result], ["a", "b", "c"])

    def test_returns_empty_when_range_before_all(self):
        result = items_in_range(self.items, 30.0, 40.0)
        self.assertEqual(result, [])


class ApplySelectionToTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            make_item("a", 0.0, 5.0),
            make_item("b", 10.0, 15.0),
            make_item("c", 20.0, 25.0),
        ]

    def test_adds_range_items_to_existing_selection(self):
        selected = apply_selection_to(self.items, {"a"}, 9.0, 21.0)
        self.assertEqual(selected, {"a", "b", "c"})

    def test_preserves_existing_selection(self):
        selected = apply_selection_to(self.items, {"a"}, 0.0, 4.0)
        self.assertEqual(selected, {"a"})


class ClipboardPasteDeepCopyTest(unittest.TestCase):
    def test_copied_entries_are_independent_after_paste(self):
        source = [make_item("a", 0.0, 5.0), make_item("b", 5.0, 9.0)]
        clipboard = [element_to_dict(item) for item in source]
        pasted = [copy.deepcopy(entry) for entry in clipboard]
        pasted[0]["id"] = "new-a"
        pasted[0]["start"] = 100.0
        self.assertEqual(clipboard[0]["id"], "a")
        self.assertEqual(clipboard[0]["start"], 0.0)
        self.assertNotEqual(pasted[0]["id"], clipboard[0]["id"])


class SplitNavigationTest(unittest.TestCase):
    def test_navigation_covers_all_split_pieces(self):
        item = make_item("x", 0.0, 10.0)
        left, right = split_item(item, 3.0)
        storage = [left, right]
        mid, tail = split_item(right, 7.0)
        storage = [left, mid, tail]
        ids = [element_identifier(item) for item in storage]
        self.assertEqual(len(set(ids)), 3)
        order = []
        current_id = element_identifier(left)
        for _ in range(3):
            order.append(current_id)
            target = next_item_on_track(storage, current_id, 1)
            if target is None:
                break
            current_id = element_identifier(target)
        self.assertEqual(len(set(order)), 3)


class RippleModeNormalizeTest(unittest.TestCase):
    def test_normalizes_to_known_modes(self):
        self.assertEqual(normalize_ripple_mode("per_track"), "per_track")
        self.assertEqual(normalize_ripple_mode("all_tracks"), "all_tracks")
        self.assertEqual(normalize_ripple_mode("off"), "off")

    def test_falls_back_to_per_track(self):
        self.assertEqual(normalize_ripple_mode("unknown"), "per_track")
        self.assertEqual(normalize_ripple_mode(""), "per_track")
        self.assertEqual(normalize_ripple_mode(None), "per_track")


if __name__ == "__main__":
    unittest.main()
