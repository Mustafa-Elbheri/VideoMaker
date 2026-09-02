import os
import ctypes
import threading
import time
from video_maker.app_paths import bundled_sounds_root, user_sounds_root
from video_maker.app_state import get_startup_sound

def play_ui_sound(sound_name):
    """
    Play a sound from the user folder or fallback to bundled sounds
    """
    try:
        user_path = user_sounds_root() / sound_name
        if user_path.exists():
            _play_async(str(user_path))
            return True
            
        bundled_path = bundled_sounds_root() / sound_name
        if bundled_path.exists():
            _play_async(str(bundled_path))
            return True
            
    except Exception:
        pass
    return False

_last_played_time = {}

def _play_async(path):
    now = time.time()
    if path in _last_played_time and now - _last_played_time[path] < 0.03:
        return
    _last_played_time[path] = now
    
    def worker():
        try:
            alias = f"snd_{time.time_ns()}"
            winmm = ctypes.windll.winmm
            open_cmd = f'open "{path}" type waveaudio alias {alias}'
            if winmm.mciSendStringW(open_cmd, None, 0, 0) != 0:
                return
            play_cmd = f'play {alias} wait'
            winmm.mciSendStringW(play_cmd, None, 0, 0)
            close_cmd = f'close {alias}'
            winmm.mciSendStringW(close_cmd, None, 0, 0)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()

def play_startup_sound():
    sound_setting = get_startup_sound()
    if sound_setting == "disable":
        return
    if sound_setting == "enable":
        play_ui_sound("startup.wav")
    else:
        if os.path.exists(sound_setting):
            _play_async(sound_setting)
