# -*- coding: utf-8 -*-
"""إدارة مسار الصوت البديل للفيديو.

هذا الملف هو النقطة المركزية الجديدة لكل ما يتعلق بصوت الفيديو البديل:
- إنشاء صوت كامل من الخط الزمني عند أول مؤثر.
- إبقاء الصوت متزامنًا بعد القص والإدراج والتكرار وتغيير السرعة.
- تجهيز نسخة تصدير مطابقة تمامًا لمدة الفيديو بدل رفض الحفظ.
- التحقق من وجود الصوت داخل ملف الفيديو الناتج.

لا يحتوي الملف على أي واجهة wxPython حتى يمكن اختباره بصورة مستقلة.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

try:
    from video_maker.app_paths import ffmpeg_binary
except ImportError:
    

    def get_setting(name):
        return ffmpeg_binary() if name == "FFMPEG_BINARY" else ""

from video_maker.operation_control import OperationCancelled
from video_maker.timeline import TimelineSegment, total_duration


class AudioOverrideError(RuntimeError):
    """خطأ واضح يمكن عرضه للمستخدم دون كشف تفاصيل تقنية غير مفيدة."""


def exported_duration_tolerance(expected_duration: float) -> float:
    expected = max(0.0, _float(expected_duration))
    return max(0.75, min(5.0, expected * 0.002))


@dataclass(frozen=True)
class PreparedAudio:
    """ملف صوت مؤقت جاهز للاستخدام مع مدة تم التحقق منها."""

    path: str
    duration: float
    temp_dir: str


@dataclass(frozen=True)
class ReconcileResult:
    """نتيجة مزامنة الصوت البديل مع خط زمني جديد."""

    path: str
    duration: float
    temp_dir: str
    changed: bool


_VISUAL_ONLY_OPERATION_TOKENS = (
    "إدراج نص",
    "إدراج صورة",
    "المؤثر المرئي",
    "تأثير مرئي",
    "علامة مائية",
    "تدوير",
    "كروما",
    "كرومة",
    "استبدال خلفية الفيديو",
    "انتقال",
    "توزيع الصور",
    "تعديل المعلومات",
)

_VISUAL_ONLY_EDIT_KINDS = frozenset({
    "image",
    "text",
    "visual_effect",
    "visual_transition",
    "transition",
    "rotate_video",
    "chroma_background",
    "watermark",
})


def visual_only_edit_kind(kind: str) -> bool:
    """تصنيف نقاط التعديل التي تغير الصورة فقط وتحافظ على الصوت كما هو."""
    return str(kind or "") in _VISUAL_ONLY_EDIT_KINDS


_AUDIO_ALREADY_UPDATED_OPERATION_TOKENS = (
    "المؤثر الصوتي",
    "استيراد صوت الفيديو",
)


def _cancelled(cancelled_callback: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(cancelled_callback and cancelled_callback())
    except Exception:
        return False


def _check_cancelled(cancelled_callback: Optional[Callable[[], bool]]) -> None:
    if _cancelled(cancelled_callback):
        raise OperationCancelled()


def _progress(progress_callback: Optional[Callable[..., Any]], percent: float, message: str = "") -> None:
    if not progress_callback:
        return
    value = max(0, min(100, int(round(percent))))
    try:
        progress_callback(value, message)
    except TypeError:
        progress_callback(value)


def _ffmpeg_binary() -> str:
    return str(ffmpeg_binary())


def _ffprobe_binary() -> str:
    """إرجاع ffprobe المجاور لـFFmpeg إن وُجد، وإلا اسم الأمر العام."""
    ffmpeg = os.path.abspath(_ffmpeg_binary())
    executable = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    candidate = os.path.join(os.path.dirname(ffmpeg), executable)
    return candidate if os.path.isfile(candidate) else executable


def _probe_stream_durations(path: str) -> dict:
    """قراءة مدد مسارات الصوت والصورة مستقلًا لضمان عدم انتهاء أحدهما مبكرًا.

    بعض الحاويات لا تكتب مدة على مستوى المسار؛ في هذه الحالة تعاد قائمة
    فارغة لذلك النوع ويظل فحص وجود المسار ومدة الحاوية هو خط الدفاع البديل.
    """
    command = [
        _ffprobe_binary(),
        "-v", "error",
        "-show_entries", "stream=codec_type,duration:format=duration",
        "-of", "json",
        path,
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=_startupinfo(),
        timeout=45,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(detail or "تعذر فحص مدد مسارات الفيديو")
    data = json.loads(process.stdout.decode("utf-8", errors="ignore") or "{}")
    result = {"audio": [], "video": [], "format": 0.0}
    format_value = (data.get("format") or {}).get("duration")
    if format_value not in (None, "", "N/A"):
        result["format"] = max(0.0, _float(format_value))
    for stream in data.get("streams") or []:
        kind = str(stream.get("codec_type", "") or "")
        if kind not in ("audio", "video"):
            continue
        value = stream.get("duration")
        if value in (None, "", "N/A"):
            continue
        duration = _float(value)
        if duration > 0.001:
            result[kind].append(duration)
    return result


def _startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def _run_ffmpeg(command: Sequence[str], error_message: str, cancelled_callback=None) -> None:
    _check_cancelled(cancelled_callback)
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=_startupinfo(),
    )
    while process.poll() is None:
        if _cancelled(cancelled_callback):
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            raise OperationCancelled()
        try:
            process.wait(timeout=0.08)
        except subprocess.TimeoutExpired:
            pass
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", errors="ignore").strip()
        raise AudioOverrideError(detail or error_message)


def _safe_copy_dicts(items: Iterable[dict]) -> List[dict]:
    return [copy.deepcopy(dict(item)) for item in (items or [])]


def _normalized_path(path: str) -> str:
    text = str(path or "")
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(text))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    if not math.isfinite(result):
        return float(default)
    return result


def _segment_speed(segment: TimelineSegment) -> float:
    return max(0.05, _float(getattr(segment, "speed", 1.0), 1.0))


def _segment_volume(segment: TimelineSegment) -> float:
    value = getattr(segment, "audio_volume", 1.0)
    if value is None:
        value = 1.0
    return max(0.0, min(1.0, _float(value, 1.0)))


def _segment_source_key(segment: TimelineSegment) -> Tuple[str, str]:
    return (
        _normalized_path(getattr(segment, "path", "")),
        _normalized_path(getattr(segment, "audio_path", "")),
    )


def _segment_contains(outer: TimelineSegment, inner: TimelineSegment, tolerance: float = 0.003) -> bool:
    if _segment_source_key(outer) != _segment_source_key(inner):
        return False
    return (
        _float(inner.start) >= _float(outer.start) - tolerance
        and _float(inner.end) <= _float(outer.end) + tolerance
        and _float(inner.end) > _float(inner.start) + tolerance
    )


def timeline_signature(timeline: Sequence[TimelineSegment]) -> Tuple[Tuple[Any, ...], ...]:
    """توقيع ثابت لا يتأثر بفروق الكسور غير المهمة."""

    result = []
    for segment in timeline or []:
        result.append((
            _segment_source_key(segment),
            round(_float(segment.start), 6),
            round(_float(segment.end), 6),
            round(_segment_speed(segment), 6),
            round(_float(getattr(segment, "audio_start", 0.0), 0.0), 6)
            if getattr(segment, "audio_start", None) is not None else None,
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
        ))
    return tuple(result)


def visual_only_operation(operation: str, old_timeline, new_timeline) -> bool:
    """هل التعديل بصري فقط ويمكن إبقاء الصوت كما هو؟"""

    name = str(operation or "")
    old_duration = total_duration(old_timeline or [])
    new_duration = total_duration(new_timeline or [])
    if abs(old_duration - new_duration) > 0.003:
        return False
    return any(token in name for token in _VISUAL_ONLY_OPERATION_TOKENS)


def audio_was_updated_by_operation(operation: str) -> bool:
    name = str(operation or "")
    return any(token in name for token in _AUDIO_ALREADY_UPDATED_OPERATION_TOKENS)


class MainAudioOverrideManager:
    """مدير مرتبط بنافذة المشغل دون الاعتماد على تفاصيل الواجهة."""

    FORMAT_VERSION = 2

    def __init__(
        self,
        player,
        *,
        duration_reader: Callable[[str], float],
        audio_stream_checker: Callable[[str], bool],
        video_stream_checker: Callable[[str], bool],
        timeline_audio_writer: Callable[..., Any],
    ):
        self.player = player
        self.duration_reader = duration_reader
        self.audio_stream_checker = audio_stream_checker
        self.video_stream_checker = video_stream_checker
        self.timeline_audio_writer = timeline_audio_writer

    # ------------------------------------------------------------------
    # الحالة والتسجيل
    # ------------------------------------------------------------------
    def initialize_player_state(self) -> None:
        defaults = {
            "main_audio_effect_chain": [],
            "main_audio_revision": 0,
            "main_audio_source_revision": 0,
            "timeline_revision": 0,
            "main_audio_format_version": self.FORMAT_VERSION,
            "main_audio_override_operation_running": False,
        }
        for name, value in defaults.items():
            if not hasattr(self.player, name):
                setattr(self.player, name, copy.deepcopy(value))

    def state_payload(self) -> dict:
        self.initialize_player_state()
        return {
            "main_audio_effect_chain": copy.deepcopy(getattr(self.player, "main_audio_effect_chain", [])),
            "main_audio_revision": int(getattr(self.player, "main_audio_revision", 0) or 0),
            "main_audio_source_revision": int(getattr(self.player, "main_audio_source_revision", 0) or 0),
            "timeline_revision": int(getattr(self.player, "timeline_revision", 0) or 0),
            "main_audio_format_version": int(getattr(self.player, "main_audio_format_version", self.FORMAT_VERSION) or self.FORMAT_VERSION),
        }

    def restore_state_payload(self, state: dict) -> None:
        self.initialize_player_state()
        self.player.main_audio_effect_chain = copy.deepcopy(state.get("main_audio_effect_chain", []) or [])
        self.player.main_audio_revision = int(state.get("main_audio_revision", 0) or 0)
        self.player.main_audio_source_revision = int(state.get("main_audio_source_revision", 0) or 0)
        self.player.timeline_revision = int(state.get("timeline_revision", 0) or 0)
        self.player.main_audio_format_version = int(state.get("main_audio_format_version", self.FORMAT_VERSION) or self.FORMAT_VERSION)

    def register_effect(self, effect_key: str, operation_name: str, start: float, end: float, parameters: Optional[dict] = None) -> None:
        self.initialize_player_state()
        item = {
            "id": str(uuid.uuid4()),
            "effect_key": str(effect_key or "audio_effect"),
            "operation_name": str(operation_name or "تطبيق المؤثر الصوتي"),
            "start": max(0.0, _float(start)),
            "end": max(0.0, _float(end)),
            "parameters": copy.deepcopy(parameters or {}),
            "timeline_revision": int(getattr(self.player, "timeline_revision", 0) or 0),
            # السجل وصفي فقط؛ الملف الصوتي الناتج هو المرجع التنفيذي.
            # يصبح replayable=False بعد أي تعديل زمني مركب لأن النطاقات الأصلية
            # لا يجوز تشغيلها آليًا على خط زمني مختلف.
            "replayable": True,
        }
        self.player.main_audio_effect_chain = [
            *copy.deepcopy(getattr(self.player, "main_audio_effect_chain", []) or []),
            item,
        ]
        self.player.main_audio_revision = int(getattr(self.player, "main_audio_revision", 0) or 0) + 1
        self.player.main_audio_source_revision = int(getattr(self.player, "timeline_revision", 0) or 0)
        self.player.main_audio_format_version = self.FORMAT_VERSION

    def mark_effect_chain_reconciled(self, operation: str) -> None:
        """وسم السجل الوصفي بعد تعديل زمني حتى لا يُعاد تشغيل نطاقات قديمة آليًا."""
        self.initialize_player_state()
        revision = int(getattr(self.player, "timeline_revision", 0) or 0)
        updated = []
        for raw_item in copy.deepcopy(getattr(self.player, "main_audio_effect_chain", []) or []):
            item = dict(raw_item or {})
            item["replayable"] = False
            item["reconciled_timeline_revision"] = revision
            item["reconciled_by_operation"] = str(operation or "تعديل زمني")
            updated.append(item)
        self.player.main_audio_effect_chain = updated

    # ------------------------------------------------------------------
    # إنشاء/فحص الصوت
    # ------------------------------------------------------------------
    def configured_path(self) -> str:
        return str(getattr(self.player, "main_audio_override_path", "") or "")

    def valid_audio_file(self, path: str) -> bool:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        try:
            return bool(self.audio_stream_checker(path)) and self.duration_reader(path) > 0.001
        except Exception:
            return False

    def exact_duration(self, path: str) -> float:
        if not self.valid_audio_file(path):
            raise AudioOverrideError("ملف صوت المشروع غير موجود أو لا يحتوي على صوت صالح")
        duration = _float(self.duration_reader(path))
        if duration <= 0.001:
            raise AudioOverrideError("تعذر قراءة مدة ملف صوت المشروع")
        return duration

    def _new_temp_dir(self, prefix: str) -> str:
        return tempfile.mkdtemp(prefix=prefix)

    def create_silence(self, duration: float, directory: str, name: str = "silence.wav", cancelled_callback=None) -> str:
        duration = max(0.001, _float(duration))
        path = os.path.join(directory, name)
        command = [
            _ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{duration:.9f}", "-c:a", "pcm_s16le", path,
        ]
        _run_ffmpeg(command, "تعذر إنشاء الصمت اللازم لصوت المشروع", cancelled_callback)
        if not self.valid_audio_file(path):
            raise AudioOverrideError("تعذر إنشاء الصمت اللازم لصوت المشروع")
        return path

    def fit_audio_to_duration(
        self,
        source_path: str,
        duration: float,
        *,
        source_start: float = 0.0,
        output_path: Optional[str] = None,
        temp_dir: Optional[str] = None,
        progress_callback=None,
        cancelled_callback=None,
    ) -> PreparedAudio:
        """قص أو إكمال الصوت بالصمت حتى يطابق المدة المطلوبة تمامًا."""

        _check_cancelled(cancelled_callback)
        duration = max(0.001, _float(duration))
        source_start = max(0.0, _float(source_start))
        if not self.valid_audio_file(source_path):
            raise AudioOverrideError("ملف صوت المشروع غير موجود أو لا يحتوي على صوت صالح")
        owned_dir = temp_dir or self._new_temp_dir("main_audio_fit_")
        os.makedirs(owned_dir, exist_ok=True)
        output_path = output_path or os.path.join(owned_dir, f"main_audio_fit_{uuid.uuid4().hex}.wav")
        _progress(progress_callback, 5, "جاري مطابقة مدة الصوت مع الفيديو")
        filter_text = (
            f"[0:a:0]atrim=start={source_start:.9f}:duration={duration:.9f},"
            "asetpts=PTS-STARTPTS,apad,"
            f"atrim=duration={duration:.9f},aresample=48000,"
            "aformat=sample_fmts=s16:channel_layouts=stereo[outa]"
        )
        command = [
            _ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", source_path,
            "-filter_complex", filter_text,
            "-map", "[outa]", "-c:a", "pcm_s16le", output_path,
        ]
        _run_ffmpeg(command, "تعذر مطابقة مدة صوت المشروع مع الفيديو", cancelled_callback)
        _progress(progress_callback, 95, "جاري التحقق من صوت الفيديو")
        actual = self.exact_duration(output_path)
        # حاوية WAV قد تختلف بعينة واحدة فقط؛ نرفض فقط الفرق الأكبر من 30 مللي ثانية.
        if abs(actual - duration) > 0.03:
            raise AudioOverrideError(
                f"تعذر تجهيز صوت مطابق لمدة الفيديو. مدة الفيديو {duration:.3f} ثانية ومدة الصوت {actual:.3f} ثانية"
            )
        _progress(progress_callback, 100, "تم تجهيز صوت الفيديو")
        return PreparedAudio(output_path, actual, owned_dir)

    @staticmethod
    def neutral_audio_timeline(timeline: Sequence[TimelineSegment]) -> List[TimelineSegment]:
        """نسخة صوتية لا تخبز الكتم داخل ملف المؤثرات.

        مستوى كل مقطع يظل خاصية غير إتلافية تُطبق في المعاينة والتصدير،
        حتى يمكن إلغاء الكتم لاحقًا دون الرجوع إلى الصوت الأصلي وفقد المؤثرات.
        """
        result: List[TimelineSegment] = []
        for segment in timeline or []:
            result.append(TimelineSegment(
                str(getattr(segment, "path", "") or ""),
                _float(getattr(segment, "start", 0.0)),
                _float(getattr(segment, "end", 0.0)),
                _segment_speed(segment),
                1.0,
                str(getattr(segment, "audio_path", "") or ""),
                getattr(segment, "audio_start", None),
                str(getattr(segment, "navigation_group", "") or ""),
                str(getattr(segment, "source_file_id", "") or ""),
                str(getattr(segment, "source_file_name", "") or ""),
            ))
        return result

    def renderable_neutral_audio_timeline(
        self,
        timeline: Sequence[TimelineSegment],
        temp_dir: str,
        cancelled_callback=None,
    ) -> List[TimelineSegment]:
        """تحويل الخط الزمني إلى مصادر صوت قابلة للقراءة مع صمت للمقاطع الصامتة."""
        result: List[TimelineSegment] = []
        silence_index = 0
        for segment in self.neutral_audio_timeline(timeline):
            _check_cancelled(cancelled_callback)
            source_path = str(getattr(segment, "audio_path", "") or getattr(segment, "path", "") or "")
            if source_path and self.valid_audio_file(source_path):
                result.append(segment)
                continue
            silence_index += 1
            silence_path = self.create_silence(
                segment.duration,
                temp_dir,
                f"base_silence_{silence_index:04d}.wav",
                cancelled_callback,
            )
            result.append(TimelineSegment(silence_path, 0.0, segment.duration))
        return result

    def ensure_effect_source(self, progress_callback=None, cancelled_callback=None) -> PreparedAudio:
        """إرجاع صوت كامل يمثل الخط الزمني الحالي، وإنشاؤه عند أول مؤثر."""

        self.initialize_player_state()
        timeline = list(getattr(self.player, "timeline", []) or [])
        expected = max(0.001, total_duration(timeline))
        current = self.configured_path()
        if self.valid_audio_file(current):
            actual = self.exact_duration(current)
            if abs(actual - expected) <= 0.03:
                return PreparedAudio(current, actual, "")
            return self.fit_audio_to_duration(
                current,
                expected,
                progress_callback=progress_callback,
                cancelled_callback=cancelled_callback,
            )

        temp_dir = self._new_temp_dir("main_audio_base_")
        output_path = os.path.join(temp_dir, f"main_audio_base_{uuid.uuid4().hex}.wav")
        _progress(progress_callback, 1, "جاري تجهيز صوت المشروع لأول مرة")
        try:
            render_timeline = self.renderable_neutral_audio_timeline(
                timeline,
                temp_dir,
                cancelled_callback,
            )
            self.timeline_audio_writer(
                render_timeline,
                output_path,
                lambda value: _progress(progress_callback, 1 + value * 0.89, "جاري تجهيز صوت المشروع لأول مرة"),
                cancelled_callback,
            )
            actual = self.exact_duration(output_path)
            if abs(actual - expected) > 0.03:
                fitted = self.fit_audio_to_duration(
                    output_path,
                    expected,
                    temp_dir=temp_dir,
                    progress_callback=lambda value, message="": _progress(progress_callback, 90 + value * 0.10, message),
                    cancelled_callback=cancelled_callback,
                )
                output_path, actual = fitted.path, fitted.duration
            _progress(progress_callback, 100, "تم تجهيز صوت المشروع")
            return PreparedAudio(output_path, actual, temp_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def compose_audio_replacement(
        self,
        source_path: str,
        replacement_path: str,
        start: float,
        end: float,
        *,
        target_duration: Optional[float] = None,
        temp_dir: Optional[str] = None,
        progress_callback=None,
        cancelled_callback=None,
    ) -> PreparedAudio:
        """استبدال نطاق داخل صوت المشروع بملف صوتي، دون لمس الفيديو.

        تستخدمها المؤثرات التي تستبدل الصوت كليًا داخل النطاق، مثل صوت
        التغطية، ويمكن إعادة استخدامها لأي مؤثر مشابه يضاف مستقبلًا.
        """
        if not self.valid_audio_file(source_path):
            raise AudioOverrideError("ملف صوت المشروع غير موجود أو لا يحتوي على صوت صالح")
        if not self.valid_audio_file(replacement_path):
            raise AudioOverrideError("ملف الصوت البديل للنطاق غير صالح")
        full_duration = _float(target_duration, self.exact_duration(source_path))
        start = max(0.0, min(_float(start), full_duration))
        end = max(start, min(_float(end), full_duration))
        if end <= start + 0.0005:
            raise AudioOverrideError("نطاق المؤثر الصوتي غير صالح")
        owned_dir = temp_dir or self._new_temp_dir("main_audio_replace_")
        os.makedirs(owned_dir, exist_ok=True)
        output_path = os.path.join(owned_dir, f"main_audio_replaced_{uuid.uuid4().hex}.wav")
        try:
            normalized_replacement = self.fit_audio_to_duration(
                replacement_path,
                end - start,
                temp_dir=owned_dir,
                progress_callback=lambda value, message="": _progress(progress_callback, value * 0.15, message),
                cancelled_callback=cancelled_callback,
            )
            parts: List[TimelineSegment] = []
            if start > 0.0005:
                parts.append(TimelineSegment(source_path, 0.0, start))
            parts.append(TimelineSegment(normalized_replacement.path, 0.0, end - start))
            if end < full_duration - 0.0005:
                parts.append(TimelineSegment(source_path, end, full_duration))
            self.timeline_audio_writer(
                parts,
                output_path,
                lambda value: _progress(progress_callback, 15 + value * 0.75, "جاري تثبيت الصوت البديل داخل النطاق"),
                cancelled_callback,
            )
            fitted = self.fit_audio_to_duration(
                output_path,
                full_duration,
                temp_dir=owned_dir,
                progress_callback=lambda value, message="": _progress(progress_callback, 90 + value * 0.10, message),
                cancelled_callback=cancelled_callback,
            )
            return fitted
        except Exception:
            if temp_dir is None:
                shutil.rmtree(owned_dir, ignore_errors=True)
            raise

    # ------------------------------------------------------------------
    # مزامنة الخط الزمني
    # ------------------------------------------------------------------
    @staticmethod
    def _old_spans(timeline: Sequence[TimelineSegment]):
        spans = []
        position = 0.0
        for index, segment in enumerate(timeline or []):
            spans.append((index, segment, position, position + segment.duration))
            position += segment.duration
        return spans

    def _best_old_mapping(self, new_segment, new_position, old_spans):
        candidates = []
        for index, old_segment, old_position, old_end in old_spans:
            if not _segment_contains(old_segment, new_segment):
                continue
            old_speed = _segment_speed(old_segment)
            source_delta_start = max(0.0, _float(new_segment.start) - _float(old_segment.start))
            source_delta_end = max(0.0, _float(new_segment.end) - _float(old_segment.start))
            mapped_start = old_position + source_delta_start / old_speed
            mapped_end = old_position + source_delta_end / old_speed
            if mapped_end <= mapped_start + 0.0005:
                continue
            old_group = str(getattr(old_segment, "navigation_group", "") or "")
            new_group = str(getattr(new_segment, "navigation_group", "") or "")
            group_penalty = 0.0 if old_group and old_group == new_group else 5.0 if old_group or new_group else 0.0
            distance = abs(mapped_start - new_position)
            candidates.append((group_penalty + distance, index, old_segment, mapped_start, mapped_end))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0]

    def _audio_parts_for_new_timeline(
        self,
        old_override_path: str,
        old_timeline: Sequence[TimelineSegment],
        new_timeline: Sequence[TimelineSegment],
        temp_dir: str,
        cancelled_callback=None,
    ) -> List[TimelineSegment]:
        old_spans = self._old_spans(old_timeline)
        parts: List[TimelineSegment] = []
        new_position = 0.0
        silence_index = 0
        for new_segment in new_timeline or []:
            _check_cancelled(cancelled_callback)
            desired_duration = max(0.0, new_segment.duration)
            if desired_duration <= 0.0005:
                continue
            mapping = self._best_old_mapping(new_segment, new_position, old_spans)
            if mapping is not None:
                _score, _index, old_segment, mapped_start, mapped_end = mapping
                mapped_duration = mapped_end - mapped_start
                speed = max(0.05, mapped_duration / desired_duration)
                # مستوى الصوت لا يُخبز داخل ملف المؤثرات؛ يطبق عند المعاينة والتصدير.
                # لذلك يظل الصوت المعالج قابلًا لإلغاء الكتم أو تغييره دون فقد المؤثرات.
                parts.append(TimelineSegment(old_override_path, mapped_start, mapped_end, speed, 1.0))
                new_position += desired_duration
                continue

            source_path = str(getattr(new_segment, "audio_path", "") or getattr(new_segment, "path", "") or "")
            if source_path and self.valid_audio_file(source_path):
                parts.append(TimelineSegment(
                    str(getattr(new_segment, "path", "") or source_path),
                    _float(getattr(new_segment, "start", 0.0)),
                    _float(getattr(new_segment, "end", 0.0)),
                    _segment_speed(new_segment),
                    1.0,
                    str(getattr(new_segment, "audio_path", "") or ""),
                    getattr(new_segment, "audio_start", None),
                    str(getattr(new_segment, "navigation_group", "") or ""),
                    str(getattr(new_segment, "source_file_id", "") or ""),
                    str(getattr(new_segment, "source_file_name", "") or ""),
                ))
            else:
                silence_index += 1
                silence_path = self.create_silence(
                    desired_duration,
                    temp_dir,
                    f"silence_{silence_index:04d}.wav",
                    cancelled_callback,
                )
                parts.append(TimelineSegment(silence_path, 0.0, desired_duration))
            new_position += desired_duration
        return parts

    def reconcile_after_timeline_edit(
        self,
        before_state: dict,
        operation: str,
        *,
        progress_callback=None,
        cancelled_callback=None,
        audio_policy: str = "auto",
    ) -> ReconcileResult:
        """تحديث الصوت البديل بعد أي تعديل زمني بصورة غير إتلافية."""

        self.initialize_player_state()
        old_path = str(before_state.get("main_audio_override_path", "") or "")
        current_path = self.configured_path()
        old_timeline = list(before_state.get("timeline", []) or [])
        new_timeline = list(getattr(self.player, "timeline", []) or [])
        old_signature = timeline_signature(old_timeline)
        new_signature = timeline_signature(new_timeline)

        if old_signature == new_signature:
            return ReconcileResult(current_path, _float(getattr(self.player, "main_audio_override_duration", 0.0)), "", False)
        self.player.timeline_revision = int(getattr(self.player, "timeline_revision", 0) or 0) + 1
        preserve_visual = audio_policy == "preserve" or (
            audio_policy == "auto" and visual_only_operation(operation, old_timeline, new_timeline)
        )
        if not preserve_visual:
            self.mark_effect_chain_reconciled(operation)

        current_is_valid = bool(current_path) and self.valid_audio_file(current_path)
        old_is_valid = bool(old_path) and self.valid_audio_file(old_path)
        if current_is_valid and (not old_is_valid or _normalized_path(current_path) != _normalized_path(old_path)):
            # العملية نفسها أنشأت أول صوت بديل أو أنشأت نسخة جديدة، مثل
            # إزالة الصمت أو استيراد صوت؛ اربطه بمراجعة الخط الزمني الحالية.
            self.player.main_audio_source_revision = int(getattr(self.player, "timeline_revision", 0) or 0)
            return ReconcileResult(current_path, self.exact_duration(current_path), "", False)
        if not old_is_valid:
            return ReconcileResult(current_path, _float(getattr(self.player, "main_audio_override_duration", 0.0)), "", False)
        if audio_policy == "already_updated" or audio_was_updated_by_operation(operation):
            self.player.main_audio_source_revision = int(getattr(self.player, "timeline_revision", 0) or 0)
            return ReconcileResult(current_path, self.exact_duration(current_path), "", False)
        if preserve_visual:
            self.player.main_audio_override_timeline_duration = total_duration(new_timeline)
            self.player.main_audio_source_revision = int(getattr(self.player, "timeline_revision", 0) or 0)
            return ReconcileResult(old_path, self.exact_duration(old_path), "", False)

        temp_dir = self._new_temp_dir("main_audio_reconcile_")
        output_path = os.path.join(temp_dir, f"main_audio_reconciled_{uuid.uuid4().hex}.wav")
        try:
            _progress(progress_callback, 1, "جاري تحديث صوت المشروع بعد التعديل")
            parts = self._audio_parts_for_new_timeline(
                old_path,
                old_timeline,
                new_timeline,
                temp_dir,
                cancelled_callback,
            )
            expected = max(0.001, total_duration(new_timeline))
            if not parts:
                silence_path = self.create_silence(expected, temp_dir, "empty_timeline_silence.wav", cancelled_callback)
                parts = [TimelineSegment(silence_path, 0.0, expected)]
            self.timeline_audio_writer(
                parts,
                output_path,
                lambda value: _progress(progress_callback, 5 + value * 0.90, "جاري تحديث صوت المشروع بعد التعديل"),
                cancelled_callback,
            )
            actual = self.exact_duration(output_path)
            if abs(actual - expected) > 0.03:
                fitted = self.fit_audio_to_duration(
                    output_path,
                    expected,
                    temp_dir=temp_dir,
                    progress_callback=lambda value, message="": _progress(progress_callback, 95 + value * 0.05, message),
                    cancelled_callback=cancelled_callback,
                )
                output_path, actual = fitted.path, fitted.duration
            self.player.main_audio_override_path = output_path
            self.player.main_audio_override_duration = actual
            self.player.main_audio_override_timeline_duration = expected
            self.player.main_audio_source_revision = int(getattr(self.player, "timeline_revision", 0) or 0)
            self.player.main_audio_revision = int(getattr(self.player, "main_audio_revision", 0) or 0) + 1
            _progress(progress_callback, 100, "تم تحديث صوت المشروع")
            return ReconcileResult(output_path, actual, temp_dir, True)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    # ------------------------------------------------------------------
    # التصدير والتحقق
    # ------------------------------------------------------------------
    def prepare_export_audio(
        self,
        *,
        source_path: Optional[str] = None,
        source_start: float = 0.0,
        duration: Optional[float] = None,
        timeline: Optional[Sequence[TimelineSegment]] = None,
        progress_callback=None,
        cancelled_callback=None,
    ) -> PreparedAudio:
        """تجهيز ملف يبدأ من صفر ومطابق للجزء المطلوب، ولا يعدّل حالة المشروع.

        إذا احتوى الخط الزمني على كتم أو خفض لمقاطع بعينها، يُطبَّق هنا على
        نسخة التصدير فقط؛ أما ملف المؤثرات فيظل غير إتلافي وقابلًا لإلغاء الكتم.
        """

        source_path = str(source_path or self.configured_path() or "")
        if not source_path:
            source = self.ensure_effect_source(progress_callback, cancelled_callback)
            source_path = source.path
        selected_timeline = list(timeline or [])
        target_duration = _float(duration, total_duration(selected_timeline or getattr(self.player, "timeline", []) or []))
        if target_duration <= 0.001:
            raise AudioOverrideError("مدة الفيديو المطلوب حفظه غير صالحة")

        needs_volume_render = bool(selected_timeline) and any(
            abs(_segment_volume(segment) - 1.0) > 0.0001
            for segment in selected_timeline
        )
        if not needs_volume_render:
            return self.fit_audio_to_duration(
                source_path,
                target_duration,
                source_start=source_start,
                progress_callback=progress_callback,
                cancelled_callback=cancelled_callback,
            )

        temp_dir = self._new_temp_dir("main_audio_export_volume_")
        raw_path = os.path.join(temp_dir, f"main_audio_export_volume_{uuid.uuid4().hex}.wav")
        position = max(0.0, _float(source_start))
        parts: List[TimelineSegment] = []
        for segment in selected_timeline:
            part_duration = max(0.0, _float(getattr(segment, "duration", 0.0)))
            if part_duration <= 0.0005:
                continue
            parts.append(TimelineSegment(
                source_path,
                position,
                position + part_duration,
                1.0,
                _segment_volume(segment),
            ))
            position += part_duration
        try:
            self.timeline_audio_writer(
                parts,
                raw_path,
                lambda value: _progress(progress_callback, value * 0.85, "جاري تطبيق مستويات صوت المقاطع"),
                cancelled_callback,
            )
            return self.fit_audio_to_duration(
                raw_path,
                target_duration,
                temp_dir=temp_dir,
                progress_callback=lambda value, message="": _progress(progress_callback, 85 + value * 0.15, message),
                cancelled_callback=cancelled_callback,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @staticmethod
    def partial_output_path(final_path: str) -> str:
        final_path = os.path.abspath(final_path)
        root, extension = os.path.splitext(final_path)
        return f"{root}.partial-{uuid.uuid4().hex}{extension}"

    def validate_exported_video(self, path: str, expected_duration: float, require_audio: bool = True) -> None:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 1024:
            raise AudioOverrideError("ملف الفيديو الناتج غير موجود أو غير مكتمل")
        try:
            if not self.video_stream_checker(path):
                raise AudioOverrideError("ملف الفيديو الناتج لا يحتوي على مسار فيديو")
            if require_audio and not self.audio_stream_checker(path):
                raise AudioOverrideError("ملف الفيديو الناتج لا يحتوي على صوت")
            actual = _float(self.duration_reader(path))
        except AudioOverrideError:
            raise
        except Exception as error:
            raise AudioOverrideError(f"تعذر فحص ملف الفيديو الناتج: {error}") from error
        tolerance = exported_duration_tolerance(expected_duration)
        if expected_duration > 0.001 and abs(actual - expected_duration) > tolerance:
            raise AudioOverrideError(
                f"مدة الفيديو الناتج غير مطابقة. المدة المطلوبة {expected_duration:.3f} ثانية والناتجة {actual:.3f} ثانية"
            )

        # مدة الحاوية وحدها لا تكفي: قد يستمر الفيديو بينما ينتهي الصوت مبكرًا.
        # نفحص مدد المسارات المستقلة عندما يوفرها ffprobe، ونحتفظ بالفحوص
        # السابقة كبديل للحاويات التي لا تسجل مدة على مستوى كل مسار.
        try:
            stream_durations = _probe_stream_durations(path)
        except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, ValueError, json.JSONDecodeError):
            stream_durations = {"audio": [], "video": []}
        if expected_duration > 0.001:
            video_durations = list(stream_durations.get("video") or [])
            if video_durations and max(video_durations) < expected_duration - tolerance:
                raise AudioOverrideError(
                    f"مسار الصورة في الفيديو الناتج ينتهي مبكرًا عند {max(video_durations):.3f} ثانية"
                )
            audio_durations = list(stream_durations.get("audio") or [])
            if require_audio and audio_durations and max(audio_durations) < expected_duration - tolerance:
                raise AudioOverrideError(
                    f"مسار الصوت في الفيديو الناتج ينتهي مبكرًا عند {max(audio_durations):.3f} ثانية"
                )

    def referenced_paths_from_history(self, current_state: dict, undo_states: Iterable[Any], redo_states: Iterable[Any]) -> set:
        """إرجاع ملفات الصوت التي يمنع حذفها لأنها مستخدمة في التراجع أو الاستعادة."""

        paths = set()
        states = [current_state]
        for item in list(undo_states or []) + list(redo_states or []):
            for attr in ("before_state", "after_state"):
                state = getattr(item, attr, None)
                if isinstance(state, dict):
                    states.append(state)
            if isinstance(item, dict):
                states.extend(state for state in (item.get("before_state"), item.get("after_state")) if isinstance(state, dict))
        for state in states:
            path = str((state or {}).get("main_audio_override_path", "") or "")
            if path:
                paths.add(_normalized_path(path))
        return paths


__all__ = [
    "AudioOverrideError",
    "MainAudioOverrideManager",
    "PreparedAudio",
    "ReconcileResult",
    "audio_was_updated_by_operation",
    "timeline_signature",
    "visual_only_operation",
    "visual_only_edit_kind",
]
