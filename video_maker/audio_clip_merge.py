import os

import wx

from video_maker.audio_image_merge import audio_wildcard, natural_sort_key
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import prepare_media_file_dialog, remember_media_paths
from video_maker.localization import tr, tr_format


class AudioClipMergeWindow(wx.Frame):
    def __init__(self, parent, merge_callback):
        super().__init__(parent, title=tr("دمج الملفات الصوتية"), size=(620, 420))
        from video_maker.menus import install_menu_bar

        self.merge_callback = merge_callback
        self.audio_paths = []

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        add_button = wx.Button(panel, label=tr("اختيار ملفات صوتية"))
        self.audio_list = wx.ListBox(panel)
        merge_button = wx.Button(panel, label=tr("دمج"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))

        add_button.SetName(tr("اختيار ملفات صوتية"))
        self.audio_list.SetName(tr("قائمة الملفات الصوتية المختارة"))
        merge_button.SetName(tr("دمج الملفات الصوتية"))
        cancel_button.SetName(tr("إلغاء"))
        merge_button.SetDefault()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(merge_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(add_button, flag=wx.ALIGN_CENTER | wx.TOP, border=12)
        main_sizer.Add(self.audio_list, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=8)

        panel.SetSizer(main_sizer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        add_button.Bind(wx.EVT_BUTTON, self.add_audio_files)
        merge_button.Bind(wx.EVT_BUTTON, self.merge_audio_files)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_window)
        self.audio_list.Bind(wx.EVT_RIGHT_DOWN, self.select_item_under_mouse)
        self.audio_list.Bind(wx.EVT_CONTEXT_MENU, self.show_context_menu)
        self.Bind(wx.EVT_CLOSE, self.close_window)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, preserve_navigation_keys=True)

        self.Centre()
        install_menu_bar(self, parent, include_shortcuts=False)
        wx.CallAfter(add_button.SetFocus)

    def speak(self, message):
        parent = self.GetParent() if hasattr(self, "GetParent") else None
        if hasattr(parent, "say"):
            parent.say(message)

    def add_audio_files(self, event=None):
        with wx.FileDialog(self, tr("اختيار ملفات صوتية"), wildcard=audio_wildcard(), style=wx.FD_OPEN | wx.FD_MULTIPLE) as dialog:
            prepare_media_file_dialog(dialog, "audio", "audio_clip_merge_files")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.audio_paths = sorted(dialog.GetPaths(), key=natural_sort_key)
            remember_media_paths(self.audio_paths, "audio", "audio_clip_merge_files")
            self.refresh_list()
            self.speak(tr_format("تم اختيار {count} ملف صوتي", count=len(self.audio_paths)))

    def focus_audio_list(self, index=None):
        if self.audio_paths:
            selection = index if index is not None else self.audio_list.GetSelection()
            if selection == wx.NOT_FOUND:
                selection = 0
            selection = min(max(selection, 0), len(self.audio_paths) - 1)
            self.audio_list.SetSelection(selection)
        wx.CallAfter(self.audio_list.SetFocus)

    def refresh_list(self, selection=None):
        self.audio_list.Clear()
        for index, path in enumerate(self.audio_paths, start=1):
            self.audio_list.Append(f"{tr('ملف صوتي')} {index} - {os.path.basename(path)}")
        self.focus_audio_list(selection)

    def selected_index(self):
        selection = self.audio_list.GetSelection()
        if selection == wx.NOT_FOUND or selection < 0 or selection >= len(self.audio_paths):
            return None
        return selection

    def select_item_under_mouse(self, event):
        index = self.audio_list.HitTest(event.GetPosition())
        if index != wx.NOT_FOUND:
            self.audio_list.SetSelection(index)
        event.Skip()

    def context_menu_actions(self, index):
        if index is None or index < 0 or index >= len(self.audio_paths):
            return []
        actions = [("delete", tr("حذف"))]
        if index > 0:
            actions.append(("move_up", tr("رفع للأعلى")))
        if index < len(self.audio_paths) - 1:
            actions.append(("move_down", tr("خفض للأسفل")))
        return actions

    def show_context_menu(self, event):
        index = self.selected_index()
        if index is None:
            return

        action_ids = {key: wx.NewIdRef() for key, _label in self.context_menu_actions(index)}
        menu = wx.Menu()
        for key, label in self.context_menu_actions(index):
            menu.Append(action_ids[key], label)

        handlers = {
            "delete": self.delete_selected,
            "move_up": self.move_selected_up,
            "move_down": self.move_selected_down,
        }
        for key, item_id in action_ids.items():
            self.Bind(wx.EVT_MENU, handlers[key], id=item_id)
        self.PopupMenu(menu)
        menu.Destroy()

    def delete_selected(self, event=None):
        index = self.selected_index()
        if index is None:
            return
        del self.audio_paths[index]
        self.refresh_list(min(index, len(self.audio_paths) - 1))

    def move_selected_up(self, event=None):
        index = self.selected_index()
        if index is None or index == 0:
            self.focus_audio_list(index)
            return
        self.audio_paths[index - 1], self.audio_paths[index] = self.audio_paths[index], self.audio_paths[index - 1]
        self.refresh_list(index - 1)

    def move_selected_down(self, event=None):
        index = self.selected_index()
        if index is None or index >= len(self.audio_paths) - 1:
            self.focus_audio_list(index)
            return
        self.audio_paths[index + 1], self.audio_paths[index] = self.audio_paths[index], self.audio_paths[index + 1]
        self.refresh_list(index + 1)

    def merge_audio_files(self, event=None):
        if not self.audio_paths:
            wx.MessageBox(tr("اختر ملفا صوتيا واحدا على الأقل."), tr("بيانات ناقصة"), wx.OK | wx.ICON_ERROR)
            return
        self.merge_callback(list(self.audio_paths))
        self.Destroy()

    def close_window(self, event=None):
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
            return
        event.Skip()
