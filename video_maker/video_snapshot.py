import os
import tempfile
import wx
import atexit
import shutil

_snapshot_temp_dirs = []

def cleanup_snapshots():
    for d in _snapshot_temp_dirs:
        try:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

atexit.register(cleanup_snapshots)

def copy_video_snapshot_to_clipboard(frame):
    if not getattr(frame, 'timeline', None) or not frame.timeline:
        return "لا يوجد ملف مفتوح"
    
    if getattr(frame, 'media_kind', None) == "audio":
        return "المشروع صوت وليس فديو"
        
    try:
        player = frame.media_ctrl._player
        if not player:
            return "لا يوجد ملف مفتوح"
    except AttributeError:
        return "لا يوجد ملف مفتوح"

    temp_dir = tempfile.mkdtemp(prefix="video_snapshot_")
    _snapshot_temp_dirs.append(temp_dir)
    temp_path = os.path.join(temp_dir, "snapshot.png")
    
    try:
        player.command("screenshot-to-file", temp_path, "video")
        
        if not os.path.exists(temp_path):
            return "لا يوجد ملف مفتوح"

        bitmap = wx.Bitmap(temp_path, wx.BITMAP_TYPE_PNG)
        if bitmap.IsOk():
            comp = wx.DataObjectComposite()
            
            file_obj = wx.FileDataObject()
            file_obj.AddFile(temp_path)
            comp.Add(file_obj)
            
            bitmap_obj = wx.BitmapDataObject(bitmap)
            comp.Add(bitmap_obj)

            if wx.TheClipboard.Open():
                try:
                    wx.TheClipboard.SetData(comp)
                    wx.TheClipboard.Flush()
                finally:
                    wx.TheClipboard.Close()
            return "تم نسخ الصورة للحافظة"
        else:
            return "لا يوجد ملف مفتوح"
    except Exception:
        return "لا يوجد ملف مفتوح"
