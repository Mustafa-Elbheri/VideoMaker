import wx

from video_maker.app_state import get_language, get_theme, normalize_ripple_mode
from video_maker.localization import tr
from video_maker.program_modes import NORMAL_MODE, PROFESSIONAL_MODE, get_program_mode, run_mode_shortcut
from video_maker.recent_files import list_recent_files
from video_maker.themes import THEMES
from video_maker.tracks import TRACKS, track_index, track_label

def menu_label(label, shortcut, include_shortcuts):
    label = tr(label)
    if include_shortcuts and shortcut:
        return f"{label}\t{localized_shortcut(shortcut)}"
    return label


def localized_shortcut(shortcut):
    key_names = {
        "ar": {
            "Right": "سهم يمين",
            "Left": "سهم يسار",
            "Up": "سهم لأعلى",
            "Down": "سهم لأسفل",
            ".": "نقطة",
        },
        "en": {".": "Period"},
        "fr": {".": "Point"},
    }
    names = key_names.get(get_language(), {})
    return "+".join(names.get(part, part) for part in shortcut.split("+"))




def history_menu_label(target, action, shortcut, include_shortcuts):
    history = getattr(target, "edit_history", None)
    if action == "undo":
        base = "تراجع"
        operation = history.next_undo_operation() if history else ""
    else:
        base = "استعادة"
        operation = history.next_restore_operation() if history else ""
    label = tr(base)
    if operation:
        label = f"{label}: {tr(operation)}"
    if include_shortcuts:
        return f"{label}\t{localized_shortcut(shortcut)}"
    return label


def selection_shortcut(kind):
    language = get_language()
    if language == "ar":
        return "ج أو Ctrl+H" if kind == "start" else "د أو K أو Ctrl+K"
    if language == "fr":
        return "[ ou Ctrl+H" if kind == "start" else "] ou K ou Ctrl+K"
    return "[ or Ctrl+H" if kind == "start" else "] or K or Ctrl+K"


def edge_shortcut(kind):
    key = "Home" if kind == "start" else "End"
    control_key = "Ctrl+Home" if kind == "start" else "Ctrl+End"
    language = get_language()
    if language == "ar":
        return f"{key} أو {control_key}"
    if language == "fr":
        return f"{key} ou {control_key}"
    return f"{key} or {control_key}"




def project_output_kind(target):
    """Return the existing save mode for the current project.

    An audio source remains an audio project until at least one visual item is
    present. Adding an image, text, or video switches saving to video; removing
    the last visual item switches it back to audio.
    """
    media_kind = getattr(target, "media_kind", "video")
    has_visuals = bool(getattr(target, "visual_items", []))
    return "audio" if media_kind == "audio" and not has_visuals else "video"


def install_menu_bar(frame, command_target=None, include_shortcuts=True):
    target = command_target or frame
    ids = target.shortcut_ids
    media_kind = getattr(target, "media_kind", "video")
    output_kind = project_output_kind(target)
    merge_audio_images_id = wx.NewIdRef()
    merge_audio_files_id = wx.NewIdRef()
    merge_audio_video_id = wx.NewIdRef()
    merge_video_clips_id = wx.NewIdRef()
    broadcast_start_id = wx.NewIdRef()
    broadcast_stop_id = wx.NewIdRef()
    visual_effects_id = wx.NewIdRef()
    save_project_id = wx.NewIdRef()
    restore_project_id = wx.NewIdRef()
    file_menu = wx.Menu()
    file_menu.Append(ids["new_program_window"], menu_label("نافذة جديدة", "Ctrl+N", include_shortcuts))
    file_menu.Append(wx.ID_OPEN, menu_label("فتح ملف", "Ctrl+O", include_shortcuts))
    recent_files_menu = wx.Menu()
    recent_files = list_recent_files()
    if recent_files:
        for recent_file in recent_files:
            item_id = wx.NewIdRef()
            recent_files_menu.Append(item_id, recent_file.label)
            frame.Bind(wx.EVT_MENU, lambda event, path=recent_file.path: target.OnOpenRecentFile(path), id=item_id)
        recent_files_menu.AppendSeparator()
        clear_recent_id = wx.NewIdRef()
        recent_files_menu.Append(clear_recent_id, tr("تفريغ القائمة"))
        frame.Bind(wx.EVT_MENU, target.OnClearRecentFiles, id=clear_recent_id)
    else:
        empty_recent_id = wx.NewIdRef()
        empty_item = recent_files_menu.Append(empty_recent_id, tr("لا توجد ملفات أخيرة"))
        empty_item.Enable(False)
    file_menu.AppendSubMenu(recent_files_menu, tr("الملفات الأخيرة"))
    save_label = "حفظ الصوت" if output_kind == "audio" else "حفظ الفيديو"
    file_menu.Append(ids["save"], menu_label(save_label, "Ctrl+S", include_shortcuts))
    file_menu.Append(ids["save_selected"], menu_label("حفظ المحدد", "Ctrl+Shift+S", include_shortcuts))
    file_menu.Append(ids["split_timeline"], tr("تقسيم"))
    if media_kind == "video":
        file_menu.Append(ids["export_video_audio"], tr("تصدير الملف الصوتي الخاص بهذا الفيديو"))
        file_menu.Append(ids["import_video_audio"], tr("استيراد الملف الصوتي الخاص بهذا الفيديو"))
    file_menu.Append(ids["clear_workspace"], menu_label("تنظيف مساحة العمل", "Ctrl+W", include_shortcuts))
    file_menu.AppendSeparator()
    file_menu.Append(save_project_id, tr("حفظ المشروع"))
    file_menu.Append(restore_project_id, tr("استعادة المشروع"))
    file_menu.AppendSeparator()
    file_menu.Append(ids["save_session"], menu_label("حفظ جلسة العمل الحالية", "Ctrl+J", include_shortcuts))
    file_menu.Append(ids["restore_session"], menu_label("استعادة جلسة العمل", "Ctrl+Shift+J", include_shortcuts))
    file_menu.Append(ids["restore_crash_session"], menu_label("استعادة جلسة الإغلاق المفاجئ", "Ctrl+Alt+J", include_shortcuts))
    file_menu.AppendSeparator()
    file_menu.Append(ids["metadata"], menu_label("المعلومات", "Ctrl+Shift+M", include_shortcuts))
    file_menu.AppendSeparator()
    file_menu.Append(wx.ID_EXIT, menu_label("خروج", "Ctrl+Q", include_shortcuts))

    merge_menu = wx.Menu()
    merge_menu.Append(merge_audio_images_id, tr("دمج الصوت مع الصور"))
    merge_menu.Append(merge_audio_files_id, tr("دمج الملفات الصوتية"))
    merge_menu.Append(merge_audio_video_id, tr("دمج الصوت مع الفيديو"))
    merge_menu.Append(merge_video_clips_id, tr("دمج مقاطع الفيديو"))

    insert_menu = wx.Menu()
    if media_kind == "audio":
        insert_menu.Append(ids["choose_work_images"], tr("اختيار صور للعمل"))
        insert_menu.Append(ids["choose_work_videos"], tr("اختيار فيديوهات للعمل"))
        insert_menu.AppendSeparator()
        insert_menu.Append(ids["insert_image"], menu_label("إدراج صورة", "Ctrl+Shift+I", include_shortcuts))
        insert_menu.Append(ids["insert_text"], menu_label("إدراج نص", "Ctrl+Shift+T", include_shortcuts))
        insert_menu.Append(ids["insert_timeline_audio"], menu_label("إدراج صوت", "Shift+F12", include_shortcuts))
        insert_menu.Append(ids["insert_timeline_silence"], menu_label("إدراج صمت", "Ctrl+D", include_shortcuts))
        insert_menu.Append(ids["insert_background_audio"], menu_label("إدراج خلفية صوتية", "Ctrl+Shift+B", include_shortcuts))
        insert_menu.Append(ids["insert_work_video"], tr("إدراج فيديو"))
        insert_menu.AppendSeparator()
        insert_menu.Append(ids["distribute_work_images"], tr("دمج الصور مع الصوت بالتوزيع المتساوي"))
        insert_menu.Append(ids["distribute_work_videos"], tr("دمج الفيديوهات مع الصوت بالتوزيع المتساوي"))
        insert_menu.Append(ids["image_duration"], tr("اختيار مدة كل صورة"))
        insert_menu.Append(ids["transition"], tr("تأثيرات الانتقالات"))
        insert_menu.Append(ids["stop_at_insert_edge"], menu_label("أوقفني عند حافة ما أضفت", "Shift+Right", include_shortcuts))
    else:
        insert_menu.Append(ids["add_video"], menu_label("إضافة فيديو عند الموضع الحالي", "Ctrl+M", include_shortcuts))
        insert_menu.Append(ids["insert_timeline_audio"], menu_label("إدراج صوت", "Shift+F12", include_shortcuts))
        insert_menu.Append(ids["insert_timeline_silence"], menu_label("إدراج صمت", "Ctrl+D", include_shortcuts))
        insert_menu.Append(ids["insert_image"], menu_label("إدراج صورة", "Ctrl+Shift+I", include_shortcuts))
        insert_menu.Append(ids["insert_text"], menu_label("إدراج نص", "Ctrl+Shift+T", include_shortcuts))
        insert_menu.Append(ids["replace_chroma_background"], menu_label("استبدال خلفية الفيديو إذا كانت كرومة", "Ctrl+Shift+C", include_shortcuts))
        watermark_menu = wx.Menu()
        watermark_menu.Append(ids["add_watermark"], tr("إضافة علامة مائية"))
        watermark_menu.Append(ids["remove_watermark"], tr("إزالة علامة مائية"))
        insert_menu.AppendSubMenu(watermark_menu, tr("العلامة المائية"))
        insert_menu.Append(ids["insert_background_audio"], menu_label("إدراج خلفية صوتية", "Ctrl+Shift+B", include_shortcuts))
        insert_menu.Append(ids["timeline_transition"], menu_label("انتقال عند الحد بين مقطعين", "Ctrl+Shift+W", include_shortcuts))

    effects_menu = wx.Menu()
    if media_kind != "audio":
        effects_menu.Append(visual_effects_id, menu_label("المؤثرات المرئية", "Ctrl+Shift+V", include_shortcuts))
    effects_menu.Append(ids["transition_effects"], menu_label("المؤثرات الانتقالية", "Ctrl+Shift+P", include_shortcuts))
    effects_menu.Append(ids["choose_audio_effect"], menu_label("المؤثرات الصوتية", "Ctrl+Shift+E", include_shortcuts))

    playback_menu = wx.Menu()
    space_label = "تشغيل أو إيقاف مؤقت" if get_program_mode() == NORMAL_MODE else "تشغيل من الموضع والعودة إليه"
    playback_menu.Append(ids["play_pause"], menu_label(space_label, "Space", include_shortcuts))
    playback_menu.Append(ids["pause"], menu_label("تشغيل أو إيقاف مؤقت", "X", include_shortcuts))
    playback_menu.Append(ids["play_selected_range"], menu_label("تشغيل الجزء المحدد", "Shift+Space", include_shortcuts))
    playback_menu.Append(ids["play_except_selection"], menu_label("تشغيل الخط الزمني فيما عدا الجزء المحدد", "Ctrl+Space", include_shortcuts))
    playback_menu.Append(wx.ID_FORWARD, menu_label("تقديم", "Right", include_shortcuts))
    playback_menu.Append(wx.ID_BACKWARD, menu_label("ترجيع", "Left", include_shortcuts))
    playback_menu.Append(ids["home"], menu_label("الانتقال إلى بداية الملف", edge_shortcut("start"), include_shortcuts))
    playback_menu.Append(ids["end_key"], menu_label("الانتقال إلى نهاية الملف", edge_shortcut("end"), include_shortcuts))
    playback_menu.Append(ids["page_up"], menu_label("رجوع 20 ثانية", "Page Up", include_shortcuts))
    playback_menu.Append(ids["page_down"], menu_label("تقدم 20 ثانية", "Page Down", include_shortcuts))
    playback_menu.Append(ids["fine_rewind"], menu_label("رجوع ثانية واحدة", "Shift+Page Up", include_shortcuts))
    playback_menu.Append(ids["fine_forward"], menu_label("تقدم ثانية واحدة", "Shift+Page Down", include_shortcuts))
    playback_menu.AppendSeparator()
    playback_menu.Append(ids["next_edit_point"], menu_label("نقطة التعديل التالية", "Ctrl+Right", include_shortcuts))
    playback_menu.Append(ids["previous_edit_point"], menu_label("نقطة التعديل السابقة", "Ctrl+Left", include_shortcuts))
    playback_menu.Append(ids["speak_current_edit_point"], menu_label("نطق نقطة التعديل الحالية", "Ctrl+Shift+Backspace", include_shortcuts))
    playback_menu.Append(ids["delete_edit_point"], menu_label("حذف نقطة التعديل الحالية", "Backspace", include_shortcuts))
    playback_menu.Append(ids["next_item_edge"], menu_label("حافة العنصر التالية", "Ctrl+Page Down", include_shortcuts))
    playback_menu.Append(ids["previous_item_edge"], menu_label("حافة العنصر السابقة", "Ctrl+Page Up", include_shortcuts))
    playback_menu.Append(ids["next_timeline_file"], menu_label("الملف التالي في الخط الزمني", "Tab", include_shortcuts))
    playback_menu.Append(ids["previous_timeline_file"], menu_label("الملف السابق في الخط الزمني", "Shift+Tab", include_shortcuts))
    playback_menu.Append(ids["next_program_window"], menu_label("النافذة التالية", "Ctrl+Tab", include_shortcuts))
    playback_menu.Append(ids["previous_program_window"], menu_label("النافذة السابقة", "Ctrl+Shift+Tab", include_shortcuts))
    playback_menu.AppendSeparator()
    playback_menu.Append(ids["next_background_audio"], menu_label("الخلفية الصوتية التالية", "Ctrl+Shift+Right", include_shortcuts))
    playback_menu.Append(ids["previous_background_audio"], menu_label("الخلفية الصوتية السابقة", "Ctrl+Shift+Left", include_shortcuts))
    playback_menu.Append(ids["increase_background_volume"], menu_label("رفع صوت الخلفية الحالية", "Ctrl+Shift+Up", include_shortcuts))
    playback_menu.Append(ids["decrease_background_volume"], menu_label("خفض صوت الخلفية الحالية", "Ctrl+Shift+Down", include_shortcuts))
    playback_menu.Append(ids["delete_background_audio"], menu_label("حذف الخلفية الصوتية الحالية", "Ctrl+Shift+D", include_shortcuts))
    playback_menu.AppendSeparator()
    playback_menu.Append(ids["speak_current_time"], menu_label("نطق الوقت الحالي", "Ctrl+T", include_shortcuts))
    playback_menu.Append(ids["speak_selection_length"], menu_label("نطق مدة التحديد", "Ctrl+L", include_shortcuts))
    playback_menu.Append(ids["speak_current_items"], menu_label("نطق عناصر الموضع الحالي", "Ctrl+Shift+L", include_shortcuts))
    playback_menu.Append(ids["speak_edit_point_count"], menu_label("نطق عدد مواضع التعديل", "Ctrl+Shift+N", include_shortcuts))
    playback_menu.AppendSeparator()
    if get_program_mode() == PROFESSIONAL_MODE:
        playback_menu.Append(ids["previous_track"], menu_label("التراك السابق", "Up", include_shortcuts))
        playback_menu.Append(ids["next_track"], menu_label("التراك التالي", "Down", include_shortcuts))
        playback_menu.Append(ids["insert_track_item"], menu_label("إدراج في التراك الحالي", "I", include_shortcuts))
    else:
        playback_menu.Append(wx.ID_UP, menu_label("رفع الصوت", "Up", include_shortcuts))
        playback_menu.Append(wx.ID_DOWN, menu_label("خفض الصوت", "Down", include_shortcuts))
    playback_menu.Append(ids["increase_volume_boost"], menu_label("رفع مستوى الصوت حتى 400 بالمئة", "Shift+Up", include_shortcuts))
    playback_menu.Append(ids["decrease_volume_boost"], menu_label("خفض مستوى الصوت فوق 100 بالمئة", "Shift+Down", include_shortcuts))
    playback_menu.AppendSeparator()
    playback_menu.Append(ids["increase_master_volume"], menu_label("رفع مستوى الماستر بدرجة ديسيبل", "Ctrl+Alt+Up", include_shortcuts))
    playback_menu.Append(ids["decrease_master_volume"], menu_label("خفض مستوى الماستر بدرجة ديسيبل", "Ctrl+Alt+Down", include_shortcuts))
    playback_menu.AppendSeparator()
    playback_menu.Append(ids["increase_track_volume"], menu_label("رفع مستوى التراك الحالي بدرجة ديسيبل", "Alt+Up", include_shortcuts))
    playback_menu.Append(ids["decrease_track_volume"], menu_label("خفض مستوى التراك الحالي بدرجة ديسيبل", "Alt+Down", include_shortcuts))

    edit_menu = wx.Menu()
    undo_item = edit_menu.Append(ids["undo"], history_menu_label(target, "undo", "Ctrl+Z", include_shortcuts))
    restore_item = edit_menu.Append(ids["restore_edit"], history_menu_label(target, "restore", "Ctrl+Y", include_shortcuts))
    history = getattr(target, "edit_history", None)
    undo_item.Enable(bool(history and history.can_undo()))
    restore_item.Enable(bool(history and history.can_restore()))
    target.undo_menu_item = undo_item
    target.restore_menu_item = restore_item
    edit_menu.AppendSeparator()
    edit_menu.Append(ids["change_speed"], menu_label("تسريع وإبطاء", "Ctrl+Shift+F", include_shortcuts))
    edit_menu.Append(ids["rotate_video"], menu_label("تدوير الفيديو", "Ctrl+Shift+O", include_shortcuts))
    edit_menu.Append(ids["mute_timeline_audio"], tr("كتم صوت الخط الزمني كامل"))
    edit_menu.Append(ids["mute_original_audio"], menu_label("كتم الجزء المحدد", "Ctrl+Shift+K", include_shortcuts))
    edit_menu.Append(ids["mute_background_selection"], menu_label("كتم صوت الخلفية في الجزء المحدد", "B", include_shortcuts))
    edit_menu.Append(ids["censor_bleep"], menu_label("كتم كلمة بصوت تغطية", "Ctrl+Shift+G", include_shortcuts))
    edit_menu.AppendSeparator()
    edit_menu.Append(ids["start"], menu_label("تحديد بداية المقطع", selection_shortcut("start"), include_shortcuts))
    edit_menu.Append(ids["end"], menu_label("تحديد نهاية المقطع", selection_shortcut("end"), include_shortcuts))
    edit_menu.Append(ids["select_all"], menu_label("تحديد كامل الخط الزمني", "Ctrl+A", include_shortcuts))
    edit_menu.Append(ids["select_start_current"], menu_label("تحديد من البداية إلى الموضع الحالي", "Ctrl+Shift+Home", include_shortcuts))
    edit_menu.Append(ids["select_current_end"], menu_label("تحديد من الموضع الحالي إلى النهاية", "Ctrl+Shift+End", include_shortcuts))
    edit_menu.Append(ids["delete"], menu_label("حذف المقطع المحدد", "Delete", include_shortcuts))
    edit_menu.Append(ids["delete_timeline_file"], menu_label("حذف الملف الحالي من الخط الزمني", "Ctrl+F4", include_shortcuts))
    edit_menu.AppendSeparator()
    edit_menu.Append(ids["repeat_selection"], menu_label("تكرار المقطع المحدد", "Ctrl+R", include_shortcuts))
    edit_menu.Append(ids["cut"], menu_label("قص المقطع المحدد", "Ctrl+X", include_shortcuts))
    edit_menu.Append(ids["copy"], menu_label("نسخ المقطع المحدد", "Ctrl+C", include_shortcuts))
    edit_menu.Append(ids["paste"], menu_label("لصق المقطع عند الموضع الحالي", "Ctrl+V", include_shortcuts))
    edit_menu.AppendSeparator()
    edit_menu.Append(ids["rename_program_window"], menu_label("تسمية النافذة", "F2", include_shortcuts))
    edit_menu.AppendSeparator()
    edit_menu.Append(ids["element_manager"], menu_label("مدير العناصر", "E", include_shortcuts))
    edit_menu.AppendSeparator()
    edit_menu.Append(ids["ctrl1"], menu_label("تقليل خطوة التنقل", "1", include_shortcuts))
    edit_menu.Append(ids["ctrl2"], menu_label("زيادة خطوة التنقل", "2", include_shortcuts))
    edit_menu.Append(ids["ctrl3"], menu_label("إعادة ضبط خطوة التنقل", "3", include_shortcuts))

    view_menu = None
    pro_view_handlers = (
        get_program_mode() == PROFESSIONAL_MODE
        and hasattr(target, "OnSetRippleModeValue")
        and hasattr(target, "OnToggleTrackMuteValue")
        and hasattr(target, "OnToggleTrackSoloValue")
        and hasattr(target, "OnSpeakEditorStatus")
    )
    if pro_view_handlers:
        view_menu = wx.Menu()
        ripple_menu = wx.Menu()
        ripple_modes = [
            ("per_track", tr("لكل تراك")),
            ("all_tracks", tr("كل التراكات")),
            ("off", tr("مطفأ")),
        ]
        current_ripple = normalize_ripple_mode(getattr(target, "ripple_mode", "per_track"))
        for mode, mode_label in ripple_modes:
            item = ripple_menu.AppendRadioItem(wx.NewIdRef(), f"{mode_label} (Ripple)")
            item.Check(mode == current_ripple)
            frame.Bind(wx.EVT_MENU, lambda event, selected=mode: target.OnSetRippleModeValue(selected), id=item.GetId())
        view_menu.AppendSubMenu(ripple_menu, tr("وضع Ripple"))
        view_menu.AppendSeparator()
        muted_tracks = set(getattr(target, "muted_tracks", ()) or ())
        for track in TRACKS:
            key = track["key"]
            item = view_menu.AppendCheckItem(
                wx.NewIdRef(),
                tr("كتم التراك {number} {label}").format(number=track_index(key) + 1, label=tr(track_label(key))),
            )
            item.Check(key in muted_tracks)
            frame.Bind(wx.EVT_MENU, lambda event, selected=key: target.OnToggleTrackMuteValue(selected), id=item.GetId())
        view_menu.AppendSeparator()
        solo_tracks = set(getattr(target, "solo_tracks", ()) or ())
        for track in TRACKS:
            key = track["key"]
            item = view_menu.AppendCheckItem(
                wx.NewIdRef(),
                tr("عزل التراك {number} {label}").format(number=track_index(key) + 1, label=tr(track_label(key))),
            )
            item.Check(key in solo_tracks)
            frame.Bind(wx.EVT_MENU, lambda event, selected=key: target.OnToggleTrackSoloValue(selected), id=item.GetId())
        view_menu.AppendSeparator()
        speak_status_id = wx.NewIdRef()
        view_menu.Append(speak_status_id, tr("نطق حالة المحرر"))
        frame.Bind(wx.EVT_MENU, target.OnSpeakEditorStatus, id=speak_status_id)

    settings_menu = wx.Menu()
    settings_menu.Append(ids["program_settings"], tr("إعدادات البرنامج"))
    settings_menu.Append(
        ids["toggle_program_mode"],
        menu_label("التبديل بين الوضع العادي والاحترافي", "Shift+.", include_shortcuts),
    )

    tools_menu = wx.Menu()
    captions_menu = wx.Menu()
    captions_menu.Append(ids["captions_settings"], tr("إعدادات الميزة"))
    captions_menu.Append(ids["grok_keys_settings"], tr("إعدادات مفاتيح Groq API"))
    captions_menu.Append(ids["captions_start"], menu_label("بدء استخراج وتحرير النطق", "/", include_shortcuts))
    tools_menu.AppendSubMenu(captions_menu, tr("كتابة النطق على الشاشة"))
    tools_menu.AppendSeparator()
    tools_menu.Append(ids["record_audio"], menu_label("تسجيل الصوت", "Ctrl+F9", include_shortcuts))
    tools_menu.Append(ids["prepare_screen_recording"], menu_label("تسجيل الشروحات المصورة", "Ctrl+Shift+F9", include_shortcuts))
    tools_menu.Append(ids["start_screen_recording"], menu_label("بدء تسجيل الشاشة المجهز", "Ctrl+Alt+F9", include_shortcuts))
    tools_menu.AppendSeparator()
    tools_menu.Append(ids["pause_recording"], menu_label("إيقاف مؤقت أو استئناف التسجيل", "Ctrl+F7", include_shortcuts))
    tools_menu.Append(ids["stop_recording"], menu_label("إيقاف التسجيل", "Ctrl+F8", include_shortcuts))

    broadcast_menu = wx.Menu()
    broadcast_menu.Append(broadcast_start_id, menu_label("إعدادات وبدء البث...", "Shift+F12", include_shortcuts))
    broadcast_menu.Append(broadcast_stop_id, menu_label("إيقاف البث", "Ctrl+F12", include_shortcuts))

    language_menu = wx.Menu()
    language_items = [
        (ids["language_ar"], "ar", "العربية"),
        (ids["language_en"], "en", "الإنجليزية"),
        (ids["language_fr"], "fr", "الفرنسية"),
    ]
    current_language = get_language()
    for item_id, language_key, label in language_items:
        item = language_menu.AppendRadioItem(item_id, tr(label))
        item.Check(language_key == current_language)

    theme_menu = wx.Menu()
    theme_items = [
        (ids["theme_default"], "default"),
        (ids["theme_dark"], "dark"),
        (ids["theme_high_black"], "high_black"),
        (ids["theme_high_light"], "high_light"),
    ]
    current_theme = get_theme()
    for item_id, theme_key in theme_items:
        item = theme_menu.AppendRadioItem(item_id, tr(THEMES[theme_key]["name"]))
        item.Check(theme_key == current_theme)

    settings_menu.AppendSeparator()
    settings_menu.AppendSubMenu(language_menu, tr("اللغات"))
    settings_menu.AppendSubMenu(theme_menu, tr("مظهر البرنامج"))

    help_menu = wx.Menu()
    help_menu.Append(ids["facebook_contact"], tr("تواصل معي على فيس بوك لاقتراح ميزة أو الإبلاغ عن خطأ"))
    help_menu.Append(ids["telegram_contact"], tr("تواصل معي على تلجرام لاقتراح ميزة أو الإبلاغ عن خطأ"))
    help_menu.Append(ids["telegram_apps"], tr("تحميل المزيد من البرامج والتطبيقات الخاصة بي والانضمام لنا على تلجرام"))
    help_menu.Append(ids["open_source_contribution"], tr("المساهمة في تطوير هذا المشروع مفتوح المصدر"))
    help_menu.AppendSeparator()
    help_menu.Append(ids["keyboard_shortcuts_help"], tr("اختصارات لوحة المفاتيح"))
    problem_log_menu = wx.Menu()
    problem_log_menu.Append(ids["copy_problem_log"], tr("نسخ سجل الأخطاء"))
    problem_log_menu.Append(ids["export_problem_log"], tr("تصدير سجل الأخطاء كملف txt"))
    problem_log_menu.Append(ids["clear_problem_log"], tr("حذف سجل الأخطاء"))
    help_menu.AppendSubMenu(problem_log_menu, tr("سجل الأخطاء"))
    help_menu.Append(ids["check_updates"], menu_label("التحقق من وجود تحديثات", "Ctrl+U", include_shortcuts))
    help_menu.Append(ids["about"], tr("حول"))
    help_menu.Append(ids["change_application_name"], menu_label("تغيير اسم التطبيق", "Ctrl+Shift+F2", include_shortcuts))

    top_level_menus = [
        (file_menu, tr("ملف")),
        (merge_menu, tr("دمج")),
        (insert_menu, tr("إدراج")),
        (effects_menu, tr("المؤثرات")),
        (edit_menu, tr("التحرير")),
    ]
    if view_menu is not None:
        top_level_menus.append((view_menu, tr("مشاهدة")))
    top_level_menus.extend([
        (tools_menu, tr("أدوات")),
        (broadcast_menu, tr("البث المباشر")),
        (playback_menu, tr("التشغيل والتنقل")),
        (settings_menu, tr("الإعدادات")),
        (help_menu, tr("المساعدة")),
    ])
    menu_bar = frame.GetMenuBar()
    if menu_bar is None:
        menu_bar = wx.MenuBar()
        for menu, title in top_level_menus:
            menu_bar.Append(menu, title)
        frame.SetMenuBar(menu_bar)
    else:
        # Reuse the native menu bar when changing language. Replacing the whole
        # bar at runtime can leave Windows accessibility/menu state stale.
        while menu_bar.GetMenuCount():
            menu_bar.Remove(menu_bar.GetMenuCount() - 1)
        for menu, title in top_level_menus:
            menu_bar.Append(menu, title)

    frame.Bind(wx.EVT_MENU, target.OnOpen, id=wx.ID_OPEN)
    if hasattr(target, "OnStartBroadcast"):
        frame.Bind(wx.EVT_MENU, target.OnStartBroadcast, id=broadcast_start_id)
        frame.Bind(wx.EVT_MENU, target.OnStopBroadcast, id=broadcast_stop_id)
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnMuteToggleCurrentTrack", "OnAddVideo"), id=ids["add_video"])
    frame.Bind(wx.EVT_MENU, target.OnInsertTimelineAudio, id=ids["insert_timeline_audio"])
    frame.Bind(wx.EVT_MENU, target.OnInsertTimelineSilence, id=ids["insert_timeline_silence"])
    frame.Bind(wx.EVT_MENU, target.OnInsertImage, id=ids["insert_image"])
    frame.Bind(wx.EVT_MENU, target.OnInsertText, id=ids["insert_text"])
    frame.Bind(wx.EVT_MENU, target.OnReplaceChromaBackground, id=ids["replace_chroma_background"])
    frame.Bind(wx.EVT_MENU, target.OnAddWatermark, id=ids["add_watermark"])
    frame.Bind(wx.EVT_MENU, target.OnRemoveWatermark, id=ids["remove_watermark"])
    frame.Bind(wx.EVT_MENU, target.OnInsertBackgroundAudio, id=ids["insert_background_audio"])
    frame.Bind(wx.EVT_MENU, target.OnInsertWorkVideo, id=ids["insert_work_video"])
    frame.Bind(wx.EVT_MENU, target.OnChooseWorkImages, id=ids["choose_work_images"])
    frame.Bind(wx.EVT_MENU, target.OnChooseWorkVideos, id=ids["choose_work_videos"])
    frame.Bind(wx.EVT_MENU, target.OnDistributeWorkImages, id=ids["distribute_work_images"])
    frame.Bind(wx.EVT_MENU, target.OnDistributeWorkVideos, id=ids["distribute_work_videos"])
    frame.Bind(wx.EVT_MENU, target.OnSetImageDuration, id=ids["image_duration"])
    frame.Bind(wx.EVT_MENU, target.OnSetTransition, id=ids["transition"])
    frame.Bind(wx.EVT_MENU, target.OnElementManager, id=ids["element_manager"])
    frame.Bind(wx.EVT_MENU, target.OnStopAtInsertEdge, id=ids["stop_at_insert_edge"])
    frame.Bind(wx.EVT_MENU, target.OnNewProgramWindow, id=ids["new_program_window"])
    frame.Bind(wx.EVT_MENU, target.OnRenameProgramWindow, id=ids["rename_program_window"])
    frame.Bind(wx.EVT_MENU, target.OnNextProgramWindow, id=ids["next_program_window"])
    frame.Bind(wx.EVT_MENU, target.OnPreviousProgramWindow, id=ids["previous_program_window"])
    frame.Bind(wx.EVT_MENU, target.OnSaveVideo, id=ids["save"])
    frame.Bind(wx.EVT_MENU, target.OnSaveSelectedVideo, id=ids["save_selected"])
    frame.Bind(wx.EVT_MENU, target.OnSplitTimeline, id=ids["split_timeline"])
    frame.Bind(wx.EVT_MENU, target.OnExportVideoAudio, id=ids["export_video_audio"])
    frame.Bind(wx.EVT_MENU, target.OnImportVideoAudio, id=ids["import_video_audio"])
    frame.Bind(wx.EVT_MENU, target.OnSaveProject, id=save_project_id)
    frame.Bind(wx.EVT_MENU, target.OnRestoreProject, id=restore_project_id)
    frame.Bind(wx.EVT_MENU, target.OnSaveSession, id=ids["save_session"])
    frame.Bind(wx.EVT_MENU, target.OnRestoreSession, id=ids["restore_session"])
    frame.Bind(wx.EVT_MENU, target.OnRestoreCrashSession, id=ids["restore_crash_session"])
    frame.Bind(wx.EVT_MENU, target.OnProgramSettings, id=ids["program_settings"])
    frame.Bind(wx.EVT_MENU, target.OnGrokKeysSettings, id=ids["grok_keys_settings"])
    frame.Bind(wx.EVT_MENU, target.OnMetadata, id=ids["metadata"])
    frame.Bind(wx.EVT_MENU, target.OnClose, id=wx.ID_EXIT)
    frame.Bind(wx.EVT_MENU, target.OnPlayPause, id=ids["play_pause"])
    frame.Bind(wx.EVT_MENU, target.OnPlaySelectedRange, id=ids["play_selected_range"])
    frame.Bind(wx.EVT_MENU, target.OnPlayTimelineExceptSelection, id=ids["play_except_selection"])
    frame.Bind(wx.EVT_MENU, target.OnForward, id=wx.ID_FORWARD)
    frame.Bind(wx.EVT_MENU, target.OnRewind, id=wx.ID_BACKWARD)
    frame.Bind(wx.EVT_MENU, target.OnHome, id=ids["home"])
    frame.Bind(wx.EVT_MENU, target.OnEnd, id=ids["end_key"])
    frame.Bind(wx.EVT_MENU, target.OnPageUp, id=ids["page_up"])
    frame.Bind(wx.EVT_MENU, target.OnPageDown, id=ids["page_down"])
    frame.Bind(wx.EVT_MENU, target.OnFineRewind, id=ids["fine_rewind"])
    frame.Bind(wx.EVT_MENU, target.OnFineForward, id=ids["fine_forward"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnNextElementOnTrack", "OnNextEditPoint"), id=ids["next_edit_point"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnPreviousElementOnTrack", "OnPreviousEditPoint"), id=ids["previous_edit_point"])
    frame.Bind(wx.EVT_MENU, target.OnSpeakCurrentEditPoint, id=ids["speak_current_edit_point"])
    frame.Bind(wx.EVT_MENU, target.OnDeleteCurrentEditPoint, id=ids["delete_edit_point"])
    frame.Bind(wx.EVT_MENU, target.OnNextItemEdge, id=ids["next_item_edge"])
    frame.Bind(wx.EVT_MENU, target.OnPreviousItemEdge, id=ids["previous_item_edge"])
    frame.Bind(wx.EVT_MENU, target.OnNextTimelineFile, id=ids["next_timeline_file"])
    frame.Bind(wx.EVT_MENU, target.OnPreviousTimelineFile, id=ids["previous_timeline_file"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnExtendSelectionLeft", "OnNextBackgroundAudio"), id=ids["next_background_audio"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnExtendSelectionRight", "OnPreviousBackgroundAudio"), id=ids["previous_background_audio"])
    frame.Bind(wx.EVT_MENU, target.OnIncreaseCurrentBackgroundVolume, id=ids["increase_background_volume"])
    frame.Bind(wx.EVT_MENU, target.OnDecreaseCurrentBackgroundVolume, id=ids["decrease_background_volume"])
    frame.Bind(wx.EVT_MENU, target.OnDeleteCurrentBackgroundAudio, id=ids["delete_background_audio"])
    frame.Bind(wx.EVT_MENU, target.OnSpeakCurrentTime, id=ids["speak_current_time"])
    frame.Bind(wx.EVT_MENU, target.OnSpeakSelectionLength, id=ids["speak_selection_length"])
    frame.Bind(wx.EVT_MENU, target.OnSpeakCurrentItems, id=ids["speak_current_items"])
    frame.Bind(wx.EVT_MENU, target.OnSpeakEditPointCount, id=ids["speak_edit_point_count"])
    frame.Bind(wx.EVT_MENU, target.OnSetStart, id=ids["start"])
    frame.Bind(wx.EVT_MENU, target.OnSetEnd, id=ids["end"])
    frame.Bind(wx.EVT_MENU, target.OnChangeSpeed, id=ids["change_speed"])
    frame.Bind(wx.EVT_MENU, target.OnRotateVideo, id=ids["rotate_video"])
    frame.Bind(wx.EVT_MENU, target.OnMuteTimelineVideos, id=ids["mute_timeline_audio"])
    frame.Bind(wx.EVT_MENU, target.OnMuteOriginalAudio, id=ids["mute_original_audio"])
    frame.Bind(wx.EVT_MENU, target.OnCensorBleep, id=ids["censor_bleep"])
    frame.Bind(wx.EVT_MENU, target.OnRecordAudio, id=ids["record_audio"])
    frame.Bind(wx.EVT_MENU, target.OnPrepareScreenRecording, id=ids["prepare_screen_recording"])
    frame.Bind(wx.EVT_MENU, target.OnStartPreparedScreenRecording, id=ids["start_screen_recording"])
    frame.Bind(wx.EVT_MENU, target.OnPauseResumeRecording, id=ids["pause_recording"])
    frame.Bind(wx.EVT_MENU, target.OnStopRecording, id=ids["stop_recording"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnSelectAllTimelinePro", "OnSelectAllTimeline"), id=ids["select_all"])
    frame.Bind(wx.EVT_MENU, target.OnSelectFromStartToCurrent, id=ids["select_start_current"])
    frame.Bind(wx.EVT_MENU, target.OnSelectFromCurrentToEnd, id=ids["select_current_end"])
    frame.Bind(wx.EVT_MENU, target.OnDeleteElement, id=ids["delete"])
    frame.Bind(wx.EVT_MENU, target.OnDeleteCurrentTimelineFile, id=ids["delete_timeline_file"])
    frame.Bind(wx.EVT_MENU, target.OnRepeatSelection, id=ids["repeat_selection"])
    frame.Bind(wx.EVT_MENU, target.OnUndoEdit, id=ids["undo"])
    frame.Bind(wx.EVT_MENU, target.OnRestoreEdit, id=ids["restore_edit"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnCutElements", "OnCutSegment"), id=ids["cut"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnCopyElements", "OnCopySegment"), id=ids["copy"])
    frame.Bind(wx.EVT_MENU, lambda event: run_mode_shortcut(target, "OnPasteElements", "OnPasteSegment"), id=ids["paste"])
    frame.Bind(wx.EVT_MENU, target.OnMergeAudioWithImages, id=merge_audio_images_id)
    frame.Bind(wx.EVT_MENU, target.OnMergeAudioFiles, id=merge_audio_files_id)
    frame.Bind(wx.EVT_MENU, target.OnMergeAudioWithVideo, id=merge_audio_video_id)
    frame.Bind(wx.EVT_MENU, target.OnMergeVideoClips, id=merge_video_clips_id)
    frame.Bind(wx.EVT_MENU, target.OnVisualEffects, id=visual_effects_id)
    frame.Bind(wx.EVT_MENU, target.OnTransitionEffects, id=ids["transition_effects"])
    frame.Bind(wx.EVT_MENU, target.OnChooseAudioEffect, id=ids["choose_audio_effect"])
    frame.Bind(wx.EVT_MENU, target.OnIncreaseVolume, id=wx.ID_UP)
    frame.Bind(wx.EVT_MENU, target.OnIncreaseVolumeBoost, id=ids["increase_volume_boost"])
    frame.Bind(wx.EVT_MENU, target.OnDecreaseVolumeBoost, id=ids["decrease_volume_boost"])
    frame.Bind(wx.EVT_MENU, target.OnDecreaseVolume, id=wx.ID_DOWN)
    frame.Bind(wx.EVT_MENU, target.OnCtrl1, id=ids["ctrl1"])
    frame.Bind(wx.EVT_MENU, target.OnCtrl2, id=ids["ctrl2"])
    frame.Bind(wx.EVT_MENU, target.OnCtrl3, id=ids["ctrl3"])
    frame.Bind(wx.EVT_MENU, target.OnFacebookContact, id=ids["facebook_contact"])
    frame.Bind(wx.EVT_MENU, target.OnTelegramContact, id=ids["telegram_contact"])
    frame.Bind(wx.EVT_MENU, target.OnTelegramApps, id=ids["telegram_apps"])
    frame.Bind(wx.EVT_MENU, target.OnOpenSourceContribution, id=ids["open_source_contribution"])
    frame.Bind(wx.EVT_MENU, target.OnKeyboardShortcutsHelp, id=ids["keyboard_shortcuts_help"])
    frame.Bind(wx.EVT_MENU, target.OnCopyProblemLog, id=ids["copy_problem_log"])
    frame.Bind(wx.EVT_MENU, target.OnExportProblemLog, id=ids["export_problem_log"])
    frame.Bind(wx.EVT_MENU, target.OnClearProblemLog, id=ids["clear_problem_log"])
    frame.Bind(wx.EVT_MENU, target.OnCheckForUpdates, id=ids["check_updates"])
    frame.Bind(wx.EVT_MENU, target.OnAbout, id=ids["about"])
    frame.Bind(wx.EVT_MENU, target.OnChangeApplicationName, id=ids["change_application_name"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetLanguage("ar"), id=ids["language_ar"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetLanguage("en"), id=ids["language_en"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetLanguage("fr"), id=ids["language_fr"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetTheme("default"), id=ids["theme_default"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetTheme("dark"), id=ids["theme_dark"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetTheme("high_black"), id=ids["theme_high_black"])
    frame.Bind(wx.EVT_MENU, lambda event: target.OnSetTheme("high_light"), id=ids["theme_high_light"])
    frame.Refresh()
    frame.Update()
