import os
import tempfile
import unittest
from unittest.mock import patch

from video_maker import auto_subtitles_module
from video_maker.auto_subtitles_module import GroqKeyManager

VALID_KEY = "gsk_abcdefghij1234567890"


class GroqKeyPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.backup_path = os.path.join(self.temp_dir.name, "groq_keys.json")
        self.prefs_patch = patch.multiple(
            auto_subtitles_module,
            read_preferences=lambda: dict(self.store),
            write_preferences=lambda data: self.store.clear() or self.store.update(data),
        )
        self.backup_patch = patch.object(GroqKeyManager, "_backup_path", lambda: self.backup_path)
        self.env_patch = patch.dict(os.environ, {"GROQ_API_KEY": ""})
        self.prefs_patch.start()
        self.backup_patch.start()
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.backup_patch.stop()
        self.prefs_patch.stop()
        self.temp_dir.cleanup()

    def test_add_key_persists_to_preferences_and_backup(self):
        self.assertTrue(GroqKeyManager.add_key(VALID_KEY))
        self.assertIn(VALID_KEY, GroqKeyManager.get_keys())
        self.assertEqual(self.store[GroqKeyManager.CONFIG_KEY], [VALID_KEY])
        with open(self.backup_path, "r", encoding="utf-8") as file:
            backup = file.read()
        self.assertIn(VALID_KEY, backup)

    def test_duplicate_key_is_not_added_twice(self):
        self.assertTrue(GroqKeyManager.add_key(VALID_KEY))
        self.assertFalse(GroqKeyManager.add_key(VALID_KEY))
        self.assertEqual(len(GroqKeyManager.get_keys()), 1)

    def test_remove_key_updates_preferences_and_backup(self):
        second_key = "gsk_second_key_123456789"
        GroqKeyManager.add_key(VALID_KEY)
        GroqKeyManager.add_key(second_key)
        self.assertTrue(GroqKeyManager.remove_key(0))
        remaining = GroqKeyManager.get_keys()
        self.assertNotIn(VALID_KEY, remaining)
        self.assertIn(second_key, remaining)
        with open(self.backup_path, "r", encoding="utf-8") as file:
            backup = file.read()
        self.assertNotIn(VALID_KEY, backup)
        self.assertIn(second_key, backup)

    def test_get_keys_restores_from_backup_after_preferences_cleared(self):
        GroqKeyManager.add_key(VALID_KEY)
        self.assertEqual(GroqKeyManager.get_keys(), [VALID_KEY])

        self.store.pop(GroqKeyManager.CONFIG_KEY, None)
        self.assertEqual(GroqKeyManager.get_keys(), [VALID_KEY])
        self.assertEqual(self.store[GroqKeyManager.CONFIG_KEY], [VALID_KEY])

    def test_invalid_key_format_is_rejected(self):
        self.assertFalse(GroqKeyManager.validate_key_format("not-a-gsk-key"))
        self.assertTrue(GroqKeyManager.validate_key_format(VALID_KEY))


if __name__ == "__main__":
    unittest.main()
