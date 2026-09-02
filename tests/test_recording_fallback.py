import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath('.'))

from video_maker.recording import AudioRecordingSession, RecordingOptions


class TestRecordingFallback(unittest.TestCase):
    @patch('video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED', False)
    @patch('video_maker.app_audio_capture.ProcessAudioCapture', None)
    @patch.object(AudioRecordingSession, 'run_standard_audio')
    def test_audio_recording_falls_back_to_standard_audio_when_app_capture_unsupported(self, mock_standard):
        options = RecordingOptions(
            "audio",
            source="internal",
            selected_apps=[1234],
            sample_rate=44100,
            channels=2
        )
        session = AudioRecordingSession(options)
        session.run()
        
        mock_standard.assert_called_once()
        self.assertEqual(session.error, "")

    @patch('video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED', True)
    @patch('video_maker.app_audio_capture.ProcessAudioCapture')
    @patch.object(AudioRecordingSession, 'run_selected_app_audio', side_effect=RuntimeError("Process audio error"))
    @patch.object(AudioRecordingSession, 'run_standard_audio')
    def test_audio_recording_falls_back_when_app_capture_raises_exception(self, mock_standard, mock_selected, mock_capture):
        options = RecordingOptions(
            "audio",
            source="both",
            selected_apps=[5678],
            sample_rate=44100,
            channels=2
        )
        session = AudioRecordingSession(options)
        session.run()

        mock_selected.assert_called_once()
        mock_standard.assert_called_once()
        self.assertEqual(session.error, "")


if __name__ == '__main__':
    unittest.main()
