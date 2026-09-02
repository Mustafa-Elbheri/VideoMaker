import os
import subprocess
import uuid
from pathlib import Path

import wx

from video_maker.app_paths import ffmpeg_binary, imported_media_root, unique_path
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.logical_files import new_file_segment
from video_maker.timeline import insert_segments


def parse_silence_duration(value):
    text = str(value or "").strip().replace(",", ".")
    duration = float(text)
    if duration <= 0.0:
        raise ValueError("silence duration must be greater than zero")
    return duration


class SilenceDurationDialog(wx.Dialog):
    def __init__(self, parent, initial_value="1"):
        super().__init__(parent, title=tr("إدراج صمت"), size=(420, 170))

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(panel, label=tr("مدة الصمت بالثواني"))
        self.duration_ctrl = wx.TextCtrl(panel, value=str(initial_value), style=wx.TE_PROCESS_ENTER)
        self.duration_ctrl.SetName(tr("مدة الصمت بالثواني"))

        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("موافق"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(ok_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=14)
        main_sizer.Add(self.duration_ctrl, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=14)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=8)
        panel.SetSizer(main_sizer)

        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        ok_button.Bind(wx.EVT_BUTTON, self.on_ok)
        cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.duration_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_ok)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, (wx.TextCtrl,), preserve_navigation_keys=True)

        self.Centre()
        wx.CallAfter(self.duration_ctrl.SetFocus)

    def on_ok(self, event=None):
        try:
            parse_silence_duration(self.duration_ctrl.GetValue())
        except (TypeError, ValueError):
            wx.MessageBox(tr("اكتب مدة صمت صحيحة أكبر من صفر."), tr("قيمة غير صحيحة"), wx.OK | wx.ICON_ERROR)
            return
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.on_cancel()
            return
        event.Skip()

    def duration(self):
        return parse_silence_duration(self.duration_ctrl.GetValue())


def choose_silence_duration(parent=None):
    dialog = SilenceDurationDialog(parent)
    try:
        if dialog.ShowModal() != wx.ID_OK:
            return None
        return dialog.duration()
    finally:
        dialog.Destroy()


def create_silence_audio_file(duration, folder=None):
    duration = parse_silence_duration(duration)
    root = Path(folder) if folder is not None else imported_media_root()
    os.makedirs(root, exist_ok=True)
    path = unique_path(root, f"inserted_silence_{uuid.uuid4().hex[:8]}.wav")
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-t",
        f"{duration:.6f}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(error or tr("تعذر إنشاء الصمت"))
    return str(path)


def inserted_silence_timeline(timeline, silence_path, insert_time, duration):
    insert_time = max(0.0, float(insert_time or 0.0))
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0.0:
        return list(timeline or [])
    return insert_segments(list(timeline or []), insert_time, [new_file_segment(silence_path, 0.0, duration)])
