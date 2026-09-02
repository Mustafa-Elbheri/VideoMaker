import os
import re
import threading

import wx

from video_maker import speech_messages
from video_maker.app_state import get_last_dialog_dir, get_last_media_dir, get_last_open_dir, get_last_save_dir, set_last_dialog_dir, set_last_media_dir, set_last_open_dir, set_last_save_dir
from video_maker.localization import tr
from video_maker.save_options import (
    AUDIO_CHANNEL_MODES,
    AUDIO_FORMATS,
    VIDEO_FORMATS,
    VIDEO_QUALITY_PRESETS,
    VIDEO_SIZE_PRESETS,
    audio_quality_label,
    format_key_for_extension,
    normalized_save_options,
    probe_source_profile,
    video_quality_label,
    video_size_label,
)


VIDEO_WILDCARD = "ملفات الفيديو (*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm)|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm"
AUDIO_WILDCARD = "ملفات الصوت (*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac;*.wma;*.opus;*.aiff;*.aif)|*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac;*.wma;*.opus;*.aiff;*.aif"
GENERAL_WILDCARD = "كل الملفات المدعومة|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm;*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac;*.wma;*.opus;*.aiff;*.aif;*.jpg;*.jpeg;*.png;*.bmp;*.webp|ملفات الفيديو|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm|ملفات الصوت|*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac;*.wma;*.opus;*.aiff;*.aif|ملفات الصور|*.jpg;*.jpeg;*.png;*.bmp;*.webp"
MP4_WILDCARD = "ملفات MP4 (*.mp4)|*.mp4"
IMAGE_WILDCARD = "ملفات الصور (*.jpg;*.jpeg;*.png;*.bmp;*.webp)|*.jpg;*.jpeg;*.png;*.bmp;*.webp"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".opus", ".aiff", ".aif"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm"}


def media_kind_for_path(path):
    extension = os.path.splitext(str(path or ""))[1].lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return ""


def natural_sort_key(path):
    name = os.path.basename(str(path or ""))
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def initial_media_dir(kind="", dialog_key=""):
    if dialog_key:
        return get_last_dialog_dir(dialog_key, kind)
    return get_last_media_dir(kind) or get_last_open_dir()


def remember_media_path(path, kind="", dialog_key=""):
    if not path:
        return
    resolved_kind = str(kind or "").lower() or media_kind_for_path(path)
    if dialog_key:
        set_last_dialog_dir(dialog_key, path, resolved_kind)
        return
    if resolved_kind:
        set_last_media_dir(resolved_kind, path)
        return
    set_last_open_dir(path)


def prepare_media_file_dialog(dialog, kind="", dialog_key=""):
    last_dir = initial_media_dir(kind, dialog_key)
    if last_dir:
        dialog.SetDirectory(last_dir)


def remember_media_paths(paths, kind="", dialog_key=""):
    for path in paths or []:
        remember_media_path(path, kind, dialog_key)


def _speak_callback_after_ui(speech_callback, text, interrupt=False):
    try:
        speech_callback(text, interrupt, False)
    except TypeError:
        try:
            speech_callback(text, interrupt)
        except TypeError:
            speech_callback(text)


def _speak_later(owner, speech_callback, text):
    if not speech_callback or not text:
        return
    previous = getattr(owner, "_speech_timer", None)
    if previous is not None:
        try:
            previous.Stop()
        except Exception:
            pass
    try:
        owner._speech_timer = wx.CallLater(
            220, _speak_callback_after_ui, speech_callback, text, False
        )
    except Exception:
        try:
            wx.CallAfter(_speak_callback_after_ui, speech_callback, text, False)
        except Exception:
            try:
                _speak_callback_after_ui(speech_callback, text, False)
            except Exception:
                pass


def _event_skip(event):
    if event is not None and hasattr(event, "Skip"):
        event.Skip()


def _selection_index(choice, maximum):
    try:
        index = choice.GetSelection()
    except Exception:
        index = 0
    if index == getattr(wx, "NOT_FOUND", -1) or index < 0 or index >= maximum:
        return 0
    return index


def _show_control(control, shown):
    if control is None:
        return
    try:
        control.Show(bool(shown))
    except Exception:
        try:
            if shown:
                control.Show()
            else:
                control.Hide()
        except Exception:
            pass


def _relayout(window):
    if window is None:
        return
    try:
        window.Layout()
    except Exception:
        pass
    try:
        parent = window.GetParent()
    except Exception:
        parent = None
    if parent is not None:
        try:
            parent.Layout()
        except Exception:
            pass


def quick_source_profile(path):
    return {
        "path": path or "",
        "extension": os.path.splitext(path or "")[1].lower(),
        "width": None,
        "height": None,
        "video_bitrate": None,
        "audio_bitrate": None,
        "channels": None,
        "sample_rate": None,
        "fps": None,
    }



_CustomizeHookBase = getattr(wx, "FileDialogCustomizeHook", object)
if not isinstance(_CustomizeHookBase, type):
    _CustomizeHookBase = object


class VideoSaveCustomizeHook(_CustomizeHookBase):
    """Accessible controls for modern native file dialogs.

    Some wxPython builds expose ``SetCustomizeHook`` but not
    ``SetExtraControlCreator``.  The custom controls are platform-owned, so
    they are kept present and the placement choice is disabled while the
    original dimensions are selected.  This is more reliable on Windows than
    hiding and showing a native custom control after the dialog is open.
    """

    SPEECH_DELAY_MS = 420

    def __init__(self, speech_callback=None):
        if _CustomizeHookBase is not object:
            super().__init__()
        self.speech_callback = speech_callback
        self.dimension_choice = None
        self.placement_label = None
        self.placement_choice = None
        self.result_options = normalized_save_options()
        self._speech_timer = None
        self._pending_speech = ""

    def AddCustomControls(self, customizer):
        customizer.AddStaticText(tr("أبعاد الفيديو عند الحفظ"))
        self.dimension_choice = customizer.AddChoice(
            [video_size_label(preset) for preset in VIDEO_SIZE_PRESETS]
        )
        self.dimension_choice.SetSelection(0)
        self.dimension_choice.Bind(wx.EVT_CHOICE, self.on_dimension_changed)

        self.placement_label = customizer.AddStaticText(
            tr("طريقة وضع المحتوى داخل الأبعاد الجديدة")
        )
        self.placement_choice = customizer.AddChoice(
            [
                tr("إظهار المحتوى كاملا"),
                tr("ملء الإطار مع القص"),
            ]
        )
        self.placement_choice.SetSelection(0)
        self.placement_choice.Bind(wx.EVT_CHOICE, self.on_placement_changed)
        self._set_placement_enabled(False)

    def selected_preset(self):
        return VIDEO_SIZE_PRESETS[
            _selection_index(self.dimension_choice, len(VIDEO_SIZE_PRESETS))
        ]

    def selected_placement(self):
        if self.placement_choice is None:
            return "fit"
        return "fill" if _selection_index(self.placement_choice, 2) == 1 else "fit"

    def _set_placement_enabled(self, enabled):
        enabled = bool(enabled)
        if not enabled and self.placement_choice is not None:
            try:
                self.placement_choice.SetSelection(0)
            except Exception:
                pass
        for control in (self.placement_label, self.placement_choice):
            if control is None:
                continue
            try:
                control.Enable(enabled)
            except Exception:
                try:
                    if enabled:
                        control.Enable()
                    else:
                        control.Disable()
                except Exception:
                    pass

    def _speak_now(self):
        text = self._pending_speech
        self._pending_speech = ""
        if not text or not self.speech_callback:
            return
        try:
            _speak_callback_after_ui(self.speech_callback, text, False)
        except Exception:
            pass

    def _queue_speech(self, text):
        if not text or not self.speech_callback:
            return
        self._pending_speech = text
        previous = self._speech_timer
        if previous is not None:
            try:
                previous.Stop()
            except Exception:
                pass
        try:
            self._speech_timer = wx.CallLater(
                self.SPEECH_DELAY_MS, self._speak_now
            )
        except Exception:
            try:
                wx.CallAfter(self._speak_now)
            except Exception:
                self._speak_now()

    def on_dimension_changed(self, event=None):
        preset = self.selected_preset()
        self._set_placement_enabled(bool(preset["size"]))
        self._queue_speech(
            speech_messages.VIDEO_SIZE_DESCRIPTIONS.get(preset["key"], "")
        )
        _event_skip(event)

    def on_placement_changed(self, event=None):
        self._queue_speech(
            speech_messages.VIDEO_PLACEMENT_DESCRIPTIONS.get(
                self.selected_placement(), ""
            )
        )
        _event_skip(event)

    def TransferDataFromCustomControls(self):
        preset = self.selected_preset()
        self.result_options = normalized_save_options(
            preset["key"], self.selected_placement()
        )


class VideoSaveExtraPanel(wx.Panel):
    """Accessible video save controls embedded in the same file dialog.

    This panel deliberately uses real wx controls through
    ``SetExtraControlCreator``.  The newer native customization hook exposes
    platform-owned pseudo-controls which cannot be given reliable accessible
    names and whose dynamic layout is controlled by Windows.
    """

    SPEECH_DELAY_MS = 420

    def __init__(self, parent, speech_callback=None):
        super().__init__(parent)
        self.speech_callback = speech_callback
        self.result_options = normalized_save_options()
        self._speech_timer = None
        self._pending_speech = ""

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.dimensions_label = wx.StaticText(
            self, label=tr("أبعاد الفيديو عند الحفظ")
        )
        self.main_sizer.Add(self.dimensions_label, 0, wx.BOTTOM, 4)

        self.dimension_choice = wx.Choice(
            self,
            choices=[video_size_label(preset) for preset in VIDEO_SIZE_PRESETS],
        )
        self.dimension_choice.SetName(tr("أبعاد الفيديو عند الحفظ"))
        self.dimension_choice.SetSelection(0)
        self.main_sizer.Add(
            self.dimension_choice, 0, wx.EXPAND | wx.BOTTOM, 8
        )

        self.placement_panel = wx.Panel(self)
        placement_sizer = wx.BoxSizer(wx.VERTICAL)
        self.placement_label = wx.StaticText(
            self.placement_panel,
            label=tr("طريقة وضع المحتوى داخل الأبعاد الجديدة"),
        )
        placement_sizer.Add(self.placement_label, 0, wx.BOTTOM, 4)
        self.placement_choice = wx.Choice(
            self.placement_panel,
            choices=[tr("إظهار المحتوى كاملا"), tr("ملء الإطار مع القص")],
        )
        self.placement_choice.SetName(
            tr("طريقة وضع المحتوى داخل الأبعاد الجديدة")
        )
        self.placement_choice.SetSelection(0)
        placement_sizer.Add(self.placement_choice, 0, wx.EXPAND)
        self.placement_panel.SetSizer(placement_sizer)
        try:
            self.placement_panel.SetMinSize(self.placement_panel.GetBestSize())
        except Exception:
            pass
        self.main_sizer.Add(self.placement_panel, 0, wx.EXPAND)

        self.SetSizer(self.main_sizer)
        self._update_accessibility_description()
        self._update_placement_visibility(False)

        self.dimension_choice.Bind(wx.EVT_CHOICE, self.on_dimension_changed)
        self.placement_choice.Bind(wx.EVT_CHOICE, self.on_placement_changed)
        focus_event = getattr(wx, "EVT_SET_FOCUS", None)
        if focus_event is not None:
            self.dimension_choice.Bind(focus_event, self.on_dimension_focus)
            self.placement_choice.Bind(focus_event, self.on_placement_focus)

    def selected_preset(self):
        return VIDEO_SIZE_PRESETS[
            _selection_index(self.dimension_choice, len(VIDEO_SIZE_PRESETS))
        ]

    def selected_placement(self):
        return "fill" if _selection_index(self.placement_choice, 2) == 1 else "fit"

    def _set_help_text(self, control, text):
        if control is None:
            return
        translated = tr(text) if text else ""
        try:
            control.SetHelpText(translated)
        except Exception:
            pass
        try:
            control.SetToolTip(translated or None)
        except Exception:
            pass

    def _update_accessibility_description(self):
        preset = self.selected_preset()
        self._set_help_text(
            self.dimension_choice,
            speech_messages.VIDEO_SIZE_DESCRIPTIONS.get(preset["key"], ""),
        )
        self._set_help_text(
            self.placement_choice,
            speech_messages.VIDEO_PLACEMENT_DESCRIPTIONS.get(
                self.selected_placement(), ""
            ),
        )

    def _show_panel_control(self, control, visible):
        if control is None:
            return
        try:
            control.Show(bool(visible))
        except Exception:
            if visible:
                try:
                    control.Show()
                except Exception:
                    pass
            else:
                try:
                    control.Hide()
                except Exception:
                    pass

    def _update_placement_visibility(self, visible):
        visible = bool(visible)
        if not visible:
            try:
                self.placement_choice.SetSelection(0)
            except Exception:
                pass

        self._show_panel_control(self.placement_label, visible)
        self._show_panel_control(self.placement_choice, visible)
        try:
            self.placement_panel.Layout()
        except Exception:
            pass
        try:
            self.Layout()
        except Exception:
            pass
        try:
            top = wx.GetTopLevelParent(self)
            if top is not None:
                top.Layout()
                top.Refresh()
        except Exception:
            pass

    def _speak_now(self):
        text = self._pending_speech
        self._pending_speech = ""
        if not text or not self.speech_callback:
            return
        try:
            _speak_callback_after_ui(self.speech_callback, text, False)
        except Exception:
            pass

    def _queue_speech(self, text):
        if not text or not self.speech_callback:
            return
        self._pending_speech = text
        previous = self._speech_timer
        if previous is not None:
            try:
                previous.Stop()
            except Exception:
                pass
        try:
            self._speech_timer = wx.CallLater(
                self.SPEECH_DELAY_MS, self._speak_now
            )
        except Exception:
            try:
                wx.CallAfter(self._speak_now)
            except Exception:
                self._speak_now()

    def on_dimension_focus(self, event=None):
        preset = self.selected_preset()
        self._queue_speech(
            speech_messages.VIDEO_SIZE_DESCRIPTIONS.get(preset["key"], "")
        )
        _event_skip(event)

    def on_placement_focus(self, event=None):
        self._queue_speech(
            speech_messages.VIDEO_PLACEMENT_DESCRIPTIONS.get(
                self.selected_placement(), ""
            )
        )
        _event_skip(event)

    def on_dimension_changed(self, event=None):
        preset = self.selected_preset()
        self._update_placement_visibility(bool(preset["size"]))
        self._update_accessibility_description()
        self._queue_speech(
            speech_messages.VIDEO_SIZE_DESCRIPTIONS.get(preset["key"], "")
        )
        _event_skip(event)

    def on_placement_changed(self, event=None):
        self._update_accessibility_description()
        self._queue_speech(
            speech_messages.VIDEO_PLACEMENT_DESCRIPTIONS.get(
                self.selected_placement(), ""
            )
        )
        _event_skip(event)

    def collect_options(self):
        preset = self.selected_preset()
        self.result_options = normalized_save_options(
            preset["key"], self.selected_placement()
        )
        return dict(self.result_options)


class AccessibleMediaSaveDialog(wx.Dialog):
    """Accessible audio/video export dialog made entirely from wx controls."""

    ANNOUNCEMENT_SETTLE_MS = 450
    MINIMUM_WIDTH = 780

    def __init__(self, parent=None, speech_callback=None, selected=False, media_kind="video", source_path=""):
        self.media_kind = "audio" if media_kind == "audio" else "video"
        self.source_path = source_path or ""
        self.source_profile = quick_source_profile(self.source_path)
        self._source_profile_load_started = False
        self._source_profile_ready = False
        if self.media_kind == "audio":
            title = tr("حفظ الجزء المحدد من الصوت") if selected else tr("حفظ الصوت")
        else:
            title = tr("حفظ الجزء المحدد من الفيديو") if selected else tr("حفظ الفيديو")
        style = getattr(wx, "DEFAULT_DIALOG_STYLE", 0) | getattr(wx, "RESIZE_BORDER", 0)
        super().__init__(parent, title=title, style=style)

        self.speech_callback = speech_callback
        self.result_path = ""
        self.result_options = normalized_save_options(media_kind=self.media_kind, source_profile=self.source_profile)
        self._pending_speech = ""
        self._pending_speech_control = None
        self._speech_serial = 0
        self._speech_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_speech_timer, self._speech_timer)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        location_text = tr("اختر مكان حفظ الصوت") if self.media_kind == "audio" else tr("اختر مكان حفظ الفيديو")
        filename_text = tr("اسم ملف الصوت") if self.media_kind == "audio" else tr("اسم ملف الفيديو")

        folder_label = wx.StaticText(self, label=location_text)
        main_sizer.Add(folder_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        folder_row = wx.BoxSizer(wx.HORIZONTAL)
        self.folder_text = wx.TextCtrl(self, value=self._default_directory())
        self._name_control(self.folder_text, location_text)
        folder_row.Add(self.folder_text, 1, wx.EXPAND | wx.RIGHT, 8)
        self.browse_button = wx.Button(self, label=tr("استعراض"))
        self._name_control(self.browse_button, tr("استعراض"))
        folder_row.Add(self.browse_button, 0)
        main_sizer.Add(folder_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        filename_label = wx.StaticText(self, label=filename_text)
        main_sizer.Add(filename_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.filename_text = wx.TextCtrl(self, value=self._default_filename())
        self._name_control(self.filename_text, filename_text)
        main_sizer.Add(self.filename_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        format_label = wx.StaticText(self, label=tr("امتداد الملف عند الحفظ"))
        main_sizer.Add(format_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.format_choice = wx.Choice(self, choices=self._format_labels())
        self._name_control(self.format_choice, tr("امتداد الملف عند الحفظ"))
        self.format_choice.SetSelection(self._default_format_index())
        main_sizer.Add(self.format_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        quality_label = wx.StaticText(self, label=tr("جودة الملف عند الحفظ"))
        main_sizer.Add(quality_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.quality_choice = wx.Choice(self, choices=[])
        self._name_control(self.quality_choice, tr("جودة الملف عند الحفظ"))
        main_sizer.Add(self.quality_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self.channels_panel = wx.Panel(self)
        channels_sizer = wx.BoxSizer(wx.VERTICAL)
        self.channels_label = wx.StaticText(self.channels_panel, label=tr("قنوات الصوت عند الحفظ"))
        channels_sizer.Add(self.channels_label, 0, wx.BOTTOM, 6)
        self.channels_choice = wx.Choice(
            self.channels_panel,
            choices=[tr(mode["label"]) for mode in AUDIO_CHANNEL_MODES],
        )
        self._name_control(self.channels_choice, tr("قنوات الصوت عند الحفظ"))
        self.channels_choice.SetSelection(1)
        channels_sizer.Add(self.channels_choice, 0, wx.EXPAND)
        self.channels_panel.SetSizer(channels_sizer)
        main_sizer.Add(self.channels_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self.video_options_panel = wx.Panel(self)
        video_options_sizer = wx.BoxSizer(wx.VERTICAL)
        dimensions_label = wx.StaticText(self.video_options_panel, label=tr("أبعاد الفيديو عند الحفظ"))
        video_options_sizer.Add(dimensions_label, 0, wx.BOTTOM, 6)
        self.dimension_choice = wx.Choice(
            self.video_options_panel,
            choices=[video_size_label(preset, self.source_profile) for preset in VIDEO_SIZE_PRESETS],
        )
        self._name_control(self.dimension_choice, tr("أبعاد الفيديو عند الحفظ"))
        self.dimension_choice.SetSelection(0)
        video_options_sizer.Add(self.dimension_choice, 0, wx.EXPAND)

        self.placement_panel = wx.Panel(self.video_options_panel)
        placement_sizer = wx.BoxSizer(wx.VERTICAL)
        self.placement_label = wx.StaticText(
            self.placement_panel,
            label=tr("طريقة وضع المحتوى داخل الأبعاد الجديدة"),
        )
        placement_sizer.Add(self.placement_label, 0, wx.BOTTOM, 6)
        self.placement_choice = wx.Choice(
            self.placement_panel,
            choices=[tr("إظهار المحتوى كاملا"), tr("ملء الإطار مع القص")],
        )
        self._name_control(self.placement_choice, tr("طريقة وضع المحتوى داخل الأبعاد الجديدة"))
        self.placement_choice.SetSelection(0)
        placement_sizer.Add(self.placement_choice, 0, wx.EXPAND)
        self.placement_panel.SetSizer(placement_sizer)
        video_options_sizer.Add(self.placement_panel, 0, wx.EXPAND | wx.TOP, 12)
        self.video_options_panel.SetSizer(video_options_sizer)
        main_sizer.Add(self.video_options_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.AddStretchSpacer(1)
        self.save_button = wx.Button(self, wx.ID_OK, tr("حفظ"))
        self._name_control(self.save_button, tr("حفظ"))
        self.save_button.SetDefault()
        button_row.Add(self.save_button, 0, wx.RIGHT, 8)
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, tr("إلغاء"))
        self._name_control(self.cancel_button, tr("إلغاء"))
        button_row.Add(self.cancel_button, 0)
        main_sizer.Add(button_row, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(main_sizer)
        self.browse_button.Bind(wx.EVT_BUTTON, self.on_browse)
        self.save_button.Bind(wx.EVT_BUTTON, self.on_save)
        self.format_choice.Bind(wx.EVT_CHOICE, self.on_format_changed)
        self.quality_choice.Bind(wx.EVT_CHOICE, self.on_quality_changed)
        self.channels_choice.Bind(wx.EVT_CHOICE, self.on_channels_changed)
        self.dimension_choice.Bind(wx.EVT_CHOICE, self.on_dimension_changed)
        self.placement_choice.Bind(wx.EVT_CHOICE, self.on_placement_changed)
        for control, handler in (
            (self.format_choice, self.on_format_focus),
            (self.quality_choice, self.on_quality_focus),
            (self.channels_choice, self.on_channels_focus),
            (self.dimension_choice, self.on_dimension_focus),
            (self.placement_choice, self.on_placement_focus),
        ):
            control.Bind(wx.EVT_SET_FOCUS, handler)
            control.Bind(wx.EVT_KILL_FOCUS, self.on_description_control_blur)
        for control in (
            self.folder_text,
            self.browse_button,
            self.filename_text,
            self.save_button,
            self.cancel_button,
        ):
            control.Bind(wx.EVT_SET_FOCUS, self.on_plain_control_focus)
            control.Bind(wx.EVT_KILL_FOCUS, self.on_plain_control_focus)
        close_event = getattr(wx, "EVT_CLOSE", None)
        if close_event is not None:
            self.Bind(close_event, self.on_close)

        self._populate_quality_choices()
        self._show_media_controls()
        self._set_placement_visible(False, resize=False)
        self._update_help_texts()
        self._resize_to_contents()
        try:
            self.CentreOnParent()
        except Exception:
            try:
                self.Centre()
            except Exception:
                pass
        try:
            self.filename_text.SetFocus()
        except Exception:
            pass
        try:
            self.Bind(wx.EVT_SHOW, self.on_show)
        except Exception:
            pass

    @staticmethod
    def _name_control(control, text):
        try:
            control.SetName(text)
        except Exception:
            pass
        if hasattr(control, "SetAccessibleName"):
            try:
                control.SetAccessibleName(text)
            except Exception:
                pass

    def _default_directory(self):
        last_dir = get_last_save_dir()
        if last_dir and os.path.isdir(last_dir):
            return last_dir
        try:
            documents = wx.StandardPaths.Get().GetDocumentsDir()
        except Exception:
            documents = ""
        if documents and os.path.isdir(documents):
            return documents
        return os.path.expanduser("~")

    def _formats(self):
        return AUDIO_FORMATS if self.media_kind == "audio" else VIDEO_FORMATS

    def _format_labels(self):
        return [item["label"] for item in self._formats()]

    def _default_format_index(self):
        if self.media_kind == "audio":
            key = "mp3"
        else:
            key = format_key_for_extension(VIDEO_FORMATS, self.source_profile.get("extension"), "mp4")
        return next((index for index, item in enumerate(self._formats()) if item["key"] == key), 0)

    def selected_format(self):
        formats = self._formats()
        return formats[_selection_index(self.format_choice, len(formats))]

    def _default_filename(self):
        stem = os.path.splitext(os.path.basename(self.source_path))[0].strip() or "output"
        invalid = '<>:"/\\|?*'
        stem = "".join("_" if character in invalid else character for character in stem).rstrip(" .") or "output"
        if self.media_kind == "audio":
            extension = ".mp3"
        else:
            key = format_key_for_extension(VIDEO_FORMATS, self.source_profile.get("extension"), "mp4")
            extension = next((item["extension"] for item in VIDEO_FORMATS if item["key"] == key), ".mp4")
        return f"{stem}{extension}"

    def _audio_quality_entries(self):
        return self.selected_format()["qualities"]

    def _populate_quality_choices(self, preferred_key=None):
        if self.media_kind == "audio":
            entries = self._audio_quality_entries()
            labels = [audio_quality_label(key) for key, _ in entries]
            default_key = preferred_key or self.selected_format()["default_quality"]
        else:
            entries = [(preset["key"], preset.get("bitrate")) for preset in VIDEO_QUALITY_PRESETS]
            labels = [video_quality_label(preset, self.source_profile) for preset in VIDEO_QUALITY_PRESETS]
            default_key = preferred_key or "original"
        try:
            self.quality_choice.Clear()
            self.quality_choice.AppendItems(labels)
        except Exception:
            self.quality_choice.SetItems(labels)
        index = next((i for i, entry in enumerate(entries) if entry[0] == default_key), 0)
        self.quality_choice.SetSelection(index)

    def _populate_dimension_choices(self, preferred_key=None):
        labels = [video_size_label(preset, self.source_profile) for preset in VIDEO_SIZE_PRESETS]
        try:
            self.dimension_choice.Clear()
            self.dimension_choice.AppendItems(labels)
        except Exception:
            self.dimension_choice.SetItems(labels)
        if preferred_key is None:
            preferred_key = "original"
            try:
                selected = self.selected_preset()
                preferred_key = selected["key"]
            except Exception:
                pass
        index = next((i for i, preset in enumerate(VIDEO_SIZE_PRESETS) if preset["key"] == preferred_key), 0)
        self.dimension_choice.SetSelection(index)

    def selected_quality_key(self):
        if self.media_kind == "audio":
            entries = self._audio_quality_entries()
        else:
            entries = [(preset["key"], preset.get("bitrate")) for preset in VIDEO_QUALITY_PRESETS]
        return entries[_selection_index(self.quality_choice, len(entries))][0]

    def selected_channel_key(self):
        return AUDIO_CHANNEL_MODES[_selection_index(self.channels_choice, len(AUDIO_CHANNEL_MODES))]["key"]

    def selected_preset(self):
        return VIDEO_SIZE_PRESETS[_selection_index(self.dimension_choice, len(VIDEO_SIZE_PRESETS))]

    def selected_placement(self):
        return "fill" if _selection_index(self.placement_choice, 2) == 1 else "fit"

    def _show_media_controls(self):
        _show_control(self.channels_panel, self.media_kind == "audio")
        _show_control(self.video_options_panel, self.media_kind == "video")

    def _set_placement_visible(self, visible, resize=True):
        visible = bool(visible and self.media_kind == "video")
        if not visible:
            try:
                self.placement_choice.SetSelection(0)
            except Exception:
                pass
        _show_control(self.placement_panel, visible)
        if resize:
            self._resize_to_contents()

    def _description_for_format(self):
        return tr("صيغة الإخراج المحددة: {value}").format(value=self.selected_format()["label"])

    def _description_for_quality(self):
        try:
            label = self.quality_choice.GetStringSelection()
        except Exception:
            label = ""
        return tr("جودة الإخراج المحددة: {value}").format(value=label)

    def _description_for_channels(self):
        try:
            label = self.channels_choice.GetStringSelection()
        except Exception:
            label = ""
        return tr("قنوات الصوت المحددة: {value}").format(value=label)

    def _description_for_dimensions(self):
        return speech_messages.VIDEO_SIZE_DESCRIPTIONS.get(self.selected_preset()["key"], "")

    def _description_for_placement(self):
        return speech_messages.VIDEO_PLACEMENT_DESCRIPTIONS.get(self.selected_placement(), "")

    def _set_help_text(self, control, text):
        if control is None:
            return
        translated = tr(text) if text else ""
        try:
            control.SetHelpText(translated)
        except Exception:
            pass
        try:
            control.SetToolTip(translated or None)
        except Exception:
            pass

    def _update_help_texts(self):
        self._set_help_text(self.format_choice, "")
        self._set_help_text(self.quality_choice, "")
        self._set_help_text(self.channels_choice, "")
        self._set_help_text(self.dimension_choice, "")
        self._set_help_text(self.placement_choice, "")

    def _resize_to_contents(self):
        try:
            self.Layout()
            self.Fit()
            size = self.GetSize()
            self.SetSize((max(int(size.GetWidth()), self.MINIMUM_WIDTH), int(size.GetHeight())))
        except Exception:
            pass

    def _cancel_pending_speech(self):
        self._speech_serial += 1
        self._pending_speech = ""
        self._pending_speech_control = None
        try:
            self._speech_timer.Stop()
        except Exception:
            pass

    def _queue_speech(self, text, control=None):
        if not text or not self.speech_callback:
            return
        self._speech_serial += 1
        self._pending_speech = text
        self._pending_speech_control = control
        try:
            self._speech_timer.Stop()
            self._speech_timer.StartOnce(self.ANNOUNCEMENT_SETTLE_MS)
        except Exception:
            serial = self._speech_serial
            try:
                wx.CallAfter(self._enqueue_pending_speech, serial)
            except Exception:
                self._enqueue_pending_speech(serial)

    def _on_speech_timer(self, event=None):
        serial = self._speech_serial
        try:
            wx.CallAfter(self._enqueue_pending_speech, serial)
        except Exception:
            self._enqueue_pending_speech(serial)
        _event_skip(event)

    def _enqueue_pending_speech(self, serial):
        if serial != self._speech_serial:
            return
        text = self._pending_speech
        control = self._pending_speech_control
        self._pending_speech = ""
        self._pending_speech_control = None
        if not text or not self.speech_callback:
            return
        if control is not None:
            if not self._control_owns_focus(control):
                return
        try:
            _speak_callback_after_ui(self.speech_callback, text, False)
        except Exception:
            pass

    def _control_owns_focus(self, control):
        if control is None:
            return False
        try:
            focused = wx.Window.FindFocus()
        except Exception:
            focused = None
        if focused is not None:
            current = focused
            while current is not None:
                if current is control:
                    return True
                if current is self:
                    return False
                try:
                    current = current.GetParent()
                except Exception:
                    return False
            return False
        return False

    def on_plain_control_focus(self, event=None):
        self._cancel_pending_speech()
        _event_skip(event)

    def on_show(self, event=None):
        try:
            shown = bool(self.IsShown())
        except Exception:
            shown = True
        if shown:
            self._start_source_profile_load()
        _event_skip(event)

    def _start_source_profile_load(self):
        if self._source_profile_load_started or self._source_profile_ready:
            return
        if not self.source_path or not os.path.exists(self.source_path):
            return
        self._source_profile_load_started = True
        source_path = self.source_path

        def worker():
            profile = probe_source_profile(source_path)
            try:
                wx.CallAfter(self._apply_source_profile, source_path, profile)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _apply_source_profile(self, source_path, profile):
        if source_path != self.source_path:
            return
        if not profile or profile == self.source_profile:
            self._source_profile_ready = True
            return
        try:
            if not self:
                return
        except Exception:
            return
        selected_format_key = self.selected_format()["key"]
        selected_quality_key = self.selected_quality_key()
        selected_preset_key = self.selected_preset()["key"] if self.media_kind == "video" else "original"
        selected_placement = self.selected_placement() if self.media_kind == "video" else "fit"
        selected_channel_key = self.selected_channel_key() if self.media_kind == "audio" else None
        self.source_profile = dict(profile)
        if self.media_kind == "video":
            self._populate_quality_choices(selected_quality_key)
            self._populate_dimension_choices(selected_preset_key)
            self.result_options = normalized_save_options(
                selected_preset_key,
                selected_placement,
                media_kind="video",
                format_key=selected_format_key,
                quality_key=selected_quality_key,
                source_profile=self.source_profile,
            )
        else:
            self.result_options = normalized_save_options(
                media_kind="audio",
                format_key=selected_format_key,
                quality_key=selected_quality_key,
                channel_key=selected_channel_key,
                source_profile=self.source_profile,
            )
        self._update_help_texts()
        self._resize_to_contents()
        self._source_profile_ready = True

    def on_description_control_blur(self, event=None):
        try:
            control = event.GetEventObject() if event is not None else None
        except Exception:
            control = None
        if control is None or control is self._pending_speech_control:
            self._cancel_pending_speech()
        _event_skip(event)

    def _replace_filename_extension(self):
        value = self.filename_text.GetValue().strip()
        if not value:
            return
        stem = os.path.splitext(value)[0] or value
        self.filename_text.SetValue(f"{stem}{self.selected_format()['extension']}")

    def on_format_changed(self, event=None):
        self._populate_quality_choices()
        self._replace_filename_extension()
        self._update_help_texts()
        self._cancel_pending_speech()
        _event_skip(event)

    def on_quality_changed(self, event=None):
        self._update_help_texts()
        self._cancel_pending_speech()
        _event_skip(event)

    def on_channels_changed(self, event=None):
        self._update_help_texts()
        self._cancel_pending_speech()
        _event_skip(event)

    def on_dimension_changed(self, event=None):
        self._set_placement_visible(bool(self.selected_preset()["size"]))
        self._update_help_texts()
        self._cancel_pending_speech()
        _event_skip(event)

    def on_placement_changed(self, event=None):
        self._update_help_texts()
        self._cancel_pending_speech()
        _event_skip(event)

    def on_format_focus(self, event=None):
        self._cancel_pending_speech()
        _event_skip(event)

    def on_quality_focus(self, event=None):
        self._cancel_pending_speech()
        _event_skip(event)

    def on_channels_focus(self, event=None):
        self._cancel_pending_speech()
        _event_skip(event)

    def on_dimension_focus(self, event=None):
        self._cancel_pending_speech()
        _event_skip(event)

    def on_placement_focus(self, event=None):
        self._cancel_pending_speech()
        _event_skip(event)

    def on_browse(self, event=None):
        current = self.folder_text.GetValue().strip()
        if not os.path.isdir(current):
            current = self._default_directory()
        prompt = tr("اختر مكان حفظ الصوت") if self.media_kind == "audio" else tr("اختر مكان حفظ الفيديو")
        style = getattr(wx, "DD_DEFAULT_STYLE", 0) | getattr(wx, "DD_DIR_MUST_EXIST", 0)
        dialog = wx.DirDialog(self, prompt, defaultPath=current, style=style)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.folder_text.SetValue(dialog.GetPath())
        finally:
            dialog.Destroy()
        _event_skip(event)

    @staticmethod
    def _normalise_filename(filename, extension):
        value = (filename or "").strip().strip('"')
        if not value or os.path.basename(value) != value:
            return ""
        root, current_extension = os.path.splitext(value)
        base = (root if current_extension else value).rstrip(" .")
        if not base or any(character in base for character in '<>:"/\\|?*'):
            return ""
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))}
        if base.upper() in reserved:
            return ""
        return f"{base}{extension}"

    def on_save(self, event=None):
        folder = self.folder_text.GetValue().strip().strip('"')
        media_word = tr("الصوت") if self.media_kind == "audio" else tr("الفيديو")
        if not folder:
            wx.MessageBox(tr("اكتب مسار ملف {media} أولا.").format(media=media_word), tr("مسار مطلوب"), wx.OK | wx.ICON_INFORMATION, self)
            self.folder_text.SetFocus()
            return
        if not os.path.isdir(folder):
            wx.MessageBox(tr("مجلد الحفظ غير موجود."), tr("مسار غير صالح"), wx.OK | wx.ICON_ERROR, self)
            self.folder_text.SetFocus()
            return
        output_format = self.selected_format()
        filename = self._normalise_filename(self.filename_text.GetValue(), output_format["extension"])
        if not filename:
            wx.MessageBox(tr("اكتب اسم ملف {media} أولا.").format(media=media_word), tr("اسم مطلوب"), wx.OK | wx.ICON_INFORMATION, self)
            self.filename_text.SetFocus()
            return
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            answer = wx.MessageBox(tr("الملف موجود بالفعل. هل تريد استبداله؟"), tr("تأكيد الاستبدال"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self)
            if answer != wx.YES:
                self.filename_text.SetFocus()
                return
        if self.media_kind == "audio":
            self.result_options = normalized_save_options(
                media_kind="audio",
                format_key=output_format["key"],
                quality_key=self.selected_quality_key(),
                channel_key=self.selected_channel_key(),
                source_profile=self.source_profile,
            )
        else:
            preset = self.selected_preset()
            self.result_options = normalized_save_options(
                preset["key"],
                self.selected_placement(),
                media_kind="video",
                format_key=output_format["key"],
                quality_key=self.selected_quality_key(),
                source_profile=self.source_profile,
            )
        self.result_path = path
        self.EndModal(wx.ID_OK)
        _event_skip(event)

    def on_close(self, event=None):
        self._cancel_pending_speech()
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()
        _event_skip(event)


# Backwards-compatible class name used by older callers and tests.
AccessibleVideoSaveDialog = AccessibleMediaSaveDialog

def ask_media_path():
    with wx.FileDialog(None, "اختيار ملف", wildcard=GENERAL_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
        prepare_media_file_dialog(dialog, dialog_key="open_media")
        if dialog.ShowModal() == wx.ID_CANCEL:
            return ""
        path = dialog.GetPath()
        remember_media_path(path, dialog_key="open_media")
        return path


def ask_media_paths():
    with wx.FileDialog(None, "اختيار ملف", wildcard=GENERAL_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dialog:
        prepare_media_file_dialog(dialog, dialog_key="open_media")
        if dialog.ShowModal() == wx.ID_CANCEL:
            return []
        paths = sorted(list(dialog.GetPaths()), key=natural_sort_key)
        remember_media_paths(paths, dialog_key="open_media")
        return paths


def ask_audio_path():
    with wx.FileDialog(None, "اختيار ملف صوتي", wildcard=AUDIO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
        prepare_media_file_dialog(dialog, "audio", "open_media")
        if dialog.ShowModal() == wx.ID_CANCEL:
            return ""
        path = dialog.GetPath()
        remember_media_path(path, dialog_key="open_media")
        return path


def ask_video_path():
    with wx.FileDialog(None, "اختيار فيديو", wildcard=VIDEO_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
        prepare_media_file_dialog(dialog, "video", "add_video")
        if dialog.ShowModal() == wx.ID_CANCEL:
            return ""
        path = dialog.GetPath()
        remember_media_path(path, "video", "add_video")
        return path


def ask_media_save_path(parent=None, speech_callback=None, selected=False, media_kind="video", source_path=""):
    dialog = AccessibleMediaSaveDialog(parent, speech_callback, selected, media_kind, source_path)
    try:
        if dialog.ShowModal() != wx.ID_OK:
            return "", None
        path = dialog.result_path
        save_options = dict(dialog.result_options)
    finally:
        dialog.Destroy()
    if not path:
        return "", None
    set_last_save_dir(path)
    return path, save_options


def ask_video_save_path(parent=None, speech_callback=None, selected=False, source_path=""):
    return ask_media_save_path(parent, speech_callback, selected, "video", source_path)


def ask_audio_save_path(parent=None, speech_callback=None, source_path="", selected=False):
    return ask_media_save_path(parent, speech_callback, selected, "audio", source_path)



def ask_save_path():
    path, _options = ask_video_save_path()
    return path


class ConfirmExitDialog(wx.Dialog):
    def __init__(self, parent, message=None, title=None):
        title = title or tr("تعديلات غير محفوظة")
        message = message or tr("هناك تعديلات لم يتم حفظها. هل تريد الخروج بدون حفظ؟")
        super().__init__(parent, title=title, size=(540, 220), style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP)
        self.parent = parent

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        msg_text = wx.StaticText(panel, label=message)
        msg_text.SetName(message)
        main_sizer.Add(msg_text, flag=wx.ALL | wx.EXPAND, border=16)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.yes_button = wx.Button(panel, wx.ID_YES, label=tr("نعم، أريد الخروج"))
        self.yes_button.SetName(tr("نعم، أريد الخروج"))

        self.no_button = wx.Button(panel, wx.ID_NO, label=tr("لا، أريد إكمال التعديل"))
        self.no_button.SetName(tr("لا، أريد إكمال التعديل"))
        self.no_button.SetDefault()

        btn_sizer.Add(self.yes_button, flag=wx.ALL, border=8)
        btn_sizer.Add(self.no_button, flag=wx.ALL, border=8)

        main_sizer.Add(btn_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=12)
        panel.SetSizer(main_sizer)

        self.yes_button.Bind(wx.EVT_BUTTON, self.on_yes)
        self.no_button.Bind(wx.EVT_BUTTON, self.on_no)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_no)
        try:
            from video_maker.dialog_keys import bind_dialog_keys
            bind_dialog_keys(self, self.on_key)
        except Exception:
            pass

        self.Centre()
        wx.CallAfter(self.no_button.SetFocus)

    def on_yes(self, event=None):
        self.EndModal(wx.ID_YES)

    def on_no(self, event=None):
        self.EndModal(wx.ID_NO)

    def on_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.on_no()
            return
        event.Skip()


def confirm_exit_prompt(parent, message=None, title=None):
    dialog = ConfirmExitDialog(parent, message=message, title=title)
    try:
        result = dialog.ShowModal()
        return result == wx.ID_YES
    finally:
        dialog.Destroy()
