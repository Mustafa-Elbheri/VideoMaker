import os
from dataclasses import dataclass

import wx

from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr


@dataclass(frozen=True)
class SplitRange:
    start: float
    end: float

    @property
    def duration(self):
        return max(0.0, self.end - self.start)


def split_ranges(start_time, end_time, requested_duration):
    start_time = float(start_time)
    end_time = float(end_time)
    requested_duration = float(requested_duration)
    total = max(0.0, end_time - start_time)
    if total <= 0:
        return []
    if requested_duration <= 0:
        raise ValueError("split duration must be greater than zero")
    count = max(1, int(round(total / requested_duration)))
    adjusted_duration = total / count
    ranges = []
    for index in range(count):
        part_start = start_time + adjusted_duration * index
        part_end = end_time if index == count - 1 else start_time + adjusted_duration * (index + 1)
        ranges.append(SplitRange(part_start, part_end))
    return ranges


def split_ranges_by_count(start_time, end_time, count):
    start_time = float(start_time)
    end_time = float(end_time)
    count = int(count)
    total = max(0.0, end_time - start_time)
    if total <= 0:
        return []
    if count <= 0:
        raise ValueError("split count must be greater than zero")
    adjusted_duration = total / count
    ranges = []
    for index in range(count):
        part_start = start_time + adjusted_duration * index
        part_end = end_time if index == count - 1 else start_time + adjusted_duration * (index + 1)
        ranges.append(SplitRange(part_start, part_end))
    return ranges


def split_ranges_for_options(start_time, end_time, options):
    if isinstance(options, dict) and options.get("mode") == "count":
        return split_ranges_by_count(start_time, end_time, int(options.get("count", 0) or 0))
    if isinstance(options, dict):
        return split_ranges(start_time, end_time, float(options.get("duration", 0.0) or 0.0))
    return split_ranges(start_time, end_time, options)


def equalized_duration_seconds(total_duration, requested_duration):
    total_duration = max(0.0, float(total_duration or 0.0))
    requested_duration = max(0.0, float(requested_duration or 0.0))
    if total_duration <= 0 or requested_duration <= 0:
        return 0
    count = max(1, int(round(total_duration / requested_duration)))
    return max(1, int(round(total_duration / count)))


def numbered_output_path(base_path, index, total):
    directory = os.path.dirname(base_path)
    name = os.path.basename(base_path)
    stem, extension = os.path.splitext(name)
    width = max(2, len(str(max(1, int(total)))))
    numbered_name = f"{int(index):0{width}d} {stem}{extension}"
    return os.path.join(directory, numbered_name)


def timed_items_for_range(items, start_time, end_time):
    adjusted_items = []
    for item in items:
        item_start = float(item.get("start", 0.0) or 0.0)
        item_end = float(item.get("end", 0.0) or 0.0)
        if item_end <= start_time or item_start >= end_time:
            continue
        adjusted = dict(item)
        item_speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        overlap_start = max(item_start, start_time)
        overlap_end = min(item_end, end_time)
        adjusted["start"] = overlap_start - start_time
        adjusted["end"] = overlap_end - start_time
        adjusted["source_offset"] = source_offset + max(0.0, overlap_start - item_start) * item_speed
        adjusted_items.append(adjusted)
    return adjusted_items


def numeric_text_value(control, default=0, minimum=0, maximum=999):
    try:
        raw_value = control.GetValue()
    except Exception:
        raw_value = ""
    digits = "".join(character for character in str(raw_value) if character.isdigit())
    if not digits:
        return int(default)
    value = int(digits)
    return max(int(minimum), min(int(maximum), value))


def set_numeric_text_value(control, value, maximum=999):
    value = max(0, min(int(maximum), int(value or 0)))
    control.SetValue(str(value))


def numeric_text_bounds(control, hours_control=None, minutes_control=None, seconds_control=None, count_control=None):
    if control is count_control:
        return 1, 999
    if control is minutes_control or control is seconds_control:
        return 0, 59
    if control is hours_control:
        return 0, 999
    return 0, 999


class TimelineSplitDialog(wx.Frame):
    def __init__(self, parent, selected_duration, split_callback):
        super().__init__(parent, title=tr("تقسيم"), size=(480, 340))
        from video_maker.menus import install_menu_bar

        self.selected_duration = max(0.0, float(selected_duration or 0.0))
        self.split_callback = split_callback
        self.show_hours = self.selected_duration >= 3600.0
        self._restore_focus_timer = None

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        mode_sizer = wx.BoxSizer(wx.VERTICAL)
        mode_label = wx.StaticText(panel, label=tr("طريقة التقسيم"))
        self.duration_mode_radio = wx.RadioButton(panel, label=tr("التقسيم بالمدة"), style=wx.RB_GROUP)
        self.count_mode_radio = wx.RadioButton(panel, label=tr("التقسيم بعدد الملفات"))
        self.duration_mode_radio.SetName(tr("التقسيم بالمدة"))
        self.count_mode_radio.SetName(tr("التقسيم بعدد الملفات"))
        self.duration_mode_radio.SetValue(True)
        self.mode_description = wx.StaticText(panel, label="")
        mode_sizer.Add(mode_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=14)
        mode_sizer.Add(self.duration_mode_radio, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=14)
        mode_sizer.Add(self.count_mode_radio, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=14)
        mode_sizer.Add(self.mode_description, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=14)

        self.duration_panel = wx.Panel(panel)
        duration_sizer = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        duration_sizer.AddGrowableCol(1)

        self.hours_spin = None
        if self.show_hours:
            self.hours_label = wx.StaticText(self.duration_panel, label=tr("ساعات كل ملف"))
            self.hours_spin = wx.TextCtrl(self.duration_panel, value="1", style=wx.TE_PROCESS_ENTER)
            self.hours_spin.SetName(tr("ساعات كل ملف"))
            duration_sizer.Add(self.hours_label, flag=wx.ALIGN_CENTER_VERTICAL)
            duration_sizer.Add(self.hours_spin, flag=wx.EXPAND)

        self.minutes_label = wx.StaticText(self.duration_panel, label=tr("دقائق كل ملف"))
        self.seconds_label = wx.StaticText(self.duration_panel, label=tr("ثواني كل ملف"))
        self.minutes_spin = wx.TextCtrl(self.duration_panel, value=str(self.default_minutes()), style=wx.TE_PROCESS_ENTER)
        self.seconds_spin = wx.TextCtrl(self.duration_panel, value=str(self.default_seconds()), style=wx.TE_PROCESS_ENTER)
        self.minutes_spin.SetName(tr("دقائق كل ملف"))
        self.seconds_spin.SetName(tr("ثواني كل ملف"))
        duration_sizer.Add(self.minutes_label, flag=wx.ALIGN_CENTER_VERTICAL)
        duration_sizer.Add(self.minutes_spin, flag=wx.EXPAND)
        duration_sizer.Add(self.seconds_label, flag=wx.ALIGN_CENTER_VERTICAL)
        duration_sizer.Add(self.seconds_spin, flag=wx.EXPAND)
        self.duration_panel.SetSizer(duration_sizer)

        self.count_panel = wx.Panel(panel)
        count_sizer = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        count_sizer.AddGrowableCol(1)
        self.count_label = wx.StaticText(self.count_panel, label=tr("عدد الملفات"))
        self.count_spin = wx.TextCtrl(self.count_panel, value="2", style=wx.TE_PROCESS_ENTER)
        self.count_spin.SetName(tr("عدد الملفات"))
        count_sizer.Add(self.count_label, flag=wx.ALIGN_CENTER_VERTICAL)
        count_sizer.Add(self.count_spin, flag=wx.EXPAND)
        self.count_panel.SetSizer(count_sizer)
        for control in (self.hours_spin, self.minutes_spin, self.seconds_spin, self.count_spin):
            if control is not None:
                control.Bind(wx.EVT_SET_FOCUS, self.on_number_text_focus)

        self.equal_split_checkbox = wx.CheckBox(panel, label=tr("التقسيم بالتساوي"))
        self.equal_split_checkbox.SetName(tr("التقسيم بالتساوي"))

        save_button = wx.Button(panel, label=tr("حفظ التقسيم"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))
        save_button.SetName(tr("حفظ التقسيم"))
        cancel_button.SetName(tr("إلغاء"))
        save_button.SetDefault()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(save_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(mode_sizer, flag=wx.EXPAND)
        main_sizer.Add(self.duration_panel, flag=wx.EXPAND | wx.ALL, border=14)
        main_sizer.Add(self.count_panel, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=14)
        main_sizer.Add(self.equal_split_checkbox, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=14)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=8)
        panel.SetSizer(main_sizer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        self.duration_mode_radio.Bind(wx.EVT_RADIOBUTTON, self.on_mode_changed)
        self.count_mode_radio.Bind(wx.EVT_RADIOBUTTON, self.on_mode_changed)
        save_button.Bind(wx.EVT_BUTTON, self.on_save)
        cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.equal_split_checkbox.Bind(wx.EVT_CHECKBOX, self.on_equal_split_changed)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, (wx.TextCtrl,), preserve_navigation_keys=True)

        self.Centre()
        install_menu_bar(self, parent, include_shortcuts=False)
        self.update_mode_controls(speak=False)
        wx.CallAfter(self.duration_mode_radio.SetFocus)

    def default_duration_seconds(self):
        if self.selected_duration >= 3600.0:
            return 3600
        if self.selected_duration >= 300.0:
            return 300
        return max(1, min(3599, int(round(self.selected_duration))))

    def default_minutes(self):
        return int(self.default_duration_seconds() // 60) % 60

    def default_seconds(self):
        return int(self.default_duration_seconds() % 60)

    def duration_seconds(self):
        hours = numeric_text_value(self.hours_spin, 0, 0, 999) if self.hours_spin is not None else 0
        minutes = numeric_text_value(self.minutes_spin, 0, 0, 59)
        seconds = numeric_text_value(self.seconds_spin, 0, 0, 59)
        return hours * 3600 + minutes * 60 + seconds

    def selected_mode(self):
        return "count" if self.count_mode_radio.GetValue() else "duration"

    def update_mode_controls(self, speak=True):
        count_mode = self.selected_mode() == "count"
        self.duration_panel.Show(not count_mode)
        self.duration_panel.Enable(not count_mode)
        self.count_panel.Show(count_mode)
        self.count_panel.Enable(count_mode)
        self.equal_split_checkbox.Show(not count_mode)
        self.equal_split_checkbox.Enable(not count_mode)
        self.mode_description.SetLabel("")
        self.Layout()
        if speak:
            self.restore_mode_focus()

    def selected_mode_control(self):
        return self.count_mode_radio if self.selected_mode() == "count" else self.duration_mode_radio

    def restore_mode_focus(self):
        target = self.selected_mode_control()
        wx.CallAfter(target.SetFocus)
        try:
            self._restore_focus_timer = wx.CallLater(80, target.SetFocus)
        except Exception:
            pass

    def on_mode_changed(self, event=None):
        self.update_mode_controls()

    def set_duration_seconds(self, seconds):
        seconds = max(0, int(seconds or 0))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds_value = divmod(remainder, 60)
        if self.hours_spin is not None:
            set_numeric_text_value(self.hours_spin, hours, 999)
        set_numeric_text_value(self.minutes_spin, minutes, 59)
        set_numeric_text_value(self.seconds_spin, seconds_value, 59)

    def on_equal_split_changed(self, event=None):
        if not self.equal_split_checkbox.GetValue():
            return
        duration = self.duration_seconds()
        equal_duration = equalized_duration_seconds(self.selected_duration, duration)
        if equal_duration > 0:
            self.set_duration_seconds(equal_duration)

    def on_save(self, event=None):
        options = self.get_options()
        if options["mode"] == "count":
            if options["count"] <= 0:
                wx.MessageBox(tr("اكتب عدد ملفات أكبر من صفر."), tr("قيمة غير صحيحة"), wx.OK | wx.ICON_ERROR)
                return
        elif options["duration"] <= 0:
            wx.MessageBox(tr("اكتب مدة أكبر من صفر."), tr("قيمة غير صحيحة"), wx.OK | wx.ICON_ERROR)
            return
        self.split_callback(options)
        self.Destroy()

    def get_options(self):
        if self.selected_mode() == "count":
            return {"mode": "count", "count": numeric_text_value(self.count_spin, 0, 0, 999)}
        return {"mode": "duration", "duration": self.duration_seconds()}

    def speak(self, message, wait_for_ui=True):
        parent = self.GetParent() if hasattr(self, "GetParent") else None
        if hasattr(parent, "say"):
            parent.say(message)

    def number_text_controls(self):
        return tuple(control for control in (self.hours_spin, self.minutes_spin, self.seconds_spin, self.count_spin) if control is not None)

    def focused_number_text_control(self):
        try:
            focused = wx.Window.FindFocus()
        except Exception:
            return None
        return focused if focused in self.number_text_controls() else None

    def on_number_text_focus(self, event):
        control = event.GetEventObject()
        wx.CallAfter(control.SetSelection, 0, -1)
        event.Skip()

    def change_number_text_value(self, control, delta):
        minimum, maximum = numeric_text_bounds(control, self.hours_spin, self.minutes_spin, self.seconds_spin, self.count_spin)
        value = numeric_text_value(control, minimum, minimum, maximum)
        value = max(minimum, min(maximum, value + int(delta)))
        set_numeric_text_value(control, value, maximum)
        try:
            control.SetInsertionPointEnd()
        except Exception:
            pass
        self.speak(str(value), wait_for_ui=False)

    def on_cancel(self, event=None):
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
            return
        focused_number_text = self.focused_number_text_control()
        if focused_number_text is not None:
            key = event.GetKeyCode()
            if key == wx.WXK_UP:
                self.change_number_text_value(focused_number_text, 1)
                return
            if key == wx.WXK_DOWN:
                self.change_number_text_value(focused_number_text, -1)
                return
            if key == wx.WXK_PAGEUP:
                self.change_number_text_value(focused_number_text, 10)
                return
            if key == wx.WXK_PAGEDOWN:
                self.change_number_text_value(focused_number_text, -10)
                return
            if hasattr(event, "DoAllowNextEvent"):
                event.DoAllowNextEvent()
            return
        event.Skip()
