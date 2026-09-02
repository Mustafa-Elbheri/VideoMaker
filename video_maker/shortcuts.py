import wx

from video_maker.program_modes import PROFESSIONAL_MODE, get_program_mode, run_mode_shortcut


OPEN_BRACKET_KEY = 219
CLOSE_BRACKET_KEY = 221
PERIOD_KEY = 190
A_KEY = 65
B_KEY = 66
C_KEY = 67
D_KEY = 68
E_KEY = 69
F_KEY = 70
G_KEY = 71
H_KEY = 72
I_KEY = 73
K_KEY = 75
J_KEY = 74
L_KEY = 76
M_KEY = 77
N_KEY = 78
O_KEY = 79
P_KEY = 80
Q_KEY = 81
R_KEY = 82
S_KEY = 83
T_KEY = 84
U_KEY = 85
V_KEY = 86
W_KEY = 87
X_KEY = 88
Y_KEY = 89
Z_KEY = 90
NUMBER_1_KEY = 49
NUMBER_2_KEY = 50
NUMBER_3_KEY = 51
HOME_KEYS = {wx.WXK_HOME, wx.WXK_NUMPAD_HOME, 36}
END_KEYS = {wx.WXK_END, wx.WXK_NUMPAD_END, 35}
NUMPAD_PRO_KEYS = {wx.WXK_NUMPAD2, wx.WXK_NUMPAD4, wx.WXK_NUMPAD6, wx.WXK_NUMPAD8}
NUMPAD_PRO_RAW_KEYS = {98, 100, 102, 104}
NUMPAD_PRO_ACTIONS = {
    wx.WXK_NUMPAD2: "OnMoveElementToNextTrack",
    wx.WXK_NUMPAD4: "OnNudgeElementLeft",
    wx.WXK_NUMPAD6: "OnNudgeElementRight",
    wx.WXK_NUMPAD8: "OnMoveElementToPreviousTrack",
    98: "OnMoveElementToNextTrack",
    100: "OnNudgeElementLeft",
    102: "OnNudgeElementRight",
    104: "OnMoveElementToPreviousTrack",
}
NUMPAD_ADD_RAW = 107
NUMPAD_SUBTRACT_RAW = 109
NUMPAD1_RAW = 97
NUMPAD3_RAW = 99
F7_KEYS = {wx.WXK_F7, 118}
F8_KEYS = {wx.WXK_F8, 119}
F9_KEYS = {wx.WXK_F9, 120}
RECORDING_KEYS = F7_KEYS | F8_KEYS | F9_KEYS
MODIFIER_KEYS = {
    wx.WXK_CONTROL,
    wx.WXK_SHIFT,
    wx.WXK_ALT,
    wx.WXK_MENU,
    16,
    17,
    18,
}
raw_control = getattr(wx, "WXK_RAW_CONTROL", None)
if raw_control is not None:
    MODIFIER_KEYS.add(raw_control)


def shortcut_requires_open_file(raw_key, key, has_modifier, shift_down, alt_down):
    if not has_modifier:
        return key in {
            wx.WXK_SPACE,
            wx.WXK_RIGHT,
            wx.WXK_LEFT,
            wx.WXK_UP,
            wx.WXK_DOWN,
            wx.WXK_PAGEUP,
            wx.WXK_PAGEDOWN,
            wx.WXK_BACK,
            wx.WXK_DELETE,
            wx.WXK_TAB,
            wx.WXK_NUMPAD1,
            wx.WXK_NUMPAD2,
            wx.WXK_NUMPAD3,
            wx.WXK_NUMPAD4,
            wx.WXK_NUMPAD6,
            wx.WXK_NUMPAD8,
        } or raw_key in {
            OPEN_BRACKET_KEY,
            CLOSE_BRACKET_KEY,
            B_KEY,
            E_KEY,
            K_KEY,
            NUMBER_1_KEY,
            NUMBER_2_KEY,
            NUMBER_3_KEY,
            98,
            100,
            102,
            104,
        }
    if key in MODIFIER_KEYS or raw_key in MODIFIER_KEYS:
        return False
    if raw_key == J_KEY and (shift_down or alt_down):
        return False
    if key in RECORDING_KEYS or raw_key in RECORDING_KEYS:
        return False
    if (key == wx.WXK_F2 or raw_key in (wx.WXK_F2, 113)) and shift_down:
        return False
    allowed_without_file = {O_KEY, U_KEY, Q_KEY, S_KEY}
    if raw_key in allowed_without_file:
        return False
    return True


def recording_shortcut_key(raw_key, key, has_modifier):
    if not has_modifier:
        return None
    if key in F7_KEYS or raw_key in F7_KEYS:
        return wx.WXK_F7
    if key in F8_KEYS or raw_key in F8_KEYS:
        return wx.WXK_F8
    if key in F9_KEYS or raw_key in F9_KEYS:
        return wx.WXK_F9
    return None


def _focused_child_top_level(frame):
    """Return the focused child top-level window, or None for the main frame."""
    try:
        focused = wx.Window.FindFocus()
    except Exception:
        return None
    if focused is None:
        return None
    try:
        top_level = focused.GetTopLevelParent()
    except AttributeError:
        top_level = wx.GetTopLevelParent(focused)
    if top_level is frame:
        return None
    return top_level


def _focused_navigation_dialog(frame):
    """Return the active marked child window that should keep navigation keys."""
    top_level = _focused_child_top_level(frame)
    if top_level is not None and getattr(top_level, "_video_maker_preserve_navigation_keys", False):
        return top_level
    try:
        active = wx.GetActiveWindow()
    except Exception:
        active = None
    if active is not None and active is not frame:
        if getattr(active, "_video_maker_preserve_navigation_keys", False):
            return active
        try:
            active_top_level = active.GetTopLevelParent()
        except Exception:
            active_top_level = wx.GetTopLevelParent(active)
        if active_top_level is not frame and getattr(active_top_level, "_video_maker_preserve_navigation_keys", False):
            return active_top_level
    dialog = getattr(frame, "update_progress_dialog", None)
    if dialog is not None and getattr(dialog, "_video_maker_preserve_navigation_keys", False):
        try:
            if hasattr(dialog, "IsShown") and not dialog.IsShown():
                return None
        except Exception:
            pass
        return dialog
    if active is None or active is frame:
        return None
    return None


def _allow_local_navigation_key(frame, event, key, has_command_modifier):
    """Keep native navigation inside child dialogs instead of main shortcuts."""
    if has_command_modifier or event.AltDown():
        return False
    is_tab = key == wx.WXK_TAB
    if event.ShiftDown() and not is_tab:
        return False
    navigation_dialog = _focused_navigation_dialog(frame)
    if navigation_dialog is None and is_tab:
        navigation_dialog = _focused_child_top_level(frame)
    local_keys = {
        wx.WXK_TAB,
        wx.WXK_SPACE,
        wx.WXK_LEFT,
        wx.WXK_RIGHT,
        wx.WXK_UP,
        wx.WXK_DOWN,
        getattr(wx, "WXK_NUMPAD_LEFT", wx.WXK_LEFT),
        getattr(wx, "WXK_NUMPAD_RIGHT", wx.WXK_RIGHT),
        getattr(wx, "WXK_NUMPAD_UP", wx.WXK_UP),
        getattr(wx, "WXK_NUMPAD_DOWN", wx.WXK_DOWN),
    }
    if key not in local_keys or navigation_dialog is None:
        return False
    focus_method = getattr(navigation_dialog, "focus_navigation_controls", None)
    if callable(focus_method):
        focus_method()
    if hasattr(event, "DoAllowNextEvent"):
        event.DoAllowNextEvent()
    # Do not Skip the CHAR_HOOK event here. Handling it stops the main-frame
    # shortcut chain, while DoAllowNextEvent still generates the normal key
    # event for the focused dialog control.
    return True


def _allow_native_editing_shortcut(frame, event, raw_key, has_command_modifier):
    """Keep standard editing shortcuts inside text fields and child dialogs."""
    if not has_command_modifier or event.AltDown() or event.ShiftDown():
        return False
    if raw_key not in {A_KEY, C_KEY, V_KEY, X_KEY, Y_KEY, Z_KEY}:
        return False
    try:
        focused = wx.Window.FindFocus()
    except Exception:
        focused = None
    if focused is None:
        return False
    try:
        top_level = focused.GetTopLevelParent()
    except Exception:
        top_level = wx.GetTopLevelParent(focused)

    # Any child dialog owns its editing keys. Handling CHAR_HOOK while allowing
    # the next native event prevents the main-frame accelerator from firing.
    if top_level is not frame:
        if hasattr(event, "DoAllowNextEvent"):
            event.DoAllowNextEvent()
        return True

    text_types = tuple(
        item for item in (
            getattr(wx, "TextCtrl", None),
            getattr(wx, "ComboBox", None),
            getattr(wx, "SearchCtrl", None),
            getattr(wx, "SpinCtrl", None),
            getattr(wx, "SpinCtrlDouble", None),
        ) if isinstance(item, type)
    )
    is_text = bool(text_types and isinstance(focused, text_types))
    if not is_text and not (hasattr(focused, "CanPaste") and hasattr(focused, "Paste")):
        return False

    # Invoke native control actions directly so the parent accelerator cannot
    # reinterpret Ctrl+V/C/X as timeline editing.
    action = {
        A_KEY: "SelectAll",
        C_KEY: "Copy",
        V_KEY: "Paste",
        X_KEY: "Cut",
        Y_KEY: "Redo",
        Z_KEY: "Undo",
    }.get(raw_key)
    method = getattr(focused, action, None) if action else None
    if callable(method):
        try:
            method()
            return True
        except Exception:
            pass
    if hasattr(event, "DoAllowNextEvent"):
        event.DoAllowNextEvent()
    return True


def handle_language_independent_shortcuts(frame, event):
    raw_key = event.GetRawKeyCode() if hasattr(event, "GetRawKeyCode") else event.GetKeyCode()
    key = event.GetKeyCode()

    has_command_modifier = event.ControlDown() or event.MetaDown()

    if _allow_native_editing_shortcut(frame, event, raw_key, has_command_modifier):
        return

    # Child merge/selection dialogs own their native Space and arrow keys.
    # Do this before the no-open-file guard so the main player never announces
    # "no file open" while the user is navigating a list, choice, slider or
    # activating a button inside those dialogs.
    if _allow_local_navigation_key(frame, event, key, has_command_modifier):
        return

    # A bare Control key is only a modifier state, not an application shortcut.
    # Let Windows and the focused control receive it without announcing that no
    # media is open.  Combined shortcuts such as Ctrl+O are unaffected because
    # their key code is the accompanying key, not WXK_CONTROL.
    control_keys = {wx.WXK_CONTROL, 17}
    raw_control = getattr(wx, "WXK_RAW_CONTROL", None)
    if raw_control is not None:
        control_keys.add(raw_control)
    if key in control_keys or raw_key in control_keys:
        if hasattr(event, "DoAllowNextEvent"):
            event.DoAllowNextEvent()
        event.Skip()
        return

    if has_command_modifier and not event.ShiftDown() and not event.AltDown() and raw_key == N_KEY:
        frame.OnNewProgramWindow()
        return

    if has_command_modifier and key == wx.WXK_TAB and not event.AltDown():
        if event.ShiftDown():
            frame.OnPreviousProgramWindow()
        else:
            frame.OnNextProgramWindow()
        return

    if has_command_modifier and event.ShiftDown() and not event.AltDown() and (key == wx.WXK_F2 or raw_key in (wx.WXK_F2, 113)):
        frame.OnChangeApplicationName()
        return

    if not has_command_modifier and not event.ShiftDown() and not event.AltDown() and (key == wx.WXK_F2 or raw_key in (wx.WXK_F2, 113)):
        frame.OnRenameProgramWindow()
        return

    # Escape receives the new selection-cancel action only when a selection is
    # active.  With no selection it is passed through unchanged, preserving all
    # existing dialog and native Escape behaviours.
    if (
        key == wx.WXK_ESCAPE
        and not has_command_modifier
        and not event.ShiftDown()
        and not event.AltDown()
    ):
        if frame.OnCancelCurrentSelection():
            return
        if hasattr(event, "DoAllowNextEvent"):
            event.DoAllowNextEvent()
        event.Skip()
        return

    recording_key = recording_shortcut_key(raw_key, key, has_command_modifier)
    if recording_key == wx.WXK_F9:
        if event.AltDown():
            frame.OnStartPreparedScreenRecording()
        elif event.ShiftDown():
            frame.OnPrepareScreenRecording()
        else:
            frame.OnRecordAudio()
        return

    if recording_key == wx.WXK_F7:
        frame.OnPauseResumeRecording()
        return

    if recording_key == wx.WXK_F8:
        frame.OnStopRecording()
        return

    # F7/F8 in professional mode toggle Mute/Solo for the current track.  These
    # run only in professional mode; in normal mode the keys keep their existing
    # behaviour unchanged.
    if (
        get_program_mode() == PROFESSIONAL_MODE
        and not has_command_modifier
        and not event.ShiftDown()
        and not event.AltDown()
    ):
        if key in F7_KEYS or raw_key in F7_KEYS:
            frame.OnMuteToggleCurrentTrack()
            return
        if key in F8_KEYS or raw_key in F8_KEYS:
            frame.OnSoloToggleCurrentTrack()
            return

    # Shift+. toggles between the normal and professional program modes.  This
    # must run before the open-file guard so the switch works with no file open.
    if (
        event.ShiftDown()
        and not has_command_modifier
        and not event.AltDown()
        and (key in (ord("."), ord(">")) or raw_key == PERIOD_KEY)
    ):
        frame.OnToggleProgramMode()
        return

    # Numpad 2/4/6/8 in professional mode move the focused element to another
    # track or nudge it. When the focus is owned by the player (a text field or
    # dialog) the key belongs to the control, so the ownership check runs before
    # the open-file guard. In normal mode the keys keep their existing behaviour.
    pro_numpad_key = (
        get_program_mode() == PROFESSIONAL_MODE
        and not has_command_modifier
        and not event.ShiftDown()
        and not event.AltDown()
        and (key in NUMPAD_PRO_KEYS or raw_key in NUMPAD_PRO_RAW_KEYS)
    )
    if pro_numpad_key and frame._numpad_key_owned_by_focus():
        if hasattr(event, "DoAllowNextEvent"):
            event.DoAllowNextEvent()
        event.Skip()
        return

    # In professional mode the numpad + and - keys adjust the horizontal zoom
    # level (pixels per second), the 3 key resets zoom to the default, and the
    # 1 key is disabled. The numpad 2/4/6/8 element-move shortcuts above are
    # left untouched. These run before the open-file guard because zoom is a
    # general setting.
    if (
        get_program_mode() == PROFESSIONAL_MODE
        and not has_command_modifier
        and not event.ShiftDown()
        and not event.AltDown()
    ):
        if raw_key == NUMPAD_ADD_RAW or key == wx.WXK_NUMPAD_ADD:
            frame.OnZoomOut()
            return
        if raw_key == NUMPAD_SUBTRACT_RAW or key == wx.WXK_NUMPAD_SUBTRACT:
            frame.OnZoomIn()
            return
        if raw_key == NUMBER_1_KEY or raw_key == NUMPAD1_RAW or key == wx.WXK_NUMPAD1:
            return
        if raw_key == NUMBER_3_KEY or raw_key == NUMPAD3_RAW or key == wx.WXK_NUMPAD3:
            frame.OnResetZoom()
            return

    if shortcut_requires_open_file(raw_key, key, has_command_modifier, event.ShiftDown(), event.AltDown()) and not frame.has_video():
        frame.say("لا يوجد أي ملف مفتوح", wait_for_ui=False)
        return

    if pro_numpad_key:
        numpad_action = NUMPAD_PRO_ACTIONS.get(key) or NUMPAD_PRO_ACTIONS.get(raw_key)
        if numpad_action:
            getattr(frame, numpad_action)()
            return

    if has_command_modifier and event.AltDown() and raw_key == J_KEY:
        frame.OnRestoreCrashSession()
        return

    if (
        has_command_modifier
        and event.AltDown()
        and not event.ShiftDown()
    ):
        if key == wx.WXK_UP:
            frame.OnIncreaseMasterVolume()
            return
        if key == wx.WXK_DOWN:
            frame.OnDecreaseMasterVolume()
            return
        if key == wx.WXK_RIGHT:
            frame.OnIncreaseTrackVolume()
            return
        if key == wx.WXK_LEFT:
            frame.OnDecreaseTrackVolume()
            return

    # Alt+Up/Alt+Down in professional mode raise/lower the current track volume.
    # This must run before the Alt catch-all below, which otherwise forwards the
    # event to native menu handling.
    if (
        not has_command_modifier
        and not event.ShiftDown()
        and event.AltDown()
        and get_program_mode() == PROFESSIONAL_MODE
    ):
        if key == wx.WXK_UP:
            frame.OnIncreaseTrackVolume()
            return
        if key == wx.WXK_DOWN:
            frame.OnDecreaseTrackVolume()
            return

    if has_command_modifier and key == wx.WXK_F4 and not event.AltDown():
        frame.OnDeleteCurrentTimelineFile()
        return

    if (
        not has_command_modifier
        and not event.ShiftDown()
        and event.AltDown()
        and (key in (ord("P"), ord("p")) or raw_key == P_KEY)
        and get_program_mode() == PROFESSIONAL_MODE
    ):
        frame.OnToggleRippleMode()
        return

    if key in (wx.WXK_ALT, wx.WXK_MENU, wx.WXK_F10) or event.AltDown():
        if hasattr(event, "DoAllowNextEvent"):
            event.DoAllowNextEvent()
        event.Skip()
        return

    has_modifier = has_command_modifier

    # Execute the editing-critical keys directly from EVT_CHAR_HOOK.  Routing
    # them through an accelerator -> EVT_MENU adds another queued GUI event and
    # makes response depend on focus and native menu dispatch.
    if not has_modifier and not event.ShiftDown():
        if key == wx.WXK_SPACE:
            frame.OnPlayPause()
            return
        if key == wx.WXK_TAB:
            frame.OnNextTimelineFile()
            return
        if key == wx.WXK_RIGHT:
            frame.OnForward()
            return
        if key == wx.WXK_LEFT:
            frame.OnRewind()
            return
        if key == wx.WXK_UP:
            if get_program_mode() == PROFESSIONAL_MODE:
                frame.OnPreviousTrack()
                return
            frame.OnIncreaseVolume()
            return
        if key == wx.WXK_DOWN:
            if get_program_mode() == PROFESSIONAL_MODE:
                frame.OnNextTrack()
                return
            frame.OnDecreaseVolume()
            return
        if key == wx.WXK_PAGEUP:
            frame.OnPageUp()
            return
        if key == wx.WXK_PAGEDOWN:
            frame.OnPageDown()
            return
        if raw_key == S_KEY:
            if get_program_mode() == PROFESSIONAL_MODE:
                frame.OnSplitAtPlayhead()
            else:
                frame.OnTakeSnapshot()
            return
        if raw_key == B_KEY:
            frame.OnMuteBackgroundAudioSelection()
            return
        if raw_key == E_KEY:
            frame.OnElementManager()
            return
        if raw_key == K_KEY:
            frame.OnSetEnd()
            return
        if raw_key == X_KEY:
            frame.OnPause()
            return
        if raw_key == I_KEY and get_program_mode() == PROFESSIONAL_MODE:
            frame.OnInsertTrackItem()
            return

    if not has_modifier and event.ShiftDown() and key == wx.WXK_UP:
        frame.OnIncreaseVolumeBoost()
        return

    if not has_modifier and event.ShiftDown() and key == wx.WXK_DOWN:
        frame.OnDecreaseVolumeBoost()
        return

    if not has_modifier and event.ShiftDown() and raw_key == S_KEY and get_program_mode() == PROFESSIONAL_MODE:
        frame.OnSoloToggleCurrentTrack()
        return

    if not has_modifier and event.ShiftDown() and key == wx.WXK_SPACE:
        frame.OnPlaySelectedRange()
        return

    if has_modifier and not event.ShiftDown() and key == wx.WXK_SPACE:
        frame.OnPlayTimelineExceptSelection()
        return

    if not has_modifier and event.ShiftDown() and key == wx.WXK_TAB:
        frame.OnPreviousTimelineFile()
        return

    if has_modifier and raw_key == O_KEY:
        frame.OnOpen()
        return

    if has_modifier and raw_key == S_KEY:
        if event.ShiftDown():
            frame.OnSaveSelectedVideo()
        else:
            frame.OnSaveVideo()
        return

    if has_modifier and raw_key == A_KEY:
        run_mode_shortcut(frame, "OnSelectAllTimelinePro", "OnSelectAllTimeline")
        return

    if has_modifier and not event.ShiftDown() and not event.AltDown() and raw_key == E_KEY:
        if get_program_mode() == PROFESSIONAL_MODE:
            frame.OnEditFocusedElement()
        return

    if has_modifier and raw_key == C_KEY:
        if event.ShiftDown() and not event.AltDown():
            frame.OnReplaceChromaBackground()
        else:
            run_mode_shortcut(frame, "OnCopyElements", "OnCopySegment")
        return

    if not has_command_modifier and not event.AltDown() and not event.ShiftDown() and key == ord("/"):
        frame.OnStartCaptionsExtraction()
        return

    if has_modifier and raw_key == H_KEY:
        frame.OnSetStart()
        return

    if has_modifier and raw_key == J_KEY:
        if event.ShiftDown():
            frame.OnRestoreSession()
        else:
            frame.OnSaveSession()
        return

    if has_modifier and raw_key == K_KEY:
        if event.ShiftDown():
            frame.OnMuteOriginalAudio()
            return
        frame.OnSetEnd()
        return

    if has_modifier and raw_key == L_KEY:
        if event.ShiftDown():
            frame.OnSpeakCurrentItems()
        else:
            frame.OnSpeakSelectionLength()
        return

    if has_modifier and raw_key == M_KEY:
        if event.ShiftDown():
            frame.OnMetadata()
        else:
            frame.OnAddVideo()
        return

    if has_modifier and event.ShiftDown() and raw_key == N_KEY:
        frame.OnSpeakEditPointCount()
        return

    if has_modifier and raw_key == Q_KEY:
        frame.OnClose()
        return

    if has_modifier and raw_key == R_KEY:
        if not event.ShiftDown():
            frame.OnRepeatSelection()
            return
        if hasattr(event, "DoAllowNextEvent"):
            event.DoAllowNextEvent()
        event.Skip()
        return

    if has_modifier and raw_key == T_KEY:
        if event.ShiftDown():
            frame.OnInsertText()
        else:
            frame.OnSpeakCurrentTime()
        return

    if has_modifier and raw_key == U_KEY:
        frame.OnCheckForUpdates()
        return

    if has_modifier and raw_key == V_KEY:
        if event.ShiftDown():
            frame.OnVisualEffects()
        else:
            run_mode_shortcut(frame, "OnPasteElements", "OnPasteSegment")
        return

    if has_modifier and raw_key == W_KEY:
        frame.OnClearWorkspace()
        return

    if has_modifier and raw_key == X_KEY:
        run_mode_shortcut(frame, "OnCutElements", "OnCutSegment")
        return

    if has_modifier and raw_key == Z_KEY:
        frame.OnUndoEdit()
        return

    if has_modifier and raw_key == Y_KEY:
        frame.OnRestoreEdit()
        return

    if has_modifier and event.ShiftDown() and raw_key == B_KEY:
        frame.OnInsertBackgroundAudio()
        return

    if has_modifier and event.ShiftDown() and raw_key == D_KEY:
        frame.OnDeleteCurrentBackgroundAudio()
        return

    if has_modifier and event.ShiftDown() and raw_key == E_KEY:
        frame.OnChooseAudioEffect()
        return

    if has_modifier and event.ShiftDown() and raw_key == F_KEY:
        frame.OnChangeSpeed()
        return

    if has_modifier and event.ShiftDown() and raw_key == G_KEY:
        frame.OnCensorBleep()
        return

    if has_modifier and event.ShiftDown() and raw_key == I_KEY:
        frame.OnInsertImage()
        return

    if has_modifier and event.ShiftDown() and raw_key == P_KEY:
        frame.OnTransitionEffects()
        return

    if has_modifier and raw_key in (NUMBER_1_KEY, wx.WXK_NUMPAD1):
        frame.OnCtrl1()
        return

    if has_modifier and raw_key in (NUMBER_2_KEY, wx.WXK_NUMPAD2):
        frame.OnCtrl2()
        return

    if has_modifier and raw_key in (NUMBER_3_KEY, wx.WXK_NUMPAD3):
        frame.OnCtrl3()
        return

    if has_modifier and (raw_key in HOME_KEYS or key in HOME_KEYS):
        if event.ShiftDown():
            frame.OnSelectFromStartToCurrent()
            return
        frame.OnHome()
        return

    if has_modifier and (raw_key in END_KEYS or key in END_KEYS):
        if event.ShiftDown():
            frame.OnSelectFromCurrentToEnd()
            return
        frame.OnEnd()
        return

    if has_modifier and event.ShiftDown() and key == wx.WXK_RIGHT:
        run_mode_shortcut(frame, "OnExtendSelectionLeft", "OnNextBackgroundAudio")
        return

    if has_modifier and event.ShiftDown() and key == wx.WXK_LEFT:
        run_mode_shortcut(frame, "OnExtendSelectionRight", "OnPreviousBackgroundAudio")
        return

    if has_modifier and event.ShiftDown() and key == wx.WXK_UP:
        frame.OnIncreaseCurrentBackgroundVolume()
        return

    if has_modifier and event.ShiftDown() and key == wx.WXK_DOWN:
        frame.OnDecreaseCurrentBackgroundVolume()
        return

    if has_modifier and event.ShiftDown() and key == wx.WXK_BACK:
        frame.OnSpeakCurrentEditPoint()
        return

    if has_modifier and key == wx.WXK_PAGEDOWN:
        frame.OnNextItemEdge()
        return

    if has_modifier and key == wx.WXK_PAGEUP:
        frame.OnPreviousItemEdge()
        return

    if has_modifier and key == wx.WXK_RIGHT:
        run_mode_shortcut(frame, "OnNextElementOnTrack", "OnNextEditPoint")
        return

    if has_modifier and key == wx.WXK_LEFT:
        run_mode_shortcut(frame, "OnPreviousElementOnTrack", "OnPreviousEditPoint")
        return

    if not has_modifier and key == wx.WXK_BACK:
        frame.OnDeleteCurrentEditPoint()
        return

    if not has_modifier and event.ShiftDown() and key == wx.WXK_PAGEDOWN:
        frame.OnFineForward()
        return

    if not has_modifier and event.ShiftDown() and key == wx.WXK_PAGEUP:
        frame.OnFineRewind()
        return

    if not has_modifier and (raw_key == wx.WXK_DELETE or key == wx.WXK_DELETE):
        frame.OnDeleteElement()
        return

    if not has_modifier and (raw_key == OPEN_BRACKET_KEY or key == ord("[")):
        frame.OnSetStart()
        return

    if not has_modifier and (raw_key == CLOSE_BRACKET_KEY or key == ord("]")):
        frame.OnSetEnd()
        return

    if hasattr(event, "DoAllowNextEvent"):
        event.DoAllowNextEvent()
    event.Skip()


def install_shortcuts(frame):
    ids = {
        "play_pause": wx.NewIdRef(),
        "pause": wx.NewIdRef(),
        "play_selected_range": wx.NewIdRef(),
        "play_except_selection": wx.NewIdRef(),
        "new_program_window": wx.NewIdRef(),
        "rename_program_window": wx.NewIdRef(),
        "next_program_window": wx.NewIdRef(),
        "previous_program_window": wx.NewIdRef(),
        "start": wx.NewIdRef(),
        "end": wx.NewIdRef(),
        "change_speed": wx.NewIdRef(),
        "speed_up_step": wx.NewIdRef(),
        "speed_down_step": wx.NewIdRef(),
        "speed_reset": wx.NewIdRef(),
        "rotate_video": wx.NewIdRef(),
        "mute_timeline_audio": wx.NewIdRef(),
        "mute_original_audio": wx.NewIdRef(),
        "mute_background_selection": wx.NewIdRef(),
        "censor_bleep": wx.NewIdRef(),
        "record_audio": wx.NewIdRef(),
        "prepare_screen_recording": wx.NewIdRef(),
        "start_screen_recording": wx.NewIdRef(),
        "pause_recording": wx.NewIdRef(),
        "stop_recording": wx.NewIdRef(),
        "delete": wx.NewIdRef(),
        "delete_timeline_file": wx.NewIdRef(),
        "save": wx.NewIdRef(),
        "save_selected": wx.NewIdRef(),
        "split_timeline": wx.NewIdRef(),
        "clear_workspace": wx.NewIdRef(),
        "save_session": wx.NewIdRef(),
        "restore_session": wx.NewIdRef(),
        "restore_crash_session": wx.NewIdRef(),
        "program_settings": wx.NewIdRef(),
        "toggle_program_mode": wx.NewIdRef(),
        "grok_keys_settings": wx.NewIdRef(),
        "captions_settings": wx.NewIdRef(),
        "captions_start": wx.NewIdRef(),
        "metadata": wx.NewIdRef(),
        "export_video_audio": wx.NewIdRef(),
        "import_video_audio": wx.NewIdRef(),
        "add_video": wx.NewIdRef(),
        "insert_timeline_audio": wx.NewIdRef(),
        "insert_timeline_silence": wx.NewIdRef(),
        "insert_image": wx.NewIdRef(),
        "insert_text": wx.NewIdRef(),
        "replace_chroma_background": wx.NewIdRef(),
        "add_watermark": wx.NewIdRef(),
        "remove_watermark": wx.NewIdRef(),
        "insert_background_audio": wx.NewIdRef(),
        "insert_work_video": wx.NewIdRef(),
        "choose_work_images": wx.NewIdRef(),
        "choose_work_videos": wx.NewIdRef(),
        "distribute_work_images": wx.NewIdRef(),
        "distribute_work_videos": wx.NewIdRef(),
        "image_duration": wx.NewIdRef(),
        "transition": wx.NewIdRef(),
        "timeline_transition": wx.NewIdRef(),
        "element_manager": wx.NewIdRef(),
        "stop_at_insert_edge": wx.NewIdRef(),
        "select_all": wx.NewIdRef(),
        "broadcast_start": wx.NewIdRef(),
        "broadcast_stop": wx.NewIdRef(),
        "broadcast_toggle": wx.NewIdRef(),
        "repeat_selection": wx.NewIdRef(),
        "undo": wx.NewIdRef(),
        "restore_edit": wx.NewIdRef(),
        "cut": wx.NewIdRef(),
        "copy": wx.NewIdRef(),
        "paste": wx.NewIdRef(),
        "home": wx.NewIdRef(),
        "end_key": wx.NewIdRef(),
        "page_up": wx.NewIdRef(),
        "page_down": wx.NewIdRef(),
        "next_edit_point": wx.NewIdRef(),
        "previous_edit_point": wx.NewIdRef(),
        "delete_edit_point": wx.NewIdRef(),
        "save_selected_shortcut": wx.NewIdRef(),
        "choose_audio_effect": wx.NewIdRef(),
        "transition_effects": wx.NewIdRef(),
        "visual_effects_shortcut": wx.NewIdRef(),
        "repeat_selection_shortcut": wx.NewIdRef(),
        "speak_selection_length": wx.NewIdRef(),
        "speak_current_time": wx.NewIdRef(),
        "speak_current_items": wx.NewIdRef(),
        "speak_edit_point_count": wx.NewIdRef(),
        "speak_current_edit_point": wx.NewIdRef(),
        "next_item_edge": wx.NewIdRef(),
        "previous_item_edge": wx.NewIdRef(),
        "next_timeline_file": wx.NewIdRef(),
        "previous_timeline_file": wx.NewIdRef(),
        "fine_forward": wx.NewIdRef(),
        "fine_rewind": wx.NewIdRef(),
        "select_start_current": wx.NewIdRef(),
        "select_current_end": wx.NewIdRef(),
        "increase_background_volume": wx.NewIdRef(),
        "decrease_background_volume": wx.NewIdRef(),
        "increase_volume_boost": wx.NewIdRef(),
        "decrease_volume_boost": wx.NewIdRef(),
        "previous_track": wx.NewIdRef(),
        "next_track": wx.NewIdRef(),
        "insert_track_item": wx.NewIdRef(),
        "increase_master_volume": wx.NewIdRef(),
        "decrease_master_volume": wx.NewIdRef(),
        "increase_track_volume": wx.NewIdRef(),
        "decrease_track_volume": wx.NewIdRef(),
        "next_background_audio": wx.NewIdRef(),
        "previous_background_audio": wx.NewIdRef(),
        "delete_background_audio": wx.NewIdRef(),
        "save_session_shortcut": wx.NewIdRef(),
        "restore_session_shortcut": wx.NewIdRef(),
        "restore_crash_session_shortcut": wx.NewIdRef(),
        "metadata_shortcut": wx.NewIdRef(),
        "exit_shortcut": wx.NewIdRef(),
        "ctrl1": wx.NewIdRef(),
        "ctrl2": wx.NewIdRef(),
        "ctrl3": wx.NewIdRef(),
        "facebook_contact": wx.NewIdRef(),
        "telegram_contact": wx.NewIdRef(),
        "telegram_apps": wx.NewIdRef(),
        "open_source_contribution": wx.NewIdRef(),
        "keyboard_shortcuts_help": wx.NewIdRef(),
        "copy_problem_log": wx.NewIdRef(),
        "export_problem_log": wx.NewIdRef(),
        "clear_problem_log": wx.NewIdRef(),
        "check_updates": wx.NewIdRef(),
        "about": wx.NewIdRef(),
        "change_application_name": wx.NewIdRef(),
        "language_ar": wx.NewIdRef(),
        "language_en": wx.NewIdRef(),
        "language_fr": wx.NewIdRef(),
        "theme_default": wx.NewIdRef(),
        "theme_dark": wx.NewIdRef(),
        "theme_high_black": wx.NewIdRef(),
        "theme_high_light": wx.NewIdRef(),
        "next_element_on_track": wx.NewIdRef(),
        "previous_element_on_track": wx.NewIdRef(),
        "extend_selection_right": wx.NewIdRef(),
        "extend_selection_left": wx.NewIdRef(),
        "select_all_pro": wx.NewIdRef(),
        "cut_elements": wx.NewIdRef(),
        "copy_elements": wx.NewIdRef(),
        "paste_elements": wx.NewIdRef(),
        "toggle_ripple_mode": wx.NewIdRef(),
    }

    frame.Bind(wx.EVT_MENU, frame.OnPlayPause, id=ids["play_pause"])
    frame.Bind(wx.EVT_MENU, frame.OnPause, id=ids["pause"])
    frame.Bind(wx.EVT_MENU, frame.OnPlaySelectedRange, id=ids["play_selected_range"])
    frame.Bind(wx.EVT_MENU, frame.OnPlayTimelineExceptSelection, id=ids["play_except_selection"])
    frame.Bind(wx.EVT_MENU, frame.OnNewProgramWindow, id=ids["new_program_window"])
    frame.Bind(wx.EVT_MENU, frame.OnRenameProgramWindow, id=ids["rename_program_window"])
    frame.Bind(wx.EVT_MENU, frame.OnNextProgramWindow, id=ids["next_program_window"])
    frame.Bind(wx.EVT_MENU, frame.OnPreviousProgramWindow, id=ids["previous_program_window"])
    frame.Bind(wx.EVT_MENU, frame.OnProgramSettings, id=ids["program_settings"])
    frame.Bind(wx.EVT_MENU, frame.OnToggleProgramMode, id=ids["toggle_program_mode"])
    frame.Bind(wx.EVT_MENU, frame.OnGrokKeysSettings, id=ids["grok_keys_settings"])
    frame.Bind(wx.EVT_MENU, frame.OnCaptionsSettings, id=ids["captions_settings"])
    frame.Bind(wx.EVT_MENU, frame.OnStartCaptionsExtraction, id=ids["captions_start"])
    frame.Bind(wx.EVT_MENU, frame.OnOpen, id=wx.ID_OPEN)
    frame.Bind(wx.EVT_MENU, frame.OnClose, id=wx.ID_EXIT)
    frame.Bind(wx.EVT_MENU, frame.OnForward, id=wx.ID_FORWARD)
    frame.Bind(wx.EVT_MENU, frame.OnRewind, id=wx.ID_BACKWARD)
    frame.Bind(wx.EVT_MENU, frame.OnIncreaseVolume, id=wx.ID_UP)
    frame.Bind(wx.EVT_MENU, frame.OnIncreaseVolumeBoost, id=ids["increase_volume_boost"])
    frame.Bind(wx.EVT_MENU, frame.OnDecreaseVolumeBoost, id=ids["decrease_volume_boost"])
    frame.Bind(wx.EVT_MENU, frame.OnIncreaseMasterVolume, id=ids["increase_master_volume"])
    frame.Bind(wx.EVT_MENU, frame.OnDecreaseMasterVolume, id=ids["decrease_master_volume"])
    frame.Bind(wx.EVT_MENU, frame.OnIncreaseTrackVolume, id=ids["increase_track_volume"])
    frame.Bind(wx.EVT_MENU, frame.OnDecreaseTrackVolume, id=ids["decrease_track_volume"])
    frame.Bind(wx.EVT_MENU, frame.OnPreviousTrack, id=ids["previous_track"])
    frame.Bind(wx.EVT_MENU, frame.OnNextTrack, id=ids["next_track"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertTrackItem, id=ids["insert_track_item"])
    frame.Bind(wx.EVT_MENU, frame.OnDecreaseVolume, id=wx.ID_DOWN)
    frame.Bind(wx.EVT_MENU, frame.OnSetStart, id=ids["start"])
    frame.Bind(wx.EVT_MENU, frame.OnSetEnd, id=ids["end"])
    frame.Bind(wx.EVT_MENU, frame.OnChangeSpeed, id=ids["change_speed"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeedUpStep, id=ids["speed_up_step"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeedDownStep, id=ids["speed_down_step"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeedReset, id=ids["speed_reset"])
    frame.Bind(wx.EVT_MENU, frame.OnRotateVideo, id=ids["rotate_video"])
    frame.Bind(wx.EVT_MENU, frame.OnMuteTimelineVideos, id=ids["mute_timeline_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnMuteOriginalAudio, id=ids["mute_original_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnMuteBackgroundAudioSelection, id=ids["mute_background_selection"])
    frame.Bind(wx.EVT_MENU, frame.OnCensorBleep, id=ids["censor_bleep"])
    frame.Bind(wx.EVT_MENU, frame.OnRecordAudio, id=ids["record_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnPrepareScreenRecording, id=ids["prepare_screen_recording"])
    frame.Bind(wx.EVT_MENU, frame.OnStartPreparedScreenRecording, id=ids["start_screen_recording"])
    frame.Bind(wx.EVT_MENU, frame.OnPauseResumeRecording, id=ids["pause_recording"])
    frame.Bind(wx.EVT_MENU, frame.OnStopRecording, id=ids["stop_recording"])
    frame.Bind(wx.EVT_MENU, frame.OnDeleteElement, id=ids["delete"])
    frame.Bind(wx.EVT_MENU, frame.OnDeleteCurrentTimelineFile, id=ids["delete_timeline_file"])
    frame.Bind(wx.EVT_MENU, frame.OnSaveVideo, id=ids["save"])
    frame.Bind(wx.EVT_MENU, frame.OnSaveSelectedVideo, id=ids["save_selected"])
    frame.Bind(wx.EVT_MENU, frame.OnClearWorkspace, id=ids["clear_workspace"])
    frame.Bind(wx.EVT_MENU, frame.OnSaveSession, id=ids["save_session"])
    frame.Bind(wx.EVT_MENU, frame.OnRestoreSession, id=ids["restore_session"])
    frame.Bind(wx.EVT_MENU, frame.OnRestoreCrashSession, id=ids["restore_crash_session"])
    frame.Bind(wx.EVT_MENU, frame.OnMetadata, id=ids["metadata"])
    frame.Bind(wx.EVT_MENU, frame.OnExportVideoAudio, id=ids["export_video_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnImportVideoAudio, id=ids["import_video_audio"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnMuteToggleCurrentTrack", "OnAddVideo"), id=ids["add_video"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertTimelineAudio, id=ids["insert_timeline_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertTimelineSilence, id=ids["insert_timeline_silence"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertImage, id=ids["insert_image"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertText, id=ids["insert_text"])
    frame.Bind(wx.EVT_MENU, frame.OnReplaceChromaBackground, id=ids["replace_chroma_background"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertBackgroundAudio, id=ids["insert_background_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnInsertWorkVideo, id=ids["insert_work_video"])
    frame.Bind(wx.EVT_MENU, frame.OnChooseWorkImages, id=ids["choose_work_images"])
    frame.Bind(wx.EVT_MENU, frame.OnChooseWorkVideos, id=ids["choose_work_videos"])
    frame.Bind(wx.EVT_MENU, frame.OnDistributeWorkImages, id=ids["distribute_work_images"])
    frame.Bind(wx.EVT_MENU, frame.OnDistributeWorkVideos, id=ids["distribute_work_videos"])
    frame.Bind(wx.EVT_MENU, frame.OnSetImageDuration, id=ids["image_duration"])
    frame.Bind(wx.EVT_MENU, frame.OnSetTransition, id=ids["transition"])
    frame.Bind(wx.EVT_MENU, frame.OnSetTimelineBoundaryTransition, id=ids["timeline_transition"])
    frame.Bind(wx.EVT_MENU, frame.OnElementManager, id=ids["element_manager"])
    frame.Bind(wx.EVT_MENU, frame.OnStopAtInsertEdge, id=ids["stop_at_insert_edge"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnSelectAllTimelinePro", "OnSelectAllTimeline"), id=ids["select_all"])
    frame.Bind(wx.EVT_MENU, frame.OnStartBroadcast, id=ids["broadcast_start"])
    frame.Bind(wx.EVT_MENU, frame.OnStopBroadcast, id=ids["broadcast_stop"])
    frame.Bind(wx.EVT_MENU, frame.OnToggleBroadcast, id=ids["broadcast_toggle"])
    frame.Bind(wx.EVT_MENU, frame.OnRepeatSelection, id=ids["repeat_selection"])
    frame.Bind(wx.EVT_MENU, frame.OnUndoEdit, id=ids["undo"])
    frame.Bind(wx.EVT_MENU, frame.OnRestoreEdit, id=ids["restore_edit"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnCutElements", "OnCutSegment"), id=ids["cut"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnCopyElements", "OnCopySegment"), id=ids["copy"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnPasteElements", "OnPasteSegment"), id=ids["paste"])
    frame.Bind(wx.EVT_MENU, frame.OnHome, id=ids["home"])
    frame.Bind(wx.EVT_MENU, frame.OnEnd, id=ids["end_key"])
    frame.Bind(wx.EVT_MENU, frame.OnPageUp, id=ids["page_up"])
    frame.Bind(wx.EVT_MENU, frame.OnPageDown, id=ids["page_down"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnNextElementOnTrack", "OnNextEditPoint"), id=ids["next_edit_point"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnPreviousElementOnTrack", "OnPreviousEditPoint"), id=ids["previous_edit_point"])
    frame.Bind(wx.EVT_MENU, frame.OnDeleteCurrentEditPoint, id=ids["delete_edit_point"])
    frame.Bind(wx.EVT_MENU, frame.OnSaveSelectedVideo, id=ids["save_selected_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnChooseAudioEffect, id=ids["choose_audio_effect"])
    frame.Bind(wx.EVT_MENU, frame.OnTransitionEffects, id=ids["transition_effects"])
    frame.Bind(wx.EVT_MENU, frame.OnVisualEffects, id=ids["visual_effects_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnRepeatSelection, id=ids["repeat_selection_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeakSelectionLength, id=ids["speak_selection_length"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeakCurrentTime, id=ids["speak_current_time"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeakCurrentItems, id=ids["speak_current_items"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeakEditPointCount, id=ids["speak_edit_point_count"])
    frame.Bind(wx.EVT_MENU, frame.OnSpeakCurrentEditPoint, id=ids["speak_current_edit_point"])
    frame.Bind(wx.EVT_MENU, frame.OnNextItemEdge, id=ids["next_item_edge"])
    frame.Bind(wx.EVT_MENU, frame.OnPreviousItemEdge, id=ids["previous_item_edge"])
    frame.Bind(wx.EVT_MENU, frame.OnNextTimelineFile, id=ids["next_timeline_file"])
    frame.Bind(wx.EVT_MENU, frame.OnPreviousTimelineFile, id=ids["previous_timeline_file"])
    frame.Bind(wx.EVT_MENU, frame.OnFineForward, id=ids["fine_forward"])
    frame.Bind(wx.EVT_MENU, frame.OnFineRewind, id=ids["fine_rewind"])
    frame.Bind(wx.EVT_MENU, frame.OnSelectFromStartToCurrent, id=ids["select_start_current"])
    frame.Bind(wx.EVT_MENU, frame.OnSelectFromCurrentToEnd, id=ids["select_current_end"])
    frame.Bind(wx.EVT_MENU, frame.OnIncreaseCurrentBackgroundVolume, id=ids["increase_background_volume"])
    frame.Bind(wx.EVT_MENU, frame.OnDecreaseCurrentBackgroundVolume, id=ids["decrease_background_volume"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnExtendSelectionLeft", "OnNextBackgroundAudio"), id=ids["next_background_audio"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(frame, "OnExtendSelectionRight", "OnPreviousBackgroundAudio"), id=ids["previous_background_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnDeleteCurrentBackgroundAudio, id=ids["delete_background_audio"])
    frame.Bind(wx.EVT_MENU, frame.OnSaveSession, id=ids["save_session_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnRestoreSession, id=ids["restore_session_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnRestoreCrashSession, id=ids["restore_crash_session_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnMetadata, id=ids["metadata_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnClose, id=ids["exit_shortcut"])
    frame.Bind(wx.EVT_MENU, frame.OnCtrl1, id=ids["ctrl1"])
    frame.Bind(wx.EVT_MENU, frame.OnCtrl2, id=ids["ctrl2"])
    frame.Bind(wx.EVT_MENU, frame.OnCtrl3, id=ids["ctrl3"])
    frame.Bind(wx.EVT_MENU, frame.OnFacebookContact, id=ids["facebook_contact"])
    frame.Bind(wx.EVT_MENU, frame.OnTelegramContact, id=ids["telegram_contact"])
    frame.Bind(wx.EVT_MENU, frame.OnTelegramApps, id=ids["telegram_apps"])
    frame.Bind(wx.EVT_MENU, frame.OnOpenSourceContribution, id=ids["open_source_contribution"])
    frame.Bind(wx.EVT_MENU, frame.OnKeyboardShortcutsHelp, id=ids["keyboard_shortcuts_help"])
    frame.Bind(wx.EVT_MENU, frame.OnCopyProblemLog, id=ids["copy_problem_log"])
    frame.Bind(wx.EVT_MENU, frame.OnExportProblemLog, id=ids["export_problem_log"])
    frame.Bind(wx.EVT_MENU, frame.OnClearProblemLog, id=ids["clear_problem_log"])
    frame.Bind(wx.EVT_MENU, frame.OnCheckForUpdates, id=ids["check_updates"])
    frame.Bind(wx.EVT_MENU, frame.OnAbout, id=ids["about"])
    frame.Bind(wx.EVT_MENU, frame.OnChangeApplicationName, id=ids["change_application_name"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetLanguage("ar"), id=ids["language_ar"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetLanguage("en"), id=ids["language_en"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetLanguage("fr"), id=ids["language_fr"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetTheme("default"), id=ids["theme_default"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetTheme("dark"), id=ids["theme_dark"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetTheme("high_black"), id=ids["theme_high_black"])
    frame.Bind(wx.EVT_MENU, lambda event: frame.OnSetTheme("high_light"), id=ids["theme_high_light"])
    frame.Bind(wx.EVT_MENU, frame.OnNextElementOnTrack, id=ids["next_element_on_track"])
    frame.Bind(wx.EVT_MENU, frame.OnPreviousElementOnTrack, id=ids["previous_element_on_track"])
    frame.Bind(wx.EVT_MENU, frame.OnExtendSelectionRight, id=ids["extend_selection_right"])
    frame.Bind(wx.EVT_MENU, frame.OnExtendSelectionLeft, id=ids["extend_selection_left"])
    frame.Bind(wx.EVT_MENU, frame.OnSelectAllTimelinePro, id=ids["select_all_pro"])
    frame.Bind(wx.EVT_MENU, frame.OnCutElements, id=ids["cut_elements"])
    frame.Bind(wx.EVT_MENU, frame.OnCopyElements, id=ids["copy_elements"])
    frame.Bind(wx.EVT_MENU, frame.OnPasteElements, id=ids["paste_elements"])
    frame.Bind(wx.EVT_MENU, frame.OnToggleRippleMode, id=ids["toggle_ripple_mode"])
    frame.Bind(wx.EVT_CHAR_HOOK, lambda event: handle_language_independent_shortcuts(frame, event))

    frame.SetAcceleratorTable(wx.AcceleratorTable([
        (wx.ACCEL_CTRL, ord("N"), ids["new_program_window"]),
        (wx.ACCEL_SHIFT, wx.WXK_F12, ids["broadcast_toggle"]),
        
        (wx.ACCEL_CTRL, ord("O"), wx.ID_OPEN),
        (wx.ACCEL_NORMAL, wx.WXK_F2, ids["rename_program_window"]),
        (wx.ACCEL_CTRL, wx.WXK_TAB, ids["next_program_window"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_TAB, ids["previous_program_window"]),
        (wx.ACCEL_NORMAL, wx.WXK_SPACE, ids["play_pause"]),
        (wx.ACCEL_SHIFT, wx.WXK_SPACE, ids["play_selected_range"]),
        (wx.ACCEL_CTRL, wx.WXK_SPACE, ids["play_except_selection"]),
        (wx.ACCEL_NORMAL, ord("X"), ids["pause"]),
        (wx.ACCEL_NORMAL, wx.WXK_RIGHT, wx.ID_FORWARD),
        (wx.ACCEL_SHIFT, wx.WXK_RIGHT, ids["stop_at_insert_edge"]),
        (wx.ACCEL_NORMAL, wx.WXK_LEFT, wx.ID_BACKWARD),
        (wx.ACCEL_NORMAL, wx.WXK_UP, wx.ID_UP),
        (wx.ACCEL_SHIFT, wx.WXK_UP, ids["increase_volume_boost"]),
        (wx.ACCEL_SHIFT, wx.WXK_DOWN, ids["decrease_volume_boost"]),
        (wx.ACCEL_NORMAL, wx.WXK_DOWN, wx.ID_DOWN),
        (wx.ACCEL_CTRL, ord("H"), ids["start"]),
        (wx.ACCEL_CTRL, ord("K"), ids["end"]),
        (wx.ACCEL_NORMAL, ord("["), ids["start"]),
        (wx.ACCEL_NORMAL, ord("]"), ids["end"]),
        (wx.ACCEL_NORMAL, wx.WXK_DELETE, ids["delete"]),
        (wx.ACCEL_CTRL, ord("S"), ids["save"]),
        (wx.ACCEL_CTRL, ord("W"), ids["clear_workspace"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("W"), ids["timeline_transition"]),
        (wx.ACCEL_CTRL, ord("M"), ids["add_video"]),
        (wx.ACCEL_CTRL, ord("B"), ids["insert_timeline_audio"]),
        (wx.ACCEL_CTRL, ord("D"), ids["insert_timeline_silence"]),
        (wx.ACCEL_NORMAL, ord("I"), ids["insert_track_item"]),
        (wx.ACCEL_CTRL, ord("A"), ids["select_all"]),
        (wx.ACCEL_CTRL, ord("Z"), ids["undo"]),
        (wx.ACCEL_CTRL, ord("Y"), ids["restore_edit"]),
        (wx.ACCEL_CTRL, ord("X"), ids["cut"]),
        (wx.ACCEL_CTRL, ord("C"), ids["copy"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("C"), ids["replace_chroma_background"]),
        (wx.ACCEL_CTRL, ord("V"), ids["paste"]),
        (wx.ACCEL_NORMAL, wx.WXK_HOME, ids["home"]),
        (wx.ACCEL_NORMAL, wx.WXK_END, ids["end_key"]),
        (wx.ACCEL_CTRL, wx.WXK_HOME, ids["home"]),
        (wx.ACCEL_CTRL, wx.WXK_END, ids["end_key"]),
        (wx.ACCEL_NORMAL, wx.WXK_PAGEUP, ids["page_up"]),
        (wx.ACCEL_NORMAL, wx.WXK_PAGEDOWN, ids["page_down"]),
        (wx.ACCEL_CTRL, wx.WXK_RIGHT, ids["next_edit_point"]),
        (wx.ACCEL_CTRL, wx.WXK_LEFT, ids["previous_edit_point"]),
        (wx.ACCEL_NORMAL, wx.WXK_BACK, ids["delete_edit_point"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("S"), ids["save_selected_shortcut"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("B"), ids["insert_background_audio"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("I"), ids["insert_image"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("T"), ids["insert_text"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("E"), ids["choose_audio_effect"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("P"), ids["transition_effects"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("V"), ids["visual_effects_shortcut"]),
        (wx.ACCEL_CTRL, ord("R"), ids["repeat_selection_shortcut"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("F"), ids["change_speed"]),
        (wx.ACCEL_ALT, wx.WXK_RIGHT, ids["speed_up_step"]),
        (wx.ACCEL_ALT, wx.WXK_LEFT, ids["speed_down_step"]),
        (wx.ACCEL_ALT, ord("0"), ids["speed_reset"]),
        (wx.ACCEL_ALT, wx.WXK_NUMPAD0, ids["speed_reset"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("O"), ids["rotate_video"]),
        (wx.ACCEL_NORMAL, ord("K"), ids["end"]),
        (wx.ACCEL_NORMAL, ord("B"), ids["mute_background_selection"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("K"), ids["mute_original_audio"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("G"), ids["censor_bleep"]),
        (wx.ACCEL_CTRL, wx.WXK_F9, ids["record_audio"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_F9, ids["prepare_screen_recording"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_F9, ids["start_screen_recording"]),
        (wx.ACCEL_CTRL, wx.WXK_F7, ids["pause_recording"]),
        (wx.ACCEL_CTRL, wx.WXK_F8, ids["stop_recording"]),
        (wx.ACCEL_CTRL, wx.WXK_F4, ids["delete_timeline_file"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_F7, ids["pause_recording"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_F8, ids["stop_recording"]),
        (wx.ACCEL_CTRL, ord("L"), ids["speak_selection_length"]),
        (wx.ACCEL_CTRL, ord("T"), ids["speak_current_time"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("L"), ids["speak_current_items"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("N"), ids["speak_edit_point_count"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_BACK, ids["speak_current_edit_point"]),
        (wx.ACCEL_CTRL, wx.WXK_PAGEDOWN, ids["next_item_edge"]),
        (wx.ACCEL_CTRL, wx.WXK_PAGEUP, ids["previous_item_edge"]),
        (wx.ACCEL_NORMAL, wx.WXK_TAB, ids["next_timeline_file"]),
        (wx.ACCEL_SHIFT, wx.WXK_TAB, ids["previous_timeline_file"]),
        (wx.ACCEL_SHIFT, wx.WXK_PAGEDOWN, ids["fine_forward"]),
        (wx.ACCEL_SHIFT, wx.WXK_PAGEUP, ids["fine_rewind"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_HOME, ids["select_start_current"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_END, ids["select_current_end"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_UP, ids["increase_background_volume"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_DOWN, ids["decrease_background_volume"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_UP, ids["increase_master_volume"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_DOWN, ids["decrease_master_volume"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_RIGHT, ids["increase_track_volume"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, wx.WXK_LEFT, ids["decrease_track_volume"]),
        (wx.ACCEL_ALT, wx.WXK_UP, ids["increase_track_volume"]),
        (wx.ACCEL_ALT, wx.WXK_DOWN, ids["decrease_track_volume"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_RIGHT, ids["next_background_audio"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_LEFT, ids["previous_background_audio"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("D"), ids["delete_background_audio"]),
        (wx.ACCEL_CTRL, ord("J"), ids["save_session_shortcut"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("J"), ids["restore_session_shortcut"]),
        (wx.ACCEL_CTRL | wx.ACCEL_ALT, ord("J"), ids["restore_crash_session_shortcut"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("M"), ids["metadata_shortcut"]),
        (wx.ACCEL_CTRL, ord("Q"), ids["exit_shortcut"]),
        (wx.ACCEL_CTRL, ord("U"), ids["check_updates"]),
        (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, wx.WXK_F2, ids["change_application_name"]),
        (wx.ACCEL_CTRL, ord("1"), ids["ctrl1"]),
        (wx.ACCEL_CTRL, ord("2"), ids["ctrl2"]),
        (wx.ACCEL_CTRL, ord("3"), ids["ctrl3"]),
        (wx.ACCEL_NORMAL, ord("1"), ids["ctrl1"]),
        (wx.ACCEL_NORMAL, ord("2"), ids["ctrl2"]),
        (wx.ACCEL_NORMAL, ord("3"), ids["ctrl3"]),
        (wx.ACCEL_NORMAL, wx.WXK_NUMPAD1, ids["ctrl1"]),
        (wx.ACCEL_NORMAL, wx.WXK_NUMPAD2, ids["ctrl2"]),
        (wx.ACCEL_NORMAL, wx.WXK_NUMPAD3, ids["ctrl3"]),
        (wx.ACCEL_NORMAL, ord('/'), ids["captions_start"]),
        (wx.ACCEL_SHIFT, ord(">"), ids["toggle_program_mode"]),
        (wx.ACCEL_ALT, ord("P"), ids["toggle_ripple_mode"]),
    ]))

    return ids
