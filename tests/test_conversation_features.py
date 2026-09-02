import os
import unittest
from unittest.mock import MagicMock, patch
import wx

from video_maker.edit_history import EditHistory
from video_maker.audio_effects import (
    AudioEffectDialog,
    RealtimeAudioPreview,
    build_pedalboard,
    direct_realtime_audio_filter_supported,
    get_audio_effect_definitions,
    hall_filter, cinematic_voice_filter, deep_voice_filter, bright_voice_filter,
    megaphone_filter, underwater_filter, space_motion_filter,
    safe_boost_filter, volume_level_filter, robot_filter, radio_phone_filter,
    flanger_filter, chorus_filter, vibrato_filter, tremolo_filter, pitch_filter,
    bass_treble_filter, normalize_filter
)
from video_maker.breath_reduction_effect import breath_reduction_effect, is_breath_reduction_effect
from video_maker.dialogs import ConfirmExitDialog, confirm_exit_prompt
from video_maker.localization import TEXTS, tr
from video_maker.player_modules.audio_effects import PlayerAudioEffectMixin
from video_maker.player_modules.state import PlayerStateMixin
from video_maker.player_modules.update_recording import PlayerUpdateRecordingMixin
from video_maker.silence_removal import RemoveSilenceDialog
from video_maker.updater import UpdateError, download_update

app = wx.App(False)


class TestConversationFeatures(unittest.TestCase):
    """مجموعة الاختبارات الشاملة المخصصة لجميع الميزات المضافة والمصصحة في الجلسة."""

    def test_01_all_19_effects_support_direct_realtime_preview_and_pedalboard(self):
        """اختبار أن الـ 19 مؤثراً صوتياً يدعم التحديث اللحظي المتصل ومحرك الذاكرة."""
        definitions = get_audio_effect_definitions()
        self.assertGreater(len(definitions), 0)
        
        effect_builders = {
            "hall": hall_filter,
            "cinematic": cinematic_voice_filter,
            "deep_voice": deep_voice_filter,
            "breath_reduction": breath_reduction_effect,
            "bright_voice": bright_voice_filter,
            "megaphone": megaphone_filter,
            "underwater": underwater_filter,
            "space_motion": space_motion_filter,
            "safe_boost": safe_boost_filter,
            "volume_level": volume_level_filter,
            "robot": robot_filter,
            "radio_phone": radio_phone_filter,
            "flanger": flanger_filter,
            "chorus": chorus_filter,
            "vibrato": vibrato_filter,
            "tremolo": tremolo_filter,
            "pitch": pitch_filter,
            "bass_treble": bass_treble_filter,
            "normalize": normalize_filter
        }
        
        for kind, builder in effect_builders.items():
            effect_filter = builder({})
            self.assertTrue(
                direct_realtime_audio_filter_supported(effect_filter),
                f"Effect {kind} should support direct realtime preview"
            )
            board = build_pedalboard(effect_filter)
            self.assertIsNotNone(board, f"Pedalboard for {kind} should not be None")
            self.assertGreater(len(board), 0, f"Pedalboard for {kind} should have plugins")

    def test_02_realtime_audio_preview_update_live_filter(self):
        """اختبار تحديث المرشح اللحظي في الذاكرة دون إغلاق المجرى أو البث الصوتي."""
        preview = RealtimeAudioPreview()
        preview.is_playing = True
        effect_filter = hall_filter({"room": 50})
        
        updated = preview.update_live_filter(effect_filter)
        self.assertTrue(updated, "update_live_filter should return True for pedalboard effects")
        self.assertIsNotNone(preview.active_board, "active_board should be set in memory")

    def test_03_remove_silence_dialog_instantiation(self):
        """اختبار إنشاء نافذة إزالة الصمت بدون خطأ NameError في دالة الترجمة tr."""
        class MockPlayer(wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.timeline = []
                self.start_time = None
                self.end_time = None
            def has_video(self): return True
            def timeline_duration(self): return 10.0
            def say(self, *args, **kwargs): pass

        parent = MockPlayer()
        try:
            dialog = RemoveSilenceDialog(parent)
            self.assertIsNotNone(dialog)
            dialog.Destroy()
        finally:
            parent.Destroy()

    def test_04_on_audio_effect_routes_special_actions(self):
        """اختبار أن OnAudioEffect يوجه الإجراءات الخاصة مثل إزالة الصمت دون طلب builder."""
        class MockPlayer(PlayerAudioEffectMixin, wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.silence_called = False
                self.ducking_called = False
            def has_video(self): return True
            def selected_effect_range(self): return None
            def OnRemoveSilence(self, event=None): self.silence_called = True
            def OnAudioDucking(self, event=None): self.ducking_called = True

        player = MockPlayer()
        try:
            player.OnAudioEffect("remove_silence")
            self.assertTrue(player.silence_called, "OnAudioEffect('remove_silence') should call OnRemoveSilence")
            player.OnAudioEffect("voice_over_ducking")
            self.assertTrue(player.ducking_called, "OnAudioEffect('voice_over_ducking') should call OnAudioDucking")
        finally:
            player.Destroy()

    def test_05_record_edit_sets_is_dirty(self):
        """اختبار أن تسجيل أي تعديل في record_edit يفعل علامة التعديل is_dirty تلقائياً."""
        class MockPlayer(PlayerStateMixin, wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.is_dirty = False
                self.timeline = []
                self.selected_element_ids = set()
                self.element_clipboard = []
                self.edit_points = []
                self.current_edit_point_id = None
                self.work_images = []
                self.work_videos = []
                self.background_audio_items = []
                self.b_roll_items = []
                self.sound_effects_items = []
                self.main_audio_override_path = ""
                self.main_audio_override_duration = 0.0
                self.main_audio_override_timeline_duration = 0.0
                self.edit_history = MagicMock()
                self.edit_history.record.return_value = True
                self.audio_override_manager = MagicMock()
                self.audio_override_manager.reconcile_after_timeline_edit.return_value = MagicMock(changed=False)
                self.window_number = 1
                self.window_name = ""

            def capture_edit_state(self):
                return {"timeline": [], "is_dirty": self.is_dirty}
            def save_crash_session_now(self): pass
            def update_edit_history_menu(self): pass

        player = MockPlayer()
        try:
            self.assertFalse(player.is_dirty)
            player.record_edit("تعديل اختباري", {"timeline": []})
            self.assertTrue(player.is_dirty, "record_edit should set is_dirty to True")
        finally:
            player.Destroy()

    def test_05b_record_edit_keeps_undo_when_reconcile_fails(self):
        """قصّ ثم تراجع يبقى متاحاً حتى لو فشل تحديث الصوت البديل (لا يُلغى التعديل)."""
        class MockPlayer(PlayerStateMixin, wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.is_dirty = False
                self.timeline = [{"id": "seg", "start": 0.0, "end": 10.0}]
                self.rollback_applied = False
                self.selected_element_ids = set()
                self.element_clipboard = []
                self.edit_points = []
                self.current_edit_point_id = None
                self.background_audio_items = []
                self.b_roll_items = []
                self.sound_effects_items = []
                self.main_audio_override_path = ""
                self.main_audio_override_duration = 0.0
                self.main_audio_override_timeline_duration = 0.0
                self.main_audio_effect_chain = []
                self.chroma_render_state = None
                self.focused_element = None
                self.start_time = None
                self.end_time = None
                self.last_insert_end = None
                self.file_metadata = {}
                self.media_kind = "video"
                self.video_path = ""
                self.muted_tracks = set()
                self.solo_tracks = set()
                self.track_volumes_db = {}
                self.ripple_mode = "per_track"
                self.timeline_revision = 0
                self.main_audio_revision = 0
                self.main_audio_source_revision = 0
                self.main_audio_format_version = 0
                self.generated_temp_dirs = []
                self.generated_temp_files = []
                self.edit_history = EditHistory(50)
                self.audio_override_manager = MagicMock()
                self.audio_override_manager.reconcile_after_timeline_edit.side_effect = RuntimeError("audio boom")
                self.speech = MagicMock()

            def capture_edit_state(self):
                return {"timeline": list(self.timeline), "is_dirty": self.is_dirty}
            def say(self, message, **kwargs):
                pass
            def apply_edit_state(self, state, **kwargs):
                self.rollback_applied = True
                self.timeline = list(state["timeline"])
            def save_crash_session_now(self):
                pass
            def update_edit_history_menu(self):
                pass

        player = MockPlayer()
        try:
            before_timeline = list(player.timeline)
            player.timeline = before_timeline + [{"id": "seg2", "start": 10.0, "end": 12.0}]
            result = player.record_edit("قص العنصر", {"timeline": before_timeline, "is_dirty": False})
            self.assertTrue(result)
            self.assertTrue(player.edit_history.can_undo(), "undo must be available even if reconcile fails")
            self.assertTrue(player.is_dirty)
            self.assertFalse(player.rollback_applied, "the edit must not be rolled back on audio failure")
            operation, undo_state = player.edit_history.undo()
            self.assertEqual(operation, "قص العنصر")
            self.assertEqual(undo_state["timeline"], before_timeline)
        finally:
            player.Destroy()

    def test_05c_undo_restores_state_after_split_with_reconcile_failure(self):
        """نموذج OnSplitAtPlayhead: فشل الصوت لا يُفرّغ التراجع، والتراجع يعيد الحالة."""
        class MockPlayer(PlayerStateMixin, wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.is_dirty = False
                self.timeline = [{"id": "seg", "start": 0.0, "end": 10.0}]
                self.selected_element_ids = set()
                self.element_clipboard = []
                self.edit_points = []
                self.current_edit_point_id = None
                self.background_audio_items = []
                self.b_roll_items = []
                self.sound_effects_items = []
                self.main_audio_override_path = ""
                self.main_audio_override_duration = 0.0
                self.main_audio_override_timeline_duration = 0.0
                self.main_audio_effect_chain = []
                self.chroma_render_state = None
                self.focused_element = None
                self.start_time = None
                self.end_time = None
                self.last_insert_end = None
                self.file_metadata = {}
                self.media_kind = "video"
                self.video_path = ""
                self.muted_tracks = set()
                self.solo_tracks = set()
                self.track_volumes_db = {}
                self.ripple_mode = "per_track"
                self.timeline_revision = 0
                self.main_audio_revision = 0
                self.main_audio_source_revision = 0
                self.main_audio_format_version = 0
                self.generated_temp_dirs = []
                self.generated_temp_files = []
                self.edit_history = EditHistory(50)
                self.audio_override_manager = MagicMock()
                self.audio_override_manager.reconcile_after_timeline_edit.side_effect = RuntimeError("audio boom")
                self.speech = MagicMock()

            def capture_edit_state(self):
                return {"timeline": list(self.timeline), "is_dirty": self.is_dirty}
            def say(self, message, **kwargs):
                pass
            def save_crash_session_now(self):
                pass
            def update_edit_history_menu(self):
                pass

        player = MockPlayer()
        try:
            left = {"id": "seg-a", "start": 0.0, "end": 3.0}
            right = {"id": "seg-b", "start": 3.0, "end": 10.0}
            before = player.capture_edit_state()
            player.timeline = [left, right]
            player.record_edit("قص العنصر", before)
            self.assertTrue(player.edit_history.can_undo())
            operation, undo_state = player.edit_history.undo()
            self.assertEqual(operation, "قص العنصر")
            self.assertEqual([s["id"] for s in undo_state["timeline"]], ["seg"])
        finally:
            player.Destroy()

    def test_06_confirm_exit_dialog_translations(self):
        """اختبار ترجمات وتدقيق أزرار وعناوين نافذة تأكيد الخروج للغات الثلاث."""
        keys = [
            ("نعم، أريد الخروج", "Yes, I want to exit", "Oui, je veux quitter"),
            ("لا، أريد إكمال التعديل", "No, I want to continue editing", "Non, je veux continuer l'édition"),
            ("هناك تعديلات لم يتم حفظها. هل تريد الخروج بدون حفظ؟", "There are unsaved changes. Do you want to exit without saving?", "Des modifications ne sont pas enregistrées. Voulez-vous quitter sans les enregistrer ?"),
            ("تعديلات غير محفوظة", "Unsaved Changes", "Modifications non enregistrées"),
        ]
        
        for ar, en, fr in keys:
            self.assertEqual(TEXTS.get("en", {}).get(ar), en, f"EN translation missing or wrong for {ar}")
            self.assertEqual(TEXTS.get("fr", {}).get(ar), fr, f"FR translation missing or wrong for {ar}")

    def test_07_confirm_exit_dialog_instantiation(self):
        """اختبار إنشاء نافذة تأكيد الخروج ConfirmExitDialog واختيار الأزرار."""
        parent = wx.Frame(None)
        try:
            dialog = ConfirmExitDialog(parent)
            self.assertIsNotNone(dialog)
            self.assertEqual(dialog.yes_button.GetLabel(), tr("نعم، أريد الخروج"))
            self.assertEqual(dialog.no_button.GetLabel(), tr("لا، أريد إكمال التعديل"))
            dialog.Destroy()
        finally:
            parent.Destroy()

    def test_08_update_download_cancellation_immediate(self):
        """اختبار إلغاء تنزيل التحديث فوراً عند طلب الإلغاء وعدم فتح نافذة الأخطاء."""
        cancelled = True
        update_info = {
            "latest_version": "9.9.9",
            "asset_name": "TestUpdate.exe",
            "asset_url": "https://httpbin.org/bytes/10485760",
        }
        
        with self.assertRaises(UpdateError):
            download_update(update_info, cancel_callback=lambda: cancelled)

        class MockUpdatePlayer(PlayerUpdateRecordingMixin, wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.update_cancel_requested = True
                self.update_progress_dialog = None
                self.spoken_messages = []
                self.error_shown = False
            def say(self, message, interrupt=True):
                self.spoken_messages.append(message)
            def show_update_error(self, message, params, details):
                self.error_shown = True

        player = MockUpdatePlayer()
        try:
            player.finish_update_download(None, update_info, "تم إلغاء تنزيل التحديث")
            self.assertFalse(player.error_shown, "show_update_error should NOT be called on cancellation")
            self.assertIn(tr("تم إلغاء تنزيل التحديث"), player.spoken_messages)
        finally:
            player.Destroy()


if __name__ == "__main__":
    unittest.main()
