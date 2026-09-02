import os
import wx
import time

import sys

if hasattr(sys, '_MEIPASS'):
    _base_dir = sys._MEIPASS
else:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_mpv_dll_dirs = [_base_dir, os.path.join(_base_dir, "video_maker")]
for directory in _mpv_dll_dirs:
    if directory not in os.environ.get("PATH", ""):
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
    if os.name == "nt":
        try:
            os.add_dll_directory(directory)
        except OSError:
            pass

try:
    import mpv
except OSError:
    os.environ["PATH"] = os.getcwd() + os.pathsep + os.environ.get("PATH", "")
    if os.name == "nt":
        try:
            os.add_dll_directory(os.getcwd())
        except OSError:
            pass
    import mpv

from .mpv_constants import MEDIASTATE_STOPPED, MEDIASTATE_PAUSED, MEDIASTATE_PLAYING
from .mpv_events import MediaLoadedEvent, MediaFinishedEvent


# مدة انتظار استرداد بحث معلّق قبل إعادة إرساله بأمر متزامن (وقاية نادرة).
SEEK_WATCHDOG_MS = 900


class MPVMediaCtrl(wx.Panel):
    def __init__(self, parent, id=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize, style=0, name="MPVMediaCtrl"):
        super().__init__(parent, id, pos, size, style, name)
        self._shutting_down = False
        self._duration_observer = None
        self._eof_observer = None
        self._player = mpv.MPV(
            wid=str(self.GetHandle()),
            input_default_bindings=False, 
            input_vo_keyboard=False,
            keep_open=True,
            osc=False,
            hwdec='no',
            # خيارات mpv الأصلية لحركة سلسة ودقيقة بدون إعادة برمجة:
            # - hr-seek: بحث عالي الدقة دائمًا (إطار دقيق).
            # - كاش الديمكسكس القابل للبحث: القفز داخل النافذة المؤقتة يكون
            #   فوريًا دون فك ترميز من أول مفتاح ضغط.
            # - display-resample + interpolation: تشغيل ناعم بلا تنتيش على
            #   أي معدل تحديث للشاشة.
            # - vd-lavc-threads: فك ترميز متوازٍ للوصول السريع للإطار.
            hr_seek='yes',
            cache='yes',
            demuxer_seekable_cache='yes',
            demuxer_readahead_secs=10,
            video_sync='display-resample',
            interpolation='yes',
            vd_lavc_threads=0,
            pause=True,
        )
        self._state = MEDIASTATE_STOPPED
        self._length = 0
        self._volume = 1.0 
        self._playback_rate = 1.0
        self._media_path = None
        # تنسيق البحث "الأحدث يفوز": أثناء الضغط المطول يبقى هدف واحد معلّق
        # ويُرسل أمر بحث واحد غير متزامن فقط عندما يكتمل سابقه. هذا يمنع
        # تكدس أوامر البحث في طابور mpv ويجعل الحركة سلسة ودقيقة.
        self._seek_target = None
        self._seek_in_flight = False
        self._seek_applied_target = None
        self._seek_mode = 'exact'
        self._seek_watchdog = None
        
        @self._player.property_observer('duration')
        def _on_duration(name, value):
            if value is not None:
                self._call_after(self._handle_duration_changed, value)
        self._duration_observer = _on_duration

        @self._player.property_observer('eof-reached')
        def _on_eof(name, value):
            if value:
                self._call_after(self._handle_eof_reached)
        self._eof_observer = _on_eof
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_window_destroy)

    def _call_after(self, func, *args):
        if self._shutting_down:
            return
        try:
            wx.CallAfter(func, *args)
        except RuntimeError:
            return

    def _handle_duration_changed(self, value):
        if self._shutting_down:
            return
        self._length = value * 1000.0
        self._post_loaded_event()

    def _handle_eof_reached(self):
        if self._shutting_down or self._state != MEDIASTATE_PLAYING:
            return
        player = self._player
        if player is not None:
            try:
                player.pause = True
            except Exception:
                pass
        self._state = MEDIASTATE_STOPPED
        self._post_finished_event()

    def _on_window_destroy(self, event):
        try:
            if event.GetEventObject() is self:
                self._destroy_mpv_player()
        finally:
            event.Skip()

    def _post_finished_event(self):
        try:
            if self.IsBeingDeleted():
                return
            evt = MediaFinishedEvent(id=self.GetId())
            evt.SetEventObject(self)
            wx.PostEvent(self, evt)
        except RuntimeError:
            return

    def _post_loaded_event(self):
        try:
            if self.IsBeingDeleted():
                return
            evt = MediaLoadedEvent(id=self.GetId())
            evt.SetEventObject(self)
            wx.PostEvent(self, evt)
        except RuntimeError:
            return

    def Load(self, url):
        if self._shutting_down or self._player is None:
            return False
        self._media_path = url
        self._state = MEDIASTATE_STOPPED
        self._reset_seek_state()
        self._player.loadfile(url)
        self._player.pause = True
        return True

    def Play(self):
        if self._shutting_down or self._player is None or not self._media_path:
            return False
        
        if self._player.eof_reached:
            self._player.pause = True
            self._state = MEDIASTATE_PAUSED
            return False
            
        self._player.pause = False
        self._state = MEDIASTATE_PLAYING
        return True

    def Pause(self):
        if self._shutting_down or self._player is None:
            return False
        self._player.pause = True
        self._state = MEDIASTATE_PAUSED
        return True

    def Stop(self):
        if self._shutting_down or self._player is None:
            return False
        self._reset_seek_state()
        self._player.pause = True
        self._player.command('seek', 0, 'absolute')
        self._state = MEDIASTATE_STOPPED
        return True

    def GetState(self):
        return self._state

    def Tell(self):
        if self._shutting_down or self._player is None:
            return 0
        # أثناء التوقف ووجود بحث معلّق نُرجع الموضع المطلوب (الهدف) بدل
        # القراءة القديمة من الجهاز حتى تبقى حركة رأس التشغيل دقيقة فورًا.
        target = self._seek_target
        if target is not None and self._state != MEDIASTATE_PLAYING:
            return target
        pos = self._player.time_pos
        if pos is None:
            return 0
        return pos * 1000.0

    def Length(self):
        return self._length

    def Seek(self, where, mode='exact'):
        if self._shutting_down or self._player is None:
            return where
        try:
            target = max(0, int(float(where or 0)))
        except (TypeError, ValueError):
            target = 0
        length = int(self._length or 0)
        if length > 0:
            target = min(target, max(0, length - 1))
        self._seek_target = target
        self._seek_mode = 'exact' if mode == 'exact' else 'absolute'
        self._pump_seek_queue()
        return target

    def _reset_seek_state(self):
        self._seek_target = None
        self._seek_in_flight = False
        self._seek_applied_target = None
        self._cancel_seek_watchdog()

    def _pump_seek_queue(self):
        if self._shutting_down or self._player is None:
            self._seek_target = None
            self._seek_in_flight = False
            self._seek_applied_target = None
            return
        if self._seek_in_flight:
            self._schedule_seek_watchdog()
            return
        target = self._seek_target
        if target is None:
            return
        self._seek_in_flight = True
        self._seek_applied_target = target
        seek_cmd = 'absolute+exact' if self._seek_mode == 'exact' else 'absolute'
        try:
            self._player.command_async('seek', target / 1000.0, seek_cmd, callback=self._on_seek_done)
        except Exception:
            self._seek_in_flight = False
            self._seek_applied_target = None
            self._schedule_seek_watchdog()
            return
        self._schedule_seek_watchdog()

    def _on_seek_done(self, error, result):
        # يعمل على خيط أحداث mpv؛ نكمل المعالجة في خيط الواجهة.
        self._call_after(self._finish_seek_after_command)

    def _finish_seek_after_command(self):
        if self._shutting_down:
            return
        self._seek_in_flight = False
        applied = self._seek_applied_target
        self._seek_applied_target = None
        if self._seek_target is not None and self._seek_target == applied:
            self._seek_target = None
        self._cancel_seek_watchdog()
        self._pump_seek_queue()

    def _schedule_seek_watchdog(self):
        if self._shutting_down:
            return
        if self._seek_watchdog is not None:
            return
        try:
            self._seek_watchdog = wx.CallLater(SEEK_WATCHDOG_MS, self._on_seek_watchdog)
        except Exception:
            self._seek_watchdog = None

    def _cancel_seek_watchdog(self):
        watchdog = getattr(self, "_seek_watchdog", None)
        self._seek_watchdog = None
        if watchdog is not None:
            try:
                watchdog.Stop()
            except Exception:
                pass

    def _on_seek_watchdog(self):
        self._seek_watchdog = None
        if self._shutting_down or self._player is None:
            self._seek_in_flight = False
            self._seek_applied_target = None
            return
        if self._seek_in_flight:
            # البحث المعلّق لم يكتمل (تعطّل نادر في سطر أوامر mpv)؛ أعد
            # إرسال الهدف بأمر متزامن حتى لا يتوقف التنقل نهائيًا.
            self._seek_in_flight = False
            target = self._seek_target
            if target is not None:
                self._seek_applied_target = target
                seek_cmd = 'absolute+exact' if self._seek_mode == 'exact' else 'absolute'
                try:
                    self._player.command('seek', target / 1000.0, seek_cmd)
                    self._seek_target = None
                except Exception:
                    self._seek_applied_target = None
                    self._seek_target = target
            else:
                self._seek_applied_target = None
        self._pump_seek_queue()

    def SetVolume(self, volume):
        self._volume = max(0.0, min(1.0, volume))
        if not self._shutting_down and self._player is not None:
            self._player.volume = self._volume * 100.0
        return True

    def GetVolume(self):
        return self._volume

    def SetPlaybackRate(self, rate):
        self._playback_rate = max(0.1, rate)
        if not self._shutting_down and self._player is not None:
            self._player.speed = self._playback_rate
        return True

    def GetPlaybackRate(self):
        return self._playback_rate

    def Refresh(self):
        pass

    def Update(self):
        pass

    def _destroy_mpv_player(self):
        self._shutting_down = True
        self._cancel_seek_watchdog()
        player = self._player
        self._player = None
        if player is None:
            return
        try:
            player.command("stop")
        except Exception:
            pass
        try:
            player.quit()
        except Exception:
            pass
        try:
            player.wait_for_shutdown(timeout=1.0)
        except Exception:
            pass
        try:
            player.terminate()
        except Exception:
            pass

    def Destroy(self):
        self._destroy_mpv_player()
        return super().Destroy()
