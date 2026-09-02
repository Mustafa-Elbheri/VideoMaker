# -*- coding: utf-8 -*-
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from video_maker.broadcasting.audio_mixer import AudioMixerManager
import time

def test_audio_mixing():
    print("Testing audio mixing to Virtual Microphone...")
    print("Note: This will output to 'CABLE Input (VB-Audio Virtual Cable)' if installed.")
    
    # Create dummy audio
    dummy_audio = "dummy_audio.mp3"
    if not os.path.exists(dummy_audio):
        os.system(f"ffmpeg -y -f lavfi -i sine=frequency=1000:duration=5 -c:a libmp3lame {dummy_audio}")
        
    mixer = AudioMixerManager()
    proc = mixer.start_mixing(dummy_audio, external_mic_name=None)
    
    print("Pushing to virtual mic for 4 seconds...")
    time.sleep(4)
    mixer.stop()
    
    if os.path.exists(dummy_audio):
        os.remove(dummy_audio)
        
    print("SUCCESS: Audio mixing test completed.")

if __name__ == '__main__':
    test_audio_mixing()
