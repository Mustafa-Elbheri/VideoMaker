from datetime import datetime
from pathlib import Path
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_DIR = PROJECT_ROOT / "video_maker"


def project_zip_path():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return PROJECT_ROOT / f"video_maker_code_{timestamp}.zip"


def iter_code_files(source_dir=None):
    source_dir = Path(source_dir) if source_dir is not None else SOURCE_DIR
    for path in sorted(source_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def compress_project():
    if not SOURCE_DIR.is_dir():
        raise FileNotFoundError(f"Project folder was not found: {SOURCE_DIR}")

    files = list(iter_code_files())
    if not files:
        raise RuntimeError("No Python code files were found in video_maker.")

    output_path = project_zip_path()
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(PROJECT_ROOT).as_posix())
    return output_path, len(files)


def show_message(message, title, is_error=False):
    try:
        import wx

        app = wx.App(False)
        style = wx.OK | (wx.ICON_ERROR if is_error else wx.ICON_INFORMATION)
        wx.MessageBox(message, title, style)
        app.Destroy()
    except Exception:
        print(f"{title}: {message}")


def main():
    try:
        output_path, file_count = compress_project()
    except Exception as error:
        show_message(str(error), "Compress Project", is_error=True)
        return 1

    show_message(
        f"Project code compressed successfully.\nFiles: {file_count}\nOutput: {output_path}",
        "Compress Project",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
