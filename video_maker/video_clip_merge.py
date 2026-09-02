import os

import wx

from video_maker.audio_image_merge import natural_sort_key
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import prepare_media_file_dialog, remember_media_paths
from video_maker.localization import tr


VIDEO_WILDCARD = "ملفات الفيديو (*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm)|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm"


class VideoClipMergeWindow(wx.Frame):
    def __init__(self, parent, merge_callback):
        super().__init__(parent, title="دمج مقاطع الفيديو", size=(620, 420))
        from video_maker.menus import install_menu_bar

        self.merge_callback = merge_callback
        self.video_paths = []

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.video_list = wx.ListBox(panel)
        self.video_list.SetName(tr("قائمة مقاطع الفيديو المختارة"))

        add_button = wx.Button(panel, label="اختيار مقطع فيديو")
        merge_button = wx.Button(panel, label="دمج")
        cancel_button = wx.Button(panel, label="إلغاء")

        add_button.SetName(tr("اختيار مقطع فيديو"))
        merge_button.SetName(tr("دمج مقاطع الفيديو"))
        cancel_button.SetName(tr("إلغاء"))
        merge_button.SetDefault()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_button, flag=wx.ALL, border=6)
        button_sizer.Add(merge_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(self.video_list, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=6)

        panel.SetSizer(main_sizer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        add_button.Bind(wx.EVT_BUTTON, self.add_video)
        merge_button.Bind(wx.EVT_BUTTON, self.merge_videos)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_window)
        self.video_list.Bind(wx.EVT_RIGHT_DOWN, self.select_item_under_mouse)
        self.video_list.Bind(wx.EVT_CONTEXT_MENU, self.show_context_menu)
        self.Bind(wx.EVT_CLOSE, self.close_window)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, preserve_navigation_keys=True)

        self.Centre()
        install_menu_bar(self, parent, include_shortcuts=False)
        wx.CallAfter(self.video_list.SetFocus)

    def add_video(self, event=None):
        with wx.FileDialog(self, "اختيار مقطع فيديو", wildcard=VIDEO_WILDCARD, style=wx.FD_OPEN | wx.FD_MULTIPLE) as dialog:
            prepare_media_file_dialog(dialog, "video", "video_clip_merge_add_video")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.add_video_paths(dialog.GetPaths())

    def add_video_paths(self, paths):
        selected_paths = sorted([path for path in paths or [] if path], key=natural_sort_key)
        if not selected_paths:
            return 0
        insert_index = len(self.video_paths)
        remember_media_paths(selected_paths, "video", "video_clip_merge_add_video")
        self.video_paths.extend(selected_paths)
        self.refresh_list(insert_index)
        return len(selected_paths)

    def focus_video_list(self, index=None):
        if self.video_paths:
            selection = index if index is not None else self.video_list.GetSelection()
            if selection == wx.NOT_FOUND:
                selection = 0
            selection = min(max(selection, 0), len(self.video_paths) - 1)
            self.video_list.SetSelection(selection)
        wx.CallAfter(self.video_list.SetFocus)

    def refresh_list(self, selection=None):
        self.video_list.Clear()
        for index, path in enumerate(self.video_paths, start=1):
            self.video_list.Append(tr("فيديو {index} - {name}", index=index, name=os.path.basename(path)))
        self.focus_video_list(selection)

    def selected_index(self):
        selection = self.video_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return None
        return selection

    def select_item_under_mouse(self, event):
        index = self.video_list.HitTest(event.GetPosition())
        if index != wx.NOT_FOUND:
            self.video_list.SetSelection(index)
        event.Skip()

    def show_context_menu(self, event):
        index = self.selected_index()
        if index is None:
            return

        delete_id = wx.NewIdRef()
        move_up_id = wx.NewIdRef()
        move_down_id = wx.NewIdRef()
        menu = wx.Menu()
        menu.Append(delete_id, tr("حذف"))
        menu.Append(move_up_id, tr("رفع للأعلى"))
        menu.Append(move_down_id, tr("خفض للأسفل"))

        self.Bind(wx.EVT_MENU, self.delete_selected, id=delete_id)
        self.Bind(wx.EVT_MENU, self.move_selected_up, id=move_up_id)
        self.Bind(wx.EVT_MENU, self.move_selected_down, id=move_down_id)
        self.PopupMenu(menu)
        menu.Destroy()

    def delete_selected(self, event=None):
        index = self.selected_index()
        if index is None:
            return
        del self.video_paths[index]
        self.refresh_list(min(index, len(self.video_paths) - 1))

    def move_selected_up(self, event=None):
        index = self.selected_index()
        if index is None or index == 0:
            self.focus_video_list(index)
            return
        self.video_paths[index - 1], self.video_paths[index] = self.video_paths[index], self.video_paths[index - 1]
        self.refresh_list(index - 1)

    def move_selected_down(self, event=None):
        index = self.selected_index()
        if index is None or index >= len(self.video_paths) - 1:
            self.focus_video_list(index)
            return
        self.video_paths[index + 1], self.video_paths[index] = self.video_paths[index], self.video_paths[index + 1]
        self.refresh_list(index + 1)

    def merge_videos(self, event=None):
        if not self.video_paths:
            wx.MessageBox("اختر مقطع فيديو واحدًا على الأقل.", "بيانات ناقصة", wx.OK | wx.ICON_ERROR)
            return
        self.merge_callback(list(self.video_paths))
        self.Destroy()

    def close_window(self, event=None):
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
            return
        event.Skip()
