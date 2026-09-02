from __future__ import annotations

from dataclasses import dataclass

from video_maker.app_state import read_preferences, write_preferences
from video_maker.localization import tr


AUDIO_DEVICES_KEY = "audio_devices"
DEFAULT_DEVICE_ID = "default"
INPUT_KIND = "input"
OUTPUT_KIND = "output"


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    kind: str
    channels: int = 0

    @property
    def label(self) -> str:
        return self.name


def system_default_device(kind: str) -> AudioDevice:
    name = "سماعة النظام الافتراضية" if kind == OUTPUT_KIND else "ميكروفون النظام الافتراضي"
    return AudioDevice(DEFAULT_DEVICE_ID, tr(name), kind, 0)


def _sounddevice_module(sd_module=None):
    if sd_module is not None:
        return sd_module
    try:
        import sounddevice as sd
        return sd
    except Exception:
        return None


def _device_channels(info: dict, kind: str) -> int:
    key = "max_output_channels" if kind == OUTPUT_KIND else "max_input_channels"
    try:
        return int(info.get(key, 0) or 0)
    except Exception:
        return 0


def available_devices(kind: str, sd_module=None) -> list[AudioDevice]:
    kind = OUTPUT_KIND if kind == OUTPUT_KIND else INPUT_KIND
    result = [system_default_device(kind)]
    sd = _sounddevice_module(sd_module)
    if sd is None:
        return result
    try:
        devices = sd.query_devices()
    except Exception:
        return result
    for index, info in enumerate(devices or []):
        if not isinstance(info, dict):
            continue
        channels = _device_channels(info, kind)
        if channels <= 0:
            continue
        name = " ".join(str(info.get("name", "") or "").split())
        if not name:
            continue
        result.append(AudioDevice(str(index), name, kind, channels))
    return result


def _preferences() -> dict:
    data = read_preferences().get(AUDIO_DEVICES_KEY, {})
    return dict(data) if isinstance(data, dict) else {}


def get_selected_device_id(kind: str) -> str:
    value = str(_preferences().get(kind, DEFAULT_DEVICE_ID) or DEFAULT_DEVICE_ID)
    return value if value == DEFAULT_DEVICE_ID or value.isdigit() else DEFAULT_DEVICE_ID


def set_selected_device_id(kind: str, device_id: str) -> None:
    kind = OUTPUT_KIND if kind == OUTPUT_KIND else INPUT_KIND
    value = str(device_id or DEFAULT_DEVICE_ID)
    if value != DEFAULT_DEVICE_ID and not value.isdigit():
        value = DEFAULT_DEVICE_ID
    data = read_preferences()
    settings = data.get(AUDIO_DEVICES_KEY, {})
    if not isinstance(settings, dict):
        settings = {}
    settings[kind] = value
    data[AUDIO_DEVICES_KEY] = settings
    write_preferences(data)


def selection_index(devices: list[AudioDevice], selected_id: str) -> int:
    for index, device in enumerate(devices or []):
        if device.id == selected_id:
            return index
    return 0


def selected_sounddevice_device(kind: str, sd_module=None):
    selected = get_selected_device_id(kind)
    if selected == DEFAULT_DEVICE_ID:
        return None
    for device in available_devices(kind, sd_module):
        if device.id == selected:
            return int(device.id)
    return None


def selected_sounddevice_output_device(sd_module=None):
    return selected_sounddevice_device(OUTPUT_KIND, sd_module)


def selected_sounddevice_input_device(sd_module=None):
    return selected_sounddevice_device(INPUT_KIND, sd_module)
