import json
import os
import shutil
import time
from pathlib import Path

import wx
from video_maker.mpv_player import MPVMediaCtrl, MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED, EVT_MEDIA_LOADED, EVT_MEDIA_FINISHED

from video_maker.app_paths import ensure_user_effects, safe_filename, unique_path
from video_maker.app_state import read_preferences, write_preferences
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import prepare_media_file_dialog, remember_media_path
from video_maker.localization import tr


VIDEO_WILDCARD = "ملفات الفيديو (*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm)|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm"
VISUAL_EFFECTS_KEY = "visual_effects_library"


def effects_root():
    return ensure_user_effects()


def manifest_path():
    return effects_root() / "accessible_manifest.json"


def read_manifest():
    path = manifest_path()
    if not path.exists():
        return {"version": 1, "effects": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(data):
    manifest_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalized_duration(value, default=0.0):
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = float(default)
    return max(0.0, duration)


def normalize_visual_effect_item(item):
    if not isinstance(item, dict):
        return None
    path = item.get("path", "")
    if not path:
        return None
    path = os.path.abspath(path)
    return {
        "path": path,
        "name": item.get("name") or item.get("description_ar") or os.path.splitext(os.path.basename(path))[0],
        "duration": normalized_duration(item.get("duration", 0)),
        "last_used": float(item.get("last_used", 0) or 0),
    }


def load_visual_effect_library():
    data = read_preferences().get(VISUAL_EFFECTS_KEY, [])
    items = []
    seen = set()
    for item in data if isinstance(data, list) else []:
        normalized = normalize_visual_effect_item(item)
        if not normalized:
            continue
        key = os.path.abspath(normalized["path"]).lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(normalized)
    return sorted(items, key=lambda item: item.get("last_used", 0), reverse=True)


def save_visual_effect_library(items):
    data = read_preferences()
    data[VISUAL_EFFECTS_KEY] = list(items)
    write_preferences(data)


def remember_visual_effect(path, name=None, duration=None):
    path = os.path.abspath(path)
    items = load_visual_effect_library()
    previous = next((item for item in items if os.path.abspath(item["path"]).lower() == path.lower()), None)
    saved_duration = previous.get("duration", 0) if previous else 0
    if duration is not None:
        saved_duration = normalized_duration(duration)
    remaining = [item for item in items if os.path.abspath(item["path"]).lower() != path.lower()]
    remaining.insert(0, {
        "path": path,
        "name": name or (previous.get("name") if previous else "") or os.path.splitext(os.path.basename(path))[0],
        "duration": saved_duration,
        "last_used": time.time(),
    })
    save_visual_effect_library(remaining)
    return remaining[0]


def visual_effect_from_library_item(item, index):
    return {
        "id": f"custom_library_{index}",
        "name_ar": item["name"],
        "category_ar": "مؤثرات مضافة",
        "type": "audio_video",
        "duration": item.get("duration", 0),
        "description_ar": item["name"],
        "contains_music": False,
        "path": item["path"],
        "custom": True,
        "last_used": item.get("last_used", 0),
    }


def is_custom_visual_effect_path(path):
    if not path:
        return False
    try:
        custom_root = os.path.abspath(str(effects_root() / "custom"))
        target = os.path.abspath(path)
        return os.path.commonpath([custom_root, target]) == custom_root
    except (OSError, ValueError):
        return False


def load_visual_effects():
    data = read_manifest()
    effects = []
    seen = set()
    for effect in data.get("effects", []):
        if effect.get("type") != "audio_video":
            continue
        path = effects_root() / effect.get("file", "")
        if not path.exists():
            continue
        item = dict(effect)
        item["path"] = str(path)
        seen.add(os.path.abspath(item["path"]).lower())
        effects.append(item)
    for index, library_item in enumerate(load_visual_effect_library(), 1):
        if not os.path.exists(library_item["path"]):
            continue
        key = os.path.abspath(library_item["path"]).lower()
        if key in seen:
            continue
        seen.add(key)
        effects.append(visual_effect_from_library_item(library_item, index))
    return effects


def add_visual_effect_from_device(source_path, effect_name):
    root = effects_root()
    folder = root / "custom"
    folder.mkdir(parents=True, exist_ok=True)
    extension = os.path.splitext(source_path)[1] or ".mp4"
    target = unique_path(folder, safe_filename(effect_name, extension))
    shutil.copy2(source_path, target)
    name = os.path.splitext(target.name)[0]
    remember_visual_effect(str(target), name, 0)
    data = read_manifest()
    effects = data.setdefault("effects", [])
    relative = target.relative_to(root).as_posix()
    effects.append({
        "id": f"custom_{len(effects) + 1}",
        "name_ar": name,
        "category_ar": "مؤثرات مضافة",
        "type": "audio_video",
        "duration": 0,
        "file": relative,
        "description_ar": name,
        "contains_music": False,
    })
    write_manifest(data)
    return str(target), name


class VisualEffectsDialog(wx.Dialog):
    def __init__(self, parent, add_callback, choose_callback):
        super().__init__(parent, title=tr("المؤثرات المرئية"), size=(720, 420))
        self.add_callback = add_callback
        self.choose_callback = choose_callback
        self.effects = load_visual_effects()
        self.loaded_path = ""
        self.pending_play = False

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.effects_list = wx.ListBox(panel)
        self.effects_list.SetName(tr("قائمة وصف المؤثرات المرئية"))
        self.preview = MPVMediaCtrl(panel, style=wx.SIMPLE_BORDER)
        self.preview.SetName(tr("معاينة المؤثر المرئي"))

        play_button = wx.Button(panel, label=tr("تشغيل"))
        rewind_button = wx.Button(panel, label=tr("ترجيع"))
        forward_button = wx.Button(panel, label=tr("تقديم"))
        pause_button = wx.Button(panel, label=tr("إيقاف مؤقت"))
        stop_button = wx.Button(panel, label=tr("إيقاف"))
        add_button = wx.Button(panel, label=tr("إضافة"))
        choose_button = wx.Button(panel, label=tr("اختيار مؤثر من الجهاز"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))

        play_button.SetName(tr("تشغيل المؤثر المحدد"))
        rewind_button.SetName(tr("ترجيع معاينة المؤثر"))
        forward_button.SetName(tr("تقديم معاينة المؤثر"))
        pause_button.SetName(tr("إيقاف مؤقت لمعاينة المؤثر"))
        stop_button.SetName(tr("إيقاف معاينة المؤثر"))
        add_button.SetName(tr("إضافة المؤثر المحدد"))
        choose_button.SetName(tr("اختيار مؤثر مرئي من الجهاز"))
        cancel_button.SetName(tr("إلغاء"))
        add_button.SetDefault()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(play_button, flag=wx.ALL, border=6)
        button_sizer.Add(rewind_button, flag=wx.ALL, border=6)
        button_sizer.Add(forward_button, flag=wx.ALL, border=6)
        button_sizer.Add(pause_button, flag=wx.ALL, border=6)
        button_sizer.Add(stop_button, flag=wx.ALL, border=6)
        button_sizer.Add(add_button, flag=wx.ALL, border=6)
        button_sizer.Add(choose_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(self.effects_list, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)
        main_sizer.Add(self.preview, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
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
        self.effects_list.Bind(wx.EVT_LISTBOX_DCLICK, self.add_selected)
        self.Bind(EVT_MEDIA_LOADED, self.on_preview_loaded, self.preview)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.close_dialog)
        bind_dialog_keys(self, self.on_key)

        self.populate()
        self.Centre()
        wx.CallAfter(self.effects_list.SetFocus)

    def populate(self):
        self.effects_list.Clear()
        for effect in self.effects:
            duration = effect.get("duration", 0)
            description = tr(effect.get("description_ar", effect.get("name_ar", "مؤثر مرئي")))
            self.effects_list.Append(f"{description} {tr('المدة')} {duration} {tr('ثانية')}")
        if self.effects:
            self.effects_list.SetSelection(0)

    def selected_effect(self):
        selection = self.effects_list.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self.effects):
            return None
        return self.effects[selection]

    def add_selected(self, event=None):
        effect = self.selected_effect()
        if not effect:
            return
        self.stop_preview()
        description = effect.get("description_ar", effect.get("name_ar", ""))
        if effect.get("custom") or is_custom_visual_effect_path(effect.get("path", "")):
            remember_visual_effect(effect["path"], description, effect.get("duration", 0))
        self.add_callback(effect["path"], description)
        self.Destroy()

    def play_selected(self, event=None):
        effect = self.selected_effect()
        if not effect:
            wx.CallAfter(self.effects_list.SetFocus)
            return
        if self.loaded_path != effect["path"]:
            if not self.preview.Load(effect["path"]):
                wx.MessageBox(tr("تعذر تحميل المؤثر المرئي للمعاينة"), tr("خطأ"), wx.OK | wx.ICON_ERROR)
                wx.CallAfter(self.effects_list.SetFocus)
                return
            self.loaded_path = effect["path"]
            self.pending_play = True
            wx.CallLater(5, self.finish_pending_preview_play)
        else:
            if self.preview.Length() > 0 and self.preview.Tell() >= self.preview.Length() - 200:
                self.preview.Seek(0)
            self.preview.Play()
        wx.CallAfter(self.effects_list.SetFocus)

    def on_preview_loaded(self, event):
        self.finish_pending_preview_play()
        event.Skip()

    def finish_pending_preview_play(self):
        if not self.pending_play:
            return
        if self.preview.Length() <= 0:
            wx.CallLater(5, self.finish_pending_preview_play)
            return
        self.pending_play = False
        self.preview.Seek(0)
        self.preview.Play()

    def pause_preview(self, event=None):
        if self.preview.GetState() == MEDIASTATE_PLAYING:
            self.preview.Pause()
        wx.CallAfter(self.effects_list.SetFocus)

    def stop_preview(self, event=None, restore_focus=True):
        self.pending_play = False
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            self.preview.Stop()
        if restore_focus:
            wx.CallAfter(self.effects_list.SetFocus)

    def rewind_preview(self, event=None):
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            position = max(0, self.preview.Tell() - 5000)
            self.preview.Seek(position)
        wx.CallAfter(self.effects_list.SetFocus)

    def forward_preview(self, event=None):
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            length = self.preview.Length()
            position = self.preview.Tell() + 5000
            if length > 0:
                position = min(length, position)
            self.preview.Seek(position)
        wx.CallAfter(self.effects_list.SetFocus)

    def toggle_preview(self):
        focused = wx.Window.FindFocus()
        if isinstance(focused, wx.Button):
            return False
        if self.preview.GetState() == MEDIASTATE_PLAYING:
            self.pause_preview()
        else:
            self.play_selected()
        return True

    def choose_from_device(self, event=None):
        with wx.FileDialog(self, tr("اختيار مؤثر مرئي"), wildcard=VIDEO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, "video", "visual_effect_device")
            if dialog.ShowModal() == wx.ID_CANCEL:
                wx.CallAfter(self.effects_list.SetFocus)
                return
            path = dialog.GetPath()
            remember_media_path(path, "video", "visual_effect_device")
        default_name = os.path.splitext(os.path.basename(path))[0]
        name_dialog = wx.TextEntryDialog(self, tr("اكتب اسم المؤثر المضاف"), tr("اسم المؤثر"), default_name)
        if name_dialog.ShowModal() != wx.ID_OK:
            name_dialog.Destroy()
            wx.CallAfter(self.effects_list.SetFocus)
            return
        effect_name = name_dialog.GetValue().strip() or default_name
        name_dialog.Destroy()
        saved_path, description = add_visual_effect_from_device(path, effect_name)
        self.choose_callback(saved_path, description)
        self.Destroy()

    def close_dialog(self, event=None):
        self.stop_preview(None, False)
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.close_dialog()
            return
        if event.GetKeyCode() == wx.WXK_F4:
            self.play_selected()
            return
        if event.GetKeyCode() == wx.WXK_F5:
            self.rewind_preview()
            return
        if event.GetKeyCode() == wx.WXK_F6:
            self.forward_preview()
            return
        if event.GetKeyCode() == wx.WXK_F7:
            self.pause_preview()
            return
        if event.GetKeyCode() == wx.WXK_F8:
            self.stop_preview()
            return
        if event.GetKeyCode() == wx.WXK_SPACE and self.toggle_preview():
            return
        event.Skip()
