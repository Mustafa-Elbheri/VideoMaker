import json
import json
import os
import re
import shutil
import time
import uuid

import wx
from video_maker.localization import tr

from video_maker.dialog_keys import bind_dialog_keys
from video_maker.timeline import TimelineSegment
from video_maker.volume_boost import persisted_master_volume_db, persisted_program_volume


APP_FOLDER = "AccessibleVideoMaker"
SESSIONS_FOLDER = "sessions"


def app_data_root():
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def sessions_root():
    path = os.path.join(app_data_root(), SESSIONS_FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def safe_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "جلسة"


def _normalized_ripple_mode(value):
    """تطبيع وضع Ripple بلا استيراد دائري من app_state."""
    return value if value in ("per_track", "all_tracks", "off") else "per_track"


def session_dir_for_name(name):
    return os.path.join(sessions_root(), safe_name(name))


def unique_asset_name(path):
    extension = os.path.splitext(path)[1] or ".mp4"
    return f"{uuid.uuid4().hex}{extension}"


def copy_assets_for_session(session_dir, timeline):
    assets_dir = os.path.join(session_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    copied = {}
    result = []
    for segment in timeline:
        source = os.path.abspath(segment.path)
        if source not in copied:
            if not os.path.exists(source):
                raise FileNotFoundError(source)
            destination = os.path.join(assets_dir, unique_asset_name(source))
            shutil.copy2(source, destination)
            copied[source] = destination
        result.append(TimelineSegment(
            copied[source],
            segment.start,
            segment.end,
            float(getattr(segment, "speed", 1.0) or 1.0),
            float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
            copy_path_for_session(assets_dir, copied, getattr(segment, "audio_path", "")) if getattr(segment, "audio_path", "") else "",
            getattr(segment, "audio_start", None),
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
            str(getattr(segment, "transition", "") or ""),
            max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        ))
    return result


def copy_path_for_session(assets_dir, copied, path):
    source = os.path.abspath(path)
    if source not in copied:
        if not os.path.exists(source):
            raise FileNotFoundError(source)
        destination = os.path.join(assets_dir, unique_asset_name(source))
        shutil.copy2(source, destination)
        copied[source] = destination
    return copied[source]


def copy_project_assets_for_session(session_dir, player):
    assets_dir = os.path.join(session_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    copied = {}
    timeline = []
    for segment in player.timeline:
        destination = copy_path_for_session(assets_dir, copied, segment.path)
        timeline.append(TimelineSegment(
            destination,
            segment.start,
            segment.end,
            float(getattr(segment, "speed", 1.0) or 1.0),
            float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
            copy_path_for_session(assets_dir, copied, getattr(segment, "audio_path", "")) if getattr(segment, "audio_path", "") else "",
            getattr(segment, "audio_start", None),
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
            str(getattr(segment, "transition", "") or ""),
            max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        ))
    visual_items = []
    for item in getattr(player, "visual_items", []):
        copied_item = dict(item)
        copied_item["path"] = copy_path_for_session(assets_dir, copied, item["path"])
        visual_items.append(copied_item)
    background_audio_items = []
    for item in getattr(player, "background_audio_items", []):
        copied_item = dict(item)
        copied_item["path"] = copy_path_for_session(assets_dir, copied, item["path"])
        if copied_item.get("original_path"):
            copied_item["original_path"] = item.get("original_path")
        background_audio_items.append(copied_item)
    b_roll_items = []
    for item in getattr(player, "b_roll_items", []):
        copied_item = dict(item)
        copied_item["path"] = copy_path_for_session(assets_dir, copied, item["path"])
        b_roll_items.append(copied_item)
    sound_effects_items = []
    for item in getattr(player, "sound_effects_items", []):
        copied_item = dict(item)
        copied_item["path"] = copy_path_for_session(assets_dir, copied, item["path"])
        if copied_item.get("original_path"):
            copied_item["original_path"] = item.get("original_path")
        sound_effects_items.append(copied_item)
    main_audio_override_path = getattr(player, "main_audio_override_path", "")
    if main_audio_override_path:
        main_audio_override_path = copy_path_for_session(assets_dir, copied, main_audio_override_path)
    work_images = [copy_path_for_session(assets_dir, copied, path) for path in getattr(player, "work_images", [])]
    work_videos = [copy_path_for_session(assets_dir, copied, path) for path in getattr(player, "work_videos", [])]
    edit_points = []
    for point in getattr(player, "edit_points", []):
        copied_point = dict(point)
        restore_segments = []
        for segment in copied_point.get("restore_segments", []):
            copied_segment = dict(segment)
            copied_segment["path"] = copy_path_for_session(assets_dir, copied, copied_segment["path"])
            if copied_segment.get("audio_path"):
                copied_segment["audio_path"] = copy_path_for_session(assets_dir, copied, copied_segment["audio_path"])
            restore_segments.append(copied_segment)
        copied_point["restore_segments"] = restore_segments
        edit_points.append(copied_point)
    return timeline, visual_items, background_audio_items, b_roll_items, sound_effects_items, work_images, work_videos, edit_points, main_audio_override_path


def session_payload(name, player, timeline, visual_items=None, background_audio_items=None, b_roll_items=None, sound_effects_items=None, work_images=None, work_videos=None, edit_points=None):
    return {
        "name": name,
        "created_at": time.time(),
        "video_path": player.video_path,
        "media_kind": getattr(player, "media_kind", "video"),
        "current_time": player.current_time,
        "start_time": player.start_time,
        "end_time": player.end_time,
        "volume": persisted_program_volume(player.volume),
        "master_volume_db": persisted_master_volume_db(getattr(player, "master_volume_db", 0.0)),
        "seek_step": player.seek_step,
        "metadata": dict(getattr(player, "file_metadata", {})),
        "visual_items": [dict(item) for item in (visual_items if visual_items is not None else getattr(player, "visual_items", []))],
        "background_audio_items": [dict(item) for item in (background_audio_items if background_audio_items is not None else getattr(player, "background_audio_items", []))],
        "b_roll_items": [dict(item) for item in (b_roll_items if b_roll_items is not None else getattr(player, "b_roll_items", []))],
        "sound_effects_items": [dict(item) for item in (sound_effects_items if sound_effects_items is not None else getattr(player, "sound_effects_items", []))],
        "main_audio_override_path": getattr(player, "main_audio_override_path", ""),
        "main_audio_override_duration": getattr(player, "main_audio_override_duration", 0.0),
        "main_audio_override_timeline_duration": getattr(player, "main_audio_override_timeline_duration", 0.0),
        "main_audio_effect_chain": list(getattr(player, "main_audio_effect_chain", []) or []),
        "main_audio_revision": int(getattr(player, "main_audio_revision", 0) or 0),
        "main_audio_source_revision": int(getattr(player, "main_audio_source_revision", 0) or 0),
        "timeline_revision": int(getattr(player, "timeline_revision", 0) or 0),
        "main_audio_format_version": int(getattr(player, "main_audio_format_version", 2) or 2),
        "edit_points": [dict(item) for item in (edit_points if edit_points is not None else getattr(player, "edit_points", []))],
        "work_images": list(work_images if work_images is not None else getattr(player, "work_images", [])),
        "work_videos": list(work_videos if work_videos is not None else getattr(player, "work_videos", [])),
        "default_image_duration": getattr(player, "default_image_duration", 5),
        "transition_name": getattr(player, "transition_name", "بدون انتقال"),
        "last_insert_end": getattr(player, "last_insert_end", None),
        "muted_tracks": list(getattr(player, "muted_tracks", []) or []),
        "solo_tracks": list(getattr(player, "solo_tracks", []) or []),
        "ripple_mode": _normalized_ripple_mode(getattr(player, "ripple_mode", "per_track")),
        "focused_element": dict(getattr(player, "focused_element", None) or {}),
        "selected_element_ids": list(getattr(player, "selected_element_ids", []) or []),
        "timeline": [
            {
                "path": segment.path,
                "start": segment.start,
                "end": segment.end,
                "speed": float(getattr(segment, "speed", 1.0) or 1.0),
                "audio_volume": float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
                "audio_path": str(getattr(segment, "audio_path", "") or ""),
                "audio_start": getattr(segment, "audio_start", None),
                "navigation_group": str(getattr(segment, "navigation_group", "") or ""),
                "source_file_id": str(getattr(segment, "source_file_id", "") or ""),
                "source_file_name": str(getattr(segment, "source_file_name", "") or ""),
                "transition": str(getattr(segment, "transition", "") or ""),
                "transition_duration": max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
                "audio_fade_in": max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
                "audio_fade_out": max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
            }
            for segment in timeline
        ],
    }


def write_session(name, player):
    name = safe_name(name)
    session_dir = session_dir_for_name(name)
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir)
    os.makedirs(session_dir, exist_ok=True)
    saved_timeline, visual_items, background_audio_items, b_roll_items, sound_effects_items, work_images, work_videos, edit_points, main_audio_override_path = copy_project_assets_for_session(session_dir, player)
    payload = session_payload(name, player, saved_timeline, visual_items, background_audio_items, b_roll_items, sound_effects_items, work_images, work_videos, edit_points)
    payload["main_audio_override_path"] = main_audio_override_path
    with open(os.path.join(session_dir, "session.json"), "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return session_dir


def session_json_path(session_dir):
    return os.path.join(session_dir, "session.json")


def read_session(session_dir):
    with open(session_json_path(session_dir), "r", encoding="utf-8") as file:
        payload = json.load(file)
    timeline = [
        TimelineSegment(
            item["path"],
            float(item["start"]),
            float(item["end"]),
            float(item.get("speed", 1.0) or 1.0),
            float(item.get("audio_volume", 1.0) if item.get("audio_volume", 1.0) is not None else 1.0),
            str(item.get("audio_path", "") or ""),
            float(item["audio_start"]) if item.get("audio_start") is not None else None,
            str(item.get("navigation_group", "") or ""),
            str(item.get("source_file_id", "") or ""),
            str(item.get("source_file_name", "") or ""),
            str(item.get("transition", "") or ""),
            max(0.0, float(item.get("transition_duration", 1.0) or 1.0)),
            max(0.0, float(item.get("audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(item.get("audio_fade_out", 0.0) or 0.0)),
        )
        for item in payload.get("timeline", [])
    ]
    payload["timeline"] = timeline
    return payload


def list_sessions():
    result = []
    for name in sorted(os.listdir(sessions_root())):
        path = os.path.join(sessions_root(), name)
        if not os.path.isdir(path) or not os.path.exists(session_json_path(path)):
            continue
        try:
            payload = read_session(path)
            result.append({"name": payload.get("name") or name, "path": path, "created_at": payload.get("created_at", 0)})
        except Exception:
            continue
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def delete_session(session_dir):
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir)


def rename_session(session_dir, new_name):
    payload = read_session(session_dir)
    new_name = safe_name(new_name)
    new_dir = session_dir_for_name(new_name)
    if os.path.abspath(new_dir) != os.path.abspath(session_dir):
        if os.path.exists(new_dir):
            raise FileExistsError(new_name)
        os.rename(session_dir, new_dir)
    payload["name"] = new_name
    with open(session_json_path(new_dir), "w", encoding="utf-8") as file:
        json.dump({
            **payload,
            "timeline": [
                {
                    "path": segment.path,
                    "start": segment.start,
                    "end": segment.end,
                    "speed": float(getattr(segment, "speed", 1.0) or 1.0),
                    "audio_volume": float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
                    "audio_path": str(getattr(segment, "audio_path", "") or ""),
                    "audio_start": getattr(segment, "audio_start", None),
                    "navigation_group": str(getattr(segment, "navigation_group", "") or ""),
                    "source_file_id": str(getattr(segment, "source_file_id", "") or ""),
                    "source_file_name": str(getattr(segment, "source_file_name", "") or ""),
                }
                for segment in payload["timeline"]
            ],
        }, file, ensure_ascii=False, indent=2)
    return new_dir


class RestoreSessionDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="استعادة جلسة العمل", size=(520, 360))
        self.selected_session = None
        self.sessions = []
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.list_box = wx.ListBox(panel)
        self.list_box.SetName(tr("الجلسات المحفوظة"))
        restore_button = wx.Button(panel, label="استعادة")
        cancel_button = wx.Button(panel, label="إلغاء")
        restore_button.SetName(tr("استعادة الجلسة المحددة"))
        cancel_button.SetName(tr("إلغاء"))
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(restore_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(self.list_box, proportion=1, flag=wx.EXPAND | wx.ALL, border=10)
        sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=8)
        panel.SetSizer(sizer)
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self.restore_selected)
        self.list_box.Bind(wx.EVT_CONTEXT_MENU, self.show_context_menu)
        self.list_box.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
        restore_button.Bind(wx.EVT_BUTTON, self.restore_selected)
        cancel_button.Bind(wx.EVT_BUTTON, self.close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key)
        self.refresh()
        self.Centre()
        wx.CallAfter(self.list_box.SetFocus)

    def refresh(self):
        self.sessions = list_sessions()
        self.list_box.Clear()
        for index, session in enumerate(self.sessions, 1):
            self.list_box.Append(f"{index} - {session['name']}")
        if self.sessions:
            self.list_box.SetSelection(0)

    def current_index(self):
        selection = self.list_box.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self.sessions):
            return None
        return selection

    def restore_selected(self, event=None):
        index = self.current_index()
        if index is None:
            wx.MessageBox("لا توجد جلسة محددة.", "استعادة جلسة العمل", wx.OK | wx.ICON_INFORMATION)
            return
        self.selected_session = self.sessions[index]["path"]
        self.EndModal(wx.ID_OK)

    def show_context_menu(self, event):
        index = self.current_index()
        if index is None:
            return
        menu = wx.Menu()
        delete_id = wx.NewIdRef()
        rename_id = wx.NewIdRef()
        menu.Append(delete_id, tr("حذف الجلسة"))
        menu.Append(rename_id, tr("إعادة تسمية الجلسة"))
        self.Bind(wx.EVT_MENU, self.delete_current, id=delete_id)
        self.Bind(wx.EVT_MENU, self.rename_current, id=rename_id)
        self.PopupMenu(menu)
        menu.Destroy()

    def delete_current(self, event=None):
        index = self.current_index()
        if index is None:
            return
        name = self.sessions[index]["name"]
        result = wx.MessageBox(f"هل تريد حذف جلسة {name}؟", "تأكيد الحذف", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if result != wx.YES:
            return
        delete_session(self.sessions[index]["path"])
        self.refresh()

    def rename_current(self, event=None):
        index = self.current_index()
        if index is None:
            return
        old_name = self.sessions[index]["name"]
        dialog = wx.TextEntryDialog(self, "اكتب الاسم الجديد", "إعادة تسمية الجلسة", old_name)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        new_name = safe_name(dialog.GetValue())
        dialog.Destroy()
        try:
            rename_session(self.sessions[index]["path"], new_name)
            self.refresh()
        except FileExistsError:
            wx.MessageBox("توجد جلسة بهذا الاسم.", "تعذر إعادة التسمية", wx.OK | wx.ICON_ERROR)

    def on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.restore_selected()
            return
        if key == wx.WXK_DELETE:
            self.delete_current()
            return
        event.Skip()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.close()
            return
        event.Skip()

    def close(self, event=None):
        self.EndModal(wx.ID_CANCEL)
