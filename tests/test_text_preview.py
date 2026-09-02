import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.text_overlay import TextOverlayOptions, serialize_text_options
from video_maker.track_items import (
    new_dynamic_text_item,
    render_preview_layer,
    should_use_fast_path,
    split_item,
    text_preview_fingerprint,
)


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


def make_item(options=None, start=1.0, end=4.0):
    return new_dynamic_text_item(options or make_options(), start, end)


class TextPreviewFingerprintTest(unittest.TestCase):
    def test_empty_input_returns_empty_string(self):
        self.assertEqual(text_preview_fingerprint(None), "")
        self.assertEqual(text_preview_fingerprint([]), "")

    def test_ignores_non_dynamic_items(self):
        items = [{"type": "image", "path": "x.png", "start": 0.0, "end": 2.0}]
        self.assertEqual(text_preview_fingerprint(items), "")

    def test_fingerprint_changes_on_option_change(self):
        before = text_preview_fingerprint([make_item()])
        changed = make_item(make_options(text="نص آخر"))
        self.assertNotEqual(before, text_preview_fingerprint([changed]))

    def test_fingerprint_changes_on_move(self):
        before = text_preview_fingerprint([make_item()])
        moved = make_item(start=2.0, end=5.0)
        self.assertNotEqual(before, text_preview_fingerprint([moved]))

    def test_fingerprint_changes_on_split(self):
        item = make_item()
        left, right = split_item(item, 2.0)
        self.assertNotEqual(
            text_preview_fingerprint([item]),
            text_preview_fingerprint([left, right]),
        )

    def test_fingerprint_changes_on_add(self):
        before = text_preview_fingerprint([make_item()])
        added = [make_item(), make_item(make_options(text="ثاني"), 5.0, 7.0)]
        self.assertNotEqual(before, text_preview_fingerprint(added))

    def test_fingerprint_changes_on_delete(self):
        items = [make_item(), make_item(make_options(text="ثاني"), 5.0, 7.0)]
        before = text_preview_fingerprint(items)
        self.assertNotEqual(before, text_preview_fingerprint([items[0]]))

    def test_fingerprint_ignores_playhead_only(self):
        items = [make_item()]
        before = text_preview_fingerprint(items)
        for playhead in (0.0, 1.0, 2.0, 3.0, 9.0):
            render_preview_layer(items, playhead)
        self.assertEqual(before, text_preview_fingerprint(items))

    def test_fingerprint_stable_across_calls(self):
        items = [make_item(), make_item(make_options(text="ثاني"), 5.0, 7.0)]
        self.assertEqual(text_preview_fingerprint(items), text_preview_fingerprint(items))

    def test_fingerprint_covers_serialized_options(self):
        item = make_item()
        restored = dict(item)
        restored["options"] = dict(serialize_text_options(item.get("options")))
        self.assertEqual(text_preview_fingerprint([item]), text_preview_fingerprint([restored]))


class RenderPreviewLayerTest(unittest.TestCase):
    def test_returns_none_when_no_items(self):
        self.assertIsNone(render_preview_layer(None, 2.0))
        self.assertIsNone(render_preview_layer([], 2.0))

    def test_returns_active_item_at_playhead(self):
        item = make_item(start=1.0, end=4.0)
        active = render_preview_layer([item], 2.5)
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], item["id"])

    def test_returns_none_outside_range(self):
        item = make_item(start=1.0, end=4.0)
        self.assertIsNone(render_preview_layer([item], 0.5))
        self.assertIsNone(render_preview_layer([item], 4.5))

    def test_start_inclusive_end_exclusive(self):
        item = make_item(start=1.0, end=4.0)
        self.assertIsNotNone(render_preview_layer([item], 1.0))
        self.assertIsNone(render_preview_layer([item], 4.0))

    def test_returns_later_item_when_overlapping(self):
        first = make_item(make_options(text="أ"), 0.0, 5.0)
        second = make_item(make_options(text="ب"), 3.0, 8.0)
        active = render_preview_layer([first, second], 4.0)
        self.assertEqual(active["id"], second["id"])


class ShouldUseFastPathTest(unittest.TestCase):
    def test_true_when_no_text_items(self):
        self.assertTrue(should_use_fast_path(None))
        self.assertTrue(should_use_fast_path([]))
        self.assertTrue(should_use_fast_path([{"type": "image", "start": 0.0, "end": 2.0}]))

    def test_false_when_text_item_exists(self):
        self.assertFalse(should_use_fast_path([make_item()]))


if __name__ == "__main__":
    unittest.main()
