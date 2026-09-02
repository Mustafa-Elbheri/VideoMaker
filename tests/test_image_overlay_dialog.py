import os
import unittest
from unittest.mock import MagicMock, patch
import wx

from video_maker.image_overlay import ImageOverlayDialog, ImageOverlayOptions
from video_maker.player import VideoPlayer
from video_maker.program_modes import NORMAL_MODE, set_program_mode


class ImageOverlayDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App()

    def test_image_overlay_dialog_initialization_no_name_error(self):
        frame = wx.Frame(None)
        dialog = None
        try:
            # Must not raise NameError for missing 'tr'
            dialog = ImageOverlayDialog(frame)
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.image_text.GetName())
            self.assertTrue(dialog.mode_choice.GetName())
            self.assertTrue(dialog.position_choice.GetName())
            self.assertTrue(dialog.width_slider.GetName())
            self.assertTrue(dialog.height_slider.GetName())
        finally:
            if dialog:
                dialog.Destroy()
            frame.Destroy()

    def test_on_insert_image_audio_project_flow(self):
        set_program_mode(NORMAL_MODE)
        player = VideoPlayer(None)
        audio_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "test.wav"))
        player.OnOpenAudio(audio_path)
        player.start_time = 1.0
        player.end_time = 3.0

        image_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "test.png"))
        options = ImageOverlayOptions(
            image_path=image_path,
            full_screen=True,
            position="center_top",
            width_percent=35,
            height_percent=35,
        )

        def fake_init(dialog_self, parent, *args, **kwargs):
            dialog_self.options = options

        with patch.object(ImageOverlayDialog, "__init__", fake_init), \
             patch.object(ImageOverlayDialog, "ShowModal", return_value=wx.ID_OK), \
             patch.object(ImageOverlayDialog, "Destroy", return_value=None):
            player.OnInsertImage()

        self.assertEqual(len(player.visual_items), 1)
        self.assertEqual(player.visual_items[0]["path"], image_path)
        self.assertEqual(player.visual_items[0]["type"], "image")
        self.assertEqual(player.visual_items[0]["start"], 1.0)
        self.assertEqual(player.visual_items[0]["end"], 3.0)
        player.Destroy()


if __name__ == "__main__":
    unittest.main()
