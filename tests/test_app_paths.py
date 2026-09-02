import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath("."))

from video_maker import app_paths
from video_maker.work_sessions import APP_FOLDER


class ProgramPathsTest(unittest.TestCase):
    def test_workspace_and_peaks_built_under_documents(self):
        with tempfile.TemporaryDirectory(prefix="avm_docs_") as tmp:
            with mock.patch.object(app_paths, "documents_root", return_value=tmp):
                workspace = app_paths.program_workspace_root()
                peaks = app_paths.peaks_root()
                self.assertTrue(os.path.isdir(workspace))
                self.assertTrue(os.path.isdir(peaks))
                self.assertEqual(os.path.dirname(workspace), os.path.abspath(tmp))
                self.assertEqual(os.path.basename(workspace), APP_FOLDER)
                self.assertEqual(os.path.basename(peaks), "peaks_data")

    def test_documents_root_returns_existing_directory(self):
        root = app_paths.documents_root()
        self.assertTrue(root)
        self.assertTrue(os.path.isdir(root))

    def test_imported_media_lives_inside_workspace(self):
        with tempfile.TemporaryDirectory(prefix="avm_docs_") as tmp:
            with mock.patch.object(app_paths, "documents_root", return_value=tmp):
                imported = app_paths.imported_media_root()
                self.assertTrue(os.path.isdir(imported))
                self.assertEqual(os.path.basename(os.path.dirname(imported)), APP_FOLDER)
                self.assertEqual(os.path.basename(imported), "media")

    def test_bundled_sounds_root_falls_back_to_installed_app_files(self):
        with tempfile.TemporaryDirectory(prefix="avm_install_") as tmp:
            executable = os.path.join(tmp, "VideoMaker.exe")
            sounds = os.path.join(tmp, "app_files", "assets", "sounds")
            os.makedirs(sounds)
            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
                sys, "executable", executable
            ), mock.patch.object(app_paths, "bundled_path", return_value=app_paths.Path(tmp) / "missing"):
                self.assertTrue(os.path.samefile(app_paths.bundled_sounds_root(), sounds))


if __name__ == "__main__":
    unittest.main()
