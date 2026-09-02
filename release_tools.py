import argparse
import contextlib
import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "VideoMaker"
REPOSITORY = "Mustafa-Elbheri/VideoMaker"

BUILD_ROOT = PROJECT_ROOT / ".all_in_one_build"
WORK_PATH = BUILD_ROOT / "work"
DIST_PATH = BUILD_ROOT / "dist"
SPEC_PATH = BUILD_ROOT / "spec"
PREVIOUS_RELEASE_PATH = BUILD_ROOT / "previous_release"
LICENSES_PATH = BUILD_ROOT / "licenses"

FINAL_EXE = PROJECT_ROOT / "VideoMaker.exe"
FINAL_APP_FILES = PROJECT_ROOT / "app_files"
OLD_RUNTIME = PROJECT_ROOT / "_runtime"
SETUP_PATH = PROJECT_ROOT / "VideoMakerSetup.exe"
ONLINE_SETUP_PATH = PROJECT_ROOT / "installer_dist" / "VideoMakerOnlineSetup.exe"
RELEASE_REPO_DIR = PROJECT_ROOT / ".github_release_repo"

EMBEDDED_LICENSES = {
    "license_ar.txt": """باستخدامك لهذا البرنامج فأنت توافق على ما يلي:

شرط واحد فقط لا غير: استخدامه بما يرضي الله عز وجل.

المبرمج لهذا المنتج يرى حرمة استخدام هذا المنتج بأي شكل لا يرضي الله عز وجل، سواء كان هذا يتعلق بالمعازف أو بنشر محتوى غير متوافق مع منهج أهل السنة والجماعة.

نسعد جدا بمساعدتك في تطوير هذا البرنامج، ونأمل أن يكون عونا لك في تحقيق أهدافك بما يتماشى مع القيم والأخلاق.

للتواصل والمزيد من المعلومات، يرجى زيارة صفحة المبرمج.
""",
    "license_en.txt": """By using this program, you agree to the following:

Only one condition: use it in a way that pleases Allah Almighty.

The developer of this product considers it forbidden to use this product in any way that does not please Allah Almighty, whether this relates to musical instruments or to publishing content that is not compatible with the methodology of Ahl al Sunnah wal Jamaah.

We are very happy to help you develop this program, and we hope it helps you achieve your goals in a way that matches values and good ethics.

For contact and more information, please visit the developer page.
""",
    "license_fr.txt": """En utilisant ce programme, vous acceptez ce qui suit:

Une seule condition: l'utiliser d'une manière qui plaît à Allah Tout Puissant.

Le développeur de ce produit considère qu'il est interdit d'utiliser ce produit d'une manière qui ne plaît pas à Allah Tout Puissant, que cela concerne les instruments de musique ou la publication de contenu non compatible avec la voie d'Ahl al Sunnah wal Jamaah.

Nous serons très heureux de vous aider à développer ce programme, et nous espérons qu'il vous aidera à atteindre vos objectifs en accord avec les valeurs et la bonne éthique.

Pour nous contacter et obtenir plus d'informations, veuillez visiter la page du développeur.
""",
}

BACKUP_ROOT = Path(
    os.environ.get(
        "VIDEO_MAKER_BACKUP_ROOT",
        r"D:\Google Drive\برمجتي\صانع الفديو",
    )
)

SOURCE_DIRECTORIES = {"assets", "video_maker", "tests", "شرح", ".github"}
SOURCE_SUFFIXES = {
    ".py",
    ".cmd",
    ".iss",
    ".txt",
    ".html",
    ".md",
    ".json",
    ".gitignore",
}
EXCLUDED_DIRECTORIES = {
    "__pycache__",
    ".git",
    ".github_release_repo",
    ".idea",
    ".vscode",
    ".all_in_one_build",
    "build",
    "dist",
    "app_files",
    "_runtime",
    "installer_build",
    "installer_dist",
    "release",
}
EXCLUDED_SUFFIXES = {
    ".exe",
    ".dll",
    ".spec",
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".bak",
    ".old",
}


class CommandError(RuntimeError):
    pass


_PROGRESS_CALLBACK = None
_LOG_CALLBACK = None
_PROGRESS_SPANS = [(0.0, 100.0)]


def set_runtime_callbacks(progress_callback=None, log_callback=None):
    global _PROGRESS_CALLBACK, _LOG_CALLBACK
    old_callbacks = _PROGRESS_CALLBACK, _LOG_CALLBACK
    _PROGRESS_CALLBACK = progress_callback
    _LOG_CALLBACK = log_callback
    return old_callbacks


def restore_runtime_callbacks(callbacks):
    global _PROGRESS_CALLBACK, _LOG_CALLBACK
    _PROGRESS_CALLBACK, _LOG_CALLBACK = callbacks


def log_message(message):
    text = str(message)
    if _LOG_CALLBACK:
        _LOG_CALLBACK(text)


def report_progress(percent, message):
    percent = max(0.0, min(100.0, float(percent)))
    base, end = _PROGRESS_SPANS[-1]
    mapped = base + ((end - base) * percent / 100.0)
    mapped = max(0.0, min(100.0, mapped))
    if _PROGRESS_CALLBACK:
        _PROGRESS_CALLBACK(int(round(mapped)), str(message))


@contextlib.contextmanager
def progress_span(start, end):
    parent_base, parent_end = _PROGRESS_SPANS[-1]
    absolute_start = parent_base + ((parent_end - parent_base) * float(start) / 100.0)
    absolute_end = parent_base + ((parent_end - parent_base) * float(end) / 100.0)
    _PROGRESS_SPANS.append((absolute_start, absolute_end))
    try:
        yield
    finally:
        _PROGRESS_SPANS.pop()


def step(message):
    print()
    print("=" * 70)
    print(message)
    print("=" * 70)
    log_message("")
    log_message("=" * 70)
    log_message(message)
    log_message("=" * 70)


def run(command, cwd=PROJECT_ROOT, check=True, capture=False):
    shown = " ".join(str(part) for part in command)
    print(f"> {shown}")
    log_message(f"> {shown}")
    if capture or not _LOG_CALLBACK:
        process = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    else:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if text:
                log_message(text)
        process.wait()
    if check and process.returncode != 0:
        output = ""
        if capture:
            output = "\n".join(
                part for part in (process.stdout, process.stderr) if part
            ).strip()
        raise CommandError(
            f"Command failed with exit code {process.returncode}:\n{shown}"
            + (f"\n\n{output}" if output else "")
        )
    if capture:
        return process.stdout.strip()
    return ""


def resolve_command(name, candidates=()):
    found = shutil.which(name)
    if found:
        return Path(found)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def resolve_python():
    virtual_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if virtual_python.is_file():
        return virtual_python
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python314" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe",
    ]
    python = resolve_command("python", candidates)
    if not python:
        raise CommandError("Python was not found.")
    return python


def resolve_inno_compiler():
    env = os.environ
    candidates = [
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 7" / "ISCC.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 5" / "ISCC.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Antigravity IDE" / "resources" / "app" / "node_modules" / "innosetup" / "bin" / "ISCC.exe",
        Path(env.get("APPDATA", "")) / "Inno Setup 7" / "ISCC.exe",
        Path(env.get("APPDATA", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(env.get("ProgramFiles", "")) / "Inno Setup 7" / "ISCC.exe",
        Path(env.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(env.get("ProgramFiles(x86)", "")) / "Inno Setup 7" / "ISCC.exe",
        Path(env.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    compiler = resolve_command("ISCC.exe", candidates)
    if compiler:
        return compiler

    local_programs = Path(env.get("LOCALAPPDATA", "")) / "Programs"
    if local_programs.is_dir():
        for found in local_programs.rglob("ISCC.exe"):
            if found.is_file():
                return found

    raise CommandError("ISCC.exe was not found. Install Inno Setup 6/7 first.")


def require_project_files(skip_github=False, include_online=False):
    required_files = [
        "main.py",
        "version_info.txt",
        "keyboard_shortcuts.html",
        "installer.iss",
        "video_maker/app_info.py",
        "video_maker/mpv-1.dll",
    ]
    if include_online:
        required_files.append("online_installer.iss")
    required_dirs = ["video_maker", "assets"]
    missing = []
    for relative in required_files:
        if not (PROJECT_ROOT / relative).is_file():
            missing.append(relative)
    for relative in required_dirs:
        if not (PROJECT_ROOT / relative).is_dir():
            missing.append(relative + "\\")
    if missing:
        raise CommandError("Missing project items:\n" + "\n".join(f" - {x}" for x in missing))


def read_text(path):
    return path.read_text(encoding="utf-8")


def write_text(path, text):
    path.write_text(text, encoding="utf-8")


def replace_once(path, pattern, replacement):
    text = read_text(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise CommandError(f"Could not update expected text in {path}")
    write_text(path, updated)


def write_embedded_licenses():
    LICENSES_PATH.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in EMBEDDED_LICENSES.items():
        path = LICENSES_PATH / name
        path.write_text(text, encoding="utf-8-sig")
        written.append(path)
        root_path = PROJECT_ROOT / name
        root_path.write_text(text, encoding="utf-8-sig")
        written.append(root_path)
    return written


def get_project_version():
    text = read_text(PROJECT_ROOT / "video_maker" / "app_info.py")
    match = re.search(r'APP_VERSION = "([^"]+)"', text)
    if not match:
        raise CommandError("Could not read APP_VERSION from video_maker/app_info.py")
    return match.group(1)


def normalize_four_part(version):
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return ".".join(str(part) for part in parts[:4])


def release_version(version):
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return ".".join(str(part) for part in parts[:3])


def next_patch_version(version):
    parts = [int(part) for part in normalize_four_part(version).split(".")]
    parts[2] += 1
    parts[3] = 0
    return ".".join(str(part) for part in parts)


def set_project_version(version):
    four = normalize_four_part(version)
    release = release_version(four)
    comma = four.replace(".", ", ")

    replace_once(
        PROJECT_ROOT / "video_maker" / "app_info.py",
        r'APP_VERSION = ".*?"',
        f'APP_VERSION = "{four}"',
    )
    replace_once(
        PROJECT_ROOT / "version_info.txt",
        r"StringStruct\('FileDescription', 'Video Maker .*?'\)",
        f"StringStruct('FileDescription', 'Video Maker {release}')",
    )
    replace_once(
        PROJECT_ROOT / "version_info.txt",
        r"StringStruct\('FileVersion', '.*?'\)",
        f"StringStruct('FileVersion', '{four}')",
    )
    replace_once(
        PROJECT_ROOT / "version_info.txt",
        r"StringStruct\('ProductVersion', '.*?'\)",
        f"StringStruct('ProductVersion', '{four}')",
    )
    replace_once(PROJECT_ROOT / "version_info.txt", r"filevers=\([0-9, ]+\)", f"filevers=({comma})")
    replace_once(PROJECT_ROOT / "version_info.txt", r"prodvers=\([0-9, ]+\)", f"prodvers=({comma})")

    replace_once(PROJECT_ROOT / "installer.iss", r"AppVersion=.*", f"AppVersion={release}")
    replace_once(PROJECT_ROOT / "installer.iss", r"AppVerName=.*", f"AppVerName={{cm:AppName}} {release}")
    replace_once(
        PROJECT_ROOT / "installer.iss",
        r"VersionInfoDescription=.*",
        f"VersionInfoDescription=Video Maker Setup {release}",
    )
    replace_once(
        PROJECT_ROOT / "installer.iss",
        r"VersionInfoProductVersion=.*",
        f"VersionInfoProductVersion={four}",
    )
    replace_once(PROJECT_ROOT / "installer.iss", r"VersionInfoVersion=.*", f"VersionInfoVersion={four}")

    online = PROJECT_ROOT / "online_installer.iss"
    if online.is_file():
        replace_once(online, r"AppVersion=.*", f"AppVersion={release}")
        replace_once(online, r"VersionInfoProductVersion=.*", f"VersionInfoProductVersion={four}")
        replace_once(online, r"VersionInfoVersion=.*", f"VersionInfoVersion={four}")

    return four


def _make_removable(path):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except OSError:
        pass


def _handle_remove_error(function, path, _exc_info):
    _make_removable(path)
    function(path)


def remove_path(path, attempts=12):
    path = Path(path)
    if not path.exists():
        return
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            _make_removable(path)
            if path.is_dir():
                shutil.rmtree(path, onerror=_handle_remove_error)
            else:
                path.unlink()
            return
        except OSError as error:
            last_error = error
            try:
                if path.exists():
                    if path.is_dir():
                        for item in path.rglob("*"):
                            _make_removable(item)
                    _make_removable(path)
            except OSError:
                pass
            time.sleep(0.35 + attempt * 0.15)
    raise CommandError(f"Could not remove {path}: {last_error}")


def copy_folder(source, destination):
    source = Path(source)
    destination = Path(destination)
    if not source.is_dir():
        raise CommandError(f"Source folder does not exist: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)


def stop_running_app():
    subprocess.run(
        ["taskkill", "/IM", f"{APP_NAME}.exe", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    time.sleep(0.8)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_program(no_version_bump=False, skip_github=True):
    report_progress(0, "بدء بناء البرنامج محليا")
    step("Checking project and build tools")
    require_project_files(skip_github=skip_github)
    python = resolve_python()
    run([python, "--version"])
    run([python, "-c", "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"])
    report_progress(10, "تم فحص أدوات بناء البرنامج")

    step("Updating version information")
    current = get_project_version()
    version = normalize_four_part(current if no_version_bump else next_patch_version(current))
    version = set_project_version(version)
    print(f"Version: {version}")
    report_progress(18, f"تم تحديث معلومات الإصدار إلى {version}")

    step("Building program with PyInstaller")
    for path in (BUILD_ROOT,):
        remove_path(path)
    WORK_PATH.mkdir(parents=True, exist_ok=True)
    DIST_PATH.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.mkdir(parents=True, exist_ok=True)
    report_progress(25, "جاري تشغيل PyInstaller لبناء البرنامج")

    args = [
        python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noupx",
        "--debug",
        "noarchive",
        "--windowed",
        "--onedir",
        "--name",
        APP_NAME,
        "--contents-directory",
        "app_files",
        "--version-file",
        PROJECT_ROOT / "version_info.txt",
        "--copy-metadata",
        "imageio",
        "--copy-metadata",
        "imageio-ffmpeg",
        "--copy-metadata",
        "moviepy",
        "--copy-metadata",
        "numpy",
        "--copy-metadata",
        "sounddevice",
        "--copy-metadata",
        "soundcard",
        "--copy-metadata",
        "proglog",
        "--copy-metadata",
        "onnxruntime",
        "--copy-metadata",
        "process_audio_capture",
        "--collect-binaries",
        "sounddevice",
        "--collect-binaries",
        "onnxruntime",
        "--collect-all",
        "process_audio_capture",
        "--collect-binaries",
        "process_audio_capture",
        "--collect-all",
        "pyvirtualcam",
        "--add-data",
        f"{PROJECT_ROOT / 'assets'}{os.pathsep}assets",
        "--add-data",
        f"{PROJECT_ROOT / 'keyboard_shortcuts.html'}{os.pathsep}.",
        "--add-data",
        f"{PROJECT_ROOT / 'video_maker' / 'mpv-1.dll'}{os.pathsep}video_maker",
        "--hidden-import",
        "PIL._tkinter_finder",
        "--hidden-import",
        "arabic_reshaper",
        "--hidden-import",
        "bidi.algorithm",
        "--hidden-import",
        "sounddevice",
        "--hidden-import",
        "soundcard",
        "--hidden-import",
        "onnxruntime",
        "--hidden-import",
        "process_audio_capture",
        "--hidden-import",
        "pyvirtualcam",
    ]
    for module in [
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "cv2",
        "librosa",
        "llvmlite",
        "numba",
        "sklearn",
        "scipy",
        "matplotlib",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        "tensorflow",
        "keras",
        "Crypto",
        "Cryptodome",
    ]:
        args.extend(["--exclude-module", module])
    args.extend(
        [
            "--distpath",
            DIST_PATH,
            "--workpath",
            WORK_PATH,
            "--specpath",
            SPEC_PATH,
            PROJECT_ROOT / "main.py",
        ]
    )
    run(args)
    report_progress(65, "انتهى PyInstaller، جاري فحص ناتج البناء")

    built_folder = DIST_PATH / APP_NAME
    built_exe = built_folder / "VideoMaker.exe"
    built_app_files = built_folder / "app_files"
    if not built_exe.is_file():
        raise CommandError(f"PyInstaller did not create {built_exe}")
    if not built_app_files.is_dir():
        raise CommandError(f"PyInstaller did not create {built_app_files}")
    report_progress(72, "تم التأكد من ملفات البرنامج الناتجة")

    step("Publishing program files to project root")
    report_progress(78, "جاري نقل VideoMaker.exe و app_files إلى جذر المشروع")
    PREVIOUS_RELEASE_PATH.mkdir(parents=True, exist_ok=True)
    if FINAL_EXE.is_file():
        shutil.copy2(FINAL_EXE, PREVIOUS_RELEASE_PATH / "VideoMaker.exe")
    if FINAL_APP_FILES.is_dir():
        copy_folder(FINAL_APP_FILES, PREVIOUS_RELEASE_PATH / "app_files")

    try:
        stop_running_app()
        remove_path(FINAL_EXE)
        remove_path(FINAL_APP_FILES)
        remove_path(OLD_RUNTIME)
        shutil.copy2(built_exe, FINAL_EXE)
        copy_folder(built_app_files, FINAL_APP_FILES)
        final_count = sum(1 for path in FINAL_APP_FILES.rglob("*") if path.is_file())
        if final_count == 0:
            raise CommandError("app_files is empty after publishing.")
        unexpected = []
        for path in FINAL_APP_FILES.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in {".ps1", ".cmd", ".bat", ".vbs", ".js", ".jse", ".wsf"}:
                unexpected.append(path)
            if suffix == ".exe" and not path.name.lower().startswith("ffmpeg"):
                unexpected.append(path)
        if unexpected:
            raise CommandError(
                "Unexpected executable/script files inside app_files:\n"
                + "\n".join(str(path) for path in unexpected)
            )
    except Exception:
        remove_path(FINAL_EXE)
        remove_path(FINAL_APP_FILES)
        backup_exe = PREVIOUS_RELEASE_PATH / "VideoMaker.exe"
        backup_app_files = PREVIOUS_RELEASE_PATH / "app_files"
        if backup_exe.is_file():
            shutil.copy2(backup_exe, FINAL_EXE)
        if backup_app_files.is_dir():
            copy_folder(backup_app_files, FINAL_APP_FILES)
        raise

    print(f"Program: {FINAL_EXE}")
    print(f"app_files: {FINAL_APP_FILES}")
    print(f"app_files count: {final_count}")
    print(f"Program SHA256: {sha256(FINAL_EXE)}")
    report_progress(100, "اكتمل بناء البرنامج محليا")
    return version


def build_installer():
    report_progress(0, "بدء بناء المثبت العادي")
    step("Building full installer with Inno Setup")
    require_project_files(skip_github=True)
    write_embedded_licenses()
    report_progress(20, "تم تجهيز الاتفاقيات المدمجة وفحص ملفات المثبت")
    if not FINAL_EXE.is_file():
        raise CommandError("VideoMaker.exe is missing. Build the program first.")
    if not FINAL_APP_FILES.is_dir():
        raise CommandError("app_files is missing. Build the program first.")
    compiler = resolve_inno_compiler()
    remove_path(SETUP_PATH)
    report_progress(40, "جاري تشغيل Inno Setup لبناء المثبت العادي")
    run([compiler, PROJECT_ROOT / "installer.iss"])
    report_progress(90, "انتهى Inno Setup، جاري فحص ملف المثبت")
    if not SETUP_PATH.is_file():
        raise CommandError(f"Inno Setup did not create {SETUP_PATH}")
    print(f"Installer: {SETUP_PATH}")
    print(f"Installer SHA256: {sha256(SETUP_PATH)}")
    report_progress(100, "اكتمل بناء المثبت العادي")
    return SETUP_PATH


def build_online_installer():
    report_progress(0, "بدء بناء المثبت الأونلاين")
    step("Building online installer")
    require_project_files(skip_github=True, include_online=True)
    compiler = resolve_inno_compiler()
    ONLINE_SETUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    remove_path(ONLINE_SETUP_PATH)
    report_progress(35, "جاري تشغيل Inno Setup لبناء المثبت الأونلاين")
    run([compiler, PROJECT_ROOT / "online_installer.iss"])
    report_progress(90, "انتهى Inno Setup، جاري فحص المثبت الأونلاين")
    if not ONLINE_SETUP_PATH.is_file():
        raise CommandError(f"Inno Setup did not create {ONLINE_SETUP_PATH}")
    print(f"Online installer: {ONLINE_SETUP_PATH}")
    report_progress(100, "اكتمل بناء المثبت الأونلاين")
    return ONLINE_SETUP_PATH


def parse_backup_version(name):
    match = re.fullmatch(r"(\d+)\.(\d+)", name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def next_backup_name(existing_versions):
    if not existing_versions:
        return "1.0"
    major, minor = max(existing_versions)
    if minor == 0:
        return f"{major}.2"
    if minor < 10:
        return f"{major}.{minor + 1}"
    return f"{major + 1}.0"


def find_next_backup_path():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    versions = []
    for item in BACKUP_ROOT.iterdir():
        if item.is_dir():
            version = parse_backup_version(item.name)
            if version:
                versions.append(version)
    version_name = next_backup_name(versions)
    destination = BACKUP_ROOT / version_name
    while destination.exists():
        parsed = parse_backup_version(version_name)
        version_name = next_backup_name([parsed])
        destination = BACKUP_ROOT / version_name
    return destination


def should_copy_file(path):
    path = Path(path)
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.parent == PROJECT_ROOT and path.name == ".gitignore":
        return True
    if path.parent == PROJECT_ROOT:
        return path.suffix.lower() in SOURCE_SUFFIXES
    return True


def iter_backup_files():
    for item in PROJECT_ROOT.iterdir():
        if item.is_dir():
            if item.name not in SOURCE_DIRECTORIES or item.name in EXCLUDED_DIRECTORIES:
                continue
            for source_file in item.rglob("*"):
                if source_file.is_dir():
                    continue
                if any(part in EXCLUDED_DIRECTORIES for part in source_file.relative_to(PROJECT_ROOT).parts):
                    continue
                if not should_copy_file(source_file):
                    continue
                yield source_file
        elif should_copy_file(item):
            yield item


def backup_source():
    report_progress(0, "بدء أخذ نسخة احتياطية من السورس على Drive")
    step("Taking source backup on Google Drive")
    files = list(iter_backup_files())
    total = len(files)
    report_progress(5, f"تم تحديد {total} ملف للنسخة الاحتياطية")
    destination = find_next_backup_path()
    destination.mkdir(parents=True, exist_ok=False)
    copied = 0
    for source_file in files:
        if source_file.parent == PROJECT_ROOT:
            target = destination / source_file.name
        else:
            target = destination / source_file.relative_to(PROJECT_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
        copied += 1
        if total:
            report_progress(5 + (copied * 90 / total), f"نسخ السورس إلى Drive: {copied} من {total} ملف")
    print(f"Backup: {destination}")
    print(f"Copied files: {copied}")
    report_progress(100, f"اكتمل باك أب السورس: {copied} ملف")
    return destination, copied


def git_output(*args, cwd=PROJECT_ROOT, check=True):
    git = resolve_command(
        "git",
        [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
        ],
    )
    if not git:
        raise CommandError("Git was not found.")
    return run([git, *args], cwd=cwd, check=check, capture=True)


def gh_output(*args, check=True):
    gh = resolve_command(
        "gh",
        [
            r"C:\Program Files\GitHub CLI\gh.exe",
            r"C:\Program Files (x86)\GitHub CLI\gh.exe",
        ],
    )
    if not gh:
        raise CommandError("GitHub CLI was not found.")
    return run([gh, *args], check=check, capture=True)


def ensure_github_ready():
    gh_output("auth", "status")
    gh_output("auth", "setup-git", check=False)
    gh_output("repo", "view", REPOSITORY, check=False)


def remove_ignored_files_from_index():
    ignored = git_output("ls-files", "-ci", "--exclude-standard").splitlines()
    if ignored:
        git_output("rm", "--cached", "-r", "--ignore-unmatch", "--", *ignored)
    return len(ignored)


def upload_source():
    report_progress(0, "بدء رفع السورس كود فقط على GitHub")
    step("Uploading source code to GitHub")
    ensure_github_ready()
    report_progress(15, "تم فحص تسجيل الدخول إلى GitHub CLI")
    inside = git_output("rev-parse", "--is-inside-work-tree").strip().lower()
    if inside != "true":
        raise CommandError(f"{PROJECT_ROOT} is not a Git repository.")
    branch = git_output("branch", "--show-current").strip()
    if not branch:
        raise CommandError("Detached HEAD. Switch to a branch before uploading.")
    report_progress(30, f"تم تحديد فرع Git الحالي: {branch}")
    git_output("pull", "--rebase", "--autostash", "origin", branch)
    report_progress(50, "تم جلب آخر تغييرات GitHub")
    removed_ignored = remove_ignored_files_from_index()
    report_progress(65, "تم إخراج الملفات المساعدة المتجاهلة من تتبع Git")
    git_output("add", "-A")
    status = git_output("status", "--porcelain")
    committed = False
    if status.strip():
        message = "Update source code"
        if removed_ignored:
            message = "Update source code and remove ignored helper files"
        git_output("commit", "-m", message)
        committed = True
    report_progress(82, "تم إنشاء commit للسورس إن وجدت تغييرات")
    git_output("push", "-u", "origin", branch)
    print(f"Branch: {branch}")
    print(f"Committed: {committed}")
    print(f"Removed ignored files from Git tracking: {removed_ignored}")
    report_progress(100, "اكتمل رفع السورس كود على GitHub")
    return branch, committed, removed_ignored


def github_release_or_tag_exists(tag):
    gh = resolve_command(
        "gh",
        [
            r"C:\Program Files\GitHub CLI\gh.exe",
            r"C:\Program Files (x86)\GitHub CLI\gh.exe",
        ],
    )
    if not gh:
        raise CommandError("GitHub CLI was not found.")
    release_code = subprocess.run(
        [str(gh), "release", "view", tag, "--repo", REPOSITORY, "--json", "tagName"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode
    if release_code == 0:
        return True
    tags = git_output("ls-remote", "--tags", f"https://github.com/{REPOSITORY}.git", tag)
    return bool(tags.strip())


def prepare_release_tag(skip_github=False):
    report_progress(0, "بدء تجهيز رقم الإصدار ووسم GitHub")
    current = set_project_version(get_project_version())
    version = release_version(current)
    tag = f"v{version}"
    report_progress(35, f"تم قراءة الإصدار الحالي: {current}")
    if not skip_github:
        ensure_github_ready()
        while github_release_or_tag_exists(tag):
            current = set_project_version(next_patch_version(current))
            version = release_version(current)
            tag = f"v{version}"
    report_progress(100, f"تم تجهيز الوسم: {tag}")
    print(f"Version: {current}")
    print(f"Tag: {tag}")
    return current, version, tag


def create_github_release(tag, version):
    report_progress(0, f"بدء إنشاء GitHub Release {tag}")
    step("Creating GitHub release")
    if not SETUP_PATH.is_file():
        raise CommandError("VideoMakerSetup.exe is missing.")
    notes_path = PROJECT_ROOT / "المستجدات.txt"
    if not notes_path.is_file():
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        notes_path = BUILD_ROOT / "release_notes.txt"
        notes_path.write_text(f"Video Maker {version}\n", encoding="utf-8")
    RELEASE_REPO_DIR.mkdir(parents=True, exist_ok=True)
    if not (RELEASE_REPO_DIR / ".git").is_dir():
        remove_path(RELEASE_REPO_DIR)
        git_output("clone", f"https://github.com/{REPOSITORY}.git", RELEASE_REPO_DIR)
    report_progress(25, "تم تجهيز مستودع الإصدار المحلي")
    git_output("-C", RELEASE_REPO_DIR, "remote", "set-url", "origin", f"https://github.com/{REPOSITORY}.git")
    git_output("-C", RELEASE_REPO_DIR, "config", "user.name", "Mustafa-Elbheri")
    git_output("-C", RELEASE_REPO_DIR, "config", "user.email", "Mustafa-Elbheri@users.noreply.github.com")

    remote_main = git_output("-C", RELEASE_REPO_DIR, "ls-remote", "--heads", "origin", "main")
    if remote_main:
        git_output("-C", RELEASE_REPO_DIR, "fetch", "origin", "main")
        git_output("-C", RELEASE_REPO_DIR, "checkout", "-B", "main", "origin/main")
    else:
        git_output("-C", RELEASE_REPO_DIR, "checkout", "-B", "main")
    report_progress(45, "تم تجهيز فرع main لإصدار GitHub")

    if github_release_or_tag_exists(tag):
        raise CommandError(f"GitHub release/tag already exists: {tag}")
    git_output("-C", RELEASE_REPO_DIR, "commit", "--allow-empty", "-m", f"Release {tag}")
    git_output("-C", RELEASE_REPO_DIR, "tag", "-a", tag, "-m", f"Release {tag}")
    report_progress(65, "تم إنشاء commit و tag للإصدار")
    git_output("-C", RELEASE_REPO_DIR, "push", "-u", "origin", "main")
    git_output("-C", RELEASE_REPO_DIR, "push", "origin", tag)
    report_progress(80, "تم رفع فرع الإصدار والوسم")
    gh_output(
        "release",
        "create",
        tag,
        str(SETUP_PATH),
        "--repo",
        REPOSITORY,
        "--title",
        f"Video Maker {version}",
        "--notes-file",
        str(notes_path),
        "--latest",
    )
    print(f"https://github.com/{REPOSITORY}/releases/download/{tag}/VideoMakerSetup.exe")
    report_progress(100, "اكتمل إنشاء GitHub Release ورفع المثبت")


def check_environment(skip_github=False):
    report_progress(0, "بدء فحص أدوات المشروع")
    step("Checking environment")
    require_project_files(skip_github=skip_github, include_online=True)
    licenses = write_embedded_licenses()
    report_progress(50, "تم فحص ملفات المشروع وتوليد الاتفاقيات المؤقتة")
    print(f"Project: {PROJECT_ROOT}")
    print(f"Python: {resolve_python()}")
    print(f"Inno Setup: {resolve_inno_compiler()}")
    print("Embedded licenses:")
    for path in licenses:
        print(path)
    if not skip_github:
        ensure_github_ready()
        print("GitHub CLI: ready")
    report_progress(100, "اكتمل فحص أدوات المشروع")


def clean_build_files():
    report_progress(0, "بدء حذف ملفات البناء المؤقتة")
    step("Cleaning generated build files")
    for path in [BUILD_ROOT, PROJECT_ROOT / "build", PROJECT_ROOT / "dist", PROJECT_ROOT / f"{APP_NAME}.spec", PREVIOUS_RELEASE_PATH]:
        remove_path(path)
    report_progress(100, "اكتمل حذف ملفات البناء المؤقتة")


def full_workflow(args):
    report_progress(0, "بدء المسار الكامل للبناء والرفع")
    with progress_span(0, 7):
        check_environment(skip_github=args.skip_github)
    with progress_span(7, 12):
        current, version, tag = prepare_release_tag(skip_github=args.skip_github)
    set_project_version(current)
    with progress_span(12, 55):
        build_program(no_version_bump=True, skip_github=args.skip_github)
    with progress_span(55, 68):
        build_installer()
    if args.online:
        with progress_span(68, 74):
            build_online_installer()
        backup_start = 74
    else:
        backup_start = 68
    with progress_span(backup_start, 82):
        backup_source()
    if not args.skip_github:
        with progress_span(82, 90):
            upload_source()
        with progress_span(90, 98):
            create_github_release(tag, version)
    if not args.keep_build_files:
        with progress_span(98, 100):
            clean_build_files()
    step("Done")
    print(f"Version: {current}")
    print(f"Installer: {SETUP_PATH}")
    if args.online:
        print(f"Online installer: {ONLINE_SETUP_PATH}")
    report_progress(100, "اكتمل المسار الكامل بنجاح")


def gui_full_workflow():
    args = argparse.Namespace(skip_github=False, keep_build_files=False, online=True)
    full_workflow(args)


def gui_local_program_build():
    report_progress(0, "بدء بناء البرنامج محليا فقط")
    with progress_span(0, 92):
        build_program(no_version_bump=True, skip_github=True)
    with progress_span(92, 100):
        clean_build_files()
    report_progress(100, "اكتمل بناء البرنامج محليا فقط")


def gui_full_installer_build():
    report_progress(0, "بدء بناء المثبت العادي فقط")
    with progress_span(0, 100):
        build_installer()
    report_progress(100, "اكتمل بناء المثبت العادي فقط")


def gui_online_installer_build():
    report_progress(0, "بدء بناء المثبت الأونلاين فقط")
    with progress_span(0, 100):
        build_online_installer()
    report_progress(100, "اكتمل بناء المثبت الأونلاين فقط")


def gui_source_upload():
    report_progress(0, "بدء رفع السورس كود فقط")
    with progress_span(0, 100):
        upload_source()
    report_progress(100, "اكتمل رفع السورس كود فقط")


def gui_drive_backup():
    report_progress(0, "بدء باك أب Drive للسورس فقط")
    with progress_span(0, 100):
        backup_source()
    report_progress(100, "اكتمل باك أب Drive للسورس فقط")


def gui_check_tools():
    report_progress(0, "بدء فحص الأدوات فقط")
    with progress_span(0, 100):
        check_environment(skip_github=False)
    report_progress(100, "اكتمل فحص الأدوات فقط")


class _GuiLogWriter:
    def __init__(self, callback):
        self.callback = callback
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.callback(line.rstrip())

    def flush(self):
        if self.buffer.strip():
            self.callback(self.buffer.rstrip())
        self.buffer = ""


def launch_gui():
    try:
        import wx
    except ImportError as error:
        raise CommandError("wxPython is not installed. Install wxPython to use the graphical interface.") from error

    class ReleaseToolsFrame(wx.Frame):
        def __init__(self):
            super().__init__(None, title="Video Maker build tools", size=(760, 560))
            self.events = queue.Queue()
            self.worker = None
            self.buttons = []

            panel = wx.Panel(self)
            main_sizer = wx.BoxSizer(wx.VERTICAL)

            title = wx.StaticText(panel, label="Video Maker build tools")
            title.SetName("عنوان أدوات بناء Video Maker")
            main_sizer.Add(title, flag=wx.ALL, border=10)

            grid = wx.GridSizer(rows=0, cols=2, vgap=8, hgap=8)
            self._add_button(grid, "تنفيذ كل شيء", "بناء كامل، مثبّت عادي، مثبّت أونلاين، باك أب، رفع سورس، و GitHub Release", gui_full_workflow)
            self._add_button(grid, "بناء البرنامج محليا فقط", "يبني VideoMaker.exe و app_files فقط بدون رفع", gui_local_program_build)
            self._add_button(grid, "بناء المثبت العادي فقط", "ينشئ VideoMakerSetup.exe من البناء الحالي", gui_full_installer_build)
            self._add_button(grid, "بناء المثبت الأونلاين فقط", "ينشئ VideoMakerOnlineSetup.exe فقط", gui_online_installer_build)
            self._add_button(grid, "رفع السورس كود فقط", "يرفع السورس كود فقط إلى GitHub", gui_source_upload)
            self._add_button(grid, "باك أب Drive للسورس فقط", "ينسخ السورس المهم فقط إلى مجلد الباك أب", gui_drive_backup)
            self._add_button(grid, "فحص الأدوات فقط", "يفحص Python و Inno Setup و GitHub CLI", gui_check_tools)
            close_button = wx.Button(panel, label="إغلاق")
            close_button.SetName("إغلاق نافذة أدوات البناء")
            close_button.Bind(wx.EVT_BUTTON, lambda event: self.Close())
            self.buttons.append(close_button)
            grid.Add(close_button, flag=wx.EXPAND)
            main_sizer.Add(grid, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

            self.status = wx.TextCtrl(
                panel,
                value="جاهز. اختر عملية من الأزرار.",
                style=wx.TE_READONLY | wx.BORDER_SIMPLE,
            )
            self.status.SetName("حالة أدوات البناء")
            main_sizer.Add(self.status, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

            self.gauge = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL)
            self.gauge.SetName("شريط تقدم أدوات البناء: صفر بالمئة")
            main_sizer.Add(self.gauge, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

            self.log = wx.TextCtrl(
                panel,
                value="",
                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
            )
            self.log.SetName("سجل عمليات البناء")
            main_sizer.Add(self.log, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

            panel.SetSizer(main_sizer)
            self.timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
            self.Bind(wx.EVT_CLOSE, self.on_close)
            self.timer.Start(150)
            self.Centre()

        def _add_button(self, grid, label, name, task):
            button = wx.Button(self.GetChildren()[0], label=label)
            button.SetName(name)
            button.Bind(wx.EVT_BUTTON, lambda event, task=task, label=label: self.start_task(label, task))
            self.buttons.append(button)
            grid.Add(button, flag=wx.EXPAND)

        def set_busy(self, busy):
            for button in self.buttons:
                if button.GetLabel() != "إغلاق":
                    button.Enable(not busy)

        def start_task(self, label, task):
            if self.worker and self.worker.is_alive():
                wx.MessageBox("هناك عملية تعمل حاليا. انتظر حتى تنتهي.", "عملية جارية", wx.OK | wx.ICON_INFORMATION)
                return
            self.log.SetValue("")
            self.gauge.SetValue(0)
            self.update_progress(0, f"بدء: {label}")
            self.set_busy(True)
            self.worker = threading.Thread(target=self.run_task, args=(label, task), daemon=True)
            self.worker.start()

        def run_task(self, label, task):
            old_callbacks = set_runtime_callbacks(
                progress_callback=lambda percent, message: self.events.put(("progress", percent, message)),
                log_callback=lambda message: self.events.put(("log", message)),
            )
            writer = _GuiLogWriter(lambda message: self.events.put(("log", message)))
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    task()
                writer.flush()
                self.events.put(("done", True, f"اكتملت العملية بنجاح: {label}"))
            except Exception as error:
                writer.flush()
                self.events.put(("log", traceback.format_exc()))
                self.events.put(("done", False, f"فشلت العملية: {label}\n{error}"))
            finally:
                restore_runtime_callbacks(old_callbacks)

        def update_progress(self, percent, message):
            percent = max(0, min(100, int(percent)))
            spoken = f"{message}. نسبة التقدم {percent} بالمئة"
            self.gauge.SetValue(percent)
            self.status.SetValue(spoken)
            self.status.SetName(spoken)
            self.gauge.SetName(f"شريط تقدم أدوات البناء: {spoken}")

        def append_log(self, message):
            if not message:
                return
            self.log.AppendText(str(message).rstrip() + "\n")

        def on_timer(self, event):
            while True:
                try:
                    item = self.events.get_nowait()
                except queue.Empty:
                    break
                kind = item[0]
                if kind == "progress":
                    self.update_progress(item[1], item[2])
                elif kind == "log":
                    self.append_log(item[1])
                elif kind == "done":
                    ok, message = item[1], item[2]
                    self.set_busy(False)
                    self.update_progress(100 if ok else self.gauge.GetValue(), message)
                    wx.MessageBox(message, "نتيجة العملية", wx.OK | (wx.ICON_INFORMATION if ok else wx.ICON_ERROR))

        def on_close(self, event):
            if self.worker and self.worker.is_alive():
                wx.MessageBox("لا يمكن إغلاق النافذة أثناء تنفيذ عملية بناء أو رفع.", "عملية جارية", wx.OK | wx.ICON_INFORMATION)
                event.Veto()
                return
            self.timer.Stop()
            event.Skip()

    app = wx.App(False)
    frame = ReleaseToolsFrame()
    frame.Show()
    app.MainLoop()


def parse_args():
    parser = argparse.ArgumentParser(description="Video Maker build, backup, and publish tools.")
    sub = parser.add_subparsers(dest="command")

    all_cmd = sub.add_parser("all", help="Build program, build installer, backup, upload source, and publish release.")
    all_cmd.add_argument("--skip-github", action="store_true", help="Build and backup without GitHub upload/release.")
    all_cmd.add_argument("--keep-build-files", action="store_true", help="Keep temporary build folders.")
    all_cmd.add_argument("--online", action="store_true", help="Also build the online installer.")

    build_cmd = sub.add_parser("build", help="Build program and full installer only.")
    build_cmd.add_argument("--no-version-bump", action="store_true")

    sub.add_parser("installer", help="Build the full installer only.")
    sub.add_parser("online-installer", help="Build the online installer only.")
    sub.add_parser("backup", help="Take a source backup only.")
    sub.add_parser("upload-source", help="Upload source code to GitHub only.")

    check_cmd = sub.add_parser("check", help="Check project tools.")
    check_cmd.add_argument("--skip-github", action="store_true")

    clean_cmd = sub.add_parser("clean", help="Clean generated build files.")
    clean_cmd.set_defaults(command="clean")

    sub.add_parser("gui", help="Open the wxPython graphical interface.")

    return parser.parse_args()


def main():
    os.chdir(PROJECT_ROOT)
    if len(sys.argv) == 1:
        launch_gui()
        return
    args = parse_args()
    command = args.command or "all"
    if command == "all":
        full_workflow(args)
    elif command == "gui":
        launch_gui()
    elif command == "build":
        build_program(no_version_bump=args.no_version_bump, skip_github=True)
        build_installer()
    elif command == "installer":
        build_installer()
    elif command == "online-installer":
        build_online_installer()
    elif command == "backup":
        backup_source()
    elif command == "upload-source":
        upload_source()
    elif command == "check":
        check_environment(skip_github=args.skip_github)
    elif command == "clean":
        clean_build_files()
    else:
        raise CommandError(f"Unknown command: {command}")


if __name__ == "__main__":
    launched_without_args = len(sys.argv) == 1
    try:
        main()
    except Exception as error:
        print()
        print("FAILED")
        print(error)
        if launched_without_args:
            input("\nPress Enter to close...")
        sys.exit(1)
