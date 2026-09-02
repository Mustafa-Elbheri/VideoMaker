import os
import re
import shutil
import subprocess
import tempfile
import threading
import math

import numpy as np
import wx
from video_maker.mpv_player import MPVMediaCtrl, MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED, EVT_MEDIA_LOADED, EVT_MEDIA_FINISHED
from video_maker.app_paths import ffmpeg_binary

from video_maker.operation_control import OperationCancelled, is_operation_cancelled
from video_maker.audio_effects import current_program_output_volume
from video_maker.localization import tr
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.error_reporting import show_error
from video_maker.problem_log import trace_event
from video_maker.save_progress import SaveProgressDialog
from video_maker.reliable_playback import ReliableAudioPlayer, atempo_filter, reliable_audio_available
from video_maker.timeline import TimelineSegment, delete_range, insert_segments, slice_segments
from video_maker.video_editing import (
    get_media_duration,
    has_audio_stream,
    has_video_stream,
    segment_audio_path,
    segment_audio_start,
    segment_audio_volume,
    segment_speed,
    write_timeline_audio,
    write_timeline_video,
)


DEFAULT_THRESHOLD_DB = -42
DEFAULT_MINIMUM_SILENCE_MS = 350
DEFAULT_PADDING_MS = 120
LIVE_PREVIEW_SAMPLE_RATE = 48000
LIVE_PREVIEW_CHANNELS = 2
LIVE_PREVIEW_BLOCK_FRAMES = 2048


def log_silence_removal(action, **fields):
    try:
        trace_event("silence_removal", action, **fields)
    except Exception:
        pass


def removal_cancelled(cancelled_callback):
    try:
        return bool(cancelled_callback and cancelled_callback())
    except Exception:
        return False


def raise_if_cancelled(cancelled_callback):
    if removal_cancelled(cancelled_callback):
        raise OperationCancelled()


def ffmpeg_startupinfo():
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def run_process_cancellable(command, cancelled_callback=None):
    if not cancelled_callback:
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    while True:
        if removal_cancelled(cancelled_callback):
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=0.2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            raise OperationCancelled()
        try:
            stdout, stderr = process.communicate(timeout=0.05)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue


def silence_remove_filter(threshold_db, minimum_silence, padding):
    minimum_silence = max(0.001, float(minimum_silence or 0.001))
    padding = max(0.0, float(padding or 0.0))
    threshold_db = int(threshold_db)
    return (
        "silenceremove="
        "start_periods=1:"
        f"start_duration={minimum_silence:.3f}:"
        f"start_threshold={threshold_db}dB:"
        f"start_silence={padding:.3f}:"
        "stop_periods=-1:"
        f"stop_duration={minimum_silence:.3f}:"
        f"stop_threshold={threshold_db}dB:"
        f"stop_silence={padding:.3f}:"
        "detection=rms:"
        "window=0.020"
    )


def intervals_duration(intervals):
    return sum(max(0.0, float(end) - float(start)) for start, end in intervals)


def trim_intervals_for_output_offset(intervals, offset=0.0, limit=None):
    offset = max(0.0, float(offset or 0.0))
    remaining_limit = None if limit is None else max(0.0, float(limit or 0.0))
    result = []
    consumed = 0.0
    for start, end in intervals:
        start = float(start)
        end = float(end)
        duration = max(0.0, end - start)
        if duration <= 0.0:
            continue
        if consumed + duration <= offset:
            consumed += duration
            continue
        local_offset = max(0.0, offset - consumed)
        keep_start = start + local_offset
        keep_duration = max(0.0, end - keep_start)
        if remaining_limit is not None:
            if remaining_limit <= 0.0:
                break
            keep_duration = min(keep_duration, remaining_limit)
            remaining_limit -= keep_duration
        if keep_duration > 0.01:
            result.append((keep_start, keep_start + keep_duration))
        consumed += duration
    return result


def live_preview_audio_parts(timeline, selection_start, intervals, offset=0.0, limit=None):
    kept_intervals = trim_intervals_for_output_offset(intervals, offset, limit)
    parts = []
    audio_stream_cache = {}
    for keep_start, keep_end in kept_intervals:
        segments = slice_segments(timeline, selection_start + keep_start, selection_start + keep_end)
        for segment in segments:
            path = segment_audio_path(segment)
            if not path or not os.path.exists(path):
                continue
            try:
                audio_key = os.path.abspath(path).lower()
                if audio_key not in audio_stream_cache:
                    audio_stream_cache[audio_key] = has_audio_stream(path)
                if not audio_stream_cache[audio_key]:
                    continue
            except Exception:
                continue
            speed = segment_speed(segment)
            source_start = segment_audio_start(segment)
            source_duration = max(0.0, float(segment.end) - float(segment.start))
            output_duration = source_duration / max(0.05, speed)
            if source_duration <= 0.01 or output_duration <= 0.01:
                continue
            parts.append({
                "path": path,
                "source_start": source_start,
                "source_duration": source_duration,
                "speed": speed,
                "volume": segment_audio_volume(segment),
                "output_duration": output_duration,
            })
    return parts


def trim_preview_parts_for_output_offset(parts, offset=0.0, limit=None):
    offset = max(0.0, float(offset or 0.0))
    remaining_limit = None if limit is None else max(0.0, float(limit or 0.0))
    result = []
    consumed = 0.0
    for part in parts:
        speed = max(0.05, float(part.get("speed", 1.0) or 1.0))
        output_duration = max(0.0, float(part.get("output_duration", 0.0) or 0.0))
        if output_duration <= 0.01:
            continue
        if consumed + output_duration <= offset:
            consumed += output_duration
            continue
        local_output_offset = max(0.0, offset - consumed)
        keep_output_duration = max(0.0, output_duration - local_output_offset)
        if remaining_limit is not None:
            if remaining_limit <= 0.0:
                break
            keep_output_duration = min(keep_output_duration, remaining_limit)
            remaining_limit -= keep_output_duration
        if keep_output_duration > 0.01:
            adjusted = dict(part)
            if adjusted.get("silence"):
                adjusted["source_duration"] = keep_output_duration
            else:
                adjusted["source_start"] = float(part["source_start"]) + local_output_offset * speed
                adjusted["source_duration"] = keep_output_duration * speed
            adjusted["output_duration"] = keep_output_duration
            result.append(adjusted)
        consumed += output_duration
    return result


def build_live_silence_preview_command_from_parts(parts, offset=0.0, limit=None, rate=1.0):
    parts = trim_preview_parts_for_output_offset(parts, offset, limit)
    if not parts:
        return None
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
    ]
    path_to_index = {}
    input_index = 0
    for part in parts:
        if part.get("silence"):
            continue
        path = part["path"]
        if path not in path_to_index:
            path_to_index[path] = input_index
            command.extend(["-i", path])
            input_index += 1
            
    labels = []
    filters = []
    for index, part in enumerate(parts):
        label = f"a{index}"
        if part.get("silence"):
            duration = max(0.05, float(part.get("output_duration", part.get("source_duration", 0.05)) or 0.05))
            filters.append(f"anullsrc=r=48000:cl=stereo:d={duration:.6f},asetpts=PTS-STARTPTS[{label}]")
            labels.append(f"[{label}]")
            continue
        
        file_input_index = path_to_index[part["path"]]
        start = part["source_start"]
        end = start + part["source_duration"]
        chain = [
            f"[{file_input_index}:a]atrim=start={start:.6f}:end={end:.6f}",
            "asetpts=PTS-STARTPTS",
            "aresample=48000",
        ]
        if abs(part["speed"] - 1.0) > 0.001:
            chain.append(atempo_filter(part["speed"]))
        if abs(part["volume"] - 1.0) > 0.001:
            chain.append(f"volume={part['volume']:.6f}")
        filters.append(",".join(chain) + f"[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        joined_label = "joined"
        filters.append(f"{labels[0]}anull[{joined_label}]")
    else:
        joined_label = "joined"
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[{joined_label}]")
    filtered_label = "silence_removed"
    playback_chain = ["anull"]
    if abs(float(rate or 1.0) - 1.0) > 0.001:
        playback_chain.append(atempo_filter(rate))
    playback_chain.append(f"aresample={LIVE_PREVIEW_SAMPLE_RATE}:async=1:first_pts=0")
    filters.append(f"[{joined_label}]" + ",".join(playback_chain) + f"[{filtered_label}]")
    command.extend([
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{filtered_label}]",
    ])
    if limit is not None:
        command.extend(["-t", f"{max(0.05, float(limit)):.6f}"])
    command.extend([
        "-f",
        "f32le",
        "-ac",
        str(LIVE_PREVIEW_CHANNELS),
        "-ar",
        str(LIVE_PREVIEW_SAMPLE_RATE),
        "pipe:1",
    ])
    return command


def build_live_silence_preview_command(timeline, selection_start, intervals, offset=0.0, limit=None, rate=1.0):
    parts = live_preview_audio_parts(timeline, selection_start, intervals)
    return build_live_silence_preview_command_from_parts(parts, offset, limit, rate)


def run_silence_detect(input_path, threshold_db, minimum_silence, start_time=0, duration=None, cancelled_callback=None):
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-nostdin",
    ]
    if start_time > 0:
        command.extend(["-ss", f"{start_time:.6f}"])
    if duration is not None:
        command.extend(["-t", f"{duration:.6f}"])
    command.extend([
        "-i",
        input_path,
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={minimum_silence}",
        "-f",
        "null",
        "-",
    ])
    result = run_process_cancellable(command, cancelled_callback)
    output = result.stderr.decode("utf-8", errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(output or "تعذر تحليل الصمت")
    silence_ranges = []
    current_start = None
    for line in output.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
        end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            silence_ranges.append((current_start, float(end_match.group(1))))
            current_start = None
    if current_start is not None and duration is not None:
        silence_ranges.append((current_start, duration))
    if duration is not None and start_time > 0 and any(max(start, end) > duration + 0.5 for start, end in silence_ranges):
        silence_ranges = [(start - start_time, end - start_time) for start, end in silence_ranges]
    if duration is not None:
        silence_ranges = [(max(0, start), min(duration, end)) for start, end in silence_ranges if min(duration, end) > max(0, start)]
    return silence_ranges


def run_fast_silence_detect(input_path, threshold_db, minimum_silence, start_time=0, duration=None, cancelled_callback=None, progress_callback=None):
    sample_rate = 4000
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
    ]
    if start_time > 0:
        command.extend(["-ss", f"{start_time:.6f}"])
    if duration is not None:
        command.extend(["-t", f"{duration:.6f}"])
    command.extend([
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ])
    
    result = run_process_cancellable(command, cancelled_callback)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="ignore") or "تعذر تحليل الصمت")
        
    audio = np.frombuffer(result.stdout, dtype=np.int16)
    if audio.size == 0:
        return [(0, duration)] if duration else []
    audio = audio.astype(np.float32) / 32768.0
    frame_size = max(1, int(sample_rate * 0.01))
    frame_count = int(np.ceil(audio.size / frame_size))
    padded_size = frame_count * frame_size
    if padded_size > audio.size:
        audio = np.pad(audio, (0, padded_size - audio.size))
    frames = audio.reshape(frame_count, frame_size)
    levels = np.sqrt(np.mean(frames * frames, axis=1))
    threshold = 10 ** (threshold_db / 20.0)
    silent = levels <= threshold
    frame_duration = frame_size / sample_rate
    silence_ranges = []
    current_start = None
    for index, is_silent in enumerate(silent):
        time_position = index * frame_duration
        if is_silent and current_start is None:
            current_start = time_position
        elif not is_silent and current_start is not None:
            end_time = time_position
            if end_time - current_start >= minimum_silence:
                silence_ranges.append((current_start, end_time))
            current_start = None
    actual_duration = duration if duration is not None else audio.size / sample_rate
    if current_start is not None and actual_duration - current_start >= minimum_silence:
        silence_ranges.append((current_start, actual_duration))
    return [(max(0, start), min(actual_duration, end)) for start, end in silence_ranges if min(actual_duration, end) > max(0, start)]


def keep_intervals(duration, silence_ranges, padding):
    intervals = []
    position = 0
    for start, end in silence_ranges:
        keep_start = position
        keep_end = max(position, start + padding)
        if keep_end - keep_start > 0.03:
            intervals.append((keep_start, min(duration, keep_end)))
        position = max(position, end - padding)
    if duration - position > 0.03:
        intervals.append((position, duration))
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged if end - start > 0.03]


def selected_range(player):
    if player.start_time is not None and player.end_time is not None and player.start_time < player.end_time:
        return player.start_time, player.end_time
    return 0, player.timeline_duration()


def selected_file(timeline, start_time, end_time, temp_dir, progress_callback=None):
    selected_segments = slice_segments(timeline, start_time, end_time)
    audio_only = selected_segments and not has_video_stream(selected_segments[0].path)
    selected_path = os.path.join(temp_dir, "selected.wav" if audio_only else "selected.mp4")
    if progress_callback:
        progress_callback(5, "جاري تجهيز الجزء المحدد")
    if audio_only:
        write_timeline_audio(selected_segments, selected_path)
    else:
        write_timeline_video(selected_segments, selected_path)
    return selected_path


def single_source_selection(timeline, start_time, end_time):
    selected_segments = slice_segments(timeline, start_time, end_time)
    if len(selected_segments) == 1:
        return selected_segments[0]
    return None


def direct_segment_silence_analysis_safe(segment):
    if not segment:
        return False
    if str(getattr(segment, "audio_path", "") or ""):
        return False
    speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
    if abs(speed - 1.0) > 0.001:
        return False
    audio_volume = float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0)
    return abs(audio_volume - 1.0) <= 0.001


def threshold_for_segment_volume(threshold_db, volume):
    volume = float(volume if volume is not None else 1.0)
    if volume <= 0.001:
        return None
    return float(threshold_db) - (20.0 * math.log10(volume))


def analyze_timeline_segments_fast(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback=None, cancelled_callback=None):
    duration = max(0.0, float(end_time or 0.0) - float(start_time or 0.0))
    selected_segments = slice_segments(timeline, start_time, end_time)
    if not selected_segments:
        return [], duration, None
    silence_ranges = []
    offset = 0.0
    audio_stream_cache = {}
    count = len(selected_segments)
    for index, segment in enumerate(selected_segments):
        raise_if_cancelled(cancelled_callback)
        if progress_callback:
            progress_callback(8 + (index / max(1, count)) * 62, "جاري تحليل الصمت")
        speed = segment_speed(segment)
        output_duration = max(0.0, float(segment.duration or 0.0))
        if output_duration <= 0.01:
            offset += output_duration
            continue
        audio_path = segment_audio_path(segment)
        if not audio_path or not os.path.exists(audio_path):
            silence_ranges.append((offset, offset + output_duration))
            offset += output_duration
            continue
        audio_key = os.path.abspath(audio_path).lower()
        if audio_key not in audio_stream_cache:
            audio_stream_cache[audio_key] = has_audio_stream(audio_path)
        if not audio_stream_cache[audio_key]:
            silence_ranges.append((offset, offset + output_duration))
            offset += output_duration
            continue
        volume = segment_audio_volume(segment)
        segment_threshold = threshold_for_segment_volume(threshold_db, volume)
        if segment_threshold is None:
            silence_ranges.append((offset, offset + output_duration))
            offset += output_duration
            continue
        source_duration = max(0.0, float(segment.end) - float(segment.start))
        source_start = segment_audio_start(segment)
        source_minimum = max(0.001, float(minimum_silence or 0.001) * speed)
        
        for silence_start, silence_end in run_fast_silence_detect(audio_path, segment_threshold, source_minimum, source_start, source_duration, cancelled_callback):
            silence_ranges.append((offset + silence_start / speed, offset + silence_end / speed))
        offset += output_duration
    raise_if_cancelled(cancelled_callback)
    intervals = keep_intervals(duration, silence_ranges, padding)
    removed = duration - intervals_duration(intervals)
    if progress_callback:
        progress_callback(70, "تم تحليل الصمت")
    return intervals, removed, None


def selected_audio_file(timeline, start_time, end_time, temp_dir, progress_callback=None, cancelled_callback=None):
    selected_segments = slice_segments(timeline, start_time, end_time)
    selected_path = os.path.join(temp_dir, "selected.wav")
    if progress_callback:
        progress_callback(5, "جاري تجهيز صوت الجزء المحدد")
    write_timeline_audio(selected_segments, selected_path, cancelled_callback=cancelled_callback)
    return selected_path


def analyzed_intervals(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback=None, cancelled_callback=None):
    raise_if_cancelled(cancelled_callback)
    duration = end_time - start_time
    try:
        return analyze_timeline_segments_fast(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback, cancelled_callback)
    except Exception as error:
        if removal_cancelled(cancelled_callback):
            raise
        log_silence_removal("analysis.fast_fallback", error=str(error))
    temp_dir = tempfile.mkdtemp(prefix="remove_silence_")
    try:
        selected_path = selected_audio_file(timeline, start_time, end_time, temp_dir, progress_callback, cancelled_callback)
        raise_if_cancelled(cancelled_callback)
        if progress_callback:
            progress_callback(45, "جاري تحليل الصمت")
        silence_ranges = run_silence_detect(selected_path, threshold_db, minimum_silence, cancelled_callback=cancelled_callback)
        raise_if_cancelled(cancelled_callback)
        intervals = keep_intervals(duration, silence_ranges, padding)
        removed = duration - sum(end - start for start, end in intervals)
        if progress_callback:
            progress_callback(70, "تم تحليل الصمت")
        return intervals, removed, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def intervals_to_segments(timeline, selection_start, intervals):
    result = []
    for start, end in intervals:
        result.extend(slice_segments(timeline, selection_start + start, selection_start + end))
    return result


def preview_silence_removed(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback=None, cancelled_callback=None):
    intervals, removed, temp_dir = analyzed_intervals(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback, cancelled_callback)
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="remove_silence_preview_")
    if not intervals:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("لم يبق أي صوت بعد إزالة الصمت")
    segments = intervals_to_segments(timeline, start_time, intervals)
    preview_path = os.path.join(temp_dir, "preview.wav")
    if progress_callback:
        progress_callback(80, "جاري تجهيز المعاينة")
    write_timeline_audio(segments, preview_path, cancelled_callback=cancelled_callback)
    if progress_callback:
        progress_callback(100, "تم تجهيز المعاينة")
    return preview_path, temp_dir, removed


def preview_silence_plan(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback=None, cancelled_callback=None):
    intervals, removed, temp_dir = analyzed_intervals(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback, cancelled_callback)
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)
    if not intervals:
        raise RuntimeError("لم يبق أي صوت بعد إزالة الصمت")
    if progress_callback:
        progress_callback(100, "تم تجهيز التجربة")
    return intervals_to_segments(timeline, start_time, intervals), removed


def apply_silence_removed(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback=None, return_intervals=False, cancelled_callback=None):
    intervals, removed, temp_dir = analyzed_intervals(timeline, start_time, end_time, threshold_db, minimum_silence, padding, progress_callback, cancelled_callback)
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return apply_silence_intervals(timeline, start_time, end_time, intervals, removed, progress_callback, return_intervals)


def apply_silence_intervals(timeline, start_time, end_time, intervals, removed=None, progress_callback=None, return_intervals=False):
    if not intervals:
        raise RuntimeError("لم يبق أي صوت بعد إزالة الصمت")
    if removed is None:
        selected_duration = max(0.0, float(end_time or 0.0) - float(start_time or 0.0))
        removed = selected_duration - intervals_duration(intervals)
    segments = intervals_to_segments(timeline, start_time, intervals)
    remaining = delete_range(timeline, start_time, end_time)
    if progress_callback:
        progress_callback(90, "جاري تطبيق إزالة الصمت")
    updated = insert_segments(remaining, start_time, segments)
    if progress_callback:
        progress_callback(100, "تم تطبيق إزالة الصمت")
    if return_intervals:
        return updated, removed, intervals
    return updated, removed


class RemoveSilenceDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="إزالة الصمت", size=(640, 340))
        self.parent = parent
        self.busy = False
        self.preview_segments = []
        self.preview_path = ""
        self.preview_dir = ""
        self.preview_duration_seconds = 0.0
        self.live_preview_command_factory = None
        self.preview_plan_key = None
        self.preview_selection_start = 0.0
        self.preview_intervals = []
        self.preview_parts = []
        self.preview_removed = 0.0
        self.preview_position = 0
        self.preview_segment_index = None
        self.preview_play_requested = False
        self.pending_seek_ms = None
        self.pending_play = False
        self.active_media_path = ""
        self.preview_dirty = True
        self.tab_order = []
        self.last_focus_control = None
        self.preview_generation = 0
        self.prepare_requested_after_busy = False
        self.play_after_prepare = False
        self.apply_requested_after_busy = False
        self.applying = False
        self.closing = False
        self.cancel_requested = threading.Event()
        self.apply_progress_dialog = None
        self.last_spoken_apply_percent = -10
        self.preview_player = ReliableAudioPlayer()
        self.preview_player_available = reliable_audio_available()
        try:
            open_start, open_end = selected_range(parent)
        except Exception:
            open_start, open_end = 0, 0
        log_silence_removal(
            "dialog.open",
            reliable_audio_available=self.preview_player_available,
            selection_start=round(float(open_start or 0), 3),
            selection_end=round(float(open_end or 0), 3),
        )

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.threshold = wx.Slider(panel, value=DEFAULT_THRESHOLD_DB, minValue=-60, maxValue=-20, style=wx.SL_HORIZONTAL)
        self.minimum = wx.Slider(panel, value=DEFAULT_MINIMUM_SILENCE_MS, minValue=100, maxValue=3000, style=wx.SL_HORIZONTAL)
        self.padding = wx.Slider(panel, value=DEFAULT_PADDING_MS, minValue=0, maxValue=1000, style=wx.SL_HORIZONTAL)
        self.threshold.SetLineSize(1)
        self.threshold.SetPageSize(5)
        self.minimum.SetLineSize(50)
        self.minimum.SetPageSize(250)
        self.padding.SetLineSize(10)
        self.padding.SetPageSize(50)
        self.status = wx.StaticText(panel, label="جاهز")
        self.gauge = wx.Gauge(panel, range=100)
        self.preview = wx.StaticText(panel, label="")

        self.update_all_slider_names()
        self.status.SetName(tr("حالة إزالة الصمت"))
        self.gauge.SetName(tr("شريط تقدم إزالة الصمت"))
        self.gauge.SetCanFocus(False)
        self.preview.SetName(tr("معاينة إزالة الصمت"))

        self.add_slider_row(main_sizer, panel, "عتبة الصمت", self.threshold)
        self.add_slider_row(main_sizer, panel, "أقل مدة للصمت", self.minimum)
        self.add_slider_row(main_sizer, panel, "احتفاظ حول الكلام", self.padding)
        main_sizer.Add(self.status, flag=wx.EXPAND | wx.ALL, border=8)
        main_sizer.Add(self.gauge, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(self.preview, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        play_button = wx.Button(panel, label="تشغيل")
        rewind_button = wx.Button(panel, label="ترجيع")
        forward_button = wx.Button(panel, label="تقديم")
        pause_button = wx.Button(panel, label="إيقاف مؤقت")
        stop_button = wx.Button(panel, label="إيقاف")
        preview_button = wx.Button(panel, label="تجربة")
        apply_button = wx.Button(panel, label="تطبيق")
        cancel_button = wx.Button(panel, label="إلغاء")

        play_button.SetName(tr("تشغيل المعاينة"))
        rewind_button.SetName(tr("ترجيع المعاينة"))
        forward_button.SetName(tr("تقديم المعاينة"))
        pause_button.SetName(tr("إيقاف مؤقت للمعاينة"))
        stop_button.SetName(tr("إيقاف المعاينة"))
        for navigation_button in (play_button, rewind_button, forward_button, pause_button, stop_button):
            navigation_button.SetCanFocus(False)
        preview_button.SetName(tr("تجربة إزالة الصمت"))
        apply_button.SetName(tr("تطبيق إزالة الصمت"))
        cancel_button.SetName(tr("إلغاء"))
        apply_button.SetDefault()
        self.tab_order.extend([self.threshold, self.minimum, self.padding, preview_button, apply_button, cancel_button])

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for button in (play_button, rewind_button, forward_button, pause_button, stop_button, preview_button, apply_button, cancel_button):
            button_sizer.Add(button, flag=wx.ALL, border=4)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=6)

        panel.SetSizer(main_sizer)
        self.bind_slider(self.threshold)
        self.bind_slider(self.minimum)
        self.bind_slider(self.padding)
        play_button.Bind(wx.EVT_BUTTON, self.play_preview)
        rewind_button.Bind(wx.EVT_BUTTON, self.rewind_preview)
        forward_button.Bind(wx.EVT_BUTTON, self.forward_preview)
        pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        preview_button.Bind(wx.EVT_BUTTON, self.make_preview)
        apply_button.Bind(wx.EVT_BUTTON, self.apply_removal)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.close_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Slider,))
        self.prepare_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_prepare_timer, self.prepare_timer)

        self.Centre()
        self.last_focus_control = self.threshold
        wx.CallAfter(self.threshold.SetFocus)
        wx.CallAfter(self.schedule_preview_prepare, 20)

    def add_slider_row(self, main_sizer, panel, label_text, slider):
        row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(panel, label=label_text)
        row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        row.Add(slider, proportion=1, flag=wx.EXPAND)
        main_sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)

    def bind_slider(self, slider):
        slider.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        slider.Bind(wx.EVT_SLIDER, self.on_setting_changed)
        slider.Bind(wx.EVT_SCROLL_THUMBTRACK, self.on_setting_changed)
        slider.Bind(wx.EVT_SCROLL_CHANGED, self.on_setting_changed)
        slider.Bind(wx.EVT_SCROLL_LINEUP, self.on_setting_changed)
        slider.Bind(wx.EVT_SCROLL_LINEDOWN, self.on_setting_changed)
        slider.Bind(wx.EVT_KEY_DOWN, self.on_control_key_down)
        slider.Bind(wx.EVT_SET_FOCUS, self.on_slider_focus)

    def on_control_key_down(self, event):
        key = event.GetKeyCode()
        delta = self.slider_key_delta(key)
        if delta is not None:
            if self.adjust_focused_slider(delta, event):
                return
            event.Skip()
            return
        event.Skip()

    def on_slider_focus(self, event):
        self.last_focus_control = event.GetEventObject()
        event.Skip()

    def remember_focus(self):
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            self.last_focus_control = focused

    def restore_focus(self):
        if self.last_focus_control and self.last_focus_control in self.tab_order:
            self.last_focus_control.SetFocus()
            return
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            return
        self.threshold.SetFocus()

    def on_setting_changed(self, event=None):
        slider = self.slider_for_event(event)
        
        was_playing = bool(self.preview_play_requested)
        was_playing_or_scheduled = was_playing or getattr(self, "play_after_prepare", False)
        
        if was_playing:
            self.preview_position = max(0.0, self.preview_player.Tell() / 1000.0)
            self.stop_preview(announce=False, reset_position=False)
            
        self.preview_dirty = True
        self.preview_generation += 1
        self.prepare_timer.Stop()
        self.prepare_requested_after_busy = False
        if self.busy and not self.applying:
            self.prepare_requested_after_busy = True
            self.cancel_requested.set()
        self.update_progress(0, "تغيرت الإعدادات")
        if slider:
            self.update_status_text(self.slider_value_text(slider))
            
        self.play_after_prepare = was_playing_or_scheduled
        if not was_playing_or_scheduled:
            self.preview_position = 0
        self.schedule_preview_prepare(250)

    def slider_key_delta(self, key):
        if key in (wx.WXK_UP, wx.WXK_RIGHT, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_RIGHT):
            return 1
        if key in (wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_NUMPAD_DOWN, wx.WXK_NUMPAD_LEFT):
            return -1
        if key in (wx.WXK_PAGEUP, wx.WXK_NUMPAD_PAGEUP):
            return 10
        if key in (wx.WXK_PAGEDOWN, wx.WXK_NUMPAD_PAGEDOWN):
            return -10
        return None

    def slider_delta(self, slider, direction):
        sign = 1 if direction > 0 else -1
        page = abs(direction) >= 10
        if slider is self.threshold:
            return sign * (5 if page else 1)
        if slider is self.minimum:
            return sign * (250 if page else 50)
        if slider is self.padding:
            return sign * (50 if page else 10)
        return direction

    def slider_for_event(self, event=None):
        event_object = event.GetEventObject() if event else None
        current_target_getter = getattr(event, "GetCurrentTarget", None) if event else None
        current_target = current_target_getter() if current_target_getter else None
        focused = wx.Window.FindFocus()
        for slider in (self.threshold, self.minimum, self.padding):
            if event_object is slider or current_target is slider or focused is slider:
                return slider
        for slider in (self.threshold, self.minimum, self.padding):
            if self.last_focus_control is slider:
                return slider
        return None

    def adjust_focused_slider(self, direction, event=None):
        slider = self.slider_for_event(event)
        if not slider:
            return False
        value = max(slider.GetMin(), min(slider.GetMax(), slider.GetValue() + self.slider_delta(slider, direction)))
        if value == slider.GetValue():
            return True
        slider.SetValue(value)
        self.last_focus_control = slider
        self.on_setting_changed()
        self.update_status_text(self.slider_value_text(slider))
        self.speak_slider_value(slider)
        return True

    def move_focus_by_tab(self, backwards=False):
        if not self.tab_order:
            return False
        focused = wx.Window.FindFocus()
        try:
            index = self.tab_order.index(focused)
        except ValueError:
            index = -1 if not backwards else 0
        index = (index - 1) % len(self.tab_order) if backwards else (index + 1) % len(self.tab_order)
        self.last_focus_control = self.tab_order[index]
        self.tab_order[index].SetFocus()
        return True

    def options(self):
        return self.threshold.GetValue(), self.minimum.GetValue() / 1000, self.padding.GetValue() / 1000

    def current_plan_key(self):
        threshold, minimum, padding = self.options()
        start_time, end_time = selected_range(self.parent)
        return (
            round(float(start_time or 0.0), 3),
            round(float(end_time or 0.0), 3),
            int(threshold),
            round(float(minimum or 0.0), 3),
            round(float(padding or 0.0), 3),
        )

    def cached_preview_plan(self):
        try:
            key = self.current_plan_key()
        except Exception:
            return None
        if key == self.preview_plan_key and self.preview_intervals:
            return (
                key,
                float(self.preview_selection_start or 0.0),
                list(self.preview_intervals),
                [dict(part) for part in self.preview_parts],
                float(self.preview_removed or 0.0),
            )
        return None

    def slider_value_text(self, slider):
        if slider is self.threshold:
            return f"عتبة الصمت {slider.GetValue()} ديسيبل"
        if slider is self.minimum:
            return f"أقل مدة للصمت {slider.GetValue()} مللي ثانية"
        if slider is self.padding:
            return f"الاحتفاظ قبل وبعد الكلام {slider.GetValue()} مللي ثانية"
        return str(slider.GetValue())

    def slider_name_text(self, slider):
        if slider is self.threshold:
            return "عتبة الصمت بالديسيبل"
        if slider is self.minimum:
            return "أقل مدة للصمت بالمللي ثانية"
        if slider is self.padding:
            return "الاحتفاظ قبل وبعد الكلام بالمللي ثانية"
        return "شريط تمرير"

    def update_slider_name(self, slider):
        slider.SetName(self.slider_name_text(slider))

    def update_all_slider_names(self):
        for slider in (self.threshold, self.minimum, self.padding):
            self.update_slider_name(slider)

    def speak_slider_value(self, slider):
        speaker = getattr(self.parent, "say", None)
        if callable(speaker):
            try:
                speaker(self.slider_value_text(slider), interrupt=True, wait_for_ui=False)
                return
            except TypeError:
                pass
            except Exception:
                return
        self.speak_status(self.slider_value_text(slider))

    def update_progress(self, value, message):
        self.gauge.SetValue(max(0, min(100, int(value))))
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.gauge.SetName(message)
        self.notify_accessibility(self.status, wx.ACC_EVENT_OBJECT_NAMECHANGE)
        self.notify_accessibility(self.gauge, wx.ACC_EVENT_OBJECT_VALUECHANGE)

    def notify_accessibility(self, window, event_type=wx.ACC_EVENT_OBJECT_VALUECHANGE):
        if not wx.USE_ACCESSIBILITY:
            return
        try:
            wx.Accessible.NotifyEvent(event_type, window, wx.OBJID_CLIENT, wx.ACC_SELF)
        except Exception:
            pass

    def update_status_text(self, message):
        self.update_progress(self.gauge.GetValue(), message)

    def speak_status(self, message):
        speaker = getattr(self.parent, "say", None)
        if callable(speaker):
            try:
                speaker(message)
            except Exception:
                pass

    def cleanup_preview(self, announce=False):
        try:
            self.preview_player.Stop(wait=True)
        except Exception:
            pass
        self.preview_segments = []
        if self.preview_dir and os.path.exists(self.preview_dir):
            shutil.rmtree(self.preview_dir, ignore_errors=True)
        self.preview_path = ""
        self.preview_dir = ""
        self.preview_duration_seconds = 0.0
        self.live_preview_command_factory = None
        self.preview_plan_key = None
        self.preview_selection_start = 0.0
        self.preview_intervals = []
        self.preview_parts = []
        self.preview_removed = 0.0
        self.preview_position = 0
        self.preview_segment_index = None
        self.pending_seek_ms = None
        self.pending_play = False
        self.active_media_path = ""
        self.preview_play_requested = False

    def run_worker(self, worker):
        if self.busy:
            return False
        self.cancel_requested.clear()
        self.busy = True
        self.update_progress(0, "جاري العمل")
        threading.Thread(target=worker, daemon=True).start()
        return True

    def schedule_preview_prepare(self, delay=250):
        if self.closing:
            return
        if self.busy:
            self.cancel_requested.set()
            self.prepare_requested_after_busy = True
            return
        self.prepare_timer.StartOnce(delay)

    def on_prepare_timer(self, event):
        if self.preview_dirty:
            self.make_preview(auto_play=False, show_errors=False, stop_current=False)

    def make_preview(self, event=None, auto_play=True, show_errors=True, stop_current=True):
        log_silence_removal(
            "preview.request",
            auto_play=bool(auto_play),
            busy=bool(self.busy),
            dirty=bool(self.preview_dirty),
            has_preview=bool(self.preview_path),
        )
        if self.closing:
            return
        if self.busy:
            self.cancel_requested.set()
            self.prepare_requested_after_busy = True
            if auto_play:
                self.play_after_prepare = True
                self.speak_status("جاري تجهيز المعاينة")
            log_silence_removal("preview.request_queued", play_after_prepare=bool(self.play_after_prepare))
            return
        generation = self.preview_generation
        if stop_current:
            self.stop_preview()
        cached = self.cached_preview_plan()
        if cached:
            key, start_time, intervals, parts, removed = cached
            self.set_live_preview(start_time, intervals, parts, removed, auto_play, generation, key)
            return
        def worker():
            try:
                threshold, minimum, padding = self.options()
                start_time, end_time = selected_range(self.parent)
                key = self.current_plan_key()
                log_silence_removal(
                    "preview.worker_start",
                    generation=generation,
                    start=round(float(start_time or 0), 3),
                    end=round(float(end_time or 0), 3),
                    threshold=threshold,
                    minimum=round(float(minimum or 0), 3),
                    padding=round(float(padding or 0), 3),
                )
                intervals, removed, temp_dir = analyzed_intervals(
                    self.parent.timeline,
                    start_time,
                    end_time,
                    threshold,
                    minimum,
                    padding,
                    lambda value, message: wx.CallAfter(self.update_progress, value, message),
                    self.cancel_requested.is_set,
                )
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                parts = live_preview_audio_parts(self.parent.timeline, start_time, intervals)
                log_silence_removal(
                    "preview.worker_complete",
                    generation=generation,
                    intervals=len(intervals),
                    parts=len(parts),
                    removed=round(float(removed or 0), 3),
                )
                wx.CallAfter(self.set_live_preview, start_time, intervals, parts, removed, auto_play, generation, key)
            except Exception as error:
                if is_operation_cancelled(error, self.cancel_requested.is_set):
                    log_silence_removal("preview.worker_cancelled", generation=generation)
                    wx.CallAfter(self.update_progress, 0, "تم إلغاء تجهيز المعاينة القديمة")
                    return
                log_silence_removal("preview.worker_error", error=str(error))
                if show_errors and not self.closing:
                    wx.CallAfter(
                        show_error,
                        f"تعذر تجربة إزالة الصمت: {error}",
                        "خطأ",
                        self,
                        exception=error,
                        context="silence_removal_preview",
                    )
                wx.CallAfter(self.update_progress, 0, "تعذر تجهيز المعاينة")
            finally:
                self.busy = False
                if self.closing:
                    wx.CallAfter(self.close_dialog)
                elif self.apply_requested_after_busy:
                    self.apply_requested_after_busy = False
                    wx.CallAfter(self.apply_removal)
                elif self.prepare_requested_after_busy:
                    self.prepare_requested_after_busy = False
                    wx.CallAfter(self.schedule_preview_prepare, 10)
        self.run_worker(worker)

    def set_live_preview(self, selection_start, intervals, parts, removed, auto_play, generation=None, key=None):
        if generation is not None and generation != self.preview_generation:
            self.preview_dirty = True
            self.schedule_preview_prepare(100)
            log_silence_removal("preview.live_ignored_stale", generation=generation, current_generation=self.preview_generation)
            return
        if not intervals:
            self.preview_dirty = True
            self.update_progress(0, "لم يبق أي صوت بعد إزالة الصمت")
            return
        old_preview_dir = self.preview_dir
        intervals = list(intervals)
        parts = [dict(part) for part in parts]
        if not parts:
            self.preview_dirty = True
            self.update_progress(0, "لا يوجد صوت متاح في موضع المعاينة")
            return
        duration = intervals_duration(intervals)
        def command_factory(offset, limit, rate):
            return build_live_silence_preview_command_from_parts(
                parts,
                offset,
                limit,
                rate,
            )
        self.preview_segments = []
        self.preview_path = "live:silence_removal"
        self.preview_dir = ""
        self.live_preview_command_factory = command_factory
        self.preview_plan_key = key
        self.preview_selection_start = float(selection_start or 0.0)
        self.preview_intervals = intervals
        self.preview_parts = parts
        self.preview_removed = float(removed or 0.0)
        self.preview_duration_seconds = max(0.0, float(duration or 0.0))
        self.preview_position = min(max(0.0, float(self.preview_position or 0.0)), self.preview_duration_seconds)
        self.preview_segment_index = None
        self.pending_seek_ms = None
        self.pending_play = False
        self.active_media_path = ""
        self.preview_dirty = False
        if old_preview_dir:
            shutil.rmtree(old_preview_dir, ignore_errors=True)
        log_silence_removal(
            "preview.live_ready",
            duration=round(float(self.preview_duration_seconds or 0), 3),
            selection_start=round(float(selection_start or 0), 3),
            intervals=len(intervals),
            removed=round(float(removed or 0), 3),
            auto_play=bool(auto_play),
            play_after_prepare=bool(self.play_after_prepare),
        )
        self.update_progress(100, f"تم تجهيز المعاينة وإزالة {removed:.1f} ثانية من الصمت")
        if auto_play or self.play_after_prepare:
            self.play_after_prepare = False
            self.play_preview(announce=auto_play)
        wx.CallAfter(self.restore_focus)

    def set_preview_file(self, preview_path, preview_dir, removed, auto_play, generation=None):
        if self.closing:
            if preview_dir:
                shutil.rmtree(preview_dir, ignore_errors=True)
            log_silence_removal("preview.set_ignored_closing", path=preview_path)
            return
        if generation is not None and generation != self.preview_generation:
            if preview_dir:
                shutil.rmtree(preview_dir, ignore_errors=True)
            self.preview_dirty = True
            self.schedule_preview_prepare(100)
            log_silence_removal("preview.set_ignored_stale", generation=generation, current_generation=self.preview_generation)
            return
        old_preview_dir = self.preview_dir
        self.preview_segments = []
        self.preview_path = preview_path
        self.preview_dir = preview_dir or ""
        try:
            self.preview_duration_seconds = get_media_duration(preview_path)
        except Exception:
            self.preview_duration_seconds = max(0.0, selected_range(self.parent)[1] - selected_range(self.parent)[0] - removed)
        self.preview_position = min(max(0.0, float(self.preview_position or 0.0)), self.preview_duration_seconds)
        self.preview_segment_index = None
        self.pending_seek_ms = None
        self.pending_play = False
        self.active_media_path = ""
        self.preview_dirty = False
        log_silence_removal(
            "preview.ready",
            path=preview_path,
            duration=round(float(self.preview_duration_seconds or 0), 3),
            auto_play=bool(auto_play),
            play_after_prepare=bool(self.play_after_prepare),
        )
        self.update_progress(100, f"تم تجهيز التجربة وإزالة {removed:.1f} ثانية من الصمت")
        if old_preview_dir and old_preview_dir != self.preview_dir:
            shutil.rmtree(old_preview_dir, ignore_errors=True)
        if auto_play or self.play_after_prepare:
            self.play_after_prepare = False
            self.play_preview(announce=auto_play)
        wx.CallAfter(self.restore_focus)

    def preview_duration(self):
        return max(0.0, float(self.preview_duration_seconds or 0.0))

    def segment_position(self, index):
        return sum(segment.duration for segment in self.preview_segments[:index])

    def play_preview_file(self, offset=0, announce=True):
        if not self.preview_path:
            log_silence_removal("play.no_preview_path")
            return
        duration = self.preview_duration()
        self.preview_position = min(max(float(offset or 0), 0), duration)
        if duration <= 0:
            self.update_progress(self.gauge.GetValue(), "ملف المعاينة بلا مدة صوتية")
            if announce:
                self.speak_status("ملف المعاينة بلا مدة صوتية")
            log_silence_removal("play.invalid_duration", path=self.preview_path, duration=duration)
            return
        if not self.preview_player_available:
            self.update_progress(self.gauge.GetValue(), "مشغل الصوت الموثوق غير متاح")
            if announce:
                self.speak_status("مشغل الصوت غير متاح")
            log_silence_removal("play.player_unavailable")
            return
        volume = current_program_output_volume(self.parent)
        if volume <= 0.001:
            self.update_progress(self.gauge.GetValue(), "صوت البرنامج صفر، ارفع الصوت ثم جرب المعاينة")
            if announce:
                self.speak_status("صوت البرنامج صفر")
            log_silence_removal("play.volume_zero", volume=volume)
            return
        log_silence_removal(
            "play.start",
            path=self.preview_path,
            duration=round(float(duration or 0), 3),
            offset=round(float(self.preview_position or 0), 3),
            volume=round(float(volume or 0), 3),
        )
        if self.live_preview_command_factory:
            self.preview_player.ConfigureCommandFactory(
                self.live_preview_command_factory,
                seek_ms=int(self.preview_position * 1000),
                rate=1.0,
                volume=volume,
                duration=duration,
                block_frames=LIVE_PREVIEW_BLOCK_FRAMES,
                latency="high",
            )
        else:
            self.preview_player.Configure(
                self.preview_path,
                seek_ms=int(self.preview_position * 1000),
                rate=1.0,
                volume=volume,
                duration=duration,
            )
        if not self.preview_player.Play():
            error = getattr(self.preview_player, "last_error", "") or "تعذر تشغيل المعاينة"
            self.preview_play_requested = False
            self.update_progress(self.gauge.GetValue(), error)
            if announce:
                self.speak_status(error)
            log_silence_removal("play.failed", error=error)
            return
        self.preview_play_requested = True
        self.update_status_text("تشغيل المعاينة")
        if announce:
            self.speak_status("تشغيل المعاينة")
        log_silence_removal("play.started", state=self.preview_player.GetState())

    def show_apply_progress_dialog(self):
        if self.apply_progress_dialog:
            return
        self.last_spoken_apply_percent = -10
        self.apply_progress_dialog = SaveProgressDialog(
            self,
            self.cancel_apply_progress,
            title="جارٍ تطبيق إزالة الصمت",
            progress_template="نسبة تطبيق إزالة الصمت {percent} بالمئة",
            status_name="حالة تطبيق إزالة الصمت",
            gauge_name="شريط تقدم تطبيق إزالة الصمت",
            cancel_label="إلغاء",
            cancel_name="إلغاء تطبيق إزالة الصمت",
            cancelling_message="جاري إلغاء تطبيق إزالة الصمت",
        )
        self.apply_progress_dialog.update_progress(0)
        self.apply_progress_dialog.Show()
        self.speak_status("جارٍ تطبيق إزالة الصمت")
        self.update_apply_progress_dialog(0)

    def update_apply_progress_dialog(self, value):
        value = max(0, min(100, int(value)))
        if self.apply_progress_dialog:
            self.apply_progress_dialog.update_progress(value)
        if self.apply_progress_dialog and (value >= self.last_spoken_apply_percent + 10 or value >= 100):
            self.last_spoken_apply_percent = value
            message = f"نسبة تطبيق إزالة الصمت {value} بالمئة"
            self.speak_status(message)

    def update_apply_work_progress(self, value, message):
        self.update_progress(value, message)
        self.update_apply_progress_dialog(value)

    def cancel_apply_progress(self):
        self.cancel_requested.set()
        self.update_progress(self.gauge.GetValue(), "جاري إلغاء تطبيق إزالة الصمت")

    def destroy_apply_progress_dialog(self):
        if self.apply_progress_dialog:
            self.apply_progress_dialog.Destroy()
            self.apply_progress_dialog = None

    def apply_removal(self, event=None):
        if self.busy:
            if not self.applying:
                self.apply_requested_after_busy = True
                self.play_after_prepare = False
                self.update_progress(self.gauge.GetValue(), "سيتم تطبيق إزالة الصمت بعد انتهاء المعاينة الحالية")
            return
        self.apply_requested_after_busy = False
        self.applying = True
        self.stop_preview(announce=False)
        cached = self.cached_preview_plan()
        self.show_apply_progress_dialog()
        def worker():
            try:
                threshold, minimum, padding = self.options()
                if cached:
                    _key, _start_time, intervals, _parts, removed = cached
                else:
                    intervals = None
                    removed = None
                removed = self.parent.apply_remove_silence(
                    threshold,
                    minimum,
                    padding,
                    lambda value, message: wx.CallAfter(self.update_apply_work_progress, value, message),
                    self.cancel_requested.is_set,
                    intervals=intervals,
                    removed=removed,
                )
                wx.CallAfter(self.update_progress, 100, f"تم تطبيق إزالة الصمت وإزالة {removed:.1f} ثانية")
                wx.CallAfter(self.close_after_apply)
            except Exception as error:
                if is_operation_cancelled(error, self.cancel_requested.is_set):
                    log_silence_removal("apply.cancelled", error=str(error))
                    wx.CallAfter(self.update_progress, 0, "تم إلغاء تطبيق إزالة الصمت")
                    wx.CallAfter(self.speak_status, "تم إلغاء تطبيق إزالة الصمت")
                    wx.CallAfter(self.restore_focus)
                elif not self.closing:
                    wx.CallAfter(
                        show_error,
                        f"تعذر تطبيق إزالة الصمت: {error}",
                        "خطأ",
                        self,
                        exception=error,
                        context="silence_removal_apply",
                    )
                    wx.CallAfter(self.update_progress, 0, "تعذر تطبيق إزالة الصمت")
            finally:
                self.applying = False
                self.busy = False
                wx.CallAfter(self.destroy_apply_progress_dialog)
                if self.closing:
                    wx.CallAfter(self.close_dialog)
        self.run_worker(worker)

    def close_after_apply(self):
        self.cleanup_preview(False)
        self.finish_dialog(wx.ID_OK)

    def play_preview(self, event=None, announce=True):
        self.remember_focus()
        log_silence_removal(
            "play.request",
            has_preview=bool(self.preview_path),
            dirty=bool(self.preview_dirty),
            busy=bool(self.busy),
            play_requested=bool(self.preview_play_requested),
        )
        if not self.preview_path:
            self.make_preview(auto_play=True)
            return
        if self.preview_dirty:
            self.make_preview(auto_play=True)
            return
        self.preview_play_requested = True
        state = self.preview_player.GetState()
        current_offset = max(0.0, self.preview_player.Tell() / 1000.0)
        offset = current_offset if state in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED) else self.preview_position
        if offset >= self.preview_duration():
            offset = 0
        self.play_preview_file(offset, announce=announce)
        wx.CallAfter(self.restore_focus)

    def pause_preview(self, event=None):
        self.remember_focus()
        self.preview_position = max(0.0, self.preview_player.Tell() / 1000.0)
        self.preview_player.Pause()
        self.update_status_text("إيقاف مؤقت")
        self.preview_play_requested = False
        wx.CallAfter(self.restore_focus)

    def stop_preview(self, event=None, announce=True, reset_position=True):
        self.remember_focus()
        if not reset_position:
            self.preview_position = max(0.0, self.preview_player.Tell() / 1000.0)
        self.preview_player.Stop()
        self.preview_play_requested = False
        if reset_position:
            self.preview_position = 0
        self.pending_seek_ms = None
        self.pending_play = False
        if announce:
            self.update_status_text("إيقاف")
        wx.CallAfter(self.restore_focus)

    def rewind_preview(self, event=None):
        self.remember_focus()
        if not self.preview_path:
            self.make_preview(auto_play=False)
            return
        if self.preview_dirty and not self.busy:
            self.schedule_preview_prepare(10)
        self.preview_position = max(0.0, self.preview_player.Tell() / 1000.0)
        self.play_preview_file(max(0.0, self.preview_position - 5))
        wx.CallAfter(self.restore_focus)

    def forward_preview(self, event=None):
        self.remember_focus()
        if not self.preview_path:
            self.make_preview(auto_play=False)
            return
        if self.preview_dirty and not self.busy:
            self.schedule_preview_prepare(10)
        self.preview_position = max(0.0, self.preview_player.Tell() / 1000.0)
        self.play_preview_file(min(self.preview_duration(), self.preview_position + 5))
        wx.CallAfter(self.restore_focus)

    def close_dialog(self, event=None):
        log_silence_removal("dialog.close_request", busy=bool(self.busy), has_preview=bool(self.preview_path))
        if self.busy:
            self.closing = True
            self.cancel_requested.set()
            self.prepare_timer.Stop()
            self.cleanup_preview(False)
            self.Hide()
            self.update_progress(self.gauge.GetValue(), "انتظر حتى ينتهي العمل الحالي")
            return
        self.closing = True
        self.cancel_requested.set()
        self.prepare_timer.Stop()
        self.cleanup_preview(False)
        self.finish_dialog(wx.ID_CANCEL)

    def finish_dialog(self, result):
        if self.IsModal():
            self.EndModal(result)
        else:
            self.Destroy()

    def on_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_F4, wx.WXK_F5, wx.WXK_F6, wx.WXK_F7, wx.WXK_F8, wx.WXK_ESCAPE):
            log_silence_removal("key", key=key, busy=bool(self.busy), has_preview=bool(self.preview_path))
        if key == wx.WXK_TAB and self.move_focus_by_tab(event.ShiftDown()):
            return
        delta = self.slider_key_delta(key)
        if delta is not None and self.adjust_focused_slider(delta, event):
            return
        if key == wx.WXK_ESCAPE:
            self.close_dialog()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            focus = wx.Window.FindFocus()
            if isinstance(focus, wx.Button):
                event.Skip()
                return
            self.apply_removal()
            return
        if key == wx.WXK_F4:
            self.play_preview()
            return
        if key == wx.WXK_F5:
            self.rewind_preview()
            return
        if key == wx.WXK_F6:
            self.forward_preview()
            return
        if key == wx.WXK_F7:
            self.pause_preview()
            return
        if key == wx.WXK_F8:
            self.stop_preview()
            return
        event.Skip()
