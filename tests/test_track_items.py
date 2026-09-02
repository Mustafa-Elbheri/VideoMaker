import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("."))

from video_maker.timeline import TimelineSegment
from video_maker.track_items import (
    base_element_name,
    element_display_name,
    element_origin_key,
    item_at_time,
    natural_span,
    new_dynamic_text_item,
    split_item,
    split_timeline_segment,
)
from video_maker.tracks import MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK, tracks_accepting


class ItemAtTimeTest(unittest.TestCase):
    def test_returns_item_whose_range_contains_time(self):
        items = [
            {"id": "a", "start": 0.0, "end": 5.0},
            {"id": "b", "start": 5.0, "end": 10.0},
        ]
        self.assertEqual(item_at_time(items, 2.5)["id"], "a")
        self.assertEqual(item_at_time(items, 5.0)["id"], "b")

    def test_end_is_exclusive(self):
        items = [
            {"id": "a", "start": 0.0, "end": 5.0},
            {"id": "b", "start": 5.0, "end": 10.0},
        ]
        self.assertIsNone(item_at_time(items, 10.0))
        self.assertIsNone(item_at_time(items, 20.0))

    def test_returns_none_for_empty_or_no_match(self):
        self.assertIsNone(item_at_time([], 1.0))
        self.assertIsNone(item_at_time([{"id": "a", "start": 3.0, "end": 5.0}], 0.5))

    def test_works_with_timeline_segments(self):
        segments = [TimelineSegment("a.mp4", 0.0, 4.0), TimelineSegment("b.mp4", 4.0, 9.0)]
        self.assertEqual(item_at_time(segments, 1.0).path, "a.mp4")
        self.assertEqual(item_at_time(segments, 4.0).path, "b.mp4")


class SplitItemTest(unittest.TestCase):
    def test_splits_dict_and_corrects_source_offset_with_speed(self):
        item = {
            "id": "x",
            "type": "video",
            "start": 10.0,
            "end": 20.0,
            "speed": 2.0,
            "source_offset": 3.0,
        }
        left, right = split_item(item, 14.0)
        self.assertEqual(left["start"], 10.0)
        self.assertEqual(left["end"], 14.0)
        self.assertEqual(right["start"], 14.0)
        self.assertEqual(right["end"], 20.0)
        self.assertEqual(right["source_offset"], 3.0 + (14.0 - 10.0) * 2.0)
        self.assertEqual(item, {  # الأصل لم يتغير
            "id": "x",
            "type": "video",
            "start": 10.0,
            "end": 20.0,
            "speed": 2.0,
            "source_offset": 3.0,
        })

    def test_source_offset_stays_when_speed_is_one(self):
        item = {"id": "x", "start": 5.0, "end": 15.0, "speed": 1.0, "source_offset": 1.0}
        left, right = split_item(item, 8.0)
        self.assertEqual(left["end"], 8.0)
        self.assertEqual(right["start"], 8.0)
        self.assertEqual(right["source_offset"], 4.0)

    def test_original_is_not_mutated(self):
        item = {"id": "x", "start": 0.0, "end": 10.0, "speed": 1.0, "source_offset": 0.0}
        split_item(item, 4.0)
        self.assertEqual(item["start"], 0.0)
        self.assertEqual(item["end"], 10.0)

    def test_split_generates_fresh_unique_ids(self):
        item = {"id": "x", "type": "video", "start": 0.0, "end": 10.0, "speed": 1.0, "source_offset": 0.0}
        left, right = split_item(item, 3.0)
        mid, tail = split_item(right, 7.0)
        ids = [left["id"], mid["id"], tail["id"]]
        self.assertEqual(len(set(ids)), 3)
        self.assertNotIn("x", ids)

    def test_split_pieces_share_origin_key(self):
        item = {"id": "x", "name": "intro", "path": "intro.mp4", "start": 0.0, "end": 10.0, "speed": 1.0, "source_offset": 0.0}
        left, right = split_item(item, 3.0)
        mid, tail = split_item(right, 7.0)
        pieces = [left, mid, tail]
        keys = {element_origin_key(piece) for piece in pieces}
        self.assertEqual(keys, {"x"})


class ElementDisplayNameTest(unittest.TestCase):
    def _item(self, item_id, start, end, name="intro"):
        return {
            "id": item_id,
            "name": name,
            "path": "intro.mp4",
            "start": float(start),
            "end": float(end),
            "speed": 1.0,
            "source_offset": 0.0,
        }

    def test_unsplit_item_keeps_plain_name(self):
        item = self._item("a", 0.0, 10.0)
        self.assertEqual(element_display_name(item, [item]), "intro")

    def test_split_pieces_are_numbered_in_order(self):
        left, right = split_item(self._item("a", 0.0, 10.0), 3.0)
        storage = [left, right]
        names = [element_display_name(item, storage) for item in storage]
        self.assertEqual(names, ["1 - intro", "2 - intro"])

    def test_double_split_numbers_all_three_pieces(self):
        left, right = split_item(self._item("a", 0.0, 10.0), 3.0)
        mid, tail = split_item(right, 7.0)
        storage = [left, mid, tail]
        names = [element_display_name(item, storage) for item in storage]
        self.assertEqual(names, ["1 - intro", "2 - intro", "3 - intro"])

    def test_base_name_from_path_fallback(self):
        item = {"id": "a", "path": "clip.wav", "start": 0.0, "end": 1.0}
        self.assertEqual(base_element_name(item), "clip")


class SplitTimelineSegmentTest(unittest.TestCase):
    def test_preserves_source_precision(self):
        segment = TimelineSegment("clip.mp4", 10.0, 20.0, speed=2.0)
        left, right = split_timeline_segment([segment], 0, 2.0)
        self.assertEqual(len(left), 1)
        self.assertEqual(len(right), 1)
        self.assertEqual(left[0].path, "clip.mp4")
        self.assertEqual((left[0].start, left[0].end), (10.0, 14.0))
        self.assertEqual((right[0].start, right[0].end), (14.0, 20.0))
        self.assertEqual(right[0].speed, 2.0)

    def test_second_segment_splits_at_correct_offset(self):
        segments = [
            TimelineSegment("a.mp4", 0.0, 10.0, speed=2.0),
            TimelineSegment("b.mp4", 20.0, 30.0, speed=1.0),
        ]
        left, right = split_timeline_segment(segments, 1, 8.0)
        self.assertEqual(len(left), 1)
        self.assertEqual(len(right), 1)
        self.assertEqual((left[0].start, left[0].end), (20.0, 23.0))
        self.assertEqual((right[0].start, right[0].end), (23.0, 30.0))


class NaturalSpanTest(unittest.TestCase):
    def test_returns_zero_for_missing_path(self):
        with patch("video_maker.track_items.get_media_duration", return_value=0.0):
            self.assertEqual(natural_span("C:\\missing\\nonexistent.mp4"), 0.0)

    def test_returns_zero_when_probe_fails(self):
        def fail(path):
            raise OSError("probe failed")

        with patch("video_maker.track_items.get_media_duration", side_effect=fail):
            self.assertEqual(natural_span("broken.mp4"), 0.0)

    def test_returns_natural_duration(self):
        with patch("video_maker.track_items.get_media_duration", return_value=12.5):
            self.assertEqual(natural_span("clip.mp4"), 12.5)


class TracksAcceptingImageTest(unittest.TestCase):
    def test_image_accepts_track_two_not_track_one(self):
        accepted = tracks_accepting("image")
        self.assertIn(SECONDARY_VIDEO_TRACK, accepted)
        self.assertNotIn(MAIN_VIDEO_TRACK, accepted)


class NewDynamicTextItemTest(unittest.TestCase):
    def test_builds_dynamic_text_item(self):
        from video_maker.text_overlay import TextOverlayOptions

        options = TextOverlayOptions(
            text="مرحبا",
            font_path="C:\\fonts\\a.ttf",
            font_name="Arial",
            font_size=44,
            color=(255, 255, 255, 255),
            background="",
            background_opacity=0,
            position="center_bottom",
            box_width_percent=60,
        )
        item = new_dynamic_text_item(options, 1.0, 4.5)
        self.assertEqual(item["type"], "text")
        self.assertEqual(item["path"], "")
        self.assertEqual(item["start"], 1.0)
        self.assertEqual(item["end"], 4.5)
        self.assertTrue(item["is_dynamic"])
        self.assertEqual(item["options"]["text"], "مرحبا")
        self.assertEqual(item["options"]["font_name"], "Arial")


class CaptureEditStateStubTest(unittest.TestCase):
    def _stub(self):
        frame = type("StubPlayer", (), {
            "timeline": [{"path": "a.mp4", "start": 0.0, "end": 5.0}],
            "media_kind": "video",
            "video_path": "a.mp4",
            "visual_items": [{"type": "text"}],
            "background_audio_items": [{"type": "background_audio"}],
            "b_roll_items": [{"type": "video"}],
            "sound_effects_items": [{"type": "sound_effect"}],
            "main_audio_override_path": "",
            "main_audio_override_duration": 0.0,
            "main_audio_override_timeline_duration": 0.0,
            "main_audio_effect_chain": [],
            "main_audio_revision": 0,
            "main_audio_source_revision": 0,
            "timeline_revision": 0,
            "main_audio_format_version": 1,
            "edit_points": [],
            "current_edit_point_id": None,
            "current_time": 1.0,
            "start_time": None,
            "end_time": None,
            "last_insert_end": None,
            "file_metadata": {},
            "is_dirty": False,
            "chroma_render_state": None,
            "focused_element": {"track": "main_video", "item_id": "abc"},
            "selected_element_ids": {"abc"},
            "element_clipboard": {"track": "b_roll", "item": {"id": "abc"}},
            "muted_tracks": {"sound_effects"},
            "ripple_mode": "all_tracks",
        })()
        from video_maker.player import VideoPlayer

        frame.capture_edit_state = VideoPlayer.capture_edit_state.__get__(frame, type(frame))
        return frame

    def test_captures_sound_effects_items(self):
        state = self._stub().capture_edit_state()
        self.assertEqual(state["sound_effects_items"], [{"type": "sound_effect"}])
        self.assertEqual(state["b_roll_items"], [{"type": "video"}])

    def test_captures_editor_state_fields(self):
        state = self._stub().capture_edit_state()
        self.assertEqual(state["focused_element"], {"track": "main_video", "item_id": "abc"})
        self.assertEqual(state["selected_element_ids"], {"abc"})
        self.assertEqual(state["element_clipboard"]["item"]["id"], "abc")
        self.assertEqual(state["muted_tracks"], {"sound_effects"})
        self.assertEqual(state["ripple_mode"], "all_tracks")


if __name__ == "__main__":
    unittest.main()
