import os
import sys
import unittest
import wave

sys.path.insert(0, os.path.abspath("."))

from video_maker.timeline import TimelineSegment
from video_maker.scrub_audio import (
    MAX_SCRUB_RATE,
    apply_fade,
    apply_tape_rate,
    apply_volume,
    build_scrub_samples,
    cache_width_for_step,
    decode_slice,
    scrub_rate_for_step,
    scrub_request_for_timeline_point,
)
from video_maker.scrub_audio import np


def make_segment(**kwargs):
    defaults = {
        "path": os.path.abspath("tests/test.wav"),
        "start": 5.0,
        "end": 15.0,
        "speed": 1.0,
        "audio_volume": 1.0,
        "audio_path": "",
        "audio_start": None,
    }
    defaults.update(kwargs)
    return TimelineSegment(**defaults)


class ScrubRequestResolutionTest(unittest.TestCase):
    def test_segment_resolution_maps_offset_speed_and_volume(self):
        timeline = [make_segment(speed=2.0, audio_volume=0.8, audio_start=1.0)]
        request = scrub_request_for_timeline_point(
            timeline=timeline,
            timeline_time=7.0,
            output_volume=0.5,
        )
        self.assertIsNotNone(request)
        # local time inside the clip: 5 + (7 - 0) * 2 = 19 -> clamped to end-0.001
        local_time = 14.999
        expected_center = (1.0 + (local_time - 5.0)) * 1000.0
        self.assertAlmostEqual(request["center_file_ms"], expected_center, delta=1.0)
        # window scales with segment speed so the real-time slice stays micro
        self.assertAlmostEqual(request["window_file_ms"], 40.0 * 2.0, delta=0.01)
        self.assertAlmostEqual(request["volume"], 0.5 * 0.8, delta=0.001)
        self.assertEqual(request["path"], os.path.abspath("tests/test.wav"))

    def test_override_resolution_uses_override_file(self):
        override = os.path.abspath("tests/test.wav")
        timeline = [make_segment(speed=2.0, audio_volume=0.9)]
        request = scrub_request_for_timeline_point(
            timeline=timeline,
            timeline_time=6.5,
            has_override=True,
            override_path=override,
            output_volume=1.0,
        )
        self.assertIsNotNone(request)
        self.assertEqual(request["path"], override)
        self.assertAlmostEqual(request["center_file_ms"], 6500.0, delta=0.01)
        # override maps 1:1 to timeline, no speed scaling
        self.assertAlmostEqual(request["window_file_ms"], 40.0, delta=0.01)

    def test_muted_track_returns_none(self):
        timeline = [make_segment(audio_volume=0.0)]
        request = scrub_request_for_timeline_point(
            timeline=timeline,
            timeline_time=6.0,
            output_volume=1.0,
        )
        self.assertIsNone(request)

    def test_empty_timeline_returns_none(self):
        request = scrub_request_for_timeline_point(
            timeline=[],
            timeline_time=6.0,
            output_volume=1.0,
        )
        self.assertIsNone(request)

    def test_missing_audio_file_returns_none(self):
        timeline = [make_segment(path="C:/definitely/missing/file.mp4")]
        request = scrub_request_for_timeline_point(
            timeline=timeline,
            timeline_time=6.0,
            output_volume=1.0,
        )
        self.assertIsNone(request)

    def test_rate_is_applied(self):
        timeline = [make_segment()]
        request = scrub_request_for_timeline_point(
            timeline=timeline,
            timeline_time=6.0,
            output_volume=1.0,
            rate=0.55,
        )
        self.assertIsNotNone(request)
        self.assertAlmostEqual(request["rate"], 0.55, delta=0.001)


class ScrubVelocityTest(unittest.TestCase):
    """سرعة الـ Scrubbing تتبع مقدار الحركة كما في REAPER."""

    def test_rate_follows_step_size(self):
        self.assertAlmostEqual(scrub_rate_for_step(1.0), 0.5, delta=0.001)
        self.assertAlmostEqual(scrub_rate_for_step(4.0), 1.0, delta=0.001)
        self.assertAlmostEqual(scrub_rate_for_step(9.0), 1.5, delta=0.001)
        self.assertAlmostEqual(scrub_rate_for_step(16.0), 2.0, delta=0.001)

    def test_rate_is_monotonic_and_clamped(self):
        previous = scrub_rate_for_step(0.1)
        for step in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 60.0, 200.0):
            rate = scrub_rate_for_step(step)
            self.assertGreaterEqual(rate, previous)
            self.assertLessEqual(rate, MAX_SCRUB_RATE)
            previous = rate

    def test_fine_moves_are_always_slow(self):
        self.assertAlmostEqual(scrub_rate_for_step(4.0, fine=True), 0.55, delta=0.001)

    def test_zero_step_is_normal_speed(self):
        self.assertAlmostEqual(scrub_rate_for_step(0.0), 1.0, delta=0.001)

    def test_cache_width_scales_with_step(self):
        small = cache_width_for_step(1000.0, 40.0)
        large = cache_width_for_step(4000.0, 40.0)
        self.assertGreater(large, small)
        capped = cache_width_for_step(100000.0, 40.0)
        self.assertLessEqual(capped, 6000.0)

    def test_request_contains_cache_width(self):
        timeline = [make_segment(speed=2.0)]
        request = scrub_request_for_timeline_point(
            timeline=timeline,
            timeline_time=6.0,
            output_volume=1.0,
            step_seconds=1.0,
        )
        self.assertIsNotNone(request)
        # step of 1s at 2x speed -> 2000ms of file per press
        expected = max(1000.0, min(6000.0, 2000.0 * 2.0 + 80.0 * 2.0))
        self.assertAlmostEqual(request["cache_file_ms"], expected, delta=0.01)


class ScrubSampleProcessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if np is None:
            raise unittest.SkipTest("numpy not available")

    def test_apply_volume_scales_and_clips(self):
        samples = np.full((100, 2), 0.8, dtype=np.float32)
        result = apply_volume(samples, 0.5)
        self.assertAlmostEqual(float(result[0, 0]), 0.4, delta=0.001)
        clipped = apply_volume(np.full((10, 2), 3.0, dtype=np.float32), 0.5)
        self.assertAlmostEqual(float(clipped[0, 0]), 1.0, delta=0.001)

    def test_apply_tape_rate_stretches_when_slower(self):
        samples = np.ones((2000, 2), dtype=np.float32)
        result = apply_tape_rate(samples, 0.5)
        self.assertEqual(len(result), 4000)
        result2 = apply_tape_rate(samples, 1.0)
        self.assertIs(result2, samples)

    def test_apply_fade_ramps_edges_to_zero(self):
        samples = np.ones((2000, 2), dtype=np.float32)
        result = apply_fade(samples, 3.0)
        self.assertAlmostEqual(float(result[0, 0]), 0.0, delta=0.01)
        self.assertAlmostEqual(float(result[-1, 0]), 0.0, delta=0.01)
        middle = len(result) // 2
        self.assertAlmostEqual(float(result[middle, 0]), 1.0, delta=0.01)

    def test_build_scrub_samples_returns_framed_audio(self):
        if not os.path.exists(os.path.abspath("tests/test.wav")):
            self.skipTest("tests/test.wav missing")
        request = {
            "path": os.path.abspath("tests/test.wav"),
            "center_file_ms": 500.0,
            "window_file_ms": 40.0,
            "volume": 0.8,
            "rate": 1.0,
        }
        samples = build_scrub_samples(request)
        self.assertIsNotNone(samples)
        self.assertGreater(len(samples), 0)
        self.assertEqual(samples.shape[1], 2)

    def test_decode_slice_within_file_bounds(self):
        path = os.path.abspath("tests/test.wav")
        if not os.path.exists(path):
            self.skipTest("tests/test.wav missing")
        try:
            with wave.open(path, "rb") as wf:
                duration_ms = wf.getnframes() / float(wf.getframerate()) * 1000.0
        except Exception:
            self.skipTest("cannot read tests/test.wav")
        if duration_ms < 100:
            self.skipTest("tests/test.wav too short")
        center = min(500.0, duration_ms / 2.0)
        samples = decode_slice(path, center, 40.0)
        self.assertIsNotNone(samples)
        self.assertGreater(len(samples), 0)


if __name__ == "__main__":
    unittest.main()
