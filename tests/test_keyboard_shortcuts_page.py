import unittest
from pathlib import Path


class KeyboardShortcutsPageTest(unittest.TestCase):
    def test_caption_review_shortcuts_are_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "بدء استخراج وتحرير النطق في الجزء المحدد ضمن ميزة كتابة النطق على الشاشة.",
            "داخل نافذة مراجعة النطق: تشغيل معاينة القطعة الحالية.",
            "داخل نافذة مراجعة النطق: الانتقال إلى القطعة السابقة.",
            "داخل نافذة مراجعة النطق: الانتقال إلى القطعة التالية أو تطبيق التعديلات عند آخر قطعة.",
            "Start speech-to-screen extraction and editing for the selected part.",
            "In the speech-to-screen review window: preview the current segment.",
            "In the speech-to-screen review window: move to the previous segment.",
            "In the speech-to-screen review window: move to the next segment or apply edits on the last segment.",
            "Démarrer l'extraction et la modification de la parole à l'écran pour la partie sélectionnée.",
            "Dans la fenêtre de révision de la parole à l'écran : écouter un aperçu du segment actuel.",
            "Dans la fenêtre de révision de la parole à l'écran : aller au segment précédent.",
            "Dans la fenêtre de révision de la parole à l'écran : aller au segment suivant ou appliquer les modifications au dernier segment.",
        ]

        for phrase in expected_phrases:
            self.assertIn(phrase, text)

    def test_change_application_name_shortcut_is_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "تغيير اسم التطبيق وإنشاء أو تحديث اختصار سطح المكتب بالاسم الجديد.",
            "Change the application name and create or update the desktop shortcut with the new name.",
            "Modifier le nom de l'application et créer ou mettre à jour le raccourci du bureau avec le nouveau nom.",
        ]

        self.assertGreaterEqual(text.count("Ctrl+Shift+F2"), 3)
        for phrase in expected_phrases:
            self.assertIn(phrase, text)

    def test_timeline_mute_is_not_a_bare_key_shortcut(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        self.assertNotIn('["K",', text)

    def test_end_point_k_shortcut_is_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "د أو K أو Ctrl+K",
            "] or K or Ctrl+K",
            "] ou K ou Ctrl+K",
        ]
        for phrase in expected_phrases:
            self.assertIn(phrase, text)

    def test_program_mode_toggle_shortcut_is_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "التبديل بين الوضع العادي والوضع الاحترافي.",
            "Toggle between normal and professional mode.",
            "Basculer entre le mode normal et le mode professionnel.",
        ]
        self.assertGreaterEqual(text.count("Shift+نقطة"), 1)
        self.assertGreaterEqual(text.count("Shift+Period"), 1)
        self.assertGreaterEqual(text.count("Shift+Point"), 1)
        for phrase in expected_phrases:
            self.assertIn(phrase, text)

    def test_insert_timeline_audio_shortcut_is_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "إدراج صوت عند الموضع الحالي في الخط الزمني.",
            "Insert audio at the current position in the timeline.",
            "Insérer un audio à la position actuelle dans la chronologie.",
        ]
        self.assertGreaterEqual(text.count("Ctrl+B"), 3)
        for phrase in expected_phrases:
            self.assertIn(phrase, text)

    def test_insert_timeline_silence_shortcut_is_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "إدراج صمت عند الموضع الحالي في الخط الزمني.",
            "Insert silence at the current position in the timeline.",
            "Insérer un silence à la position actuelle dans la chronologie.",
        ]
        self.assertGreaterEqual(text.count("Ctrl+D"), 3)
        for phrase in expected_phrases:
            self.assertIn(phrase, text)

    def test_mute_background_selection_shortcut_is_documented_in_all_languages(self):
        page = Path(__file__).resolve().parents[1] / "keyboard_shortcuts.html"
        text = page.read_text(encoding="utf-8")

        expected_phrases = [
            "كتم صوت الخلفية الصوتية في الجزء المحدد في الوضع العادي والوضع الاحترافي.",
            "Mute background audio in the selected part in normal and professional mode.",
            "Couper le fond sonore dans la partie sélectionnée en mode normal et professionnel.",
        ]
        self.assertGreaterEqual(text.count('["B",'), 3)
        for phrase in expected_phrases:
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
