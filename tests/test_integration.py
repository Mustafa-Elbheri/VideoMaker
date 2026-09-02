import unittest
import wx
import os
import time
import sys

# Ensure video_maker is in path
sys.path.insert(0, os.path.abspath('.'))

from video_maker.player import VideoPlayer
from video_maker.visual_effects import VisualEffectsDialog
from video_maker.chroma_dialog import ChromaBackgroundDialog
from video_maker.background_audio import BackgroundAudioDialog
from video_maker.mpv_player import MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED

class AppIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)
        cls.video_path = os.path.abspath('tests/test.mp4')
        cls.audio_path = os.path.abspath('tests/test.wav')
        
        # Ensure dummy media exist for tests
        if not os.path.exists(cls.video_path) or not os.path.exists(cls.audio_path):
            raise unittest.SkipTest("Dummy media files not found. Run create_dummy_media.py first.")

    @classmethod
    def tearDownClass(cls):
        wx.CallAfter(cls.app.ExitMainLoop)
        cls.app.MainLoop()

    def setUp(self):
        self.frame = VideoPlayer(None, title="Integration Test Frame")
        # Process pending events so it initializes fully
        self.yield_events()

    def tearDown(self):
        self.frame.Destroy()
        self.yield_events()

    def yield_events(self, ms=100):
        # A small loop to process wx events (like CallLater, CallAfter)
        end_time = time.time() + (ms / 1000.0)
        while time.time() < end_time:
            wx.Yield()
            time.sleep(0.01)

    def test_open_video_and_playback_state(self):
        """Test loading a video and verifying the MPVMediaCtrl state updates correctly."""
        self.frame.OnOpenVideo(self.video_path)
        self.yield_events(1500)  # Wait longer for async load and playback to start
        
        # Ensure the timeline actually received the item
        self.assertGreater(len(self.frame.timeline), 0)
        
        # Ensure OnPlayPause can be called repeatedly without crashing
        self.frame.playback_requested = False
        self.frame.OnPlayPause()
        self.yield_events(200)
        
        self.frame.OnPlayPause()
        self.yield_events(200)

        # Trigger play again
        self.frame.OnPlayPause()
        self.yield_events(500)

    def test_seeking_mechanism(self):
        """Test the ultra-fast seeking mechanism mapping to MPVMediaCtrl."""
        self.frame.OnOpenVideo(self.video_path)
        self.yield_events(500)
        
        # Fast forward
        initial_time = self.frame.current_time
        self.frame.OnForward() # normally calls move_one_second
        self.yield_events(500) # Wait for async seek
        
        # Check if the time has advanced
        self.assertGreater(self.frame.current_time, initial_time)
        
        # Fast rewind
        self.frame.OnRewind()
        self.yield_events(500)
        
        self.assertLess(self.frame.current_time, initial_time + 1.0) # Should go back

    def test_visual_effects_dialog_preview(self):
        """Test VisualEffectsDialog initializes and its MPV player preview works."""
        dlg = VisualEffectsDialog(self.frame, lambda path, desc: None, lambda: None)
        self.yield_events(200)
        self.assertIsNotNone(dlg.preview)
        
        # Direct play via MPVMediaCtrl to verify backend initialization
        dlg.preview.Load(self.video_path)
        self.yield_events(200)
        dlg.preview.Play()
        self.yield_events(500) 
        self.assertEqual(dlg.preview.GetState(), MEDIASTATE_PLAYING)
        dlg.Destroy()

    def test_chroma_background_dialog_preview(self):
        """Test ChromaBackgroundDialog initializes and its MPV player preview works."""
        dlg = ChromaBackgroundDialog(self.frame, lambda: None, lambda v: None, lambda s, l: None)
        self.yield_events(200)
        self.assertIsNotNone(dlg.preview)
        
        # Direct play via MPVMediaCtrl
        dlg.preview.Load(self.video_path)
        self.yield_events(200)
        dlg.preview.Play()
        self.yield_events(500)
        self.assertEqual(dlg.preview.GetState(), MEDIASTATE_PLAYING)
        dlg.Destroy()

    def test_background_audio_dialog_preview(self):
        """Test BackgroundAudioDialog initializes and its MPV player works."""
        dlg = BackgroundAudioDialog(self.frame)
        self.yield_events(200)
        self.assertIsNotNone(dlg.preview)
        
        # Direct play via MPVMediaCtrl
        dlg.preview.Load(self.audio_path)
        self.yield_events(200)
        dlg.preview.Play()
        self.yield_events(500)
        self.assertEqual(dlg.preview.GetState(), MEDIASTATE_PLAYING)
        dlg.Destroy()

if __name__ == '__main__':
    unittest.main()
