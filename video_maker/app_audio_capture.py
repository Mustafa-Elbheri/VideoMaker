import queue
import threading
import time
import psutil

try:
    import numpy as np
except ImportError:
    np = None

import sys

def _load_process_audio_capture():
    try:
        from process_audio_capture import ProcessAudioCapture
        return ProcessAudioCapture
    except ImportError:
        pass

    if getattr(sys, "frozen", False):
        base_dirs = []
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            base_dirs.append(os.path.join(meipass, "process_audio_capture"))
            base_dirs.append(meipass)
        exec_dir = os.path.dirname(sys.executable)
        base_dirs.append(os.path.join(exec_dir, "app_files", "process_audio_capture"))
        base_dirs.append(os.path.join(exec_dir, "app_files"))

        for bdir in base_dirs:
            if bdir not in sys.path and os.path.isdir(bdir):
                sys.path.insert(0, bdir)

        try:
            from process_audio_capture import ProcessAudioCapture
            return ProcessAudioCapture
        except ImportError:
            pass

    return None


ProcessAudioCapture = _load_process_audio_capture()
if ProcessAudioCapture is not None:
    try:
        PROCESS_AUDIO_SUPPORTED = ProcessAudioCapture.is_supported()
    except Exception:
        PROCESS_AUDIO_SUPPORTED = False
else:
    PROCESS_AUDIO_SUPPORTED = False

class ProcessAudioReader:
    def __init__(self, pid, sample_rate, channels, source_name=""):
        self.pid = int(pid)
        self.sample_rate = int(sample_rate)
        self.output_channels = int(channels)
        self.source_name = source_name
        self.source = f"app_{self.pid}"
        
        self.queue = queue.SimpleQueue()
        self.pending_data = np.zeros((0, self.output_channels), dtype=np.float32) if np is not None else None
        
        self.capture_thread = None
        self.is_running = False
        self.actual_sample_rate = float(self.sample_rate)
        
        self.silence_mode = False
        self.stream_start_time = None
        self.frames_read = 0
        
        import time
        self.pipe_name = f"\\\\.\\pipe\\broadcast_audio_{self.pid}_{int(time.time()*1000)}"
        self.pipe = None
        self.capture_obj = None

    def start(self):
        if not PROCESS_AUDIO_SUPPORTED or ProcessAudioCapture is None:
            raise RuntimeError("process-audio-capture is not installed or supported on this system.")
            
        import win32pipe
        self.pipe = win32pipe.CreateNamedPipe(
            self.pipe_name,
            win32pipe.PIPE_ACCESS_INBOUND,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1, 65536, 65536,
            0,
            None
        )
        
        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def _capture_loop(self):
        import win32pipe, win32file, pywintypes
        try:
            self.capture_obj = ProcessAudioCapture(pid=self.pid, output_path=self.pipe_name)
            self.capture_obj.start()
            
            win32pipe.ConnectNamedPipe(self.pipe, None)
            
            while self.is_running:
                try:
                    hr, raw = win32file.ReadFile(self.pipe, 4096)
                    if raw and np is not None:
                        data = np.frombuffer(raw, dtype=np.float32)
                        if len(data) % 2 == 0:
                            data = data.reshape(-1, 2)
                        else:
                            continue
                        self.queue.put(self.normalize(data))
                except pywintypes.error as e:
                    if e.winerror == 109:  # ERROR_BROKEN_PIPE
                        break
                    time.sleep(0.01)
                    
        except Exception as e:
            print(f"Error capturing process {self.pid}: {e}")
        finally:
            if self.capture_obj:
                try:
                    self.capture_obj.stop()
                except Exception:
                    pass
            self.capture_obj = None

    def normalize(self, data):
        if data.shape[1] == self.output_channels:
            return data
        if self.output_channels == 1:
            return np.mean(data, axis=1, keepdims=True).astype(np.float32)
        if data.shape[1] == 1:
            return np.repeat(data, self.output_channels, axis=1).astype(np.float32)
        return data[:, :self.output_channels].astype(np.float32)

    def read(self, block_size=1024):
        if self.stream_start_time is None:
            self.stream_start_time = time.monotonic()
            self.frames_read = 0
            self.silence_mode = False

        while self.pending_data.shape[0] < block_size:
            target_time = self.stream_start_time + ((self.frames_read + block_size) / max(1.0, self.actual_sample_rate))
            remaining_time = target_time - time.monotonic()

            if remaining_time < -0.2:
                self.stream_start_time = time.monotonic() - ((self.frames_read + block_size) / max(1.0, self.actual_sample_rate))
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
                missing = block_size - self.pending_data.shape[0]
                data = np.zeros((missing, self.output_channels), dtype=np.float32)

            if data.shape[0]:
                if self.pending_data.shape[0]:
                    self.pending_data = np.concatenate((self.pending_data, data), axis=0)
                else:
                    self.pending_data = data

        result = self.pending_data[:block_size]
        self.pending_data = self.pending_data[block_size:]
        self.frames_read += block_size
        
        return result

    def stop(self):
        self.is_running = False
        if self.capture_obj:
            try:
                self.capture_obj.stop()
            except Exception:
                pass
            self.capture_obj = None
        if self.pipe:
            import win32file
            try:
                win32file.CloseHandle(self.pipe)
            except Exception:
                pass
            self.pipe = None
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=1.0)
