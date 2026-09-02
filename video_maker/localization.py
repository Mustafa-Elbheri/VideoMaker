# -*- coding: utf-8 -*-
"""
Core localization module for Video Maker.
Provides multi-language support (Arabic, English, French).
"""
from video_maker.locales.en import TEXTS_EN, EFFECT_TEXTS_EN
from video_maker.locales.fr import TEXTS_FR, EFFECT_TEXTS_FR

LANGUAGES = {
    "ar": "العربية",
    "en": "English",
    "fr": "Français",
}

TEXTS = {
    "en": TEXTS_EN,
    "fr": TEXTS_FR,
}

EFFECT_TEXTS = {
    "en": EFFECT_TEXTS_EN,
    "fr": EFFECT_TEXTS_FR,
}

import re

DYNAMIC_SPEECH_PATTERNS = [
    (re.compile(r'^الصوت\s+(\d+)\s+بالمئة$'), 'الصوت {percent} بالمئة', lambda m: {'percent': m.group(1)}),
    (re.compile(r'^مستوى الصوت\s+(\d+)\s+بالمئة$'), 'مستوى الصوت {percent} بالمئة', lambda m: {'percent': m.group(1)}),
    (re.compile(r'^المستوى الرئيسي\s+([\+\-\d\.]+)\s+ديسيبل$'), 'المستوى الرئيسي {db} ديسيبل', lambda m: {'db': m.group(1)}),
    (re.compile(r'^([\+\-\d\.]+)\s+ديسيبل$'), '{db} ديسيبل', lambda m: {'db': m.group(1)}),
    (re.compile(r'^نسبة الحفظ\s+(\d+)\s+بالمئة$'), 'نسبة الحفظ {percent} بالمئة', lambda m: {'percent': m.group(1)}),
    (re.compile(r'^تم تقليل خطوة التنقل إلى\s+(.+)$'), 'تم تقليل خطوة التنقل إلى {seconds} ثانية', lambda m: {'seconds': m.group(1)}),
    (re.compile(r'^تم توسيع خطوة التنقل إلى\s+(.+)$'), 'تم توسيع خطوة التنقل إلى {seconds} ثانية', lambda m: {'seconds': m.group(1)}),
    (re.compile(r'^تمت إعادة ضبط خطوة التنقل إلى\s+(.+)$'), 'تمت إعادة ضبط خطوة التنقل إلى {seconds} ثانية', lambda m: {'seconds': m.group(1)}),
    (re.compile(r'^السرعة الحالية\s+(.+)$'), 'السرعة الحالية {speed}', lambda m: {'speed': m.group(1)}),
    (re.compile(r'^(\d+(?:\.\d+)?)\s+ثانية$'), '{seconds} ثانية', lambda m: {'seconds': m.group(1)}),
]

def tr(text, **kwargs):
    if not isinstance(text, str):
        return text
    lang = get_language()
    if lang == "ar":
        result = text
    else:
        lang_dict = TEXTS.get(lang, {})
        effect_dict = EFFECT_TEXTS.get(lang, {})
        result = lang_dict.get(text) or effect_dict.get(text)
        if result is None:
            for pattern, template_key, arg_extractor in DYNAMIC_SPEECH_PATTERNS:
                m = pattern.match(text)
                if m:
                    template = lang_dict.get(template_key) or effect_dict.get(template_key)
                    if template:
                        extracted = arg_extractor(m)
                        try:
                            return template.format(**extracted)
                        except Exception:
                            return template
            result = text
    if kwargs:
        try:
            return result.format(**kwargs)
        except Exception:
            return result
    return result

def current_language():
    try:
        from video_maker.app_state import get_language
        return get_language()
    except Exception:
        return "ar"

def get_language():
    try:
        from video_maker.app_state import get_language as _get_language
        return _get_language()
    except Exception:
        return "ar"

def tr_format(text, **kwargs):
    return tr(text, **kwargs)

def spoken_duration(seconds):
    try:
        val = round(float(seconds), 2)
        if val == int(val):
            val = int(val)
    except Exception:
        val = seconds
    return tr("{seconds} ثانية", seconds=val)

def history_feedback_message(restored, operation, undo_count=0, restore_count=0):
    op_translated = tr(operation) if operation else ""
    if restored:
        return tr("إعادة {op}", op=op_translated) if op_translated else tr("إعادة")
    else:
        return tr("تراجع عن {op}", op=op_translated) if op_translated else tr("تراجع")
