import unittest
from unittest.mock import patch
import os
import tempfile
import threading
import wave

from video_maker import audio_devices


class FakeSoundDevice:
    default = type("Default", (), {"device": [1, 2]})()

    @staticmethod
    def query_devices(index=None):
        devices = [
            {"name": "Silent output", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "USB Microphone", "max_output_channels": 0, "max_input_channels": 1},
            {"name": "Realtek Speakers", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "Webcam Mic", "max_output_channels": 0, "max_input_channels": 2},
        ]
        if index is None:
            return devices
        return devices[index]


class AudioDevicesTest(unittest.TestCase):
    def setUp(self):
        self.preferences = {}
        self.patches = [
            patch("video_maker.audio_devices.read_preferences", lambda: dict(self.preferences)),
            patch("video_maker.audio_devices.write_preferences", self._write_preferences),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def _write_preferences(self, data):
        self.preferences = dict(data)

    def test_lists_output_and_input_devices_with_system_default_first(self):
        outputs = audio_devices.available_devices(audio_devices.OUTPUT_KIND, FakeSoundDevice)
        inputs = audio_devices.available_devices(audio_devices.INPUT_KIND, FakeSoundDevice)

        self.assertEqual(outputs[0].id, audio_devices.DEFAULT_DEVICE_ID)
        self.assertEqual(inputs[0].id, audio_devices.DEFAULT_DEVICE_ID)
        self.assertEqual([device.name for device in outputs[1:]], ["Silent output", "Realtek Speakers"])
        self.assertEqual([device.name for device in inputs[1:]], ["USB Microphone", "Webcam Mic"])

    def test_saves_and_resolves_selected_devices(self):
        audio_devices.set_selected_device_id(audio_devices.OUTPUT_KIND, "2")
        audio_devices.set_selected_device_id(audio_devices.INPUT_KIND, "3")

        self.assertEqual(audio_devices.get_selected_device_id(audio_devices.OUTPUT_KIND), "2")
        self.assertEqual(audio_devices.get_selected_device_id(audio_devices.INPUT_KIND), "3")
        self.assertEqual(audio_devices.selected_sounddevice_output_device(FakeSoundDevice), 2)
        self.assertEqual(audio_devices.selected_sounddevice_input_device(FakeSoundDevice), 3)

    def test_invalid_or_missing_selection_falls_back_to_system_default(self):
        audio_devices.set_selected_device_id(audio_devices.OUTPUT_KIND, "bad")
        self.assertEqual(audio_devices.get_selected_device_id(audio_devices.OUTPUT_KIND), audio_devices.DEFAULT_DEVICE_ID)
        self.assertIsNone(audio_devices.selected_sounddevice_output_device(FakeSoundDevice))

        audio_devices.set_selected_device_id(audio_devices.INPUT_KIND, "99")
        self.assertIsNone(audio_devices.selected_sounddevice_input_device(FakeSoundDevice))

    def test_audio_device_text_is_translated_to_english_and_french(self):
        expected = {
            "en": {
                "إعدادات الصوت": "Audio settings",
                "أجهزة الصوت": "Audio devices",
                "السماعة الافتراضية": "Default speaker",
                "الميكروفون الافتراضي": "Default microphone",
                "الميكروفون لهذه الجلسة": "Microphone for this session",
                "سماعة النظام الافتراضية": "System default speaker",
                "ميكروفون النظام الافتراضي": "System default microphone",
            },
            "fr": {
                "إعدادات الصوت": "Paramètres audio",
                "أجهزة الصوت": "Périphériques audio",
                "السماعة الافتراضية": "Haut-parleur par défaut",
                "الميكروفون الافتراضي": "Microphone par défaut",
                "الميكروفون لهذه الجلسة": "Microphone pour cette session",
                "سماعة النظام الافتراضية": "Haut-parleur système par défaut",
                "ميكروفون النظام الافتراضي": "Microphone système par défaut",
            },
        }
        for language, translations in expected.items():
            with patch("video_maker.localization.get_language", return_value=language):
                for source, translated in translations.items():
                    self.assertEqual(audio_devices.tr(source), translated)

    def test_recording_external_source_uses_session_microphone(self):
        from video_maker import recording

        reader = recording.AudioSourceReader.__new__(recording.AudioSourceReader)
        reader.source = "external"
        reader.output_channels = 2
        reader.input_device_id = "3"

        with patch.object(recording, "sd", FakeSoundDevice):
            device, channels, extra = reader.device_settings()

        self.assertEqual(device, 3)
        self.assertEqual(channels, 2)
        self.assertIsNone(extra)

    def test_recording_external_source_default_uses_system_microphone(self):
        from video_maker import recording

        reader = recording.AudioSourceReader.__new__(recording.AudioSourceReader)
        reader.source = "external"
        reader.output_channels = 2
        reader.input_device_id = audio_devices.DEFAULT_DEVICE_ID

        with patch.object(recording, "sd", FakeSoundDevice):
            device, channels, extra = reader.device_settings()

        self.assertEqual(device, 1)
        self.assertEqual(channels, 1)
        self.assertIsNone(extra)

    def test_audio_mixer_passes_session_microphone_to_external_reader(self):
        from video_maker import recording

        created = []

        class Reader:
            def __init__(self, source, sample_rate, channels, input_device_id=audio_devices.DEFAULT_DEVICE_ID):
                created.append((source, sample_rate, channels, input_device_id))

            def start(self):
                pass

        options = recording.RecordingOptions(mode="audio", source="external", input_device_id="3")
        mixer = recording.AudioMixer(options)
        with patch("video_maker.recording.AudioSourceReader", Reader):
            mixer.start()

        self.assertEqual(created, [("external", options.sample_rate, options.channels, "3")])

    def test_selected_app_pids_are_normalized(self):
        from video_maker import recording

        options = recording.RecordingOptions(mode="audio", source="internal", selected_apps=["42", "bad", 42, 0])

        self.assertEqual(recording.selected_app_pids(options), [42])

    def test_audio_mixer_refuses_selected_apps_loopback_fallback(self):
        from video_maker import recording

        options = recording.RecordingOptions(mode="audio", source="internal", selected_apps=[42])
        mixer = recording.AudioMixer(options)

        with self.assertRaises(recording.RecordingError):
            mixer.start()

    def test_audio_recording_uses_process_capture_for_selected_apps(self):
        from video_maker import recording

        captures = []

        def write_wav(path):
            with wave.open(path, "wb") as file:
                file.setnchannels(2)
                file.setsampwidth(2)
                file.setframerate(48000)
                file.writeframes(b"\0" * 4096)

        class Capture:
            def __init__(self, pid, output_path):
                self.pid = pid
                self.output_path = output_path
                captures.append(self)

            def start(self):
                pass

            def stop(self):
                write_wav(self.output_path)

        def fake_run_ffmpeg(command, error_text):
            write_wav(command[-1])

        with tempfile.TemporaryDirectory() as temporary:
            options = recording.RecordingOptions(mode="audio", source="internal", selected_apps=[42])
            session = recording.AudioRecordingSession.__new__(recording.AudioRecordingSession)
            session.options = options
            session.folder = temporary
            session.segments = []
            session.running = False
            session.stop_event = threading.Event()
            session.error = ""

            with patch("video_maker.app_audio_capture.PROCESS_AUDIO_SUPPORTED", True), patch(
                "video_maker.app_audio_capture.ProcessAudioCapture", Capture
            ), patch("video_maker.recording.run_ffmpeg", fake_run_ffmpeg):
                session.run_selected_app_audio()

            self.assertEqual([capture.pid for capture in captures], [42])
            self.assertEqual(len(session.segments), 1)
            self.assertTrue(os.path.exists(session.segments[0]))

    def test_reliable_audio_available_checks_selected_speaker(self):
        from video_maker import reliable_playback

        class FakeDefaultMissing(FakeSoundDevice):
            default = type("Default", (), {"device": [1, -1]})()

        with patch.object(reliable_playback, "sd", FakeDefaultMissing), patch.object(
            reliable_playback, "np", object()
        ), patch("video_maker.reliable_playback.selected_sounddevice_output_device", return_value=2):
            self.assertTrue(reliable_playback.reliable_audio_available())


if __name__ == "__main__":
    unittest.main()
