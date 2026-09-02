import threading
import time
from collections import deque

import accessible_output2

from accessible_output2.outputs.auto import OutputError

try:
    import wx
except Exception:  # pragma: no cover - the application itself requires wx.
    wx = None


EXCLUDED_OUTPUTS = {"SAPI5"}
NVDA_INITIAL_SETTLE_DELAY_MS = 60
NVDA_STABLE_FOCUS_DELAY_MS = 140
NVDA_MAX_SETTLE_DELAY_MS = 750
NVDA_SILENCE_GUARD_INTERVAL_MS = 35
NVDA_POST_SETTLE_QUIET_DELAY_MS = 90


class ScreenReaderSpeech:
    """Send application feedback through the active screen reader.

    NVDA's controller client queues application speech asynchronously. A focus
    or window announcement triggered by the same UI action can therefore race
    with application feedback. For NVDA only, feedback is held until the active
    window and keyboard focus settle. Interrupting feedback briefly suppresses
    UI-triggered announcements while settling and through a short post-settle
    quiet period. It then queues one final cancellation immediately followed
    by the application message without another cancel, making that message
    the final command. Other screen readers retain the original immediate
    behaviour.
    """

    def __init__(self):
        self.outputs = []
        for output_class in accessible_output2.get_output_classes():
            if output_class.__name__ in EXCLUDED_OUTPUTS:
                continue
            try:
                self.outputs.append(output_class())
            except OutputError:
                pass
            except Exception:
                pass

        self._lock = threading.RLock()
        self._pending_nvda_speech = deque()
        self._settle_timer = None
        self._silence_timer = None
        self._quiet_timer = None
        self._silence_guard_active = False
        self._settle_started_at = 0.0
        self._stable_since = 0.0
        self._last_focus_token = None
        self._phase = "idle"
        self._cycle_id = 0

    def get_active_output(self):
        for output in self.outputs:
            try:
                if output.is_active():
                    return output
            except Exception:
                continue
        return None

    @staticmethod
    def _is_nvda_output(output):
        if output is None:
            return False
        name = getattr(output, "name", "")
        class_name = output.__class__.__name__
        return str(name).strip().lower() == "nvda" or class_name.lower() == "nvda"

    @staticmethod
    def _speak_with_output(output, text, interrupt):
        try:
            output.speak(text, interrupt=interrupt)
        except TypeError:
            output.speak(text)
        except Exception:
            return

    @staticmethod
    def _silence_with_output(output):
        try:
            output.silence()
            return True
        except Exception:
            return False

    @staticmethod
    def _wx_main_loop_running():
        if wx is None:
            return False
        try:
            app = wx.GetApp()
        except Exception:
            return False
        if app is None:
            return False
        try:
            return bool(app.IsMainLoopRunning())
        except Exception:
            return True

    @staticmethod
    def _window_token(window):
        if window is None:
            return None
        try:
            handle = int(window.GetHandle())
            if handle:
                return ("handle", handle)
        except Exception:
            pass
        return ("object", id(window))

    def _focus_token(self):
        if wx is None:
            return None
        try:
            focus = wx.Window.FindFocus()
        except Exception:
            focus = None
        try:
            active = wx.GetActiveWindow()
        except Exception:
            active = None
        return (self._window_token(active), self._window_token(focus))

    @staticmethod
    def _stop_timer(timer):
        if timer is None:
            return
        try:
            timer.Stop()
        except Exception:
            pass

    def _stop_settle_timer_locked(self):
        timer = self._settle_timer
        self._settle_timer = None
        self._stop_timer(timer)

    def _stop_silence_timer_locked(self):
        timer = self._silence_timer
        self._silence_timer = None
        self._stop_timer(timer)

    def _stop_quiet_timer_locked(self):
        timer = self._quiet_timer
        self._quiet_timer = None
        self._stop_timer(timer)

    def _stop_silence_guard_locked(self):
        self._silence_guard_active = False
        self._stop_silence_timer_locked()

    def _invalidate_timers_locked(self):
        self._cycle_id += 1
        self._stop_settle_timer_locked()
        self._stop_silence_guard_locked()
        self._stop_quiet_timer_locked()
        return self._cycle_id

    def _schedule_settle_check_locked(self, delay_ms, cycle_id):
        self._stop_settle_timer_locked()
        delay_ms = max(1, int(delay_ms))
        try:
            self._settle_timer = wx.CallLater(
                delay_ms,
                self._check_nvda_settled,
                cycle_id,
            )
        except Exception:
            try:
                wx.CallAfter(self._check_nvda_settled, cycle_id)
            except Exception:
                self._phase = "idle"

    def _schedule_silence_tick_locked(self, cycle_id):
        self._stop_silence_timer_locked()
        if not self._silence_guard_active:
            return
        try:
            self._silence_timer = wx.CallLater(
                NVDA_SILENCE_GUARD_INTERVAL_MS,
                self._silence_guard_tick,
                cycle_id,
            )
        except Exception:
            self._silence_timer = None

    def _schedule_quiet_locked(self, cycle_id, focus_token):
        self._stop_quiet_timer_locked()
        try:
            self._quiet_timer = wx.CallLater(
                NVDA_POST_SETTLE_QUIET_DELAY_MS,
                self._finish_nvda_quiet,
                cycle_id,
                focus_token,
            )
        except Exception:
            try:
                wx.CallAfter(
                    self._finish_nvda_quiet,
                    cycle_id,
                    focus_token,
                )
            except Exception:
                self._phase = "idle"

    def _silence_active_nvda_locked(self):
        output = self.get_active_output()
        if self._is_nvda_output(output):
            self._silence_with_output(output)

    def _start_silence_guard_locked(self, cycle_id):
        self._silence_guard_active = True
        # Cancel speech immediately because the UI action may already have
        # queued a window-title or control announcement before say() is called.
        self._silence_active_nvda_locked()
        self._schedule_silence_tick_locked(cycle_id)

    def _silence_guard_tick(self, cycle_id):
        with self._lock:
            self._silence_timer = None
            if (
                cycle_id != self._cycle_id
                or self._phase not in {"settling", "quieting"}
                or not self._silence_guard_active
                or not self._pending_nvda_speech
                or not any(
                    interrupt
                    for _text, interrupt in self._pending_nvda_speech
                )
            ):
                return

            # Keep the cancellation and state check under the same lock. More
            # importantly, all cancellation ticks stop before the application
            # message is queued, so none can be issued after it.
            self._silence_active_nvda_locked()
            self._schedule_silence_tick_locked(cycle_id)

    def _reset_settle_state_locked(self, now):
        self._settle_started_at = now
        self._stable_since = 0.0
        self._last_focus_token = None

    def _begin_settle_cycle_locked(self, use_silence_guard):
        cycle_id = self._invalidate_timers_locked()
        now = time.monotonic()
        self._phase = "settling"
        self._reset_settle_state_locked(now)
        if use_silence_guard:
            self._start_silence_guard_locked(cycle_id)
        self._schedule_settle_check_locked(
            NVDA_INITIAL_SETTLE_DELAY_MS,
            cycle_id,
        )
        return cycle_id

    def _take_pending_locked(self):
        pending = list(self._pending_nvda_speech)
        self._pending_nvda_speech.clear()
        self._stop_settle_timer_locked()
        self._stop_silence_guard_locked()
        self._stop_quiet_timer_locked()
        self._phase = "idle"
        self._last_focus_token = None
        self._stable_since = 0.0
        self._settle_started_at = 0.0
        self._cycle_id += 1
        return pending

    def _deliver_pending(self, pending, nvda_pre_silenced=False):
        for text, interrupt in pending:
            output = self.get_active_output()
            if not output:
                continue
            effective_interrupt = interrupt
            if (
                nvda_pre_silenced
                and interrupt
                and self._is_nvda_output(output)
            ):
                # The guarded cycle has already queued the final cancellation
                # and stopped every guard timer. Sending another interrupt here
                # would create an unnecessary asynchronous cancel which can
                # race with the message itself. Speak normally so the application
                # message is the final queued command.
                effective_interrupt = False
            self._speak_with_output(output, text, effective_interrupt)

    def _enqueue_nvda_speech(self, text, interrupt, wait_for_ui):
        pending_to_deliver = None

        with self._lock:
            if interrupt:
                # A newer interrupting message supersedes pending feedback and
                # starts a fresh settle cycle for the newest UI state.
                self._pending_nvda_speech.clear()

            queue_was_empty = not self._pending_nvda_speech
            self._pending_nvda_speech.append((text, bool(interrupt)))

            if not wait_for_ui:
                pending_to_deliver = self._take_pending_locked()
            elif interrupt:
                self._begin_settle_cycle_locked(use_silence_guard=True)
            elif queue_was_empty or self._phase == "idle":
                self._begin_settle_cycle_locked(use_silence_guard=False)
            # A non-interrupting message added to an existing cycle keeps the
            # existing timing and order.

        if pending_to_deliver is not None:
            self._deliver_pending(pending_to_deliver)

    def _check_nvda_settled(self, cycle_id):
        pending_to_deliver = None

        with self._lock:
            self._settle_timer = None
            if (
                cycle_id != self._cycle_id
                or self._phase != "settling"
                or not self._pending_nvda_speech
            ):
                return

            now = time.monotonic()
            token = self._focus_token()
            elapsed_ms = (now - self._settle_started_at) * 1000.0

            if elapsed_ms >= NVDA_MAX_SETTLE_DELAY_MS:
                settled = True
            elif token != self._last_focus_token:
                self._last_focus_token = token
                self._stable_since = now
                self._schedule_settle_check_locked(
                    NVDA_STABLE_FOCUS_DELAY_MS,
                    cycle_id,
                )
                return
            else:
                stable_ms = (now - self._stable_since) * 1000.0
                if stable_ms < NVDA_STABLE_FOCUS_DELAY_MS:
                    self._schedule_settle_check_locked(
                        NVDA_STABLE_FOCUS_DELAY_MS - stable_ms,
                        cycle_id,
                    )
                    return
                settled = True

            if not settled:
                return

            has_interrupt = any(
                interrupt for _text, interrupt in self._pending_nvda_speech
            )
            if has_interrupt:
                # Keep suppressing late UI announcements for a short quiet
                # period after focus first appears stable. This absorbs window
                # title and control events which NVDA may process slightly
                # later than wx reports the focus change.
                self._phase = "quieting"
                self._stop_settle_timer_locked()
                self._silence_active_nvda_locked()
                self._schedule_quiet_locked(cycle_id, token)
            else:
                pending_to_deliver = self._take_pending_locked()

        if pending_to_deliver is not None:
            self._deliver_pending(pending_to_deliver)

    def _finish_nvda_quiet(self, cycle_id, expected_focus_token):
        pending_to_deliver = None

        with self._lock:
            self._quiet_timer = None
            if (
                cycle_id != self._cycle_id
                or self._phase != "quieting"
                or not self._pending_nvda_speech
            ):
                return

            current_token = self._focus_token()
            elapsed_ms = (time.monotonic() - self._settle_started_at) * 1000.0
            if (
                current_token != expected_focus_token
                and elapsed_ms < NVDA_MAX_SETTLE_DELAY_MS
            ):
                # The UI moved again during the quiet period. Continue the
                # guarded settle cycle and wait for the new focus to stabilise.
                now = time.monotonic()
                self._phase = "settling"
                self._last_focus_token = current_token
                self._stable_since = now
                self._silence_active_nvda_locked()
                self._schedule_settle_check_locked(
                    NVDA_STABLE_FOCUS_DELAY_MS,
                    cycle_id,
                )
                return

            # Queue one final cancel, stop every guard timer, and immediately
            # queue the application message without another interrupt. There
            # is no release gap in which NVDA can begin speaking one character
            # of the window title, and no later timer can cancel the message.
            self._silence_active_nvda_locked()
            self._stop_silence_guard_locked()
            pending_to_deliver = self._take_pending_locked()

        if pending_to_deliver is not None:
            self._deliver_pending(
                pending_to_deliver,
                nvda_pre_silenced=True,
            )

    def _say_on_ui_thread(self, text, interrupt, wait_for_ui):
        output = self.get_active_output()
        if not output:
            return
        if self._is_nvda_output(output):
            self._enqueue_nvda_speech(text, interrupt, wait_for_ui)
            return
        self._speak_with_output(output, text, interrupt)

    def say(self, text, interrupt=True, wait_for_ui=True):
        if text is None:
            return
        text = str(text)
        if not text:
            return

        if not self._wx_main_loop_running():
            output = self.get_active_output()
            if output:
                self._speak_with_output(output, text, interrupt)
            return

        try:
            on_main_thread = wx.IsMainThread()
        except Exception:
            on_main_thread = True

        if on_main_thread:
            self._say_on_ui_thread(text, interrupt, wait_for_ui)
            return

        try:
            wx.CallAfter(
                self._say_on_ui_thread,
                text,
                bool(interrupt),
                bool(wait_for_ui),
            )
        except Exception:
            output = self.get_active_output()
            if output:
                self._speak_with_output(output, text, interrupt)
