import os
import re
import subprocess

from video_maker.localization import current_language, tr


VIDEO_SIZE_PRESETS = [
    {"key": "original", "size": None, "shape": "", "ratio": None, "quality": ""},
    {"key": "landscape_4k", "size": (3840, 2160), "shape": "أفقي", "ratio": (16, 9), "quality": "4K"},
    {"key": "landscape_qhd", "size": (2560, 1440), "shape": "أفقي", "ratio": (16, 9), "quality": "QHD"},
    {"key": "landscape_fhd", "size": (1920, 1080), "shape": "أفقي", "ratio": (16, 9), "quality": "Full HD"},
    {"key": "landscape_hd", "size": (1280, 720), "shape": "أفقي", "ratio": (16, 9), "quality": "HD"},
    {"key": "landscape_sd", "size": (854, 480), "shape": "أفقي", "ratio": (16, 9), "quality": "SD"},
    {"key": "landscape_low", "size": (640, 360), "shape": "أفقي", "ratio": (16, 9), "quality": "جودة منخفضة"},
    {"key": "portrait_4k", "size": (2160, 3840), "shape": "رأسي", "ratio": (9, 16), "quality": "4K"},
    {"key": "portrait_qhd", "size": (1440, 2560), "shape": "رأسي", "ratio": (9, 16), "quality": "QHD"},
    {"key": "portrait_fhd", "size": (1080, 1920), "shape": "رأسي", "ratio": (9, 16), "quality": "Full HD"},
    {"key": "portrait_hd", "size": (720, 1280), "shape": "رأسي", "ratio": (9, 16), "quality": "HD"},
    {"key": "square_2160", "size": (2160, 2160), "shape": "مربع", "ratio": (1, 1), "quality": "2160p"},
    {"key": "square_1080", "size": (1080, 1080), "shape": "مربع", "ratio": (1, 1), "quality": "1080p"},
    {"key": "square_720", "size": (720, 720), "shape": "مربع", "ratio": (1, 1), "quality": "720p"},
    {"key": "feed_1080", "size": (1080, 1350), "shape": "رأسي للمنشورات", "ratio": (4, 5), "quality": "1080p"},
    {"key": "feed_720", "size": (720, 900), "shape": "رأسي للمنشورات", "ratio": (4, 5), "quality": "720p"},
    {"key": "portrait_3_4_1080", "size": (1080, 1440), "shape": "رأسي", "ratio": (3, 4), "quality": "1080p"},
    {"key": "portrait_3_4_720", "size": (720, 960), "shape": "رأسي", "ratio": (3, 4), "quality": "720p"},
    {"key": "landscape_4_3_1080", "size": (1440, 1080), "shape": "أفقي", "ratio": (4, 3), "quality": "1080p"},
    {"key": "landscape_4_3_xga", "size": (1024, 768), "shape": "أفقي", "ratio": (4, 3), "quality": "XGA"},
    {"key": "landscape_4_3_svga", "size": (800, 600), "shape": "أفقي", "ratio": (4, 3), "quality": "SVGA"},
    {"key": "landscape_4_3_vga", "size": (640, 480), "shape": "أفقي", "ratio": (4, 3), "quality": "VGA"},
    {"key": "cinematic_3440", "size": (3440, 1440), "shape": "سينمائي عريض", "ratio": (21, 9), "quality": "UWQHD"},
    {"key": "cinematic_2560", "size": (2560, 1080), "shape": "سينمائي عريض", "ratio": (21, 9), "quality": "UltraWide Full HD"},
    {"key": "cinematic_1920", "size": (1920, 804), "shape": "سينمائي عريض", "ratio": None, "quality": "Full HD"},
]


AUDIO_FORMATS = [
    {
        "key": "mp3", "extension": ".mp3", "label": "MP3", "codec": "libmp3lame",
        "qualities": [(str(value), value) for value in (32, 40, 48, 56, 64, 96, 128, 192, 256, 320)],
        "default_quality": "128",
    },
    {
        "key": "m4a", "extension": ".m4a", "label": "M4A (AAC)", "codec": "aac",
        "qualities": [(str(value), value) for value in (64, 96, 128, 192, 256, 320)],
        "default_quality": "128",
    },
    {
        "key": "aac", "extension": ".aac", "label": "AAC", "codec": "aac",
        "qualities": [(str(value), value) for value in (64, 96, 128, 192, 256, 320)],
        "default_quality": "128",
    },
    {
        "key": "wav", "extension": ".wav", "label": "WAV", "codec": "pcm_s16le",
        "qualities": [("pcm16", None), ("pcm24", None)], "default_quality": "pcm16",
    },
    {
        "key": "flac", "extension": ".flac", "label": "FLAC", "codec": "flac",
        "qualities": [("lossless16", None), ("lossless24", None)], "default_quality": "lossless16",
    },
    {
        "key": "ogg", "extension": ".ogg", "label": "OGG Vorbis", "codec": "libvorbis",
        "qualities": [(str(value), value) for value in (64, 96, 128, 160, 192, 224)],
        "default_quality": "128",
    },
    {
        "key": "opus", "extension": ".opus", "label": "Opus", "codec": "libopus",
        "qualities": [(str(value), value) for value in (32, 48, 64, 96, 128, 160, 192, 256)],
        "default_quality": "128",
    },
    {
        "key": "wma", "extension": ".wma", "label": "WMA", "codec": "wmav2",
        "qualities": [(str(value), value) for value in (64, 96, 128, 192, 256, 320)],
        "default_quality": "128",
    },
    {
        "key": "aiff", "extension": ".aiff", "label": "AIFF", "codec": "pcm_s16be",
        "qualities": [("pcm16", None), ("pcm24", None)], "default_quality": "pcm16",
    },
]


VIDEO_FORMATS = [
    {"key": "mp4", "extension": ".mp4", "label": "MP4 (H.264 + AAC)", "video_codec": "libx264", "audio_codec": "aac"},
    {"key": "mkv", "extension": ".mkv", "label": "MKV (H.264 + AAC)", "video_codec": "libx264", "audio_codec": "aac"},
    {"key": "mov", "extension": ".mov", "label": "MOV (H.264 + AAC)", "video_codec": "libx264", "audio_codec": "aac"},
    {"key": "m4v", "extension": ".m4v", "label": "M4V (H.264 + AAC)", "video_codec": "libx264", "audio_codec": "aac"},
    {"key": "webm", "extension": ".webm", "label": "WebM (VP9 + Opus)", "video_codec": "libvpx-vp9", "audio_codec": "libopus"},
    {"key": "avi", "extension": ".avi", "label": "AVI (MPEG-4 + MP3)", "video_codec": "mpeg4", "audio_codec": "libmp3lame"},
    {"key": "wmv", "extension": ".wmv", "label": "WMV (WMV2 + WMA)", "video_codec": "wmv2", "audio_codec": "wmav2"},
    {"key": "mpg", "extension": ".mpg", "label": "MPEG (MPEG-2 + MP2)", "video_codec": "mpeg2video", "audio_codec": "mp2"},
]


VIDEO_QUALITY_PRESETS = [
    {"key": "original", "bitrate": None, "label": "الجودة الأصلية"},
    {"key": "compact", "bitrate": 1500, "label": "جودة اقتصادية"},
    {"key": "standard", "bitrate": 4000, "label": "جودة قياسية"},
    {"key": "high", "bitrate": 8000, "label": "جودة عالية"},
    {"key": "very_high", "bitrate": 16000, "label": "جودة عالية جدا"},
    {"key": "maximum", "bitrate": 30000, "label": "أقصى جودة"},
]


AUDIO_CHANNEL_MODES = [
    {"key": "mono", "channels": 1, "label": "أحادي القناة مونو"},
    {"key": "stereo", "channels": 2, "label": "ثنائي القناة استريو"},
]


def _ffmpeg_binary():
    try:
        from video_maker.app_paths import ffmpeg_binary
        return ffmpeg_binary()
    except Exception:
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return "ffmpeg"


def probe_source_profile(path):
    profile = {
        "path": path or "",
        "extension": os.path.splitext(path or "")[1].lower(),
        "width": None,
        "height": None,
        "video_bitrate": None,
        "audio_bitrate": None,
        "channels": None,
        "sample_rate": None,
        "fps": None,
    }
    if not path or not os.path.exists(path):
        return profile
    try:
        process = subprocess.run(
            [_ffmpeg_binary(), "-hide_banner", "-i", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=20,
        )
        output = process.stderr.decode("utf-8", errors="ignore")
    except Exception:
        return profile
    video_line = next((line for line in output.splitlines() if " Video: " in line), "")
    audio_line = next((line for line in output.splitlines() if " Audio: " in line), "")
    size_match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    if size_match:
        profile["width"] = int(size_match.group(1))
        profile["height"] = int(size_match.group(2))
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", video_line)
    if fps_match:
        profile["fps"] = float(fps_match.group(1))
    video_bitrate_match = re.search(r"(\d+)\s*kb/s", video_line)
    if video_bitrate_match:
        profile["video_bitrate"] = int(video_bitrate_match.group(1))
    audio_bitrate_match = re.search(r"(\d+)\s*kb/s", audio_line)
    if audio_bitrate_match:
        profile["audio_bitrate"] = int(audio_bitrate_match.group(1))
    sample_match = re.search(r"(\d+)\s*Hz", audio_line)
    if sample_match:
        profile["sample_rate"] = int(sample_match.group(1))
    lower_audio = audio_line.lower()
    if "mono" in lower_audio:
        profile["channels"] = 1
    elif "stereo" in lower_audio:
        profile["channels"] = 2
    return profile


def ratio_text(ratio, language=None):
    if not ratio:
        return ""
    language = language or current_language()
    first, second = ratio
    if language == "ar":
        return f"{first} إلى {second}"
    if language == "fr":
        return f"{first} sur {second}"
    return f"{first} by {second}"


def dimensions_text(size, language=None):
    if not size:
        return ""
    language = language or current_language()
    width, height = size
    if language == "ar":
        return f"{width} في {height}"
    if language == "fr":
        return f"{width} par {height}"
    return f"{width} by {height}"


def video_size_label(preset, source_profile=None):
    if preset["key"] == "original":
        label = tr("الحفاظ على الأبعاد الأصلية")
        width = (source_profile or {}).get("width")
        height = (source_profile or {}).get("height")
        if width and height:
            return f"{label} — {dimensions_text((width, height))}"
        return label
    parts = [tr(preset["shape"])]
    ratio = ratio_text(preset["ratio"])
    if ratio:
        parts.append(ratio)
    parts.append(dimensions_text(preset["size"]))
    if preset["quality"]:
        parts.append(tr(preset["quality"]))
    return " — ".join(parts)


def bitrate_label(kbps):
    language = current_language()
    if language == "ar":
        return f"{kbps} كيلوبت في الثانية"
    if language == "fr":
        return f"{kbps} kbit/s"
    return f"{kbps} kbps"


def audio_quality_label(quality_key):
    if str(quality_key).isdigit():
        return bitrate_label(int(quality_key))
    labels = {
        "pcm16": "PCM 16 بت",
        "pcm24": "PCM 24 بت",
        "lossless16": "ضغط بلا فقد 16 بت",
        "lossless24": "ضغط بلا فقد 24 بت",
    }
    return tr(labels.get(quality_key, quality_key))


def video_quality_label(preset, source_profile=None):
    label = tr(preset["label"])
    if preset["key"] == "original":
        bitrate = (source_profile or {}).get("video_bitrate")
        if bitrate:
            return f"{label} — {bitrate_label(bitrate)}"
    elif preset.get("bitrate"):
        return f"{label} — {bitrate_label(preset['bitrate'])}"
    return label


def format_by_key(formats, key):
    return next((item for item in formats if item["key"] == key), formats[0])


def preset_by_key(key):
    return next((preset for preset in VIDEO_SIZE_PRESETS if preset["key"] == key), VIDEO_SIZE_PRESETS[0])


def video_quality_by_key(key):
    return next((preset for preset in VIDEO_QUALITY_PRESETS if preset["key"] == key), VIDEO_QUALITY_PRESETS[0])


def format_key_for_extension(formats, extension, fallback):
    extension = (extension or "").lower()
    match = next((item for item in formats if item["extension"] == extension), None)
    return match["key"] if match else fallback


def audio_format_settings(format_key, quality_key):
    output_format = format_by_key(AUDIO_FORMATS, format_key)
    quality_key = quality_key or output_format["default_quality"]
    codec = output_format["codec"]
    bitrate = None
    nbytes = 2
    ffmpeg_params = []
    if str(quality_key).isdigit():
        bitrate = f"{int(quality_key)}k"
    elif quality_key in ("pcm24", "lossless24"):
        nbytes = 4
        if output_format["key"] == "wav":
            codec = "pcm_s24le"
        elif output_format["key"] == "aiff":
            codec = "pcm_s24be"
        elif output_format["key"] == "flac":
            ffmpeg_params.extend(["-sample_fmt", "s32"])
    elif output_format["key"] == "flac":
        ffmpeg_params.extend(["-sample_fmt", "s16"])
    return {
        "audio_codec": codec,
        "audio_bitrate": bitrate,
        "audio_nbytes": nbytes,
        "audio_ffmpeg_params": ffmpeg_params,
    }


def normalized_save_options(
    preset_key="original",
    placement="fit",
    media_kind="video",
    format_key=None,
    quality_key=None,
    channel_key=None,
    source_profile=None,
):
    source_profile = dict(source_profile or {})
    if media_kind == "audio":
        format_key = format_key or "mp3"
        output_format = format_by_key(AUDIO_FORMATS, format_key)
        quality_key = quality_key or output_format["default_quality"]
        channel_key = channel_key or "stereo"
        channel_mode = next((mode for mode in AUDIO_CHANNEL_MODES if mode["key"] == channel_key), AUDIO_CHANNEL_MODES[1])
        settings = audio_format_settings(output_format["key"], quality_key)
        return {
            "media_kind": "audio",
            "format": output_format["key"],
            "extension": output_format["extension"],
            "quality": quality_key,
            "audio_channels": channel_mode["channels"],
            "source_profile": source_profile,
            **settings,
        }

    source_extension = source_profile.get("extension", "")
    format_key = format_key or format_key_for_extension(VIDEO_FORMATS, source_extension, "mp4")
    output_format = format_by_key(VIDEO_FORMATS, format_key)
    quality = video_quality_by_key(quality_key or "original")
    preset = preset_by_key(preset_key)
    return {
        "media_kind": "video",
        "format": output_format["key"],
        "extension": output_format["extension"],
        "video_codec": output_format["video_codec"],
        "audio_codec": output_format["audio_codec"],
        "video_quality": quality["key"],
        "video_bitrate": quality["bitrate"],
        "preset": preset["key"],
        "size": preset["size"],
        "placement": placement if preset["size"] else "original",
        "source_profile": source_profile,
    }
