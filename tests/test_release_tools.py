import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath("."))

import release_tools


class ReleaseToolsRemovePathTest(unittest.TestCase):
    def test_remove_path_deletes_readonly_build_folder(self):
        with tempfile.TemporaryDirectory(prefix="avm_build_") as temporary:
            build_root = Path(temporary) / ".all_in_one_build"
            licenses = build_root / "licenses"
            licenses.mkdir(parents=True)
            license_file = licenses / "license_ar.txt"
            license_file.write_text("license", encoding="utf-8")

            os.chmod(license_file, stat.S_IREAD)
            os.chmod(licenses, stat.S_IREAD | stat.S_IEXEC)

            release_tools.remove_path(build_root, attempts=1)

            self.assertFalse(build_root.exists())


if __name__ == "__main__":
    unittest.main()
