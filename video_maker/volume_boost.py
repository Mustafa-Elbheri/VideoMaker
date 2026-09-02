NORMAL_OUTPUT_VOLUME = 1.0
BOOST_START_VOLUME = 1.05
BOOSTED_OUTPUT_VOLUME = 4.0
VOLUME_STEP = 0.05
VOLUME_EPSILON = 0.000001

MASTER_VOLUME_STEP_DB = 1.0
DEFAULT_MASTER_VOLUME_DB = 0.0
MIN_MASTER_VOLUME_DB = -60.0
MAX_MASTER_VOLUME_DB = 0.0
MASTER_VOLUME_DB_EPSILON = 0.0001


def normalized_program_volume(value, default=NORMAL_OUTPUT_VOLUME):
    try:
        volume = float(value)
    except (TypeError, ValueError):
        volume = float(default)
    if volume > BOOSTED_OUTPUT_VOLUME:
        volume /= 100.0
    return max(0.0, min(BOOSTED_OUTPUT_VOLUME, volume))


def persisted_program_volume(value, default=NORMAL_OUTPUT_VOLUME):
    return min(NORMAL_OUTPUT_VOLUME, normalized_program_volume(value, default))


def normal_volume_up(value):
    """رفع المستوى العادي داخل 0%..100% فقط.

    إذا كان البرنامج في نطاق التضخيم فلا تغيّر الأسهم العادية المستوى مطلقًا.
    """
    volume = normalized_program_volume(value)
    if volume > NORMAL_OUTPUT_VOLUME + VOLUME_EPSILON:
        return volume
    return min(NORMAL_OUTPUT_VOLUME, volume + VOLUME_STEP)


def volume_down(value):
    """خفض المستوى العادي داخل 0%..100% فقط دون لمس نطاق التضخيم."""
    volume = normalized_program_volume(value)
    if volume > NORMAL_OUTPUT_VOLUME + VOLUME_EPSILON:
        return volume
    return max(0.0, volume - VOLUME_STEP)


def boosted_volume_up(value):
    """رفع نطاق التضخيم باستخدام Shift+Up فقط.

    أول ضغطة من أي مستوى عادي تنقل مباشرة إلى 105%، ثم تزيد بخطوات 5%.
    """
    volume = normalized_program_volume(value)
    if volume < BOOST_START_VOLUME - VOLUME_EPSILON:
        return BOOST_START_VOLUME
    return min(BOOSTED_OUTPUT_VOLUME, volume + VOLUME_STEP)


def boosted_volume_down(value):
    """خفض نطاق التضخيم باستخدام Shift+Down فقط.

    لا تعمل داخل النطاق العادي. ومن 105% تعود إلى 100% لإنهاء التضخيم.
    """
    volume = normalized_program_volume(value)
    if volume < BOOST_START_VOLUME - VOLUME_EPSILON:
        return volume
    if volume <= BOOST_START_VOLUME + VOLUME_EPSILON:
        return NORMAL_OUTPUT_VOLUME
    return max(BOOST_START_VOLUME, volume - VOLUME_STEP)


def volume_changed(old_value, new_value):
    """هل نتج تغير حقيقي يستدعي تحديث المشغلات والنطق؟"""
    return abs(normalized_program_volume(old_value) - normalized_program_volume(new_value)) > VOLUME_EPSILON


def volume_percent(value):
    return int(round(normalized_program_volume(value) * 100))


def device_volume(value):
    return max(0.0, min(NORMAL_OUTPUT_VOLUME, normalized_program_volume(value)))


def export_volume_multiplier_from_options(save_options):
    return normalized_program_volume((save_options or {}).get("output_volume", NORMAL_OUTPUT_VOLUME))


def save_options_with_output_volume(save_options, volume):
    options = dict(save_options or {})
    output_volume = normalized_program_volume(volume)
    if output_volume > NORMAL_OUTPUT_VOLUME + 0.001:
        options["output_volume"] = output_volume
    else:
        options.pop("output_volume", None)
    return options or None


def save_options_with_master_volume(save_options, db_value):
    """تسجيل مستوى الماستر (dB) في خيارات التصدير كمرحلة صوت نهائية للمشروع."""
    options = dict(save_options or {})
    db = normalized_master_volume_db(db_value)
    if abs(db) > MASTER_VOLUME_DB_EPSILON:
        options["master_volume_db"] = db
    else:
        options.pop("master_volume_db", None)
    return options or None


def export_master_multiplier_from_options(save_options):
    """المضاعف الخطي لمستوى الماستر المسجل في خيارات التصدير."""
    return master_db_to_linear((save_options or {}).get("master_volume_db", 0.0))


def normalized_master_volume_db(value, default=DEFAULT_MASTER_VOLUME_DB):
    """ضبط قيمة مستوى الماستر بالديسيبل داخل النطاق المسموح."""
    try:
        db = float(value)
    except (TypeError, ValueError):
        db = float(default)
    return max(MIN_MASTER_VOLUME_DB, min(MAX_MASTER_VOLUME_DB, db))


def persisted_master_volume_db(value, default=DEFAULT_MASTER_VOLUME_DB):
    return normalized_master_volume_db(value, default)


def master_volume_up_db(value):
    """رفع مستوى الماستر بخطوة ديسيبل واحدة."""
    return normalized_master_volume_db(normalized_master_volume_db(value) + MASTER_VOLUME_STEP_DB)


def master_volume_down_db(value):
    """خفض مستوى الماستر بخطوة ديسيبل واحدة."""
    return normalized_master_volume_db(normalized_master_volume_db(value) - MASTER_VOLUME_STEP_DB)


def master_volume_db_changed(old_value, new_value):
    return abs(normalized_master_volume_db(old_value) - normalized_master_volume_db(new_value)) > MASTER_VOLUME_DB_EPSILON


def master_db_to_linear(db_value):
    """تحويل الديسيبل إلى مضاعف خطي (10^(dB/20))."""
    return 10 ** (normalized_master_volume_db(db_value) / 20.0)


def master_linear_into_volume(volume, db_value):
    """دمج مستوى الماستر (dB) مع المستوى الحالي دون تجاوز نطاق التضخيم."""
    return max(
        0.0,
        min(BOOSTED_OUTPUT_VOLUME, normalized_program_volume(volume) * master_db_to_linear(db_value)),
    )


def format_master_db(value):
    return "{0:g}".format(normalized_master_volume_db(value))


def format_track_db(value):
    """تنسيق مستوى التراك بالديسيبل للأرقام داخل النطاق المسموح."""
    return "{0:g}".format(normalized_track_volume_db(value))


TRACK_VOLUME_STEP_DB = 1.0
DEFAULT_TRACK_VOLUME_DB = 0.0
MIN_TRACK_VOLUME_DB = -60.0
MAX_TRACK_VOLUME_DB = 20.0
TRACK_VOLUME_DB_EPSILON = 0.0001


def normalized_track_volume_db(value, default=DEFAULT_TRACK_VOLUME_DB):
    """ضبط قيمة مستوى التراك بالديسيبل داخل النطاق المسموح."""
    try:
        db = float(value)
    except (TypeError, ValueError):
        db = float(default)
    return max(MIN_TRACK_VOLUME_DB, min(MAX_TRACK_VOLUME_DB, db))


def persisted_track_volume_db(value, default=DEFAULT_TRACK_VOLUME_DB):
    return normalized_track_volume_db(value, default)


def track_volume_up_db(value):
    """رفع مستوى التراك بخطوة ديسيبل واحدة."""
    return normalized_track_volume_db(normalized_track_volume_db(value) + TRACK_VOLUME_STEP_DB)


def track_volume_down_db(value):
    """خفض مستوى التراك بخطوة ديسيبل واحدة."""
    return normalized_track_volume_db(normalized_track_volume_db(value) - TRACK_VOLUME_STEP_DB)


def track_volume_db_changed(old_value, new_value):
    return abs(normalized_track_volume_db(old_value) - normalized_track_volume_db(new_value)) > TRACK_VOLUME_DB_EPSILON


def track_db_to_linear(db_value):
    """تحويل الديسيبل إلى مضاعف خطي (10^(dB/20))."""
    return 10 ** (normalized_track_volume_db(db_value) / 20.0)


def track_volume_gain(track, track_volumes_db=None):
    """المضاعف الخطي لمستوى التراك المسجل (dB)؛ 0dB يعطي 1.0.

    `track_volumes_db` قاموس {track_key: db} والمستوى الافتراضي 0dB.
    """
    db = 0.0
    if track_volumes_db:
        db = normalized_track_volume_db(track_volumes_db.get(track, DEFAULT_TRACK_VOLUME_DB))
    return track_db_to_linear(db)


def save_options_with_track_volumes(save_options, track_volumes_db):
    """تسجيل مستويات التراكات (dB) في خيارات التصدير كمرحلة خلط لكل تراك."""
    options = dict(save_options or {})
    valid = {}
    for key, db in (track_volumes_db or {}).items():
        db = normalized_track_volume_db(db)
        if abs(db) > TRACK_VOLUME_DB_EPSILON:
            valid[str(key)] = db
    if valid:
        options["track_volumes_db"] = valid
    else:
        options.pop("track_volumes_db", None)
    return options or None
