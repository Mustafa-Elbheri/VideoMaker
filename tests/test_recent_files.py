import os
import tempfile
import unittest
from unittest.mock import patch

from video_maker import recent_files
from video_maker.player import VideoPlayer


class RecentFilesTest(unittest.TestCase):
    def setUp(self):
        self.preferences = {}
        self.patches = [
            patch("video_maker.recent_files.read_preferences", lambda: dict(self.preferences)),
            patch("video_maker.recent_files.write_preferences", self._write_preferences),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def _write_preferences(self, data):
        self.preferences = dict(data)

    def _touch(self, folder, name):
        path = os.path.join(folder, name)
        with open(path, "wb") as file:
            file.write(b"test")
        return path

    def test_remember_recent_files_keeps_latest_first_without_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self._touch(folder, "first.mp4")
            second = self._touch(folder, "second.wav")

            recent_files.remember_recent_file(first)
            recent_files.remember_recent_file(second)
            recent_files.remember_recent_file(first)

            self.assertEqual([item.path for item in recent_files.list_recent_files()], [first, second])

    def test_recent_files_ignore_missing_and_unsupported_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            video = self._touch(folder, "clip.mp4")
            unsupported = self._touch(folder, "notes.txt")
            missing = os.path.join(folder, "missing.wav")
            self.preferences[recent_files.RECENT_FILES_KEY] = [missing, unsupported, video]

            self.assertEqual([item.path for item in recent_files.list_recent_files()], [video])

    def test_remember_recent_files_preserves_selected_open_order(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self._touch(folder, "one.mp4")
            second = self._touch(folder, "two.mp4")

            recent_files.remember_recent_files([first, second])

            self.assertEqual([item.path for item in recent_files.list_recent_files()], [first, second])

    def test_open_recent_file_uses_normal_open_when_workspace_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._touch(folder, "voice.wav")
            opened = []

            class Player:
                def has_video(self):
                    return False

                def OnOpenMedia(self, selected_path):
                    opened.append(selected_path)

            player = Player()

            with patch("video_maker.clipboard_media_paste.paste_file_path", return_value=True) as paste:
                self.assertTrue(recent_files.open_recent_file(player, path))

            paste.assert_not_called()
            self.assertEqual(opened, [path])
            self.assertEqual([item.path for item in recent_files.list_recent_files()], [path])

    def test_open_recent_file_uses_computer_paste_path_behavior_when_project_is_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._touch(folder, "voice.wav")

            class Player:
                def has_video(self):
                    return True

            player = Player()

            with patch("video_maker.clipboard_media_paste.paste_file_path", return_value=True) as paste:
                self.assertTrue(recent_files.open_recent_file(player, path))

            paste.assert_called_once_with(player, path)

    def test_clear_recent_files_removes_only_recent_list(self):
        self.preferences = {
            recent_files.RECENT_FILES_KEY: ["one.mp4"],
            "language": "ar",
        }

        recent_files.clear_recent_files()

        self.assertNotIn(recent_files.RECENT_FILES_KEY, self.preferences)
        self.assertEqual(self.preferences["language"], "ar")

    def test_player_recent_menu_handler_delegates_to_recent_file_module(self):
        player = VideoPlayer.__new__(VideoPlayer)
        with patch("video_maker.player.open_recent_file", return_value=True) as opener:
            player.OnOpenRecentFile("clip.mp4")

        opener.assert_called_once_with(player, "clip.mp4")

    def test_player_clear_recent_files_refreshes_menu_and_announces(self):
        player = VideoPlayer.__new__(VideoPlayer)
        refreshed = []
        spoken = []
        player.refresh_menu_bar = lambda: refreshed.append(True)
        player.say = lambda message: spoken.append(message)

        with patch("video_maker.player.clear_recent_files") as clear:
            player.OnClearRecentFiles()

        clear.assert_called_once_with()
        self.assertEqual(refreshed, [True])
        self.assertTrue(spoken)

    def test_recent_file_text_is_translated_to_english_and_french(self):
        expected = {
            "en": {
                "الملفات الأخيرة": "Recent files",
                "لا توجد ملفات أخيرة": "No recent files",
                "تفريغ القائمة": "Clear list",
                "تم تفريغ قائمة الملفات الأخيرة": "Recent files list cleared",
                "الملف لم يعد موجودا على الكمبيوتر": "The file no longer exists on the computer",
            },
            "fr": {
                "الملفات الأخيرة": "Fichiers récents",
                "لا توجد ملفات أخيرة": "Aucun fichier récent",
                "تفريغ القائمة": "Vider la liste",
                "تم تفريغ قائمة الملفات الأخيرة": "Liste des fichiers récents vidée",
                "الملف لم يعد موجودا على الكمبيوتر": "Le fichier n'existe plus sur l'ordinateur",
            },
        }

        for language, translations in expected.items():
            with patch("video_maker.localization.get_language", return_value=language):
                for source, translated in translations.items():
                    self.assertEqual(recent_files.tr(source), translated)


if __name__ == "__main__":
    unittest.main()
