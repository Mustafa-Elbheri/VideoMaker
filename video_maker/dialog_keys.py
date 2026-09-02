import wx


def bind_dialog_keys(window, handler, excluded_types=(), preserve_navigation_keys=False):
    """Bind a dialog key handler and optionally keep navigation keys local.

    Some child windows live under the main frame, so unhandled Space/arrow keys
    can bubble to the application's playback shortcuts.  Mark only dialogs that
    need native navigation so the main shortcut handler can leave those keys to
    the focused control without changing keyboard behaviour elsewhere.
    """
    if preserve_navigation_keys:
        window._video_maker_preserve_navigation_keys = True
    seen = set()

    def walk(control):
        identifier = id(control)
        if identifier in seen:
            return
        seen.add(identifier)
        if not isinstance(control, excluded_types):
            control.Bind(wx.EVT_CHAR_HOOK, handler)
        for child in control.GetChildren():
            walk(child)

    walk(window)
