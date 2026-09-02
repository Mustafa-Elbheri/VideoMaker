import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("."))

from video_maker import localization, tracks
from video_maker.player import VideoPlayer
from video_maker.program_modes import NORMAL_MODE, PROFESSIONAL_MODE
from video_maker.tracks import (
    BACKGROUND_AUDIO_TRACK,
    DEFAULT_TRACK,
    MAIN_VIDEO_TRACK,
    SECONDARY_VIDEO_TRACK,
    SOUND_EFFECTS_TRACK,
    TEXT_TRACK,
)


def _ar():
    return patch.object(localization, "get_language", lambda default="ar", language="ar": "ar")


def _new_frame():
    frame = VideoPlayer.__new__(VideoPlayer)
    frame.timeline = []
    frame.visual_items = []
    frame.background_audio_items = []
    frame.b_roll_items = []
    frame.sound_effects_items = []
    frame.current_track = DEFAULT_TRACK
    frame.current_time = 0.0
    frame.require_open_file = lambda: True
    frame.say = lambda *args, **kwargs: None
    frame.refresh_menu_bar = lambda: None
    return frame


class TrackDefinitionsTest(unittest.TestCase):
    def test_five_tracks_in_order(self):
        self.assertEqual([item["key"] for item in tracks.TRACKS], [
            MAIN_VIDEO_TRACK,
            SECONDARY_VIDEO_TRACK,
            SOUND_EFFECTS_TRACK,
            BACKGROUND_AUDIO_TRACK,
            TEXT_TRACK,
        ])

    def test_track_media_types_match_requirements(self):
        self.assertEqual(tracks.track_media_type(MAIN_VIDEO_TRACK), "video")
        self.assertEqual(tracks.track_media_type(SECONDARY_VIDEO_TRACK), "video")
        self.assertEqual(tracks.track_media_type(SOUND_EFFECTS_TRACK), "audio")
        self.assertEqual(tracks.track_media_type(BACKGROUND_AUDIO_TRACK), "audio")
        self.assertEqual(tracks.track_media_type(TEXT_TRACK), "text")

    def test_track_channels_match_independent_storage(self):
        self.assertEqual(tracks.track_channel(MAIN_VIDEO_TRACK), "timeline")
        self.assertEqual(tracks.track_channel(SECONDARY_VIDEO_TRACK), "b_roll")
        self.assertEqual(tracks.track_channel(SOUND_EFFECTS_TRACK), "sound_effects")
        self.assertEqual(tracks.track_channel(BACKGROUND_AUDIO_TRACK), "background_audio")
        self.assertEqual(tracks.track_channel(TEXT_TRACK), "visual_text")

    def test_normalize_track_falls_back_to_default(self):
        self.assertEqual(tracks.normalize_track("unknown"), DEFAULT_TRACK)
        self.assertEqual(tracks.normalize_track(None), DEFAULT_TRACK)
        self.assertEqual(tracks.normalize_track(MAIN_VIDEO_TRACK), MAIN_VIDEO_TRACK)

    def test_track_index_and_at(self):
        self.assertEqual(tracks.track_index(MAIN_VIDEO_TRACK), 0)
        self.assertEqual(tracks.track_index(TEXT_TRACK), 4)
        self.assertEqual(tracks.track_at(0), MAIN_VIDEO_TRACK)
        self.assertEqual(tracks.track_at(4), TEXT_TRACK)
        self.assertEqual(tracks.track_at(99), DEFAULT_TRACK)

    def test_track_navigation_wraps_around(self):
        self.assertEqual(tracks.next_track(TEXT_TRACK), MAIN_VIDEO_TRACK)
        self.assertEqual(tracks.previous_track(MAIN_VIDEO_TRACK), TEXT_TRACK)
        self.assertEqual(tracks.next_track(SECONDARY_VIDEO_TRACK), SOUND_EFFECTS_TRACK)
        self.assertEqual(tracks.previous_track(BACKGROUND_AUDIO_TRACK), SOUND_EFFECTS_TRACK)

    def test_tracks_accepting_lists(self):
        self.assertEqual(tracks.tracks_accepting("video"), [MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK])
        self.assertEqual(tracks.tracks_accepting("audio"), [SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK])
        self.assertEqual(tracks.tracks_accepting("text"), [TEXT_TRACK])
        self.assertEqual(tracks.tracks_accepting("image"), [SECONDARY_VIDEO_TRACK])

    def test_track_labels_and_ordinals(self):
        self.assertEqual(tracks.track_label(MAIN_VIDEO_TRACK), "المقطع الرئيسي")
        self.assertEqual(tracks.track_label(TEXT_TRACK), "النصوص")
        self.assertEqual(tracks.track_ordinal(MAIN_VIDEO_TRACK), "الأول")
        self.assertEqual(tracks.track_ordinal(TEXT_TRACK), "الخامس")


class TrackNavigationTest(unittest.TestCase):
    def test_next_track_moves_and_announces(self):
        frame = _new_frame()
        frame.b_roll_items = [object(), object()]
        frame.current_track = MAIN_VIDEO_TRACK
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)
        with _ar():
            frame.OnNextTrack()
        self.assertEqual(frame.current_track, SECONDARY_VIDEO_TRACK)
        self.assertEqual(spoken, ["التراك 2 المقطع الثانوي، عدد المقاطع 2"])

    def test_previous_track_moves_and_announces(self):
        frame = _new_frame()
        frame.current_track = TEXT_TRACK
        frame.visual_items = [{"type": "text"}, {"type": "text"}, {"type": "image"}]
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)
        with _ar():
            frame.OnPreviousTrack()
        self.assertEqual(frame.current_track, BACKGROUND_AUDIO_TRACK)
        self.assertEqual(spoken, ["التراك 4 الخلفية الصوتية، لا يحتوي على أصوات"])

    def test_wrap_from_last_track_to_first(self):
        frame = _new_frame()
        frame.current_track = TEXT_TRACK
        frame.require_open_file = lambda: True
        with _ar():
            frame.OnNextTrack()
        self.assertEqual(frame.current_track, MAIN_VIDEO_TRACK)

    def test_navigation_requires_open_file(self):
        frame = _new_frame()
        frame.current_track = MAIN_VIDEO_TRACK
        frame.require_open_file = lambda: False
        with _ar():
            frame.OnNextTrack()
        self.assertEqual(frame.current_track, MAIN_VIDEO_TRACK)

    def test_sound_effects_track_counts_sound_effects_items(self):
        frame = _new_frame()
        frame.current_track = SOUND_EFFECTS_TRACK
        frame.sound_effects_items = [{"path": "a.wav"}, {"path": "b.wav"}]
        frame.background_audio_items = [{"path": "x.wav"}]
        with _ar():
            self.assertEqual(frame.track_content_text(), "عدد الأصوات 2")

    def test_background_audio_track_counts_background_audio_items(self):
        frame = _new_frame()
        frame.current_track = BACKGROUND_AUDIO_TRACK
        frame.sound_effects_items = [{"path": "a.wav"}, {"path": "b.wav"}]
        frame.background_audio_items = [{"path": "x.wav"}, {"path": "y.wav"}, {"path": "z.wav"}]
        with _ar():
            self.assertEqual(frame.track_content_text(), "عدد الأصوات 3")

    def test_secondary_video_track_counts_b_roll_items(self):
        frame = _new_frame()
        frame.current_track = SECONDARY_VIDEO_TRACK
        frame.b_roll_items = [{"path": "a.mp4"}]
        with _ar():
            self.assertEqual(frame.track_content_text(), "عدد المقاطع 1")

    def test_text_track_counts_only_text_items(self):
        frame = _new_frame()
        frame.current_track = TEXT_TRACK
        frame.visual_items = [{"type": "text"}, {"type": "text"}, {"type": "image"}, {"type": "video"}]
        with _ar():
            self.assertEqual(frame.track_content_text(), "عدد النصوص 2")

    def test_empty_content_messages(self):
        frame = _new_frame()
        with _ar():
            frame.current_track = MAIN_VIDEO_TRACK
            self.assertEqual(frame.track_content_text(), "لا يحتوي على مقاطع")
            frame.current_track = BACKGROUND_AUDIO_TRACK
            self.assertEqual(frame.track_content_text(), "لا يحتوي على أصوات")
            frame.current_track = TEXT_TRACK
            self.assertEqual(frame.track_content_text(), "لا يحتوي على نصوص")


class TrackTypeValidationTest(unittest.TestCase):
    def test_professional_video_tracks_accept_only_video(self):
        frame = _new_frame()
        frame.say = lambda *args, **kwargs: None
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            for track in (MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK):
                frame.current_track = track
                self.assertTrue(frame.track_accepts_media("video"))
                self.assertFalse(frame.track_accepts_media("audio"))
                self.assertFalse(frame.track_accepts_media("text"))

    def test_professional_audio_tracks_accept_only_audio(self):
        frame = _new_frame()
        frame.say = lambda *args, **kwargs: None
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            for track in (SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK):
                frame.current_track = track
                self.assertTrue(frame.track_accepts_media("audio"))
                self.assertFalse(frame.track_accepts_media("video"))
                self.assertFalse(frame.track_accepts_media("text"))

    def test_professional_text_track_accepts_only_text(self):
        frame = _new_frame()
        frame.say = lambda *args, **kwargs: None
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.current_track = TEXT_TRACK
            self.assertTrue(frame.track_accepts_media("text"))
            self.assertFalse(frame.track_accepts_media("video"))
            self.assertFalse(frame.track_accepts_media("audio"))

    def test_normal_mode_never_rejects(self):
        frame = _new_frame()
        frame.say = lambda *args, **kwargs: None
        with patch("video_maker.player.get_program_mode", lambda: NORMAL_MODE):
            for track in (MAIN_VIDEO_TRACK, SOUND_EFFECTS_TRACK, TEXT_TRACK):
                frame.current_track = track
                self.assertTrue(frame.track_accepts_media("video"))
                self.assertTrue(frame.track_accepts_media("audio"))
                self.assertTrue(frame.track_accepts_media("text"))

    def test_rejection_message_points_to_compatible_tracks(self):
        frame = _new_frame()
        with _ar():
            frame.current_track = TEXT_TRACK
            self.assertEqual(
                frame.track_rejection_message("video"),
                "هذا التراك لا يقبل الفيديو، انتقل إلى التراك 1 أو 2 لإدراج الفيديو",
            )
            frame.current_track = MAIN_VIDEO_TRACK
            self.assertEqual(
                frame.track_rejection_message("audio"),
                "هذا التراك لا يقبل الصوت، انتقل إلى التراك 3 أو 4 لإدراج الصوت",
            )
            frame.current_track = MAIN_VIDEO_TRACK
            self.assertEqual(
                frame.track_rejection_message("text"),
                "هذا التراك لا يقبل النصوص، انتقل إلى التراك 5 لإدراج النصوص",
            )

    def test_rejection_is_spoken_once(self):
        frame = _new_frame()
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE), _ar():
            frame.current_track = MAIN_VIDEO_TRACK
            self.assertFalse(frame.track_accepts_media("audio"))
        self.assertEqual(len(spoken), 1)
        self.assertIn("الصوت", spoken[0])


class InsertionGuardIntegrationTest(unittest.TestCase):
    def test_on_insert_text_rejects_on_video_track(self):
        frame = _new_frame()
        frame.has_video = lambda: True
        frame.current_track = MAIN_VIDEO_TRACK
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)

        def fail_selection(*args, **kwargs):
            self.fail("insertion proceeded on an incompatible track")

        frame.selected_effect_range = fail_selection
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertText()
        self.assertTrue(spoken)

    def test_on_insert_background_audio_rejects_on_video_track(self):
        frame = _new_frame()
        frame.has_video = lambda: True
        frame.current_track = MAIN_VIDEO_TRACK
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)

        def fail_selection(*args, **kwargs):
            self.fail("insertion proceeded on an incompatible track")

        frame.selected_effect_range = fail_selection
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertBackgroundAudio()
        self.assertTrue(spoken)

    def test_on_add_video_rejects_on_text_track(self):
        frame = _new_frame()
        frame.has_video = lambda: True
        frame.current_track = TEXT_TRACK
        frame.media_kind = "video"
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnAddVideo()
        self.assertTrue(spoken)
        self.assertEqual(frame.timeline, [])

    def test_insert_text_allows_on_text_track(self):
        frame = _new_frame()
        frame.has_video = lambda: True
        frame.current_track = TEXT_TRACK
        frame.say = lambda text, **kwargs: None

        class Reached(Exception):
            pass

        def reached_selection(*args, **kwargs):
            raise Reached()

        frame.selected_effect_range = reached_selection
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            with self.assertRaises(Reached):
                frame.OnInsertText()

    def test_insert_background_audio_allows_on_audio_track(self):
        frame = _new_frame()
        frame.has_video = lambda: True
        frame.current_track = SOUND_EFFECTS_TRACK
        frame.say = lambda text, **kwargs: None

        class Reached(Exception):
            pass

        def reached_selection(*args, **kwargs):
            raise Reached()

        frame.selected_effect_range = reached_selection
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            with self.assertRaises(Reached):
                frame.OnInsertBackgroundAudio()


class TrackItemInsertionTest(unittest.TestCase):
    def _frame(self):
        frame = _new_frame()
        frame.transition_name = "fade"
        frame.last_insert_end = 0.0
        frame.current_time = 5.0
        frame.is_dirty = False
        frame.capture_edit_state = lambda: {}
        frame.record_edit = lambda *args, **kwargs: None
        frame.reload_current_position = lambda *args, **kwargs: None
        frame.add_edit_point = lambda *args, **kwargs: None
        frame.selected_effect_range = lambda: (1.0, 5.0)
        return frame

    def test_insert_secondary_video_appends_to_b_roll(self):
        frame = self._frame()
        frame.current_track = SECONDARY_VIDEO_TRACK
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)
        with patch("video_maker.player.ask_video_path", lambda: "C:\\clips\\b.mp4"), patch(
            "video_maker.player.get_video_duration", lambda path: 3.0
        ), _ar():
            frame.insert_secondary_video()
        self.assertEqual(len(frame.b_roll_items), 1)
        self.assertEqual(frame.b_roll_items[0]["path"], "C:\\clips\\b.mp4")
        self.assertEqual(frame.b_roll_items[0]["type"], "video")
        self.assertEqual(frame.b_roll_items[0]["start"], 5.0)
        self.assertEqual(frame.b_roll_items[0]["end"], 8.0)
        self.assertEqual(frame.timeline, [])
        self.assertEqual(spoken, ["تم إدراج المقطع الثانوي"])

    def test_insert_secondary_video_cancel_does_nothing(self):
        frame = self._frame()
        with patch("video_maker.player.ask_video_path", lambda: ""):
            frame.insert_secondary_video()
        self.assertEqual(frame.b_roll_items, [])

    def test_insert_sound_effect_appends_to_sound_effects(self):
        frame = self._frame()
        frame.current_track = SOUND_EFFECTS_TRACK
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)
        options = {
            "path": "C:\\sounds\\boom.wav",
            "volume": 0.8,
            "trim_silence": False,
        }
        frame.InsertSoundEffect(options)
        self.assertEqual(len(frame.sound_effects_items), 1)
        item = frame.sound_effects_items[0]
        self.assertEqual(item["path"], "C:\\sounds\\boom.wav")
        self.assertEqual(item["type"], "sound_effect")
        self.assertEqual(item["start"], 1.0)
        self.assertEqual(item["end"], 5.0)
        self.assertEqual(item["volume"], 0.8)
        self.assertEqual(frame.background_audio_items, [])
        self.assertEqual(spoken, ["تم إدراج المؤثر الصوتي"])

    def test_insert_background_audio_still_uses_background_audio_items(self):
        frame = self._frame()
        frame.current_track = BACKGROUND_AUDIO_TRACK
        options = {
            "path": "C:\\sounds\\music.wav",
            "volume": 0.4,
            "trim_silence": False,
        }
        frame.InsertBackgroundAudio(options)
        self.assertEqual(len(frame.background_audio_items), 1)
        self.assertEqual(frame.background_audio_items[0]["type"], "background_audio")
        self.assertEqual(frame.sound_effects_items, [])


class OnInsertTrackItemTest(unittest.TestCase):
    def _frame(self):
        frame = _new_frame()
        frame.calls = []
        frame.OnInsertText = lambda event=None: frame.calls.append("insert_text")
        frame._insert_secondary_at_playhead = lambda at_time: frame.calls.append("insert_secondary")
        frame._insert_audio_at_playhead = lambda at_time: frame.calls.append("insert_audio")
        return frame

    def test_main_video_track_routes_to_playhead_insert(self):
        frame = self._frame()
        frame.current_track = MAIN_VIDEO_TRACK
        frame._insert_main_video_at_playhead = lambda at_time: frame.calls.append("insert_main_video")
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertTrackItem()
        self.assertEqual(frame.calls, ["insert_main_video"])

    def test_secondary_video_track_routes_to_playhead_insert(self):
        frame = self._frame()
        frame.current_track = SECONDARY_VIDEO_TRACK
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertTrackItem()
        self.assertEqual(frame.calls, ["insert_secondary"])

    def test_sound_effects_track_routes_to_playhead_insert(self):
        frame = self._frame()
        frame.current_track = SOUND_EFFECTS_TRACK
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertTrackItem()
        self.assertEqual(frame.calls, ["insert_audio"])

    def test_background_audio_track_routes_to_playhead_insert(self):
        frame = self._frame()
        frame.current_track = BACKGROUND_AUDIO_TRACK
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertTrackItem()
        self.assertEqual(frame.calls, ["insert_audio"])

    def test_text_track_routes_to_insert_text(self):
        frame = self._frame()
        frame.current_track = TEXT_TRACK
        with patch("video_maker.player.get_program_mode", lambda: PROFESSIONAL_MODE):
            frame.OnInsertTrackItem()
        self.assertEqual(frame.calls, ["insert_text"])

    def test_normal_mode_does_nothing(self):
        frame = self._frame()
        frame.current_track = SECONDARY_VIDEO_TRACK
        with patch("video_maker.player.get_program_mode", lambda: NORMAL_MODE):
            frame.OnInsertTrackItem()
        self.assertEqual(frame.calls, [])


class ToggleResetTest(unittest.TestCase):
    def test_toggling_mode_resets_current_track(self):
        frame = _new_frame()
        frame.current_track = TEXT_TRACK
        with patch("video_maker.player.toggle_program_mode", lambda: PROFESSIONAL_MODE), _ar():
            frame.OnToggleProgramMode()
        self.assertEqual(frame.current_track, DEFAULT_TRACK)


if __name__ == "__main__":
    unittest.main()
