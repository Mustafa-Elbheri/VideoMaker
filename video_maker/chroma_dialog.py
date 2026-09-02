import os
import shutil
import threading

import wx
from video_maker.mpv_player import MPVMediaCtrl, MEDIASTATE_PLAYING, MEDIASTATE_PAUSED, MEDIASTATE_STOPPED, EVT_MEDIA_LOADED, EVT_MEDIA_FINISHED

from video_maker.chroma_key import ChromaBackgroundOptions, analysis_message
from video_maker.dialog_keys import bind_dialog_keys
from video_maker.dialogs import IMAGE_WILDCARD, VIDEO_WILDCARD, prepare_media_file_dialog, remember_media_path
from video_maker.localization import tr


BACKGROUND_TYPES = [
    ("image", "صورة"),
    ("video", "فيديو"),
]

FIT_MODES = [
    ("fill", "ملء الإطار مع القص"),
    ("fit", "إظهار الخلفية كاملة"),
    ("stretch", "تمديد الخلفية لتملأ الإطار"),
]

BACKGROUND_TYPE_DESCRIPTIONS = {
    "image": "يضع صورة ثابتة خلف الشخص طوال الفيديو",
    "video": (
        "يضع فيديو متحركا خلف الشخص. إذا كان أقصر من الفيديو الأصلي، "
        "يبدأ من أوله مرة أخرى. لا يستخدم صوته"
    ),
}

FIT_MODE_DESCRIPTIONS = {
    "fill": (
        "تملأ الخلفية الشاشة كلها. قد يحذف البرنامج جزءا صغيرا من أطراف الخلفية "
        "حتى لا تظهر مساحات فارغة"
    ),
    "fit": (
        "يعرض الخلفية كاملة من دون حذف أي جزء منها. قد تظهر مساحات فارغة "
        "إذا كان مقاسها مختلفا عن الفيديو"
    ),
    "stretch": (
        "يمدد الخلفية حتى تملأ الشاشة من دون حذف أي جزء. قد يتغير شكل الصورة "
        "أو الفيديو قليلا"
    ),
}


class ChromaBackgroundDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        analyze_callback,
        preview_callback,
        speech_callback=None,
    ):
        super().__init__(
            parent,
            title=tr("استبدال خلفية الفيديو إذا كانت كرومة"),
            size=(760, 560),
        )
        self.analyze_callback = analyze_callback
        self.preview_callback = preview_callback
        self.speech_callback = speech_callback
        self.options = None
        self.analysis_result = None
        self.preview_path = ""
        self.preview_temp_dir = ""
        self.preview_signature = None
        self.pending_preview_play = False
        self.busy = False
        self.closed = False
        self._description_speech_serial = 0

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        instructions = wx.StaticText(
            panel,
            label=tr(
                "يكتشف البرنامج اللون الأخضر ويضبط إزالة الكرومة وتنظيف الحواف تلقائيا. "
                "يحتفظ بصوت الفيديو الأصلي ولا يستخدم صوت فيديو الخلفية"
            ),
        )
        instructions.SetName(tr("شرح استبدال خلفية الكرومة"))
        main_sizer.Add(instructions, flag=wx.EXPAND | wx.ALL, border=10)

        type_label = wx.StaticText(panel, label=tr("نوع الخلفية الجديدة"))
        self.type_choice = wx.Choice(
            panel,
            choices=[tr(label) for _, label in BACKGROUND_TYPES],
        )
        self.type_choice.SetSelection(0)
        self.type_choice.SetName(tr("اختيار نوع الخلفية الجديدة صورة أو فيديو"))
        main_sizer.Add(type_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(self.type_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        path_label = wx.StaticText(panel, label=tr("ملف الخلفية الجديدة"))
        path_row = wx.BoxSizer(wx.HORIZONTAL)
        self.path_ctrl = wx.TextCtrl(panel, style=wx.TE_READONLY)
        self.path_ctrl.SetName(tr("مسار ملف الخلفية الجديدة"))
        self.browse_button = wx.Button(panel, label=tr("اختيار ملف الخلفية"))
        self.browse_button.SetName(tr("اختيار صورة أو فيديو للخلفية الجديدة"))
        path_row.Add(self.path_ctrl, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=8)
        path_row.Add(self.browse_button)
        main_sizer.Add(path_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(path_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        fit_label = wx.StaticText(panel, label=tr("طريقة ملاءمة الخلفية"))
        self.fit_choice = wx.Choice(
            panel,
            choices=[tr(label) for _, label in FIT_MODES],
        )
        self.fit_choice.SetSelection(0)
        self.fit_choice.SetName(tr("اختيار طريقة ملاءمة الخلفية داخل إطار الفيديو"))
        main_sizer.Add(fit_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        main_sizer.Add(self.fit_choice, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        self.status = wx.StaticText(panel, label=tr("لم يتم فحص الكرومة بعد"))
        self.status.SetName(tr("نتيجة فحص الكرومة"))
        main_sizer.Add(self.status, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        self.preview = MPVMediaCtrl(panel, style=wx.SIMPLE_BORDER)
        self.preview.SetName(tr("معاينة استبدال خلفية الكرومة"))
        main_sizer.Add(self.preview, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        preview_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.analyze_button = wx.Button(panel, label=tr("فحص الكرومة"))
        self.play_button = wx.Button(panel, label=tr("تشغيل المعاينة"))
        self.rewind_button = wx.Button(panel, label=tr("ترجيع"))
        self.forward_button = wx.Button(panel, label=tr("تقديم"))
        self.pause_button = wx.Button(panel, label=tr("إيقاف مؤقت"))
        self.stop_button = wx.Button(panel, label=tr("إيقاف"))
        self.analyze_button.SetName(tr("فحص جودة الكرومة تلقائيا"))
        self.play_button.SetName(tr("تشغيل معاينة استبدال الخلفية"))
        self.rewind_button.SetName(tr("ترجيع معاينة استبدال الخلفية"))
        self.forward_button.SetName(tr("تقديم معاينة استبدال الخلفية"))
        self.pause_button.SetName(tr("إيقاف مؤقت لمعاينة استبدال الخلفية"))
        self.stop_button.SetName(tr("إيقاف معاينة استبدال الخلفية"))
        for button in (
            self.analyze_button,
            self.play_button,
            self.rewind_button,
            self.forward_button,
            self.pause_button,
            self.stop_button,
        ):
            preview_buttons.Add(button, flag=wx.ALL, border=4)
        main_sizer.Add(preview_buttons, flag=wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT, border=8)

        action_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.apply_button = wx.Button(panel, wx.ID_OK, tr("تطبيق"))
        self.cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        self.apply_button.SetName(tr("تطبيق استبدال خلفية الفيديو"))
        self.cancel_button.SetName(tr("إلغاء استبدال خلفية الفيديو"))
        self.apply_button.SetDefault()
        action_buttons.Add(self.apply_button, flag=wx.ALL, border=6)
        action_buttons.Add(self.cancel_button, flag=wx.ALL, border=6)
        main_sizer.Add(action_buttons, flag=wx.ALIGN_CENTER | wx.ALL, border=8)

        panel.SetSizer(main_sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, proportion=1, flag=wx.EXPAND)
        self.SetSizer(outer)

        self.type_choice.Bind(wx.EVT_CHOICE, self.on_type_changed)
        self.fit_choice.Bind(wx.EVT_CHOICE, self.on_options_changed)
        focus_event = getattr(wx, "EVT_SET_FOCUS", None)
        if focus_event is not None:
            self.type_choice.Bind(focus_event, self.on_type_focus)
            self.fit_choice.Bind(focus_event, self.on_fit_focus)
        kill_focus_event = getattr(wx, "EVT_KILL_FOCUS", None)
        if kill_focus_event is not None:
            self.type_choice.Bind(kill_focus_event, self.on_description_blur)
            self.fit_choice.Bind(kill_focus_event, self.on_description_blur)
        self.browse_button.Bind(wx.EVT_BUTTON, self.choose_background)
        self.analyze_button.Bind(wx.EVT_BUTTON, self.start_analysis)
        self.play_button.Bind(wx.EVT_BUTTON, self.play_preview)
        self.rewind_button.Bind(wx.EVT_BUTTON, self.rewind_preview)
        self.forward_button.Bind(wx.EVT_BUTTON, self.forward_preview)
        self.pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        self.stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        self.apply_button.Bind(wx.EVT_BUTTON, self.apply_options)
        self.cancel_button.Bind(wx.EVT_BUTTON, self.cancel_dialog)
        self.Bind(EVT_MEDIA_LOADED, self.on_preview_loaded, self.preview)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.cancel_dialog)
        bind_dialog_keys(self, self.on_key, (wx.TextCtrl, wx.Choice))

        self._update_choice_descriptions()
        self.Centre()
        wx.CallAfter(self.type_choice.SetFocus)

    def say(self, text, interrupt=True, wait_for_ui=True):
        if not self.speech_callback:
            return
        try:
            self.speech_callback(text, interrupt, wait_for_ui)
        except TypeError:
            try:
                self.speech_callback(text, interrupt)
            except TypeError:
                self.speech_callback(text)

    def _description_for_type(self):
        return tr(BACKGROUND_TYPE_DESCRIPTIONS.get(self.selected_type(), ""))

    def _description_for_fit(self):
        return tr(FIT_MODE_DESCRIPTIONS.get(self.selected_fit(), ""))

    @staticmethod
    def _set_help_text(control, text):
        if control is None:
            return
        try:
            control.SetHelpText(text)
        except Exception:
            pass
        try:
            control.SetToolTip(text or None)
        except Exception:
            pass

    def _update_choice_descriptions(self):
        self._set_help_text(self.type_choice, self._description_for_type())
        self._set_help_text(self.fit_choice, self._description_for_fit())

    def _speak_choice_description(self, text, control):
        if not text or not self.speech_callback:
            return
        self._description_speech_serial += 1
        serial = self._description_speech_serial

        def speak_after_control_name():
            if self.closed or serial != self._description_speech_serial:
                return
            try:
                if control is not None and not control.HasFocus():
                    return
            except Exception:
                pass
            self.say(text, False, False)

        try:
            wx.CallLater(220, speak_after_control_name)
        except Exception:
            wx.CallAfter(speak_after_control_name)

    def on_type_focus(self, event=None):
        self._update_choice_descriptions()
        self._speak_choice_description(self._description_for_type(), self.type_choice)
        if event is not None:
            event.Skip()

    def on_fit_focus(self, event=None):
        self._update_choice_descriptions()
        self._speak_choice_description(self._description_for_fit(), self.fit_choice)
        if event is not None:
            event.Skip()

    def on_description_blur(self, event=None):
        self._description_speech_serial += 1
        if event is not None:
            event.Skip()

    def selected_type(self):
        index = self.type_choice.GetSelection()
        if index < 0 or index >= len(BACKGROUND_TYPES):
            index = 0
        return BACKGROUND_TYPES[index][0]

    def selected_fit(self):
        index = self.fit_choice.GetSelection()
        if index < 0 or index >= len(FIT_MODES):
            index = 0
        return FIT_MODES[index][0]

    def current_options(self):
        return ChromaBackgroundOptions(
            background_kind=self.selected_type(),
            background_path=self.path_ctrl.GetValue().strip(),
            fit_mode=self.selected_fit(),
        )

    def options_signature(self):
        options = self.current_options()
        return (options.background_kind, os.path.abspath(options.background_path or ""), options.fit_mode)

    def on_type_changed(self, event=None):
        self.path_ctrl.SetValue("")
        self.invalidate_preview()
        self._update_choice_descriptions()
        self._speak_choice_description(self._description_for_type(), self.type_choice)
        if event is not None:
            event.Skip()

    def on_options_changed(self, event=None):
        self.invalidate_preview()
        self._update_choice_descriptions()
        self._speak_choice_description(self._description_for_fit(), self.fit_choice)
        if event is not None:
            event.Skip()

    def choose_background(self, event=None):
        media_kind = self.selected_type()
        wildcard = IMAGE_WILDCARD if media_kind == "image" else VIDEO_WILDCARD
        title = tr("اختيار صورة للخلفية الجديدة") if media_kind == "image" else tr("اختيار فيديو للخلفية الجديدة")
        dialog_key = "chroma_background_image" if media_kind == "image" else "chroma_background_video"
        with wx.FileDialog(self, title, wildcard=wildcard, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            prepare_media_file_dialog(dialog, media_kind, dialog_key)
            if dialog.ShowModal() != wx.ID_OK:
                wx.CallAfter(self.browse_button.SetFocus)
                return
            path = dialog.GetPath()
            remember_media_path(path, media_kind, dialog_key)
            self.path_ctrl.SetValue(path)
        self.invalidate_preview()
        self.say(tr("تم اختيار ملف الخلفية"))
        wx.CallAfter(self.browse_button.SetFocus)

    def validate_path(self):
        path = self.path_ctrl.GetValue().strip()
        if not path or not os.path.isfile(path):
            message = tr("اختر ملف الخلفية الجديدة أولا")
            # self.say(message)
            wx.MessageBox(message, tr("بيانات ناقصة"), wx.OK | wx.ICON_INFORMATION)
            wx.CallAfter(self.browse_button.SetFocus)
            return False
        return True

    def set_busy(self, busy, status_text=""):
        self.busy = bool(busy)
        for control in (
            self.type_choice,
            self.fit_choice,
            self.browse_button,
            self.analyze_button,
            self.play_button,
            self.apply_button,
        ):
            control.Enable(not self.busy)
        if status_text:
            self.status.SetLabel(status_text)
            self.status.SetName(status_text)
            self.say(status_text)
        self.Layout()

    def start_analysis(self, event=None):
        if self.busy:
            return
        self.set_busy(True, tr("جاري فحص الكرومة"))
        threading.Thread(target=self._analysis_worker, daemon=True).start()

    def _analysis_worker(self):
        try:
            result = self.analyze_callback()
            error = ""
        except Exception as caught:
            result = None
            error = str(caught)
        wx.CallAfter(self.finish_analysis, result, error)

    def finish_analysis(self, result, error):
        if self.closed:
            return
        self.set_busy(False)
        if error:
            message = error or tr("تعذر فحص الكرومة")
            self.status.SetLabel(message)
            self.status.SetName(message)
            # self.say(message)
            wx.MessageBox(message, tr("خطأ"), wx.OK | wx.ICON_ERROR)
            wx.CallAfter(self.analyze_button.SetFocus)
            return
        self.analysis_result = result
        message = analysis_message(result)
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.say(message)
        wx.CallAfter(self.play_button.SetFocus)

    def play_preview(self, event=None):
        if self.busy:
            return
        if not self.validate_path():
            return
        signature = self.options_signature()
        if self.preview_path and self.preview_signature == signature:
            if self.preview.Length() > 0 and self.preview.Tell() >= self.preview.Length() - 200:
                self.preview.Seek(0)
            self.preview.Play()
            wx.CallAfter(self.play_button.SetFocus)
            return
        self.set_busy(True, tr("جاري تجهيز معاينة استبدال الخلفية"))
        options = self.current_options()
        threading.Thread(target=self._preview_worker, args=(options, signature), daemon=True).start()

    def _preview_worker(self, options, signature):
        try:
            path, temp_dir, analysis = self.preview_callback(options)
            error = ""
        except Exception as caught:
            path, temp_dir, analysis = "", "", None
            error = str(caught)
        wx.CallAfter(self.finish_preview, path, temp_dir, analysis, signature, error)

    def finish_preview(self, path, temp_dir, analysis, signature, error):
        if self.closed:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return
        self.set_busy(False)
        if error:
            message = error or tr("تعذر تجهيز معاينة استبدال الخلفية")
            self.status.SetLabel(message)
            self.status.SetName(message)
            # self.say(message)
            wx.MessageBox(message, tr("خطأ"), wx.OK | wx.ICON_ERROR)
            wx.CallAfter(self.play_button.SetFocus)
            return
        self.cleanup_preview()
        self.preview_path = path
        self.preview_temp_dir = temp_dir
        self.preview_signature = signature
        self.analysis_result = analysis
        message = tr("تم تجهيز معاينة استبدال الخلفية")
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.say(message)
        if not self.preview.Load(path):
            self.cleanup_preview()
            message = tr("تعذر تشغيل معاينة استبدال الخلفية")
            # self.say(message)
            wx.MessageBox(message, tr("خطأ"), wx.OK | wx.ICON_ERROR)
            return
        self.pending_preview_play = True
        wx.CallLater(5, self.finish_pending_preview_play)

    def on_preview_loaded(self, event):
        self.finish_pending_preview_play()
        event.Skip()

    def finish_pending_preview_play(self):
        if self.closed or not self.pending_preview_play:
            return
        if self.preview.Length() <= 0:
            wx.CallLater(5, self.finish_pending_preview_play)
            return
        self.pending_preview_play = False
        self.preview.Seek(0)
        self.preview.Play()
        wx.CallAfter(self.play_button.SetFocus)

    def rewind_preview(self, event=None):
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            self.preview.Seek(max(0, self.preview.Tell() - 5000))
        wx.CallAfter(self.rewind_button.SetFocus)

    def forward_preview(self, event=None):
        if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
            length = self.preview.Length()
            target = self.preview.Tell() + 5000
            if length > 0:
                target = min(length, target)
            self.preview.Seek(target)
        wx.CallAfter(self.forward_button.SetFocus)

    def pause_preview(self, event=None):
        if self.preview.GetState() == MEDIASTATE_PLAYING:
            self.preview.Pause()
        wx.CallAfter(self.pause_button.SetFocus)

    def stop_preview(self, event=None, restore_focus=True):
        self.pending_preview_play = False
        try:
            if self.preview.GetState() in (MEDIASTATE_PLAYING, MEDIASTATE_PAUSED):
                self.preview.Stop()
        except Exception:
            pass
        if restore_focus:
            wx.CallAfter(self.stop_button.SetFocus)

    def toggle_preview(self):
        focused = wx.Window.FindFocus()
        if isinstance(focused, (wx.Button, wx.TextCtrl, wx.Choice)):
            return False
        if self.preview.GetState() == MEDIASTATE_PLAYING:
            self.pause_preview()
        else:
            self.play_preview()
        return True

    def invalidate_preview(self):
        self.stop_preview(None, False)
        self.cleanup_preview()
        self.preview_signature = None

    def cleanup_preview(self):
        self.pending_preview_play = False
        if self.preview_temp_dir:
            shutil.rmtree(self.preview_temp_dir, ignore_errors=True)
        self.preview_path = ""
        self.preview_temp_dir = ""

    def apply_options(self, event=None):
        if self.busy:
            self.say(tr("انتظر حتى ينتهي العمل الحالي"))
            return
        if not self.validate_path():
            return
        self.options = self.current_options()
        self.stop_preview(None, False)
        self.EndModal(wx.ID_OK)

    def cancel_dialog(self, event=None):
        if self.busy:
            self.say(tr("انتظر حتى ينتهي العمل الحالي"))
            return
        self.stop_preview(None, False)
        self.EndModal(wx.ID_CANCEL)

    def on_key(self, event):
        key = event.GetKeyCode()
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
        if key == wx.WXK_SPACE and self.toggle_preview():
            return
        event.Skip()

    def Destroy(self):
        self.closed = True
        self.stop_preview(None, False)
        self.cleanup_preview()
        return super().Destroy()
