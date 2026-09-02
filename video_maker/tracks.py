from video_maker.localization import tr


MAIN_VIDEO_TRACK = "main_video"
SECONDARY_VIDEO_TRACK = "secondary_video"
SOUND_EFFECTS_TRACK = "sound_effects"
BACKGROUND_AUDIO_TRACK = "background_audio"
TEXT_TRACK = "text"

DEFAULT_TRACK = MAIN_VIDEO_TRACK

TRACKS = [
    {"key": MAIN_VIDEO_TRACK, "label": "المقطع الرئيسي", "ordinal": "الأول", "media_types": ["video"], "channel": "timeline"},
    {"key": SECONDARY_VIDEO_TRACK, "label": "المقطع الثانوي", "ordinal": "الثاني", "media_types": ["video", "image"], "channel": "b_roll"},
    {"key": SOUND_EFFECTS_TRACK, "label": "المؤثرات الصوتية", "ordinal": "الثالث", "media_types": ["audio"], "channel": "sound_effects"},
    {"key": BACKGROUND_AUDIO_TRACK, "label": "الخلفية الصوتية", "ordinal": "الرابع", "media_types": ["audio"], "channel": "background_audio"},
    {"key": TEXT_TRACK, "label": "النصوص", "ordinal": "الخامس", "media_types": ["text"], "channel": "visual_text"},
]


def normalize_track(value):
    keys = {track["key"] for track in TRACKS}
    return value if value in keys else DEFAULT_TRACK


def track_index(track):
    for index, item in enumerate(TRACKS):
        if item["key"] == normalize_track(track):
            return index
    return 0


def track_at(index):
    try:
        return TRACKS[int(index)]["key"]
    except (IndexError, TypeError, ValueError):
        return DEFAULT_TRACK


def next_track(track):
    return track_at(track_index(track) + 1)


def previous_track(track):
    return track_at(track_index(track) - 1)


def track_label(track):
    for item in TRACKS:
        if item["key"] == normalize_track(track):
            return item["label"]
    return TRACKS[0]["label"]


def track_ordinal(track):
    for item in TRACKS:
        if item["key"] == normalize_track(track):
            return item["ordinal"]
    return TRACKS[0]["ordinal"]


def track_media_types(track):
    for item in TRACKS:
        if item["key"] == normalize_track(track):
            return list(item.get("media_types") or [])
    return ["video"]


def track_media_type(track):
    """سلوك قديم: يعيد أول نوع مقبول للتراك حتى لا نكسر المستدعين."""
    types = track_media_types(track)
    return types[0] if types else "video"


def track_channel(track):
    for item in TRACKS:
        if item["key"] == normalize_track(track):
            return item["channel"]
    return "timeline"


def tracks_accepting(media_type):
    return [item["key"] for item in TRACKS if media_type in (item.get("media_types") or [])]


def tracks_accepting_labels(media_type):
    return [tr(item["label"]) for item in TRACKS if media_type in (item.get("media_types") or [])]


TRACK_VOLUME_TRACKS = (MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK, SOUND_EFFECTS_TRACK, BACKGROUND_AUDIO_TRACK)


def track_has_volume(track):
    """هل التراك من التراكات الأربعة القابلة للتحكم بمستوى صوت مستقل؟"""
    return normalize_track(track) in TRACK_VOLUME_TRACKS
