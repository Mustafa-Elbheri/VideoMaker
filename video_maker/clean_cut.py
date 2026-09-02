import os
import subprocess

import numpy as np
from video_maker.app_paths import ffmpeg_binary

from video_maker.timeline import locate_segment
from video_maker.video_editing import ffmpeg_startupinfo


SEARCH_WINDOW = 0.008
SAMPLE_RATE = 16000


def timeline_to_media_time(timeline, time):
    index, segment, position = locate_segment(timeline, time)
    if segment is None:
        return None, None
    return segment, segment.start + time - position


def read_audio_window(path, center_time, window=SEARCH_WINDOW):
    start = max(0, center_time - window)
    duration = window * 2
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{duration:.6f}",
        "-i",
        path,
        "-vn",
        "-f",
        "f32le",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "pipe:1",
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0 or not result.stdout:
        return start, np.array([], dtype=np.float32)
    return start, np.frombuffer(result.stdout, dtype=np.float32)


def nearest_zero_crossing_time(path, media_time):
    if not os.path.exists(path):
        return media_time
    start, samples = read_audio_window(path, media_time)
    if samples.size < 3:
        return media_time
    target_index = int(round((media_time - start) * SAMPLE_RATE))
    target_index = max(0, min(samples.size - 1, target_index))
    signs = np.signbit(samples)
    crossing_indexes = np.flatnonzero(signs[:-1] != signs[1:]) + 1
    if crossing_indexes.size:
        best_index = int(crossing_indexes[np.argmin(np.abs(crossing_indexes - target_index))])
    else:
        search_radius = max(1, int(SEARCH_WINDOW * SAMPLE_RATE))
        left = max(0, target_index - search_radius)
        right = min(samples.size, target_index + search_radius + 1)
        best_index = left + int(np.argmin(np.abs(samples[left:right])))
    adjusted = start + best_index / SAMPLE_RATE
    if abs(adjusted - media_time) > SEARCH_WINDOW:
        return media_time
    return adjusted


def adjust_cut_time_to_zero_crossing(timeline, time):
    segment, media_time = timeline_to_media_time(timeline, time)
    if segment is None:
        return time
    adjusted_media_time = nearest_zero_crossing_time(segment.path, media_time)
    return time + (adjusted_media_time - media_time)


def clean_delete_range(timeline, start_time, end_time):
    if start_time is None or end_time is None or start_time >= end_time:
        return start_time, end_time
    adjusted_start = adjust_cut_time_to_zero_crossing(timeline, start_time)
    adjusted_end = adjust_cut_time_to_zero_crossing(timeline, end_time)
    if adjusted_start >= adjusted_end:
        return start_time, end_time
    return adjusted_start, adjusted_end
