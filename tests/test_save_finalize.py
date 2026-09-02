import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_maker.player_modules import save as save_module


class SaveFinalizeTest(unittest.TestCase):
    def test_access_denied_replace_saves_locked_output_as_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render_path = root / "movie.partial-123.mp4"
            final_path = root / "movie.mp4"
            render_path.write_bytes(b"rendered")
            final_path.write_bytes(b"locked")
            real_replace = os.replace

            def replace_or_deny(source, destination):
                if Path(destination) == final_path:
                    raise PermissionError(5, "Access is denied", str(destination))
                return real_replace(source, destination)

            with patch.object(save_module.os, "replace", side_effect=replace_or_deny):
                actual_path = save_module.finalize_rendered_output(str(render_path), str(final_path))

            self.assertEqual(Path(actual_path).name, "movie saved copy.mp4")
            self.assertEqual(Path(actual_path).read_bytes(), b"rendered")
            self.assertEqual(final_path.read_bytes(), b"locked")
            self.assertFalse(render_path.exists())

    def test_access_denied_replace_to_new_name_copies_to_requested_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render_path = root / "movie.partial-123.mp4"
            final_path = root / "new name.mp4"
            render_path.write_bytes(b"rendered")

            def deny_replace(_source, _destination):
                raise PermissionError(5, "Access is denied", str(_destination))

            with patch.object(save_module.os, "replace", side_effect=deny_replace):
                actual_path = save_module.finalize_rendered_output(str(render_path), str(final_path))

            self.assertEqual(Path(actual_path), final_path)
            self.assertEqual(final_path.read_bytes(), b"rendered")
            self.assertFalse(render_path.exists())


if __name__ == "__main__":
    unittest.main()
