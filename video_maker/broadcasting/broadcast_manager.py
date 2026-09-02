# -*- coding: utf-8 -*-
from .virtual_camera import VirtualCameraManager
from .audio_mixer import AudioMixerManager
import os
import tempfile
import time
import subprocess
import threading

# We import the App Audio capture logic safely
try:
    from video_maker.app_audio_capture import ProcessAudioCapture, PROCESS_AUDIO_SUPPORTED
except ImportError:
    PROCESS_AUDIO_SUPPORTED = False
    ProcessAudioCapture = None

class BroadcastManager:
    def __init__(self):
        self.cam = VirtualCameraManager()
        self.mixer = AudioMixerManager()
        self.app_audio_processes = []
        self.app_audio_threads = []
        self.pipe_names = []

    def start_broadcast(self, source_type, file_path=None, window_title=None, window_pid=None, audio_source="internal", selected_apps=[], external_mic_name=None):
        """
        Starts the broadcasting engine by routing video to Virtual Camera and audio to VB-Cable.
        """
        try:
            self.stop_broadcast()
            mic_to_use = external_mic_name if audio_source in ("both", "external") else None

            if source_type == "file":
                if not file_path or not os.path.exists(file_path):
                    return False
                self.cam.start_pushing_video_file(file_path)
                self.mixer.start_mixing([file_path], mic_to_use)
                return True
                
            elif source_type == "screen":
                self.cam.start_pushing_screen(window_title)
                
                audio_inputs = []
                
                if audio_source in ("internal", "both"):
                    if selected_apps and PROCESS_AUDIO_SUPPORTED and ProcessAudioCapture is not None:
                        # Capture specific apps via pipes
                        import uuid
                        for pid in selected_apps:
                            pipe_id = str(uuid.uuid4().hex)
                            pipe_name = f"\\\\.\\pipe\\broadcast_audio_{pipe_id}"
                            self.pipe_names.append(pipe_name)
                            audio_inputs.append(pipe_name)
                    else:
                        # Capture all internal audio
                        audio_inputs.append("screen_audio")
                
                # Start ffmpeg mixer first
                self.mixer.start_mixing(audio_inputs, mic_to_use)
                
                # Now start the app capture processes if any pipes were created
                if self.pipe_names:
                    import threading
                    for i, pid in enumerate(selected_apps):
                        def capture_app_audio(p=pid, p_name=self.pipe_names[i]):
                            try:
                                capture = ProcessAudioCapture(pid=p, output_path=p_name)
                                capture.start()
                                self.app_audio_processes.append(capture)
                            except Exception as e:
                                print(f"App audio capture error for {p}: {e}")
                        
                        thread = threading.Thread(target=capture_app_audio, daemon=True)
                        self.app_audio_threads.append(thread)
                        thread.start()
                
                return True
                
            return False
        except Exception as e:
            print(f"Broadcast error: {e}")
            return False

    def stop_broadcast(self):
        self.cam.stop()
        self.mixer.stop()
        
        for proc in self.app_audio_processes:
            try:
                if hasattr(proc, "stop"):
                    proc.stop()
                elif hasattr(proc, "terminate"):
                    proc.terminate()
                elif hasattr(proc, "process") and proc.process:
                    proc.process.terminate()
            except Exception:
                pass
                
        self.app_audio_processes = []
        self.pipe_names = []
        
        for t in self.app_audio_threads:
            if t and t.is_alive():
                t.join(timeout=1)
        self.app_audio_threads = []

    @property
    def is_broadcasting(self):
        return self.cam.process is not None or self.mixer.process is not None
