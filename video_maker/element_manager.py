import os

import wx

from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr


ELEMENT_TYPE_NAMES = {
    "image": "صورة",
    "text": "نص",
    "video": "فيديو",
}


def element_type_name(item_type):
    return tr(ELEMENT_TYPE_NAMES.get(item_type, "عنصر"))


def element_action_name(item_type):
    if item_type == "image":
        return tr("هذه الصورة")
    if item_type == "text":
        return tr("هذا النص")
    if item_type == "video":
        return tr("هذا الفيديو")
    return tr("هذا العنصر")


def spoken_time(value):
    value = max(0.0, float(value or 0.0))
    if abs(value - round(value)) <= 0.01:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def element_list_label(index, item):
    item_type = element_type_name(item.get("type"))
    start = spoken_time(item.get("start", 0))
    end = spoken_time(item.get("end", 0))
    path = str(item.get("path", "") or "")
    name = os.path.basename(path) if path and item.get("type") != "text" else ""
    if name:
        return tr("{number} {type} {name} من الثانية {start} إلى الثانية {end}").format(
            number=index,
            type=item_type,
            name=name,
            start=start,
            end=end,
        )
    return tr("{number} {type} من الثانية {start} إلى الثانية {end}").format(
        number=index,
        type=item_type,
        start=start,
        end=end,
    )


def visual_neighbor_indexes(items, target_index):
    previous_index = None
    next_index = None
    target = items[target_index]
    target_start = float(target.get("start", 0) or 0)
    target_end = float(target.get("end", 0) or 0)
    for index in range(target_index - 1, -1, -1):
        if float(items[index].get("end", 0) or 0) <= target_start + 0.03:
            previous_index = index
            break
    for index in range(target_index + 1, len(items)):
        if float(items[index].get("start", 0) or 0) >= target_end - 0.03:
            next_index = index
            break
    return previous_index, next_index


def compensate_deleted_visual_item(items, item_id, mode):
    ordered = sorted([dict(item) for item in items], key=lambda item: (float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)))
    target_index = next((index for index, item in enumerate(ordered) if item.get("id") == item_id), None)
    if target_index is None:
        return ordered
    target = ordered[target_index]
    start = float(target.get("start", 0) or 0)
    end = float(target.get("end", start) or start)
    duration = max(0.0, end - start)
    previous_index, next_index = visual_neighbor_indexes(ordered, target_index)
    use_previous = mode in ("previous", "both") and previous_index is not None
    use_next = mode in ("next", "both") and next_index is not None
    if mode == "both" and use_previous and use_next:
        middle = start + duration / 2.0
        next_old_start = float(ordered[next_index].get("start", middle) or middle)
        ordered[previous_index]["end"] = max(float(ordered[previous_index].get("start", 0) or 0), middle)
        ordered[next_index]["start"] = min(float(ordered[next_index].get("end", middle) or middle), middle)
        speed = max(0.05, float(ordered[next_index].get("speed", 1.0) or 1.0))
        offset = max(0.0, float(ordered[next_index].get("source_offset", 0.0) or 0.0))
        ordered[next_index]["source_offset"] = max(0.0, offset - max(0.0, next_old_start - middle) * speed)
    elif use_previous:
        ordered[previous_index]["end"] = max(float(ordered[previous_index].get("start", 0) or 0), end)
    elif use_next:
        ordered[next_index]["start"] = min(float(ordered[next_index].get("end", start) or start), start)
        speed = max(0.05, float(ordered[next_index].get("speed", 1.0) or 1.0))
        offset = max(0.0, float(ordered[next_index].get("source_offset", 0.0) or 0.0))
        ordered[next_index]["source_offset"] = max(0.0, offset - duration * speed)
    return [item for index, item in enumerate(ordered) if index != target_index]


class ElementManagerWindow(wx.Frame):
    def __init__(self, parent):
        super().__init__(parent, title=tr("مدير العناصر"), size=(680, 420))
        from video_maker.menus import install_menu_bar

        self.parent = parent
        self.items = []

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.element_list = wx.ListBox(panel)
        self.element_list.SetName(tr("قائمة العناصر المضافة على الخط الزمني"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))
        cancel_button.SetName(tr("إغلاق مدير العناصر"))

        main_sizer.Add(self.element_list, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)
        main_sizer.Add(cancel_button, flag=wx.ALIGN_CENTER | wx.ALL, border=8)
        panel.SetSizer(main_sizer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        self.element_list.Bind(wx.EVT_RIGHT_DOWN, self.select_item_under_mouse)
        self.element_list.Bind(wx.EVT_CONTEXT_MENU, self.show_context_menu)
        self.element_list.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
        self.element_list.Bind(wx.EVT_LISTBOX_DCLICK, self.jump_to_selected)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_window)
        self.Bind(wx.EVT_CLOSE, self.close_window)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, preserve_navigation_keys=True)

        self.refresh_items()
        self.Centre()
        install_menu_bar(self, parent, include_shortcuts=False)
        wx.CallAfter(self.element_list.SetFocus)

    def refresh_items(self, keep_id=None):
        previous_id = keep_id
        if previous_id is None:
            selected = self.selected_item()
            previous_id = selected.get("id") if selected else None
        self.items = self.parent.element_manager_items()
        self.element_list.Clear()
        for index, item in enumerate(self.items, start=1):
            self.element_list.Append(element_list_label(index, item))
        if self.items:
            selection = 0
            if previous_id:
                selection = next((index for index, item in enumerate(self.items) if item.get("id") == previous_id), 0)
            self.element_list.SetSelection(selection)

    def selected_index(self):
        selection = self.element_list.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self.items):
            return None
        return selection

    def selected_item(self):
        index = self.selected_index()
        if index is None:
            return None
        return self.items[index]

    def select_item_under_mouse(self, event):
        index = self.element_list.HitTest(event.GetPosition())
        if index != wx.NOT_FOUND:
            self.element_list.SetSelection(index)
        event.Skip()

    def on_list_key(self, event):
        key = event.GetKeyCode()
        apps_keys = {
            getattr(wx, "WXK_WINDOWS_MENU", 395),
            getattr(wx, "WXK_APPS", 395),
        }
        if key in apps_keys or (event.ShiftDown() and key == wx.WXK_F10):
            self.show_context_menu(event)
            return
        event.Skip()

    def append_menu_item(self, menu, label, handler):
        item_id = wx.NewIdRef()
        menu.Append(item_id, label)
        self.Bind(wx.EVT_MENU, handler, id=item_id)

    def show_context_menu(self, event):
        item = self.selected_item()
        if not item:
            self.parent.say(tr("لا توجد عناصر مضافة على الخط الزمني"))
            return
        action_name = element_action_name(item.get("type"))
        media_kind = getattr(self.parent, "media_kind", "video")
        menu = wx.Menu()
        self.append_menu_item(menu, tr("حذف {item}").format(item=action_name), lambda evt: self.delete_selected(""))
        if media_kind == "audio":
            self.append_menu_item(menu, tr("حذف {item} مع تعويض مدته من العنصر السابق").format(item=action_name), lambda evt: self.delete_selected("previous"))
            self.append_menu_item(menu, tr("حذف {item} مع تعويض مدته من العنصر التالي").format(item=action_name), lambda evt: self.delete_selected("next"))
            self.append_menu_item(menu, tr("حذف {item} مع تعويض مدته من العنصر السابق والتالي").format(item=action_name), lambda evt: self.delete_selected("both"))
        self.append_menu_item(menu, tr("استبدال {item}").format(item=action_name), self.replace_selected)
        self.append_menu_item(menu, tr("إضافة تأثير انتقال هنا"), self.set_transition_for_selected)
        self.PopupMenu(menu)
        menu.Destroy()

    def delete_selected(self, compensation_mode=""):
        item = self.selected_item()
        if not item:
            return
        self.parent.delete_element_manager_item(item, compensation_mode)
        self.refresh_items()
        self.element_list.SetFocus()

    def replace_selected(self, event=None):
        item = self.selected_item()
        if not item:
            return
        self.parent.replace_element_manager_item(item)
        self.refresh_items(item.get("id"))
        self.element_list.SetFocus()

    def set_transition_for_selected(self, event=None):
        item = self.selected_item()
        if not item:
            return
        self.parent.OnTransitionEffects(manager_item=item)
        self.refresh_items(item.get("id"))
        self.element_list.SetFocus()

    def jump_to_selected(self, event=None):
        item = self.selected_item()
        if item:
            self.parent.jump_to_element_manager_item(item)

    def close_window(self, event=None):
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
            return
        event.Skip()
