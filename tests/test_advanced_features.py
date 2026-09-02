import unittest
import wx
import os
import time
import sys

sys.path.insert(0, os.path.abspath('.'))

from video_maker.player import VideoPlayer

class AdvancedFeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)
        cls.video_path = os.path.abspath('tests/test.mp4')
        cls.audio_path = os.path.abspath('tests/test.wav')
        cls.image_path = os.path.abspath('tests/test.png')
        
        if not os.path.exists(cls.video_path):
            raise unittest.SkipTest("Dummy media files not found.")

    @classmethod
    def tearDownClass(cls):
        wx.CallAfter(cls.app.ExitMainLoop)
        cls.app.MainLoop()

    def setUp(self):
        self.frame = VideoPlayer(None, title="Advanced Test Frame")
        self.yield_events()

    def tearDown(self):
        self.frame.Destroy()
        self.yield_events()

    def yield_events(self, ms=100):
        end_time = time.time() + (ms / 1000.0)
        while time.time() < end_time:
            wx.Yield()
            time.sleep(0.01)

    def test_multiple_visual_effects(self):
        """Test applying multiple visual effects consecutively."""
        self.frame.OnOpenVideo(self.video_path)
        self.yield_events(1000)
        
        # Select range 0 to 2 seconds
        self.frame.start_time = 0.0
        self.frame.end_time = 2.0
        
        # Apply first effect
        self.frame.InsertVisualEffect(self.video_path, "Effect 1")
        self.yield_events(500)
        
        # Verify first effect added
        self.assertEqual(len(self.frame.timeline), 2)  # Timeline split or expanded
        
        # Apply second effect
        self.frame.start_time = 2.0
        self.frame.end_time = 4.0
        self.frame.InsertVisualEffect(self.video_path, "Effect 2")
        self.yield_events(500)
        
        # Verify second effect added without crash
        self.assertGreater(len(self.frame.timeline), 2)

    def test_all_visual_effects_capacity(self):
        """Stress-test applying many effects (simulating 'all effects')."""
        self.frame.OnOpenVideo(self.video_path)
        self.yield_events(1000)
        
        for i in range(5):
            self.frame.start_time = 0.0
            self.frame.end_time = 1.0
            self.frame.InsertVisualEffect(self.video_path, f"Bulk Effect {i}")
            self.yield_events(200) # Process UI events
            
        self.assertGreater(len(self.frame.timeline), 3)

    def test_audio_only_project(self):
        """Test creating an audio-only project."""
        self.frame.OnOpenAudio(self.audio_path)
        self.yield_events(1000)
        
        self.assertEqual(self.frame.media_kind, "audio")
        self.assertGreater(len(self.frame.timeline), 0)
        
        # Test basic playback in audio-only mode
        self.frame.playback_requested = False
        self.frame.OnPlayPause()
        self.yield_events(200)

    def test_image_overlay_on_audio(self):
        """Test adding an image on top of an audio project."""
        self.frame.OnOpenAudio(self.audio_path)
        self.yield_events(1000)
        
        # Select range
        self.frame.start_time = 0.0
        self.frame.end_time = 3.0
        
        initial_visual_items = len(self.frame.visual_items)
        self.frame.InsertAudioVisualItem("image", self.image_path)
        self.yield_events(500)
        
        self.assertEqual(len(self.frame.visual_items), initial_visual_items + 1)
        self.assertEqual(self.frame.visual_items[-1]['type'], "image")

    def test_video_overlay_on_audio(self):
        """Test adding a video overlay on an audio project."""
        self.frame.OnOpenAudio(self.audio_path)
        self.yield_events(1000)
        
        self.frame.start_time = 0.0
        self.frame.end_time = 6.0
        
        initial_visual_items = len(self.frame.visual_items)
        self.frame.InsertAudioVisualItem("video", self.video_path)
        self.yield_events(500)
        
        self.assertEqual(len(self.frame.visual_items), initial_visual_items + 1)
        self.assertEqual(self.frame.visual_items[-1]['type'], "video")

    def test_background_audio_insertion(self):
        """Test inserting background audio track over video."""
        self.frame.OnOpenVideo(self.video_path)
        self.yield_events(1000)
        
        # Select range
        self.frame.start_time = 0.0
        self.frame.end_time = 3.0
        
        # Directly call InsertBackgroundAudio with mock option
        options = {
            "path": self.audio_path,
            "volume": 1.0,
            "start": None,
            "end": None,
            "id": "test_bg_audio",
            "delay": 0.0
        }
        self.frame.InsertBackgroundAudio(options)
        self.yield_events(500)
        
        self.assertEqual(len(self.frame.background_audio_items), 1)

if __name__ == '__main__':
    unittest.main()
