import threading
import unittest

from video_maker.audio_effects import RealtimeAudioPreview


class AudioEffectPreviewTest(unittest.TestCase):
    def test_stopped_output_stream_is_handled_as_preview_stop(self):
        messages = []
        preview = RealtimeAudioPreview()
        preview.say_status = lambda message, enabled=True: messages.append(message) if enabled else None
        stop_requested = threading.Event()

        class StoppedStream:
            def write(self, _data):
                raise RuntimeError("Stream is stopped [PaErrorCode -9983]")

        self.assertFalse(preview.write_stream_chunk(StoppedStream(), b"data", stop_requested))
        self.assertTrue(stop_requested.is_set())
        self.assertTrue(messages)

    def test_stopped_output_stream_during_requested_stop_is_quiet(self):
        messages = []
        preview = RealtimeAudioPreview()
        preview.say_status = lambda message, enabled=True: messages.append(message) if enabled else None
        stop_requested = threading.Event()
        stop_requested.set()

        class StoppedStream:
            def write(self, _data):
                raise RuntimeError("Stream is stopped [PaErrorCode -9983]")

        self.assertFalse(preview.write_stream_chunk(StoppedStream(), b"data", stop_requested))
        self.assertEqual(messages, [])

    def test_stop_does_not_abort_output_stream_from_caller_thread(self):
        preview = RealtimeAudioPreview()

        class Stream:
            def __init__(self):
                self.abort_count = 0

            def abort(self, *args, **kwargs):
                self.abort_count += 1

        class Process:
            def __init__(self):
                self.terminate_count = 0

            def poll(self):
                return None

            def terminate(self):
                self.terminate_count += 1

        stream = Stream()
        process = Process()
        preview.stream = stream
        preview.process = process
        preview.is_playing = True
        preview.play_requested = True

        preview.stop()

        self.assertEqual(stream.abort_count, 0)
        self.assertEqual(process.terminate_count, 1)
        self.assertFalse(preview.is_playing)
        self.assertFalse(preview.play_requested)


if __name__ == "__main__":
    unittest.main()
