import unittest
from unittest.mock import MagicMock, patch
import wx

from video_maker.shortcuts import handle_language_independent_shortcuts, shortcut_requires_open_file


class ChangeAppNameShortcutTest(unittest.TestCase):
    def test_ctrl_shift_f2_does_not_require_open_file(self):
        # When Ctrl and Shift are down, F2 should be allowed even without an open file
        req = shortcut_requires_open_file(113, wx.WXK_F2, has_modifier=True, shift_down=True, alt_down=False)
        self.assertFalse(req)

    def test_ctrl_shift_f2_triggers_change_application_name_without_file(self):
        app = wx.App.Get() or wx.App()
        frame = MagicMock()
        frame.has_video.return_value = False
        frame.OnChangeApplicationName = MagicMock()

        event = MagicMock()
        event.ControlDown.return_value = True
        event.MetaDown.return_value = False
        event.ShiftDown.return_value = True
        event.AltDown.return_value = False
        event.GetKeyCode.return_value = wx.WXK_F2
        event.GetRawKeyCode.return_value = 113

        handle_language_independent_shortcuts(frame, event)
        frame.OnChangeApplicationName.assert_called_once()
        frame.say.assert_not_called()

    def test_f2_alone_triggers_rename_program_window(self):
        app = wx.App.Get() or wx.App()
        frame = MagicMock()
        frame.OnRenameProgramWindow = MagicMock()

        event = MagicMock()
        event.ControlDown.return_value = False
        event.MetaDown.return_value = False
        event.ShiftDown.return_value = False
        event.AltDown.return_value = False
        event.GetKeyCode.return_value = wx.WXK_F2
        event.GetRawKeyCode.return_value = 113

        handle_language_independent_shortcuts(frame, event)
        frame.OnRenameProgramWindow.assert_called_once()


if __name__ == "__main__":
    unittest.main()
