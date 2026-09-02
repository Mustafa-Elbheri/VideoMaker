import wx
from video_maker.localization import tr

from video_maker.dialog_keys import bind_dialog_keys


FIELDS = [
    ("title", "اسم الملف"),
    ("artist", "الفنان"),
    ("album", "الألبوم"),
    ("genre", "النوع"),
    ("date", "التاريخ"),
    ("comment", "تعليق"),
]


class MetadataDialog(wx.Dialog):
    def __init__(self, parent, metadata):
        super().__init__(parent, title="المعلومات", size=(520, 420))
        self.metadata = dict(metadata or {})
        self.controls = {}
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        for key, label_text in FIELDS:
            label = wx.StaticText(panel, label=label_text)
            text = wx.TextCtrl(panel, value=self.metadata.get(key, ""))
            text.SetName(label_text)
            main_sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
            main_sizer.Add(text, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
            self.controls[key] = text
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, label="موافق")
        cancel_button = wx.Button(panel, label="إلغاء")
        ok_button.SetName(tr("موافق"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        button_sizer.Add(ok_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=8)
        panel.SetSizer(main_sizer)
        ok_button.Bind(wx.EVT_BUTTON, self.accept)
        cancel_button.Bind(wx.EVT_BUTTON, self.close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key)
        self.Centre()
        first = self.controls[FIELDS[0][0]]
        wx.CallAfter(first.SetFocus)

    def accept(self, event=None):
        self.metadata = {key: control.GetValue().strip() for key, control in self.controls.items() if control.GetValue().strip()}
        self.EndModal(wx.ID_OK)

    def close(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.close()
            return
        event.Skip()
