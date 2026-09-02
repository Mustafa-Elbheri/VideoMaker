import sys
import wx
import subprocess
import time
import os
import threading
from pathlib import Path

def start_window():
    app = wx.App(False)
    frame = wx.Frame(None, title=" نافذة اختبار الشاشة - المفكرة ")
    frame.SetSize((500, 400))
    
    panel = wx.Panel(frame)
    wx.StaticText(panel, label="هذه النافذة مخصصة لاختبار تصوير الشاشة (gdigrab) مع الأسماء العربية والمعقدة.", pos=(20, 20))
    
    frame.Show()
    app.MainLoop()

def test_recording():
    time.sleep(2.0)
    
    output_file = "test_output.mp4"
    if os.path.exists(output_file):
        os.remove(output_file)
        
    print("Testing capture with exact string (with spaces and Arabic)...")
    exact_title = " نافذة اختبار الشاشة - المفكرة "
    
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "gdigrab", "-i", f"title={exact_title}",
        "-c:v", "libx264", "-preset", "ultrafast", "-t", "3",
        output_file
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    
    try:
        proc = subprocess.Popen(cmd)
        proc.wait(timeout=15)
        
        if os.path.exists(output_file) and os.path.getsize(output_file) > 1000:
            print("\n[SUCCESS] ffmpeg successfully captured the window with the EXACT Arabic string (including spaces).")
            print(f"Output file size: {os.path.getsize(output_file)} bytes")
        else:
            print("\n[FAILED] ffmpeg could NOT capture the window. The output file is missing or 0 bytes.")
            
    except Exception as e:
        print(f"\n[ERROR] FFmpeg execution failed: {e}")
        
    finally:
        wx.CallAfter(wx.GetApp().ExitMainLoop)

if __name__ == "__main__":
    print("Starting Arabic Window Capture Test...")
    threading.Thread(target=test_recording, daemon=True).start()
    start_window()
