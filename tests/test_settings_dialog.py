import unittest
from unittest.mock import patch
import wx
import os
import sys

sys.path.insert(0, os.path.abspath('.'))

from video_maker.settings_dialog import ProgramSettingsDialog
from video_maker.recording import RecordingSettingsDialog

class TestSettingsDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls):
        wx.CallAfter(cls.app.ExitMainLoop)
        cls.app.MainLoop()

    @patch('video_maker.localization.get_language', return_value='ar')
    @patch('video_maker.app_state.get_language', return_value='ar')
    @patch('video_maker.settings_dialog.get_program_mode', return_value='advanced')
    @patch('video_maker.settings_dialog.program_mode_labels', return_value=['Basic', 'Advanced'])
    @patch('video_maker.settings_dialog.program_mode_index', return_value=1)
    @patch('video_maker.settings_dialog.available_devices')
    def test_settings_dialog_creation(self, mock_devices, mock_index, mock_labels, mock_get_mode, mock_lang, mock_lang2):
        from video_maker.audio_devices import AudioDevice, DEFAULT_DEVICE_ID, INPUT_KIND, OUTPUT_KIND
        mock_devices.side_effect = lambda kind: [
            AudioDevice(DEFAULT_DEVICE_ID, "Default", kind),
            AudioDevice("1", "Speaker" if kind == OUTPUT_KIND else "Mic", kind, 2 if kind == OUTPUT_KIND else 1),
        ]
        dialog = ProgramSettingsDialog(None)
        
        self.assertEqual(dialog.GetTitle(), 'إعدادات البرنامج')
        self.assertEqual(dialog.notebook.GetPageCount(), 4)
        
        self.assertEqual(dialog.notebook.GetPageText(0), 'عام')
        self.assertEqual(dialog.notebook.GetPageText(1), 'أجهزة الصوت')
        self.assertEqual(dialog.notebook.GetPageText(2), 'النطق')
        
        self.assertEqual(dialog.program_mode_choice.GetCount(), 2)
        self.assertEqual(dialog.program_mode_choice.GetSelection(), 1)
        self.assertEqual(dialog.output_audio_choice.GetCount(), 2)
        self.assertEqual(dialog.input_audio_choice.GetCount(), 2)
        self.assertEqual(dialog.output_audio_choice.GetName(), 'السماعة الافتراضية')
        self.assertEqual(dialog.input_audio_choice.GetName(), 'الميكروفون الافتراضي')
        
        dialog.Destroy()

    def _recording_devices(self, kind):
        from video_maker.audio_devices import AudioDevice, DEFAULT_DEVICE_ID, INPUT_KIND
        return [
            AudioDevice(DEFAULT_DEVICE_ID, "Default microphone", INPUT_KIND),
            AudioDevice("1", "USB Microphone", INPUT_KIND, 1),
            AudioDevice("3", "Webcam Mic", INPUT_KIND, 2),
        ]

    @patch('video_maker.localization.get_language', return_value='ar')
    @patch('video_maker.recording.get_selected_device_id', return_value='3')
    @patch('video_maker.recording.available_devices')
    def test_audio_recording_dialog_uses_default_microphone_for_session_only(self, mock_devices, mock_selected, mock_lang):
        mock_devices.side_effect = self._recording_devices
        dialog = RecordingSettingsDialog(None, "audio")

        self.assertEqual(dialog.input_device_choice.GetName(), 'الميكروفون لهذه الجلسة')
        self.assertEqual(dialog.input_device_choice.GetSelection(), 2)
        self.assertTrue(dialog.input_device_choice.IsEnabled())

        dialog.input_device_choice.SetSelection(1)
        options = dialog.options()
        self.assertEqual(options.input_device_id, "1")
        mock_selected.assert_called()

        dialog.Destroy()

    @patch('video_maker.recording.get_selected_device_id', return_value='3')
    @patch('video_maker.recording.available_devices')
    def test_screen_recording_dialog_uses_same_session_microphone_behavior(self, mock_devices, mock_selected):
        mock_devices.side_effect = self._recording_devices
        dialog = RecordingSettingsDialog(None, "screen")

        self.assertEqual(dialog.input_device_choice.GetSelection(), 2)
        self.assertIsNotNone(dialog.frame_rate_choice)

        dialog.source_choice.SetSelection(0)
        dialog.update_microphone_choice()
        self.assertFalse(dialog.input_device_choice.IsEnabled())

        dialog.source_choice.SetSelection(1)
        dialog.update_microphone_choice()
        self.assertTrue(dialog.input_device_choice.IsEnabled())

        dialog.input_device_choice.SetSelection(1)
        self.assertEqual(dialog.options().input_device_id, "1")
        mock_selected.assert_called()

        dialog.Destroy()

if __name__ == '__main__':
    unittest.main()
