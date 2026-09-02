# -*- coding: utf-8 -*-
import sys
import os
import time
import subprocess
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from video_maker.broadcasting.broadcast_manager import BroadcastManager
from video_maker.audio_devices import available_devices, INPUT_KIND
from video_maker.recording import ffmpeg_binary, get_visible_windows

REPORT_PATH = PROJECT_ROOT / "tests" / "broadcasting_tests" / "broadcast_test_report.md"

def log(msg, report_file, is_header=False):
    try:
        print(msg.encode('utf-8').decode('cp1256', errors='ignore'))
    except Exception:
        pass
    
    if is_header:
        report_file.write(f"\n### {msg}\n")
    else:
        report_file.write(f"- {msg}\n")

def get_dummy_file():
    video_path = PROJECT_ROOT / "tests" / "broadcasting_tests" / "dummy_test_video.mp4"
    if not video_path.exists():
        subprocess.run([
            ffmpeg_binary(), "-y", "-f", "lavfi", "-i", "testsrc=duration=5:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(video_path)

def test_combination(test_name, kwargs, report_file, manager):
    log(test_name, report_file, is_header=True)
    try:
        success = manager.start_broadcast(**kwargs)
        if not success:
            log("❌ فشل بدء البث (start_broadcast أرجع False).", report_file)
            return False
            
        log("✅ تم بدء البث بنجاح.", report_file)
        
        # Monitor for 5 seconds
        for i in range(5):
            time.sleep(1)
            # Check if internal threads crashed
            is_cam_alive = False
            if manager.cam and manager.cam.thread:
                is_cam_alive = manager.cam.thread.is_alive()
            elif manager.cam and manager.cam.process:
                # File mode might use process poll
                is_cam_alive = manager.cam.process is not None
                
            is_mixer_alive = False
            if manager.mixer and manager.mixer.thread:
                is_mixer_alive = manager.mixer.thread.is_alive()
            elif manager.mixer and manager.mixer.process:
                is_mixer_alive = manager.mixer.process is not None

            if not is_cam_alive and not is_mixer_alive:
                log("❌ توقفت عمليات البث بشكل غير متوقع (الانهيار الصامت).", report_file)
                manager.stop_broadcast()
                return False
                
        log("✅ استمر البث بنجاح لمدة 5 ثوانٍ دون انهيار.", report_file)
        manager.stop_broadcast()
        log("✅ تم إيقاف البث بشكل نظيف.", report_file)
        return True
    except Exception as e:
        log(f"❌ حدث خطأ برمجي أثناء الاختبار: {e}", report_file)
        manager.stop_broadcast()
        return False

def run_all_tests():
    with open(REPORT_PATH, 'w', encoding='utf-8') as report:
        report.write("# تقرير اختبارات البث المباشر العملي الشاملة\n")
        report.write("يهدف هذا التقرير للتحقق من سلامة كافة خيارات نافذة البث المباشر عملياً.\n")
        
        manager = BroadcastManager()
        
        # 1. Test File
        video_file = get_dummy_file()
        test_combination(
            "الاختبار الأول: بث ملف فيديو (صوت وصورة)",
            {"source_type": "file", "file_path": video_file},
            report, manager
        )
        
        # Find external mic
        mics = available_devices(INPUT_KIND)
        external_mic = mics[0].name if mics else None
        
        if external_mic:
            log(f"**معلومة:** تم العثور على ميكروفون خارجي للاختبار: {external_mic}", report, is_header=True)
        else:
            log("⚠️ لم يتم العثور على ميكروفون خارجي! سيتم تجاوز اختبارات الميكروفون.", report, is_header=True)
            
        # Find a valid window for App Audio
        windows = get_visible_windows()
        test_pid = None
        if windows:
            import ctypes
            hwnd, title, _ = windows[0]
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            test_pid = pid.value
        
        # 2. Screen + Internal
        test_combination(
            "الاختبار الثاني: بث الشاشة مع الصوت الداخلي فقط",
            {"source_type": "screen", "audio_source": "internal", "selected_apps": []},
            report, manager
        )
        
        # 3. Screen + External
        if external_mic:
            test_combination(
                "الاختبار الثالث: بث الشاشة مع الميكروفون الخارجي فقط",
                {"source_type": "screen", "audio_source": "external", "external_mic_name": external_mic, "selected_apps": []},
                report, manager
            )
            
        # 4. Screen + Both
        if external_mic:
            test_combination(
                "الاختبار الرابع: بث الشاشة مع الصوت الداخلي والميكروفون معاً",
                {"source_type": "screen", "audio_source": "both", "external_mic_name": external_mic, "selected_apps": []},
                report, manager
            )
            
        # 5. Screen + Specific App (Internal)
        if test_pid:
            test_combination(
                "الاختبار الخامس: بث الشاشة مع صوت تطبيق محدد",
                {"source_type": "screen", "audio_source": "internal", "selected_apps": [test_pid]},
                report, manager
            )
            
        report.write("\n---\n**اكتملت الاختبارات.**\n")
    print("All tests finished. Check broadcast_test_report.md")

if __name__ == "__main__":
    run_all_tests()
