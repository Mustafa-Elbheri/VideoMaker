# Stereo Audio Balance & Channel Rebalancing Effect
import numpy as np

STEREO_BALANCE_ENGINE = "stereo_balance"


def _clamp(value, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = low
    return max(low, min(high, value))


def stereo_balance_effect(values):
    from video_maker.audio_effects import pedalboard_effect
    values = dict(values or {})
    return pedalboard_effect("stereo_balance", values, ffmpeg_stereo_balance_filter(values))


def is_stereo_balance_effect(audio_filter):
    return isinstance(audio_filter, dict) and (
        audio_filter.get("engine") == STEREO_BALANCE_ENGINE
        or audio_filter.get("kind") == "stereo_balance"
    )


def apply_stereo_balance_dsp(audio, values):
    """
    معالجة موازنة الصوت في الذاكرة على مصفوفة PCM الاستريو (2, N).
    audio: np.ndarray من النوع float32 وشكل (2, N) حيث audio[0] يسار و audio[1] يمين.
    """
    if not isinstance(audio, np.ndarray) or audio.ndim != 2 or audio.shape[0] < 2:
        return audio

    values = dict(values or {})
    mode = str(values.get("mode", "stereo_balance"))
    balance = _clamp(values.get("balance", 0), -100, 100)
    fill_volume = _clamp(values.get("volume", 100), 0, 200) / 100.0

    left_ch = audio[0]
    right_ch = audio[1]

    if mode == "center_mono":
        mono = 0.5 * (left_ch + right_ch) * fill_volume
        return np.vstack([mono, mono])
    elif mode == "left_to_both":
        out = left_ch * fill_volume
        return np.vstack([out, out])
    elif mode == "right_to_both":
        out = right_ch * fill_volume
        return np.vstack([out, out])
    else:
        if balance < 0:
            left_gain = 1.0
            right_gain = _clamp((100 + balance) / 100.0, 0.0, 1.0)
        elif balance > 0:
            left_gain = _clamp((100 - balance) / 100.0, 0.0, 1.0)
            right_gain = 1.0
        else:
            left_gain = 1.0
            right_gain = 1.0

        left_gain *= fill_volume
        right_gain *= fill_volume

        out_left = left_ch * left_gain
        out_right = right_ch * right_gain
        return np.vstack([out_left, out_right])


def ffmpeg_stereo_balance_filter(values):
    values = dict(values or {})
    mode = str(values.get("mode", "stereo_balance"))
    balance = _clamp(values.get("balance", 0), -100, 100)
    fill_volume = _clamp(values.get("volume", 100), 0, 200) / 100.0

    if mode == "center_mono":
        return f"pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,volume={fill_volume:.2f}"
    elif mode == "left_to_both":
        return f"pan=stereo|c0=c0|c1=c0,volume={fill_volume:.2f}"
    elif mode == "right_to_both":
        return f"pan=stereo|c0=c1|c1=c1,volume={fill_volume:.2f}"
    else:
        if balance < 0:
            left_gain = 1.0
            right_gain = _clamp((100 + balance) / 100.0, 0.0, 1.0)
        elif balance > 0:
            left_gain = _clamp((100 - balance) / 100.0, 0.0, 1.0)
            right_gain = 1.0
        else:
            left_gain = 1.0
            right_gain = 1.0

        left_gain *= fill_volume
        right_gain *= fill_volume

        return f"pan=stereo|c0={left_gain:.3f}*c0|c1={right_gain:.3f}*c1"


def stereo_balance_filter(values):
    return ffmpeg_stereo_balance_filter(values)
