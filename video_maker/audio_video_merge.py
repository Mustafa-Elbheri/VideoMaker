import os
import shutil
from dataclasses import dataclass

import wx

from video_maker.audio_image_merge import (
    TRANSITIONS,
    audio_wildcard,
    natural_sort_key,
    translated_transitions,
)
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.video_editing import get_media_duration, ffmpeg_parse_infos, ffmpeg_startupinfo
from video_maker.watermark import run_ffmpeg_with_progress, ffmpeg_binary
from video_maker.dialogs import prepare_media_file_dialog, remember_media_path, remember_media_paths
from video_maker.localization import tr, tr_format


def video_wildcard():
    return f"{tr('ملفات الفيديو')} (*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm)|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.webm"


@dataclass
class AudioVideoMergeOptions:
    audio: str
    videos: list
    transition: str


@dataclass
class VideoSection:
    index: int
    start: float
    source_duration: float
    transition_span: float


def safe_transition_span(transition, previous_duration, current_available):
    if transition != TRANSITIONS[1]:
        return 0.0
    return max(0.0, min(1.0, previous_duration / 2.0, current_available / 2.0))


def plan_video_sections(video_durations, audio_duration, transition):
    if audio_duration <= 0:
        raise ValueError(tr("مدة ملف الصوت غير صالحة."))

    valid_durations = [max(0.0, float(duration or 0.0)) for duration in video_durations]
    valid_durations = [duration for duration in valid_durations if duration > 0]
    if not valid_durations:
        raise ValueError(tr("لم يتم اختيار فيديو صالح."))

    sections = []
    timeline_end = 0.0
    previous_duration = 0.0
    source_cursor = 0

    while timeline_end < audio_duration - 0.01:
        index = source_cursor % len(video_durations)
        duration = max(0.0, float(video_durations[index] or 0.0))
        source_cursor += 1
        if duration <= 0:
            if source_cursor >= len(video_durations):
                raise ValueError(tr("لم يتم اختيار فيديو صالح."))
            continue
        transition_span = 0.0
        start = timeline_end
        if sections:
            transition_span = safe_transition_span(transition, previous_duration, duration)
            start = max(0.0, timeline_end - transition_span)

        remaining = audio_duration - start
        if remaining <= 0.01:
            break

        source_duration = min(duration, remaining)
        if source_duration <= 0.01:
            break

        sections.append(VideoSection(index, start, source_duration, transition_span))
        timeline_end = max(timeline_end, start + source_duration)
        previous_duration = source_duration

    return sections, audio_duration



class AudioVideoMergeProgressDialog(wx.Dialog):
    def __init__(self, parent, cancel_callback):
        super().__init__(parent, title=tr("جاري دمج الصوت مع الفيديو"), size=(420, 140))
        self.cancel_callback = cancel_callback
        self.gauge = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL)
        self.cancel_button = wx.Button(self, label=tr("إلغاء"))
        self.gauge.SetName(tr("شريط تقدم دمج الصوت مع الفيديو"))
        self.cancel_button.SetName(tr("إلغاء دمج الصوت مع الفيديو"))
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
        self.gauge.SetValue(int(max(0.0, min(1.0, progress)) * 100))


class AudioVideoMergeDialog(wx.Frame):
    def __init__(self, parent, start_callback):
        super().__init__(parent, title=tr("دمج الصوت مع الفيديو"), size=(620, 360))
        from video_maker.menus import install_menu_bar

        self.start_callback = start_callback
        self.audio = ""
        self.videos = []

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.audio_button = wx.Button(panel, label=tr("إضافة ملف صوتي"), size=(260, 36))
        self.videos_button = wx.Button(panel, label=tr("إضافة فيديوهات"), size=(260, 36))
        self.videos_list = wx.ListBox(panel)
        transition_label = wx.StaticText(panel, label=tr("تأثير الانتقال"))
        self.transition_choice = wx.Choice(panel, choices=translated_transitions())
        self.transition_choice.SetSelection(0)

        merge_button = wx.Button(panel, label=tr("دمج"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))

        self.audio_button.SetName(tr("إضافة ملف صوتي"))
        self.videos_button.SetName(tr("إضافة فيديوهات"))
        self.videos_list.SetName(tr("قائمة الفيديوهات المختارة"))
        self.transition_choice.SetName(tr("تأثير الانتقال"))
        merge_button.SetName(tr("دمج الصوت مع الفيديو"))
        cancel_button.SetName(tr("إلغاء"))
        merge_button.SetDefault()

        action_sizer = wx.BoxSizer(wx.HORIZONTAL)
        action_sizer.Add(merge_button, flag=wx.ALL, border=6)
        action_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        main_sizer.Add(self.audio_button, flag=wx.ALIGN_CENTER | wx.TOP, border=12)
        main_sizer.Add(self.videos_button, flag=wx.ALIGN_CENTER | wx.TOP, border=6)
        main_sizer.Add(self.videos_list, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        main_sizer.Add(transition_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
        main_sizer.Add(self.transition_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)
        main_sizer.Add(action_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=8)

        panel.SetSizer(main_sizer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(frame_sizer)

        self.audio_button.Bind(wx.EVT_BUTTON, self.select_audio)
        self.videos_button.Bind(wx.EVT_BUTTON, self.select_videos)
        merge_button.Bind(wx.EVT_BUTTON, self.on_ok)
        cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.videos_list.Bind(wx.EVT_RIGHT_DOWN, self.select_item_under_mouse)
        self.videos_list.Bind(wx.EVT_CONTEXT_MENU, self.show_context_menu)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        bind_dialog_keys(self, self.on_key, (wx.Choice,), preserve_navigation_keys=True)

        self.Centre()
        self.initial_focus = self.audio_button
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

    def select_audio(self, event=None):
        with wx.FileDialog(self, tr("اختيار الصوت"), wildcard=audio_wildcard(), style=wx.FD_OPEN) as dialog:
            prepare_media_file_dialog(dialog, "audio", "audio_video_merge_audio")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.audio = dialog.GetPath()
            remember_media_path(self.audio, "audio", "audio_video_merge_audio")
            message = tr_format("تم اختيار ملف الصوت {name}", name=os.path.basename(self.audio))
            self.audio_button.SetLabel(message)
            self.audio_button.SetName(message)
            self.speak(message)

    def select_videos(self, event=None):
        with wx.FileDialog(self, tr("اختيار الفيديوهات"), wildcard=video_wildcard(), style=wx.FD_OPEN | wx.FD_MULTIPLE) as dialog:
            prepare_media_file_dialog(dialog, "video", "audio_video_merge_videos")
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            self.videos = sorted(dialog.GetPaths(), key=natural_sort_key)
            remember_media_paths(self.videos, "video", "audio_video_merge_videos")
            self.refresh_videos_list()
            message = tr_format("تم اختيار {count} فيديو", count=len(self.videos))
            self.videos_button.SetLabel(message)
            self.videos_button.SetName(message)
            self.speak(message)

    def focus_videos_list(self, index=None):
        if self.videos:
            selection = index if index is not None else self.videos_list.GetSelection()
            if selection == wx.NOT_FOUND:
                selection = 0
            selection = min(max(selection, 0), len(self.videos) - 1)
            self.videos_list.SetSelection(selection)
        wx.CallAfter(self.videos_list.SetFocus)

    def refresh_videos_list(self, selection=None):
        self.videos_list.Clear()
        for index, path in enumerate(self.videos, start=1):
            self.videos_list.Append(f"{tr('فيديو')} {index} - {os.path.basename(path)}")
        self.focus_videos_list(selection)

    def selected_video_index(self):
        selection = self.videos_list.GetSelection()
        if selection == wx.NOT_FOUND or selection < 0 or selection >= len(self.videos):
            return None
        return selection

    def select_item_under_mouse(self, event):
        index = self.videos_list.HitTest(event.GetPosition())
        if index != wx.NOT_FOUND:
            self.videos_list.SetSelection(index)
        event.Skip()

    def context_menu_actions(self, index):
        if index is None or index < 0 or index >= len(self.videos):
            return []
        actions = [("replace", tr("استبدال هذا الفيديو")), ("delete", tr("حذف"))]
        if index > 0:
            actions.append(("move_up", tr("رفع للأعلى")))
        if index < len(self.videos) - 1:
            actions.append(("move_down", tr("خفض للأسفل")))
        return actions

    def show_context_menu(self, event):
        index = self.selected_video_index()
        if index is None:
            return

        action_ids = {key: wx.NewIdRef() for key, _label in self.context_menu_actions(index)}
        menu = wx.Menu()
        for key, label in self.context_menu_actions(index):
            menu.Append(action_ids[key], label)

        handlers = {
            "replace": self.replace_selected_video,
            "delete": self.delete_selected_video,
            "move_up": self.move_selected_video_up,
            "move_down": self.move_selected_video_down,
        }
        for key, item_id in action_ids.items():
            self.Bind(wx.EVT_MENU, handlers[key], id=item_id)
        self.PopupMenu(menu)
        menu.Destroy()

    def replace_selected_video(self, event=None):
        index = self.selected_video_index()
        if index is None:
            return
        with wx.FileDialog(self, tr("اختيار فيديو"), wildcard=video_wildcard(), style=wx.FD_OPEN) as dialog:
            prepare_media_file_dialog(dialog, "video", "audio_video_merge_replace_video")
            if dialog.ShowModal() == wx.ID_CANCEL:
                self.focus_videos_list(index)
                return
            self.videos[index] = dialog.GetPath()
            remember_media_path(self.videos[index], "video", "audio_video_merge_replace_video")
            self.refresh_videos_list(index)

    def delete_selected_video(self, event=None):
        index = self.selected_video_index()
        if index is None:
            return
        del self.videos[index]
        self.refresh_videos_list(min(index, len(self.videos) - 1))

    def move_selected_video_up(self, event=None):
        index = self.selected_video_index()
        if index is None or index == 0:
            self.focus_videos_list(index)
            return
        self.videos[index - 1], self.videos[index] = self.videos[index], self.videos[index - 1]
        self.refresh_videos_list(index - 1)

    def move_selected_video_down(self, event=None):
        index = self.selected_video_index()
        if index is None or index >= len(self.videos) - 1:
            self.focus_videos_list(index)
            return
        self.videos[index + 1], self.videos[index] = self.videos[index], self.videos[index + 1]
        self.refresh_videos_list(index + 1)

    def on_ok(self, event=None):
        if not self.audio:
            wx.MessageBox(tr("اختر ملف الصوت أولا."), tr("بيانات ناقصة"), wx.OK | wx.ICON_ERROR)
            return
        if not self.videos:
            wx.MessageBox(tr("اختر فيديو واحدا على الأقل."), tr("بيانات ناقصة"), wx.OK | wx.ICON_ERROR)
            return
        self.start_callback(self.get_options())
        self.Destroy()

    def on_cancel(self, event=None):
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
            return
        event.Skip()

    def get_options(self):
        return AudioVideoMergeOptions(
            audio=self.audio,
            videos=list(self.videos),
            transition=TRANSITIONS[max(0, self.transition_choice.GetSelection())],
        )


def create_audio_video_merge(options, output_file, temp_dir, progress_callback, cancelled_callback):
    output_file = os.path.abspath(output_file)
    try:
        audio_duration = float(get_media_duration(options.audio) or 0.0)
        if audio_duration <= 0:
            raise ValueError(tr("مدة ملف الصوت غير صالحة."))

        video_durations = []
        target_size = None
        target_fps = 24
        for index, video_path in enumerate(options.videos):
            if cancelled_callback():
                return False
            duration = float(get_media_duration(video_path) or 0.0)
            if duration <= 0:
                raise ValueError(tr_format("مدة الفيديو غير صالحة: {name}", name=os.path.basename(video_path)))
            if target_size is None:
                info = ffmpeg_parse_infos(video_path)
                target_size = info.get("video_size", (1280, 720))
                target_size = (int(target_size[0]) + int(target_size[0]) % 2, int(target_size[1]) + int(target_size[1]) % 2)
                target_fps = int(round(info.get("video_fps", 24) or 24))
            video_durations.append(duration)
            progress_callback((index + 1) / max(1, len(options.videos)) * 0.2)

        if not video_durations or target_size is None:
            raise ValueError(tr("لم يتم اختيار فيديو صالح."))

        sections, final_duration = plan_video_sections(video_durations, audio_duration, options.transition)
        if final_duration <= 0:
            raise ValueError(tr("لم يتم اختيار فيديو صالح."))
        if cancelled_callback():
            return False

        filters = []
        inputs = []
        
        is_crossfade = (options.transition == TRANSITIONS[1])
        
        target_w, target_h = target_size
        scale_pad = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps},format=yuv420p"

        for i, section in enumerate(sections):
            vid_path = options.videos[section.index]
            inputs.extend(["-t", str(section.source_duration), "-i", vid_path])
            
            d = section.source_duration
            eff = options.transition
            f_str = ""
            if eff == TRANSITIONS[3]: # rotate
                f_str = f",rotate=2*PI*t/{d}:c=black"
            elif eff == TRANSITIONS[4]: # fadeout
                f_str = f",fade=t=out:st={max(0, d-1)}:d=1"
            elif eff == TRANSITIONS[5]: # mirror
                f_str = ",hflip"
            elif eff == TRANSITIONS[6]: # colorx
                f_str = f",geq=r='clip(r(X,Y)*(1+0.5*T/{d}),0,255)':g='clip(g(X,Y)*(1+0.5*T/{d}),0,255)':b='clip(b(X,Y)*(1+0.5*T/{d}),0,255)'"

            filters.append(f"[{i}:v]{scale_pad}{f_str}[v{i}];")

        if is_crossfade and len(sections) > 1:
            current_offset = sections[0].source_duration - sections[1].transition_span
            filters.append(f"[v0][v1]xfade=transition=fade:duration={sections[1].transition_span}:offset={current_offset}[xf1];")
            for i in range(2, len(sections)):
                current_offset += sections[i-1].source_duration - sections[i].transition_span
                filters.append(f"[xf{i-1}][v{i}]xfade=transition=fade:duration={sections[i].transition_span}:offset={current_offset}[xf{i}];")
            out_pad = f"[xf{len(sections)-1}]"
        else:
            concat_inputs = "".join([f"[v{i}]" for i in range(len(sections))])
            filters.append(f"{concat_inputs}concat=n={len(sections)}:v=1:a=0[outv];")
            out_pad = "[outv]"
        filters.append(f"{out_pad}tpad=stop_mode=clone:stop_duration=1.000000[outv_final];")
        out_pad = "[outv_final]"

        script_path = os.path.join(temp_dir, "filter_script.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("".join(filters))
            
        def build_command(audio_args):
            return [ffmpeg_binary(), "-y"] + inputs + [
                "-i", options.audio,
                "-filter_complex_script", script_path,
                "-map", out_pad,
                "-map", f"{len(sections)}:a?",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
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
                progress_callback=lambda p: progress_callback(0.2 + p * 0.8 / 100) if progress_callback else None,
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
                progress_callback=lambda p: progress_callback(0.2 + p * 0.8 / 100) if progress_callback else None,
                cancelled_callback=cancelled_callback
            )
        return not cancelled_callback()
    except Exception as e:
        if not cancelled_callback():
            raise e
        return False
