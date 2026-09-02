from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from video_maker.app_state import read_preferences, write_preferences
from video_maker.encrypted_projects import PROJECT_EXTENSION
from video_maker.localization import tr


RECENT_FILES_KEY = "recent_files"
MAX_RECENT_FILES = 10
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".webp",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".ac3", ".amr", ".ape", ".mka",
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".3gp",
}


@dataclass(frozen=True)
class RecentFile:
    path: str

    @property
    def label(self) -> str:
        return os.path.basename(self.path) or self.path


def _normalise_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path or "").strip()))


def _normalise_key(path: str) -> str:
    return os.path.normcase(_normalise_path(path))


def is_supported_recent_file(path: str) -> bool:
    extension = os.path.splitext(str(path or ""))[1].lower()
    return extension == PROJECT_EXTENSION.lower() or extension in MEDIA_EXTENSIONS


def _stored_paths() -> list[str]:
    paths = read_preferences().get(RECENT_FILES_KEY, [])
    if not isinstance(paths, list):
        return []
    return [str(path) for path in paths if isinstance(path, str) and path.strip()]


def list_recent_files() -> list[RecentFile]:
    result = []
    seen = set()
    for path in _stored_paths():
        absolute = _normalise_path(path)
        key = os.path.normcase(absolute)
        if key in seen or not os.path.isfile(absolute) or not is_supported_recent_file(absolute):
            continue
        seen.add(key)
        result.append(RecentFile(absolute))
    return result


def remember_recent_file(path: str) -> None:
    if not path or not is_supported_recent_file(path):
        return
    absolute = _normalise_path(path)
    if not os.path.isfile(absolute):
        return
    key = os.path.normcase(absolute)
    existing = [stored for stored in _stored_paths() if _normalise_key(stored) != key]
    data = read_preferences()
    data[RECENT_FILES_KEY] = [absolute] + existing[: MAX_RECENT_FILES - 1]
    write_preferences(data)


def remember_recent_files(paths: Iterable[str]) -> None:
    for path in reversed(list(paths or [])):
        remember_recent_file(path)


def clear_recent_files() -> None:
    data = read_preferences()
    if RECENT_FILES_KEY in data:
        data.pop(RECENT_FILES_KEY, None)
        write_preferences(data)


def _player_has_open_project(player) -> bool:
    has_video = getattr(player, "has_video", None)
    if callable(has_video):
        try:
            return bool(has_video())
        except Exception:
            pass
    return bool(getattr(player, "timeline", []))


def open_recent_file(player, path: str) -> bool:
    from video_maker.clipboard_media_paste import paste_file_path

    if not path or not os.path.isfile(path):
        try:
            player.say(tr("الملف لم يعد موجودا على الكمبيوتر"))
        except Exception:
            pass
        return True
    remember_recent_file(path)
    if not _player_has_open_project(player) and not path.lower().endswith(PROJECT_EXTENSION.lower()):
        open_media = getattr(player, "OnOpenMedia", None)
        if callable(open_media):
            open_media(path)
            return True
    return paste_file_path(player, path)
