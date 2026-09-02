import ast
import os
import re
import unittest


class CodebaseIntegrityScannerTest(unittest.TestCase):
    """Scans all Python files in video_maker using AST to detect any syntax errors

    or unimported localization functions.
    """
    def test_all_python_files_parse_without_syntax_errors(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "video_maker"))
        py_files = []
        for dirpath, _, filenames in os.walk(root_dir):
            for filename in filenames:
                if filename.endswith(".py"):
                    py_files.append(os.path.join(dirpath, filename))

        self.assertGreater(len(py_files), 20, "Should scan all project Python files")

        for py_path in py_files:
            rel_name = os.path.relpath(py_path, root_dir)
            with open(py_path, "r", encoding="utf-8") as f:
                source = f.read()
            try:
                tree = ast.parse(source, filename=py_path)
                self.assertIsNotNone(tree)
            except SyntaxError as e:
                self.fail(f"Syntax error in {rel_name}: {e}")

    def test_all_modules_using_tr_import_it_properly(self):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "video_maker"))
        for dirpath, _, filenames in os.walk(root_dir):
            for filename in filenames:
                if filename.endswith(".py") and filename != "localization.py":
                    py_path = os.path.join(dirpath, filename)
                    rel_name = os.path.relpath(py_path, root_dir)
                    with open(py_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Match actual tr(...) function call with word boundary
                    if re.search(r'\btr\s*\(', content):
                        has_tr_import = bool(
                            re.search(r'from\s+(video_maker\.)?localization\s+import\s+.*?\btr\b', content)
                            or re.search(r'from\s+(video_maker\.)?player_modules\.shared\s+import\s+\*', content)
                            or re.search(r'\bimport\s+tr\b', content)
                            or re.search(r'def\s+tr\s*\(', content)
                        )
                        self.assertTrue(
                            has_tr_import,
                            f"File '{rel_name}' calls tr() but does NOT import or define 'tr'!"
                        )


if __name__ == "__main__":
    unittest.main()
