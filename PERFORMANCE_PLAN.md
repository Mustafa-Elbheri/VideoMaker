# خطة إصلاح اختناقات الأداء — video_maker

## النظرة العامة

المشروع يعاني من 6 اختناقات رئيسية تسبب بطء في التعامل مع المقاطع والتنقل على الخط الزمني.
هذه الخطة مرتبة حسب الأولوية والتأثير.

---

## الخطوة 1: إنشاء نظام Cache مركزي لنتائج ffmpeg/ffprobe

**الملفات المتأثرة:**
- `video_maker/video_editing.py` (الملف الرئيسي)
- ملف جديد: `video_maker/media_cache.py`

**المشكلة:**
- `get_media_duration(path)` يستدعي `has_video_stream(path)` → `media_info_text(path)` → `subprocess.run(ffmpeg -i)`
- ثم يستدعي `get_video_duration(path)` → `ffmpeg_parse_infos(path)` → `subprocess.run(ffprobe ...)`
- كل ملف يُعالج بـ 2-3 عمليات فرعية subprocess بدون أي تخزين مؤقت
- يوجد +100 استدعاء عبر المشروع كله

**الحل التفصيلي:**

### أ) إنشاء `video_maker/media_cache.py`

```python
import os
import threading

_media_cache = {}
_cache_lock = threading.Lock()

def _cache_key(path):
    """مفتاح فريد: المسار + الحجم + وقت التعديل الأخير."""
    try:
        stat = os.stat(path)
        return (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime)
    except OSError:
        return None

def cached_media_info(path):
    """يعيد dict تحتوي: duration, has_video, has_audio, info_text, raw_info.
    تُخزّن النتيجة مرة واحدة فقط لكل ملف."""
    key = _cache_key(path)
    if key is None:
        return None
    with _cache_lock:
        if key in _media_cache:
            return _media_cache[key]
    # خارج الـ lock: تشغيل ffprobe مرة واحدة فقط
    result = _probe_media(path)
    if result is not None:
        with _cache_lock:
            _media_cache[key] = result
    return result

def invalidate_media_cache(path=None):
    """حذف cache لملف محدد أو كل الـ cache."""
    with _cache_lock:
        if path is None:
            _media_cache.clear()
        else:
            key = _cache_key(path)
            if key in _media_cache:
                del _media_cache[key]

def clear_media_cache():
    """مسح كل الـ cache."""
    with _cache_lock:
        _media_cache.clear()
```

دالة `_probe_media(path)` تُشغّل `ffprobe` مرة واحدة وتعيد:
- `has_video`: هل يوجد stream فيديو (وليس attached_pic)
- `has_audio`: هل يوجد stream صوت
- `duration`: المدة من ffprobe
- `info_text`: نص `ffmpeg -i` الكامل (لمن يحتاج تفاصيل إضافية)
- `raw_info`: ناتج `ffmpeg_parse_infos` الكامل

### ب) تعديل `video_editing.py`

**1. تعديل `media_info_text(path)` (سطر 467):**
```python
def media_info_text(video_path):
    cached = cached_media_info(video_path)
    if cached and cached.get("info_text") is not None:
        return cached["info_text"]
    # fallback: subprocess كما هو
    command = [ffmpeg_binary(), "-i", video_path]
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    return process.stderr.decode("utf-8", errors="ignore")
```

**2. تعديل `has_video_stream(path)` (سطر 394):**
```python
def has_video_stream(path):
    cached = cached_media_info(path)
    if cached is not None:
        return cached.get("has_video", False)
    # fallback
    output = media_info_text(path)
    # ... الكود الحالي
```

**3. تعديل `get_media_duration(path)` (سطر 376):**
```python
def get_media_duration(path):
    cached = cached_media_info(path)
    if cached is not None:
        return cached.get("duration", 0.0)
    # fallback
    if has_video_stream(path):
        return get_video_duration(path)
    return get_audio_duration(path)
```

**4. تعديل `has_audio_stream(path)` (سطر 413):**
```python
def has_audio_stream(path):
    cached = cached_media_info(path)
    if cached is not None:
        return cached.get("has_audio", False)
    output = media_info_text(path)
    return any(" Audio: " in line for line in output.splitlines())
```

**5. تعديل `ffmpeg_parse_infos(path)` (سطر 1614):**
```python
def ffmpeg_parse_infos(path, *args, **kwargs):
    cached = cached_media_info(path)
    if cached and cached.get("raw_info") is not None:
        return cached["raw_info"]
    # fallback: الكود الحالي
```

**6. تعديل `parse_media_signature(video_path)` (سطر 478):**
```python
def parse_media_signature(video_path):
    cached = cached_media_info(video_path)
    if cached and cached.get("info_text") is not None:
        output = cached["info_text"]
    else:
        output = media_info_text(video_path)
    # ... بقية الكود كما هو
```

### ج) استيراد `cached_media_info` في `video_editing.py`

أضف في أعلى الملف:
```python
from video_maker.media_cache import cached_media_info, invalidate_media_cache
```

### د) إضافة `invalidate_media_cache` في الأماكن اللي تعدل ملفات

في الدوال اللي تُنشئ ملفات مخرجات جديدة (مثل `write_timeline_video`, `build_xfade_transition_segment`) أضف:
```python
invalidate_media_cache(output_path)
```

**النتيجة المتوقعة:** تقليل استدعاءات ffmpeg من 2-3 لكل ملف إلى 1 فقط (أو 0 لو من Cache).

---

## الخطوة 2: إصلاح `timeline_metrics` cache signature

**الملف المتأثر:** `video_maker/player_modules/preview.py`

**المشكلة:**
- سطر 10-11: `timeline_cache_signature()` تستخدم `id(segment)` كتوقيع
- `id()` في بايثون يُعاد تعيينه بعد GC — لو كائن جديد أخذ نفس id، يُخطأ cache أن المحتوى لم يتغير
- هذا يسبب أخطاء في حساب مواقع المقاطع والتنقل

**الحل:**
بدلاً من `id()` نستخدم محتوى المقاطع نفسها:

```python
# سطر 10-11: بدلاً من
def timeline_cache_signature(self):
    return tuple(id(segment) for segment in self.timeline)

# نكتب
def timeline_cache_signature(self):
    return tuple(
        (segment.path, segment.start, segment.end, segment.speed,
         segment.audio_volume, segment.transition, segment.transition_duration)
        for segment in self.timeline
    )
```

**ملاحظة:** `TimelineSegment` dataclass في `timeline.py` — كل حقولها معرّفة. التوقيع يشمل كل الحقول اللي تؤثر على `duration`.

**النتيجة المتوقعة:** إصلاح أخطاء التنقل الناتجة عن cache خاطئ.

---

## الخطوة 3: تحسين `capture_edit_state` لتقليل النسخ

**الملف المتأثر:** `video_maker/player_modules/state.py`

**المشكلة:**
- `capture_edit_state()` (سطر 23-56) تنسخ كامل الحالة لكل تعديل: 5 قوائم items + deepcopy للـ effect chain + clipboard + sets
- `record_edit()` (سطر 76) تستدعيها مرة ثانية بعد التعديل
- مع 100+ عنصر هذا نسخ ضخم

**الحل:**

### أ) تقليل النسخ في `capture_edit_state()`

**السطور 28-31:** بدلاً من نسخ كل عنصر dict بشكل مستقل:
```python
# بدلاً من:
"visual_items": [dict(item) for item in self.visual_items],
"background_audio_items": [dict(item) for item in self.background_audio_items],
"b_roll_items": [dict(item) for item in self.b_roll_items],
"sound_effects_items": [dict(item) for item in self.sound_effects_items],

# نكتب:
"visual_items": list(self.visual_items),
"background_audio_items": list(self.background_audio_items),
"b_roll_items": list(self.b_roll_items),
"sound_effects_items": list(self.sound_effects_items),
```

**ملاحظة:** القوائم دي مستقلة عن بعض — نسخ القائمة (shallow copy) يكفي لأن التعديلات على العناصر تتم على نسخ جديدة. لكن لاحظ أن `list()` فقط يعطيك shallow copy — العناصر نفسها (dicts) تبقى مشاركة. هذا آمن لأن:
- عند التعديل نُنشئ عنصر جديد ونضعه في القائمة
- لا نعدّل عنصر dict موجود في مكانها

**السطر 35:** `copy.deepcopy(self.main_audio_effect_chain)` — نحتفظ بـ deepcopy هنا لأن chains قد تُعدّل في مكانها.

**السطر 51:** `copy.deepcopy(self.element_clipboard)` — نحتفظ بـ deepcopy لأن الحافظة قد تُعدّل.

### ب) تحسين `record_edit()` (سطر 58-84)

**السطر 76:** `capture_edit_state()` تستدعى هنا لتسجيل الحالة الجديدة بعد التعديل. بدلاً من نسخ كامل:
```python
# نُمرر الحالة القديمة فقط لـ edit_history
# الحالة الجديدة تُلتقط عند الطلب فقط (lazy snapshot)
self.edit_history.record(operation, before_state, None)
```

**ملاحظة:** هذا يتطلب تعديل `edit_history.record()` ليدعم `None` كحالة جديدة، ويلتقط الحالة الحالية عند الحاجة (undo/redo). لكن هذا تعديل أrisk — الأفضل نبدأ بالنسخ الخفيف فقط (المقطع أ) ونترك `record_edit` كما هي في البداية.

**الأولوية:** تنفيذ المقطع أ فقط في البداية. المقطع ب اختياري ويتطلب مراجعة `edit_history.py`.

**النتيجة المتوقعة:** تقليل استهلاك الذاكرة وسرعة في كل تعديل.

---

## الخطوة 4: تحسين المؤقت الرئيسي وتقسيم أعمال OnTimer

**الملفات المتأثرة:**
- `video_maker/player.py` (سطر 228)
- `video_maker/player_modules/progress_context.py` (سطر 200+)

**المشكلة:**
- `timer.Start(15)` = ~67 مرة/ثانية
- `OnTimer` يفعل 10+ عمليات حسابية كل مرة
- بعض هذه العمليات غير ضرورية في كل دورة

**الحل:**

### أ) تغيير فترة المؤقت في `player.py` سطر 228
```python
# بدلاً من:
self.timer.Start(15)

# نكتب:
self.timer.Start(25)
```
هذا يُقلل التكرار من 67 إلى 40 مرة/ثانية — لا يزال كافياً لل視觉ية السلسة.

### ب) تقسيم أعمال OnTimer في `progress_context.py`

أضف عداد داخلي في بداية `OnTimer`:
```python
def OnTimer(self, event):
    # عداد لتقسيم الأعمال الثقيلة
    if not hasattr(self, '_timer_tick_count'):
        self._timer_tick_count = 0
    self._timer_tick_count += 1

    # أعمال خفيفة: كل مرة
    note_ui_heartbeat(self, ...)

    # أعمال متوسطة: كل 3 مرات (~75ms)
    if self._timer_tick_count % 3 == 0:
        if self.audio_effect_background_preview_state:
            self.sync_audio_effect_background_preview()

    # باقي الكود كما هو (validations, segment location, etc.)
```

**النتيجة المتوقعة:** تقليل حمل المعالج وتحسين استجابة الواجهة.

---

## الخطوة 5: تحسين ReliableAudioPlayer — تجنب إنشاء ffmpeg جديد

**الملف المتأثر:** `video_maker/reliable_playback.py`

**المشكلة:**
- `Seek()` (سطر 121-127): `_stop_process_locked()` ثم `_start_locked()` — توقف وتشغيل جديد
- `SetPlaybackRate()` (سطر 134-144): نفس الشيء
- كل تغيير في السرعة أو الموضع during playback يُشغّل ffmpeg جديد بالكامل

**الحل:**

### أ) تحسين `Seek()` (سطر 121)
```python
def Seek(self, seek_ms):
    with self.lock:
        self.position_ms = max(0, min(int(seek_ms or 0), self.Length()))
        if self.state == MEDIASTATE_PLAYING:
            # بدلاً من stop/start كامل:
            # نتوقف ونبدأ من الموضع الجديد فقط إذا كان الفارق كبيراً
            current = self.Tell()
            if abs(current - seek_ms) > 500:  # أكثر من 500ms فارق
                self._stop_process_locked()
                self._start_locked()
            # وإلا: نترك التشغيل مستمر ونُحدّث الموضع فقط
            # (ffmpeg سيصل تلقائياً عند pipe exhaustion)
        return True
```

### ب) تحسين `SetPlaybackRate()` (سطر 134)
```python
def SetPlaybackRate(self, rate):
    with self.lock:
        rate = max(0.05, float(rate or 1.0))
        if abs(rate - self.rate) <= 0.001:
            return True
        was_playing = self.state == MEDIASTATE_PLAYING
        self.position_ms = self.Tell()
        self.rate = rate
        if was_playing:
            # تغيير السرعة يتطلب إعادة تشغيل لأن atempo filter يعتمد على rate
            # لكن نُحسّن: نُوقف فقط إذا كان التغيير كبيراً
            if abs(rate - self.rate) > 0.5:
                self._stop_process_locked()
                self._start_locked()
            else:
                # صغير: نترك ffmpeg يعمل ونُحدّث فقط
                self._stop_process_locked()
                self._start_locked()
        return True
```

**ملاحظة:** تغيير السرعة في ffmpeg يتطلب `atempo` filter جديد — لا يمكن تغييره أثناء التشغيل بدون إعادة بناء pipeline. الحل الفعلي هو تقليل عدد مرات الإعادة فقط.

**بديل أفضل:** استخدام `command_factory` مع `seek_ms` parameter لتجنب إعادة بناء كل شيء:
```python
def Seek(self, seek_ms):
    with self.lock:
        self.position_ms = max(0, min(int(seek_ms or 0), self.Length()))
        if self.state == MEDIASTATE_PLAYING:
            self._stop_process_locked()
            self._start_locked()
        return True
```
(نفس السلوك الحالي لكن مع تحسين `_start_locked` ليكون أسرع)

### ج) تحسين `_start_locked()` ليكون أخف

تأكد أن `_start_locked()` تستخدم:
- `startupinfo=ffmpeg_startupinfo()` (موجود بالفعل)
- `-threads 1` (موجود في `FFMPEG_LOW_MEMORY_INPUT_OPTIONS`)
- timeout قصير للعملية الفرعية

**النتيجة المتوقعة:** تقليل تأخيرات التشغيل أثناء التحريك والتنقل.

---

## الخطوة 6 (اختيارية): تحسين scrub_audio decode window

**الملف المتأثر:** `video_maker/scrub_audio.py`

**المشكلة:** عند فك ترميز الشرائح، يُشغّل `ffmpeg` كل مرة حتى مع نافذة التخزين المؤقت.

**الحل:** توسعة نافذة التخزين المؤقت لتغطي نطاق أوسع.

**ملاحظة:** هذا يتطلب تعديل `decode_slice()` و `DEFAULT_SLICE_MS` و逻辑of cache window. implemented فقط إذا كانت الخطوات 1-5 لا تكفي.

---

## ملخص الترتيب

| الخطوة | الوصف | التأثير | الصعوبة | الوقت |
|--------|-------|---------|---------|-------|
| 1 | Cache مركزي لـ ffmpeg/ffprobe | أعلى | متوسطة | ~1 ساعة |
| 2 | إصلاح timeline_metrics signature | مرتفع (أخطاء) | سهلة | ~15 دقيقة |
| 3 | تحسين capture_edit_state | مرتفع | متوسطة | ~45 دقيقة |
| 4 | تحسين المؤقت و OnTimer | متوسط-مرتفع | سهلة | ~30 دقيقة |
| 5 | تحسين ReliableAudioPlayer | متوسط | متوسطة | ~1 ساعة |
| 6 | تحسين scrub_audio (اختياري) | منخفض-متوسط | متوسطة | ~30 دقيقة |

---

## طريقة العمل

1. أقرأ الخطوة المطلوبة من هذا الملف
2. أنفذ التعديلات المطلوبة
3. أشغّل الاختبارات: `python -m pytest tests/test_tracks.py tests/test_track_items.py -v`
4. أتأكد أن لا يوجد تعديلات جانبية غير مقصودة
5. أنتقل للخطوة التالية بعد موافقة المستخدم
