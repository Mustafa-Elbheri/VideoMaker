"""Paste media files copied in the operating system into the timeline.

The existing in-application segment clipboard remains independent.  This
module handles a real file-list clipboard (for example, media or .elbheri files
copied in Windows Explorer) and returns False when normal timeline paste should
run.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from video_maker.app_state import get_language
from video_maker.encrypted_projects import PROJECT_EXTENSION
from video_maker.save_options import _ffmpeg_binary
from video_maker.timeline import TimelineSegment, delete_range, insert_segments, slice_segments, total_duration
from video_maker.logical_files import display_file_name, new_file_segment, new_logical_file_id
from video_maker.video_editing import (
    ffmpeg_startupinfo,
    get_media_duration,
    has_audio_stream,
    has_video_stream,
    write_timeline_audio,
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SUPPORTED_KINDS = {"image", "audio", "video"}
_EPSILON = 0.001


_MESSAGES = {
    "ar": {
        "clipboard_read_failed": "تعذر قراءة الملفات المنسوخة من الحافظة",
        "copy_one": "انسخ ملف صورة أو صوت أو فيديو أو مشروع بحيري واحدا في كل مرة",
        "missing": "الملف المنسوخ لم يعد موجودا على الكمبيوتر",
        "unsupported": "الملف المنسوخ ليس صورة أو ملفا صوتيا أو فيديو مدعوما",
        "invalid_duration": "تعذر تحديد مدة الملف المنسوخ",
        "image_requires_media": "أضف ملفا صوتيا أو ملف فيديو أولا ثم الصق الصورة",
        "image_requires_end": "حدد نقطة النهاية للصورة أولا",
        "preparing_image": "جاري تجهيز الصورة للصق في الخط الزمني",
        "preparing_audio": "جاري تجهيز الملف الصوتي للصق في الخط الزمني",
        "background_audio_no_room": "لا توجد مدة بعد نقطة البداية لإضافة الخلفية الصوتية",
        "background_audio_failed": "تعذر إدراج الملف الصوتي كخلفية صوتية",
        "background_audio_inserted": "تم إدراج الملف الصوتي كخلفية صوتية",
        "video_expand_question": "الفيديو أكبر من التحديد. هل تريد توسيع نقطة النهاية؟",
        "video_expand_title": "توسيع التحديد",
        "video_no_room": "لا توجد مدة بعد نقطة البداية لإضافة الفيديو فوق الملف الصوتي",
        "failed_image": "تعذر لصق الصورة في الخط الزمني",
        "failed_audio": "تعذر لصق الملف الصوتي في الخط الزمني",
        "failed_video": "تعذر لصق الفيديو في الخط الزمني",
        "image_append": "تم لصق الصورة في نهاية الخط الزمني",
        "audio_append": "تم لصق الملف الصوتي في نهاية الخط الزمني",
        "video_append": "تم لصق الفيديو في نهاية الخط الزمني",
        "audio_new_project": "تم فتح الملف الصوتي كمشروع جديد",
        "video_new_project": "تم فتح الفيديو كمشروع جديد",
        "image_start": "تم لصق الصورة عند نقطة البداية",
        "audio_start": "تم لصق الملف الصوتي عند نقطة البداية",
        "video_start": "تم لصق الفيديو عند نقطة البداية",
        "image_range": "تم لصق الصورة داخل التحديد",
        "audio_range": "تم لصق الملف الصوتي داخل التحديد",
        "video_range": "تم لصق الفيديو داخل التحديد",
        "image_point": "هنا أضفت صورة من الحافظة",
        "audio_point": "هنا أضفت ملفا صوتيا من الحافظة",
        "video_point": "هنا أضفت فيديو من الحافظة",
        "clipboard_empty": "الحافظة لا تحتوي على ملف وسائط أو مشروع أو مقطع من الخط الزمني",
        "paste_busy": "انتظر حتى تنتهي العملية الحالية ثم الصق مرة أخرى",
        "paste_running": "جاري لصق ملف بالفعل. انتظر حتى يكتمل ثم يمكنك تكرار اللصق",
        "paste_preparing": "جاري فحص وتجهيز الملف المنسوخ",
        "paste_progress": "نسبة تجهيز الملف المنسوخ {percent} بالمئة",
        "paste_status": "حالة تجهيز الملف المنسوخ",
        "paste_gauge": "شريط تقدم تجهيز الملف المنسوخ",
        "paste_cancel": "إلغاء لصق الملف",
        "paste_cancelling": "جاري إلغاء لصق الملف",
        "paste_cancelled": "تم إلغاء لصق الملف",
        "paste_timeout": "استغرق فحص الملف وقتا أطول من المسموح",
        "source_changed": "تغير الملف المنسوخ أثناء تجهيزه. انسخه من جديد ثم أعد اللصق",
        "internal_kind_mismatch": "نوع المقطع المنسوخ لا يطابق نوع المشروع المفتوح",
    },
    "en": {
        "clipboard_read_failed": "Could not read the copied files from the clipboard",
        "copy_one": "Copy one image, audio, video, or Albheri project file at a time",
        "missing": "The copied file no longer exists on the computer",
        "unsupported": "The copied file is not a supported image, audio, or video file",
        "invalid_duration": "Could not determine the duration of the copied file",
        "image_requires_media": "Add an audio or video file first, then paste the image",
        "image_requires_end": "Set the image end point first",
        "preparing_image": "Preparing the image for pasting into the timeline",
        "preparing_audio": "Preparing the audio file for pasting into the timeline",
        "background_audio_no_room": "There is no duration after the start point for background audio",
        "background_audio_failed": "Could not insert the audio file as background audio",
        "background_audio_inserted": "The audio file was inserted as background audio",
        "video_expand_question": "The video is longer than the selection. Do you want to extend the end point?",
        "video_expand_title": "Extend selection",
        "video_no_room": "There is no duration after the start point for adding the video over the audio file",
        "failed_image": "Could not paste the image into the timeline",
        "failed_audio": "Could not paste the audio file into the timeline",
        "failed_video": "Could not paste the video into the timeline",
        "image_append": "The image was pasted at the end of the timeline",
        "audio_append": "The audio file was pasted at the end of the timeline",
        "video_append": "The video was pasted at the end of the timeline",
        "audio_new_project": "The audio file was opened as a new project",
        "video_new_project": "The video was opened as a new project",
        "image_start": "The image was pasted at the start point",
        "audio_start": "The audio file was pasted at the start point",
        "video_start": "The video was pasted at the start point",
        "image_range": "The image was pasted inside the selection",
        "audio_range": "The audio file was pasted inside the selection",
        "video_range": "The video was pasted inside the selection",
        "image_point": "An image copied from the clipboard was added here",
        "audio_point": "An audio file copied from the clipboard was added here",
        "video_point": "A video copied from the clipboard was added here",
        "clipboard_empty": "The clipboard does not contain a media file, project, or timeline segment",
        "paste_busy": "Wait for the current operation to finish, then paste again",
        "paste_running": "A file is already being pasted. Wait for it to finish, then you can paste it again",
        "paste_preparing": "Checking and preparing the copied file",
        "paste_progress": "Copied file preparation is {percent} percent complete",
        "paste_status": "Copied file preparation status",
        "paste_gauge": "Copied file preparation progress",
        "paste_cancel": "Cancel file paste",
        "paste_cancelling": "Cancelling file paste",
        "paste_cancelled": "File paste was cancelled",
        "paste_timeout": "Checking the file took longer than allowed",
        "source_changed": "The copied file changed while it was being prepared. Copy it again, then paste",
        "internal_kind_mismatch": "The copied segment type does not match the open project type",
    },
    "fr": {
        "clipboard_read_failed": "Impossible de lire les fichiers copiés depuis le presse-papiers",
        "copy_one": "Copiez un seul fichier image, audio, vidéo ou projet Albheri à la fois",
        "missing": "Le fichier copié n’existe plus sur l’ordinateur",
        "unsupported": "Le fichier copié n’est pas un fichier image, audio ou vidéo pris en charge",
        "invalid_duration": "Impossible de déterminer la durée du fichier copié",
        "image_requires_media": "Ajoutez d’abord un fichier audio ou vidéo, puis collez l’image",
        "image_requires_end": "Définissez d’abord le point de fin de l’image",
        "preparing_image": "Préparation de l’image pour le collage dans la ligne de temps",
        "preparing_audio": "Préparation du fichier audio pour le collage dans la ligne de temps",
        "background_audio_no_room": "Il n’y a aucune durée après le point de début pour ajouter l’audio d’arrière-plan",
        "background_audio_failed": "Impossible d’insérer le fichier audio comme audio d’arrière-plan",
        "background_audio_inserted": "Le fichier audio a été inséré comme audio d’arrière-plan",
        "video_expand_question": "La vidéo est plus longue que la sélection. Voulez-vous prolonger le point de fin ?",
        "video_expand_title": "Prolonger la sélection",
        "video_no_room": "Il n’y a aucune durée après le point de début pour ajouter la vidéo sur le fichier audio",
        "failed_image": "Impossible de coller l’image dans la ligne de temps",
        "failed_audio": "Impossible de coller le fichier audio dans la ligne de temps",
        "failed_video": "Impossible de coller la vidéo dans la ligne de temps",
        "image_append": "L’image a été collée à la fin de la ligne de temps",
        "audio_append": "Le fichier audio a été collé à la fin de la ligne de temps",
        "video_append": "La vidéo a été collée à la fin de la ligne de temps",
        "audio_new_project": "Le fichier audio a été ouvert comme nouveau projet",
        "video_new_project": "La vidéo a été ouverte comme nouveau projet",
        "image_start": "L’image a été collée au point de début",
        "audio_start": "Le fichier audio a été collé au point de début",
        "video_start": "La vidéo a été collée au point de début",
        "image_range": "L’image a été collée dans la sélection",
        "audio_range": "Le fichier audio a été collé dans la sélection",
        "video_range": "La vidéo a été collée dans la sélection",
        "image_point": "Une image copiée depuis le presse-papiers a été ajoutée ici",
        "audio_point": "Un fichier audio copié depuis le presse-papiers a été ajouté ici",
        "video_point": "Une vidéo copiée depuis le presse-papiers a été ajoutée ici",
        "clipboard_empty": "Le presse-papiers ne contient aucun média, projet ou segment de ligne de temps",
        "paste_busy": "Attendez la fin de l’opération en cours, puis recollez",
        "paste_running": "Un fichier est déjà en cours de collage. Attendez la fin, puis vous pourrez le recoller",
        "paste_preparing": "Vérification et préparation du fichier copié",
        "paste_progress": "Préparation du fichier copié : {percent} pour cent",
        "paste_status": "État de préparation du fichier copié",
        "paste_gauge": "Progression de la préparation du fichier copié",
        "paste_cancel": "Annuler le collage du fichier",
        "paste_cancelling": "Annulation du collage du fichier",
        "paste_cancelled": "Le collage du fichier a été annulé",
        "paste_timeout": "La vérification du fichier a dépassé le délai autorisé",
        "source_changed": "Le fichier copié a changé pendant sa préparation. Copiez-le de nouveau, puis recollez",
        "internal_kind_mismatch": "Le type du segment copié ne correspond pas au type du projet ouvert",
    },
}


@dataclass(frozen=True)
class PastePlacement:
    mode: str  # append, start, or range
    start: float
    end: Optional[float] = None

    @property
    def duration(self) -> Optional[float]:
        if self.end is None:
            return None
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class PreparedMedia:
    kind: str
    duration: float
    has_audio: bool
    has_video: bool
    proxy_path: str = ""
    proxy_temp_dir: str = ""


class PastePreparationCancelled(RuntimeError):
    pass


_last_internal_copy_sequence: Optional[int] = None
_last_internal_copy_files: tuple[str, ...] = ()
_active_clipboard_source = "none"  # none, internal, files, or other
_active_file_sequence: Optional[int] = None
_active_file_signature: tuple[str, ...] = ()
_paste_owner = None
_paste_owner_lock = threading.Lock()
_timeline_clipboard_lock = threading.RLock()
_timeline_clipboard_segments: tuple[TimelineSegment, ...] = ()
_timeline_clipboard_media_kind = "none"
_timeline_clipboard_generation = 0
_timeline_clipboard_operation = "copy"
_AUDIO_ONLY_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".ac3", ".amr", ".ape", ".mka",
}


def message(key: str) -> str:
    language = str(get_language() or "ar").lower()
    table = _MESSAGES.get(language, _MESSAGES["ar"])
    return table.get(key, _MESSAGES["ar"].get(key, key))


def clear_marker_state(player) -> None:
    player._clipboard_paste_start_explicit = False
    player._clipboard_paste_end_explicit = False


def note_start_marker(player) -> None:
    # A new start command begins a fresh one-point placement.  Reset any old
    # end flag left by a completed edit so stale selection history can never
    # turn a new start point into an unintended range.
    player._clipboard_paste_start_explicit = True
    player._clipboard_paste_end_explicit = False


def note_end_marker(player) -> None:
    # OnSetEnd creates a zero start when needed, so an explicit end always
    # represents a complete range in the program's existing selection model.
    player._clipboard_paste_start_explicit = True
    player._clipboard_paste_end_explicit = True


def note_full_selection(player) -> None:
    player._clipboard_paste_start_explicit = True
    player._clipboard_paste_end_explicit = True


def clipboard_sequence_number() -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def _normalised_file_signature(paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(os.path.normcase(os.path.abspath(str(path))) for path in paths or ())


def _clone_timeline_segments(segments: Sequence[TimelineSegment]) -> tuple[TimelineSegment, ...]:
    result = []
    for segment in segments or ():
        result.append(TimelineSegment(
            str(segment.path),
            float(segment.start),
            float(segment.end),
            float(getattr(segment, "speed", 1.0) or 1.0),
            float(getattr(segment, "audio_volume", 1.0) if getattr(segment, "audio_volume", 1.0) is not None else 1.0),
            str(getattr(segment, "audio_path", "") or ""),
            (None if getattr(segment, "audio_start", None) is None else float(segment.audio_start)),
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
            str(getattr(segment, "transition", "") or ""),
            max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        ))
    return tuple(result)


def set_internal_timeline_clipboard(player, segments, operation: str = "copy") -> bool:
    """Publish an immutable timeline clipboard shared by every open window."""
    global _timeline_clipboard_segments, _timeline_clipboard_media_kind
    global _timeline_clipboard_generation, _timeline_clipboard_operation
    snapshot = _clone_timeline_segments(segments)
    if not snapshot:
        return False
    media_kind = str(getattr(player, "media_kind", "none") or "none")
    if media_kind not in ("audio", "video"):
        media_kind = "none"
    with _timeline_clipboard_lock:
        _timeline_clipboard_segments = snapshot
        _timeline_clipboard_media_kind = media_kind
        _timeline_clipboard_operation = "cut" if str(operation).lower() == "cut" else "copy"
        _timeline_clipboard_generation += 1
    note_internal_timeline_copy(player)
    return True


def internal_timeline_clipboard_snapshot():
    """Return independent segment objects plus source metadata."""
    with _timeline_clipboard_lock:
        return (
            list(_clone_timeline_segments(_timeline_clipboard_segments)),
            _timeline_clipboard_media_kind,
            _timeline_clipboard_operation,
            _timeline_clipboard_generation,
        )


def internal_timeline_clipboard_segments() -> list[TimelineSegment]:
    return internal_timeline_clipboard_snapshot()[0]


def internal_timeline_clipboard_media_kind() -> str:
    return internal_timeline_clipboard_snapshot()[1]


def note_internal_timeline_copy(player=None) -> None:
    """Make the application's segment clipboard the explicit active source."""
    global _last_internal_copy_sequence, _last_internal_copy_files, _active_clipboard_source
    _last_internal_copy_sequence = clipboard_sequence_number()
    try:
        _last_internal_copy_files = _normalised_file_signature(read_file_clipboard(retries=2))
    except Exception:
        _last_internal_copy_files = ()
    _active_clipboard_source = "internal"


def read_file_clipboard(retries: int = 5, delay: float = 0.04) -> list[str]:
    """Return native file paths without changing the clipboard.

    Windows Explorer and clipboard managers can hold the clipboard briefly.
    Retrying for a fraction of a second avoids false failures without freezing
    the interface for a noticeable period.
    """
    import wx

    attempts = max(1, int(retries))
    for attempt in range(attempts):
        if wx.TheClipboard.Open():
            try:
                file_format = wx.DataFormat(wx.DF_FILENAME)
                if not wx.TheClipboard.IsSupported(file_format):
                    return []
                data = wx.FileDataObject()
                if not wx.TheClipboard.GetData(data):
                    return []
                return [str(path) for path in data.GetFilenames()]
            finally:
                wx.TheClipboard.Close()
        if attempt + 1 < attempts:
            time.sleep(max(0.0, float(delay)))
    raise RuntimeError("clipboard busy")


def _has_internal_timeline_clipboard(player) -> bool:
    try:
        if internal_timeline_clipboard_segments():
            return True
    except Exception:
        pass
    # Compatibility for callers that still publish their local clipboard and
    # then call note_internal_timeline_copy directly.
    try:
        return bool(player.timeline_clipboard_for_paste())
    except Exception:
        return False


def _same_native_clipboard_as_internal(file_paths: Sequence[str], sequence: Optional[int]) -> bool:
    if sequence is not None and _last_internal_copy_sequence is not None:
        return sequence == _last_internal_copy_sequence
    return _normalised_file_signature(file_paths) == _last_internal_copy_files


def _select_clipboard_source(player, file_paths: Sequence[str], sequence: Optional[int]) -> str:
    """Return the one authoritative source for this paste request.

    Internal copy wins until Windows' native clipboard actually changes. Once a
    new file is copied, repeated paste keeps using that file. Copying a timeline
    segment afterwards switches back to the internal source even though the old
    Windows file remains available. Text or other non-file clipboard content
    never falls back to an older internal segment.
    """
    global _active_clipboard_source, _active_file_sequence, _active_file_signature
    has_internal = _has_internal_timeline_clipboard(player)
    signature = _normalised_file_signature(file_paths)

    if _active_clipboard_source == "internal" and has_internal:
        if _same_native_clipboard_as_internal(file_paths, sequence):
            return "internal"
        if file_paths:
            _active_clipboard_source = "files"
            _active_file_sequence = sequence
            _active_file_signature = signature
            return "files"
        _active_clipboard_source = "other"
        return "other"

    if file_paths:
        if (
            _active_clipboard_source == "files"
            and signature == _active_file_signature
            and (sequence is None or _active_file_sequence is None or sequence == _active_file_sequence)
        ):
            return "files"
        _active_clipboard_source = "files"
        _active_file_sequence = sequence
        _active_file_signature = signature
        return "files"

    if _active_clipboard_source == "internal" and has_internal:
        return "internal"
    _active_clipboard_source = "other"
    return "other"


def focused_control_owns_paste(player, perform: bool = False) -> bool:
    """Return True only when Ctrl+V genuinely belongs to an editing control.

    Another main program window is not a child dialog. wx can briefly report
    focus in the previously active main window while the new frame receives
    its accelerator event; treating that as a dialog used to block
    cross-window timeline paste silently.
    """
    try:
        import wx
        focused = wx.Window.FindFocus()
    except Exception:
        return False
    if focused is None:
        return False

    text_types = tuple(
        item for item in (
            getattr(wx, "TextCtrl", None),
            getattr(wx, "ComboBox", None),
            getattr(wx, "SearchCtrl", None),
            getattr(wx, "SpinCtrl", None),
            getattr(wx, "SpinCtrlDouble", None),
        ) if isinstance(item, type)
    )
    owns_text_paste = bool(text_types and isinstance(focused, text_types)) or (
        hasattr(focused, "CanPaste") and hasattr(focused, "Paste")
    )
    if owns_text_paste:
        if perform:
            method = getattr(focused, "Paste", None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
        return True

    try:
        top_level = focused.GetTopLevelParent()
    except Exception:
        try:
            top_level = wx.GetTopLevelParent(focused)
        except Exception:
            top_level = None
    if top_level is player:
        return False

    # A stale focus reference to another main VideoPlayer window must not
    # suppress paste in the frame that actually received Ctrl+V.
    try:
        if top_level in player.open_program_windows():
            return False
    except Exception:
        pass

    # Real child dialogs and controls owned by other applications keep their
    # native paste behaviour.
    return top_level is not None

def _operation_busy(player) -> bool:
    if getattr(player, "closing", False):
        return True
    if getattr(player, "timeline_transform_progress_dialog", None) is not None:
        return True
    if bool(getattr(player, "project_operation_running", False)) or getattr(player, "project_progress_dialog", None) is not None:
        return True
    if getattr(player, "merge_progress_dialog", None) is not None:
        return True
    if getattr(player, "progress_dialog", None) is not None or bool(getattr(player, "save_operation_running", False)):
        return True
    if getattr(player, "update_progress_dialog", None) is not None or bool(getattr(player, "update_check_running", False)):
        return True
    if bool(getattr(player, "recording_finalizing", False)) or bool(getattr(player, "recording_start_pending", False)):
        return True
    try:
        if player.recording_is_active():
            return True
    except Exception:
        pass
    if bool(getattr(player, "text_overlay_running", False)):
        return True
    return False


def can_start_paste(player, announce: bool = True) -> bool:
    with _paste_owner_lock:
        owner = _paste_owner
    if owner is not None:
        if announce:
            player.say(message("paste_running"))
        return False
    if _operation_busy(player):
        if announce:
            player.say(message("paste_busy"))
        return False
    return True


def begin_paste_operation(player, announce: bool = True) -> bool:
    global _paste_owner
    if _operation_busy(player):
        if announce:
            player.say(message("paste_busy"))
        return False
    with _paste_owner_lock:
        if _paste_owner is not None:
            if announce:
                player.say(message("paste_running"))
            return False
        _paste_owner = player
    player._clipboard_paste_running = True
    return True


def end_paste_operation(player) -> None:
    global _paste_owner
    try:
        player._clipboard_paste_running = False
    except Exception:
        pass
    with _paste_owner_lock:
        if _paste_owner is player:
            _paste_owner = None

def classify_media_file(path: str) -> Optional[str]:
    extension = os.path.splitext(path)[1].lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    try:
        if has_video_stream(path):
            return "video"
        if has_audio_stream(path):
            return "audio"
    except Exception:
        return None
    return None



def _ffprobe_binary() -> str:
    ffmpeg = os.path.abspath(_ffmpeg_binary())
    directory = os.path.dirname(ffmpeg)
    executable = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    candidate = os.path.join(directory, executable) if directory else executable
    return candidate if os.path.exists(candidate) else executable


def _run_process_capture(
    command: Sequence[str],
    cancel_event: Optional[threading.Event] = None,
    timeout: float = 60.0,
) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
    )
    started = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            raise PastePreparationCancelled(message("paste_cancelled"))
        try:
            stdout, stderr = process.communicate(timeout=0.15)
            return int(process.returncode or 0), stdout, stderr
        except subprocess.TimeoutExpired:
            if timeout and time.monotonic() - started > timeout:
                try:
                    process.kill()
                except Exception:
                    pass
                process.communicate()
                raise RuntimeError(message("paste_timeout"))


def _probe_media(path: str, cancel_event: Optional[threading.Event] = None) -> PreparedMedia:
    extension = os.path.splitext(path)[1].lower()
    if extension in IMAGE_EXTENSIONS:
        return PreparedMedia("image", 0.0, False, True)

    command = [
        _ffprobe_binary(),
        "-v", "error",
        "-show_entries", "stream=codec_type,duration:stream_disposition=attached_pic:format=duration",
        "-of", "json",
        path,
    ]
    try:
        return_code, stdout, stderr = _run_process_capture(command, cancel_event, timeout=45.0)
        if return_code != 0 and not stdout:
            raise RuntimeError(stderr.decode("utf-8", errors="ignore").strip())
        data = json.loads(stdout.decode("utf-8", errors="ignore") or "{}")
        streams = data.get("streams") or []
        has_audio = any(str(stream.get("codec_type", "")) == "audio" for stream in streams)
        has_video = False
        if extension not in _AUDIO_ONLY_EXTENSIONS:
            for stream in streams:
                if str(stream.get("codec_type", "")) != "video":
                    continue
                disposition = stream.get("disposition") or {}
                if not int(disposition.get("attached_pic", 0) or 0):
                    has_video = True
                    break
        duration_values = []
        format_duration = (data.get("format") or {}).get("duration")
        if format_duration not in (None, "", "N/A"):
            duration_values.append(float(format_duration))
        for stream in streams:
            value = stream.get("duration")
            if value not in (None, "", "N/A"):
                try:
                    duration_values.append(float(value))
                except (TypeError, ValueError):
                    pass
        duration = max(duration_values or [0.0])
    except FileNotFoundError:
        # Packaged builds normally include ffprobe. Fall back to FFmpeg output
        # while retaining a hard timeout and cancellation support.
        _return_code, stdout, stderr = _run_process_capture(
            [_ffmpeg_binary(), "-hide_banner", "-i", path],
            cancel_event,
            timeout=45.0,
        )
        text = stderr.decode("utf-8", errors="ignore")
        has_audio = " Audio: " in text
        video_lines = [line for line in text.splitlines() if " Video: " in line]
        has_video = extension not in _AUDIO_ONLY_EXTENSIONS and any(
            "attached pic" not in line.lower().replace("_", " ") for line in video_lines
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        duration = 0.0
        if match:
            duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))

    if has_video:
        kind = "video"
    elif has_audio:
        kind = "audio"
    else:
        raise RuntimeError(message("unsupported"))
    if duration <= _EPSILON:
        raise RuntimeError(message("invalid_duration"))
    return PreparedMedia(kind, duration, has_audio, has_video)


def _run_preparation_dialog(player, worker):
    """Run media inspection/preparation off the GUI thread with cancellation."""
    try:
        import wx
        from video_maker.save_progress import SaveProgressDialog
    except Exception:
        cancel_event = threading.Event()
        return "success", worker(lambda _percent: None, cancel_event), None

    cancel_event = threading.Event()
    result = {"status": "error", "value": None, "error": None}
    last_spoken = [-25]

    def request_cancel():
        cancel_event.set()
        player.say(message("paste_cancelling"))

    dialog = SaveProgressDialog(
        player,
        request_cancel,
        title=message("paste_preparing"),
        progress_template=message("paste_progress"),
        status_name=message("paste_status"),
        gauge_name=message("paste_gauge"),
        cancel_name=message("paste_cancel"),
        cancelling_message=message("paste_cancelling"),
    )

    def progress(percent):
        value = max(0, min(100, int(percent)))
        wx.CallAfter(dialog.update_progress, value)
        if value >= last_spoken[0] + 25 or value >= 100:
            last_spoken[0] = value
            wx.CallAfter(
                player.say,
                message("paste_progress").format(percent=value),
                False,
            )

    def finish(status, value=None, error=None):
        result.update(status=status, value=value, error=error)
        try:
            if dialog.IsModal():
                dialog.EndModal(wx.ID_OK)
            else:
                dialog.Hide()
        except Exception:
            pass

    def run():
        try:
            value = worker(progress, cancel_event)
            if cancel_event.is_set():
                wx.CallAfter(finish, "cancelled", None, None)
            else:
                wx.CallAfter(finish, "success", value, None)
        except PastePreparationCancelled as error:
            wx.CallAfter(finish, "cancelled", None, error)
        except Exception as error:
            wx.CallAfter(finish, "error", None, error)

    threading.Thread(target=run, daemon=True).start()
    player.say(message("paste_preparing"))
    try:
        dialog.ShowModal()
    finally:
        dialog.Destroy()
    return result["status"], result["value"], result["error"]


def _prepare_media_for_paste(player, path: str, placement: PastePlacement) -> PreparedMedia:
    empty_workspace = not bool(getattr(player, "timeline", []))
    current_kind = str(getattr(player, "media_kind", "video") or "video")

    def worker(progress, cancel_event):
        progress(5)
        prepared = _probe_media(path, cancel_event)
        progress(35)
        effective_kind = (
            "audio" if empty_workspace and prepared.kind == "audio"
            else "video" if empty_workspace
            else current_kind
        )
        proxy_path = ""
        proxy_temp_dir = ""
        if prepared.kind == "audio" and effective_kind == "video" and placement.mode == "append":
            progress(45)
            proxy_path, proxy_temp_dir = create_black_video_proxy(
                path, prepared.duration, cancel_event=cancel_event
            )
        elif (
            prepared.kind == "video"
            and effective_kind == "audio"
            and placement.mode == "append"
            and not prepared.has_audio
        ):
            progress(45)
            proxy_path, proxy_temp_dir = create_silent_audio_proxy(
                path, prepared.duration, cancel_event=cancel_event
            )
        progress(100)
        return PreparedMedia(
            prepared.kind,
            prepared.duration,
            prepared.has_audio,
            prepared.has_video,
            proxy_path,
            proxy_temp_dir,
        )

    status, value, error = _run_preparation_dialog(player, worker)
    if status == "cancelled":
        player.say(message("paste_cancelled"))
        return None
    if status != "success":
        raise RuntimeError(str(error or message("unsupported")))
    return value

def resolve_placement(player) -> PastePlacement:
    duration = max(0.0, float(player.timeline_duration()))
    start_value = getattr(player, "start_time", None)
    end_value = getattr(player, "end_time", None)
    start_explicit = bool(getattr(player, "_clipboard_paste_start_explicit", False))
    end_explicit = bool(getattr(player, "_clipboard_paste_end_explicit", False))

    if start_value is not None:
        start = max(0.0, min(float(start_value), duration))
    else:
        start = None
    if end_value is not None:
        end = max(0.0, min(float(end_value), duration))
    else:
        end = None

    # The main program automatically fills end_time with the project duration
    # when the user sets only a start point.  The explicit-marker flags retain
    # the user's real intent so a single start point remains an insertion point.
    if start_explicit and not end_explicit and start is not None:
        return PastePlacement("start", start)

    if start is not None and end is not None and end > start + _EPSILON:
        return PastePlacement("range", start, end)
    if start is not None and end is None:
        return PastePlacement("start", start)
    return PastePlacement("append", duration)


def _safe_duration(path: str) -> float:
    try:
        duration = float(get_media_duration(path))
    except Exception as error:
        raise RuntimeError(message("invalid_duration")) from error
    if duration <= _EPSILON:
        raise RuntimeError(message("invalid_duration"))
    return duration


def repeated_segments(path: str, source_duration: float, target_duration: Optional[float] = None) -> list[TimelineSegment]:
    source_duration = max(0.0, float(source_duration))
    if source_duration <= _EPSILON:
        raise RuntimeError(message("invalid_duration"))
    file_id = new_logical_file_id()
    file_name = display_file_name(path)
    if target_duration is None:
        return [TimelineSegment(path, 0.0, source_duration, source_file_id=file_id, source_file_name=file_name)]
    remaining = max(0.0, float(target_duration))
    result: list[TimelineSegment] = []
    while remaining > _EPSILON:
        part = min(source_duration, remaining)
        result.append(TimelineSegment(path, 0.0, part, source_file_id=file_id, source_file_name=file_name))
        remaining -= part
    return result


def _run_ffmpeg(
    command: list[str],
    error_key: str,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    return_code, stdout, stderr = _run_process_capture(command, cancel_event, timeout=7200.0)
    output_path = str(command[-1]) if command else ""
    if return_code != 0 or not output_path or not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        details = stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"{message(error_key)}: {details}" if details else message(error_key))


def _temporary_output(source_path: str, prefix: str, suffix: str) -> tuple[str, str]:
    temp_dir = tempfile.mkdtemp(prefix=prefix)
    stem = os.path.splitext(os.path.basename(source_path))[0].strip() or "clipboard_media"
    return temp_dir, os.path.join(temp_dir, f"{stem}{suffix}")


def create_image_video_proxy(image_path: str, duration: float, cancel_event: Optional[threading.Event] = None) -> tuple[str, str]:
    temp_dir, output_path = _temporary_output(image_path, "clipboard_image_", "_image.mp4")
    command = [
        _ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-i",
        image_path,
        "-t",
        f"{max(0.05, float(duration)):.6f}",
        "-r",
        "24",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        _run_ffmpeg(command, "failed_image", cancel_event)
        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def create_silent_audio_proxy(source_path: str, duration: float, cancel_event: Optional[threading.Event] = None) -> tuple[str, str]:
    temp_dir, output_path = _temporary_output(source_path, "clipboard_silence_", "_silence.flac")
    command = [
        _ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t",
        f"{max(0.05, float(duration)):.6f}",
        "-c:a",
        "flac",
        "-compression_level",
        "0",
        output_path,
    ]
    try:
        _run_ffmpeg(command, "failed_audio", cancel_event)
        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def create_black_video_proxy(audio_path: str, duration: float, cancel_event: Optional[threading.Event] = None) -> tuple[str, str]:
    temp_dir, output_path = _temporary_output(audio_path, "clipboard_audio_video_", "_audio.mp4")
    command = [
        _ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=1280x720:r=1",
        "-t",
        f"{max(0.05, float(duration)):.6f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        _run_ffmpeg(command, "failed_audio", cancel_event)
        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _register_generated_file(player, temp_dir: str, path: str) -> None:
    player.generated_temp_dirs.append(temp_dir)
    player.generated_temp_files.append(path)


def _success_key(kind: str, placement: PastePlacement) -> str:
    suffix = "append" if placement.mode == "append" else "start" if placement.mode == "start" else "range"
    return f"{kind}_{suffix}"


def _point_label(kind: str) -> str:
    return message(f"{kind}_point")


def _operation(kind: str, background: bool = False) -> str:
    if background:
        return "إدراج خلفية صوتية"
    if kind == "image":
        return "إدراج صورة"
    if kind == "video":
        return "إضافة فيديو"
    return "إضافة ملف صوتي"


def _commit_common(
    player,
    before_state,
    start: float,
    end: float,
    operation: str,
    success: str,
    initial_project: Optional[tuple[str, str]] = None,
) -> None:
    # A first clipboard file creates a real project rather than trying to append
    # to a project that does not exist.  Set the identity only after every media
    # preparation step succeeded, so a failed paste leaves the empty workspace
    # completely untouched.
    if initial_project is not None:
        source_path, media_kind = initial_project
        player.video_path = source_path
        player.media_kind = media_kind
    player.current_time = max(0.0, min(start, player.timeline_duration()))
    player.start_time = None
    player.end_time = None
    clear_marker_state(player)
    player.last_insert_end = end
    player.is_dirty = True
    if initial_project is None:
        player.record_edit(operation, before_state)
    else:
        # Creating the first timeline is equivalent to opening a new source.
        # It must not create an undo state that leaves an empty timeline with a
        # stale source identity.
        try:
            player.clear_edit_history()
        except Exception:
            try:
                player.edit_history.clear()
            except Exception:
                pass
    player.refresh_menu_bar()
    player.reload_current_position()
    if initial_project is not None:
        # Persist recovery only after the new source was loaded successfully.
        try:
            player.save_crash_session_now()
        except Exception:
            pass
    if initial_project is not None:
        _source_path, media_kind = initial_project
        success = message("audio_new_project" if media_kind == "audio" else "video_new_project")
    player.say(success)


def _insert_primary_segments(
    player,
    segments: Sequence[TimelineSegment],
    kind: str,
    placement: PastePlacement,
    generated: Sequence[tuple[str, str]] = (),
    initial_project: Optional[tuple[str, str]] = None,
) -> None:
    inserted_duration = total_duration(segments)
    if inserted_duration <= _EPSILON:
        raise RuntimeError(message("invalid_duration"))
    before_state = player.capture_edit_state()
    original_project_identity = (
        str(getattr(player, "video_path", "") or ""),
        str(getattr(player, "media_kind", "none") or "none"),
    )
    try:
        if placement.mode == "range":
            assert placement.end is not None
            restore_segments = slice_segments(player.timeline, placement.start, placement.end)
            player.timeline = insert_segments(
                delete_range(player.timeline, placement.start, placement.end),
                placement.start,
                segments,
            )
            mode = "replace"
        else:
            restore_segments = None
            player.timeline = insert_segments(player.timeline, placement.start, segments)
            player.shift_timed_items_after_insert(placement.start, inserted_duration)
            mode = "insert"
        for path, temp_dir in generated:
            _register_generated_file(player, temp_dir, path)
        player.add_edit_point(
            kind,
            placement.start,
            placement.start + inserted_duration,
            "timeline",
            restore_segments=restore_segments,
            mode=mode,
            label=_point_label(kind),
        )
        _commit_common(
            player,
            before_state,
            placement.start,
            placement.start + inserted_duration,
            _operation(kind),
            message(_success_key(kind, placement)),
            initial_project=initial_project,
        )
    except Exception:
        player.apply_edit_state(before_state)
        if initial_project is not None:
            player.video_path, player.media_kind = original_project_identity
        for path, temp_dir in generated:
            try:
                player.generated_temp_files.remove(path)
            except (AttributeError, ValueError):
                pass
            try:
                player.generated_temp_dirs.remove(temp_dir)
            except (AttributeError, ValueError):
                pass
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _insert_audio_project_visual(player, path: str, kind: str, placement: PastePlacement, media_duration: float) -> None:
    before_state = player.capture_edit_state()
    generated: list[tuple[str, str]] = []
    try:
        if placement.mode == "range":
            assert placement.end is not None
            start = placement.start
            end = placement.end
            point_target = "visual"
            point_mode = ""
        else:
            duration = media_duration if kind == "video" else max(0.05, float(player.default_image_duration))
            silence_path, temp_dir = create_silent_audio_proxy(path, duration)
            generated.append((silence_path, temp_dir))
            # Keep the copied media path on the timeline segment so Tab and
            # Shift+Tab announce the real copied file name.  The audio_path is
            # a silent proxy used by the audio project for playback and export.
            segment = new_file_segment(path, 0.0, duration, audio_path=silence_path, audio_start=0.0)
            player.timeline = insert_segments(player.timeline, placement.start, [segment])
            player.shift_timed_items_after_insert(placement.start, duration)
            start = placement.start
            end = start + duration
            point_target = "timeline"
            point_mode = "insert"
        item_id = uuid.uuid4().hex
        player.visual_items.append(
            {
                "id": item_id,
                "type": kind,
                "path": path,
                "start": start,
                "end": end,
                "transition": player.transition_name,
            }
        )
        for generated_path, temp_dir in generated:
            _register_generated_file(player, temp_dir, generated_path)
        player.add_edit_point(
            kind,
            start,
            end,
            point_target,
            item_id=item_id if point_target == "visual" else "",
            mode=point_mode,
            label=_point_label(kind),
        )
        _commit_common(
            player,
            before_state,
            start,
            end,
            _operation(kind),
            message(_success_key(kind, placement)),
        )
    except Exception:
        player.apply_edit_state(before_state)
        for generated_path, temp_dir in generated:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _background_audio_placement(
    player,
    placement: PastePlacement,
    source_duration: float,
) -> Optional[PastePlacement]:
    """Resolve the exact background-audio interval requested by the user."""
    if placement.mode == "append":
        return None
    if placement.mode == "range":
        return placement
    timeline_duration = max(0.0, float(player.timeline_duration()))
    end = min(timeline_duration, placement.start + max(0.0, float(source_duration)))
    if end <= placement.start + _EPSILON:
        return PastePlacement("range", placement.start, placement.start)
    return PastePlacement("range", placement.start, end)


def _show_background_audio_options(
    player,
    path: str,
    placement: Optional[PastePlacement] = None,
) -> Optional[dict]:
    """Open the program's existing background-audio dialog with *path* selected.

    The copied file is transient until the user presses Add.  Cancelling the
    dialog therefore does not silently alter the saved background-audio
    library.
    """
    import wx

    from video_maker.background_audio import BackgroundAudioDialog, DEFAULT_BACKGROUND_VOLUME

    original_selection = (
        getattr(player, "start_time", None),
        getattr(player, "end_time", None),
        bool(getattr(player, "_clipboard_paste_start_explicit", False)),
        bool(getattr(player, "_clipboard_paste_end_explicit", False)),
    )
    if placement is not None and placement.end is not None:
        # The existing dialog previews the parent's selected range.  A single
        # start point means the copied audio's natural duration, not the
        # program's automatic start-to-end-of-project selection.
        player.start_time = placement.start
        player.end_time = placement.end
    dialog = BackgroundAudioDialog(player)
    try:
        absolute = os.path.abspath(path)
        target = os.path.normcase(absolute)
        selected_index = None
        for index, item in enumerate(dialog.items):
            if os.path.normcase(os.path.abspath(str(item.get("path", "")))) == target:
                selected_index = index
                break
        if selected_index is None:
            transient = {
                "path": absolute,
                "name": os.path.splitext(os.path.basename(absolute))[0],
                "volume": DEFAULT_BACKGROUND_VOLUME,
                "last_used": 0.0,
            }
            dialog.items.append(transient)
            selected_index = len(dialog.items) - 1
            dialog.list_box.Append(f"{selected_index + 1} - {transient['name']}")
        dialog.list_box.SetSelection(selected_index)
        dialog.apply_selected_volume()
        # Keep the normal list-first keyboard flow.  NVDA announces the copied
        # file, then Tab reaches volume, trim-silence, preview, and Add.
        wx.CallAfter(dialog.list_box.SetFocus)
        if dialog.ShowModal() != wx.ID_OK:
            return None
        return dict(dialog.selection_options or {})
    finally:
        player.start_time, player.end_time = original_selection[:2]
        player._clipboard_paste_start_explicit = original_selection[2]
        player._clipboard_paste_end_explicit = original_selection[3]
        dialog.Destroy()


def _insert_background_audio_with_options(
    player,
    options: dict,
    placement: PastePlacement,
    prepared_trim: Optional[tuple[str, str]] = None,
) -> None:
    """Insert background audio using the unchanged options from its dialog."""
    assert placement.end is not None
    from video_maker.background_audio import trim_background_audio_silence

    before_state = player.capture_edit_state()
    path = str(options.get("path", "") or "")
    source_temp_dir = str(options.get("source_temp_dir", "") or "")
    source_temp_registered = False
    temp_dir = ""
    try:
        if options.get("trim_silence"):
            if prepared_trim:
                path, temp_dir = prepared_trim
            else:
                path, temp_dir = trim_background_audio_silence(path)
        elif source_temp_dir:
            _register_generated_file(player, source_temp_dir, path)
            source_temp_registered = True
        if temp_dir:
            _register_generated_file(player, temp_dir, path)
        if source_temp_dir and not source_temp_registered:
            shutil.rmtree(source_temp_dir, ignore_errors=True)
            source_temp_dir = ""
        item_id = uuid.uuid4().hex
        player.background_audio_items.append(
            {
                "id": item_id,
                "type": "background_audio",
                "path": path,
                "original_path": str(options.get("path", "") or path),
                "name": options.get("name") or os.path.splitext(os.path.basename(path))[0],
                "start": placement.start,
                "end": placement.end,
                "volume": float(options.get("volume", 0.4) or 0.0),
                "trim_silence": bool(options.get("trim_silence")),
                "speed": 1.0,
                "source_offset": 0.0,
            }
        )
        player.add_edit_point(
            "background_audio",
            placement.start,
            placement.end,
            "background_audio",
            item_id=item_id,
            label=_point_label("audio"),
        )
        _commit_common(
            player,
            before_state,
            placement.start,
            placement.end,
            _operation("audio", background=True),
            message("background_audio_inserted"),
        )
    except Exception:
        player.apply_edit_state(before_state)
        if temp_dir:
            try:
                player.generated_temp_files.remove(path)
            except (AttributeError, ValueError):
                pass
            try:
                player.generated_temp_dirs.remove(temp_dir)
            except (AttributeError, ValueError):
                pass
            shutil.rmtree(temp_dir, ignore_errors=True)
        if source_temp_registered:
            try:
                player.generated_temp_files.remove(str(options.get("path", "") or ""))
            except (AttributeError, ValueError):
                pass
            try:
                player.generated_temp_dirs.remove(source_temp_dir)
            except (AttributeError, ValueError):
                pass
            shutil.rmtree(source_temp_dir, ignore_errors=True)
        elif source_temp_dir:
            shutil.rmtree(source_temp_dir, ignore_errors=True)
        raise



def _commit_background_audio_safely(player, options: dict, placement: PastePlacement) -> None:
    """Prepare optional silence trimming off-thread, then commit atomically."""
    if not options.get("trim_silence"):
        _insert_background_audio_with_options(player, options, placement)
        return

    source_path = str(options.get("path", "") or "")

    def worker(progress, cancel_event):
        from video_maker.background_audio import trim_background_audio_silence
        progress(10)
        if cancel_event.is_set():
            raise PastePreparationCancelled(message("paste_cancelled"))
        # The existing trimmer is invoked on a worker thread. Its output is not
        # committed until it returns successfully, so cancellation/failure
        # leaves the current timeline untouched.
        trimmed_path, temp_dir = trim_background_audio_silence(source_path)
        if cancel_event.is_set():
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise PastePreparationCancelled(message("paste_cancelled"))
        progress(100)
        return trimmed_path, temp_dir

    status, value, error = _run_preparation_dialog(player, worker)
    if status == "cancelled":
        player.say(message("paste_cancelled"))
        return
    if status != "success":
        raise RuntimeError(str(error or message("background_audio_failed")))
    _insert_background_audio_with_options(player, options, placement, prepared_trim=value)


def _render_timeline_audio_clipboard(
    segments: Sequence[TimelineSegment],
    progress,
    cancel_event: threading.Event,
) -> tuple[str, str]:
    temp_dir = tempfile.mkdtemp(prefix="clipboard_timeline_audio_")
    output_path = os.path.join(temp_dir, "copied_segment.flac")

    def cancelled():
        return cancel_event.is_set()

    try:
        write_timeline_audio(
            list(segments),
            output_path,
            progress_callback=progress,
            cancelled_callback=cancelled,
        )
        if cancel_event.is_set():
            raise PastePreparationCancelled(message("paste_cancelled"))
        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def paste_timeline_audio_clipboard_as_background(
    player,
    segments: Sequence[TimelineSegment],
    source_kind: str,
    placement: Optional[PastePlacement] = None,
) -> bool:
    """Paste an internal audio timeline selection through file-paste rules."""
    if str(source_kind or "").lower() != "audio":
        return False
    if not segments or not getattr(player, "timeline", []):
        return False
    placement = placement or resolve_placement(player)
    media_duration = total_duration(segments)
    background_placement = _background_audio_placement(player, placement, media_duration)
    if background_placement is None:
        return False
    if background_placement.end is None or background_placement.end <= background_placement.start + _EPSILON:
        player.say(message("background_audio_no_room"))
        return True
    if not begin_paste_operation(player):
        return True
    rendered_path = ""
    rendered_temp_dir = ""
    source_temp_dir_for_commit = ""
    try:
        status, value, error = _run_preparation_dialog(
            player,
            lambda progress, cancel_event: _render_timeline_audio_clipboard(segments, progress, cancel_event),
        )
        if status == "cancelled":
            player.say(message("paste_cancelled"))
            return True
        if status != "success":
            player.say(f"{message('background_audio_failed')}: {error or message('invalid_duration')}")
            return True
        rendered_path, rendered_temp_dir = value
        options = _show_background_audio_options(player, rendered_path, background_placement)
        if not options:
            shutil.rmtree(rendered_temp_dir, ignore_errors=True)
            return True
        options["source_temp_dir"] = rendered_temp_dir
        source_temp_dir_for_commit = rendered_temp_dir
        rendered_temp_dir = ""
        _commit_background_audio_safely(player, options, background_placement)
        source_temp_dir_for_commit = ""
        return True
    except Exception as error:
        if source_temp_dir_for_commit:
            shutil.rmtree(source_temp_dir_for_commit, ignore_errors=True)
        player.say(f"{message('background_audio_failed')}: {error}")
        return True
    finally:
        if rendered_temp_dir:
            shutil.rmtree(rendered_temp_dir, ignore_errors=True)
        end_paste_operation(player)

def _is_full_timeline_range(player, placement: PastePlacement) -> bool:
    if placement.mode != "range" or placement.end is None:
        return False
    duration = max(0.0, float(player.timeline_duration()))
    return abs(placement.start) <= 0.03 and abs(placement.end - duration) <= 0.03


def _confirm_video_range_expansion(player) -> bool:
    import wx

    result = wx.MessageBox(
        message("video_expand_question"),
        message("video_expand_title"),
        wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
    )
    return result == wx.YES


def _audio_project_video_placement(
    player,
    placement: PastePlacement,
    video_duration: float,
) -> Optional[PastePlacement]:
    """Resolve a pasted video's visual interval over an audio project."""
    timeline_duration = max(0.0, float(player.timeline_duration()))
    if placement.mode == "append":
        return placement
    if placement.mode == "start":
        end = min(timeline_duration, placement.start + video_duration)
        if end <= placement.start + _EPSILON:
            player.say(message("video_no_room"))
            return None
        return PastePlacement("range", placement.start, end)
    assert placement.end is not None
    if _is_full_timeline_range(player, placement):
        # Full-timeline placement always fits the audio: short video loops and
        # long video is cropped by the existing visual renderer.
        return placement
    if video_duration > placement.duration + _EPSILON:
        if not _confirm_video_range_expansion(player):
            return None
        expanded_end = min(timeline_duration, placement.start + video_duration)
        if expanded_end <= placement.start + _EPSILON:
            player.say(message("video_no_room"))
            return None
        return PastePlacement("range", placement.start, expanded_end)
    return placement


def _insert_video_into_audio_project(
    player,
    path: str,
    placement: PastePlacement,
    media_duration: float,
    prepared_silence: Optional[tuple[str, str]] = None,
    source_has_audio: Optional[bool] = None,
) -> None:
    """Paste video into an audio project while keeping its audio rules clear."""
    if placement.mode != "append":
        _insert_audio_project_visual(player, path, "video", placement, media_duration)
        return

    # With no selection the user requested a new timeline file.  Use the
    # video's own audio when present; a silent proxy is needed only for a
    # genuinely silent video.  The matching visual item makes it export and
    # navigate as one new file after the existing audio.
    generated: list[tuple[str, str]] = []
    has_source_audio = has_audio_stream(path) if source_has_audio is None else bool(source_has_audio)
    if has_source_audio:
        segment = new_file_segment(path, 0.0, media_duration)
    else:
        if prepared_silence:
            silence_path, temp_dir = prepared_silence
        else:
            silence_path, temp_dir = create_silent_audio_proxy(path, media_duration)
        generated.append((silence_path, temp_dir))
        segment = new_file_segment(path, 0.0, media_duration, audio_path=silence_path, audio_start=0.0)
    before_state = player.capture_edit_state()
    try:
        player.timeline = insert_segments(player.timeline, placement.start, [segment])
        player.shift_timed_items_after_insert(placement.start, media_duration)
        item_id = uuid.uuid4().hex
        player.visual_items.append(
            {
                "id": item_id,
                "type": "video",
                "path": path,
                "start": placement.start,
                "end": placement.start + media_duration,
                "transition": player.transition_name,
            }
        )
        for generated_path, temp_dir in generated:
            _register_generated_file(player, temp_dir, generated_path)
        player.add_edit_point(
            "video",
            placement.start,
            placement.start + media_duration,
            "timeline",
            mode="insert",
            label=_point_label("video"),
        )
        _commit_common(
            player,
            before_state,
            placement.start,
            placement.start + media_duration,
            _operation("video"),
            message("video_append"),
        )
    except Exception:
        player.apply_edit_state(before_state)
        for generated_path, temp_dir in generated:
            try:
                player.generated_temp_files.remove(generated_path)
            except (AttributeError, ValueError):
                pass
            try:
                player.generated_temp_dirs.remove(temp_dir)
            except (AttributeError, ValueError):
                pass
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise



def _paste_image_over_video(player, path: str, placement: PastePlacement) -> None:
    """Open the existing image options for a copied image, then overlay it.

    The dialog and transform path are exactly the same ones used by the current
    Insert Image command.  Only the image path is pre-filled so clipboard paste
    never bypasses the user's size and position choices.
    """
    import wx

    from video_maker.image_overlay import ImageOverlayDialog
    from video_maker.localization import tr

    if placement.end is None or placement.end <= placement.start + _EPSILON:
        raise RuntimeError(message("image_requires_end"))

    dialog = ImageOverlayDialog(
        player,
        title=tr("إدراج صورة"),
        apply_label=tr("إدراج"),
        apply_name=tr("إدراج الصورة"),
    )
    try:
        dialog.image_path = path
        dialog.image_text.SetValue(path)
        dialog.image_text.SetName(f"{tr('الصورة')} {os.path.basename(path)}")
        # The existing dialog normally focuses the Browse button.  A pasted
        # image is already selected, so move directly to the first unchanged
        # option instead of making a blind user tab past an unnecessary field.
        try:
            wx.CallAfter(dialog.mode_choice.SetFocus)
        except Exception:
            pass
        if dialog.ShowModal() != wx.ID_OK:
            return
        options = dialog.options
    finally:
        dialog.Destroy()

    if not options:
        return

    start_time = placement.start
    end_time = placement.end
    timeline_snapshot = list(player.timeline)
    player.start_timeline_transform(
        "image",
        tr("جاري إدراج الصورة"),
        tr("نسبة إدراج الصورة {percent} بالمئة"),
        tr("حالة إدراج الصورة"),
        tr("شريط تقدم إدراج الصورة"),
        tr("إلغاء إدراج الصورة"),
        tr("جاري إلغاء إدراج الصورة"),
        lambda progress, cancelled: player.build_image_overlay_transform(
            timeline_snapshot,
            start_time,
            end_time,
            options,
            progress,
            cancelled,
        ),
        (start_time, end_time),
        "إدراج صورة",
        tr("تم إدراج الصورة"),
        scale_timed_items=False,
        preserve_continuous_audio=True,
    )



def _discard_prepared_proxy(prepared: Optional[PreparedMedia]) -> None:
    if prepared and prepared.proxy_temp_dir:
        shutil.rmtree(prepared.proxy_temp_dir, ignore_errors=True)


def _paste_prepared_media(
    player,
    path: str,
    placement: PastePlacement,
    prepared: PreparedMedia,
) -> bool:
    """Commit already inspected/prepared media on the GUI thread."""
    kind = prepared.kind
    empty_workspace = not bool(getattr(player, "timeline", []))
    if kind == "image":
        if empty_workspace:
            player.say(message("image_requires_media"))
            return True
        if placement.mode != "range" or placement.end is None or placement.end <= placement.start + _EPSILON:
            player.say(message("image_requires_end"))
            return True

    effective_media_kind = (
        "audio" if empty_workspace and kind == "audio"
        else "video" if empty_workspace
        else str(getattr(player, "media_kind", "video") or "video")
    )
    initial_project = (path, effective_media_kind) if empty_workspace else None
    proxy_consumed = False
    try:
        if kind == "image":
            if effective_media_kind == "audio":
                _insert_audio_project_visual(
                    player, path, "image", placement, float(player.default_image_duration)
                )
            else:
                _paste_image_over_video(player, path, placement)
            return True

        media_duration = prepared.duration
        if kind == "video":
            if effective_media_kind == "audio":
                resolved = _audio_project_video_placement(player, placement, media_duration)
                if resolved is None:
                    return True
                prepared_silence = None
                if prepared.proxy_path and prepared.proxy_temp_dir:
                    prepared_silence = (prepared.proxy_path, prepared.proxy_temp_dir)
                    proxy_consumed = resolved.mode == "append"
                _insert_video_into_audio_project(
                    player,
                    path,
                    resolved,
                    media_duration,
                    prepared_silence=prepared_silence,
                    source_has_audio=prepared.has_audio,
                )
            else:
                target = placement.duration if placement.mode == "range" else None
                segments = repeated_segments(path, media_duration, target)
                _insert_primary_segments(
                    player, segments, "video", placement, initial_project=initial_project
                )
            return True

        background_placement = None
        if not empty_workspace:
            background_placement = _background_audio_placement(player, placement, media_duration)
        if background_placement is not None:
            if (
                background_placement.end is None
                or background_placement.end <= background_placement.start + _EPSILON
            ):
                player.say(message("background_audio_no_room"))
                return True
            options = _show_background_audio_options(player, path, background_placement)
            if not options:
                return True
            _commit_background_audio_safely(player, options, background_placement)
        elif effective_media_kind == "audio":
            target = placement.duration if placement.mode == "range" else None
            segments = repeated_segments(path, media_duration, target)
            _insert_primary_segments(
                player, segments, "audio", placement, initial_project=initial_project
            )
        else:
            if not prepared.proxy_path or not prepared.proxy_temp_dir:
                raise RuntimeError(message("failed_audio"))
            segment = new_file_segment(
                prepared.proxy_path,
                0.0,
                media_duration,
                audio_path=path,
                audio_start=0.0,
                source_file_name=display_file_name(path),
            )
            proxy_consumed = True
            _insert_primary_segments(
                player,
                [segment],
                "audio",
                placement,
                [(prepared.proxy_path, prepared.proxy_temp_dir)],
                initial_project=initial_project,
            )
        return True
    finally:
        if not proxy_consumed:
            _discard_prepared_proxy(prepared)


def request_media_paste(player, path: str) -> bool:
    """Safely inspect and prepare one media file before touching the timeline."""
    path = os.path.abspath(str(path))
    if not os.path.isfile(path):
        player.say(message("missing"))
        return True
    if not begin_paste_operation(player):
        return True
    try:
        try:
            initial_stat = os.stat(path)
            initial_identity = (int(initial_stat.st_size), int(initial_stat.st_mtime_ns))
        except OSError:
            player.say(message("missing"))
            return True
        placement = resolve_placement(player)
        try:
            prepared = _prepare_media_for_paste(player, path, placement)
        except Exception as error:
            player.say(str(error))
            return True
        if prepared is None:
            return True
        try:
            final_stat = os.stat(path)
            final_identity = (int(final_stat.st_size), int(final_stat.st_mtime_ns))
        except OSError:
            _discard_prepared_proxy(prepared)
            player.say(message("missing"))
            return True
        if final_identity != initial_identity:
            _discard_prepared_proxy(prepared)
            player.say(message("source_changed"))
            return True
        try:
            return _paste_prepared_media(player, path, placement, prepared)
        except Exception as error:
            _discard_prepared_proxy(prepared)
            failure_key = f"failed_{prepared.kind}" if prepared.kind in SUPPORTED_KINDS else "unsupported"
            player.say(f"{message(failure_key)}: {error}")
            return True
    finally:
        end_paste_operation(player)

def paste_media_path(player, path: str, placement: Optional[PastePlacement] = None) -> bool:
    """Paste one validated media path.  Exposed separately for deterministic tests."""
    path = os.path.abspath(str(path))
    if not os.path.isfile(path):
        player.say(message("missing"))
        return True
    kind = classify_media_file(path)
    if kind not in SUPPORTED_KINDS:
        player.say(message("unsupported"))
        return True

    empty_workspace = not bool(getattr(player, "timeline", []))
    placement = placement or resolve_placement(player)

    # An image is a timed visual layer, not a standalone source project.  It
    # therefore requires an existing audio or video timeline and a real end
    # point.  This prevents an accidental image-only workspace and guarantees
    # that its visible duration is always explicit.
    if kind == "image":
        if empty_workspace:
            player.say(message("image_requires_media"))
            return True
        if placement.mode != "range" or placement.end is None or placement.end <= placement.start + _EPSILON:
            player.say(message("image_requires_end"))
            return True

    effective_media_kind = (
        "audio" if empty_workspace and kind == "audio"
        else "video" if empty_workspace
        else str(getattr(player, "media_kind", "video") or "video")
    )
    initial_project = (path, effective_media_kind) if empty_workspace else None

    try:
        if kind == "image":
            if effective_media_kind == "audio":
                # Audio projects have no underlying video frame to position an
                # overlay on.  The copied image is therefore a full-screen
                # visual item for the exact selected interval.
                _insert_audio_project_visual(player, path, "image", placement, float(player.default_image_duration))
            else:
                # Video projects must keep the existing image size/position
                # options.  Paste only pre-selects the copied file.
                _paste_image_over_video(player, path, placement)
            return True

        media_duration = _safe_duration(path)
        if kind == "video":
            if effective_media_kind == "audio":
                resolved_video_placement = _audio_project_video_placement(
                    player,
                    placement,
                    media_duration,
                )
                if resolved_video_placement is None:
                    return True
                _insert_video_into_audio_project(
                    player,
                    path,
                    resolved_video_placement,
                    media_duration,
                )
            else:
                target = placement.duration if placement.mode == "range" else None
                segments = repeated_segments(path, media_duration, target)
                _insert_primary_segments(
                    player, segments, "video", placement,
                    initial_project=initial_project,
                )
            return True

        # With any explicit placement, copied audio is background audio over
        # the existing project and uses the program's full, unchanged options
        # dialog.  With no selection it remains a normal new timeline file at
        # the end, regardless of whether the current project is audio or video.
        background_placement = None
        if not empty_workspace:
            background_placement = _background_audio_placement(player, placement, media_duration)
        if background_placement is not None:
            if background_placement.end is None or background_placement.end <= background_placement.start + _EPSILON:
                player.say(message("background_audio_no_room"))
                return True
            try:
                options = _show_background_audio_options(player, path, background_placement)
                if not options:
                    return True
                _insert_background_audio_with_options(player, options, background_placement)
            except Exception as error:
                player.say(f"{message('background_audio_failed')}: {error}")
                return True
        elif effective_media_kind == "audio":
            target = placement.duration if placement.mode == "range" else None
            segments = repeated_segments(path, media_duration, target)
            _insert_primary_segments(
                player, segments, "audio", placement,
                initial_project=initial_project,
            )
        else:
            player.say(message("preparing_audio"))
            proxy_path, temp_dir = create_black_video_proxy(path, media_duration)
            segment = new_file_segment(
                proxy_path,
                0.0,
                media_duration,
                audio_path=path,
                audio_start=0.0,
                source_file_name=display_file_name(path),
            )
            _insert_primary_segments(
                player, [segment], "audio", placement, [(proxy_path, temp_dir)],
                initial_project=initial_project,
            )
        return True
    except Exception as error:
        failure_key = f"failed_{kind}"
        player.say(f"{message(failure_key)}: {error}")
        return True


def paste_file_path(player, path: str) -> bool:
    """Paste a supported media file or restore a copied .elbheri project."""
    path = os.path.abspath(str(path))
    if path.lower().endswith(PROJECT_EXTENSION):
        if not begin_paste_operation(player):
            return True
        try:
            restore = getattr(player, "restore_project_from_path", None)
            if not callable(restore):
                player.say(message("unsupported"))
                return True
            restore(path, confirm_unsaved=True)
            return True
        finally:
            end_paste_operation(player)
    return request_media_paste(player, path)


def paste_media_from_clipboard(player) -> bool:
    """Route Ctrl+V to exactly one authoritative clipboard source.

    False means the caller should paste the application's internal timeline
    segments. True means this function handled the request, including clear
    errors for file/other clipboard content.
    """
    sequence = clipboard_sequence_number()
    try:
        paths = read_file_clipboard()
    except Exception:
        # Never fall through to an old timeline segment when native clipboard
        # content may have changed but is temporarily unavailable.
        if _active_clipboard_source == "internal" and (
            sequence is None or sequence == _last_internal_copy_sequence
        ):
            return False
        player.say(message("clipboard_read_failed"))
        return True

    source = _select_clipboard_source(player, paths, sequence)
    if source == "internal":
        return False
    if source == "other":
        player.say(message("clipboard_empty"))
        return True
    if len(paths) != 1:
        player.say(message("copy_one"))
        return True
    return paste_file_path(player, paths[0])
