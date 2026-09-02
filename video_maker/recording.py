import os
import queue
import shutil
import subprocess
import ctypes
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime

import wx
from video_maker.app_paths import ffmpeg_binary

from video_maker.app_paths import user_data_path, unique_path
from video_maker.audio_devices import (
    DEFAULT_DEVICE_ID,
    INPUT_KIND,
    available_devices,
    get_selected_device_id,
    selected_sounddevice_input_device,
    selected_sounddevice_output_device,
    selection_index,
)
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.video_editing import ffmpeg_startupinfo

try:
    import numpy as np
    import sounddevice as sd
    RECORDING_IMPORT_ERROR = ""
except Exception as error:
    np = None
    sd = None
    RECORDING_IMPORT_ERROR = str(error)

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_IMPORT_ERROR = ""
except Exception as error:
    pyaudio = None
    PYAUDIO_IMPORT_ERROR = str(error)


AUDIO_SOURCE_CHOICES = [
    ("internal", "تسجيل الصوت الداخلي للحاسوب"),
    ("both", "تسجيل الصوت الداخلي مع الخارجي"),
    ("external", "تسجيل الصوت الخارجي فقط"),
]

AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "flac"]
VIDEO_EXTENSIONS = ["mp4", "mkv"]
SAMPLE_RATES = [44100, 48000]
BITRATES = ["96k", "128k", "192k", "256k", "320k"]
CHANNEL_CHOICES = [("stereo", "استريو", 2), ("mono", "مونو", 1)]
FRAME_RATES = [25, 30, 60]
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_BITRATE = "128k"
DEFAULT_CHANNEL_KEY = "stereo"
BLOCK_SIZE = 1024
WRITER_BUFFER_SIZE = 1024 * 1024
WRITER_FLUSH_INTERVAL_SECONDS = 2.0
RECORDING_STOP_TIMEOUT_SECONDS = 30


class RecordingError(RuntimeError):
    pass


@dataclass
class RecordingOptions:
    mode: str
    source: str = "external"
    extension: str = "mp3"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    bitrate: str = DEFAULT_BITRATE
    channels: int = 2
    frame_rate: int = 30
    input_device_id: str = DEFAULT_DEVICE_ID
    selected_apps: list = None
    capture_target: str = "desktop"


def recordings_root():
    path = user_data_path("recordings", "keep.txt").parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_root():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = recordings_root() / f"session_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_bitrate(value):
    text = str(value or DEFAULT_BITRATE).lower().strip()
    if text.endswith("kbps"):
        text = text[:-4].strip() + "k"
    if text.endswith("k"):
        return text
    digits = "".join(character for character in text if character.isdigit())
    return f"{digits or '128'}k"


def final_recording_path(mode, extension):
    extension = (extension or ("mp4" if mode == "screen" else "mp3")).lower().lstrip(".")
    base = "تسجيل شاشة" if mode == "screen" else "تسجيل صوت"
    stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    return unique_path(recordings_root(), f"{base} {stamp}.{extension}")


def ffmpeg_audio_command(input_path, output_path, options):
    extension = os.path.splitext(output_path)[1].lower()
    command = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", "-i", input_path]
    if extension == ".mp3":
        command.extend(["-c:a", "libmp3lame", "-b:a", clean_bitrate(options.bitrate)])
    elif extension == ".m4a":
        command.extend(["-c:a", "aac", "-b:a", clean_bitrate(options.bitrate)])
    elif extension == ".flac":
        command.extend(["-c:a", "flac"])
    else:
        command.extend(["-c:a", "pcm_s16le"])
    command.extend(["-ac", str(options.channels), "-ar", str(options.sample_rate), output_path])
    return command


def selected_app_pids(options):
    result = []
    for pid in getattr(options, "selected_apps", None) or []:
        try:
            value = int(pid)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in result:
            result.append(value)
    return result


def get_visible_windows():
    """Get visible windows."""
    if os.name != "nt":
        return []
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        windows = []
        current_pid = os.getpid()

        def enum_callback(hwnd, lParam):
            if user32.IsWindowVisible(hwnd):
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == current_pid:
                    return True
                
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    exact_title = buffer.value.rstrip('\x00')
                    stripped_title = exact_title.strip()
                    if stripped_title:
                        windows.append((hwnd, exact_title, stripped_title))
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return windows
    except Exception:
        return []


def ffmpeg_screen_segment_command(output_path, options):
    return [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "gdigrab",
        "-framerate",
        str(options.frame_rate),
        "-draw_mouse",
        "1" if getattr(options, "cursor", True) else "0",
        "-i",
        "desktop" if getattr(options, "capture_target", "desktop") == "desktop" else f"title={options.capture_target}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "setpts=PTS-STARTPTS,crop=trunc(iw/2)*2:trunc(ih/2)*2",
        "-f",
        "mp4",
        output_path,
    ]


def ffmpeg_wav_mix_command(input_paths, output_path, options):
    command = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error"]
    for path in input_paths:
        command.extend(["-i", path])
    if len(input_paths) > 1:
        inputs = "".join(f"[{index}:a]" for index in range(len(input_paths)))
        command.extend(
            [
                "-filter_complex",
                f"{inputs}amix=inputs={len(input_paths)}:duration=longest:dropout_transition=0",
            ]
        )
    command.extend(
        [
            "-c:a",
            "pcm_s16le",
            "-ac",
            str(options.channels),
            "-ar",
            str(options.sample_rate),
            output_path,
        ]
    )
    return command


def get_native_loopback_rate():
    if not pyaudio:
        return 48000
    try:
        p = pyaudio.PyAudio()
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
        p.terminate()
        return int(default_speakers['defaultSampleRate'])
    except Exception:
        return 48000


def concat_list_path(folder, paths):
    list_path = os.path.join(folder, "segments.txt")
    with open(list_path, "w", encoding="utf-8") as file:
        for path in paths:
            safe_path = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
            file.write(f"file '{safe_path}'\n")
    return list_path


def run_ffmpeg(command, error_text):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RecordingError(message or error_text)


def initialize_recording_thread():
    if os.name != "nt":
        return False
    try:
        ctypes.windll.ole32.CoInitialize(None)
        return True
    except Exception:
        return False


def uninitialize_recording_thread(initialized):
    if initialized and os.name == "nt":
        try:
            ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass


def input_devices_available():
    if sd is None:
        return False
    try:
        selected_input = selected_sounddevice_input_device(sd)
        default_input = selected_input if selected_input is not None else sd.default.device[0]
        if default_input is None or default_input < 0:
            return False
        info = sd.query_devices(default_input)
        return int(info.get("max_input_channels", 0) or 0) > 0
    except Exception:
        return False


def loopback_available():
    if pyaudio_loopback_available():
        return True
    if sd is None or not hasattr(sd, "WasapiSettings"):
        return find_system_audio_input_device() is not None
    try:
        default_output = sd.default.device[1]
        if default_output is None or default_output < 0:
            return find_system_audio_input_device() is not None
        info = sd.query_devices(default_output)
        if int(info.get("max_output_channels", 0) or 0) > 0:
            try:
                sd.WasapiSettings(loopback=True)
                return True
            except TypeError:
                return find_system_audio_input_device() is not None
        return find_system_audio_input_device() is not None
    except Exception:
        return find_system_audio_input_device() is not None


def find_system_audio_input_device():
    if sd is None:
        return None
    names = ("stereo mix", "what u hear", "wave out mix", "speaker mix", "loopback")
    try:
        devices = sd.query_devices()
        for index, info in enumerate(devices):
            name = str(info.get("name", "")).lower()
            if int(info.get("max_input_channels", 0) or 0) > 0 and any(token in name for token in names):
                return index
        for index, info in enumerate(devices):
            name = str(info.get("name", "")).lower()
            if int(info.get("max_input_channels", 0) or 0) > 0 and "output" in name:
                return index
    except Exception:
        return None
    return None


def pyaudio_loopback_available():
    if pyaudio is None:
        return False
    try:
        p = pyaudio.PyAudio()
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
        p.terminate()
        return True
    except Exception:
        return False


class AudioSourceReader:
    def __init__(self, source, sample_rate, channels, input_device_id=DEFAULT_DEVICE_ID):
        if np is None:
            raise RecordingError(RECORDING_IMPORT_ERROR or SOUNDCARD_IMPORT_ERROR or "تعذر تجهيز مكتبة التسجيل")
        if sd is None and not (source == "internal" and sc is not None):
            raise RecordingError(RECORDING_IMPORT_ERROR or "تعذر تجهيز مكتبة التسجيل")
        self.source = source
        self.input_device_id = str(input_device_id or DEFAULT_DEVICE_ID)
        self.sample_rate = int(sample_rate)
        self.output_channels = int(channels)
        # Audio callbacks must never discard captured frames merely because the
        # disk or encoder is momentarily slow.  The previous bounded queue
        # dropped the oldest blocks, shortening recordings on slower Windows
        # 10 systems and making speech sound accelerated.
        self.pending_data = np.zeros((0, channels), dtype=np.float32)
        self.queue = queue.SimpleQueue()
        self.stream = None
        self.recorder = None
        self.recorder_manager = None
        self.backend = "sounddevice"
        self.input_channels = self.output_channels
        self.actual_sample_rate = float(self.sample_rate)
        self.soundcard_started_at = None
        self.soundcard_frames_delivered = 0
        self.input_overflow_count = 0
        self.pending_data = np.empty((0, self.output_channels), dtype=np.float32)

    def start(self):
        if self.source == "internal" and selected_sounddevice_output_device(sd) is None and pyaudio_loopback_available():
            self.start_pyaudio_loopback()
            return
        device, channels, extra_settings = self.device_settings()
        self.input_channels = channels
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            # Let PortAudio choose the host-optimal callback block size.
            # Fixed small blocks can be fragile with older Windows drivers;
            # read() below normalizes the variable chunks for the mixer.
            blocksize=0,
            dtype="float32",
            channels=channels,
            device=device,
            callback=self.callback,
            extra_settings=extra_settings,
        )
        self.stream.start()
        try:
            rate = float(self.stream.samplerate)
            if rate > 0:
                self.actual_sample_rate = rate
        except Exception:
            self.actual_sample_rate = float(self.sample_rate)

    def start_pyaudio_loopback(self):
        if not pyaudio:
            raise RecordingError(f"PyAudio (loopback) is not installed: {PYAUDIO_IMPORT_ERROR}")
            
        self.pyaudio_instance = pyaudio.PyAudio()
        wasapi_info = self.pyaudio_instance.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = self.pyaudio_instance.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
        
        loopback_device = None
        if not default_speakers['isLoopbackDevice']:
            for loopback in self.pyaudio_instance.get_loopback_device_info_generator():
                if default_speakers['name'] in loopback['name']:
                    loopback_device = loopback
                    break
        else:
            loopback_device = default_speakers
            
        if not loopback_device:
            raise RecordingError("No loopback device found for default speakers")
            
        native_rate = int(loopback_device['defaultSampleRate'])
        self.backend = "pyaudio"
        self.actual_sample_rate = float(native_rate)
        
        def pyaudio_callback(in_data, frame_count, time_info, status):
            if in_data:
                data = np.frombuffer(in_data, dtype=np.float32)
                data = np.reshape(data, (frame_count, int(loopback_device['maxInputChannels'])))
                self.queue.put(self.normalize(data))
            return (in_data, pyaudio.paContinue)
            
        self.pyaudio_stream = self.pyaudio_instance.open(
            format=pyaudio.paFloat32,
            channels=int(loopback_device['maxInputChannels']),
            rate=native_rate,
            input=True,
            input_device_index=loopback_device['index'],
            stream_callback=pyaudio_callback,
            frames_per_buffer=BLOCK_SIZE
        )
        self.pyaudio_stream.start_stream()

    def device_settings(self):
        if self.source == "internal":
            selected_output = selected_sounddevice_output_device(sd)
            device = selected_output if selected_output is not None else sd.default.device[1]
            if device is not None and device >= 0 and hasattr(sd, "WasapiSettings"):
                info = sd.query_devices(device)
                if int(info.get("max_output_channels", 0) or 0) > 0:
                    try:
                        settings = sd.WasapiSettings(loopback=True)
                        channels = max(1, min(int(info.get("max_output_channels", 0) or 1), self.output_channels))
                        return device, channels, settings
                    except TypeError:
                        pass
            device = find_system_audio_input_device()
            if device is None:
                raise RecordingError("تعذر الوصول إلى الصوت الداخلي")
            info = sd.query_devices(device)
            channels = max(1, min(int(info.get("max_input_channels", 0) or 1), self.output_channels))
            return device, channels, None
        if self.input_device_id != DEFAULT_DEVICE_ID and self.input_device_id.isdigit():
            device = int(self.input_device_id)
        else:
            device = sd.default.device[0]
        if device is None or device < 0:
            raise RecordingError("تعذر الوصول إلى الميكروفون")
        info = sd.query_devices(device)
        if int(info.get("max_input_channels", 0) or 0) <= 0:
            raise RecordingError("تعذر الوصول إلى الميكروفون")
        channels = max(1, min(int(info.get("max_input_channels", 0) or 1), self.output_channels))
        return device, channels, None

    def callback(self, indata, frames, time_info, status):
        if status and getattr(status, "input_overflow", False):
            self.input_overflow_count += 1
        self.queue.put(self.normalize(indata.copy()))

    def normalize(self, data):
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if data.shape[1] == self.output_channels:
            return data.astype(np.float32, copy=False)
        if self.output_channels == 1:
            return np.mean(data, axis=1, keepdims=True).astype(np.float32)
        if data.shape[1] == 1:
            return np.repeat(data, self.output_channels, axis=1).astype(np.float32)
        return data[:, :self.output_channels].astype(np.float32)

    def read(self, timeout=None):
        if getattr(self, "stream_start_time", None) is None:
            self.stream_start_time = time.monotonic()
            self.frames_read = 0
            self.silence_mode = False

        while self.pending_data.shape[0] < BLOCK_SIZE:
            target_time = self.stream_start_time + ((self.frames_read + BLOCK_SIZE) / max(1.0, self.actual_sample_rate))
            remaining_time = target_time - time.monotonic()

            if remaining_time < -0.2:
                self.stream_start_time = time.monotonic() - ((self.frames_read + BLOCK_SIZE) / max(1.0, self.actual_sample_rate))
                remaining_time = 0.0
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except Exception:
                        pass

            try:
                timeout = max(0.01, remaining_time) if getattr(self, "silence_mode", False) else max(0.2, remaining_time)
                if timeout <= 0:
                    data = self.queue.get(block=False)
                else:
                    data = self.queue.get(timeout=timeout)
                self.silence_mode = False
            except queue.Empty:
                self.silence_mode = True
                missing = BLOCK_SIZE - self.pending_data.shape[0]
                data = np.zeros((missing, self.output_channels), dtype=np.float32)

            if data.shape[0]:
                if self.pending_data.shape[0]:
                    self.pending_data = np.concatenate((self.pending_data, data), axis=0)
                else:
                    self.pending_data = data

        result = self.pending_data[:BLOCK_SIZE]
        self.pending_data = self.pending_data[BLOCK_SIZE:]
        self.frames_read += BLOCK_SIZE
        
        return result

    def stop(self):
        if getattr(self, "backend", "") == "pyaudio":
            if hasattr(self, "pyaudio_stream") and self.pyaudio_stream:
                try:
                    self.pyaudio_stream.stop_stream()
                    self.pyaudio_stream.close()
                except Exception:
                    pass
                self.pyaudio_stream = None
            if hasattr(self, "pyaudio_instance") and self.pyaudio_instance:
                try:
                    self.pyaudio_instance.terminate()
                except Exception:
                    pass
                self.pyaudio_instance = None
        if self.stream:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class AudioMixer:
    def __init__(self, options):
        self.options = options
        self.readers = []
        self.sample_rate = float(options.sample_rate)

    def start(self):

        sources = []
        if self.options.source in ("internal", "both"):
            sources.append("internal")
        if self.options.source in ("external", "both"):
            sources.append("external")
            
        self.readers = []
        target_rate = self.options.sample_rate
        
        try:
            if "internal" in sources:
                target_rate = float(get_native_loopback_rate())
                
            readers_to_start = []
            
            if "external" in sources:
                external_reader = AudioSourceReader(
                    "external",
                    target_rate,
                    self.options.channels,
                    getattr(self.options, "input_device_id", DEFAULT_DEVICE_ID),
                )
                readers_to_start.append(external_reader)
                
            if "internal" in sources:
                internal_reader = AudioSourceReader("internal", target_rate, self.options.channels)
                readers_to_start.append(internal_reader)
                
            threads = []
            for r in readers_to_start:
                t = threading.Thread(target=r.start)
                t.start()
                threads.append(t)
                self.readers.append(r)
                
            for t in threads:
                t.join()
                
            self.sample_rate = target_rate
        except Exception:
            self.stop()
            raise

    def read(self):
        if not self.readers:
            return np.zeros((BLOCK_SIZE, self.options.channels), dtype=np.float32)
        frames = [reader.read() for reader in self.readers]
        length = min(frame.shape[0] for frame in frames)
        mixed = np.zeros((length, self.options.channels), dtype=np.float32)
        
        has_both = len(self.readers) > 1
        time_elapsed = getattr(self, "frames_mixed", 0) / max(1.0, self.sample_rate)
        
        for i, frame in enumerate(frames):
            data = frame[:length].copy()
            if self.readers[i].source == "internal":
                # Mute internal audio for 1.2s to hide NVDA announcement, with 100ms fade-in
                if time_elapsed < 1.2:
                    fade_start, fade_end = 1.1, 1.2
                    if time_elapsed >= fade_start:
                        block_times = time_elapsed + np.arange(length) / max(1.0, self.sample_rate)
                        fade = np.clip((block_times - fade_start) / (fade_end - fade_start), 0.0, 1.0)
                        data *= fade[:, None].astype(np.float32)
                    else:
                        data *= 0.0
                if has_both:
                    data *= 0.5
            else:
                if has_both:
                    data *= 0.8
            mixed += data
            
        self.frames_mixed = getattr(self, "frames_mixed", 0) + length
        return np.clip(mixed, -1.0, 1.0)

    def stop(self):
        for reader in self.readers:
            reader.stop()
        self.readers = []


class AudioSegmentWriter:
    def __init__(self, path, options, sample_rate=None):
        self.file_handle = open(path, "wb", buffering=WRITER_BUFFER_SIZE)
        self.writer = wave.open(self.file_handle, "wb")
        self.writer.setnchannels(options.channels)
        self.writer.setsampwidth(2)
        rate = float(sample_rate or options.sample_rate)
        self.writer.setframerate(max(1, int(round(rate))))
        self.last_flush_time = time.monotonic()

    def write(self, samples):
        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767.0).astype("<i2", copy=False)
        # writeframes() patches the WAV header after every tiny block.  Together
        # with fsync() this caused severe disk stalls on some Windows 10 PCs.
        # writeframesraw() streams the same PCM bytes and patches the header once
        # on close, preserving quality while keeping capture real-time.
        self.writer.writeframesraw(pcm.tobytes())
        now = time.monotonic()
        if now - self.last_flush_time >= WRITER_FLUSH_INTERVAL_SECONDS:
            try:
                self.file_handle.flush()
            except Exception:
                pass
            self.last_flush_time = now

    def close(self):
        try:
            self.writer.close()
        finally:
            try:
                self.file_handle.close()
            except Exception:
                pass


class BaseRecordingSession:
    def __init__(self, options):
        self.options = options
        self.folder = session_root()
        self.segments = []
        self.running = False
        self.paused = False
        self.thread = None
        self.stop_event = threading.Event()
        self.error = ""
        self.final_path = ""
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.paused = False
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def pause(self):
        with self.lock:
            if not self.running or self.paused:
                return False
            self.paused = True
            self.stop_event.set()
            thread = self.thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        return True

    def resume(self):
        with self.lock:
            if not self.running or not self.paused:
                return False
            self.paused = False
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            return True

    def stop(self):
        with self.lock:
            if not self.running:
                return ""
            self.running = False
            self.paused = False
            self.stop_event.set()
            thread = self.thread
        self._signal_stop()
        if thread and thread.is_alive():
            thread.join(timeout=RECORDING_STOP_TIMEOUT_SECONDS)
        if thread and thread.is_alive():
            # لا نبدأ دمج/ترميز ملف ما زال كاتبه يعمل؛ هذا يمنع ملفًا ناقصًا عند الخروج.
            raise RecordingError(tr("انتهت مهلة إيقاف التسجيل قبل حفظ الملف"))
        if self.error:
            raise RecordingError(self.error)
        return self.finalize()

    def _signal_stop(self):
        pass

    def segment_path(self, extension):
        index = len(self.segments) + 1
        return os.path.join(self.folder, f"part_{index:04d}.{extension}")

    def finalize(self):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError


class AudioRecordingSession(BaseRecordingSession):
    def run(self):
        from video_maker.app_audio_capture import PROCESS_AUDIO_SUPPORTED, ProcessAudioCapture

        if selected_app_pids(self.options) and self.options.source in ("internal", "both"):
            if PROCESS_AUDIO_SUPPORTED and ProcessAudioCapture is not None:
                try:
                    self.run_selected_app_audio()
                    return
                except Exception as error:
                    try:
                        from video_maker.problem_log import trace_event
                        trace_event("recording", "app_audio_failed", level="WARNING", error=str(error))
                    except Exception:
                        pass
            else:
                try:
                    from video_maker.problem_log import trace_event
                    trace_event("recording", "app_audio_unsupported", level="WARNING")
                except Exception:
                    pass

        self.run_standard_audio()

    def run_standard_audio(self):
        com_initialized = initialize_recording_thread()
        mixer = AudioMixer(self.options)
        writer = None
        segment_path = self.segment_path("wav")
        try:
            mixer.start()
            writer = AudioSegmentWriter(segment_path, self.options, mixer.sample_rate)
            start_time = time.monotonic()
            while self.running and not self.stop_event.is_set():
                writer.write(mixer.read())
                if time.monotonic() - start_time >= 30:
                    writer.close()
                    if os.path.exists(segment_path) and os.path.getsize(segment_path) > 44:
                        self.segments.append(segment_path)
                    segment_path = self.segment_path("wav")
                    writer = AudioSegmentWriter(segment_path, self.options, mixer.sample_rate)
                    start_time = time.monotonic()
            
            drain_start = time.monotonic()
            while (time.monotonic() - drain_start) < 0.5:
                writer.write(mixer.read())
                
            writer.close()
            writer = None
            if os.path.exists(segment_path) and os.path.getsize(segment_path) > 44:
                self.segments.append(segment_path)
        except Exception as error:
            self.error = str(error)
            if writer:
                try:
                    writer.close()
                except Exception:
                    pass
        finally:
            mixer.stop()
            uninitialize_recording_thread(com_initialized)

    def finalize(self):
        if not self.segments:
            raise RecordingError("لم يتم تسجيل أي صوت")
        self.final_path = str(final_recording_path("audio", self.options.extension))
        if len(self.segments) == 1:
            source = self.segments[0]
        else:
            source = os.path.join(self.folder, "joined.wav")
            list_path = concat_list_path(self.folder, self.segments)
            command = [
                ffmpeg_binary(),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c",
                "copy",
                source,
            ]
            run_ffmpeg(command, "تعذر دمج أجزاء التسجيل الصوتي")
        run_ffmpeg(ffmpeg_audio_command(source, self.final_path, self.options), "تعذر تجهيز ملف التسجيل الصوتي")
        return self.final_path

    def run_selected_app_audio(self):
        com_initialized = initialize_recording_thread()
        captures = []
        writer = None
        external_reader = None
        app_paths = []
        mic_path = ""
        segment_path = self.segment_path("wav")
        try:
            from video_maker.app_audio_capture import PROCESS_AUDIO_SUPPORTED, ProcessAudioCapture

            if not PROCESS_AUDIO_SUPPORTED or ProcessAudioCapture is None:
                raise RecordingError("تسجيل صوت تطبيق محدد غير مدعوم على هذا النظام أو مكتبة ProcessAudioCapture غير مثبتة")

            for pid in selected_app_pids(self.options):
                app_path = self.segment_path(f"app_{pid}.wav")
                capture = ProcessAudioCapture(pid=pid, output_path=app_path)
                capture.start()
                captures.append(capture)
                app_paths.append(app_path)

            if self.options.source == "both":
                external_reader = AudioSourceReader(
                    "external",
                    self.options.sample_rate,
                    self.options.channels,
                    getattr(self.options, "input_device_id", DEFAULT_DEVICE_ID),
                )
                external_reader.start()
                mic_path = self.segment_path("mic.wav")
                writer = AudioSegmentWriter(mic_path, self.options, external_reader.actual_sample_rate)

            if writer is None:
                while self.running and not self.stop_event.is_set():
                    time.sleep(0.05)
            else:
                while self.running and not self.stop_event.is_set():
                    writer.write(external_reader.read())
                drain_start = time.monotonic()
                while (time.monotonic() - drain_start) < 0.5:
                    writer.write(external_reader.read())
                writer.close()
                writer = None

            for capture in captures:
                capture.stop()
            captures = []

            input_paths = [path for path in app_paths if os.path.exists(path) and os.path.getsize(path) > 44]
            if mic_path and os.path.exists(mic_path) and os.path.getsize(mic_path) > 44:
                input_paths.append(mic_path)
            if not input_paths:
                raise RecordingError("لم يتم تسجيل أي صوت من التطبيقات المحددة")
            run_ffmpeg(ffmpeg_wav_mix_command(input_paths, segment_path, self.options), "تعذر تجهيز صوت التطبيقات المحددة")
            if os.path.exists(segment_path) and os.path.getsize(segment_path) > 44:
                self.segments.append(segment_path)
        except Exception as error:
            self.error = str(error)
            if writer:
                try:
                    writer.close()
                except Exception:
                    pass
        finally:
            if external_reader:
                external_reader.stop()
            for capture in captures:
                try:
                    capture.stop()
                except Exception:
                    pass
            uninitialize_recording_thread(com_initialized)


class ScreenRecordingSession(BaseRecordingSession):
    def __init__(self, options):
        super().__init__(options)
        self.process = None

    def _signal_stop(self):
        process = self.process
        if process and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
                    process.stdin.close()
            except Exception:
                pass

    def run(self):
        com_initialized = initialize_recording_thread()
        mixer = AudioMixer(self.options)
        process = None
        segment_path = self.segment_path("mkv")
        video_path = self.segment_path("mp4")
        audio_path = self.segment_path("wav")
        writer = AudioSegmentWriter(audio_path, self.options, mixer.sample_rate)
        
        # Inject 300ms of silence at the very beginning to sync audio perfectly 
        # with the screen capture hardware startup delay and preserve the first spoken word.
        silence_frames = int(mixer.sample_rate * 0.3)
        silence_padding = np.zeros((silence_frames, self.options.channels), dtype=np.float32)
        writer.write(silence_padding)
        
        try:
            mixer.start()
            process = subprocess.Popen(
                ffmpeg_screen_segment_command(video_path, self.options),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=ffmpeg_startupinfo(),
            )
            self.process = process
            while self.running and not self.stop_event.is_set() and process.poll() is None:
                writer.write(mixer.read())
                    
            if process.poll() is None:
                drain_start = time.monotonic()
                while process.poll() is None and (time.monotonic() - drain_start) < 0.5:
                    writer.write(mixer.read())
                    
            writer.close()
                        
            try:
                if process.stdin:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=10)
            except Exception:
                process.terminate()
            
            if os.path.exists(video_path) and os.path.getsize(video_path) > 0 and os.path.exists(audio_path):
                mux_command = [
                    ffmpeg_binary(),
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-i", video_path,
                    "-i", audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", clean_bitrate(self.options.bitrate),
                    segment_path
                ]
                run_ffmpeg(mux_command, "تعذر دمج الصوت والصورة")
                if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                    self.segments.append(segment_path)
                try:
                    os.remove(video_path)
                    os.remove(audio_path)
                except Exception:
                    pass
        except Exception as error:
            self.error = str(error)
            if process and process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
        finally:
            mixer.stop()
            self.process = None
            uninitialize_recording_thread(com_initialized)

    def pause(self):
        result = super().pause()
        process = self.process
        if process and process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
                    process.stdin.close()
            except Exception:
                pass
        return result

    def finalize(self):
        if not self.segments:
            raise RecordingError("لم يتم تسجيل أي فيديو")
        self.final_path = str(final_recording_path("screen", self.options.extension))
        list_path = concat_list_path(self.folder, self.segments)
        if self.options.extension == "mkv":
            output_path = self.final_path
        else:
            output_path = os.path.join(self.folder, "joined.mkv")
        copy_command = [
            ffmpeg_binary(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            output_path,
        ]
        try:
            run_ffmpeg(copy_command, "تعذر دمج أجزاء تسجيل الشاشة")
        except RecordingError:
            reencode_command = [
                ffmpeg_binary(),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                clean_bitrate(self.options.bitrate),
                output_path,
            ]
            run_ffmpeg(reencode_command, "تعذر دمج أجزاء تسجيل الشاشة")
        if self.options.extension == "mp4":
            remux_command = [
                ffmpeg_binary(),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                output_path,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                self.final_path,
            ]
            try:
                run_ffmpeg(remux_command, "تعذر تجهيز ملف تسجيل الشاشة")
            except RecordingError:
                transcode_command = [
                    ffmpeg_binary(),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    output_path,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    clean_bitrate(self.options.bitrate),
                    "-movflags",
                    "+faststart",
                    self.final_path,
                ]
                run_ffmpeg(transcode_command, "تعذر تجهيز ملف تسجيل الشاشة")
        return self.final_path


def make_recording_session(options):
    if options.mode == "screen":
        return ScreenRecordingSession(options)
    return AudioRecordingSession(options)



class SelectAppsDialog(wx.Dialog):
    def __init__(self, parent, selected_apps):
        super().__init__(parent, title=tr("اختيار البرامج"), size=(500, 400))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        label = wx.StaticText(panel, label=tr("حدد البرامج التي ترغب في تسجيل صوتها بالضغط على مسافة (Space)، وإذا لم تحدد شيئاً سيتم تسجيل النظام بأكمله:"))
        sizer.Add(label, 0, wx.ALL | wx.EXPAND, 12)
        
        try:
            from video_maker.app_audio_capture import get_available_applications
            self.apps = get_available_applications()
        except Exception:
            self.apps = []
            
        self.selected_pids = set(selected_apps) if selected_apps else set()
        
        self.list_box = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.list_box.SetName(tr("قائمة البرامج المتاحة للتسجيل"))
        
        self.refresh_list()
        
        sizer.Add(self.list_box, 1, wx.ALL | wx.EXPAND, 12)
        
        buttons = wx.StdDialogButtonSizer()
        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetDefault()
        ok_button.SetName(tr("موافق"))
        cancel_button.SetName(tr("إلغاء"))
        buttons.AddButton(ok_button)
        buttons.AddButton(cancel_button)
        buttons.Realize()
        
        sizer.Add(buttons, 0, wx.ALL | wx.EXPAND, 12)
        panel.SetSizer(sizer)
        self.Centre()
        
        self.list_box.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self.toggle_selection)
        
    def refresh_list(self):
        self.list_box.Clear()
        for pid, name in self.apps:
            state = tr("محدد") if pid in self.selected_pids else tr("غير محدد")
            self.list_box.Append(f"{name} ({state})")
            
    def toggle_selection(self, event=None):
        selection = self.list_box.GetSelection()
        if selection != wx.NOT_FOUND:
            pid = self.apps[selection][0]
            if pid in self.selected_pids:
                self.selected_pids.remove(pid)
            else:
                self.selected_pids.add(pid)
            
            name = self.apps[selection][1]
            state = tr("محدد") if pid in self.selected_pids else tr("غير محدد")
            self.list_box.SetString(selection, f"{name} ({state})")
            
    def on_key_down(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_SPACE, wx.WXK_RETURN):
            self.toggle_selection()
        else:
            event.Skip()
            
    def get_selected_apps(self):
        return list(self.selected_pids)


class RecordingSettingsDialog(wx.Dialog):
    def __init__(self, parent, mode):
        title = "تسجيل الشروحات المصورة" if mode == "screen" else "تسجيل الصوت"
        super().__init__(parent, title=tr(title), size=(620, 530 if mode == "screen" else 370))
        self.mode = mode
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        if mode == "screen":
            self.capture_scope_choice = self.add_choice(panel, sizer, "نطاق التصوير", [tr("تصوير الكل"), tr("مخصص")], 0)
            self.available_windows = get_visible_windows()
            window_titles = [stripped for _hwnd, exact, stripped in self.available_windows]
            self.window_choice = self.add_choice(panel, sizer, "اختيار النافذة", window_titles if window_titles else [tr("لا توجد نوافذ مفتوحة")], 0)
            self.window_choice.Enable(False)
            self.capture_scope_choice.Bind(wx.EVT_CHOICE, self.on_capture_scope_change)
        else:
            self.capture_scope_choice = None
            self.window_choice = None
            self.available_windows = []

        self.source_choice = self.add_choice(panel, sizer, "مصدر الصوت", [tr(label) for _key, label in AUDIO_SOURCE_CHOICES], 2)
        
        self.select_apps_button = wx.Button(panel, label=tr("اختيار البرامج التي يتم تسجيلها"))
        self.select_apps_button.SetName(tr("اختيار البرامج التي يتم تسجيلها"))
        sizer.Add(self.select_apps_button, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        self.select_apps_button.Bind(wx.EVT_BUTTON, self.on_select_apps)
        self.selected_apps = []
        
        self.input_audio_devices = available_devices(INPUT_KIND)
        self.input_device_choice = self.add_choice(
            panel,
            sizer,
            "الميكروفون لهذه الجلسة",
            [device.label for device in self.input_audio_devices],
            selection_index(self.input_audio_devices, get_selected_device_id(INPUT_KIND)),
        )
        extensions = VIDEO_EXTENSIONS if mode == "screen" else AUDIO_EXTENSIONS
        default_extension = "mp4" if mode == "screen" else "mp3"
        self.extension_choice = self.add_choice(panel, sizer, "امتداد التسجيل", extensions, extensions.index(default_extension))
        self.sample_rate_choice = self.add_choice(panel, sizer, "معدل العينة", [str(value) for value in SAMPLE_RATES], SAMPLE_RATES.index(DEFAULT_SAMPLE_RATE))
        self.bitrate_choice = self.add_choice(panel, sizer, "معدل البث", BITRATES, BITRATES.index(DEFAULT_BITRATE))
        self.channel_choice = self.add_choice(panel, sizer, "نوع القنوات", [tr(label) for _key, label, _channels in CHANNEL_CHOICES], 0)
        if mode == "screen":
            self.frame_rate_choice = self.add_choice(panel, sizer, "معدل إطارات الفيديو", [str(value) for value in FRAME_RATES], FRAME_RATES.index(30))
        else:
            self.frame_rate_choice = None
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_label = "تجهيز تسجيل الشاشة" if mode == "screen" else "بدء التسجيل"
        ok_button = wx.Button(panel, wx.ID_OK, tr(ok_label))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr(ok_label))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        panel.SetSizer(sizer)
        cancel_button.Bind(wx.EVT_BUTTON, self.cancel_dialog)
        self.source_choice.Bind(wx.EVT_CHOICE, self.on_source_change)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.cancel_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Choice,))
        self.Centre()
        self.update_microphone_choice()
        if mode == "screen" and self.capture_scope_choice:
            wx.CallAfter(self.capture_scope_choice.SetFocus)
        else:
            wx.CallAfter(self.source_choice.SetFocus)


    def add_choice(self, panel, sizer, label_text, choices, selection):
        label = wx.StaticText(panel, label=tr(label_text))
        choice = wx.Choice(panel, choices=choices)
        choice.SetName(tr(label_text))
        choice.SetSelection(selection)
        sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        sizer.Add(choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        return choice

    def options(self):
        source_index = max(0, self.source_choice.GetSelection())
        extension_index = max(0, self.extension_choice.GetSelection())
        sample_index = max(0, self.sample_rate_choice.GetSelection())
        bitrate_index = max(0, self.bitrate_choice.GetSelection())
        channel_index = max(0, self.channel_choice.GetSelection())
        extensions = VIDEO_EXTENSIONS if self.mode == "screen" else AUDIO_EXTENSIONS
        frame_rate = 30
        if self.frame_rate_choice:
            frame_index = max(0, self.frame_rate_choice.GetSelection())
            frame_rate = FRAME_RATES[frame_index]
        input_device_index = self.input_device_choice.GetSelection()
        input_device_id = DEFAULT_DEVICE_ID
        if input_device_index != wx.NOT_FOUND and input_device_index < len(self.input_audio_devices):
            input_device_id = self.input_audio_devices[input_device_index].id
        capture_target = "desktop"
        if self.capture_scope_choice and self.capture_scope_choice.GetSelection() == 1:
            if self.available_windows and self.window_choice:
                window_index = max(0, self.window_choice.GetSelection())
                if window_index < len(self.available_windows):
                    capture_target = self.available_windows[window_index][1]
        return RecordingOptions(
            mode=self.mode,
            source=AUDIO_SOURCE_CHOICES[source_index][0],
            extension=extensions[extension_index],
            sample_rate=SAMPLE_RATES[sample_index],
            bitrate=BITRATES[bitrate_index],
            channels=CHANNEL_CHOICES[channel_index][2],
            frame_rate=frame_rate,
            input_device_id=input_device_id,
            selected_apps=getattr(self, 'selected_apps', []),
            capture_target=capture_target,
        )

    def update_microphone_choice(self):
        source_index = max(0, self.source_choice.GetSelection())
        source = AUDIO_SOURCE_CHOICES[source_index][0]
        self.input_device_choice.Enable(source in ("external", "both"))
        if hasattr(self, 'select_apps_button'):
            self.select_apps_button.Enable(source in ("internal", "both"))

    def on_source_change(self, event):
        self.update_microphone_choice()
        event.Skip()

    def on_capture_scope_change(self, event):
        if self.window_choice:
            scope_index = max(0, self.capture_scope_choice.GetSelection())
            self.window_choice.Enable(scope_index == 1)
        event.Skip()

    def on_select_apps(self, event):
        dialog = SelectAppsDialog(self, self.selected_apps)
        if dialog.ShowModal() == wx.ID_OK:
            self.selected_apps = dialog.get_selected_apps()
        dialog.Destroy()
        self.source_choice.SetFocus()

    def cancel_dialog(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.cancel_dialog()
            return
        event.Skip()


def remove_session_folder(path):
    try:
        root = str(recordings_root().resolve())
        target = str(os.path.abspath(path))
        if target.startswith(root):
            shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass
