import os
import shutil
import sys
from pathlib import Path

from video_maker.work_sessions import APP_FOLDER, app_data_root


def bundled_root():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def bundled_path(*parts):
    return bundled_root().joinpath(*parts)


def user_data_path(*parts):
    path = Path(app_data_root()).joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def user_effects_root():
    path = Path(app_data_root()) / "effects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_effects_root():
    return bundled_path("assets", "effects")


def user_sounds_root():
    path = Path(app_data_root()) / "sounds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def imported_media_root():
    path = program_workspace_root() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def documents_root():
    """مجلد المستندات الحقيقي على أي نظام (Windows/macOS/Linux) ديناميكياً."""
    user_profile = os.environ.get("USERPROFILE", "")
    home = os.path.expanduser("~")
    candidates = []
    if user_profile:
        candidates.append(os.path.join(user_profile, "Documents"))
    candidates.append(os.path.join(home, "Documents"))
    candidates.append(home)
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return candidates[-1]


def program_workspace_root():
    """مجلد عمل البرنامج داخل المستندات: Documents/AccessibleVideoMaker."""
    path = Path(documents_root()) / APP_FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def peaks_root():
    """مخزن بيانات الوسائط/التحليل (بنية Peaks) داخل مجلد عمل البرنامج.

    تُخزَّن هنا ملفات تحليل الوسائط والبيانات المساعدة (مثل أشكال الموجات
    والأصول المشتقة) في مجلد حقيقي دائم بجوار أعمال المستخدم بدلاً من
    المجلدات المؤقتة.
    """
    path = program_workspace_root() / "peaks_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_sounds_root():
    candidates = [bundled_path("assets", "sounds")]
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                executable_dir / "app_files" / "assets" / "sounds",
                executable_dir / "assets" / "sounds",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def ensure_user_effects():
    destination = user_effects_root()
    marker = destination / "accessible_manifest.json"
    if marker.exists():
        return destination
    source = bundled_effects_root()
    if source.exists():
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir():
                if not target.exists():
                    shutil.copytree(item, target)
            elif not target.exists():
                shutil.copy2(item, target)
    return destination


def safe_filename(name, extension):
    invalid = '<>:"/\\|?*'
    cleaned = "".join(" " if character in invalid or ord(character) < 32 else character for character in name)
    cleaned = " ".join(cleaned.strip().split())
    if not cleaned:
        cleaned = "effect"
    extension = extension if extension.startswith(".") else f".{extension}"
    return f"{cleaned}{extension}"


def unique_path(folder, filename):
    path = folder / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = folder / f"{stem} {index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1

def ffmpeg_binary():
    import os
    import shutil
    executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = bundled_path(executable)
    if os.path.exists(bundled):
        return str(bundled)
    # Fallback to imageio_ffmpeg if available
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    # Fallback to shutil.which to find it in PATH
    path_exec = shutil.which(executable)
    if path_exec:
        return path_exec
    return executable


def ffprobe_binary():
    import os
    import shutil
    executable = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    bundled = bundled_path(executable)
    if os.path.exists(bundled):
        return str(bundled)
    ffmpeg = ffmpeg_binary()
    candidate = os.path.join(os.path.dirname(os.path.abspath(ffmpeg)), executable)
    if os.path.exists(candidate):
        return candidate
    path_exec = shutil.which(executable)
    if path_exec:
        return path_exec
    return executable
