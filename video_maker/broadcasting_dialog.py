import wx
import os
from video_maker.localization import tr
from video_maker.recording import get_visible_windows, available_devices, INPUT_KIND, selection_index, get_selected_device_id, AUDIO_SOURCE_CHOICES, SelectAppsDialog

class BroadcastSettingsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=tr("نافذة إعدادات البث"), size=(600, 450))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Source Selection (File or Screen)
        self.source_radio = wx.RadioBox(panel, label=tr("مصدر البث:"), choices=[tr("بث الشاشة الحالية"), tr("بث ملف مسجل")])
        self.source_radio.SetName(tr("مصدر البث:"))
        sizer.Add(self.source_radio, flag=wx.EXPAND | wx.ALL, border=12)
        
        # Screen Selection Options
        self.capture_scope_choice = self.add_choice(panel, sizer, tr("تحديد الشاشة"), [tr("كل الشاشة"), tr("اختر تطبيقاً:")], 1)
        self.available_windows = get_visible_windows()
        window_titles = [stripped for _hwnd, exact, stripped in self.available_windows]
        self.window_choice = self.add_choice(panel, sizer, "اختر تطبيقاً:", window_titles if window_titles else [tr("لا يوجد")], 0)
        self.window_choice.Enable(False)
        
        # File Selection Options
        self.file_picker_btn = wx.Button(panel, label=tr("اختر ملف البث"))
        self.file_picker_btn.SetName(tr("اختر ملف البث"))
        self.file_picker_btn.Show(False)
        self.selected_file_path = None
        sizer.Add(self.file_picker_btn, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        
        # Audio Selection
        self.source_choice = self.add_choice(panel, sizer, "إعدادات الصوت:", [tr(label) for _key, label in AUDIO_SOURCE_CHOICES], 0)
        
        self.select_apps_button = wx.Button(panel, label=tr("تحديد التطبيقات لالتقاط صوتها بشكل منفصل"))
        self.select_apps_button.SetName(tr("تحديد التطبيقات لالتقاط صوتها بشكل منفصل"))
        sizer.Add(self.select_apps_button, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        self.selected_apps = []
        
        self.input_audio_devices = [d for d in available_devices(INPUT_KIND) if 'CABLE Output' not in d.name and 'CABLE Output' not in d.label]
        self.mic_choice = self.add_choice(
            panel,
            sizer,
            "اختر الميكروفون:",
            [device.label for device in self.input_audio_devices],
            selection_index(self.input_audio_devices, get_selected_device_id(INPUT_KIND)),
        )
        self.update_microphone_choice()
        
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, tr("بدء البث"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("بدء البث"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        
        panel.SetSizer(sizer)
        
        # Bindings
        self.source_radio.Bind(wx.EVT_RADIOBOX, self.on_source_change)
        self.source_choice.Bind(wx.EVT_CHOICE, self.on_source_change_audio)
        self.select_apps_button.Bind(wx.EVT_BUTTON, self.on_select_apps)
        self.capture_scope_choice.Bind(wx.EVT_CHOICE, self.on_capture_scope_change)
        self.file_picker_btn.Bind(wx.EVT_BUTTON, self.on_pick_file)
        
    def add_choice(self, panel, sizer, label_text, choices, selection):
        label = wx.StaticText(panel, label=tr(label_text))
        choice = wx.Choice(panel, choices=choices)
        choice.SetName(tr(label_text))
        choice.SetSelection(selection)
        sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        sizer.Add(choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        return choice
        
    def on_source_change(self, event):
        is_screen = self.source_radio.GetSelection() == 0
        self.capture_scope_choice.Show(is_screen)
        self.window_choice.Show(is_screen and self.capture_scope_choice.GetSelection() == 1)
        self.file_picker_btn.Show(not is_screen)
        self.Layout()
        
    def on_source_change_audio(self, event):
        self.update_microphone_choice()

    def update_microphone_choice(self):
        source_index = max(0, self.source_choice.GetSelection())
        source = AUDIO_SOURCE_CHOICES[source_index][0]
        self.mic_choice.Enable(source in ("both", "external"))

    def on_select_apps(self, event):
        dialog = SelectAppsDialog(self, self.selected_apps)
        if dialog.ShowModal() == wx.ID_OK:
            self.selected_apps = dialog.get_selected_apps()
        dialog.Destroy()
        self.source_choice.SetFocus()

    def on_capture_scope_change(self, event):
        self.window_choice.Show(self.capture_scope_choice.GetSelection() == 1)
        self.Layout()
        
    def on_pick_file(self, event):
        with wx.FileDialog(self, tr("اختر الملف"), wildcard="Media files (*.mp4;*.mp3;*.wav;*.avi;*.mkv)|*.mp4;*.mp3;*.wav;*.avi;*.mkv|All files (*.*)|*.*",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return
            self.selected_file_path = fileDialog.GetPath()
            self.file_picker_btn.SetLabel(os.path.basename(self.selected_file_path))

    def get_options(self):
        is_screen = self.source_radio.GetSelection() == 0
        source_index = max(0, self.source_choice.GetSelection())
        audio_source = AUDIO_SOURCE_CHOICES[source_index][0]
        
        source_type = "screen" if is_screen else "file"
        file_path = self.selected_file_path if not is_screen else None
        
        window_title = None
        window_pid = None
        if is_screen and self.capture_scope_choice.GetSelection() == 1:
            idx = self.window_choice.GetSelection()
            if idx >= 0 and idx < len(self.available_windows):
                hwnd, exact_title, _ = self.available_windows[idx]
                window_title = exact_title
                import ctypes
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                window_pid = pid.value
                
        external_mic_name = None
        if audio_source in ("both", "external"):
            idx = self.mic_choice.GetSelection()
            if idx >= 0 and idx < len(self.input_audio_devices):
                external_mic_name = self.input_audio_devices[idx].name

        apps_to_record = list(self.selected_apps)
        if window_pid and window_pid not in apps_to_record:
            apps_to_record.append(window_pid)

        return {
            "source_type": source_type,
            "file_path": file_path,
            "window_title": window_title,
            "window_pid": window_pid,
            "audio_source": audio_source,
            "selected_apps": apps_to_record,
            "external_mic_name": external_mic_name
        }
