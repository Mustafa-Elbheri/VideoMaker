import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("."))

from video_maker import localization, program_modes, settings
from video_maker.mpv_player import MEDIASTATE_PAUSED
from video_maker.player import VideoPlayer


class ProgramModePersistenceTest(unittest.TestCase):
    def test_program_mode_defaults_to_normal_when_not_saved(self):
        with patch.object(program_modes, "read_preferences", lambda: {}):
            self.assertEqual(program_modes.get_program_mode(), program_modes.NORMAL_MODE)

    def test_normal_mode_seek_step_defaults_to_five_seconds(self):
        with patch.object(settings, "read_preferences", lambda: {}):
            self.assertEqual(settings.read_normal_seek_step(), 5000)

    def test_program_mode_is_saved_and_read_from_preferences(self):
        store = {}

        with patch.object(program_modes, "read_preferences", lambda: dict(store)), patch.object(
            program_modes, "update_preferences", lambda **updates: store.update(updates)
        ):
            program_modes.set_program_mode(program_modes.PROFESSIONAL_MODE)
            self.assertEqual(store["program_mode"], program_modes.PROFESSIONAL_MODE)
            self.assertEqual(program_modes.get_program_mode(), program_modes.PROFESSIONAL_MODE)

            program_modes.set_program_mode("unknown")
            self.assertEqual(store["program_mode"], program_modes.NORMAL_MODE)
            self.assertEqual(program_modes.get_program_mode(), program_modes.NORMAL_MODE)

    def test_program_mode_strings_are_translated(self):
        strings = [
            "الإعدادات",
            "إعدادات البرنامج",
            "تبويبات إعدادات البرنامج",
            "إعدادات عامة",
            "إعدادات النطق",
            "عام",
            "النطق",
            "وضع البرنامج",
            "الوضع العادي",
            "الوضع الاحترافي",
            "التبديل بين الوضع العادي والاحترافي",
            "تم التبديل إلى",
            "رفع مستوى الماستر بدرجة ديسيبل",
            "خفض مستوى الماستر بدرجة ديسيبل",
            "سيتم إضافة الإعدادات هنا",
            "التراك السابق",
            "التراك التالي",
            "التراك {number} {label}، {content}",
            "لا يحتوي على مقاطع",
            "عدد المقاطع {count}",
            "لا يحتوي على أصوات",
            "عدد الأصوات {count}",
            "لا يحتوي على نصوص",
            "عدد النصوص {count}",
            "هذا التراك لا يقبل {type}، انتقل إلى التراك {numbers} لإدراج {type}",
            "هذا التراك لا يقبل {type}",
            "الفيديو",
            "الصوت",
            "النصوص",
            "المقطع الرئيسي",
            "المقطع الثانوي",
            "المؤثرات الصوتية",
            "الخلفية الصوتية",
        ]
        for language in ("en", "fr"):
            with patch.object(localization, "get_language", lambda default="ar", language=language: language):
                untranslated = [text for text in strings if localization.tr(text) == text]
            self.assertEqual(untranslated, [])

    def test_toggle_program_mode_switches_between_modes(self):
        store = {}

        with patch.object(program_modes, "read_preferences", lambda: dict(store)), patch.object(
            program_modes, "update_preferences", lambda **updates: store.update(updates)
        ):
            next_mode = program_modes.toggle_program_mode()
            self.assertEqual(next_mode, program_modes.PROFESSIONAL_MODE)
            self.assertEqual(program_modes.get_program_mode(), program_modes.PROFESSIONAL_MODE)

            next_mode = program_modes.toggle_program_mode()
            self.assertEqual(next_mode, program_modes.NORMAL_MODE)
            self.assertEqual(program_modes.get_program_mode(), program_modes.NORMAL_MODE)


class ProgramModePlaybackTest(unittest.TestCase):
    def test_space_uses_pause_without_announcement_in_normal_mode(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        calls = []
        frame.OnPause = lambda event=None, announce_pause=True: calls.append((event, announce_pause))

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE):
            frame.OnPlayPause("event")

        self.assertEqual(calls, [("event", False)])

    def test_space_keeps_return_playback_behavior_in_professional_mode(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.timeline = [object()]
        frame.has_video = lambda: True
        frame.timeline_duration = lambda: 10.0
        frame.current_time = 3.0
        frame.current_segment_index = 0
        frame.active_media_path = "source.mp4"
        frame.playback_requested = False
        frame.media_ctrl = type("MediaCtrl", (), {"GetState": lambda self: MEDIASTATE_PAUSED})()
        frame.selected_playback_range = "selection"
        frame.skipped_playback_range = "skip"
        load_calls = []
        frame.load_timeline_time = lambda time, play: load_calls.append((time, play))

        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnPlayPause()

        self.assertEqual(frame.playback_return_position, 3.0)
        self.assertTrue(frame.playback_requested)
        self.assertIsNone(frame.selected_playback_range)
        self.assertIsNone(frame.skipped_playback_range)
        self.assertEqual(load_calls, [(3.0, True)])

    def test_arrow_navigation_uses_normal_step_in_normal_mode(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.seek_step = 250
        frame.normal_seek_step = 5000
        frame.require_open_file = lambda: True
        calls = []
        frame.seek_timeline_by = lambda delta: calls.append(delta)

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE):
            frame.OnForward()
            frame.OnRewind()

        self.assertEqual(calls, [5.0, -5.0])

    def test_arrow_navigation_uses_zoom_in_professional_mode(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.seek_step = 250
        frame.normal_seek_step = 5000
        frame.pixels_per_second = 160
        frame.require_open_file = lambda: True
        calls = []
        frame.seek_timeline_by = lambda delta: calls.append(delta)

        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnForward()
            frame.OnRewind()

        self.assertEqual(calls, [0.006, -0.006])

    def test_professional_mode_default_zoom_moves_about_12ms(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.pixels_per_second = settings.DEFAULT_PIXELS_PER_SECOND
        frame.say = lambda *args, **kwargs: None
        frame.normal_seek_step = 5000
        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            step_ms = frame.current_seek_step_ms()
        self.assertGreaterEqual(step_ms, 10)
        self.assertLessEqual(step_ms, 15)

    def test_normal_mode_arrow_press_never_uses_realtime_scrub(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.timeline = []
        frame.current_time = 1.0
        frame.playback_requested = False
        frame.timeline_duration = lambda: 20.0
        load_calls = []
        played_sounds = []
        stop_calls = []

        def fail_scrub(*args, **kwargs):
            self.fail("normal-mode arrow navigation must not use realtime scrub audio")

        frame.scrub_preview_slice = fail_scrub
        frame.stop_scrub_playback = lambda: stop_calls.append(True)
        frame.load_timeline_time = lambda time_value, play=False: load_calls.append((time_value, play))
        frame._last_audio_tick_time = 0

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE), patch(
            "video_maker.ui_sounds.play_ui_sound", lambda name: played_sounds.append(name) or True
        ):
            frame.seek_timeline_by(2.0)

        self.assertEqual(frame.current_time, 3.0)
        self.assertEqual(load_calls, [(3.0, False)])
        self.assertEqual(stop_calls, [True])
        self.assertEqual(played_sounds, [])

    def test_normal_mode_repeated_forward_uses_five_second_steps_and_tape_sound(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.timeline = []
        frame.seek_step = 250
        frame.normal_seek_step = 5000
        frame.current_time = 0.0
        frame.playback_requested = False
        frame.require_open_file = lambda: True
        frame.timeline_duration = lambda: 20.0
        load_calls = []
        played_sounds = []

        def fail_scrub(*args, **kwargs):
            self.fail("normal-mode arrow navigation must not use realtime scrub audio")

        frame.scrub_preview_slice = fail_scrub
        frame.stop_scrub_playback = lambda: None
        frame.load_timeline_time = lambda time_value, play=False: load_calls.append((time_value, play))

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE), patch(
            "video_maker.ui_sounds.play_ui_sound", lambda name: played_sounds.append(name) or True
        ):
            frame.OnForward()
            frame.OnForward()

        self.assertEqual(frame.current_time, 10.0)
        self.assertEqual(load_calls, [(5.0, False), (10.0, False)])
        self.assertEqual(played_sounds, ["tape_scrub.wav"])

    def test_normal_mode_long_arrow_press_uses_tape_sound_without_realtime_scrub(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.timeline = []
        frame.current_time = 1.0
        frame.playback_requested = False
        frame.timeline_duration = lambda: 20.0
        load_calls = []
        played_sounds = []

        def fail_scrub(*args, **kwargs):
            self.fail("normal-mode long press must not use realtime scrub audio")

        frame.scrub_preview_slice = fail_scrub
        frame.stop_scrub_playback = lambda: None
        frame.load_timeline_time = lambda time_value, play=False: load_calls.append((time_value, play))
        frame._last_audio_tick_time = time.monotonic()

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE), patch(
            "video_maker.ui_sounds.play_ui_sound", lambda name: played_sounds.append(name) or True
        ):
            frame.seek_timeline_by(2.0)

        self.assertEqual(frame.current_time, 3.0)
        self.assertEqual(load_calls, [(3.0, False)])
        self.assertEqual(played_sounds, ["tape_scrub.wav"])

    def test_professional_mode_long_arrow_press_keeps_realtime_scrub(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.timeline = []
        frame.current_time = 1.0
        frame.playback_requested = False
        frame.timeline_duration = lambda: 20.0
        load_calls = []
        scrub_calls = []
        played_sounds = []
        frame.scrub_preview_slice = lambda *args, **kwargs: scrub_calls.append((args, kwargs)) or True
        frame.load_timeline_time = lambda time_value, play=False: load_calls.append((time_value, play))
        frame._last_audio_tick_time = time.monotonic()

        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.ui_sounds.play_ui_sound", lambda name: played_sounds.append(name) or True
        ):
            frame.seek_timeline_by(2.0)

        self.assertEqual(frame.current_time, 3.0)
        self.assertEqual(load_calls, [(3.0, False)])
        self.assertEqual(len(scrub_calls), 1)
        self.assertEqual(played_sounds, [])

    def test_normal_mode_navigation_step_controls_use_normal_scale(self):
        store = {}
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.normal_seek_step = 5000
        frame.require_open_file = lambda: True
        frame.say = lambda *args, **kwargs: None

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE), patch(
            "video_maker.settings.update_preferences", lambda **updates: store.update(updates)
        ), patch("video_maker.player.write_normal_seek_step", lambda value: store.update(normal_seek_step=value)):
            frame.OnCtrl1()
            self.assertEqual(frame.normal_seek_step, 4000)
            frame.normal_seek_step = 1000
            frame.OnCtrl1()
            self.assertEqual(frame.normal_seek_step, 900)
            frame.normal_seek_step = 100
            frame.OnCtrl1()
            self.assertEqual(frame.normal_seek_step, 0)
            frame.OnCtrl2()
            self.assertEqual(frame.normal_seek_step, 100)
            frame.normal_seek_step = 1000
            frame.OnCtrl2()
            self.assertEqual(frame.normal_seek_step, 2000)
            frame.OnCtrl3()
            self.assertEqual(frame.normal_seek_step, 5000)

        self.assertEqual(store["normal_seek_step"], 5000)

    def test_professional_mode_zoom_handlers_by_pixels(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.require_open_file = lambda: True
        frame.say = lambda *args, **kwargs: None
        frame.current_seek_step_ms = lambda: 500
        writes = []

        with patch("video_maker.player_modules.professional.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.player_modules.professional.write_pixels_per_second", lambda value: writes.append(value)
        ):
            frame.pixels_per_second = 80
            frame.OnZoomIn()
            self.assertEqual(frame.pixels_per_second, 100)
            frame.OnZoomOut()
            self.assertEqual(frame.pixels_per_second, 80)
            frame.OnResetZoom()
            self.assertEqual(frame.pixels_per_second, 80)

        self.assertEqual(writes, [100, 80, 80])

    def test_professional_mode_ctrl_1_2_3_no_longer_change_seek_step(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.require_open_file = lambda: True
        frame.say = lambda *args, **kwargs: None
        frame.current_seek_step_ms = lambda: 500
        writes = []
        frame.pixels_per_second = 80

        with patch("video_maker.player_modules.professional.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE), patch(
            "video_maker.player_modules.professional.write_pixels_per_second", lambda value: writes.append(value)
        ):
            frame.OnCtrl1()
            frame.OnCtrl2()
            self.assertEqual(frame.pixels_per_second, 80)
            frame.OnCtrl3()
            self.assertEqual(frame.pixels_per_second, 80)

        self.assertEqual(writes, [80])


class ToggleProgramModeShortcutTest(unittest.TestCase):
    class FakeEvent:
        def GetRawKeyCode(self):
            return 190

        def GetKeyCode(self):
            return 62

        def ControlDown(self):
            return False

        def MetaDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return True

    def test_shift_period_toggles_program_mode_without_open_file(self):
        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame = type("Frame", (), {})()
        calls = []
        frame.OnToggleProgramMode = lambda event=None: calls.append(True)
        frame.has_video = lambda: False
        handle_language_independent_shortcuts(frame, self.FakeEvent())
        self.assertEqual(calls, [True])


class MasterVolumeUnitTest(unittest.TestCase):
    def test_master_volume_helpers_clamp_and_step(self):
        from video_maker.volume_boost import (
            DEFAULT_MASTER_VOLUME_DB,
            MAX_MASTER_VOLUME_DB,
            MIN_MASTER_VOLUME_DB,
            format_master_db,
            master_db_to_linear,
            master_linear_into_volume,
            master_volume_down_db,
            master_volume_up_db,
            normalized_master_volume_db,
            persisted_master_volume_db,
        )

        self.assertEqual(normalized_master_volume_db(None), DEFAULT_MASTER_VOLUME_DB)
        self.assertEqual(normalized_master_volume_db("bad"), DEFAULT_MASTER_VOLUME_DB)
        self.assertEqual(normalized_master_volume_db(-999), MIN_MASTER_VOLUME_DB)
        self.assertEqual(normalized_master_volume_db(999), MAX_MASTER_VOLUME_DB)
        self.assertEqual(master_volume_up_db(0.0), MAX_MASTER_VOLUME_DB)
        self.assertEqual(master_volume_up_db(5.0), MAX_MASTER_VOLUME_DB)
        self.assertEqual(master_volume_down_db(0.0), -1.0)
        self.assertEqual(master_volume_up_db(MAX_MASTER_VOLUME_DB), MAX_MASTER_VOLUME_DB)
        self.assertEqual(master_volume_down_db(MIN_MASTER_VOLUME_DB), MIN_MASTER_VOLUME_DB)
        self.assertEqual(persisted_master_volume_db(-2.0), -2.0)
        self.assertAlmostEqual(master_db_to_linear(0.0), 1.0)
        self.assertAlmostEqual(master_db_to_linear(-6.0), 10 ** (-6 / 20))
        self.assertAlmostEqual(master_linear_into_volume(1.0, 0.0), 1.0)
        self.assertAlmostEqual(master_linear_into_volume(1.0, -6.0), 10 ** (-6 / 20))
        self.assertEqual(format_master_db(-6), "-6")
        self.assertEqual(format_master_db(0), "0")

    def test_effective_output_volume_applies_master_db(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.volume = 1.0
        frame.master_volume_db = -6.0
        self.assertAlmostEqual(frame.effective_output_volume(), 10 ** (-6 / 20))
        frame.master_volume_db = 0.0
        self.assertAlmostEqual(frame.effective_output_volume(), 1.0)

    def test_effective_output_volume_without_master_attribute(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.volume = 1.0
        frame.master_volume_db = None
        self.assertAlmostEqual(frame.effective_output_volume(), 1.0)

    def test_master_volume_handlers_only_in_professional_mode(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.has_video = lambda: True
        frame.master_volume_db = -1.0
        frame.playback_requested = False
        frame.set_media_control_volume = lambda: None
        frame.sync_original_audio_playback = lambda *args, **kwargs: None
        frame.sync_background_audio_playback = lambda *args, **kwargs: None
        frame.schedule_master_volume_save = lambda: None
        frame.say = lambda *args, **kwargs: None

        with patch("video_maker.player.get_program_mode", lambda: program_modes.NORMAL_MODE):
            frame.OnIncreaseMasterVolume()
            frame.OnDecreaseMasterVolume()
        self.assertEqual(frame.master_volume_db, -1.0)

        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnIncreaseMasterVolume()
        self.assertEqual(frame.master_volume_db, 0.0)

        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnDecreaseMasterVolume()
        self.assertEqual(frame.master_volume_db, -1.0)

    def test_master_volume_handlers_require_open_file(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.has_video = lambda: False
        frame.master_volume_db = 0.0
        spoken = []
        frame.say = lambda text, **kwargs: spoken.append(text)

        with patch("video_maker.player.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.OnIncreaseMasterVolume()
        self.assertEqual(frame.master_volume_db, 0.0)
        self.assertEqual(spoken, ["لا يوجد أي ملف مفتوح"])

    def test_track_volume_change_does_not_reload_media(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = "main_video"
        frame.track_volumes_db = {}
        frame.is_dirty = False
        frame.playback_requested = False
        calls = []
        frame.capture_edit_state = lambda: {"fake_state": True}
        frame.record_edit = lambda *args, **kwargs: calls.append("record")
        frame.set_media_control_volume = lambda *args, **kwargs: calls.append("media_volume")
        frame.sync_original_audio_playback = lambda *args, **kwargs: calls.append("sync_original")
        frame.sync_background_audio_playback = lambda *args, **kwargs: calls.append("sync_background")
        frame.apply_edit_state = lambda *args, **kwargs: calls.append("apply_edit")
        frame.say = lambda *args, **kwargs: None

        result = frame._apply_current_track_volume_change(-1.0, "test track volume")
        self.assertTrue(result)
        self.assertAlmostEqual(frame.track_volumes_db["main_video"], -1.0)
        self.assertTrue(frame.is_dirty)
        self.assertNotIn("apply_edit", calls)
        self.assertIn("record", calls)
        self.assertIn("media_volume", calls)
        self.assertIn("sync_original", calls)
        self.assertIn("sync_background", calls)

    def test_track_volume_change_skipped_when_unchanged(self):
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = "main_video"
        frame.track_volumes_db = {"main_video": 0.0}
        frame.is_dirty = False
        frame.playback_requested = False
        frame.capture_edit_state = lambda: {"fake_state": True}
        frame.record_edit = lambda *args, **kwargs: None
        frame.set_media_control_volume = lambda *args, **kwargs: None
        frame.sync_original_audio_playback = lambda *args, **kwargs: None
        frame.sync_background_audio_playback = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.say = lambda *args, **kwargs: None

        result = frame._apply_current_track_volume_change(0.0, "test track volume")
        self.assertFalse(result)
        self.assertFalse(frame.is_dirty)

    def test_save_options_with_master_volume(self):
        from video_maker.volume_boost import (
            export_master_multiplier_from_options,
            save_options_with_master_volume,
        )

        self.assertIsNone(save_options_with_master_volume(None, 0.0))
        options = save_options_with_master_volume(None, -6.0)
        self.assertAlmostEqual(options["master_volume_db"], -6.0)
        self.assertAlmostEqual(export_master_multiplier_from_options(options), 10 ** (-6 / 20))
        self.assertAlmostEqual(export_master_multiplier_from_options(None), 1.0)
        combined = save_options_with_master_volume({"output_volume": 2.0}, -3.0)
        self.assertEqual(combined["output_volume"], 2.0)
        self.assertAlmostEqual(combined["master_volume_db"], -3.0)
        self.assertAlmostEqual(export_master_multiplier_from_options(combined), 10 ** (-3 / 20))

    def test_runtime_payload_records_master_volume_db(self):
        from video_maker.encrypted_projects import capture_runtime_payload

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.volume = 1.0
        frame.master_volume_db = -6.0
        frame.timeline = []
        payload = capture_runtime_payload(frame)
        self.assertEqual(payload["master_volume_db"], -6.0)

    def test_decode_state_restores_master_volume_db(self):
        from video_maker.encrypted_projects import _decode_state

        state = {
            "timeline": [{"path": "", "start": 0.0, "end": 10.0, "speed": 1.0, "audio_volume": 1.0}],
            "master_volume_db": -6.0,
        }
        payload = _decode_state(state, {})
        self.assertEqual(payload["master_volume_db"], -6.0)

    def test_decode_state_clamps_master_volume_db(self):
        from video_maker.encrypted_projects import _decode_state
        from video_maker.volume_boost import MAX_MASTER_VOLUME_DB

        state = {
            "timeline": [{"path": "", "start": 0.0, "end": 10.0, "speed": 1.0, "audio_volume": 1.0}],
            "master_volume_db": 999.0,
        }
        payload = _decode_state(state, {})
        self.assertEqual(payload["master_volume_db"], MAX_MASTER_VOLUME_DB)


class MasterVolumeShortcutTest(unittest.TestCase):
    class ArrowEvent:
        def __init__(self, key, ctrl=False, alt=False, shift=False):
            self._key = key
            self._ctrl = ctrl
            self._alt = alt
            self._shift = shift
            self.skipped = False
            self.allowed_next = False

        def GetRawKeyCode(self):
            return self._key

        def GetKeyCode(self):
            return self._key

        def ControlDown(self):
            return self._ctrl

        def MetaDown(self):
            return False

        def AltDown(self):
            return self._alt

        def ShiftDown(self):
            return self._shift

        def Skip(self):
            self.skipped = True

        def DoAllowNextEvent(self):
            self.allowed_next = True

    def _frame(self):
        calls = []
        frame = type("Frame", (), {})()
        frame.has_video = lambda: True
        frame.say = lambda *args, **kwargs: None
        frame.OnIncreaseVolume = lambda event=None: calls.append("up")
        frame.OnDecreaseVolume = lambda event=None: calls.append("down")
        frame.OnPreviousTrack = lambda event=None: calls.append("prev_track")
        frame.OnNextTrack = lambda event=None: calls.append("next_track")
        frame.OnInsertTrackItem = lambda event=None: calls.append("insert_track_item")
        frame.OnNextElementOnTrack = lambda event=None: calls.append("next_element")
        frame.OnPreviousElementOnTrack = lambda event=None: calls.append("previous_element")
        frame.OnNextEditPoint = lambda event=None: calls.append("next_edit_point")
        frame.OnPreviousEditPoint = lambda event=None: calls.append("previous_edit_point")
        frame.OnIncreaseMasterVolume = lambda event=None: calls.append("inc_master")
        frame.OnDecreaseMasterVolume = lambda event=None: calls.append("dec_master")
        frame.OnIncreaseTrackVolume = lambda event=None: calls.append("inc_track")
        frame.OnDecreaseTrackVolume = lambda event=None: calls.append("dec_track")
        frame.OnMuteBackgroundAudioSelection = lambda event=None: calls.append("mute_background_selection")
        return frame, calls

    def test_professional_mode_plain_arrows_navigate_tracks(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_UP))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_DOWN))
        self.assertEqual(calls, ["prev_track", "next_track"])

    def test_normal_mode_plain_arrows_still_change_volume(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.NORMAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_UP))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_DOWN))
        self.assertEqual(calls, ["up", "down"])

    def test_marked_child_dialog_keeps_arrow_keys_without_open_file(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        dialog = type("Dialog", (), {"_video_maker_preserve_navigation_keys": True})()
        focused = type("Focused", (), {"GetTopLevelParent": lambda self: dialog})()
        frame, calls = self._frame()
        frame.has_video = lambda: False
        spoken = []
        frame.say = lambda message, **_kwargs: spoken.append(message)
        event = self.ArrowEvent(native_wx.WXK_UP)

        with patch("video_maker.shortcuts.wx.Window.FindFocus", return_value=focused):
            handle_language_independent_shortcuts(frame, event)

        self.assertEqual(calls, [])
        self.assertEqual(spoken, [])
        self.assertTrue(event.allowed_next)

    def test_active_marked_progress_dialog_keeps_arrow_keys_without_open_file(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        dialog = type("Dialog", (), {"_video_maker_preserve_navigation_keys": True})()
        frame, calls = self._frame()
        frame.has_video = lambda: False
        spoken = []
        frame.say = lambda message, **_kwargs: spoken.append(message)
        event = self.ArrowEvent(native_wx.WXK_UP)

        with patch("video_maker.shortcuts.wx.Window.FindFocus", return_value=None), patch(
            "video_maker.shortcuts.wx.GetActiveWindow", return_value=dialog
        ):
            handle_language_independent_shortcuts(frame, event)

        self.assertEqual(calls, [])
        self.assertEqual(spoken, [])
        self.assertTrue(event.allowed_next)

    def test_update_progress_dialog_keeps_arrow_keys_when_main_frame_still_has_focus(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        class Dialog:
            _video_maker_preserve_navigation_keys = True

            def __init__(self):
                self.focused = False

            def IsShown(self):
                return True

            def focus_navigation_controls(self):
                self.focused = True

        frame, calls = self._frame()
        frame.GetTopLevelParent = lambda: frame
        frame.has_video = lambda: False
        frame.update_progress_dialog = Dialog()
        spoken = []
        frame.say = lambda message, **_kwargs: spoken.append(message)
        event = self.ArrowEvent(native_wx.WXK_UP)

        with patch("video_maker.shortcuts.wx.Window.FindFocus", return_value=frame), patch(
            "video_maker.shortcuts.wx.GetActiveWindow", return_value=frame
        ):
            handle_language_independent_shortcuts(frame, event)

        self.assertEqual(calls, [])
        self.assertEqual(spoken, [])
        self.assertTrue(event.allowed_next)
        self.assertTrue(frame.update_progress_dialog.focused)

    def test_professional_mode_i_key_inserts_in_current_track(self):
        from video_maker.shortcuts import I_KEY, handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(I_KEY))
        self.assertEqual(calls, ["insert_track_item"])

    def test_normal_mode_i_key_is_not_dispatched(self):
        from video_maker.shortcuts import I_KEY, handle_language_independent_shortcuts

        frame, calls = self._frame()
        event = self.ArrowEvent(I_KEY)
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.NORMAL_MODE):
            handle_language_independent_shortcuts(frame, event)
        self.assertEqual(calls, [])
        self.assertTrue(event.skipped)

    def test_b_key_mutes_background_selection_in_normal_and_professional_modes(self):
        from video_maker.shortcuts import B_KEY, handle_language_independent_shortcuts

        for mode in (program_modes.NORMAL_MODE, program_modes.PROFESSIONAL_MODE):
            frame, calls = self._frame()
            with patch("video_maker.shortcuts.get_program_mode", lambda mode=mode: mode):
                handle_language_independent_shortcuts(frame, self.ArrowEvent(B_KEY))
            self.assertEqual(calls, ["mute_background_selection"])

    def test_b_key_accelerator_is_bound_to_mute_background_selection(self):
        import wx as native_wx

        from video_maker.shortcuts import install_shortcuts

        calls = []
        accelerator_tables = []

        class FakeFrame:
            def __init__(self):
                self.handlers = {}

            def Bind(self, _event_type, handler, id=None):
                if id is not None:
                    self.handlers[int(id)] = handler

            def SetAcceleratorTable(self, table):
                accelerator_tables.append(table)

            def __getattr__(self, name):
                if name == "OnMuteBackgroundAudioSelection":
                    return lambda event=None: calls.append(name)
                if name.startswith("On"):
                    return lambda event=None: None
                raise AttributeError(name)

        frame = FakeFrame()
        with patch("video_maker.shortcuts.wx.AcceleratorTable", lambda entries: entries):
            ids = install_shortcuts(frame)

        self.assertIn((native_wx.ACCEL_NORMAL, ord("B"), ids["mute_background_selection"]), accelerator_tables[0])
        frame.handlers[int(ids["mute_background_selection"])](object())
        self.assertEqual(calls, ["OnMuteBackgroundAudioSelection"])

    def test_ctrl_alt_arrows_route_to_volume_handlers(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_UP, ctrl=True, alt=True))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_DOWN, ctrl=True, alt=True))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_RIGHT, ctrl=True, alt=True))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_LEFT, ctrl=True, alt=True))
        self.assertEqual(calls, ["inc_master", "dec_master", "inc_track", "dec_track"])

    def test_professional_ctrl_right_moves_to_next_element_on_track(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_RIGHT, ctrl=True))
        self.assertEqual(calls, ["next_element"])

    def test_professional_ctrl_left_moves_to_previous_element_on_track(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_LEFT, ctrl=True))
        self.assertEqual(calls, ["previous_element"])

    def test_normal_ctrl_arrows_keep_edit_point_navigation(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.NORMAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_RIGHT, ctrl=True))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_LEFT, ctrl=True))
        self.assertEqual(calls, ["next_edit_point", "previous_edit_point"])

    def test_accelerator_menu_routes_ctrl_arrows_by_mode(self):
        from video_maker.shortcuts import install_shortcuts

        calls = []

        class FakeFrame:
            def __init__(self):
                self.handlers = {}

            def Bind(self, _event_type, handler, id=None):
                if id is not None:
                    self.handlers[int(id)] = handler

            def SetAcceleratorTable(self, _table):
                pass

            def __getattr__(self, name):
                if name.startswith("On"):
                    return lambda event=None, method=name: calls.append(method)
                raise AttributeError(name)

        frame = FakeFrame()
        with patch("video_maker.shortcuts.wx.AcceleratorTable", lambda entries: entries):
            ids = install_shortcuts(frame)

        event = object()
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.handlers[int(ids["next_edit_point"])](event)
            frame.handlers[int(ids["previous_edit_point"])](event)
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.NORMAL_MODE):
            frame.handlers[int(ids["next_edit_point"])](event)
            frame.handlers[int(ids["previous_edit_point"])](event)

        self.assertEqual(
            calls,
            [
                "OnNextElementOnTrack",
                "OnPreviousElementOnTrack",
                "OnNextEditPoint",
                "OnPreviousEditPoint",
            ],
        )

    def test_menu_bar_routes_ctrl_arrows_by_mode(self):
        from video_maker.menus import install_menu_bar
        from video_maker.shortcuts import install_shortcuts

        calls = []

        class FakeMenuItem:
            def __init__(self, item_id):
                self._id = item_id

            def Enable(self, _enabled):
                pass

            def Check(self, _checked):
                pass

            def GetId(self):
                return self._id

        class FakeMenu:
            def Append(self, item_id, *_args, **_kwargs):
                return FakeMenuItem(item_id)

            def AppendSubMenu(self, *_args, **_kwargs):
                return FakeMenuItem(0)

            def AppendSeparator(self):
                pass

            def AppendRadioItem(self, item_id, *_args, **_kwargs):
                return FakeMenuItem(item_id)

            def AppendCheckItem(self, item_id, *_args, **_kwargs):
                return FakeMenuItem(item_id)

        class FakeMenuBar:
            def __init__(self):
                self.menus = []

            def Append(self, menu, title):
                self.menus.append((menu, title))

            def GetMenuCount(self):
                return len(self.menus)

            def Remove(self, index):
                self.menus.pop(index)

        class FakeFrame:
            def __init__(self):
                self.handlers = {}
                self.menu_bar = None

            def Bind(self, _event_type, handler, id=None):
                if id is not None:
                    self.handlers[int(id)] = handler

            def SetAcceleratorTable(self, _table):
                pass

            def GetMenuBar(self):
                return self.menu_bar

            def SetMenuBar(self, menu_bar):
                self.menu_bar = menu_bar

            def Refresh(self):
                pass

            def Update(self):
                pass

            def __getattr__(self, name):
                if name.startswith("On"):
                    return lambda event=None, method=name: calls.append(method)
                raise AttributeError(name)

        frame = FakeFrame()
        target = FakeFrame()
        with patch("video_maker.shortcuts.wx.AcceleratorTable", lambda entries: entries):
            target.shortcut_ids = install_shortcuts(FakeFrame())

        with patch("video_maker.menus.wx.Menu", FakeMenu), patch("video_maker.menus.wx.MenuBar", FakeMenuBar), patch(
            "video_maker.menus.list_recent_files", lambda: []
        ):
            install_menu_bar(frame, command_target=target)

        event = object()
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            frame.handlers[int(target.shortcut_ids["next_edit_point"])](event)
            frame.handlers[int(target.shortcut_ids["previous_edit_point"])](event)
        with patch.object(program_modes, "get_program_mode", lambda: program_modes.NORMAL_MODE):
            frame.handlers[int(target.shortcut_ids["next_edit_point"])](event)
            frame.handlers[int(target.shortcut_ids["previous_edit_point"])](event)

        self.assertEqual(
            calls,
            [
                "OnNextElementOnTrack",
                "OnPreviousElementOnTrack",
                "OnNextEditPoint",
                "OnPreviousEditPoint",
            ],
        )

    def test_alt_arrows_route_to_track_volume_handlers_in_professional_mode(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_UP, alt=True))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_DOWN, alt=True))
        self.assertEqual(calls, ["inc_track", "dec_track"])

    def test_alt_arrows_do_not_change_track_volume_in_normal_mode(self):
        import wx as native_wx

        from video_maker.shortcuts import handle_language_independent_shortcuts

        frame, calls = self._frame()
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.NORMAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_UP, alt=True))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(native_wx.WXK_DOWN, alt=True))
        self.assertEqual(calls, [])

    def test_professional_numpad_plus_increases_step_and_minus_decreases_it(self):
        from video_maker.shortcuts import (
            NUMPAD_ADD_RAW,
            NUMPAD_SUBTRACT_RAW,
            handle_language_independent_shortcuts,
        )

        frame, calls = self._frame()
        frame.OnZoomOut = lambda event=None: calls.append("zoom_out")
        frame.OnZoomIn = lambda event=None: calls.append("zoom_in")
        with patch("video_maker.shortcuts.get_program_mode", lambda: program_modes.PROFESSIONAL_MODE):
            handle_language_independent_shortcuts(frame, self.ArrowEvent(NUMPAD_ADD_RAW))
            handle_language_independent_shortcuts(frame, self.ArrowEvent(NUMPAD_SUBTRACT_RAW))
        # '+' raises the zoom level (larger time step) -> zoom_out lowers pps
        self.assertEqual(calls, ["zoom_out", "zoom_in"])


if __name__ == "__main__":
    unittest.main()
