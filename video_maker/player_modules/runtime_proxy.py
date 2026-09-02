import sys

from video_maker.player_modules import shared as _shared

_MISSING = object()


def _player_attr(name):
    module = sys.modules.get("video_maker.player")
    if module is not None:
        value = module.__dict__.get(name, _MISSING)
        if value is not _MISSING:
            return value
    return getattr(_shared, name)


def _call_proxy(name):
    def proxy(*args, **kwargs):
        return _player_attr(name)(*args, **kwargs)
    proxy.__name__ = name
    return proxy


for _name in (
    "ask_audio_save_path",
    "ask_video_path",
    "ask_video_save_path",
    "clean_delete_range",
    "clear_recent_files",
    "get_media_duration",
    "get_program_mode",
    "get_video_duration",
    "has_video_stream",
    "imported_media_root",
    "natural_span",
    "open_recent_file",
    "SaveProgressDialog",
    "set_ripple_mode",
    "split_ranges_for_options",
    "toggle_program_mode",
    "write_normal_seek_step",
    "write_pixels_per_second",
    "write_seek_step",
):
    globals()[_name] = _call_proxy(_name)


def app_data_root(*args, **kwargs):
    return _player_attr("app_data_root")(*args, **kwargs)


def publish_player_methods(cls):
    cls.__module__ = "video_maker.player"
    for name, value in cls.__dict__.items():
        if callable(value):
            try:
                value.__module__ = "video_maker.player"
                value.__qualname__ = f"VideoPlayer.{name}"
            except Exception:
                pass
    return cls
