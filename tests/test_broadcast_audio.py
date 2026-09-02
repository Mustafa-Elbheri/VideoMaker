import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from video_maker.broadcasting.broadcast_manager import BroadcastManager
from video_maker.recording import available_devices, INPUT_KIND, get_visible_windows

def log(msg):
    print(msg)
    log_path = os.path.join(os.path.dirname(__file__), 'broadcast_tests.log')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

def run_tests():
    log_path = os.path.join(os.path.dirname(__file__), 'broadcast_tests.log')
    if os.path.exists(log_path):
        os.remove(log_path)
        
    log("Starting Broadcasting Audio Practical Tests...")
    
    manager = BroadcastManager()
    
    mics = available_devices(INPUT_KIND)
    external_mic_name = mics[0].name if mics else None
    log(f"Using external mic for tests: {external_mic_name}")
    
    windows = get_visible_windows()
    test_pid = None
    test_window_title = None
    if windows:
        import ctypes
        hwnd, exact_title, _ = windows[0]
        test_window_title = exact_title
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        test_pid = pid.value
        log(f"Using window for testing: {test_window_title} (PID: {test_pid})")
    
    tests = [
        {"name": "Test 1: Full Internal Audio Only", "kwargs": {"source_type": "screen", "audio_source": "internal", "selected_apps": []}},
        {"name": "Test 2: Specific App Audio Only", "kwargs": {"source_type": "screen", "audio_source": "internal", "selected_apps": [test_pid] if test_pid else []}},
        {"name": "Test 3: Full Internal + External Mic", "kwargs": {"source_type": "screen", "audio_source": "both", "selected_apps": [], "external_mic_name": external_mic_name}},
        {"name": "Test 4: Specific App Audio + External Mic", "kwargs": {"source_type": "screen", "audio_source": "both", "selected_apps": [test_pid] if test_pid else [], "external_mic_name": external_mic_name}},
        {"name": "Test 5: External Mic Only", "kwargs": {"source_type": "screen", "audio_source": "external", "selected_apps": [], "external_mic_name": external_mic_name}},
    ]
    
    for test in tests:
        log(f"\n--- Running {test['name']} ---")
        try:
            success = manager.start_broadcast(**test["kwargs"])
            log(f"Start broadcast returned: {success}")
            if success:
                log(f"Mixer running: {manager.mixer.process is not None}")
                if manager.app_audio_processes:
                    log(f"App Audio Processes started: {len(manager.app_audio_processes)}")
                if manager.pipe_names:
                    log(f"Pipes created: {manager.pipe_names}")
                
                log("Letting it run for 3 seconds...")
                time.sleep(3)
                
                manager.stop_broadcast()
                log("Broadcast stopped successfully.")
            else:
                log("Broadcast failed to start.")
        except Exception as e:
            log(f"Exception during test: {e}")
            
    log("\nAll tests completed.")

if __name__ == "__main__":
    run_tests()
