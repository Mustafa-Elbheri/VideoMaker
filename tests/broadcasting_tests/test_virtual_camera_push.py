# -*- coding: utf-8 -*-
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from video_maker.broadcasting.virtual_camera import VirtualCameraManager
import time

def test_push_to_virtual_cam():
    print("Testing pushing a dummy video to Virtual Camera...")
    print("Note: This will output to 'OBS Virtual Camera' if installed.")
    
    # Create a dummy video using ffmpeg
    dummy_video = "dummy_test.mp4"
    if not os.path.exists(dummy_video):
        os.system(f"ffmpeg -y -f lavfi -i testsrc=duration=5:size=640x480:rate=30 -c:v libx264 -pix_fmt yuv420p {dummy_video}")
        
    cam = VirtualCameraManager()
    proc = cam.start_pushing_video_file(dummy_video)
    
    print("Pushing to virtual camera for 4 seconds...")
    time.sleep(4)
    cam.stop()
    
    if os.path.exists(dummy_video):
        os.remove(dummy_video)
        
    print("SUCCESS: Virtual camera push test completed.")

if __name__ == '__main__':
    test_push_to_virtual_cam()
