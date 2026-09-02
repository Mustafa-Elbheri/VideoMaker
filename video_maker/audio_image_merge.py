import os
import re
import tempfile
import time
from dataclasses import dataclass
from math import ceil

import wx
from video_maker.video_editing import get_media_duration, ffmpeg_startupinfo
from video_maker.watermark import run_ffmpeg_with_progress, ffmpeg_binary
from PIL import Image, ImageFilter, ImageOps

from video_maker.operation_control import OperationCancelled
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import prepare_media_file_dialog, remember_media_path, remember_media_paths
from video_maker.localization import tr, tr_format


try:
    from PIL.Image import Resampling
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Resampling.LANCZOS
except ImportError:
    if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "LANCZOS"):
        Image.ANTIALIAS = Image.LANCZOS


TRANSITIONS = ["بدون انتقال", "تلاشي", "قطع مباشر", "دوران", "تلاشي للخارج", "انعكاس أفقي", "زيادة الإضاءة"]


def image_wildcard():
    return f"{tr('ملفات الصور')} (*.jpg;*.jpeg;*.png;*.bmp;*.webp)|*.jpg;*.jpeg;*.png;*.bmp;*.webp"


def audio_wildcard():
    return f"{tr('ملفات الصوت')} (*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac)|*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac"


def translated_transitions():
    return [tr(name) for name in TRANSITIONS]


def image_duration_message(seconds):
    return f"{seconds} {tr('ثانية')}"




@dataclass
class AudioImageMergeOptions:
    images: list
    audio: str
    image_duration: int
    distribute_evenly: bool
    transition: str


def natural_sort_key(path):
    name = os.path.basename(path)
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def choose_video_size(images):
    counts = {"landscape": 0, "portrait": 0, "square": 0}
    valid_images = 0

    for image_path in images:
        try:
            image = ImageOps.exif_transpose(Image.open(image_path))
            ratio = image.width / image.height
            if ratio > 1.2:
                counts["landscape"] += 1
            elif ratio < 0.8:
                counts["portrait"] += 1
            else:
                counts["square"] += 1
            valid_images += 1
            image.close()
        except Exception:
            continue

    if valid_images == 0:
        return 1280, 720

    dominant = max(counts, key=counts.get)
    if counts[dominant] / valid_images < 0.6:
        try:
            image = ImageOps.exif_transpose(Image.open(images[0]))
            ratio = image.width / image.height
            image.close()
            if ratio > 1.2:
                return 1280, 720
            if ratio < 0.8:
                return 720, 1280
            return 1080, 1080
        except Exception:
            return 1280, 720

    if dominant == "portrait":
        return 720, 1280
    if dominant == "square":
        return 1080, 1080
    return 1280, 720


def process_images(images, temp_dir, progress_callback, cancelled_callback):
    resized_images = []
    target_width, target_height = choose_video_size(images)

    for index, image_path in enumerate(images):
        if cancelled_callback():
            return []

        try:
            image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
            background = image.copy()
            background.thumbnail((64, 64), Image.Resampling.LANCZOS)
            background = background.resize((target_width, target_height), Image.Resampling.BICUBIC)
            background = background.filter(ImageFilter.GaussianBlur(radius=10))

            ratio = min(target_width / image.width, target_height / image.height)
            foreground_size = int(image.width * ratio), int(image.height * ratio)
            foreground = image.resize(foreground_size, Image.Resampling.LANCZOS)
            x = (target_width - foreground.width) // 2
            y = (target_height - foreground.height) // 2
            background.paste(foreground, (x, y))

            resized_path = os.path.join(temp_dir, f"image_{index}.jpg")
            background.save(resized_path, format="JPEG", quality=95)
            resized_images.append(resized_path)

            image.close()
            background.close()
            foreground.close()
        except Exception:
            continue

        progress_callback((index + 1) / len(images) * 0.3)
        time.sleep(0.01)

    return resized_images


def calculate_durations(image_count, audio_duration, image_duration, distribute_evenly):
    if image_count <= 0:
        raise ValueError(tr("لم يتم اختيار صور صالحة."))
    if audio_duration <= 0:
        raise ValueError(tr("مدة الصوت غير صالحة."))

    if distribute_evenly:
        single_duration = audio_duration / image_count
        durations = [single_duration] * image_count
        durations[-1] = audio_duration - sum(durations[:-1])
        return durations, image_count

    required_count = int(ceil(audio_duration / image_duration))
    durations = [image_duration] * required_count
    durations[-1] -= sum(durations) - audio_duration

    while durations and durations[-1] <= 0 and len(durations) > 1:
        durations.pop()
        durations[-1] -= sum(durations) - audio_duration

    if not durations or durations[-1] <= 0:
        raise ValueError(tr("مدة الصور أطول من مدة الصوت."))

    return durations, len(durations)




class MergeProgressDialog(wx.Dialog):
    def __init__(self, parent, cancel_callback):
        super().__init__(parent, title=tr("جاري دمج الصوت مع الصور"), size=(420, 140))
        self.cancel_callback = cancel_callback
        self.gauge = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL)
        self.cancel_button = wx.Button(self, label=tr("إلغاء"))
        self.cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.gauge, proportion=1, flag=wx.EXPAND | wx.ALL, border=12)
        sizer.Add(self.cancel_button, flag=wx.ALIGN_CENTER | wx.ALL, border=8)
        self.SetSizer(sizer)
        self.Centre()

    def on_cancel(self, event):
        self.cancel_callback()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_cancel(event)
            return
        event.Skip()

    def update_progress(self, progress):
        self.gauge.SetValue(int(progress * 100))


class AudioImageMergeDialog(wx.Frame):
    def __init__(self, parent, start_callback):
        super().__init__(parent, title=tr("دمج الصوت مع الصور"), size=(620, 320))
        from video_maker.menus import install_menu_bar

        self.start_callback = start_callback
        self.images = []
        self.audio = ""

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.images_button = wx.Button(panel, label=tr("اختيار الصور"), size=(260, 36))
        self.audio_button = wx.Button(panel, label=tr("اختيار الصوت"), size=(260, 36))
        duration_label = wx.StaticText(panel, label=tr("مدة كل صورة بالثواني"))
        self.duration_choice = wx.Choice(panel, choices=[str(value) for value in range(1, 101)])
        self.duration_choice.SetSelection(4)
        self.distribute_checkbox = wx.CheckBox(panel, label=tr("توزيع الصور على مدة الصوت بالتساوي"))
        transition_label = wx.StaticText(panel, label=tr("تأثير الانتقال"))
        self.transition_choice = wx.Choice(panel, choices=translated_transitions())
        self.transition_choice.SetSelection(0)

        start_button = wx.Button(panel, label=tr("بدء الدمج"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))
        start_button.SetName(tr("بدء الدمج"))
        cancel_button.SetName(tr("إلغاء"))
        self.images_button.SetName(tr("اختيار الصور"))
        self.audio_button.SetName(tr("اختيار الصوت"))
        self.duration_choice.SetName(tr("مدة كل صورة بالثواني"))
        self.distribute_checkbox.SetName(tr("توزيع الصور على مدة الصوت بالتساوي"))
        self.transition_choice.SetName(tr("تأثير الانتقال"))
        start_button.SetDefault()
        action_sizer = wx.BoxSizer(wx.HORIZONTAL)
        action_sizer.Add(start_button, flag=wx.ALL, border=6)
        action_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(self.images_button, flag=wx.ALIGN_CENTER | wx.TOP, border=12)
        main_sizer.Add(self.audio_button, flag=wx.ALIGN_CENTER | wx.TOP, border=6)
        main_sizer.Add(duration_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        main_sizer.Add(self.duration_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        main_sizer.Add(self.distribute_checkbox, flag=wx.ALL | wx.ALIGN_CENTER, border=6)
        main_sizer.Add(transition_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        main_sizer.Add(self.transition_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        main_sizer.Add(action_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=8)

        panel.SetSizer(main_sizer)
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(dialog_sizer)

        self.images_button.Bind(wx.EVT_BUTTON, self.select_images)
        self.audio_button.Bind(wx.EVT_BUTTON, self.select_audio)
        self.duration_choice.Bind(wx.EVT_CHAR_HOOK, self.on_duration_key)
        start_button.Bind(wx.EVT_BUTTON, self.on_ok)
        cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, (wx.Choice,), preserve_navigation_keys=True)
        self.Centre()
        self.initial_focus = self.images_button
        install_menu_bar(self, parent, include_shortcuts=False)

    def Show(self, show=True):
        wx.CallAfter(self.set_initial_focus)
        return super().Show(show)

    def set_initial_focus(self):
        self.initial_focus.SetFocus()

    def speak(self, message):
        parent = self.GetParent() if hasattr(self, "GetParent") else None
        if hasattr(parent, "say"):
            parent.say(message)

    def announce_duration_choice(self):
        selection = self.duration_choice.GetSelection()
        if selection == wx.NOT_FOUND:
            return
        self.speak(image_duration_message(self.duration_choice.GetString(selection)))

    def change_duration_choice(self, step):
        selection = self.duration_choice.GetSelection()
        if selection == wx.NOT_FOUND:
            selection = 0
        selection = max(0, min(self.duration_choice.GetCount() - 1, selection + step))
        self.duration_choice.SetSelection(selection)
        self.announce_duration_choice()

    def on_duration_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_UP, getattr(wx, "WXK_NUMPAD_UP", wx.WXK_UP)):
            self.change_duration_choice(1)
            return
        if key in (wx.WXK_DOWN, getattr(wx, "WXK_NUMPAD_DOWN", wx.WXK_DOWN)):
            self.change_duration_choice(-1)
            return
        if key in (wx.WXK_PAGEUP, getattr(wx, "WXK_NUMPAD_PAGEUP", wx.WXK_PAGEUP)):
            self.change_duration_choice(10)
            return
        if key in (wx.WXK_PAGEDOWN, getattr(wx, "WXK_NUMPAD_PAGEDOWN", wx.WXK_PAGEDOWN)):
            self.change_duration_choice(-10)
            return
        event.Skip()

    def select_images(self, event):
        with wx.FileDialog(self, tr("اختيار الصور"), wildcard=image_wildcard(), style=wx.FD_OPEN | wx.FD_MULTIPLE) as dialog:
            prepare_media_file_dialog(dialog, "image", "audio_image_merge_images")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.images = sorted(dialog.GetPaths(), key=natural_sort_key)
            remember_media_paths(self.images, "image", "audio_image_merge_images")
            self.images_button.SetLabel(tr_format("تم اختيار {count} صورة", count=len(self.images)))

    def select_audio(self, event):
        with wx.FileDialog(self, tr("اختيار الصوت"), wildcard=audio_wildcard(), style=wx.FD_OPEN) as dialog:
            prepare_media_file_dialog(dialog, "audio", "audio_image_merge_audio")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.audio = dialog.GetPath()
            remember_media_path(self.audio, "audio", "audio_image_merge_audio")
            self.audio_button.SetLabel(tr_format("تم اختيار {name}", name=os.path.basename(self.audio)))

    def on_ok(self, event):
        if not self.images:
            wx.MessageBox(tr("اختر الصور أولًا."), tr("بيانات ناقصة"), wx.OK | wx.ICON_ERROR)
            return
        if not self.audio:
            wx.MessageBox(tr("اختر ملف الصوت أولًا."), tr("بيانات ناقصة"), wx.OK | wx.ICON_ERROR)
            return
        self.start_callback(self.get_options())
        self.Destroy()

    def on_cancel(self, event):
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
            return
        event.Skip()

    def get_options(self):
        return AudioImageMergeOptions(
            images=self.images,
            audio=self.audio,
            image_duration=int(self.duration_choice.GetString(self.duration_choice.GetSelection())),
            distribute_evenly=self.distribute_checkbox.GetValue(),
            transition=TRANSITIONS[max(0, self.transition_choice.GetSelection())],
        )


def create_audio_image_video(options, output_file, temp_dir, progress_callback, cancelled_callback):
    output_file = os.path.abspath(output_file)
    resized_images = process_images(options.images, temp_dir, progress_callback, cancelled_callback)
    if cancelled_callback():
        return False

    audio_duration = get_media_duration(options.audio)
    if audio_duration <= 0:
        audio_duration = 5.0
    durations, required_count = calculate_durations(len(resized_images), audio_duration, options.image_duration, options.distribute_evenly)

    if options.distribute_evenly:
        images = resized_images
    else:
        repeats = required_count // len(resized_images)
        remainder = required_count % len(resized_images)
        images = resized_images * repeats + resized_images[:remainder]

    filters = []
    inputs = []
    
    for i in range(len(images)):
        inputs.extend(["-loop", "1", "-t", str(durations[i]), "-i", images[i]])

    for i in range(len(images)):
        d = durations[i]
        eff = options.transition
        f_str = ""
        if eff == TRANSITIONS[3]: # rotate
            f_str = f"rotate=2*PI*t/{d}:c=black"
        elif eff == TRANSITIONS[4]: # fadeout
            f_str = f"fade=t=out:st={max(0, d-1)}:d=1"
        elif eff == TRANSITIONS[5]: # mirror
            f_str = "hflip"
        elif eff == TRANSITIONS[6]: # colorx (brightness)
            f_str = f"geq=r='clip(r(X,Y)*(1+0.5*T/{d}),0,255)':g='clip(g(X,Y)*(1+0.5*T/{d}),0,255)':b='clip(b(X,Y)*(1+0.5*T/{d}),0,255)'"

        if f_str:
            filters.append(f"[{i}:v]fps=24,format=yuv420p,{f_str}[v{i}];")
        else:
            filters.append(f"[{i}:v]fps=24,format=yuv420p[v{i}];")

    if options.transition == TRANSITIONS[1] and len(images) > 1: # crossfade
        current_offset = durations[0] - 1
        filters.append(f"[v0][v1]xfade=transition=fade:duration=1:offset={current_offset}[xf1];")
        for i in range(2, len(images)):
            current_offset += durations[i-1] - 1
            filters.append(f"[xf{i-1}][v{i}]xfade=transition=fade:duration=1:offset={current_offset}[xf{i}];")
        out_pad = f"[xf{len(images)-1}]"
        visual_duration = sum(durations) - (len(images) - 1)
        if visual_duration < audio_duration:
            filters.append(f"{out_pad}tpad=stop_mode=clone:stop_duration={audio_duration - visual_duration:.6f}[outv];")
            out_pad = "[outv]"
    else:
        concat_inputs = "".join([f"[v{i}]" for i in range(len(images))])
        filters.append(f"{concat_inputs}concat=n={len(images)}:v=1:a=0[outv];")
        out_pad = "[outv]"
        
    script_path = os.path.join(temp_dir, "filter_script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write("".join(filters))
        
    def build_command(audio_args):
        return [
            ffmpeg_binary(),
            "-y",
        ] + inputs + [
            "-i", options.audio,
            "-filter_complex_script", script_path,
            "-map", out_pad,
            "-map", f"{len(images)}:a?",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
        ] + audio_args + [
            "-shortest",
            "-movflags", "+faststart",
            output_file
        ]

    cmd = build_command(["-c:a", "copy"])
    
    try:
        run_ffmpeg_with_progress(
            cmd, 
            options.audio,
            output_file,
            tr("فشل إنشاء الفيديو النهائي."),
            progress_callback=lambda p: progress_callback(0.3 + p * 0.7 / 100) if progress_callback else None,
            cancelled_callback=cancelled_callback
        )
    except Exception:
        if cancelled_callback():
            return False
        fallback_cmd = build_command(["-c:a", "aac", "-b:a", "320k"])
        run_ffmpeg_with_progress(
            fallback_cmd,
            options.audio,
            output_file,
            tr("فشل إنشاء الفيديو النهائي."),
            progress_callback=lambda p: progress_callback(0.3 + p * 0.7 / 100) if progress_callback else None,
            cancelled_callback=cancelled_callback
        )
    
    return not cancelled_callback()
