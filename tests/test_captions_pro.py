import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.auto_subtitles_module import SubtitleSegment
from video_maker.text_overlay import TextOverlayOptions
from video_maker.track_items import build_text_segments, from_grok_caption, new_dynamic_text_item


def make_defaults(text="", position="center_bottom"):
    return TextOverlayOptions(
        text=text,
        font_path="C:\\fonts\\subtitle.ttf",
        font_name="Arial",
        font_size=48,
        color=(255, 230, 0, 255),
        background="black",
        background_opacity=70,
        position=position,
        box_width_percent=80,
        mode="subtitles",
    )


def grok_caption(text, start, end):
    return {"text": text, "start": start, "end": end}


class FromGrokCaptionTest(unittest.TestCase):
    def test_dict_caption_becomes_dynamic_item_with_correct_options(self):
        defaults = make_defaults()
        item = from_grok_caption(grok_caption("مرحبا بالعالم", 1.5, 3.5), defaults)
        self.assertTrue(item["is_dynamic"])
        self.assertEqual(item["type"], "text")
        self.assertEqual(item["start"], 1.5)
        self.assertEqual(item["end"], 3.5)
        options = item["options"]
        self.assertEqual(options["text"], "مرحبا بالعالم")
        self.assertEqual(options["font_size"], 48)
        self.assertEqual(options["font_path"], "C:\\fonts\\subtitle.ttf")
        self.assertEqual(options["color"], (255, 230, 0, 255))
        self.assertEqual(options["background"], "black")
        self.assertEqual(options["position"], "center_bottom")
        self.assertEqual(options["mode"], "subtitles")

    def test_segment_object_supported(self):
        defaults = make_defaults()
        seg = SubtitleSegment(start=2.0, end=4.0, text="نص من القطعة")
        item = from_grok_caption(seg, defaults)
        self.assertEqual(item["start"], 2.0)
        self.assertEqual(item["end"], 4.0)
        self.assertEqual(item["options"]["text"], "نص من القطعة")

    def test_without_defaults_uses_base_defaults(self):
        item = from_grok_caption(grok_caption("نص فقط", 0.0, 2.0), None)
        self.assertEqual(item["options"]["text"], "نص فقط")
        self.assertEqual(item["options"]["font_size"], 44)
        self.assertEqual(item["options"]["position"], "center_bottom")

    def test_each_caption_gets_unique_id(self):
        defaults = make_defaults()
        first = from_grok_caption(grok_caption("أ", 0.0, 1.0), defaults)
        second = from_grok_caption(grok_caption("ب", 1.0, 2.0), defaults)
        self.assertNotEqual(first["id"], second["id"])

    def test_negative_duration_is_clamped(self):
        item = from_grok_caption(grok_caption("نص", 3.0, 1.0), make_defaults())
        self.assertGreaterEqual(item["end"], item["start"])


class CaptionsAppearInBuildTextSegmentsTest(unittest.TestCase):
    def test_captions_appear_with_same_timings(self):
        defaults = make_defaults()
        captions = [
            from_grok_caption(grok_caption("أول", 0.0, 2.0), defaults),
            from_grok_caption(grok_caption("ثان", 2.5, 5.0), defaults),
        ]
        segments = build_text_segments(captions)
        self.assertEqual(len(segments), 2)
        self.assertEqual([segment["options"]["text"] for segment in segments], ["أول", "ثان"])
        self.assertEqual([segment["start"] for segment in segments], [0.0, 2.5])
        self.assertEqual([segment["end"] for segment in segments], [2.0, 5.0])

    def test_manual_dynamic_items_equal_grok_conversion(self):
        defaults = make_defaults()
        converted = from_grok_caption(grok_caption("مساو", 1.0, 3.0), defaults)
        manual = new_dynamic_text_item(make_defaults(text="مساو"), 1.0, 3.0)
        self.assertEqual(
            build_text_segments([converted])[0]["options"]["text"],
            build_text_segments([manual])[0]["options"]["text"],
        )


class CaptionOverlapLayeringTest(unittest.TestCase):
    def test_consecutive_captions_do_not_overlap(self):
        defaults = make_defaults()
        first = from_grok_caption(grok_caption("أول", 0.0, 2.0), defaults)
        second = from_grok_caption(grok_caption("ثان", 2.0, 4.0), defaults)
        self.assertGreaterEqual(second["start"], first["end"])
        segments = build_text_segments([first, second])
        self.assertEqual([segment["layer"] for segment in segments], [0, 0])

    def test_overlapping_captions_raise_layer(self):
        defaults = make_defaults()
        older = from_grok_caption(grok_caption("قديم", 0.0, 5.0), defaults)
        newer = from_grok_caption(grok_caption("جديد", 3.0, 8.0), defaults)
        segments = build_text_segments([older, newer])
        self.assertEqual(segments[0]["layer"], 0)
        self.assertEqual(segments[1]["layer"], 1)


if __name__ == "__main__":
    unittest.main()
