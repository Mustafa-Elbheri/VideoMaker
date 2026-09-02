import os
import shutil
import tempfile

import wx
from video_maker.app_paths import ffmpeg_binary

from video_maker.audio_effects import run_ffmpeg_with_progress
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.timeline_transforms import timeline_has_video, write_selected_media
from video_maker.video_editing import get_media_duration


ROTATION_OPTIONS = [
    {
        "key": "right_90",
        "name": "تدوير 90 درجة لليمين",
        "description": "يجعل أعلى الصورة ناحية اليمين ومناسب لتحويل الفيديو الأفقي إلى عمودي",
        "filter": "transpose=1",
    },
    {
        "key": "left_90",
        "name": "تدوير 90 درجة لليسار",
        "description": "يجعل أعلى الصورة ناحية اليسار ومناسب لتحويل الفيديو الأفقي إلى عمودي في الاتجاه العكسي",
        "filter": "transpose=2",
    },
    {
        "key": "upside_down",
        "name": "تدوير 180 درجة",
        "description": "يقلب الفيديو رأسا على عقب عندما يكون التصوير مقلوبا بالكامل",
        "filter": "transpose=1,transpose=1",
    },
    {
        "key": "horizontal_flip",
        "name": "قلب أفقي",
        "description": "يعكس يمين ويسار الصورة مثل المرآة مع بقاء الأعلى والأسفل كما هما",
        "filter": "hflip",
    },
    {
        "key": "vertical_flip",
        "name": "قلب عمودي",
        "description": "يعكس أعلى وأسفل الصورة ويجعل الفيديو مقلوبا عموديا",
        "filter": "vflip",
    },
]


def rotation_option(key):
    return next((option for option in ROTATION_OPTIONS if option["key"] == key), ROTATION_OPTIONS[0])


def build_rotated_video_segment(timeline, start_time, end_time, rotation_key, progress_callback=None, cancelled_callback=None):
    if not timeline_has_video(timeline):
        raise RuntimeError("تدوير الفيديو يحتاج إلى ملف فيديو")
    option = rotation_option(rotation_key)
    temp_dir = tempfile.mkdtemp(prefix="timeline_rotation_")
    selected_path = os.path.join(temp_dir, "selected.mp4")
    output_path = os.path.join(temp_dir, "rotated.mp4")
    try:
        write_selected_media(timeline, start_time, end_time, selected_path, lambda percent: progress_callback(percent * 0.35) if progress_callback else None, cancelled_callback)
        command = [
            ffmpeg_binary(),
            "-y",
            "-i",
            selected_path,
            "-vf",
            option["filter"],
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            output_path,
        ]
        run_ffmpeg_with_progress(
            command,
            selected_path,
            output_path,
            "تعذر تدوير الفيديو",
            lambda percent: progress_callback(35 + percent * 0.65) if progress_callback else None,
            cancelled_callback,
        )
        return output_path, temp_dir, get_media_duration(output_path)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


class VideoRotationDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=tr("تدوير الفيديو"), size=(620, 240))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label=tr("طريقة تدوير الفيديو"))
        self.choice = wx.Choice(panel, choices=[f"{tr(option['name'])} {tr(option['description'])}" for option in ROTATION_OPTIONS])
        self.choice.SetName(tr("اختيار طريقة تدوير الفيديو"))
        self.choice.SetSelection(0)
        sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        sizer.Add(self.choice, flag=wx.EXPAND | wx.ALL, border=12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("تطبيق تدوير الفيديو"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        panel.SetSizer(sizer)
        cancel_button.Bind(wx.EVT_BUTTON, self.cancel_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.cancel_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Choice,))
        self.Centre()
        wx.CallAfter(self.choice.SetFocus)

    def rotation_key(self):
        selection = self.choice.GetSelection()
        if 0 <= selection < len(ROTATION_OPTIONS):
            return ROTATION_OPTIONS[selection]["key"]
        return ROTATION_OPTIONS[0]["key"]

    def cancel_dialog(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.cancel_dialog()
            return
        event.Skip()
