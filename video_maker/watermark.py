import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import wx
from PIL import Image, ImageDraw, ImageFont

from video_maker.app_paths import bundled_path
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import IMAGE_WILDCARD, prepare_media_file_dialog, remember_media_path
from video_maker.localization import tr
from video_maker.video_editing import ffmpeg_progress_seconds, ffmpeg_startupinfo, get_media_duration, write_timeline_video


WATERMARK_WIDTH_PERCENT = 12
WATERMARK_HEIGHT_PERCENT = 12
WATERMARK_MARGIN_PERCENT = 3
WATERMARK_OPACITY_PERCENT = 55
REMOVAL_BOX_PERCENT = 15

REMOVAL_POSITIONS = [
    ("right_bottom", "أسفل اليمين"),
    ("left_bottom", "أسفل اليسار"),
    ("right_top", "أعلى اليمين"),
    ("left_top", "أعلى اليسار"),
]


@dataclass(frozen=True)
class WatermarkOptions:
    kind: str
    image_path: str = ""
    text: str = ""


@dataclass(frozen=True)
class WatermarkRemovalOptions:
    position: str = "right_bottom"


def ffmpeg_binary():
    try:
        from video_maker.app_paths import ffmpeg_binary

        value = ffmpeg_binary()
        if value:
            return value
    except Exception:
        pass
    return os.environ.get("IMAGEIO_FFMPEG_EXE") or shutil.which("ffmpeg") or "ffmpeg"


def _cancelled(cancelled_callback):
    return bool(cancelled_callback and cancelled_callback())


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def run_ffmpeg_with_progress(command, input_path, output_path, error_message, progress_callback=None, cancelled_callback=None, total_duration=None):
    from video_maker.audio_effects import AudioEffectPreparationCancelled
    if _cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    duration = max(0.001, float(total_duration)) if total_duration else max(0.001, float(get_media_duration(input_path)))
    stderr_file = tempfile.TemporaryFile(mode="w+b")
    progress_command = [
        command[0],
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *command[1:],
    ]
    process = subprocess.Popen(
        progress_command,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    last_percent = -1
    stderr_text = ""
    try:
        if progress_callback:
            progress_callback(0)
        if process.stdout:
            for line in process.stdout:
                if _cancelled(cancelled_callback):
                    _terminate(process)
                    break
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                seconds = ffmpeg_progress_seconds(key, value)
                if seconds is not None:
                    percent = max(0, min(99, int(seconds * 100 / duration)))
                    if progress_callback and percent != last_percent:
                        last_percent = percent
                        progress_callback(percent)
                elif key == "progress" and value == "end" and progress_callback:
                    progress_callback(100)
        if _cancelled(cancelled_callback):
            _terminate(process)
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            raise AudioEffectPreparationCancelled()
        return_code = process.wait() if process.poll() is None else process.poll()
        stderr_file.seek(0)
        stderr_text = stderr_file.read().decode("utf-8", errors="ignore")
    finally:
        if process.poll() is None:
            _terminate(process)
        try:
            if process.stdout:
                process.stdout.close()
        except Exception:
            pass
        stderr_file.close()
    if return_code != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        try:
            from video_maker.problem_log import trace_event
            trace_event(
                "ffmpeg",
                "failed",
                level="ERROR",
                immediate=True,
                return_code=return_code,
                command=" ".join(str(part) for part in command),
                output_exists=bool(os.path.exists(output_path)),
                output_size=os.path.getsize(output_path) if os.path.exists(output_path) else 0,
                stderr_head=stderr_text.strip()[:2000],
            )
        except Exception:
            pass
        raise RuntimeError(stderr_text.strip() or error_message)
    if progress_callback:
        progress_callback(100)


def _font_candidates():
    return [
        str(bundled_path("assets", "fonts", "arabic", "NotoSansArabic.ttf")),
        str(bundled_path("assets", "fonts", "arabic", "Cairo.ttf")),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "tahomabd.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]


def _watermark_font(size=150):
    for path in _font_candidates():
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _shape_text(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def render_text_watermark(text, output_path):
    clean_text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split()).strip()
    if not clean_text:
        raise ValueError(tr("اكتب نص العلامة المائية أولا"))
    font = _watermark_font(150)
    shaped = _shape_text(clean_text)
    probe = Image.new("RGBA", (2400, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    try:
        box = draw.textbbox((0, 0), shaped, font=font, stroke_width=2)
    except TypeError:
        box = draw.textbbox((0, 0), shaped, font=font)
    text_width = max(1, box[2] - box[0])
    text_height = max(1, box[3] - box[1])
    padding_x = max(24, int(text_height * 0.45))
    padding_y = max(18, int(text_height * 0.28))
    width = text_width + padding_x * 2
    height = text_height + padding_y * 2
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(12, int(height * 0.16))
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=(0, 0, 0, 170))
    draw.text(
        (padding_x - box[0], padding_y - box[1]),
        shaped,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 230),
    )
    image.save(output_path, "PNG")


def validate_image(path):
    if not path or not os.path.isfile(path):
        raise ValueError(tr("اختر صورة العلامة المائية أولا"))
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as error:
        raise ValueError(tr("ملف صورة العلامة المائية غير صالح")) from error


def prepare_watermark_image(options, temp_dir):
    if options.kind == "image":
        validate_image(options.image_path)
        return options.image_path
    output_path = os.path.join(temp_dir, "text_watermark.png")
    render_text_watermark(options.text, output_path)
    return output_path


def add_watermark_filter(rectangle):
    width = WATERMARK_WIDTH_PERCENT
    height = WATERMARK_HEIGHT_PERCENT
    margin = WATERMARK_MARGIN_PERCENT
    opacity = WATERMARK_OPACITY_PERCENT / 100.0
    patch_x, patch_y, patch_width, patch_height = rectangle
    return (
        "[0:v]split=3[base][ref][original];"
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity:.2f}[wm0];"
        f"[wm0][ref]scale=w=rw*{width}/100:h=rh*{height}/100:"
        "force_original_aspect_ratio=decrease[wm];"
        f"[base][wm]overlay=x=main_w-overlay_w-main_w*{margin}/100:"
        f"y=main_h-overlay_h-main_h*{margin}/100:format=auto:shortest=1[v];"
        f"[original]crop=w={patch_width}:h={patch_height}:x={patch_x}:y={patch_y}[patch]"
    )


def _overlay_command(input_path, watermark_path, output_path, rectangle, copy_audio):
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-loop",
        "1",
        "-i",
        watermark_path,
        "-filter_complex",
        add_watermark_filter(rectangle),
        "-map",
        "[v]",
        "-map",
        "[patch]",
        "-map",
        "0:a?",
        "-c:v:0",
        "libx264",
        "-preset:v:0",
        "slow",
        "-crf:v:0",
        "16",
        "-pix_fmt:v:0",
        "yuv420p",
        "-c:v:1",
        "libx264",
        "-preset:v:1",
        "medium",
        "-crf:v:1",
        "0",
        "-pix_fmt:v:1",
        "yuv420p",
        "-metadata:s:v:1",
        "handler_name=Video Maker watermark restoration patch",
        "-disposition:v:0",
        "default",
        "-disposition:v:1",
        "0",
    ]
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", "320k"])
    command.extend(["-movflags", "+faststart", "-shortest", output_path])
    return command


def apply_watermark(input_path, watermark_path, output_path, progress_callback=None, cancelled_callback=None):
    width, height = _video_dimensions(input_path)
    rectangle = removal_rectangle(width, height, "right_bottom")
    try:
        run_ffmpeg_with_progress(
            _overlay_command(input_path, watermark_path, output_path, rectangle, True),
            input_path,
            output_path,
            tr("تعذر إضافة العلامة المائية"),
            progress_callback,
            cancelled_callback,
        )
    except AudioEffectPreparationCancelled:
        raise
    except RuntimeError:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        run_ffmpeg_with_progress(
            _overlay_command(input_path, watermark_path, output_path, rectangle, False),
            input_path,
            output_path,
            tr("تعذر إضافة العلامة المائية"),
            progress_callback,
            cancelled_callback,
        )

def build_watermarked_segment(timeline, options, progress_callback=None, cancelled_callback=None):
    temp_dir = tempfile.mkdtemp(prefix="watermark_add_")
    selected_path = os.path.join(temp_dir, "source.mp4")
    output_path = os.path.join(temp_dir, "watermarked.mp4")
    try:
        watermark_path = prepare_watermark_image(options, temp_dir)
        write_timeline_video(
            list(timeline),
            selected_path,
            lambda percent: progress_callback(percent * 0.4) if progress_callback else None,
            cancelled_callback,
        )
        apply_watermark(
            selected_path,
            watermark_path,
            output_path,
            lambda percent: progress_callback(40 + percent * 0.6) if progress_callback else None,
            cancelled_callback,
        )
        return output_path, temp_dir, get_media_duration(output_path)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _video_dimensions(path):
    from video_maker.video_editing import ffmpeg_parse_infos

    info = ffmpeg_parse_infos(path, check_duration=False)
    size = info.get("video_size")
    if not size or len(size) != 2:
        raise RuntimeError(tr("تعذر تحديد أبعاد الفيديو الحالية"))
    return int(size[0]), int(size[1])


def _even_floor(value, minimum=2):
    number = max(minimum, int(value))
    return number - number % 2


def removal_rectangle(width, height, position):
    box_w = _even_floor(round(width * REMOVAL_BOX_PERCENT / 100), 8)
    box_h = _even_floor(round(height * REMOVAL_BOX_PERCENT / 100), 8)
    margin_x = _even_floor(round(width * WATERMARK_MARGIN_PERCENT / 100), 2)
    margin_y = _even_floor(round(height * WATERMARK_MARGIN_PERCENT / 100), 2)
    box_w = _even_floor(min(box_w, width - margin_x - 2), 8)
    box_h = _even_floor(min(box_h, height - margin_y - 2), 8)
    if position.startswith("right_"):
        x = _even_floor(width - margin_x - box_w, 2)
    else:
        x = margin_x
    top = position.endswith("top")
    y = margin_y if top else _even_floor(height - margin_y - box_h, 2)
    return max(2, x), max(2, y), max(8, box_w), max(8, box_h)

def _remove_command(input_path, output_path, rectangle, copy_audio):
    x, y, width, height = rectangle
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-filter_complex",
        f"[0:v]delogo=x={x}:y={y}:w={width}:h={height}:show=0[v]",
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
    ]
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", "320k"])
    command.extend(["-movflags", "+faststart", output_path])
    return command


def _has_restoration_patch(input_path):
    result = subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-i", input_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
    )
    message = result.stderr.decode("utf-8", errors="ignore")
    return "Video Maker watermark restoration patch" in message


def _restore_patch_command(input_path, output_path, rectangle, copy_audio):
    x, y, _width, _height = rectangle
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-filter_complex",
        f"[0:v:0][0:v:1]overlay=x={x}:y={y}:format=auto:shortest=1[v]",
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
    ]
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", "320k"])
    command.extend(["-movflags", "+faststart", "-shortest", output_path])
    return command


def _run_audio_fallback(command_builder, input_path, output_path, rectangle, error_message, progress_callback, cancelled_callback):
    try:
        run_ffmpeg_with_progress(
            command_builder(input_path, output_path, rectangle, True),
            input_path,
            output_path,
            error_message,
            progress_callback,
            cancelled_callback,
        )
    except AudioEffectPreparationCancelled:
        raise
    except RuntimeError:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        run_ffmpeg_with_progress(
            command_builder(input_path, output_path, rectangle, False),
            input_path,
            output_path,
            error_message,
            progress_callback,
            cancelled_callback,
        )


def apply_watermark_removal(input_path, output_path, options, progress_callback=None, cancelled_callback=None):
    width, height = _video_dimensions(input_path)
    rectangle = removal_rectangle(width, height, options.position)
    error_message = tr("تعذر إزالة العلامة المائية")
    if options.position == "right_bottom" and _has_restoration_patch(input_path):
        _run_audio_fallback(
            _restore_patch_command,
            input_path,
            output_path,
            rectangle,
            error_message,
            progress_callback,
            cancelled_callback,
        )
        return
    _run_audio_fallback(
        _remove_command,
        input_path,
        output_path,
        rectangle,
        error_message,
        progress_callback,
        cancelled_callback,
    )

def build_watermark_removed_segment(timeline, options, progress_callback=None, cancelled_callback=None):
    temp_dir = tempfile.mkdtemp(prefix="watermark_remove_")
    selected_path = os.path.join(temp_dir, "source.mp4")
    output_path = os.path.join(temp_dir, "watermark_removed.mp4")
    try:
        write_timeline_video(
            list(timeline),
            selected_path,
            lambda percent: progress_callback(percent * 0.4) if progress_callback else None,
            cancelled_callback,
        )
        apply_watermark_removal(
            selected_path,
            output_path,
            options,
            lambda percent: progress_callback(40 + percent * 0.6) if progress_callback else None,
            cancelled_callback,
        )
        return output_path, temp_dir, get_media_duration(output_path)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def preserve_watermark_restoration_patch(saved_path, source_path):
    """Keep the reversible patch after a normal full-video save or resize."""
    if not saved_path or not source_path or not os.path.exists(saved_path) or not os.path.exists(source_path):
        return False
    if _has_restoration_patch(saved_path):
        return True
    if not _has_restoration_patch(source_path):
        return False
    width, height = _video_dimensions(saved_path)
    _x, _y, patch_width, patch_height = removal_rectangle(width, height, "right_bottom")
    duration = max(0.05, get_media_duration(saved_path))
    folder = os.path.dirname(os.path.abspath(saved_path))
    suffix = os.path.splitext(saved_path)[1] or ".mp4"
    handle, temp_path = tempfile.mkstemp(prefix="watermark_patch_", suffix=suffix, dir=folder)
    os.close(handle)
    try:
        command = [
            ffmpeg_binary(),
            "-y",
            "-i",
            saved_path,
            "-i",
            source_path,
            "-filter_complex",
            f"[1:v:1]scale=w={patch_width}:h={patch_height}[patch]",
            "-map",
            "0:v:0",
            "-map",
            "[patch]",
            "-map",
            "0:a?",
            "-map_metadata",
            "0",
            "-c:v:0",
            "copy",
            "-c:v:1",
            "libx264",
            "-preset:v:1",
            "medium",
            "-crf:v:1",
            "0",
            "-pix_fmt:v:1",
            "yuv420p",
            "-metadata:s:v:1",
            "handler_name=Video Maker watermark restoration patch",
            "-disposition:v:0",
            "default",
            "-disposition:v:1",
            "0",
            "-c:a",
            "copy",
            "-t",
            f"{duration:.6f}",
            "-movflags",
            "+faststart",
            temp_path,
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
        )
        if result.returncode != 0 or not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            return False
        os.replace(temp_path, saved_path)
        return _has_restoration_patch(saved_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _set_control_name(control, text):
    value = tr(text)
    control.SetName(value)
    if hasattr(control, "SetAccessibleName"):
        control.SetAccessibleName(value)


class AddWatermarkDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=tr("إضافة علامة مائية"), size=(610, 430))
        self.parent = parent
        self.options = None
        self.image_path = ""
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.kind_box = wx.RadioBox(
            panel,
            label=tr("نوع العلامة المائية"),
            choices=[tr("صورة"), tr("نص")],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
        )
        _set_control_name(self.kind_box, "اختيار نوع العلامة المائية صورة أو نص")
        self.kind_box.SetSelection(0)
        main_sizer.Add(self.kind_box, flag=wx.EXPAND | wx.ALL, border=12)

        self.image_panel = wx.Panel(panel)
        image_sizer = wx.BoxSizer(wx.HORIZONTAL)
        image_label = wx.StaticText(self.image_panel, label=tr("صورة العلامة المائية"))
        self.image_text = wx.TextCtrl(self.image_panel, style=wx.TE_READONLY)
        self.browse_button = wx.Button(self.image_panel, label=tr("اختيار صورة العلامة المائية"))
        _set_control_name(self.image_text, "مسار صورة العلامة المائية")
        _set_control_name(self.browse_button, "اختيار صورة العلامة المائية")
        image_sizer.Add(image_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        image_sizer.Add(self.image_text, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=8)
        image_sizer.Add(self.browse_button)
        self.image_panel.SetSizer(image_sizer)
        main_sizer.Add(self.image_panel, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)

        self.text_panel = wx.Panel(panel)
        text_sizer = wx.BoxSizer(wx.VERTICAL)
        text_label = wx.StaticText(self.text_panel, label=tr("نص العلامة المائية"))
        self.text_ctrl = wx.TextCtrl(self.text_panel, style=wx.TE_PROCESS_ENTER)
        self.text_ctrl.SetMaxLength(80)
        _set_control_name(self.text_ctrl, "اكتب نص العلامة المائية")
        text_sizer.Add(text_label, flag=wx.BOTTOM, border=5)
        text_sizer.Add(self.text_ctrl, flag=wx.EXPAND)
        self.text_panel.SetSizer(text_sizer)
        main_sizer.Add(self.text_panel, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)

        description = tr(
            "ستظهر العلامة أسفل اليمين بحجم وشفافية تلقائيين من بداية الفيديو إلى نهايته"
        )
        self.description = wx.StaticText(panel, label=description)
        _set_control_name(self.description, description)
        main_sizer.Add(self.description, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)

        buttons = wx.StdDialogButtonSizer()
        add_button = wx.Button(panel, wx.ID_OK, tr("إضافة العلامة المائية"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        _set_control_name(add_button, "تنفيذ إضافة العلامة المائية على كامل الفيديو")
        _set_control_name(cancel_button, "إلغاء")
        add_button.SetDefault()
        buttons.AddButton(add_button)
        buttons.AddButton(cancel_button)
        buttons.Realize()
        main_sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.ALL, border=12)

        panel.SetSizer(main_sizer)
        self.kind_box.Bind(wx.EVT_RADIOBOX, self.on_kind_changed)
        self.browse_button.Bind(wx.EVT_BUTTON, self.choose_image)
        add_button.Bind(wx.EVT_BUTTON, self.accept)
        bind_dialog_keys(self, self.on_char_hook)
        self.on_kind_changed()
        self.CentreOnParent()
        wx.CallAfter(self.kind_box.SetFocus)

    def on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def on_kind_changed(self, event=None):
        is_image = self.kind_box.GetSelection() == 0
        self.image_panel.Show(is_image)
        self.text_panel.Show(not is_image)
        self.Layout()

    def choose_image(self, event=None):
        with wx.FileDialog(
            self,
            tr("اختيار صورة العلامة المائية"),
            wildcard=IMAGE_WILDCARD,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dialog:
            prepare_media_file_dialog(dialog, "image", "watermark_image")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.image_path = dialog.GetPath()
            remember_media_path(self.image_path, "image", "watermark_image")
        self.image_text.SetValue(self.image_path)
        _set_control_name(self.image_text, f"{tr('مسار صورة العلامة المائية')} {os.path.basename(self.image_path)}")

    def accept(self, event=None):
        try:
            if self.kind_box.GetSelection() == 0:
                validate_image(self.image_path)
                self.options = WatermarkOptions("image", image_path=self.image_path)
            else:
                text = self.text_ctrl.GetValue().strip()
                if not text:
                    raise ValueError(tr("اكتب نص العلامة المائية أولا"))
                self.options = WatermarkOptions("text", text=text)
        except ValueError as error:
            wx.MessageBox(str(error), tr("بيانات ناقصة"), wx.OK | wx.ICON_INFORMATION)
            return
        self.EndModal(wx.ID_OK)


class RemoveWatermarkDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=tr("إزالة علامة مائية"), size=(570, 300))
        self.options = None
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        explanation = tr(
            "سيتم معالجة منطقة العلامة طوال الفيديو. أفضل نتيجة تكون للعلامة القياسية التي أضافها البرنامج"
        )
        info = wx.StaticText(panel, label=explanation)
        _set_control_name(info, explanation)
        main_sizer.Add(info, flag=wx.EXPAND | wx.ALL, border=12)

        label = wx.StaticText(panel, label=tr("مكان العلامة الموجودة"))
        self.position_choice = wx.Choice(panel, choices=[tr(label) for _key, label in REMOVAL_POSITIONS])
        self.position_choice.SetSelection(0)
        _set_control_name(self.position_choice, "اختيار مكان العلامة الموجودة")
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        row.Add(self.position_choice, proportion=1, flag=wx.EXPAND)
        main_sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)

        buttons = wx.StdDialogButtonSizer()
        remove_button = wx.Button(panel, wx.ID_OK, tr("إزالة العلامة المائية"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        _set_control_name(remove_button, "تنفيذ إزالة العلامة المائية على كامل الفيديو")
        _set_control_name(cancel_button, "إلغاء")
        remove_button.SetDefault()
        buttons.AddButton(remove_button)
        buttons.AddButton(cancel_button)
        buttons.Realize()
        main_sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.ALL, border=12)

        panel.SetSizer(main_sizer)
        remove_button.Bind(wx.EVT_BUTTON, self.accept)
        bind_dialog_keys(self, self.on_char_hook)
        self.CentreOnParent()
        wx.CallAfter(self.position_choice.SetFocus)

    def on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def accept(self, event=None):
        index = max(0, self.position_choice.GetSelection())
        self.options = WatermarkRemovalOptions(REMOVAL_POSITIONS[index][0])
        self.EndModal(wx.ID_OK)
