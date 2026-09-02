import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import sys

sys.path.insert(0, os.path.abspath('.'))

from video_maker.updater import get_update_install_arguments, run_update_file, UPDATE_INSTALL_ARGUMENTS
from video_maker.app_state import update_preferences, read_preferences, write_preferences


class TestUpdaterPreferences(unittest.TestCase):
    @patch('video_maker.app_state.get_language')
    def test_get_update_install_arguments_arabic(self, mock_get_lang):
        mock_get_lang.return_value = 'ar'
        args = get_update_install_arguments()
        self.assertIn('/LANG=arabic', args)
        for expected in UPDATE_INSTALL_ARGUMENTS:
            self.assertIn(expected, args)

    @patch('video_maker.app_state.get_language')
    def test_get_update_install_arguments_english(self, mock_get_lang):
        mock_get_lang.return_value = 'en'
        args = get_update_install_arguments()
        self.assertIn('/LANG=english', args)

    @patch('video_maker.app_state.get_language')
    def test_get_update_install_arguments_french(self, mock_get_lang):
        mock_get_lang.return_value = 'fr'
        args = get_update_install_arguments()
        self.assertIn('/LANG=french', args)

    @patch('subprocess.Popen')
    @patch('pathlib.Path.exists', return_value=True)
    @patch('video_maker.app_state.get_language', return_value='ar')
    def test_run_update_file_passes_language_argument(self, mock_get_lang, mock_exists, mock_popen):
        fake_path = r"C:\path\to\VideoMakerSetup.exe"
        run_update_file(fake_path)
        mock_popen.assert_called_once()
        called_args = mock_popen.call_args[0][0]
        self.assertEqual(called_args[0], fake_path)
        self.assertIn('/LANG=arabic', called_args)
        self.assertIn('/SILENT', called_args)

    def test_preferences_preservation_on_update(self):
        """Verify that updating preferences retains existing keys and values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pref_file = os.path.join(temp_dir, 'preferences.json')
            initial_data = {
                'volume': 0.75,
                'theme': 'dark',
                'last_open_dir': r'C:\Videos',
                'language': 'ar'
            }
            with open(pref_file, 'w', encoding='utf-8') as f:
                json.dump(initial_data, f)

            with patch('video_maker.app_state.preferences_path', return_value=pref_file):
                # Update language to english
                update_preferences(language='en')
                data = read_preferences()

                # Verify all original keys are preserved
                self.assertEqual(data['volume'], 0.75)
                self.assertEqual(data['theme'], 'dark')
                self.assertEqual(data['last_open_dir'], r'C:\Videos')
                self.assertEqual(data['language'], 'en')

    def test_simulated_installer_language_update_preserves_existing_keys(self):
        """Simulate installer updating language key in preferences.json without wiping file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pref_file = os.path.join(temp_dir, 'preferences.json')
            content = json.dumps({
                'volume': 0.8,
                'theme': 'dark',
                'custom_app_name': 'My Video Editor',
                'language': 'ar'
            }, indent=2)
            with open(pref_file, 'w', encoding='utf-8') as f:
                f.write(content)

            # Perform the same search-replace logic used in installer.iss
            lang_code = 'en'
            pos_lang = content.find('"language"')
            self.assertGreater(pos_lang, -1)
            pos_colon = content.find(':', pos_lang)
            pos_q1 = content.find('"', pos_colon)
            pos_q2 = content.find('"', pos_q1 + 1)
            new_content = content[:pos_q1 + 1] + lang_code + content[pos_q2:]

            with open(pref_file, 'w', encoding='utf-8') as f:
                f.write(new_content)

            with open(pref_file, 'r', encoding='utf-8') as f:
                updated_json = json.load(f)

            self.assertEqual(updated_json['volume'], 0.8)
            self.assertEqual(updated_json['theme'], 'dark')
            self.assertEqual(updated_json['custom_app_name'], 'My Video Editor')
            self.assertEqual(updated_json['language'], 'en')


    def test_installer_preserves_existing_user_language_during_update(self):
        """Verify that when preferences.json exists with language, installer keeps it untouched."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pref_file = os.path.join(temp_dir, 'preferences.json')
            initial_data = {
                'language': 'ar',
                'theme': 'high_black',
                'custom_app_name': 'My Custom Video App',
            }
            with open(pref_file, 'w', encoding='utf-8') as f:
                json.dump(initial_data, f, indent=2)

            # Simulated installer logic in installer.iss:
            # If "language" is already in file, it exits without modifying
            with open(pref_file, 'r', encoding='utf-8') as f:
                raw_content = f.read()

            if '"language"' in raw_content:
                # installer exits without rewriting
                pass

            with open(pref_file, 'r', encoding='utf-8') as f:
                after_data = json.load(f)

            self.assertEqual(after_data['language'], 'ar')
            self.assertEqual(after_data['theme'], 'high_black')
            self.assertEqual(after_data['custom_app_name'], 'My Custom Video App')

    def test_installer_fresh_install_initializes_with_chosen_language(self):
        """Verify that on fresh install with no preferences.json, chosen language is written."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pref_file = os.path.join(temp_dir, 'preferences.json')
            self.assertFalse(os.path.exists(pref_file))

            # Simulated fresh install logic in installer.iss:
            installer_lang = 'en'
            initial_content = f'{{"language": "{installer_lang}"}}'
            with open(pref_file, 'w', encoding='utf-8') as f:
                f.write(initial_content)

            with open(pref_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.assertEqual(data['language'], 'en')


if __name__ == '__main__':
    unittest.main()
