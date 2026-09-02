import math
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

import numpy as np

from video_maker.audio_ducking import (
    AUDIO_DUCKING_FIELD,
    AudioDuckingDialog,
    LOCALIZED_TEXTS,
    apply_main_volume_to_timeline,
    apply_ducking_to_background_items,
    build_live_ducking_preview_args,
    normalize_ducking_settings,
)
from video_maker.app_paths import ffmpeg_binary
from video_maker.timeline import TimelineSegment
from video_maker.video_editing import write_timeline_audio


SAMPLE_RATE = 48000


def write_tone_pattern(path, frequency, segments):
    samples = []
    for duration, amplitude in segments:
        count = int(duration * SAMPLE_RATE)
        t = np.arange(count, dtype=np.float32) / SAMPLE_RATE
        wave_data = np.sin(2 * math.pi * frequency * t) * float(amplitude)
        samples.append(wave_data)
    mono = np.concatenate(samples) if samples else np.zeros(0, dtype=np.float32)
    stereo = np.column_stack([mono, mono])
    pcm = np.clip(stereo * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())


def read_wav_mono(path):
    with wave.open(path, "rb") as source:
        channels = source.getnchannels()
        rate = source.getframerate()
        data = source.readframes(source.getnframes())
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return rate, audio


def tone_magnitude(audio, rate, start, end, frequency):
    window = audio[int(start * rate):int(end * rate)]
    window = window * np.hanning(len(window))
    spectrum = np.fft.rfft(window)
    freqs = np.fft.rfftfreq(len(window), 1 / rate)
    index = int(np.argmin(np.abs(freqs - frequency)))
    return float(np.abs(spectrum[index]) / max(1, len(window)))


class AudioDuckingTest(unittest.TestCase):
    def test_preview_reports_when_no_background_audio_was_added(self):
        class Parent:
            background_audio_items = []

        dialog = AudioDuckingDialog.__new__(AudioDuckingDialog)
        dialog.parent = Parent()
        dialog.text = LOCALIZED_TEXTS["en"]

        with self.assertRaisesRegex(RuntimeError, "No background audio has been added"):
            dialog.live_preview_source()

    def test_preview_warning_is_spoken_by_parent_say(self):
        spoken = []
        progress = []

        class Parent:
            def say(self, message):
                spoken.append(message)

        dialog = AudioDuckingDialog.__new__(AudioDuckingDialog)
        dialog.parent = Parent()
        dialog.update_progress = lambda value, message: progress.append((value, message))

        dialog.announce_preview_message("No background audio has been added")

        self.assertEqual(spoken, ["No background audio has been added"])
        self.assertEqual(progress, [(0, "No background audio has been added")])

    def test_main_volume_label_matches_project_kind(self):
        class Parent:
            media_kind = "video"

        dialog = AudioDuckingDialog.__new__(AudioDuckingDialog)
        dialog.parent = Parent()
        dialog.text = LOCALIZED_TEXTS["en"]
        control = {"key": "main_volume_percent", "name": LOCALIZED_TEXTS["en"]["main_volume"]}

        self.assertEqual(dialog.control_display_name(control), "Original video volume")
        dialog.parent.media_kind = "audio"
        self.assertEqual(dialog.control_display_name(control), "Audio file volume")

    def test_apply_ducking_splits_background_item_at_selection_edges(self):
        item = {
            "id": "bg1",
            "type": "background_audio",
            "path": "background.wav",
            "start": 0.0,
            "end": 10.0,
            "source_offset": 2.0,
            "speed": 1.0,
            "volume": 0.8,
        }

        updated, changed = apply_ducking_to_background_items([item], 3.0, 7.0, {"reduction_db": 20})

        self.assertEqual(changed, 1)
        self.assertEqual(len(updated), 3)
        self.assertEqual((updated[0]["start"], updated[0]["end"]), (0.0, 3.0))
        self.assertEqual((updated[1]["start"], updated[1]["end"]), (3.0, 7.0))
        self.assertEqual((updated[2]["start"], updated[2]["end"]), (7.0, 10.0))
        self.assertAlmostEqual(updated[1]["source_offset"], 5.0)
        self.assertIn(AUDIO_DUCKING_FIELD, updated[1])
        self.assertNotEqual(updated[0]["id"], updated[1]["id"])
        self.assertNotEqual(updated[1]["id"], updated[2]["id"])

    def test_rendered_audio_ducking_raises_background_during_silence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_path = os.path.join(temp_dir, "voice.wav")
            background_path = os.path.join(temp_dir, "background.wav")
            output_path = os.path.join(temp_dir, "ducked.wav")
            write_tone_pattern(voice_path, 220, [(1.0, 0.75), (1.0, 0.0), (1.0, 0.75)])
            write_tone_pattern(background_path, 880, [(3.0, 0.30)])

            settings = normalize_ducking_settings({
                "reduction_db": 36,
                "threshold_db": -45,
                "attack_ms": 5,
                "release_ms": 120,
            })
            timeline = [TimelineSegment(voice_path, 0.0, 3.0)]
            background_items = [{
                "id": "bg",
                "type": "background_audio",
                "path": background_path,
                "start": 0.0,
                "end": 3.0,
                "volume": 1.0,
                AUDIO_DUCKING_FIELD: settings,
            }]

            write_timeline_audio(timeline, output_path, background_audio_items=background_items)

            rate, audio = read_wav_mono(output_path)
            speech_background = tone_magnitude(audio, rate, 0.25, 0.85, 880)
            silence_background = tone_magnitude(audio, rate, 1.25, 1.85, 880)

            self.assertGreater(silence_background, speech_background * 1.25)

    def test_live_preview_args_render_ducking_without_prepared_preview_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_path = os.path.join(temp_dir, "voice.wav")
            background_path = os.path.join(temp_dir, "background.wav")
            output_path = os.path.join(temp_dir, "live_preview.wav")
            write_tone_pattern(voice_path, 220, [(1.0, 0.75), (1.0, 0.0), (1.0, 0.75)])
            write_tone_pattern(background_path, 880, [(3.0, 0.30)])

            settings = normalize_ducking_settings({
                "reduction_db": 36,
                "threshold_db": -45,
                "attack_ms": 5,
                "release_ms": 120,
            })
            timeline = [TimelineSegment(voice_path, 0.0, 3.0)]
            background_items = [{
                "id": "bg",
                "type": "background_audio",
                "path": background_path,
                "start": 0.0,
                "end": 3.0,
                "volume": 1.0,
                AUDIO_DUCKING_FIELD: settings,
            }]

            args = build_live_ducking_preview_args(timeline, background_items, [], 3.0, temp_dir)
            command = [ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *args, "-c:a", "pcm_s16le", output_path]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="ignore"))

            rate, audio = read_wav_mono(output_path)
            speech_background = tone_magnitude(audio, rate, 0.25, 0.85, 880)
            silence_background = tone_magnitude(audio, rate, 1.25, 1.85, 880)
            original_voice = tone_magnitude(audio, rate, 0.25, 0.85, 220)

            self.assertGreater(silence_background, speech_background * 1.25)
            self.assertGreater(silence_background, 0.02)
            self.assertGreater(original_voice, 0.02)

    def test_live_preview_keeps_background_audible_with_default_ducking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_path = os.path.join(temp_dir, "voice.wav")
            background_path = os.path.join(temp_dir, "background.wav")
            output_path = os.path.join(temp_dir, "live_default.wav")
            write_tone_pattern(voice_path, 220, [(3.0, 0.75)])
            write_tone_pattern(background_path, 880, [(3.0, 0.30)])

            settings = normalize_ducking_settings({})
            timeline = [TimelineSegment(voice_path, 0.0, 3.0)]
            background_items = [{
                "id": "bg",
                "type": "background_audio",
                "path": background_path,
                "start": 0.0,
                "end": 3.0,
                "volume": 1.0,
                AUDIO_DUCKING_FIELD: settings,
            }]

            args = build_live_ducking_preview_args(timeline, background_items, [], 3.0, temp_dir)
            subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *args, "-c:a", "pcm_s16le", output_path], check=True)

            rate, audio = read_wav_mono(output_path)
            original_voice = tone_magnitude(audio, rate, 0.25, 0.85, 220)
            background = tone_magnitude(audio, rate, 0.25, 0.85, 880)

            self.assertGreater(original_voice, 0.02)
            self.assertGreater(background, 0.02)

    def test_live_preview_main_volume_control_lowers_original_audio_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_path = os.path.join(temp_dir, "voice.wav")
            background_path = os.path.join(temp_dir, "background.wav")
            full_output_path = os.path.join(temp_dir, "full.wav")
            low_output_path = os.path.join(temp_dir, "low.wav")
            write_tone_pattern(voice_path, 220, [(3.0, 0.75)])
            write_tone_pattern(background_path, 880, [(3.0, 0.30)])

            settings = normalize_ducking_settings({
                "reduction_db": 18,
                "threshold_db": -20,
                "attack_ms": 5,
                "release_ms": 120,
            })
            timeline = [TimelineSegment(voice_path, 0.0, 3.0)]
            background_items = [{
                "id": "bg",
                "type": "background_audio",
                "path": background_path,
                "start": 0.0,
                "end": 3.0,
                "volume": 1.0,
                AUDIO_DUCKING_FIELD: settings,
            }]

            full_args = build_live_ducking_preview_args(timeline, background_items, [], 3.0, temp_dir, main_volume_percent=100)
            low_args = build_live_ducking_preview_args(timeline, background_items, [], 3.0, temp_dir, main_volume_percent=25)
            subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *full_args, "-c:a", "pcm_s16le", full_output_path], check=True)
            subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *low_args, "-c:a", "pcm_s16le", low_output_path], check=True)

            full_rate, full_audio = read_wav_mono(full_output_path)
            low_rate, low_audio = read_wav_mono(low_output_path)
            full_voice = tone_magnitude(full_audio, full_rate, 0.25, 0.85, 220)
            low_voice = tone_magnitude(low_audio, low_rate, 0.25, 0.85, 220)
            low_background = tone_magnitude(low_audio, low_rate, 0.25, 0.85, 880)

            self.assertLess(low_voice, full_voice * 0.35)
            self.assertGreater(low_background, 0.005)

    def test_dialog_live_preview_passes_main_volume_and_keeps_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_path = os.path.join(temp_dir, "voice.wav")
            background_path = os.path.join(temp_dir, "background.wav")
            full_output_path = os.path.join(temp_dir, "dialog_full.wav")
            low_output_path = os.path.join(temp_dir, "dialog_low.wav")
            write_tone_pattern(voice_path, 220, [(3.0, 0.75)])
            write_tone_pattern(background_path, 880, [(3.0, 0.30)])

            settings = normalize_ducking_settings({
                "reduction_db": 18,
                "threshold_db": -20,
                "attack_ms": 5,
                "release_ms": 120,
            })

            class Parent:
                background_audio_items = [{
                    "id": "bg",
                    "type": "background_audio",
                    "path": background_path,
                    "start": 0.0,
                    "end": 3.0,
                    "volume": 1.0,
                }]
                sound_effects_items = []
                muted_tracks = set()
                solo_tracks = set()
                timeline = [TimelineSegment(voice_path, 0.0, 3.0)]

            dialog = AudioDuckingDialog.__new__(AudioDuckingDialog)
            dialog.parent = Parent()
            dialog.preview_temp_dir = ""
            dialog.selected_range = lambda: (0.0, 3.0)

            def render_with_main_volume(percent, output_path):
                values = dict(settings)
                values["main_volume_percent"] = percent
                dialog.values = lambda: values
                input_path, _start_time, duration, live_temp_dir = dialog.live_preview_source()
                try:
                    args = input_path["build_args"](0.0, duration)
                    subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", *args, "-c:a", "pcm_s16le", output_path], check=True)
                finally:
                    shutil.rmtree(live_temp_dir, ignore_errors=True)

            render_with_main_volume(100, full_output_path)
            render_with_main_volume(25, low_output_path)

            full_rate, full_audio = read_wav_mono(full_output_path)
            low_rate, low_audio = read_wav_mono(low_output_path)
            full_voice = tone_magnitude(full_audio, full_rate, 0.25, 0.85, 220)
            low_voice = tone_magnitude(low_audio, low_rate, 0.25, 0.85, 220)
            low_background = tone_magnitude(low_audio, low_rate, 0.25, 0.85, 880)

            self.assertLess(low_voice, full_voice * 0.35)
            self.assertGreater(low_background, 0.02)

    def test_apply_main_volume_splits_and_lowers_selected_original_audio(self):
        timeline = [TimelineSegment("voice.wav", 0.0, 10.0, audio_volume=1.0)]

        updated, changed = apply_main_volume_to_timeline(timeline, 3.0, 7.0, {"main_volume_percent": 25})

        self.assertEqual(changed, 1)
        self.assertEqual(len(updated), 3)
        self.assertEqual((updated[0].start, updated[0].end, updated[0].audio_volume), (0.0, 3.0, 1.0))
        self.assertEqual((updated[1].start, updated[1].end, updated[1].audio_volume), (3.0, 7.0, 0.25))
        self.assertEqual((updated[2].start, updated[2].end, updated[2].audio_volume), (7.0, 10.0, 1.0))

    def test_setting_change_restarts_preview_from_current_offset_when_play_requested(self):
        class Slider:
            def GetValue(self):
                return 18

        class PreviewPlayer:
            is_playing = False
            play_requested = True
            reset_calls = 0

            def current_offset(self):
                return 4.5

            def reset(self, *_args, **_kwargs):
                self.reset_calls += 1

        dialog = AudioDuckingDialog.__new__(AudioDuckingDialog)
        preview_player = PreviewPlayer()
        dialog.preview_player = preview_player
        dialog.controls = {"reduction_db": {"slider": Slider(), "name": "Reduction", "unit": "decibels"}}
        dialog.update_all_slider_names = lambda: None
        dialog.cleanup_preview_file = lambda: None
        dialog.update_status_text = lambda _message: None
        started_offsets = []
        dialog.start_preview_audio = lambda offset=0: started_offsets.append(offset)

        with patch("wx.Window.FindFocus", return_value=None):
            dialog.on_setting_changed("reduction_db")

        self.assertEqual(started_offsets, [4.5])
        self.assertEqual(preview_player.reset_calls, 0)


if __name__ == "__main__":
    unittest.main()
