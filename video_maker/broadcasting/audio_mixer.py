# -*- coding: utf-8 -*-
import threading
import time
import numpy as np

class AudioMixerManager:
    def __init__(self):
        self.virtual_mic_name_substring = "CABLE Input"
        self.mixer = None
        self.stream = None
        self.running = False
        self.thread = None
        self.process = None

    def _get_virtual_mic_id(self, sd):
        for index, device in enumerate(sd.query_devices()):
            if self.virtual_mic_name_substring in device.get("name", "") and device.get("max_output_channels", 0) > 0:
                return index
        return None

    def start_mixing(self, internal_audio_paths, external_mic_name=None):
        self.stop()
        
        try:
            import sounddevice as sd
            from video_maker.recording import AudioMixer, RecordingOptions
            from video_maker.audio_devices import available_devices, INPUT_KIND, DEFAULT_DEVICE_ID
            
            has_internal = False
            for path in internal_audio_paths:
                if path == "screen_audio" or str(path).startswith("\\\\.\\pipe\\") or path:
                    has_internal = True
            
            source_opt = "internal" if has_internal else "external"
            if has_internal and external_mic_name:
                source_opt = "both"
            elif external_mic_name:
                source_opt = "external"
            elif has_internal:
                source_opt = "internal"
            else:
                return None
                
            input_device_id = DEFAULT_DEVICE_ID
            if external_mic_name:
                for device in available_devices(INPUT_KIND):
                    if device.name == external_mic_name or device.label == external_mic_name:
                        input_device_id = device.id
                        break
                        
            options = RecordingOptions(
                mode="screen",
                source=source_opt,
                sample_rate=48000,
                channels=2,
                input_device_id=input_device_id
            )
            
            self.mixer = AudioMixer(options)
            self.mixer.start()
            
            virtual_mic_id = self._get_virtual_mic_id(sd)
            if virtual_mic_id is None:
                raise Exception(f"Virtual microphone '{self.virtual_mic_name_substring}' not found.")
                
            self.running = True
            self.thread = threading.Thread(target=self._mix_loop, args=(sd, virtual_mic_id), daemon=True)
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
            print(f"Error starting audio mixer: {e}")
            self.stop()
            return None

    def _mix_loop(self, sd, virtual_mic_id):
        try:
            with sd.OutputStream(
                samplerate=self.mixer.sample_rate,
                channels=self.mixer.options.channels,
                device=virtual_mic_id,
                blocksize=1024,
                latency='high',
                dtype='float32'
            ) as stream:
                while self.running:
                    data = self.mixer.read()
                    if data is not None and data.shape[0] > 0:
                        stream.write(data)
        except Exception as e:
            print(f"Audio Mixer Loop Error: {e}")
            self.running = False

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None
        
        if self.mixer:
            self.mixer.stop()
            self.mixer = None
        
        self.process = None
