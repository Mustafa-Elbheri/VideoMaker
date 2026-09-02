import unittest
from pathlib import Path

from video_maker.mpv_player import MEDIASTATE_PLAYING, MEDIASTATE_STOPPED
from video_maker.mpv_player.mpv_ctrl import MPVMediaCtrl


class FakeMpvPlayer:
    def __init__(self):
        self.pause = False
        self.unobserved = []
        self.terminated = False
        self.quit_count = 0
        self.commands = []
        self.wait_timeouts = []

    def unobserve_property(self, name, observer):
        self.unobserved.append((name, observer))

    def terminate(self):
        self.terminated = True

    def quit(self):
        self.quit_count += 1

    def command(self, *args):
        self.commands.append(args)

    def wait_for_shutdown(self, timeout=None):
        self.wait_timeouts.append(timeout)


class MpvCtrlShutdownTest(unittest.TestCase):
    def test_mpv_disables_hardware_decoding_by_default(self):
        source = Path("video_maker/mpv_player/mpv_ctrl.py").read_text(encoding="utf-8")

        self.assertIn("hwdec='no'", source)
        self.assertNotIn("hwdec='auto'", source)

    def test_eof_handler_pauses_from_ui_side_and_posts_finished_event(self):
        ctrl = MPVMediaCtrl.__new__(MPVMediaCtrl)
        ctrl._shutting_down = False
        ctrl._state = MEDIASTATE_PLAYING
        ctrl._player = FakeMpvPlayer()
        posted = []
        ctrl._post_finished_event = lambda: posted.append(True)

        ctrl._handle_eof_reached()

        self.assertTrue(ctrl._player.pause)
        self.assertEqual(ctrl._state, MEDIASTATE_STOPPED)
        self.assertEqual(posted, [True])

    def test_destroy_mpv_player_requests_shutdown_and_terminates_player(self):
        ctrl = MPVMediaCtrl.__new__(MPVMediaCtrl)
        duration_observer = object()
        eof_observer = object()
        player = FakeMpvPlayer()
        ctrl._shutting_down = False
        ctrl._player = player
        ctrl._duration_observer = duration_observer
        ctrl._eof_observer = eof_observer

        ctrl._destroy_mpv_player()

        self.assertTrue(ctrl._shutting_down)
        self.assertIsNone(ctrl._player)
        self.assertEqual(player.commands, [("stop",)])
        self.assertEqual(player.quit_count, 1)
        self.assertEqual(player.wait_timeouts, [1.0])
        self.assertTrue(player.terminated)


if __name__ == "__main__":
    unittest.main()
