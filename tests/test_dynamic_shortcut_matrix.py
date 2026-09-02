import unittest
from unittest.mock import MagicMock, patch
import wx

from video_maker.player import VideoPlayer
from video_maker.shortcuts import (
    handle_language_independent_shortcuts,
    shortcut_requires_open_file,
    RECORDING_KEYS,
)


class DynamicShortcutMatrixTest(unittest.TestCase):
    """Verifies that all registered shortcuts dispatch correctly and global actions

    are never blocked when no file is open.
    """
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App()

    def test_shortcut_ids_dictionary_integrity(self):
        player = VideoPlayer(None)
        try:
            ids = player.shortcut_ids
            self.assertIsInstance(ids, dict)
            # Critical global actions must always exist in the shortcut registry
            self.assertIn("change_application_name", ids)
            self.assertIn("rename_program_window", ids)
            self.assertIn("new_program_window", ids)
            self.assertIn("program_settings", ids)
            self.assertIn("check_updates", ids)
            self.assertIn("insert_image", ids)
            self.assertIn("insert_text", ids)
        finally:
            player.Destroy()

    def test_global_shortcuts_allowed_without_file(self):
        # Ctrl+Shift+F2 (Change Application Name)
        self.assertFalse(shortcut_requires_open_file(113, wx.WXK_F2, has_modifier=True, shift_down=True, alt_down=False))
        # Ctrl+O (Open File)
        self.assertFalse(shortcut_requires_open_file(79, ord('O'), has_modifier=True, shift_down=False, alt_down=False))
        # Ctrl+U (Check for Updates)
        self.assertFalse(shortcut_requires_open_file(85, ord('U'), has_modifier=True, shift_down=False, alt_down=False))
        # Ctrl+Q (Exit)
        self.assertFalse(shortcut_requires_open_file(81, ord('Q'), has_modifier=True, shift_down=False, alt_down=False))
        # Recording keys
        for key in RECORDING_KEYS:
            self.assertFalse(shortcut_requires_open_file(key, key, has_modifier=True, shift_down=False, alt_down=False))

    def test_matrix_shortcut_dispatch_no_crash(self):
        frame = MagicMock()
        frame.has_video.return_value = True
        frame.current_track = "main_video"
        frame._numpad_key_owned_by_focus.return_value = False

        test_keys = [
            (wx.WXK_F2, 113, True, True, False),    # Ctrl+Shift+F2 -> ChangeApplicationName
            (wx.WXK_F2, 113, False, False, False),  # F2 -> RenameProgramWindow
            (ord('N'), 78, True, False, False),     # Ctrl+N -> NewProgramWindow
            (ord('O'), 79, True, False, False),     # Ctrl+O -> Open
            (ord('I'), 73, True, True, False),      # Ctrl+Shift+I -> InsertImage
            (ord('T'), 84, True, True, False),      # Ctrl+Shift+T -> InsertText
            (ord('B'), 66, True, True, False),      # Ctrl+Shift+B -> InsertBackgroundAudio
            (ord('M'), 77, True, True, False),      # Ctrl+Shift+M -> Metadata
            (ord('U'), 85, True, False, False),     # Ctrl+U -> CheckForUpdates
            (ord('Q'), 81, True, False, False),     # Ctrl+Q -> Close
        ]

        for key, raw_key, ctrl, shift, alt in test_keys:
            event = MagicMock()
            event.ControlDown.return_value = ctrl
            event.MetaDown.return_value = False
            event.ShiftDown.return_value = shift
            event.AltDown.return_value = alt
            event.GetKeyCode.return_value = key
            event.GetRawKeyCode.return_value = raw_key

            try:
                handle_language_independent_shortcuts(frame, event)
            except Exception as e:
                self.fail(f"Shortcut key={key} (ctrl={ctrl}, shift={shift}, alt={alt}) crashed: {e}")


if __name__ == "__main__":
    unittest.main()
