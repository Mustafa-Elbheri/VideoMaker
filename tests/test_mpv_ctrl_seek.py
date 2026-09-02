import os
import sys
import unittest

sys.path.insert(0, os.path.abspath("."))

from video_maker.mpv_player import MEDIASTATE_PAUSED, MEDIASTATE_PLAYING
from video_maker.mpv_player.mpv_ctrl import MPVMediaCtrl


class FakeMpvPlayer:
    def __init__(self):
        self.async_commands = []
        self.sync_commands = []
        self.time_pos = None
        self.pause = False
        self.callback = None

    def command_async(self, *args, callback=None, **kwargs):
        self.async_commands.append(args)
        self.callback = callback

    def command(self, *args, **kwargs):
        self.sync_commands.append(args)

    def loadfile(self, url):
        pass

    def quit(self):
        pass

    def terminate(self):
        pass


def make_ctrl(length=10000):
    ctrl = MPVMediaCtrl.__new__(MPVMediaCtrl)
    ctrl._shutting_down = False
    ctrl._player = FakeMpvPlayer()
    ctrl._state = MEDIASTATE_PAUSED
    ctrl._length = length
    ctrl._seek_target = None
    ctrl._seek_in_flight = False
    ctrl._seek_applied_target = None
    ctrl._seek_mode = "exact"
    ctrl._seek_watchdog = None
    return ctrl


class SeekCoalescingTest(unittest.TestCase):
    def test_rapid_seeks_apply_latest_win_only(self):
        ctrl = make_ctrl()
        ctrl.Seek(1000)
        ctrl.Seek(5000)
        ctrl.Seek(9000)

        # first request issues one command immediately
        self.assertEqual(len(ctrl._player.async_commands), 1)
        self.assertAlmostEqual(ctrl._player.async_commands[0][1], 1.0, delta=0.001)
        self.assertEqual(ctrl._seek_target, 9000)
        self.assertTrue(ctrl._seek_in_flight)

        # intermediate target 5000 must never be sent
        ctrl._finish_seek_after_command()
        self.assertEqual(len(ctrl._player.async_commands), 2)
        self.assertAlmostEqual(ctrl._player.async_commands[1][1], 9.0, delta=0.001)

        # final target matches applied target so nothing stays pending
        ctrl._finish_seek_after_command()
        self.assertEqual(len(ctrl._player.async_commands), 2)
        self.assertIsNone(ctrl._seek_target)
        self.assertFalse(ctrl._seek_in_flight)

    def test_tell_reports_pending_target_while_paused(self):
        ctrl = make_ctrl()
        ctrl._player.time_pos = 0.25
        ctrl.Seek(4321)
        self.assertEqual(ctrl.Tell(), 4321)

        # once the seek completes and no newer request arrives, Tell returns hardware
        ctrl._finish_seek_after_command()
        self.assertEqual(ctrl.Tell(), 250.0)

    def test_tell_uses_hardware_while_playing(self):
        ctrl = make_ctrl()
        ctrl._player.time_pos = 2.5
        ctrl.Seek(4321)
        ctrl._state = MEDIASTATE_PLAYING
        self.assertEqual(ctrl.Tell(), 2500.0)

    def test_seek_clamps_to_length(self):
        ctrl = make_ctrl(length=1000)
        result = ctrl.Seek(5000)
        self.assertEqual(result, 999)
        self.assertEqual(ctrl._seek_target, 999)

    def test_watchdog_recovers_stuck_seek(self):
        ctrl = make_ctrl()
        ctrl._seek_in_flight = True
        ctrl._seek_target = 2000
        ctrl._on_seek_watchdog()
        self.assertEqual(len(ctrl._player.sync_commands), 1)
        self.assertAlmostEqual(ctrl._player.sync_commands[0][1], 2.0, delta=0.001)
        self.assertIsNone(ctrl._seek_target)
        self.assertFalse(ctrl._seek_in_flight)

    def test_load_resets_seek_state(self):
        ctrl = make_ctrl()
        ctrl.Seek(3000)
        self.assertTrue(ctrl._seek_in_flight or ctrl._seek_target is not None)
        ctrl.Load("C:/nonexistent.mp4")
        self.assertIsNone(ctrl._seek_target)
        self.assertFalse(ctrl._seek_in_flight)

    def test_absolute_mode_uses_keyframe_flag(self):
        ctrl = make_ctrl()
        ctrl.Seek(3000, mode="absolute")
        self.assertEqual(ctrl._player.async_commands[0][2], "absolute")


if __name__ == "__main__":
    unittest.main()
