# -*- coding: utf-8 -*-
import subprocess
import threading
from video_maker.recording import ffmpeg_binary

class VirtualCameraManager:
    def __init__(self):
        self.camera_name = "OBS Virtual Camera"
        self.process = None
        self.ffmpeg_proc = None
        self.running = False
        self.thread = None

    def _push_loop(self, width, height, fps):
        import pyvirtualcam
        import numpy as np
        
        frame_size = width * height * 3
        
        try:
            with pyvirtualcam.Camera(width=width, height=height, fps=fps) as cam:
                while self.running and self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
                    # Read 1 frame
                    raw_frame = self.ffmpeg_proc.stdout.read(frame_size)
                    if not raw_frame or len(raw_frame) != frame_size:
                        break
                    
                    frame = np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))
                    cam.send(frame)
                    cam.sleep_until_next_frame()
        except Exception as e:
            print(f"Virtual Camera Push Error: {e}")
        finally:
            self.running = False

    def start_pushing_video_file(self, video_path):
        self.stop()
        try:
            # We don't know the exact resolution beforehand easily, but let's assume 1280x720 30fps
            width, height, fps = 1280, 720, 30
            cmd = [
                ffmpeg_binary(),
                "-re",
                "-i", video_path,
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-s", f"{width}x{height}",
                "-r", str(fps),
                "-"
            ]
            
            self.ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.running = True
            self.thread = threading.Thread(target=self._push_loop, args=(width, height, fps), daemon=True)
            self.thread.start()
            
            class DummyProcess:
                def __init__(self, manager):
                    self.manager = manager
                def terminate(self_inner):
                    self_inner.manager.stop()
                def wait(self_inner, timeout=5):
                    pass
            self.process = DummyProcess(self)
            return self.process
        except Exception as e:
            print(f"Error starting virtual camera (file): {e}")
            self.stop()
            return None

    def start_pushing_screen(self, window_title=None):
        self.stop()
        try:
            # gdigrab usually captures screen resolution. We can scale it down or keep it fixed.
            # Let's fix it to 1280x720 30fps for stable virtual camera.
            width, height, fps = 1280, 720, 30
            input_target = "desktop" if not window_title else f"title={window_title}"
            
            cmd = [
                ffmpeg_binary(),
                "-f", "gdigrab",
                "-framerate", str(fps),
                "-i", input_target,
                "-vf", f"scale={width}:{height}",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-"
            ]
            
            self.ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.running = True
            self.thread = threading.Thread(target=self._push_loop, args=(width, height, fps), daemon=True)
            self.thread.start()
            
            class DummyProcess:
                def __init__(self, manager):
                    self.manager = manager
                def terminate(self_inner):
                    self_inner.manager.stop()
                def wait(self_inner, timeout=5):
                    pass
            self.process = DummyProcess(self)
            return self.process
            
        except Exception as e:
            print(f"Error starting virtual camera (screen): {e}")
            self.stop()
            return None

    def stop(self):
        self.running = False
        
        if self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.terminate()
                self.ffmpeg_proc.wait(timeout=2)
            except:
                pass
            self.ffmpeg_proc = None
            
        if self.thread and self.thread.is_alive():
            try:
                self.thread.join(timeout=2.0)
            except:
                pass
        self.thread = None
        self.process = None
