from video_maker.app_state import read_preferences, update_preferences
from video_maker.localization import tr


PROGRAM_MODE_KEY = "program_mode"
NORMAL_MODE = "normal"
PROFESSIONAL_MODE = "professional"

PROGRAM_MODES = [
    {"key": NORMAL_MODE, "label": "الوضع العادي"},
    {"key": PROFESSIONAL_MODE, "label": "الوضع الاحترافي"},
]


def normalize_program_mode(value):
    keys = {mode["key"] for mode in PROGRAM_MODES}
    return value if value in keys else NORMAL_MODE


def get_program_mode():
    return normalize_program_mode(read_preferences().get(PROGRAM_MODE_KEY, NORMAL_MODE))


def set_program_mode(value):
    update_preferences(**{PROGRAM_MODE_KEY: normalize_program_mode(value)})


def toggle_program_mode():
    current = get_program_mode()
    next_mode = NORMAL_MODE if current == PROFESSIONAL_MODE else PROFESSIONAL_MODE
    set_program_mode(next_mode)
    return next_mode


def program_mode_labels():
    return [tr(mode["label"]) for mode in PROGRAM_MODES]


def program_mode_index(value):
    mode = normalize_program_mode(value)
    for index, item in enumerate(PROGRAM_MODES):
        if item["key"] == mode:
            return index
    return 0


def program_mode_at(index):
    try:
        return PROGRAM_MODES[int(index)]["key"]
    except (IndexError, TypeError, ValueError):
        return NORMAL_MODE


def run_mode_shortcut(target, pro_method, normal_method):
    """يوجّه اختصاراً للسلوك الاحترافي إن كنا في الوضع الاحترافي، وإلا للسلوك القديم.

    القاعدة العامة للخطوة 03: أي مفتاح من الجدول يُفحص الوضع أولاً، وإن لم يكن
    احترافياً يعود بالسلوك القديم دون أي تغيير.
    """
    if get_program_mode() == PROFESSIONAL_MODE:
        getattr(target, pro_method)()
    else:
        getattr(target, normal_method)()
