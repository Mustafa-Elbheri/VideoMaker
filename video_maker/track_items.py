import copy
import os
import uuid
from dataclasses import replace

from video_maker.timeline import slice_segments
from video_maker.video_editing import get_media_duration


def _item_bounds(item):
    if isinstance(item, dict):
        return float(item.get("start", 0.0) or 0.0), float(item.get("end", 0.0) or 0.0)
    return float(getattr(item, "start", 0.0) or 0.0), float(getattr(item, "end", 0.0) or 0.0)


def item_bounds(item):
    """يعيد (start, end) لعنصر dict أو TimelineSegment."""
    return _item_bounds(item)


def should_ripple(ripple_mode):
    """يعيد True إذا كان وضع Ripple نشطاً (per_track أو all_tracks) وليس off."""
    return ripple_mode != "off"


def ripple_shift(panels, from_time, delta, ripple_mode="per_track"):
    """يُزيح عناصر dict بعد from_time بمقدار delta داخل كل لوحة (تعديل في مكانها).

    - `panels` dict يربط مفتاح تراك بقائمة عناصر dict القابلة للتعديل.
    - لكل عنصر حيث `start >= from_time` (أو `end > from_time`) ينزاح `start` و`end`
      بمقدار `delta` مع الاحتفاظ بحدود المدة.
    - عند `ripple_mode == "off"` لا تنفذ شيئاً.
    - تُرجع قائمة توصيف تعديلات لأغراض التصحيح فقط (لا تُلتقط للتراجع).
    """
    from_time = float(from_time)
    delta = float(delta)
    if not should_ripple(ripple_mode) or delta == 0:
        return []
    modifications = []
    for track_key, items in (panels or {}).items():
        for item in items or ():
            start, end = _item_bounds(item)
            if start >= from_time or end > from_time:
                item["start"] = start + delta
                item["end"] = end + delta
                modifications.append((track_key, start, end, item["start"], item["end"]))
    return modifications


def ripple_shift_segments(segments, from_time, delta, ripple_mode="per_track"):
    """نسخة الفيديو الرئيسي من ripple_shift لعناصر TimelineSegment.

    تُنفَّذ فقط عند `ripple_mode != "off"`، وتعيد قائمة جديدة يُنقل فيها كل مقطع
    يبدأ عند from_time أو بعده عبر deepcopy مع تحديث `start`/`end`.
    """
    from_time = float(from_time)
    delta = float(delta)
    if not should_ripple(ripple_mode) or delta == 0:
        return list(segments)
    shifted = []
    for segment in segments or ():
        if float(getattr(segment, "start", 0.0) or 0.0) >= from_time:
            shifted.append(
                replace(segment, start=float(segment.start) + delta, end=float(segment.end) + delta)
            )
        else:
            shifted.append(segment)
    return shifted


def item_at_time(items, time, tolerance=0.08):
    """يعيد العنصر الذي `start <= time < end` على أي تراك، أو None."""
    time = float(time)
    for item in reversed(list(items or ())):
        start, end = _item_bounds(item)
        if start <= time < end:
            return item
    return None


def split_item(item, at_time):
    """يقسم عنصر dict إلى (left, right) عند الزمن المعطى.

    - `left` نسخة من العنصر تنتهي عند at_time.
    - `right` نسخة تبدأ عند at_time مع تصحيح source_offset حسب السرعة.
    - كل جزء يأخذ معرّفاً جديداً حتى تبقى القطع مميزة داخل التحديد والتنقل
      (بدون ذلك تتعامل الدوال مع كل القطع كعنصر واحد).
    - القطع ترث `source_id` من العنصر الأصلي فيُعرف أصلها ويُرقّم باسمه.
    """
    at_time = float(at_time)
    left = copy.deepcopy(item)
    left["end"] = at_time
    right = copy.deepcopy(item)
    right["start"] = at_time
    speed = float(item.get("speed", 1.0) or 1.0)
    right["source_offset"] = float(item.get("source_offset", 0.0) or 0.0) + (at_time - float(item["start"])) * speed
    if isinstance(item, dict):
        source_id = str(item.get("source_id") or item.get("id") or uuid.uuid4().hex)
        left["source_id"] = source_id
        right["source_id"] = source_id
        if item.get("id"):
            left["id"] = uuid.uuid4().hex
            right["id"] = uuid.uuid4().hex
    return left, right


def mute_timed_audio_items_range(items, start_time, end_time):
    start_time = max(0.0, float(start_time))
    end_time = max(start_time, float(end_time))
    updated = []
    changed = False
    touched = 0
    for item in items or ():
        if not isinstance(item, dict):
            updated.append(item)
            continue
        item_start, item_end = item_bounds(item)
        overlap_start = max(item_start, start_time)
        overlap_end = min(item_end, end_time)
        if overlap_end <= overlap_start:
            updated.append(dict(item))
            continue

        speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
        source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
        spans = []
        if item_start < overlap_start:
            spans.append((item_start, overlap_start, False))
        spans.append((overlap_start, overlap_end, True))
        if overlap_end < item_end:
            spans.append((overlap_end, item_end, False))

        source_id = str(item.get("source_id") or item.get("id") or uuid.uuid4().hex)
        for piece_start, piece_end, muted in spans:
            if piece_end <= piece_start:
                continue
            piece = dict(item)
            piece["start"] = piece_start
            piece["end"] = piece_end
            piece["source_offset"] = source_offset + max(0.0, piece_start - item_start) * speed
            if len(spans) > 1:
                piece["source_id"] = source_id
                if item.get("id"):
                    piece["id"] = uuid.uuid4().hex
            if muted:
                touched += 1
                volume = float(piece.get("volume", 1.0) if piece.get("volume") is not None else 1.0)
                if volume > 0.001:
                    changed = True
                piece["volume"] = 0.0
            updated.append(piece)
    return updated, changed, touched


def split_timeline_segment(segments, segment_index, at_time):
    """يقسم المقطع في index إلى (left, right) عند زمن خط زمني بدقة المصدر.

    يستخدم slice_segments من timeline.py على المقطع وحده، ويعيد قائمتين
    (يسار/يمين) للاستبدال في موضع المقطع الأصلي.
    """
    segment_index = int(segment_index)
    segment = segments[segment_index]
    position = sum(float(s.duration) for s in segments[:segment_index])
    local_time = max(0.0, min(float(at_time) - position, float(segment.duration)))
    left = slice_segments([segment], 0, local_time)
    right = slice_segments([segment], local_time, float(segment.duration))
    return left, right


def natural_span(path):
    """يعيد مدة الملف الطبيعية عبر get_media_duration؛ 0.0 عند أي فشل."""
    try:
        return float(get_media_duration(path))
    except Exception:
        return 0.0


def default_duration_for(media_type, natural, default_image_duration=5.0):
    """مدة عنصر افتراضية: المدة الطبيعية إن وُجدت، وإلا default_image_duration (للصور)."""
    natural = float(natural or 0.0)
    if natural > 0:
        return natural
    return max(0.0, float(default_image_duration))


def new_dynamic_text_item(text_options, start, end):
    """عنصر نص ديناميكي موحد يُستخدم في التراك النصي (الخطوة 05)."""
    from video_maker.text_overlay import serialize_text_options

    return {
        "id": uuid.uuid4().hex,
        "type": "text",
        "path": "",
        "start": float(start),
        "end": float(end),
        "options": serialize_text_options(text_options),
        "is_dynamic": True,
    }


def from_grok_caption(segment, options=None):
    """يحوّل شريحة ترجمة (dict أو SubtitleSegment) إلى عنصر نص ديناميكي (الخطوة 09)."""
    if isinstance(segment, dict):
        text = str(segment.get("text", "") or "")
        start = float(segment.get("start", 0.0) or 0.0)
        end = float(segment.get("end", 0.0) or 0.0)
    else:
        text = str(getattr(segment, "text", "") or "")
        start = float(getattr(segment, "start", 0.0) or 0.0)
        end = float(getattr(segment, "end", 0.0) or 0.0)

    if end < start:
        end = start

    item = new_dynamic_text_item(options or {}, start, end)
    item["options"]["text"] = text
    return item


def build_text_segments(items, start_key="start", end_key="end"):
    """يعيد قائمة فترات نصية مرتبة من عناصر is_dynamic على التراك النصي.

    كل إدخال: {"start", "end", "options", "layer"} مرتباً بزمن البداية ثم النهاية.
    `layer` ترتيب الظهور عند التقاطع (يبدأ من 0): أول عنصر متقاطع يرث 0، ثم
    تتزايد الطبقة لكل عنصر لاحق يبدأ قبل انتهاء سلفه.
    """
    from video_maker.text_overlay import deserialize_text_options

    segments = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if not item.get("is_dynamic"):
            continue
        start = float(item.get(start_key, 0.0) or 0.0)
        end = float(item.get(end_key, 0.0) or 0.0)
        if end <= start:
            continue
        options = deserialize_text_options(item.get("options"))
        if not options.get("text"):
            continue
        segments.append({"start": start, "end": end, "options": options, "layer": 0})
    segments.sort(key=lambda segment: (segment["start"], segment["end"]))
    for index in range(1, len(segments)):
        previous = segments[index - 1]
        current = segments[index]
        if current["start"] < previous["end"]:
            current["layer"] = previous["layer"] + 1
    return segments


def element_identifier(item):
    """مفتاح مستقر يميّز عنصراً داخل تراك (id للـ dict أو مفتاح حقلي للمقطع الرئيسي).

    مقاطع الفيديو الرئيسي من نوع TimelineSegment بلا id، لذا نُشتق معرفاً
    من حقولها المميزة حتى تدخل في التحديد المتعدد والحافظة. نسخة dict من
    مقطع رئيسي (element_to_dict) تفتقد id أيضاً فتُشتق لها نفس المفتاح كي
    يطابق المقطع الحي عند التنقل والتحديد.
    """
    if isinstance(item, dict):
        item_id = str(item.get("id", "") or "")
        if item_id:
            return item_id
        path = str(item.get("path", "") or "")
        start = float(item.get("start", 0.0) or 0.0)
        end = float(item.get("end", 0.0) or 0.0)
        speed = float(item.get("speed", 1.0) or 1.0)
        return "main:{path}:{start}:{end}:{speed}".format(path=path, start=start, end=end, speed=speed)
    return "main:{path}:{start}:{end}:{speed}".format(
        path=str(getattr(item, "path", "") or ""),
        start=float(getattr(item, "start", 0.0) or 0.0),
        end=float(getattr(item, "end", 0.0) or 0.0),
        speed=float(getattr(item, "speed", 1.0) or 1.0),
    )


def element_origin_key(item):
    """مفتاح يشير إلى الأصل الذي انحدرت منه قطعة (القطع من قص واحد تشترك به)."""
    if isinstance(item, dict):
        return str(item.get("source_id") or item.get("id") or "")
    return str(getattr(item, "source_file_id", "") or "") or "file:{path}".format(
        path=str(getattr(item, "path", "") or "")
    )


def base_element_name(item, fallback="عنصر"):
    """الاسم الأساسي للعنصر من اسمه المباشر أو اسم ملفه أو مقتطف النص."""
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("source_file_name") or "")
        if not name and item.get("is_dynamic") and isinstance(item.get("options"), dict):
            snippet = str(item["options"].get("text", "") or "").strip()
            if snippet:
                name = snippet if len(snippet) <= 20 else snippet[:20]
        return name or (
            os.path.splitext(os.path.basename(str(item.get("path") or "")))[0] or fallback
        )
    return str(getattr(item, "source_file_name", "") or "") or (
        os.path.splitext(os.path.basename(str(getattr(item, "path", "") or "")))[0] or fallback
    )


def element_display_name(item, items):
    """اسم العنصر المعروض مع ترقيم القطع المنحدرة من الأصل نفسه.

    إذا كان للعنصر قطع شقيقة (قصّ سابق) يُعرض اسمه مسبوقاً برقم ترتيبه
    الزمني بينها، مثل: "2 - intro". أما العنصر غير المقصوص فيبقى باسمه فقط.
    """
    key = element_origin_key(item)
    if not key:
        return base_element_name(item)
    peers = [peer for peer in (items or ()) if element_origin_key(peer) == key]
    if len(peers) <= 1:
        return base_element_name(item)
    identity = element_identifier(item)
    ordered = sorted(peers, key=lambda peer: _item_bounds(peer)[0])
    for index, peer in enumerate(ordered, start=1):
        if element_identifier(peer) == identity:
            return "{index} - {base}".format(
                index=index,
                base=base_element_name(item),
            )
    return base_element_name(item)


def element_to_dict(item):
    """نسخة dict آمنة من أي عنصر (بما فيه TimelineSegment) لتخزينها في الحافظة."""
    if isinstance(item, dict):
        return copy.deepcopy(item)
    return {
        "path": str(getattr(item, "path", "") or ""),
        "start": float(getattr(item, "start", 0.0) or 0.0),
        "end": float(getattr(item, "end", 0.0) or 0.0),
        "speed": float(getattr(item, "speed", 1.0) or 1.0),
        "audio_volume": float(getattr(item, "audio_volume", 1.0) if getattr(item, "audio_volume", 1.0) is not None else 1.0),
        "audio_path": str(getattr(item, "audio_path", "") or ""),
        "audio_start": getattr(item, "audio_start", None),
        "navigation_group": str(getattr(item, "navigation_group", "") or ""),
        "source_file_id": str(getattr(item, "source_file_id", "") or ""),
        "source_file_name": str(getattr(item, "source_file_name", "") or ""),
        "transition": str(getattr(item, "transition", "") or ""),
        "transition_duration": float(getattr(item, "transition_duration", 1.0) or 1.0),
        "audio_fade_in": max(0.0, float(getattr(item, "audio_fade_in", 0.0) or 0.0)),
        "audio_fade_out": max(0.0, float(getattr(item, "audio_fade_out", 0.0) or 0.0)),
    }


def next_item_on_track(items, current_id, direction=1, bounds_fn=None):
    """يعيد العنصر التالي/السابق على التراك نفسه أو None إن كانت القائمة فارغة.

    - `direction` موجب: أول عنصر `start >= end` العنصر المُركز، أو None في النهاية.
    - `direction` سالب: آخر عنصر `end <= start` العنصر المُركز، أو None في البداية.
    - عند غياب العنصر المُركز (`current_id` فارغ أو غير موجود) يُعاد أول/آخر عنصر.
    - تُقبل الحدود المتطابقة تماماً (`start == end`) لأن القصّ يولّد قطعاً متجاورة.
    - `bounds_fn`: دالة اختيارية `(item) -> (start, end)` لحساب مواقع العناصر.
      عند غيابها تُستخدم `_item_bounds` (نطاقات ملف المصدر).
    """
    if bounds_fn is None:
        bounds_fn = _item_bounds
    items = list(items or ())
    if not items:
        return None
    current_id = str(current_id or "")
    current = None
    if current_id:
        for item in items:
            if element_identifier(item) == current_id:
                current = item
                break
    if current is None:
        return items[0] if direction >= 0 else items[-1]
    cstart, cend = bounds_fn(current)
    if direction >= 0:
        for item in items:
            start, _end = bounds_fn(item)
            if start >= cend - 1e-9:
                return item
        return None
    for item in reversed(items):
        _start, end = bounds_fn(item)
        if end <= cstart + 1e-9:
            return item
    return None


def previous_item_on_track(items, current_id, direction=-1, bounds_fn=None):
    """عكس next_item_on_track."""
    return next_item_on_track(items, current_id, -1, bounds_fn=bounds_fn)
    return next_item_on_track(items, current_id, -1, bounds_fn=bounds_fn)


def items_in_range(items, start, end):
    """يعيد قائمة العناصر المتقاطعة مع النطاق [start, end)."""
    start = float(start)
    end = float(end)
    result = []
    for item in items or ():
        istart, iend = _item_bounds(item)
        if istart < end and iend > start:
            result.append(item)
    return result


def apply_selection_to(items, selected_ids, start, end):
    """يوسّع التحديد بعناصر متقاطعة مع النطاق ويعيد مجموعة ids الجديدة.

    يحافظ على المحدد السابق ويضيف عناصر النطاق — تُستخدم لتحديد النطاقات
    في الواجهة ثم رسمها.
    """
    selected = set(selected_ids or ())
    for item in items_in_range(items, start, end):
        selected.add(element_identifier(item))
    return selected


def can_insert_media_type(track_media_types, item_type):
    """يعيد True إذا كان نوع العنصر مقبولاً على التراك حسب media_types.

    أنواع الصوت الفرعية (`sound_effect`/`background_audio`) تقبلها التراكات
    التي `media_types` فيها `audio`.
    """
    types = set(track_media_types or ())
    if item_type in types:
        return True
    return item_type in ("audio", "sound_effect", "background_audio") and "audio" in types


def muted_items(items, muted_tracks, track):
    """يستبعد عناصر التراك المكتوم من أي قائمة عمل (معاينة/تصدير)."""
    if track in (muted_tracks or ()):
        return []
    return list(items)


def is_track_audible(track, muted_tracks=None, solo_tracks=None):
    """يحدد هل التراك مسموع/مُضمَّن حسب منطق Solo/Mute القياسي.

    - عند وجود تراكات في `solo_tracks`، تُسمع فقط تراكات Solo
      (Solo يتجاوز Mute مهما كانت حالة الكتم).
    - وإلا تُستبعد التراكات المكتومة في `muted_tracks`.
    """
    solo = set(solo_tracks or ())
    if solo:
        return track in solo
    return track not in set(muted_tracks or ())


def audible_tracks(muted_tracks=None, solo_tracks=None):
    """يرجع قائمة مفاتيح التراكات المسموعة حالياً بالترتيب القياسي."""
    from video_maker.tracks import TRACKS

    solo = set(solo_tracks or ())
    if solo:
        return [track["key"] for track in TRACKS if track["key"] in solo]
    muted = set(muted_tracks or ())
    return [track["key"] for track in TRACKS if track["key"] not in muted]


def filter_audio_sources_for_export(background_audio_items, sound_effects_items, b_roll_items, muted_tracks=None, solo_tracks=None):
    """يطبق منطق Solo/Mute على قوائم مصادر الصوت كما يفعل write_timeline_video.

    يُرجع (background_audio_items, sound_effects_items, b_roll_items) بعد استبعاد
    التراكات المكتومة أو غير المضمنة في Solo. يُستخدم لحساب ما إذا كان الناتج
    سيحتوي مسار صوت قبل الفحص النهائي.
    """
    from video_maker.tracks import BACKGROUND_AUDIO_TRACK, SECONDARY_VIDEO_TRACK, SOUND_EFFECTS_TRACK

    if not is_track_audible(BACKGROUND_AUDIO_TRACK, muted_tracks, solo_tracks):
        background_audio_items = []
    if not is_track_audible(SOUND_EFFECTS_TRACK, muted_tracks, solo_tracks):
        sound_effects_items = []
    if not is_track_audible(SECONDARY_VIDEO_TRACK, muted_tracks, solo_tracks):
        b_roll_items = []
    return background_audio_items, sound_effects_items, b_roll_items


def build_preview_audio_mix(background_audio_items, sound_effects_items, muted_tracks, playhead, solo_tracks=None, b_roll_items=None):
    """يبني قنوات صوت المعاينة النشطة عند المؤشر (منطق خالص بلا wx).

    - العناصر على تراكات مكتومة (`muted_tracks`) تُستبعد نهائياً.
    - عند وجود تراكات في `solo_tracks` تُضمَّن تراكات Solo فقط وتُستبعد بقية التراكات.
    - تُحتفظ فقط بالعناصر النشطة عند playhead (`start <= playhead < end`)
      وملفها موجود فعلياً.
    - لكل عنصر تُحسب خريطته الزمنية داخل ملفه: موضع البداية في المصدر
      (`local_offset`) والمدة الفعالة المتبقية (`channel_duration`).
    - `b_roll_items` (الفيديو الثانوي) تُضمَّن كقناة على تراك SECONDARY_VIDEO_TRACK
      مع احترام نفس منطق Solo/Mute.

    تُرجع dict:
      {
        "playhead": float,
        "channels": [ {
            "track": BACKGROUND_AUDIO_TRACK | SOUND_EFFECTS_TRACK | SECONDARY_VIDEO_TRACK,
            "item": item, "path": str,
            "start": float, "end": float,
            "volume": float, "speed": float, "source_offset": float,
            "local_offset": float, "channel_duration": float,
        }, ... ],
      }
    """
    from video_maker.tracks import BACKGROUND_AUDIO_TRACK, SECONDARY_VIDEO_TRACK, SOUND_EFFECTS_TRACK

    playhead = max(0.0, float(playhead))
    channels = []
    for track, items in (
        (BACKGROUND_AUDIO_TRACK, background_audio_items or ()),
        (SOUND_EFFECTS_TRACK, sound_effects_items or ()),
        (SECONDARY_VIDEO_TRACK, b_roll_items or ()),
    ):
        if not is_track_audible(track, muted_tracks, solo_tracks):
            continue
        for item in items or ():
            start, end = _item_bounds(item)
            if not (start <= playhead < end):
                continue
            path = str(item.get("path", "") or "")
            if not path or not os.path.isfile(path):
                continue
            speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
            source_offset = max(0.0, float(item.get("source_offset", 0.0) or 0.0))
            volume = float(item.get("volume", 1.0) if item.get("volume") is not None else 1.0)
            channels.append(
                {
                    "track": track,
                    "item": item,
                    "path": path,
                    "start": start,
                    "end": end,
                    "volume": volume,
                    "speed": speed,
                    "source_offset": source_offset,
                    "local_offset": source_offset + max(0.0, playhead - start) * speed,
                    "channel_duration": max(0.0, end - playhead),
                }
            )
    return {"playhead": playhead, "channels": channels}


def insert_at_playhead(items, item, playhead, ripple_mode="per_track", panels=None):
    """يُدرج عنصراً عند المؤشر مع احترام وضع Ripple (تعديل في مكانها).

    - `off`: يقسّم المتعارض داخل القائمة دون أي إزاحة.
    - `per_track`: يُزيح عناصر القائمة بعد playhead بمقدار مدة العنصر.
    - `all_tracks`: يُزيح كل قوائم `panels` بعد playhead (أو القائمة نفسها
      إن لم يُمرَّر panels).
    يُعيد العنصر المُدرج (نسخة منقّحة بالزمن الصحيح).
    """
    playhead = max(0.0, float(playhead))
    inserted = copy.deepcopy(item)
    duration = max(0.0, float(item.get("end", 0.0) or 0.0) - float(item.get("start", 0.0) or 0.0))
    inserted["start"] = playhead
    inserted["end"] = playhead + duration
    conflict = item_at_time(items, playhead)
    if conflict is not None:
        start, end = _item_bounds(conflict)
        if playhead - start > 0.05 and end - playhead > 0.05:
            index = items.index(conflict)
            left, right = split_item(conflict, playhead)
            items[index:index + 1] = [left, right]
    if should_ripple(ripple_mode):
        sources = panels if (ripple_mode == "all_tracks" and panels) else {id(items): items}
        ripple_shift(sources, playhead, duration, ripple_mode)
    index = len(items)
    for existing_index, existing in enumerate(items):
        if float(existing.get("start", 0.0) or 0.0) >= playhead:
            index = existing_index
            break
    items.insert(index, inserted)
    return inserted


def text_preview_fingerprint(items):
    """بصمة موحدة لعناصر النصوص الديناميكية على التراك النصي.

    يبني سلسلة واحدة من كل عنصر is_dynamic: `id, start, end, options_serialized`.
    أي تغيير (تحريك/تقسيم/تعديل خيارات/إضافة/حذف) يغيّر البصمة. تعيد "" إن
    لم توجد عناصر نصية (مسار سريع عبر mpv بلا إعادة بناء).
    """
    from video_maker.text_overlay import serialize_text_options

    entries = []
    for item in items or ():
        if not isinstance(item, dict) or not item.get("is_dynamic"):
            continue
        start = float(item.get("start", 0.0) or 0.0)
        end = float(item.get("end", 0.0) or 0.0)
        item_id = str(item.get("id", "") or "")
        options = serialize_text_options(item.get("options"))
        stable = repr(sorted((key, options[key]) for key in sorted(options)))
        entries.append("{id}|{start}|{end}|{options}".format(id=item_id, start=start, end=end, options=stable))
    return "\n".join(entries)


def render_preview_layer(items, playhead):
    """يعيد العنصر النصي النشط عند playhead أو None (منطق خالص بلا رسم).

    العنصر النشط: عنصر is_dynamic يقع playhead ضمن نطاقه [start, end).
    """
    playhead = float(playhead)
    for item in reversed(list(items or ())):
        if not isinstance(item, dict) or not item.get("is_dynamic"):
            continue
        start = float(item.get("start", 0.0) or 0.0)
        end = float(item.get("end", 0.0) or 0.0)
        if start <= playhead < end:
            return item
    return None


def should_use_fast_path(items):
    """True عندما لا توجد عناصر نصية (المسار السريع القديم عبر mpv بلا إعادة بناء)."""
    return not text_preview_fingerprint(items)
