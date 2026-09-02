import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.timeline_engine import (
    BACKGROUND_AUDIO_TRACK,
    MAIN_VIDEO_TRACK,
    NUDGE_STEP_SAMPLES,
    RIPPLE_MODE_ALL_TRACKS,
    RIPPLE_MODE_OFF,
    RIPPLE_MODE_PER_TRACK,
    SECONDARY_VIDEO_TRACK,
    SOUND_EFFECTS_TRACK,
    Engine,
    MediaItem,
    Timeline,
    Track,
    move_to_track,
    nudge_item,
    to_samples,
    to_seconds,
)


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


def _media_type(item):
    item_type = str(item.get("type", "") or "")
    if item_type in ("sound_effect", "background_audio"):
        return "audio"
    if item_type in ("image", "video"):
        return "video"
    if item_type == "text":
        return "text"
    return item_type or "video"


def _track(key, media_types, items):
    return Track(
        key=key,
        media_types=media_types,
        items=[
            MediaItem(id=item["id"], media_type=_media_type(item), payload=dict(item), track_key=key)
            for item in items
        ],
    )


def _engine(secondary=None, sfx=None, background_audio=None, main_segments=None):
    engine = Engine()
    engine.tracks[SECONDARY_VIDEO_TRACK] = _track(SECONDARY_VIDEO_TRACK, ("video", "image"), secondary or [])
    engine.tracks[SOUND_EFFECTS_TRACK] = _track(SOUND_EFFECTS_TRACK, ("audio",), sfx or [])
    engine.tracks[BACKGROUND_AUDIO_TRACK] = _track(
        BACKGROUND_AUDIO_TRACK, ("audio",), background_audio or []
    )
    engine.timeline = Timeline(main_segments=list(main_segments or ()), main_media_type="video")
    return engine


def _positions(engine, key):
    return [(item.id, item.timeline_start, item.timeline_end) for item in engine.tracks[key].items]


class EngineConversionsTest(unittest.TestCase):
    def test_to_seconds_clamps_negative(self):
        self.assertEqual(to_seconds(-3), 0.0)
        self.assertEqual(to_seconds(None), 0.0)
        self.assertEqual(to_seconds(1.5), 1.5)

    def test_to_samples_uses_sample_rate(self):
        self.assertEqual(to_samples(1.0), 48000)
        self.assertEqual(to_samples(None), 0)
        self.assertEqual(to_samples(0.05), 2400)
        self.assertEqual(NUDGE_STEP_SAMPLES, 2400)


class NudgeTest(unittest.TestCase):
    def test_nudge_right_per_track_shifts_following_items(self):
        engine = _engine(secondary=[make_item("a", 0, 10), make_item("b", 10, 20)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", NUDGE_STEP_SAMPLES, RIPPLE_MODE_PER_TRACK)
        self.assertTrue(result.ok)
        self.assertEqual(result.announcement, "nudge_success")
        self.assertEqual(result.new_start_seconds, 0.05)
        self.assertEqual(
            _positions(engine, SECONDARY_VIDEO_TRACK),
            [("a", 0.05, 10.05), ("b", 10.05, 20.05)],
        )
        self.assertEqual(
            result.ops,
            [("nudge_item", SECONDARY_VIDEO_TRACK, "a", 0.05, 0.05, 0.0)],
        )

    def test_nudge_left_per_track(self):
        engine = _engine(secondary=[make_item("a", 1, 11), make_item("b", 11, 21)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", -NUDGE_STEP_SAMPLES, RIPPLE_MODE_PER_TRACK)
        self.assertTrue(result.ok)
        self.assertEqual(
            _positions(engine, SECONDARY_VIDEO_TRACK),
            [("a", 0.95, 10.95), ("b", 10.95, 20.95)],
        )

    def test_nudge_left_at_timeline_start_blocked(self):
        engine = _engine(secondary=[make_item("a", 0, 10)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", -NUDGE_STEP_SAMPLES, RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن إزاحة العنصر أبعد من بداية الخط الزمني")
        self.assertEqual(_positions(engine, SECONDARY_VIDEO_TRACK), [("a", 0.0, 10.0)])

    def test_nudge_left_into_previous_item_blocked_and_restored(self):
        engine = _engine(secondary=[make_item("a", 0, 5), make_item("b", 5, 9)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "b", -3 * NUDGE_STEP_SAMPLES, RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن إزاحة العنصر لأنه سيتداخل مع عنصر آخر")
        self.assertEqual(_positions(engine, SECONDARY_VIDEO_TRACK), [("a", 0.0, 5.0), ("b", 5.0, 9.0)])

    def test_nudge_off_overlap_blocked(self):
        engine = _engine(secondary=[make_item("a", 0, 5), make_item("b", 5, 9)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", NUDGE_STEP_SAMPLES, RIPPLE_MODE_OFF)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن إزاحة العنصر لأنه سيتداخل مع عنصر آخر")
        self.assertEqual(_positions(engine, SECONDARY_VIDEO_TRACK), [("a", 0.0, 5.0), ("b", 5.0, 9.0)])

    def test_nudge_off_with_free_space_moves(self):
        engine = _engine(secondary=[make_item("a", 0, 5), make_item("b", 6, 10)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", NUDGE_STEP_SAMPLES, RIPPLE_MODE_OFF)
        self.assertTrue(result.ok)
        self.assertEqual(_positions(engine, SECONDARY_VIDEO_TRACK), [("a", 0.05, 5.05), ("b", 6.0, 10.0)])

    def test_nudge_all_tracks_shifts_other_tracks_and_opens_main_gap(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[make_item("a", 0, 5), make_item("b", 5, 10)],
            sfx=[make_item("s", 4, 6, item_type="sound_effect")],
            main_segments=[TimelineSegment("main.mp4", 0.0, 10.0)],
        )
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", NUDGE_STEP_SAMPLES, RIPPLE_MODE_ALL_TRACKS)
        self.assertTrue(result.ok)
        self.assertEqual(
            _positions(engine, SECONDARY_VIDEO_TRACK),
            [("a", 0.05, 5.05), ("b", 5.05, 10.05)],
        )
        self.assertEqual(_positions(engine, SOUND_EFFECTS_TRACK), [("s", 4.05, 6.05)])
        self.assertIn(("ripple_main_gap", 0.0, 0.05), result.ops)

    def test_nudge_all_tracks_left_ripples_main_range(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[make_item("a", 0.1, 5.1), make_item("b", 5.1, 10.1)],
            main_segments=[TimelineSegment("main.mp4", 0.0, 10.0)],
        )
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "a", -NUDGE_STEP_SAMPLES, RIPPLE_MODE_ALL_TRACKS)
        self.assertTrue(result.ok)
        self.assertEqual(
            _positions(engine, SECONDARY_VIDEO_TRACK),
            [("a", 0.05, 5.05), ("b", 5.05, 10.05)],
        )
        self.assertIn(("ripple_main_range", 0.05, 0.1), result.ops)

    def test_nudge_main_track_blocked(self):
        engine = _engine(secondary=[make_item("a", 0, 10)])
        result = nudge_item(engine, MAIN_VIDEO_TRACK, "anything", NUDGE_STEP_SAMPLES, RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن إزاحة عنصر على المقطع الرئيسي")

    def test_nudge_missing_item_reports_not_found(self):
        engine = _engine(secondary=[make_item("a", 0, 10)])
        result = nudge_item(engine, SECONDARY_VIDEO_TRACK, "zzz", NUDGE_STEP_SAMPLES, RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "العنصر المركّز غير موجود على الخط الزمني")


class MoveTest(unittest.TestCase):
    def test_move_same_track_blocked(self):
        engine = _engine(secondary=[make_item("a", 0, 5)])
        result = move_to_track(engine, SECONDARY_VIDEO_TRACK, SECONDARY_VIDEO_TRACK, "a", RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر إلى التراك نفسه")

    def test_move_rejected_media_type(self):
        engine = _engine(secondary=[make_item("a", 0, 5)])
        result = move_to_track(engine, SECONDARY_VIDEO_TRACK, SOUND_EFFECTS_TRACK, "a", RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر إلى هذا التراك")

    def test_move_between_overlays_per_track(self):
        engine = _engine(sfx=[make_item("s", 2, 5, item_type="sound_effect")])
        result = move_to_track(
            engine, SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK, "s", RIPPLE_MODE_PER_TRACK
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.track_key, BACKGROUND_AUDIO_TRACK)
        self.assertEqual(_positions(engine, SOUND_EFFECTS_TRACK), [])
        self.assertEqual(_positions(engine, BACKGROUND_AUDIO_TRACK), [("s", 2.0, 5.0)])
        self.assertEqual(
            result.ops,
            [
                ("remove_item", SOUND_EFFECTS_TRACK, "s"),
                ("shift_track", BACKGROUND_AUDIO_TRACK, 2.0, 3.0),
                ("shift_track", SOUND_EFFECTS_TRACK, 5.0, -3.0),
                ("insert_item", BACKGROUND_AUDIO_TRACK, make_item("s", 2, 5, item_type="sound_effect")),
            ],
        )

    def test_move_between_overlays_off(self):
        engine = _engine(sfx=[make_item("s", 2, 5, item_type="sound_effect")])
        result = move_to_track(
            engine, SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK, "s", RIPPLE_MODE_OFF
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.ops[0][0], "move_item")
        self.assertEqual(result.ops[0][1], SOUND_EFFECTS_TRACK)
        self.assertEqual(result.ops[0][2], BACKGROUND_AUDIO_TRACK)
        self.assertEqual(len(result.ops), 1)

    def test_move_overlay_per_track_straddle_blocked(self):
        engine = _engine(
            sfx=[make_item("s", 3, 6, item_type="sound_effect")],
            background_audio=[make_item("t", 1, 4, item_type="background_audio")],
        )
        result = move_to_track(
            engine, SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK, "s", RIPPLE_MODE_PER_TRACK
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر لأنه سيتداخل مع عنصر آخر على التراك الهدف")

    def test_move_overlay_off_overlap_blocked(self):
        engine = _engine(
            sfx=[make_item("s", 2, 5, item_type="sound_effect")],
            background_audio=[make_item("u", 2, 6, item_type="background_audio")],
        )
        result = move_to_track(
            engine, SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK, "s", RIPPLE_MODE_OFF
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر لأنه سيتداخل مع عنصر آخر على التراك الهدف")

    def test_move_to_main_from_secondary(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[make_item("a", 0, 5)],
            main_segments=[TimelineSegment("main.mp4", 0.0, 10.0)],
        )
        result = move_to_track(engine, SECONDARY_VIDEO_TRACK, MAIN_VIDEO_TRACK, "a", RIPPLE_MODE_PER_TRACK)
        self.assertTrue(result.ok)
        self.assertEqual(result.track_key, MAIN_VIDEO_TRACK)
        self.assertEqual(result.new_start_seconds, 0.0)
        self.assertEqual(len(engine.tracks[SECONDARY_VIDEO_TRACK].items), 0)
        ops = result.ops
        self.assertEqual(ops[0][0], "remove_item")
        self.assertEqual(ops[0][1], SECONDARY_VIDEO_TRACK)
        self.assertEqual(ops[1][0], "shift_track")
        self.assertEqual(ops[1][3], -5.0)
        self.assertEqual(ops[2][0], "insert_main_segment")
        self.assertEqual(ops[2][1], 0.0)
        self.assertEqual(result.segment_payload["path"], "a.mp4")
        self.assertEqual(result.segment_payload["start"], 0.0)
        self.assertEqual(result.segment_payload["end"], 5.0)

    def test_move_to_main_clamps_at_end_of_timeline(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[make_item("a", 12, 17)],
            main_segments=[TimelineSegment("main.mp4", 0.0, 10.0)],
        )
        result = move_to_track(engine, SECONDARY_VIDEO_TRACK, MAIN_VIDEO_TRACK, "a", RIPPLE_MODE_PER_TRACK)
        self.assertTrue(result.ok)
        self.assertEqual(result.new_start_seconds, 10.0)

    def test_move_to_main_rejects_audio(self):
        engine = _engine(sfx=[make_item("s", 0, 5, item_type="sound_effect")])
        result = move_to_track(engine, SOUND_EFFECTS_TRACK, MAIN_VIDEO_TRACK, "s", RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر إلى هذا التراك")

    def test_move_from_main_to_secondary(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[],
            main_segments=[
                TimelineSegment("b.mp4", 0.0, 5.0),
                TimelineSegment("c.mp4", 5.0, 10.0),
            ],
        )
        identifier = "main:c.mp4:5.0:10.0:1.0"
        result = move_to_track(engine, MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK, identifier, RIPPLE_MODE_PER_TRACK)
        self.assertTrue(result.ok)
        self.assertEqual(result.track_key, SECONDARY_VIDEO_TRACK)
        self.assertEqual(result.new_start_seconds, 5.0)
        self.assertEqual(result.length_seconds, 5.0)
        self.assertEqual(result.ops[0][0], "remove_main_segment")
        self.assertEqual(result.ops[1][0], "shift_track")
        self.assertEqual(result.ops[2][0], "insert_item")
        self.assertEqual(len(engine.tracks[SECONDARY_VIDEO_TRACK].items), 1)
        moved = engine.tracks[SECONDARY_VIDEO_TRACK].items[0]
        self.assertEqual(moved.timeline_start, 5.0)
        self.assertEqual(moved.timeline_end, 10.0)

    def test_move_from_main_rejects_audio_track(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            main_segments=[TimelineSegment("b.mp4", 0.0, 5.0)],
        )
        identifier = "main:b.mp4:0.0:5.0:1.0"
        result = move_to_track(engine, MAIN_VIDEO_TRACK, SOUND_EFFECTS_TRACK, identifier, RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر إلى هذا التراك")

    def test_move_from_main_per_track_straddle_blocked(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[make_item("t", 4, 7)],
            main_segments=[
                TimelineSegment("b.mp4", 0.0, 5.0),
                TimelineSegment("c.mp4", 5.0, 10.0),
            ],
        )
        identifier = "main:c.mp4:5.0:10.0:1.0"
        result = move_to_track(engine, MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK, identifier, RIPPLE_MODE_PER_TRACK)
        self.assertFalse(result.ok)
        self.assertEqual(result.announcement, "لا يمكن نقل العنصر لأنه سيتداخل مع عنصر آخر على التراك الهدف")

    def test_engine_find_item_locates_overlay_and_main(self):
        from video_maker.timeline import TimelineSegment

        engine = _engine(
            secondary=[make_item("a", 0, 5)],
            main_segments=[TimelineSegment("c.mp4", 5.0, 10.0)],
        )
        self.assertEqual(engine.find_item("a"), (SECONDARY_VIDEO_TRACK, "a"))
        self.assertEqual(engine.find_item("main:c.mp4:5.0:10.0:1.0"), (MAIN_VIDEO_TRACK, "main:c.mp4:5.0:10.0:1.0"))
        self.assertEqual(engine.find_item("zzz"), (None, None))


if __name__ == "__main__":
    unittest.main()
