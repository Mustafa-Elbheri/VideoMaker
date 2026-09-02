import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from video_maker.app_audio_capture import get_available_applications, ProcessAudioReader

class TestAppAudioCapture(unittest.TestCase):
    @patch('video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED', False)
    @patch('psutil.process_iter')
    def test_get_available_applications_falls_back_to_process_list(self, mock_process_iter):
        mock_proc1 = MagicMock()
        mock_proc1.info = {'pid': 1234, 'name': 'test_app.exe', 'status': 'running'}
        mock_proc2 = MagicMock()
        mock_proc2.info = {'pid': 5678, 'name': 'explorer.exe', 'status': 'running'}
        
        mock_process_iter.return_value = [mock_proc1, mock_proc2]
        
        apps = get_available_applications()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0][0], 1234)
        self.assertEqual(apps[0][1], 'Test_app')

    @patch('video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED', True)
    @patch('video_maker.app_audio_capture.ProcessAudioCapture')
    def test_get_available_applications_prefers_audio_processes(self, mock_capture):
        mock_capture.enumerate_audio_processes.return_value = [
            SimpleNamespace(pid=1234, name='browser.exe', window_title='Browser tab'),
            SimpleNamespace(pid=5678, name='player.exe', window_title=''),
        ]

        apps = get_available_applications()

        self.assertEqual(apps, [(1234, 'Browser tab'), (5678, 'player.exe')])

    @patch('video_maker.app_audio_capture.ProcessAudioCapture', None)
    @patch('video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED', False)
    def test_reader_fails_without_library(self):
        reader = ProcessAudioReader(1234, 48000, 2)
        with self.assertRaises(RuntimeError):
            reader.start()

if __name__ == '__main__':
    unittest.main()
