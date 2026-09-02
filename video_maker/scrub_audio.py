# -*- coding: utf-8 -*-
"""شريحة الصوت عند تحريك رأس التشغيل أثناء التوقف (Audio Scrubbing).

عند الضغط على أسهم التقديم/الترجيع والمشغل في حالة توقف، نستخرج شريحة
صوتية مجهرية (20-50ms) من الموقع الزمني الجديد ونشغّلها فورًا مع تنعيم
الأطراف لمنع الطقطقة.

السلوك مستوحى من برنامج REAPER: سرعة الـ Scrubbing تتبع مقدار الحركة —
كلما كانت خطوة التقديم أكبر كلما سُمعت الشريحة أسرع (مع تغير الدرجة
كشريط مسجل)، وكلما صغرت الخطوة كلما هبطت السرعة والدرجة.

التصميم:
- خيط واحد دائم + دفق صوتي واحد دائم (OutputStream) يُفتح مرة واحدة،
  وهذا يمنع تكدس الخيوط وتكرار فتح/إغلاق الأجهزة عند الضغط المطول.
- طلبات "الأحدث يفوز": أي طلب جديد يُلغي ما قبله قبل بدء تشغيله.
- ذاكرة مؤقتة لنافذة فك الترميز: نفك نافذة واسعة مرة واحدة ونقطع منها
  الشرائح اللاحقة فورًا أثناء الضغط المطول دون إعادة تشغيل ffmpeg.

هذا الملف مستقل عن wxPython قدر الإمكان ليكون قابلًا للاختبار.
"""
from __future__ import annotations

import collections
import math
import os
import subprocess
import threading

from video_maker.app_paths import ffmpeg_binary
from video_maker.audio_devices import selected_sounddevice_output_device
from video_maker.video_editing import ffmpeg_startupinfo
from video_maker.reliable_playback import reliable_audio_available

try:
    import numpy as np
    import sounddevice as sd
    AUDIO_IMPORT_ERROR = ""
except Exception as error:
    np = None
    sd = None
    AUDIO_IMPORT_ERROR = str(error)


SAMPLE_RATE = 48000
CHANNELS = 2
DTYPE = "float32"
BYTES_PER_SAMPLE = 4
AUDIO_BLOCK_FRAMES = 512
DEFAULT_SLICE_MS = 40
DEFAULT_FADE_MS = 3
CROSSFADE_MS = 8
CROSSFADE_FRAMES = int(SAMPLE_RATE * CROSSFADE_MS / 1000.0)
DECODE_TIMEOUT = 5.0
FINE_SCRUB_RATE = 0.55
MIN_SCRUB_RATE = 0.35
MAX_SCRUB_RATE = 2.0
CACHE_MIN_MS = 2000
CACHE_MAX_MS = 10000
CACHE_STEP_FACTOR = 3.0
THREAD_STOP_TIMEOUT = 1.0


def scrub_rate_for_step(delta_seconds, fine=False):
    """سرعة الـ Scrubbing تتبع مقدار الحركة (نمط REAPER).

    خطوة 1 ثانية تُسمع عند نصف السرعة (بطيء مع انخفاض الدرجة)، وخطوة
    4 ثوانٍ عند السرعة الطبيعية، وخطوة 16 ثانية فأكثر عند ضعف السرعة.
    الحركات الدقيقة (fine) تبقى بطيئة دائمًا.
    """
    if fine:
        return FINE_SCRUB_RATE
    try:
        step = abs(float(delta_seconds or 0.0))
    except (TypeError, ValueError):
        step = 1.0
    if step <= 0.001:
        return 1.0
    rate = 0.5 * math.sqrt(step)
    return max(MIN_SCRUB_RATE, min(MAX_SCRUB_RATE, rate))


def cache_width_for_step(step_file_ms, slice_ms=DEFAULT_SLICE_MS):
    """عرض نافذة فك الترميز المؤقتة بالمللي ثانية داخل الملف.

    يجب أن تغطي خطوتين متتاليتين على الأقل حتى تصلنا الشرائح المتتالية
    من الذاكرة دون إعادة فك ترميز أثناء الضغط المطول.
    """
    try:
        step_file_ms = max(0.0, float(step_file_ms or 0.0))
    except (TypeError, ValueError):
        step_file_ms = 0.0
    try:
        slice_ms = max(5.0, float(slice_ms or DEFAULT_SLICE_MS))
    except (TypeError, ValueError):
        slice_ms = DEFAULT_SLICE_MS
    width = step_file_ms * CACHE_STEP_FACTOR + slice_ms * 2.0
    return max(CACHE_MIN_MS, min(CACHE_MAX_MS, width))


def decode_slice(path, center_file_ms, window_file_ms):
    """فك ترميز نافذة قصيرة من ملف الصوت إلى float32 بمعدل SAMPLE_RATE.

    ``center_file_ms`` هو موضع وسط النافذة داخل الملف بالمللي ثانية،
    و``window_file_ms`` عرض النافذة. الوسط يسمح بسماع ما قبل الموضع
    وما بعده عند الاقتطاع.
    """
    if np is None:
        return None
    if not path or not os.path.exists(str(path)):
        return None
    window_file_ms = max(5.0, float(window_file_ms or DEFAULT_SLICE_MS))
    half = window_file_ms / 2.0
    start_seconds = max(0.0, (float(center_file_ms or 0.0) - half) / 1000.0)
    duration_seconds = window_file_ms / 1000.0
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_seconds:.4f}",
        "-t",
        f"{duration_seconds:.4f}",
        "-i",
        str(path),
        "-vn",
        "-af",
        "aresample=48000",
        "-f",
        "f32le",
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
            timeout=DECODE_TIMEOUT,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    data = result.stdout
    bytes_per_frame = CHANNELS * BYTES_PER_SAMPLE
    usable = len(data) - (len(data) % bytes_per_frame)
    if usable <= 0:
        return None
    return np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, CHANNELS).copy()


def apply_volume(samples, volume):
    """تطبيق مستوى الصوت مع حصر الإشارة داخل النطاق القانوني."""
    if samples is None or len(samples) == 0:
        return samples
    try:
        volume = max(0.0, min(1.0, float(volume if volume is not None else 1.0)))
    except (TypeError, ValueError):
        volume = 1.0
    if volume >= 1.0:
        return samples
    return np.clip(samples * volume, -1.0, 1.0)


def apply_tape_rate(samples, rate):
    """إعادة تشكيل تتغير فيها المدة والدرجة معًا (أسلوب الشريط).

    ``rate < 1`` يطيل العينة ويُخفض درجتها، و``rate > 1`` يسرّعها
    ويرفع درجتها، تمامًا كشريط مسجل وهو يتحرك أسرع أو أبطأ.
    """
    if samples is None or len(samples) == 0:
        return samples
    try:
        rate = max(MIN_SCRUB_RATE, min(MAX_SCRUB_RATE, float(rate or 1.0)))
    except (TypeError, ValueError):
        rate = 1.0
    if abs(rate - 1.0) <= 0.001:
        return samples
    frame_count = len(samples)
    out_frames = max(1, int(round(frame_count / rate)))
    x_old = np.linspace(0.0, 1.0, frame_count, dtype=np.float64)
    x_new = np.linspace(0.0, 1.0, out_frames, dtype=np.float64)
    out = np.empty((out_frames, CHANNELS), dtype=np.float32)
    for channel in range(CHANNELS):
        out[:, channel] = np.interp(x_new, x_old, samples[:, channel].astype(np.float64)).astype(np.float32)
    return out


def apply_fade(samples, fade_ms=DEFAULT_FADE_MS):
    """تنعيم أطراف الشريحة بفايد داخلي/خارجي لمنع الطقطقة (Clicks/Pops)."""
    if samples is None or len(samples) == 0:
        return samples
    frame_count = len(samples)
    fade_frames = max(1, int(round(SAMPLE_RATE * max(0.0, float(fade_ms or 0.0)) / 1000.0)))
    fade_frames = min(fade_frames, max(1, frame_count // 2))
    if fade_frames < 2:
        return samples
    ramp = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
    samples[:fade_frames] = samples[:fade_frames] * ramp[:, None]
    samples[-fade_frames:] = samples[-fade_frames:] * ramp[::-1][:, None]
    return samples


def build_scrub_samples(request):
    """تجهيز عينة الاقتطاع الكاملة من بيانات الطلب (فك + صوت + سرعة + تنعيم)."""
    if not request or np is None:
        return None
    samples = decode_slice(
        request.get("path", ""),
        request.get("center_file_ms", 0.0),
        request.get("window_file_ms", DEFAULT_SLICE_MS),
    )
    if samples is None or len(samples) == 0:
        return None
    samples = apply_volume(samples, request.get("volume", 1.0))
    samples = apply_tape_rate(samples, request.get("rate", 1.0))
    samples = apply_fade(samples, DEFAULT_FADE_MS)
    return samples


def scrub_request_for_timeline_point(
    *,
    timeline,
    timeline_time,
    has_override=False,
    override_path="",
    output_volume=1.0,
    rate=1.0,
    window_ms=DEFAULT_SLICE_MS,
    step_seconds=None,
):
    """حساب ملف الصوت وموضعه بداخله لموقع زمني معين في الخط الزمني.

    إذا وُجد صوت مشروع بديل فهو المصدر الفعلي المسموع عند التشغيل
    (بتطابق زمني 1:1). وإلا يُستخدم صوت المقطع نفسه مع مراعاة موضع بداية
    الصوت وسرعة المقطع ومستوى صوته. يُرجع None عندما لا يوجد صوت مسموع
    (كتم أو ملف مفقود).
    """
    if not timeline:
        return None
    try:
        timeline_time = max(0.0, float(timeline_time or 0.0))
    except (TypeError, ValueError):
        return None
    try:
        step_seconds = max(0.0, float(step_seconds or 0.0))
    except (TypeError, ValueError):
        step_seconds = 0.0
    from video_maker.timeline import locate_segment
    from video_maker.video_editing import segment_audio_start, segment_audio_volume

    _index, segment, segment_position = locate_segment(timeline, timeline_time)
    if segment is None:
        return None
    speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
    try:
        volume = max(0.0, min(1.0, float(output_volume or 1.0))) * segment_audio_volume(segment)
    except (TypeError, ValueError):
        volume = 0.0
    if volume <= 0.001:
        return None
    window_ms = max(5.0, float(window_ms or DEFAULT_SLICE_MS))

    if has_override and override_path and os.path.exists(str(override_path)):
        step_file_ms = step_seconds * 1000.0
        return {
            "path": str(override_path),
            "center_file_ms": timeline_time * 1000.0,
            "window_file_ms": window_ms,
            "cache_file_ms": cache_width_for_step(step_file_ms, window_ms),
            "volume": volume,
            "rate": max(MIN_SCRUB_RATE, min(MAX_SCRUB_RATE, float(rate or 1.0))),
        }

    audio_path = str(getattr(segment, "audio_path", "") or segment.path)
    if not audio_path or not os.path.exists(audio_path):
        return None
    local_time = segment.start + (timeline_time - segment_position) * speed
    local_time = min(max(float(segment.start), local_time), max(float(segment.start), float(segment.end) - 0.001))
    audio_start = segment_audio_start(segment)
    center_file_ms = (audio_start + max(0.0, local_time - float(segment.start))) * 1000.0
    step_file_ms = step_seconds * 1000.0 * speed
    return {
        "path": audio_path,
        "center_file_ms": center_file_ms,
        "window_file_ms": window_ms * speed,
        "cache_file_ms": cache_width_for_step(step_file_ms, window_ms * speed),
        "volume": volume,
        "rate": max(MIN_SCRUB_RATE, min(MAX_SCRUB_RATE, float(rate or 1.0))),
    }


class ScrubPlayer:
    """مشغّل شرائح بخيط فك ترميز ودفق صوتي بنمط الاستدعاء (Callback).

    - طلب واحد في كل مرة: أي ضغطة جديدة تُلغي ما قبلها قبل بدء التشغيل
      حتى لا تتكدس أصوات قديمة (الأحدث يفوز).
    - دفق الخرج يعمل بنمط الاستدعاء: لا نستدعي Pa_WriteStream المحجوب
      أبدًا، بل يمرر PortAudio العينة من ذاكرة مشتركة محمية، وهذا يمنع
      الانهيار الأصلي الذي يحدث عند الضغط المطول والتنقل السريع.
    - فك الترميز يجري في الخيط الخلفي ولا يمس واجهة التحكم أو قارئ الشاشة.
    - الذاكرة المؤقتة تجعل الشرائح المتتالية أثناء الضغط المطول فورية.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.queue_lock = threading.Lock()
        self.stream_lock = threading.Lock()
        self.cb_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.shutdown_event = threading.Event()
        self.thread = None
        self.stream = None
        self.pending = collections.deque()
        self.cache_path = ""
        self.cache_start_ms = 0.0
        self.cache_samples = None
        self.last_error = ""
        self.cb_samples = None
        self.cb_offset = 0

    @property
    def available(self):
        if np is None or sd is None:
            return False
        try:
            return reliable_audio_available()
        except Exception:
            return False

    def play_request(self, request):
        if not request or np is None or sd is None:
            return False
        if not self.available:
            return False
        with self.queue_lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
            self.pending.clear()
            self.pending.append(dict(request))
        self.stop_event.clear()
        self.wake_event.set()
        return True

    def stop(self):
        with self.queue_lock:
            self.pending.clear()
        self.stop_event.set()
        self.wake_event.set()
        with self.lock:
            self.cache_samples = None
            self.cache_path = ""
        with self.cb_lock:
            self.cb_samples = None
            self.cb_offset = 0

    def shutdown(self):
        self.shutdown_event.set()
        self.stop()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=THREAD_STOP_TIMEOUT)
            except Exception:
                pass
        self._close_stream()

    def _close_stream(self):
        with self.stream_lock:
            stream = self.stream
            self.stream = None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def _run(self):
        try:
            while not self.shutdown_event.is_set():
                self.wake_event.wait(timeout=0.05)
                self.wake_event.clear()
                if self.shutdown_event.is_set():
                    break
                request = None
                with self.queue_lock:
                    if self.pending:
                        request = self.pending.popleft()
                        self.pending.clear()
                if request is None:
                    continue
                if self.stop_event.is_set() or self.shutdown_event.is_set():
                    continue
                samples = self._samples_for(request)
                if samples is None or len(samples) == 0:
                    continue
                with self.queue_lock:
                    stale = bool(self.pending)
                if stale or self.stop_event.is_set() or self.shutdown_event.is_set():
                    continue
                self._publish(samples)
        finally:
            self._close_stream()

    def _ensure_stream(self):
        if self.stream is not None:
            return True
        try:
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=AUDIO_BLOCK_FRAMES,
                latency="low",
                callback=self._stream_callback,
                device=selected_sounddevice_output_device(sd),
            )
            stream.start()
            self.stream = stream
            return True
        except Exception as error:
            self.last_error = str(error)
            return False

    def _publish(self, samples):
        if np is None or samples is None or len(samples) == 0:
            return
        with self.stream_lock:
            if self.stream is None:
                if not self._ensure_stream():
                    return
            stream = self.stream
        if stream is None:
            return
        blended = self._crossfade_into(samples)
        with self.cb_lock:
            self.cb_samples = blended
            self.cb_offset = 0
        try:
            if not stream.active:
                stream.start()
        except Exception:
            pass

    def _crossfade_into(self, samples):
        """دمج ذيل الشريحة السابقة (الجارية) مع رأس الشريحة الجديدة بسلاسة.

        عند وصول طلب جديد بينما الشريحة السابقة لا تزال تُشغَّل، كان
        الاستبدال الجاف يقطع الصوت في منتصفه فيُسمَع تقطيع/بُوب. هنا ننسّج
        نافذة crossfade قصيرة بين ذيل القديمة ورأس الجديدة حتى لا ينقطع
        الصوت فجأة مع بقاء نموذج "الأحدث يفوز" وسلامة الخيط.
        """
        if np is None or samples is None:
            return samples
        with self.cb_lock:
            old = self.cb_samples
            old_offset = self.cb_offset
        if old is None or old_offset is None or old_offset >= len(old):
            return samples
        remaining = len(old) - old_offset
        overlap = min(CROSSFADE_FRAMES, remaining, len(samples))
        if overlap < 2:
            return samples
        tail = old[old_offset:old_offset + overlap].copy()
        head = samples[:overlap].copy()
        ramp = np.linspace(0.0, 1.0, overlap, dtype=np.float32)[:, None]
        blended_tail = tail * (1.0 - ramp) + head * ramp
        return np.concatenate([blended_tail, samples[overlap:]], axis=0)

    def _stream_callback(self, outdata, frames, time_info, status):
        try:
            with self.cb_lock:
                data = self.cb_samples
                offset = self.cb_offset
            if data is None or offset >= len(data):
                outdata.fill(0.0)
                return
            end = min(len(data), offset + frames)
            outdata[:end - offset] = data[offset:end]
            if end - offset < frames:
                outdata[end - offset:].fill(0.0)
            with self.cb_lock:
                if self.cb_samples is data:
                    self.cb_offset = end
        except Exception:
            try:
                outdata.fill(0.0)
            except Exception:
                pass

    def _samples_for(self, request):
        if np is None:
            return None
        path = request.get("path", "")
        try:
            center = float(request.get("center_file_ms") or 0.0)
        except (TypeError, ValueError):
            return None
        window_ms = max(5.0, float(request.get("window_file_ms") or DEFAULT_SLICE_MS))
        cache_ms = float(request.get("cache_file_ms") or CACHE_MIN_MS)
        half_window_frames = int(round(window_ms / 2000.0 * SAMPLE_RATE))
        need_frames = int(round(window_ms / 1000.0 * SAMPLE_RATE))

        with self.lock:
            cache_path = self.cache_path
            cache_start = self.cache_start_ms
            cache = self.cache_samples

        slice_samples = None
        if cache is not None and cache_path == path and len(cache) > 0:
            cache_end_ms = cache_start + len(cache) / float(SAMPLE_RATE) * 1000.0
            margin = max(10.0, window_ms / 2.0)
            if (cache_start + margin) <= center <= (cache_end_ms - margin):
                start_frame = int(round((center - cache_start) / 1000.0 * SAMPLE_RATE)) - half_window_frames
                start_frame = max(0, start_frame)
                segment = cache[start_frame:start_frame + need_frames]
                if len(segment) > 0:
                    slice_samples = segment.copy()

        if slice_samples is None:
            decoded = decode_slice(path, center, cache_ms)
            if decoded is None or len(decoded) == 0:
                return None
            with self.lock:
                self.cache_path = path
                self.cache_start_ms = max(0.0, center - cache_ms / 2.0)
                self.cache_samples = decoded
                cache_start = self.cache_start_ms
            start_frame = int(round((center - cache_start) / 1000.0 * SAMPLE_RATE)) - half_window_frames
            start_frame = max(0, start_frame)
            slice_samples = decoded[start_frame:start_frame + need_frames].copy()

        if slice_samples is None or len(slice_samples) == 0:
            return None
        samples = apply_volume(slice_samples, request.get("volume", 1.0))
        samples = apply_tape_rate(samples, request.get("rate", 1.0))
        samples = apply_fade(samples, DEFAULT_FADE_MS)
        return samples
