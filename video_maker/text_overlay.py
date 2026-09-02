import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass

import wx
from video_maker.localization import tr
from video_maker.app_paths import ffmpeg_binary
from PIL import Image, ImageDraw, ImageFont

from video_maker.app_paths import bundled_path, unique_path, user_data_path
from video_maker.app_state import get_text_overlay_last_settings, set_text_overlay_last_settings
from video_maker.audio_effects import AudioEffectPreparationCancelled
from video_maker.image_overlay import ImageOverlayOptions, _cancelled, _terminate_process, apply_image_overlay, replace_image_overlay_range
from video_maker.localization import current_language
from video_maker.operation_control import raise_if_cancelled
from video_maker.timeline import slice_segments
from video_maker.video_editing import ffmpeg_startupinfo, has_audio_stream, media_info_text, write_timeline_video


POSITIONS = [
    ("right_top", "في اليمين أعلى", "مناسب للتعليق القصير عندما تكون التفاصيل المهمة بعيدة عن أعلى يمين الصورة"),
    ("left_top", "في اليسار أعلى", "مناسب للشعار أو الملاحظة القصيرة عندما يكون يمين الصورة مزدحما"),
    ("center_top", "في المنتصف أعلى", "مناسب للعنوان المختصر أعلى الفيديو"),
    ("right_bottom", "في اليمين أسفل", "مناسب للتعليق السفلي مع فيديوهات عربية واتجاه قراءة من اليمين"),
    ("center_bottom", "في المنتصف أسفل", "مناسب للترجمة أو الجملة القصيرة أسفل الشاشة"),
    ("left_bottom", "في اليسار أسفل", "مناسب لمعلومة جانبية بعيدة عن اليمين"),
    ("center", "في وسط الشاشة", "مناسب للتنبيه أو العنوان الرئيسي في منتصف المشهد"),
]


COLORS = [
    ("white", "أبيض", (255, 255, 255, 255), "واضح جدا على الخلفيات الداكنة"),
    ("black", "أسود", (0, 0, 0, 255), "واضح على الخلفيات الفاتحة"),
    ("yellow", "أصفر", (255, 230, 0, 255), "ملفت للتنبيه والعناوين القصيرة"),
    ("gold", "ذهبي", (255, 196, 0, 255), "مناسب للعناوين الرسمية الهادئة"),
    ("orange", "برتقالي", (255, 140, 40, 255), "مناسب للتأكيد والتنبيه بدون حدة عالية"),
    ("red", "أحمر", (255, 60, 60, 255), "مناسب للتحذير أو التنبيه المهم"),
    ("green", "أخضر", (70, 220, 120, 255), "مناسب للنجاح أو الرسائل الإيجابية"),
    ("cyan", "سماوي", (60, 220, 255, 255), "واضح على الخلفيات الداكنة ومناسب للمعلومات"),
    ("blue", "أزرق", (80, 150, 255, 255), "مناسب للعناوين الهادئة والمعلومات"),
    ("navy", "أزرق داكن", (30, 70, 150, 255), "مناسب على خلفية فاتحة"),
    ("purple", "بنفسجي", (170, 110, 255, 255), "مناسب للعناوين الإبداعية المعتدلة"),
    ("gray", "رمادي", (180, 180, 180, 255), "مناسب للنص الثانوي على الخلفيات الداكنة"),
]


BACKGROUNDS = [
    ("none", "بدون خلفية", "يعرض النص فقط ويحتاج تباينا واضحا مع الفيديو"),
    ("black", "خلفية سوداء شفافة", "مناسبة جدا للنص الفاتح فوق الفيديوهات المتغيرة"),
    ("white", "خلفية بيضاء شفافة", "مناسب للنص الداكن فوق الفيديوهات الداكنة"),
    ("blue", "خلفية زرقاء شفافة", "مناسبة للمعلومات والعناوين الهادئة"),
    ("green", "خلفية خضراء شفافة", "مناسبة للرسائل الإيجابية أو التعليمية"),
    ("gold", "خلفية ذهبية شفافة", "مناسبة للعناوين الرسمية القصيرة"),
]

BACKGROUND_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "blue": (20, 70, 150),
    "green": (20, 120, 80),
    "gold": (185, 130, 0),
}


class QuietStaticTextAccessible(wx.Accessible):
    def __init__(self, window):
        super().__init__(window)

    def GetName(self, childId):
        return wx.ACC_OK, ""

    def GetDescription(self, childId):
        return wx.ACC_OK, ""

    def GetRole(self, childId):
        return wx.ACC_OK, wx.ROLE_SYSTEM_WHITESPACE

    def GetState(self, childId):
        return wx.ACC_OK, wx.ACC_STATE_SYSTEM_INVISIBLE

    def GetChildCount(self):
        return wx.ACC_OK, 0


def make_visual_label(parent, label_text):
    label = wx.StaticText(parent, label=label_text)
    if hasattr(label, "DisableFocusFromKeyboard"):
        label.DisableFocusFromKeyboard()
    if hasattr(label, "SetCanFocus"):
        label.SetCanFocus(False)
    if hasattr(label, "SetAccessible"):
        accessible = QuietStaticTextAccessible(label)
        label.SetAccessible(accessible)
        label._quiet_accessible = accessible
    return label


FONT_DESCRIPTIONS = {
    "Noto Naskh Arabic": "خط نسخي واضح مناسب للآيات والأحاديث والنص العربي الرسمي الطويل",
    "Noto Sans Arabic": "خط حديث واضح مناسب للشروحات والعناوين البسيطة",
    "Amiri": "خط عربي كلاسيكي مناسب للنصوص الرسمية والدينية والقراءة الهادئة",
    "Cairo": "خط عصري مناسب للعناوين والفيديوهات التعليمية",
    "Arial": "خط عملي منتشر مناسب للنصوص العامة",
    "Arial Bold": "خط عريض مناسب للعناوين والتنبيه",
    "Tahoma": "خط واضح على الشاشات مناسب للنصوص القصيرة",
    "Tahoma Bold": "خط واضح عريض مناسب للعناوين القصيرة",
    "Times New Roman": "خط رسمي مناسب للنصوص التقليدية",
    "Times New Roman Bold": "خط رسمي عريض مناسب للعناوين",
    "Traditional Arabic": "خط عربي تقليدي مناسب للنصوص الدينية والتراثية",
    "Traditional Arabic Bold": "خط عربي تقليدي عريض مناسب للعناوين الرسمية",
    "Simplified Arabic": "خط عربي مبسط مناسب للشرح والتعليم",
    "Simplified Arabic Bold": "خط عربي مبسط عريض مناسب للعناوين التعليمية",
    "Calibri": "خط واضح مناسب للمحتوى العام",
    "Calibri Bold": "خط واضح عريض مناسب للعناوين",
    "Segoe UI": "خط واجهات ويندوز مناسب للنصوص المختصرة",
    "Segoe UI Bold": "خط واجهات عريض مناسب للعناوين القصيرة",
}

FONT_DESCRIPTION_TRANSLATIONS = {
    "en": {
        "Noto Naskh Arabic": "Clear Naskh-style Arabic font suitable for Quranic verses, hadiths, and long formal Arabic text",
        "Noto Sans Arabic": "Modern clear Arabic font suitable for explanations and simple titles",
        "Amiri": "Classic Arabic font suitable for formal and religious text and calm reading",
        "Cairo": "Modern font suitable for titles and educational videos",
        "Arial": "Common practical font suitable for general text",
        "Arial Bold": "Bold font suitable for titles and alerts",
        "Tahoma": "Clear screen font suitable for short text",
        "Tahoma Bold": "Clear bold screen font suitable for short titles",
        "Times New Roman": "Formal font suitable for traditional text",
        "Times New Roman Bold": "Formal bold font suitable for titles",
        "Traditional Arabic": "Traditional Arabic font suitable for religious and heritage text",
        "Traditional Arabic Bold": "Bold traditional Arabic font suitable for formal titles",
        "Simplified Arabic": "Simplified Arabic font suitable for explanations and education",
        "Simplified Arabic Bold": "Bold simplified Arabic font suitable for educational titles",
        "Calibri": "Clear font suitable for general content",
        "Calibri Bold": "Clear bold font suitable for titles",
        "Segoe UI": "Windows interface font suitable for short text",
        "Segoe UI Bold": "Bold interface font suitable for short titles",
    },
    "fr": {
        "Noto Naskh Arabic": "Police arabe claire de style naskh, adaptée aux versets, hadiths et longs textes arabes formels",
        "Noto Sans Arabic": "Police arabe moderne et claire, adaptée aux explications et aux titres simples",
        "Amiri": "Police arabe classique, adaptée aux textes formels et religieux et à une lecture calme",
        "Cairo": "Police moderne adaptée aux titres et aux vidéos éducatives",
        "Arial": "Police pratique et courante, adaptée aux textes généraux",
        "Arial Bold": "Police grasse adaptée aux titres et aux alertes",
        "Tahoma": "Police claire à l'écran, adaptée aux textes courts",
        "Tahoma Bold": "Police claire et grasse à l'écran, adaptée aux titres courts",
        "Times New Roman": "Police formelle adaptée aux textes traditionnels",
        "Times New Roman Bold": "Police formelle grasse adaptée aux titres",
        "Traditional Arabic": "Police arabe traditionnelle adaptée aux textes religieux et patrimoniaux",
        "Traditional Arabic Bold": "Police arabe traditionnelle grasse adaptée aux titres formels",
        "Simplified Arabic": "Police arabe simplifiée adaptée aux explications et à l'éducation",
        "Simplified Arabic Bold": "Police arabe simplifiée grasse adaptée aux titres éducatifs",
        "Calibri": "Police claire adaptée au contenu général",
        "Calibri Bold": "Police claire et grasse adaptée aux titres",
        "Segoe UI": "Police d'interface Windows adaptée aux textes courts",
        "Segoe UI Bold": "Police d'interface grasse adaptée aux titres courts",
    },
}


def localized_font_description(name):
    language = current_language()
    if language == "ar":
        return FONT_DESCRIPTIONS.get(name, "")
    return FONT_DESCRIPTION_TRANSLATIONS.get(language, {}).get(name, FONT_DESCRIPTIONS.get(name, ""))


@dataclass(frozen=True)
class TextOverlayOptions:
    text: str
    font_path: str
    font_name: str
    font_size: int
    color: tuple
    background: str
    background_opacity: int
    position: str
    box_width_percent: int
    mode: str = ""
    max_lines: int = 0
    typing_sound: str = ""
    typing_volume: int = 25
    typing_speed: int = 10
    mixed_text: bool = False


_TEXT_OPTION_FIELDS = (
    ("text", ""),
    ("font_path", ""),
    ("font_name", ""),
    ("font_size", 44),
    ("color", (255, 255, 255, 255)),
    ("background", ""),
    ("background_opacity", 0),
    ("position", "center_bottom"),
    ("box_width_percent", 60),
    ("mode", ""),
    ("max_lines", 0),
    ("typing_sound", ""),
    ("typing_volume", 25),
    ("typing_speed", 10),
    ("mixed_text", False),
)


def serialize_text_options(options):
    """يحول TextOverlayOptions (أو dict) إلى dict آمن بكل الحقول المعروفة."""
    result = {}
    if hasattr(options, "__dataclass_fields__"):
        for name, default in _TEXT_OPTION_FIELDS:
            result[name] = getattr(options, name, default)
    elif isinstance(options, dict):
        for name, default in _TEXT_OPTION_FIELDS:
            result[name] = options.get(name, default)
    else:
        for name, default in _TEXT_OPTION_FIELDS:
            result[name] = getattr(options, name, default)
    return result


def deserialize_text_options(data):
    """يعيد dict موحد بحقول افتراضية آمنة قابلة للإطعام لـ TextOverlayOptions(**...)."""
    if data is None:
        data = {}
    if hasattr(data, "__dataclass_fields__"):
        return serialize_text_options(data)
    result = {}
    for name, default in _TEXT_OPTION_FIELDS:
        result[name] = data.get(name, default)
    return result


def from_text_item(item):
    """يستخرج خيارات النص من عنصر نصي ديناميكي (dict) إلى TextOverlayOptions.

    يقبل None أو TextOverlayOptions ويعيده كما هو، أو dict من نوع عنصر تراك
    النصوص (is_dynamic) ويستخرج الحقول من item["options"].
    """
    if item is None:
        return None
    if isinstance(item, TextOverlayOptions):
        return item
    if isinstance(item, dict):
        data = deserialize_text_options(item.get("options"))
        return TextOverlayOptions(**data)
    return None


def typing_sounds_dirs():
    directories = []
    bundled = bundled_path("assets", "Typing sounds")
    if os.path.isdir(str(bundled)):
        directories.append(str(bundled))
    directories.append(str(user_data_path("typing_sounds")))
    return directories


def list_typing_sounds():
    found = {}
    for folder in typing_sounds_dirs():
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
                found[entry] = os.path.join(folder, entry)
    return sorted(found.items(), key=lambda item: item[0].lower())


def copy_typing_sound(source_path):
    source_path = str(source_path)
    if not os.path.exists(source_path):
        return ""
    destination = unique_path(user_data_path("typing_sounds"), os.path.basename(source_path))
    shutil.copy2(source_path, str(destination))
    return str(destination)


def _remove_preview_wav_later(path):
    try:
        timer = threading.Timer(3.0, _remove_preview_wav, args=(path,))
        timer.daemon = True
        timer.start()
    except Exception:
        pass


def _remove_preview_wav(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _decode_typing_preview_wav(sound_path, volume):
    """تحويل بداية الصوت إلى ملف WAV قصير بمستوى الصوت المطلوب."""
    try:
        ffmpeg = ffmpeg_binary()
        if not ffmpeg or not os.path.exists(str(ffmpeg)):
            return ""
        handle, wav_path = tempfile.mkstemp(prefix="typing_preview_", suffix=".wav")
        os.close(handle)
        command = [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-t",
            "1.8",
            "-i",
            str(sound_path),
            "-af",
            f"volume={volume:.4f}",
            "-ar",
            "22050",
            "-ac",
            "1",
            wav_path,
        ]
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
            timeout=10,
        )
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            return ""
        _remove_preview_wav_later(wav_path)
        return wav_path
    except Exception:
        return ""


def preview_typing_sound(sound_path, volume_percent):
    """تشغيل معاينة قصيرة لصوت الكتابة بمستوى الصوت المحدد."""
    sound_path = str(sound_path or "")
    if not sound_path or not os.path.exists(sound_path):
        return
    try:
        volume = max(0.0, min(1.0, float(int(volume_percent or 0)) / 100.0))
    except (TypeError, ValueError):
        volume = 0.0
    try:
        import winsound

        wav_path = _decode_typing_preview_wav(sound_path, volume)
        if not wav_path:
            return
        winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


def stop_typing_preview():
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def fonts_dir():
    return str(bundled_path("assets", "fonts", "arabic"))


def available_fonts():
    bundled_items = [
        ("Noto Naskh Arabic", fonts_dir(), "NotoNaskhArabic.ttf"),
        ("Noto Sans Arabic", fonts_dir(), "NotoSansArabic.ttf"),
        ("Amiri", fonts_dir(), "Amiri-Regular.ttf"),
        ("Cairo", fonts_dir(), "Cairo.ttf"),
    ]
    windows_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    system_items = [
        ("Arial", windows_fonts, "arial.ttf"),
        ("Arial Bold", windows_fonts, "arialbd.ttf"),
        ("Tahoma", windows_fonts, "tahoma.ttf"),
        ("Tahoma Bold", windows_fonts, "tahomabd.ttf"),
        ("Times New Roman", windows_fonts, "times.ttf"),
        ("Times New Roman Bold", windows_fonts, "timesbd.ttf"),
        ("Traditional Arabic", windows_fonts, "trado.ttf"),
        ("Traditional Arabic Bold", windows_fonts, "tradbdo.ttf"),
        ("Simplified Arabic", windows_fonts, "simpo.ttf"),
        ("Simplified Arabic Bold", windows_fonts, "simpbdo.ttf"),
        ("Calibri", windows_fonts, "calibri.ttf"),
        ("Calibri Bold", windows_fonts, "calibrib.ttf"),
        ("Segoe UI", windows_fonts, "segoeui.ttf"),
        ("Segoe UI Bold", windows_fonts, "segoeuib.ttf"),
    ]
    result = []
    seen = set()
    for name, folder, filename in [*bundled_items, *system_items]:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            key = os.path.abspath(path).lower()
            if key not in seen:
                result.append((name, path, localized_font_description(name)))
                seen.add(key)
    return result


def shaped_line(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def line_size(draw, line, font):
    box = draw.textbbox((0, 0), line, font=font)
    return box[2] - box[0], box[3] - box[1]


def break_long_word(draw, word, font, max_width):
    parts = []
    piece = ""
    for char in word:
        if line_size(draw, shaped_line(piece + char), font)[0] <= max_width:
            piece += char
        else:
            if piece:
                parts.append(piece)
            piece = char
    if piece:
        parts.append(piece)
    return parts


def wrap_paragraph(draw, paragraph, font, max_width):
    if paragraph == "":
        return [""]
    tokens = re.findall(r"\s+|\S+", paragraph)
    lines = []
    current = ""
    for token in tokens:
        candidate = f"{current}{token}"
        if line_size(draw, shaped_line(candidate), font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current.rstrip())
            current = ""
        token = token.lstrip()
        if not token:
            continue
        if line_size(draw, shaped_line(token), font)[0] <= max_width:
            current = token
            continue
        parts = break_long_word(draw, token, font, max_width)
        if parts:
            lines.extend(parts[:-1])
            current = parts[-1]
    if current:
        lines.append(current.rstrip())
    return lines


def wrap_text(draw, text, font, max_width):
    lines = []
    for paragraph in text.splitlines():
        lines.extend(wrap_paragraph(draw, paragraph, font, max_width))
    return lines or [""]


def background_color(kind, opacity):
    alpha = max(0, min(100, opacity))
    value = int(alpha * 255 / 100)
    if kind in BACKGROUND_COLORS:
        red, green, blue = BACKGROUND_COLORS[kind]
        return (red, green, blue, value)
    return (0, 0, 0, 0)


def render_text_image(options, output_path, canvas_size=None):
    canvas_width, canvas_height = (int(canvas_size[0]), int(canvas_size[1])) if canvas_size else (1920, 1080)
    font = ImageFont.truetype(options.font_path, options.font_size)
    max_width = max(80, int(canvas_width * max(10, min(100, options.box_width_percent)) / 100))
    probe = Image.new("RGBA", (max_width, 2000), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    padding = max(10, int(options.font_size * 0.45))
    lines = wrap_text(draw, options.text, font, max_width - padding * 2)
    shaped_lines = [shaped_line(line) for line in lines]
    sizes = [line_size(draw, line, font) for line in shaped_lines]
    line_height = max([height for width, height in sizes] + [options.font_size])
    width = min(max_width, max([width for width, height in sizes] + [1]) + padding * 2)
    height = max(1, line_height * len(shaped_lines) + padding * 2 + max(0, len(shaped_lines) - 1) * int(options.font_size * 0.25))
    block = Image.new("RGBA", (width, height), background_color(options.background, options.background_opacity))
    draw = ImageDraw.Draw(block)
    y = padding
    spacing = int(options.font_size * 0.25)
    for line, size in zip(shaped_lines, sizes):
        text_width, text_height = size
        x = width - padding - text_width
        draw.text((x, y), line, font=font, fill=options.color)
        y += line_height + spacing
    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    margin = 40
    if options.position == "right_top":
        x, y = canvas_width - width - margin, margin
    elif options.position == "left_top":
        x, y = margin, margin
    elif options.position == "center_top":
        x, y = (canvas_width - width) // 2, margin
    elif options.position == "right_bottom":
        x, y = canvas_width - width - margin, canvas_height - height - margin
    elif options.position == "center_bottom":
        x, y = (canvas_width - width) // 2, canvas_height - height - margin
    elif options.position == "left_bottom":
        x, y = margin, canvas_height - height - margin
    else:
        x, y = (canvas_width - width) // 2, (canvas_height - height) // 2
    image.alpha_composite(block, (max(0, x), max(0, y)))
    image.save(output_path)
    return output_path


class TypingFrameRenderer:
    def __init__(self, options, canvas_size=(1920, 1080)):
        self.options = options
        self.canvas_width, self.canvas_height = canvas_size
        self.font = ImageFont.truetype(options.font_path, options.font_size)
        self.max_width = max(80, int(self.canvas_width * max(10, min(100, options.box_width_percent)) / 100))
        self.padding = max(10, int(options.font_size * 0.45))
        self.line_spacing = int(options.font_size * 0.25)
        self.margin = max(20, int(min(self.canvas_width, self.canvas_height) * 0.035))
        self.speed = max(1, int(getattr(options, "typing_speed", 10) or 10))
        self.position = options.position
        self.text = options.text
        self._cache = {}
        probe = Image.new("RGBA", (self.max_width, 4000), (0, 0, 0, 0))
        self.probe_draw = ImageDraw.Draw(probe)

    def visible_text(self, elapsed):
        count = min(len(self.text), max(0, int(elapsed * self.speed)))
        return self.text[:count]

    def block_for(self, text):
        if text in self._cache:
            return self._cache[text]
        draw = self.probe_draw
        lines = wrap_text(draw, text, self.font, self.max_width - self.padding * 2)
        shaped_lines = [shaped_line(line) for line in lines]
        sizes = [line_size(draw, line, self.font) for line in shaped_lines]
        line_height = max([height for width, height in sizes] + [self.options.font_size])
        width = min(self.max_width, max([width for width, height in sizes] + [1]) + self.padding * 2)
        height = max(1, line_height * len(shaped_lines) + self.padding * 2 + max(0, len(shaped_lines) - 1) * self.line_spacing)
        block = Image.new("RGBA", (width, height), background_color(self.options.background, self.options.background_opacity))
        bdraw = ImageDraw.Draw(block)
        y = self.padding
        for line, size in zip(shaped_lines, sizes):
            text_width, _ = size
            bdraw.text((width - self.padding - text_width, y), line, font=self.font, fill=self.options.color)
            y += line_height + self.line_spacing
        self._cache[text] = block
        return block

    def block_position(self, block):
        width, height = block.size
        margin = self.margin
        if self.position == "right_top":
            return self.canvas_width - width - margin, margin
        if self.position == "left_top":
            return margin, margin
        if self.position == "center_top":
            return (self.canvas_width - width) // 2, margin
        if self.position == "right_bottom":
            return self.canvas_width - width - margin, self.canvas_height - height - margin
        if self.position == "left_bottom":
            return margin, self.canvas_height - height - margin
        if self.position == "center_bottom":
            return (self.canvas_width - width) // 2, self.canvas_height - height - margin
        return (self.canvas_width - width) // 2, (self.canvas_height - height) // 2

    def cursor_position_in_block(self, text):
        if not text:
            return None, None, None
        draw = self.probe_draw
        lines = wrap_text(draw, text, self.font, self.max_width - self.padding * 2)
        sizes = [line_size(draw, shaped_line(line), self.font) for line in lines]
        line_height = max([height for width, height in sizes] + [self.options.font_size])
        block_width = min(self.max_width, max([width for width, height in sizes] + [1]) + self.padding * 2)
        last_index = len(lines) - 1
        last_width, _ = sizes[last_index]
        last_char = lines[last_index][-1] if lines[last_index] else ""
        is_arabic = "\u0600" <= last_char <= "\u06FF"
        y = self.padding + last_index * (line_height + self.line_spacing)
        if is_arabic:
            x = block_width - self.padding - last_width - 4
        else:
            x = block_width - self.padding + 4
        return x, y, line_height

    def frame_bytes(self, elapsed, cursor=True):
        text = self.visible_text(elapsed)
        block = self.block_for(text)
        frame = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        block_x, block_y = self.block_position(block)
        frame.alpha_composite(block, (block_x, block_y))
        if cursor:
            cursor_x, cursor_y, cursor_height = self.cursor_position_in_block(text)
            if cursor_x is not None:
                bdraw = ImageDraw.Draw(frame)
                thickness = max(3, int(self.options.font_size * 0.09))
                bdraw.rectangle(
                    [block_x + cursor_x, block_y + cursor_y, block_x + cursor_x + thickness, block_y + cursor_y + cursor_height],
                    fill=self.options.color,
                )
        return frame.tobytes()


def probe_video_size_fps(path):
    try:
        output = media_info_text(path)
        video_line = next((line for line in output.splitlines() if " Video: " in line), "")
        width = height = 0
        for match in re.finditer(r"(\d+)x(\d+)", video_line):
            part_w, part_h = int(match.group(1)), int(match.group(2))
            if part_w >= 16 and part_h >= 16:
                width, height = part_w, part_h
                break
        fps = 0.0
        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video_line)
        if fps_match:
            fps = float(fps_match.group(1))
        if width > 0 and height > 0:
            return width, height, fps
    except Exception:
        pass
    return 1920, 1080, 0.0


def render_typing_video(
    base_path,
    output_path,
    options,
    duration,
    progress_callback=None,
    cancelled_callback=None,
    frame_rate=24,
):
    if duration is None or duration <= 0:
        raise RuntimeError("مدة النص غير صالحة")
    duration = max(0.05, float(duration))
    canvas_width, canvas_height, probed_fps = probe_video_size_fps(base_path) if base_path else (1920, 1080, 0.0)
    if probed_fps and probed_fps > 0:
        frame_rate = probed_fps
    frame_rate = int(round(frame_rate))
    if frame_rate <= 0:
        frame_rate = 24
    renderer = TypingFrameRenderer(options, (canvas_width, canvas_height))
    text_length = max(1, len(str(getattr(options, "text", "") or "")))
    renderer.speed = max(renderer.speed, math.ceil(text_length / max(0.05, duration) * 1.05))
    total_frames = int(max(1, round(duration * frame_rate)))
    has_audio = bool(base_path) and has_audio_stream(base_path)
    typing_sound = str(getattr(options, "typing_sound", "") or "")
    use_sound = bool(typing_sound) and os.path.exists(typing_sound)
    volume = max(0.0, min(1.0, float(int(getattr(options, "typing_volume", 25) or 25)) / 100.0))
    ffmpeg = ffmpeg_binary()
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    filter_parts = []
    audio_map = []
    if base_path:
        command += ["-progress", "pipe:1", "-nostats", "-i", base_path]
        command += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{canvas_width}x{canvas_height}", "-r", str(frame_rate), "-i", "pipe:0"]
        sound_input_index = 2
        if use_sound:
            command += ["-stream_loop", "-1", "-i", typing_sound]
        if not has_audio and use_sound:
            command += ["-f", "lavfi", "-t", f"{duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
        filter_parts = [f"[1:v]format=rgba[fg]", f"[0:v][fg]overlay=0:0:eof_action=pass:shortest=0[v]"]
        if use_sound:
            if has_audio:
                filter_parts.append(f"[0:a]apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,volume=1.0[a0]")
                filter_parts.append(f"[{sound_input_index}:a]volume={volume:.4f}[keys]")
                filter_parts.append("[a0][keys]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
            else:
                filter_parts.append(f"[{sound_input_index}:a]volume={volume:.4f}[keys]")
                filter_parts.append(f"[{sound_input_index + 1}:a]anull[a0]")
                filter_parts.append("[a0][keys]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
            audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "320k"]
        elif has_audio:
            filter_parts.append(f"[0:a]apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[aout]")
            audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "320k"]
        command += ["-filter_complex", ";".join(filter_parts), "-map", "[v]", *audio_map]
    else:
        command += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{canvas_width}x{canvas_height}", "-r", str(frame_rate), "-i", "pipe:0"]
        if use_sound:
            command += ["-stream_loop", "-1", "-i", typing_sound]
            command += ["-f", "lavfi", "-t", f"{duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
            filter_parts = [
                "[0:v]format=rgba[v]",
                f"[1:a]volume={volume:.4f}[keys]",
                "[2:a]anull[a0]",
                "[a0][keys]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
            ]
            audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "320k"]
        else:
            filter_parts = ["[0:v]format=rgba[v]"]
            audio_map = []
        command += ["-filter_complex", ";".join(filter_parts), "-map", "[v]", *audio_map]
    command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    command += ["-t", f"{duration:.6f}", "-movflags", "+faststart", output_path]

    stderr_file = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            stdin=subprocess.PIPE,
            startupinfo=ffmpeg_startupinfo(),
        )
    except Exception as error:
        stderr_file.close()
        raise RuntimeError(f"تعذر تشغيل محرك الفيديو: {error}")
    progress_lines = queue.Queue()
    reader_finished = threading.Event()

    def read_progress():
        try:
            if process.stdout:
                for line in process.stdout:
                    progress_lines.put(line)
        except Exception:
            pass
        finally:
            reader_finished.set()

    reader = threading.Thread(target=read_progress, daemon=True)
    reader.start()
    last_percent = -1
    cancelled = False
    try:
        try:
            for index in range(total_frames):
                if _cancelled(cancelled_callback):
                    cancelled = True
                    _terminate_process(process)
                    break
                elapsed = index / float(frame_rate)
                cursor = int(elapsed * 2) % 2 == 0
                frame_data = renderer.frame_bytes(elapsed, cursor)
                try:
                    process.stdin.write(frame_data)
                except BrokenPipeError:
                    break
                if base_path:
                    percent = int(index * 100 / total_frames)
                    if percent != last_percent:
                        last_percent = percent
                        if progress_callback:
                            progress_callback(percent)
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass
        return_code = process.wait() if process.poll() is None else process.poll()
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
    if cancelled or _cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    if return_code != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(stderr_text.strip() or "تعذر إنشاء نص الكتابة")
    if progress_callback:
        progress_callback(100)
    return output_path


def build_typing_overlay_segment(timeline, start_time, end_time, options, progress_callback=None, cancelled_callback=None):
    temp_dir = tempfile.mkdtemp(prefix="typing_overlay_")
    selected_path = os.path.join(temp_dir, "selected.mp4")
    output_path = os.path.join(temp_dir, "typing_overlay.mp4")
    try:
        if _cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        selected_segments = slice_segments(timeline, start_time, end_time)
        if not selected_segments:
            raise RuntimeError("تعذر تحديد الجزء المطلوب لإدراج النص")
        write_timeline_video(
            selected_segments,
            selected_path,
            progress_callback=lambda percent: progress_callback(float(percent) * 0.3) if progress_callback else None,
            cancelled_callback=cancelled_callback,
        )
        if _cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        duration = max(0.05, float(end_time) - float(start_time))
        render_typing_video(
            selected_path,
            output_path,
            options,
            duration,
            progress_callback=lambda percent: progress_callback(30 + float(percent) * 0.7) if progress_callback else None,
            cancelled_callback=cancelled_callback,
        )
        return output_path, temp_dir
    except AudioEffectPreparationCancelled:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def build_text_overlay_segment(
    timeline,
    start_time,
    end_time,
    options,
    progress_callback=None,
    cancelled_callback=None,
):
    """إنتاج جزء النص (عادي أو كتابة تدريجية) مع إلغاء طبيعي وتقدم موحد."""
    if getattr(options, "mode", "") == "typing":
        return build_typing_overlay_segment(
            timeline,
            start_time,
            end_time,
            options,
            progress_callback=progress_callback,
            cancelled_callback=cancelled_callback,
        )
    temp_dir = tempfile.mkdtemp(prefix="text_overlay_")
    selected_path = os.path.join(temp_dir, "selected.mp4")
    text_image_path = os.path.join(temp_dir, "text.png")
    output_path = os.path.join(temp_dir, "text_overlay.mp4")
    try:
        raise_if_cancelled(cancelled_callback)
        selected_segments = slice_segments(timeline, start_time, end_time)
        write_timeline_video(
            selected_segments,
            selected_path,
            progress_callback=(lambda value: progress_callback(float(value) * 0.45)) if progress_callback else None,
            cancelled_callback=cancelled_callback,
        )
        raise_if_cancelled(cancelled_callback)
        render_text_image(options, text_image_path)
        raise_if_cancelled(cancelled_callback)
        overlay_options = ImageOverlayOptions(
            image_path=text_image_path,
            full_screen=True,
            position=options.position,
            width_percent=100,
            height_percent=100,
        )
        apply_image_overlay(
            selected_path,
            output_path,
            overlay_options,
            progress_callback=(lambda value: progress_callback(45.0 + float(value) * 0.55)) if progress_callback else None,
            cancelled_callback=cancelled_callback,
        )
        raise_if_cancelled(cancelled_callback)
        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise



class TextOverlayDialog(wx.Dialog):
    def __init__(self, parent, is_auto_subtitle_mode=False, title="إدراج نص", apply_label="تطبيق", apply_name="تطبيق إعدادات النص وإدراجه", part_duration=None, canvas_size=None, initial_options=None, range_start=None, range_end=None):
        super().__init__(parent, title=title, size=(620, 600))
        self.parent = parent
        self.options = None
        self.range_start = None
        self.range_end = None
        self.is_auto_subtitle_mode = is_auto_subtitle_mode
        self.fonts = available_fonts()
        self.choice_last_selection = {}
        self._tip_cache = {}
        self.part_duration = part_duration
        self.canvas_size = canvas_size or (1920, 1080)
        self.initial_options = initial_options
        self.range_start_value = range_start
        self.range_end_value = range_end
        self._typing_was_active = False
        self._previous_mode = 0
        self._speed_overflow_warned = False
        self._speech_later = None
        self._pending_announce_choice = None
        self._pending_speech_message = None
        self._preview_later = None
        self.closed = False
        self._description_speech_serial = 0
        self.panel = wx.Panel(self)
        self.panel.SetName(tr(" "))
        self.panel.SetLabel(" ")
        if hasattr(self.panel, "SetAccessibleName"):
            self.panel.SetAccessibleName(" ")
        panel = self.panel
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.text_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.text_ctrl.SetName(tr("النص"))
        self.text_ctrl.Bind(wx.EVT_TEXT, self.on_text_changed)
        self.mode_choice = wx.Choice(panel, choices=["عادي", "الترجمة", "كتابة (تظهر الحروف تدريجيا)"])
        self.mode_choice.SetSelection(0)
        self.mode_choice.SetName(tr("طريقة عرض النص"))
        self.font_choice = wx.Choice(panel, choices=[name for name, path, description in self.fonts])
        self.font_choice.SetName(tr("اختيار الخط"))
        if self.fonts:
            self.font_choice.SetSelection(0)
        self.color_choice = wx.Choice(panel, choices=[name for key, name, color, description in COLORS])
        self.color_choice.SetSelection(0)
        self.color_choice.SetName(tr("اختيار لون الخط"))
        self.background_choice = wx.Choice(panel, choices=[name for key, name, description in BACKGROUNDS])
        self.background_choice.SetSelection(1)
        self.background_choice.SetName(tr("اختيار خلفية النص"))
        self.position_choice = wx.Choice(panel, choices=[label for key, label, description in POSITIONS])
        self.position_choice.SetSelection(4)
        self.position_choice.SetName(tr("اختيار مكان النص على الفيديو"))
        self.font_slider = wx.Slider(panel, value=44, minValue=12, maxValue=160, style=wx.SL_HORIZONTAL)
        self.width_slider = wx.Slider(panel, value=60, minValue=10, maxValue=100, style=wx.SL_HORIZONTAL)
        self.opacity_slider = wx.Slider(panel, value=45, minValue=0, maxValue=100, style=wx.SL_HORIZONTAL)
        for slider in (self.font_slider, self.width_slider, self.opacity_slider):
            slider.SetLineSize(1)
            slider.SetPageSize(10)
            slider.Bind(wx.EVT_SLIDER, self.on_slider_changed)
            slider.Bind(wx.EVT_KEY_DOWN, self.on_slider_key)
            slider.Bind(wx.EVT_SET_FOCUS, self.on_slider_focus)
        self.font_slider.SetName(tr("حجم الخط"))
        self.width_slider.SetName(tr("عرض النص"))
        self.opacity_slider.SetName(tr("شفافية خلفية النص"))
        self._typing_add_label = "أضف صوت كتابة جديد..."
        self._typing_sounds = list_typing_sounds()
        self._typing_sound_before_add = 0
        self.typing_sound_choice = wx.Choice(panel, choices=self.typing_sound_choice_labels())
        self.typing_sound_choice.SetName(tr("اختيار صوت الكتابة"))
        if self._typing_sounds:
            self.typing_sound_choice.SetSelection(0)
        self.typing_volume_slider = wx.Slider(panel, value=25, minValue=0, maxValue=100, style=wx.SL_HORIZONTAL)
        self.typing_speed_slider = wx.Slider(panel, value=10, minValue=1, maxValue=40, style=wx.SL_HORIZONTAL)
        for slider in (self.typing_volume_slider, self.typing_speed_slider):
            slider.SetLineSize(1)
            slider.SetPageSize(5)
            slider.Bind(wx.EVT_SLIDER, self.on_slider_changed)
            slider.Bind(wx.EVT_KEY_DOWN, self.on_slider_key)
            slider.Bind(wx.EVT_SET_FOCUS, self.on_slider_focus)
        self.typing_volume_slider.SetName(tr("مستوى صوت الكتابة"))
        self.typing_speed_slider.SetName(tr("سرعة الكتابة"))
        self.mixed_checkbox = wx.CheckBox(panel, label="نصوص مختلطة عربي وإنجليزي")
        self.mixed_checkbox.SetName(tr("نصوص مختلطة عربي وإنجليزي"))
        self.mixed_checkbox.SetToolTip("حدد هذا المربع إذا كان هناك نصوص مختلطة بين العربية والإنجليزية")
        self.add_control(main_sizer, panel, "النص", self.text_ctrl, 1)
        self.mode_label = self.add_control(main_sizer, panel, "طريقة عرض النص", self.mode_choice)
        self.add_control(main_sizer, panel, "اختيار الخط", self.font_choice)
        self.add_control(main_sizer, panel, "اختيار لون الخط", self.color_choice)
        self.add_control(main_sizer, panel, "اختيار خلفية النص", self.background_choice)
        self.add_control(main_sizer, panel, "اختيار مكان النص", self.position_choice)
        self.add_control(main_sizer, panel, "شريط تمرير حجم الخط", self.font_slider)
        self.add_control(main_sizer, panel, "شريط تمرير عرض صندوق النص", self.width_slider)
        self.opacity_label = self.add_control(main_sizer, panel, "شريط تمرير شفافية خلفية النص", self.opacity_slider)
        self.sound_label = self.add_control(main_sizer, panel, "اختيار صوت الكتابة", self.typing_sound_choice)
        self.volume_label = self.add_control(main_sizer, panel, "شريط تمرير مستوى صوت الكتابة", self.typing_volume_slider)
        self.speed_label = self.add_control(main_sizer, panel, "شريط تمرير سرعة الكتابة", self.typing_speed_slider)
        main_sizer.Add(self.mixed_checkbox, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        self.range_start_ctrl = None
        self.range_end_ctrl = None
        if self.initial_options is not None or self.range_start_value is not None or self.range_end_value is not None:
            self.range_start_ctrl = wx.TextCtrl(panel)
            self.range_start_ctrl.SetName(tr("بداية النطاق"))
            self.range_start_ctrl.SetToolTip("زمن بداية ظهور النص بالثواني")
            self.range_end_ctrl = wx.TextCtrl(panel)
            self.range_end_ctrl.SetName(tr("نهاية النطاق"))
            self.range_end_ctrl.SetToolTip("زمن نهاية ظهور النص بالثواني")
            self.range_start_ctrl.SetValue(f"{self.range_start_value:.3f}" if self.range_start_value is not None else "0.000")
            self.range_end_ctrl.SetValue(f"{self.range_end_value:.3f}" if self.range_end_value is not None else "")
            self.add_control(main_sizer, panel, "بداية النطاق بالثواني", self.range_start_ctrl)
            self.add_control(main_sizer, panel, "نهاية النطاق بالثواني", self.range_end_ctrl)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        reset_button = wx.Button(panel, label="إعادة القيم إلى الافتراضي")
        insert_button = wx.Button(panel, label=apply_label)
        cancel_button = wx.Button(panel, label="إلغاء")
        reset_button.SetName(tr("إعادة القيم إلى الافتراضي"))
        insert_button.SetName(apply_name)
        cancel_button.SetName(tr("إلغاء"))
        insert_button.SetDefault()
        buttons.Add(reset_button, flag=wx.ALL, border=6)
        buttons.Add(insert_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        main_sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=8)
        panel.SetSizer(main_sizer)
        self.font_choice.Bind(wx.EVT_CHOICE, self.on_choice_changed)
        self.color_choice.Bind(wx.EVT_CHOICE, self.on_choice_changed)
        self.background_choice.Bind(wx.EVT_CHOICE, self.on_choice_changed)
        self.position_choice.Bind(wx.EVT_CHOICE, self.on_choice_changed)
        self.mode_choice.Bind(wx.EVT_CHOICE, self.on_mode_changed)
        self.typing_sound_choice.Bind(wx.EVT_CHOICE, self.on_typing_sound_changed)
        self.mixed_checkbox.Bind(wx.EVT_CHECKBOX, self.on_mixed_changed)
        self.mixed_checkbox.Bind(wx.EVT_SET_FOCUS, self.on_mixed_focus)
        for choice in (self.font_choice, self.color_choice, self.background_choice, self.position_choice, self.mode_choice, self.typing_sound_choice):
            choice.Bind(wx.EVT_SET_FOCUS, self.on_choice_focus)
            choice.Bind(wx.EVT_KEY_DOWN, self.on_choice_key)
        insert_button.Bind(wx.EVT_BUTTON, self.accept)
        cancel_button.Bind(wx.EVT_BUTTON, self.close)
        reset_button.Bind(wx.EVT_BUTTON, self.reset_to_defaults)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_INIT_DIALOG, self.on_init_dialog)
        self.Bind(wx.EVT_SHOW, self.on_show)
        if is_auto_subtitle_mode:
            self.text_ctrl.Hide()
            main_sizer.Hide(self.text_ctrl)
        self.apply_saved_settings()
        self.apply_initial_options()
        self.update_opacity_visibility()
        self._previous_mode = self.mode_choice.GetSelection()
        self._typing_was_active = self.mode_choice.GetSelection() == 2
        self.update_typing_visibility()
        self.reset_choice_tracking()
        self.update_status()
        self.Centre()
        if not is_auto_subtitle_mode:
            self.force_text_focus()

    def ShowModal(self):
        if not self.is_auto_subtitle_mode:
            self.force_text_focus()
        else:
            wx.CallAfter(self.font_choice.SetFocus)
        return super().ShowModal()

    def add_control(self, main_sizer, panel, label_text, control, proportion=0):
        label = make_visual_label(panel, label_text)
        main_sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        main_sizer.Add(control, proportion=proportion, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, border=8)
        return label

    def force_text_focus(self):
        self.text_ctrl.SetFocus()
        self.text_ctrl.SetInsertionPointEnd()
        wx.CallAfter(self.text_ctrl.SetFocus)
        wx.CallLater(30, self.text_ctrl.SetFocus)
        wx.CallLater(120, self.text_ctrl.SetFocus)

    def on_init_dialog(self, event):
        self.force_text_focus()

    def on_show(self, event):
        if event.IsShown():
            self.force_text_focus()
        event.Skip()

    def speak(self, message, interrupt=True, wait_for_ui=True):
        if hasattr(self.parent, "say"):
            self.parent.say(message, interrupt=interrupt, wait_for_ui=wait_for_ui)

    def schedule_speak(self, message):
        self._pending_speech_message = message
        self._schedule_speech_flush()

    def schedule_announce(self, choice):
        self._pending_announce_choice = choice
        self._schedule_speech_flush()

    def _schedule_speech_flush(self):
        if self._speech_later is None:
            self._speech_later = wx.CallLater(200, self._flush_pending_speech)
        else:
            self._speech_later.Restart()

    def _flush_pending_speech(self):
        self._speech_later = None
        choice = self._pending_announce_choice
        message = self._pending_speech_message
        self._pending_announce_choice = None
        self._pending_speech_message = None
        if choice is not None:
            self.announce_choice(choice, force=True)
        elif message:
            self.speak(message)

    def _stop_pending_speech(self):
        if self._speech_later is not None:
            self._speech_later.Stop()
            self._speech_later = None
        self._pending_announce_choice = None
        self._pending_speech_message = None

    def set_control_tip(self, control, message):
        if self._tip_cache.get(control) != message:
            control.SetToolTip(message)
            self._tip_cache[control] = message

    def selected_font_info(self):
        if not self.fonts:
            return "", "", "لا توجد خطوط متاحة"
        selection = self.font_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self.fonts):
            selection = 0
        return self.fonts[selection]

    def selected_color_info(self):
        selection = self.color_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(COLORS):
            selection = 0
        return COLORS[selection]

    def selected_background_info(self):
        selection = self.background_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(BACKGROUNDS):
            selection = 0
        return BACKGROUNDS[selection]

    def selected_position_info(self):
        selection = self.position_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(POSITIONS):
            selection = 4
        return POSITIONS[selection]

    def font_choice_message(self):
        name, path, description = self.selected_font_info()
        return f"{name} {description}".strip()

    def color_choice_message(self):
        key, name, color, description = self.selected_color_info()
        return name

    def background_choice_message(self):
        key, name, description = self.selected_background_info()
        return name

    def position_choice_message(self):
        key, label, description = self.selected_position_info()
        return label

    def mode_choice_message(self):
        selection = self.mode_choice.GetSelection()
        if selection == 1:
            return "الترجمة"
        if selection == 2:
            return "كتابة تظهر الحروف تدريجيا"
        return "عادي"

    def typing_sound_choice_message(self):
        selection = self.typing_sound_choice.GetSelection()
        if selection == wx.NOT_FOUND:
            return ""
        if selection >= len(self._typing_sounds):
            return "إضافة صوت كتابة جديد"
        return self._typing_sounds[selection][0]

    def mode_choice_tip(self):
        selection = self.mode_choice.GetSelection()
        if selection == 1:
            return "الترجمة: إعدادات جاهزة لشكل الترجمات أسفل الفيديو بخط واضح وخلفية داكنة"
        if selection == 2:
            return "كتابة: تظهر الحروف على الشاشة واحدا تلو الآخر مع صوت لوحة مفاتيح في الخلفية"
        return "عادي: يظهر النص كاملا مرة واحدة بدون حركة"

    def typing_sound_choice_tip(self):
        selection = self.typing_sound_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self._typing_sounds):
            return "إضافة صوت لوحة مفاتيح جديد من جهازك"
        name, path = self._typing_sounds[selection]
        return f"صوت الكتابة {name}"

    def selected_typing_sound_path(self):
        selection = self.typing_sound_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self._typing_sounds):
            return ""
        return self._typing_sounds[selection][1]

    def font_choice_tip(self):
        name, path, description = self.selected_font_info()
        return f"{name} {description}".strip()

    def color_choice_tip(self):
        key, name, color, description = self.selected_color_info()
        return f"{name} {description}".strip()

    def background_choice_tip(self):
        key, name, description = self.selected_background_info()
        return f"{name} {description}".strip()

    def position_choice_tip(self):
        key, label, description = self.selected_position_info()
        return f"{label} {description}".strip()

    def choice_tip(self, choice):
        if choice is self.font_choice:
            return self.font_choice_tip()
        if choice is self.color_choice:
            return self.color_choice_tip()
        if choice is self.background_choice:
            return self.background_choice_tip()
        if choice is self.position_choice:
            return self.position_choice_tip()
        if choice is self.mode_choice:
            return self.mode_choice_tip()
        if choice is self.typing_sound_choice:
            return self.typing_sound_choice_tip()
        return ""

    def refresh_choice_tip(self, choice):
        self.set_control_tip(choice, self.choice_tip(choice))

    def refresh_choice_names(self):
        for choice in (
            self.font_choice,
            self.color_choice,
            self.background_choice,
            self.position_choice,
            self.mode_choice,
            self.typing_sound_choice,
        ):
            self.refresh_choice_tip(choice)

    def choice_message(self, choice):
        return self.choice_tip(choice)

    def choice_control_name(self, choice):
        if choice is self.font_choice:
            return "اختيار الخط"
        if choice is self.color_choice:
            return "اختيار لون الخط"
        if choice is self.background_choice:
            return "اختيار خلفية النص"
        if choice is self.position_choice:
            return "اختيار مكان النص على الفيديو"
        if choice is self.mode_choice:
            return "طريقة عرض النص"
        if choice is self.typing_sound_choice:
            return "اختيار صوت الكتابة"
        return ""

    def on_choice_focus(self, event):
        event.Skip()

    def on_choice_changed(self, event):
        choice = event.GetEventObject()
        self.refresh_choice_tip(choice)
        self.announce_choice(choice, force=True)
        if choice is self.background_choice:
            self.update_opacity_visibility()
        event.Skip()

    def on_mode_changed(self, event):
        self.update_typing_visibility()
        self.update_status()
        self.announce_choice(self.mode_choice, force=True)
        event.Skip()

    def _speak_description_after_name(self, text, control):
        if not text:
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
            self.speak(text, interrupt=False, wait_for_ui=False)

        try:
            wx.CallLater(220, speak_after_control_name)
        except Exception:
            try:
                wx.CallAfter(speak_after_control_name)
            except Exception:
                pass

    def on_mixed_changed(self, event):
        if self.mixed_checkbox.GetValue():
            self.speak("تم تحديد النصوص المختلطة")
        else:
            self.speak("تم إلغاء تحديد النصوص المختلطة")
        event.Skip()

    def on_mixed_focus(self, event):
        self._speak_description_after_name(
            "حدد هذا المربع إذا كان هناك نصوص مختلطة بين العربية والإنجليزية",
            self.mixed_checkbox,
        )
        event.Skip()

    def on_typing_sound_changed(self, event):
        selection = self.typing_sound_choice.GetSelection()
        if selection == len(self._typing_sounds):
            self._typing_sound_before_add = self._typing_sound_previous_selection()
            self.add_typing_sound_from_dialog()
            return
        self.refresh_choice_tip(self.typing_sound_choice)
        self.announce_choice(self.typing_sound_choice, force=True)
        self._preview_selected_typing_sound()
        event.Skip()

    def _typing_sound_previous_selection(self):
        selection = self.typing_sound_choice.GetSelection()
        if 0 <= selection < len(self._typing_sounds):
            return selection
        return 0

    def typing_sound_choice_labels(self):
        return [name for name, path in self._typing_sounds] + [self._typing_add_label]

    def refresh_typing_sound_choice(self, select=None):
        labels = self.typing_sound_choice_labels()
        self.typing_sound_choice.SetItems(labels)
        if select and select in labels:
            self.typing_sound_choice.SetSelection(labels.index(select))
        else:
            self.typing_sound_choice.SetSelection(0)

    def add_typing_sound_from_dialog(self):
        previous = self._typing_sound_before_add
        dialog = wx.FileDialog(
            self.parent,
            "اختر صوت كتابة لإضافته",
            wildcard="ملفات الصوت|*.mp3;*.wav;*.ogg;*.m4a|جميع الملفات|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dialog.ShowModal() == wx.ID_OK:
            source = dialog.GetPath()
            dialog.Destroy()
            copied = copy_typing_sound(source)
            if copied:
                self._typing_sounds = list_typing_sounds()
                self.refresh_typing_sound_choice(select=os.path.basename(copied))
                self.schedule_announce(self.typing_sound_choice)
                self.speak("تمت إضافة صوت الكتابة")
                self._preview_selected_typing_sound()
            else:
                self.typing_sound_choice.SetSelection(previous)
            return
        dialog.Destroy()
        self.typing_sound_choice.SetSelection(previous)

    def _preview_selected_typing_sound(self):
        if self.mode_choice.GetSelection() != 2:
            return
        path = self.selected_typing_sound_path()
        if not path:
            return
        preview_typing_sound(path, self.typing_volume_slider.GetValue())

    def _schedule_volume_preview(self):
        if self._preview_later is not None:
            self._preview_later.Stop()
            self._preview_later = None
        self._preview_later = wx.CallLater(350, self._preview_selected_typing_sound)

    def update_typing_visibility(self):
        selection = self.mode_choice.GetSelection()
        is_typing = selection == 2
        self._typing_was_active = is_typing
        typing_controls = (
            self.sound_label,
            self.typing_sound_choice,
            self.volume_label,
            self.typing_volume_slider,
            self.speed_label,
            self.typing_speed_slider,
            self.mixed_checkbox,
        )
        visibility_changed = False
        self.Freeze()
        try:
            for control in typing_controls:
                if control.IsShown() != is_typing:
                    visibility_changed = True
                control.Enable(is_typing)
                control.Show(is_typing)
            previous = self._previous_mode
            self._previous_mode = selection
            if selection == 2 and previous != 2:
                self.apply_typing_defaults()
                self._preview_selected_typing_sound()
            elif selection == 1 and previous != 1:
                self.apply_subtitle_defaults()
            self.panel.Layout()
            if visibility_changed:
                self.Fit()
        finally:
            self.Thaw()

    def update_opacity_visibility(self):
        key = self.selected_background_info()[0]
        is_visible = key != "none"
        controls = (self.opacity_label, self.opacity_slider)
        visibility_changed = False
        self.Freeze()
        try:
            for control in controls:
                if control.IsShown() != is_visible:
                    visibility_changed = True
                control.Enable(is_visible)
                control.Show(is_visible)
            self.panel.Layout()
            if visibility_changed:
                self.Fit()
        finally:
            self.Thaw()

    def reset_to_defaults(self, event=None):
        self.text_ctrl.SetValue("")
        self.mode_choice.SetSelection(0)
        if self.fonts:
            self.font_choice.SetSelection(0)
        self.color_choice.SetSelection(0)
        self.background_choice.SetSelection(1)
        self.position_choice.SetSelection(4)
        self.font_slider.SetValue(44)
        self.width_slider.SetValue(60)
        self.opacity_slider.SetValue(45)
        if self._typing_sounds:
            self.typing_sound_choice.SetSelection(0)
        self.typing_volume_slider.SetValue(25)
        self.typing_speed_slider.SetValue(10)
        self.mixed_checkbox.SetValue(False)
        self.update_typing_visibility()
        self.update_opacity_visibility()
        self.update_slider_names()
        self.refresh_choice_names()
        self.speak("تمت إعادة القيم إلى الافتراضي")
        if event is not None:
            event.Skip()

    def typing_text(self):
        return self.text_ctrl.GetValue()

    def _typing_default_font_size(self):
        canvas_height = max(1, self.canvas_size[1])
        return max(28, min(160, int(canvas_height * 0.08)))

    def _subtitle_default_font_size(self):
        canvas_height = max(1, self.canvas_size[1])
        return max(24, min(120, int(canvas_height * 0.055)))

    def select_font_by_name(self, name):
        for index, (font_name, _path, _description) in enumerate(self.fonts):
            if font_name == name:
                self.font_choice.SetSelection(index)
                return

    def needed_typing_speed(self):
        text = self.typing_text()
        if not text:
            return None
        duration = self.part_duration
        if not duration or duration <= 0:
            return None
        return max(1, math.ceil(len(text) / max(0.05, float(duration)) * 1.05))

    def apply_auto_typing_speed(self):
        needed = self.needed_typing_speed()
        if needed is None:
            return
        slider_max = self.typing_speed_slider.GetMax()
        if needed > slider_max:
            if not self._speed_overflow_warned:
                self._speed_overflow_warned = True
                self.speak("النص أطول من أن يظهر كاملا في الجزء المحدد حتى بأقصى سرعة كتابة")
            self.typing_speed_slider.SetValue(slider_max)
            return
        self._speed_overflow_warned = False
        self.typing_speed_slider.SetValue(needed)

    def apply_typing_defaults(self):
        self._speed_overflow_warned = False
        self.position_choice.SetSelection(6)
        self.width_slider.SetValue(80)
        self.font_slider.SetValue(self._typing_default_font_size())
        self.apply_auto_typing_speed()

    def apply_subtitle_defaults(self):
        self.select_font_by_name("Noto Sans Arabic")
        self.color_choice.SetSelection(0)
        self.background_choice.SetSelection(1)
        self.opacity_slider.SetValue(55)
        self.update_opacity_visibility()
        self.position_choice.SetSelection(4)
        self.font_slider.SetValue(self._subtitle_default_font_size())
        self.width_slider.SetValue(90)

    def on_text_changed(self, event):
        if self.mode_choice.GetSelection() == 2:
            self.apply_auto_typing_speed()
        event.Skip()

    def reset_choice_tracking(self):
        for choice in (self.font_choice, self.color_choice, self.background_choice, self.position_choice, self.mode_choice, self.typing_sound_choice):
            self.choice_last_selection[choice] = choice.GetSelection()

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

    def announce_choice(self, choice, force=False):
        selection = choice.GetSelection()
        if not force and self.choice_last_selection.get(choice) == selection:
            return
        self.choice_last_selection[choice] = selection
        message = self.choice_message(choice)
        if message:
            self.speak(message)

    def slider_value_message(self, slider):
        value = slider.GetValue()
        if slider is self.typing_speed_slider:
            return f"{value} حرف في الثانية"
        if slider is self.width_slider:
            return f"{value} بالمئة من عرض الفيديو"
        return f"{value} بالمئة"

    def slider_label(self, slider):
        """التسمية المناسبة لكل شريط تمرير حسب وظيفته."""
        if slider is self.font_slider:
            return "حجم الخط"
        if slider is self.width_slider:
            return "عرض صندوق النص"
        if slider is self.opacity_slider:
            return "شفافية خلفية النص"
        if slider is self.typing_volume_slider:
            return "مستوى صوت الكتابة"
        if slider is self.typing_speed_slider:
            return "سرعة الكتابة"
        return ""

    def update_slider_names(self):
        for slider in (
            self.font_slider,
            self.width_slider,
            self.opacity_slider,
            self.typing_volume_slider,
            self.typing_speed_slider,
        ):
            name = self.slider_label(slider)
            if name and slider.GetName() != name:
                slider.SetName(name)

    def on_slider_changed(self, event=None):
        slider = event.GetEventObject() if event else None
        self.update_slider_names()
        if slider is self.typing_volume_slider:
            self._schedule_volume_preview()

    def update_status(self, event=None):
        self.update_slider_names()
        self.refresh_choice_names()

    def on_slider_focus(self, event):
        self._stop_pending_speech()
        slider = event.GetEventObject()
        value = self.slider_value_message(slider)
        label = self.slider_label(slider)
        message = f"{value}، {label}" if label else value
        self.speak(message)
        event.Skip()

    def on_slider_key(self, event):
        key = event.GetKeyCode()
        slider = event.GetEventObject()
        if key in (wx.WXK_TAB, wx.WXK_ESCAPE):
            event.Skip()
            return
        if key in (wx.WXK_UP, wx.WXK_RIGHT, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_RIGHT):
            slider.SetValue(min(slider.GetMax(), slider.GetValue() + 1))
            self.update_slider_names()
            self._stop_pending_speech()
            self.speak(self.slider_value_message(slider), wait_for_ui=False)
            if slider is self.typing_volume_slider:
                self._schedule_volume_preview()
            return
        if key in (wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_NUMPAD_DOWN, wx.WXK_NUMPAD_LEFT):
            slider.SetValue(max(slider.GetMin(), slider.GetValue() - 1))
            self.update_slider_names()
            self._stop_pending_speech()
            self.speak(self.slider_value_message(slider), wait_for_ui=False)
            if slider is self.typing_volume_slider:
                self._schedule_volume_preview()
            return
        if key in (wx.WXK_PAGEUP, wx.WXK_NUMPAD_PAGEUP):
            slider.SetValue(min(slider.GetMax(), slider.GetValue() + 10))
            self.update_slider_names()
            self._stop_pending_speech()
            self.speak(self.slider_value_message(slider), wait_for_ui=False)
            if slider is self.typing_volume_slider:
                self._schedule_volume_preview()
            return
        if key in (wx.WXK_PAGEDOWN, wx.WXK_NUMPAD_PAGEDOWN):
            slider.SetValue(max(slider.GetMin(), slider.GetValue() - 10))
            self.update_slider_names()
            self._stop_pending_speech()
            self.speak(self.slider_value_message(slider), wait_for_ui=False)
            if slider is self.typing_volume_slider:
                self._schedule_volume_preview()
            return
        event.Skip()

    def selected_font(self):
        name, path, description = self.selected_font_info()
        return name, path

    def selected_color(self):
        key, name, color, description = self.selected_color_info()
        return color

    def selected_background(self):
        key, name, description = self.selected_background_info()
        return key

    def selected_position(self):
        key, label, description = self.selected_position_info()
        return key

    def _set_slider_value(self, slider, value):
        if value is None:
            return
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return
        slider.SetValue(max(slider.GetMin(), min(slider.GetMax(), numeric)))

    def apply_saved_settings(self):
        settings = get_text_overlay_last_settings()
        if not settings:
            return
        mode = settings.get("mode")
        if isinstance(mode, int) and mode in (0, 1, 2):
            self.mode_choice.SetSelection(mode)
        font_name = settings.get("font_name")
        if font_name:
            self.select_font_by_name(str(font_name))
        color_key = settings.get("color")
        if color_key:
            for index, (key, _name, _color, _description) in enumerate(COLORS):
                if key == color_key:
                    self.color_choice.SetSelection(index)
                    break
        background_key = settings.get("background")
        if background_key:
            for index, (key, _name, _description) in enumerate(BACKGROUNDS):
                if key == background_key:
                    self.background_choice.SetSelection(index)
                    break
        position_key = settings.get("position")
        if position_key:
            for index, (key, _label, _description) in enumerate(POSITIONS):
                if key == position_key:
                    self.position_choice.SetSelection(index)
                    break
        self._set_slider_value(self.font_slider, settings.get("font_size"))
        self._set_slider_value(self.width_slider, settings.get("width_percent"))
        self._set_slider_value(self.opacity_slider, settings.get("background_opacity"))
        self._set_slider_value(self.typing_volume_slider, settings.get("typing_volume"))
        self._set_slider_value(self.typing_speed_slider, settings.get("typing_speed"))
        typing_sound = settings.get("typing_sound")
        if typing_sound:
            saved_path = os.path.normpath(os.path.abspath(str(typing_sound)))
            for index, (_name, path) in enumerate(self._typing_sounds):
                if os.path.normpath(os.path.abspath(path)) == saved_path:
                    self.typing_sound_choice.SetSelection(index)
                    break
        mixed = settings.get("mixed_text")
        if isinstance(mixed, bool):
            self.mixed_checkbox.SetValue(mixed)

    def apply_initial_options(self):
        """يُعبّئ الحوار بخيارات عنصر نصي قائم عند إعادة فتح المحرر للتعديل."""
        options = self.initial_options
        if options is None:
            return
        self.text_ctrl.SetValue(options.text)
        if options.font_name:
            self.select_font_by_name(options.font_name)
        for index, (key, _name, color, _description) in enumerate(COLORS):
            if key == options.color:
                self.color_choice.SetSelection(index)
                break
        for index, (key, _name, _description) in enumerate(BACKGROUNDS):
            if key == options.background:
                self.background_choice.SetSelection(index)
                break
        for index, (key, _label, _description) in enumerate(POSITIONS):
            if key == options.position:
                self.position_choice.SetSelection(index)
                break
        self._set_slider_value(self.font_slider, options.font_size)
        self._set_slider_value(self.width_slider, options.box_width_percent)
        self._set_slider_value(self.opacity_slider, options.background_opacity)
        self._set_slider_value(self.typing_volume_slider, options.typing_volume)
        self._set_slider_value(self.typing_speed_slider, options.typing_speed)
        if options.mode == "typing":
            self.mode_choice.SetSelection(2)
        elif options.mode == "subtitles":
            self.mode_choice.SetSelection(1)
        else:
            self.mode_choice.SetSelection(0)
        if options.typing_sound:
            saved_path = os.path.normpath(os.path.abspath(str(options.typing_sound)))
            for index, (_name, path) in enumerate(self._typing_sounds):
                if os.path.normpath(os.path.abspath(path)) == saved_path:
                    self.typing_sound_choice.SetSelection(index)
                    break
        self.mixed_checkbox.SetValue(bool(options.mixed_text))
        self._previous_mode = self.mode_choice.GetSelection()
        self._typing_was_active = self.mode_choice.GetSelection() == 2
        self.update_opacity_visibility()
        self.update_typing_visibility()

    def _save_last_settings(self):
        set_text_overlay_last_settings({
            "mode": self.mode_choice.GetSelection(),
            "font_name": self.selected_font_info()[0],
            "font_size": self.font_slider.GetValue(),
            "color": self.selected_color_info()[0],
            "background": self.selected_background_info()[0],
            "background_opacity": self.opacity_slider.GetValue(),
            "position": self.selected_position_info()[0],
            "width_percent": self.width_slider.GetValue(),
            "typing_sound": self.selected_typing_sound_path(),
            "typing_volume": self.typing_volume_slider.GetValue(),
            "typing_speed": self.typing_speed_slider.GetValue(),
            "mixed_text": self.mixed_checkbox.GetValue(),
        })

    def accept(self, event=None):
        text = self.text_ctrl.GetValue()
        if not text.strip() and not self.is_auto_subtitle_mode:
            wx.MessageBox("اكتب النص أولا.", "بيانات ناقصة", wx.OK | wx.ICON_INFORMATION)
            return
        if not self.fonts:
            wx.MessageBox("لا توجد خطوط عربية متاحة.", "خطأ", wx.OK | wx.ICON_ERROR)
            return
        font_name, font_path = self.selected_font()
        self.options = TextOverlayOptions(
            text=text,
            font_path=font_path,
            font_name=font_name,
            font_size=self.font_slider.GetValue(),
            color=self.selected_color(),
            background=self.selected_background(),
            background_opacity=self.opacity_slider.GetValue(),
            position=self.selected_position(),
            box_width_percent=self.width_slider.GetValue(),
            mode="typing" if self.mode_choice.GetSelection() == 2 else "",
            typing_sound=self.selected_typing_sound_path(),
            typing_volume=self.typing_volume_slider.GetValue(),
            typing_speed=self.typing_speed_slider.GetValue(),
            mixed_text=self.mixed_checkbox.GetValue(),
        )
        if self.mode_choice.GetSelection() == 2:
            needed = self.needed_typing_speed()
            if needed and needed > self.typing_speed_slider.GetMax():
                answer = wx.MessageBox(
                    "النص أطول من أن يظهر كاملا خلال الجزء المحدد حتى بأقصى سرعة كتابة.\nقلل النص أو زد مدة الجزء المحدد ثم أعد المحاولة.\n\nهل تريد إدراج النص رغم ذلك؟",
                    "تنبيه",
                    wx.YES_NO | wx.ICON_WARNING,
                )
                if answer != wx.YES:
                    return
        self._save_last_settings()
        if self.range_start_ctrl is not None:
            try:
                range_start = float(self.range_start_ctrl.GetValue().strip() or "0.0")
                range_end = float(self.range_end_ctrl.GetValue().strip() or "0.0")
            except ValueError:
                wx.MessageBox("أدخل أرقاما صحيحة للبداية والنهاية.", "بيانات ناقصة", wx.OK | wx.ICON_INFORMATION)
                return
            if range_end <= range_start:
                wx.MessageBox("النهاية يجب أن تكون بعد البداية.", "بيانات ناقصة", wx.OK | wx.ICON_INFORMATION)
                return
            self.range_start = range_start
            self.range_end = range_end
        self.finish_dialog(wx.ID_OK)

    def close(self, event=None):
        self.finish_dialog(wx.ID_CANCEL)

    def on_close(self, event):
        self.closed = True
        stop_typing_preview()
        if self._preview_later is not None:
            self._preview_later.Stop()
            self._preview_later = None
        self._stop_pending_speech()
        event.Skip()

    def finish_dialog(self, result):
        self.closed = True
        stop_typing_preview()
        if self._preview_later is not None:
            self._preview_later.Stop()
            self._preview_later = None
        self._stop_pending_speech()
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
