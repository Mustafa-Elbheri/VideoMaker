import numpy as np
import unittest
import wx

from video_maker.audio_effects import (
    AudioEffectDialog,
    build_pedalboard,
    direct_realtime_audio_filter_supported,
    get_audio_effect_definitions,
    process_pedalboard_block,
)
from video_maker.localization import TEXTS, tr
from video_maker.stereo_balance_effect import (
    apply_stereo_balance_dsp,
    ffmpeg_stereo_balance_filter,
    is_stereo_balance_effect,
    stereo_balance_effect,
)

app = wx.App(False)


class StereoBalanceEffectTest(unittest.TestCase):
    """مجموعة اختبارات مخصصة ومستقلة لمؤثر موازنة الصوت (Stereo Balance Effect)."""

    def test_01_effect_definition_present(self):
        definitions = get_audio_effect_definitions()
        keys = [effect["key"] for effect in definitions]
        self.assertIn("stereo_balance", keys, "stereo_balance effect key missing from definitions")
        
        definition = next(effect for effect in definitions if effect["key"] == "stereo_balance")
        self.assertEqual(definition["name"], "موازنة الصوت والتوازن الصوتي")
        self.assertTrue(callable(definition["builder"]))
        self.assertGreaterEqual(len(definition["controls"]), 3)

    def test_02_ffmpeg_filter_string_generation(self):
        mono_filter = ffmpeg_stereo_balance_filter({"mode": "center_mono", "volume": 100})
        self.assertIn("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1", mono_filter)

        left_filter = ffmpeg_stereo_balance_filter({"mode": "left_to_both", "volume": 100})
        self.assertIn("pan=stereo|c0=c0|c1=c0", left_filter)

        right_filter = ffmpeg_stereo_balance_filter({"mode": "right_to_both", "volume": 100})
        self.assertIn("pan=stereo|c0=c1|c1=c1", right_filter)

        balance_right = ffmpeg_stereo_balance_filter({"mode": "stereo_balance", "balance": 50, "volume": 100})
        self.assertIn("pan=stereo|c0=0.500*c0|c1=1.000*c1", balance_right)

        balance_left = ffmpeg_stereo_balance_filter({"mode": "stereo_balance", "balance": -50, "volume": 100})
        self.assertIn("pan=stereo|c0=1.000*c0|c1=0.500*c1", balance_left)

    def test_03_dsp_left_channel_only_to_both_ears(self):
        """اختبار إصلاح الصوت القادم من السماعة اليسرى فقط بنقله إلى السماعتين معاً."""
        # صدمة صوتية في السماعة اليسرى (c0)، وسكون في اليمنى (c1)
        t = np.linspace(0, 1.0, 1000)
        left_audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        right_audio = np.zeros(1000, dtype=np.float32)
        input_pcm = np.vstack([left_audio, right_audio])

        # تطبيق نمط left_to_both
        output_pcm = apply_stereo_balance_dsp(input_pcm, {"mode": "left_to_both", "volume": 100})
        
        # السطوع في السماعة اليسرى واليمين متماثل وموجود في السماعتين معاً
        self.assertGreater(np.max(np.abs(output_pcm[0])), 0.9)
        self.assertGreater(np.max(np.abs(output_pcm[1])), 0.9)
        np.testing.assert_allclose(output_pcm[0], output_pcm[1])

    def test_04_dsp_right_channel_only_to_both_ears(self):
        """اختبار إصلاح الصوت القادم من السماعة اليمنى فقط بنقله إلى السماعتين معاً."""
        t = np.linspace(0, 1.0, 1000)
        left_audio = np.zeros(1000, dtype=np.float32)
        right_audio = np.sin(2 * np.pi * 880 * t).astype(np.float32)
        input_pcm = np.vstack([left_audio, right_audio])

        output_pcm = apply_stereo_balance_dsp(input_pcm, {"mode": "right_to_both", "volume": 100})
        
        self.assertGreater(np.max(np.abs(output_pcm[0])), 0.9)
        self.assertGreater(np.max(np.abs(output_pcm[1])), 0.9)
        np.testing.assert_allclose(output_pcm[0], output_pcm[1])

    def test_05_dsp_balance_slider_panning(self):
        """اختبار انحياز الصوت إلى اليسار أو اليمين عند سحب السلايدر."""
        t = np.linspace(0, 1.0, 1000)
        tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        input_pcm = np.vstack([tone, tone])

        # سحب السلايدر 100% إلى اليسار (-100)
        left_only = apply_stereo_balance_dsp(input_pcm, {"mode": "stereo_balance", "balance": -100, "volume": 100})
        self.assertGreater(np.max(np.abs(left_only[0])), 0.9)
        self.assertEqual(np.max(np.abs(left_only[1])), 0.0, "Right channel must be muted at -100 balance")

        # سحب السلايدر 100% إلى اليمين (+100)
        right_only = apply_stereo_balance_dsp(input_pcm, {"mode": "stereo_balance", "balance": 100, "volume": 100})
        self.assertEqual(np.max(np.abs(right_only[0])), 0.0, "Left channel must be muted at +100 balance")
        self.assertGreater(np.max(np.abs(right_only[1])), 0.9)

    def test_06_process_pedalboard_block_integration(self):
        """اختبار تطبيق معالجة موازنة الصوت داخل مجرى المعاينة الفورية process_pedalboard_block."""
        t = np.linspace(0, 1.0, 1000)
        left = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        right = np.zeros(1000, dtype=np.float32)
        input_pcm = np.vstack([left, right])

        effect_obj = stereo_balance_effect({"mode": "left_to_both", "volume": 100})
        board = build_pedalboard(effect_obj)
        
        processed = process_pedalboard_block(input_pcm, effect_obj, board, {}, 44100)
        self.assertGreater(np.max(np.abs(processed[0])), 0.9)
        self.assertGreater(np.max(np.abs(processed[1])), 0.9)

    def test_07_accessibility_and_dialog_instantiation(self):
        definitions = get_audio_effect_definitions()
        definition = next(effect for effect in definitions if effect["key"] == "stereo_balance")

        class MockParent(wx.Frame):
            def __init__(self):
                super().__init__(None)
                self.timeline = []
                self.start_time = None
                self.end_time = None
            def selected_effect_range(self): return (0.0, 10.0)
            def has_video(self): return True
            def timeline_duration(self): return 10.0
            def say(self, *args, **kwargs): pass

        parent = MockParent()
        try:
            dialog = AudioEffectDialog(parent, definition)
            self.assertIsNotNone(dialog)
            self.assertIn("mode", dialog.controls)
            self.assertIn("balance", dialog.controls)
            self.assertIn("volume", dialog.controls)
            self.assertTrue(dialog.controls["mode"]["choice"].GetName())
            self.assertTrue(dialog.controls["balance"]["slider"].GetName())
            self.assertTrue(dialog.controls["volume"]["slider"].GetName())
            dialog.Destroy()
        finally:
            parent.Destroy()

    def test_08_multilingual_translations(self):
        keys = [
            "موازنة الصوت والتوازن الصوتي",
            "نمط الموازنة",
            "توازن استريو بين اليمين واليسار",
            "تمركز الصوت على السماعتين (مونو)",
            "القناة اليسرى على السماعتين معاً",
            "القناة اليمنى على السماعتين معاً",
            "موازنة اليمين واليسار",
        ]
        for key in keys:
            self.assertIsNotNone(TEXTS.get("en", {}).get(key), f"EN translation missing for {key}")
            self.assertIsNotNone(TEXTS.get("fr", {}).get(key), f"FR translation missing for {key}")


if __name__ == "__main__":
    unittest.main()
