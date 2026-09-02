import os
import shutil
import webbrowser

import wx

from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.work_sessions import app_data_root
from video_maker.app_state import get_language, get_startup_sound, set_startup_sound
from video_maker.audio_devices import (
    INPUT_KIND,
    OUTPUT_KIND,
    available_devices,
    get_selected_device_id,
    selection_index,
    set_selected_device_id,
)
from video_maker.program_modes import (
    get_program_mode,
    program_mode_at,
    program_mode_index,
    program_mode_labels,
    set_program_mode,
)


def set_accessible_name(control, label):
    text = tr(label)
    control.SetName(text)
    if hasattr(control, "SetAccessibleName"):
        try:
            control.SetAccessibleName(text)
        except Exception:
            pass
    if hasattr(control, "SetHelpText"):
        try:
            control.SetHelpText(text)
        except Exception:
            pass


class ProgramSettingsDialog(wx.Dialog):
    def __init__(self, parent):
        self.language = get_language()
        super().__init__(
            parent,
            title=tr("إعدادات البرنامج"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        panel = wx.Panel(self)
        self.apply_layout_direction(self)
        self.apply_layout_direction(panel)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.notebook = wx.Notebook(panel)
        self.apply_layout_direction(self.notebook)
        set_accessible_name(self.notebook, tr("تبويبات إعدادات البرنامج"))

        self.general_page = self.create_page(self.notebook, "إعدادات عامة", placeholder=False)
        self.audio_devices_page = self.create_page(self.notebook, "إعدادات الصوت", placeholder=False)
        self.speech_page = self.create_page(self.notebook, "إعدادات النطق", placeholder=False)
        self.nav_sounds_page = self.create_page(self.notebook, "أصوات التنقل", placeholder=False)
        self.create_general_settings()
        self.create_audio_device_settings()
        self.create_speech_settings()
        self.create_nav_sounds_settings()
        self.load_settings()
        self.notebook.AddPage(self.general_page, tr("عام"))
        self.notebook.AddPage(self.audio_devices_page, tr("أجهزة الصوت"))
        self.notebook.AddPage(self.speech_page, tr("النطق"))
        self.notebook.AddPage(self.nav_sounds_page, tr("أصوات التنقل"))

        button_sizer = wx.StdDialogButtonSizer()
        self.ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        self.cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        set_accessible_name(self.ok_button, tr("موافق"))
        set_accessible_name(self.cancel_button, tr("إلغاء"))
        button_sizer.AddButton(self.ok_button)
        button_sizer.AddButton(self.cancel_button)
        button_sizer.Realize()

        main_sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(button_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        panel.SetSizer(main_sizer)

        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        outer_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer_sizer)
        self.SetMinSize((520, 360))
        self.SetSize((620, 420))
        self.CentreOnParent()

        bind_dialog_keys(self, self.on_key, preserve_navigation_keys=True)
        self.notebook.Bind(wx.EVT_CHAR_HOOK, self.on_notebook_key)
        self.ok_button.Bind(wx.EVT_BUTTON, self.on_ok)
        self.cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        wx.CallAfter(self.focus_notebook_if_alive)

    def apply_layout_direction(self, window):
        direction = wx.Layout_RightToLeft if self.language == "ar" else wx.Layout_LeftToRight
        try:
            window.SetLayoutDirection(direction)
        except Exception:
            pass

    def focus_notebook_if_alive(self):
        try:
            if not self.notebook.IsBeingDeleted():
                self.notebook.SetFocus()
        except (AttributeError, RuntimeError):
            pass

    def create_page(self, parent, accessible_name, placeholder=True):
        page = wx.Panel(parent)
        self.apply_layout_direction(page)
        set_accessible_name(page, accessible_name)
        sizer = wx.BoxSizer(wx.VERTICAL)
        if placeholder:
            placeholder_text = wx.StaticText(page, label=tr("سيتم إضافة الإعدادات هنا"))
            set_accessible_name(placeholder_text, accessible_name)
            sizer.Add(placeholder_text, 0, wx.ALL | wx.EXPAND, 12)
        page.SetSizer(sizer)
        return page

    def create_general_settings(self):
        sizer = self.general_page.GetSizer()

        action_label = wx.StaticText(self.general_page, label=tr("عند بدء التشغيل"))
        set_accessible_name(action_label, tr("عند بدء التشغيل"))
        self.startup_action_choice = wx.Choice(self.general_page, choices=[
            tr("فتح مشروع جديد فارغ"),
            tr("استعادة آخر جلسة عمل غير محفوظة"),
            tr("فتح آخر مشروع تم حفظه يدوياً")
        ])
        set_accessible_name(self.startup_action_choice, tr("عند بدء التشغيل"))
        sizer.Insert(0, self.startup_action_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        sizer.Insert(0, action_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        
        label = wx.StaticText(self.general_page, label=tr("وضع البرنامج"))
        set_accessible_name(label, tr("وضع البرنامج"))
        self.program_mode_choice = wx.Choice(self.general_page, choices=program_mode_labels())
        set_accessible_name(self.program_mode_choice, tr("وضع البرنامج"))
        
        sound_label = wx.StaticText(self.general_page, label=tr("صوت بدء التشغيل"))
        set_accessible_name(sound_label, tr("صوت بدء التشغيل"))
        self.startup_sound_choice = wx.Choice(self.general_page, choices=[tr("تشغيل"), tr("إيقاف"), tr("تغيير")])
        set_accessible_name(self.startup_sound_choice, tr("صوت بدء التشغيل"))
        self.startup_sound_browse = wx.Button(self.general_page, label=tr("تصفح..."))
        set_accessible_name(self.startup_sound_browse, tr("اختيار ملف صوت بدء التشغيل بصيغة wav فقط"))
        self.startup_sound_browse.SetToolTip(tr("اختيار ملف صوت بدء التشغيل بصيغة wav فقط"))
        self.startup_sound_browse.Hide()

        sound_sizer = wx.BoxSizer(wx.HORIZONTAL)
        if self.language == "ar":
            sound_sizer.Add(self.startup_sound_choice, 1, wx.EXPAND | wx.LEFT, 10)
            sound_sizer.Add(self.startup_sound_browse, 0)
        else:
            sound_sizer.Add(self.startup_sound_choice, 1, wx.EXPAND | wx.RIGHT, 10)
            sound_sizer.Add(self.startup_sound_browse, 0)

        sizer.Insert(0, sound_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        sizer.Insert(0, sound_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        
        sizer.Insert(0, self.program_mode_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        sizer.Insert(0, label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)

        self.startup_sound_choice.Bind(wx.EVT_CHOICE, self.on_startup_sound_change)
        self.startup_sound_browse.Bind(wx.EVT_BUTTON, self.on_startup_sound_browse)
        self.startup_sound_browse.Bind(wx.EVT_SET_FOCUS, self.on_browse_focus)

    def create_audio_device_settings(self):
        sizer = self.audio_devices_page.GetSizer()

        output_label = wx.StaticText(self.audio_devices_page, label=tr("السماعة الافتراضية"))
        set_accessible_name(output_label, tr("السماعة الافتراضية"))
        self.output_audio_devices = available_devices(OUTPUT_KIND)
        self.output_audio_choice = wx.Choice(self.audio_devices_page, choices=[device.label for device in self.output_audio_devices])
        set_accessible_name(self.output_audio_choice, tr("السماعة الافتراضية"))

        input_label = wx.StaticText(self.audio_devices_page, label=tr("الميكروفون الافتراضي"))
        set_accessible_name(input_label, tr("الميكروفون الافتراضي"))
        self.input_audio_devices = available_devices(INPUT_KIND)
        self.input_audio_choice = wx.Choice(self.audio_devices_page, choices=[device.label for device in self.input_audio_devices])
        set_accessible_name(self.input_audio_choice, tr("الميكروفون الافتراضي"))

        sizer.Add(output_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        sizer.Add(self.output_audio_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        sizer.Add(input_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        sizer.Add(self.input_audio_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)

    def create_speech_settings(self):
        from video_maker.speech_messages import SPEECH_CATEGORIES
        
        sizer = self.speech_page.GetSizer()
        
        mode_label = wx.StaticText(self.speech_page, label=tr("نمط النطق"))
        set_accessible_name(mode_label, tr("نمط النطق"))
        
        self.speech_mode_choice = wx.Choice(self.speech_page, choices=[
            tr("تشغيل كل الأوصاف"), 
            tr("إيقاف كل الأوصاف"), 
            tr("تخصيص")
        ])
        set_accessible_name(self.speech_mode_choice, tr("نمط النطق"))
        
        self.speech_group_label = wx.StaticText(self.speech_page, label=tr("المجموعة"))
        set_accessible_name(self.speech_group_label, tr("المجموعة"))
        
        self.speech_group_choice = wx.Choice(self.speech_page, choices=[
            tr(c["name"]) for c in SPEECH_CATEGORIES
        ])
        set_accessible_name(self.speech_group_choice, tr("المجموعة"))
        
        self.speech_settings_container = wx.Panel(self.speech_page)
        self.speech_container_sizer = wx.BoxSizer(wx.VERTICAL)
        self.speech_settings_container.SetSizer(self.speech_container_sizer)
        
        self.speech_category_panels = []
        from video_maker.app_state import get_speech_custom_settings
        settings = get_speech_custom_settings()
        
        for category in SPEECH_CATEGORIES:
            cat_panel = wx.Panel(self.speech_settings_container)
            cat_sizer = wx.BoxSizer(wx.VERTICAL)
            cat_panel.SetSizer(cat_sizer)
            
            for setting in category["settings"]:
                cb = wx.CheckBox(cat_panel, label=tr(setting["label"]))
                is_checked = settings.get(setting["id"], True)
                cb.SetValue(is_checked)
                cb.Bind(wx.EVT_CHECKBOX, lambda e, sid=setting["id"]: self.on_speech_setting_toggled(e, sid))
                cat_sizer.Add(cb, 0, wx.ALL, 5)
                
            self.speech_container_sizer.Add(cat_panel, 1, wx.EXPAND)
            cat_panel.Hide()
            self.speech_category_panels.append(cat_panel)
        
        sizer.Add(mode_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        sizer.Add(self.speech_mode_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        
        sizer.Add(self.speech_group_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        sizer.Add(self.speech_group_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        
        sizer.Add(self.speech_settings_container, 1, wx.ALL | wx.EXPAND, 12)
        
        self.speech_mode_choice.Bind(wx.EVT_CHOICE, self.on_speech_mode_change)
        self.speech_group_choice.Bind(wx.EVT_CHOICE, self.on_speech_group_change)

    def load_settings(self):
        from video_maker.app_state import get_startup_action
        self.startup_action_choice.SetSelection(get_startup_action())
        
        self.program_mode_choice.SetSelection(program_mode_index(get_program_mode()))
        
        sound = get_startup_sound()
        if sound == "enable":
            self.startup_sound_choice.SetSelection(0)
            self.startup_sound_browse.Hide()
        elif sound == "disable":
            self.startup_sound_choice.SetSelection(1)
            self.startup_sound_browse.Hide()
        else:
            self.startup_sound_choice.SetSelection(2)
            self.startup_sound_browse.Show()
            
        from video_maker.app_state import get_speech_mode, get_nav_sounds_mode
        mode = get_speech_mode()
        if mode == "disable":
            self.speech_mode_choice.SetSelection(1)
        elif mode == "custom":
            self.speech_mode_choice.SetSelection(2)
        else:
            self.speech_mode_choice.SetSelection(0)
            
        self.speech_group_choice.SetSelection(0)
        self.update_speech_ui()

        nav_mode = get_nav_sounds_mode()
        if nav_mode == "disable":
            self.nav_mode_choice.SetSelection(1)
        elif nav_mode == "custom":
            self.nav_mode_choice.SetSelection(2)
        else:
            self.nav_mode_choice.SetSelection(0)
            
        if self.nav_listbox.GetCount() > 0:
            self.nav_listbox.SetSelection(0)
        self.update_nav_ui()

        self.output_audio_choice.SetSelection(selection_index(self.output_audio_devices, get_selected_device_id(OUTPUT_KIND)))
        self.input_audio_choice.SetSelection(selection_index(self.input_audio_devices, get_selected_device_id(INPUT_KIND)))

    def save_settings(self):
        from video_maker.app_state import set_startup_action
        set_startup_action(self.startup_action_choice.GetSelection())
        set_program_mode(program_mode_at(self.program_mode_choice.GetSelection()))
        output_selection = self.output_audio_choice.GetSelection()
        input_selection = self.input_audio_choice.GetSelection()
        if output_selection != wx.NOT_FOUND and output_selection < len(self.output_audio_devices):
            set_selected_device_id(OUTPUT_KIND, self.output_audio_devices[output_selection].id)
        if input_selection != wx.NOT_FOUND and input_selection < len(self.input_audio_devices):
            set_selected_device_id(INPUT_KIND, self.input_audio_devices[input_selection].id)

    def on_ok(self, event=None):
        self.save_settings()
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_notebook_key(self, event):
        key = event.GetKeyCode()
        if event.ControlDown() or event.AltDown() or event.ShiftDown():
            event.Skip()
            return
        if key not in (wx.WXK_LEFT, wx.WXK_RIGHT):
            event.Skip()
            return
        if self.notebook.GetPageCount() <= 1:
            event.Skip()
            return
        current = self.notebook.GetSelection()
        if self.language == "ar":
            step = 1 if key == wx.WXK_LEFT else -1
        else:
            step = 1 if key == wx.WXK_RIGHT else -1
        self.notebook.SetSelection((current + step) % self.notebook.GetPageCount())

    def on_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.on_cancel()
            return
        if key == wx.WXK_RETURN and event.ControlDown():
            self.on_ok()
            return
        event.Skip()

    def on_startup_sound_change(self, event):
        selection = self.startup_sound_choice.GetSelection()
        if selection == 2:
            self.startup_sound_browse.Show()
            self.startup_sound_choice.SetFocus()
        else:
            self.startup_sound_browse.Hide()
            if selection == 0:
                set_startup_sound("enable")
            elif selection == 1:
                set_startup_sound("disable")
        self.general_page.Layout()

    def on_startup_sound_browse(self, event):
        with wx.FileDialog(self, tr("اختيار ملف صوت"), wildcard=tr("ملفات الصوت") + " (*.wav)|*.wav", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            source_path = dialog.GetPath()
            target_path = os.path.join(app_data_root(), "startup_sound.wav")
            try:
                if source_path != target_path:
                    shutil.copy2(source_path, target_path)
                set_startup_sound(target_path)
            except Exception as e:
                wx.MessageBox(tr("تعذر نسخ الملف الصوتي:") + f" {e}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
            self.startup_sound_choice.SetFocus()

    def on_browse_focus(self, event):
        event.Skip()
        if hasattr(self.Parent, 'say'):
            desc = tr("اختيار ملف صوت بدء التشغيل بصيغة wav فقط")
            self.Parent.say(desc, interrupt=False)

    def on_speech_mode_change(self, event):
        from video_maker.app_state import set_speech_mode
        sel = self.speech_mode_choice.GetSelection()
        if sel == 1:
            set_speech_mode("disable")
        elif sel == 2:
            set_speech_mode("custom")
        else:
            set_speech_mode("enable")
        self.update_speech_ui()
        event.Skip()

    def update_speech_ui(self):
        sel = self.speech_mode_choice.GetSelection()
        is_custom = (sel == 2)
        
        if is_custom:
            self.speech_group_label.Show()
            self.speech_group_choice.Show()
            self.speech_settings_container.Show()
            self.on_speech_group_change(None)
        else:
            self.speech_group_label.Hide()
            self.speech_group_choice.Hide()
            self.speech_settings_container.Hide()
            
        self.speech_page.Layout()

    def on_speech_group_change(self, event):
        group_idx = self.speech_group_choice.GetSelection()
        if group_idx == wx.NOT_FOUND:
            if event: event.Skip()
            return
            
        for idx, panel in enumerate(self.speech_category_panels):
            if idx == group_idx:
                panel.Show()
            else:
                panel.Hide()
            
        self.speech_settings_container.Layout()
        self.speech_page.Layout()
        if event:
            event.Skip()

    def on_speech_setting_toggled(self, event, setting_id):
        from video_maker.app_state import set_speech_custom_setting
        cb = event.GetEventObject()
        is_checked = cb.GetValue()
        set_speech_custom_setting(setting_id, is_checked)

    def create_nav_sounds_settings(self):
        from video_maker.navigation_sounds import NAV_SOUNDS_DEFAULTS
        
        sizer = self.nav_sounds_page.GetSizer()
        
        mode_label = wx.StaticText(self.nav_sounds_page, label=tr("حالة أصوات التنقل"))
        set_accessible_name(mode_label, tr("حالة أصوات التنقل"))
        
        self.nav_mode_choice = wx.Choice(self.nav_sounds_page, choices=[
            tr("تشغيل كل الأصوات"), 
            tr("إيقاف كل الأصوات"), 
            tr("تخصيص")
        ])
        set_accessible_name(self.nav_mode_choice, tr("حالة أصوات التنقل"))
        
        self.nav_custom_panel = wx.Panel(self.nav_sounds_page)
        custom_sizer = wx.BoxSizer(wx.VERTICAL)
        self.nav_custom_panel.SetSizer(custom_sizer)
        
        list_label = wx.StaticText(self.nav_custom_panel, label=tr("عناصر الواجهة"))
        set_accessible_name(list_label, tr("عناصر الواجهة"))
        
        self.nav_keys = list(NAV_SOUNDS_DEFAULTS.keys())
        choices = [tr(NAV_SOUNDS_DEFAULTS[k]["name"]) for k in self.nav_keys]
        self.nav_listbox = wx.ListBox(self.nav_custom_panel, choices=choices)
        set_accessible_name(self.nav_listbox, tr("عناصر الواجهة"))
        
        self.nav_item_panel = wx.Panel(self.nav_custom_panel)
        item_sizer = wx.BoxSizer(wx.VERTICAL)
        self.nav_item_panel.SetSizer(item_sizer)
        
        self.nav_enable_cb = wx.CheckBox(self.nav_item_panel, label=tr("تشغيل الصوت لهذا العنصر"))
        
        buttons_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.nav_change_btn = wx.Button(self.nav_item_panel, label=tr("تغيير الصوت..."))
        self.nav_reset_btn = wx.Button(self.nav_item_panel, label=tr("استعادة الافتراضي"))
        set_accessible_name(self.nav_change_btn, tr("تغيير الصوت لهذا العنصر"))
        set_accessible_name(self.nav_reset_btn, tr("استعادة الصوت الافتراضي لهذا العنصر"))
        
        buttons_sizer.Add(self.nav_change_btn, 0, wx.RIGHT, 5)
        buttons_sizer.Add(self.nav_reset_btn, 0, wx.RIGHT, 5)
        
        item_sizer.Add(self.nav_enable_cb, 0, wx.ALL, 5)
        item_sizer.Add(buttons_sizer, 0, wx.ALL, 5)
        
        custom_sizer.Add(list_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 5)
        custom_sizer.Add(self.nav_listbox, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 5)
        custom_sizer.Add(self.nav_item_panel, 0, wx.ALL | wx.EXPAND, 5)
        
        sizer.Add(mode_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 12)
        sizer.Add(self.nav_mode_choice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        sizer.Add(self.nav_custom_panel, 1, wx.ALL | wx.EXPAND, 12)
        
        self.nav_mode_choice.Bind(wx.EVT_CHOICE, self.on_nav_mode_change)
        self.nav_listbox.Bind(wx.EVT_LISTBOX, self.on_nav_listbox_change)
        self.nav_enable_cb.Bind(wx.EVT_CHECKBOX, self.on_nav_enable_change)
        self.nav_change_btn.Bind(wx.EVT_BUTTON, self.on_nav_change_btn)
        self.nav_reset_btn.Bind(wx.EVT_BUTTON, self.on_nav_reset_btn)

    def on_nav_mode_change(self, event):
        from video_maker.app_state import set_nav_sounds_mode
        sel = self.nav_mode_choice.GetSelection()
        if sel == 1:
            set_nav_sounds_mode("disable")
        elif sel == 2:
            set_nav_sounds_mode("custom")
        else:
            set_nav_sounds_mode("enable")
        self.update_nav_ui()
        event.Skip()

    def update_nav_ui(self):
        sel = self.nav_mode_choice.GetSelection()
        is_custom = (sel == 2)
        
        if is_custom:
            self.nav_custom_panel.Show()
            self.on_nav_listbox_change(None)
        else:
            self.nav_custom_panel.Hide()
            
        self.nav_sounds_page.Layout()

    def get_current_nav_key(self):
        sel = self.nav_listbox.GetSelection()
        if sel != wx.NOT_FOUND:
            return self.nav_keys[sel]
        return None

    def on_nav_listbox_change(self, event):
        from video_maker.app_state import get_nav_sounds_custom
        key = self.get_current_nav_key()
        if not key:
            self.nav_item_panel.Hide()
            if event: event.Skip()
            return
            
        self.nav_item_panel.Show()
        custom_settings = get_nav_sounds_custom()
        item_settings = custom_settings.get(key, {})
        
        is_enabled = item_settings.get("enabled", True)
        self.nav_enable_cb.SetValue(is_enabled)
        
        self.nav_custom_panel.Layout()
        self.nav_sounds_page.Layout()
        if event:
            event.Skip()

    def _update_nav_custom_setting(self, key, attr, value):
        from video_maker.app_state import get_nav_sounds_custom, set_nav_sounds_custom
        custom_settings = get_nav_sounds_custom()
        if key not in custom_settings:
            custom_settings[key] = {}
        custom_settings[key][attr] = value
        set_nav_sounds_custom(custom_settings)

    def on_nav_enable_change(self, event):
        key = self.get_current_nav_key()
        cb = self.nav_enable_cb
        if key:
            self._update_nav_custom_setting(key, "enabled", cb.GetValue())
        event.Skip()

    def on_nav_change_btn(self, event):
        key = self.get_current_nav_key()
        if not key:
            return
            
        dlg = wx.FileDialog(
            self, message=tr("اختيار ملف صوت"),
            wildcard="WAV files (*.wav)|*.wav",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            self._update_nav_custom_setting(key, "file", path)
            from video_maker.ui_sounds import _play_async
            _play_async(path)
        dlg.Destroy()

    def on_nav_reset_btn(self, event):
        key = self.get_current_nav_key()
        if not key:
            return
            
        self._update_nav_custom_setting(key, "file", "")
        # Play default
        from video_maker.navigation_sounds import play_nav_sound
        play_nav_sound(key)
