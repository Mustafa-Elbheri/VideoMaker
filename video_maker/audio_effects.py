import math
from video_maker.stereo_balance_effect import stereo_balance_effect, ffmpeg_stereo_balance_filter
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import wx
from video_maker.app_paths import ffmpeg_binary

from video_maker.dialog_keys import bind_dialog_keys
from video_maker.error_reporting import show_error
from video_maker.app_state import get_audio_effect_values, get_volume, set_audio_effect_values
from video_maker.app_paths import bundled_path
from video_maker.audio_devices import selected_sounddevice_output_device
from video_maker.audio_ducking import audio_ducking_effect_definition
from video_maker.breath_reduction_effect import breath_reduction_effect, breath_reduction_filter, is_breath_reduction_effect
from video_maker.dpdfnet_effect import DPDFNET_PREVIEW_HOP, DPDFNET_SAMPLE_RATE, DpdfnetRealtimeProcessor, apply_dpdfnet_filter, dpdfnet_effect, is_dpdfnet_effect
from video_maker.localization import tr
from video_maker.operation_control import OperationCancelled, is_operation_cancelled
from video_maker.save_progress import SaveProgressDialog
from video_maker.timeline import TimelineSegment, delete_range, insert_segments, slice_segments
from video_maker.logical_files import replacement_segments_preserving_files
from video_maker.video_editing import get_media_duration, has_video_stream, write_timeline_audio, write_timeline_video
from video_maker.volume_boost import normalized_program_volume


LONG_EFFECT_PROGRESS_SECONDS = 10.0
PEDALBOARD_WORKER_FLAG = "--video-maker-audio-effect-worker"
PREVIEW_THREAD_STOP_TIMEOUT = 0.75


class AudioEffectPreparationCancelled(OperationCancelled):
    """اسم متوافق مع الكود القديم لنفس حالة الإلغاء الطبيعية."""
    pass


def audio_effect_cancelled(cancelled_callback):
    return bool(cancelled_callback and cancelled_callback())


def ffmpeg_startupinfo():
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def terminate_subprocess(process):
    if not process:
        return
    for stream_name in ("stdin", "stdout"):
        stream = getattr(process, stream_name, None)
        if stream:
            try:
                stream.close()
            except Exception:
                pass
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=2)
        return
    except (subprocess.TimeoutExpired, TypeError):
        pass
    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=2)
    except Exception:
        pass


def scale(value, low, high):
    return low + (high - low) * value / 100


def shaped_scale(value, low, high, curve=1.0):
    value = max(0.0, min(100.0, float(value or 0.0))) / 100.0
    if curve and abs(curve - 1.0) > 0.001:
        value = value ** curve
    return low + (high - low) * value


def parameter_value(values, key, default=50):
    return values.get(key, default)


def project_asset_path(*parts):
    return str(bundled_path("assets", *parts))


def echo_time_choices():
    values = []
    for value in range(1, 10):
        values.append(value / 100)
    for value in range(1, 101):
        values.append(value / 10)
    return [{"label": f"{value:g}", "value": int(round(value * 1000))} for value in values]


def echo_repeat_choices():
    return [{"label": str(value), "value": value} for value in range(1, 201)]


def ffmpeg_echo_filter(values, include_reverb=None):
    delay = int(parameter_value(values, "delay", 300))
    repeats = int(parameter_value(values, "echoes", 4))
    volume = scale(parameter_value(values, "volume", 45), 0.04, 0.72)
    feedback = scale(parameter_value(values, "feedback", 18), 0.0, 0.48)
    delays = []
    decays = []
    for index in range(repeats):
        echo_number = index + 1
        delays.append(str(delay * echo_number))
        gain = (volume ** echo_number) + (volume * feedback * echo_number / max(1, repeats))
        decays.append(f"{max(0.01, min(0.85, gain)):.3f}")
    result = f"aecho=0.90:0.78:{'|'.join(delays)}:{'|'.join(decays)}"
    use_reverb = bool(values.get("reverb", True)) if include_reverb is None else include_reverb
    if use_reverb:
        level = scale(parameter_value(values, "reverb_level", 18), 0.03, 0.28)
        stereo_delay = 12 if values.get("stereo", False) else 0
        result += f",aecho=0.92:0.80:{55 + stereo_delay}|{110 + stereo_delay}:{level:.3f}|{max(0.02, level * 0.55):.3f}"
    return f"{result},alimiter=limit=0.94"


def pedalboard_effect(kind, values, preview_filter):
    return {"engine": "pedalboard", "kind": kind, "values": dict(values), "preview_filter": preview_filter}


def echo_reverb_studio_effect(values):
    return pedalboard_effect("echo_reverb_studio", values, ffmpeg_echo_filter(values))


def reverb_studio_effect(values):
    decay = parameter_value(values, "decay", parameter_value(values, "tail", 36))
    pre_delay = parameter_value(values, "pre_delay", 24)
    damping = parameter_value(values, "damping", 42)
    wet = parameter_value(values, "wet", 24)
    early = parameter_value(values, "early", 28)
    lowpass = int(shaped_scale(100 - damping, 4200, 15500, 1.15))
    decay_a = shaped_scale(decay, 0.11, 0.62, 1.25)
    decay_b = shaped_scale(decay, 0.07, 0.48, 1.25)
    early_gain = shaped_scale(early, 0.03, 0.22, 1.15)
    wet_gain = shaped_scale(wet, 0.06, 0.46, 1.35)
    dry_gain = shaped_scale(parameter_value(values, "dry", 88), 0.0, 1.0, 0.75)
    return pedalboard_effect(
        "reverb_studio",
        values,
        (
            f"asplit=2[dry][verb];"
            f"[dry]volume={dry_gain:.3f}[d];"
            f"[verb]adelay={int(pre_delay)}|{int(pre_delay)},"
            f"aecho=0.72:0.86:23|47|91:{early_gain:.3f}|{early_gain * 0.72:.3f}|{early_gain * 0.48:.3f},"
            f"lowpass=f={lowpass},aecho=0.70:0.84:89|173|331:{wet_gain:.3f}|{decay_a:.3f}|{decay_b:.3f}[v];"
            "[d][v]amix=inputs=2:normalize=0,alimiter=limit=0.94"
        ),
    )


def mosque_reverb_effect(values):
    wet = scale(parameter_value(values, "wet", 24), 0.10, 0.44)
    dry = scale(100 - parameter_value(values, "wet", 24), 0.78, 1.0)
    return pedalboard_effect("mosque_reverb", values, f"highpass=f=75,aecho=0.90:0.86:38|82|147:{wet:.3f}|{max(0.02, wet * 0.55):.3f}|{max(0.02, wet * 0.34):.3f},volume={dry:.2f},alimiter=limit=0.94")


def goldwave_fade_effect(values):
    return {"engine": "goldwave_fade", "values": dict(values)}


def is_goldwave_fade_effect(audio_filter):
    return isinstance(audio_filter, dict) and audio_filter.get("engine") == "goldwave_fade"


def fade_level_gain(level_db):
    level_db = max(-160.0, min(0.0, float(level_db)))
    if level_db <= -159.0:
        return 0.0
    return 10.0 ** (level_db / 20.0)


def goldwave_fade_filter(audio_filter, total_duration, offset=0.0):
    """Build a GoldWave-style fade without requiring recent FFmpeg options.

    GoldWave fades between a user-selected initial/final level and full volume.
    Older FFmpeg builds do not provide the ``silence`` and ``unity`` options on
    ``afade``.  Mixing a constant-gain branch with a normal 0-to-1 fade gives
    the same level range and works on both old and new FFmpeg versions.
    """
    values = audio_filter.get("values", {})
    direction = values.get("direction", "in")
    curve = "log" if values.get("curve", "log") == "log" else "tri"
    level = fade_level_gain(values.get("level_db", -160))
    duration_percent = max(1.0, min(100.0, float(values.get("duration_percent", 100))))
    total_duration = max(0.05, float(total_duration or 0.05))
    fade_duration = max(0.05, min(total_duration, total_duration * duration_percent / 100.0))
    start = 0.0 if direction == "in" else max(0.0, total_duration - fade_duration)
    fade_type = "in" if direction == "in" else "out"
    offset = max(0.0, float(offset or 0.0))

    if level <= 0.0:
        return (
            f"asetpts=PTS+{offset:.6f}/TB,"
            f"afade=t={fade_type}:st={start:.6f}:d={fade_duration:.6f}:curve={curve},"
            "asetpts=PTS-STARTPTS"
        )

    moving_gain = max(0.0, 1.0 - level)
    return (
        f"asetpts=PTS+{offset:.6f}/TB,asplit=2[fade_base][fade_move];"
        f"[fade_base]volume={level:.10f}[fade_floor];"
        f"[fade_move]afade=t={fade_type}:st={start:.6f}:d={fade_duration:.6f}:curve={curve},"
        f"volume={moving_gain:.10f}[fade_curve];"
        "[fade_floor][fade_curve]amix=inputs=2:normalize=0,asetpts=PTS-STARTPTS"
    )


def resolved_audio_filter(audio_filter, duration=0.0, offset=0.0):
    if is_goldwave_fade_effect(audio_filter):
        return goldwave_fade_filter(audio_filter, duration, offset)
    if is_breath_reduction_effect(audio_filter):
        return breath_reduction_filter(audio_filter, duration, offset)
    if is_dpdfnet_effect(audio_filter):
        raise RuntimeError("DPDFNet audio effects must use the DPDFNet engine")
    if is_pedalboard_effect(audio_filter):
        if isinstance(audio_filter, dict) and audio_filter.get("preview_filter"):
            return audio_filter.get("preview_filter")
        raise RuntimeError("Pedalboard audio effects must use the pedalboard engine")
    return audio_filter


def is_pedalboard_effect(audio_filter):
    return isinstance(audio_filter, dict) and audio_filter.get("engine") == "pedalboard"


def direct_realtime_audio_filter_supported(audio_filter):
    if is_pedalboard_effect(audio_filter):
        return isinstance(audio_filter, dict) and bool(audio_filter.get("preview_filter"))
    return True


def preview_filter_text(audio_filter, duration=0.0, offset=0.0):
    return resolved_audio_filter(audio_filter, duration, offset)


def audio_filter_cache_key(value):
    if isinstance(value, dict):
        return tuple(sorted((key, audio_filter_cache_key(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(audio_filter_cache_key(item) for item in value)
    return value


_IR_CACHE = {}


def get_cached_ir(ir_path):
    if ir_path not in _IR_CACHE:
        try:
            from pedalboard.io import AudioFile
            with AudioFile(ir_path) as f:
                data = f.read(f.frames)
                sr = f.samplerate
            _IR_CACHE[ir_path] = (data, sr)
        except Exception:
            pass
    return _IR_CACHE.get(ir_path, (ir_path, None))


def build_pedalboard(audio_filter):
    from pedalboard import Bitcrush, Chorus, Compressor, Convolution, Delay, Gain, HighpassFilter, HighShelfFilter, Limiter, LowpassFilter, LowShelfFilter, PeakFilter, Pedalboard, PitchShift, Reverb
    values = audio_filter.get("values", {})
    kind = audio_filter.get("kind", "")
    plugins = []
    if kind == "echo_reverb_studio":
        feedback = scale(parameter_value(values, "feedback", 18), 0.0, 0.48)
        if bool(values.get("reverb", True)):
            plugins.append(Reverb(
                room_size=scale(parameter_value(values, "reverb_level", 18), 0.08, 0.62),
                damping=0.62,
                wet_level=scale(parameter_value(values, "reverb_level", 18), 0.02, 0.28),
                dry_level=0.96,
                width=1.0 if values.get("stereo", False) else 0.15,
            ))
        plugins.append(Compressor(threshold_db=-6, ratio=2.0, attack_ms=2, release_ms=90))
        plugins.append(Gain(gain_db=-0.7))
    elif kind == "reverb_studio":
        plugins.append(Reverb(
            room_size=shaped_scale(parameter_value(values, "decay", parameter_value(values, "room", 36)), 0.14, 0.94, 1.25),
            damping=shaped_scale(parameter_value(values, "damping", 42), 0.18, 0.86, 1.0),
            wet_level=1.0,
            dry_level=0.0,
            width=shaped_scale(parameter_value(values, "width", 52), 0.28, 1.0, 0.9),
        ))
        plugins.append(Compressor(threshold_db=-5, ratio=1.55, attack_ms=6, release_ms=180))
        plugins.append(Gain(gain_db=-1.4))
    elif kind == "mosque_reverb":
        ir_path = project_asset_path("impulses", "large_mosque.wav")
        ir_data, ir_sr = get_cached_ir(ir_path)
        plugins.append(HighpassFilter(cutoff_frequency_hz=scale(parameter_value(values, "clarity", 42), 60, 105)))
        plugins.append(LowShelfFilter(cutoff_frequency_hz=180, gain_db=scale(parameter_value(values, "warmth", 44), -1.0, 2.2), q=0.7))
        plugins.append(HighShelfFilter(cutoff_frequency_hz=4200, gain_db=scale(parameter_value(values, "clarity", 42), -1.8, 1.8), q=0.65))
        if ir_sr is not None:
            plugins.append(Convolution(ir_data, scale(parameter_value(values, "wet", 24), 0.08, 0.46), sample_rate=ir_sr))
        else:
            plugins.append(Convolution(ir_data, scale(parameter_value(values, "wet", 24), 0.08, 0.46)))
        plugins.append(LowpassFilter(cutoff_frequency_hz=scale(100 - parameter_value(values, "warmth", 44), 7600, 13200)))
        plugins.append(Compressor(threshold_db=-8, ratio=1.8, attack_ms=8, release_ms=180))
        plugins.append(Gain(gain_db=-1.2))
    elif kind == "hall":
        room = parameter_value(values, "room", 60)
        tail = parameter_value(values, "tail", 55)
        warmth = parameter_value(values, "warmth", 45)
        plugins.append(LowShelfFilter(cutoff_frequency_hz=180, gain_db=shaped_scale(warmth, -1.0, 7.0, 1.0)))
        plugins.append(Reverb(room_size=shaped_scale(room, 0.25, 0.88, 1.05), damping=0.50, wet_level=shaped_scale(tail, 0.10, 0.52, 1.2), dry_level=0.88, width=0.6))
        plugins.append(Compressor(threshold_db=-6, ratio=1.6))
        plugins.append(Gain(gain_db=-1.0))
    elif kind == "cinematic":
        bass_gain = shaped_scale(parameter_value(values, "bass", 60), 0, 14, 1.05)
        clarity_gain = shaped_scale(parameter_value(values, "clarity", 55), 0, 11, 1.05)
        punch = shaped_scale(parameter_value(values, "punch", 55), -26, -7, 1.0)
        plugins.append(HighpassFilter(cutoff_frequency_hz=65))
        plugins.append(LowShelfFilter(cutoff_frequency_hz=95, gain_db=bass_gain))
        plugins.append(PeakFilter(cutoff_frequency_hz=2800, gain_db=clarity_gain, q=1.0))
        plugins.append(Compressor(threshold_db=punch, ratio=2.2))
        plugins.append(Gain(gain_db=-1.2))
    elif kind == "deep_voice":
        depth = parameter_value(values, "depth", 60)
        semitones = shaped_scale(100 - depth, -9.0, 0.0, 1.0)
        bass_gain = shaped_scale(parameter_value(values, "bass", 60), 1, 17, 1.1)
        growl = parameter_value(values, "growl", 35)
        bits = int(shaped_scale(100 - growl, 6, 20, 1.0))
        if abs(semitones) > 0.01:
            plugins.append(PitchShift(semitones=semitones))
        plugins.append(HighpassFilter(cutoff_frequency_hz=45))
        plugins.append(LowShelfFilter(cutoff_frequency_hz=80, gain_db=bass_gain))
        if growl > 5:
            plugins.append(Bitcrush(bit_depth=bits))
        plugins.append(PeakFilter(cutoff_frequency_hz=1800, gain_db=3.0, q=1.0))
        plugins.append(Gain(gain_db=-1.5))
    elif kind == "breath_reduction":
        shape = str(values.get("shape", "balanced"))
        protect_words = bool(values.get("protect_words", True))
        air_frequency = 5400
        air_db = 3.5
        plugins.append(PeakFilter(cutoff_frequency_hz=air_frequency, gain_db=-air_db, q=0.95))
        plugins.append(Compressor(threshold_db=-14, ratio=1.8))
    elif kind == "bright_voice":
        pitch_val = parameter_value(values, "pitch", 55)
        semitones = shaped_scale(pitch_val, -2.0, 9.0, 1.0)
        sparkle = shaped_scale(parameter_value(values, "sparkle", 50), -1, 12, 1.05)
        lightness = shaped_scale(parameter_value(values, "lightness", 45), -3, 8, 1.0)
        if abs(semitones) > 0.01:
            plugins.append(PitchShift(semitones=semitones))
        plugins.append(HighpassFilter(cutoff_frequency_hz=120))
        plugins.append(HighShelfFilter(cutoff_frequency_hz=5200, gain_db=sparkle))
        plugins.append(PeakFilter(cutoff_frequency_hz=1600, gain_db=lightness, q=1.1))
        plugins.append(Gain(gain_db=-1.0))
    elif kind == "megaphone":
        narrow = parameter_value(values, "narrow", 55)
        drive = shaped_scale(parameter_value(values, "drive", 55), 0.0, 10.0, 1.15)
        rough = parameter_value(values, "rough", 35)
        low = int(shaped_scale(narrow, 180, 820, 1.05))
        high = int(shaped_scale(100 - narrow, 2200, 5800, 1.0))
        bits = int(shaped_scale(100 - rough, 5, 20, 1.0))
        plugins.append(HighpassFilter(cutoff_frequency_hz=low))
        plugins.append(LowpassFilter(cutoff_frequency_hz=high))
        plugins.append(Gain(gain_db=drive))
        plugins.append(PeakFilter(cutoff_frequency_hz=1800, gain_db=8.0, q=0.8))
        if rough > 5:
            plugins.append(Bitcrush(bit_depth=bits))
        plugins.append(Gain(gain_db=-2.0))
    elif kind == "underwater":
        muffled = parameter_value(values, "muffled", 60)
        movement = parameter_value(values, "movement", 45)
        depth = parameter_value(values, "depth", 45)
        lowpass = int(shaped_scale(100 - muffled, 420, 3400, 1.25))
        tremolo_speed = shaped_scale(movement, 0.35, 5.2, 1.05)
        echo_delay = shaped_scale(depth, 0.06, 0.36, 1.1)
        plugins.append(LowpassFilter(cutoff_frequency_hz=lowpass))
        plugins.append(LowShelfFilter(cutoff_frequency_hz=150, gain_db=4.0))
        plugins.append(Chorus(rate_hz=tremolo_speed, depth=0.4))
        plugins.append(Delay(delay_seconds=echo_delay, feedback=0.35, mix=0.4))
    elif kind == "space_motion":
        space = parameter_value(values, "space", 55)
        motion = parameter_value(values, "motion", 45)
        width = parameter_value(values, "width", 50)
        chorus_speed = shaped_scale(motion, 0.08, 1.35, 1.1)
        delay_one = shaped_scale(space, 0.09, 0.65, 1.1)
        plugins.append(Chorus(rate_hz=chorus_speed, depth=shaped_scale(motion, 0.1, 0.8, 1.0), mix=shaped_scale(width, 0.2, 0.8, 1.0)))
        plugins.append(Delay(delay_seconds=delay_one, feedback=shaped_scale(space, 0.1, 0.5, 1.0), mix=0.5))
    elif kind == "safe_boost":
        boost_db = shaped_scale(parameter_value(values, "boost", 55), 0.0, 12.0, 1.15)
        compression = shaped_scale(parameter_value(values, "protection", 60), -38, -12, 1.0)
        clarity = shaped_scale(parameter_value(values, "clarity", 40), -1, 8, 1.05)
        plugins.append(Gain(gain_db=boost_db))
        plugins.append(PeakFilter(cutoff_frequency_hz=2600, gain_db=clarity, q=1.0))
        plugins.append(Compressor(threshold_db=compression, ratio=2.0))
        plugins.append(Limiter(threshold_db=-1.0))
    elif kind == "volume_level":
        level = max(100.0, min(400.0, float(parameter_value(values, "level", 100))))
        gain_db = 20.0 * math.log10(level / 100.0) if level > 100 else 0.0
        plugins.append(Gain(gain_db=gain_db))
    elif kind == "robot":
        crush = int(shaped_scale(100 - parameter_value(values, "metal", 55), 4, 20, 1.0))
        tremolo_freq = shaped_scale(parameter_value(values, "movement", 40), 4, 32, 1.0)
        plugins.append(HighpassFilter(cutoff_frequency_hz=120))
        plugins.append(Bitcrush(bit_depth=crush))
        plugins.append(Chorus(rate_hz=tremolo_freq, depth=0.7))
        plugins.append(PeakFilter(cutoff_frequency_hz=1200, gain_db=5.0, q=1.2))
        plugins.append(Gain(gain_db=-1.0))
    elif kind == "radio_phone":
        narrow = parameter_value(values, "narrow", 55)
        low = int(shaped_scale(narrow, 180, 720, 1.05))
        high = int(shaped_scale(100 - narrow, 1900, 5600, 1.0))
        drive = shaped_scale(parameter_value(values, "drive", 40), 0.0, 9.0, 1.1)
        noise = parameter_value(values, "noise", 15)
        bits = int(shaped_scale(100 - noise, 5, 18, 1.0))
        plugins.append(HighpassFilter(cutoff_frequency_hz=low))
        plugins.append(LowpassFilter(cutoff_frequency_hz=high))
        plugins.append(Gain(gain_db=drive))
        if noise > 5:
            plugins.append(Bitcrush(bit_depth=bits))
        plugins.append(Gain(gain_db=-1.5))
    elif kind == "flanger":
        speed = shaped_scale(parameter_value(values, "speed", 45), 0.1, 2.0, 1.15)
        depth = shaped_scale(parameter_value(values, "depth", 50), 0.1, 0.9, 1.15)
        feedback = shaped_scale(parameter_value(values, "feedback", 35), 0.0, 0.7, 1.0)
        plugins.append(Chorus(rate_hz=speed, depth=depth, feedback=feedback))
    elif kind == "chorus":
        speed = shaped_scale(parameter_value(values, "speed", 45), 0.08, 1.1, 1.1)
        depth = shaped_scale(parameter_value(values, "depth", 45), 0.05, 0.86, 1.15)
        width = shaped_scale(parameter_value(values, "width", 55), 0.1, 0.8, 1.0)
        plugins.append(Chorus(rate_hz=speed, depth=depth, mix=width))
    elif kind == "vibrato":
        speed = shaped_scale(parameter_value(values, "speed", 45), 1.5, 13.5, 1.0)
        depth = shaped_scale(parameter_value(values, "depth", 35), 0.04, 0.95, 1.2)
        plugins.append(Chorus(rate_hz=speed, depth=depth))
    elif kind == "tremolo":
        speed = shaped_scale(parameter_value(values, "speed", 50), 1.0, 22.0, 1.0)
        depth = shaped_scale(parameter_value(values, "depth", 55), 0.04, 0.98, 1.15)
        plugins.append(Chorus(rate_hz=speed, depth=depth))
    elif kind == "pitch":
        pitch_val = parameter_value(values, "pitch", 60)
        semitones = shaped_scale(pitch_val, -8.0, 8.0, 1.0)
        if abs(semitones) > 0.01:
            plugins.append(PitchShift(semitones=semitones))
        plugins.append(Gain(gain_db=-0.5))
    elif kind == "bass_treble":
        bass_gain = shaped_scale(parameter_value(values, "bass", 55), -9, 16)
        treble_gain = shaped_scale(parameter_value(values, "treble", 55), -8, 15)
        plugins.append(LowShelfFilter(cutoff_frequency_hz=110, gain_db=bass_gain, q=0.7))
        plugins.append(HighShelfFilter(cutoff_frequency_hz=4200, gain_db=treble_gain, q=0.7))
        plugins.append(Gain(gain_db=-0.5))
    elif kind == "stereo_balance":
        balance = parameter_value(values, "balance", 0)
        mode = str(values.get("mode", "stereo_balance"))
        vol = scale(parameter_value(values, "volume", 100), 0.0, 2.0)
        if mode == "stereo_balance" and balance != 0:
            left_gain = (100 - balance) / 100.0 if balance > 0 else 1.0
            right_gain = (100 + balance) / 100.0 if balance < 0 else 1.0
            avg_gain = (left_gain + right_gain) / 2.0 * vol
            gain_db = 20.0 * math.log10(max(0.01, avg_gain))
            plugins.append(Gain(gain_db=gain_db))
        else:
            gain_db = 20.0 * math.log10(max(0.01, vol))
            plugins.append(Gain(gain_db=gain_db))
        plugins.append(Limiter(threshold_db=-0.5))
    elif kind == "normalize":
        loudness = shaped_scale(parameter_value(values, "level", 55), -23, -12, 1.0)
        compression = shaped_scale(parameter_value(values, "stability", 55), -42, -14, 1.0)
        plugins.append(Compressor(threshold_db=compression, ratio=2.0))
        plugins.append(Gain(gain_db=loudness + 18.0))
        plugins.append(Limiter(threshold_db=-1.5))
    return Pedalboard(plugins)


def echo_tap_settings(values, sample_rate):
    delay_ms = max(100, int(parameter_value(values, "delay", 300)))
    repeats = max(1, min(120, int(parameter_value(values, "echoes", 4))))
    volume = scale(parameter_value(values, "volume", 45), 0.04, 0.72)
    feedback = scale(parameter_value(values, "feedback", 18), 0.0, 0.48)
    stereo = bool(values.get("stereo", False))
    width = scale(parameter_value(values, "stereo_width", 18), 0.04, 0.34) if stereo else 0
    taps = []
    for index in range(repeats):
        echo_number = index + 1
        samples = max(1, int(delay_ms * echo_number * sample_rate / 1000))
        gain = (volume ** echo_number) + (volume * feedback * echo_number / max(1, repeats))
        gain = max(0.005, min(0.85, gain))
        taps.append((samples, gain))
    return taps, width


def echo_tail_duration(audio_filter):
    if not is_pedalboard_effect(audio_filter):
        return 0
    if audio_filter.get("kind") == "mosque_reverb":
        return scale(parameter_value(audio_filter.get("values", {}), "wet", 24), 2.8, 6.0)
    if audio_filter.get("kind") != "echo_reverb_studio":
        return 0
    values = audio_filter.get("values", {})
    if not values.get("tail", True):
        return 0
    delay_ms = max(100, int(parameter_value(values, "delay", 300)))
    repeats = max(1, min(120, int(parameter_value(values, "echoes", 4))))
    return min(20.0, delay_ms * repeats / 1000.0)


def echo_history(state, max_delay):
    history = state.get("echo_history")
    if history is None or history.shape[1] < max_delay:
        history = np.zeros((2, max_delay), dtype=np.float32)
    return history


def delay_block(audio, state, key, delay_samples):
    delay_samples = max(0, int(delay_samples))
    if delay_samples <= 0:
        return audio
    history = state.get(key)
    if history is None or history.shape[1] != delay_samples:
        history = np.zeros((2, delay_samples), dtype=np.float32)
    combined = np.concatenate([history, audio], axis=1)
    delayed = combined[:, :audio.shape[1]]
    state[key] = combined[:, audio.shape[1]:audio.shape[1] + delay_samples].copy()
    return delayed.astype(np.float32, copy=False)


def early_reflections(audio, values, state, sample_rate):
    level = parameter_value(values, "early", 28)
    if level <= 0:
        return np.zeros_like(audio, dtype=np.float32)
    room = parameter_value(values, "room", parameter_value(values, "decay", 36))
    width = shaped_scale(parameter_value(values, "width", 52), 0.0, 0.42, 1.0)
    gain = shaped_scale(level, 0.025, 0.24, 1.2)
    taps_ms = [
        shaped_scale(room, 9, 24, 0.9),
        shaped_scale(room, 17, 43, 0.9),
        shaped_scale(room, 29, 72, 0.9),
        shaped_scale(room, 43, 118, 0.9),
    ]
    gains = [gain, gain * 0.74, gain * 0.52, gain * 0.36]
    max_delay = max(1, int(max(taps_ms) * sample_rate / 1000.0))
    history = state.get("reverb_early_history")
    if history is None or history.shape[1] < max_delay:
        history = np.zeros((2, max_delay), dtype=np.float32)
    combined = np.concatenate([history, audio], axis=1)
    history_size = history.shape[1]
    result = np.zeros_like(audio, dtype=np.float32)
    for tap_ms, tap_gain in zip(taps_ms, gains):
        delay = max(1, int(tap_ms * sample_rate / 1000.0))
        delayed = combined[:, history_size - delay:history_size - delay + audio.shape[1]]
        if width:
            delayed = np.vstack([
                delayed[0] * (1 - width) + delayed[1] * width,
                delayed[1] * (1 - width) + delayed[0] * width,
            ])
        result += delayed * tap_gain
    state["reverb_early_history"] = combined[:, -max_delay:].copy()
    return result


def apply_echo_taps(audio, values, state, sample_rate):
    taps, width = echo_tap_settings(values, sample_rate)
    if not taps:
        return audio
    max_delay = max(delay for delay, gain in taps)
    history = echo_history(state, max_delay)
    combined = np.concatenate([history, audio], axis=1)
    output = audio.astype(np.float32, copy=True) * 0.90
    history_size = history.shape[1]
    for delay, gain in taps:
        delayed = combined[:, history_size - delay:history_size - delay + audio.shape[1]]
        if width:
            delayed = np.vstack([delayed[0] * (1 - width) + delayed[1] * width, delayed[1] * (1 - width) + delayed[0] * width])
        output += delayed * gain
    state["echo_history"] = combined[:, -max_delay:].copy()
    return np.clip(output, -1.0, 1.0).astype(np.float32, copy=False)


def process_pedalboard_block(audio, audio_filter, board, state, sample_rate):
    if audio_filter.get("kind") == "stereo_balance" or (isinstance(audio_filter, dict) and audio_filter.get("engine") == "stereo_balance"):
        from video_maker.stereo_balance_effect import apply_stereo_balance_dsp
        processed = apply_stereo_balance_dsp(audio, audio_filter.get("values", {}))
        if board:
            processed = board(processed, sample_rate, reset=False)
        return np.clip(processed, -1.0, 1.0).astype(np.float32, copy=False)
    if audio_filter.get("kind") == "echo_reverb_studio":
        audio = apply_echo_taps(audio, audio_filter.get("values", {}), state, sample_rate)
    if audio_filter.get("kind") == "reverb_studio":
        values = audio_filter.get("values", {})
        pre_delay_samples = int(parameter_value(values, "pre_delay", 24) * sample_rate / 1000.0)
        wet_input = delay_block(audio, state, "reverb_predelay_history", pre_delay_samples)
        wet = board(wet_input, sample_rate, reset=False)
        early = early_reflections(audio, values, state, sample_rate)
        wet_level = shaped_scale(parameter_value(values, "wet", 24), 0.08, 0.52, 1.35)
        dry_level = shaped_scale(parameter_value(values, "dry", 88), 0.0, 1.0, 0.75)
        output = audio * dry_level + wet * wet_level + early
        return np.clip(output, -1.0, 1.0).astype(np.float32, copy=False)
    processed = board(audio, sample_rate, reset=False)
    return np.clip(processed, -1.0, 1.0).astype(np.float32, copy=False)


def clean_voice_filter(values):
    noise = -16 - shaped_scale(parameter_value(values, "noise", 65), 0, 30, 1.15)
    clarity = shaped_scale(parameter_value(values, "clarity", 55), 0, 11, 1.1)
    compression = shaped_scale(parameter_value(values, "compression", 55), -24, -7, 1.0)
    return f"highpass=f=80,lowpass=f=12500,afftdn=nf={noise:.1f},equalizer=f=3200:t=q:w=1.1:g={clarity:.1f},compand=attacks=0.15:decays=0.65:points=-80/-80|-45/-32|{compression:.1f}/-10|0/-2,loudnorm=I=-16:TP=-1.5:LRA=9,alimiter=limit=0.95"


def strong_noise_filter(values):
    noise = -22 - shaped_scale(parameter_value(values, "noise", 75), 0, 34, 1.1)
    clarity = shaped_scale(parameter_value(values, "clarity", 45), 0, 10, 1.05)
    warmth = shaped_scale(parameter_value(values, "warmth", 35), -1, 6, 1.0)
    return f"afftdn=nf={noise:.1f},highpass=f=95,lowpass=f=9800,bass=g={warmth:.1f}:f=140:w=0.7,equalizer=f=3000:t=q:w=1.2:g={clarity:.1f},loudnorm=I=-16:TP=-1.5:LRA=10,alimiter=limit=0.95"


def echo_reverb_filter(values):
    delay = int(scale(parameter_value(values, "delay", 45), 90, 720))
    echo = scale(parameter_value(values, "echo", 45), 0.18, 0.72)
    room = scale(parameter_value(values, "room", 45), 0.12, 0.58)
    second_delay = delay + int(scale(parameter_value(values, "room", 45), 70, 220))
    return f"aecho=0.75:0.88:{delay}|{second_delay}:{echo:.2f}|{room:.2f},alimiter=limit=0.95"


def ffmpeg_hall_filter(values):
    room = parameter_value(values, "room", 60)
    delay_one = int(shaped_scale(room, 38, 165, 1.05))
    delay_two = int(shaped_scale(room, 105, 430, 1.05))
    delay_three = int(shaped_scale(room, 230, 820, 1.05))
    decay_one = shaped_scale(parameter_value(values, "tail", 55), 0.12, 0.62, 1.2)
    decay_two = shaped_scale(parameter_value(values, "tail", 55), 0.08, 0.48, 1.2)
    decay_three = shaped_scale(parameter_value(values, "tail", 55), 0.05, 0.36, 1.2)
    warmth = shaped_scale(parameter_value(values, "warmth", 45), -1, 7, 1.0)
    return f"bass=g={warmth:.1f}:f=180:w=0.8,aecho=0.7:0.82:{delay_one}|{delay_two}|{delay_three}:{decay_one:.2f}|{decay_two:.2f}|{decay_three:.2f},alimiter=limit=0.95"

def hall_filter(values):
    return pedalboard_effect("hall", values, ffmpeg_hall_filter(values))


def ffmpeg_cinematic_voice_filter(values):
    bass_gain = shaped_scale(parameter_value(values, "bass", 60), 0, 14, 1.05)
    clarity_gain = shaped_scale(parameter_value(values, "clarity", 55), 0, 11, 1.05)
    punch = shaped_scale(parameter_value(values, "punch", 55), -26, -7, 1.0)
    return f"highpass=f=65,bass=g={bass_gain:.1f}:f=95:w=0.7,equalizer=f=2800:t=q:w=1.0:g={clarity_gain:.1f},compand=attacks=0.08:decays=0.45:points=-80/-80|-35/-23|{punch:.1f}/-8|0/-1.5,loudnorm=I=-15:TP=-1.2:LRA=8,alimiter=limit=0.94"

def cinematic_voice_filter(values):
    return pedalboard_effect("cinematic", values, ffmpeg_cinematic_voice_filter(values))


def ffmpeg_deep_voice_filter(values):
    pitch = shaped_scale(100 - parameter_value(values, "depth", 60), 0.52, 1.0, 1.0)
    bass_gain = shaped_scale(parameter_value(values, "bass", 60), 1, 17, 1.1)
    growl = parameter_value(values, "growl", 35)
    bits = int(shaped_scale(100 - growl, 6, 20, 1.0))
    mix = shaped_scale(growl, 0.0, 0.58, 1.25)
    return f"rubberband=pitch={pitch:.3f},highpass=f=45,bass=g={bass_gain:.1f}:f=80:w=0.8,acrusher=bits={bits}:mix={mix:.2f},equalizer=f=1800:t=q:w=1.0:g=3,alimiter=limit=0.93"

def deep_voice_filter(values):
    return pedalboard_effect("deep_voice", values, ffmpeg_deep_voice_filter(values))


def ffmpeg_bright_voice_filter(values):
    pitch = shaped_scale(parameter_value(values, "pitch", 55), 0.88, 1.82, 1.0)
    sparkle = shaped_scale(parameter_value(values, "sparkle", 50), -1, 12, 1.05)
    lightness = shaped_scale(parameter_value(values, "lightness", 45), -3, 8, 1.0)
    return f"rubberband=pitch={pitch:.3f},highpass=f=120,treble=g={sparkle:.1f}:f=5200:w=0.8,equalizer=f=1600:t=q:w=1.1:g={lightness:.1f},alimiter=limit=0.94"

def bright_voice_filter(values):
    return pedalboard_effect("bright_voice", values, ffmpeg_bright_voice_filter(values))


def ffmpeg_megaphone_filter(values):
    narrow = parameter_value(values, "narrow", 55)
    drive = shaped_scale(parameter_value(values, "drive", 55), 1.0, 3.8, 1.15)
    rough = parameter_value(values, "rough", 35)
    low = int(shaped_scale(narrow, 180, 820, 1.05))
    high = int(shaped_scale(100 - narrow, 2200, 5800, 1.0))
    bits = int(shaped_scale(100 - rough, 5, 20, 1.0))
    mix = shaped_scale(rough, 0.0, 0.52, 1.25)
    return f"highpass=f={low},lowpass=f={high},volume={drive:.2f},equalizer=f=1800:t=q:w=0.8:g=8,acrusher=bits={bits}:mix={mix:.2f},alimiter=limit=0.88"

def megaphone_filter(values):
    return pedalboard_effect("megaphone", values, ffmpeg_megaphone_filter(values))


def ffmpeg_underwater_filter(values):
    muffled = parameter_value(values, "muffled", 60)
    movement = parameter_value(values, "movement", 45)
    depth = parameter_value(values, "depth", 45)
    lowpass = int(shaped_scale(100 - muffled, 420, 3400, 1.25))
    tremolo_speed = shaped_scale(movement, 0.35, 5.2, 1.05)
    tremolo_depth = shaped_scale(movement, 0.03, 0.55, 1.2)
    echo_delay = int(shaped_scale(depth, 60, 360, 1.1))
    echo_decay = shaped_scale(depth, 0.04, 0.40, 1.15)
    return f"lowpass=f={lowpass},bass=g=4:f=150:w=0.9,tremolo=f={tremolo_speed:.2f}:d={tremolo_depth:.2f},aecho=0.65:0.72:{echo_delay}:{echo_decay:.2f},alimiter=limit=0.94"

def underwater_filter(values):
    return pedalboard_effect("underwater", values, ffmpeg_underwater_filter(values))


def ffmpeg_space_motion_filter(values):
    space = parameter_value(values, "space", 55)
    motion = parameter_value(values, "motion", 45)
    width = parameter_value(values, "width", 50)
    delay_one = int(shaped_scale(space, 90, 650, 1.1))
    delay_two = int(shaped_scale(space, 190, 980, 1.1))
    decay_one = shaped_scale(space, 0.08, 0.55, 1.2)
    decay_two = shaped_scale(space, 0.05, 0.42, 1.2)
    chorus_delay = shaped_scale(width, 14, 92, 1.0)
    chorus_decay = shaped_scale(width, 0.08, 0.78, 1.1)
    chorus_speed = shaped_scale(motion, 0.08, 1.35, 1.1)
    chorus_depth = shaped_scale(motion, 0.06, 0.88, 1.15)
    return f"chorus=0.6:0.9:{chorus_delay:.1f}:{chorus_decay:.2f}:{chorus_speed:.2f}:{chorus_depth:.2f},aecho=0.68:0.82:{delay_one}|{delay_two}:{decay_one:.2f}|{decay_two:.2f},alimiter=limit=0.94"

def space_motion_filter(values):
    return pedalboard_effect("space_motion", values, ffmpeg_space_motion_filter(values))


def ffmpeg_safe_boost_filter(values):
    boost = shaped_scale(parameter_value(values, "boost", 55), 1.0, 4.0, 1.15)
    compression = shaped_scale(parameter_value(values, "protection", 60), -38, -12, 1.0)
    clarity = shaped_scale(parameter_value(values, "clarity", 40), -1, 8, 1.05)
    return f"volume={boost:.2f},equalizer=f=2600:t=q:w=1.0:g={clarity:.1f},compand=attacks=0.08:decays=0.45:points=-80/-80|{compression:.1f}/-22|-8/-6|0/-1.5,loudnorm=I=-15:TP=-1.2:LRA=7,alimiter=limit=0.9"

def safe_boost_filter(values):
    return pedalboard_effect("safe_boost", values, ffmpeg_safe_boost_filter(values))


def ffmpeg_volume_level_filter(values):
    level = max(100.0, min(400.0, float(parameter_value(values, "level", 100))))
    return f"volume={level / 100.0:.4f}"

def volume_level_filter(values):
    return pedalboard_effect("volume_level", values, ffmpeg_volume_level_filter(values))


def ffmpeg_robot_filter(values):
    crush = int(shaped_scale(100 - parameter_value(values, "metal", 55), 4, 20, 1.0))
    tremolo_depth = shaped_scale(parameter_value(values, "movement", 40), 0.08, 0.92, 1.1)
    tremolo_freq = shaped_scale(parameter_value(values, "movement", 40), 4, 32, 1.0)
    return f"highpass=f=120,acrusher=bits={crush}:mix=0.75,tremolo=f={tremolo_freq:.1f}:d={tremolo_depth:.2f},equalizer=f=1200:t=q:w=1.2:g=5,alimiter=limit=0.95"

def robot_filter(values):
    return pedalboard_effect("robot", values, ffmpeg_robot_filter(values))


def ffmpeg_radio_phone_filter(values):
    narrow = parameter_value(values, "narrow", 55)
    low = int(shaped_scale(narrow, 180, 720, 1.05))
    high = int(shaped_scale(100 - narrow, 1900, 5600, 1.0))
    drive = shaped_scale(parameter_value(values, "drive", 40), 1.0, 2.9, 1.1)
    noise = parameter_value(values, "noise", 15)
    bits = int(shaped_scale(100 - noise, 5, 18, 1.0))
    mix = shaped_scale(noise, 0.0, 0.65, 1.2)
    return f"highpass=f={low},lowpass=f={high},volume={drive:.2f},acrusher=bits={bits}:mix={mix:.2f},alimiter=limit=0.9"

def radio_phone_filter(values):
    return pedalboard_effect("radio_phone", values, ffmpeg_radio_phone_filter(values))


def ffmpeg_flanger_filter(values):
    delay = shaped_scale(parameter_value(values, "depth", 50), 0.4, 10.0, 1.15)
    depth = shaped_scale(parameter_value(values, "depth", 50), 0.6, 9.5, 1.15)
    speed = shaped_scale(parameter_value(values, "speed", 45), 0.05, 1.75, 1.15)
    feedback = shaped_scale(parameter_value(values, "feedback", 35), -20, 82, 1.0)
    return f"flanger=delay={delay:.1f}:depth={depth:.1f}:regen={feedback:.1f}:speed={speed:.2f}:width=70,alimiter=limit=0.95"

def flanger_filter(values):
    return pedalboard_effect("flanger", values, ffmpeg_flanger_filter(values))


def ffmpeg_chorus_filter(values):
    delay = shaped_scale(parameter_value(values, "width", 55), 12, 88, 1.0)
    decay = shaped_scale(parameter_value(values, "width", 55), 0.08, 0.78, 1.1)
    speed = shaped_scale(parameter_value(values, "speed", 45), 0.08, 1.1, 1.1)
    depth = shaped_scale(parameter_value(values, "depth", 45), 0.05, 0.86, 1.15)
    return f"chorus=0.65:0.9:{delay:.1f}:{decay:.2f}:{speed:.2f}:{depth:.2f},alimiter=limit=0.95"

def chorus_filter(values):
    return pedalboard_effect("chorus", values, ffmpeg_chorus_filter(values))


def ffmpeg_vibrato_filter(values):
    speed = shaped_scale(parameter_value(values, "speed", 45), 1.5, 13.5, 1.0)
    depth = shaped_scale(parameter_value(values, "depth", 35), 0.04, 0.95, 1.2)
    return f"vibrato=f={speed:.1f}:d={depth:.2f},alimiter=limit=0.95"

def vibrato_filter(values):
    return pedalboard_effect("vibrato", values, ffmpeg_vibrato_filter(values))


def ffmpeg_tremolo_filter(values):
    speed = shaped_scale(parameter_value(values, "speed", 50), 1.0, 22.0, 1.0)
    depth = shaped_scale(parameter_value(values, "depth", 55), 0.04, 0.98, 1.15)
    return f"tremolo=f={speed:.1f}:d={depth:.2f},alimiter=limit=0.95"

def tremolo_filter(values):
    return pedalboard_effect("tremolo", values, ffmpeg_tremolo_filter(values))


def reverse_filter(values):
    return "areverse"


def ffmpeg_pitch_filter(values):
    pitch = shaped_scale(parameter_value(values, "pitch", 60), 0.60, 1.70, 1.0)
    return f"rubberband=pitch={pitch:.3f},alimiter=limit=0.95"

def pitch_filter(values):
    return pedalboard_effect("pitch", values, ffmpeg_pitch_filter(values))


def ffmpeg_bass_treble_filter(values):
    bass_gain = shaped_scale(parameter_value(values, "bass", 55), -9, 16)
    treble_gain = shaped_scale(parameter_value(values, "treble", 55), -8, 15)
    return f"bass=g={bass_gain:.1f}:f=110:w=0.7,treble=g={treble_gain:.1f}:f=4200:w=0.7,alimiter=limit=0.95"

def bass_treble_filter(values):
    return pedalboard_effect("bass_treble", values, ffmpeg_bass_treble_filter(values))


def ffmpeg_normalize_filter(values):
    loudness = shaped_scale(parameter_value(values, "level", 55), -23, -12, 1.0)
    compression = shaped_scale(parameter_value(values, "stability", 55), -42, -14, 1.0)
    return f"compand=attacks=0.18:decays=0.7:points=-80/-80|{compression:.1f}/-24|-10/-8|0/-2,loudnorm=I={loudness:.1f}:TP=-1.5:LRA=8,alimiter=limit=0.95"

def normalize_filter(values):
    return pedalboard_effect("normalize", values, ffmpeg_normalize_filter(values))


EFFECT_DESCRIPTIONS = {
    "clean_voice": "يعالج الكلام بمحرك تنقية احترافي 48 كيلوهرتز لتقليل الضوضاء مع الحفاظ على طبيعة الصوت",
    "remove_silence": "يحذف فترات الصمت من التحديد أو الملف مع معاينة فورية، ويحافظ على تزامن الصوت والفيديو.",
    "strong_noise": "يقلل الضوضاء الثابتة القوية مثل الهسهسة والمروحة",
    "echo_reverb": "يضيف صدى متكرر مع مساحة صوتية ويمكن توليد ذيل للصدى",
    "reverb_studio": "يضيف ريفيرب ناعم للتحكم في حجم المكان وامتداد الذيل",
    "mosque_reverb": "يعطي إحساس مسجد واسع مع الحفاظ على وضوح الكلام",
    "hall": "يعطي إحساس قاعة واسعة أو مسرح مع ذيل صوتي ناعم",
    "cinematic": "يعطي الصوت عمقا وحضورا سينمائيا مع ضغط واضح",
    "deep_voice": "يجعل طبقة الصوت أعمق وأكثر امتلاء",
    "bright_voice": "يرفع لمعان الصوت وخفته مع طبقة أعلى",
    "megaphone": "يجعل الصوت كأنه خارج من مكبر نداء",
    "underwater": "يجعل الصوت مكتوما ومتموجا كأنه تحت الماء",
    "space_motion": "يوسع المجال الصوتي ويضيف حركة وطبقات",
    "safe_boost": "يرفع مستوى الصوت مع حماية من التشويه",
    "volume_level": "يرفع مستوى الصوت مباشرة من 100 إلى 400 بالمئة بدون معالجة إضافية",
    "robot": "يحول الصوت إلى طابع آلي ومعدني",
    "radio_phone": "يجعل الصوت ضيقا مثل الهاتف أو جهاز لاسلكي",
    "flanger": "يضيف تموجا معدنياً متحركا للصوت",
    "chorus": "يوسع الصوت كأنه عدة طبقات متقاربة",
    "vibrato": "يهز طبقة الصوت صعودا وهبوطا",
    "tremolo": "يرفع ويخفض مستوى الصوت بسرعة كنبضات",
    "pitch": "يرفع أو يخفض طبقة الصوت مع الحفاظ على السرعة قدر الإمكان",
    "reverse": "يشغل الصوت بالعكس بدون إعدادات إضافية",
    "bass_treble": "يضبط الجهير والحدة بسرعة",
    "normalize": "يوحد مستوى الصوت ليصبح السماع أوضح وأكثر ثباتا",
    "fade": "يرفع الصوت تدريجيا أو يخفضه داخل الجزء المحدد مع مستوى وشكل ومدة قابلة للضبط",
    "stereo_balance": "يصلح الصوت القادم من سماعة واحدة (يمين أو يسار فقط) ويوازن بين القناتين أو يحوله إلى مونو متمركز على السماعتين.",
}


def get_audio_effect_definitions():
    effects = [
        {"key": "clean_voice", "name": "تنقية الصوت الاحترافية", "description": "تنقية ذكية فورية للكلام بمحرك 48 كيلوهرتز: تقلل الهسهسة والمروحة والضجيج المستمر مع أدوات تحفظ طبيعة الصوت وتوضح الحروف.", "builder": dpdfnet_effect, "controls": [{"key": "attenuation", "name": "قوة التنقية", "min": 0, "max": 100, "default": 78, "unit": "بالمئة"}, {"key": "dry_mix", "name": "حفظ الأصل Dry Mix", "min": 0, "max": 70, "default": 12, "unit": "بالمئة"}, {"key": "presence", "name": "Presence وضوح الكلام", "min": 0, "max": 100, "default": 38}, {"key": "de_ess", "name": "De-ess حماية السين والشين", "min": 0, "max": 100, "default": 24}, {"key": "limiter", "name": "Limiter حماية الذروة", "type": "checkbox", "default": True}], "realtime_preview": True},
        {"key": "remove_silence", "name": "إزالة الصمت", "description": "يحذف فترات الصمت من التحديد أو الملف مع معاينة فورية، ويحافظ على تزامن الصوت والفيديو.", "special_action": "remove_silence", "controls": []},
        audio_ducking_effect_definition(),
        {"key": "breath_reduction", "name": "خفض صوت النفس", "description": "يخفض الشهيق والزفير داخل التحديد بسلاسة، بدون حذف النفس أو قص أطراف الكلام.", "builder": breath_reduction_effect, "controls": [{"key": "reduction_db", "name": "مقدار خفض النفس", "min": 3, "max": 36, "default": 14, "unit": "ديسيبل"}, {"key": "edge_ms", "name": "نعومة الدخول والخروج", "min": 5, "max": 250, "default": 50, "unit": "مللي ثانية", "tick": 5}, {"key": "air_control", "name": "تهذيب هواء النفس", "min": 0, "max": 100, "default": 35}, {"key": "natural_breath", "name": "حفظ النفس الطبيعي", "min": 0, "max": 70, "default": 18, "unit": "بالمئة"}, {"key": "shape", "name": "نوع النفس", "type": "choice", "choices": [{"label": "متوازن", "value": "balanced"}, {"label": "شهيق حاد", "value": "sharp"}, {"label": "زفير قريب من الميكروفون", "value": "close"}, {"label": "نفس خفيف", "value": "soft"}], "default": "balanced"}, {"key": "protect_words", "name": "حماية أطراف الكلمات", "type": "checkbox", "default": True}]},
        {"key": "echo_reverb", "name": "صدى", "description": "", "builder": echo_reverb_studio_effect, "controls": [{"key": "echoes", "name": "عدد الصدى", "type": "choice", "choices": echo_repeat_choices(), "default": 4, "unit": "مرة"}, {"key": "delay", "name": "التأخير", "type": "choice", "choices": echo_time_choices(), "default": 300, "unit": ""}, {"key": "volume", "name": "مستوى الصدى", "min": 0, "max": 100, "default": 45, "unit": "بالمئة"}, {"key": "feedback", "name": "التغذية الراجعة", "min": 0, "max": 100, "default": 18, "unit": "بالمئة"}, {"key": "reverb", "name": "ريفيرب", "type": "checkbox", "default": True}, {"key": "reverb_level", "name": "مستوى الريفيرب", "min": 0, "max": 100, "default": 18, "unit": "بالمئة"}, {"key": "stereo", "name": "استريو", "type": "checkbox", "default": False}, {"key": "tail", "name": "توليد الذيل", "type": "checkbox", "default": True}, {"key": "stereo_width", "name": "عرض الاستريو", "min": 0, "max": 100, "default": 18, "unit": "بالمئة"}]},
        {"key": "reverb_studio", "name": "ريفيرب", "description": "ريفيرب مستقل يتحكم في حجم المكان وامتداد الذيل ونسبة الصوت المعالج وعرض المجال الصوتي.", "builder": reverb_studio_effect, "controls": [{"key": "room", "name": "حجم المكان", "min": 0, "max": 100, "default": 28}, {"key": "tail", "name": "امتداد الذيل", "min": 0, "max": 100, "default": 24}, {"key": "wet", "name": "مستوى الريفيرب", "min": 0, "max": 100, "default": 18}, {"key": "width", "name": "عرض الصوت", "min": 0, "max": 100, "default": 48}]},
        {"key": "mosque_reverb", "name": "ريفيرب المسجد", "description": "", "builder": mosque_reverb_effect, "controls": [{"key": "wet", "name": "مستوى الريفيرب", "min": 0, "max": 100, "default": 24}, {"key": "clarity", "name": "وضوح الكلام", "min": 0, "max": 100, "default": 42}, {"key": "warmth", "name": "دفء الصوت", "min": 0, "max": 100, "default": 44}]},
        {"key": "hall", "name": "قاعة واسعة", "description": "إحساس قاعة أو مسجد أو مسرح واسع مع ذيل صوتي ناعم.", "builder": hall_filter, "controls": [{"key": "room", "name": "حجم المكان", "min": 0, "max": 100, "default": 38}, {"key": "tail", "name": "امتداد الذيل", "min": 0, "max": 100, "default": 32}, {"key": "warmth", "name": "دفء المكان", "min": 0, "max": 100, "default": 48}]},
        {"key": "cinematic", "name": "صوت سينمائي قوي", "description": "تعميق وتضخيم الصوت مع وضوح وضغط قوي وثبات في المستوى.", "builder": cinematic_voice_filter, "controls": [{"key": "bass", "name": "عمق الصوت", "min": 0, "max": 100, "default": 45}, {"key": "clarity", "name": "لمعان الكلام", "min": 0, "max": 100, "default": 35}, {"key": "punch", "name": "قوة الحضور", "min": 0, "max": 100, "default": 34}]},
        {"key": "deep_voice", "name": "صوت عميق جدا", "description": "يخفض طبقة الصوت ويزيد العمق والامتلاء مع إمكانية إضافة خشونة قوية.", "builder": deep_voice_filter, "controls": [{"key": "depth", "name": "عمق الطبقة", "min": 0, "max": 100, "default": 60}, {"key": "bass", "name": "قوة الجهير", "min": 0, "max": 100, "default": 60}, {"key": "growl", "name": "خشونة الصوت", "min": 0, "max": 100, "default": 35}]},
        {"key": "bright_voice", "name": "صوت حاد وخفيف", "description": "يرفع طبقة الصوت ويزيد اللمعان والخفة مع تحكم في حدة الكلام.", "builder": bright_voice_filter, "controls": [{"key": "pitch", "name": "ارتفاع الطبقة", "min": 0, "max": 100, "default": 55}, {"key": "sparkle", "name": "لمعان الصوت", "min": 0, "max": 100, "default": 50}, {"key": "lightness", "name": "خفة الكلام", "min": 0, "max": 100, "default": 45}]},
        {"key": "megaphone", "name": "مكبر صوت", "description": "إحساس نداء أو مكبر صوت في شارع أو قاعة مع دفع وخشونة يمكن التحكم فيهما.", "builder": megaphone_filter, "controls": [{"key": "narrow", "name": "ضيق النطاق", "min": 0, "max": 100, "default": 55}, {"key": "drive", "name": "قوة النداء", "min": 0, "max": 100, "default": 55}, {"key": "rough", "name": "خشونة المكبر", "min": 0, "max": 100, "default": 35}]},
        {"key": "underwater", "name": "تحت الماء", "description": "يجعل الصوت مكتوما ومتموجا مع عمق وصدى ناعم.", "builder": underwater_filter, "controls": [{"key": "muffled", "name": "كتم الصوت", "min": 0, "max": 100, "default": 60}, {"key": "movement", "name": "حركة الموج", "min": 0, "max": 100, "default": 45}, {"key": "depth", "name": "عمق المكان", "min": 0, "max": 100, "default": 45}]},
        {"key": "space_motion", "name": "فضاء وحركة", "description": "اتساع وحركة صوتية مع طبقات وصدى وتحكم في عرض المجال.", "builder": space_motion_filter, "controls": [{"key": "space", "name": "اتساع الفضاء", "min": 0, "max": 100, "default": 55}, {"key": "motion", "name": "حركة الصوت", "min": 0, "max": 100, "default": 45}, {"key": "width", "name": "عرض الطبقات", "min": 0, "max": 100, "default": 50}]},
        {"key": "safe_boost", "name": "تضخيم آمن", "description": "يرفع الصوت بقوة مع حماية من التشويه وتحسين وضوح الكلام.", "builder": safe_boost_filter, "controls": [{"key": "boost", "name": "قوة التضخيم", "min": 0, "max": 100, "default": 55}, {"key": "protection", "name": "حماية من التشويه", "min": 0, "max": 100, "default": 60}, {"key": "clarity", "name": "وضوح إضافي", "min": 0, "max": 100, "default": 40}]},
        {"key": "volume_level", "name": "حجم مستوى الصوت", "description": "رفع مباشر لمستوى الصوت داخل التحديد من 100 إلى 400 بالمئة، بدون كومبرسور أو ليمتر.", "builder": volume_level_filter, "controls": [{"key": "level", "name": "حجم مستوى الصوت", "min": 100, "max": 400, "default": 100, "unit": "بالمئة", "step": 5, "page_step": 10, "home_value": 400, "end_value": 100, "tick": 5}]},
        {"key": "robot", "name": "روبوت وميكانيكي", "description": "تحويل الصوت إلى إحساس روبوت أو جهاز إلكتروني.", "builder": robot_filter, "controls": [{"key": "metal", "name": "معدنية الصوت", "min": 0, "max": 100, "default": 55}, {"key": "movement", "name": "حركة الروبوت", "min": 0, "max": 100, "default": 40}]},
        {"key": "radio_phone", "name": "راديو وهاتف", "description": "صوت مكالمة أو جهاز لاسلكي مع تضييق النطاق وإمكانية إضافة خشونة خفيفة.", "builder": radio_phone_filter, "controls": [{"key": "narrow", "name": "ضيق النطاق", "min": 0, "max": 100, "default": 55}, {"key": "drive", "name": "دفع الصوت", "min": 0, "max": 100, "default": 40}, {"key": "noise", "name": "خشونة خفيفة", "min": 0, "max": 100, "default": 15}]},
        {"key": "flanger", "name": "فلانجر", "description": "تموج صوتي واضح مع تحكم في العمق والسرعة ورجوع التأثير.", "builder": flanger_filter, "controls": [{"key": "depth", "name": "عمق التموج", "min": 0, "max": 100, "default": 50}, {"key": "speed", "name": "سرعة التموج", "min": 0, "max": 100, "default": 45}, {"key": "feedback", "name": "رجوع التأثير", "min": 0, "max": 100, "default": 35}]},
        {"key": "chorus", "name": "كورَس وتوسيع", "description": "يوسع الصوت ويجعله كأنه أكثر من طبقة.", "builder": chorus_filter, "controls": [{"key": "width", "name": "عرض الصوت", "min": 0, "max": 100, "default": 55}, {"key": "speed", "name": "سرعة الحركة", "min": 0, "max": 100, "default": 45}, {"key": "depth", "name": "عمق الطبقات", "min": 0, "max": 100, "default": 45}]},
        {"key": "vibrato", "name": "اهتزاز الصوت", "description": "اهتزاز واضح في طبقة الصوت مع تحكم في السرعة والعمق.", "builder": vibrato_filter, "controls": [{"key": "speed", "name": "سرعة الاهتزاز", "min": 0, "max": 100, "default": 45}, {"key": "depth", "name": "عمق الاهتزاز", "min": 0, "max": 100, "default": 35}]},
        {"key": "tremolo", "name": "تقطيع الصوت", "description": "رفع وخفض سريع لمستوى الصوت يعطي إحساس تقطيع إيقاعي.", "builder": tremolo_filter, "controls": [{"key": "speed", "name": "سرعة التقطيع", "min": 0, "max": 100, "default": 50}, {"key": "depth", "name": "عمق التقطيع", "min": 0, "max": 100, "default": 55}]},
        {"key": "pitch", "name": "تغيير طبقة الصوت", "description": "رفع أو خفض طبقة الصوت مع الحفاظ على السرعة قدر الإمكان.", "builder": pitch_filter, "controls": [{"key": "pitch", "name": "طبقة الصوت", "min": 0, "max": 100, "default": 60}]},
        {"key": "fade", "name": "تلاشي الصوت", "description": "تلاشي تدريجي للأعلى أو للأسفل على الجزء المحدد، بأسلوب خطي أو طبيعي مثل GoldWave.", "builder": goldwave_fade_effect, "controls": [{"key": "direction", "name": "اتجاه التلاشي", "type": "choice", "choices": [{"label": "تلاشي تدريجي للأعلى", "value": "in"}, {"label": "تلاشي تدريجي للأسفل", "value": "out"}], "default": "in"}, {"key": "curve", "name": "شكل التلاشي", "type": "choice", "choices": [{"label": "طبيعي", "value": "log"}, {"label": "خطي", "value": "tri"}], "default": "log"}, {"key": "level_db", "name": "مستوى بداية التلاشي للأعلى أو نهاية التلاشي للأسفل", "min": -160, "max": 0, "default": -160, "unit": "ديسيبل", "tick": 5}, {"key": "duration_percent", "name": "مدة التلاشي كنسبة من التحديد، الأقل أسرع", "min": 1, "max": 100, "default": 100, "unit": "بالمئة"}]},
        {"key": "reverse", "name": "عكس الصوت", "description": "تشغيل الصوت بالعكس بدون تحكمات إضافية.", "builder": reverse_filter, "controls": [], "realtime_preview": False},
        {"key": "bass_treble", "name": "جهير وحدّة", "description": "تحكم سريع في عمق الصوت ولمعانه.", "builder": bass_treble_filter, "controls": [{"key": "bass", "name": "الجهير", "min": 0, "max": 100, "default": 55}, {"key": "treble", "name": "الحدة", "min": 0, "max": 100, "default": 55}]},
        {"key": "normalize", "name": "توحيد مستوى الصوت المتقدم", "description": "توحيد الصوت مع ضغط خفيف ليصبح السماع أوضح وثابتا.", "builder": normalize_filter, "controls": [{"key": "level", "name": "مستوى الصوت", "min": 0, "max": 100, "default": 55}, {"key": "stability", "name": "ثبات المستوى", "min": 0, "max": 100, "default": 55}]},
        {"key": "stereo_balance", "name": "موازنة الصوت والتوازن الصوتي", "description": "يصلح الصوت القادم من سماعة واحدة (يمين أو يسار فقط) ويوازن بين القناتين أو يحوله إلى مونو متمركز على السماعتين.", "builder": stereo_balance_effect, "controls": [{"key": "mode", "name": "نمط الموازنة", "type": "choice", "choices": [{"label": "توازن استريو بين اليمين واليسار", "value": "stereo_balance"}, {"label": "تمركز الصوت على السماعتين (مونو)", "value": "center_mono"}, {"label": "القناة اليسرى على السماعتين معاً", "value": "left_to_both"}, {"label": "القناة اليمنى على السماعتين معاً", "value": "right_to_both"}], "default": "stereo_balance"}, {"key": "balance", "name": "موازنة اليمين واليسار", "min": -100, "max": 100, "default": 0, "unit": "بالمئة", "step": 5, "page_step": 10, "home_value": 0, "end_value": 100, "tick": 5}, {"key": "volume", "name": "مستوى الصوت العام", "min": 0, "max": 200, "default": 100, "unit": "بالمئة", "step": 5, "page_step": 10, "home_value": 100, "end_value": 200, "tick": 5}]},
    ]
    presets = {
        "clean_voice": [
            {"name": "مرتل", "values": {"noise": 42, "clarity": 38, "compression": 34}},
            {"name": "تنقية خفيفة", "values": {"noise": 28, "clarity": 32, "compression": 22}},
            {"name": "تنقية قوية", "values": {"noise": 68, "clarity": 48, "compression": 48}},
        ],
        "strong_noise": [
            {"name": "مرتل", "values": {"noise": 58, "clarity": 34, "warmth": 42}},
            {"name": "ضوضاء خفيفة", "values": {"noise": 38, "clarity": 30, "warmth": 38}},
            {"name": "ضوضاء شديدة", "values": {"noise": 78, "clarity": 48, "warmth": 48}},
        ],
        "echo_reverb": [
            {"name": "مرتل", "values": {"echoes": 4, "delay": 300, "volume": 45, "feedback": 18, "reverb": True, "reverb_level": 18, "stereo": False, "tail": True, "stereo_width": 18}},
            {"name": "صدى خفيف", "values": {"echoes": 3, "delay": 200, "volume": 35, "feedback": 8, "reverb": True, "reverb_level": 10, "stereo": False, "tail": True, "stereo_width": 12}},
            {"name": "صدى واضح", "values": {"echoes": 5, "delay": 500, "volume": 52, "feedback": 28, "reverb": True, "reverb_level": 28, "stereo": True, "tail": True, "stereo_width": 24}},
        ],
        "reverb_studio": [
            {"name": "مرتل", "values": {"room": 28, "tail": 24, "wet": 18, "width": 48}},
            {"name": "مكان صغير", "values": {"room": 18, "tail": 16, "wet": 12, "width": 40}},
            {"name": "مكان واسع", "values": {"room": 48, "tail": 42, "wet": 28, "width": 62}},
        ],
        "mosque_reverb": [
            {"name": "مرتل", "values": {"wet": 24, "clarity": 42, "warmth": 44}},
            {"name": "خطبة", "values": {"wet": 28, "clarity": 48, "warmth": 38}},
            {"name": "واسع", "values": {"wet": 36, "clarity": 36, "warmth": 46}},
        ],
        "deep_voice": [
            {"name": "افتراضي", "values": {"depth": 60, "bass": 60, "growl": 35}},
            {"name": "عمق خفيف", "values": {"depth": 35, "bass": 45, "growl": 15}},
            {"name": "عمق شديد", "values": {"depth": 85, "bass": 80, "growl": 50}},
        ],
        "bright_voice": [
            {"name": "افتراضي", "values": {"pitch": 55, "sparkle": 50, "lightness": 45}},
            {"name": "رفع خفيف", "values": {"pitch": 35, "sparkle": 40, "lightness": 35}},
            {"name": "رفع شديد", "values": {"pitch": 85, "sparkle": 70, "lightness": 65}},
        ],
        "safe_boost": [
            {"name": "مرتل", "values": {"boost": 34, "protection": 58, "clarity": 28}},
            {"name": "رفع خفيف", "values": {"boost": 24, "protection": 48, "clarity": 22}},
            {"name": "رفع قوي", "values": {"boost": 62, "protection": 74, "clarity": 42}},
        ],
        "volume_level": [
            {"name": "100 بالمئة", "values": {"level": 100}},
            {"name": "150 بالمئة", "values": {"level": 150}},
            {"name": "200 بالمئة", "values": {"level": 200}},
            {"name": "400 بالمئة", "values": {"level": 400}},
        ],
        "bass_treble": [
            {"name": "مرتل", "values": {"bass": 44, "treble": 34}},
            {"name": "جهير أكثر", "values": {"bass": 62, "treble": 30}},
            {"name": "حدة أكثر", "values": {"bass": 38, "treble": 52}},
        ],
        "stereo_balance": [
            {"name": "توازن متمركز", "values": {"mode": "stereo_balance", "balance": 0, "volume": 100}},
            {"name": "تمركز الصوت على السماعتين (مونو)", "values": {"mode": "center_mono", "balance": 0, "volume": 100}},
            {"name": "إصلاح السماعة اليسرى (اليسار على السماعتين)", "values": {"mode": "left_to_both", "balance": 0, "volume": 100}},
            {"name": "إصلاح السماعة اليمنى (اليمين على السماعتين)", "values": {"mode": "right_to_both", "balance": 0, "volume": 100}},
            {"name": "انحياز لليمين", "values": {"mode": "stereo_balance", "balance": 50, "volume": 100}},
            {"name": "انحياز لليسار", "values": {"mode": "stereo_balance", "balance": -50, "volume": 100}},
        ],
        "normalize": [
            {"name": "مرتل", "values": {"level": 45, "stability": 38}},
            {"name": "توحيد خفيف", "values": {"level": 36, "stability": 28}},
            {"name": "توحيد قوي", "values": {"level": 62, "stability": 58}},
        ],
    }
    safe_presets = {
        "clean_voice": [
            {"name": "هادئ", "values": {"noise": 34, "clarity": 28, "compression": 24}},
            {"name": "واضح", "values": {"noise": 44, "clarity": 34, "compression": 30}},
            {"name": "تنقية أكثر", "values": {"noise": 58, "clarity": 38, "compression": 36}},
        ],
        "strong_noise": [
            {"name": "هادئ", "values": {"noise": 46, "clarity": 24, "warmth": 36}},
            {"name": "متوازن", "values": {"noise": 58, "clarity": 30, "warmth": 40}},
            {"name": "ضوضاء أكثر", "values": {"noise": 72, "clarity": 36, "warmth": 42}},
        ],
        "echo_reverb": [
            {"name": "هادئ", "values": {"echoes": 4, "delay": 300, "volume": 45, "feedback": 18, "reverb": True, "reverb_level": 18, "stereo": False, "tail": True, "stereo_width": 18}},
            {"name": "متوازن", "values": {"echoes": 5, "delay": 500, "volume": 52, "feedback": 28, "reverb": True, "reverb_level": 28, "stereo": True, "tail": True, "stereo_width": 24}},
            {"name": "واضح", "values": {"echoes": 6, "delay": 700, "volume": 60, "feedback": 36, "reverb": True, "reverb_level": 36, "stereo": True, "tail": True, "stereo_width": 34}},
        ],
        "reverb_studio": [
            {"name": "هادئ", "values": {"room": 16, "tail": 12, "wet": 8, "width": 28}},
            {"name": "متوازن", "values": {"room": 24, "tail": 18, "wet": 12, "width": 38}},
            {"name": "واسع", "values": {"room": 38, "tail": 30, "wet": 22, "width": 54}},
        ],
        "mosque_reverb": [
            {"name": "هادئ", "values": {"wet": 18, "clarity": 44, "warmth": 40}},
            {"name": "متوازن", "values": {"wet": 24, "clarity": 42, "warmth": 44}},
            {"name": "واسع", "values": {"wet": 34, "clarity": 36, "warmth": 48}},
        ],
        "hall": [
            {"name": "هادئ", "values": {"room": 22, "tail": 16, "warmth": 34}},
            {"name": "متوازن", "values": {"room": 32, "tail": 24, "warmth": 40}},
            {"name": "واسع", "values": {"room": 46, "tail": 34, "warmth": 42}},
        ],
        "cinematic": [
            {"name": "هادئ", "values": {"bass": 28, "clarity": 20, "punch": 20}},
            {"name": "متوازن", "values": {"bass": 36, "clarity": 28, "punch": 26}},
            {"name": "واضح", "values": {"bass": 46, "clarity": 34, "punch": 34}},
        ],
        "deep_voice": [
            {"name": "هادئ", "values": {"depth": 18, "bass": 24, "growl": 0}},
            {"name": "متوازن", "values": {"depth": 28, "bass": 34, "growl": 8}},
            {"name": "عميق", "values": {"depth": 42, "bass": 46, "growl": 16}},
        ],
        "bright_voice": [
            {"name": "هادئ", "values": {"pitch": 48, "sparkle": 22, "lightness": 18}},
            {"name": "متوازن", "values": {"pitch": 54, "sparkle": 30, "lightness": 26}},
            {"name": "واضح", "values": {"pitch": 62, "sparkle": 38, "lightness": 34}},
        ],
        "megaphone": [
            {"name": "هادئ", "values": {"narrow": 24, "drive": 18, "rough": 0}},
            {"name": "متوازن", "values": {"narrow": 34, "drive": 28, "rough": 8}},
            {"name": "واضح", "values": {"narrow": 46, "drive": 38, "rough": 16}},
        ],
        "underwater": [
            {"name": "هادئ", "values": {"muffled": 28, "movement": 14, "depth": 12}},
            {"name": "متوازن", "values": {"muffled": 40, "movement": 24, "depth": 20}},
            {"name": "واضح", "values": {"muffled": 54, "movement": 34, "depth": 30}},
        ],
        "space_motion": [
            {"name": "هادئ", "values": {"space": 18, "motion": 12, "width": 20}},
            {"name": "متوازن", "values": {"space": 28, "motion": 20, "width": 32}},
            {"name": "واسع", "values": {"space": 42, "motion": 30, "width": 48}},
        ],
        "safe_boost": [
            {"name": "هادئ", "values": {"boost": 20, "protection": 50, "clarity": 18}},
            {"name": "متوازن", "values": {"boost": 30, "protection": 58, "clarity": 24}},
            {"name": "أعلى", "values": {"boost": 46, "protection": 70, "clarity": 32}},
        ],
        "robot": [
            {"name": "هادئ", "values": {"metal": 12, "movement": 10}},
            {"name": "متوازن", "values": {"metal": 24, "movement": 18}},
            {"name": "واضح", "values": {"metal": 38, "movement": 28}},
        ],
        "radio_phone": [
            {"name": "هادئ", "values": {"narrow": 24, "drive": 16, "noise": 0}},
            {"name": "متوازن", "values": {"narrow": 38, "drive": 24, "noise": 6}},
            {"name": "واضح", "values": {"narrow": 54, "drive": 34, "noise": 12}},
        ],
        "flanger": [
            {"name": "هادئ", "values": {"depth": 10, "speed": 12, "feedback": 0}},
            {"name": "متوازن", "values": {"depth": 22, "speed": 20, "feedback": 8}},
            {"name": "واضح", "values": {"depth": 36, "speed": 28, "feedback": 16}},
        ],
        "chorus": [
            {"name": "هادئ", "values": {"width": 18, "speed": 12, "depth": 12}},
            {"name": "متوازن", "values": {"width": 30, "speed": 20, "depth": 22}},
            {"name": "واسع", "values": {"width": 44, "speed": 28, "depth": 34}},
        ],
        "vibrato": [
            {"name": "هادئ", "values": {"speed": 18, "depth": 8}},
            {"name": "متوازن", "values": {"speed": 28, "depth": 16}},
            {"name": "واضح", "values": {"speed": 40, "depth": 26}},
        ],
        "tremolo": [
            {"name": "هادئ", "values": {"speed": 16, "depth": 12}},
            {"name": "متوازن", "values": {"speed": 26, "depth": 24}},
            {"name": "واضح", "values": {"speed": 40, "depth": 38}},
        ],
        "pitch": [
            {"name": "طبيعي", "values": {"pitch": 42}},
            {"name": "أقل", "values": {"pitch": 28}},
            {"name": "أعلى", "values": {"pitch": 56}},
        ],
        "bass_treble": [
            {"name": "هادئ", "values": {"bass": 34, "treble": 24}},
            {"name": "دافئ", "values": {"bass": 44, "treble": 22}},
            {"name": "أوضح", "values": {"bass": 32, "treble": 38}},
        ],
        "stereo_balance": [
            {"name": "توازن متمركز", "values": {"mode": "stereo_balance", "balance": 0, "volume": 100}},
            {"name": "تمركز الصوت على السماعتين (مونو)", "values": {"mode": "center_mono", "balance": 0, "volume": 100}},
            {"name": "إصلاح السماعة اليسرى (اليسار على السماعتين)", "values": {"mode": "left_to_both", "balance": 0, "volume": 100}},
            {"name": "إصلاح السماعة اليمنى (اليمين على السماعتين)", "values": {"mode": "right_to_both", "balance": 0, "volume": 100}},
            {"name": "انحياز لليمين", "values": {"mode": "stereo_balance", "balance": 50, "volume": 100}},
            {"name": "انحياز لليسار", "values": {"mode": "stereo_balance", "balance": -50, "volume": 100}},
        ],
        "normalize": [
            {"name": "هادئ", "values": {"level": 34, "stability": 24}},
            {"name": "متوازن", "values": {"level": 44, "stability": 34}},
            {"name": "ثابت", "values": {"level": 54, "stability": 46}},
        ],
    }
    presets.update(safe_presets)
    engineer_presets = {
        "clean_voice": [
            {"name": "متوازن", "values": {"noise": 44, "clarity": 42, "compression": 38}},
            {"name": "هادئ", "values": {"noise": 30, "clarity": 30, "compression": 24}},
            {"name": "واضح", "values": {"noise": 62, "clarity": 58, "compression": 54}},
        ],
        "strong_noise": [
            {"name": "متوازن", "values": {"noise": 58, "clarity": 42, "warmth": 38}},
            {"name": "هادئ", "values": {"noise": 42, "clarity": 30, "warmth": 34}},
            {"name": "واضح", "values": {"noise": 78, "clarity": 56, "warmth": 44}},
        ],
        "echo_reverb": [
            {"name": "متوازن", "values": {"echoes": 4, "delay": 300, "volume": 46, "feedback": 22, "reverb": True, "reverb_level": 22, "stereo": False, "tail": True, "stereo_width": 20}},
            {"name": "هادئ", "values": {"echoes": 3, "delay": 180, "volume": 32, "feedback": 10, "reverb": True, "reverb_level": 12, "stereo": False, "tail": True, "stereo_width": 12}},
            {"name": "واضح", "values": {"echoes": 6, "delay": 520, "volume": 58, "feedback": 34, "reverb": True, "reverb_level": 34, "stereo": True, "tail": True, "stereo_width": 34}},
        ],
        "reverb_studio": [
            {"name": "متوازن", "values": {"decay": 42, "pre_delay": 28, "damping": 46, "early": 30, "wet": 26, "dry": 88, "width": 58}},
            {"name": "هادئ", "values": {"decay": 24, "pre_delay": 16, "damping": 62, "early": 18, "wet": 14, "dry": 94, "width": 42}},
            {"name": "واضح", "values": {"decay": 68, "pre_delay": 42, "damping": 34, "early": 42, "wet": 42, "dry": 82, "width": 76}},
        ],
        "mosque_reverb": [
            {"name": "متوازن", "values": {"wet": 30, "clarity": 46, "warmth": 42}},
            {"name": "هادئ", "values": {"wet": 18, "clarity": 50, "warmth": 38}},
            {"name": "واضح", "values": {"wet": 44, "clarity": 40, "warmth": 50}},
        ],
        "hall": [
            {"name": "متوازن", "values": {"room": 42, "tail": 38, "warmth": 40}},
            {"name": "هادئ", "values": {"room": 24, "tail": 18, "warmth": 34}},
            {"name": "واضح", "values": {"room": 64, "tail": 58, "warmth": 48}},
        ],
        "cinematic": [
            {"name": "متوازن", "values": {"bass": 42, "clarity": 36, "punch": 40}},
            {"name": "هادئ", "values": {"bass": 26, "clarity": 24, "punch": 24}},
            {"name": "واضح", "values": {"bass": 64, "clarity": 58, "punch": 62}},
        ],
        "deep_voice": [
            {"name": "متوازن", "values": {"depth": 42, "bass": 44, "growl": 14}},
            {"name": "هادئ", "values": {"depth": 24, "bass": 28, "growl": 0}},
            {"name": "واضح", "values": {"depth": 72, "bass": 66, "growl": 34}},
        ],
        "bright_voice": [
            {"name": "متوازن", "values": {"pitch": 54, "sparkle": 36, "lightness": 34}},
            {"name": "هادئ", "values": {"pitch": 46, "sparkle": 22, "lightness": 18}},
            {"name": "واضح", "values": {"pitch": 74, "sparkle": 62, "lightness": 56}},
        ],
        "megaphone": [
            {"name": "متوازن", "values": {"narrow": 44, "drive": 42, "rough": 18}},
            {"name": "هادئ", "values": {"narrow": 28, "drive": 24, "rough": 0}},
            {"name": "واضح", "values": {"narrow": 68, "drive": 64, "rough": 42}},
        ],
        "underwater": [
            {"name": "متوازن", "values": {"muffled": 50, "movement": 34, "depth": 34}},
            {"name": "هادئ", "values": {"muffled": 28, "movement": 16, "depth": 14}},
            {"name": "واضح", "values": {"muffled": 76, "movement": 58, "depth": 58}},
        ],
        "space_motion": [
            {"name": "متوازن", "values": {"space": 42, "motion": 32, "width": 48}},
            {"name": "هادئ", "values": {"space": 22, "motion": 14, "width": 26}},
            {"name": "واضح", "values": {"space": 68, "motion": 56, "width": 72}},
        ],
        "safe_boost": [
            {"name": "متوازن", "values": {"boost": 40, "protection": 62, "clarity": 34}},
            {"name": "هادئ", "values": {"boost": 22, "protection": 54, "clarity": 18}},
            {"name": "واضح", "values": {"boost": 66, "protection": 76, "clarity": 52}},
        ],
        "volume_level": [
            {"name": "100 بالمئة", "values": {"level": 100}},
            {"name": "150 بالمئة", "values": {"level": 150}},
            {"name": "200 بالمئة", "values": {"level": 200}},
            {"name": "400 بالمئة", "values": {"level": 400}},
        ],
        "robot": [
            {"name": "متوازن", "values": {"metal": 36, "movement": 28}},
            {"name": "هادئ", "values": {"metal": 16, "movement": 12}},
            {"name": "واضح", "values": {"metal": 66, "movement": 52}},
        ],
        "radio_phone": [
            {"name": "متوازن", "values": {"narrow": 48, "drive": 36, "noise": 16}},
            {"name": "هادئ", "values": {"narrow": 28, "drive": 20, "noise": 0}},
            {"name": "واضح", "values": {"narrow": 74, "drive": 58, "noise": 38}},
        ],
        "flanger": [
            {"name": "متوازن", "values": {"depth": 34, "speed": 28, "feedback": 22}},
            {"name": "هادئ", "values": {"depth": 16, "speed": 12, "feedback": 0}},
            {"name": "واضح", "values": {"depth": 62, "speed": 48, "feedback": 46}},
        ],
        "chorus": [
            {"name": "متوازن", "values": {"width": 42, "speed": 26, "depth": 34}},
            {"name": "هادئ", "values": {"width": 20, "speed": 12, "depth": 16}},
            {"name": "واضح", "values": {"width": 68, "speed": 46, "depth": 58}},
        ],
        "vibrato": [
            {"name": "متوازن", "values": {"speed": 36, "depth": 28}},
            {"name": "هادئ", "values": {"speed": 20, "depth": 12}},
            {"name": "واضح", "values": {"speed": 58, "depth": 54}},
        ],
        "tremolo": [
            {"name": "متوازن", "values": {"speed": 36, "depth": 34}},
            {"name": "هادئ", "values": {"speed": 18, "depth": 16}},
            {"name": "واضح", "values": {"speed": 62, "depth": 68}},
        ],
        "pitch": [
            {"name": "متوازن", "values": {"pitch": 50}},
            {"name": "أقل", "values": {"pitch": 32}},
            {"name": "أعلى", "values": {"pitch": 68}},
        ],
        "bass_treble": [
            {"name": "متوازن", "values": {"bass": 42, "treble": 40}},
            {"name": "دافئ", "values": {"bass": 62, "treble": 28}},
            {"name": "أوضح", "values": {"bass": 36, "treble": 60}},
        ],
        "stereo_balance": [
            {"name": "توازن متمركز", "values": {"mode": "stereo_balance", "balance": 0, "volume": 100}},
            {"name": "تمركز الصوت على السماعتين (مونو)", "values": {"mode": "center_mono", "balance": 0, "volume": 100}},
            {"name": "إصلاح السماعة اليسرى (اليسار على السماعتين)", "values": {"mode": "left_to_both", "balance": 0, "volume": 100}},
            {"name": "إصلاح السماعة اليمنى (اليمين على السماعتين)", "values": {"mode": "right_to_both", "balance": 0, "volume": 100}},
            {"name": "انحياز لليمين", "values": {"mode": "stereo_balance", "balance": 50, "volume": 100}},
            {"name": "انحياز لليسار", "values": {"mode": "stereo_balance", "balance": -50, "volume": 100}},
        ],
        "normalize": [
            {"name": "متوازن", "values": {"level": 50, "stability": 44}},
            {"name": "هادئ", "values": {"level": 34, "stability": 28}},
            {"name": "ثابت", "values": {"level": 64, "stability": 66}},
        ],
    }
    presets.update(engineer_presets)
    presets["clean_voice"] = [
        {"name": "متوازن طبيعي", "values": {"attenuation": 78, "dry_mix": 12, "presence": 38, "de_ess": 24, "limiter": True}},
        {"name": "حفظ النبرة", "values": {"attenuation": 58, "dry_mix": 26, "presence": 32, "de_ess": 16, "limiter": True}},
        {"name": "ضوضاء ثابتة قوية", "values": {"attenuation": 94, "dry_mix": 6, "presence": 46, "de_ess": 34, "limiter": True}},
        {"name": "تفاصيل هادئة", "values": {"attenuation": 84, "dry_mix": 10, "presence": 42, "de_ess": 20, "limiter": True}},
    ]
    presets["breath_reduction"] = [
        {"name": "طبيعي", "values": {"reduction_db": 14, "edge_ms": 50, "air_control": 35, "natural_breath": 18, "shape": "balanced", "protect_words": True}},
        {"name": "نفس واضح", "values": {"reduction_db": 20, "edge_ms": 70, "air_control": 48, "natural_breath": 12, "shape": "balanced", "protect_words": True}},
        {"name": "شهيق حاد", "values": {"reduction_db": 18, "edge_ms": 45, "air_control": 72, "natural_breath": 10, "shape": "sharp", "protect_words": True}},
        {"name": "زفير قريب", "values": {"reduction_db": 16, "edge_ms": 85, "air_control": 62, "natural_breath": 16, "shape": "close", "protect_words": True}},
    ]
    presets.pop("strong_noise", None)
    presets["fade"] = [
        {"name": "من الصمت إلى كامل الصوت طبيعي", "values": {"direction": "in", "curve": "log", "level_db": -160, "duration_percent": 100}},
        {"name": "من كامل الصوت إلى الصمت طبيعي", "values": {"direction": "out", "curve": "log", "level_db": -160, "duration_percent": 100}},
        {"name": "من الصمت إلى كامل الصوت خطي", "values": {"direction": "in", "curve": "tri", "level_db": -160, "duration_percent": 100}},
        {"name": "من كامل الصوت إلى الصمت خطي", "values": {"direction": "out", "curve": "tri", "level_db": -160, "duration_percent": 100}},
    ]
    technical_names = {
        "clean_voice": "تنقية الصوت الاحترافية 48 kHz",
        "breath_reduction": "خفض صوت النفس",
        "strong_noise": "خفض الضوضاء المتقدم",
        "echo_reverb": "ديلاي وصدى",
        "reverb_studio": "ريفيرب",
        "mosque_reverb": "كونفولوشن ريفيرب",
        "hall": "ريفيرب قاعة",
        "cinematic": "إيكولايزر وكومبرسور",
        "deep_voice": "تغيير الطبقة للأسفل",
        "bright_voice": "تغيير الطبقة للأعلى",
        "megaphone": "فلتر مكبر صوت",
        "underwater": "لو باس وتموج",
        "space_motion": "كورَس وديلاي عريض",
        "safe_boost": "Gain وLimiter",
        "volume_level": "Gain حجم مستوى الصوت",
        "robot": "Bitcrusher وتريمولو",
        "radio_phone": "Band Pass للهاتف",
        "flanger": "فلانجر",
        "chorus": "كورَس",
        "vibrato": "فيبراتو",
        "tremolo": "تريمولو",
        "pitch": "تغيير الطبقة",
        "fade": "Fade",
        "reverse": "Reverse",
        "bass_treble": "موازن جهير وحدّة",
        "normalize": "توحيد مستوى الصوت",
    }
    user_descriptions = {
        "clean_voice": "ينقي الكلام بمحرك فوري 48 كيلوهرتز، مع تحكم في قوة الخفض، حفظ جزء من الأصل، وضوح الكلام، تهذيب السين والشين، وحماية الذروة.",
        "remove_silence": "يحذف فترات الصمت من التحديد أو الملف مع معاينة فورية، ويحافظ على تزامن الصوت والفيديو.",
        "breath_reduction": "يخفض الشهيق والزفير داخل الجزء المحدد بسلاسة، ويترك أثرا طبيعيا خفيفا بدلا من حذف النفس بالكامل.",
        "strong_noise": "يستخدم مع الهسهسة أو صوت المروحة أو الضجيج الثابت القوي، وقد يغير طبيعة الصوت إذا زادت قوته.",
        "echo_reverb": "يكرر الصوت بعد زمن قصير ويمكن أن يترك أثرا مسموعا بعد نهاية الكلام.",
        "reverb_studio": "يضيف إحساس غرفة أو مساحة حول الصوت بدون تكرار واضح.",
        "mosque_reverb": "يعطي إحساس مكان واسع مثل مسجد أو قاعة كبيرة مع محاولة إبقاء الكلام مفهوما.",
        "hall": "يجعل الصوت كأنه في قاعة أو مسرح ويضيف امتدادا ناعما بعد الكلام.",
        "cinematic": "يزيد عمق الصوت ووضوحه وثباته ليصبح أكثر حضورا.",
        "deep_voice": "يخفض طبقة الصوت ويزيد امتلاءه، والزيادة الكبيرة قد تجعله مصطنعا.",
        "bright_voice": "يرفع طبقة الصوت ويزيد لمعانه وخفته.",
        "megaphone": "يجعل الصوت قريبا من صوت مكبر النداء أو السماعة العامة.",
        "underwater": "يكتم الحدة ويضيف حركة خفيفة ليبدو الصوت كأنه تحت الماء.",
        "space_motion": "يوسع الصوت ويضيف حركة وتأخيرا خفيفا في المجال الصوتي.",
        "safe_boost": "يرفع الصوت مع حماية من التشويه، وقد يقل فرق الرفع إذا كان الصوت عاليا أصلا.",
        "volume_level": "يرفع مستوى الصوت مباشرة داخل الجزء المحدد حتى 400 بالمئة، بدون حماية أو ضغط أو تعديل نبرة؛ مناسب عندما تريد زيادة واضحة وحرفية في الحجم فقط.",
        "robot": "يضيف طابعا آليا ومعدنيا للصوت.",
        "radio_phone": "يضيق الصوت ليشبه الهاتف أو جهاز اللاسلكي.",
        "flanger": "يضيف تموجا معدنياً متحركا يظهر مع الكلام الطويل أو الموسيقى أكثر.",
        "chorus": "يوسع الصوت كأنه أكثر من طبقة قريبة في نفس الوقت.",
        "vibrato": "يهز طبقة الصوت صعودا وهبوطا بدون تغيير مستوى الصوت الأساسي.",
        "tremolo": "يرفع ويخفض مستوى الصوت بسرعة على شكل نبضات.",
        "pitch": "يرفع أو يخفض طبقة الصوت مع الحفاظ على السرعة قدر الإمكان.",
        "fade": "يرفع الصوت تدريجيا أو يخفضه داخل الجزء المحدد.",
        "reverse": "يشغل الصوت بالعكس بدون إعدادات إضافية.",
        "bass_treble": "يزيد أو يقلل الجهير والحدة لتغيير دفء الصوت ولمعانه.",
        "normalize": "يقرب الأصوات الهادئة والعالية من مستوى واحد لتصبح أسهل في السماع.",
    }
    control_names = {
        "clean_voice": {"attenuation": "Attenuation قوة التنقية", "dry_mix": "Dry Mix حفظ الأصل", "presence": "Presence وضوح الكلام", "de_ess": "De-ess حماية السين والشين", "limiter": "Limiter حماية الذروة"},
        "breath_reduction": {"reduction_db": "خفض النفس بالديسيبل", "edge_ms": "Fade نعومة الأطراف", "air_control": "Air تهذيب هواء النفس", "natural_breath": "حفظ النفس الطبيعي", "shape": "نوع النفس", "protect_words": "حماية أطراف الكلمات"},
        "strong_noise": {"noise": "شدة الخفض", "clarity": "تعويض الوضوح", "warmth": "دفء الصوت"},
        "echo_reverb": {"echoes": "عدد التكرارات", "delay": "زمن الديلاي", "volume": "مستوى الصدى", "feedback": "فيدباك", "reverb": "إضافة ريفيرب", "reverb_level": "مستوى الريفيرب", "stereo": "توسيع ستيريو", "tail": "ذيل الصدى", "stereo_width": "عرض الستيريو"},
        "reverb_studio": {"decay": "زمن الاضمحلال", "pre_delay": "Pre Delay", "damping": "Damping", "early": "Early Reflections", "wet": "Wet", "dry": "Dry", "width": "عرض الستيريو"},
        "mosque_reverb": {"wet": "نسبة الريفيرب", "clarity": "وضوح الكلام", "warmth": "دفء الصوت"},
        "hall": {"room": "حجم القاعة", "tail": "طول الذيل", "warmth": "دفء القاعة"},
        "cinematic": {"bass": "جهير", "clarity": "حضور الترددات العالية", "punch": "ضغط وحضور"},
        "deep_voice": {"depth": "خفض الطبقة", "bass": "جهير", "growl": "خشونة"},
        "bright_voice": {"pitch": "رفع الطبقة", "sparkle": "لمعان", "lightness": "خفة الصوت"},
        "megaphone": {"narrow": "ضيق النطاق", "drive": "Drive", "rough": "خشونة"},
        "underwater": {"muffled": "كتم الحدة", "movement": "حركة التموج", "depth": "عمق الأثر"},
        "space_motion": {"space": "اتساع الديلاي", "motion": "حركة الكورَس", "width": "عرض المجال"},
        "safe_boost": {"boost": "Gain", "protection": "حماية Limiter", "clarity": "وضوح إضافي"},
        "volume_level": {"level": "Gain حجم مستوى الصوت"},
        "robot": {"metal": "Bitcrush", "movement": "حركة التريمولو"},
        "radio_phone": {"narrow": "ضيق Band Pass", "drive": "Drive", "noise": "خشونة"},
        "flanger": {"depth": "عمق الفلانجر", "speed": "سرعة الحركة", "feedback": "فيدباك"},
        "chorus": {"width": "عرض الكورَس", "speed": "سرعة الحركة", "depth": "عمق الكورَس"},
        "vibrato": {"speed": "سرعة الفيبراتو", "depth": "عمق الفيبراتو"},
        "tremolo": {"speed": "سرعة التريمولو", "depth": "عمق التريمولو"},
        "pitch": {"pitch": "قيمة الطبقة"},
        "fade": {"direction": "اتجاه Fade", "curve": "منحنى Fade", "level_db": "مستوى البداية أو النهاية", "duration_percent": "مدة Fade من التحديد"},
        "bass_treble": {"bass": "Bass", "treble": "Treble"},
        "normalize": {"level": "مستوى الهدف", "stability": "ثبات الصوت"},
    }
    custom_controls = {
        "reverb_studio": [
            {"key": "decay", "name": "زمن الاضمحلال", "min": 0, "max": 100, "default": 42},
            {"key": "pre_delay", "name": "Pre Delay", "min": 0, "max": 120, "default": 28, "unit": "مللي ثانية", "tick": 5},
            {"key": "damping", "name": "Damping", "min": 0, "max": 100, "default": 46},
            {"key": "early", "name": "Early Reflections", "min": 0, "max": 100, "default": 30},
            {"key": "wet", "name": "Wet", "min": 0, "max": 100, "default": 26},
            {"key": "dry", "name": "Dry", "min": 0, "max": 100, "default": 88},
            {"key": "width", "name": "عرض الستيريو", "min": 0, "max": 100, "default": 58},
        ],
    }
    for effect in effects:
        effect["name"] = technical_names.get(effect["key"], effect["name"])
        effect["description"] = user_descriptions.get(effect["key"], EFFECT_DESCRIPTIONS.get(effect["key"], ""))
        if effect["key"] in custom_controls:
            effect["controls"] = [dict(control) for control in custom_controls[effect["key"]]]
        for control in effect.get("controls", []):
            control["name"] = control_names.get(effect["key"], {}).get(control["key"], control["name"])
        if "presets" not in effect:
            effect["presets"] = presets.get(effect["key"], [{"name": "افتراضي", "values": {control["key"]: control.get("default", False) for control in effect.get("controls", [])}}])
        if effect["presets"]:
            first_values = effect["presets"][0].get("values", {})
            for control in effect.get("controls", []):
                if control["key"] in first_values:
                    control["default"] = first_values[control["key"]]
    return effects


def effect_spoken_label(effect):
    name = tr(effect.get("name", ""))
    description = tr(effect.get("description", "")).strip()
    if description:
        return f"{name} {description}"
    return name


class AudioEffectChooserDialog(wx.Dialog):
    def __init__(self, parent, effects):
        super().__init__(parent, title=tr("المؤثرات الصوتية"), size=(700, 420))
        self.parent = parent
        self.effects = list(effects)
        self.selected_effect = None

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        choices = [effect_spoken_label(effect) for effect in self.effects]
        self.effects_list = wx.ListBox(panel, choices=choices, style=wx.LB_SINGLE | wx.WANTS_CHARS)
        self.effects_list.SetName(tr("قائمة المؤثرات الصوتية"))
        if choices:
            self.effects_list.SetSelection(0)
        sizer.Add(self.effects_list, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)

        self.description = wx.StaticText(panel, label="")
        self.description.SetName(tr("وصف المؤثر الصوتي"))
        sizer.Add(self.description, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        ok_button = wx.Button(panel, wx.ID_OK, tr("موافق"))
        cancel_button = wx.Button(panel, wx.ID_CANCEL, tr("إلغاء"))
        ok_button.SetName(tr("اختيار المؤثر الصوتي"))
        cancel_button.SetName(tr("إلغاء"))
        ok_button.SetDefault()
        buttons.Add(ok_button, flag=wx.ALL, border=6)
        buttons.Add(cancel_button, flag=wx.ALL, border=6)
        sizer.Add(buttons, flag=wx.ALIGN_CENTER | wx.BOTTOM, border=8)

        panel.SetSizer(sizer)

        self.effects_list.Bind(wx.EVT_LISTBOX, self.on_selection)
        self.effects_list.Bind(wx.EVT_LISTBOX_DCLICK, self.accept)
        self.effects_list.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        ok_button.Bind(wx.EVT_BUTTON, self.accept)
        cancel_button.Bind(wx.EVT_BUTTON, self.cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.update_description()
        self.Centre()
        wx.CallAfter(self.effects_list.SetFocus)

    def current_effect(self):
        selection = self.effects_list.GetSelection()
        if 0 <= selection < len(self.effects):
            return self.effects[selection]
        return None

    def current_message(self):
        effect = self.current_effect()
        return effect_spoken_label(effect) if effect else ""

    def update_description(self):
        message = self.current_message()
        self.description.SetLabel(message)
        self.description.SetName(message or tr("وصف المؤثر الصوتي"))

    def on_selection(self, event):
        self.update_description()
        event.Skip()

    def on_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_RETURN:
            self.accept()
            return
        if key == wx.WXK_ESCAPE:
            self.cancel()
            return
        event.Skip()

    def accept(self, event=None):
        self.selected_effect = self.current_effect()
        if self.selected_effect:
            self.EndModal(wx.ID_OK)

    def cancel(self, event=None):
        self.EndModal(wx.ID_CANCEL)


class RealtimeAudioPreview:
    def __init__(self, status_callback=None):
        self.process = None
        self.thread = None
        self.stop_requested = threading.Event()
        self.lock = threading.Lock()
        self.stream = None
        self.generation = 0
        self.play_requested = False
        self.status_callback = status_callback
        self.input_path = ""
        self.audio_filter = ""
        self.start_time = 0
        self.duration = 0
        self.offset = 0
        self.output_volume = 1.0
        self.output_volume_provider = None
        self.started_at = 0
        self.is_playing = False
        self.active_audio_filter = None
        self.active_board = None
        self.active_processor = None

    def say_status(self, message, enabled=True):
        if enabled and self.status_callback:
            wx.CallAfter(self.status_callback, message)

    @staticmethod
    def normalized_output_volume(value, default=1.0):
        return normalized_program_volume(value, default)

    @staticmethod
    def scaled_audio_bytes(data, dtype, volume):
        volume = RealtimeAudioPreview.normalized_output_volume(volume)
        if dtype == "float32":
            audio = np.frombuffer(data, dtype=np.float32)
            if volume < 0.999:
                audio = audio.astype(np.float32, copy=True) * volume
            return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False).tobytes()
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        audio *= volume
        return np.clip(audio, -32768, 32767).astype(np.int16).tobytes()

    @staticmethod
    def live_output_volume(output_volume, output_volume_provider=None):
        if callable(output_volume_provider):
            try:
                return RealtimeAudioPreview.normalized_output_volume(output_volume_provider(), output_volume)
            except Exception:
                pass
        return RealtimeAudioPreview.normalized_output_volume(output_volume)

    def write_stream_chunk(self, stream, data, stop_requested):
        if stop_requested.is_set():
            return False
        try:
            stream.write(data)
            return True
        except Exception:
            if not stop_requested.is_set():
                self.say_status(tr("تعذر تشغيل المعاينة"))
            stop_requested.set()
            return False

    @staticmethod
    def stream_settings(audio_filter):
        frames_per_chunk = 4096
        if is_dpdfnet_effect(audio_filter):
            return "float32", DPDFNET_PREVIEW_HOP, DPDFNET_PREVIEW_HOP * 4
        if is_pedalboard_effect(audio_filter):
            return "float32", frames_per_chunk, frames_per_chunk * 2 * 4
        return "int16", frames_per_chunk, frames_per_chunk * 2 * 2

    @staticmethod
    def ffmpeg_input_args(input_path):
        if isinstance(input_path, dict):
            path = str(input_path.get("path", "") or "")
            if input_path.get("format") == "concat":
                return ["-f", "concat", "-safe", "0", "-i", path]
            return ["-i", path]
        return ["-i", str(input_path)]

    @staticmethod
    def ffmpeg_direct_preview_args(input_path, offset, duration):
        if not isinstance(input_path, dict) or input_path.get("format") != "ffmpeg_preview":
            return None
        prepared_args = input_path.get("prepared_args")
        try:
            prepared_offset = float(input_path.get("prepared_offset", -1.0))
        except (TypeError, ValueError):
            prepared_offset = -1.0
        if prepared_args is not None and abs(prepared_offset - max(0.0, float(offset or 0.0))) <= 0.001:
            return list(prepared_args)
        builder = input_path.get("build_args")
        if callable(builder):
            return list(builder(max(0.0, float(offset or 0.0)), max(0.0, float(duration or 0.0))))
        return list(input_path.get("args") or [])

    def start(self, input_path, audio_filter, start_time=0, duration=0, offset=0, output_volume=1.0, output_volume_provider=None):
        self.stop(True, wait=True)
        if not direct_realtime_audio_filter_supported(audio_filter):
            self.say_status(tr("جارٍ تجهيز المعاينة"))
            with self.lock:
                self.is_playing = False
                self.play_requested = False
            return
        self.input_path = input_path
        self.audio_filter = audio_filter
        self.start_time = start_time
        self.duration = duration
        self.offset = max(0, min(duration, offset)) if duration else max(0, offset)
        self.output_volume = self.normalized_output_volume(output_volume)
        self.output_volume_provider = output_volume_provider
        self.started_at = time.monotonic()
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.stop_requested = threading.Event()
            stop_requested = self.stop_requested
            self.play_requested = True
        self.thread = threading.Thread(target=self.worker, args=(generation, stop_requested, input_path, audio_filter, start_time, duration, self.offset, self.output_volume, self.output_volume_provider), daemon=True)
        self.thread.start()

    def worker(self, generation, stop_requested, input_path, audio_filter, start_time, duration, offset, output_volume, output_volume_provider=None):
        try:
            import sounddevice as sd
        except Exception:
            self.say_status("مكتبة التشغيل الفوري غير متاحة")
            with self.lock:
                if generation == self.generation:
                    self.is_playing = False
                    self.play_requested = False
            return
        try:
            direct_preview_args = self.ffmpeg_direct_preview_args(input_path, offset, duration)
        except Exception:
            self.say_status(tr("تعذر تشغيل المعاينة"))
            with self.lock:
                if generation == self.generation:
                    self.is_playing = False
                    self.play_requested = False
                    self.process = None
            return
        command = [
            ffmpeg_binary(),
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if direct_preview_args is None:
            command.extend(["-ss", str(start_time + offset)])
            if duration:
                remaining = max(0.05, duration - offset)
                command.extend(["-t", str(remaining)])
            command.extend(self.ffmpeg_input_args(input_path))
            command.append("-vn")
        else:
            command.extend(direct_preview_args)
        try:
            if is_dpdfnet_effect(audio_filter):
                command.extend(["-f", "f32le", "-ac", "1", "-ar", str(DPDFNET_SAMPLE_RATE), "pipe:1"])
                processor = DpdfnetRealtimeProcessor(audio_filter, DPDFNET_SAMPLE_RATE)
                board = None
                audio_state = None
                with self.lock:
                    self.active_processor = processor
                    self.active_board = None
            elif is_pedalboard_effect(audio_filter):
                command.extend(["-f", "f32le", "-ac", "2", "-ar", "44100", "pipe:1"])
                board = build_pedalboard(audio_filter)
                audio_state = {}
                processor = None
                with self.lock:
                    self.active_board = board
                    self.active_audio_filter = audio_filter
                    self.active_processor = None
            else:
                if direct_preview_args is None:
                    filter_text = resolved_audio_filter(audio_filter, duration, offset)
                    command.extend(["-af", filter_text])
                command.extend(["-f", "s16le", "-ac", "2", "-ar", "44100", "pipe:1"])
                board = None
                stereo_state = None
                processor = None
            stream_dtype, frames_per_chunk, block_size = self.stream_settings(audio_filter)
        except Exception:
            self.say_status(tr("تعذر تشغيل المعاينة"))
            with self.lock:
                if generation == self.generation:
                    self.is_playing = False
                    self.play_requested = False
                    self.process = None
            return
        with self.lock:
            if generation != self.generation:
                return
            self.started_at = time.monotonic()
            self.is_playing = True
        self.say_status("تشغيل المعاينة")
        stream = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
        except Exception:
            self.say_status(tr("تعذر تشغيل المعاينة"))
            with self.lock:
                if generation == self.generation:
                    self.is_playing = False
                    self.play_requested = False
                    self.process = None
            return
        with self.lock:
            if generation != self.generation:
                process.terminate()
                return
            self.process = process
        stream = None
        try:
            output_sample_rate = DPDFNET_SAMPLE_RATE if is_dpdfnet_effect(audio_filter) else 44100
            with sd.RawOutputStream(samplerate=output_sample_rate, channels=2, dtype=stream_dtype, blocksize=frames_per_chunk, latency="high", device=selected_sounddevice_output_device(sd)) as stream:
                with self.lock:
                    if generation != self.generation or stop_requested.is_set():
                        return
                    self.stream = stream
                while not stop_requested.is_set():
                    data = process.stdout.read(block_size)
                    if not data:
                        break
                    live_volume = self.live_output_volume(output_volume, output_volume_provider)
                    with self.lock:
                        current_processor = self.active_processor
                        current_board = self.active_board
                        current_filter = self.active_audio_filter or audio_filter

                    if current_processor:
                        audio = np.frombuffer(data, dtype=np.float32)
                        if len(audio) < 1:
                            continue
                        processed = current_processor.process(audio)
                        if len(processed) < 1:
                            continue
                        processed *= live_volume
                        stereo = np.repeat(np.clip(processed, -1.0, 1.0)[:, np.newaxis], 2, axis=1)
                        data = stereo.astype(np.float32, copy=False).tobytes()
                    elif current_board:
                        audio = np.frombuffer(data, dtype=np.float32)
                        if len(audio) < 2:
                            continue
                        audio = audio[:len(audio) - len(audio) % 2].reshape(-1, 2).T
                        processed = process_pedalboard_block(audio, current_filter, current_board, audio_state, 44100)
                        processed *= live_volume
                        processed = np.clip(processed.T, -1.0, 1.0).astype(np.float32, copy=False)
                        data = processed.tobytes()
                    else:
                        data = self.scaled_audio_bytes(data, "int16", live_volume)
                    with self.lock:
                        if generation != self.generation or stop_requested.is_set() or self.stream is not stream:
                            break
                    if not self.write_stream_chunk(stream, data, stop_requested):
                        break
                if processor and not stop_requested.is_set():
                    processed = processor.flush()
                    if len(processed) > 0:
                        processed *= self.live_output_volume(output_volume, output_volume_provider)
                        stereo = np.repeat(np.clip(processed, -1.0, 1.0)[:, np.newaxis], 2, axis=1)
                        self.write_stream_chunk(stream, stereo.astype(np.float32, copy=False).tobytes(), stop_requested)
                if board and not stop_requested.is_set():
                    tail_samples = int(echo_tail_duration(audio_filter) * 44100)
                    remaining = tail_samples
                    while remaining > 0 and not stop_requested.is_set():
                        count = min(8192, remaining)
                        audio = np.zeros((2, count), dtype=np.float32)
                        processed = process_pedalboard_block(audio, audio_filter, board, audio_state, 44100)
                        processed *= self.live_output_volume(output_volume, output_volume_provider)
                        data = np.clip(processed.T, -1.0, 1.0).astype(np.float32, copy=False).tobytes()
                        if not self.write_stream_chunk(stream, data, stop_requested):
                            break
                        remaining -= count
        except Exception:
            if not stop_requested.is_set():
                self.say_status(tr("تعذر تشغيل المعاينة"))
        finally:
            with self.lock:
                if self.stream is stream:
                    self.stream = None
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.25)
                except Exception:
                    pass
            with self.lock:
                if generation == self.generation:
                    self.is_playing = False
                    self.process = None
                    if not stop_requested.is_set():
                        self.play_requested = False
                if self.thread is threading.current_thread():
                    self.thread = None

    def current_offset(self):
        if self.is_playing or self.play_requested:
            elapsed = max(0, time.monotonic() - self.started_at)
            return min(self.duration, self.offset + elapsed) if self.duration else self.offset + elapsed
        return self.offset

    def pause(self, announce=True):
        self.offset = self.current_offset()
        self.stop()
        self.say_status("إيقاف مؤقت", announce)

    def stop(self, keep_request=False, wait=False):
        with self.lock:
            stop_requested = self.stop_requested
            process = self.process
            stream = self.stream
            thread = self.thread
            if not keep_request:
                self.play_requested = False
                self.is_playing = False
        if stop_requested:
            stop_requested.set()
        if process and process.poll() is None:
            process.terminate()
        if wait and thread and thread is not threading.current_thread():
            try:
                thread.join(timeout=PREVIEW_THREAD_STOP_TIMEOUT)
            except Exception:
                pass
        with self.lock:
            if self.process is process:
                self.process = None
            if self.stream is stream:
                self.stream = None

    def reset(self, announce=True, wait=False):
        self.offset = 0
        self.stop(wait=wait)
        self.say_status("إيقاف", announce)

    def seek(self, seconds):
        self.offset = max(0, self.current_offset() + seconds)
        if self.duration:
            self.offset = min(self.duration, self.offset)
        was_playing = self.is_playing or self.play_requested
        self.stop(True, wait=True)
        if was_playing and self.input_path:
            self.start(self.input_path, self.audio_filter, self.start_time, self.duration, self.offset, self.output_volume, self.output_volume_provider)

    def update_live_filter(self, audio_filter):
        with self.lock:
            if not (self.is_playing or self.play_requested):
                self.audio_filter = audio_filter
                return False
            if is_dpdfnet_effect(audio_filter):
                try:
                    self.active_processor = DpdfnetRealtimeProcessor(audio_filter, DPDFNET_SAMPLE_RATE)
                    self.audio_filter = audio_filter
                    return True
                except Exception:
                    pass
            if is_pedalboard_effect(audio_filter):
                try:
                    self.active_board = build_pedalboard(audio_filter)
                    self.active_audio_filter = audio_filter
                    self.audio_filter = audio_filter
                    return True
                except Exception:
                    pass
        return False

    def restart_with_filter(self, audio_filter):
        if not self.input_path:
            return
        if self.update_live_filter(audio_filter):
            return
        offset = self.current_offset()
        if self.duration and offset >= self.duration:
            offset = 0
        was_playing = self.is_playing or self.play_requested
        self.stop(True, wait=False)
        self.audio_filter = audio_filter
        if was_playing:
            self.start(self.input_path, audio_filter, self.start_time, self.duration, offset, self.output_volume, self.output_volume_provider)


def current_program_output_volume(parent=None):
    if parent is not None and hasattr(parent, "effective_output_volume"):
        try:
            return RealtimeAudioPreview.normalized_output_volume(parent.effective_output_volume())
        except Exception:
            pass
    if parent is not None and hasattr(parent, "volume"):
        try:
            return RealtimeAudioPreview.normalized_output_volume(getattr(parent, "volume", 1.0))
        except Exception:
            pass
    return RealtimeAudioPreview.normalized_output_volume(get_volume())


def run_ffmpeg_with_progress(command, input_path, output_path, error_message, progress_callback=None, cancelled_callback=None):
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    if not progress_callback and not cancelled_callback:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            message = result.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(message or error_message)
        return
    duration = max(0.001, get_media_duration(input_path))
    progress_command = [
        command[0],
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *command[1:],
    ]
    process = subprocess.Popen(
        progress_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        startupinfo=ffmpeg_startupinfo(),
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    last_percent = -1
    if progress_callback:
        progress_callback(0)
    cancelled = False
    try:
        if process.stdout:
            for line in process.stdout:
                if audio_effect_cancelled(cancelled_callback):
                    cancelled = True
                    terminate_subprocess(process)
                    break
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        seconds = int(value) / 1000000.0
                    except ValueError:
                        continue
                    percent = max(0, min(99, int(seconds * 100 / duration)))
                    if progress_callback and percent != last_percent:
                        last_percent = percent
                        progress_callback(percent)
                elif key == "progress" and value == "end" and progress_callback:
                    progress_callback(100)
        if audio_effect_cancelled(cancelled_callback):
            cancelled = True
            terminate_subprocess(process)
        return_code = process.wait() if process.poll() is None else process.poll()
        message = ""
    finally:
        if process.poll() is None:
            terminate_subprocess(process)
    if cancelled:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        raise AudioEffectPreparationCancelled()
    if return_code != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(message or error_message)
    if progress_callback:
        progress_callback(100)


def run_audio_filter_command(input_path, output_path, audio_filter, copy_video, progress_callback=None, cancelled_callback=None):
    video_options = ["-c:v", "copy"] if copy_video else ["-c:v", "libx264", "-preset", "medium", "-crf", "16"]
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-af",
        audio_filter,
        *video_options,
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    run_ffmpeg_with_progress(command, input_path, output_path, "تعذر تطبيق المؤثر الصوتي", progress_callback, cancelled_callback)


def audio_mean_volume_db(path):
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-vn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        os.devnull,
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    if result.returncode != 0:
        return None
    message = result.stderr.decode("utf-8", errors="ignore")
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", message)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def match_audio_effect_output_level(source_path, output_path, progress_callback=None, cancelled_callback=None):
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    if progress_callback:
        progress_callback(5)
    source_mean = audio_mean_volume_db(source_path)
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    if progress_callback:
        progress_callback(35)
    output_mean = audio_mean_volume_db(output_path)
    if progress_callback:
        progress_callback(70)
    if source_mean is None or output_mean is None:
        if progress_callback:
            progress_callback(100)
        return False
    reduction_db = output_mean - source_mean
    if reduction_db <= 0.75:
        if progress_callback:
            progress_callback(100)
        return False
    gain = 10 ** (-reduction_db / 20.0)
    base, extension = os.path.splitext(output_path)
    adjusted_path = f"{base}_matched{extension or '.wav'}"
    if os.path.exists(adjusted_path):
        try:
            os.remove(adjusted_path)
        except OSError:
            pass
    if has_video_stream(output_path):
        command = [
            ffmpeg_binary(),
            "-y",
            "-i",
            output_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "copy",
            "-af",
            f"volume={gain:.8f}",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-movflags",
            "+faststart",
            adjusted_path,
        ]
    else:
        command = [
            ffmpeg_binary(),
            "-y",
            "-i",
            output_path,
            "-vn",
            "-af",
            f"volume={gain:.8f}",
            "-c:a",
            "pcm_s16le",
            adjusted_path,
        ]
    try:
        run_ffmpeg_with_progress(
            command,
            output_path,
            adjusted_path,
            "تعذر ضبط مستوى صوت المؤثر",
            (lambda percent: progress_callback(70 + percent * 0.30)) if progress_callback else None,
            cancelled_callback,
        )
        os.replace(adjusted_path, output_path)
        if progress_callback:
            progress_callback(100)
        return True
    finally:
        if os.path.exists(adjusted_path):
            try:
                os.remove(adjusted_path)
            except OSError:
                pass


def finalize_audio_effect_output(source_path, output_path, audio_filter, progress_callback=None, cancelled_callback=None):
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    if not output_path or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Audio effect output was not created")
    if progress_callback:
        progress_callback(100)
    return False


def process_audio_with_pedalboard(input_path, audio_path, audio_filter, progress_callback=None, cancelled_callback=None):
    board = build_pedalboard(audio_filter)
    audio_state = {}
    decode_command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-vn",
        "-f",
        "f32le",
        "-ac",
        "2",
        "-ar",
        "44100",
        "pipe:1",
    ]
    encode_command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "f32le",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-i",
        "pipe:0",
        "-c:a",
        "pcm_s16le",
        audio_path,
    ]
    decoder = subprocess.Popen(decode_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    encoder = subprocess.Popen(encode_command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=ffmpeg_startupinfo())
    total_bytes = max(1, int(max(0.001, get_media_duration(input_path)) * 44100 * 2 * 4))
    processed_bytes = 0
    last_percent = -1
    cancelled = False
    try:
        while True:
            if audio_effect_cancelled(cancelled_callback):
                cancelled = True
                break
            data = decoder.stdout.read(65536)
            if not data:
                break
            processed_bytes += len(data)
            if progress_callback:
                percent = max(0, min(98, int(processed_bytes * 98 / total_bytes)))
                if percent != last_percent:
                    last_percent = percent
                    progress_callback(percent)
            audio = np.frombuffer(data, dtype=np.float32)
            if len(audio) < 2:
                continue
            audio = audio[:len(audio) - len(audio) % 2].reshape(-1, 2).T
            processed = process_pedalboard_block(audio, audio_filter, board, audio_state, 44100)
            processed = np.clip(processed.T, -1.0, 1.0).astype(np.float32, copy=False)
            encoder.stdin.write(processed.tobytes())
        tail_samples = 0 if audio_filter.get("kind") == "echo_reverb_studio" else int(echo_tail_duration(audio_filter) * 44100)
        remaining = tail_samples
        while remaining > 0:
            if audio_effect_cancelled(cancelled_callback):
                cancelled = True
                break
            count = min(8192, remaining)
            audio = np.zeros((2, count), dtype=np.float32)
            processed = process_pedalboard_block(audio, audio_filter, board, audio_state, 44100)
            processed = np.clip(processed.T, -1.0, 1.0).astype(np.float32, copy=False)
            encoder.stdin.write(processed.tobytes())
            remaining -= count
    finally:
        if encoder.stdin:
            encoder.stdin.close()
    if cancelled:
        terminate_subprocess(decoder)
        terminate_subprocess(encoder)
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
        raise AudioEffectPreparationCancelled()
    decoder_result = decoder.wait()
    encoder_result = encoder.wait()
    decoder_error = ""
    encoder_error = ""
    if decoder_result != 0 or encoder_result != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        raise RuntimeError(decoder_error or encoder_error or "تعذر معالجة الصوت")
    if progress_callback:
        progress_callback(100)


def pedalboard_worker_command(payload_path):
    if getattr(sys, "frozen", False):
        return [sys.executable, PEDALBOARD_WORKER_FLAG, payload_path]
    return [sys.executable, "-m", "video_maker.audio_effect_worker", payload_path]


def _worker_exit_text(return_code):
    try:
        code = int(return_code)
    except Exception:
        return str(return_code)
    return f"{code} (0x{code & 0xFFFFFFFF:08X})"


def _read_worker_progress(progress_path, offset, progress_callback):
    if not os.path.exists(progress_path):
        return offset
    try:
        with open(progress_path, "r", encoding="utf-8", errors="ignore") as progress_file:
            progress_file.seek(offset)
            for line in progress_file:
                try:
                    value = float(line.strip())
                except ValueError:
                    continue
                if progress_callback:
                    progress_callback(max(0, min(100, value)))
            return progress_file.tell()
    except OSError:
        return offset


def run_isolated_pedalboard_filter(input_path, audio_path, audio_filter, progress_callback=None, cancelled_callback=None, command_builder=None):
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    temp_dir = tempfile.mkdtemp(prefix="pedalboard_worker_")
    payload_path = os.path.join(temp_dir, "payload.json")
    progress_path = os.path.join(temp_dir, "progress.txt")
    error_path = os.path.join(temp_dir, "error.txt")
    fatal_path = os.path.join(temp_dir, "fatal.txt")
    payload = {
        "input_path": input_path,
        "audio_path": audio_path,
        "audio_filter": audio_filter,
        "progress_path": progress_path,
        "error_path": error_path,
        "fatal_path": fatal_path,
    }
    try:
        with open(payload_path, "w", encoding="utf-8") as payload_file:
            json.dump(payload, payload_file, ensure_ascii=False)
        command = command_builder(payload_path) if command_builder else pedalboard_worker_command(payload_path)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            startupinfo=ffmpeg_startupinfo(),
        )
        progress_offset = 0
        cancelled = False
        while True:
            progress_offset = _read_worker_progress(progress_path, progress_offset, progress_callback)
            if audio_effect_cancelled(cancelled_callback):
                cancelled = True
                terminate_subprocess(process)
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
        return_code = process.wait() if process.poll() is None else process.poll()
        progress_offset = _read_worker_progress(progress_path, progress_offset, progress_callback)
        stderr_text = ""
        if process.stderr:
            try:
                stderr_text = process.stderr.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                stderr_text = ""
            try:
                process.stderr.close()
            except Exception:
                pass
        if cancelled:
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            raise AudioEffectPreparationCancelled()
        if return_code != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            details = []
            for path in (error_path, fatal_path):
                if os.path.exists(path):
                    try:
                        text = open(path, "r", encoding="utf-8", errors="ignore").read().strip()
                    except OSError:
                        text = ""
                    if text:
                        details.append(text)
            if stderr_text:
                details.append(stderr_text)
            detail_text = "\n".join(details).strip()
            message = (
                "تعذر تطبيق مؤثر الصوت لأن عامل المعالجة توقف فجأة "
                f"(رمز الخروج {_worker_exit_text(return_code)}). لم يتم إغلاق البرنامج."
            )
            if detail_text:
                message = f"{message}\n{detail_text}"
            raise RuntimeError(message)
        if progress_callback:
            progress_callback(100)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def mux_processed_audio(input_path, audio_path, output_path, copy_video, shortest=True, progress_callback=None, cancelled_callback=None):
    video_options = ["-c:v", "copy"] if copy_video else ["-c:v", "libx264", "-preset", "medium", "-crf", "16"]
    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        input_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *video_options,
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    if shortest:
        command.insert(-3, "-shortest")
    run_ffmpeg_with_progress(command, input_path, output_path, "تعذر دمج الصوت المعالج مع الفيديو", progress_callback, cancelled_callback)


def apply_pedalboard_filter(input_path, output_path, audio_filter, progress_callback=None, cancelled_callback=None):
    temp_dir = tempfile.mkdtemp(prefix="pedalboard_audio_")
    audio_path = os.path.join(temp_dir, "processed.wav")
    try:
        run_isolated_pedalboard_filter(
            input_path,
            audio_path,
            audio_filter,
            (lambda percent: progress_callback(percent * 0.98)) if progress_callback else None,
            cancelled_callback,
        )
        if not has_video_stream(input_path):
            shutil.copy2(audio_path, output_path)
            if progress_callback:
                progress_callback(100)
            return
        shortest = echo_tail_duration(audio_filter) <= 0
        try:
            mux_processed_audio(
                input_path,
                audio_path,
                output_path,
                True,
                shortest,
                (lambda percent: progress_callback(98 + percent * 0.02)) if progress_callback else None,
                cancelled_callback,
            )
        except RuntimeError:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            mux_processed_audio(
                input_path,
                audio_path,
                output_path,
                False,
                shortest,
                (lambda percent: progress_callback(98 + percent * 0.02)) if progress_callback else None,
                cancelled_callback,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def apply_audio_filter(input_path, output_path, audio_filter, progress_callback=None, cancelled_callback=None):
    if is_goldwave_fade_effect(audio_filter):
        audio_filter = resolved_audio_filter(audio_filter, get_media_duration(input_path), 0.0)
    if is_breath_reduction_effect(audio_filter):
        audio_filter = resolved_audio_filter(audio_filter, get_media_duration(input_path), 0.0)
    if is_dpdfnet_effect(audio_filter):
        apply_dpdfnet_filter(input_path, output_path, audio_filter, progress_callback, cancelled_callback, AudioEffectPreparationCancelled)
        return
    if is_pedalboard_effect(audio_filter):
        apply_pedalboard_filter(input_path, output_path, audio_filter, progress_callback, cancelled_callback)
        return
    if not has_video_stream(input_path):
        command = [
            ffmpeg_binary(),
            "-y",
            "-i",
            input_path,
            "-vn",
            "-af",
            audio_filter,
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
        run_ffmpeg_with_progress(command, input_path, output_path, "تعذر تطبيق المؤثر الصوتي", progress_callback, cancelled_callback)
        return
    try:
        run_audio_filter_command(input_path, output_path, audio_filter, True, progress_callback, cancelled_callback)
    except RuntimeError:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        run_audio_filter_command(input_path, output_path, audio_filter, False, progress_callback, cancelled_callback)


class AudioEffectDialog(wx.Dialog):
    def __init__(self, parent, effect_definition):
        super().__init__(parent, title=tr(effect_definition["name"]), size=(760, 520))
        self.parent = parent
        self.effect_definition = effect_definition
        self.preview_path = ""
        self.preview_dir = ""
        self.busy = False
        self.close_when_ready = False
        self.preview_player = RealtimeAudioPreview(self.update_status_text)
        self.preview_source = None
        self.controls = {}
        self.first_control = None
        self.tab_order = []
        self.last_focus_control = None
        self.saved_values = get_audio_effect_values(effect_definition["key"])
        self.silent_prepare_lock = threading.Lock()
        self.silent_prepare_generation = 0
        self.silent_prepare_pending = None
        self.silent_prepare_running = False
        self.silent_prepare_timer = None
        self.silent_prepare_done = threading.Event()
        self.silent_prepare_closed = False
        self.silent_prepare_transferred = False
        self.silent_apply_filter = None
        self.silent_apply_waiting = False
        self.silent_prepare_source_path = ""
        self.silent_prepare_temp_dir = ""
        self.silent_prepare_result_path = ""
        self.silent_prepare_result_key = None
        self.silent_prepare_progress = 0
        self.apply_progress_dialog = None
        self.preview_progress_dialog = None
        self.last_spoken_effect_percent = -10
        self.last_spoken_preview_percent = -10
        self.last_apply_progress_state = None
        self.apply_cancel_requested = threading.Event()
        self.preview_cancel_requested = threading.Event()
        self.preview_prepare_waiting = False
        self.silent_prepare_source_ready = threading.Event()
        self.silent_prepare_error = None
        selected_range = self.parent.selected_effect_range()
        self.silent_prepare_start, self.silent_prepare_end = selected_range if selected_range else (None, None)
        source_factory = getattr(self.parent, "audio_effect_preparation_timeline", None)
        self.silent_prepare_timeline = list(source_factory() if callable(source_factory) else self.parent.timeline) if selected_range else []

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.preset_choice = None

        preset_sizer = wx.BoxSizer(wx.HORIZONTAL)
        presets = effect_definition.get("presets", [])
        if presets:
            preset_label = wx.StaticText(panel, label=tr("الإعداد"))
            self.preset_choice = wx.Choice(panel, choices=[tr(preset["name"]) for preset in presets])
            self.preset_choice.SetSelection(0)
            self.preset_choice.SetName(tr("الإعداد الجاهز"))
            self.tab_order.append(self.preset_choice)
            preset_sizer.Add(preset_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
            preset_sizer.Add(self.preset_choice, proportion=1, flag=wx.EXPAND)

        controls_sizer = wx.BoxSizer(wx.VERTICAL)
        for control in effect_definition.get("controls", []):
            control_name = tr(control["name"])
            if control.get("type") == "checkbox":
                checkbox = wx.CheckBox(panel, label=control_name)
                value = self.saved_values.get(control["key"], control.get("default", False))
                checkbox.SetValue(bool(value))
                checkbox.SetName(control_name)
                controls_sizer.Add(checkbox, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
                self.controls[control["key"]] = {"checkbox": checkbox, "name": control_name, "type": "checkbox"}
                if self.first_control is None:
                    self.first_control = checkbox
                self.tab_order.append(checkbox)
                checkbox.Bind(wx.EVT_CHECKBOX, self.make_checkbox_handler(control["key"]))
            elif control.get("type") == "choice":
                row = wx.BoxSizer(wx.HORIZONTAL)
                label = wx.StaticText(panel, label=control_name)
                choices = control.get("choices", [])
                choice = wx.Choice(panel, choices=[tr(item["label"]) for item in choices])
                value = self.saved_values.get(control["key"], control.get("default"))
                choice.SetSelection(self.choice_index_for_value(choices, value))
                choice.SetName(control_name)
                row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
                row.Add(choice, proportion=1, flag=wx.EXPAND)
                controls_sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
                self.controls[control["key"]] = {"choice": choice, "choices": choices, "name": control_name, "type": "choice", "unit": tr(control.get("unit", ""))}
                if self.first_control is None:
                    self.first_control = choice
                self.tab_order.append(choice)
                choice.Bind(wx.EVT_CHOICE, self.make_choice_handler(control["key"]))
                choice_key_handler = self.make_choice_key_handler(control["key"])
                choice.Bind(wx.EVT_CHAR_HOOK, choice_key_handler)
                choice.Bind(wx.EVT_CHAR, choice_key_handler)
                choice.Bind(wx.EVT_KEY_DOWN, choice_key_handler)
            else:
                row = wx.BoxSizer(wx.HORIZONTAL)
                label = wx.StaticText(panel, label=control_name)
                value = int(self.saved_values.get(control["key"], control["default"]))
                value = max(control["min"], min(control["max"], value))
                slider = wx.Slider(panel, value=value, minValue=control["min"], maxValue=control["max"], style=wx.SL_HORIZONTAL | wx.WANTS_CHARS)
                line_step = max(1, int(control.get("step", 1)))
                page_step = max(1, int(control.get("page_step", 10)))
                slider.SetLineSize(line_step)
                slider.SetPageSize(page_step)
                slider.SetTickFreq(max(1, int(control.get("tick", 1))))
                slider.SetName(control_name)
                row.Add(label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
                row.Add(slider, proportion=1, flag=wx.EXPAND)
                controls_sizer.Add(row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
                self.controls[control["key"]] = {"slider": slider, "name": control_name, "type": "slider", "unit": tr(control.get("unit", "بالمئة")), "line_step": line_step, "page_step": page_step, "home_value": control.get("home_value"), "end_value": control.get("end_value"), "last_value": value}
                if self.first_control is None:
                    self.first_control = slider
                self.tab_order.append(slider)
                slider.Bind(wx.EVT_SCROLL_THUMBTRACK, self.make_slider_handler(control["key"]))
                slider.Bind(wx.EVT_SCROLL_CHANGED, self.make_slider_handler(control["key"]))
                slider.Bind(wx.EVT_SCROLL_LINEUP, self.make_slider_scroll_handler(control["key"], 1))
                slider.Bind(wx.EVT_SCROLL_LINEDOWN, self.make_slider_scroll_handler(control["key"], -1))
                slider.Bind(wx.EVT_SCROLL_PAGEUP, self.make_slider_scroll_handler(control["key"], 10))
                slider.Bind(wx.EVT_SCROLL_PAGEDOWN, self.make_slider_scroll_handler(control["key"], -10))
                slider_key_handler = self.make_slider_key_handler(control["key"])
                slider.Bind(wx.EVT_CHAR_HOOK, slider_key_handler)
                slider.Bind(wx.EVT_CHAR, slider_key_handler)
                slider.Bind(wx.EVT_KEY_DOWN, slider_key_handler)
                slider.Bind(wx.EVT_SET_FOCUS, self.on_slider_focus)

        if not self.controls:
            no_controls = wx.StaticText(panel, label=tr("هذا المؤثر يعمل بدون تحكمات إضافية"))
            no_controls.SetName(tr("لا توجد تحكمات إضافية لهذا المؤثر"))
            controls_sizer.Add(no_controls, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        self.status = wx.StaticText(panel, label=tr("جاهز"))
        self.gauge = wx.Gauge(panel, range=100)
        self.status.SetName(tr("حالة المؤثر الصوتي"))
        self.gauge.SetName(tr("شريط تقدم المؤثر الصوتي"))
        self.gauge.SetCanFocus(False)

        play_button = wx.Button(panel, label=tr("تشغيل"))
        rewind_button = wx.Button(panel, label=tr("ترجيع"))
        forward_button = wx.Button(panel, label=tr("تقديم"))
        pause_button = wx.Button(panel, label=tr("إيقاف مؤقت"))
        stop_button = wx.Button(panel, label=tr("إيقاف"))
        reset_button = wx.Button(panel, label=tr("الافتراضي"))
        apply_button = wx.Button(panel, label=tr("تطبيق"))
        cancel_button = wx.Button(panel, label=tr("إلغاء"))

        play_button.SetName(tr("تشغيل معاينة المؤثر"))
        rewind_button.SetName(tr("ترجيع معاينة المؤثر"))
        forward_button.SetName(tr("تقديم معاينة المؤثر"))
        pause_button.SetName(tr("إيقاف مؤقت لمعاينة المؤثر"))
        stop_button.SetName(tr("إيقاف معاينة المؤثر"))
        for navigation_button in (play_button, rewind_button, forward_button, pause_button, stop_button):
            navigation_button.SetCanFocus(False)
        reset_button.SetName(tr("إرجاع إعدادات المؤثر إلى الافتراضي"))
        apply_button.SetName(tr("تطبيق المؤثر على التحديد"))
        cancel_button.SetName(tr("إلغاء"))
        apply_button.SetDefault()
        self.tab_order.extend([reset_button, apply_button, cancel_button])

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(play_button, flag=wx.ALL, border=6)
        button_sizer.Add(rewind_button, flag=wx.ALL, border=6)
        button_sizer.Add(forward_button, flag=wx.ALL, border=6)
        button_sizer.Add(pause_button, flag=wx.ALL, border=6)
        button_sizer.Add(stop_button, flag=wx.ALL, border=6)
        button_sizer.Add(reset_button, flag=wx.ALL, border=6)
        button_sizer.Add(apply_button, flag=wx.ALL, border=6)
        button_sizer.Add(cancel_button, flag=wx.ALL, border=6)

        if presets:
            main_sizer.Add(preset_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(controls_sizer, flag=wx.EXPAND)
        main_sizer.Add(self.status, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(self.gauge, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)
        main_sizer.Add(button_sizer, flag=wx.ALIGN_CENTER | wx.ALL, border=6)
        panel.SetSizer(main_sizer)

        play_button.Bind(wx.EVT_BUTTON, self.play_preview)
        rewind_button.Bind(wx.EVT_BUTTON, self.rewind_preview)
        forward_button.Bind(wx.EVT_BUTTON, self.forward_preview)
        pause_button.Bind(wx.EVT_BUTTON, self.pause_preview)
        stop_button.Bind(wx.EVT_BUTTON, self.stop_preview)
        reset_button.Bind(wx.EVT_BUTTON, self.reset_defaults)
        apply_button.Bind(wx.EVT_BUTTON, self.apply_effect)
        cancel_button.Bind(wx.EVT_BUTTON, self.close_dialog)
        if self.preset_choice:
            self.preset_choice.Bind(wx.EVT_CHOICE, self.on_preset_changed)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.close_dialog)
        bind_dialog_keys(self, self.on_key, (wx.Slider, wx.Choice))

        self.Centre()
        wx.CallAfter(self.focus_main_control)
        wx.CallAfter(self.schedule_preview_preparation, 0)

    def remember_focus(self):
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            self.last_focus_control = focused

    def restore_focus(self):
        if self.last_focus_control and self.last_focus_control in self.tab_order:
            self.last_focus_control.SetFocus()
            return
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            return
        self.focus_main_control()

    def effect_values(self):
        values = {}
        for key, widgets in self.controls.items():
            if widgets.get("type") == "checkbox":
                values[key] = widgets["checkbox"].GetValue()
            elif widgets.get("type") == "choice":
                values[key] = self.choice_value(widgets)
            else:
                values[key] = widgets["slider"].GetValue()
        return values

    def audio_filter(self):
        values = self.effect_values()
        return self.effect_definition["builder"](values)

    def selected_effect_duration(self):
        if self.silent_prepare_start is None or self.silent_prepare_end is None:
            return 0
        return max(0, self.silent_prepare_end - self.silent_prepare_start)

    def direct_realtime_preview_source(self):
        if not self.effect_definition.get("realtime_preview", True):
            return None
        if not direct_realtime_audio_filter_supported(self.audio_filter()):
            return None
        source_factory = getattr(self.parent, "create_direct_audio_effect_preview_source", None)
        if not callable(source_factory):
            return None
        return source_factory()

    def can_start_direct_realtime_preview(self):
        if not self.effect_definition.get("realtime_preview", True):
            return False
        if not direct_realtime_audio_filter_supported(self.audio_filter()):
            return False
        availability = getattr(self.parent, "can_create_direct_audio_effect_preview_source", None)
        if callable(availability):
            try:
                return bool(availability())
            except Exception:
                return False
        try:
            source = self.direct_realtime_preview_source()
        except Exception:
            return False
        if source and len(source) >= 4 and source[3]:
            shutil.rmtree(source[3], ignore_errors=True)
        return bool(source)

    def schedule_preview_preparation(self, delay=300):
        if self.can_start_direct_realtime_preview():
            return
        self.schedule_silent_preparation(delay)

    def should_show_apply_progress(self):
        return True

    def show_apply_progress_dialog(self):
        if self.apply_progress_dialog:
            return
        self.last_spoken_effect_percent = -10
        self.apply_progress_dialog = SaveProgressDialog(
            self,
            self.cancel_apply_effect,
            title=tr("جارٍ تطبيق المؤثر"),
            progress_template=tr("نسبة تطبيق المؤثر {percent} بالمئة"),
            status_name=tr("حالة تطبيق المؤثر"),
            gauge_name=tr("شريط تقدم تطبيق المؤثر"),
            cancel_label=tr("إلغاء"),
            cancel_name=tr("إلغاء تطبيق المؤثر"),
            cancelling_message=tr("جاري إلغاء تطبيق المؤثر"),
        )
        self.apply_progress_dialog.update_progress(self.silent_prepare_progress)
        self.apply_progress_dialog.Show()
        self.parent.say("جارٍ تطبيق المؤثر")
        self.update_apply_progress_dialog(self.silent_prepare_progress)

    def update_apply_progress_dialog(self, value):
        value = max(0, min(100, int(value)))
        if self.apply_progress_dialog:
            self.apply_progress_dialog.update_progress(value)
        if self.apply_progress_dialog and (value >= self.last_spoken_effect_percent + 10 or value >= 100):
            self.last_spoken_effect_percent = value
            message = tr("نسبة تطبيق المؤثر {percent} بالمئة").format(percent=value)
            self.parent.speech.say(message, False)

    def cancel_apply_effect(self):
        self.apply_cancel_requested.set()
        self.close_when_ready = True
        self.silent_apply_waiting = False
        self.stop_silent_preparation()
        self.update_progress(self.gauge.GetValue(), "جاري إلغاء تطبيق المؤثر")

    def apply_effect_cancelled(self):
        return self.apply_cancel_requested.is_set()

    def finish_cancelled_apply(self):
        close_requested = self.close_when_ready
        self.close_when_ready = False
        self.silent_apply_waiting = False
        self.busy = False
        self.destroy_apply_progress_dialog()
        self.update_progress(0, "تم إلغاء تطبيق المؤثر")
        self.parent.say("تم إلغاء تطبيق المؤثر")
        if close_requested:
            wx.CallAfter(self.close_dialog)
        else:
            wx.CallAfter(self.restore_focus)

    def destroy_apply_progress_dialog(self):
        if self.apply_progress_dialog:
            self.apply_progress_dialog.Destroy()
            self.apply_progress_dialog = None

    def show_preview_progress_dialog(self):
        if self.preview_progress_dialog:
            return
        self.last_spoken_preview_percent = -10
        self.preview_progress_dialog = SaveProgressDialog(
            self,
            self.cancel_preview_preparation,
            title=tr("جارٍ تجهيز المعاينة"),
            progress_template=tr("نسبة تجهيز المعاينة {percent} بالمئة"),
            status_name=tr("حالة تجهيز معاينة المؤثر"),
            gauge_name=tr("شريط تقدم تجهيز معاينة المؤثر"),
            cancel_label=tr("إلغاء"),
            cancel_name=tr("إلغاء تجهيز معاينة المؤثر"),
            cancelling_message=tr("جاري إلغاء تجهيز المعاينة"),
        )
        self.preview_progress_dialog.Show()
        self.parent.say("جارٍ تجهيز المعاينة")
        self.update_preview_progress_dialog(0)

    def update_preview_progress_dialog(self, value):
        value = max(0, min(100, int(value)))
        if self.preview_progress_dialog:
            self.preview_progress_dialog.update_progress(value)
        if self.preview_progress_dialog and (value >= self.last_spoken_preview_percent + 10 or value >= 100):
            self.last_spoken_preview_percent = value
            message = tr("نسبة تجهيز المعاينة {percent} بالمئة").format(percent=value)
            self.parent.speech.say(message, False)

    def destroy_preview_progress_dialog(self):
        if self.preview_progress_dialog:
            self.preview_progress_dialog.Destroy()
            self.preview_progress_dialog = None

    def cancel_preview_preparation(self):
        self.preview_cancel_requested.set()
        self.close_when_ready = True
        self.stop_silent_preparation()
        self.update_progress(self.gauge.GetValue(), "جاري إلغاء تجهيز المعاينة")

    def report_silent_preparation_progress(self, value, generation=None):
        value = max(0, min(100, int(value)))
        with self.silent_prepare_lock:
            if generation is not None:
                latest_generation = self.silent_prepare_pending[0] if self.silent_prepare_pending else generation
                if latest_generation != generation:
                    return
            self.silent_prepare_progress = value
            apply_waiting = self.silent_apply_waiting
            preview_waiting = self.preview_prepare_waiting
        if apply_waiting:
            wx.CallAfter(self.update_apply_progress_dialog, value)
        if preview_waiting:
            preview_percent = 100 if self.silent_prepare_done.is_set() else min(99, value)
            wx.CallAfter(self.update_preview_progress_dialog, preview_percent)

    def update_apply_work_progress(self, value, message):
        state = (int(max(0, min(100, value))), message)
        if state == self.last_apply_progress_state:
            return
        self.last_apply_progress_state = state
        self.update_progress(value, message)
        self.update_apply_progress_dialog(value)

    def schedule_silent_preparation(self, delay=300, allow_during_apply=False):
        if (self.silent_apply_waiting and not allow_during_apply) or self.silent_prepare_closed or self.silent_prepare_start is None or self.silent_prepare_end is None:
            return
        audio_filter = self.audio_filter()
        cache_key = audio_filter_cache_key(audio_filter)
        with self.silent_prepare_lock:
            self.silent_prepare_generation += 1
            generation = self.silent_prepare_generation
            self.silent_prepare_pending = (generation, cache_key, audio_filter)
            self.silent_prepare_progress = 70 if self.silent_prepare_source_path else 0
            self.silent_prepare_error = None
            self.silent_prepare_done.clear()
            if not self.silent_prepare_source_path:
                self.silent_prepare_source_ready.clear()
        timer = self.silent_prepare_timer
        self.silent_prepare_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        if delay <= 0:
            self.start_silent_preparation()
        else:
            self.silent_prepare_timer = wx.CallLater(delay, self.start_silent_preparation)

    def start_silent_preparation(self):
        self.silent_prepare_timer = None
        with self.silent_prepare_lock:
            if self.silent_prepare_closed or self.silent_prepare_running or not self.silent_prepare_pending:
                return
            self.silent_prepare_running = True
        threading.Thread(target=self.silent_preparation_worker, daemon=True).start()

    def silent_preparation_cancelled(self, generation):
        with self.silent_prepare_lock:
            if self.silent_prepare_closed or not self.silent_prepare_pending:
                return True
            return self.silent_prepare_pending[0] != generation

    def silent_source_preparation_cancelled(self):
        with self.silent_prepare_lock:
            closed = self.silent_prepare_closed
        return closed or self.apply_cancel_requested.is_set() or self.preview_cancel_requested.is_set()

    def silent_preparation_worker(self):
        created_temp_dir = ""
        try:
            with self.silent_prepare_lock:
                source_path = self.silent_prepare_source_path
                temp_dir = self.silent_prepare_temp_dir
            if not source_path:
                created_temp_dir = tempfile.mkdtemp(prefix="audio_effect_ready_")
                selected_segments = slice_segments(self.silent_prepare_timeline, self.silent_prepare_start, self.silent_prepare_end)
                source_path = os.path.join(created_temp_dir, "selected.wav")
                self.report_silent_preparation_progress(0)
                try:
                    write_timeline_audio(
                        selected_segments,
                        source_path,
                        lambda percent: self.report_silent_preparation_progress(percent * 0.70),
                        self.silent_source_preparation_cancelled,
                    )
                except IOError:
                    if self.silent_source_preparation_cancelled():
                        raise AudioEffectPreparationCancelled()
                    raise
                with self.silent_prepare_lock:
                    if self.silent_prepare_closed:
                        raise AudioEffectPreparationCancelled()
                    self.silent_prepare_source_path = source_path
                    self.silent_prepare_temp_dir = created_temp_dir
                    temp_dir = created_temp_dir
                    created_temp_dir = ""
                    self.silent_prepare_error = None
                    self.silent_prepare_source_ready.set()
                self.report_silent_preparation_progress(70)

            while True:
                with self.silent_prepare_lock:
                    if self.silent_prepare_closed or not self.silent_prepare_pending:
                        return
                    generation, cache_key, audio_filter = self.silent_prepare_pending
                    source_path = self.silent_prepare_source_path
                    temp_dir = self.silent_prepare_temp_dir
                extension = ".wav" if os.path.splitext(source_path)[1].lower() == ".wav" else ".mp4"
                output_path = os.path.join(temp_dir, f"effect_ready_{generation}{extension}")
                try:
                    self.report_silent_preparation_progress(70, generation)
                    apply_audio_filter(
                        source_path,
                        output_path,
                        audio_filter,
                        lambda percent: self.report_silent_preparation_progress(70 + percent * 0.24, generation),
                        lambda: self.silent_preparation_cancelled(generation),
                    )
                    self.report_silent_preparation_progress(94, generation)
                    finalize_audio_effect_output(
                        source_path,
                        output_path,
                        audio_filter,
                        lambda percent: self.report_silent_preparation_progress(94 + percent * 0.06, generation),
                        lambda: self.silent_preparation_cancelled(generation),
                    )
                except AudioEffectPreparationCancelled:
                    if os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                    continue
                except Exception:
                    if os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                    with self.silent_prepare_lock:
                        latest_generation = self.silent_prepare_pending[0] if self.silent_prepare_pending else generation
                    if latest_generation != generation:
                        continue
                    self.silent_prepare_done.set()
                    return

                old_result = ""
                keep_result = False
                with self.silent_prepare_lock:
                    latest_generation = self.silent_prepare_pending[0] if self.silent_prepare_pending else generation
                    if not self.silent_prepare_closed and latest_generation == generation:
                        old_result = self.silent_prepare_result_path
                        self.silent_prepare_result_path = output_path
                        self.silent_prepare_result_key = cache_key
                        keep_result = True
                if old_result and old_result != output_path and os.path.exists(old_result):
                    try:
                        os.remove(old_result)
                    except OSError:
                        pass
                if not keep_result and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                if keep_result:
                    self.report_silent_preparation_progress(100, generation)
                    self.silent_prepare_done.set()
                    return
        except AudioEffectPreparationCancelled as error:
            with self.silent_prepare_lock:
                self.silent_prepare_error = error
                self.silent_prepare_source_ready.set()
            self.silent_prepare_done.set()
        except Exception as error:
            with self.silent_prepare_lock:
                self.silent_prepare_error = error
                self.silent_prepare_source_ready.set()
            self.silent_prepare_done.set()
        finally:
            cleanup_dir = ""
            with self.silent_prepare_lock:
                self.silent_prepare_running = False
                if self.silent_prepare_closed and not self.silent_prepare_transferred:
                    cleanup_dir = self.silent_prepare_temp_dir or created_temp_dir
                    self.silent_prepare_source_path = ""
                    self.silent_prepare_temp_dir = ""
                    self.silent_prepare_result_path = ""
                    self.silent_prepare_result_key = None
            if cleanup_dir and os.path.exists(cleanup_dir):
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    def take_silent_prepared_result(self, audio_filter):
        cache_key = audio_filter_cache_key(audio_filter)
        timer = self.silent_prepare_timer
        self.silent_prepare_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        with self.silent_prepare_lock:
            result_path = self.silent_prepare_result_path
            if self.silent_prepare_result_key != cache_key or not result_path or not os.path.exists(result_path):
                return None
            temp_dir = self.silent_prepare_temp_dir

        selected_segments = slice_segments(self.silent_prepare_timeline, self.silent_prepare_start, self.silent_prepare_end)
        effect_result = result_path
        if selected_segments:
            has_video = False
            try:
                has_video = any(has_video_stream(segment.path) for segment in selected_segments)
            except Exception:
                has_video = True
            if has_video:
                if not _audio_effect_video_proxy_safe(selected_segments):
                    return None
                effect_result = _build_audio_effect_proxy_segments(selected_segments, result_path, temp_dir)
                if not effect_result:
                    return None

        with self.silent_prepare_lock:
            if self.silent_prepare_result_key != cache_key or self.silent_prepare_result_path != result_path:
                return None
            result = (
                effect_result,
                temp_dir,
                self.silent_prepare_start,
                self.silent_prepare_end,
            )
            self.silent_prepare_closed = True
            self.silent_prepare_transferred = True
            self.silent_prepare_source_path = ""
            self.silent_prepare_temp_dir = ""
            self.silent_prepare_result_path = ""
            self.silent_prepare_result_key = None
            return result

    def stop_silent_preparation(self):
        timer = self.silent_prepare_timer
        self.silent_prepare_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        cleanup_dir = ""
        with self.silent_prepare_lock:
            self.silent_prepare_closed = True
            self.silent_prepare_done.set()
            self.silent_prepare_source_ready.set()
            if not self.silent_prepare_running and not self.silent_prepare_transferred:
                cleanup_dir = self.silent_prepare_temp_dir
                self.silent_prepare_source_path = ""
                self.silent_prepare_temp_dir = ""
                self.silent_prepare_result_path = ""
                self.silent_prepare_result_key = None
        if cleanup_dir and os.path.exists(cleanup_dir):
            shutil.rmtree(cleanup_dir, ignore_errors=True)

    def focus_main_control(self):
        if self.tab_order:
            self.last_focus_control = self.tab_order[0]
            self.tab_order[0].SetFocus()
            return
        if self.preset_choice:
            self.preset_choice.SetFocus()
            return

    def update_progress(self, value, message):
        value = max(0, min(100, int(value)))
        self.gauge.SetValue(value)
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.gauge.SetName(message)
        self.notify_accessibility(self.status, wx.ACC_EVENT_OBJECT_NAMECHANGE)
        self.notify_accessibility(self.gauge, wx.ACC_EVENT_OBJECT_VALUECHANGE)

    def update_status_text(self, message):
        self.status.SetLabel(message)
        self.status.SetName(message)
        self.notify_accessibility(self.status, wx.ACC_EVENT_OBJECT_NAMECHANGE)

    def notify_accessibility(self, window, event_type=wx.ACC_EVENT_OBJECT_VALUECHANGE):
        if not wx.USE_ACCESSIBILITY:
            return
        try:
            wx.Accessible.NotifyEvent(event_type, window, wx.OBJID_CLIENT, wx.ACC_SELF)
        except Exception:
            pass

    def run_background(self, worker):
        if self.busy:
            return False
        self.busy = True
        self.update_progress(1, tr("جاري العمل"))
        threading.Thread(target=worker, daemon=True).start()
        return True

    def make_slider_handler(self, key):
        def handler(event=None):
            self.on_effect_setting_changed(key)
        return handler

    def make_slider_key_handler(self, key):
        def handler(event):
            key_code = event.GetKeyCode()
            target_value = self.slider_key_target(key_code, self.controls.get(key))
            if target_value is not None:
                if self.set_slider_by_key(key, target_value):
                    self.controls[key]["ignore_scroll_until"] = time.monotonic() + 0.08
                return
            delta = self.slider_key_delta(key_code, self.controls.get(key))
            if delta is None:
                event.Skip()
                return
            if self.adjust_slider_by_key(key, delta):
                self.controls[key]["ignore_scroll_until"] = time.monotonic() + 0.08
        return handler

    def make_slider_scroll_handler(self, key, delta):
        def handler(event):
            self.adjust_slider_from_scroll(key, delta, event)
        return handler

    def make_checkbox_handler(self, key):
        def handler(event=None):
            self.on_effect_setting_changed(key)
        return handler

    def make_choice_handler(self, key):
        def handler(event=None):
            self.on_effect_setting_changed(key)
        return handler

    def make_choice_key_handler(self, key):
        def handler(event):
            delta = self.slider_key_delta(event.GetKeyCode())
            if delta is None:
                event.Skip()
                return
            self.adjust_choice_by_key(key, delta)
        return handler

    def choice_index_for_value(self, choices, value):
        for index, item in enumerate(choices):
            if item["value"] == value:
                return index
        return 0

    def choice_value(self, widgets):
        choices = widgets.get("choices", [])
        selection = widgets["choice"].GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(choices):
            return choices[0]["value"] if choices else 0
        return choices[selection]["value"]

    def adjust_choice_by_key(self, key, direction):
        widgets = self.controls.get(key)
        if not widgets or widgets.get("type") != "choice":
            return False
        choice = widgets["choice"]
        count = choice.GetCount()
        if count <= 0:
            return False
        selection = choice.GetSelection()
        if selection == wx.NOT_FOUND:
            selection = 0
        selection = max(0, min(count - 1, selection + direction))
        if selection == choice.GetSelection():
            return True
        choice.SetSelection(selection)
        self.last_focus_control = choice
        self.on_effect_setting_changed(key)
        self.notify_accessibility(choice, wx.ACC_EVENT_OBJECT_SELECTION)
        self.notify_accessibility(choice, wx.ACC_EVENT_OBJECT_VALUECHANGE)
        return True

    def on_control_key_down(self, event):
        key = event.GetKeyCode()
        slider_key, _slider = self.slider_for_event(event)
        slider_widgets = self.controls.get(slider_key) if slider_key else None
        target_value = self.slider_key_target(key, slider_widgets)
        if target_value is not None:
            if self.set_slider_by_key(slider_key, target_value):
                return
            event.Skip()
            return
        delta = self.slider_key_delta(key, slider_widgets)
        if delta is not None:
            if self.adjust_focused_slider(delta, event):
                return
            event.Skip()
            return
        event.Skip()

    def on_slider_focus(self, event):
        self.last_focus_control = event.GetEventObject()
        event.Skip()

    def set_control_value(self, key, value):
        widgets = self.controls.get(key)
        if not widgets:
            return
        if widgets.get("type") == "checkbox":
            widgets["checkbox"].SetValue(bool(value))
            return
        if widgets.get("type") == "choice":
            widgets["choice"].SetSelection(self.choice_index_for_value(widgets.get("choices", []), value))
            self.notify_accessibility(widgets["choice"], wx.ACC_EVENT_OBJECT_SELECTION)
            self.notify_accessibility(widgets["choice"], wx.ACC_EVENT_OBJECT_VALUECHANGE)
            return
        slider = widgets["slider"]
        value = max(slider.GetMin(), min(slider.GetMax(), int(value)))
        slider.SetValue(value)
        widgets["last_value"] = value
        self.notify_accessibility(slider)

    def reset_defaults(self, event=None):
        if self.preset_choice:
            self.preset_choice.SetSelection(0)
        for control in self.effect_definition.get("controls", []):
            self.set_control_value(control["key"], control.get("default", False))
        self.update_status_text(tr("تم إرجاع الإعداد الافتراضي"))
        if self.can_start_direct_realtime_preview():
            self.preview_source = None
        self.schedule_preview_preparation()
        if self.preview_player.is_playing:
            self.schedule_restart_preview_filter(0)

    def on_preset_changed(self, event=None):
        if not self.preset_choice:
            return
        presets = self.effect_definition.get("presets", [])
        selection = self.preset_choice.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(presets):
            return
        for key, value in presets[selection].get("values", {}).items():
            self.set_control_value(key, value)
        self.update_status_text(f"{tr('الإعداد')} {tr(presets[selection]['name'])}")
        if self.can_start_direct_realtime_preview():
            self.preview_source = None
        self.schedule_preview_preparation()
        if self.preview_player.is_playing:
            self.schedule_restart_preview_filter(0)

    def schedule_restart_preview_filter(self, delay=50):
        if not self.preview_player.is_playing:
            return
        timer = getattr(self, "restart_filter_timer", None)
        self.restart_filter_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        if delay <= 0:
            self.preview_player.restart_with_filter(self.audio_filter())
        else:
            self.restart_filter_timer = wx.CallLater(delay, lambda: self.preview_player.restart_with_filter(self.audio_filter()))

    def on_effect_setting_changed(self, key):
        widgets = self.controls[key]
        if widgets.get("type") == "checkbox":
            value = tr("نعم") if widgets["checkbox"].GetValue() else tr("لا")
            self.update_status_text(f"{widgets['name']} {value}")
        elif widgets.get("type") == "choice":
            value = widgets["choice"].GetStringSelection()
            self.update_status_text(f"{widgets['name']} {value}")
        else:
            value = widgets["slider"].GetValue()
            widgets["last_value"] = value
            self.update_status_text(f"{widgets['name']} {value} {widgets.get('unit', tr('بالمئة'))}")
        if self.can_start_direct_realtime_preview():
            self.preview_source = None
        self.schedule_preview_preparation()
        if self.preview_player.is_playing:
            self.schedule_restart_preview_filter(50)

    def slider_key_delta(self, key, widgets=None):
        line_step = max(1, int((widgets or {}).get("line_step", 1)))
        page_step = max(1, int((widgets or {}).get("page_step", 10)))
        if key in (wx.WXK_UP, wx.WXK_RIGHT, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_RIGHT):
            return line_step
        if key in (wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_NUMPAD_DOWN, wx.WXK_NUMPAD_LEFT):
            return -line_step
        if key in (wx.WXK_PAGEUP, wx.WXK_NUMPAD_PAGEUP):
            return page_step
        if key in (wx.WXK_PAGEDOWN, wx.WXK_NUMPAD_PAGEDOWN):
            return -page_step
        return None

    def slider_key_target(self, key, widgets=None):
        widgets = widgets or {}
        if key in (wx.WXK_HOME, wx.WXK_NUMPAD_HOME):
            return widgets.get("home_value")
        if key in (wx.WXK_END, wx.WXK_NUMPAD_END):
            return widgets.get("end_value")
        return None

    def slider_for_event(self, event=None):
        event_object = event.GetEventObject() if event else None
        current_target_getter = getattr(event, "GetCurrentTarget", None) if event else None
        current_target = current_target_getter() if current_target_getter else None
        focused = wx.Window.FindFocus()
        if focused in self.tab_order:
            for widgets in self.controls.values():
                if widgets.get("type") == "slider" and focused is widgets["slider"]:
                    break
            else:
                return None, None
        for key, widgets in self.controls.items():
            if widgets.get("type") != "slider":
                continue
            slider = widgets["slider"]
            if event_object is slider or current_target is slider or focused is slider or self.last_focus_control is slider:
                return key, slider
        return None, None

    def adjust_focused_slider(self, direction, event=None):
        key, slider = self.slider_for_event(event)
        if not slider:
            return False
        return self.adjust_slider_by_key(key, direction)

    def adjust_slider_by_key(self, key, direction):
        widgets = self.controls.get(key)
        if not widgets or widgets.get("type") != "slider":
            return False
        slider = widgets["slider"]
        value = max(slider.GetMin(), min(slider.GetMax(), slider.GetValue() + direction))
        if value == slider.GetValue():
            return True
        slider.SetValue(value)
        widgets["last_value"] = value
        self.last_focus_control = slider
        self.on_effect_setting_changed(key)
        self.notify_accessibility(slider)
        return True

    def set_slider_by_key(self, key, value):
        widgets = self.controls.get(key)
        if not widgets or widgets.get("type") != "slider" or value is None:
            return False
        slider = widgets["slider"]
        value = max(slider.GetMin(), min(slider.GetMax(), int(value)))
        if value == slider.GetValue():
            return True
        slider.SetValue(value)
        widgets["last_value"] = value
        self.last_focus_control = slider
        self.on_effect_setting_changed(key)
        self.notify_accessibility(slider)
        return True

    def adjust_slider_from_scroll(self, key, direction, event=None):
        widgets = self.controls.get(key)
        if not widgets or widgets.get("type") != "slider":
            return False
        slider = widgets["slider"]
        if time.monotonic() < widgets.get("ignore_scroll_until", 0):
            slider.SetValue(int(widgets.get("last_value", slider.GetValue())))
            return True
        base_value = int(widgets.get("last_value", slider.GetValue()))
        if event:
            event_type = event.GetEventType()
            if event_type in (wx.EVT_SCROLL_PAGEUP.typeId, wx.EVT_SCROLL_PAGEDOWN.typeId):
                page_step = max(1, int(widgets.get("page_step", 10)))
                direction = page_step if direction > 0 else -page_step
            elif event_type in (wx.EVT_SCROLL_LINEUP.typeId, wx.EVT_SCROLL_LINEDOWN.typeId):
                line_step = max(1, int(widgets.get("line_step", 1)))
                direction = line_step if direction > 0 else -line_step
        value = max(slider.GetMin(), min(slider.GetMax(), base_value + direction))
        slider.SetValue(value)
        widgets["last_value"] = value
        self.last_focus_control = slider
        self.on_effect_setting_changed(key)
        self.notify_accessibility(slider)
        return True

    def focused_slider_page_step(self):
        return 10

    def move_focus_by_tab(self, backwards=False):
        if not self.tab_order:
            return False
        focused = wx.Window.FindFocus()
        try:
            index = self.tab_order.index(focused)
        except ValueError:
            index = -1 if not backwards else 0
        if backwards:
            index = (index - 1) % len(self.tab_order)
        else:
            index = (index + 1) % len(self.tab_order)
        self.last_focus_control = self.tab_order[index]
        self.tab_order[index].SetFocus()
        return True

    def play_preview(self, event=None):
        self.remember_focus()
        if self.busy:
            self.update_progress(self.gauge.GetValue(), "جاري إكمال العمل الحالي")
            wx.CallAfter(self.restore_focus)
            return
        if self.preview_player.is_playing:
            wx.CallAfter(self.restore_focus)
            return
        output_volume = current_program_output_volume(self.parent)
        output_volume_provider = lambda: current_program_output_volume(self.parent)
        if self.preview_source:
            input_path, start_time, duration, temp_dir, *preview_filter = self.preview_source
            audio_filter = preview_filter[0] if preview_filter else self.audio_filter()
            self.preview_player.start(input_path, audio_filter, start_time, duration, self.preview_player.offset, output_volume, output_volume_provider)
            if hasattr(self.parent, "start_audio_effect_background_preview"):
                self.parent.start_audio_effect_background_preview(self.preview_player.offset)
            wx.CallAfter(self.restore_focus)
            return

        try:
            direct_source = self.direct_realtime_preview_source()
        except Exception:
            direct_source = None
        if direct_source:
            input_path, start_time, duration, temp_dir = direct_source
            self.preview_source = direct_source
            self.preview_dir = temp_dir or ""
            self.preview_player.start(input_path, self.audio_filter(), start_time, duration, 0, output_volume, output_volume_provider)
            if hasattr(self.parent, "start_audio_effect_background_preview"):
                self.parent.start_audio_effect_background_preview(0)
            wx.CallAfter(self.restore_focus)
            return

        self.preview_cancel_requested.clear()
        self.preview_prepare_waiting = True
        self.show_preview_progress_dialog()
        with self.silent_prepare_lock:
            has_pending_preparation = bool(self.silent_prepare_pending)
        if has_pending_preparation:
            self.start_silent_preparation()
        else:
            self.schedule_silent_preparation(0)

        def worker():
            try:
                requested_filter = self.audio_filter()
                requested_key = audio_filter_cache_key(requested_filter)
                while not self.silent_prepare_done.wait(0.10):
                    if self.preview_cancel_requested.is_set():
                        raise AudioEffectPreparationCancelled()
                if self.preview_cancel_requested.is_set():
                    raise AudioEffectPreparationCancelled()
                with self.silent_prepare_lock:
                    input_path = self.silent_prepare_result_path
                    result_key = self.silent_prepare_result_key
                    error = self.silent_prepare_error
                if error:
                    raise error
                if result_key != requested_key or not input_path or not os.path.exists(input_path):
                    raise RuntimeError("تعذر تجهيز مصدر المعاينة الصوتية")
                duration = self.selected_effect_duration()
                self.preview_source = (input_path, 0, duration, "", "anull")
                wx.CallAfter(self.update_preview_progress_dialog, 100)
                wx.CallAfter(self.destroy_preview_progress_dialog)
                wx.CallAfter(self.update_progress, 100, "تم تجهيز المعاينة الفورية")
                output_volume = current_program_output_volume(self.parent)
                output_volume_provider = lambda: current_program_output_volume(self.parent)
                self.preview_player.start(input_path, "anull", 0, duration, 0, output_volume, output_volume_provider)
                if hasattr(self.parent, "start_audio_effect_background_preview"):
                    wx.CallAfter(self.parent.start_audio_effect_background_preview, 0)
                wx.CallAfter(self.restore_focus)
            except OperationCancelled:
                wx.CallAfter(self.destroy_preview_progress_dialog)
                wx.CallAfter(self.update_progress, 0, "تم إلغاء تجهيز المعاينة")
            except Exception as error:
                wx.CallAfter(self.destroy_preview_progress_dialog)
                if is_operation_cancelled(error, self.silent_source_preparation_cancelled):
                    wx.CallAfter(self.update_progress, 0, "تم إلغاء تجهيز المعاينة")
                    return
                wx.CallAfter(
                    show_error,
                    f"تعذر تشغيل المعاينة الفورية: {error}",
                    "خطأ",
                    self,
                    exception=error,
                    context="audio_effect_preview",
                )
                wx.CallAfter(self.update_progress, 0, "تعذر تجهيز المعاينة")
            finally:
                self.preview_prepare_waiting = False
                self.busy = False
                if self.close_when_ready:
                    wx.CallAfter(self.close_dialog)

        self.run_background(worker)

    def pause_preview(self, event=None):
        self.remember_focus()
        self.preview_player.pause()
        if hasattr(self.parent, "pause_audio_effect_background_preview"):
            self.parent.pause_audio_effect_background_preview()
        wx.CallAfter(self.restore_focus)

    def stop_preview(self, event=None):
        self.remember_focus()
        self.preview_player.reset()
        if hasattr(self.parent, "stop_audio_effect_background_preview"):
            self.parent.stop_audio_effect_background_preview()
        wx.CallAfter(self.restore_focus)

    def rewind_preview(self, event=None):
        self.remember_focus()
        self.preview_player.seek(-5)
        if hasattr(self.parent, "seek_audio_effect_background_preview"):
            self.parent.seek_audio_effect_background_preview(self.preview_player.offset)
        wx.CallAfter(self.restore_focus)

    def forward_preview(self, event=None):
        self.remember_focus()
        self.preview_player.seek(5)
        if hasattr(self.parent, "seek_audio_effect_background_preview"):
            self.parent.seek_audio_effect_background_preview(self.preview_player.offset)
        wx.CallAfter(self.restore_focus)

    def toggle_preview(self):
        focused = wx.Window.FindFocus()
        if isinstance(focused, wx.Button):
            return False
        if self.preview_player.is_playing:
            self.stop_preview()
        else:
            self.play_preview()
        return True

    def apply_effect(self, event=None):
        set_audio_effect_values(self.effect_definition["key"], self.effect_values())
        audio_filter = self.audio_filter()
        self.apply_cancel_requested.clear()
        self.cleanup_preview(False)
        prepared = None if getattr(self.parent, "media_kind", "") == "video" else self.take_silent_prepared_result(audio_filter)
        if prepared:
            if self.apply_effect_cancelled():
                self.finish_cancelled_apply()
                return
            effect_path, temp_dir, start_time, end_time = prepared
            self.finish_apply_success(effect_path, temp_dir, start_time, end_time)
            return
        self.close_when_ready = True
        self.busy = True
        self.silent_apply_filter = audio_filter
        self.silent_apply_waiting = True
        self.show_apply_progress_dialog()
        if getattr(self.parent, "media_kind", "") == "video":
            # في الفيديو لا نثبت نتيجة المعاينة الجزئية داخل مقاطع الخط الزمني؛
            # نجهز صوت المشروع الكامل من خلال المدير المركزي مباشرة.
            self.finish_silent_apply()
            return
        timer = self.silent_prepare_timer
        self.silent_prepare_timer = None
        if timer:
            try:
                timer.Stop()
            except Exception:
                pass
        self.schedule_silent_preparation(0, allow_during_apply=True)
        threading.Thread(target=self.wait_for_silent_apply, daemon=True).start()

    def wait_for_silent_apply(self):
        self.silent_prepare_done.wait()
        wx.CallAfter(self.finish_silent_apply)

    def finish_silent_apply(self):
        if self.apply_effect_cancelled():
            self.finish_cancelled_apply()
            return
        audio_filter = self.silent_apply_filter
        prepared = None if getattr(self.parent, "media_kind", "") == "video" else self.take_silent_prepared_result(audio_filter)
        if prepared:
            if self.apply_effect_cancelled():
                self.finish_cancelled_apply()
                return
            effect_path, temp_dir, start_time, end_time = prepared
            self.finish_apply_success(effect_path, temp_dir, start_time, end_time)
            return
        self.stop_silent_preparation()

        def worker():
            try:
                effect_path, temp_dir, start_time, end_time = self.parent.prepare_audio_effect_to_selection(
                    audio_filter,
                    lambda value, message: wx.CallAfter(self.update_apply_work_progress, value, message),
                    self.apply_effect_cancelled,
                )
                if self.apply_effect_cancelled():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    wx.CallAfter(self.finish_cancelled_apply)
                    return
                wx.CallAfter(self.finish_apply_success, effect_path, temp_dir, start_time, end_time)
            except OperationCancelled:
                wx.CallAfter(self.finish_cancelled_apply)
            except Exception as error:
                # بعض المكتبات القديمة ترمي OSError عند الإلغاء. نعامله كإلغاء طبيعي
                # ولا نرسل تقرير خطأ ولا نترك نافذة التقدم معلقة.
                if is_operation_cancelled(error, self.apply_effect_cancelled):
                    wx.CallAfter(self.finish_cancelled_apply)
                    return
                self.close_when_ready = False
                self.silent_apply_waiting = False
                wx.CallAfter(self.destroy_apply_progress_dialog)
                wx.CallAfter(
                    show_error,
                    f"تعذر تطبيق المؤثر الصوتي: {error}",
                    "خطأ",
                    self,
                    exception=error,
                    context="audio_effect_apply",
                )
                wx.CallAfter(self.update_progress, 0, "تعذر تطبيق المؤثر الصوتي")
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True).start()

    def finish_apply_success(self, effect_path, temp_dir, start_time, end_time):
        if self.apply_effect_cancelled():
            self.finish_cancelled_apply()
            return
        try:
            self.destroy_apply_progress_dialog()
            effect_name = tr(self.effect_definition.get("name", "المؤثر الصوتي"))
            operation_name = tr("تطبيق {name}").format(name=effect_name)
            self.parent.commit_audio_effect_to_selection(
                effect_path,
                temp_dir,
                start_time,
                end_time,
                operation_name=operation_name,
                effect_key=self.effect_definition.get("key", "audio_effect"),
                effect_parameters=self.effect_values(),
            )
            self.close_after_apply()
        except Exception as error:
            self.close_when_ready = False
            self.silent_apply_waiting = False
            self.busy = False
            self.destroy_apply_progress_dialog()
            show_error(
                f"تعذر تثبيت المؤثر الصوتي في الخط الزمني: {error}",
                "خطأ",
                self,
                exception=error,
                context="audio_effect_commit",
            )
            self.update_progress(0, "تعذر تثبيت المؤثر الصوتي")
            wx.CallAfter(self.restore_focus)

    def cleanup_preview(self, announce=True):
        self.preview_player.reset(announce, wait=True)
        if hasattr(self.parent, "stop_audio_effect_background_preview"):
            self.parent.stop_audio_effect_background_preview()
        if self.preview_dir and os.path.exists(self.preview_dir):
            shutil.rmtree(self.preview_dir, ignore_errors=True)
        self.preview_path = ""
        self.preview_dir = ""
        self.preview_source = None

    def close_dialog(self, event=None):
        if self.busy:
            self.close_when_ready = True
            self.cleanup_preview(False)
            if self.silent_apply_waiting:
                self.apply_cancel_requested.set()
            if self.preview_prepare_waiting:
                self.preview_cancel_requested.set()
            self.stop_silent_preparation()
            self.update_progress(self.gauge.GetValue(), "جاري إلغاء العمل الحالي")
            return
        self.destroy_apply_progress_dialog()
        self.destroy_preview_progress_dialog()
        self.stop_silent_preparation()
        self.cleanup_preview()
        self.Destroy()

    def close_after_apply(self):
        self.busy = False
        self.destroy_apply_progress_dialog()
        self.destroy_preview_progress_dialog()
        self.stop_silent_preparation()
        self.cleanup_preview(False)
        self.Destroy()

    def on_key(self, event):
        if event.GetKeyCode() == wx.WXK_TAB and self.move_focus_by_tab(event.ShiftDown()):
            return
        focused_slider_key, _focused_slider = self.slider_for_event(event)
        focused_widgets = self.controls.get(focused_slider_key) if focused_slider_key else None
        target_value = self.slider_key_target(event.GetKeyCode(), focused_widgets)
        if target_value is not None and self.set_slider_by_key(focused_slider_key, target_value):
            return
        delta = self.slider_key_delta(event.GetKeyCode(), focused_widgets)
        if delta is not None and self.adjust_focused_slider(delta, event):
            return
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.close_dialog()
            return
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            focus = wx.Window.FindFocus()
            if isinstance(focus, wx.Button):
                event.Skip()
                return
            self.apply_effect()
            return
        if event.GetKeyCode() == wx.WXK_F4:
            self.play_preview()
            return
        if event.GetKeyCode() == wx.WXK_F5:
            self.rewind_preview()
            return
        if event.GetKeyCode() == wx.WXK_F6:
            self.forward_preview()
            return
        if event.GetKeyCode() == wx.WXK_F7:
            self.pause_preview()
            return
        if event.GetKeyCode() == wx.WXK_F8:
            self.stop_preview()
            return
        if event.GetKeyCode() == wx.WXK_SPACE and self.toggle_preview():
            return
        event.Skip()


def _audio_effect_video_proxy_safe(selected_segments):
    if not selected_segments:
        return False
    for segment in selected_segments:
        try:
            if not has_video_stream(segment.path):
                return False
        except Exception:
            return False
        speed = max(0.05, float(getattr(segment, "speed", 1.0) or 1.0))
        if abs(speed - 1.0) > 0.001:
            return False
    return True


def _build_audio_effect_proxy_segments(selected_segments, effect_audio_path, temp_dir=None, progress_callback=None, cancelled_callback=None):
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    total_selected_duration = sum(max(0.0, segment.duration) for segment in selected_segments)
    effect_duration = get_media_duration(effect_audio_path)
    if abs(effect_duration - total_selected_duration) > 0.05:
        return None
    result = []
    cursor = 0.0
    for segment in selected_segments:
        if audio_effect_cancelled(cancelled_callback):
            raise AudioEffectPreparationCancelled()
        duration = max(0.0, segment.duration)
        if duration <= 0.001:
            continue
        result.append(TimelineSegment(
            segment.path,
            segment.start,
            segment.end,
            float(getattr(segment, "speed", 1.0) or 1.0),
            1.0,
            effect_audio_path,
            cursor,
            str(getattr(segment, "navigation_group", "") or ""),
            str(getattr(segment, "source_file_id", "") or ""),
            str(getattr(segment, "source_file_name", "") or ""),
            str(getattr(segment, "transition", "") or ""),
            max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        ))
        cursor += duration
        if progress_callback:
            progress_callback(min(100, cursor * 100.0 / max(0.001, total_selected_duration)))
    if progress_callback:
        progress_callback(100)
    return result or None


def _build_audio_effect_video_render(
    selected_segments,
    temp_dir,
    audio_filter,
    progress_callback=None,
    cancelled_callback=None,
    prepare_start=5,
    prepare_scale=0.65,
    apply_start=75,
    apply_scale=0.20,
    match_start=95,
    match_scale=0.05,
):
    selected_path = os.path.join(temp_dir, "selected.mp4")
    output_path = os.path.join(temp_dir, "effect.mp4")
    write_timeline_video(
        selected_segments,
        selected_path,
        (lambda percent: progress_callback(prepare_start + percent * prepare_scale, "جاري تجهيز الجزء المحدد")) if progress_callback else None,
        cancelled_callback,
    )
    apply_audio_filter(
        selected_path,
        output_path,
        audio_filter,
        (lambda percent: progress_callback(apply_start + percent * apply_scale, "جاري تطبيق المؤثر الصوتي")) if progress_callback else None,
        cancelled_callback,
    )
    finalize_audio_effect_output(
        selected_path,
        output_path,
        audio_filter,
        (lambda percent: progress_callback(match_start + percent * match_scale, "جاري إنهاء المؤثر الصوتي")) if progress_callback else None,
        cancelled_callback,
    )
    return output_path


def build_audio_effect_segment(timeline, start_time, end_time, audio_filter):
    temp_dir = tempfile.mkdtemp(prefix="audio_effect_")
    selected_segments = slice_segments(timeline, start_time, end_time)
    audio_only = selected_segments and not has_video_stream(selected_segments[0].path)
    selected_path = os.path.join(temp_dir, "selected.wav" if audio_only else "selected.mp4")
    output_path = os.path.join(temp_dir, "effect.wav" if audio_only else "effect.mp4")
    if audio_only:
        write_timeline_audio(selected_segments, selected_path)
    elif _audio_effect_video_proxy_safe(selected_segments):
        selected_path = os.path.join(temp_dir, "selected.wav")
        output_path = os.path.join(temp_dir, "effect.wav")
        write_timeline_audio(selected_segments, selected_path)
    else:
        write_timeline_video(selected_segments, selected_path)
    apply_audio_filter(selected_path, output_path, audio_filter)
    finalize_audio_effect_output(selected_path, output_path, audio_filter)
    if not audio_only and _audio_effect_video_proxy_safe(selected_segments):
        effect_segments = _build_audio_effect_proxy_segments(selected_segments, output_path, temp_dir)
        if effect_segments:
            return effect_segments, temp_dir
        output_path = _build_audio_effect_video_render(selected_segments, temp_dir, audio_filter)
    return output_path, temp_dir


def build_audio_effect_segment_with_progress(timeline, start_time, end_time, audio_filter, progress_callback=None, cancelled_callback=None):
    if audio_effect_cancelled(cancelled_callback):
        raise AudioEffectPreparationCancelled()
    temp_dir = tempfile.mkdtemp(prefix="audio_effect_")
    try:
        selected_segments = slice_segments(timeline, start_time, end_time)
        audio_only = selected_segments and not has_video_stream(selected_segments[0].path)
        video_proxy_safe = not audio_only and _audio_effect_video_proxy_safe(selected_segments)
        fast_audio_path = audio_only or video_proxy_safe
        prepare_scale = 0.05 if fast_audio_path else 0.20
        apply_start = 10 if fast_audio_path else 25
        apply_scale = 0.85 if fast_audio_path else 0.60
        match_start = 95 if fast_audio_path else 85
        match_scale = 0.05 if fast_audio_path else 0.10
        selected_path = os.path.join(temp_dir, "selected.wav" if audio_only else "selected.mp4")
        output_path = os.path.join(temp_dir, "effect.wav" if audio_only else "effect.mp4")
        if progress_callback:
            progress_callback(5, "جاري تجهيز الجزء المحدد")
        if audio_only:
            write_timeline_audio(selected_segments, selected_path, lambda percent: progress_callback(5 + percent * prepare_scale, "جاري تجهيز الجزء المحدد") if progress_callback else None, cancelled_callback)
        elif video_proxy_safe:
            selected_path = os.path.join(temp_dir, "selected.wav")
            output_path = os.path.join(temp_dir, "effect.wav")
            write_timeline_audio(selected_segments, selected_path, lambda percent: progress_callback(5 + percent * prepare_scale, "جاري تجهيز الجزء المحدد") if progress_callback else None, cancelled_callback)
        else:
            write_timeline_video(selected_segments, selected_path, lambda percent: progress_callback(5 + percent * prepare_scale, "جاري تجهيز الجزء المحدد") if progress_callback else None, cancelled_callback)
        if progress_callback:
            progress_callback(apply_start, "جارٍ تطبيق المؤثر الصوتي")
        apply_audio_filter(
            selected_path,
            output_path,
            audio_filter,
            (lambda percent: progress_callback(apply_start + percent * apply_scale, "جارٍ تطبيق المؤثر الصوتي")) if progress_callback else None,
            cancelled_callback,
        )
        if progress_callback:
            progress_callback(match_start, "جاري إنهاء المؤثر الصوتي")
        finalize_audio_effect_output(
            selected_path,
            output_path,
            audio_filter,
            (lambda percent: progress_callback(match_start + percent * match_scale, "جاري إنهاء المؤثر الصوتي")) if progress_callback else None,
            cancelled_callback,
        )
        if video_proxy_safe:
            if progress_callback:
                progress_callback(95, "جاري تجهيز صوت المؤثر داخل الفيديو")
            effect_segments = _build_audio_effect_proxy_segments(
                selected_segments,
                output_path,
                temp_dir,
                (lambda percent: progress_callback(95 + percent * 0.05, "جاري تجهيز صوت المؤثر داخل الفيديو")) if progress_callback else None,
                cancelled_callback,
            )
            if effect_segments:
                if progress_callback:
                    progress_callback(100, "تم تطبيق المؤثر الصوتي")
                return effect_segments, temp_dir
            output_path = _build_audio_effect_video_render(
                selected_segments,
                temp_dir,
                audio_filter,
                progress_callback,
                cancelled_callback,
                prepare_start=95,
                prepare_scale=0.02,
                apply_start=97,
                apply_scale=0.02,
                match_start=99,
                match_scale=0.01,
            )
        if progress_callback:
            progress_callback(100, "تم تطبيق المؤثر الصوتي")
        return output_path, temp_dir
    except AudioEffectPreparationCancelled:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def replace_audio_effect_range(timeline, start_time, end_time, effect_path):
    if isinstance(effect_path, (list, tuple)):
        remaining = delete_range(timeline, start_time, end_time)
        return insert_segments(remaining, start_time, list(effect_path))
    duration = get_media_duration(effect_path)
    effect_segments = replacement_segments_preserving_files(
        timeline, start_time, end_time, effect_path, duration
    )
    remaining = delete_range(timeline, start_time, end_time)
    return insert_segments(remaining, start_time, effect_segments)

