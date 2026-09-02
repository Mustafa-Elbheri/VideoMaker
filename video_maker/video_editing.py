import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
import json

from video_maker.app_paths import ffmpeg_binary, ffprobe_binary
from video_maker.media_cache import cached_media_info, invalidate_media_cache

from video_maker.operation_control import OperationCancelled
from video_maker.save_options import AUDIO_FORMATS, VIDEO_FORMATS, audio_format_settings, format_by_key
from video_maker.timeline import slice_segments
from video_maker.tracks import (
    BACKGROUND_AUDIO_TRACK,
    MAIN_VIDEO_TRACK,
    SECONDARY_VIDEO_TRACK,
    SOUND_EFFECTS_TRACK,
    TEXT_TRACK,
)
from video_maker.transition_effects import all_transition_effects
from video_maker.volume_boost import export_master_multiplier_from_options, export_volume_multiplier_from_options




FFMPEG_LOW_MEMORY_INPUT_OPTIONS = ("-threads", "1", "-fflags", "+discardcorrupt", "-err_detect", "ignore_err")
FFMPEG_LOW_MEMORY_FILTER_OPTIONS = ("-filter_threads", "1", "-filter_complex_threads", "1")


def append_ffmpeg_input(command, path, ss=None, duration=None, loop=False, low_memory=True, audio_loop=False):
    if ss is not None:
        command.extend(["-ss", f"{max(0.0, float(ss)):.6f}"])
    if audio_loop:
        command.extend(["-stream_loop", "-1"])
    elif loop:
        command.extend(["-loop", "1"])
    if duration is not None:
        command.extend(["-t", f"{max(0.001, float(duration)):.6f}"])
    if low_memory and not loop and not audio_loop:
        command.extend(FFMPEG_LOW_MEMORY_INPUT_OPTIONS)
    command.extend(["-i", str(path)])


def copy_file_with_progress(source_path, save_path, progress_callback=None, cancelled_callback=None):
    total_size = os.path.getsize(source_path)
    copied_size = 0
    if progress_callback:
        progress_callback(1)
    with open(source_path, "rb") as source_file, open(save_path, "wb") as output_file:
        while True:
            if cancelled_callback and cancelled_callback():
                raise OperationCancelled()
            chunk = source_file.read(1024 * 1024)
            if not chunk:
                break
            output_file.write(chunk)
            copied_size += len(chunk)
            if progress_callback and total_size:
                progress_callback(min(99, int(copied_size / total_size * 100)))
    shutil.copystat(source_path, save_path)
    if progress_callback:
        progress_callback(100)


def normalized_speed(value):
    return max(0.05, float(value or 1.0))


def segment_speed(segment):
    return normalized_speed(getattr(segment, "speed", 1.0))


def segment_audio_volume(segment):
    value = getattr(segment, "audio_volume", 1.0)
    if value is None:
        value = 1.0
    return max(0.0, min(1.0, float(value)))


def segment_audio_fade_in(segment):
    return max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0))


def segment_audio_fade_out(segment):
    return max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0))


def segment_audio_path(segment):
    return str(getattr(segment, "audio_path", "") or segment.path)


def segment_audio_start(segment):
    audio_start = getattr(segment, "audio_start", None)
    if audio_start is None:
        return float(segment.start)
    return max(0.0, float(audio_start))


def exact_timeline_audio_chain(input_label, duration, output_duration, speed=1.0, volume=1.0, fade_in=0.0, fade_out=0.0, start_time=None):
    duration = max(0.001, float(duration or 0.001))
    output_duration = max(0.001, float(output_duration or 0.001))
    speed = normalized_speed(speed)
    volume = max(0.0, min(1.0, float(volume if volume is not None else 1.0)))
    fade_in = min(max(0.0, float(fade_in or 0.0)), output_duration / 2.0)
    fade_out = min(max(0.0, float(fade_out or 0.0)), output_duration / 2.0)
    if start_time is not None:
        atrim_expr = f"start={max(0.0, float(start_time)):.6f}:duration={duration:.6f}"
    else:
        atrim_expr = f"duration={duration:.6f}"
    chain = [
        f"{input_label}atrim={atrim_expr}",
        "asetpts=PTS-STARTPTS",
    ]
    if abs(speed - 1.0) > 0.001:
        try:
            from video_maker.reliable_playback import atempo_filter
            chain.append(atempo_filter(speed))
        except ImportError:
            chain.append(f"atempo={speed:.6f}")
    if abs(volume - 1.0) > 0.001:
        chain.append(f"volume={volume:.6f}")
    if fade_in > 0.0005:
        chain.append(f"afade=t=in:st=0:d={fade_in:.6f}")
    if fade_out > 0.0005:
        chain.append(f"afade=t=out:st={max(0.0, output_duration - fade_out):.6f}:d={fade_out:.6f}")
    chain.extend([
        "apad",
        f"atrim=duration={output_duration:.6f}",
        "asetpts=N/SR/TB",
        "aresample=48000",
    ])
    return ",".join(chain)


def exact_timeline_video_chain(input_label, output_duration, tail_pad=1.0):
    output_duration = max(0.001, float(output_duration or 0.001))
    tail_pad = max(0.001, float(tail_pad or 0.001))
    return (
        f"{input_label}tpad=stop_mode=clone:stop_duration={tail_pad:.6f},"
        f"trim=duration={output_duration:.6f},setpts=PTS-STARTPTS"
    )


def ffmpeg_progress_seconds(key, value):
    key = str(key or "").strip()
    value = str(value or "").strip()
    if key in ("out_time_us", "out_time_ms"):
        try:
            return max(0.0, int(value) / 1_000_000.0)
        except ValueError:
            return None
    if key in ("out_time", "time"):
        if not value or value.upper() == "N/A":
            return None
        match = re.match(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$", value)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            seconds = float(match.group(3))
            return max(0.0, hours * 3600 + minutes * 60 + seconds)
        try:
            return max(0.0, float(value))
        except ValueError:
            return None
    return None


BOUNDARY_SAFE_AUDIO_CODECS = {
    "flac",
    "alac",
    "wavpack",
    "tta",
    "pcm_s8",
    "pcm_u8",
    "pcm_s16le",
    "pcm_s16be",
    "pcm_u16le",
    "pcm_u16be",
    "pcm_s24le",
    "pcm_s24be",
    "pcm_u24le",
    "pcm_u24be",
    "pcm_s32le",
    "pcm_s32be",
    "pcm_u32le",
    "pcm_u32be",
    "pcm_f32le",
    "pcm_f32be",
    "pcm_f64le",
    "pcm_f64be",
}


def prepare_boundary_safe_audio_proxy(source_path, progress_callback=None, cancelled_callback=None):
    """Create a lossless seek-safe audio proxy for cuts around visual effects.

    Lossy codecs such as MP3, AAC, Opus, and AC-3 use coded frames and may have
    decoder delay or pre-skip.  Starting independent decoders at an arbitrary
    timeline cut can soften samples next to that cut.  The proxy is generated
    only for codecs that need it; PCM and recognised lossless codecs continue
    through the original path unchanged.
    """
    if not source_path or not has_audio_stream(source_path):
        return "", ""
    if cancelled_callback and cancelled_callback():
        raise OperationCancelled()
    try:
        audio_codec = str(parse_media_signature(source_path).get("audio_codec", "") or "").lower()
    except Exception:
        audio_codec = ""
    if audio_codec in BOUNDARY_SAFE_AUDIO_CODECS:
        if progress_callback:
            progress_callback(100)
        return "", ""
    temp_dir = tempfile.mkdtemp(prefix="visual_effect_audio_proxy_")
    proxy_path = os.path.join(temp_dir, "audio.flac")
    try:
        source_duration = max(0.001, float(get_media_duration(source_path) or 0.001))
    except Exception:
        source_duration = 0.001
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        source_path,
        "-map",
        "0:a:0",
        "-vn",
        "-c:a",
        "flac",
        "-compression_level",
        "0",
        proxy_path,
    ]
    stderr_file = tempfile.TemporaryFile(mode="w+b")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="ignore",
        startupinfo=ffmpeg_startupinfo(),
    )
    if progress_callback:
        progress_callback(0)
    last_percent = -1
    stderr_text = ""
    try:
        if process.stdout:
            for line in process.stdout:
                if cancelled_callback and cancelled_callback():
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    raise OperationCancelled()
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                seconds = ffmpeg_progress_seconds(key, value)
                if seconds is None:
                    continue
                percent = max(0, min(99, int(seconds * 100 / source_duration)))
                if progress_callback and percent != last_percent:
                    last_percent = percent
                    progress_callback(percent)
        return_code = process.wait() if process.poll() is None else process.poll()
        stderr_file.seek(0)
        stderr_text = stderr_file.read().decode("utf-8", errors="ignore")
    finally:
        if process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            if process.stdout:
                process.stdout.close()
        except Exception:
            pass
        stderr_file.close()
    if return_code != 0 or not os.path.exists(proxy_path) or os.path.getsize(proxy_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError((stderr_text or "").strip() or "تعذر تجهيز صوت دقيق للمؤثر المرئي")
    if progress_callback:
        progress_callback(100)
    return proxy_path, temp_dir


def prepare_timeline_boundary_safe_audio_proxies(timeline, progress_callback=None, cancelled_callback=None, extra_paths=None, include_timeline_segments=True):
    proxy_paths = {}
    proxy_dirs = []
    source_paths = []
    seen = set()
    if include_timeline_segments:
        for segment in timeline or []:
            explicit_audio_path = str(getattr(segment, "audio_path", "") or "")
            if explicit_audio_path:
                continue
            source_path = str(getattr(segment, "path", "") or "")
            if not source_path:
                continue
            source_key = os.path.abspath(source_path)
            if source_key in seen:
                continue
            seen.add(source_key)
            source_paths.append((source_key, source_path))
    for extra_path in extra_paths or []:
        source_path = str(extra_path or "")
        if not source_path:
            continue
        source_key = os.path.abspath(source_path)
        if source_key in seen:
            continue
        seen.add(source_key)
        source_paths.append((source_key, source_path))
    if progress_callback:
        progress_callback(0)
    total = max(1, len(source_paths))
    for index, (source_key, source_path) in enumerate(source_paths):
        if cancelled_callback and cancelled_callback():
            raise OperationCancelled()
        base = index / total * 100.0
        span = 100.0 / total
        proxy_path, proxy_dir = prepare_boundary_safe_audio_proxy(
            source_path,
            progress_callback=(lambda value, base=base, span=span: progress_callback(base + max(0.0, min(100.0, float(value))) * span / 100.0)) if progress_callback else None,
            cancelled_callback=cancelled_callback,
        )
        if proxy_path:
            proxy_paths[source_key] = proxy_path
        if proxy_dir:
            proxy_dirs.append(proxy_dir)
    if progress_callback:
        progress_callback(100)
    return proxy_paths, proxy_dirs





def timeline_needs_render(segment):
    return (
        bool(getattr(segment, "audio_path", ""))
        or abs(segment_speed(segment) - 1.0) > 0.001
        or abs(segment_audio_volume(segment) - 1.0) > 0.001
        or segment_audio_fade_in(segment) > 0.001
        or segment_audio_fade_out(segment) > 0.001
    )




def get_video_duration(video_path):
    try:
        return float(ffmpeg_parse_infos(video_path).get('duration', 0.0))
    except Exception:
        return 0.0

def get_audio_duration(audio_path):
    try:
        return float(ffmpeg_parse_infos(audio_path).get('duration', 0.0))
    except Exception:
        return 0.0


def get_media_duration(path):
    cached = cached_media_info(path)
    if cached is not None:
        return cached.get("duration", 0.0)
    if has_video_stream(path):
        return get_video_duration(path)
    return get_audio_duration(path)


AUDIO_ONLY_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".ac3", ".amr", ".ape", ".mka",
}


def _is_attached_picture_stream(line):
    """Return True for album artwork streams reported by FFmpeg as video."""
    normalized = str(line or "").lower().replace("_", " ")
    return "attached pic" in normalized


def has_video_stream(path):
    """Return whether *path* contains playable moving video.

    Audio files can contain embedded cover artwork. FFmpeg reports that artwork
    as a video stream marked ``attached_pic``; it must not turn an audio project
    into a video project or expose video save options. Known audio containers
    are also kept audio-only even when metadata artwork is malformed and lacks
    the disposition marker.
    """
    cached = cached_media_info(path)
    if cached is not None:
        return cached.get("has_video", False)
    output = media_info_text(path)
    video_lines = [line for line in output.splitlines() if " Video: " in line]
    if not video_lines:
        return False
    extension = os.path.splitext(path or "")[1].lower()
    if extension in AUDIO_ONLY_EXTENSIONS:
        return False
    return any(not _is_attached_picture_stream(line) for line in video_lines)


def has_audio_stream(path):
    cached = cached_media_info(path)
    if cached is not None:
        return cached.get("has_audio", False)
    output = media_info_text(path)
    return any(" Audio: " in line for line in output.splitlines())


def timed_items_have_audio(items):
    for item in items or []:
        path = str(item.get("path", "") or "")
        if not path or not os.path.exists(path):
            continue
        try:
            if has_audio_stream(path):
                return True
        except Exception:
            continue
    return False


def parse_bitrates_from_ffmpeg(video_path):
    command = [ffmpeg_binary(), "-i", video_path]
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=startupinfo)
    output = process.stderr.decode("utf-8", errors="ignore")
    video_lines = [line for line in output.splitlines() if " Video: " in line]
    audio_lines = [line for line in output.splitlines() if " Audio: " in line]
    video_bitrates = []
    audio_bitrates = []
    for line in video_lines:
        match = re.search(r"(\d+)\s+kb/s", line)
        if match:
            video_bitrates.append(int(match.group(1)))
    for line in audio_lines:
        match = re.search(r"(\d+)\s+kb/s", line)
        if match:
            audio_bitrates.append(int(match.group(1)))
    container_match = re.search(r"bitrate:\s+(\d+)\s+kb/s", output)
    container_bitrate = int(container_match.group(1)) if container_match else None
    return (
        max(video_bitrates) if video_bitrates else container_bitrate,
        max(audio_bitrates) if audio_bitrates else None,
    )


def ffmpeg_startupinfo():
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def media_info_text(video_path):
    cached = cached_media_info(video_path)
    if cached is not None and cached.get("info_text") is not None:
        return cached["info_text"]
    command = [ffmpeg_binary(), "-i", video_path]
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    return process.stderr.decode("utf-8", errors="ignore")


def first_codec(line):
    match = re.search(r":\s*([^,\s]+)", line)
    return match.group(1).lower() if match else ""


def parse_media_signature(video_path):
    output = media_info_text(video_path)
    video_line = next((line for line in output.splitlines() if " Video: " in line), "")
    audio_line = next((line for line in output.splitlines() if " Audio: " in line), "")
    size_match = re.search(r"(\d+)x(\d+)", video_line)
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video_line)
    sample_match = re.search(r"(\d+)\s*Hz", audio_line)
    channels = "stereo" if "stereo" in audio_line.lower() else "mono" if "mono" in audio_line.lower() else ""
    return {
        "video_codec": first_codec(video_line),
        "audio_codec": first_codec(audio_line) if audio_line else "",
        "size": size_match.group(0) if size_match else "",
        "fps": fps_match.group(1) if fps_match else "",
        "sample_rate": sample_match.group(1) if sample_match else "",
        "channels": channels,
        "extension": os.path.splitext(video_path)[1].lower(),
    }


def is_full_segment(segment):
    if timeline_needs_render(segment):
        return False
    if segment.start > 0.05:
        return False
    duration = get_video_duration(segment.path)
    return abs(segment.end - duration) <= 0.05


def can_stream_copy_concat(timeline):
    if len(timeline) < 2:
        return False
    signatures = []
    for segment in timeline:
        if timeline_needs_render(segment):
            return False
        if not is_full_segment(segment):
            return False
        signatures.append(parse_media_signature(segment.path))
    first = signatures[0]
    required_keys = ("video_codec", "audio_codec", "size", "fps", "sample_rate", "channels", "extension")
    if not first["video_codec"] or not first["size"] or not first["fps"]:
        return False
    return all(all(signature.get(key) == first.get(key) for key in required_keys) for signature in signatures[1:])


def concat_list_line(path):
    normalized = path.replace("\\", "/")
    return "file '{}'\n".format(normalized.replace("'", "'\\''"))


def run_concat_copy(timeline, save_path, progress_callback=None, cancelled_callback=None):
    output_dir = os.path.dirname(os.path.abspath(save_path)) or os.getcwd()
    handle, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_", dir=output_dir, text=True)
    temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(save_path)[1] or ".mp4", prefix="saving_", dir=output_dir).name
    os.close(handle)
    total = sum(segment.duration for segment in timeline)
    try:
        with open(list_path, "w", encoding="utf-8") as list_file:
            for segment in timeline:
                list_file.write(concat_list_line(os.path.abspath(segment.path)))
        command = [
            ffmpeg_binary(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            temp_output,
        ]
        if progress_callback:
            progress_callback(1)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, startupinfo=ffmpeg_startupinfo())
        for line in process.stdout:
            if cancelled_callback and cancelled_callback():
                process.terminate()
                raise OperationCancelled()
            key, separator, value = line.strip().partition("=")
            if separator and total:
                seconds = ffmpeg_progress_seconds(key, value)
                if seconds is None:
                    continue
                percent = min(99, max(1, int(seconds / total * 100)))
                if progress_callback:
                    progress_callback(percent)
        result = process.wait()
        if result != 0:
            return False
        if os.path.abspath(temp_output) != os.path.abspath(save_path):
            shutil.move(temp_output, save_path)
        if progress_callback:
            progress_callback(100)
        return True
    finally:
        for path in (list_path, temp_output):
            if os.path.exists(path) and os.path.abspath(path) != os.path.abspath(save_path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def absolute_output_path(path):
    return os.path.abspath(path)


def apply_metadata(save_path, metadata):
    metadata = {key: value for key, value in (metadata or {}).items() if value}
    if not metadata:
        return
    output_dir = os.path.dirname(os.path.abspath(save_path)) or os.getcwd()
    temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(save_path)[1] or ".mp4", prefix="metadata_", dir=output_dir).name
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        save_path,
        "-map",
        "0",
        "-c",
        "copy",
    ]
    if os.path.splitext(save_path)[1].lower() in (".mp4", ".m4a", ".m4v", ".mov"):
        command.extend(["-movflags", "+faststart"])
    for key, value in metadata.items():
        command.extend(["-metadata", f"{key}={value}"])
    command.append(temp_output)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0 or not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass
        message = result.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(message or "تعذر حفظ معلومات الملف")
    os.replace(temp_output, save_path)


def preferred_audio_bitrate_for_codec(audio_codec, source_audio_bitrate=None):
    codec = str(audio_codec or "").lower()
    high_quality_defaults = {
        "libopus": 256,
        "libmp3lame": 320,
        "wmav2": 320,
        "mp2": 384,
        "aac": 320,
    }
    audio_limits = {
        "libopus": (64, 256),
        "libmp3lame": (64, 320),
        "wmav2": (64, 320),
        "mp2": (64, 384),
        "aac": (64, 320),
    }
    minimum, maximum = audio_limits.get(codec, (64, 320))
    baseline = high_quality_defaults.get(codec, maximum)
    try:
        requested = int(source_audio_bitrate or 0)
    except (TypeError, ValueError):
        requested = 0
    return max(minimum, min(maximum, max(baseline, requested)))


def audio_bitrate_from_paths(paths):
    audio_bitrates = []
    seen = set()
    for path in paths or []:
        path = str(path or "")
        if not path:
            continue
        key = os.path.abspath(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            _video_bitrate, audio_bitrate = parse_bitrates_from_ffmpeg(path)
        except Exception:
            audio_bitrate = None
        if audio_bitrate:
            audio_bitrates.append(audio_bitrate)
    return max(audio_bitrates) if audio_bitrates else None


def video_output_settings(save_path, save_options, source_bitrate, source_audio_bitrate):
    extension = os.path.splitext(save_path)[1].lower()
    format_key = (save_options or {}).get("format")
    if not format_key:
        format_key = next((item["key"] for item in VIDEO_FORMATS if item["extension"] == extension), "mp4")
    output_format = format_by_key(VIDEO_FORMATS, format_key)
    video_codec = (save_options or {}).get("video_codec") or output_format["video_codec"]
    audio_codec = (save_options or {}).get("audio_codec") or output_format["audio_codec"]
    quality_key = (save_options or {}).get("video_quality", "original")
    requested_bitrate = (save_options or {}).get("video_bitrate")
    if quality_key == "original":
        video_bitrate = int(source_bitrate or 8000)
    else:
        video_bitrate = int(requested_bitrate or 4000)
    explicit_audio_bitrate = (save_options or {}).get("audio_bitrate")
    if explicit_audio_bitrate:
        audio_bitrate = explicit_audio_bitrate
    else:
        audio_bitrate = f"{preferred_audio_bitrate_for_codec(audio_codec, source_audio_bitrate)}k"
    ffmpeg_params = []
    preset = "medium"
    if video_codec == "libx264":
        ffmpeg_params.extend(["-pix_fmt", "yuv420p"])
        if extension in (".mp4", ".m4v", ".mov"):
            ffmpeg_params.extend(["-movflags", "+faststart"])
    elif video_codec == "libvpx-vp9":
        preset = "good"
        ffmpeg_params.extend(["-crf", "30", "-row-mt", "1"])
    elif video_codec == "mpeg4":
        ffmpeg_params.extend(["-pix_fmt", "yuv420p"])
    elif video_codec in ("wmv2", "mpeg2video"):
        ffmpeg_params.extend(["-pix_fmt", "yuv420p"])
    return {
        "codec": video_codec,
        "audio_codec": audio_codec,
        "preset": preset,
        "bitrate": f"{video_bitrate}k",
        "audio_bitrate": str(audio_bitrate),
        "ffmpeg_params": ffmpeg_params,
    }


def get_video_quality_settings(timeline):
    sizes = []
    frame_rates = []
    bitrates = []
    audio_bitrates = []

    for segment in timeline:
        try:
            infos = ffmpeg_parse_infos(segment.path)
            if infos.get("video_size"):
                sizes.append(tuple(infos["video_size"]))
            if infos.get("video_fps"):
                frame_rates.append(float(infos["video_fps"]))
            bitrate, audio_bitrate = parse_bitrates_from_ffmpeg(segment.path)
            if bitrate:
                bitrates.append(bitrate)
            if audio_bitrate:
                audio_bitrates.append(audio_bitrate)
            audio_path = segment_audio_path(segment)
            if audio_path and os.path.abspath(audio_path) != os.path.abspath(segment.path):
                _audio_video_bitrate, explicit_audio_bitrate = parse_bitrates_from_ffmpeg(audio_path)
                if explicit_audio_bitrate:
                    audio_bitrates.append(explicit_audio_bitrate)
        except Exception:
            continue

    target_size = max(sizes, key=lambda size: size[0] * size[1]) if sizes else None
    fps = max(frame_rates) if frame_rates else 30
    bitrate = max(bitrates) if bitrates else None
    audio_bitrate = max(audio_bitrates) if audio_bitrates else None
    return target_size, fps, bitrate, audio_bitrate






def requested_output_size(save_options):
    if not save_options:
        return None
    size = save_options.get("size")
    if not size or len(size) != 2:
        return None
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        return None
    return width + width % 2, height + height % 2


def audio_codec_for_extension(extension):
    extension = extension.lower()
    if extension == ".mp3":
        return "libmp3lame"
    if extension in (".m4a", ".aac"):
        return "aac"
    if extension == ".ogg":
        return "libvorbis"
    if extension == ".opus":
        return "libopus"
    if extension == ".flac":
        return "flac"
    if extension == ".wav":
        return "pcm_s16le"
    if extension in (".aiff", ".aif"):
        return "pcm_s16be"
    if extension == ".wma":
        return "wmav2"
    return "aac"


def fast_write_timeline_audio(timeline, save_path, progress_callback=None, cancelled_callback=None):
    if cancelled_callback and cancelled_callback():
        raise OperationCancelled()
    
    command = [ffmpeg_binary(), '-hide_banner', '-loglevel', 'error', '-stats', '-y']
    path_to_index = {}
    input_index = 0
    for segment in timeline:
        if cancelled_callback and cancelled_callback():
            raise OperationCancelled()
        path = segment_audio_path(segment)
        if not path or not os.path.exists(path):
            continue
        if path not in path_to_index:
            path_to_index[path] = input_index
            command.extend(["-i", path])
            input_index += 1
            
    if not path_to_index:
        raise RuntimeError("لا يوجد صوت صالح لتصديره")
        
    labels = []
    filters = []
    for index, segment in enumerate(timeline):
        if cancelled_callback and cancelled_callback():
            raise OperationCancelled()
            
        path = segment_audio_path(segment)
        if not path or path not in path_to_index:
            duration = max(0.05, float(segment.end) - float(segment.start))
            filters.append(f"anullsrc=r=48000:cl=stereo:d={duration:.6f}[a{index}]")
            labels.append(f"[a{index}]")
            continue
            
        file_input_index = path_to_index[path]
        start = segment_audio_start(segment)
        duration = max(0.0, float(segment.end) - float(segment.start))
        end = start + duration
        chain = [f"[{file_input_index}:a]atrim=start={start:.6f}:end={end:.6f}"]
        speed = segment_speed(segment)
        volume = segment_audio_volume(segment)
        output_duration = duration / speed
        filters.append(
            ",".join(chain)
            + ",asetpts=PTS-STARTPTS,"
            + exact_timeline_audio_chain(
                "",
                duration,
                output_duration,
                speed,
                volume,
                segment_audio_fade_in(segment),
                segment_audio_fade_out(segment),
            )
            + f"[a{index}]"
        )
        labels.append(f"[a{index}]")
        
    if len(labels) == 1:
        joined_label = "joined"
        filters.append(f"{labels[0]}anull[{joined_label}]")
    else:
        joined_label = "joined"
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[{joined_label}]")
        
    extension = os.path.splitext(save_path)[1].lower()
    codec = audio_codec_for_extension(extension)
    
    import tempfile
    fd, script_path = tempfile.mkstemp(suffix=".txt", prefix="filter_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(";".join(filters))
        
    command.extend([
        "-filter_complex_script", script_path,
        "-map", f"[{joined_label}]",
        "-c:a", codec,
    ])
    if codec not in ("pcm_s16le", "flac"):
        command.extend(["-b:a", "320k"])
    command.append(save_path)
    
    if progress_callback:
        progress_callback(5)
    try:
        import subprocess
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
        total_duration = max(0.01, sum(max(0.0, float(seg.end) - float(seg.start)) for seg in timeline))
        error_lines = []
        last_percent = -1
        
        import re
        buffer = ""
        
        while True:
            if cancelled_callback and cancelled_callback():
                try:
                    process.terminate()
                except Exception:
                    pass
                raise OperationCancelled()
                
            chunk = process.stderr.read1(4096) if hasattr(process.stderr, 'read1') else process.stderr.read(4096)
            if not chunk:
                break
                
            text_chunk = chunk.decode('utf-8', errors='ignore')
            buffer += text_chunk
            error_lines.append(text_chunk)
            
            if "time=" in buffer and progress_callback:
                matches = re.findall(r"time=(\d+):(\d+):([\d\.]+)", buffer)
                if matches:
                    match = matches[-1]
                    h, m, s = float(match[0]), float(match[1]), float(match[2])
                    current_time = h * 3600 + m * 60 + s
                    percent = 5 + int((current_time / total_duration) * 90)
                    if percent != last_percent:
                        progress_callback(min(99, percent))
                        last_percent = percent
                buffer = buffer[-1024:]
                
        process.wait()
        if process.returncode != 0:
            message = "".join(error_lines)
            raise RuntimeError(message or "تعذر دمج وحفظ الصوت")
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass
            
    if progress_callback:
        progress_callback(100)


def write_timeline_audio(timeline, save_path, progress_callback=None, cancelled_callback=None, metadata=None, background_audio_items=None, save_options=None, sound_effects_items=None, muted_tracks=None, solo_tracks=None):
    write_timeline_video(timeline=timeline, save_path=save_path, progress_callback=progress_callback, cancelled_callback=cancelled_callback, metadata=metadata, save_options=save_options, background_audio_items=background_audio_items, sound_effects_items=sound_effects_items, muted_tracks=muted_tracks, solo_tracks=solo_tracks, audio_only=True)


def xfade_transition_keys():
    return {effect["key"] for effect in all_transition_effects()}


def segment_has_valid_transition(segment):
    return str(getattr(segment, "transition", "") or "") in xfade_transition_keys()


def timeline_boundary_transitions(timeline):
    """انتقالات حدود مقاطع الخط الزمني.

    الانتقال الموجود على المقطع index يطبق على الحد الفاصل بين index-1 و index.
    تُقصّ المدة إلى نصف مدة كل مقطع مجاور، وتُهمل الحدود التي تقل مدتها الفعالة
    عن 0.05 ثانية.
    """
    result = {}
    for index in range(1, len(timeline or [])):
        segment = timeline[index]
        if not segment_has_valid_transition(segment):
            continue
        requested = max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0))
        left_duration = max(0.0, float(getattr(timeline[index - 1], "duration", 0.0) or 0.0))
        right_duration = max(0.0, float(getattr(segment, "duration", 0.0) or 0.0))
        effective = min(requested, left_duration / 2.0, right_duration / 2.0)
        if effective < 0.05:
            continue
        result[index] = (str(getattr(segment, "transition", "") or ""), effective)
    return result


def timeline_transition_overlap(timeline):
    boundaries = timeline_boundary_transitions(timeline)
    return sum(duration for _key, duration in boundaries.values())


def timeline_export_duration(timeline):
    from video_maker.timeline import total_duration

    return max(0.0, total_duration(timeline) - timeline_transition_overlap(timeline))


def xfade_chain_filters(video_labels, playback_durations, boundary_transitions):
    """سلسلة xfade/concat تدمج مقاطع متجاورة، مع انتقالات عند الحدود المحددة.

    video_labels: تسميات مخارج الفيديو [v0], [v1], ...
    playback_durations: مدة تشغيل كل مقطع.
    boundary_transitions: {index: (key, effective_duration)} من timeline_boundary_transitions.
    يعيد (filter_string, final_label, final_duration).
    """
    filters = []
    current_label = video_labels[0]
    current_length = max(0.0, float(playback_durations[0]))
    chain_index = 0
    for index in range(1, len(video_labels)):
        boundary = boundary_transitions.get(index)
        if boundary:
            transition_key, duration = boundary
            duration = max(0.0, min(float(duration), current_length, float(playback_durations[index])))
            if duration < 0.05:
                boundary = None
            else:
                chain_index += 1
                offset = max(0.01, current_length - duration)
                output_label = f"[xtf{chain_index}]"
                filters.append(
                    f"{current_label}{video_labels[index]}xfade=transition={transition_key}:duration={duration:.6f}:offset={offset:.6f}{output_label}"
                )
                current_label = output_label
                current_length = current_length - duration + max(0.0, float(playback_durations[index]))
        if not boundary:
            chain_index += 1
            output_label = f"[xtc{chain_index}]"
            filters.append(
                f"{current_label}{video_labels[index]}concat=n=2:v=1:a=0{output_label}"
            )
            current_label = output_label
            current_length = current_length + max(0.0, float(playback_durations[index]))
    return ";".join(filters), current_label, current_length




def repeated_transition_values(value, count):
    if isinstance(value, (list, tuple)):
        values = list(value)
        if len(values) >= count:
            return values[:count]
        if values:
            values.extend([values[-1]] * (count - len(values)))
            return values
    return [value] * count


def xfade_filter_for_parts(part_durations, transition_key, transition_duration, fps):
    filters = []
    current_label = "0:v"
    transition_count = max(0, len(part_durations) - 1)
    transition_keys = repeated_transition_values(transition_key, transition_count)
    transition_durations = [float(value or 1.0) for value in repeated_transition_values(transition_duration, transition_count)]
    first_transition = transition_durations[0] if transition_durations else 0
    original_elapsed = part_durations[0] - (first_transition if len(part_durations) > 1 else 0)
    for index in range(1, len(part_durations)):
        pair_duration = transition_durations[index - 1]
        pair_key = transition_keys[index - 1]
        output_label = f"x{index}"
        offset = max(0.01, original_elapsed)
        filters.append(
            f"[{current_label}][{index}:v]xfade=transition={pair_key}:duration={pair_duration:.6f}:offset={offset:.6f}[{output_label}]"
        )
        current_label = output_label
        next_original = part_durations[index]
        if index < len(part_durations) - 1:
            next_original -= transition_durations[index]
        original_elapsed += max(0.0, next_original)
    return ";".join(filters), current_label


def run_xfade_video(part_paths, part_durations, output_path, transition_key, transition_duration, fps):
    filter_complex, output_label = xfade_filter_for_parts(part_durations, transition_key, transition_duration, fps)
    command = [ffmpeg_binary(), "-y"]
    for path in part_paths:
        command.extend(["-r", str(max(1, int(round(float(fps or 24))))), "-i", path])
    command.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{output_label}]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        output_path,
    ])
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        message = result.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(message or "تعذر تطبيق تأثير الانتقال")


def create_video_from_image(image_path, duration, output_path, progress_callback=None, cancelled_callback=None):
    output_path = absolute_output_path(output_path)
    if cancelled_callback and cancelled_callback():
        raise OperationCancelled()
    if progress_callback:
        progress_callback(1)
    command = [
        ffmpeg_binary(),
        "-y",
        "-loop",
        "1",
        "-t",
        f"{max(0.1, float(duration or 0.1)):.6f}",
        "-i",
        image_path,
        "-vf",
        "scale=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1,format=yuv420p",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-an",
        output_path,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        message = result.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(message or "تعذر إنشاء فيديو من الصورة")
    if progress_callback:
        progress_callback(100)


def is_xfade_transition(item):
    return item.get("transition") in xfade_transition_keys()


def pair_transition_settings(left_item, right_item):
    for item in (right_item, left_item):
        if is_xfade_transition(item):
            return item.get("transition"), float(item.get("transition_duration", 1.0) or 1.0)
    return "", 1.0


def items_are_contiguous(left_item, right_item):
    left_end = float(left_item.get("end", left_item.get("start", 0)) or 0)
    right_start = float(right_item.get("start", 0) or 0)
    return abs(right_start - left_end) <= 0.08


def visual_item_groups(visual_items):
    ordered = sorted([dict(item) for item in visual_items], key=lambda item: (float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)))
    groups = []
    index = 0
    while index < len(ordered):
        group = [ordered[index]]
        transition_keys = []
        transition_durations = []
        index += 1
        while index < len(ordered):
            next_item = ordered[index]
            pair_key, pair_duration = pair_transition_settings(group[-1], next_item)
            if not pair_key or not items_are_contiguous(group[-1], next_item):
                break
            transition_keys.append(pair_key)
            transition_durations.append(pair_duration)
            group.append(next_item)
            index += 1
        if len(transition_keys) == 1:
            transition_key = transition_keys[0]
            transition_duration = transition_durations[0]
        else:
            transition_key = transition_keys
            transition_duration = transition_durations
        groups.append((group, transition_key, transition_duration))
    return groups


def visual_item_signature(item):
    path = str(item.get("path", "") or "")
    signature = {
        "id": str(item.get("id", "") or ""),
        "type": str(item.get("type", "") or ""),
        "path": os.path.abspath(path) if path else "",
        "start": round(float(item.get("start", 0) or 0), 4),
        "end": round(float(item.get("end", 0) or 0), 4),
        "transition": str(item.get("transition", "") or ""),
        "transition_duration": round(float(item.get("transition_duration", 1.0) or 1.0), 4),
        "speed": round(normalized_speed(item.get("speed", 1.0)), 4),
        "source_offset": round(float(item.get("source_offset", 0.0) or 0.0), 4),
    }
    try:
        signature["file_size"] = os.path.getsize(path)
        signature["file_mtime"] = round(os.path.getmtime(path), 3)
    except OSError:
        signature["file_size"] = 0
        signature["file_mtime"] = 0
    return signature


def visual_group_signature(group_items, transition_key, transition_duration):
    if isinstance(transition_key, (list, tuple)):
        transition_value = [str(value or "") for value in transition_key]
    else:
        transition_value = str(transition_key or "")
    if isinstance(transition_duration, (list, tuple)):
        duration_value = [round(float(value or 1.0), 4) for value in transition_duration]
    else:
        duration_value = round(float(transition_duration or 1.0), 4)
    return {
        "transition": transition_value,
        "transition_duration": duration_value,
        "items": [visual_item_signature(item) for item in group_items],
    }


def generate_visual_item_part(item, size, duration, output_path, fps=24):
    import subprocess
    from video_maker.app_paths import ffmpeg_binary
    path = item.get("path")
    is_image = item.get("type") in ("image", "text")
    w, h = size
    speed = float(item.get("speed", 1.0) or 1.0)
    source_offset = float(item.get("source_offset", 0.0) or 0.0)
    
    cmd = [ffmpeg_binary(), "-y"]
    if is_image:
        cmd.extend(["-loop", "1", "-i", path])
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
        cmd.extend(["-t", str(duration), "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", str(fps), output_path])
    else:
        cmd.extend(["-ss", str(source_offset), "-i", path])
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
        if abs(speed - 1.0) > 0.01:
            vf = f"setpts={1.0/speed}*PTS," + vf
        cmd.extend(["-t", str(duration), "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", str(fps), output_path])
        
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def render_xfade_visual_overlay_file(group_items, size, transition_key, transition_duration, fps=24):
    temp_dir = tempfile.mkdtemp(prefix="audio_visual_transition_")
    part_paths = []
    try:
        base_clips = []
        for item in group_items:
            duration = max(0.05, float(item.get("end", 0) or 0) - float(item.get("start", 0) or 0))
            base_clips.append((item, duration))
            
        if len(base_clips) < 2:
            raise RuntimeError("Need at least two visual items for xfade")
            
        requested_transitions = [float(value or 1.0) for value in repeated_transition_values(transition_duration, len(base_clips) - 1)]
        safe_transitions = [
            min(requested, max(0.05, base_clips[index][1] / 2.0))
            for index, requested in enumerate(requested_transitions)
        ]
        
        prepared = []
        for index, (item, duration) in enumerate(base_clips):
            if index < len(base_clips) - 1:
                safe_transition = safe_transitions[index]
                prepared.append((item, duration + safe_transition))
            else:
                prepared.append((item, duration))
                
        for index, (item, duration) in enumerate(prepared):
            part_path = os.path.join(temp_dir, f"part_{index}.mp4")
            generate_visual_item_part(item, size, duration, part_path, fps)
            part_paths.append(part_path)
            
        output_path = os.path.join(temp_dir, "overlay_transition.mp4")
        run_xfade_video(part_paths, [duration for item, duration in prepared], output_path, transition_key, safe_transitions, fps)
        return output_path, temp_dir, visual_group_signature(group_items, transition_key, transition_duration)
    except Exception:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _resolve_visual_item_source(item, size, render_dir):
    """يعيد مسار مصدر مرئي لعنصر جاهز للتراكب، أو "" إن لم يتوفر.

    النصوص الديناميكية (is_dynamic) لا تملك ملفاً؛ تُرسم إلى PNG شفاف عبر
    render_text_image بمقاس الشاشة المطلوب وتُعاد مسارها. أما عناصر الصور
    والفيديو فتستخدم ملفها إن وُجد فعلياً.
    """
    if item.get("is_dynamic"):
        try:
            from video_maker.text_overlay import from_text_item, render_text_image

            options = from_text_item(item)
            if options is None or not getattr(options, "text", ""):
                return ""
            overlay_path = os.path.join(
                render_dir,
                "text_overlay_{id}.png".format(id=str(item.get("id", "") or "item")),
            )
            render_text_image(options, overlay_path, canvas_size=size)
            return overlay_path
        except Exception:
            return ""
    path = str(item.get("path", "") or "")
    if path and os.path.exists(path):
        return path
    return ""


def _append_visual_track_overlay_filters(inputs, filters, current_v, visual_items, size, duration, input_idx, cancelled_callback=None):
    """تراكب عناصر التراك المرئي (نصوص ديناميكية/صور/فيديو) فوق الفيديو.

    - النصوص الديناميكية والصور تُدخل كصورة loop داخل نافذتها الزمنية.
    - الفيديو يُدخل مع احترام source_offset والسرعة ثم يُوضع في نافذته.
    - كل عنصر يظهر فقط ضمن [start, end) عبر overlay enable.
    يعيد (current_v, input_idx, render_temp_dirs) بعد تحديثها.
    """
    overlay_w, overlay_h = size if size else (1280, 720)
    render_dir = tempfile.mkdtemp(prefix="visual_track_overlay_")
    render_dirs = [render_dir]
    try:
        ordered = sorted(
            [dict(item) for item in visual_items or ()],
            key=lambda item: (float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)),
        )
        for item in ordered:
            if cancelled_callback and cancelled_callback():
                raise OperationCancelled()
            source = _resolve_visual_item_source(item, (overlay_w, overlay_h), render_dir)
            if not source:
                continue
            start = max(0.0, float(item.get("start", 0) or 0))
            end = min(float(duration), float(item.get("end", duration) or duration))
            if end <= start:
                continue
            is_video_source = str(item.get("type", "") or "") == "video"
            item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
            source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
            if is_video_source:
                source_duration = (end - start) * item_speed
                append_ffmpeg_input(inputs, source, ss=source_offset, duration=source_duration)
            else:
                append_ffmpeg_input(inputs, source, duration=end - start, loop=True, low_memory=False)
            idx = input_idx
            input_idx += 1
            if is_video_source:
                pts_expr = f"(PTS-STARTPTS)/{item_speed:.6f}+{start:.6f}/TB"
            else:
                pts_expr = f"PTS-STARTPTS+{start:.6f}/TB"
            scale_chain = (
                f"[{idx}:v]scale={overlay_w}:{overlay_h}:force_original_aspect_ratio=increase,"
                f"crop={overlay_w}:{overlay_h},setsar=1,format=rgba,setpts={pts_expr}"
            )
            filters.append(f"{scale_chain}[v_vis_{idx}];")
            filters.append(
                f"{current_v}[v_vis_{idx}]overlay=enable='between(t,{start:.6f},{end:.6f})':format=auto[v_vis_out_{idx}];"
            )
            current_v = f"[v_vis_out_{idx}]"
    except Exception:
        for directory in render_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    return current_v, input_idx, render_dirs


def _append_audio_visual_overlay_filters(cmd, filters, current_v, visual_items, size, duration, input_idx, cancelled_callback=None, include_typing_audio=True, audio_mix_inputs=None):
    w, h = size
    render_temp_dirs = []
    text_render_dir = tempfile.mkdtemp(prefix="audio_visual_text_")
    render_temp_dirs.append(text_render_dir)
    for group, transition_key, transition_duration in visual_item_groups(visual_items):
        if cancelled_callback and cancelled_callback():
            raise OperationCancelled()
        resolved = []
        for item in group:
            source = _resolve_visual_item_source(item, (w, h), text_render_dir)
            resolved.append((item, source))
        valid_items = [(item, source) for item, source in resolved if source]
        if not valid_items:
            continue
        all_file_based = all(not item.get("is_dynamic") for item, _source in valid_items)
        if len(group) >= 2 and transition_key and all_file_based and len(valid_items) == len(group):
            group_items = [item for item, _source in valid_items]
            group_start = min(float(item.get("start", 0) or 0) for item in group_items)
            group_end = max(float(item.get("end", 0) or 0) for item in group_items)
            render_path, render_temp_dir, _signature = render_xfade_visual_overlay_file(group_items, size, transition_key, transition_duration)
            render_temp_dirs.append(render_temp_dir)
            cmd.extend(["-i", render_path])
            item_v = f"[{input_idx}:v]"
            rendered_duration = float(get_media_duration(render_path) or (group_end - group_start))
            overlay_end = group_start + max(0.0, rendered_duration)
            filters.append(f"{item_v}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=rgba[v_proc_{input_idx}];")
            filters.append(f"{current_v}[v_proc_{input_idx}]overlay=enable='between(t,{group_start},{overlay_end})':format=auto[v_out_{input_idx}];")
            current_v = f"[v_out_{input_idx}]"
            input_idx += 1
        else:
            for item, source in valid_items:
                if cancelled_callback and cancelled_callback():
                    raise OperationCancelled()
                start = max(0.0, float(item.get("start", 0) or 0))
                end = min(duration, float(item.get("end", duration) or duration))
                if end <= start:
                    continue
                is_video_source = str(item.get("type", "") or "") == "video"
                if is_video_source:
                    source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
                    cmd.extend(["-ss", f"{source_offset:.6f}", "-i", source])
                else:
                    cmd.extend(["-loop", "1", "-t", f"{end - start:.6f}", "-i", source])
                item_v = f"[{input_idx}:v]"
                filters.append(f"{item_v}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=rgba[v_proc_{input_idx}];")
                filters.append(f"{current_v}[v_proc_{input_idx}]overlay=enable='between(t,{start},{end})':format=auto[v_out_{input_idx}];")
                current_v = f"[v_out_{input_idx}]"
                if include_typing_audio and item.get("is_typing") and audio_mix_inputs is not None:
                    start_ms = int(start * 1000)
                    filters.append(f"[{input_idx}:a]adelay={start_ms}|{start_ms}[a_delayed_{input_idx}];")
                    audio_mix_inputs.append(f"[a_delayed_{input_idx}]")
                input_idx += 1
    return current_v, input_idx, render_temp_dirs


def write_audio_visual_video(audio_timeline, visual_items, save_path, progress_callback=None, cancelled_callback=None, metadata=None, save_options=None, background_audio_items=None, sound_effects_items=None, muted_tracks=None, solo_tracks=None):
    save_path = absolute_output_path(save_path)
    if cancelled_callback and cancelled_callback(): raise OperationCancelled()
    if progress_callback: progress_callback(1)

    temp_dir = tempfile.mkdtemp(prefix="audio_visual_")
    audio_path = os.path.join(temp_dir, "master_audio.wav")
    render_temp_dirs = []
    
    try:
        def build_progress(completed, total):
            if progress_callback:
                progress_callback(1 + int((completed / max(1, total)) * 20))
                
        # 1. Build master audio track (handles background music & volume)
        write_timeline_audio(
            timeline=audio_timeline,
            save_path=audio_path,
            progress_callback=lambda p: build_progress(p, 100),
            cancelled_callback=cancelled_callback,
            background_audio_items=background_audio_items,
            sound_effects_items=sound_effects_items,
            muted_tracks=muted_tracks,
            solo_tracks=solo_tracks,
            save_options=save_options
        )
        
        duration = get_audio_duration(audio_path)
        if duration <= 0:
            duration = sum(max(0.0, float(s.end) - float(s.start)) for s in audio_timeline)
            
        # 2. Build FFmpeg command for visual overlays
        cmd = [ffmpeg_binary(), "-y", "-i", audio_path]
        filters = []
        input_idx = 1
        
        size = requested_output_size(save_options)
        if not size: size = (1280, 720)
        w, h = size
        
        filters.append(f"color=c=black:s={w}x{h}:r=24:d={duration}[bg];")
        current_v = "[bg]"
        audio_mix_inputs = ["[0:a]"]
        
        current_v, input_idx, extra_render_dirs = _append_audio_visual_overlay_filters(
            cmd, filters, current_v, visual_items, size, duration, input_idx,
            cancelled_callback=cancelled_callback,
            include_typing_audio=True,
            audio_mix_inputs=audio_mix_inputs,
        )
        render_temp_dirs.extend(extra_render_dirs)
        
        filters.append(f"{current_v}copy[final_v];")
        
        if len(audio_mix_inputs) > 1:
            mix_str = "".join(audio_mix_inputs)
            filters.append(f"{mix_str}amix=inputs={len(audio_mix_inputs)}:duration=longest[final_a];")
        else:
            filters.append(f"[0:a]acopy[final_a];")
            
        script_path = os.path.join(temp_dir, "filter_script.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("".join(filters))
            
        source_audio_bitrate = None
        try:
            source_audio_bitrate = audio_bitrate_from_paths([segment.path for segment in audio_timeline])
        except Exception:
            source_audio_bitrate = None
        audio_codec = (save_options or {}).get("audio_codec") or "aac"
        audio_bitrate_value = (save_options or {}).get("audio_bitrate")
        if not audio_bitrate_value:
            audio_bitrate_value = f"{preferred_audio_bitrate_for_codec(audio_codec, source_audio_bitrate)}k"
        cmd.extend([
            "-filter_complex_script", script_path,
            "-map", "[final_v]",
            "-map", "[final_a]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", audio_codec,
            "-b:a", audio_bitrate_value,
            "-shortest",
            "-movflags", "+faststart",
            save_path
        ])
        
        def encode_progress(percent):
            if progress_callback:
                progress_callback(25 + int(max(0, min(100, percent)) * 0.75))
                
        from video_maker.watermark import run_ffmpeg_with_progress
        run_ffmpeg_with_progress(cmd, save_path, save_path, "فشل تصدير الفيديو البصري.", progress_callback=encode_progress, cancelled_callback=cancelled_callback, total_duration=duration)
        apply_metadata(save_path, metadata)
        if progress_callback:
            progress_callback(100)
    finally:
        import shutil
        for extra_dir in render_temp_dirs:
            shutil.rmtree(extra_dir, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _append_timeline_preview_base_filters(inputs, filters, timeline, duration, size, input_idx):
    """يبني الطبقة الأساسية للمعاينة من مقاطع الخط الزمني الرئيسي.

    يحمّل كل مقطع عبر -ss/-t ويوحّد مقاسه وإطاره ثم يجمع المقاطع عبر
    concat (بدون انتقالات) لإخراج [preview_base]. إن لم يوجد أي مقطع فيديو
    صالح يعيد خلفية سوداء ثابتة بمدة المعاينة.
    """
    w, h = size
    video_outputs = []
    for segment in timeline or []:
        is_dict = isinstance(segment, dict)
        path = (segment.get("path") or "") if is_dict else (getattr(segment, "path", "") or "")
        if not path or not os.path.exists(path):
            continue
        try:
            if not has_video_stream(path):
                continue
        except Exception:
            continue
        start = max(0.0, float((segment.get("start") or 0) if is_dict else (getattr(segment, "start", 0) or 0)))
        end = float((segment.get("end") or 0) if is_dict else (getattr(segment, "end", 0) or 0))
        dur = max(0.01, end - start)
        inputs.extend(["-ss", str(start), "-t", str(dur), "-i", path])
        idx = input_idx
        input_idx += 1
        filters.append(
            f"[{idx}:v]setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v_pb{idx}];"
        )
        video_outputs.append(f"[v_pb{idx}]")
    if not video_outputs:
        filters.append(f"color=c=black:s={w}x{h}:r=24:d={duration}[preview_base];")
        return "[preview_base]", input_idx
    if len(video_outputs) == 1:
        filters.append(f"{video_outputs[0]}copy[preview_base];")
        return "[preview_base]", input_idx
    concat_inputs = "".join(video_outputs)
    filters.append(f"{concat_inputs}concat=n={len(video_outputs)}:v=1:a=0[preview_base];")
    return "[preview_base]", input_idx


def write_audio_visual_preview_video(visual_items, duration, save_path, progress_callback=None, cancelled_callback=None, timeline=None, b_roll_items=None):
    save_path = absolute_output_path(save_path)
    if cancelled_callback and cancelled_callback(): raise OperationCancelled()
    
    temp_dir = tempfile.mkdtemp(prefix="visual_preview_")
    render_temp_dirs = []
    try:
        cmd = [ffmpeg_binary(), "-y"]
        filters = []
        input_idx = 0
        
        size = (1280, 720)
        w, h = size
        
        if timeline:
            current_v, input_idx = _append_timeline_preview_base_filters(cmd, filters, timeline, duration, size, input_idx)
        else:
            filters.append(f"color=c=black:s={w}x{h}:r=24:d={duration}[bg];")
            current_v = "[bg]"
        
        if b_roll_items:
            current_v, input_idx = _append_b_roll_overlay_filters(cmd, filters, current_v, b_roll_items, duration, size, input_idx)
        
        current_v, input_idx, extra_render_dirs = _append_audio_visual_overlay_filters(
            cmd, filters, current_v, visual_items, size, duration, input_idx,
            cancelled_callback=cancelled_callback,
            include_typing_audio=False,
            audio_mix_inputs=None,
        )
        render_temp_dirs.extend(extra_render_dirs)
        
        filters.append(f"{current_v}copy[final_v];")
        
        script_path = os.path.join(temp_dir, "filter_script.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("".join(filters))
            
        cmd.extend([
            "-filter_complex_script", script_path,
            "-map", "[final_v]",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "30",
            save_path
        ])
        
        from video_maker.watermark import run_ffmpeg_with_progress
        run_ffmpeg_with_progress(cmd, save_path, save_path, "فشل إنشاء المعاينة.", progress_callback=progress_callback, cancelled_callback=cancelled_callback, total_duration=duration)
    finally:
        import shutil
        for extra_dir in render_temp_dirs:
            shutil.rmtree(extra_dir, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)






def _ffmpeg_stderr_parse_infos(path):
    cmd = [ffmpeg_binary(), "-hide_banner", "-i", path]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
    )
    output = result.stderr.decode("utf-8", errors="ignore")
    info = {}
    video_line = next((line for line in output.splitlines() if " Video: " in line and not _is_attached_picture_stream(line)), "")
    size_match = re.search(r"(\d{2,5})x(\d{2,5})", video_line)
    if size_match:
        info["video_size"] = [int(size_match.group(1)), int(size_match.group(2))]
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video_line)
    if fps_match:
        info["video_fps"] = float(fps_match.group(1))
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if dur_match:
        info["duration"] = int(dur_match.group(1)) * 3600 + int(dur_match.group(2)) * 60 + float(dur_match.group(3))
    return info


def ffmpeg_parse_infos(path, *args, **kwargs):
    cached = cached_media_info(path)
    if cached is not None and cached.get("raw_info") is not None:
        return cached["raw_info"]
    try:
        cmd = [
            ffprobe_binary(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            path,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
        )
        if result.returncode != 0:
            return _ffmpeg_stderr_parse_infos(path)
        data = json.loads(result.stdout.decode("utf-8", errors="ignore") or "{}")
        info = {}
        streams = data.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if video:
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)
            if width and height:
                info["video_size"] = [width, height]
            rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
            if rate and rate != "0/0":
                info["video_fps"] = float(Fraction(rate))
        duration = (video or audio or {}).get("duration") or (data.get("format") or {}).get("duration")
        if duration not in (None, "", "N/A"):
            info["duration"] = float(duration)
        return info
    except Exception:
        try:
            return _ffmpeg_stderr_parse_infos(path)
        except Exception:
            return {}

def build_xfade_transition_segment(timeline, start_time, end_time, transition_key, transition_duration, progress_callback=None, cancelled_callback=None):
    if cancelled_callback and cancelled_callback(): raise OperationCancelled()
    selected_segments = slice_segments(timeline, start_time, end_time)
    if not selected_segments: raise RuntimeError("تعذر تجهيز الجزء المحدد")
    temp_dir = tempfile.mkdtemp(prefix="visual_transition_")
    
    target_size, fps, bitrate, audio_bitrate = get_video_quality_settings(selected_segments)
    fps = fps or 24
    
    inputs = []
    filters = []
    scale_pad = f"scale={target_size[0]}:{target_size[1]}:force_original_aspect_ratio=decrease,pad={target_size[0]}:{target_size[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
    
    current_offset = 0.0
    for i, segment in enumerate(selected_segments):
        start = segment.start
        end = segment.end
        dur = end - start
        inputs.extend(["-ss", str(start), "-t", str(dur), "-i", segment.path])
        filters.append(f"[{i}:v]{scale_pad}[v{i}];")
        
    if len(selected_segments) > 1:
        current_offset = (selected_segments[0].end - selected_segments[0].start) - transition_duration
        filters.append(f"[v0][v1]xfade=transition={transition_key}:duration={transition_duration}:offset={current_offset}[xf1];")
        for i in range(2, len(selected_segments)):
            current_offset += (selected_segments[i-1].end - selected_segments[i-1].start) - transition_duration
            filters.append(f"[xf{i-1}][v{i}]xfade=transition={transition_key}:duration={transition_duration}:offset={current_offset}[xf{i}];")
        out_pad = f"[xf{len(selected_segments)-1}]"
    else:
        out_pad = "[v0]"

    script_path = os.path.join(temp_dir, "xfade_script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write("".join(filters))
        
    output_path = os.path.join(temp_dir, "transition.mp4")
    cmd = [ffmpeg_binary(), "-y"] + inputs + [
        "-filter_complex_script", script_path,
        "-map", out_pad,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-an",
        output_path
    ]
    subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    
    from video_maker.timeline import TimelineSegment
    duration = float(get_media_duration(output_path))
    return [TimelineSegment(output_path, 0.0, duration)]

def build_visual_transition_segment(timeline, start_time, end_time, transition, progress_callback=None, cancelled_callback=None, transition_duration=1.0):
    if cancelled_callback and cancelled_callback(): raise OperationCancelled()
    selected_segments = slice_segments(timeline, start_time, end_time)
    if not selected_segments: raise RuntimeError("تعذر تجهيز الجزء المحدد")
    temp_dir = tempfile.mkdtemp(prefix="visual_transition_")
    
    target_size, fps, bitrate, audio_bitrate = get_video_quality_settings(selected_segments)
    fps = fps or 24
    
    segment = selected_segments[0]
    dur = segment.end - segment.start
    
    eff = transition
    f_str = ""
    if eff == "rotate": f_str = f",rotate=2*PI*t/{dur}:c=black"
    elif eff == "fadeout": f_str = f",fade=t=out:st={max(0, dur-1)}:d=1"
    elif eff == "mirror": f_str = ",hflip"
    elif eff == "colorx": f_str = f",geq=r='clip(r(X,Y)*(1+0.5*T/{dur}),0,255)':g='clip(g(X,Y)*(1+0.5*T/{dur}),0,255)':b='clip(b(X,Y)*(1+0.5*T/{dur}),0,255)'"

    scale_pad = f"scale={target_size[0]}:{target_size[1]}:force_original_aspect_ratio=decrease,pad={target_size[0]}:{target_size[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
    
    output_path = os.path.join(temp_dir, "transition.mp4")
    cmd = [ffmpeg_binary(), "-y", "-ss", str(segment.start), "-t", str(dur), "-i", segment.path,
           "-vf", f"{scale_pad}{f_str}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "23",
           "-an", output_path]
    if progress_callback: progress_callback(25)
    process = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if cancelled_callback and cancelled_callback():
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise OperationCancelled()
    if process.returncode != 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("تعذر تجهيز تأثير الانتقال")
    if progress_callback: progress_callback(90)
    duration = float(get_media_duration(output_path))
    if progress_callback: progress_callback(100)
    return output_path, temp_dir, duration


def build_caption_transition_segment(
    timeline,
    start_time,
    end_time,
    text_options,
    progress_callback=None,
    cancelled_callback=None,
):
    """Build a caption overlay over its own range.

    The returned clip spans exactly [start_time, end_time] and never extends
    into, or erases, neighbouring captions."""
    from video_maker.text_overlay import TextOverlayOptions, build_text_overlay_segment
    from video_maker.timeline import total_duration

    if cancelled_callback and cancelled_callback():
        raise OperationCancelled()
    timeline_duration = total_duration(timeline)
    start_time = max(0.0, min(float(start_time), timeline_duration))
    end_time = max(start_time, min(float(end_time), timeline_duration))
    if end_time <= start_time:
        raise RuntimeError("تعذر تجهيز الجزء المحدد")

    if isinstance(text_options, dict):
        text_options = TextOverlayOptions(**text_options)
    overlay_path, overlay_temp_dir = build_text_overlay_segment(
        timeline,
        start_time,
        end_time,
        text_options,
        progress_callback=progress_callback,
        cancelled_callback=cancelled_callback,
    )
    return overlay_path, [overlay_temp_dir], [start_time, end_time]


def _append_b_roll_overlay_filters(inputs, filters, current_v, b_roll_items, duration, target_size, input_idx):
    """تراكب الفيديوهات الثانوية فوق الفيديو الرئيسي في نافذتها الزمنية.

    يضيف مدخلات ومقاطع فلاتر للمقاطع الثانوية التي توجد ملفاتها وتحتوي على
    فيديو، ويرجع (current_v, input_idx) بعد تحديثهما.
    """
    overlay_w, overlay_h = target_size if target_size else (1280, 720)
    overlay_count = 0
    for item in b_roll_items or []:
        path = str(item.get("path", "") or "")
        if not path or not os.path.exists(path):
            continue
        start = max(0.0, float(item.get("start", 0) or 0))
        end = min(float(duration), float(item.get("end", duration) or duration))
        if end <= start:
            continue
        try:
            if not has_video_stream(path):
                continue
        except Exception:
            continue
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        append_ffmpeg_input(inputs, path, ss=source_offset, duration=(end - start) * item_speed)
        idx = input_idx
        input_idx += 1
        pts_expr = f"(PTS-STARTPTS)/{item_speed:.6f}+{start:.6f}/TB"
        filters.append(
            f"[{idx}:v]setpts={pts_expr},scale={overlay_w}:{overlay_h}:force_original_aspect_ratio=increase,"
            f"crop={overlay_w}:{overlay_h},setsar=1,format=rgba[v_broll_{idx}];"
        )
        filters.append(
            f"{current_v}[v_broll_{idx}]overlay=enable='between(t,{start},{end})':format=auto[v_broll_out_{idx}];"
        )
        current_v = f"[v_broll_out_{idx}]"
        overlay_count += 1
    return current_v, input_idx


def _timed_audio_mix_items(inputs, filters, amix_inputs, items, boundary_proxy_paths, input_index, total_duration, gain=1.0, ducking_sidechains=None):
    """يخلط عناصر صوتية موقّتة (خلفية/مؤثرات) في مواضعها الزمنية الصحيحة.

    كل عنصر يُقرأ من موضع `source_offset` داخل ملفه بمدة `(end-start)*speed`
    ثم يُشغَّل بسرعته ويوضع عند `start` عبر adelay، بنفس دقة خلط مقاطع
    الخط الزمني الرئيسي (exact_timeline_audio_chain). `gain` مضاعف إضافي
    لمستوى التراك كله (بالخطي).
    """
    for item in items or []:
        start = max(0.0, float(item.get("start", 0) or 0))
        end = float(item.get("end", start) or start)
        if end <= start:
            start = 0.0
            end = max(0.0, float(total_duration or 0.0))
        if end <= start:
            continue
        path = str(item.get("path", "") or "")
        if not path or not os.path.exists(path):
            continue
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        volume = float(item.get("volume", 1.0) if item.get("volume") is not None else 1.0) * gain
        source_duration = max(0.001, (end - start) * speed)
        output_duration = max(0.001, end - start)
        input_path = boundary_proxy_paths.get(os.path.abspath(path)) if boundary_proxy_paths else path
        append_ffmpeg_input(inputs, input_path, ss=source_offset, duration=source_duration, audio_loop=True)
        idx = input_index
        input_index += 1
        chain = exact_timeline_audio_chain(f"[{idx}:a]", source_duration, output_duration, speed, volume)
        start_ms = max(0, int(start * 1000))
        sidechain_label = None
        if ducking_sidechains:
            from video_maker.audio_ducking import has_audio_ducking

            if has_audio_ducking(item):
                sidechain_label = ducking_sidechains.pop(0)
        if sidechain_label:
            from video_maker.audio_ducking import audio_ducking_filter_chain

            filters.append(f"{chain}[duck_bg{idx}];")
            filters.append(
                f"{sidechain_label}apad,atrim=start={start:.6f}:duration={output_duration:.6f},"
                f"asetpts=N/SR/TB,aresample=48000[duck_sc{idx}];"
            )
            filters.append(audio_ducking_filter_chain(f"[duck_bg{idx}]", f"[duck_sc{idx}]", item.get("audio_ducking", {}), f"[ducked_bg{idx}]"))
            filters.append(f"[ducked_bg{idx}]adelay={start_ms}|{start_ms}[t_a{idx}];")
        else:
            filters.append(f"{chain},adelay={start_ms}|{start_ms}[t_a{idx}];")
        amix_inputs.append(f"[t_a{idx}]")
    return input_index


def write_timeline_video(timeline, save_path, progress_callback=None, cancelled_callback=None, metadata=None, save_options=None, background_audio_items=None, main_audio_override_path="", main_audio_override_start=0.0, audio_only=False, b_roll_items=None, sound_effects_items=None, visual_items=None, muted_tracks=None, solo_tracks=None):
    save_path = absolute_output_path(save_path)
    if cancelled_callback and cancelled_callback(): raise OperationCancelled()
    if progress_callback: progress_callback(1)
        
    output_size = requested_output_size(save_options)
    output_extension = os.path.splitext(save_path)[1].lower()
    original_export = (not save_options) or ((save_options or {}).get("video_quality", "original") == "original" and not output_size)
    final_export_vol = export_volume_multiplier_from_options(save_options) * export_master_multiplier_from_options(save_options)
    from video_maker.volume_boost import track_volume_gain
    track_volumes_db = (save_options or {}).get("track_volumes_db") or {}
    main_track_gain = track_volume_gain(MAIN_VIDEO_TRACK, track_volumes_db)
    bg_track_gain = track_volume_gain(BACKGROUND_AUDIO_TRACK, track_volumes_db)
    sfx_track_gain = track_volume_gain(SOUND_EFFECTS_TRACK, track_volumes_db)
    broll_track_gain = track_volume_gain(SECONDARY_VIDEO_TRACK, track_volumes_db)
    needs_volume_render = abs(final_export_vol - 1.0) > 0.0005 or any(
        abs(gain - 1.0) > 0.0005
        for gain in (main_track_gain, bg_track_gain, sfx_track_gain, broll_track_gain)
    )

    from video_maker.track_items import is_track_audible
    solo = set(solo_tracks or ())
    muted = set(muted_tracks or ())
    if not is_track_audible(BACKGROUND_AUDIO_TRACK, muted, solo):
        background_audio_items = []
    if not is_track_audible(SOUND_EFFECTS_TRACK, muted, solo):
        sound_effects_items = []
    if not is_track_audible(SECONDARY_VIDEO_TRACK, muted, solo):
        b_roll_items = []
    if not is_track_audible(TEXT_TRACK, muted, solo):
        visual_items = []
    main_audio_muted = not is_track_audible(MAIN_VIDEO_TRACK, muted, solo)
    if main_audio_muted:
        main_audio_override_path = ""
    has_main_audio_override = bool(main_audio_override_path)
    direct_main_audio_copy_candidate = (
        has_main_audio_override
        and not audio_only
        and not background_audio_items
        and not sound_effects_items
        and not b_roll_items
        and abs(final_export_vol - 1.0) <= 0.0005
    )

    boundary_transitions = {}
    if not audio_only and len(timeline or []) > 1:
        boundary_transitions = timeline_boundary_transitions(timeline)
    has_timeline_transitions = bool(boundary_transitions)
    transition_overlap = sum(duration for _key, duration in boundary_transitions.values())
    export_duration = max(0.0, sum(segment.duration for segment in (timeline or [])) - transition_overlap) if has_timeline_transitions else None
    
    if not has_main_audio_override and not background_audio_items and not b_roll_items and not sound_effects_items and not visual_items and original_export and not needs_volume_render and len(timeline) == 1 and is_full_segment(timeline[0]):
        source_extension = os.path.splitext(timeline[0].path)[1].lower()
        if source_extension == output_extension:
            if os.path.abspath(timeline[0].path) != os.path.abspath(save_path):
                copy_file_with_progress(timeline[0].path, save_path, progress_callback, cancelled_callback)
            elif progress_callback:
                progress_callback(100)
            apply_metadata(save_path, metadata)
            return

    if not has_main_audio_override and not background_audio_items and not b_roll_items and not sound_effects_items and not visual_items and original_export and not needs_volume_render and not has_timeline_transitions and can_stream_copy_concat(timeline):
        source_extensions = {os.path.splitext(segment.path)[1].lower() for segment in timeline}
        if source_extensions == {output_extension} and run_concat_copy(timeline, save_path, progress_callback, cancelled_callback):
            apply_metadata(save_path, metadata)
            return

    if progress_callback: progress_callback(2)
        
    target_size, fps, bitrate, audio_bitrate = get_video_quality_settings(timeline)
    if output_size: target_size = output_size
    audio_source_paths = []
    if main_audio_override_path:
        audio_source_paths.append(main_audio_override_path)
    for item in background_audio_items or []:
        audio_source_paths.append(item.get("path"))
    for item in sound_effects_items or []:
        audio_source_paths.append(item.get("path"))
    for item in b_roll_items or []:
        audio_source_paths.append(item.get("path"))
    explicit_audio_bitrate = audio_bitrate_from_paths(audio_source_paths)
    if explicit_audio_bitrate:
        audio_bitrate = max(audio_bitrate or 0, explicit_audio_bitrate)

        
    if progress_callback: progress_callback(4)

    boundary_proxy_paths = {}
    boundary_proxy_dirs = []
    visual_render_dirs = []
    proxy_extra_paths = []
    if not has_main_audio_override:
        for segment in timeline or []:
            explicit_audio_path = str(getattr(segment, "audio_path", "") or "")
            if explicit_audio_path:
                proxy_extra_paths.append(explicit_audio_path)
    if main_audio_override_path and not direct_main_audio_copy_candidate:
        proxy_extra_paths.append(main_audio_override_path)
    for item in background_audio_items or []:
        item_path = str(item.get("path", "") or "")
        if item_path:
            proxy_extra_paths.append(item_path)
    for item in sound_effects_items or []:
        item_path = str(item.get("path", "") or "")
        if item_path:
            proxy_extra_paths.append(item_path)
    boundary_proxy_paths, boundary_proxy_dirs = prepare_timeline_boundary_safe_audio_proxies(
        timeline,
        progress_callback=(lambda value: progress_callback(4 + max(0.0, min(100.0, float(value))) * 0.10)) if progress_callback else None,
        cancelled_callback=cancelled_callback,
        extra_paths=proxy_extra_paths,
        include_timeline_segments=not has_main_audio_override,
    )

    inputs = []
    filters = []
    video_outputs = []
    audio_outputs = []
    segment_audio_info = []
    segment_video_info = []
    for segment in timeline:
        explicit_audio_path = str(getattr(segment, "audio_path", "") or "")
        audio_path = segment_audio_path(segment) or segment.path
        proxy_source_key = os.path.abspath(explicit_audio_path if explicit_audio_path else str(getattr(segment, "path", "") or ""))
        proxy_path = boundary_proxy_paths.get(proxy_source_key)
        if proxy_path:
            audio_path = proxy_path
        source_path = audio_path if audio_only or audio_path != segment.path else segment.path
        try:
            source_has_audio = bool(source_path and os.path.exists(source_path) and has_audio_stream(source_path))
        except Exception:
            source_has_audio = False
        segment_audio_info.append((audio_path, source_path, source_has_audio))
        try:
            source_has_video = bool((not audio_only) and segment.path and os.path.exists(segment.path) and has_video_stream(segment.path))
        except Exception:
            source_has_video = False
        segment_video_info.append(source_has_video)
    timeline_has_audio = any(has_audio for _audio_path, _source_path, has_audio in segment_audio_info)
    b_roll_audio_items = []
    for item in b_roll_items or []:
        item_path = str(item.get("path", "") or "")
        if not item_path or not os.path.exists(item_path):
            continue
        try:
            if has_audio_stream(item_path):
                b_roll_audio_items.append(item)
        except Exception:
            continue
    direct_main_audio_copy = direct_main_audio_copy_candidate and not b_roll_audio_items
    needs_timeline_audio = audio_only or (not has_main_audio_override and (timeline_has_audio or bool(background_audio_items) or bool(b_roll_audio_items) or bool(sound_effects_items)))
    
    input_index = 0
    total_duration = 0.0
    playback_durations = []
    source_input_indices = {}

    def get_source_input_index(path):
        nonlocal input_index
        norm_key = os.path.abspath(str(path)) if path else str(path)
        if norm_key not in source_input_indices:
            source_input_indices[norm_key] = input_index
            append_ffmpeg_input(inputs, path)
            input_index += 1
        return source_input_indices[norm_key]

    for segment_index, segment in enumerate(timeline):
        vid_path = segment.path
        start = float(segment.start)
        end = float(segment.end)
        speed = segment_speed(segment)
        vol = 0.0 if main_audio_muted else segment_audio_volume(segment) * main_track_gain
        
        dur = max(0.01, end - start)
        playback_duration = dur / speed
        total_duration += playback_duration
        playback_durations.append(playback_duration)
        
        audio_path, audio_source_path, audio_source_has_stream = segment_audio_info[segment_index]
        video_source_has_stream = segment_video_info[segment_index]
        audio_start = segment_audio_start(segment) or start
        aud_idx = None
        
        if audio_only:
            if audio_source_has_stream:
                aud_idx = get_source_input_index(audio_source_path)
        else:
            if video_source_has_stream:
                vid_idx = get_source_input_index(vid_path)
                if audio_source_has_stream and audio_path and audio_path != vid_path:
                    aud_idx = get_source_input_index(audio_path)
                elif audio_source_has_stream:
                    aud_idx = vid_idx

                v_trim = f"trim=start={start:.6f}:duration={dur:.6f},setpts=PTS-STARTPTS"
                v_pts = f"setpts={1/speed}*PTS"
                if target_size:
                    v_scale = f"scale={target_size[0]}:{target_size[1]}:force_original_aspect_ratio=decrease,pad={target_size[0]}:{target_size[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
                    filters.append(f"[{vid_idx}:v]{v_trim},{v_pts},{v_scale}[v{len(video_outputs)}];")
                else:
                    filters.append(f"[{vid_idx}:v]{v_trim},{v_pts}[v{len(video_outputs)}];")
            else:
                if audio_source_has_stream:
                    aud_idx = get_source_input_index(audio_source_path)
                fallback_w, fallback_h = target_size or (1280, 720)
                filters.append(f"color=c=black:s={int(fallback_w)}x{int(fallback_h)}:r={fps}:d={playback_duration:.6f},format=yuv420p[v{len(video_outputs)}];")
            video_outputs.append(f"[v{len(video_outputs)}]")
            
        if needs_timeline_audio:
            audio_label = f"[a{len(audio_outputs)}]"
            next_boundary = boundary_transitions.get(segment_index + 1) if has_timeline_transitions else None
            tail_cut = next_boundary[1] if next_boundary else 0.0
            if aud_idx is None:
                audio_output_duration = max(0.001, playback_duration - tail_cut)
                filters.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={audio_output_duration:.6f},asetpts=N/SR/TB{audio_label};")
            else:
                audio_source_duration = max(0.001, dur - tail_cut * speed)
                audio_output_duration = max(0.001, playback_duration - tail_cut)
                filters.append(
                    exact_timeline_audio_chain(
                        f"[{aud_idx}:a]",
                        audio_source_duration,
                        audio_output_duration,
                        speed,
                        vol,
                        segment_audio_fade_in(segment),
                        segment_audio_fade_out(segment),
                        start_time=audio_start,
                    )
                    + f"{audio_label};"
                )
            audio_outputs.append(audio_label)

    if needs_timeline_audio and audio_only:
        if len(audio_outputs) > 1:
            a_concat = "".join(audio_outputs)
            filters.append(f"{a_concat}concat=n={len(audio_outputs)}:v=0:a=1[base_a];")
        else:
            filters.append(f"{audio_outputs[0]}acopy[base_a];")
    elif not audio_only:
        if has_timeline_transitions:
            chain_filters, final_label, _final_duration = xfade_chain_filters(
                video_outputs, playback_durations, boundary_transitions
            )
            if chain_filters:
                filters.append(f"{chain_filters};")
            filters.append(f"{final_label}copy[base_v];")
            if needs_timeline_audio:
                if len(audio_outputs) > 1:
                    a_concat = "".join(audio_outputs)
                    filters.append(f"{a_concat}concat=n={len(audio_outputs)}:v=0:a=1[base_a];")
                elif audio_outputs:
                    filters.append(f"{audio_outputs[0]}acopy[base_a];")
        elif len(video_outputs) > 1:
            if needs_timeline_audio:
                concat_inputs = "".join(f"{v}{a}" for v, a in zip(video_outputs, audio_outputs))
                filters.append(f"{concat_inputs}concat=n={len(video_outputs)}:v=1:a=1[base_v][base_a];")
            else:
                concat_inputs = "".join(video_outputs)
                filters.append(f"{concat_inputs}concat=n={len(video_outputs)}:v=1:a=0[base_v];")
        else:
            filters.append(f"{video_outputs[0]}copy[base_v];")
            if needs_timeline_audio:
                filters.append(f"{audio_outputs[0]}acopy[base_a];")
        
    current_a = "[base_a]" if needs_timeline_audio else ""
    direct_audio_input_index = None
    if has_main_audio_override:
        override_input_path = boundary_proxy_paths.get(os.path.abspath(main_audio_override_path)) or main_audio_override_path
        append_ffmpeg_input(inputs, override_input_path, ss=main_audio_override_start)
        override_idx = input_index
        input_index += 1
        if direct_main_audio_copy:
            direct_audio_input_index = override_idx
        else:
            current_a = f"[{override_idx}:a]"

    if background_audio_items:
        ducking_sidechains = []
        if current_a:
            from video_maker.audio_ducking import has_audio_ducking

            ducking_count = 0
            for item in background_audio_items or []:
                item_path = str(item.get("path", "") or "")
                if not has_audio_ducking(item) or not item_path or not os.path.exists(item_path):
                    continue
                try:
                    item_start = float(item.get("start", 0) or 0)
                    item_end = float(item.get("end", item_start) or item_start)
                except (TypeError, ValueError):
                    item_start = 0.0
                    item_end = 0.0
                if item_end > item_start or float(total_duration or 0.0) > 0:
                    ducking_count += 1
            if ducking_count:
                sidechain_labels = "".join(f"[bg_duck_sc{i}]" for i in range(ducking_count))
                filters.append(f"{current_a}asplit={ducking_count + 1}[bg_mix_base]{sidechain_labels};")
                amix_inputs = ["[bg_mix_base]"]
                ducking_sidechains = [f"[bg_duck_sc{i}]" for i in range(ducking_count)]
            else:
                amix_inputs = [current_a]
        else:
            amix_inputs = []
        input_index = _timed_audio_mix_items(inputs, filters, amix_inputs, background_audio_items, boundary_proxy_paths, input_index, total_duration, gain=bg_track_gain, ducking_sidechains=ducking_sidechains)
        if amix_inputs:
            amix_str = "".join(amix_inputs)
            filters.append(f"{amix_str}amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[mixed_a];")
            current_a = "[mixed_a]"

    if sound_effects_items:
        amix_inputs = [current_a] if current_a else []
        input_index = _timed_audio_mix_items(inputs, filters, amix_inputs, sound_effects_items, boundary_proxy_paths, input_index, total_duration, gain=sfx_track_gain)
        if amix_inputs:
            amix_str = "".join(amix_inputs)
            filters.append(f"{amix_str}amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[sfx_mixed];")
            current_a = "[sfx_mixed]"

    if b_roll_audio_items:
        amix_inputs = [current_a] if current_a else []
        for item in b_roll_audio_items:
            start = max(0.0, float(item.get("start", 0) or 0))
            end = float(item.get("end", 0) or 0)
            vol = float(item.get("volume", 1.0) if item.get("volume") is not None else 1.0) * broll_track_gain
            path = item.get("path")
            item_duration = max(0.0, end - start) if end > start else 0.0
            source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
            append_ffmpeg_input(inputs, path, ss=source_offset, duration=item_duration or None)
            idx = input_index
            input_index += 1
            if item_duration > 0:
                filters.append(
                    f"[{idx}:a]atrim=duration={item_duration:.6f},asetpts=N/SR/TB,"
                    f"adelay={int(start * 1000)}|{int(start * 1000)},volume={vol}[broll_a{idx}];"
                )
            else:
                filters.append(f"[{idx}:a]volume={vol}[broll_a{idx}];")
            amix_inputs.append(f"[broll_a{idx}]")
        amix_str = "".join(amix_inputs)
        filters.append(f"{amix_str}amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[broll_mixed];")
        current_a = "[broll_mixed]"
        
    if current_a:
        final_audio_duration = export_duration if has_timeline_transitions else total_duration
        filters.append(f"{current_a}apad,atrim=duration={max(0.001, final_audio_duration):.6f},asetpts=N/SR/TB[export_a];")
        current_a = "[export_a]"
        if abs(final_export_vol - 1.0) > 0.0005:
            filters.append(f"{current_a}volume={final_export_vol:.6f}[final_a];")
            current_a = "[final_a]"
        else:
            filters.append(f"{current_a}acopy[final_a];")
            current_a = "[final_a]"
        
    if not audio_only:
        final_video_duration = export_duration if has_timeline_transitions else total_duration
        filters.append(exact_timeline_video_chain("[base_v]", final_video_duration) + "[final_v];")
        if b_roll_items:
            current_v, input_index = _append_b_roll_overlay_filters(
                inputs, filters, "[final_v]", b_roll_items, final_video_duration, target_size, input_index
            )
            if current_v != "[final_v]":
                filters.append(f"{current_v}copy[final_v];")
        if visual_items:
            current_v, input_index, visual_render_dirs = _append_visual_track_overlay_filters(
                inputs, filters, "[final_v]", visual_items, target_size or (1280, 720), final_video_duration, input_index,
                cancelled_callback=cancelled_callback,
            )
            if current_v != "[final_v]":
                filters.append(f"{current_v}copy[final_v];")        
    script_path = os.path.join(tempfile.gettempdir(), f"timeline_script_{os.getpid()}.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write("".join(filters))
        
    cmd = [ffmpeg_binary(), "-y", *FFMPEG_LOW_MEMORY_FILTER_OPTIONS] + inputs + [
        "-filter_complex_script", script_path,
    ]
    video_settings = None
    if not audio_only:
        video_settings = video_output_settings(save_path, save_options, bitrate, audio_bitrate)
        cmd.extend([
            "-map", "[final_v]",
            "-c:v", video_settings.get("codec") or "libx264",
        ])
        if video_settings.get("preset"):
            cmd.extend(["-preset", str(video_settings["preset"])])
        if video_settings.get("bitrate"):
            cmd.extend(["-b:v", str(video_settings["bitrate"])])
        cmd.extend(list(video_settings.get("ffmpeg_params") or []))
            
    output_ext = os.path.splitext(save_path)[1].lower()
    if direct_audio_input_index is not None:
        cmd.extend(["-map", f"{direct_audio_input_index}:a:0"])
    elif current_a:
        cmd.extend(["-map", "[final_a]"])
    if audio_only:
        format_key = (save_options or {}).get("format")
        if not format_key:
            format_key = next((item["key"] for item in AUDIO_FORMATS if item["extension"] == output_ext), "mp3")
        audio_settings = audio_format_settings(format_key, (save_options or {}).get("quality"))
        audio_codec = (save_options or {}).get("audio_codec") or audio_settings.get("audio_codec") or audio_codec_for_extension(output_ext)
        audio_bitrate_value = (save_options or {}).get("audio_bitrate") or audio_settings.get("audio_bitrate")
        audio_channels = (save_options or {}).get("audio_channels")
        audio_params = list((save_options or {}).get("audio_ffmpeg_params") or audio_settings.get("audio_ffmpeg_params") or [])
        if output_ext in (".wav", ".aiff", ".aif"):
            container_pcm_codec = "pcm_s16le" if output_ext == ".wav" else "pcm_s16be"
            if not str(audio_codec or "").startswith("pcm_"):
                audio_codec = container_pcm_codec
                audio_bitrate_value = None
                audio_channels = None
                audio_params = []
        cmd.extend(["-c:a", audio_codec])
        if audio_bitrate_value:
            cmd.extend(["-b:a", str(audio_bitrate_value)])
        if audio_codec == "libopus":
            cmd.extend(["-ar", "48000"])
        if audio_channels:
            cmd.extend(["-ac", str(audio_channels)])
        cmd.extend(audio_params)
    elif direct_audio_input_index is not None:
        cmd.extend(["-c:a", "copy"])
    elif current_a:
        audio_codec = (save_options or {}).get("audio_codec") or (video_settings or {}).get("audio_codec") or "aac"
        audio_bitrate_value = (video_settings or {}).get("audio_bitrate") or (str(audio_bitrate) + "k" if audio_bitrate else "320k")
        cmd.extend(["-c:a", audio_codec])
        if audio_bitrate_value:
            cmd.extend(["-b:a", str(audio_bitrate_value)])
        if audio_codec == "libopus":
            cmd.extend(["-ar", "48000"])
    final_mux_duration = export_duration if (not audio_only and has_timeline_transitions) else total_duration
    if final_mux_duration > 0.001:
        cmd.extend(["-t", f"{final_mux_duration:.6f}"])
    cmd.append(save_path)
    

    try:
        try:
            from video_maker.watermark import run_ffmpeg_with_progress
            run_ffmpeg_with_progress(
                cmd,
                save_path,
                save_path,
                "فشل تصدير الفيديو النهائي.",
                progress_callback=lambda p: progress_callback(14 + p * 0.86) if progress_callback else None,
                cancelled_callback=cancelled_callback,
                total_duration=total_duration
            )
        except Exception as e:
            if not (cancelled_callback and cancelled_callback()):
                raise

        apply_metadata(save_path, metadata)
    finally:
        for directory in boundary_proxy_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        for directory in visual_render_dirs:
            shutil.rmtree(directory, ignore_errors=True)
