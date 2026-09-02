import os
import shutil
import subprocess
import tempfile
import time

import wx
from video_maker.mpv_player import MPVMediaCtrl, MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED, EVT_MEDIA_LOADED, EVT_MEDIA_FINISHED
from video_maker.app_paths import ffmpeg_binary

from video_maker.app_state import read_preferences, write_preferences
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import AUDIO_WILDCARD, prepare_media_file_dialog, remember_media_path
from video_maker.localization import tr


BACKGROUND_AUDIO_KEY = "background_audio_library"
DEFAULT_BACKGROUND_VOLUME = 0.4


def normalized_volume(value, default=1.0):
    try:
        volume = float(value)
    except (TypeError, ValueError):
        volume = float(default)
    if volume > 1.0:
        volume /= 100.0
    return max(0.0, min(1.0, volume))


def normalize_library_item(item):
    if not isinstance(item, dict):
        return None
    path = item.get("path", "")
    if not path:
        return None
    return {
        "path": path,
        "name": item.get("name") or os.path.splitext(os.path.basename(path))[0],
        "volume": normalized_volume(item.get("volume", DEFAULT_BACKGROUND_VOLUME), DEFAULT_BACKGROUND_VOLUME),
        "last_used": float(item.get("last_used", 0) or 0),
    }


def load_background_audio_library():
    data = read_preferences().get(BACKGROUND_AUDIO_KEY, [])
    items = []
    seen = set()
    for item in data if isinstance(data, list) else []:
        normalized = normalize_library_item(item)
        if not normalized:
            continue
        key = os.path.abspath(normalized["path"]).lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(normalized)
    return sorted(items, key=lambda item: item.get("last_used", 0), reverse=True)


def save_background_audio_library(items):
    data = read_preferences()
    data[BACKGROUND_AUDIO_KEY] = list(items)
    write_preferences(data)


def remember_background_audio(path, name=None, volume=None):
    path = os.path.abspath(path)
    items = load_background_audio_library()
    previous = next((item for item in items if os.path.abspath(item["path"]).lower() == path.lower()), None)
    saved_volume = previous.get("volume", DEFAULT_BACKGROUND_VOLUME) if previous else DEFAULT_BACKGROUND_VOLUME
    if volume is not None:
        saved_volume = normalized_volume(volume, DEFAULT_BACKGROUND_VOLUME)
    remaining = [item for item in items if os.path.abspath(item["path"]).lower() != path.lower()]
    remaining.insert(0, {
        "path": path,
        "name": name or (previous.get("name") if previous else "") or os.path.splitext(os.path.basename(path))[0],
        "volume": saved_volume,
        "last_used": time.time(),
    })
    save_background_audio_library(remaining)
    return remaining[0]


def set_background_audio_volume(path, volume):
    target = os.path.abspath(path).lower()
    items = load_background_audio_library()
    for item in items:
        if os.path.abspath(item["path"]).lower() == target:
            item["volume"] = normalized_volume(volume, DEFAULT_BACKGROUND_VOLUME)
            break
    save_background_audio_library(items)


def delete_background_audio_item(path):
    target = os.path.abspath(path).lower()
    save_background_audio_library([
        item for item in load_background_audio_library()
        if os.path.abspath(item["path"]).lower() != target
    ])


def rename_background_audio_item(path, new_name):
    target = os.path.abspath(path).lower()
    items = load_background_audio_library()
    for item in items:
        if os.path.abspath(item["path"]).lower() == target:
            item["name"] = new_name.strip() or item["name"]
            item["last_used"] = time.time()
            break
    save_background_audio_library(items)


def ffmpeg_startupinfo():
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def trim_background_audio_silence(source_path):
    temp_dir = tempfile.mkdtemp(prefix="background_audio_trim_")
    output_path = os.path.join(temp_dir, "trimmed.wav")
    command = [
        ffmpeg_binary(),
        "-y",
        "-i", source_path,
        "-af", "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.12:stop_periods=-1:stop_threshold=-45dB:stop_silence=0.12",
        "-ac", "2",
        "-ar", "44100",
        output_path,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(tr("تعذر قص الصمت من الخلفية الصوتية"))
    return output_path, temp_dir


class BackgroundAudioDialog(wx.Dialog):
    def __init__(self, parent, title=None):
        super().__init__(parent, title=title or tr("إدراج خلفية صوتية"), size=(740, 470))
        self.selected_item = None
        self.selection_options = None
        self.items = load_background_audio_library()
        self.loaded_path = ""
        self.pending_play = False
        self.pending_play_checks = 0
        self.preview_temp_dir = ""
        self.preview_cache_key = None
        self.live_preview_running = False

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.list_box = wx.ListBox(panel)
        self.list_box.SetName(tr("قائمة الخلفيات الصوتية السابقة"))
        self.preview = MPVMediaCtrl(panel, style=wx.SIMPLE_BORDER)
        self.preview.SetName(tr("معاينة الخلفية الصوتية"))

        volume_label = wx.StaticText(panel, label=tr("مستوى صوت الخلفية الصوتية فقط"))
        self.volume_slider = wx.Slider(panel, value=40, minValue=0, maxValue=100)
        self.volume_slider.SetName(tr("مستوى صوت الخلفية الصوتية فقط"))
        self.trim_checkbox = wx.CheckBox(panel, label=tr("قص الصمت من أطراف الخلفية الصوتية"))
        self.trim_checkbox.SetName(tr("قص الصمت من أطراف الخلفية الصوتية"))
        self.trim_checkbox.Bind(wx.EVT_CHECKBOX, self.on_trim_toggled)

        play_button = wx.Button(panel, label=tr("تشغيل"))
        rewind_button = wx.Button(panel, label=tr("ترجيع"))
        forward_button = wx.Button(panel, label=tr("تقديم"))
        pause_button = wx.Button(panel, label=tr("إيقاف مؤقت"))
        stop_button = wx.Button(panel, label=tr("إيقاف"))
        add_button = wx.Button(panel, label=tr("إضافة"))
        choose_button = wx.Button(panel, label=tr("اختيار خلفية صوتية من الجهاز"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))

        play_button.SetName(tr("تشغيل الخلفية الصوتية المحددة"))
        rewind_button.SetName(tr("ترجيع معاينة الخلفية الصوتية"))
        forward_button.SetName(tr("تقديم معاينة الخلفية الصوتية"))
        pause_button.SetName(tr("إيقاف مؤقت لمعاينة الخلفية الصوتية"))
        stop_button.SetName(tr("إيقاف معاينة الخلفية الصوتية"))
        add_button.SetName(tr("إضافة الخلفية الصوتية المحددة"))
        choose_button.SetName(tr("اختيار خلفية صوتية من الجهاز"))
        cancel_button.SetName(tr("إلغاء"))
        add_button.SetDefault()

        volume_sizer = wx.BoxSizer(wx.HORIZONTAL)
        volume_sizer.Add(volume_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=6)
        volume_sizer.Add(self.volume_slider, proportion=1, flag=wx.EXPAND | wx.ALL, border=6)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for button in (play_button, rewind_button, forward_button, pause_button, stop_button, add_button, choose_button, cancel_button):
            button_sizer.Add(button, flag=wx.ALL, border=5)

        main_sizer.Add(self.list_box, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)
        main_sizer.Add(self.preview, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(volume_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
        main_sizer.Add(self.trim_checkbox, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=6)
        panel.SetSizer(main_sizer)

        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        play_button.Bind(wx.EVT_BUTTON, self.play_selected)
        rewind_button.Bind(wx.EVT_BUTTON, self.rewind_preview)
        forward_button.Bind(wx.EVT_BUTTON, self.forward_preview)
        pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        add_button.Bind(wx.EVT_BUTTON, self.add_selected)
        choose_button.Bind(wx.EVT_BUTTON, self.choose_from_device)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_dialog)
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self.add_selected)
        self.list_box.Bind(wx.EVT_LISTBOX, self.on_selection_changed)
        self.list_box.Bind(wx.EVT_CONTEXT_MENU, self.show_context_menu)
        self.list_box.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
        self.volume_slider.Bind(wx.EVT_KEY_DOWN, self.on_volume_key)
        self.volume_slider.Bind(wx.EVT_SLIDER, self.on_volume_changed)
        self.Bind(EVT_MEDIA_LOADED, self.on_preview_loaded, self.preview)
        self.Bind(EVT_MEDIA_FINISHED, self.on_background_preview_finished, self.preview)
        self.preview_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_preview_timer, self.preview_timer)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.close_dialog)
        bind_dialog_keys(self, self.on_key, preserve_navigation_keys=True)

        self.populate()
        self.Centre()
        wx.CallAfter(self.list_box.SetFocus)

    def populate(self):
        selected = self.current_index()
        self.items = load_background_audio_library()
        self.list_box.Clear()
        for index, item in enumerate(self.items, 1):
            status = "" if os.path.exists(item["path"]) else f" - {tr('الملف غير موجود')}"
            self.list_box.Append(f"{index} - {item['name']}{status}")
        if self.items:
            self.list_box.SetSelection(min(selected or 0, len(self.items) - 1))
            self.apply_selected_volume()

    def current_index(self):
        selection = self.list_box.GetSelection()
        if selection == wx.NOT_FOUND or selection < 0 or selection >= len(self.items):
            return None
        return selection

    def selected_background(self):
        index = self.current_index()
        return None if index is None else self.items[index]

    def options(self, item):
        return {
            "path": item["path"],
            "name": item["name"],
            "volume": self.volume_slider.GetValue() / 100.0,
            "trim_silence": self.trim_checkbox.GetValue(),
        }

    def restore_preferred_focus(self):
        focused = wx.Window.FindFocus()
        if focused in (self.volume_slider, self.trim_checkbox):
            wx.CallAfter(focused.SetFocus)
        else:
            wx.CallAfter(self.list_box.SetFocus)

    def apply_selected_volume(self):
        item = self.selected_background()
        if not item:
            return
        self.volume_slider.SetValue(int(round(float(item.get("volume", DEFAULT_BACKGROUND_VOLUME)) * 100)))
        self.apply_preview_volume()

    def effective_preview_volume(self):
        parent = self.GetParent()
        if hasattr(parent, "effective_output_volume"):
            parent_volume = parent.effective_output_volume()
        else:
            parent_volume = normalized_volume(getattr(parent, "volume", 1.0))
        background_volume = self.volume_slider.GetValue() / 100.0
        return max(0.0, min(1.0, parent_volume * background_volume))

    def apply_preview_volume(self):
        self.preview.SetVolume(self.effective_preview_volume())

    def save_current_volume_for_selected(self):
        item = self.selected_background()
        if item:
            volume = self.volume_slider.GetValue() / 100.0
            item["volume"] = volume
            set_background_audio_volume(item["path"], volume)

    def on_selection_changed(self, event=None):
        self.apply_selected_volume()
        if event is not None:
            event.Skip()

    def preview_key(self, item):
        return (
            os.path.abspath(item["path"]).lower(),
            self.volume_slider.GetValue(),
            bool(self.trim_checkbox.GetValue()),
        )

    def cleanup_preview_file(self):
        if self.preview_temp_dir and os.path.exists(self.preview_temp_dir):
            shutil.rmtree(self.preview_temp_dir, ignore_errors=True)
        self.preview_temp_dir = ""
        self.preview_cache_key = None
        self.loaded_path = ""

    def add_selected(self, event=None):
        item = self.selected_background()
        if not item:
            return
        if not os.path.exists(item["path"]):
            wx.MessageBox(tr("ملف الخلفية الصوتية غير موجود"), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            self.restore_preferred_focus()
            return
        self.stop_preview(None, False)
        self.cleanup_preview_file()
        self.selected_item = remember_background_audio(item["path"], item["name"], self.volume_slider.GetValue() / 100.0)
        self.selection_options = self.options(self.selected_item)
        self.EndModal(wx.ID_OK)

    def choose_from_device(self, event=None):
        with wx.FileDialog(self, tr("اختيار خلفية صوتية"), wildcard=AUDIO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, "audio", "background_audio_device")
            if dialog.ShowModal() == wx.ID_CANCEL:
                self.restore_preferred_focus()
                return
            path = dialog.GetPath()
            remember_media_path(path, "audio", "background_audio_device")
        default_name = os.path.splitext(os.path.basename(path))[0]
        name_dialog = wx.TextEntryDialog(self, tr("اكتب اسم الخلفية الصوتية"), tr("اسم الخلفية الصوتية"), default_name)
        if name_dialog.ShowModal() != wx.ID_OK:
            name_dialog.Destroy()
            self.restore_preferred_focus()
            return
        name = name_dialog.GetValue().strip() or default_name
        name_dialog.Destroy()
        item = remember_background_audio(path, name, self.volume_slider.GetValue() / 100.0)
        self.populate()
        for index, current in enumerate(self.items):
            if os.path.abspath(current["path"]).lower() == os.path.abspath(item["path"]).lower():
                self.list_box.SetSelection(index)
                break
        self.apply_selected_volume()
        self.restore_preferred_focus()

    def play_selected(self, event=None):
        item = self.selected_background()
        if not item:
            self.restore_preferred_focus()
            return
        if not os.path.exists(item["path"]):
            wx.MessageBox(tr("ملف الخلفية الصوتية غير موجود"), tr("خطأ"), wx.OK | wx.ICON_ERROR)
            self.restore_preferred_focus()
            return
        preview_path = item["path"]
        parent = self.GetParent()
        if hasattr(parent, "start_background_audio_live_preview"):
            duration = parent.start_background_audio_live_preview()
            self.live_preview_running = True
            if duration > 0:
                self.preview_timer.StartOnce(int(duration * 1000))
        self.load_and_play_preview(preview_path)
        self.restore_preferred_focus()

    def load_and_play_preview(self, preview_path):
        self.apply_preview_volume()
        if self.loaded_path != preview_path:
            if not self.preview.Load(preview_path):
                wx.MessageBox(tr("تعذر تحميل الخلفية الصوتية للمعاينة"), tr("خطأ"), wx.OK | wx.ICON_ERROR)
                return
            self.loaded_path = preview_path
            self.pending_play = True
            self.pending_play_checks = 0
            wx.CallLater(5, self.finish_pending_preview_play)
        else:
            if self.preview.Length() > 0 and self.preview.Tell() >= self.preview.Length() - 200:
                self.preview.Seek(0)
            self.preview.Play()

    def on_preview_loaded(self, event):
        self.finish_pending_preview_play()
        event.Skip()

    def on_background_preview_finished(self, event):
        if self.live_preview_running and self.preview_timer.IsRunning():
            self.preview.Seek(0)
            self.preview.Play()
            return
        event.Skip()

    def finish_pending_preview_play(self):
        if not self.pending_play:
            return
        if self.preview.Length() <= 0:
            self.pending_play_checks += 1
            if self.pending_play_checks >= 20:
                self.pending_play = False
                self.pending_play_checks = 0
                wx.MessageBox(tr("تعذر تحميل الخلفية الصوتية للمعاينة"), tr("خطأ"), wx.OK | wx.ICON_ERROR)
                return
            wx.CallLater(5, self.finish_pending_preview_play)
            return
        self.pending_play = False
        self.pending_play_checks = 0
        self.preview.Seek(0)
        self.apply_preview_volume()
        self.preview.Play()

    def pause_preview(self, event=None):
        if self.preview.GetState() == MEDIASTATE_PLAYING:
            self.preview.Pause()
        parent = self.GetParent()
        if hasattr(parent, "pause_background_audio_live_preview"):
            parent.pause_background_audio_live_preview()
        self.restore_preferred_focus()

    def stop_preview(self, event=None, restore_focus=True):
        self.pending_play = False
        if self.preview_timer.IsRunning():
            self.preview_timer.Stop()
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            self.preview.Stop()
        parent = self.GetParent()
        if self.live_preview_running and hasattr(parent, "stop_background_audio_live_preview"):
            parent.stop_background_audio_live_preview()
        self.live_preview_running = False
        if restore_focus:
            self.restore_preferred_focus()

    def on_trim_toggled(self, event=None):
        checked = self.trim_checkbox.GetValue()
        state = tr("محدد") if checked else tr("غير محدد")
        label = tr("قص الصمت من أطراف الخلفية الصوتية")
        parent = self.GetParent()
        if hasattr(parent, "say"):
            parent.say(f"{label}: {state}")

    def on_volume_changed(self, event=None):
        self.apply_preview_volume()
        self.save_current_volume_for_selected()
        wx.CallAfter(self.volume_slider.SetFocus)
        if event is not None:
            event.Skip()

    def on_volume_key(self, event):
        key = event.GetKeyCode()
        value = self.volume_slider.GetValue()
        if key == wx.WXK_PAGEDOWN:
            self.volume_slider.SetValue(max(self.volume_slider.GetMin(), value - 10))
            self.apply_preview_volume()
            self.save_current_volume_for_selected()
            wx.CallAfter(self.volume_slider.SetFocus)
            return
        if key == wx.WXK_PAGEUP:
            self.volume_slider.SetValue(min(self.volume_slider.GetMax(), value + 10))
            self.apply_preview_volume()
            self.save_current_volume_for_selected()
            wx.CallAfter(self.volume_slider.SetFocus)
            return
        if key in (wx.WXK_DOWN, wx.WXK_LEFT):
            self.volume_slider.SetValue(max(self.volume_slider.GetMin(), value - 1))
            self.apply_preview_volume()
            self.save_current_volume_for_selected()
            wx.CallAfter(self.volume_slider.SetFocus)
            return
        if key in (wx.WXK_UP, wx.WXK_RIGHT):
            self.volume_slider.SetValue(min(self.volume_slider.GetMax(), value + 1))
            self.apply_preview_volume()
            self.save_current_volume_for_selected()
            wx.CallAfter(self.volume_slider.SetFocus)
            return
        event.Skip()

    def rewind_preview(self, event=None):
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            self.preview.Seek(max(0, self.preview.Tell() - 5000))
            parent = self.GetParent()
            if hasattr(parent, "seek_background_audio_live_preview"):
                parent.seek_background_audio_live_preview(-5)
        self.restore_preferred_focus()

    def forward_preview(self, event=None):
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            length = self.preview.Length()
            position = self.preview.Tell() + 5000
            self.preview.Seek(min(length, position) if length > 0 else position)
            parent = self.GetParent()
            if hasattr(parent, "seek_background_audio_live_preview"):
                parent.seek_background_audio_live_preview(5)
        self.restore_preferred_focus()

    def on_preview_timer(self, event=None):
        self.stop_preview(None, True)

    def toggle_preview(self):
        focused = wx.Window.FindFocus()
        if isinstance(focused, (wx.Button, wx.CheckBox, wx.Slider)):
            return False
        if self.preview.GetState() == MEDIASTATE_PLAYING:
            self.pause_preview()
        else:
            self.play_selected()
        return True

    def show_context_menu(self, event=None):
        if self.current_index() is None:
            return
        menu = wx.Menu()
        delete_id = wx.NewIdRef()
        rename_id = wx.NewIdRef()
        menu.Append(delete_id, tr("حذف الخلفية الصوتية"))
        menu.Append(rename_id, tr("إعادة تسمية الخلفية الصوتية"))
        self.Bind(wx.EVT_MENU, self.delete_current, id=delete_id)
        self.Bind(wx.EVT_MENU, self.rename_current, id=rename_id)
        self.PopupMenu(menu)
        menu.Destroy()

    def delete_current(self, event=None):
        item = self.selected_background()
        if not item:
            return
        result = wx.MessageBox(
            tr("هل تريد حذف الخلفية الصوتية من القائمة؟ لن يتم حذف الملف من الجهاز."),
            tr("تأكيد الحذف"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        if result != wx.YES:
            return
        delete_background_audio_item(item["path"])
        self.populate()
        self.restore_preferred_focus()

    def rename_current(self, event=None):
        item = self.selected_background()
        if not item:
            return
        dialog = wx.TextEntryDialog(self, tr("اكتب الاسم الجديد"), tr("إعادة تسمية الخلفية الصوتية"), item["name"])
        if dialog.ShowModal() == wx.ID_OK:
            new_name = dialog.GetValue().strip()
            if new_name:
                rename_background_audio_item(item["path"], new_name)
                self.populate()
        dialog.Destroy()
        self.restore_preferred_focus()

    def on_list_key(self, event):
        key = event.GetKeyCode()
        menu_keys = {getattr(wx, "WXK_WINDOWS_MENU", None), getattr(wx, "WXK_MENU", None)}
        if key in menu_keys:
            self.show_context_menu()
            return
        event.Skip()

    def close_dialog(self, event=None):
        self.stop_preview(None, False)
        self.cleanup_preview_file()
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        key = event.GetKeyCode()
        focused = wx.Window.FindFocus()
        if isinstance(focused, wx.CheckBox) and key == wx.WXK_SPACE:
            event.Skip()
            return
        if isinstance(focused, wx.Slider) and key in (wx.WXK_PAGEUP, wx.WXK_PAGEDOWN, wx.WXK_UP, wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_RIGHT):
            event.Skip()
            return
        if key == wx.WXK_ESCAPE:
            self.close_dialog()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if isinstance(focused, wx.Button):
                event.Skip()
                return
            self.add_selected()
            return
        if key == wx.WXK_F4:
            self.play_selected()
            return
        if key == wx.WXK_F5:
            self.rewind_preview()
            return
        if key == wx.WXK_F6:
            self.forward_preview()
            return
        if key == wx.WXK_F7:
            self.pause_preview()
            return
        if key == wx.WXK_F8:
            self.stop_preview()
            return
        if key == wx.WXK_SPACE and self.toggle_preview():
            return
        event.Skip()
