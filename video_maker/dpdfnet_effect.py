import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from video_maker.app_paths import ffmpeg_binary

from video_maker.app_paths import bundled_path
from video_maker.video_editing import get_media_duration, has_video_stream


DPDFNET_ENGINE = "dpdfnet_48khz"
DPDFNET_MODEL_NAME = "dpdfnet2_48khz_hr"
DPDFNET_SAMPLE_RATE = 48000
DPDFNET_PREVIEW_HOP = 480
DPDFNET_STREAM_DRY_DELAY_SAMPLES = DPDFNET_PREVIEW_HOP * 4
DPDFNET_CACHE_MAX_FILES = 8
_DPDFNET_CACHE_LOCK = threading.Lock()


def clamp(value, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = low
    return max(low, min(high, value))


def shaped_scale(value, low, high, curve=1.0):
    value = clamp(value, 0, 100) / 100.0
    if curve and abs(curve - 1.0) > 0.001:
        value = value ** curve
    return low + (high - low) * value


def dpdfnet_model_path():
    bundled = Path(bundled_path("assets", "dpdfnet", f"{DPDFNET_MODEL_NAME}.onnx"))
    if bundled.exists():
        return str(bundled)
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    cached = local_appdata / "dpdfnet" / "models" / f"{DPDFNET_MODEL_NAME}.onnx"
    if cached.exists():
        return str(cached)
    return ""


def dpdfnet_available():
    path = dpdfnet_model_path()
    if not path or not os.path.exists(path):
        return False
    return True


def dpdfnet_effect(values):
    return {"engine": DPDFNET_ENGINE, "values": dict(values or {})}


def is_dpdfnet_effect(audio_filter):
    return isinstance(audio_filter, dict) and audio_filter.get("engine") == DPDFNET_ENGINE


def dpdfnet_attenuation_limit_db(values):
    strength = clamp(values.get("attenuation", 78), 0, 100)
    if strength <= 0.01:
        return 0.0
    if strength >= 98.0:
        return None
    return shaped_scale(strength, 0.0, 36.0, 0.85)


def _to_mono(audio):
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim == 1:
        return x
    if x.ndim != 2:
        raise ValueError(f"Expected mono/stereo audio, got shape {x.shape}")
    return np.mean(x, axis=1, dtype=np.float32)


def _fit_length(audio, target_len):
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.shape[0] == target_len:
        return x
    if x.shape[0] > target_len:
        return x[:target_len]
    out = np.zeros(target_len, dtype=np.float32)
    out[: x.shape[0]] = x
    return out


def _vorbis_window(window_len):
    half = window_len / 2.0
    indices = np.arange(window_len)
    s = np.sin(0.5 * np.pi * (indices + 0.5) / half)
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


@dataclass(frozen=True)
class _RuntimeModel:
    session: ort.InferenceSession
    init_state: np.ndarray
    in_spec_name: str
    in_state_name: str
    out_spec_name: str
    out_state_name: str


def _create_cpu_session(onnx_path):
    path = Path(onnx_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"ONNX model file not found: {path}")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    if "CPUExecutionProvider" not in session.get_providers():
        raise RuntimeError("CPUExecutionProvider is not active for DPDFNet")
    return session


def _load_initial_state_from_metadata(session):
    meta = session.get_modelmeta().custom_metadata_map
    try:
        state_size = int(meta["state_size"])
        erb_norm_state_size = int(meta["erb_norm_state_size"])
        spec_norm_state_size = int(meta["spec_norm_state_size"])
        erb_norm_init = np.array([float(x) for x in meta["erb_norm_init"].split(",")], dtype=np.float32)
        spec_norm_init = np.array([float(x) for x in meta["spec_norm_init"].split(",")], dtype=np.float32)
    except KeyError as exc:
        raise ValueError(f"DPDFNet ONNX model is missing metadata key: {exc}") from exc
    state = np.zeros(state_size, dtype=np.float32)
    state[0:erb_norm_state_size] = erb_norm_init
    state[erb_norm_state_size:erb_norm_state_size + spec_norm_state_size] = spec_norm_init
    return np.ascontiguousarray(state)


def _build_runtime_model(onnx_path):
    session = _create_cpu_session(onnx_path)
    if len(session.get_inputs()) < 2 or len(session.get_outputs()) < 2:
        raise ValueError("Expected DPDFNet streaming ONNX signature with 2 inputs and 2 outputs")
    return _RuntimeModel(
        session=session,
        init_state=_load_initial_state_from_metadata(session),
        in_spec_name=session.get_inputs()[0].name,
        in_state_name=session.get_inputs()[1].name,
        out_spec_name=session.get_outputs()[0].name,
        out_state_name=session.get_outputs()[1].name,
    )


def _infer_win_len(session, default_sr=DPDFNET_SAMPLE_RATE):
    spec_shape = session.get_inputs()[0].shape
    freq_bins = spec_shape[-2] if len(spec_shape) >= 2 else None
    if isinstance(freq_bins, int) and freq_bins > 1:
        return int((freq_bins - 1) * 2)
    return int(round(default_sr * 0.02))


def _startupinfo():
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def _cancelled(cancelled_callback):
    return bool(cancelled_callback and cancelled_callback())


def _raise_cancelled(cancelled_exception):
    if cancelled_exception:
        raise cancelled_exception()
    raise RuntimeError("تم إلغاء تنقية الصوت الاحترافية")


def _run_ffmpeg(command, input_path, output_path, error_message, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    if _cancelled(cancelled_callback):
        _raise_cancelled(cancelled_exception)
    if not progress_callback and not cancelled_callback:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=_startupinfo())
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            message = result.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(message or error_message)
        return
    duration = max(0.001, get_media_duration(input_path))
    progress_command = [
        command[0],
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *command[1:],
    ]
    stderr_file = tempfile.TemporaryFile(mode="w+b")
    process = subprocess.Popen(
        progress_command,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        stdin=subprocess.DEVNULL,
        startupinfo=_startupinfo(),
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    last_percent = -1
    if progress_callback:
        progress_callback(0)
    try:
        if process.stdout:
            for line in process.stdout:
                if _cancelled(cancelled_callback):
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    _raise_cancelled(cancelled_exception)
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        seconds = int(value) / 1000000.0
                    except ValueError:
                        continue
                    percent = max(0, min(99, int(seconds * 100 / duration)))
                    if progress_callback and percent != last_percent:
                        last_percent = percent
                        progress_callback(percent)
                elif key == "progress" and value == "end" and progress_callback:
                    progress_callback(100)
        return_code = process.wait() if process.poll() is None else process.poll()
        stderr_file.seek(0)
        stderr = stderr_file.read().decode("utf-8", errors="ignore")
    finally:
        if process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass
        try:
            if process.stdout:
                process.stdout.close()
        except Exception:
            pass
        stderr_file.close()
    if return_code != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError((stderr or "").strip() or error_message)
    if progress_callback:
        progress_callback(100)


def _input_audio_channel_count(path):
    if not path or not os.path.exists(path):
        return 2
    if os.path.splitext(path)[1].lower() == ".wav":
        try:
            with wave.open(path, "rb") as wav:
                return max(1, min(2, int(wav.getnchannels() or 2)))
        except Exception:
            pass
    try:
        process = subprocess.run(
            [ffmpeg_binary(), "-hide_banner", "-i", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            startupinfo=_startupinfo(),
            timeout=20,
        )
        output = process.stderr.decode("utf-8", errors="ignore")
    except Exception:
        return 2
    audio_line = next((line for line in output.splitlines() if " Audio: " in line), "")
    lower_audio = audio_line.lower()
    if "mono" in lower_audio:
        return 1
    if "stereo" in lower_audio:
        return 2
    return 2


def _convert_to_dpdfnet_wav(input_path, wav_path, channel_count=2, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    channel_count = 1 if int(channel_count or 2) <= 1 else 2
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-vn",
        "-ar",
        str(DPDFNET_SAMPLE_RATE),
        "-ac",
        str(channel_count),
        "-c:a",
        "pcm_s16le",
        wav_path,
    ]
    _run_ffmpeg(command, input_path, wav_path, "تعذر تجهيز الصوت لتنقية الصوت الاحترافية", progress_callback, cancelled_callback, cancelled_exception)


def _dpdfnet_cache_dir():
    path = Path(tempfile.gettempdir()) / "video_maker_dpdfnet48_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clear_dpdfnet_cache_for_tests():
    cache_dir = _dpdfnet_cache_dir()
    with _DPDFNET_CACHE_LOCK:
        shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)


def _dpdfnet_core_key(input_path, values, channel_count):
    try:
        stat = os.stat(input_path)
        source_signature = (
            os.path.abspath(input_path),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )
    except OSError:
        source_signature = (os.path.abspath(input_path or ""), 0, 0)
    model_path = dpdfnet_model_path()
    try:
        model_stat = os.stat(model_path)
        model_signature = (os.path.abspath(model_path), int(model_stat.st_size), int(model_stat.st_mtime_ns))
    except OSError:
        model_signature = (os.path.abspath(model_path or ""), 0, 0)
    core_values = (
        int(channel_count),
        DPDFNET_MODEL_NAME,
        model_signature,
    )
    digest = hashlib.sha256(repr((source_signature, core_values)).encode("utf-8", errors="ignore")).hexdigest()
    return digest


def _cached_enhanced_path(cache_key):
    return str(_dpdfnet_cache_dir() / f"{cache_key}.wav")


def _evict_old_cache_files():
    cache_dir = _dpdfnet_cache_dir()
    files = sorted(cache_dir.glob("*.wav"), key=lambda item: item.stat().st_mtime, reverse=True)
    for item in files[DPDFNET_CACHE_MAX_FILES:]:
        try:
            item.unlink()
        except OSError:
            pass


def _post_filter(values):
    dry_mix = clamp(values.get("dry_mix", 12), 0, 70) / 100.0
    strength = clamp(values.get("attenuation", 78), 0, 100) / 100.0
    if strength <= 0.001:
        clean_gain = 0.0
        dry_gain = 1.0
    else:
        strength_blend = (1.0 - strength) * 0.35
        dry_gain = min(1.0, dry_mix + (1.0 - dry_mix) * strength_blend)
        clean_gain = 1.0 - dry_gain
    clarity = (clamp(values.get("presence", 38), 0, 100) - 50.0) / 50.0 * 4.0
    de_ess = clamp(values.get("de_ess", 24), 0, 100) / 100.0 * 5.5
    filters = []
    if dry_gain > 0.001:
        dry_delay_ms = int(round(DPDFNET_STREAM_DRY_DELAY_SAMPLES * 1000.0 / DPDFNET_SAMPLE_RATE))
        filters.append(
            f"[0:a]adelay=delays={dry_delay_ms}:all=1,volume={dry_gain:.4f}[dry];"
            f"[1:a]volume={clean_gain:.4f}[clean];"
            "[dry][clean]amix=inputs=2:duration=shortest:normalize=0[mix]"
        )
        current = "[mix]"
    else:
        current = "[1:a]"
    chain = []
    if abs(clarity) > 0.05:
        chain.append(f"equalizer=f=2800:t=q:w=1.0:g={clarity:.2f}")
    if de_ess > 0.05:
        chain.append(f"equalizer=f=6500:t=q:w=1.8:g=-{de_ess:.2f}")
    if values.get("limiter", True):
        chain.append("alimiter=limit=0.95")
    if not chain:
        chain.append("anull")
    filters.append(f"{current}{','.join(chain)}[out]")
    return ";".join(filters)


def _read_wav_float32(path):
    with wave.open(path, "rb") as wav:
        channels = max(1, int(wav.getnchannels() or 1))
        sample_rate = int(wav.getframerate() or DPDFNET_SAMPLE_RATE)
        sample_width = int(wav.getsampwidth() or 2)
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise RuntimeError("تنقية الصوت الاحترافية تحتاج صوت PCM 16-bit بعد التجهيز")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels)
    return samples, sample_rate


def _write_wav_float32(path, audio, sample_rate):
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim == 1:
        channels = 1
    else:
        channels = samples.shape[1]
    data = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(data.tobytes())


def _enhance_stream_array(audio, sample_rate, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    if int(sample_rate) != DPDFNET_SAMPLE_RATE:
        raise RuntimeError("تعذر تشغيل نموذج التنقية لأن الصوت لم يجهز على 48 kHz")
    enhancer = OnnxDpdfnetStreamEnhancer(onnx_path=dpdfnet_model_path())
    chunks = []
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    total = max(1, len(audio))
    for start in range(0, len(audio), DPDFNET_PREVIEW_HOP):
        if _cancelled(cancelled_callback):
            _raise_cancelled(cancelled_exception)
        chunk = audio[start:start + DPDFNET_PREVIEW_HOP]
        out = enhancer.process(chunk, sample_rate=sample_rate)
        if out.size:
            chunks.append(out)
        if progress_callback:
            progress_callback(max(0, min(100, start * 100 / total)))
    flushed = enhancer.flush()
    if flushed.size:
        chunks.append(flushed)
    if not chunks:
        return audio.copy()
    enhanced = np.concatenate(chunks)
    return _fit_length(enhanced, len(audio)).astype(np.float32, copy=False)


def _run_dpdfnet_uncached(input_wav, output_wav, values, channel_count=2, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    if not dpdfnet_available():
        raise RuntimeError("نموذج تنقية الصوت الاحترافية 48 kHz غير مثبت داخل البرنامج")
    if _cancelled(cancelled_callback):
        _raise_cancelled(cancelled_exception)

    source, sample_rate = _read_wav_float32(input_wav)
    if source.size == 0:
        raise RuntimeError("ملف الصوت فارغ")
    if source.ndim == 1:
        source = source.reshape(-1, 1)
    channel_count = min(source.shape[1], 2)
    source = source[:, :channel_count]
    enhanced_channels = []
    for channel_index in range(channel_count):
        if _cancelled(cancelled_callback):
            _raise_cancelled(cancelled_exception)
        enhanced = _enhance_stream_array(
            source[:, channel_index],
            sample_rate,
            (lambda percent, index=channel_index: progress_callback(
                max(0, min(100, ((index + percent / 100.0) / max(1, channel_count)) * 100))
            )) if progress_callback else None,
            cancelled_callback,
            cancelled_exception,
        )
        enhanced_channels.append(np.asarray(enhanced, dtype=np.float32))

    min_len = min(len(item) for item in enhanced_channels)
    enhanced_audio = np.column_stack([item[:min_len] for item in enhanced_channels])
    _write_wav_float32(output_wav, enhanced_audio, sample_rate)
    if progress_callback:
        progress_callback(100)
    return output_wav


def _run_dpdfnet(input_wav, output_dir, values, channel_count=2, cache_key=None, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    if cache_key:
        cached_path = _cached_enhanced_path(cache_key)
        with _DPDFNET_CACHE_LOCK:
            if os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                try:
                    os.utime(cached_path, None)
                except OSError:
                    pass
                if progress_callback:
                    progress_callback(100)
                return cached_path
    enhanced_wav = os.path.join(output_dir, "dpdfnet48_clean.wav")
    _run_dpdfnet_uncached(
        input_wav,
        enhanced_wav,
        values,
        channel_count,
        progress_callback,
        cancelled_callback,
        cancelled_exception,
    )
    if cache_key and enhanced_wav and os.path.exists(enhanced_wav):
        cached_path = _cached_enhanced_path(cache_key)
        with _DPDFNET_CACHE_LOCK:
            try:
                shutil.copy2(enhanced_wav, cached_path)
                _evict_old_cache_files()
                return cached_path
            except OSError:
                return enhanced_wav
    return enhanced_wav


def _render_post_chain(original_wav, enhanced_wav, output_wav, values, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        original_wav,
        "-i",
        enhanced_wav,
        "-filter_complex",
        _post_filter(values),
        "-map",
        "[out]",
        "-ar",
        str(DPDFNET_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        output_wav,
    ]
    _run_ffmpeg(command, original_wav, output_wav, "تعذر إنهاء تنقية الصوت الاحترافية", progress_callback, cancelled_callback, cancelled_exception)


def _mux_audio(input_path, audio_path, output_path, copy_video, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    video_options = ["-c:v", "copy"] if copy_video else ["-c:v", "libx264", "-preset", "medium", "-crf", "16"]
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *video_options,
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ]
    _run_ffmpeg(command, input_path, output_path, "تعذر دمج الصوت المنقى مع الفيديو", progress_callback, cancelled_callback, cancelled_exception)


def apply_dpdfnet_filter(input_path, output_path, audio_filter, progress_callback=None, cancelled_callback=None, cancelled_exception=None):
    if not is_dpdfnet_effect(audio_filter):
        raise RuntimeError("هذا ليس مؤثر تنقية الصوت الاحترافية")
    values = dict(audio_filter.get("values", {}))
    temp_dir = tempfile.mkdtemp(prefix="dpdfnet48_")
    source_wav = os.path.join(temp_dir, "source_48k.wav")
    enhanced_dir = os.path.join(temp_dir, "enhanced")
    processed_wav = os.path.join(temp_dir, "processed.wav")
    os.makedirs(enhanced_dir, exist_ok=True)
    try:
        channel_count = _input_audio_channel_count(input_path)
        cache_key = _dpdfnet_core_key(input_path, values, channel_count)
        _convert_to_dpdfnet_wav(
            input_path,
            source_wav,
            channel_count,
            (lambda percent: progress_callback(percent * 0.05)) if progress_callback else None,
            cancelled_callback,
            cancelled_exception,
        )
        enhanced_wav = _run_dpdfnet(
            source_wav,
            enhanced_dir,
            values,
            channel_count,
            cache_key,
            (lambda percent: progress_callback(5 + percent * 0.90)) if progress_callback else None,
            cancelled_callback,
            cancelled_exception,
        )
        _render_post_chain(
            source_wav,
            enhanced_wav,
            processed_wav,
            values,
            (lambda percent: progress_callback(95 + percent * 0.03)) if progress_callback else None,
            cancelled_callback,
            cancelled_exception,
        )
        if not has_video_stream(input_path):
            shutil.copy2(processed_wav, output_path)
            if progress_callback:
                progress_callback(100)
            return
        try:
            _mux_audio(
                input_path,
                processed_wav,
                output_path,
                True,
                (lambda percent: progress_callback(98 + percent * 0.02)) if progress_callback else None,
                cancelled_callback,
                cancelled_exception,
            )
        except RuntimeError:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            _mux_audio(
                input_path,
                processed_wav,
                output_path,
                False,
                (lambda percent: progress_callback(98 + percent * 0.02)) if progress_callback else None,
                cancelled_callback,
                cancelled_exception,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _peaking_eq_coefficients(sample_rate, frequency, q, gain_db):
    a = 10.0 ** (float(gain_db) / 40.0)
    omega = 2.0 * math.pi * float(frequency) / float(sample_rate)
    alpha = math.sin(omega) / (2.0 * float(q))
    cos_omega = math.cos(omega)
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_omega
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_omega
    a2 = 1.0 - alpha / a
    return (
        np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float32),
        np.array([1.0, a1 / a0, a2 / a0], dtype=np.float32),
    )


def _apply_biquad(audio, b, a, zi):
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    y = np.empty_like(x)
    z1, z2 = float(zi[0]), float(zi[1])
    b0, b1, b2 = float(b[0]), float(b[1]), float(b[2])
    a1, a2 = float(a[1]), float(a[2])
    for index, sample in enumerate(x):
        out = b0 * float(sample) + z1
        z1 = b1 * float(sample) - a1 * out + z2
        z2 = b2 * float(sample) - a2 * out
        y[index] = out
    return y, np.array([z1, z2], dtype=np.float32)


class OnnxDpdfnetStreamEnhancer:
    def __init__(self, onnx_path=None):
        self._runtime = _build_runtime_model(onnx_path or dpdfnet_model_path())
        self._model_sr = DPDFNET_SAMPLE_RATE
        self._win_len = _infer_win_len(self._runtime.session, self._model_sr)
        self._hop_size = self._win_len // 2
        self._window = _vorbis_window(self._win_len)
        self.reset()

    def reset(self):
        self._state = self._runtime.init_state.copy()
        self._in_buf = np.zeros(0, dtype=np.float32)
        self._out_buf = np.zeros(self._win_len, dtype=np.float32)
        self._input_sr = None

    def process(self, chunk, sample_rate=None):
        chunk = _to_mono(np.asarray(chunk, dtype=np.float32))
        if chunk.size == 0:
            return np.zeros(0, dtype=np.float32)
        sr_in = int(sample_rate if sample_rate is not None else self._model_sr)
        if sr_in != self._model_sr:
            raise RuntimeError("تعذر تشغيل نموذج التنقية لأن الصوت لم يجهز على 48 kHz")
        if self._input_sr is None:
            self._input_sr = sr_in
        elif self._input_sr != sr_in:
            raise ValueError("Sample rate changed during DPDFNet streaming")

        self._in_buf = np.concatenate([self._in_buf, chunk])
        output_frames = []
        while len(self._in_buf) >= self._win_len:
            windowed = self._in_buf[: self._win_len] * self._window
            spec_complex = np.fft.rfft(windowed, n=self._win_len)
            spec_ri = np.stack(
                [spec_complex.real.astype(np.float32), spec_complex.imag.astype(np.float32)],
                axis=-1,
            )
            spec_t = spec_ri[np.newaxis, np.newaxis, :, :]
            spec_e_t, self._state = self._runtime.session.run(
                [self._runtime.out_spec_name, self._runtime.out_state_name],
                {
                    self._runtime.in_spec_name: spec_t,
                    self._runtime.in_state_name: self._state,
                },
            )
            ri = spec_e_t[0, 0]
            complex_frame = ri[:, 0] + 1j * ri[:, 1]
            time_frame = (np.fft.irfft(complex_frame, n=self._win_len) * self._window).astype(np.float32)
            self._out_buf += time_frame
            committed = self._out_buf[: self._hop_size].copy()
            self._out_buf[: self._win_len - self._hop_size] = self._out_buf[self._hop_size :]
            self._out_buf[self._win_len - self._hop_size :] = 0.0
            output_frames.append(committed)
            self._in_buf = self._in_buf[self._hop_size :]
        if not output_frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(output_frames).astype(np.float32, copy=False)

    def flush(self):
        if self._in_buf.size == 0:
            return np.zeros(0, dtype=np.float32)
        remainder = len(self._in_buf)
        pad = np.zeros(self._win_len - remainder, dtype=np.float32)
        out = self.process(pad, sample_rate=self._model_sr)
        real_out = min(self._hop_size, len(out))
        return out[:real_out].astype(np.float32, copy=False) if len(out) > 0 else out


class DpdfnetRealtimeProcessor:
    def __init__(self, audio_filter, sample_rate=DPDFNET_SAMPLE_RATE):
        if not dpdfnet_available():
            raise RuntimeError("نموذج تنقية الصوت الاحترافية 48 kHz غير مثبت داخل البرنامج")

        self.values = dict(audio_filter.get("values", {}) if is_dpdfnet_effect(audio_filter) else {})
        self.sample_rate = int(sample_rate)
        self.enhancer = OnnxDpdfnetStreamEnhancer(onnx_path=dpdfnet_model_path())
        delay = int(round(DPDFNET_STREAM_DRY_DELAY_SAMPLES * self.sample_rate / DPDFNET_SAMPLE_RATE))
        self.dry_buffer = np.zeros(max(0, delay), dtype=np.float32)
        self.filters = self._build_realtime_filters()

    def _build_realtime_filters(self):
        filters = []
        presence = (clamp(self.values.get("presence", 38), 0, 100) - 50.0) / 50.0 * 4.0
        de_ess = clamp(self.values.get("de_ess", 24), 0, 100) / 100.0 * 5.5
        if abs(presence) > 0.05:
            filters.append((*_peaking_eq_coefficients(self.sample_rate, 2800, 1.0, presence), np.zeros(2, dtype=np.float32)))
        if de_ess > 0.05:
            filters.append((*_peaking_eq_coefficients(self.sample_rate, 6500, 1.8, -de_ess), np.zeros(2, dtype=np.float32)))
        return filters

    def _take_dry(self, count):
        count = int(max(0, count))
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        if len(self.dry_buffer) < count:
            dry = np.pad(self.dry_buffer, (0, count - len(self.dry_buffer)))
            self.dry_buffer = np.zeros(0, dtype=np.float32)
            return dry.astype(np.float32, copy=False)
        dry = self.dry_buffer[:count].copy()
        self.dry_buffer = self.dry_buffer[count:]
        return dry

    def _post_process(self, enhanced):
        enhanced = np.asarray(enhanced, dtype=np.float32)
        if enhanced.size == 0:
            return enhanced
        dry = self._take_dry(len(enhanced))
        strength = clamp(self.values.get("attenuation", 78), 0, 100) / 100.0
        dry_mix = clamp(self.values.get("dry_mix", 12), 0, 70) / 100.0
        if strength <= 0.001:
            clean_gain = 0.0
            dry_gain = 1.0
        else:
            preview_strength_blend = (1.0 - strength) * 0.35
            dry_gain = min(1.0, dry_mix + (1.0 - dry_mix) * preview_strength_blend)
            clean_gain = 1.0 - dry_gain
        mixed = dry * dry_gain + enhanced * clean_gain
        if self.filters:
            for index, (b, a, zi) in enumerate(self.filters):
                mixed, new_zi = _apply_biquad(mixed, b, a, zi)
                self.filters[index] = (b, a, new_zi)
        if self.values.get("limiter", True):
            mixed = np.clip(mixed, -0.95, 0.95)
        return mixed.astype(np.float32, copy=False)

    def process(self, chunk):
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return np.zeros(0, dtype=np.float32)
        self.dry_buffer = np.concatenate([self.dry_buffer, chunk])
        enhanced = self.enhancer.process(chunk, sample_rate=self.sample_rate)
        return self._post_process(enhanced)

    def flush(self):
        enhanced = self.enhancer.flush()
        return self._post_process(enhanced)
