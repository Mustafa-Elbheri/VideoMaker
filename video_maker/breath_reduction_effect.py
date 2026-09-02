BREATH_REDUCTION_ENGINE = "breath_reduction"


def _clamp(value, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = low
    return max(low, min(high, value))


def _db_to_gain(db_value):
    return 10.0 ** (float(db_value) / 20.0)


def breath_reduction_effect(values):
    from video_maker.audio_effects import pedalboard_effect
    values = dict(values or {})
    return pedalboard_effect("breath_reduction", values, breath_reduction_filter({"values": values}))


def is_breath_reduction_effect(audio_filter):
    return isinstance(audio_filter, dict) and (audio_filter.get("engine") == BREATH_REDUCTION_ENGINE or audio_filter.get("kind") == "breath_reduction")


def _breath_shape_settings(shape, air_amount, protect_words):
    air_db = _clamp(air_amount, 0, 100) / 100.0
    if protect_words:
        air_db *= 0.78
    if shape == "sharp":
        return 6500, 1.15, air_db * 16.0, 0, 0.0
    if shape == "close":
        return 3800, 0.85, air_db * 10.0, 240, air_db * 7.0
    if shape == "soft":
        return 4700, 0.75, air_db * 7.5, 0, 0.0
    return 5400, 0.95, air_db * 11.0, 0, 0.0


def _envelope_filter(gain, duration, attack, release, offset):
    duration = max(0.001, float(duration or 0.001))
    attack = max(0.001, min(float(attack), duration / 2.0))
    release = max(0.001, min(float(release), duration / 2.0))
    offset = max(0.0, min(duration, float(offset or 0.0)))
    if offset > 0.001:
        return f"volume={gain:.8f}"
    if duration <= attack + release + 0.001:
        return f"volume={gain:.8f}"
    r_start = duration - release
    diff = 1.0 - gain
    expr = f"if(lt(t,{attack:.6f}),1.0-{diff:.8f}*(t/{attack:.6f}),if(gt(t,{r_start:.6f}),min({gain:.8f}+{diff:.8f}*((t-{r_start:.6f})/{release:.6f}),1.0),{gain:.8f}))"
    return f"volume='{expr}':eval=frame"


def breath_reduction_filter(audio_filter, duration=0.0, offset=0.0):
    values = audio_filter.get("values", {}) if is_breath_reduction_effect(audio_filter) else {}
    protect_words = bool(values.get("protect_words", True))
    shape = str(values.get("shape", "balanced"))
    air_frequency, air_q, air_db, low_frequency, low_db = _breath_shape_settings(
        shape,
        values.get("air_control", 35),
        protect_words,
    )
    filters = []
    if low_frequency and low_db > 0.05:
        filters.append(f"equalizer=f={int(low_frequency)}:t=q:w=1.15:g=-{low_db:.2f}")
    if air_db > 0.05:
        filters.append(f"equalizer=f={int(air_frequency)}:t=q:w={air_q:.2f}:g=-{air_db:.2f}")
    
    if not filters:
        return "anull"
        
    return ",".join(filters)

