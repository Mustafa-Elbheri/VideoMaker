import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_maker import app_state, localization, windows_shell_integration


class ApplicationNameTest(unittest.TestCase):
    def test_custom_app_name_is_normalized_and_can_be_cleared(self):
        store = {}

        with patch.object(app_state, "read_preferences", lambda: dict(store)), patch.object(
            app_state, "write_preferences", lambda data: store.clear() or store.update(data)
        ):
            app_state.set_custom_app_name("  My\nVideo   Tool  ")
            self.assertEqual(app_state.get_custom_app_name(), "My Video Tool")

            app_state.set_custom_app_name("   ")
            self.assertEqual(app_state.get_custom_app_name(), "")

    def test_desktop_shortcut_uses_new_name_and_removes_previous_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            desktop = Path(temporary) / "Desktop"
            desktop.mkdir()
            executable = Path(temporary) / "VideoMaker.exe"
            executable.write_text("", encoding="utf-8")
            old_shortcut = desktop / "Old Name.lnk"
            old_shortcut.write_text("old", encoding="utf-8")
            default_shortcuts = [
                desktop / "صانع الفيديو.lnk",
                desktop / "Video Maker.lnk",
                desktop / "Créateur vidéo.lnk",
            ]
            for default_shortcut in default_shortcuts:
                default_shortcut.write_text("old default", encoding="utf-8")

            def fake_create(shortcut_path, _executable, display_name):
                shortcut_path.write_text(display_name, encoding="utf-8")
                return True

            with patch.object(windows_shell_integration, "_desktop_directory", lambda: desktop), patch.object(
                windows_shell_integration, "_create_shortcut_with_powershell", fake_create
            ), patch.object(windows_shell_integration, "_create_shortcut_with_cscript", lambda *_args: False):
                shortcut = windows_shell_integration._create_desktop_shortcut(
                    str(executable),
                    "New Name",
                    previous_display_name="Old Name",
                )

            self.assertEqual(shortcut, desktop / "New Name.lnk")
            self.assertTrue(shortcut.is_file())
            self.assertFalse(old_shortcut.exists())
            for default_shortcut in default_shortcuts:
                self.assertFalse(default_shortcut.exists())

    def test_desktop_shortcut_keeps_current_localized_default_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            desktop = Path(temporary) / "Desktop"
            desktop.mkdir()
            executable = Path(temporary) / "VideoMaker.exe"
            executable.write_text("", encoding="utf-8")
            current_shortcut = desktop / "Video Maker.lnk"
            current_shortcut.write_text("current", encoding="utf-8")
            old_shortcut = desktop / "Old Name.lnk"
            old_shortcut.write_text("old", encoding="utf-8")

            def fake_create(shortcut_path, _executable, display_name):
                shortcut_path.write_text(display_name, encoding="utf-8")
                return True

            with patch.object(windows_shell_integration, "_desktop_directory", lambda: desktop), patch.object(
                windows_shell_integration, "_create_shortcut_with_powershell", fake_create
            ), patch.object(windows_shell_integration, "_create_shortcut_with_cscript", lambda *_args: False):
                shortcut = windows_shell_integration._create_desktop_shortcut(
                    str(executable),
                    "Video Maker",
                    previous_display_name="Old Name",
                )

            self.assertEqual(shortcut, current_shortcut)
            self.assertTrue(current_shortcut.is_file())
            self.assertEqual(current_shortcut.read_text(encoding="utf-8"), "Video Maker")
            self.assertFalse(old_shortcut.exists())

    def test_send_to_shortcut_removes_previous_custom_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            send_to = Path(temporary) / "SendTo"
            send_to.mkdir()
            executable = Path(temporary) / "VideoMaker.exe"
            executable.write_text("", encoding="utf-8")
            old_shortcut = send_to / "Old Name.lnk"
            old_shortcut.write_text("old", encoding="utf-8")

            def fake_create(shortcut_path, _executable, display_name):
                shortcut_path.write_text(display_name, encoding="utf-8")
                return True

            with patch.object(windows_shell_integration, "_send_to_directory", lambda: send_to), patch.object(
                windows_shell_integration, "_create_shortcut_with_powershell", fake_create
            ), patch.object(windows_shell_integration, "_create_shortcut_with_cscript", lambda *_args: False):
                shortcut = windows_shell_integration._create_send_to_shortcut(
                    str(executable),
                    "New Name",
                    previous_display_name="Old Name",
                )

            self.assertEqual(shortcut, send_to / "New Name.lnk")
            self.assertTrue(shortcut.is_file())
            self.assertFalse(old_shortcut.exists())

    def test_application_name_strings_are_translated(self):
        strings = [
            "تغيير اسم التطبيق",
            "اسم التطبيق",
            "اكتب اسم التطبيق",
            "اترك الاسم فارغا لاستخدام اسم التطبيق الحالي",
            "تم حفظ اسم التطبيق",
            "تم تحديث اختصار سطح المكتب",
            "تم حفظ اسم التطبيق وتحديث اختصار سطح المكتب",
        ]
        for language in ("en", "fr"):
            with patch.object(localization, "get_language", lambda language=language: language):
                untranslated = [text for text in strings if localization.tr(text) == text]
            self.assertEqual(untranslated, [])


if __name__ == "__main__":
    unittest.main()
