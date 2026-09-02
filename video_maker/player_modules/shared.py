import threading
import copy
import tempfile
import shutil
import os
import bisect
import uuid
import webbrowser
import time
import subprocess

import wx
from video_maker.localization import tr
from video_maker.mpv_player import MPVMediaCtrl, MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED, EVT_MEDIA_LOADED, EVT_MEDIA_FINISHED

from video_maker.operation_control import OperationCancelled, is_operation_cancelled
from video_maker.app_state import (
    build_crash_session_payload,
    clear_crash_session,
    crash_session_exists,
    get_custom_app_name,
    get_declined_update_install_id,
    get_language,
    get_master_volume_db,
    get_ripple_mode,
    get_volume,
    normalize_ripple_mode,
    normalized_custom_app_name,
    read_crash_session,
    set_last_project_path,
    set_declined_update_install_id,
    set_custom_app_name,
    set_language,
    set_master_volume_db,
    set_ripple_mode,
    set_theme,
    set_volume,
    write_crash_session_payload,
)
from video_maker.app_paths import bundled_path, ensure_user_effects, imported_media_root
from video_maker.audio_clip_merge import AudioClipMergeWindow
from video_maker.audio_override_manager import MainAudioOverrideManager, visual_only_edit_kind
from video_maker.audio_image_merge import AudioImageMergeDialog, MergeProgressDialog, TRANSITIONS, create_audio_image_video, natural_sort_key
from video_maker.audio_video_merge import AudioVideoMergeDialog, AudioVideoMergeProgressDialog, create_audio_video_merge
from video_maker.background_audio import BackgroundAudioDialog, trim_background_audio_silence
from video_maker.clean_cut import clean_delete_range
from video_maker.continuous_playback import audio_clock_time, media_seek_ms, should_live_skip_deleted_gap, should_preserve_override_audio
from video_maker.clipboard_media_paste import (
    begin_paste_operation,
    can_start_paste,
    clear_marker_state as clear_clipboard_paste_marker_state,
    end_paste_operation,
    focused_control_owns_paste,
    internal_timeline_clipboard_media_kind,
    internal_timeline_clipboard_segments,
    note_end_marker as note_clipboard_paste_end_marker,
    note_full_selection as note_clipboard_paste_full_selection,
    note_start_marker as note_clipboard_paste_start_marker,
    paste_media_from_clipboard,
    paste_file_path,
    paste_timeline_audio_clipboard_as_background,
    resolve_placement,
    set_internal_timeline_clipboard,
)
from video_maker.chroma_dialog import ChromaBackgroundDialog
from video_maker.chroma_key import analyze_timeline_chroma, build_chroma_background_segment, build_chroma_preview
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import AUDIO_WILDCARD, GENERAL_WILDCARD, IMAGE_WILDCARD, VIDEO_WILDCARD, ask_audio_path, ask_audio_save_path, ask_media_paths, ask_video_save_path, ask_video_path, media_kind_for_path, prepare_media_file_dialog, remember_media_paths
from video_maker.edit_history import EditHistory
from video_maker.edit_points import adjust_points_after_delete, adjust_points_after_insert, delete_name, dicts_to_segments, make_edit_point, next_point, normalize_edit_points, point_at_time, point_by_id, point_description, previous_point, remove_point
from video_maker.encrypted_projects import (
    PROJECT_EXTENSION,
    ProjectCancelled,
    ProjectError,
    capture_project_snapshot,
    capture_runtime_payload,
    ensure_project_extension,
    project_error_text_key,
    restore_project_file,
    save_project_file,
)
from video_maker.element_manager import ElementManagerWindow, compensate_deleted_visual_item
from video_maker.error_reporting import install_error_reporting, show_error
from video_maker.image_overlay import ImageOverlayDialog, build_image_overlay_segment, replace_image_overlay_range
from video_maker.localization import history_feedback_message, spoken_duration, tr, tr_format
from video_maker.logical_files import (
    display_file_name,
    ensure_logical_file_metadata,
    file_intervals_descending,
    logical_file_at_time,
    logical_file_entries,
    new_file_segment,
    new_logical_file_id,
    replacement_segments_preserving_files,
    segment_file_id,
    segment_file_name,
    segment_with_file_identity,
)
from video_maker.menus import install_menu_bar, project_output_kind
from video_maker.metadata_dialog import MetadataDialog
from video_maker.program_modes import NORMAL_MODE, PROFESSIONAL_MODE, get_program_mode, toggle_program_mode
from video_maker.recent_files import clear_recent_files, open_recent_file, remember_recent_file, remember_recent_files
from video_maker.tracks import (
    BACKGROUND_AUDIO_TRACK,
    MAIN_VIDEO_TRACK,
    SECONDARY_VIDEO_TRACK,
    SOUND_EFFECTS_TRACK,
    TEXT_TRACK,
    DEFAULT_TRACK,
    next_track,
    previous_track,
    track_index,
    track_label,
    track_media_type,
    track_media_types,
    tracks_accepting,
    track_has_volume,
)
from video_maker.track_items import (
    apply_selection_to,
    base_element_name,
    build_preview_audio_mix,
    default_duration_for,
    element_display_name,
    element_identifier,
    element_to_dict,
    filter_audio_sources_for_export,
    is_track_audible,
    item_at_time,
    item_bounds,
    items_in_range,
    mute_timed_audio_items_range,
    natural_span,
    new_dynamic_text_item,
    next_item_on_track,
    previous_item_on_track,
    render_preview_layer,
    ripple_shift,
    ripple_shift_segments,
    should_ripple,
    should_use_fast_path,
    split_item,
    split_timeline_segment,
    text_preview_fingerprint,
)
from video_maker.reliable_playback import ReliableAudioPlayer, reliable_audio_available
from video_maker.scrub_audio import ScrubPlayer
from video_maker.recording import RecordingError, RecordingSettingsDialog, make_recording_session
from video_maker.problem_log import (
    append_problem,
    clear_problem_log,
    copy_problem_log_to_clipboard,
    enable_runtime_diagnostics,
    export_problem_log,
    flush_problem_log,
    install_problem_logging,
    log_project_state_change,
    note_ui_heartbeat,
    remove_ui_heartbeat,
    trace_event,
)
from video_maker.save_progress import SaveProgressDialog
from video_maker.settings_dialog import ProgramSettingsDialog
from video_maker.settings import (
    DEFAULT_PIXELS_PER_SECOND,
    DEFAULT_SEEK_STEP,
    DEFAULT_NORMAL_SEEK_STEP,
    MAX_PIXELS_PER_SECOND,
    MIN_PIXELS_PER_SECOND,
    MIN_SEEK_STEP,
    SEEK_PIXELS,
    decrease_normal_seek_step,
    delete_seek_step_file,
    format_seek_step_ms,
    increase_normal_seek_step,
    normalize_pixels_per_second,
    pixels_per_second_increment,
    read_normal_seek_step,
    read_pixels_per_second,
    read_seek_step,
    seek_seconds_for_pixels,
    seek_step_increment,
    write_normal_seek_step,
    write_pixels_per_second,
    write_seek_step,
)
from video_maker.shortcuts import install_shortcuts
from video_maker.speech_feedback import ScreenReaderSpeech
from video_maker import speech_messages
from video_maker.audio_effects import AudioEffectChooserDialog, AudioEffectDialog, build_audio_effect_segment, build_audio_effect_segment_with_progress, get_audio_effect_definitions, replace_audio_effect_range
from video_maker.auto_subtitles_module import (
    CaptionsPipeline,
    CaptionsProgressDialog,
    GroqKeyManager,
    _debug,
    captionsSettingsDialog,
    ffmpeg_startupinfo,
    transcribe_with_groq,
)
from video_maker.silence_removal import RemoveSilenceDialog, apply_silence_intervals, apply_silence_removed, preview_silence_removed, selected_range
from video_maker.text_overlay import TextOverlayDialog, build_text_overlay_segment, from_text_item, render_text_image, render_typing_video
from video_maker.timeline import TimelineSegment, apply_audio_cut_fade_at_boundary, boundary_index_at_time, delete_range, insert_segments, locate_segment, slice_segments, total_duration, with_transition
from video_maker.timeline_split import TimelineSplitDialog, numbered_output_path, split_ranges_for_options, timed_items_for_range
from video_maker.timeline_transforms import AudioEffectPreparationCancelled, CensorBleepDialog, SPEED_CHOICES, SpeedDialog, TimelineTransitionDialog, build_censor_segment, mute_original_audio_range, mute_timeline_audio_ranges, replace_timeline_range, speed_timeline_range
from video_maker.transition_effects import TransitionEffectsDialog
from video_maker.updater import (
    UpdateError,
    check_for_update,
    delete_downloaded_update,
    download_update,
    find_downloaded_update,
    find_pending_downloaded_update,
    format_unexpected_error,
    run_update_file,
    update_download_id,
    update_install_id,
)
from video_maker.update_error_dialog import UpdateErrorDialog
from video_maker.video_rotation import VideoRotationDialog, build_rotated_video_segment
from video_maker.video_clip_merge import VideoClipMergeWindow
from video_maker.video_editing import build_visual_transition_segment, create_video_from_image, get_media_duration, get_video_duration, has_audio_stream, has_video_stream, prepare_boundary_safe_audio_proxy, render_xfade_visual_overlay_file, segment_audio_start, timed_items_have_audio, timeline_export_duration, visual_item_groups, write_audio_visual_preview_video, write_audio_visual_video, write_timeline_audio, write_timeline_video
from video_maker.visual_effects import VisualEffectsDialog
from video_maker.volume_boost import BOOSTED_OUTPUT_VOLUME, boosted_volume_down, boosted_volume_up, device_volume, format_master_db, format_track_db, master_linear_into_volume, master_volume_db_changed, master_volume_down_db, master_volume_up_db, normal_volume_up, normalized_master_volume_db, normalized_program_volume, normalized_track_volume_db, persisted_master_volume_db, persisted_program_volume, save_options_with_master_volume, save_options_with_output_volume, save_options_with_track_volumes, track_volume_db_changed, track_volume_down_db, track_volume_gain, track_volume_up_db, volume_changed, volume_down, volume_percent
from video_maker.watermark import AddWatermarkDialog, RemoveWatermarkDialog, build_watermarked_segment, build_watermark_removed_segment, preserve_watermark_restoration_patch
from video_maker.work_sessions import RestoreSessionDialog, app_data_root, read_session, session_dir_for_name, write_session
from video_maker.themes import apply_theme
from video_maker.video_snapshot import copy_video_snapshot_to_clipboard
from video_maker.video_snapshot import copy_video_snapshot_to_clipboard


install_error_reporting()
install_problem_logging()


PLAYBACK_EDGE_GUARD = 0.008
SEEK_BOUNDARY_NUDGE = 0.03
APP_TITLE = "صانع الفيديو"
MAIN_AUDIO_OVERRIDE_MIN_TOLERANCE = 1.0
MAIN_AUDIO_OVERRIDE_MAX_TOLERANCE = 5.0
RECORDING_ANNOUNCEMENT_DELAY_MS = 300
OPEN_PLAYER_WINDOWS = []
PROGRAM_WINDOW_SEQUENCE = 0
STARTUP_FILE_ARGUMENTS_HANDLED = False
SHELL_INTEGRATION_STARTED = False


def normalized_volume(value, default=1.0):
    try:
        volume = float(value)
    except (TypeError, ValueError):
        volume = float(default)
    if volume > 1.0:
        volume /= 100.0
    return max(0.0, min(1.0, volume))


def main_audio_override_tolerance(video_duration):
    try:
        duration = max(0.0, float(video_duration or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return max(MAIN_AUDIO_OVERRIDE_MIN_TOLERANCE, min(MAIN_AUDIO_OVERRIDE_MAX_TOLERANCE, duration * 0.01))


def main_audio_duration_is_compatible(video_duration, audio_duration):
    try:
        expected = max(0.0, float(video_duration or 0.0))
        actual = max(0.0, float(audio_duration or 0.0))
    except (TypeError, ValueError):
        return False, 0.0
    tolerance = main_audio_override_tolerance(expected)
    return expected > 0 and actual > 0 and abs(expected - actual) <= tolerance, tolerance


def silence_media_control_accessibility(ctrl):
    ctrl.SetName(tr(" "))
    ctrl.SetLabel(" ")
    if hasattr(ctrl, "SetCanFocus"):
        ctrl.SetCanFocus(False)
    if hasattr(ctrl, "DisableFocusFromKeyboard"):
        ctrl.DisableFocusFromKeyboard()
    if hasattr(ctrl, "SetAccessibleName"):
        ctrl.SetAccessibleName(" ")


def set_accessible_label(window, label):
    text = tr(label)
    window.SetName(text)
    window.SetLabel(text)
    if hasattr(window, "SetAccessibleName"):
        window.SetAccessibleName(text)


class ReadOnlyTextDialog(wx.Dialog):
    def __init__(self, parent, title, message):
        super().__init__(parent, title=tr(title), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.text = wx.TextCtrl(
            panel,
            value=str(message or ""),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        )
        accessible_title = tr(title)
        self.text.SetName(accessible_title)
        if hasattr(self.text, "SetAccessibleName"):
            self.text.SetAccessibleName(accessible_title)
        sizer.Add(self.text, 1, wx.ALL | wx.EXPAND, 12)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.close_button = wx.Button(panel, wx.ID_CLOSE, tr("إغلاق"))
        set_accessible_label(self.close_button, "إغلاق")
        self.close_button.SetDefault()
        self.close_button.Bind(wx.EVT_BUTTON, self.on_close)
        button_sizer.Add(self.close_button, 0)
        sizer.Add(button_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.ALIGN_RIGHT, 12)

        panel.SetSizer(sizer)
        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        outer_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer_sizer)
        self.SetMinSize((520, 260))
        self.SetSize((560, 320))
        self.CentreOnParent()
        bind_dialog_keys(self, self.on_key, (wx.TextCtrl,), preserve_navigation_keys=True)
        self.text.Bind(wx.EVT_CHAR_HOOK, self.on_text_key)
        wx.CallAfter(self.text.SetFocus)

    def on_close(self, event=None):
        self.EndModal(wx.ID_CLOSE)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_close()
            return
        event.Skip()

    def on_text_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_close()
            return
        event.Skip()


class ApplicationNameDialog(wx.Dialog):
    def __init__(self, parent, current_name):
        super().__init__(
            parent,
            title=tr("تغيير اسم التطبيق"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.result_name = None

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(panel, label=tr("اسم التطبيق"))
        label.SetName(tr("اسم التطبيق"))
        sizer.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self.name_text = wx.TextCtrl(panel, value=current_name or "")
        self.name_text.SetName(tr("اكتب اسم التطبيق"))
        if hasattr(self.name_text, "SetAccessibleName"):
            self.name_text.SetAccessibleName(tr("اكتب اسم التطبيق"))
        sizer.Add(self.name_text, 0, wx.ALL | wx.EXPAND, 12)

        hint = wx.StaticText(panel, label=tr("اترك الاسم فارغا لاستخدام اسم التطبيق الحالي"))
        hint.SetName(tr("اترك الاسم فارغا لاستخدام اسم التطبيق الحالي"))
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        cancel_button.SetName(tr("إلغاء"))
        save_button = wx.Button(panel, wx.ID_OK, tr("حفظ"))
        save_button.SetName(tr("حفظ"))
        save_button.SetDefault()
        cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        save_button.Bind(wx.EVT_BUTTON, self.on_save)
        button_sizer.Add(cancel_button, 0, wx.RIGHT, 8)
        button_sizer.Add(save_button, 0)
        sizer.Add(button_sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.ALIGN_RIGHT, 12)

        panel.SetSizer(sizer)
        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        outer_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(outer_sizer)
        self.SetMinSize((460, self.GetSize().GetHeight()))
        self.CentreOnParent()
        bind_dialog_keys(self, self.on_key, (wx.TextCtrl,), preserve_navigation_keys=True)
        self.name_text.Bind(wx.EVT_CHAR_HOOK, self.on_text_key)
        wx.CallAfter(self.name_text.SetFocus)

    def on_save(self, event=None):
        self.result_name = normalized_custom_app_name(self.name_text.GetValue())
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_cancel()
            return
        event.Skip()

    def on_text_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_cancel()
            return
        event.Skip()


def call_on_wx_main_thread(callback, *args, **kwargs):
    """Run a UI callback on wx's main thread and preserve synchronous semantics."""
    if wx.IsMainThread():
        return callback(*args, **kwargs)
    completed = threading.Event()
    outcome = {}

    def invoke():
        try:
            outcome["result"] = callback(*args, **kwargs)
        except BaseException as error:
            outcome["error"] = error
        finally:
            completed.set()

    wx.CallAfter(invoke)
    completed.wait()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")


