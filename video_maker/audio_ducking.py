import os
import shutil
import tempfile
import threading
import time
import uuid

import wx

from video_maker.app_state import get_audio_effect_values, get_language, set_audio_effect_values
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.save_progress import SaveProgressDialog
from video_maker.timeline import slice_segments


AUDIO_DUCKING_KEY = "voice_over_ducking"
AUDIO_DUCKING_FIELD = "audio_ducking"


LOCALIZED_TEXTS = {
    "ar": {
        "name": "خفض الخلفية وقت الكلام",
        "description": "يخفض الخلفية الصوتية تلقائيا أثناء الكلام ثم يعيد رفعها وقت السكوت.",
        "amount": "مقدار خفض الخلفية أثناء الكلام",
        "threshold": "حساسية اكتشاف الكلام",
        "attack": "سرعة خفض الخلفية",
        "release": "سرعة رجوع الخلفية",
        "main_volume": "مستوى صوت الفيديو أو الصوت الأصلي",
        "main_video_volume": "مستوى صوت الفيديو",
        "main_audio_volume": "مستوى صوت الملف",
        "preset_default": "افتراضي",
        "preset_soft": "هادئ",
        "preset_strong": "واضح",
        "ready": "جاهز",
        "preview": "تشغيل المعاينة",
        "rewind": "ترجيع معاينة خفض الخلفية",
        "forward": "تقديم معاينة خفض الخلفية",
        "pause": "إيقاف مؤقت لمعاينة خفض الخلفية",
        "stop": "إيقاف معاينة خفض الخلفية",
        "reset": "إرجاع إعدادات خفض الخلفية إلى الافتراضي",
        "apply": "تطبيق خفض الخلفية على التحديد",
        "cancel": "إلغاء",
        "play": "تشغيل",
        "rewind_button": "ترجيع",
        "forward_button": "تقديم",
        "pause_button": "إيقاف مؤقت",
        "stop_button": "إيقاف",
        "reset_button": "الافتراضي",
        "apply_button": "تطبيق",
        "no_background_added": "لا توجد خلفية صوتية",
        "no_background": "لا توجد خلفية صوتية داخل التحديد",
        "applied": "تم تطبيق خفض الخلفية وقت الكلام",
        "rendering": "جاري تجهيز المعاينة",
        "render_failed": "تعذر تجهيز معاينة خفض الخلفية",
        "preview_failed": "تعذر تشغيل معاينة خفض الخلفية",
        "cancel_preview": "إلغاء تجهيز معاينة خفض الخلفية",
        "progress": "نسبة تجهيز معاينة خفض الخلفية {percent} بالمئة",
        "status": "حالة تجهيز معاينة خفض الخلفية",
        "gauge": "شريط تقدم تجهيز معاينة خفض الخلفية",
        "db": "ديسيبل",
        "ms": "مللي ثانية",
        "percent": "بالمئة",
    },
    "en": {
        "name": "Voice-over ducking",
        "description": "Automatically lowers background audio while speech is present, then raises it during silence.",
        "amount": "Background reduction while speech is present",
        "threshold": "Speech detection sensitivity",
        "attack": "Background lowering speed",
        "release": "Background return speed",
        "main_volume": "Original video or audio volume",
        "main_video_volume": "Original video volume",
        "main_audio_volume": "Audio file volume",
        "preset_default": "Default",
        "preset_soft": "Gentle",
        "preset_strong": "Clear",
        "ready": "Ready",
        "preview": "Play ducking preview",
        "rewind": "Rewind ducking preview",
        "forward": "Forward ducking preview",
        "pause": "Pause ducking preview",
        "stop": "Stop ducking preview",
        "reset": "Reset ducking settings to defaults",
        "apply": "Apply ducking to the selection",
        "cancel": "Cancel",
        "play": "Play",
        "rewind_button": "Rewind",
        "forward_button": "Forward",
        "pause_button": "Pause",
        "stop_button": "Stop",
        "reset_button": "Default",
        "apply_button": "Apply",
        "no_background_added": "No background audio has been added",
        "no_background": "No background audio in the selection",
        "applied": "Voice-over ducking applied",
        "rendering": "Preparing preview",
        "render_failed": "Could not prepare the ducking preview",
        "preview_failed": "Could not play the ducking preview",
        "cancel_preview": "Cancel ducking preview preparation",
        "progress": "Ducking preview preparation progress {percent} percent",
        "status": "Ducking preview preparation status",
        "gauge": "Ducking preview preparation progress bar",
        "db": "decibels",
        "ms": "milliseconds",
        "percent": "percent",
    },
    "fr": {
        "name": "Ducking de voix off",
        "description": "Baisse automatiquement l'audio de fond pendant la parole, puis le remonte pendant les silences.",
        "amount": "Réduction du fond pendant la parole",
        "threshold": "Sensibilité de détection de la parole",
        "attack": "Vitesse de baisse du fond",
        "release": "Vitesse de remontée du fond",
        "main_volume": "Volume de la vidéo ou de l'audio d'origine",
        "main_video_volume": "Volume de la vidéo",
        "main_audio_volume": "Volume du fichier audio",
        "preset_default": "Par défaut",
        "preset_soft": "Doux",
        "preset_strong": "Clair",
        "ready": "Prêt",
        "preview": "Lire l'aperçu du ducking",
        "rewind": "Reculer dans l'aperçu du ducking",
        "forward": "Avancer dans l'aperçu du ducking",
        "pause": "Mettre en pause l'aperçu du ducking",
        "stop": "Arrêter l'aperçu du ducking",
        "reset": "Rétablir les paramètres de ducking par défaut",
        "apply": "Appliquer le ducking à la sélection",
        "cancel": "Annuler",
        "play": "Lire",
        "rewind_button": "Reculer",
        "forward_button": "Avancer",
        "pause_button": "Pause",
        "stop_button": "Arrêter",
        "reset_button": "Par défaut",
        "apply_button": "Appliquer",
        "no_background_added": "Aucun audio de fond n'a été ajouté",
        "no_background": "Aucun audio de fond dans la sélection",
        "applied": "Ducking de voix off appliqué",
        "rendering": "Préparation de l'aperçu",
        "render_failed": "Impossible de préparer l'aperçu du ducking",
        "preview_failed": "Impossible de lire l'aperçu du ducking",
        "cancel_preview": "Annuler la préparation de l'aperçu du ducking",
        "progress": "Progression de la préparation de l'aperçu du ducking : {percent} pour cent",
        "status": "État de préparation de l'aperçu du ducking",
        "gauge": "Barre de progression de préparation de l'aperçu du ducking",
        "db": "décibels",
        "ms": "millisecondes",
        "percent": "pour cent",
    },
}


DEFAULT_DUCKING_VALUES = {
    "reduction_db": 18,
    "threshold_db": -32,
    "attack_ms": 20,
    "release_ms": 450,
    "main_volume_percent": 100,
}


def texts():
    return LOCALIZED_TEXTS.get(get_language(), LOCALIZED_TEXTS["ar"])


def audio_ducking_effect_definition():
    t = texts()
    return {
        "key": AUDIO_DUCKING_KEY,
        "name": t["name"],
        "description": t["description"],
        "special_action": AUDIO_DUCKING_KEY,
        "controls": [
            {"key": "reduction_db", "name": t["amount"], "min": 0, "max": 36, "default": 18, "unit": t["db"], "step": 1, "page_step": 3},
            {"key": "threshold_db", "name": t["threshold"], "min": -60, "max": -12, "default": -32, "unit": t["db"], "step": 1, "page_step": 4},
            {"key": "attack_ms", "name": t["attack"], "min": 5, "max": 200, "default": 20, "unit": t["ms"], "step": 5, "page_step": 20},
            {"key": "release_ms", "name": t["release"], "min": 50, "max": 2000, "default": 450, "unit": t["ms"], "step": 25, "page_step": 100},
            {"key": "main_volume_percent", "name": t["main_volume"], "min": 0, "max": 100, "default": 100, "unit": t["percent"], "step": 5, "page_step": 10, "home_value": 100, "end_value": 0, "tick": 5},
        ],
        "presets": [
            {"name": t["preset_default"], "values": dict(DEFAULT_DUCKING_VALUES)},
            {"name": t["preset_soft"], "values": {"reduction_db": 10, "threshold_db": -36, "attack_ms": 35, "release_ms": 650, "main_volume_percent": 100}},
            {"name": t["preset_strong"], "values": {"reduction_db": 26, "threshold_db": -38, "attack_ms": 12, "release_ms": 350, "main_volume_percent": 100}},
        ],
    }


def _clamp_number(values, key, default, minimum, maximum):
    try:
        value = float(values.get(key, default))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def normalize_ducking_settings(values):
    values = values if isinstance(values, dict) else {}
    return {
        "enabled": True,
        "reduction_db": _clamp_number(values, "reduction_db", DEFAULT_DUCKING_VALUES["reduction_db"], 0, 36),
        "threshold_db": _clamp_number(values, "threshold_db", DEFAULT_DUCKING_VALUES["threshold_db"], -60, -12),
        "attack_ms": _clamp_number(values, "attack_ms", DEFAULT_DUCKING_VALUES["attack_ms"], 5, 200),
        "release_ms": _clamp_number(values, "release_ms", DEFAULT_DUCKING_VALUES["release_ms"], 50, 2000),
        "main_volume_percent": _clamp_number(values, "main_volume_percent", DEFAULT_DUCKING_VALUES["main_volume_percent"], 0, 100),
    }


def has_audio_ducking(item):
    if not isinstance(item, dict):
        return False
    settings = item.get(AUDIO_DUCKING_FIELD)
    if not isinstance(settings, dict) or not settings.get("enabled", True):
        return False
    try:
        return float(settings.get("reduction_db", 0) or 0) > 0.05
    except (TypeError, ValueError):
        return False


def sidechain_threshold_linear(threshold_db):
    return max(0.0001, min(1.0, 10.0 ** (float(threshold_db) / 20.0)))


def ducking_ratio(reduction_db):
    return max(1.0, min(6.0, 1.0 + float(reduction_db) / 12.0))


def audio_ducking_filter_chain(background_label, sidechain_label, settings, output_label):
    settings = normalize_ducking_settings(settings)
    threshold = sidechain_threshold_linear(settings["threshold_db"])
    ratio = ducking_ratio(settings["reduction_db"])
    attack = max(1.0, float(settings["attack_ms"]))
    release = max(1.0, float(settings["release_ms"]))
    floor_gain = max(0.0, min(1.0, (10.0 ** (-float(settings["reduction_db"]) / 20.0)) * 1.6))
    label_base = output_label.strip("[]") or "ducked"
    return (
        f"{background_label}asplit[{label_base}_proc][{label_base}_floor];"
        f"[{label_base}_proc]{sidechain_label}"
        f"sidechaincompress=threshold={threshold:.6f}:ratio={ratio:.3f}:attack={attack:.1f}:release={release:.1f}:makeup=1"
        f"[{label_base}_duck];"
        f"[{label_base}_floor]volume={floor_gain:.6f}[{label_base}_bed];"
        f"[{label_base}_duck][{label_base}_bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.98"
        f"{output_label};"
    )


def timed_items_for_range(items, start_time, end_time):
    start_time = max(0.0, float(start_time))
    end_time = max(start_time, float(end_time))
    result = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        item_start = float(item.get("start", 0.0) or 0.0)
        item_end = float(item.get("end", item_start) or item_start)
        if item_end <= start_time or item_start >= end_time:
            continue
        speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        overlap_start = max(item_start, start_time)
        overlap_end = min(item_end, end_time)
        adjusted = dict(item)
        adjusted["start"] = overlap_start - start_time
        adjusted["end"] = overlap_end - start_time
        adjusted["source_offset"] = source_offset + max(0.0, overlap_start - item_start) * speed
        result.append(adjusted)
    return result


def main_volume_multiplier(values):
    settings = normalize_ducking_settings(values)
    return max(0.0, min(1.0, float(settings.get("main_volume_percent", 100.0)) / 100.0))


def build_live_ducking_preview_args(timeline, background_audio_items, sound_effects_items, duration, temp_dir, muted_tracks=None, solo_tracks=None, main_volume_percent=100):
    from video_maker.track_items import is_track_audible
    from video_maker.tracks import BACKGROUND_AUDIO_TRACK, MAIN_VIDEO_TRACK, SOUND_EFFECTS_TRACK
    from video_maker.video_editing import (
        _timed_audio_mix_items,
        exact_timeline_audio_chain,
        has_audio_stream,
        segment_audio_path,
        segment_audio_start,
        segment_audio_volume,
        segment_speed,
    )

    muted = set(muted_tracks or ())
    solo = set(solo_tracks or ())
    duration = max(0.001, float(duration or 0.001))
    main_audio_muted = not is_track_audible(MAIN_VIDEO_TRACK, muted, solo)
    if not is_track_audible(BACKGROUND_AUDIO_TRACK, muted, solo):
        background_audio_items = []
    if not is_track_audible(SOUND_EFFECTS_TRACK, muted, solo):
        sound_effects_items = []
    main_volume = max(0.0, min(1.0, float(main_volume_percent if main_volume_percent is not None else 100.0) / 100.0))

    inputs = []
    filters = []
    audio_outputs = []
    input_index = 0

    for segment in timeline or []:
        speed = segment_speed(segment)
        source_duration = max(0.001, float(segment.end) - float(segment.start))
        output_duration = max(0.001, source_duration / speed)
        audio_label = f"[a{len(audio_outputs)}]"
        audio_path = segment_audio_path(segment) or segment.path
        audio_start = segment_audio_start(segment)
        has_audio = False
        try:
            has_audio = bool(audio_path and os.path.exists(audio_path) and has_audio_stream(audio_path))
        except Exception:
            has_audio = False
        if main_audio_muted or not has_audio:
            filters.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={output_duration:.6f},asetpts=N/SR/TB{audio_label};")
        else:
            inputs.extend(["-ss", f"{audio_start:.6f}", "-t", f"{source_duration:.6f}", "-i", audio_path])
            idx = input_index
            input_index += 1
            volume = 0.0 if main_audio_muted else segment_audio_volume(segment) * main_volume
            filters.append(exact_timeline_audio_chain(f"[{idx}:a]", source_duration, output_duration, speed, volume) + f"{audio_label};")
        audio_outputs.append(audio_label)

    if len(audio_outputs) > 1:
        filters.append("".join(audio_outputs) + f"concat=n={len(audio_outputs)}:v=0:a=1[base_a];")
        current_a = "[base_a]"
    elif audio_outputs:
        filters.append(f"{audio_outputs[0]}acopy[base_a];")
        current_a = "[base_a]"
    else:
        current_a = ""

    if background_audio_items:
        ducking_sidechains = []
        if current_a:
            ducking_count = 0
            for item in background_audio_items or []:
                item_path = str(item.get("path", "") or "")
                if not has_audio_ducking(item) or not item_path or not os.path.exists(item_path):
                    continue
                item_start = float(item.get("start", 0) or 0)
                item_end = float(item.get("end", item_start) or item_start)
                if item_end > item_start:
                    ducking_count += 1
            if ducking_count:
                sidechain_labels = "".join(f"[bg_duck_sc{i}]" for i in range(ducking_count))
                filters.append(f"{current_a}asplit={ducking_count + 1}[bg_mix_base]{sidechain_labels};")
                amix_inputs = ["[bg_mix_base]"]
                ducking_sidechains = [f"[bg_duck_sc{i}]" for i in range(ducking_count)]
            else:
                amix_inputs = [current_a]
        else:
            amix_inputs = []
        input_index = _timed_audio_mix_items(
            inputs,
            filters,
            amix_inputs,
            background_audio_items,
            {},
            input_index,
            duration,
            ducking_sidechains=ducking_sidechains,
        )
        if amix_inputs:
            filters.append("".join(amix_inputs) + f"amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[mixed_a];")
            current_a = "[mixed_a]"

    if sound_effects_items:
        amix_inputs = [current_a] if current_a else []
        input_index = _timed_audio_mix_items(inputs, filters, amix_inputs, sound_effects_items, {}, input_index, duration)
        if amix_inputs:
            filters.append("".join(amix_inputs) + f"amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[sfx_mixed];")
            current_a = "[sfx_mixed]"

    if current_a:
        filters.append(f"{current_a}apad,atrim=duration={duration:.6f},asetpts=N/SR/TB[final_a];")
    else:
        filters.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={duration:.6f},asetpts=N/SR/TB[final_a];")

    os.makedirs(temp_dir, exist_ok=True)
    script_path = os.path.join(temp_dir, f"ducking_live_{uuid.uuid4().hex}.txt")
    with open(script_path, "w", encoding="utf-8") as script:
        script.write("".join(filters))
    return inputs + ["-filter_complex_script", script_path, "-map", "[final_a]", "-vn"]


def apply_main_volume_to_timeline(timeline, start_time, end_time, values):
    from video_maker.timeline import TimelineSegment
    from video_maker.video_editing import segment_audio_volume, segment_speed

    multiplier = main_volume_multiplier(values)
    if abs(multiplier - 1.0) <= 0.0005:
        return list(timeline or []), 0
    start_time = max(0.0, float(start_time))
    end_time = max(start_time, float(end_time))
    position = 0.0
    updated = []
    changed = 0
    for segment in timeline or []:
        speed = segment_speed(segment)
        segment_duration = max(0.0, float(getattr(segment, "duration", 0.0) or 0.0))
        segment_end_time = position + segment_duration
        overlap_start = max(position, start_time)
        overlap_end = min(segment_end_time, end_time)
        if overlap_end <= overlap_start:
            updated.append(segment)
            position = segment_end_time
            continue

        pieces = []
        if position < overlap_start:
            pieces.append((position, overlap_start, False))
        pieces.append((overlap_start, overlap_end, True))
        if overlap_end < segment_end_time:
            pieces.append((overlap_end, segment_end_time, False))

        original_start = float(getattr(segment, "start", 0.0) or 0.0)
        original_audio_start = getattr(segment, "audio_start", None)
        for piece_start, piece_end, adjusted in pieces:
            if piece_end <= piece_start:
                continue
            local_start = original_start + max(0.0, piece_start - position) * speed
            local_end = original_start + max(0.0, piece_end - position) * speed
            audio_start = original_audio_start
            if audio_start is not None:
                audio_start = float(audio_start) + max(0.0, local_start - original_start)
            audio_volume = segment_audio_volume(segment) * (multiplier if adjusted else 1.0)
            updated.append(TimelineSegment(
                segment.path,
                local_start,
                local_end,
                speed,
                audio_volume,
                str(getattr(segment, "audio_path", "") or ""),
                audio_start,
                str(getattr(segment, "navigation_group", "") or ""),
                str(getattr(segment, "source_file_id", "") or ""),
                str(getattr(segment, "source_file_name", "") or ""),
                str(getattr(segment, "transition", "") or ""),
                max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            ))
            if adjusted:
                changed += 1
        position = segment_end_time
    return updated, changed


def _with_piece_identity(piece, original, split_count):
    if split_count <= 1:
        return piece
    original_id = str(original.get("source_id") or original.get("id") or "")
    if original_id:
        piece["source_id"] = original_id
    piece["id"] = uuid.uuid4().hex
    return piece


def apply_ducking_to_background_items(items, start_time, end_time, values):
    settings = normalize_ducking_settings(values)
    start_time = max(0.0, float(start_time))
    end_time = max(start_time, float(end_time))
    updated = []
    changed = 0
    for item in items or ():
        if not isinstance(item, dict):
            updated.append(item)
            continue
        item_start = float(item.get("start", 0.0) or 0.0)
        item_end = float(item.get("end", item_start) or item_start)
        overlap_start = max(item_start, start_time)
        overlap_end = min(item_end, end_time)
        if overlap_end <= overlap_start:
            updated.append(dict(item))
            continue

        speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        spans = []
        if item_start < overlap_start:
            spans.append((item_start, overlap_start, False))
        spans.append((overlap_start, overlap_end, True))
        if overlap_end < item_end:
            spans.append((overlap_end, item_end, False))

        for piece_start, piece_end, ducked in spans:
            if piece_end <= piece_start:
                continue
            piece = dict(item)
            piece["start"] = piece_start
            piece["end"] = piece_end
            piece["source_offset"] = source_offset + max(0.0, piece_start - item_start) * speed
            if ducked:
                piece[AUDIO_DUCKING_FIELD] = dict(settings)
                changed += 1
            updated.append(_with_piece_identity(piece, item, len(spans)))
    return updated, changed


class AudioDuckingDialog(wx.Dialog):
    def __init__(self, parent):
        self.text = texts()
        super().__init__(parent, title=self.text["name"], size=(760, 440))
        self.parent = parent
        self.effect_definition = audio_ducking_effect_definition()
        self.controls = {}
        self.tab_order = []
        self.last_focus_control = None
        self.preview_temp_dir = ""
        self.preview_path = ""
        self.preview_duration = 0.0
        self.preview_generation = 0
        self.preview_cancelled = False
        self.preview_rendering = False
        self.preview_render_pending = False
        self.preview_render_timer = None
        self.preview_play_after_render = False
        self.preview_source_key = None
        self.preview_progress_dialog = None
        from video_maker.audio_effects import RealtimeAudioPreview

        self.preview_player = RealtimeAudioPreview(self.update_status_text)
        self.saved_values = get_audio_effect_values(AUDIO_DUCKING_KEY)
        self._build()

    def _build(self):
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        presets = self.effect_definition["presets"]
        preset_sizer = wx.BoxSizer(wx.HORIZONTAL)
        preset_label = wx.StaticText(panel, label=tr("الإعداد"))
        self.preset_choice = wx.Choice(panel, choices=[preset["name"] for preset in presets])
        self.preset_choice.SetSelection(0)
        self.preset_choice.SetName(tr("الإعداد الجاهز"))
        preset_sizer.Add(preset_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        preset_sizer.Add(self.preset_choice, proportion=1, flag=wx.EXPAND)
        self.tab_order.append(self.preset_choice)

        controls_sizer = wx.BoxSizer(wx.VERTICAL)
        for control in self.effect_definition["controls"]:
            row = wx.BoxSizer(wx.HORIZONTAL)
            control_name = self.control_display_name(control)
            label = wx.StaticText(panel, label=control_name)
            value = int(self.saved_values.get(control["key"], control["default"]))
            value = max(control["min"], min(control["max"], value))
            slider = wx.Slider(panel, value=value, minValue=control["min"], maxValue=control["max"], style=wx.SL_HORIZONTAL | wx.WANTS_CHARS)
            slider.SetLineSize(max(1, int(control.get("step", 1))))
            slider.SetPageSize(max(1, int(control.get("page_step", 10))))
            slider.SetTickFreq(max(1, int(control.get("tick", 1))))
            row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
            row.Add(slider, proportion=1, flag=wx.EXPAND)
            controls_sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
            self.controls[control["key"]] = {
                "slider": slider,
                "name": control_name,
                "type": "slider",
                "unit": control["unit"],
                "control": control,
                "line_step": max(1, int(control.get("step", 1))),
                "page_step": max(1, int(control.get("page_step", 10))),
                "home_value": control.get("home_value"),
                "end_value": control.get("end_value"),
                "last_value": value,
                "ignore_scroll_until": 0,
            }
            self.tab_order.append(slider)
            slider.Bind(wx.EVT_SCROLL_THUMBTRACK, self.make_slider_handler(control["key"]))
            slider.Bind(wx.EVT_SCROLL_CHANGED, self.make_slider_handler(control["key"]))
            slider.Bind(wx.EVT_SCROLL_LINEUP, self.make_slider_scroll_handler(control["key"], 1))
            slider.Bind(wx.EVT_SCROLL_LINEDOWN, self.make_slider_scroll_handler(control["key"], -1))
            slider.Bind(wx.EVT_SCROLL_PAGEUP, self.make_slider_scroll_handler(control["key"], 10))
            slider.Bind(wx.EVT_SCROLL_PAGEDOWN, self.make_slider_scroll_handler(control["key"], -10))
            slider_key_handler = self.make_slider_key_handler(control["key"])
            slider.Bind(wx.EVT_CHAR_HOOK, slider_key_handler)
            slider.Bind(wx.EVT_CHAR, slider_key_handler)
            slider.Bind(wx.EVT_KEY_DOWN, slider_key_handler)
            slider.Bind(wx.EVT_SET_FOCUS, self.on_slider_focus)

        self.status = wx.StaticText(panel, label=self.text["ready"])
        self.gauge = wx.Gauge(panel, range=100)
        self.status.SetName(self.text["status"])
        self.gauge.SetName(self.text["gauge"])
        self.gauge.SetCanFocus(False)

        play_button = wx.Button(panel, label=self.text["play"])
        rewind_button = wx.Button(panel, label=self.text["rewind_button"])
        forward_button = wx.Button(panel, label=self.text["forward_button"])
        pause_button = wx.Button(panel, label=self.text["pause_button"])
        stop_button = wx.Button(panel, label=self.text["stop_button"])
        reset_button = wx.Button(panel, label=self.text["reset_button"])
        apply_button = wx.Button(panel, label=self.text["apply_button"])
        cancel_button = wx.Button(panel, label=self.text["cancel"])
        play_button.SetName(self.text["preview"])
        rewind_button.SetName(self.text["rewind"])
        forward_button.SetName(self.text["forward"])
        pause_button.SetName(self.text["pause"])
        stop_button.SetName(self.text["stop"])
        for navigation_button in (play_button, rewind_button, forward_button, pause_button, stop_button):
            navigation_button.SetCanFocus(False)
        reset_button.SetName(self.text["reset"])
        apply_button.SetName(self.text["apply"])
        cancel_button.SetName(self.text["cancel"])
        apply_button.SetDefault()
        self.tab_order.extend([reset_button, apply_button, cancel_button])

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for button in (play_button, rewind_button, forward_button, pause_button, stop_button, reset_button, apply_button, cancel_button):
            button_sizer.Add(button, flag=wx.ALL, border=6)

        main_sizer.Add(preset_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(controls_sizer, flag=wx.EXPAND)
        main_sizer.Add(self.status, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(self.gauge, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=6)
        panel.SetSizer(main_sizer)

        self.preset_choice.Bind(wx.EVT_CHOICE, self.on_preset_changed)
        play_button.Bind(wx.EVT_BUTTON, self.play_preview)
        rewind_button.Bind(wx.EVT_BUTTON, self.rewind_preview)
        forward_button.Bind(wx.EVT_BUTTON, self.forward_preview)
        pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        reset_button.Bind(wx.EVT_BUTTON, self.reset_defaults)
        apply_button.Bind(wx.EVT_BUTTON, self.apply_effect)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.close_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Slider, wx.Choice))

        self.update_all_slider_names()
        self.Centre()
        wx.CallAfter(self.focus_main_control)

    def focus_main_control(self):
        if self.tab_order:
            self.last_focus_control = self.tab_order[0]
            self.tab_order[0].SetFocus()

    def control_display_name(self, control):
        if control.get("key") != "main_volume_percent":
            return control["name"]
        if getattr(self.parent, "media_kind", "") == "video":
            return self.text["main_video_volume"]
        if getattr(self.parent, "media_kind", "") == "audio":
            return self.text["main_audio_volume"]
        return control["name"]

    def values(self):
        return normalize_ducking_settings({key: widgets["slider"].GetValue() for key, widgets in self.controls.items()})

    def selected_range(self):
        selected = self.parent.selected_effect_range()
        if not selected:
            return None
        return selected

    def remember_focus(self):
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            self.last_focus_control = focused

    def notify_accessibility(self, window, event_type=wx.ACC_EVENT_OBJECT_VALUECHANGE):
        if not wx.USE_ACCESSIBILITY:
            return
        try:
            wx.Accessible.NotifyEvent(event_type, window, wx.OBJID_CLIENT, wx.ACC_SELF)
        except Exception:
            pass

    def update_progress(self, value, message):
        value = max(0, min(100, int(value)))
        self.gauge.SetValue(value)
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.gauge.SetName(message)
        self.notify_accessibility(self.status, wx.ACC_EVENT_OBJECT_NAMECHANGE)
        self.notify_accessibility(self.gauge, wx.ACC_EVENT_OBJECT_VALUECHANGE)

    def update_status_text(self, message):
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.notify_accessibility(self.status, wx.ACC_EVENT_OBJECT_NAMECHANGE)

    def slider_value_text(self, key):
        widgets = self.controls[key]
        value = widgets["slider"].GetValue()
        return f"{widgets['name']} {value} {widgets['unit']}"

    def update_slider_name(self, key):
        widgets = self.controls[key]
        widgets["slider"].SetName(self.slider_value_text(key))

    def update_all_slider_names(self):
        for key in self.controls:
            self.update_slider_name(key)

    def set_control_value(self, key, value):
        widgets = self.controls.get(key)
        if not widgets:
            return
        slider = widgets["slider"]
        value = max(slider.GetMin(), min(slider.GetMax(), int(value)))
        slider.SetValue(value)
        widgets["last_value"] = value
        self.notify_accessibility(slider)

    def make_slider_handler(self, key):
        def handler(event=None):
            self.on_setting_changed(key)
        return handler

    def make_slider_key_handler(self, key):
        def handler(event):
            key_code = event.GetKeyCode()
            target_value = self.slider_key_target(key_code, self.controls.get(key))
            if target_value is not None:
                if self.set_slider_by_key(key, target_value):
                    self.controls[key]["ignore_scroll_until"] = time.monotonic() + 0.08
                return
            delta = self.slider_key_delta(key_code, self.controls.get(key))
            if delta is None:
                event.Skip()
                return
            if self.adjust_slider_by_key(key, delta):
                self.controls[key]["ignore_scroll_until"] = time.monotonic() + 0.08
        return handler

    def make_slider_scroll_handler(self, key, delta):
        def handler(event):
            self.adjust_slider_from_scroll(key, delta, event)
        return handler

    def on_slider_focus(self, event):
        self.last_focus_control = event.GetEventObject()
        event.Skip()

    def slider_key_delta(self, key, widgets=None):
        line_step = max(1, int((widgets or {}).get("line_step", 1)))
        page_step = max(1, int((widgets or {}).get("page_step", 10)))
        if key in (wx.WXK_UP, wx.WXK_RIGHT, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_RIGHT):
            return line_step
        if key in (wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_NUMPAD_DOWN, wx.WXK_NUMPAD_LEFT):
            return -line_step
        if key in (wx.WXK_PAGEUP, wx.WXK_NUMPAD_PAGEUP):
            return page_step
        if key in (wx.WXK_PAGEDOWN, wx.WXK_NUMPAD_PAGEDOWN):
            return -page_step
        return None

    def slider_key_target(self, key, widgets=None):
        widgets = widgets or {}
        if key in (wx.WXK_HOME, wx.WXK_NUMPAD_HOME):
            return widgets.get("home_value")
        if key in (wx.WXK_END, wx.WXK_NUMPAD_END):
            return widgets.get("end_value")
        return None

    def slider_for_event(self, event=None):
        event_object = event.GetEventObject() if event else None
        current_target_getter = getattr(event, "GetCurrentTarget", None) if event else None
        current_target = current_target_getter() if current_target_getter else None
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            for widgets in self.controls.values():
                if focused is widgets["slider"]:
                    break
            else:
                return None, None
        for key, widgets in self.controls.items():
            slider = widgets["slider"]
            if event_object is slider or current_target is slider or focused is slider or self.last_focus_control is slider:
                return key, slider
        return None, None

    def adjust_focused_slider(self, direction, event=None):
        key, slider = self.slider_for_event(event)
        if not slider:
            return False
        return self.adjust_slider_by_key(key, direction)

    def adjust_slider_by_key(self, key, direction):
        widgets = self.controls.get(key)
        if not widgets:
            return False
        slider = widgets["slider"]
        value = max(slider.GetMin(), min(slider.GetMax(), slider.GetValue() + direction))
        if value == slider.GetValue():
            return True
        slider.SetValue(value)
        widgets["last_value"] = value
        self.last_focus_control = slider
        self.on_setting_changed(key)
        self.notify_accessibility(slider)
        return True

    def set_slider_by_key(self, key, value):
        widgets = self.controls.get(key)
        if not widgets or value is None:
            return False
        slider = widgets["slider"]
        value = max(slider.GetMin(), min(slider.GetMax(), int(value)))
        if value == slider.GetValue():
            return True
        slider.SetValue(value)
        widgets["last_value"] = value
        self.last_focus_control = slider
        self.on_setting_changed(key)
        self.notify_accessibility(slider)
        return True

    def adjust_slider_from_scroll(self, key, direction, event=None):
        widgets = self.controls.get(key)
        if not widgets:
            return False
        slider = widgets["slider"]
        if time.monotonic() < widgets.get("ignore_scroll_until", 0):
            slider.SetValue(int(widgets.get("last_value", slider.GetValue())))
            return True
        base_value = int(widgets.get("last_value", slider.GetValue()))
        if event:
            event_type = event.GetEventType()
            if event_type in (wx.EVT_SCROLL_PAGEUP.typeId, wx.EVT_SCROLL_PAGEDOWN.typeId):
                page_step = max(1, int(widgets.get("page_step", 10)))
                direction = page_step if direction > 0 else -page_step
            elif event_type in (wx.EVT_SCROLL_LINEUP.typeId, wx.EVT_SCROLL_LINEDOWN.typeId):
                line_step = max(1, int(widgets.get("line_step", 1)))
                direction = line_step if direction > 0 else -line_step
        value = max(slider.GetMin(), min(slider.GetMax(), base_value + direction))
        slider.SetValue(value)
        widgets["last_value"] = value
        self.last_focus_control = slider
        self.on_setting_changed(key)
        self.notify_accessibility(slider)
        return True

    def on_slider_key(self, event):
        key, _slider = self.slider_for_event(event)
        target_value = self.slider_key_target(event.GetKeyCode(), self.controls.get(key) if key else None)
        if target_value is not None:
            if self.set_slider_by_key(key, target_value):
                return
            event.Skip()
            return
        delta = self.slider_key_delta(event.GetKeyCode(), self.controls.get(key) if key else None)
        if delta is None or not self.adjust_focused_slider(delta, event):
            event.Skip()

    def on_setting_changed(self, key_or_event=None):
        key = key_or_event if isinstance(key_or_event, str) else None
        if key is None and key_or_event is not None:
            key, _slider = self.slider_for_event(key_or_event)
        was_playing = self.preview_player.is_playing or self.preview_player.play_requested
        offset = self.preview_player.current_offset() if was_playing else 0
        if not was_playing:
            self.preview_player.reset(False, wait=True)
            self.cleanup_preview_file()
        self.update_all_slider_names()
        focused = wx.Window.FindFocus()
        if focused:
            self.last_focus_control = focused
        if key and key in self.controls:
            self.controls[key]["last_value"] = self.controls[key]["slider"].GetValue()
            self.update_status_text(self.slider_value_text(key))
        if was_playing:
            self.start_preview_audio(offset)
        if key_or_event is not None and not isinstance(key_or_event, str):
            key_or_event.Skip()

    def on_preset_changed(self, event=None):
        selection = self.preset_choice.GetSelection()
        presets = self.effect_definition["presets"]
        if 0 <= selection < len(presets):
            values = presets[selection]["values"]
            for key, widgets in self.controls.items():
                if key in values:
                    self.set_control_value(key, values[key])
        self.update_status_text(f"{tr('الإعداد')} {presets[selection]['name']}")
        self.on_setting_changed()

    def reset_defaults(self, event=None):
        for key, widgets in self.controls.items():
            self.set_control_value(key, DEFAULT_DUCKING_VALUES[key])
        if self.preset_choice:
            self.preset_choice.SetSelection(0)
        self.update_status_text(tr("تم إرجاع الإعداد الافتراضي"))
        self.on_setting_changed()

    def restore_focus(self):
        if self.last_focus_control and self.last_focus_control in self.tab_order:
            self.last_focus_control.SetFocus()
            return
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            return
        self.focus_main_control()

    def move_focus_by_tab(self, backwards=False):
        if not self.tab_order:
            return False
        focused = wx.Window.FindFocus()
        try:
            index = self.tab_order.index(focused)
        except ValueError:
            index = -1 if not backwards else 0
        index = (index - 1) % len(self.tab_order) if backwards else (index + 1) % len(self.tab_order)
        self.last_focus_control = self.tab_order[index]
        self.tab_order[index].SetFocus()
        return True

    def cleanup_preview_file(self):
        if self.preview_temp_dir and os.path.exists(self.preview_temp_dir):
            shutil.rmtree(self.preview_temp_dir, ignore_errors=True)
        self.preview_temp_dir = ""
        self.preview_path = ""
        self.preview_source_key = None

    def show_preview_progress(self):
        if self.preview_progress_dialog:
            return
        self.preview_progress_dialog = SaveProgressDialog(
            self,
            self.cancel_preview,
            title=self.text["rendering"],
            progress_template=self.text["progress"],
            status_name=self.text["status"],
            gauge_name=self.text["gauge"],
            cancel_label=self.text["cancel"],
            cancel_name=self.text["cancel_preview"],
            cancelling_message=self.text["cancel_preview"],
        )
        self.preview_progress_dialog.Show()

    def update_preview_progress(self, value):
        self.update_progress(value, self.text["rendering"])
        if self.preview_progress_dialog:
            self.preview_progress_dialog.update_progress(int(max(0, min(100, value))))

    def destroy_preview_progress(self):
        if self.preview_progress_dialog:
            self.preview_progress_dialog.Destroy()
            self.preview_progress_dialog = None

    def cancel_preview(self):
        self.preview_cancelled = True

    def preview_key(self):
        selected = self.selected_range()
        background_signature = tuple(
            (
                item.get("id", ""),
                item.get("path", ""),
                float(item.get("start", 0) or 0),
                float(item.get("end", 0) or 0),
                float(item.get("volume", 1.0) if item.get("volume") is not None else 1.0),
                float(item.get("speed", 1.0) or 1.0),
                float(item.get("source_offset", 0.0) or 0.0),
            )
            for item in getattr(self.parent, "background_audio_items", [])
        )
        return (
            tuple(selected or ()),
            tuple(sorted((key, value) for key, value in self.values().items() if key != "enabled")),
            background_signature,
        )

    def render_preview(self, generation, output_path, temp_dir):
        try:
            selected = self.selected_range()
            if not selected:
                raise RuntimeError(self.text["no_background"])
            start_time, end_time = selected
            duration = max(0.001, end_time - start_time)
            timeline_factory = getattr(self.parent, "audio_effect_preparation_timeline", None)
            source_timeline = list(timeline_factory() if callable(timeline_factory) else self.parent.timeline)
            timeline = slice_segments(source_timeline, start_time, end_time)
            background = timed_items_for_range(getattr(self.parent, "background_audio_items", []), start_time, end_time)
            background, count = apply_ducking_to_background_items(background, 0.0, duration, self.values())
            if count <= 0:
                raise RuntimeError(self.text["no_background"])
            sound_effects = timed_items_for_range(getattr(self.parent, "sound_effects_items", []), start_time, end_time)

            def progress(value):
                if self.preview_cancelled:
                    raise RuntimeError(self.text["cancel_preview"])
                wx.CallAfter(self.update_preview_progress, value)

            from video_maker.video_editing import write_timeline_audio

            write_timeline_audio(
                timeline,
                output_path,
                progress_callback=progress,
                background_audio_items=background,
                sound_effects_items=sound_effects,
                muted_tracks=set(getattr(self.parent, "muted_tracks", ()) or ()),
                solo_tracks=set(getattr(self.parent, "solo_tracks", ()) or ()),
                main_volume_percent=self.values().get("main_volume_percent", 100),
            )
            wx.CallAfter(self.finish_preview_rendered, generation, output_path, temp_dir, duration)
        except Exception as error:
            wx.CallAfter(self.finish_preview_failed, generation, str(error), temp_dir)

    def schedule_preview_preparation(self, delay=300, play_after_render=False):
        timer = self.preview_render_timer
        self.preview_render_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        if delay <= 0:
            self.start_preview_render(play_after_render)
        else:
            self.preview_render_timer = wx.CallLater(delay, self.start_preview_render, play_after_render)

    def start_preview_render(self, play_after_render=False):
        if self.preview_rendering:
            self.preview_play_after_render = self.preview_play_after_render or bool(play_after_render)
            if self.preview_source_key == self.preview_key() and not self.preview_cancelled:
                return False
            self.preview_cancelled = True
            self.preview_render_pending = True
            return False
        key = self.preview_key()
        if self.preview_path and os.path.exists(self.preview_path) and self.preview_source_key == key:
            if play_after_render:
                self.start_preview_audio()
            return True
        self.cleanup_preview_file()
        self.preview_generation += 1
        generation = self.preview_generation
        self.preview_cancelled = False
        self.preview_rendering = True
        self.preview_render_pending = False
        self.preview_play_after_render = bool(play_after_render)
        self.preview_source_key = key
        temp_dir = tempfile.mkdtemp(prefix="audio_ducking_preview_")
        output_path = os.path.join(temp_dir, "preview.wav")
        self.preview_temp_dir = temp_dir
        self.update_progress(1, self.text["rendering"])
        threading.Thread(target=self.render_preview, args=(generation, output_path, temp_dir), daemon=True).start()
        return False

    def restart_pending_preview_render(self):
        if not self.preview_render_pending:
            return False
        play_after_render = self.preview_play_after_render
        self.preview_render_pending = False
        self.preview_play_after_render = False
        wx.CallAfter(self.start_preview_render, play_after_render)
        return True

    def finish_preview_rendered(self, generation, output_path, temp_dir, duration):
        self.preview_rendering = False
        self.destroy_preview_progress()
        if generation != self.preview_generation:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.restart_pending_preview_render()
            return
        self.preview_render_pending = False
        self.preview_path = output_path
        self.preview_temp_dir = temp_dir
        self.preview_duration = duration
        self.update_progress(100, self.text["ready"])
        if self.preview_play_after_render:
            self.preview_play_after_render = False
            self.start_preview_audio()
        else:
            wx.CallAfter(self.restore_focus)

    def finish_preview_failed(self, generation, message, temp_dir):
        self.preview_rendering = False
        self.destroy_preview_progress()
        if generation != self.preview_generation:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.restart_pending_preview_render()
            return
        shutil.rmtree(temp_dir, ignore_errors=True)
        self.preview_render_pending = False
        self.preview_temp_dir = ""
        self.preview_path = ""
        self.update_progress(0, message or self.text["render_failed"])
        self.restore_focus()

    def play_preview(self, event=None):
        self.remember_focus()
        if self.preview_player.is_playing:
            wx.CallAfter(self.restore_focus)
            return
        self.start_preview_audio(self.preview_player.offset)

    def announce_preview_message(self, message):
        self.update_progress(0, message)
        speaker = getattr(self.parent, "say", None)
        if callable(speaker):
            try:
                speaker(message)
                return
            except Exception:
                pass
        speech = getattr(self.parent, "speech", None)
        speech_say = getattr(speech, "say", None)
        if callable(speech_say):
            try:
                speech_say(message, True, False)
            except Exception:
                pass

    def live_preview_source(self):
        if not getattr(self.parent, "background_audio_items", []):
            raise RuntimeError(self.text["no_background_added"])
        selected = self.selected_range()
        if not selected:
            raise RuntimeError(self.text["no_background"])
        start_time, end_time = selected
        duration = max(0.001, end_time - start_time)
        background = timed_items_for_range(getattr(self.parent, "background_audio_items", []), start_time, end_time)
        _preview_background, count = apply_ducking_to_background_items(background, 0.0, duration, self.values())
        if count <= 0:
            raise RuntimeError(self.text["no_background"])
        temp_dir = tempfile.mkdtemp(prefix="audio_ducking_live_")
        self.preview_temp_dir = temp_dir

        def build_args(offset, _duration):
            offset = max(0.0, min(duration, float(offset or 0.0)))
            preview_start = start_time + offset
            preview_end = end_time
            preview_duration = max(0.001, preview_end - preview_start)
            timeline_factory = getattr(self.parent, "audio_effect_preparation_timeline", None)
            source_timeline = list(timeline_factory() if callable(timeline_factory) else self.parent.timeline)
            timeline = slice_segments(source_timeline, preview_start, preview_end)
            live_background = timed_items_for_range(getattr(self.parent, "background_audio_items", []), preview_start, preview_end)
            live_background, _count = apply_ducking_to_background_items(live_background, 0.0, preview_duration, self.values())
            live_sound_effects = timed_items_for_range(getattr(self.parent, "sound_effects_items", []), preview_start, preview_end)
            return build_live_ducking_preview_args(
                timeline,
                live_background,
                live_sound_effects,
                preview_duration,
                temp_dir,
                muted_tracks=set(getattr(self.parent, "muted_tracks", ()) or ()),
                solo_tracks=set(getattr(self.parent, "solo_tracks", ()) or ()),
                main_volume_percent=self.values().get("main_volume_percent", 100),
            )

        return {"format": "ffmpeg_preview", "build_args": build_args}, 0, duration, temp_dir

    def start_preview_audio(self, offset=0):
        from video_maker.audio_effects import current_program_output_volume

        try:
            self.cleanup_preview_file()
            input_path, start_time, duration, temp_dir = self.live_preview_source()
            if isinstance(input_path, dict) and input_path.get("format") == "ffmpeg_preview":
                prepared_offset = max(0.0, min(float(duration or 0.0), float(offset or 0.0)))
                builder = input_path.get("build_args")
                if callable(builder):
                    input_path = dict(input_path)
                    input_path["prepared_offset"] = prepared_offset
                    input_path["prepared_args"] = list(builder(prepared_offset, duration))
            self.preview_path = input_path
            self.preview_temp_dir = temp_dir or ""
            self.preview_duration = duration
            output_volume = current_program_output_volume(self.parent)
            output_volume_provider = lambda: current_program_output_volume(self.parent)
            self.preview_player.start(input_path, "anull", start_time, duration, offset, output_volume, output_volume_provider)
            self.update_status_text(self.text["ready"])
        except Exception as error:
            self.announce_preview_message(str(error) or self.text["preview_failed"])
        wx.CallAfter(self.restore_focus)

    def pause_preview(self, event=None):
        self.remember_focus()
        self.preview_player.pause()
        wx.CallAfter(self.restore_focus)

    def stop_preview(self, event=None):
        self.remember_focus()
        self.preview_player.reset()
        wx.CallAfter(self.restore_focus)

    def rewind_preview(self, event=None):
        self.remember_focus()
        self.preview_player.seek(-5)
        wx.CallAfter(self.restore_focus)

    def forward_preview(self, event=None):
        self.remember_focus()
        self.preview_player.seek(5)
        wx.CallAfter(self.restore_focus)

    def apply_effect(self, event=None):
        selected = self.selected_range()
        if not selected:
            self.update_progress(0, self.text["no_background"])
            return
        start_time, end_time = selected
        updated, count = apply_ducking_to_background_items(getattr(self.parent, "background_audio_items", []), start_time, end_time, self.values())
        if count <= 0:
            self.update_progress(0, self.text["no_background"])
            return
        before_state = self.parent.capture_edit_state()
        set_audio_effect_values(AUDIO_DUCKING_KEY, self.values())
        self.parent.background_audio_items = updated
        self.parent.timeline, _main_volume_count = apply_main_volume_to_timeline(self.parent.timeline, start_time, end_time, self.values())
        self.parent.current_time = min(start_time, self.parent.timeline_duration())
        self.parent.is_dirty = True
        self.parent.record_edit(self.text["name"], before_state)
        self.parent.refresh_menu_bar()
        self.parent.reload_current_position()
        self.parent.say(self.text["applied"])
        self.close_dialog()

    def close_dialog(self, event=None):
        self.preview_generation += 1
        self.preview_cancelled = True
        self.preview_render_pending = False
        self.preview_play_after_render = False
        timer = self.preview_render_timer
        self.preview_render_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        self.destroy_preview_progress()
        self.preview_player.reset(False, wait=True)
        self.cleanup_preview_file()
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_TAB and self.move_focus_by_tab(event.ShiftDown()):
            return
        focused_slider_key, _focused_slider = self.slider_for_event(event)
        focused_widgets = self.controls.get(focused_slider_key) if focused_slider_key else None
        target_value = self.slider_key_target(key, focused_widgets)
        if target_value is not None:
            if self.set_slider_by_key(focused_slider_key, target_value):
                return
            event.Skip()
            return
        delta = self.slider_key_delta(key, focused_widgets)
        if delta is not None and self.adjust_focused_slider(delta, event):
            return
        focused = wx.Window.FindFocus()
        if key == wx.WXK_ESCAPE:
            self.close_dialog()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if isinstance(focused, wx.Button):
                event.Skip()
                return
            self.apply_effect()
            return
        if key == wx.WXK_F4:
            self.play_preview()
            return
        if key == wx.WXK_F5:
            self.rewind_preview()
            return
        if key == wx.WXK_F6:
            self.forward_preview()
            return
        if key == wx.WXK_F7:
            self.pause_preview()
            return
        if key == wx.WXK_F8:
            self.stop_preview()
            return
        if key == wx.WXK_SPACE and self.toggle_preview():
            return
        event.Skip()

    def toggle_preview(self):
        focused = wx.Window.FindFocus()
        if isinstance(focused, wx.Button):
            return False
        if self.preview_player.is_playing:
            self.stop_preview()
        else:
            self.play_preview()
        return True
