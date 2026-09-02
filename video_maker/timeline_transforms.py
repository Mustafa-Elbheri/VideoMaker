import os
import shutil
import subprocess
import tempfile

import wx

from video_maker.audio_effects import AudioEffectPreparationCancelled, RealtimeAudioPreview, current_program_output_volume, run_ffmpeg_with_progress
from video_maker.app_paths import ffmpeg_binary
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.localization import tr
from video_maker.timeline import TimelineSegment, delete_range, insert_segments, slice_segments, with_transition
from video_maker.logical_files import replacement_segments_preserving_files
from video_maker.transition_effects import TRANSITION_DURATIONS, all_transition_effects
from video_maker.video_editing import ffmpeg_startupinfo, get_media_duration, has_video_stream, timeline_boundary_transitions, write_timeline_audio, write_timeline_video


SPEED_CHOICES = [
    ("0.25x", 0.25),
    ("0.33x", 0.33),
    ("0.5x", 0.5),
    ("0.67x", 0.67),
    ("0.75x", 0.75),
    ("0.9x", 0.9),
    ("1x", 1.0),
    ("1.1x", 1.1),
    ("1.25x", 1.25),
    ("1.5x", 1.5),
    ("1.75x", 1.75),
    ("2x", 2.0),
    ("2.5x", 2.5),
    ("3x", 3.0),
    ("3.5x", 3.5),
    ("4x", 4.0),
    ("5x", 5.0),
    ("6x", 6.0),
    ("8x", 8.0),
]


CENSOR_SOUNDS = [
    {
        "key": "beep_high",
        "name": "تيت حاد",
        "description": "صوت تيت واضح وقصير مناسب لكتم كلمة",
        "source": lambda duration: f"sine=frequency=1000:sample_rate=48000:duration={duration:.3f}",
    },
    {
        "key": "beep_low",
        "name": "تيت منخفض",
        "description": "صوت تيت منخفض وأهدأ للتغطية",
        "source": lambda duration: f"sine=frequency=520:sample_rate=48000:duration={duration:.3f}",
    },
    {
        "key": "beep_medium",
        "name": "تيت متوسط",
        "description": "صوت تيت متوسط وواضح مناسب لمعظم الكلمات",
        "source": lambda duration: f"sine=frequency=760:sample_rate=48000:duration={duration:.3f}",
    },
    {
        "key": "beep_very_high",
        "name": "تيت عالي جدا",
        "description": "صفارة عالية حادة للتغطية القوية والسريعة",
        "source": lambda duration: f"sine=frequency=1650:sample_rate=48000:duration={duration:.3f}",
    },
    {
        "key": "digital_pulse",
        "name": "نبض إلكتروني",
        "description": "نبض إلكتروني متقطع مناسب للتغطية الواضحة",
        "source": lambda duration: f"sine=frequency=1200:sample_rate=48000:duration={duration:.3f},tremolo=f=14:d=0.85",
    },
    {
        "key": "soft_noise",
        "name": "تشويش ناعم",
        "description": "تشويش خفيف يغطي الكلمة بدون صفارة",
        "source": lambda duration: f"anoisesrc=color=pink:amplitude=0.18:duration={duration:.3f}:sample_rate=48000",
    },
    {
        "key": "white_noise",
        "name": "تشويش أبيض",
        "description": "تشويش أبيض ثابت يغطي الكلمة بدون نغمة",
        "source": lambda duration: f"anoisesrc=color=white:amplitude=0.16:duration={duration:.3f}:sample_rate=48000",
    },
    {
        "key": "deep_noise",
        "name": "تشويش عميق",
        "description": "تشويش منخفض وأكثر امتلاء للتغطية الهادئة",
        "source": lambda duration: f"anoisesrc=color=brown:amplitude=0.24:duration={duration:.3f}:sample_rate=48000",
    },
    {
        "key": "short_buzz",
        "name": "طنين قصير",
        "description": "طنين منخفض متقطع يعطي تغطية مختلفة عن التيت",
        "source": lambda duration: f"sine=frequency=180:sample_rate=48000:duration={duration:.3f},tremolo=f=24:d=0.8",
    },
    {
        "key": "silence",
        "name": "صمت تام",
        "description": "يكتم الكلمة تماما بدون صوت تغطية",
        "source": lambda duration: f"anullsrc=channel_layout=stereo:sample_rate=48000",
    },
]


def timeline_has_video(timeline):
    return bool(timeline and has_video_stream(timeline[0].path))


def write_selected_media(timeline, start_time, end_time, output_path, progress_callback=None, cancelled_callback=None):
    selected_segments = slice_segments(timeline, start_time, end_time)
    if not selected_segments:
        raise RuntimeError("لا يوجد جزء محدد")
    if timeline_has_video(timeline):
        write_timeline_video(
            selected_segments,
            output_path,
            lambda percent: progress_callback(percent * 0.35) if progress_callback else None,
            cancelled_callback,
        )
    else:
        write_timeline_audio(
            selected_segments,
            output_path,
            lambda percent: progress_callback(percent * 0.35) if progress_callback else None,
            cancelled_callback,
        )
    return selected_segments


def speed_timeline_range(timeline, start_time, end_time, speed):
    speed = max(0.25, min(8.0, float(speed)))
    selected = slice_segments(timeline, start_time, end_time)
    accelerated = [
        TimelineSegment(
            segment.path,
            segment.start,
            segment.end,
            max(0.05, float(getattr(segment, "speed", 1.0) or 1.0)) * speed,
            max(0.0, min(1.0, float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0))),
            str(getattr(segment, "audio_path", "") or ""),
            getattr(segment, "audio_start", None),
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
            str(getattr(segment, "transition", "") or ""),
            max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        )
        for segment in selected
    ]
    remaining = delete_range(timeline, start_time, end_time)
    return insert_segments(remaining, start_time, accelerated), sum(segment.duration for segment in accelerated)


def mute_original_audio_range(timeline, start_time, end_time):
    selected = slice_segments(timeline, start_time, end_time)
    muted = [
        TimelineSegment(
            segment.path,
            segment.start,
            segment.end,
            max(0.05, float(getattr(segment, "speed", 1.0) or 1.0)),
            0.0,
            str(getattr(segment, "audio_path", "") or ""),
            getattr(segment, "audio_start", None),
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
            str(getattr(segment, "transition", "") or ""),
            max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        )
        for segment in selected
    ]
    remaining = delete_range(timeline, start_time, end_time)
    return insert_segments(remaining, start_time, muted)


def mute_timeline_audio_ranges(timeline, ranges):
    updated = list(timeline or [])
    changed = False
    for start_time, end_time in sorted(ranges or [], reverse=True):
        selected = slice_segments(updated, start_time, end_time)
        if any(float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0) > 0.001 for segment in selected):
            changed = True
        updated = mute_original_audio_range(updated, start_time, end_time)
    return updated, changed


def censor_source(sound_key, duration):
    sound = next((item for item in CENSOR_SOUNDS if item["key"] == sound_key), CENSOR_SOUNDS[0])
    return sound["source"](max(0.05, float(duration)))


def build_censor_sound_preview(sound_key, duration=1.0):
    temp_dir = tempfile.mkdtemp(prefix="censor_sound_preview_")
    output_path = os.path.join(temp_dir, "preview.wav")
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        censor_source(sound_key, duration),
        "-t",
        f"{max(0.05, float(duration)):.3f}",
        "-c:a",
        "pcm_s16le",
        output_path,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        message = result.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(message or "تعذر تجهيز تجربة صوت التغطية")
    return output_path, temp_dir


def build_censor_segment(timeline, start_time, end_time, sound_key, progress_callback=None, cancelled_callback=None):
    temp_dir = tempfile.mkdtemp(prefix="timeline_censor_")
    has_video = timeline_has_video(timeline)
    selected_path = os.path.join(temp_dir, "selected.mp4" if has_video else "selected.wav")
    output_path = os.path.join(temp_dir, "censor.mp4" if has_video else "censor.wav")
    write_selected_media(timeline, start_time, end_time, selected_path, progress_callback, cancelled_callback)
    duration = max(0.05, get_media_duration(selected_path))
    source = censor_source(sound_key, duration)

    if has_video:
        command = [
            ffmpeg_binary(),
            "-y",
            "-i",
            selected_path,
            "-f",
            "lavfi",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-shortest",
            "-movflags",
            "+faststart",
            output_path,
        ]
    else:
        command = [
            ffmpeg_binary(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
    run_ffmpeg_with_progress(
        command,
        selected_path,
        output_path,
        "تعذر تطبيق كتم الكلمة",
        lambda percent: progress_callback(35 + percent * 0.65) if progress_callback else None,
        cancelled_callback,
    )
    return output_path, temp_dir, get_media_duration(output_path)


def replace_timeline_range(timeline, start_time, end_time, media_path):
    duration = get_media_duration(media_path)
    transformed = replacement_segments_preserving_files(
        timeline, start_time, end_time, media_path, duration
    )
    remaining = delete_range(timeline, start_time, end_time)
    return insert_segments(remaining, start_time, transformed), duration


class SpeedDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=tr("تسريع وإبطاء"), size=(560, 230))
        self.parent = parent
        self.preview_offset = 0.0
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label=tr("سرعة التشغيل"))
        self.choice = wx.Choice(panel, choices=[label for label, _value in SPEED_CHOICES])
        self.choice.SetName(tr("اختيار سرعة التشغيل"))
        default_index = next((index for index, (_label, value) in enumerate(SPEED_CHOICES) if abs(value - 1.0) < 0.001), 0)
        start_index = getattr(parent, "_speed_step_index", default_index)
        self.choice.SetSelection(start_index)
        sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        sizer.Add(self.choice, flag=wx.EXPAND | wx.ALL, border=12)
        preview_buttons = wx.BoxSizer(wx.HORIZONTAL)
        play_button = wx.Button(panel, label=tr("تشغيل"))
        rewind_button = wx.Button(panel, label=tr("ترجيع"))
        forward_button = wx.Button(panel, label=tr("تقديم"))
        pause_button = wx.Button(panel, label=tr("إيقاف مؤقت"))
        stop_button = wx.Button(panel, label=tr("إيقاف"))
        play_button.SetName(tr("تشغيل معاينة السرعة"))
        rewind_button.SetName(tr("ترجيع معاينة السرعة"))
        forward_button.SetName(tr("تقديم معاينة السرعة"))
        pause_button.SetName(tr("إيقاف مؤقت لمعاينة السرعة"))
        stop_button.SetName(tr("إيقاف معاينة السرعة"))
        for preview_button in (play_button, rewind_button, forward_button, pause_button, stop_button):
            preview_button.SetCanFocus(False)
            preview_buttons.Add(preview_button, flag=wx.ALL, border=6)
        sizer.Add(preview_buttons, flag=wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT, border=12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("تطبيق تغيير السرعة"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        panel.SetSizer(sizer)
        self.choice.Bind(wx.EVT_CHOICE, self.on_speed_changed)
        self.choice.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        play_button.Bind(wx.EVT_BUTTON, self.play_preview)
        rewind_button.Bind(wx.EVT_BUTTON, self.rewind_preview)
        forward_button.Bind(wx.EVT_BUTTON, self.forward_preview)
        pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        cancel_button.Bind(wx.EVT_BUTTON, self.cancel_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.cancel_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Choice,))
        self.Centre()
        wx.CallAfter(self.choice.SetFocus)

    def speed(self):
        selection = self.choice.GetSelection()
        if 0 <= selection < len(SPEED_CHOICES):
            return SPEED_CHOICES[selection][1]
        return 1.0

    def _default_index(self):
        return next(
            (i for i, (_l, v) in enumerate(SPEED_CHOICES) if abs(v - 1.0) < 0.001),
            0,
        )

    def step_up(self):
        """تسريع خطوة واحدة بدون فتح القائمة."""
        current = self.choice.GetSelection()
        if current < len(SPEED_CHOICES) - 1:
            self.choice.SetSelection(current + 1)
            label = SPEED_CHOICES[current + 1][0]
            self.on_speed_changed()
            self.parent.say(tr("تسريع خطوة واحدة سرعة {speed}").format(speed=label))

    def step_down(self):
        """تبطيئ خطوة واحدة بدون فتح القائمة."""
        current = self.choice.GetSelection()
        if current > 0:
            self.choice.SetSelection(current - 1)
            label = SPEED_CHOICES[current - 1][0]
            self.on_speed_changed()
            self.parent.say(tr("تبطيئ خطوة واحدة سرعة {speed}").format(speed=label))

    def reset_speed(self):
        """إعادة السرعة للوضع الافتراضي 1x."""
        default = self._default_index()
        self.choice.SetSelection(default)
        self.on_speed_changed()
        self.parent.say(tr("إعادة السرعة للوضع الافتراضي"))

    def has_preview(self):
        return bool(getattr(self.parent, "speed_preview_state", None))

    def preview_duration(self):
        selected = self.parent.selected_effect_range()
        if not selected:
            return 0.0
        speed = max(0.05, float(self.speed() or 1.0))
        return max(0.0, selected[1] - selected[0]) / speed

    def focus_choice(self):
        wx.CallAfter(self.choice.SetFocus)

    def play_preview(self, event=None):
        if self.has_preview():
            self.preview_offset = self.parent.speed_preview_offset()
        duration = self.preview_duration()
        if duration > 0 and self.preview_offset >= duration - 0.05:
            self.preview_offset = 0.0
        self.parent.start_speed_preview(self.speed(), self.preview_offset)
        self.focus_choice()

    def pause_preview(self, event=None):
        if self.has_preview():
            self.preview_offset = self.parent.speed_preview_offset()
        self.parent.pause_speed_preview()
        self.focus_choice()

    def stop_preview(self, event=None):
        self.preview_offset = 0.0
        self.parent.stop_speed_preview()
        self.focus_choice()

    def rewind_preview(self, event=None):
        if self.has_preview():
            self.preview_offset = self.parent.seek_speed_preview(-5)
        else:
            self.preview_offset = max(0.0, self.preview_offset - 5)
        self.focus_choice()

    def forward_preview(self, event=None):
        if self.has_preview():
            self.preview_offset = self.parent.seek_speed_preview(5)
        else:
            duration = self.preview_duration()
            self.preview_offset = min(duration, self.preview_offset + 5)
        self.focus_choice()

    def toggle_preview(self):
        if self.has_preview() and getattr(self.parent, "playback_requested", False):
            self.stop_preview()
        else:
            self.play_preview()
        return True

    def on_speed_changed(self, event=None):
        if self.has_preview():
            self.preview_offset = self.parent.speed_preview_offset()
        self.parent.start_speed_preview(self.speed(), self.preview_offset, silent=True)
        self.focus_choice()
        if event:
            event.Skip()

    def cancel_dialog(self, event=None):
        self.parent.stop_speed_preview()
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        key = event.GetKeyCode()
        modifiers = event.GetModifiers()
        alt_only = modifiers == wx.MOD_ALT
        if key == wx.WXK_ESCAPE:
            self.cancel_dialog()
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
        if alt_only and key == wx.WXK_RIGHT:
            self.step_up()
            return
        if alt_only and key == wx.WXK_LEFT:
            self.step_down()
            return
        if alt_only and key in (ord('0'), wx.WXK_NUMPAD0):
            self.reset_speed()
            return
        if key == wx.WXK_SPACE:
            focused = wx.Window.FindFocus()
            if not isinstance(focused, wx.Button):
                self.toggle_preview()
                return
        event.Skip()


class CensorBleepDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=tr("كتم كلمة بصوت تغطية"), size=(560, 280))
        self.parent = parent
        self.preview_player = RealtimeAudioPreview()
        self.preview_path = ""
        self.preview_dir = ""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label=tr("صوت تغطية الكلمة"))
        self.choice = wx.Choice(panel, choices=[f"{tr(item['name'])} {tr(item['description'])}" for item in CENSOR_SOUNDS])
        self.choice.SetName(tr("اختيار صوت تغطية الكلمة"))
        self.choice.SetSelection(0)
        sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        sizer.Add(self.choice, flag=wx.EXPAND | wx.ALL, border=12)
        preview_buttons = wx.BoxSizer(wx.HORIZONTAL)
        play_button = wx.Button(panel, label=tr("تشغيل"))
        pause_button = wx.Button(panel, label=tr("إيقاف مؤقت"))
        stop_button = wx.Button(panel, label=tr("إيقاف"))
        play_button.SetName(tr("تشغيل تجربة صوت التغطية"))
        pause_button.SetName(tr("إيقاف مؤقت لتجربة صوت التغطية"))
        stop_button.SetName(tr("إيقاف تجربة صوت التغطية"))
        for preview_button in (play_button, pause_button, stop_button):
            preview_button.SetCanFocus(False)
            preview_buttons.Add(preview_button, flag=wx.ALL, border=6)
        sizer.Add(preview_buttons, flag=wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT, border=12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("تطبيق كتم الكلمة"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        panel.SetSizer(sizer)
        self.choice.Bind(wx.EVT_CHOICE, self.on_sound_changed)
        self.choice.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        play_button.Bind(wx.EVT_BUTTON, self.play_preview)
        pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        ok_button.Bind(wx.EVT_BUTTON, self.accept_dialog)
        cancel_button.Bind(wx.EVT_BUTTON, self.cancel_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.cancel_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Choice,))
        self.Centre()
        wx.CallAfter(self.choice.SetFocus)

    def sound_key(self):
        selection = self.choice.GetSelection()
        if 0 <= selection < len(CENSOR_SOUNDS):
            return CENSOR_SOUNDS[selection]["key"]
        return CENSOR_SOUNDS[0]["key"]

    def focus_choice(self):
        wx.CallAfter(self.choice.SetFocus)

    def cleanup_preview_file(self):
        if self.preview_dir and os.path.exists(self.preview_dir):
            shutil.rmtree(self.preview_dir, ignore_errors=True)
        self.preview_path = ""
        self.preview_dir = ""

    def ensure_preview_file(self):
        key = self.sound_key()
        if self.preview_path and os.path.exists(self.preview_path) and getattr(self, "preview_key", "") == key:
            return self.preview_path
        self.cleanup_preview_file()
        self.preview_path, self.preview_dir = build_censor_sound_preview(key, 1.2)
        self.preview_key = key
        return self.preview_path

    def play_preview(self, event=None):
        try:
            path = self.ensure_preview_file()
            output_volume = current_program_output_volume(self.parent)
            provider = lambda: current_program_output_volume(self.parent)
            self.preview_player.start(path, "anull", 0, 1.2, 0, output_volume, provider)
        except Exception as error:
            wx.MessageBox(f"{tr('تعذر تشغيل تجربة صوت التغطية')}: {error}", tr("خطأ"), wx.OK | wx.ICON_ERROR)
        self.focus_choice()

    def pause_preview(self, event=None):
        self.preview_player.pause()
        self.focus_choice()

    def stop_preview(self, event=None):
        self.preview_player.reset()
        self.focus_choice()

    def toggle_preview(self):
        if self.preview_player.is_playing:
            self.stop_preview()
        else:
            self.play_preview()
        return True

    def on_sound_changed(self, event=None):
        if self.preview_player.is_playing or self.preview_player.play_requested:
            self.play_preview()
        if event:
            event.Skip()

    def cleanup_preview(self):
        self.preview_player.reset(wait=True)
        self.cleanup_preview_file()

    def accept_dialog(self, event=None):
        self.cleanup_preview()
        self.EndModal(wx.ID_OK)

    def cancel_dialog(self, event=None):
        self.cleanup_preview()
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.cancel_dialog()
            return
        if key == wx.WXK_F4:
            self.play_preview()
            return
        if key == wx.WXK_F7:
            self.pause_preview()
            return
        if key == wx.WXK_F8:
            self.stop_preview()
            return
        if key == wx.WXK_SPACE:
            focused = wx.Window.FindFocus()
            if not isinstance(focused, wx.Button):
                self.toggle_preview()
                return
        event.Skip()


class TimelineTransitionDialog(wx.Dialog):
    def __init__(self, parent, boundary_time=0.0, current_key="", current_duration=1.0):
        super().__init__(parent, title=tr("انتقال بين المقطعين"), size=(560, 300))
        self.parent = parent
        self.boundary_time = max(0.0, float(boundary_time or 0.0))
        self.transition_options = [{"key": "", "name": tr("بدون انتقال")}] + [
            {"key": effect["key"], "name": effect["name"]} for effect in all_transition_effects()
        ]
        self.transition_names = [option["name"] for option in self.transition_options]
        self.duration_labels = [label for label, _value in TRANSITION_DURATIONS]
        self.duration_values = [value for _label, value in TRANSITION_DURATIONS]
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        info = wx.StaticText(
            panel,
            label=tr("حدد انتقال الحد الفاصل عند {time} ثانية").format(time=round(self.boundary_time, 2)),
        )
        info.SetName(tr("معلومات حد الانتقال"))
        sizer.Add(info, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        effect_label = wx.StaticText(panel, label=tr("تأثير الانتقال"))
        sizer.Add(effect_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        self.effect_choice = wx.Choice(panel, choices=self.transition_names)
        self.effect_choice.SetName(tr("اختيار تأثير انتقال الحد"))
        current_key = str(current_key or "")
        current_index = next(
            (index for index, option in enumerate(self.transition_options) if option["key"] == current_key),
            0,
        )
        self.effect_choice.SetSelection(current_index)
        sizer.Add(self.effect_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        duration_label = wx.StaticText(panel, label=tr("مدة الانتقال"))
        sizer.Add(duration_label, flag=wx.LEFT | wx.RIGHT, border=12)
        self.duration_choice = wx.Choice(panel, choices=self.duration_labels)
        self.duration_choice.SetName(tr("اختيار مدة انتقال الحد"))
        duration_index = next(
            (index for index, value in enumerate(self.duration_values) if abs(value - float(current_duration or 1.0)) < 0.001),
            next((index for index, value in enumerate(self.duration_values) if value >= float(current_duration or 1.0)), 1),
        )
        self.duration_choice.SetSelection(duration_index)
        sizer.Add(self.duration_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("تطبيق انتقال الحد"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        panel.SetSizer(sizer)
        cancel_button.Bind(wx.EVT_BUTTON, self.cancel_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.cancel_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Choice,))
        self.Centre()
        wx.CallAfter(self.effect_choice.SetFocus)

    def selected_key(self):
        selection = self.effect_choice.GetSelection()
        if 0 <= selection < len(self.transition_options):
            return self.transition_options[selection]["key"]
        return ""

    def selected_duration(self):
        selection = self.duration_choice.GetSelection()
        if 0 <= selection < len(self.duration_values):
            return float(self.duration_values[selection])
        return 1.0

    def cancel_dialog(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.cancel_dialog()
            return
        event.Skip()
