import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath('.'))

from video_maker.app_audio_capture import (
    ProcessAudioCapture,
    PROCESS_AUDIO_SUPPORTED,
    get_available_applications,
    _load_process_audio_capture
)
from video_maker.recording import (
    selected_app_pids,
    RecordingOptions,
    AudioRecordingSession
)


class TestAppAudioCapture(unittest.TestCase):
    def test_process_audio_capture_module_loaded(self):
        cls = _load_process_audio_capture()
        self.assertIsNotNone(cls, "ProcessAudioCapture module should be loaded successfully.")
        self.assertTrue(hasattr(cls, "is_supported"))
        self.assertTrue(hasattr(cls, "enumerate_audio_processes"))

    def test_get_available_applications_returns_list(self):
        apps = get_available_applications()
        self.assertIsInstance(apps, list)
        for item in apps:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)
            self.assertIsInstance(item[0], int)
            self.assertIsInstance(item[1], str)

    def test_selected_app_pids_parsing(self):
        options = RecordingOptions("audio", selected_apps=[101, "202", "invalid", None, 101])
        pids = selected_app_pids(options)
        self.assertEqual(pids, [101, 202])

    @patch('video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED', True)
    @patch('video_maker.app_audio_capture.ProcessAudioCapture')
    def test_run_selected_app_audio_session_flow(self, mock_pac_cls):
        mock_capture_instance = MagicMock()
        mock_pac_cls.return_value = mock_capture_instance

        options = RecordingOptions(
            "audio",
            source="internal",
            selected_apps=[9999],
            sample_rate=44100,
            channels=2
        )
        session = AudioRecordingSession(options)

        with patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', return_value=1000), \
             patch('video_maker.recording.run_ffmpeg'):
             
            session.running = True
            def stop_session_soon():
                import time
                time.sleep(0.1)
                session.running = False
                session.stop_event.set()

            import threading
            t = threading.Thread(target=stop_session_soon)
            t.start()

            session.run_selected_app_audio()
            t.join()

            expected_path = os.path.join(session.folder, "part_0001.app_9999.wav")
            mock_pac_cls.assert_called_with(pid=9999, output_path=expected_path)
            mock_capture_instance.start.assert_called_once()
            mock_capture_instance.stop.assert_called()


if __name__ == '__main__':
    unittest.main()
