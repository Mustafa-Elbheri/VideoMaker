import os
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass

import wx
from video_maker.app_paths import ffmpeg_binary

from video_maker.audio_effects import AudioEffectPreparationCancelled
from video_maker.dialogs import IMAGE_WILDCARD, prepare_media_file_dialog, remember_media_path
from video_maker.localization import tr
from video_maker.problem_log import trace_event
from video_maker.timeline import TimelineSegment, delete_range, insert_segments, slice_segments
from video_maker.logical_files import replacement_segments_preserving_files
from video_maker.video_editing import ffmpeg_startupinfo, write_timeline_video


POSITIONS = [
    ("right_top", "في اليمين أعلى"),
    ("left_top", "في اليسار أعلى"),
    ("center_top", "في المنتصف أعلى"),
    ("right_bottom", "في اليمين أسفل"),
    ("center_bottom", "في المنتصف أسفل"),
    ("left_bottom", "في اليسار أسفل"),
]

IMAGE_SIZE_MODES = [
    ("full", "حجم الشاشة الكاملة"),
    ("custom", "مخصص"),
]


@dataclass(frozen=True)
class ImageOverlayOptions:
    image_path: str
    full_screen: bool
    position: str
    width_percent: int
    height_percent: int


def overlay_position_expression(position):
    expressions = {
        "right_top": ("main_w-overlay_w", "0"),
        "left_top": ("0", "0"),
        "center_top": ("(main_w-overlay_w)/2", "0"),
        "right_bottom": ("main_w-overlay_w", "main_h-overlay_h"),
        "center_bottom": ("(main_w-overlay_w)/2", "main_h-overlay_h"),
        "left_bottom": ("0", "main_h-overlay_h"),
    }
    return expressions.get(position, expressions["center_top"])


def overlay_filter(options):
    if options.full_screen:
        scale = "w=rw:h=rh:force_original_aspect_ratio=decrease"
        x, y = "(main_w-overlay_w)/2", "(main_h-overlay_h)/2"
    else:
        width = max(1, min(100, int(options.width_percent)))
        height = max(1, min(100, int(options.height_percent)))
        scale = f"w=rw*{width}/100:h=rh*{height}/100:force_original_aspect_ratio=decrease"
        x, y = overlay_position_expression(options.position)
    return (
        f"[0:v]split[base][ref];"
        f"[1:v]format=rgba[img0];"
        f"[img0][ref]scale={scale}[img];"
        f"[base][img]overlay={x}:{y}:format=auto:shortest=1[v]"
    )


def _cancelled(cancelled_callback):
    return bool(cancelled_callback and cancelled_callback())


def _terminate_process(process):
    if not process or process.poll() is not None:
        trace_event("image_overlay", "process_termination.skipped", process_exists=bool(process), returncode=getattr(process, "returncode", None))
        return
    trace_event("image_overlay", "process_termination.start", level="WARNING", immediate=True, pid=getattr(process, "pid", None))
    try:
        process.terminate()
        process.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=2)
    except Exception as error:
        trace_event("image_overlay", "process_termination.wait_after_kill_failed", level="ERROR", error_type=type(error).__name__, error=str(error))
    trace_event("image_overlay", "process_termination.complete", level="WARNING", immediate=True, pid=getattr(process, "pid", None), returncode=getattr(process, "returncode", None))


def _simple_source_segment(segment):
    return (
        abs(float(getattr(segment, "speed", 1.0) or 1.0) - 1.0) <= 0.0001
        and abs(float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0) - 1.0) <= 0.0001
        and not str(getattr(segment, "audio_path", "") or "")
    )


def run_overlay_command(
    selected_path,
    output_path,
    options,
    copy_audio,
    progress_callback=None,
    cancelled_callback=None,
    source_start=0.0,
    source_duration=None,
):
    trace_event(
        "image_overlay",
        "ffmpeg_prepare",
        selected_path=selected_path,
        output_path=output_path,
        image_path=getattr(options, "image_path", ""),
        copy_audio=copy_audio,
        source_start=source_start,
        source_duration=source_duration,
        full_screen=getattr(options, "full_screen", None),
        position=getattr(options, "position", None),
        width_percent=getattr(options, "width_percent", None),
        height_percent=getattr(options, "height_percent", None),
    )
    if _cancelled(cancelled_callback):
        trace_event("image_overlay", "cancelled_before_ffmpeg", level="WARNING", immediate=True)
        raise AudioEffectPreparationCancelled()
    duration = max(0.001, float(source_duration or 0.0))
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    if source_start and source_start > 0.001:
        command.extend(["-ss", f"{float(source_start):.6f}"])
    if source_duration is not None:
        command.extend(["-t", f"{duration:.6f}"])
    command.extend([
        "-i",
        selected_path,
        "-loop",
        "1",
        "-i",
        options.image_path,
        "-filter_complex",
        overlay_filter(options),
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
    ])
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", "320k"])
    if source_duration is not None:
        command.extend(["-t", f"{duration:.6f}"])
    command.extend(["-movflags", "+faststart", "-shortest", output_path])

    trace_event("image_overlay", "ffmpeg_command_ready", command=command, duration=duration)
    stderr_file = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            stdin=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
        )
    except Exception as error:
        stderr_file.close()
        trace_event(
            "image_overlay",
            "ffmpeg_start_failed",
            level="ERROR",
            immediate=True,
            error_type=type(error).__name__,
            error=str(error),
            command=command,
        )
        raise
    trace_event("image_overlay", "ffmpeg_started", pid=getattr(process, "pid", None), duration=duration, copy_audio=copy_audio)
    progress_lines = queue.Queue()
    reader_finished = threading.Event()

    def read_progress():
        trace_event("image_overlay", "progress_reader.start", pid=getattr(process, "pid", None))
        line_count = 0
        try:
            if process.stdout:
                for line in process.stdout:
                    line_count += 1
                    progress_lines.put(line)
        except Exception as error:
            trace_event("image_overlay", "progress_reader.error", level="ERROR", error_type=type(error).__name__, error=str(error), lines=line_count)
        finally:
            reader_finished.set()
            trace_event("image_overlay", "progress_reader.complete", pid=getattr(process, "pid", None), lines=line_count)

    reader = threading.Thread(target=read_progress, daemon=True)
    reader.start()
    last_percent = -1
    cancelled = False
    try:
        if progress_callback:
            trace_event("image_overlay", "progress", percent=0, pid=getattr(process, "pid", None))
            progress_callback(0)
        while True:
            if _cancelled(cancelled_callback):
                cancelled = True
                trace_event("image_overlay", "cancel_detected", level="WARNING", immediate=True, pid=getattr(process, "pid", None), last_percent=last_percent)
                _terminate_process(process)
                break
            try:
                line = progress_lines.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and reader_finished.is_set():
                    break
                continue
            key, separator, value = line.strip().partition("=")
            if not separator:
                continue
            if key in ("out_time_us", "out_time_ms") and duration > 0:
                try:
                    seconds = int(value) / 1_000_000.0
                except ValueError:
                    continue
                percent = max(0, min(99, int(seconds * 100 / duration)))
                if percent != last_percent:
                    previous_percent = last_percent
                    last_percent = percent
                    # Keep the UI smooth by reporting every actual percentage
                    # to the dialog, but persist only useful 10% diagnostic
                    # milestones instead of one log record per percent.
                    if percent == 0 or percent >= 99 or percent // 10 != max(0, previous_percent) // 10:
                        trace_event("image_overlay", "progress", percent=percent, pid=getattr(process, "pid", None))
                    if progress_callback:
                        progress_callback(percent)
            elif key == "progress" and value == "end":
                # Report 100 only after the process and output file have been
                # validated, so a failed copy-audio attempt cannot announce a
                # completed operation before the fallback starts.
                continue
        return_code = process.wait() if process.poll() is None else process.poll()
        trace_event("image_overlay", "ffmpeg_process_exited", pid=getattr(process, "pid", None), returncode=return_code, last_percent=last_percent)
    finally:
        if process.poll() is None:
            _terminate_process(process)
        try:
            if process.stdout:
                process.stdout.close()
        except Exception:
            pass

    stderr_file.seek(0)
    stderr_text = stderr_file.read().decode("utf-8", errors="ignore")
    stderr_file.close()
    output_exists = os.path.exists(output_path)
    output_size = os.path.getsize(output_path) if output_exists else 0
    trace_event(
        "image_overlay",
        "ffmpeg_result_checked",
        level="ERROR" if return_code != 0 else "INFO",
        immediate=return_code != 0,
        returncode=return_code,
        output_exists=output_exists,
        output_size=output_size,
        stderr=stderr_text,
        cancelled=cancelled,
    )
    if cancelled or _cancelled(cancelled_callback):
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        trace_event("image_overlay", "cancel_complete", level="WARNING", immediate=True, output_removed=not os.path.exists(output_path))
        raise AudioEffectPreparationCancelled()
    if return_code != 0 or not output_exists or output_size == 0:
        trace_event("image_overlay", "ffmpeg_failed", level="ERROR", immediate=True, returncode=return_code, stderr=stderr_text, output_exists=output_exists, output_size=output_size)
        raise RuntimeError(stderr_text.strip() or "تعذر إدراج الصورة")
    trace_event("image_overlay", "progress", percent=100, pid=getattr(process, "pid", None))
    if progress_callback:
        progress_callback(100)
    trace_event("image_overlay", "ffmpeg_complete", output_path=output_path, output_size=output_size, copy_audio=copy_audio)


def apply_image_overlay(
    selected_path,
    output_path,
    options,
    progress_callback=None,
    cancelled_callback=None,
    source_start=0.0,
    source_duration=None,
):
    trace_event("image_overlay", "apply.start", selected_path=selected_path, output_path=output_path, source_start=source_start, source_duration=source_duration)
    try:
        run_overlay_command(
            selected_path,
            output_path,
            options,
            True,
            progress_callback,
            cancelled_callback,
            source_start,
            source_duration,
        )
    except AudioEffectPreparationCancelled:
        trace_event("image_overlay", "apply.cancelled", level="WARNING", immediate=True)
        raise
    except RuntimeError as error:
        trace_event("image_overlay", "audio_copy_fallback", level="WARNING", error=str(error), output_path=output_path)
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        run_overlay_command(
            selected_path,
            output_path,
            options,
            False,
            progress_callback,
            cancelled_callback,
            source_start,
            source_duration,
        )
    trace_event("image_overlay", "apply.complete", output_path=output_path, output_size=os.path.getsize(output_path) if os.path.exists(output_path) else 0)


def build_image_overlay_segment(
    timeline,
    start_time,
    end_time,
    options,
    progress_callback=None,
    cancelled_callback=None,
):
    trace_event(
        "image_overlay",
        "build.start",
        start_time=start_time,
        end_time=end_time,
        selected_duration=max(0.0, float(end_time) - float(start_time)),
        timeline_items=len(timeline),
        image_path=getattr(options, "image_path", ""),
    )
    temp_dir = tempfile.mkdtemp(prefix="image_overlay_")
    trace_event("image_overlay", "temp_directory.created", path=temp_dir)
    selected_path = os.path.join(temp_dir, "selected.mp4")
    output_path = os.path.join(temp_dir, "image_overlay.mp4")
    try:
        if _cancelled(cancelled_callback):
            trace_event("image_overlay", "build.cancelled_before_slice", level="WARNING", immediate=True)
            raise AudioEffectPreparationCancelled()
        selected_segments = slice_segments(timeline, start_time, end_time)
        trace_event("image_overlay", "range.sliced", segment_count=len(selected_segments), segments=selected_segments)
        if not selected_segments:
            raise RuntimeError("تعذر تحديد الجزء المطلوب لإدراج الصورة")

        # Most image inserts cover a short range inside one normal source file.
        # In that common case FFmpeg can seek directly to the selected range,
        # avoiding an unnecessary complete temporary render before the overlay.
        if len(selected_segments) == 1 and _simple_source_segment(selected_segments[0]):
            segment = selected_segments[0]
            trace_event("image_overlay", "path.fast_direct_source", source_path=segment.path, source_start=segment.start, duration=segment.duration)
            apply_image_overlay(
                segment.path,
                output_path,
                options,
                progress_callback,
                cancelled_callback,
                source_start=segment.start,
                source_duration=segment.duration,
            )
            trace_event("image_overlay", "build.complete", path="fast", output_path=output_path, temp_dir=temp_dir)
            return output_path, temp_dir

        trace_event("image_overlay", "path.composite_timeline_render", segment_count=len(selected_segments), selected_path=selected_path)

        def preparation_progress(percent):
            if progress_callback:
                progress_callback(float(percent) * 0.4)

        try:
            write_timeline_video(
                selected_segments,
                selected_path,
                progress_callback=preparation_progress,
                cancelled_callback=cancelled_callback,
            )
        except Exception:
            if _cancelled(cancelled_callback):
                raise AudioEffectPreparationCancelled()
            raise
        if _cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        selected_duration = max(0.001, end_time - start_time)
        apply_image_overlay(
            selected_path,
            output_path,
            options,
            lambda percent: progress_callback(40 + float(percent) * 0.6) if progress_callback else None,
            cancelled_callback,
            source_duration=selected_duration,
        )
        trace_event("image_overlay", "build.complete", path="composite", output_path=output_path, temp_dir=temp_dir)
        return output_path, temp_dir
    except Exception as error:
        trace_event(
            "image_overlay",
            "build.error",
            level="ERROR",
            immediate=True,
            error_type=type(error).__name__,
            error=str(error),
            temp_dir=temp_dir,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def replace_image_overlay_range(timeline, start_time, end_time, overlay_path):
    duration = end_time - start_time
    overlay_segments = replacement_segments_preserving_files(
        timeline, start_time, end_time, overlay_path, duration
    )
    remaining = delete_range(timeline, start_time, end_time)
    return insert_segments(remaining, start_time, overlay_segments)


class ImageOverlayDialog(wx.Dialog):
    def __init__(self, parent, title="إدراج صورة", apply_label="إدراج", apply_name="إدراج الصورة"):
        super().__init__(parent, title=title, size=(560, 430))
        self.parent = parent
        self.options = None
        self.choice_last_selection = {}
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.image_path = ""
        self.image_text = wx.TextCtrl(panel, value="", style=wx.TE_READONLY)
        self.image_text.SetName(tr("مسار الصورة"))
        browse_button = wx.Button(panel, label="اختيار صورة")
        browse_button.SetName(tr("اختيار صورة"))
        image_row = wx.BoxSizer(wx.HORIZONTAL)
        image_row.Add(self.image_text, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=8)
        image_row.Add(browse_button)
        self.mode_choice = wx.Choice(panel, choices=[label for key, label in IMAGE_SIZE_MODES])
        self.mode_choice.SetSelection(0)
        self.mode_choice.SetName(tr("اختيار حجم الصورة"))
        self.position_choice = wx.Choice(panel, choices=[label for key, label in POSITIONS])
        self.position_choice.SetSelection(0)
        self.position_choice.SetName(tr("اختيار مكان الصورة على الفيديو"))
        self.width_slider = wx.Slider(panel, value=35, minValue=1, maxValue=100, style=wx.SL_HORIZONTAL)
        self.height_slider = wx.Slider(panel, value=35, minValue=1, maxValue=100, style=wx.SL_HORIZONTAL)
        self.width_slider.SetLineSize(1)
        self.height_slider.SetLineSize(1)
        self.width_slider.SetPageSize(10)
        self.height_slider.SetPageSize(10)
        self.width_slider.SetName(tr("عرض الصورة"))
        self.height_slider.SetName(tr("ارتفاع الصورة"))
        self.status = wx.StaticText(panel, label="")
        self.status.SetName(tr("حالة أبعاد الصورة"))
        self.add_labeled_row(main_sizer, panel, "الصورة", image_row)
        self.add_control_row(main_sizer, panel, "اختيار حجم الصورة", self.mode_choice)
        self.add_control_row(main_sizer, panel, "اختيار مكان الصورة", self.position_choice)
        self.add_control_row(main_sizer, panel, "العرض", self.width_slider)
        self.add_control_row(main_sizer, panel, "الارتفاع", self.height_slider)
        main_sizer.Add(self.status, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        insert_button = wx.Button(panel, label=apply_label)
        cancel_button = wx.Button(panel, label="إلغاء")
        insert_button.SetName(apply_name)
        cancel_button.SetName(tr("إلغاء"))
        insert_button.SetDefault()
        buttons.Add(insert_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        main_sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=8)
        panel.SetSizer(main_sizer)
        browse_button.Bind(wx.EVT_BUTTON, self.choose_image)
        self.mode_choice.Bind(wx.EVT_CHOICE, self.on_mode_changed)
        self.position_choice.Bind(wx.EVT_CHOICE, self.on_choice_changed)
        for choice in (self.mode_choice, self.position_choice):
            choice.Bind(wx.EVT_SET_FOCUS, self.on_choice_focus)
            choice.Bind(wx.EVT_KEY_DOWN, self.on_choice_key)
        for slider in (self.width_slider, self.height_slider):
            slider.Bind(wx.EVT_SLIDER, self.update_status)
            slider.Bind(wx.EVT_KEY_DOWN, self.on_slider_key)
            slider.Bind(wx.EVT_SET_FOCUS, self.on_slider_focus)
        insert_button.Bind(wx.EVT_BUTTON, self.accept)
        cancel_button.Bind(wx.EVT_BUTTON, self.close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.reset_choice_tracking()
        self.on_mode_changed()
        self.Centre()
        wx.CallAfter(browse_button.SetFocus)

    def add_labeled_row(self, main_sizer, panel, label_text, row_sizer):
        label = wx.StaticText(panel, label=label_text)
        label.SetName(label_text)
        main_sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(row_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

    def add_control_row(self, main_sizer, panel, label_text, control):
        row = wx.BoxSizer(wx.HORIZONTAL)
        label = wx.StaticText(panel, label=label_text)
        label.SetName(label_text)
        row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        row.Add(control, proportion=1, flag=wx.EXPAND)
        main_sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

    def choose_image(self, event=None):
        with wx.FileDialog(self, "اختيار صورة", wildcard=IMAGE_WILDCARD, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, "image", "insert_image_overlay")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.image_path = dialog.GetPath()
            remember_media_path(self.image_path, "image", "insert_image_overlay")
        self.image_text.SetValue(self.image_path)
        self.image_text.SetName(tr("الصورة {name}", name=os.path.basename(self.image_path)))

    def on_mode_changed(self, event=None):
        custom = self.mode_choice.GetSelection() == 1
        self.position_choice.Enable(custom)
        self.width_slider.Enable(custom)
        self.height_slider.Enable(custom)
        self.update_status()
        if event is not None:
            self.announce_choice(self.mode_choice, force=True)

    def speak(self, message, wait_for_ui=True):
        if hasattr(self.parent, "say"):
            self.parent.say(message, wait_for_ui=wait_for_ui)

    def choice_control_name(self, choice):
        if choice is self.mode_choice:
            return "اختيار حجم الصورة"
        if choice is self.position_choice:
            return "اختيار مكان الصورة على الفيديو"
        return ""

    def choice_message(self, choice):
        if choice is self.mode_choice:
            selection = self.mode_choice.GetSelection()
            if selection == wx.NOT_FOUND or selection >= len(IMAGE_SIZE_MODES):
                selection = 0
            return IMAGE_SIZE_MODES[selection][1]
        if choice is self.position_choice:
            selection = self.position_choice.GetSelection()
            if selection == wx.NOT_FOUND or selection >= len(POSITIONS):
                selection = 0
            return POSITIONS[selection][1]
        return ""

    def reset_choice_tracking(self):
        for choice in (self.mode_choice, self.position_choice):
            self.choice_last_selection[choice] = choice.GetSelection()

    def on_choice_focus(self, event):
        name = self.choice_control_name(event.GetEventObject())
        if name:
            self.speak(name)
        event.Skip()

    def on_choice_changed(self, event):
        self.update_status()
        self.announce_choice(event.GetEventObject(), force=True)
        event.Skip()

    def on_choice_key(self, event):
        key = event.GetKeyCode()
        choice = event.GetEventObject()
        event.Skip()
        if key in (
            wx.WXK_UP,
            wx.WXK_DOWN,
            wx.WXK_LEFT,
            wx.WXK_RIGHT,
            wx.WXK_HOME,
            wx.WXK_END,
            wx.WXK_PAGEUP,
            wx.WXK_PAGEDOWN,
            wx.WXK_NUMPAD_UP,
            wx.WXK_NUMPAD_DOWN,
            wx.WXK_NUMPAD_LEFT,
            wx.WXK_NUMPAD_RIGHT,
            wx.WXK_NUMPAD_HOME,
            wx.WXK_NUMPAD_END,
            wx.WXK_NUMPAD_PAGEUP,
            wx.WXK_NUMPAD_PAGEDOWN,
        ):
            wx.CallAfter(self.announce_choice, choice)

    def announce_choice(self, choice, force=False, wait_for_ui=True):
        selection = choice.GetSelection()
        if not force and self.choice_last_selection.get(choice) == selection:
            return
        self.choice_last_selection[choice] = selection
        message = self.choice_message(choice)
        if message:
            self.speak(message)

    def coverage_percent(self):
        if self.mode_choice.GetSelection() == 0:
            return 100
        return max(1, min(100, round(self.width_slider.GetValue() * self.height_slider.GetValue() / 100)))

    def update_status(self, event=None):
        self.status.SetLabel("")
        self.status.SetName(tr("حالة أبعاد الصورة"))
        self.width_slider.SetName(tr("عرض الصورة"))
        self.height_slider.SetName(tr("ارتفاع الصورة"))

    def slider_message(self, slider):
        if slider is self.width_slider:
            return f"عرض الصورة {slider.GetValue()} بالمئة"
        return f"ارتفاع الصورة {slider.GetValue()} بالمئة"

    def on_slider_focus(self, event):
        self.speak(self.slider_message(event.GetEventObject()))
        event.Skip()

    def on_slider_key(self, event):
        key = event.GetKeyCode()
        slider = event.GetEventObject()
        if key in (wx.WXK_TAB, wx.WXK_ESCAPE):
            event.Skip()
            return
        if key in (wx.WXK_UP, wx.WXK_RIGHT, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_RIGHT):
            slider.SetValue(min(slider.GetMax(), slider.GetValue() + 1))
            self.update_status()
            self.speak(self.slider_message(slider), wait_for_ui=False)
            return
        if key in (wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_NUMPAD_DOWN, wx.WXK_NUMPAD_LEFT):
            slider.SetValue(max(slider.GetMin(), slider.GetValue() - 1))
            self.update_status()
            self.speak(self.slider_message(slider), wait_for_ui=False)
            return
        if key in (wx.WXK_PAGEUP, wx.WXK_NUMPAD_PAGEUP):
            slider.SetValue(min(slider.GetMax(), slider.GetValue() + 10))
            self.update_status()
            self.speak(self.slider_message(slider), wait_for_ui=False)
            return
        if key in (wx.WXK_PAGEDOWN, wx.WXK_NUMPAD_PAGEDOWN):
            slider.SetValue(max(slider.GetMin(), slider.GetValue() - 10))
            self.update_status()
            self.speak(self.slider_message(slider), wait_for_ui=False)
            return
        event.Skip()

    def selected_position(self):
        selection = self.position_choice.GetSelection()
        if selection == wx.NOT_FOUND:
            selection = 0
        return POSITIONS[selection][0]

    def accept(self, event=None):
        if not self.image_path:
            wx.MessageBox("اختر صورة أولا.", "بيانات ناقصة", wx.OK | wx.ICON_INFORMATION)
            return
        self.options = ImageOverlayOptions(
            image_path=self.image_path,
            full_screen=self.mode_choice.GetSelection() == 0,
            position=self.selected_position(),
            width_percent=self.width_slider.GetValue(),
            height_percent=self.height_slider.GetValue(),
        )
        self.finish_dialog(wx.ID_OK)

    def close(self, event=None):
        self.finish_dialog(wx.ID_CANCEL)

    def finish_dialog(self, result):
        if self.IsModal():
            self.EndModal(result)
        else:
            self.SetReturnCode(result)
            self.Hide()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.close()
            return
        event.Skip()
