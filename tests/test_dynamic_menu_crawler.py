import os
import unittest
import webbrowser
from unittest.mock import MagicMock, patch
import wx

from video_maker.player import VideoPlayer
from video_maker.program_modes import NORMAL_MODE, PROFESSIONAL_MODE, set_program_mode


class DynamicMenuCrawlerTest(unittest.TestCase):
    """Dynamically traverses all top-level menus and submenus, invoking each menu item

    to ensure no handler raises unhandled NameError, AttributeError, or TypeError.
    """
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App()
        cls.audio_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "test.wav"))
        cls.video_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "test.mp4"))

    def _collect_menu_items(self, menu_bar):
        items = []
        def traverse_menu(menu, prefix=""):
            for item in menu.GetMenuItems():
                if item.IsSeparator():
                    continue
                sub = item.GetSubMenu()
                if sub is not None:
                    traverse_menu(sub, prefix + item.GetItemLabelText() + " > ")
                else:
                    items.append((item.GetId(), prefix + item.GetItemLabelText()))
        for i in range(menu_bar.GetMenuCount()):
            title = menu_bar.GetMenuLabel(i)
            menu = menu_bar.GetMenu(i)
            traverse_menu(menu, title + " > ")
        return items

    def _simulate_menu_dispatch(self, player, item_id, item_label):
        event = wx.CommandEvent(wx.EVT_MENU.typeId, item_id)
        with patch.object(wx, "MessageBox", return_value=wx.OK), \
             patch.object(wx, "GetTextFromUser", return_value=""), \
             patch.object(wx, "GetSingleChoice", return_value=""), \
             patch.object(wx, "GetNumberFromUser", return_value=0), \
             patch.object(wx.Dialog, "ShowModal", return_value=wx.ID_CANCEL), \
             patch.object(wx.FileDialog, "ShowModal", return_value=wx.ID_CANCEL), \
             patch.object(wx.TextEntryDialog, "ShowModal", return_value=wx.ID_CANCEL), \
             patch.object(wx.SingleChoiceDialog, "ShowModal", return_value=wx.ID_CANCEL), \
             patch.object(webbrowser, "open", return_value=True), \
             patch.object(player, "say", return_value=None), \
             patch("video_maker.app_state.write_preferences", return_value=None), \
             patch("video_maker.app_state.update_preferences", return_value=None):
            try:
                player.GetEventHandler().ProcessEvent(event)
            except Exception as e:
                self.fail(f"Menu item (ID: {item_id}) crashed with error: {type(e).__name__}: {e}")

    def test_crawl_all_menus_with_no_file_open(self):
        set_program_mode(NORMAL_MODE)
        player = VideoPlayer(None)
        try:
            menu_bar = player.GetMenuBar()
            self.assertIsNotNone(menu_bar, "MenuBar must exist")
            items = self._collect_menu_items(menu_bar)
            self.assertGreater(len(items), 20, "Should have discovered all top-level menu items")
            for item_id, item_label in items:
                if item_id in (wx.ID_EXIT,):
                    continue
                self._simulate_menu_dispatch(player, item_id, item_label)
        finally:
            player.Destroy()

    def test_crawl_all_menus_with_audio_file_open_normal_mode(self):
        set_program_mode(NORMAL_MODE)
        player = VideoPlayer(None)
        try:
            player.OnOpenAudio(self.audio_path)
            player.start_time = 0.5
            player.end_time = 1.5
            menu_bar = player.GetMenuBar()
            items = self._collect_menu_items(menu_bar)
            for item_id, item_label in items:
                if item_id in (wx.ID_EXIT,):
                    continue
                self._simulate_menu_dispatch(player, item_id, item_label)
        finally:
            player.Destroy()

    def test_crawl_all_menus_with_video_file_open_pro_mode(self):
        set_program_mode(PROFESSIONAL_MODE)
        player = VideoPlayer(None)
        try:
            player.OnOpenVideo(self.video_path)
            player.start_time = 0.0
            player.end_time = 0.5
            menu_bar = player.GetMenuBar()
            items = self._collect_menu_items(menu_bar)
            for item_id, item_label in items:
                if item_id in (wx.ID_EXIT,):
                    continue
                self._simulate_menu_dispatch(player, item_id, item_label)
        finally:
            set_program_mode(NORMAL_MODE)
            player.Destroy()


if __name__ == "__main__":
    unittest.main()
