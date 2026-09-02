import wx

from video_maker.app_state import get_theme


THEMES = {
    "default": {
        "name": "الافتراضي",
        "background": None,
        "foreground": None,
        "panel": None,
        "surface": None,
        "control": None,
        "control_text": None,
        "readonly": None,
        "button": None,
        "button_text": None,
        "accent": (0, 96, 160),
        "gauge": (0, 120, 215),
        "font_delta": 0,
        "bold_labels": False,
    },
    "dark": {
        "name": "داكن",
        "background": (24, 26, 28),
        "foreground": (244, 246, 248),
        "panel": (31, 34, 37),
        "surface": (38, 42, 46),
        "control": (250, 251, 252),
        "control_text": (0, 0, 0),
        "readonly": (229, 233, 237),
        "button": (52, 58, 64),
        "button_text": (255, 255, 255),
        "accent": (58, 165, 255),
        "gauge": (58, 165, 255),
        "font_delta": 1,
        "bold_labels": False,
    },
    "high_black": {
        "name": "تباين عال أسود",
        "background": (0, 0, 0),
        "foreground": (255, 255, 255),
        "panel": (0, 0, 0),
        "surface": (0, 0, 0),
        "control": (0, 0, 0),
        "control_text": (255, 255, 255),
        "readonly": (0, 0, 0),
        "button": (0, 0, 0),
        "button_text": (255, 255, 0),
        "accent": (255, 255, 0),
        "gauge": (255, 255, 0),
        "font_delta": 3,
        "bold_labels": True,
    },
    "high_light": {
        "name": "تباين عال فاتح",
        "background": (255, 255, 255),
        "foreground": (0, 0, 0),
        "panel": (255, 255, 255),
        "surface": (255, 255, 255),
        "control": (255, 255, 255),
        "control_text": (0, 0, 0),
        "readonly": (255, 255, 255),
        "button": (255, 255, 255),
        "button_text": (0, 0, 0),
        "accent": (0, 0, 160),
        "gauge": (0, 0, 160),
        "font_delta": 3,
        "bold_labels": True,
    },
}


_THEME_HOOKS_INSTALLED = False


def current_theme():
    theme = get_theme()
    return theme if theme in THEMES else "default"


def theme_name(theme_key):
    return THEMES.get(theme_key, THEMES["default"])["name"]


def theme_palette(theme_key=None):
    theme = dict(THEMES.get(theme_key or current_theme(), THEMES["default"]))
    system_background = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
    system_foreground = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
    system_panel = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)
    system_button_text = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNTEXT)
    system_control = wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOX)
    theme["background"] = theme["background"] or colour_tuple(system_panel)
    theme["foreground"] = theme["foreground"] or colour_tuple(system_foreground)
    theme["panel"] = theme["panel"] or theme["background"]
    theme["surface"] = theme["surface"] or theme["panel"]
    theme["control"] = theme["control"] or colour_tuple(system_control)
    theme["control_text"] = theme["control_text"] or theme["foreground"]
    theme["readonly"] = theme["readonly"] or theme["control"]
    theme["button"] = theme["button"] or theme["surface"]
    theme["button_text"] = theme["button_text"] or colour_tuple(system_button_text)
    return theme


def apply_theme(window, theme_key=None):
    palette = theme_palette(theme_key)
    apply_to_window(window, palette, True)
    try:
        window.Layout()
    except Exception:
        pass
    try:
        window.Refresh()
        window.Update()
    except Exception:
        pass


def install_theme_hooks():
    global _THEME_HOOKS_INSTALLED
    if _THEME_HOOKS_INSTALLED:
        return
    _THEME_HOOKS_INSTALLED = True

    original_dialog_show_modal = wx.Dialog.ShowModal
    original_dialog_show = wx.Dialog.Show
    original_frame_show = wx.Frame.Show

    def themed_dialog_show_modal(self, *args, **kwargs):
        apply_theme(self)
        return original_dialog_show_modal(self, *args, **kwargs)

    def themed_dialog_show(self, *args, **kwargs):
        apply_theme(self)
        return original_dialog_show(self, *args, **kwargs)

    def themed_frame_show(self, *args, **kwargs):
        apply_theme(self)
        return original_frame_show(self, *args, **kwargs)

    wx.Dialog.ShowModal = themed_dialog_show_modal
    wx.Dialog.Show = themed_dialog_show
    wx.Frame.Show = themed_frame_show


def apply_to_window(window, palette, root=False):
    if is_media_canvas(window):
        set_window_colours(window, (0, 0, 0), palette["foreground"])
        return

    background, foreground = colours_for_window(window, palette, root)
    set_window_colours(window, background, foreground)
    apply_font(window, palette)
    apply_widget_details(window, palette)

    for child in getattr(window, "GetChildren", lambda: [])():
        apply_to_window(child, palette, False)


def colours_for_window(window, palette, root):
    if isinstance(window, (wx.TextCtrl, wx.ComboBox)):
        return palette["readonly"] if is_readonly_text(window) else palette["control"], palette["control_text"]
    if isinstance(window, (wx.ListCtrl, wx.ListBox, wx.TreeCtrl)):
        return palette["control"], palette["control_text"]
    if isinstance(window, wx.Choice):
        return palette["control"], palette["control_text"]
    if isinstance(window, wx.Button):
        return palette["button"], palette["button_text"]
    if isinstance(window, wx.Gauge):
        return palette["surface"], palette["foreground"]
    if isinstance(window, wx.Panel):
        return palette["panel"], palette["foreground"]
    if root:
        return palette["background"], palette["foreground"]
    return palette["surface"], palette["foreground"]


def apply_widget_details(window, palette):
    if isinstance(window, wx.Gauge):
        try:
            window.SetForegroundColour(to_colour(palette["gauge"]))
        except Exception:
            pass
    if isinstance(window, wx.StaticText) and palette.get("bold_labels"):
        try:
            font = window.GetFont()
            if font.IsOk():
                font.SetWeight(wx.FONTWEIGHT_BOLD)
                window.SetFont(font)
        except Exception:
            pass
    if isinstance(window, (wx.Slider, wx.CheckBox, wx.RadioButton)):
        set_window_colours(window, palette["panel"], palette["foreground"])


def apply_font(window, palette):
    try:
        font = window.GetFont()
        if not font.IsOk():
            return
        if not hasattr(window, "_theme_base_font_size"):
            window._theme_base_font_size = font.GetPointSize()
        size = max(9, int(window._theme_base_font_size) + int(palette["font_delta"]))
        if font.GetPointSize() != size:
            font.SetPointSize(size)
        if not palette.get("bold_labels") and isinstance(window, wx.StaticText):
            font.SetWeight(wx.FONTWEIGHT_NORMAL)
        window.SetFont(font)
    except Exception:
        pass


def set_window_colours(window, background, foreground):
    try:
        window.SetBackgroundColour(to_colour(background))
    except Exception:
        pass
    try:
        window.SetForegroundColour(to_colour(foreground))
    except Exception:
        pass


def is_readonly_text(window):
    try:
        return bool(window.GetWindowStyleFlag() & wx.TE_READONLY)
    except Exception:
        return False


def is_media_canvas(window):
    return window.__class__.__name__ in {"MPVMediaCtrl"}


def colour_tuple(value):
    colour = to_colour(value)
    return (colour.Red(), colour.Green(), colour.Blue())


def to_colour(value):
    if isinstance(value, wx.Colour):
        return value
    if isinstance(value, tuple):
        return wx.Colour(*value)
    return wx.Colour(value)


def relative_luminance(value):
    rgb = []
    for channel in colour_tuple(value):
        normal = channel / 255.0
        if normal <= 0.03928:
            rgb.append(normal / 12.92)
        else:
            rgb.append(((normal + 0.055) / 1.055) ** 2.4)
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast_ratio(first, second):
    l1 = relative_luminance(first)
    l2 = relative_luminance(second)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
