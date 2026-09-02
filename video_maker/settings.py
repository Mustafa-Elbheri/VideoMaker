import os
import tempfile

from video_maker.app_state import read_preferences, update_preferences


SEEK_STEP_FILE = "seek_step.txt"
DEFAULT_SEEK_STEP = 100
MIN_SEEK_STEP = 1
NORMAL_SEEK_STEP_KEY = "normal_seek_step"
DEFAULT_NORMAL_SEEK_STEP = 5000
MIN_NORMAL_SEEK_STEP = 0


def get_seek_step_path():
    return os.path.join(tempfile.gettempdir(), SEEK_STEP_FILE)


def read_seek_step():
    path = get_seek_step_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as file:
                value = int(file.read().strip())
        except (ValueError, OSError):
            return DEFAULT_SEEK_STEP
        return max(MIN_SEEK_STEP, value)
    return DEFAULT_SEEK_STEP


def seek_step_increment(current):
    current = max(MIN_SEEK_STEP, int(current))
    if current < 10:
        return 1
    if current < 50:
        return 2
    if current < 100:
        return 5
    if current < 500:
        return 10
    if current < 1000:
        return 25
    if current < 5000:
        return 100
    if current < 10000:
        return 500
    if current < 60000:
        return 1000
    return 5000


def format_seek_step_ms(ms):
    ms = max(0, int(ms))
    if ms % 1000 == 0:
        return f"{ms // 1000} ثانية"
    return f"{ms} ملي ثانية"


def write_seek_step(seek_step):
    with open(get_seek_step_path(), "w") as file:
        file.write(str(seek_step))


def delete_seek_step_file():
    path = get_seek_step_path()
    if os.path.exists(path):
        os.remove(path)
        return path
    return None


def normalize_normal_seek_step(value):
    try:
        return max(MIN_NORMAL_SEEK_STEP, int(value))
    except (TypeError, ValueError):
        return DEFAULT_NORMAL_SEEK_STEP


def read_normal_seek_step():
    return normalize_normal_seek_step(read_preferences().get(NORMAL_SEEK_STEP_KEY, DEFAULT_NORMAL_SEEK_STEP))


def write_normal_seek_step(seek_step):
    update_preferences(**{NORMAL_SEEK_STEP_KEY: normalize_normal_seek_step(seek_step)})


def decrease_normal_seek_step(current):
    current = normalize_normal_seek_step(current)
    if current > 1000:
        return max(1000, current - 1000)
    if current > 0:
        return max(0, current - 100)
    return 0


def increase_normal_seek_step(current):
    current = normalize_normal_seek_step(current)
    if current < 1000:
        return min(1000, current + 100)
    return current + 1000


PIXELS_PER_SECOND_KEY = "pixels_per_second"
DEFAULT_PIXELS_PER_SECOND = 80
MIN_PIXELS_PER_SECOND = 1
MAX_PIXELS_PER_SECOND = 1000
SEEK_PIXELS = 1


def normalize_pixels_per_second(value):
    try:
        return max(MIN_PIXELS_PER_SECOND, min(MAX_PIXELS_PER_SECOND, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PIXELS_PER_SECOND


def read_pixels_per_second():
    return normalize_pixels_per_second(read_preferences().get(PIXELS_PER_SECOND_KEY, DEFAULT_PIXELS_PER_SECOND))


def write_pixels_per_second(value):
    update_preferences(**{PIXELS_PER_SECOND_KEY: normalize_pixels_per_second(value)})


def pixels_per_second_increment(current):
    current = normalize_pixels_per_second(current)
    if current < 20:
        return 5
    if current < 60:
        return 10
    if current < 120:
        return 20
    if current < 240:
        return 40
    return 80


def seek_seconds_for_pixels(pixels, pixels_per_second):
    px = max(1, int(pixels or SEEK_PIXELS))
    pps = max(MIN_PIXELS_PER_SECOND, int(pixels_per_second) or DEFAULT_PIXELS_PER_SECOND)
    return px / pps
