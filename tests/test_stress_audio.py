import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from video_maker.broadcasting.broadcast_manager import BroadcastManager
from video_maker.recording import available_devices, INPUT_KIND, get_visible_windows

def log(msg):
    print(msg)
    log_path = os.path.join(os.path.dirname(__file__), 'broadcast_stress_tests.log')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

def run_tests():
    log_path = os.path.join(os.path.dirname(__file__), 'broadcast_stress_tests.log')
    if os.path.exists(log_path):
        os.remove(log_path)
        
    log("Starting Broadcasting Audio Stress & Integration Tests...")
    
    manager = BroadcastManager()
    
    mics = available_devices(INPUT_KIND)
    external_mic_name = mics[0].name if mics else None
    
    windows = get_visible_windows()
    pids_to_test = []
    if windows:
        import ctypes
        for w in windows[:3]:  # Get up to 3 windows
            hwnd, exact_title, _ = w
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids_to_test:
                pids_to_test.append(pid.value)
                
    log(f"Found {len(pids_to_test)} unique PIDs to stress test with: {pids_to_test}")
    
    # Test 1: Multiple Apps Filtergraph Stress Test
    log("\n--- Stress Test 1: Multiple Apps (amix filter dynamic scaling) ---")
    try:
        success = manager.start_broadcast(
            source_type="screen", 
            audio_source="both", 
            selected_apps=pids_to_test, 
            external_mic_name=external_mic_name
        )
        log(f"Start broadcast returned: {success}")
        if success:
            log(f"Mixer running: {manager.mixer.process is not None}")
            log(f"Number of audio pipes created: {len(manager.pipe_names)}")
            
            # Verify the FFmpeg filtergraph didn't crash immediately due to syntax errors
            time.sleep(2)
            mixer_alive = manager.mixer.process.poll() is None
            log(f"Mixer survived filtergraph initialization: {mixer_alive}")
            
            log("Stress test running for 5 seconds...")
            time.sleep(5)
            
            manager.stop_broadcast()
            log("Stopped successfully. Verifying cleanup...")
            
            # Verify cleanup
            cleaned_up = True
            if manager.app_audio_processes:
                cleaned_up = False
                log("WARNING: App audio processes list not cleared!")
            if manager.pipe_names:
                cleaned_up = False
                log("WARNING: Pipe names list not cleared!")
            if manager.app_audio_threads:
                cleaned_up = False
                log("WARNING: App audio threads list not cleared!")
                
            if cleaned_up:
                log("Cleanup verified: All threads and pipes properly destroyed without leaks.")
    except Exception as e:
        log(f"Exception during stress test: {e}")
        
    log("\nAll stress tests completed.")

if __name__ == "__main__":
    run_tests()
