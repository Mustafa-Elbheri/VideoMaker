import os
import subprocess
import threading
import time

from video_maker.mpv_player import MPVMediaCtrl, MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED, EVT_MEDIA_LOADED, EVT_MEDIA_FINISHED
from video_maker.app_paths import ffmpeg_binary

from video_maker.audio_devices import selected_sounddevice_output_device
from video_maker.volume_boost import normalized_program_volume
from video_maker.video_editing import ffmpeg_startupinfo

try:
    import numpy as np
    import sounddevice as sd
    AUDIO_IMPORT_ERROR = ""
except Exception as error:
    np = None
    sd = None
    AUDIO_IMPORT_ERROR = str(error)


SAMPLE_RATE = 48000
CHANNELS = 2
DTYPE = "float32"
BYTES_PER_SAMPLE = 4
AUDIO_BLOCK_FRAMES = 512
PROCESS_EXIT_TIMEOUT = 0.20
AUDIO_THREAD_STOP_TIMEOUT = 0.75


def atempo_filter(speed):
    speed = max(0.05, min(100.0, float(speed or 1.0)))
    filters = []
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    filters.append(f"atempo={speed:.6f}")
    return ",".join(filters)


class ReliableAudioPlayer:
    def __init__(self):
        self.path = ""
        self.duration = 0.0
        self.position_ms = 0
        self.play_start_ms = 0
        self.play_start_time = 0.0
        self.volume = 1.0
        self.rate = 1.0
        self.limit_ms = None
        self.state = MEDIASTATE_STOPPED
        self.process = None
        self.stream = None
        self.thread = None
        self.stop_event = None
        self.command_factory = None
        self.block_frames = AUDIO_BLOCK_FRAMES
        self.stream_latency = "low"
        self.generation = 0
        self.lock = threading.RLock()
        self.last_error = ""

    def Load(self, path, duration=None):
        with self.lock:
            self.Stop()
            self.path = path
            self.command_factory = None
            self.block_frames = AUDIO_BLOCK_FRAMES
            self.stream_latency = "low"
            self.duration = max(0.0, float(duration or 0.0))
            self.position_ms = 0
            self.limit_ms = None
            return bool(self.duration > 0)

    def ConfigureCommandFactory(self, command_factory, seek_ms=0, end_ms=None, rate=1.0, volume=1.0, duration=None, block_frames=None, latency=None):
        with self.lock:
            self.Stop()
            self.path = ""
            self.command_factory = command_factory
            self.block_frames = max(128, int(block_frames or AUDIO_BLOCK_FRAMES))
            self.stream_latency = latency or "low"
            self.duration = max(0.0, float(duration or 0.0))
            self.rate = max(0.05, float(rate or 1.0))
            self.volume = normalized_program_volume(volume)
            self.limit_ms = None if end_ms is None else max(0, int(end_ms))
            if self.limit_ms is not None:
                self.duration = max(self.duration, self.limit_ms / 1000.0)
            self.Seek(seek_ms)
            return bool(self.duration > 0 and callable(self.command_factory))

    def Configure(self, path, seek_ms=0, end_ms=None, rate=1.0, volume=1.0, duration=None):
        with self.lock:
            if self.path != path:
                self.Load(path, duration)
            elif duration is not None:
                self.duration = max(self.duration, float(duration or 0.0))
            self.rate = max(0.05, float(rate or 1.0))
            self.volume = normalized_program_volume(volume)
            self.limit_ms = None if end_ms is None else max(0, int(end_ms))
            if self.limit_ms is not None:
                self.duration = max(self.duration, self.limit_ms / 1000.0)
            self.Seek(seek_ms)

    def Length(self):
        return int(self.duration * 1000)

    def Tell(self):
        with self.lock:
            if self.state == MEDIASTATE_PLAYING:
                elapsed = max(0.0, time.monotonic() - self.play_start_time)
                position = self.play_start_ms + int(elapsed * self.rate * 1000)
                if self.limit_ms is not None:
                    position = min(position, self.limit_ms)
                return min(position, self.Length())
            return min(self.position_ms, self.Length())

    def Seek(self, seek_ms):
        with self.lock:
            target = max(0, min(int(seek_ms or 0), self.Length()))
            if self.state == MEDIASTATE_PLAYING:
                current = self.Tell()
                if abs(current - target) > 300:
                    self.position_ms = target
                    self._stop_process_locked()
                    self._start_locked()
                else:
                    self.position_ms = target
            else:
                self.position_ms = target
            return True

    def SetVolume(self, volume):
        with self.lock:
            self.volume = normalized_program_volume(volume)
        return True

    def SetPlaybackRate(self, rate):
        with self.lock:
            rate = max(0.05, float(rate or 1.0))
            if abs(rate - self.rate) <= 0.01:
                return True
            was_playing = self.state == MEDIASTATE_PLAYING
            self.position_ms = self.Tell()
            self.rate = rate
            if was_playing:
                self._stop_process_locked()
                self._start_locked()
        return True

    def GetState(self):
        return self.state

    def IsPlaying(self):
        return self.GetState() == MEDIASTATE_PLAYING

    def Play(self):
        with self.lock:
            if sd is None or np is None:
                self.last_error = AUDIO_IMPORT_ERROR or "Reliable audio is not available"
                return False
            if (not self.path and not callable(self.command_factory)) or self.volume <= 0.001:
                self.state = MEDIASTATE_STOPPED
                return False
            if self.state == MEDIASTATE_PLAYING:
                return True
            return self._start_locked()

    def Pause(self):
        with self.lock:
            if self.state == MEDIASTATE_PLAYING:
                self.position_ms = self.Tell()
            self._stop_process_locked()
            self.state = MEDIASTATE_PAUSED
        return True

    def Stop(self, wait=False):
        with self.lock:
            thread = self.thread
            self._stop_process_locked()
            self.position_ms = 0
            self.state = MEDIASTATE_STOPPED
        if wait and thread and thread is not threading.current_thread():
            try:
                thread.join(timeout=AUDIO_THREAD_STOP_TIMEOUT)
            except Exception:
                pass
        return True

    def Destroy(self):
        self.Stop(wait=True)

    def _start_locked(self):
        if self.position_ms >= self.Length():
            self.state = MEDIASTATE_STOPPED
            return False
        self.last_error = ""
        self.generation += 1
        generation = self.generation
        start_seconds = max(0.0, self.position_ms / 1000.0)
        if callable(self.command_factory):
            remaining = None
            if self.limit_ms is not None:
                remaining = max(0.0, (self.limit_ms - self.position_ms) / 1000.0)
                if remaining <= 0:
                    self.state = MEDIASTATE_STOPPED
                    return False
            command = self.command_factory(start_seconds, remaining, self.rate)
            if not command:
                self.last_error = "لا يوجد صوت متاح في موضع المعاينة"
                self.state = MEDIASTATE_STOPPED
                return False
        else:
            command = [
                ffmpeg_binary(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_seconds:.3f}",
            ]
            if self.limit_ms is not None:
                remaining = max(0.0, (self.limit_ms - self.position_ms) / 1000.0)
                if remaining <= 0:
                    self.state = MEDIASTATE_STOPPED
                    return False
                command.extend(["-t", f"{remaining:.3f}"])
            command.extend([
                "-i",
                self.path,
                "-vn",
                "-af",
                f"{atempo_filter(self.rate)},aresample={SAMPLE_RATE}",
                "-f",
                "f32le",
                "-ac",
                str(CHANNELS),
                "-ar",
                str(SAMPLE_RATE),
                "pipe:1",
            ])
        self.play_start_ms = self.position_ms
        self.play_start_time = time.monotonic()
        self.state = MEDIASTATE_PLAYING
        stop_event = threading.Event()
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._audio_thread, args=(command, generation, stop_event), daemon=True)
        self.thread.start()
        return True

    def _stop_process_locked(self):
        """Request the audio worker to stop without touching PortAudio from the GUI thread."""
        self.generation += 1
        stop_event = self.stop_event
        self.stop_event = None
        if stop_event:
            stop_event.set()

        self.stream = None

        process = self.process
        self.process = None
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    @staticmethod
    def _reap_process(process):
        if process is None:
            return
        try:
            process.wait(timeout=PROCESS_EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.wait(timeout=PROCESS_EXIT_TIMEOUT)
            except Exception:
                pass
        except Exception:
            pass
        for pipe in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
            try:
                if pipe:
                    pipe.close()
            except Exception:
                pass

    def _audio_thread(self, command, generation, stop_event):
        process = None
        stream = None
        bytes_per_frame = CHANNELS * BYTES_PER_SAMPLE
        with self.lock:
            block_frames = max(128, int(self.block_frames or AUDIO_BLOCK_FRAMES))
            stream_latency = self.stream_latency or "low"
        chunk_size = block_frames * bytes_per_frame
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
            with self.lock:
                if generation != self.generation or stop_event.is_set():
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    return
                self.process = process
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=block_frames,
                latency=stream_latency,
                device=selected_sounddevice_output_device(sd),
            )
            with stream:
                with self.lock:
                    if generation != self.generation or stop_event.is_set():
                        return
                    self.stream = stream
                while process and not stop_event.is_set():
                    data = process.stdout.read(chunk_size) if process.stdout else b""
                    if not data or stop_event.is_set():
                        break
                    usable = len(data) - (len(data) % bytes_per_frame)
                    if usable <= 0:
                        continue
                    samples = np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, CHANNELS).copy()
                    with self.lock:
                        volume = self.volume
                    if volume <= 0.001:
                        samples.fill(0)
                    elif abs(volume - 1.0) > 0.001:
                        samples = np.clip(samples * volume, -1.0, 1.0)
                    with self.lock:
                        if generation != self.generation or stop_event.is_set() or self.stream is not stream:
                            break
                    stream.write(samples)
        except Exception as error:
            if not stop_event.is_set() and generation == self.generation:
                self.last_error = str(error)
        finally:
            with self.lock:
                if self.stream is stream:
                    self.stream = None
                if generation == self.generation and self.process is process:
                    self.position_ms = self.Tell()
                    self.process = None
                    if self.state == MEDIASTATE_PLAYING:
                        self.state = MEDIASTATE_STOPPED
                if self.thread is threading.current_thread():
                    self.thread = None
            self._reap_process(process)


def reliable_audio_available():
    if sd is None or np is None:
        return False
    try:
        selected_output = selected_sounddevice_output_device(sd)
        default_output = selected_output if selected_output is not None else sd.default.device[1]
        if default_output is None or default_output < 0:
            return False
        info = sd.query_devices(default_output)
        return int(info.get("max_output_channels", 0) or 0) > 0
    except Exception:
        return False
