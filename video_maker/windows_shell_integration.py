"""Windows Explorer integration for the installed Video Maker executable.

The integration is deliberately per-user: it needs no administrator rights and
never registers a source checkout or a temporary Python interpreter.  The
installed executable is exposed in Explorer's Send to menu, and ``.elbheri``
project files open with the application when activated.
"""

from __future__ import annotations

import base64
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Optional, Sequence


PROJECT_EXTENSION = ".elbheri"
PROJECT_PROG_ID = "Elbheri.VideoMaker.Project"
DEFAULT_APP_TITLE = "صانع الفيديو"
FALLBACK_DEFAULT_APP_NAMES = (
    DEFAULT_APP_TITLE,
    "Video Maker",
    "Créateur vidéo",
)


def installed_executable() -> Optional[str]:
    """Return the installed frozen executable, never a source interpreter."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    try:
        executable = Path(sys.executable).resolve()
    except Exception:
        return None
    if executable.suffix.lower() != ".exe" or not executable.is_file():
        return None
    return str(executable)


def startup_file_arguments(argv: Optional[Sequence[str]] = None) -> list[str]:
    """Extract existing file paths passed by Send to or a file association."""
    values = list(sys.argv if argv is None else argv)
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in values[1:]:
        value = str(raw_value or "").strip().strip('"')
        if not value or value.startswith("--"):
            continue
        try:
            path = os.path.abspath(os.path.expandvars(os.path.expanduser(value)))
        except Exception:
            continue
        if not os.path.isfile(path):
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _send_to_directory() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise RuntimeError("APPDATA is unavailable")
    path = Path(appdata) / "Microsoft" / "Windows" / "SendTo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _desktop_directory() -> Path:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "Desktop")
            path = Path(os.path.expandvars(str(value)))
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            pass
    path = Path.home() / "Desktop"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _powershell_executable() -> Optional[str]:
    candidate = shutil.which("powershell.exe") or shutil.which("powershell")
    if candidate:
        return candidate
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    path = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(path) if path.is_file() else None


def _ps_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_hidden(command: Sequence[str], timeout: int = 15) -> int:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
        creationflags=creation_flags,
    )
    return int(completed.returncode)


def _create_shortcut_with_powershell(shortcut_path: Path, executable: str, display_name: str) -> bool:
    powershell = _powershell_executable()
    if not powershell:
        return False
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$shell = New-Object -ComObject WScript.Shell",
            f"$shortcut = $shell.CreateShortcut({_ps_literal(str(shortcut_path))})",
            f"$shortcut.TargetPath = {_ps_literal(executable)}",
            f"$shortcut.WorkingDirectory = {_ps_literal(str(Path(executable).parent))}",
            f"$shortcut.IconLocation = {_ps_literal(executable + ',0')}",
            f"$shortcut.Description = {_ps_literal(display_name)}",
            "$shortcut.Save()",
        )
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        return _run_hidden(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ]
        ) == 0 and shortcut_path.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


def _cscript_executable() -> Optional[str]:
    candidate = shutil.which("cscript.exe") or shutil.which("cscript")
    if candidate:
        return candidate
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    path = Path(system_root) / "System32" / "cscript.exe"
    return str(path) if path.is_file() else None


def _create_shortcut_with_cscript(shortcut_path: Path, executable: str, display_name: str) -> bool:
    cscript = _cscript_executable()
    if not cscript:
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix="video_maker_sendto_", suffix=".vbs")
    os.close(descriptor)
    script_path = Path(temporary_name)
    script = "\r\n".join(
        (
            'Set shell = CreateObject("WScript.Shell")',
            'Set shortcut = shell.CreateShortcut(WScript.Arguments(0))',
            'shortcut.TargetPath = WScript.Arguments(1)',
            'shortcut.WorkingDirectory = WScript.Arguments(2)',
            'shortcut.IconLocation = WScript.Arguments(3)',
            'shortcut.Description = WScript.Arguments(4)',
            'shortcut.Save',
        )
    )
    try:
        script_path.write_text(script, encoding="ascii")
        try:
            return _run_hidden(
                [
                    cscript,
                    "//NoLogo",
                    str(script_path),
                    str(shortcut_path),
                    executable,
                    str(Path(executable).parent),
                    executable + ",0",
                    display_name,
                ]
            ) == 0 and shortcut_path.is_file()
        except (OSError, subprocess.SubprocessError):
            return False
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def _shortcut_safe_name(display_name: str) -> str:
    safe_name = "".join(character for character in display_name if character not in '<>:"/\\|?*').strip()
    return safe_name or DEFAULT_APP_TITLE


def _default_app_display_names() -> tuple[str, ...]:
    names = list(FALLBACK_DEFAULT_APP_NAMES)
    try:
        from video_maker.localization import TEXTS

        for translations in TEXTS.values():
            translated = str(translations.get(DEFAULT_APP_TITLE, "")).strip()
            if translated:
                names.append(translated)
    except Exception:
        pass
    return tuple(dict.fromkeys(name for name in names if name.strip()))


def _shortcut_names_for_display_names(display_names: Sequence[str] = ()) -> tuple[str, ...]:
    names = []
    for display_name in (*_default_app_display_names(), *display_names):
        safe_name = _shortcut_safe_name(display_name)
        if safe_name:
            names.append(f"{safe_name}.lnk")
    return tuple(dict.fromkeys(names))


def _remove_obsolete_shortcuts(directory: Path, current_display_name: str, previous_display_names: Sequence[str] = ()) -> None:
    current_path = directory / f"{_shortcut_safe_name(current_display_name)}.lnk"
    for shortcut_name in _shortcut_names_for_display_names(previous_display_names):
        shortcut_path = directory / shortcut_name
        if shortcut_path == current_path:
            continue
        try:
            shortcut_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _create_send_to_shortcut(executable: str, display_name: str, previous_display_name: str = "") -> Path:
    send_to = _send_to_directory()
    shortcut_path = send_to / f"{_shortcut_safe_name(display_name)}.lnk"

    _remove_obsolete_shortcuts(send_to, display_name, (previous_display_name,))

    if not _create_shortcut_with_powershell(shortcut_path, executable, display_name):
        if not _create_shortcut_with_cscript(shortcut_path, executable, display_name):
            raise RuntimeError("Could not create the Send to shortcut")
    return shortcut_path


def _create_desktop_shortcut(executable: str, display_name: str, previous_display_name: str = "") -> Path:
    desktop = _desktop_directory()
    safe_name = _shortcut_safe_name(display_name)
    shortcut_path = desktop / f"{safe_name}.lnk"

    _remove_obsolete_shortcuts(desktop, display_name, (previous_display_name,))

    if not _create_shortcut_with_powershell(shortcut_path, executable, display_name):
        if not _create_shortcut_with_cscript(shortcut_path, executable, display_name):
            raise RuntimeError("Could not create the desktop shortcut")
    return shortcut_path


def _set_registry_value(winreg, root, key_path: str, name, value: str) -> None:
    with winreg.CreateKeyEx(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _register_project_association(executable: str, app_name: str, project_type_name: str) -> None:
    import winreg

    classes = r"Software\Classes"
    extension_key = classes + "\\" + PROJECT_EXTENSION
    _set_registry_value(winreg, winreg.HKEY_CURRENT_USER, extension_key, None, PROJECT_PROG_ID)
    _set_registry_value(
        winreg,
        winreg.HKEY_CURRENT_USER,
        extension_key + r"\OpenWithProgids",
        PROJECT_PROG_ID,
        "",
    )

    prog_key = classes + "\\" + PROJECT_PROG_ID
    _set_registry_value(winreg, winreg.HKEY_CURRENT_USER, prog_key, None, project_type_name)
    _set_registry_value(winreg, winreg.HKEY_CURRENT_USER, prog_key, "FriendlyTypeName", project_type_name)
    _set_registry_value(winreg, winreg.HKEY_CURRENT_USER, prog_key + r"\DefaultIcon", None, f'"{executable}",0')
    _set_registry_value(
        winreg,
        winreg.HKEY_CURRENT_USER,
        prog_key + r"\shell\open\command",
        None,
        f'"{executable}" "%1"',
    )

    executable_name = Path(executable).name
    app_key = classes + rf"\Applications\{executable_name}"
    _set_registry_value(winreg, winreg.HKEY_CURRENT_USER, app_key, "FriendlyAppName", app_name)
    _set_registry_value(
        winreg,
        winreg.HKEY_CURRENT_USER,
        app_key + r"\SupportedTypes",
        PROJECT_EXTENSION,
        "",
    )


def _notify_shell_association_changed() -> None:
    try:
        shell32 = ctypes.windll.shell32
        # SHCNE_ASSOCCHANGED | SHCNF_IDLIST
        shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def ensure_windows_shell_integration(
    app_name: str = "صانع الفيديو",
    project_type_name: str = "مشروع صانع الفيديو",
    executable: Optional[str] = None,
    create_desktop_shortcut: bool = False,
    previous_app_name: str = "",
) -> bool:
    """Idempotently install Send to and .elbheri integration for this user."""
    target = executable or installed_executable()
    if not target:
        return False
    _register_project_association(target, app_name, project_type_name)
    _create_send_to_shortcut(target, app_name, previous_app_name)
    if create_desktop_shortcut:
        _create_desktop_shortcut(target, app_name, previous_app_name)
    _notify_shell_association_changed()
    return True


def _delete_registry_tree(winreg, root, key_path: str) -> None:
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_registry_tree(winreg, root, key_path + "\\" + child)
        winreg.DeleteKey(root, key_path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def remove_windows_shell_integration(executable: Optional[str] = None) -> bool:
    """Remove registrations created by this module (usable by an uninstaller)."""
    if os.name != "nt":
        return False
    import winreg

    classes = r"Software\Classes"
    extension_key = classes + "\\" + PROJECT_EXTENSION
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, extension_key) as key:
            current, _ = winreg.QueryValueEx(key, None)
    except OSError:
        current = None
    if current == PROJECT_PROG_ID:
        _delete_registry_tree(winreg, winreg.HKEY_CURRENT_USER, extension_key)
    _delete_registry_tree(winreg, winreg.HKEY_CURRENT_USER, classes + "\\" + PROJECT_PROG_ID)

    target = executable or installed_executable()
    if target:
        _delete_registry_tree(
            winreg,
            winreg.HKEY_CURRENT_USER,
            classes + rf"\Applications\{Path(target).name}",
        )
    try:
        send_to = _send_to_directory()
        for name in _shortcut_names_for_display_names():
            try:
                (send_to / name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
    except Exception:
        pass
    _notify_shell_association_changed()
    return True
