import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.text_overlay import TextOverlayOptions, from_text_item, serialize_text_options
from video_maker.track_items import base_element_name, build_text_segments, new_dynamic_text_item


def make_options(text="مرحبا", position="center_bottom"):
    return TextOverlayOptions(
        text=text,
        font_path="C:\\fonts\\a.ttf",
        font_name="Arial",
        font_size=44,
        color=(255, 255, 255, 255),
        background="",
        background_opacity=0,
        position=position,
        box_width_percent=60,
    )


class FromTextItemTest(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(from_text_item(None))

    def test_options_instance_returns_itself(self):
        options = make_options()
        self.assertIs(from_text_item(options), options)

    def test_unknown_type_returns_none(self):
        self.assertIsNone(from_text_item("not an item"))

    def test_dynamic_item_extracts_options(self):
        options = make_options(text="قمر", position="center")
        item = new_dynamic_text_item(options, 1.0, 4.0)
        restored = from_text_item(item)
        self.assertIsInstance(restored, TextOverlayOptions)
        self.assertEqual(restored.text, "قمر")
        self.assertEqual(restored.position, "center")
        self.assertEqual(restored.font_name, "Arial")

    def test_item_without_options_returns_defaults(self):
        restored = from_text_item({"is_dynamic": True, "start": 0.0, "end": 2.0})
        self.assertIsInstance(restored, TextOverlayOptions)
        self.assertEqual(restored.text, "")
        self.assertEqual(restored.font_size, 44)


class BuildTextSegmentsTest(unittest.TestCase):
    def test_empty_input_returns_empty_list(self):
        self.assertEqual(build_text_segments(None), [])
        self.assertEqual(build_text_segments([]), [])

    def test_skips_non_dynamic_and_invalid_items(self):
        items = [
            {"is_dynamic": False, "type": "text", "start": 0.0, "end": 2.0},
            {"is_dynamic": True, "start": 3.0, "end": 3.0},
            {"is_dynamic": True, "options": serialize_text_options(make_options(text="  "))},
        ]
        self.assertEqual(build_text_segments(items), [])

    def test_orders_by_start_then_end(self):
        items = [
            new_dynamic_text_item(make_options(text="ثالث"), 5.0, 7.0),
            new_dynamic_text_item(make_options(text="أول"), 1.0, 2.0),
            new_dynamic_text_item(make_options(text="ثاني"), 3.0, 4.0),
        ]
        segments = build_text_segments(items)
        self.assertEqual([segment["options"]["text"] for segment in segments], ["أول", "ثاني", "ثالث"])

    def test_overlap_increases_layer(self):
        items = [
            new_dynamic_text_item(make_options(text="أ"), 0.0, 5.0),
            new_dynamic_text_item(make_options(text="ب"), 3.0, 8.0),
            new_dynamic_text_item(make_options(text="ج"), 6.0, 9.0),
        ]
        segments = build_text_segments(items)
        self.assertEqual([segment["layer"] for segment in segments], [0, 1, 2])

    def test_disjoint_segment_resets_layer_chain(self):
        items = [
            new_dynamic_text_item(make_options(text="أ"), 0.0, 2.0),
            new_dynamic_text_item(make_options(text="ب"), 3.0, 8.0),
            new_dynamic_text_item(make_options(text="ج"), 6.0, 9.0),
        ]
        segments = build_text_segments(items)
        self.assertEqual([segment["layer"] for segment in segments], [0, 0, 1])

    def test_segment_carries_range_and_options(self):
        item = new_dynamic_text_item(make_options(text="مرحبا"), 1.5, 4.5)
        segment = build_text_segments([item])[0]
        self.assertEqual(segment["start"], 1.5)
        self.assertEqual(segment["end"], 4.5)
        self.assertEqual(segment["options"]["text"], "مرحبا")


class BaseElementNameTextTest(unittest.TestCase):
    def test_dynamic_item_uses_text_snippet(self):
        item = new_dynamic_text_item(make_options(text="تحية صباحية"), 0.0, 2.0)
        self.assertEqual(base_element_name(item), "تحية صباحية")

    def test_long_text_truncated_to_twenty_chars(self):
        item = new_dynamic_text_item(make_options(text="أ" * 30), 0.0, 2.0)
        self.assertEqual(base_element_name(item), "أ" * 20)

    def test_empty_text_falls_back(self):
        item = new_dynamic_text_item(make_options(text="   "), 0.0, 2.0)
        self.assertEqual(base_element_name(item, fallback="نص"), "نص")

    def test_explicit_name_wins_over_snippet(self):
        item = new_dynamic_text_item(make_options(text="المقتطف"), 0.0, 2.0)
        item["name"] = "عنوان مخصص"
        self.assertEqual(base_element_name(item), "عنوان مخصص")


if __name__ == "__main__":
    unittest.main()
