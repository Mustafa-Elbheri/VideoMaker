import unittest
from unittest.mock import call, patch

from video_maker import dialogs
from video_maker.logical_files import logical_file_entries
from video_maker.player import VideoPlayer
from video_maker.video_clip_merge import VideoClipMergeWindow


class DummyHistory:
    def __init__(self):
        self.clear_count = 0

    def clear(self):
        self.clear_count += 1


def make_player():
    player = VideoPlayer.__new__(VideoPlayer)
    player.window_number = 1
    player.edit_history = DummyHistory()
    player.stop_background_audio_playback = lambda: None
    player.clear_audio_visual_preview = lambda: None
    player.reset_main_audio_override_state = lambda: None
    player.invalidate_pending_media_load = lambda: None
    player.refresh_menu_bar = lambda: None
    player.load_timeline_time = lambda *_args, **_kwargs: None
    player.say = lambda *_args, **_kwargs: None
    player.timeline = []
    player.edit_points = []
    player.video_path = ""
    player.current_time = 0.0
    player.timeline_boundaries_cache_signature = None
    player.timeline_positions_cache = []
    player.timeline_boundaries_cache = []
    player.timeline_duration_cache = 0.0
    return player


class MultiFileOpenTest(unittest.TestCase):
    def test_remember_media_paths_records_every_selected_file(self):
        with patch("video_maker.dialogs.remember_media_path") as remember:
            dialogs.remember_media_paths(["first.mp4", "second.mp4"], "video", "open_media")

        self.assertEqual(
            remember.call_args_list,
            [
                call("first.mp4", "video", "open_media"),
                call("second.mp4", "video", "open_media"),
            ],
        )

    def test_natural_sort_places_numbered_files_in_numeric_order(self):
        paths = [r"C:\media\10.mp4", r"C:\media\1.mp4", r"C:\media\2.mp4"]

        ordered = sorted(paths, key=dialogs.natural_sort_key)

        self.assertEqual(
            [path.rsplit("\\", 1)[-1] for path in ordered],
            ["1.mp4", "2.mp4", "10.mp4"],
        )

    def test_multiple_videos_open_as_one_timeline_with_two_logical_files(self):
        player = make_player()
        durations = {"first.mp4": 1.25, "second.mp4": 2.5}

        with patch("video_maker.player.get_video_duration", side_effect=lambda path: durations[path]):
            player.StartVideoClipMerge(["first.mp4", "second.mp4"])

        self.assertEqual(player.media_kind, "video")
        self.assertEqual(player.video_path, "first.mp4")
        self.assertEqual(len(player.timeline), 2)
        self.assertTrue(player.is_dirty)
        self.assertEqual(player.edit_history.clear_count, 1)
        entries = logical_file_entries(player.timeline)
        self.assertEqual([entry.name for entry in entries], ["first.mp4", "second.mp4"])
        self.assertNotEqual(entries[0].file_id, entries[1].file_id)

        player.current_time = 1.25
        current, ranges = player.timeline_file_at_current_time()
        self.assertEqual(current.name, "second.mp4")
        self.assertEqual(len(ranges), 2)

    def test_multiple_audio_files_open_as_one_timeline_with_two_logical_files(self):
        player = make_player()
        durations = {"first.wav": 1.0, "second.wav": 1.5}

        with patch("video_maker.player.get_media_duration", side_effect=lambda path: durations[path]):
            player.StartAudioClipMerge(["first.wav", "second.wav"])

        self.assertEqual(player.media_kind, "audio")
        self.assertEqual(player.video_path, "first.wav")
        self.assertEqual(len(player.timeline), 2)
        entries = logical_file_entries(player.timeline)
        self.assertEqual([entry.name for entry in entries], ["first.wav", "second.wav"])

        player.current_time = 1.0
        current, ranges = player.timeline_file_at_current_time()
        self.assertEqual(current.name, "second.wav")
        self.assertEqual(len(ranges), 2)

    def test_delete_current_timeline_file_removes_only_selected_logical_file(self):
        player = make_player()
        durations = {"first.mp4": 1.0, "second.mp4": 1.0, "third.mp4": 1.0}
        with patch("video_maker.player.get_video_duration", side_effect=lambda path: durations[path]):
            player.StartVideoClipMerge(["first.mp4", "second.mp4", "third.mp4"])
        player.current_time = 1.25
        player.require_open_file = lambda: True
        player.capture_edit_state = lambda: {"timeline": list(player.timeline)}
        player.adjust_timed_items_after_delete = lambda *_args, **_kwargs: None
        player.reload_current_position = lambda: None
        player.record_edit = lambda *_args, **_kwargs: None
        player.selected_playback_range = None
        player.skipped_playback_range = None

        player.OnDeleteCurrentTimelineFile()

        entries = logical_file_entries(player.timeline)
        self.assertEqual([entry.name for entry in entries], ["first.mp4", "third.mp4"])
        self.assertEqual(player.current_time, 1.0)
        self.assertTrue(player.is_dirty)

    def test_move_current_timeline_file_up_changes_real_timeline_order(self):
        player = make_player()
        durations = {"2.mp4": 1.0, "1.mp4": 1.0}
        with patch("video_maker.player.get_video_duration", side_effect=lambda path: durations[path]):
            player.StartVideoClipMerge(["2.mp4", "1.mp4"])
        player.current_time = 1.0
        player.require_open_file = lambda: True
        player.capture_edit_state = lambda: {"timeline": list(player.timeline)}
        player.record_edit = lambda *_args, **_kwargs: None
        player.reload_current_position = lambda: None

        player.OnMoveCurrentTimelineFileUp()

        entries = logical_file_entries(player.timeline)
        self.assertEqual([entry.name for entry in entries], ["1.mp4", "2.mp4"])
        self.assertEqual(player.current_time, 0.0)
        self.assertEqual([key for key, _label in player.timeline_file_reorder_actions()], ["move_down"])

    def test_on_open_media_paths_routes_same_kind_batches(self):
        player = make_player()
        routed = []
        player.StartVideoClipMerge = lambda paths: routed.append(("video", list(paths)))
        player.StartAudioClipMerge = lambda paths: routed.append(("audio", list(paths)))

        with patch("video_maker.player.has_video_stream", side_effect=lambda path: path.endswith(".mp4")):
            player.OnOpenMediaPaths(["one.mp4", "two.mp4"])
            player.OnOpenMediaPaths(["one.wav", "two.wav"])

        self.assertEqual(
            routed,
            [
                ("video", ["one.mp4", "two.mp4"]),
                ("audio", ["one.wav", "two.wav"]),
            ],
        )

    def test_video_clip_merge_adds_multiple_selected_videos(self):
        window = VideoClipMergeWindow.__new__(VideoClipMergeWindow)
        window.video_paths = ["existing.mp4"]
        selections = []
        window.refresh_list = lambda selection=None: selections.append(selection)

        added = window.add_video_paths(["clip10.mp4", "clip2.mp4"])

        self.assertEqual(added, 2)
        self.assertEqual(window.video_paths, ["existing.mp4", "clip2.mp4", "clip10.mp4"])
        self.assertEqual(selections, [1])


if __name__ == "__main__":
    unittest.main()
