import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.scrub_audio import ScrubPlayer


class ScrubPlayerThreadingTest(unittest.TestCase):
    """التحقق من سلامة خيط مشغّل الشرائح عند الضغطات السريعة المتتالية.

    كان الخلل السابق: عند استدعاء stop() قبل انتهاء فك الترميز (الذي قد
    يستغرق حتى DECODE_TIMEOUT)، كان join مهلة قصيرة يعيد self.thread إلى
    None ويغلق الدفق بينما الخيط القديم لا يزال حيًا، ثم play_request
    يفتح خيطًا ثانيًا فيشتغل خيطان على نفس الدفق الصوتي فينهار البرنامج
    بخطأ وصول أصلي (Access Violation).
    """

    def _new_player(self):
        return ScrubPlayer()

    def test_play_stop_cycle_never_spawns_second_thread(self):
        player = self._new_player()
        request = {
            "path": os.path.abspath("tests/test.wav"),
            "center_file_ms": 500.0,
            "window_file_ms": 40.0,
            "volume": 0.8,
            "rate": 1.0,
        }
        for _ in range(200):
            player.play_request(request)
            player.stop()
        self.assertIsNotNone(player.thread)
        self.assertEqual(player.thread, player.thread)
        player.shutdown()
        self.assertFalse(player.thread.is_alive())

    def test_rapid_interleaved_play_stop_keeps_single_thread(self):
        player = self._new_player()
        request = {
            "path": os.path.abspath("tests/test.wav"),
            "center_file_ms": 500.0,
            "window_file_ms": 40.0,
            "volume": 0.8,
            "rate": 1.0,
        }
        thread_ids = set()

        def hammer():
            for _ in range(100):
                player.play_request(request)
                player.stop()

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        thread_ids.add(player.thread)
        self.assertEqual(len(thread_ids), 1, "must keep exactly one worker thread")
        player.shutdown()
        self.assertFalse(player.thread.is_alive())

    def test_worker_survives_stop_and_accepts_new_request(self):
        player = self._new_player()
        request = {
            "path": os.path.abspath("tests/test.wav"),
            "center_file_ms": 500.0,
            "window_file_ms": 40.0,
            "volume": 0.8,
            "rate": 1.0,
        }
        for _ in range(50):
            player.stop()
            player.play_request(request)
        self.assertIsNotNone(player.thread)
        self.assertTrue(player.thread.is_alive())
        player.shutdown()
        self.assertFalse(player.thread.is_alive())

    def test_shutdown_joins_and_closes_stream(self):
        player = self._new_player()
        request = {
            "path": os.path.abspath("tests/test.wav"),
            "center_file_ms": 500.0,
            "window_file_ms": 40.0,
            "volume": 0.8,
            "rate": 1.0,
        }
        player.play_request(request)
        player.shutdown()
        self.assertIsNotNone(player.thread)
        self.assertFalse(player.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
