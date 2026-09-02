import wx

from video_maker.dialog_keys import bind_dialog_keys


class SaveProgressDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        cancel_callback,
        title="\u062c\u0627\u0631\u064a \u062d\u0641\u0638 \u0627\u0644\u0641\u064a\u062f\u064a\u0648",
        progress_template="\u0646\u0633\u0628\u0629 \u0627\u0644\u062d\u0641\u0638 {percent} \u0628\u0627\u0644\u0645\u0626\u0629",
        status_name="\u062d\u0627\u0644\u0629 \u062d\u0641\u0638 \u0627\u0644\u0641\u064a\u062f\u064a\u0648",
        gauge_name="\u0634\u0631\u064a\u0637 \u062a\u0642\u062f\u0645 \u062d\u0641\u0638 \u0627\u0644\u0641\u064a\u062f\u064a\u0648",
        cancel_label="\u0625\u0644\u063a\u0627\u0621",
        cancel_name="\u0625\u0644\u063a\u0627\u0621 \u062d\u0641\u0638 \u0627\u0644\u0641\u064a\u062f\u064a\u0648",
        cancelling_message="\u062c\u0627\u0631\u064a \u0625\u0644\u063a\u0627\u0621 \u0627\u0644\u062d\u0641\u0638",
        show_cancel=True,
    ):
        super().__init__(parent, title=title, size=(460, 170))
        self.cancel_callback = cancel_callback
        self.progress_template = progress_template
        self.cancelling_message = cancelling_message
        self.status = wx.TextCtrl(self, value=self.progress_template.format(percent=0), style=wx.TE_READONLY | wx.BORDER_NONE)
        self.gauge = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL)
        self.cancel_button = wx.Button(self, label=cancel_label) if show_cancel else None

        self.status.SetName(status_name)
        self.gauge.SetName(gauge_name)
        if self.cancel_button:
            self.cancel_button.SetName(cancel_name)
            self.cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
            self.Bind(wx.EVT_CLOSE, self.on_cancel)
        else:
            self.Bind(wx.EVT_CLOSE, self.on_close_without_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, preserve_navigation_keys=True)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.status, flag=wx.EXPAND | wx.ALL, border=12)
        sizer.Add(self.gauge, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        if self.cancel_button:
            sizer.Add(self.cancel_button, flag=wx.ALIGN_CENTER | wx.ALL, border=8)
        self.SetSizer(sizer)
        self.Centre()
        wx.CallAfter(self.focus_navigation_controls)

    def focus_navigation_controls(self):
        try:
            if not self.IsShown():
                return
        except Exception:
            pass
        try:
            self.Raise()
        except Exception:
            pass
        focus_control = self.cancel_button if self.cancel_button else self.status
        try:
            focus_control.SetFocus()
        except Exception:
            try:
                self.SetFocus()
            except Exception:
                pass

    def update_progress(self, percent):
        percent = max(0, min(100, int(percent)))
        message = self.progress_template.format(percent=percent)
        self.gauge.SetValue(percent)
        self.status.SetValue(message)
        self.status.SetName(message)
        self.gauge.SetName(message)

    def on_cancel(self, event):
        if not self.cancel_callback:
            return
        self.cancel_callback()
        self.cancel_button.Disable()
        self.status.SetValue(self.cancelling_message)

    def on_close_without_cancel(self, event):
        if event.CanVeto():
            event.Veto()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            if self.cancel_callback:
                self.on_cancel(event)
            return
        event.Skip()
