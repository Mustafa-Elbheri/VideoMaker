import glob
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from typing import Optional

import requests
import wx
from video_maker.localization import tr

from video_maker.app_state import read_preferences, write_preferences
from video_maker.image_overlay import replace_image_overlay_range
from video_maker.program_modes import PROFESSIONAL_MODE, get_program_mode
from video_maker.text_overlay import TextOverlayOptions, TextOverlayDialog, build_text_overlay_segment, render_text_image, render_typing_video
from video_maker.track_items import from_grok_caption


def ffmpeg_startupinfo():
    info = None
    if os.name == "nt":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def _run_ffmpeg(args, **kwargs):
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + [a for a in args if a != "-y"]
    args.insert(1, "-y")
    kwargs["startupinfo"] = ffmpeg_startupinfo()
    return subprocess.run(args, **kwargs)


def _run_ffprobe(args, **kwargs):
    args = ["ffprobe", "-v", "error"] + args
    kwargs["startupinfo"] = ffmpeg_startupinfo()
    return subprocess.run(args, **kwargs)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SubtitleSegment:
    start: float
    end: float
    text: str


# ---------------------------------------------------------------------------
# Groq Key Manager
# ---------------------------------------------------------------------------

class GroqKeyManager:
    CONFIG_KEY = "groq_keys"
    EXHAUSTED_UNTIL_KEY = "groq_exhausted_until"
    ENV_VAR = "GROQ_API_KEY"
    BACKUP_FILE = "groq_keys.json"

    @staticmethod
    def _prefs():
        return read_preferences()

    @staticmethod
    def _backup_path():
        from video_maker.app_paths import user_data_path

        return user_data_path(GroqKeyManager.BACKUP_FILE)

    @staticmethod
    def _read_backup():
        path = GroqKeyManager._backup_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (ValueError, OSError):
            return []
        return [str(item).strip() for item in data if isinstance(item, str) and str(item).strip()]

    @staticmethod
    def _write_backup(keys):
        stored = [str(item).strip() for item in keys if isinstance(item, str) and str(item).strip()]
        try:
            with open(GroqKeyManager._backup_path(), "w", encoding="utf-8") as file:
                json.dump(stored, file, ensure_ascii=False, indent=2)
        except OSError:
            pass

    @staticmethod
    def _save(data):
        write_preferences(data)
        GroqKeyManager._write_backup(data.get(GroqKeyManager.CONFIG_KEY, []))

    @staticmethod
    def storage_path():
        return GroqKeyManager._backup_path()

    @staticmethod
    def restore_from_backup():
        """Restore keys from the dedicated backup file if preferences lost them."""
        backup = GroqKeyManager._read_backup()
        if not backup:
            return False
        data = GroqKeyManager._prefs()
        existing = [k for k in data.get(GroqKeyManager.CONFIG_KEY, []) if isinstance(k, str)]
        merged = list(existing)
        for key in backup:
            if key not in merged:
                merged.append(key)
        if merged == existing:
            return False
        data[GroqKeyManager.CONFIG_KEY] = merged
        GroqKeyManager._save(data)
        return True

    @staticmethod
    def get_keys():
        keys = list(GroqKeyManager._prefs().get(GroqKeyManager.CONFIG_KEY, []))
        if not keys:
            GroqKeyManager.restore_from_backup()
            keys = list(GroqKeyManager._prefs().get(GroqKeyManager.CONFIG_KEY, []))
        env_key = os.environ.get(GroqKeyManager.ENV_VAR, "").strip()
        if env_key and env_key not in keys:
            keys.insert(0, env_key)
        return keys

    @staticmethod
    def add_key(key):
        key = key.strip()
        if not key:
            return False
        keys = GroqKeyManager.get_keys()
        if key in keys:
            return False
        keys.append(key)
        data = GroqKeyManager._prefs()
        data[GroqKeyManager.CONFIG_KEY] = [k for k in keys if k != os.environ.get(GroqKeyManager.ENV_VAR, "").strip()]
        GroqKeyManager._save(data)
        return True

    @staticmethod
    def remove_key(index):
        keys = [k for k in GroqKeyManager.get_keys() if k != os.environ.get(GroqKeyManager.ENV_VAR, "").strip()]
        if 0 <= index < len(keys):
            keys.pop(index)
            data = GroqKeyManager._prefs()
            data[GroqKeyManager.CONFIG_KEY] = keys
            GroqKeyManager._save(data)
            return True
        return False

    @staticmethod
    def mask_key(key):
        if len(key) > 12:
            return key[:8] + "*" * (len(key) - 12) + key[-4:]
        return "***"

    @staticmethod
    def validate_key_format(key):
        return bool(re.match(r'^gsk_[a-zA-Z0-9]{10,}$', key.strip()))

    @staticmethod
    def mark_exhausted(key):
        data = GroqKeyManager._prefs()
        exhausted = data.get(GroqKeyManager.EXHAUSTED_UNTIL_KEY, {})
        exhausted[key] = time.time() + 3600
        data[GroqKeyManager.EXHAUSTED_UNTIL_KEY] = exhausted
        GroqKeyManager._save(data)

    @staticmethod
    def is_exhausted(key):
        data = GroqKeyManager._prefs()
        exhausted = data.get(GroqKeyManager.EXHAUSTED_UNTIL_KEY, {})
        until = exhausted.get(key, 0)
        if until > time.time():
            return True
        if until > 0:
            exhausted.pop(key, None)
            data[GroqKeyManager.EXHAUSTED_UNTIL_KEY] = exhausted
            GroqKeyManager._save(data)
        return False

    @staticmethod
    def next_available_key(start_index=0):
        keys = GroqKeyManager.get_keys()
        if not keys:
            return None, -1
        n = len(keys)
        for offset in range(n):
            idx = (start_index + offset) % n
            key = keys[idx]
            if not GroqKeyManager.is_exhausted(key):
                return key, idx
        return None, -1

    @staticmethod
    def clear_exhausted():
        data = GroqKeyManager._prefs()
        data.pop(GroqKeyManager.EXHAUSTED_UNTIL_KEY, None)
        GroqKeyManager._save(data)


# ---------------------------------------------------------------------------
# Debug log
# ---------------------------------------------------------------------------

_DEBUG_LOG = None
_DEBUG_LOCK = threading.Lock()


def _debug(msg):
    global _DEBUG_LOG
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if _DEBUG_LOG is None:
            log_dir = tempfile.gettempdir()
            _DEBUG_LOG = os.path.join(log_dir, "captions_debug.log")
            with open(_DEBUG_LOG, "w", encoding="utf-8") as f:
                f.write(f"{ts} DEBUG START\n")
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Audio rendering
# ---------------------------------------------------------------------------

def render_audio_to_mp3(video_path, progress_callback=None):
    temp_dir = tempfile.mkdtemp(prefix="captions_audio_")
    output_path = os.path.join(temp_dir, "audio.mp3")
    try:
        if progress_callback:
            progress_callback(0, "جاري استخراج الصوت من الفيديو...")
        _run_ffmpeg(
            ["-i", video_path,
             "-vn", "-acodec", "libmp3lame",
             "-ar", "16000", "-ac", "1", "-b:a", "32k",
             output_path],
            check=True, capture_output=True, timeout=300
        )
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("فشل استخراج الصوت")
        if progress_callback:
            progress_callback(100, "تم استخراج الصوت بنجاح")
        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Groq Whisper transcription
# ---------------------------------------------------------------------------

def transcribe_with_groq(audio_path, keys, progress_callback=None, cancelled_callback=None):
    MAX_FILE_SIZE = 24 * 1024 * 1024
    size = os.path.getsize(audio_path)
    split_temp_dir = None
    if size > MAX_FILE_SIZE:
        dur = _get_audio_duration(audio_path)
        bitrate_guess = (size * 8) / dur if dur > 0 else 128000
        chunk_dur = (MAX_FILE_SIZE * 8) / bitrate_guess * 0.8
        chunk_dur = max(60, min(chunk_dur, 600))
        chunks, split_temp_dir = _split_audio_by_duration(audio_path, chunk_dur, progress_callback)
    else:
        chunks = [audio_path]

    all_segments = []
    all_texts = []
    chunk_durations = []
    failed_indices = {}
    time_offset = 0.0
    try:
        for ci, chunk in enumerate(chunks):
            if cancelled_callback and cancelled_callback():
                return None, None
            if progress_callback:
                progress_callback(int((ci / len(chunks)) * 50), f"جاري نسخ الجزء {ci + 1}/{len(chunks)}...")
            result = _transcribe_chunk(chunk, keys, progress_callback, time_offset)
            if result is None:
                failed_indices[ci] = time_offset
            else:
                text, segs = result
                all_texts.append(text)
                all_segments.extend(segs)
            dur = _get_audio_duration(chunk)
            chunk_durations.append(dur)
            time_offset += dur

        MAX_RETRY_ROUNDS = 3
        for retry_round in range(MAX_RETRY_ROUNDS):
            if not failed_indices or (cancelled_callback and cancelled_callback()):
                break
            if progress_callback:
                progress_callback(0, f"جولة إعادة المحاولة {retry_round + 1}/{MAX_RETRY_ROUNDS} لـ {len(failed_indices)} قطع فاشلة...")
            still_failed = {}
            for ci in failed_indices:
                if cancelled_callback and cancelled_callback():
                    return None, None
                chunk = chunks[ci]
                chunk_offset = sum(chunk_durations[j] for j in range(ci))
                if progress_callback:
                    progress_callback(0, f"إعادة إرسال الجزء {ci + 1}/{len(chunks)}...")
                result = _transcribe_chunk(chunk, keys, progress_callback, chunk_offset)
                if result is None:
                    still_failed[ci] = failed_indices[ci]
                else:
                    text, segs = result
                    all_texts.append(text)
                    all_segments.extend(segs)
                    if progress_callback:
                        progress_callback(0, f"نجح الجزء {ci + 1} في المحاولة {retry_round + 1}.")
            failed_indices = still_failed

        if failed_indices and progress_callback:
            progress_callback(0, f"بقيت {len(failed_indices)} قطع بعد كل جولات إعادة المحاولة، سيتم التحقق من التغطية الزمنية...")

        all_segments.sort(key=lambda s: s.get("start", 0))

        # إزالة القطع المكررة الناتجة عن إعادة المحاولات
        seen = set()
        unique_segments = []
        for s in all_segments:
            key = (round(s.get("start", 0), 2), round(s.get("end", 0), 2), s.get("text", ""))
            if key in seen:
                continue
            seen.add(key)
            unique_segments.append(s)
        all_segments = unique_segments

        if size > MAX_FILE_SIZE and len(all_segments) > 1:
            all_segments = _verify_and_fill_gaps(
                all_segments, chunks, chunk_durations, audio_path, keys, progress_callback
            )

        if progress_callback:
            progress_callback(100, "اكتمل النسخ")
        return " ".join(all_texts), all_segments
    finally:
        if split_temp_dir:
            shutil.rmtree(split_temp_dir, ignore_errors=True)


def _transcribe_chunk(chunk_path, keys, progress_callback, time_offset=0.0):
    use_word_timestamps = True
    MAX_ATTEMPTS = 5
    key_index = 0
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            backoff = min(30, 2 ** attempt)
            if progress_callback:
                progress_callback(0, f"إعادة المحاولة بعد {backoff} ثانية...")
            time.sleep(backoff)
        for _ in range(len(keys) * 2):
            key, key_index = GroqKeyManager.next_available_key(key_index)
            if key is None:
                if progress_callback:
                    progress_callback(0, "جميع المفاتيح مستنفدة، يرجى إضافة مفاتيح جديدة")
                return None
            masked = GroqKeyManager.mask_key(key)
            if progress_callback:
                progress_callback(0, f"محاولة باستخدام المفتاح {masked}...")
            try:
                with open(chunk_path, "rb") as f:
                    data = {
                        "model": "whisper-large-v3",
                        "response_format": "verbose_json",
                    }
                    if use_word_timestamps:
                        data["timestamp_granularities[]"] = ["word", "segment"]
                    resp = requests.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {key}"},
                        files={"file": (os.path.basename(chunk_path), f, "audio/mpeg")},
                        data=data,
                        timeout=600
                    )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("text", "").strip()
                    if not text:
                        continue
                    raw_segments = result.get("segments", [])
                    parsed = []
                    for seg in raw_segments:
                        seg_text = seg.get("text", "").strip()
                        if not seg_text:
                            continue
                        seg_start = seg.get("start", 0) + time_offset
                        seg_end = seg.get("end", 0) + time_offset
                        words = []
                        for w in seg.get("words", []):
                            w_start = w.get("start")
                            w_end = w.get("end")
                            w_word = w.get("word")
                            if w_start is None or w_end is None or not w_word:
                                continue
                            words.append({
                                "start": w_start + time_offset,
                                "end": w_end + time_offset,
                                "word": w_word,
                            })
                        if not words:
                            text_words = seg_text.split()
                            if text_words:
                                duration = seg.get("end", 0) - seg.get("start", 0)
                                if duration > 0:
                                    lead_in = min(0.3, duration * 0.1)
                                    word_duration = (duration - lead_in) / len(text_words)
                                    for j, w in enumerate(text_words):
                                        w_start = seg.get("start", 0) + lead_in + j * word_duration + time_offset
                                        w_end = w_start + word_duration
                                        words.append({"start": w_start, "end": w_end, "word": w})
                        parsed.append({
                            "start": seg_start,
                            "end": seg_end,
                            "text": seg_text,
                            "words": words,
                        })
                    if not parsed:
                        return text, []
                    return text, parsed
                elif resp.status_code in (401, 403):
                    if progress_callback:
                        progress_callback(0, "المفتاح غير صالح، تجربة التالي...")
                    key_index = (key_index + 1) % len(keys)
                elif resp.status_code == 429:
                    if progress_callback:
                        progress_callback(0, "تجاوز حد الاستخدام، تجربة المفتاح التالي...")
                    GroqKeyManager.mark_exhausted(key)
                    key_index = (key_index + 1) % len(keys)
                elif resp.status_code == 413:
                    if progress_callback:
                        progress_callback(0, "حجم الملف كبير جداً...")
                    break
                else:
                    err_msg = resp.text[:200]
                    if use_word_timestamps and "timestamp" in err_msg.lower():
                        if progress_callback:
                            progress_callback(0, "Word timestamps غير مدعومة، إعادة المحاولة بدونها...")
                        use_word_timestamps = False
                        break
                    if progress_callback:
                        progress_callback(0, f"خطأ Groq: {err_msg}")
                    key_index = (key_index + 1) % len(keys)
            except requests.exceptions.Timeout:
                if progress_callback:
                    progress_callback(0, "انتهت مهلة الاتصال...")
                if use_word_timestamps:
                    use_word_timestamps = False
                    break
                key_index = (key_index + 1) % len(keys)
            except requests.exceptions.ConnectionError:
                if progress_callback:
                    progress_callback(0, "فشل الاتصال...")
                key_index = (key_index + 1) % len(keys)
            except Exception as e:
                if progress_callback:
                    progress_callback(0, f"خطأ: {str(e)}")
                key_index = (key_index + 1) % len(keys)
    return None


def _split_audio_by_duration(audio_path, chunk_duration, progress_callback=None):
    """يقسم الصوت عند نقاط الصمت القريبة من حدود الأجزاء للحصول على تقطيع نظيف.

    يعيد قائمة مسارات الأجزاء + مجلد مؤقت يُحذف بعد الانتهاء، أو (الصوت الأصلي، None).
    """
    total_dur = _get_audio_duration(audio_path)
    if total_dur <= 0:
        return [audio_path], None
    num_chunks = max(1, math.ceil(total_dur / chunk_duration))

    if progress_callback:
        progress_callback(0, "تجاوز حد الحجم، جارٍ تقسيم الصوت...")

    temp_dir = tempfile.mkdtemp(prefix="captions_split_")
    try:
        # رصد مواضع الصمت لاختيار نقاط قطع طبيعية
        silence_end_times = []
        try:
            ret = subprocess.run(
                ["ffmpeg", "-i", audio_path, "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"],
                capture_output=True, text=True, timeout=60, startupinfo=ffmpeg_startupinfo()
            )
            stderr = ret.stderr or ""
            for line in stderr.split("\n"):
                if "silence_end" in line:
                    m = re.search(r'silence_end: ([\d.]+)', line)
                    if m:
                        silence_end_times.append(float(m.group(1)))
        except Exception:
            silence_end_times = []

        # لكل حدود الجزء نبحث عن أقرب نقطة صمت ضمن ±5 ثوانٍ
        split_times = []
        for i in range(1, num_chunks):
            target = i * chunk_duration
            if silence_end_times:
                nearest = min(silence_end_times, key=lambda x: abs(x - target))
                if abs(nearest - target) <= 5.0:
                    split_times.append(nearest)
                else:
                    split_times.append(target)
            else:
                split_times.append(target)

        filtered = []
        for t in split_times:
            if not filtered or t - filtered[-1] >= 2.0:
                filtered.append(t)
        split_times = filtered

        output_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")
        if split_times:
            seg_times_str = ",".join(f"{t:.2f}" for t in split_times)
            ret = _run_ffmpeg(
                ["-i", audio_path, "-f", "segment", "-segment_times", seg_times_str,
                 "-c", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "32k", output_pattern],
                capture_output=True, timeout=120
            )
        else:
            ret = _run_ffmpeg(
                ["-i", audio_path, "-f", "segment", "-segment_time", str(chunk_duration),
                 "-c", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "32k", output_pattern],
                capture_output=True, timeout=120
            )

        if ret.returncode != 0:
            if progress_callback:
                progress_callback(0, "فشل التقسيم عند نقاط الصمت، الرجوع للتقسيم الزمني...")
            chunks = []
            for i in range(num_chunks):
                start = i * chunk_duration
                dur = min(chunk_duration, total_dur - start)
                chunk_path = os.path.join(temp_dir, f"chunk_{i:03d}.mp3")
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", audio_path, "-ss", str(start),
                         "-t", str(dur), "-acodec", "libmp3lame", "-ar", "16000",
                         "-ac", "1", "-b:a", "32k", chunk_path],
                        check=True, capture_output=True, timeout=120, startupinfo=ffmpeg_startupinfo()
                    )
                    if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                        chunks.append(chunk_path)
                except Exception:
                    pass
            if not chunks:
                if progress_callback:
                    progress_callback(0, "فشل تقسيم الصوت.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return [audio_path], None
            return chunks, temp_dir

        chunk_files = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.mp3")))
        chunk_files = [c for c in chunk_files if os.path.getsize(c) > 0]
        if not chunk_files:
            if progress_callback:
                progress_callback(0, "لم يتم إنشاء أجزاء بعد التقسيم.")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return [audio_path], None

        if progress_callback:
            progress_callback(0, f"تم تقسيم الصوت إلى {len(chunk_files)} أجزاء على نقاط الصمت.")
        return chunk_files, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return [audio_path], None


def _verify_and_fill_gaps(segments, chunks, chunk_durations, audio_path, keys, progress_callback=None):
    """يكشف الثغرات الزمنية في النسخ ويعيد إرسال القطع المقابلة لسدها."""
    if not segments or len(segments) < 2:
        return segments

    total_dur = _get_audio_duration(audio_path)
    if total_dur <= 0:
        return segments

    sorted_segs = sorted(segments, key=lambda s: s.get("start", 0))

    gaps = []
    for i in range(1, len(sorted_segs)):
        gap_start = sorted_segs[i - 1].get("end", 0)
        gap_end = sorted_segs[i].get("start", 0)
        gap_duration = gap_end - gap_start
        if gap_duration > 5.0:
            gaps.append((gap_start, gap_end, gap_duration))

    last_end = sorted_segs[-1].get("end", 0) if sorted_segs else 0
    coverage_pct = (last_end / total_dur * 100) if total_dur > 0 else 100

    if progress_callback:
        progress_callback(0, f"نسبة التغطية: {coverage_pct:.1f}% | الثغرات: {len(gaps)}")

    if coverage_pct >= 80 and not gaps:
        if progress_callback:
            progress_callback(0, "التغطية كاملة.")
        return sorted_segs

    if gaps:
        if progress_callback:
            progress_callback(0, f"تم اكتشاف {len(gaps)} ثغرة زمنية، جارٍ إعادة إرسال القطع المقابلة...")
        for gap_start, gap_end, gap_dur in gaps:
            chunk_offset = 0.0
            for ci, chunk in enumerate(chunks):
                if ci >= len(chunk_durations):
                    break
                cdur = chunk_durations[ci]
                chunk_start = chunk_offset
                chunk_end_t = chunk_offset + cdur
                chunk_offset += cdur
                if chunk_start < gap_end and chunk_end_t > gap_start:
                    if progress_callback:
                        progress_callback(0, f"إعادة إرسال الجزء {ci + 1}/{len(chunks)}...")
                    result = _transcribe_chunk(chunk, keys, progress_callback, chunk_start)
                    if result:
                        _text, segs = result
                        segments.extend(segs)
        segments = sorted(segments, key=lambda s: s.get("start", 0))
        new_last_end = segments[-1].get("end", 0) if segments else 0
        new_coverage = (new_last_end / total_dur * 100) if total_dur > 0 else 100
        if progress_callback:
            progress_callback(0, f"نسبة التغطية بعد الإصلاح: {new_coverage:.1f}%")

    return segments


def _get_audio_duration(path):
    try:
        r = _run_ffprobe(
            ["-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Captions Settings Dialog
# ---------------------------------------------------------------------------

class captionsSettingsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="إعدادات الترجمة على الشاشة", size=(520, 440))
        self.parent = parent
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        btn_get_key = wx.Button(panel, label="الحصول على مفتاح Groq API")
        btn_get_key.Bind(wx.EVT_BUTTON, lambda e: webbrowser.open("https://console.groq.com/keys"))
        main_sizer.Add(btn_get_key, flag=wx.ALIGN_CENTER | wx.ALL, border=10)

        key_input_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.key_input = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        self.key_input.SetName(tr("مفتاح Groq الجديد"))
        key_input_sizer.Add(self.key_input, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)
        btn_add = wx.Button(panel, label="إضافة")
        btn_add.Bind(wx.EVT_BUTTON, self.on_add_key)
        key_input_sizer.Add(btn_add, flag=wx.ALL, border=5)
        main_sizer.Add(key_input_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        list_label = wx.StaticText(panel, label="المفاتيح المخزنة:")
        main_sizer.Add(list_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        self.key_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=(-1, 150))
        self.key_list.AppendColumn("المفتاح", width=350)
        self.key_list.AppendColumn("حالة", width=100)
        main_sizer.Add(self.key_list, proportion=1, flag=wx.EXPAND | wx.ALL, border=10)

        btn_delete = wx.Button(panel, label="حذف المفتاح المحدد")
        btn_delete.Bind(wx.EVT_BUTTON, self.on_delete_key)
        main_sizer.Add(btn_delete, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=5)

        btn_save = wx.Button(panel, label="حفظ التعديلات")
        btn_save.Bind(wx.EVT_BUTTON, self.on_save)
        btn_close = wx.Button(panel, label="إغلاق")
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(btn_save, flag=wx.ALL, border=5)
        btn_sizer.Add(btn_close, flag=wx.ALL, border=5)
        btn_sizer.AddStretchSpacer()
        main_sizer.Add(btn_sizer, flag=wx.EXPAND | wx.ALL, border=5)

        panel.SetSizer(main_sizer)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.refresh_list()
        self.Centre()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_OK)
            return
        event.Skip()

    def refresh_list(self):
        self.key_list.DeleteAllItems()
        keys = GroqKeyManager.get_keys()
        for i, key in enumerate(keys):
            masked = GroqKeyManager.mask_key(key)
            idx = self.key_list.InsertItem(i, masked)
            if GroqKeyManager.is_exhausted(key):
                self.key_list.SetItem(idx, 1, "مستنفد")
            else:
                self.key_list.SetItem(idx, 1, "نشط")

    def on_add_key(self, event):
        key = self.key_input.GetValue().strip()
        if not key:
            wx.MessageBox("يرجى إدخال مفتاح Groq.", "تنبيه", wx.OK | wx.ICON_WARNING)
            return
        if not GroqKeyManager.validate_key_format(key):
            wx.MessageBox("صيغة المفتاح غير صحيحة. يجب أن يبدأ بـ gsk_", "خطأ", wx.OK | wx.ICON_ERROR)
            return
        if GroqKeyManager.add_key(key):
            self.key_input.Clear()
            self.refresh_list()
            self.say("تمت إضافة المفتاح")
        else:
            wx.MessageBox("المفتاح موجود مسبقاً أو غير صالح.", "تنبيه", wx.OK | wx.ICON_WARNING)

    def on_delete_key(self, event):
        selected = self.key_list.GetFirstSelected()
        if selected == wx.NOT_FOUND:
            wx.MessageBox("يرجى تحديد مفتاح من القائمة.", "تنبيه", wx.OK | wx.ICON_WARNING)
            return
        confirm = wx.MessageBox("هل أنت متأكد من حذف هذا المفتاح؟", "تأكيد الحذف",
                                wx.YES_NO | wx.ICON_QUESTION)
        if confirm != wx.YES:
            return
        GroqKeyManager.remove_key(selected)
        self.refresh_list()
        self.say("تم حذف المفتاح")

    def on_save(self, event):
        self.refresh_list()
        self.say("تم حفظ الإعدادات")

    def say(self, message):
        if hasattr(self.parent, "say"):
            self.parent.say(message)


# ---------------------------------------------------------------------------
# Progress Dialog for the pipeline
# ---------------------------------------------------------------------------

class CaptionsProgressDialog(wx.Dialog):
    def __init__(self, parent, title):
        super().__init__(parent, title=title, size=(560, 340),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.cancelled = False
        self.process_to_kill = None
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        log_label = wx.StaticText(panel, label="سير العمل:")
        sizer.Add(log_label, flag=wx.ALL, border=8)

        self.log_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                    size=(-1, 180))
        self.log_ctrl.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        self.log_ctrl.AppendText("تم بدء عملية استخراج الترجمة...\n")
        sizer.Add(self.log_ctrl, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)

        self.gauge = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL, size=(-1, 20))
        sizer.Add(self.gauge, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=8)

        self.btn_cancel = wx.Button(panel, label="إلغاء")
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        sizer.Add(self.btn_cancel, flag=wx.ALIGN_CENTER | wx.ALL, border=8)

        panel.SetSizer(sizer)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        wx.CallAfter(self.btn_cancel.SetFocus)

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.on_cancel(None)
        event.Skip()

    def on_cancel(self, event=None):
        self.cancelled = True
        self.btn_cancel.Disable()
        self._append_log("-- جاري إلغاء العملية، انتظر... --")
        proc = self.process_to_kill
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def set_process(self, proc):
        self.process_to_kill = proc

    def set_progress(self, value, status=""):
        wx.CallAfter(self._do_progress, value, status)

    def _append_log(self, text):
        self.log_ctrl.AppendText(text + "\n")
        self.log_ctrl.ShowPosition(self.log_ctrl.GetLastPosition())

    def _do_progress(self, value, status):
        self.gauge.SetValue(min(100, max(0, int(value))))
        if status:
            self._append_log(status)

    def is_cancelled(self):
        return self.cancelled


# ---------------------------------------------------------------------------
# Captions Pipeline (moved from player.py for better structure)
# ---------------------------------------------------------------------------

class CaptionsPipeline:
    """Encapsulates the full captions extraction pipeline."""

    def __init__(self, player):
        self.player = player

    # Convenience accessors to reduce verbosity
    @property
    def _p(self):
        return self.player

    def run(self, start_time, end_time, timeline_snapshot, progress_dlg):
        _debug("CaptionsPipeline.run started")
        audio_result = None
        try:
            _debug("Step 1: rendering audio")
            progress_dlg.set_progress(0, "جاري استخراج الصوت من التايم لاين...")
            audio_result = self._render_captions_audio(start_time, end_time, timeline_snapshot, progress_dlg)
            if audio_result is None:
                _debug("audio_result is None")
                return
            if progress_dlg.is_cancelled():
                _debug("cancelled after audio render")
                return
            _debug(f"audio rendered: {audio_result[0]}")

            audio_path, _audio_temp_dir = audio_result

            if progress_dlg.is_cancelled():
                _debug("cancelled before transcribe")
                return
            keys = GroqKeyManager.get_keys()
            _debug(f"Step 2: transcribing with {len(keys)} keys")
            progress_dlg.set_progress(30, "جاري إرسال الصوت لـ Groq Whisper...")
            progress_dlg.set_process(None)
            try:
                full_text, segments = transcribe_with_groq(
                    audio_path, keys, progress_dlg.set_progress, progress_dlg.is_cancelled
                )
                if full_text is None:
                    _debug("cancelled during transcription")
                    return
                _debug(f"transcription complete: {len(segments)} segments")
            except Exception as exc:
                _debug(f"transcription failed: {exc}")
                raise
            if progress_dlg.is_cancelled():
                _debug("cancelled after transcribe")
                return

            if not segments:
                _debug("no segments returned from Groq")
                wx.CallAfter(wx.MessageBox, f"لم يتم التعرف على أي نطق في الصوت.\nالنص الخام: {full_text[:200] if full_text else '(فارغ)'}", "تنبيه", wx.OK | wx.ICON_INFORMATION)
                return
            _debug(f"segments from Groq: {len(segments)}, text length: {len(full_text)}")

            _debug("Step 3: review dialog")
            timeline_dur = self._p.timeline_duration()
            review_result = [None, None]
            event = threading.Event()
            wx.CallAfter(self._show_review_dialog_on_main, segments, timeline_dur, start_time, end_time, progress_dlg, review_result, event)
            event.wait()
            if progress_dlg.is_cancelled():
                _debug("cancelled during review")
                return

            modified, options = review_result
            if not modified or not options:
                _debug("review cancelled or no options")
                return
            _debug(f"review done: {len(modified)} segments")

            _debug("Step 4: applying captions")
            self._apply_captions_worker(modified, options, progress_dlg)
            _debug("pipeline completed successfully")

        except Exception as error:
            _debug(f"pipeline error: {error}")
            wx.CallAfter(self._p.say, "تعذر استخراج الترجمة")
            wx.CallAfter(wx.MessageBox, f"خطأ في استخراج الترجمة: {str(error)}", "خطأ", wx.OK | wx.ICON_ERROR)
        finally:
            if audio_result:
                shutil.rmtree(audio_result[1], ignore_errors=True)
            wx.CallAfter(progress_dlg.Destroy)
            self._p._captions_running = False
            _debug("pipeline finished, _captions_running=False")

    def _render_captions_audio(self, start_time, end_time, timeline_snapshot, progress_dlg=None):
        from video_maker.timeline import TimelineSegment, slice_segments, total_duration
        from video_maker.video_editing import write_timeline_audio
        timeline_dur = total_duration(timeline_snapshot)
        if start_time < 0 or end_time > timeline_dur or start_time >= end_time:
            return None
        selected_segments = slice_segments(timeline_snapshot, start_time, end_time)
        if not selected_segments:
            wx.CallAfter(wx.MessageBox, "لا توجد مقاطع في النطاق المحدد.", "خطأ", wx.OK | wx.ICON_ERROR)
            return None
        shifted = []
        for s in selected_segments:
            shifted.append(TimelineSegment(
                path=s.path, start=s.start - start_time, end=s.end - start_time,
                speed=getattr(s, "speed", 1.0),
                audio_volume=getattr(s, "audio_volume", 1.0),
                audio_path=str(getattr(s, "audio_path", "") or ""),
                audio_start=getattr(s, "audio_start", None),
            ))
        temp_dir = tempfile.mkdtemp(prefix="captions_render_")
        output_path = os.path.join(temp_dir, "captions_audio.mp3")
        if progress_dlg:
            def check_cancel():
                return progress_dlg.is_cancelled()
        else:
            check_cancel = lambda: False
        try:
            write_timeline_audio(
                shifted, output_path,
                progress_callback=lambda p: (progress_dlg and progress_dlg.set_progress(p, "جاري تصدير الصوت...")),
                cancelled_callback=check_cancel,
                save_options={"audio_codec": "libmp3lame", "audio_bitrate": "32k",
                              "audio_channels": 1, "audio_nbytes": 2, "audio_ffmpeg_params": ["-ar", "16000"]},
            )
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise RuntimeError("فشل تصدير الصوت")
            return output_path, temp_dir
        except (IOError, RuntimeError):
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _show_review_dialog_on_main(self, segments, timeline_dur, start_time, end_time, progress_dlg, review_result, event):
        _debug("review: entered on main thread")
        try:
            if progress_dlg.is_cancelled():
                _debug("review: cancelled before dialog")
                event.set()
                return
            progress_dlg.set_progress(95, "تم استخراج النصوص. جاري فتح محاورة المراجعة...")
            progress_dlg.set_process(None)

            progress_dlg.Hide()
            _debug("review: creating SubtitleReviewWizardDialog")
            dlg = SubtitleReviewWizardDialog(self._p, segments, timeline_dur)
            _debug("review: dialog created, raising and showing modal")
            dlg.Raise()
            wx.CallAfter(self._p.say, "محاورة مراجعة وتعديل الترجمة")
            result = dlg.ShowModal()
            _debug(f"review: ShowModal returned {result}")
            if result != wx.ID_OK:
                _debug("review: ShowModal was not OK")
                dlg.Destroy()
                event.set()
                return
            modified = dlg.modified_segments
            dlg.Destroy()
            _debug(f"review: modified_segments count: {len(modified) if modified else 0}")
            if not modified:
                _debug("review: no modified segments")
                event.set()
                return

            if progress_dlg.is_cancelled():
                _debug("review: cancelled before TextOverlayDialog")
                event.set()
                return
            progress_dlg.set_progress(95, "جاري فتح إعدادات تنسيق النص...")

            _debug("review: creating TextOverlayDialog")
            dialog = TextOverlayDialog(self._p, is_auto_subtitle_mode=True)
            _debug("review: showing TextOverlayDialog modally")
            result2 = dialog.ShowModal()
            _debug(f"review: TextOverlayDialog ShowModal returned {result2}")
            if result2 != wx.ID_OK:
                _debug("review: TextOverlayDialog was not OK")
                dialog.Destroy()
                event.set()
                return
            options = dialog.options
            dialog.Destroy()
            _debug(f"review: options exist: {options is not None}")
            if not options:
                _debug("review: no options")
                event.set()
                return

            for seg in modified:
                if seg.start < start_time:
                    seg.start = start_time
                if seg.end > end_time:
                    seg.end = end_time

            progress_dlg.set_progress(95, "جاري تطبيق الترجمة على الخط الزمني...")
            review_result[0] = modified
            review_result[1] = options
            _debug("review: review_result set successfully")
        except Exception as e:
            _debug(f"review: EXCEPTION: {e}")
        finally:
            _debug("review: event.set() called")
            event.set()

    def _apply_captions_worker(self, segments, options, progress_dlg):
        from video_maker.timeline import TimelineSegment, slice_segments, total_duration
        try:
            before_state = self._p.capture_edit_state()
            timeline_snapshot = list(self._p.timeline)
            media_kind = self._p.media_kind
            total = len(segments)
            visual_items = []
            temp_dirs = []
            temp_files = []
            edit_points = []
            new_timeline = list(timeline_snapshot) if media_kind != "audio" else None
            pro_mode = get_program_mode() == PROFESSIONAL_MODE
            pro_items = []

            for idx, seg in enumerate(segments):
                if progress_dlg.is_cancelled() or getattr(self._p, "closing", False):
                    raise RuntimeError("ألغى المستخدم العملية")

                if not seg.text.strip():
                    continue

                text_options = TextOverlayOptions(
                    text=seg.text,
                    font_path=options.font_path,
                    font_name=options.font_name,
                    font_size=options.font_size,
                    color=options.color,
                    background=options.background,
                    background_opacity=options.background_opacity,
                    position=options.position,
                    box_width_percent=options.box_width_percent,
                    mode=getattr(options, "mode", ""),
                    typing_sound=getattr(options, "typing_sound", ""),
                    typing_volume=getattr(options, "typing_volume", 25),
                    typing_speed=getattr(options, "typing_speed", 10),
                    mixed_text=getattr(options, "mixed_text", False),
                )

                progress_val = int(((idx + 1) / total) * 90)
                progress_dlg.set_progress(progress_val, f"جاري معالجة المقطع {idx + 1}/{total}...")

                if pro_mode:
                    pro_items.append(from_grok_caption(seg, options))
                elif media_kind == "audio":
                    self._render_one_caption_visual(seg, text_options, visual_items, temp_dirs, temp_files)
                else:
                    new_timeline = self._render_one_caption_timeline(seg, text_options, new_timeline, timeline_snapshot, temp_dirs, temp_files, edit_points)

            progress_dlg.set_progress(95, "جاري تطبيق النتائج...")

            def apply_results():
                try:
                    if getattr(self._p, "closing", False) or progress_dlg.is_cancelled():
                        return

                    if pro_mode:
                        for item in pro_items:
                            self._p.visual_items.append(item)
                        if pro_items:
                            self._p.focused_element = dict(pro_items[0])
                        self._p.is_dirty = True
                        self._p.record_edit("إدراج ترجمة تلقائية", before_state)
                        self._p.refresh_menu_bar()
                        self._p.apply_edit_state(self._p.capture_edit_state(), focus_timeline=False)
                        self._p.request_preview_rebuild()
                        self._p.say("تم إدراج الترجمة التلقائية")
                        return

                    self._p.generated_temp_dirs.extend(temp_dirs)
                    self._p.generated_temp_files.extend(temp_files)

                    if media_kind == "audio":
                        for item in visual_items:
                            self._p.visual_items.append(item)
                            self._p.add_edit_point("text", item["start"], item["end"], "visual", item_id=item["id"])
                    else:
                        self._p.timeline = new_timeline
                        for seg_start, seg_end, restore_segs in edit_points:
                            self._p.add_edit_point("text", seg_start, seg_end, "timeline", restore_segments=restore_segs, mode="replace")

                    self._p.is_dirty = True
                    self._p.record_edit("إدراج ترجمة تلقائية", before_state)
                    self._p.refresh_menu_bar()
                    self._p.say("تم إدراج الترجمة التلقائية")
                except Exception as error:
                    wx.MessageBox(f"خطأ في تطبيق النتائج: {str(error)}", "خطأ", wx.OK | wx.ICON_ERROR)

            wx.CallAfter(apply_results)

        except Exception as error:
            wx.CallAfter(self._p.say, "تعذر تطبيق الترجمة")
            wx.CallAfter(wx.MessageBox, f"خطأ في تطبيق الترجمة: {str(error)}", "خطأ", wx.OK | wx.ICON_ERROR)

    def _render_one_caption_visual(self, seg, text_options, visual_items, temp_dirs, temp_files):
        temp_dir = tempfile.mkdtemp(prefix="captions_text_")
        temp_dirs.append(temp_dir)
        typing_mode = getattr(text_options, "mode", "") == "typing"
        if typing_mode:
            text_path = os.path.join(temp_dir, f"caption_{uuid.uuid4().hex}.mp4")
            render_typing_video(None, text_path, text_options, max(0.05, seg.end - seg.start))
            item_type = "video"
        else:
            text_path = os.path.join(temp_dir, f"caption_{uuid.uuid4().hex}.png")
            render_text_image(text_options, text_path)
            item_type = "text"
        temp_files.append(text_path)
        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "type": item_type,
            "path": text_path,
            "start": seg.start,
            "end": seg.end,
            "transition": "",
            "transition_duration": 1.0,
        }
        if typing_mode:
            item["is_typing"] = True
        visual_items.append(item)

    def _render_one_caption_timeline(self, seg, text_options, new_timeline, timeline_snapshot, temp_dirs, temp_files, edit_points):
        from video_maker.timeline import slice_segments
        from video_maker.video_editing import build_caption_transition_segment
        overlay_path, overlay_temp_dirs, replaced_range = build_caption_transition_segment(
            new_timeline,
            seg.start,
            seg.end,
            text_options,
        )
        temp_dirs.extend(overlay_temp_dirs)
        temp_files.append(overlay_path)
        replace_start, replace_end = replaced_range
        result = replace_image_overlay_range(
            new_timeline, replace_start, replace_end, overlay_path
        )
        restore_segs = slice_segments(timeline_snapshot, replace_start, replace_end)
        edit_points.append((replace_start, replace_end, restore_segs))
        return result


def run_captions_pipeline(player, start_time, end_time, timeline_snapshot, progress_dlg):
    """Convenience entry point: create pipeline and run it."""
    pipeline = CaptionsPipeline(player)
    pipeline.run(start_time, end_time, timeline_snapshot, progress_dlg)


# ---------------------------------------------------------------------------
# Subtitle Review Wizard Dialog
# ---------------------------------------------------------------------------

class SubtitleReviewWizardDialog(wx.Dialog):
    def __init__(self, parent, segments, timeline_duration):
        super().__init__(parent, title="مراجعة وتعديل الترجمة", size=(700, 520))
        self.parent = parent
        self.segments = segments
        self.timeline_duration = timeline_duration
        self.current_index = 0
        self.modified_segments = [
            SubtitleSegment(s["start"], s["end"], s["text"])
            for s in segments
        ]

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.segment_counter = wx.StaticText(panel, label="")
        font = self.segment_counter.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.segment_counter.SetFont(font)
        main_sizer.Add(self.segment_counter, flag=wx.ALIGN_CENTER | wx.ALL, border=8)

        self.start_spin = wx.SpinCtrlDouble(panel, min=0.0, max=timeline_duration, initial=0)
        self.start_spin.SetIncrement(0.1)
        self.start_spin.SetName(tr("نقطة البداية"))
        self.end_spin = wx.SpinCtrlDouble(panel, min=0.0, max=timeline_duration, initial=0)
        self.end_spin.SetIncrement(0.1)
        self.end_spin.SetName(tr("نقطة النهاية"))
        self.text_input = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 80))
        self.text_input.SetName(tr("تحرير النص المفرغ"))

        time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        time_sizer.Add(wx.StaticText(panel, label="البداية:"), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        time_sizer.Add(self.start_spin, flag=wx.ALL, border=5)
        time_sizer.Add(wx.StaticText(panel, label="النهاية:"), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        time_sizer.Add(self.end_spin, flag=wx.ALL, border=5)
        main_sizer.Add(time_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=5)
        main_sizer.Add(self.text_input, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        self.start_spin.Bind(wx.EVT_SET_FOCUS, self.on_start_spin_focus)
        self.end_spin.Bind(wx.EVT_SET_FOCUS, self.on_end_spin_focus)
        self.text_input.Bind(wx.EVT_SET_FOCUS, self.on_text_input_focus)

        preview_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_preview = wx.Button(panel, label="تشغيل معاينة (Space)")
        self.btn_preview.Bind(wx.EVT_BUTTON, self.on_preview)
        preview_sizer.AddStretchSpacer()
        preview_sizer.Add(self.btn_preview, flag=wx.ALL, border=5)
        preview_sizer.AddStretchSpacer()
        main_sizer.Add(preview_sizer, flag=wx.EXPAND | wx.ALL, border=5)

        nav_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_prev = wx.Button(panel, label="السابق (Page Up)")
        self.btn_prev.Bind(wx.EVT_BUTTON, self.on_previous)
        nav_sizer.Add(self.btn_prev, flag=wx.ALL, border=5)
        nav_sizer.AddStretchSpacer()
        self.btn_next = wx.Button(panel, label="التالي (Page Down)")
        self.btn_next.Bind(wx.EVT_BUTTON, self.on_next)
        self.btn_next.SetDefault()
        nav_sizer.Add(self.btn_next, flag=wx.ALL, border=5)
        main_sizer.Add(nav_sizer, flag=wx.EXPAND | wx.ALL, border=5)

        panel.SetSizer(main_sizer)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self._preview_stop_call = None
        self.load_segment(0)
        self.Centre()
        wx.CallAfter(self.text_input.SetFocus)

    def load_segment(self, index):
        if not self.segments:
            return
        seg = self.modified_segments[index]
        self.segment_counter.SetLabel(f"القطعة {index + 1} من {len(self.modified_segments)}")
        self.start_spin.SetValue(seg.start)
        self.end_spin.SetValue(seg.end)
        self.text_input.SetValue(seg.text)
        is_last = index >= len(self.modified_segments) - 1
        self.btn_next.SetLabel("تطبيق التعديلات" if is_last else "التالي (Page Down)")
        self.btn_prev.Enable(index > 0)

    def on_start_spin_focus(self, event):
        self.say("نقطة البداية")
        event.Skip()

    def on_end_spin_focus(self, event):
        self.say("نقطة النهاية")
        event.Skip()

    def on_text_input_focus(self, event):
        self.say("تحرير النص المفرغ")
        event.Skip()

    def say(self, message):
        if hasattr(self.parent, "say"):
            self.parent.say(message)

    def save_current(self):
        seg = self.modified_segments[self.current_index]
        seg.start = self.start_spin.GetValue()
        seg.end = self.end_spin.GetValue()
        seg.text = self.text_input.GetValue()

    def on_previous(self, event):
        if self.current_index > 0:
            self._stop_preview()
            self.save_current()
            self.current_index -= 1
            self.load_segment(self.current_index)

    def on_next(self, event):
        is_last = self.current_index >= len(self.modified_segments) - 1
        self.save_current()
        self._stop_preview()
        if is_last:
            self.EndModal(wx.ID_OK)
            return
        self.current_index += 1
        self.load_segment(self.current_index)

    def on_preview(self, event):
        if not (hasattr(self.parent, "media_ctrl") and self.parent.media_ctrl):
            return
        start = self.start_spin.GetValue()
        end = self.end_spin.GetValue()
        if end <= start:
            return
        self._cancel_scheduled_stop()
        parent = self.parent
        parent.selected_playback_range = (start, end)
        parent.skipped_playback_range = None
        parent.current_time = start
        parent.playback_requested = True
        parent.load_timeline_time(start, True)
        self.btn_preview.SetLabel("إيقاف معاينة (Space)")
        self._preview_stop_call = wx.CallLater(int((end - start) * 1000) + 100, self._stop_preview)

    def toggle_preview(self):
        if getattr(self.parent, "playback_requested", False):
            self._stop_preview()
        else:
            self.on_preview(None)
        return True

    def _cancel_scheduled_stop(self):
        if self._preview_stop_call:
            try:
                self._preview_stop_call.Stop()
            except Exception:
                pass
            self._preview_stop_call = None

    def _stop_preview(self):
        self._cancel_scheduled_stop()
        try:
            self.btn_preview.SetLabel("تشغيل معاينة (Space)")
        except Exception:
            pass
        parent = self.parent
        if not (hasattr(parent, "media_ctrl") and parent.media_ctrl):
            return
        parent.playback_requested = False
        parent.pending_play = False
        parent.selected_playback_range = None
        parent.skipped_playback_range = None
        try:
            parent.media_ctrl.Pause()
        except Exception:
            pass
        parent.pause_original_audio_playback()
        parent.pause_background_audio_playback()

    def on_close(self, event):
        self._stop_preview()
        event.Skip()

    def on_char_hook(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_SPACE:
            focused = wx.Window.FindFocus()
            if focused is self.btn_preview:
                self.toggle_preview()
                return
            event.Skip()
            return
        elif key == wx.WXK_PAGEDOWN:
            self.on_next(None)
            return
        elif key == wx.WXK_PAGEUP:
            self.on_previous(None)
            return
        elif key == wx.WXK_ESCAPE:
            self._stop_preview()
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
