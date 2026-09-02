import wx
import os
from video_maker.app_state import get_nav_sounds_mode, get_nav_sounds_custom
from video_maker.app_paths import bundled_sounds_root
from video_maker.ui_sounds import _play_async

# Dictionary of sound categories
# Key: UI Element / Event
# Value: {"name": Arabic Name, "default": default_filename}
NAV_SOUNDS_DEFAULTS = {
    "button_focus": {"name": "صوت التركيز على الأزرار", "default": "button.wav"},
    "checkbox_focus": {"name": "صوت التركيز على مربعات التحديد", "default": "checkbox.wav"},
    "checkbox_toggle": {"name": "صوت تحديد المربعات", "default": "checked.wav"},
    "checkbox_untoggle": {"name": "صوت إلغاء تحديد المربعات", "default": "Unchecked.wav"},
    "text_focus": {"name": "صوت التركيز على مربعات النص", "default": "edit.wav"},
    "list_focus": {"name": "صوت التركيز على قوائم الخيارات (Combo/Choice/List)", "default": "list.wav"},
    "list_item": {"name": "صوت التنقل بين عناصر القوائم والخط الزمني", "default": "item.wav"},
    "menu_open": {"name": "صوت فتح القوائم", "default": "menu-popUp.wav"},
    "menu_item": {"name": "صوت التنقل في القوائم", "default": "menu.wav"},
    "menu_nav": {"name": "صوت التنقل بين القوائم الرئيسية", "default": "menu.wav"},
    "tab_change": {"name": "صوت التنقل بين التبويبات", "default": "ListView.wav"},
    "dialog_open": {"name": "صوت فتح النوافذ", "default": "window_state.wav"}
}

import time
from video_maker.app_state import get_startup_sound

_app_start_time = time.time()

def play_nav_sound(sound_key):
    # Don't play any sounds while startup sound is playing
    if get_startup_sound() != "disable" and time.time() - _app_start_time < 2.0:
        return

    mode = get_nav_sounds_mode()
    if mode == "disable":
        return
        
    custom = get_nav_sounds_custom()
    item_settings = custom.get(sound_key, {})
    
    # If in custom mode, check if enabled
    if mode == "custom":
        if not item_settings.get("enabled", True):
            return
            
    # Resolve path
    file_path = item_settings.get("file", "")
    if not file_path or not os.path.exists(file_path):
        default_file = NAV_SOUNDS_DEFAULTS[sound_key]["default"]
        file_path = os.path.join(bundled_sounds_root(), "Navigation sounds", default_file)
        
    if os.path.exists(file_path):
        _play_async(str(file_path))


class NavigationSoundsFilter(wx.EventFilter):
    def FilterEvent(self, event):
        evt_type = event.GetEventType()
        
        try:
            if evt_type == wx.wxEVT_SET_FOCUS:
                obj = event.GetEventObject()
                if isinstance(obj, wx.Button):
                    play_nav_sound("button_focus")
                elif isinstance(obj, wx.CheckBox):
                    play_nav_sound("checkbox_focus")
                elif isinstance(obj, (wx.TextCtrl, wx.SearchCtrl)):
                    play_nav_sound("text_focus")
                elif isinstance(obj, (wx.Choice, wx.ComboBox, wx.ListBox, wx.ListCtrl)):
                    play_nav_sound("list_focus")
                elif isinstance(obj, wx.Slider):
                    play_nav_sound("list_item")
                
            elif evt_type == wx.wxEVT_COMMAND_CHECKBOX_CLICKED:
                obj = event.GetEventObject()
                if isinstance(obj, wx.CheckBox):
                    if obj.GetValue():
                        play_nav_sound("checkbox_toggle")
                    else:
                        play_nav_sound("checkbox_untoggle")
                        
            elif evt_type == wx.wxEVT_MENU_HIGHLIGHT:
                play_nav_sound("menu_item")
                
            elif evt_type == wx.wxEVT_MENU_OPEN:
                play_nav_sound("menu_nav")
                
            elif evt_type == wx.wxEVT_SHOW:
                obj = event.GetEventObject()
                if isinstance(obj, (wx.Dialog, wx.Frame)) and event.IsShown():
                    play_nav_sound("dialog_open")
                    
            elif evt_type == wx.wxEVT_LISTBOX:
                play_nav_sound("list_item")
                
            elif evt_type == wx.wxEVT_CHOICE:
                play_nav_sound("list_item")
                
            elif evt_type == wx.wxEVT_NOTEBOOK_PAGE_CHANGED:
                play_nav_sound("tab_change")
                
        except Exception:
            pass
            
        return self.Event_Skip

def install_navigation_sounds_hook():
    global _nav_sounds_filter
    if '_nav_sounds_filter' not in globals():
        _nav_sounds_filter = NavigationSoundsFilter()
        wx.EvtHandler.AddFilter(_nav_sounds_filter)
