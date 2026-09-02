import json
import json
import os
import tempfile
import time

from video_maker.timeline import TimelineSegment
from video_maker.work_sessions import app_data_root, session_payload


PREFERENCES_FILE = "preferences.json"
CRASH_SESSION_FILE = "crash_session.json"
CRASH_SESSION_BACKUP_FILE = "crash_session.backup.json"
CRASH_SESSION_SCHEMA_VERSION = 2
DECLINED_UPDATE_INSTALL_KEY = "declined_update_install_id"
CUSTOM_APP_NAME_KEY = "custom_app_name"
USAGE_CONSENT_ACCEPTED_KEY = "usage_consent_accepted"


def preferences_path():
    return os.path.join(app_data_root(), PREFERENCES_FILE)


def crash_session_path():
    return os.path.join(app_data_root(), CRASH_SESSION_FILE)


def crash_session_backup_path():
    return os.path.join(app_data_root(), CRASH_SESSION_BACKUP_FILE)


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def write_json(path, data):
    """Atomically persist JSON and force its contents to disk before replace."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prefix = f".{os.path.basename(path)}."
    handle, temp_path = tempfile.mkstemp(suffix=".tmp", prefix=prefix, dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def read_preferences():
    data = read_json(preferences_path(), {})
    if not isinstance(data, dict):
        return {}
    return data


def write_preferences(data):
    write_json(preferences_path(), dict(data or {}))


def update_preferences(**updates):
    data = read_preferences()
    data.update(updates)
    write_preferences(data)
    return data


def get_declined_update_install_id():
    return str(read_preferences().get(DECLINED_UPDATE_INSTALL_KEY, "") or "")


def set_declined_update_install_id(value):
    update_preferences(**{DECLINED_UPDATE_INSTALL_KEY: str(value or "")})


def normalized_custom_app_name(value):
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:120]


def get_custom_app_name():
    return normalized_custom_app_name(read_preferences().get(CUSTOM_APP_NAME_KEY, ""))


def set_custom_app_name(value):
    update_preferences(**{CUSTOM_APP_NAME_KEY: normalized_custom_app_name(value)})


def is_usage_consent_accepted():
    return bool(read_preferences().get(USAGE_CONSENT_ACCEPTED_KEY, False))


def set_usage_consent_accepted(value=True):
    update_preferences(**{USAGE_CONSENT_ACCEPTED_KEY: bool(value)})


def get_volume(default=1.0):
    from video_maker.volume_boost import persisted_program_volume

    try:
        return persisted_program_volume(read_preferences().get("volume", default), default)
    except Exception:
        return persisted_program_volume(default)


def set_volume(value):
    from video_maker.volume_boost import persisted_program_volume

    update_preferences(volume=persisted_program_volume(value))


def get_master_volume_db(default=0.0):
    from video_maker.volume_boost import persisted_master_volume_db

    try:
        return persisted_master_volume_db(read_preferences().get("master_volume_db", default), default)
    except Exception:
        return persisted_master_volume_db(default)


def set_master_volume_db(value):
    from video_maker.volume_boost import persisted_master_volume_db

    update_preferences(master_volume_db=persisted_master_volume_db(value))


def get_last_open_dir():
    path = read_preferences().get("last_open_dir", "")
    return path if path and os.path.isdir(path) else ""


def set_last_open_dir(path):
    if path:
        update_preferences(last_open_dir=os.path.dirname(os.path.abspath(path)))


MEDIA_DIR_KEYS = {
    "image": "last_image_dir",
    "audio": "last_audio_dir",
    "video": "last_video_dir",
}


def _directory_for_path(path):
    if not path:
        return ""
    absolute = os.path.abspath(path)
    return absolute if os.path.isdir(absolute) else os.path.dirname(absolute)


def get_last_media_dir(kind):
    key = MEDIA_DIR_KEYS.get(str(kind or "").lower())
    if not key:
        return ""
    path = read_preferences().get(key, "")
    return path if path and os.path.isdir(path) else ""


def set_last_media_dir(kind, path):
    key = MEDIA_DIR_KEYS.get(str(kind or "").lower())
    directory = _directory_for_path(path)
    if key and directory:
        update_preferences(**{key: directory, "last_open_dir": directory})


def get_last_dialog_dir(dialog_key, fallback_kind=""):
    key = str(dialog_key or "").strip()
    data = read_preferences()
    dialog_dirs = data.get("last_open_dialog_dirs", {})
    if key and isinstance(dialog_dirs, dict):
        path = dialog_dirs.get(key, "")
        if path and os.path.isdir(path):
            return path
    return get_last_media_dir(fallback_kind) or get_last_open_dir()


def set_last_dialog_dir(dialog_key, path, fallback_kind=""):
    key = str(dialog_key or "").strip()
    directory = _directory_for_path(path)
    if not key or not directory:
        return
    data = read_preferences()
    dialog_dirs = data.get("last_open_dialog_dirs", {})
    if not isinstance(dialog_dirs, dict):
        dialog_dirs = {}
    dialog_dirs[key] = directory
    data["last_open_dialog_dirs"] = dialog_dirs
    data["last_open_dir"] = directory
    media_key = MEDIA_DIR_KEYS.get(str(fallback_kind or "").lower())
    if media_key:
        data[media_key] = directory
    write_preferences(data)


def get_last_save_dir():
    path = read_preferences().get("last_save_dir", "")
    return path if path and os.path.isdir(path) else ""


def set_last_save_dir(path):
    if path:
        update_preferences(last_save_dir=os.path.dirname(os.path.abspath(path)))


def get_audio_effect_values(effect_key):
    values = read_preferences().get("audio_effects", {}).get(effect_key, {})
    return dict(values) if isinstance(values, dict) else {}


def set_audio_effect_values(effect_key, values):
    data = read_preferences()
    effects = data.get("audio_effects", {})
    if not isinstance(effects, dict):
        effects = {}
    effects[effect_key] = dict(values or {})
    data["audio_effects"] = effects
    write_preferences(data)


def get_text_overlay_last_settings():
    section = read_preferences().get("text_overlay", {})
    values = section.get("last_settings", {}) if isinstance(section, dict) else {}
    return dict(values) if isinstance(values, dict) else {}


def set_text_overlay_last_settings(values):
    data = read_preferences()
    section = data.get("text_overlay", {})
    if not isinstance(section, dict):
        section = {}
    section["last_settings"] = dict(values or {})
    data["text_overlay"] = section
    write_preferences(data)


def get_language(default="ar"):
    language = read_preferences().get("language", default)
    return language if language in ("ar", "en", "fr") else default


def set_language(language):
    if language in ("ar", "en", "fr"):
        update_preferences(language=language)


def get_theme(default="default"):
    theme = read_preferences().get("theme", default)
    return theme if theme in ("default", "dark", "high_black", "high_light") else default


def set_theme(theme):
    if theme in ("default", "dark", "high_black", "high_light"):
        update_preferences(theme=theme)


RIPPLE_MODE_KEY = "ripple_mode"
RIPPLE_MODES = ("per_track", "all_tracks", "off")


def normalize_ripple_mode(value):
    return value if value in RIPPLE_MODES else RIPPLE_MODES[0]


def get_ripple_mode(default="per_track"):
    return normalize_ripple_mode(read_preferences().get(RIPPLE_MODE_KEY, default))


def set_ripple_mode(value):
    update_preferences(**{RIPPLE_MODE_KEY: normalize_ripple_mode(value)})


def _path_exists(path):
    return isinstance(path, str) and bool(path) and os.path.exists(path)


def _payload_has_recoverable_timeline(payload):
    """Return True only when the payload can restore a complete timeline."""
    if not isinstance(payload, dict):
        return False
    timeline = payload.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return False
    for item in timeline:
        if not isinstance(item, dict) or not _path_exists(item.get("path")):
            return False
        audio_path = str(item.get("audio_path", "") or "")
        if audio_path and not _path_exists(audio_path):
            return False
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            return False
        if end <= start:
            return False
    main_audio_override_path = str(payload.get("main_audio_override_path", "") or "")
    if main_audio_override_path and not _path_exists(main_audio_override_path):
        return False
    return True


def _crash_session_candidates():
    candidates = []
    for path in (crash_session_path(), crash_session_backup_path()):
        payload = read_json(path, {})
        if not _payload_has_recoverable_timeline(payload):
            continue
        try:
            updated_at = float(payload.get("updated_at", payload.get("created_at", 0)) or 0)
        except (TypeError, ValueError):
            updated_at = 0
        try:
            modified_at = os.path.getmtime(path)
        except OSError:
            modified_at = 0
        candidates.append((max(updated_at, modified_at), path, payload))
    return sorted(candidates, key=lambda item: item[0], reverse=True)


def crash_session_exists():
    return bool(_crash_session_candidates())


def build_crash_session_payload(player):
    if not getattr(player, "timeline", None):
        return None
    payload = session_payload("استعادة جلسة الإغلاق المفاجئ", player, list(player.timeline))
    payload["schema_version"] = CRASH_SESSION_SCHEMA_VERSION
    payload["updated_at"] = time.time()
    return payload


def write_crash_session_payload(payload):
    if not payload or not isinstance(payload.get("timeline"), list) or not payload.get("timeline"):
        return
    stable_payload = dict(payload)
    stable_payload["schema_version"] = CRASH_SESSION_SCHEMA_VERSION
    stable_payload["updated_at"] = time.time()

    # Write the independent backup first. If the application is terminated
    # while the primary is being replaced, recovery still has a complete file.
    backup_written = False
    backup_error = None
    try:
        write_json(crash_session_backup_path(), stable_payload)
        backup_written = True
    except Exception as error:
        backup_error = error

    try:
        write_json(crash_session_path(), stable_payload)
    except Exception:
        if not backup_written:
            if backup_error is not None:
                raise backup_error
            raise


def write_crash_session(player):
    write_crash_session_payload(build_crash_session_payload(player))


def read_crash_session():
    candidates = _crash_session_candidates()
    if not candidates:
        return {"timeline": []}
    _, source_path, payload = candidates[0]
    timeline = [
        TimelineSegment(
            item["path"],
            float(item["start"]),
            float(item["end"]),
            float(item.get("speed", 1.0) or 1.0),
            float(item.get("audio_volume", 1.0) if item.get("audio_volume", 1.0) is not None else 1.0),
            str(item.get("audio_path", "") or ""),
            float(item["audio_start"]) if item.get("audio_start") is not None else None,
            str(item.get("navigation_group", "") or ""),
            str(item.get("source_file_id", "") or ""),
            str(item.get("source_file_name", "") or ""),
            str(item.get("transition", "") or ""),
            max(0.0, float(item.get("transition_duration", 1.0) or 1.0)),
            max(0.0, float(item.get("audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(item.get("audio_fade_out", 0.0) or 0.0)),
        )
        for item in payload.get("timeline", [])
    ]
    payload = dict(payload)
    payload["timeline"] = timeline

    # Repair a missing/corrupt primary from a valid backup so subsequent reads
    # remain predictable. Failure here does not block the current restoration.
    if source_path != crash_session_path():
        try:
            serializable = dict(payload)
            serializable["timeline"] = [
                {
                    "path": segment.path,
                    "start": segment.start,
                    "end": segment.end,
                    "speed": float(getattr(segment, "speed", 1.0) or 1.0),
                    "audio_volume": float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
                    "audio_path": str(getattr(segment, "audio_path", "") or ""),
                    "audio_start": getattr(segment, "audio_start", None),
                    "navigation_group": str(getattr(segment, "navigation_group", "") or ""),
                    "source_file_id": str(getattr(segment, "source_file_id", "") or ""),
                    "source_file_name": str(getattr(segment, "source_file_name", "") or ""),
                    "transition": str(getattr(segment, "transition", "") or ""),
                    "transition_duration": max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
                    "audio_fade_in": max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
                    "audio_fade_out": max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
                }
                for segment in timeline
            ]
            write_json(crash_session_path(), serializable)
        except Exception:
            pass
    return payload


def clear_crash_session():
    for path in (crash_session_path(), crash_session_backup_path()):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    root = app_data_root()
    for name in os.listdir(root):
        if not (name.startswith(f".{CRASH_SESSION_FILE}.") or name.startswith(f".{CRASH_SESSION_BACKUP_FILE}.")):
            continue
        try:
            os.remove(os.path.join(root, name))
        except OSError:
            pass

def get_language(default="ar"):
    language = read_preferences().get("language", default)
    return language if language in ("ar", "en", "fr") else default

def set_language(language):
    if language in ("ar", "en", "fr"):
        update_preferences(language=language)

def get_theme(default="default"):
    theme = read_preferences().get("theme", default)
    return theme if theme in ("default", "dark", "high_black", "high_light") else default

def set_theme(theme):
    if theme in ("default", "dark", "high_black", "high_light"):
        update_preferences(theme=theme)

def get_startup_sound(default="enable"):
    return read_preferences().get("startup_sound", default)

def set_startup_sound(value):
    update_preferences(startup_sound=value)

def get_speech_mode():
    return read_preferences().get("speech_mode", "enable")

def set_speech_mode(mode):
    update_preferences(speech_mode=mode)

def get_speech_custom_settings():
    settings = read_preferences().get("speech_custom_settings", {})
    if not isinstance(settings, dict):
        return {}
    return settings

def set_speech_custom_setting(key, state):
    settings = get_speech_custom_settings()
    settings[key] = bool(state)
    update_preferences(speech_custom_settings=settings)

def get_nav_sounds_mode():
    return read_preferences().get("nav_sounds_mode", "enable")

def set_nav_sounds_mode(mode):
    update_preferences(nav_sounds_mode=mode)

def get_nav_sounds_custom():
    settings = read_preferences().get("nav_sounds_custom", {})
    if not isinstance(settings, dict):
        return {}
    return settings

def set_nav_sounds_custom(custom_dict):
    update_preferences(nav_sounds_custom=custom_dict)

def get_startup_action():
    return int(read_preferences().get('startup_action', 0))

def set_startup_action(value):
    update_preferences(startup_action=int(value))

def get_last_project_path():
    return read_preferences().get('last_project_path', '')

def set_last_project_path(path):
    update_preferences(last_project_path=path)
