import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("."))

from video_maker import program_modes
from video_maker.player import VideoPlayer


class RippleModeMenuHandlersTest(unittest.TestCase):
    def _frame(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.ripple_mode = "per_track"
        frame.muted_tracks = set()
        frame.current_track = "main_video"
        frame.require_open_file = lambda: True
        frame.capture_edit_state = lambda: {"ripple_mode": frame.ripple_mode, "muted_tracks": set(frame.muted_tracks)}
        frame.record_edit = lambda *args: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.refresh_menu_bar = lambda: None
        frame.say = lambda *args, **kwargs: None
        frame.track_announcement = lambda: "track announcement"
        return frame

    def test_set_ripple_mode_value_updates_and_persists(self):
        frame = self._frame()
        calls = []
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.player.set_ripple_mode", lambda mode: calls.append(mode)
        ):
            frame.OnSetRippleModeValue("all_tracks")
        self.assertEqual(frame.ripple_mode, "all_tracks")
        self.assertEqual(calls, ["all_tracks"])

    def test_set_ripple_mode_value_ignored_in_normal_mode(self):
        frame = self._frame()
        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE):
            frame.OnSetRippleModeValue("off")
        self.assertEqual(frame.ripple_mode, "per_track")

    def test_set_ripple_mode_value_normalizes_unknown_mode(self):
        frame = self._frame()
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.player.set_ripple_mode"
        ):
            frame.OnSetRippleModeValue("bogus")
        self.assertEqual(frame.ripple_mode, "per_track")

    def test_toggle_ripple_mode_cycles_and_refreshes_menu_bar(self):
        frame = self._frame()
        refreshed = []
        frame.refresh_menu_bar = lambda: refreshed.append(True)
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.player.set_ripple_mode"
        ):
            frame.OnToggleRippleMode()
        self.assertEqual(frame.ripple_mode, "all_tracks")
        self.assertEqual(refreshed, [True])

    def test_toggle_track_mute_adds_track_to_muted_set(self):
        frame = self._frame()
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnToggleTrackMuteValue("sound_effects")
        self.assertIn("sound_effects", frame.muted_tracks)

    def test_toggle_track_mute_removes_existing_mute(self):
        frame = self._frame()
        frame.muted_tracks = {"background_audio"}
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnToggleTrackMuteValue("background_audio")
        self.assertNotIn("background_audio", frame.muted_tracks)

    def test_toggle_track_mute_requires_open_file(self):
        frame = self._frame()
        frame.require_open_file = lambda: False
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnToggleTrackMuteValue("text")
        self.assertNotIn("text", frame.muted_tracks)

    def test_speak_editor_status_announces_ripple_and_mutes(self):
        frame = self._frame()
        frame.ripple_mode = "all_tracks"
        frame.muted_tracks = {"sound_effects"}
        spoken = []
        frame.say = lambda message, **kwargs: spoken.append(message)
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.localization.get_language", lambda default="ar", language="ar": "ar"
        ):
            frame.OnSpeakEditorStatus()
        self.assertTrue(spoken)
        self.assertIn("كل التراكات", spoken[0])
        self.assertIn("المؤثرات الصوتية", spoken[0])
        self.assertIn("track announcement", spoken[0])

    def test_speak_editor_status_announces_no_muted_tracks(self):
        frame = self._frame()
        frame.muted_tracks = set()
        spoken = []
        frame.say = lambda message, **kwargs: spoken.append(message)
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.localization.get_language", lambda default="ar", language="ar": "ar"
        ):
            frame.OnSpeakEditorStatus()
        self.assertIn("لا توجد تراكات مكتومة", spoken[0])


if __name__ == "__main__":
    unittest.main()
