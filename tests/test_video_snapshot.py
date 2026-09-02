import os
import unittest
from unittest.mock import Mock, patch

from video_maker.video_snapshot import copy_video_snapshot_to_clipboard

class TestVideoSnapshot(unittest.TestCase):
    def setUp(self):
        self.mock_frame = Mock()
        self.mock_frame.timeline = ["fake_segment"]
        self.mock_frame.media_kind = "video"
        self.player = Mock()
        self.mock_frame.media_ctrl._player = self.player

    def test_no_open_file(self):
        self.mock_frame.timeline = None
        res = copy_video_snapshot_to_clipboard(self.mock_frame)
        self.assertEqual(res, "لا يوجد ملف مفتوح")

    def test_audio_project(self):
        self.mock_frame.media_kind = "audio"
        res = copy_video_snapshot_to_clipboard(self.mock_frame)
        self.assertEqual(res, "المشروع صوت وليس فديو")

    @patch('video_maker.video_snapshot.wx')
    @patch('video_maker.video_snapshot.tempfile.mkdtemp')
    @patch('video_maker.video_snapshot.os.path.exists')
    @patch('video_maker.video_snapshot.os.remove')
    @patch('video_maker.video_snapshot.os.rmdir')
    def test_successful_snapshot(self, mock_rmdir, mock_remove, mock_exists, mock_mkdtemp, mock_wx):
        mock_mkdtemp.return_value = "/fake/temp"
        mock_exists.return_value = True
        
        mock_bitmap = Mock()
        mock_bitmap.IsOk.return_value = True
        mock_wx.Bitmap.return_value = mock_bitmap
        mock_wx.BITMAP_TYPE_PNG = 1
        
        clipboard = Mock()
        clipboard.Open.return_value = True
        mock_wx.TheClipboard = clipboard
        
        res = copy_video_snapshot_to_clipboard(self.mock_frame)
        
        self.mock_frame.media_ctrl._player.command.assert_called_once_with("screenshot-to-file", os.path.join("/fake/temp", "snapshot.png"), "video")
        clipboard.SetData.assert_called_once()
        clipboard.Close.assert_called_once()
        
        self.assertEqual(res, "تم نسخ الصورة للحافظة")

if __name__ == '__main__':
    unittest.main()
