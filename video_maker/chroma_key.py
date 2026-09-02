import io
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image
from video_maker.app_paths import ffmpeg_binary

from video_maker.audio_effects import AudioEffectPreparationCancelled
from video_maker.localization import tr
from video_maker.timeline import locate_segment, slice_segments, total_duration
from video_maker.video_editing import (
    ffmpeg_startupinfo,
    get_media_duration,
    has_video_stream,
    is_full_segment,
    write_timeline_video,
)
from video_maker.watermark import run_ffmpeg_with_progress


ANALYSIS_FRAME_WIDTH = 320
ANALYSIS_SAMPLE_COUNT = 7
PREVIEW_DURATION_SECONDS = 8.0
PREVIEW_MAX_WIDTH = 960
TEMPORAL_MASK_MAX_WIDTH = 960
PREVIEW_TEMPORAL_MASK_MAX_WIDTH = 640

_FFMPEG_FILTERS_CACHE = None

# FFmpeg's chromakey filter measures only chroma (U/V), not brightness.  The
# old implementation measured the spread in RGB, so shadows on the green
# screen produced a huge similarity value and made neutral dark clothing and
# skin transparent.  These limits intentionally favour keeping the subject;
# an imperfect screen may leave a small green remnant, but the foreground must
# never disappear.
MIN_CHROMA_SIMILARITY = 0.038
MAX_CHROMA_SIMILARITY = 0.055
MIN_CHROMA_BLEND = 0.010
MAX_CHROMA_BLEND = 0.018
MAX_CHROMA_KEY_RADIUS = 0.066


@dataclass(frozen=True)
class ChromaAnalysis:
    color: tuple
    similarity: float
    blend: float
    green_ratio: float
    consistency: float
    rating: str

    @property
    def color_hex(self):
        red, green, blue = self.color
        return f"0x{red:02X}{green:02X}{blue:02X}"


@dataclass(frozen=True)
class ChromaBackgroundOptions:
    background_kind: str
    background_path: str
    fit_mode: str = "fill"


class ChromaAnalysisError(RuntimeError):
    pass


def files_are_identical(first_path, second_path, chunk_size=1024 * 1024):
    """Return True only when two files are byte-for-byte identical.

    The expensive comparison runs only when sizes match. This is a final safety
    check for transforms: a renderer must never report success after returning
    the unchanged source file.
    """
    try:
        if os.path.abspath(first_path) == os.path.abspath(second_path):
            return True
        if os.path.getsize(first_path) != os.path.getsize(second_path):
            return False
        with open(first_path, "rb") as first, open(second_path, "rb") as second:
            while True:
                first_chunk = first.read(chunk_size)
                second_chunk = second.read(chunk_size)
                if first_chunk != second_chunk:
                    return False
                if not first_chunk:
                    return True
    except OSError:
        return False


def _ffmpeg_binary():
    try:
        value = ffmpeg_binary()
        if value:
            return value
    except Exception:
        pass
    return os.environ.get("IMAGEIO_FFMPEG_EXE") or shutil.which("ffmpeg") or "ffmpeg"


def _cancelled(cancelled_callback):
    return bool(cancelled_callback and cancelled_callback())


def _sample_times(duration, count=ANALYSIS_SAMPLE_COUNT):
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0.08:
        return [0.0]
    count = max(3, int(count or ANALYSIS_SAMPLE_COUNT))
    safe_end = max(0.0, duration - 0.04)
    if count == 1:
        return [safe_end / 2]
    return [safe_end * index / (count - 1) for index in range(count)]


def _frame_png(path, time_seconds, width=ANALYSIS_FRAME_WIDTH):
    command = [
        _ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(time_seconds or 0.0)):.6f}",
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-2:flags=bilinear",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
    )
    if result.returncode == 0 and not result.stdout:
        requested_time = max(0.0, float(time_seconds or 0.0))
        for fallback_time in (
            max(0.0, requested_time - 0.08),
            max(0.0, requested_time - 0.16),
            max(0.0, requested_time - 0.32),
            0.0,
        ):
            if abs(fallback_time - requested_time) <= 0.000001:
                continue
            retry_command = list(command)
            retry_command[5] = f"{fallback_time:.6f}"
            retry_result = subprocess.run(
                retry_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                startupinfo=ffmpeg_startupinfo(),
            )
            if retry_result.returncode == 0 and retry_result.stdout:
                result = retry_result
                break
    if result.returncode != 0 or not result.stdout:
        message = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(message or tr("تعذر قراءة إطارات الفيديو لفحص الكرومة"))
    with Image.open(io.BytesIO(result.stdout)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32)


def _border_pixels(frame):
    height, width = frame.shape[:2]
    border_y = max(4, int(round(height * 0.18)))
    border_x = max(4, int(round(width * 0.18)))
    mask = np.zeros((height, width), dtype=bool)
    mask[:border_y, :] = True
    mask[-border_y:, :] = True
    mask[:, :border_x] = True
    mask[:, -border_x:] = True
    return frame[mask]


def _green_candidates(pixels):
    red = pixels[:, 0]
    green = pixels[:, 1]
    blue = pixels[:, 2]
    dominance = green - np.maximum(red, blue)
    return (
        (green >= 42)
        & (dominance >= 9)
        & (green >= red * 1.07 + 4)
        & (green >= blue * 1.04 + 3)
    )


def _rgb_to_uv(pixels):
    """Convert RGB values to the U/V coordinates used by FFmpeg chromakey."""
    values = np.asarray(pixels, dtype=np.float32)
    red = values[..., 0]
    green = values[..., 1]
    blue = values[..., 2]
    u = -0.16874 * red - 0.33126 * green + 0.50000 * blue + 128.0
    v = 0.50000 * red - 0.41869 * green - 0.08131 * blue + 128.0
    return np.stack((u, v), axis=-1)


def _normalized_uv_distance(values, key_uv):
    return np.linalg.norm(values - key_uv, axis=-1) / (255.0 * np.sqrt(2.0))


def _central_non_green_distances(frame, key_uv):
    """Return likely foreground chroma distances for the subject-protection cap."""
    height, width = frame.shape[:2]
    y0 = max(0, int(round(height * 0.10)))
    x0 = max(0, int(round(width * 0.16)))
    x1 = min(width, int(round(width * 0.84)))
    region = frame[y0:, x0:x1]
    if region.size == 0:
        return np.empty((0,), dtype=np.float32)
    flat = region.reshape(-1, 3)
    non_green = ~_green_candidates(flat)
    if not np.any(non_green):
        return np.empty((0,), dtype=np.float32)
    return _normalized_uv_distance(_rgb_to_uv(flat[non_green]), key_uv)


def analyze_frames(frames):
    border_sets = []
    ratios = []
    valid_frames = []
    for frame in frames:
        if frame is None or getattr(frame, "size", 0) == 0:
            continue
        valid_frames.append(frame)
        border = _border_pixels(frame)
        mask = _green_candidates(border)
        ratio = float(np.count_nonzero(mask)) / max(1, len(border))
        ratios.append(ratio)
        if np.any(mask):
            border_sets.append(border[mask])
    if not border_sets or not ratios:
        raise ChromaAnalysisError(tr("لم يتم اكتشاف خلفية خضراء كافية في الفيديو"))

    median_ratio = float(statistics.median(ratios))
    all_green = np.concatenate(border_sets, axis=0)
    if median_ratio < 0.10:
        raise ChromaAnalysisError(tr("لم يتم اكتشاف خلفية خضراء كافية في الفيديو"))

    # Pick a real sampled pixel nearest to the median U/V value.  This avoids
    # producing an RGB median whose converted chroma is not representative.
    green_uv = _rgb_to_uv(all_green)
    median_uv = np.median(green_uv, axis=0)
    nearest_index = int(np.argmin(np.linalg.norm(green_uv - median_uv, axis=1)))
    color = all_green[nearest_index]
    key_uv = _rgb_to_uv(color)
    background_distances = _normalized_uv_distance(green_uv, key_uv)

    background_p95 = float(np.percentile(background_distances, 95))
    background_p985 = float(np.percentile(background_distances, 98.5))
    background_p995 = float(np.percentile(background_distances, 99.5))

    # Cover almost all of the sampled screen while keeping a strict ceiling.
    similarity = max(
        MIN_CHROMA_SIMILARITY,
        min(MAX_CHROMA_SIMILARITY, background_p985 + 0.004),
    )
    blend = max(
        MIN_CHROMA_BLEND,
        min(MAX_CHROMA_BLEND, background_p995 - similarity + 0.008),
    )

    # Protect non-green pixels in the central area, where the subject normally
    # appears.  The total transparent/semi-transparent radius is kept below the
    # first percentile of likely foreground chroma values with a safety margin.
    foreground_sets = [
        values for values in (_central_non_green_distances(frame, key_uv) for frame in valid_frames)
        if len(values)
    ]
    if foreground_sets:
        foreground_distances = np.concatenate(foreground_sets)
        foreground_floor = float(np.percentile(foreground_distances, 1.0))
        safe_radius = max(MIN_CHROMA_SIMILARITY + MIN_CHROMA_BLEND, foreground_floor - 0.010)
    else:
        safe_radius = MAX_CHROMA_KEY_RADIUS
    safe_radius = min(MAX_CHROMA_KEY_RADIUS, safe_radius)

    if similarity + blend > safe_radius:
        blend = max(MIN_CHROMA_BLEND, min(blend, safe_radius - similarity))
    if similarity + blend > safe_radius:
        similarity = max(MIN_CHROMA_SIMILARITY, safe_radius - blend)
    similarity = min(MAX_CHROMA_SIMILARITY, similarity)
    blend = min(MAX_CHROMA_BLEND, blend)

    consistency = max(0.0, min(1.0, 1.0 - background_p95 / 0.075))
    if median_ratio >= 0.52 and consistency >= 0.52 and background_p985 <= 0.050:
        rating = "good"
    elif median_ratio >= 0.30 and consistency >= 0.28:
        rating = "acceptable"
    else:
        rating = "weak"

    rounded_color = tuple(max(0, min(255, int(round(value)))) for value in color)
    return ChromaAnalysis(
        color=rounded_color,
        similarity=round(similarity, 4),
        blend=round(blend, 4),
        green_ratio=round(median_ratio, 4),
        consistency=round(consistency, 4),
        rating=rating,
    )


def analyze_video_chroma(path, cancelled_callback=None, start_time=0.0, duration=None):
    if not path or not os.path.isfile(path) or not has_video_stream(path):
        raise ChromaAnalysisError(tr("افتح فيديو يحتوي على كرومة خضراء أولا"))
    media_duration = max(0.0, get_media_duration(path))
    start_time = max(0.0, min(float(start_time or 0.0), media_duration))
    available_duration = max(0.0, media_duration - start_time)
    if duration is None:
        duration = available_duration
    duration = max(0.0, min(float(duration or 0.0), available_duration))
    frames = []
    for sample_time in _sample_times(duration):
        if _cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        frames.append(_frame_png(path, start_time + sample_time))
    return analyze_frames(frames)


def analyze_timeline_chroma(timeline, cancelled_callback=None):
    timeline = list(timeline or [])
    duration = total_duration(timeline)
    if not timeline or duration <= 0:
        raise ChromaAnalysisError(tr("افتح فيديو يحتوي على كرومة خضراء أولا"))
    frames = []
    for timeline_time in _sample_times(duration):
        if _cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        _, segment, segment_position = locate_segment(timeline, timeline_time)
        if segment is None or not has_video_stream(segment.path):
            continue
        speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
        source_time = float(segment.start) + max(0.0, timeline_time - segment_position) * speed
        source_time = min(max(float(segment.start), source_time), max(float(segment.start), float(segment.end) - 0.01))
        frames.append(_frame_png(segment.path, source_time))
    return analyze_frames(frames)


def analysis_message(analysis):
    if analysis.rating == "good":
        return tr("الكرومة مناسبة للاستبدال التلقائي")
    if analysis.rating == "acceptable":
        return tr("الكرومة مقبولة. استمع إلى المعاينة قبل التطبيق")
    return tr("اللون الأخضر موجود لكنه غير متساو. استمع إلى المعاينة قبل التطبيق")


def validate_background(options):
    path = str(getattr(options, "background_path", "") or "")
    kind = str(getattr(options, "background_kind", "") or "")
    if not path or not os.path.isfile(path):
        raise ValueError(tr("اختر ملف الخلفية الجديدة أولا"))
    if kind == "image":
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise ValueError(tr("ملف صورة الخلفية غير صالح")) from error
    elif kind == "video":
        if not has_video_stream(path):
            raise ValueError(tr("ملف فيديو الخلفية غير صالح"))
    else:
        raise ValueError(tr("اختر نوع الخلفية الجديدة"))


def _source_profile(path):
    result = subprocess.run(
        [_ffmpeg_binary(), "-hide_banner", "-i", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
    )
    output = result.stderr.decode("utf-8", errors="ignore")
    video_line = next((line for line in output.splitlines() if " Video: " in line), "")
    dimensions = []
    for width_text, height_text in re.findall(r"(?<![0-9A-Fa-f])([1-9]\d{1,4})x([1-9]\d{1,4})(?![0-9A-Fa-f])", video_line):
        width, height = int(width_text), int(height_text)
        if 16 <= width <= 16384 and 16 <= height <= 16384:
            dimensions.append((width, height))
    if not dimensions:
        raise RuntimeError(tr("تعذر تحديد أبعاد الفيديو"))
    width, height = dimensions[0]
    width += width % 2
    height += height % 2
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video_line)
    try:
        fps = float(fps_match.group(1)) if fps_match else 30.0
    except (TypeError, ValueError):
        fps = 30.0
    fps = max(1.0, min(120.0, fps))
    return width, height, fps


def _background_filter(width, height, fit_mode):
    if fit_mode == "stretch":
        return f"scale={width}:{height}:flags=lanczos"
    if fit_mode == "fit":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height}"
    )


def _scaled_dimensions(width, height, max_width=None):
    width = max(2, int(width))
    height = max(2, int(height))
    if max_width and width > int(max_width):
        scale = float(max_width) / float(width)
        width = int(round(width * scale))
        height = int(round(height * scale))
    width += width % 2
    height += height % 2
    return width, height


def _available_ffmpeg_filters():
    """Return the filters supported by the bundled FFmpeg binary.

    The result is cached because this helper is used for both previews and the
    final render.  Older bundled FFmpeg builds may not have every temporal
    filter, so the graph degrades safely instead of failing at run time.
    """
    global _FFMPEG_FILTERS_CACHE
    if _FFMPEG_FILTERS_CACHE is not None:
        return _FFMPEG_FILTERS_CACHE
    filters = set()
    try:
        result = subprocess.run(
            [_ffmpeg_binary(), "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
        )
        output = (result.stdout + result.stderr).decode("utf-8", errors="ignore")
        for line in output.splitlines():
            match = re.match(r"\s*[TSC.]{3}\s+([A-Za-z0-9_]+)\s", line)
            if match:
                filters.add(match.group(1))
    except Exception:
        filters = set()
    _FFMPEG_FILTERS_CACHE = frozenset(filters)
    return _FFMPEG_FILTERS_CACHE


def _temporal_mask_filter(width, height, preview=False):
    """Build a light-weight, motion-safe temporal stabilizer for the alpha mask.

    The key mask is calculated at a bounded resolution, while the foreground
    colour stays at the original output resolution.  This keeps preview and
    saving responsive without changing the visible size or timing of the video.
    A three-frame temporal median removes one-frame edge flips without averaging
    the subject into a visible trail.  Cloned boundary frames preserve the exact
    frame count and duration.
    """
    filters = _available_ffmpeg_filters()
    max_width = PREVIEW_TEMPORAL_MASK_MAX_WIDTH if preview else TEMPORAL_MASK_MAX_WIDTH
    mask_width, mask_height = _scaled_dimensions(width, height, max_width)
    steps = ["alphaextract", "format=gray"]

    if "tmedian" in filters and "tpad" in filters:
        # tmedian consumes one frame at each end for radius=1.  Cloning the
        # boundaries before it keeps the original frame count and timestamps.
        steps.extend([
            "tpad=start=1:start_mode=clone:stop=1:stop_mode=clone",
            "tmedian=radius=1:planes=1",
            "setpts=PTS-STARTPTS",
        ])
    elif "atadenoise" in filters:
        # Adaptive averaging is a safe fallback: it smooths only similar alpha
        # values and therefore avoids smearing clear subject motion.
        steps.append("atadenoise=0a=0.012:0b=0.024:s=5:p=1:a=s")

    if "median" in filters:
        steps.append("median=radius=1:radiusV=1:planes=1")
    if (mask_width, mask_height) != (width, height):
        steps.append(f"scale={width}:{height}:flags=bilinear")
    return mask_width, mask_height, ",".join(steps)


def _chroma_filter(width, height, options, analysis, preview=False):
    background = _background_filter(width, height, options.fit_mode)
    mask_width, mask_height, stable_mask = _temporal_mask_filter(width, height, preview)
    mask_scale = ""
    if (mask_width, mask_height) != (width, height):
        mask_scale = f"scale={mask_width}:{mask_height}:flags=area,"
    return (
        f"[1:v]setpts=PTS-STARTPTS,{background},setsar=1,format=yuva444p[background];"
        f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height}:flags=bicubic,setsar=1,format=yuva444p,"
        "split=2[foreground_color_source][foreground_mask_source];"
        "[foreground_color_source]despill=type=green:mix=0.15:expand=0.02[foreground_color];"
        f"[foreground_mask_source]{mask_scale}format=yuva444p,"
        f"chromakey={analysis.color_hex}:{analysis.similarity:.4f}:{analysis.blend:.4f},"
        f"{stable_mask}[foreground_mask];"
        "[foreground_color]format=rgba[foreground_base];"
        "[foreground_base][foreground_mask]alphamerge[foreground];"
        "[background][foreground]overlay=x=0:y=0:shortest=1:format=auto[v]"
    )


def _replace_background_command(
    source_path,
    options,
    analysis,
    output_path,
    copy_audio=True,
    source_start=0.0,
    source_duration=None,
    preview=False,
):
    source_width, source_height, fps = _source_profile(source_path)
    width, height = _scaled_dimensions(
        source_width,
        source_height,
        PREVIEW_MAX_WIDTH if preview else None,
    )
    media_duration = max(0.05, get_media_duration(source_path))
    source_start = max(0.0, min(float(source_start or 0.0), media_duration))
    available_duration = max(0.05, media_duration - source_start)
    if source_duration is None:
        duration = available_duration
    else:
        duration = max(0.05, min(float(source_duration or 0.0), available_duration))

    command = [_ffmpeg_binary(), "-y"]
    if source_start > 0.0005:
        command.extend(["-ss", f"{source_start:.6f}"])
    command.extend(["-i", source_path])
    if options.background_kind == "image":
        command.extend(["-loop", "1", "-framerate", f"{fps:g}", "-i", options.background_path])
    else:
        command.extend(["-stream_loop", "-1", "-i", options.background_path])
    command.extend([
        "-filter_complex",
        _chroma_filter(width, height, options, analysis, preview),
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast" if preview else "veryfast",
        "-crf",
        "23" if preview else "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        f"{fps:g}",
    ])
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend([
            "-af",
            "asetpts=PTS-STARTPTS",
            "-c:a",
            "aac",
            "-b:a",
            "160k" if preview else "320k",
        ])
    command.extend([
        "-t",
        f"{duration:.6f}",
        "-movflags",
        "+faststart",
        output_path,
    ])
    return command


def replace_chroma_background(
    source_path,
    options,
    output_path,
    analysis=None,
    progress_callback=None,
    cancelled_callback=None,
    source_start=0.0,
    source_duration=None,
    preview=False,
):
    validate_background(options)
    if analysis is None:
        analysis = analyze_video_chroma(
            source_path,
            cancelled_callback,
            start_time=source_start,
            duration=source_duration,
        )
    if _cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()

    # Preview clips are short and may begin between audio packet boundaries;
    # re-encoding their audio avoids timestamp problems and is faster to load.
    first_copy_audio = not preview
    try:
        run_ffmpeg_with_progress(
            _replace_background_command(
                source_path,
                options,
                analysis,
                output_path,
                first_copy_audio,
                source_start,
                source_duration,
                preview,
            ),
            source_path,
            output_path,
            tr("تعذر استبدال خلفية الفيديو"),
            progress_callback,
            cancelled_callback,
        )
    except AudioEffectPreparationCancelled:
        raise
    except RuntimeError:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        if not first_copy_audio:
            raise
        run_ffmpeg_with_progress(
            _replace_background_command(
                source_path,
                options,
                analysis,
                output_path,
                False,
                source_start,
                source_duration,
                preview,
            ),
            source_path,
            output_path,
            tr("تعذر استبدال خلفية الفيديو"),
            progress_callback,
            cancelled_callback,
        )
    return analysis


def _segment_can_be_read_directly(segment):
    speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
    volume = float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0)
    return (
        abs(speed - 1.0) <= 0.001
        and abs(volume - 1.0) <= 0.001
        and not str(getattr(segment, "audio_path", "") or "")
    )


def build_chroma_background_segment(timeline, options, progress_callback=None, cancelled_callback=None):
    timeline = list(timeline or [])
    temp_dir = tempfile.mkdtemp(prefix="chroma_background_")
    rendered_source_path = os.path.join(temp_dir, "source.mp4")
    output_path = os.path.join(temp_dir, "background_replaced.mp4")
    owns_source = False
    try:
        validate_background(options)
        if len(timeline) == 1 and is_full_segment(timeline[0]):
            source_path = timeline[0].path
            if progress_callback:
                progress_callback(8)
        else:
            source_path = rendered_source_path
            owns_source = True
            write_timeline_video(
                timeline,
                source_path,
                lambda percent: progress_callback(percent * 0.34) if progress_callback else None,
                cancelled_callback,
            )
        if _cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        if progress_callback:
            progress_callback(36)
        analysis = analyze_video_chroma(source_path, cancelled_callback)
        if progress_callback:
            progress_callback(42)
        replace_chroma_background(
            source_path,
            options,
            output_path,
            analysis,
            lambda percent: progress_callback(42 + percent * 0.58) if progress_callback else None,
            cancelled_callback,
        )
        if files_are_identical(source_path, output_path):
            raise RuntimeError(tr("لم يتم استبدال الخلفية في الملف الناتج"))
        if owns_source and os.path.exists(rendered_source_path):
            try:
                os.remove(rendered_source_path)
            except OSError:
                pass
        return output_path, temp_dir, get_media_duration(output_path)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _slice_timed_items(items, start_time, end_time):
    selected = []
    for item in items or []:
        item_start = float(item.get("start", 0) or 0)
        item_end = float(item.get("end", item_start) or item_start)
        overlap_start = max(item_start, start_time)
        overlap_end = min(item_end, end_time)
        if overlap_end <= overlap_start:
            continue
        updated = dict(item)
        speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        updated["start"] = overlap_start - start_time
        updated["end"] = overlap_end - start_time
        updated["source_offset"] = source_offset + max(0.0, overlap_start - item_start) * speed
        selected.append(updated)
    return selected


def build_chroma_preview(timeline, options, current_time=0.0, background_audio_items=None, cancelled_callback=None):
    timeline = list(timeline or [])
    full_duration = total_duration(timeline)
    if full_duration <= 0:
        raise RuntimeError(tr("تعذر تحديد مدة الفيديو"))
    preview_duration = min(PREVIEW_DURATION_SECONDS, full_duration)
    start_time = max(0.0, min(float(current_time or 0.0), max(0.0, full_duration - preview_duration)))
    end_time = min(full_duration, start_time + preview_duration)
    preview_timeline = slice_segments(timeline, start_time, end_time)
    preview_background_audio = _slice_timed_items(background_audio_items, start_time, end_time)
    temp_dir = tempfile.mkdtemp(prefix="chroma_preview_")
    rendered_source_path = os.path.join(temp_dir, "source.mp4")
    output_path = os.path.join(temp_dir, "preview.mp4")
    owns_source = False
    try:
        # The common case is one ordinary video segment with no mixed audio.
        # Read that range directly instead of rendering an intermediate preview
        # and then encoding it a second time.
        if (
            len(preview_timeline) == 1
            and _segment_can_be_read_directly(preview_timeline[0])
            and not preview_background_audio
        ):
            segment = preview_timeline[0]
            source_path = segment.path
            source_start = float(segment.start)
            source_duration = float(segment.duration)
        else:
            source_path = rendered_source_path
            source_start = 0.0
            source_duration = None
            owns_source = True
            write_timeline_video(
                preview_timeline,
                source_path,
                cancelled_callback=cancelled_callback,
                background_audio_items=preview_background_audio,
            )
        analysis = analyze_video_chroma(
            source_path,
            cancelled_callback,
            start_time=source_start,
            duration=source_duration,
        )
        replace_chroma_background(
            source_path,
            options,
            output_path,
            analysis,
            cancelled_callback=cancelled_callback,
            source_start=source_start,
            source_duration=source_duration,
            preview=True,
        )
        if owns_source and os.path.exists(rendered_source_path):
            try:
                os.remove(rendered_source_path)
            except OSError:
                pass
        return output_path, temp_dir, analysis
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
