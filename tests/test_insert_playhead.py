import os
import tempfile
import unittest
from unittest import mock

from video_maker.track_items import can_insert_media_type, insert_at_playhead, item_bounds, muted_items
from video_maker.tracks import BACKGROUND_AUDIO_TRACK, MAIN_VIDEO_TRACK, SECONDARY_VIDEO_TRACK, SOUND_EFFECTS_TRACK


def make_item(start, end, item_id=None):
    return {
        "id": item_id or "item-{start}-{end}".format(start=start, end=end),
        "type": "audio",
        "path": "x.wav",
        "start": float(start),
        "end": float(end),
    }


class InsertAtPlayheadTest(unittest.TestCase):
    def test_off_inserts_at_playhead_without_shift(self):
        items = [make_item(0, 10, "a")]
        new = make_item(0, 2, "n")
        insert_at_playhead(items, new, 5.0, "off")
        self.assertEqual(len(items), 3)
        self.assertEqual(item_bounds(items[0]), (0.0, 5.0))
        self.assertEqual(item_bounds(items[1]), (5.0, 7.0))
        self.assertEqual(item_bounds(items[2]), (5.0, 10.0))

    def test_per_track_shifts_items_after_playhead(self):
        items = [make_item(0, 10, "a")]
        new = make_item(0, 2, "n")
        insert_at_playhead(items, new, 5.0, "per_track")
        self.assertEqual(len(items), 3)
        self.assertEqual(item_bounds(items[0]), (0.0, 5.0))
        self.assertEqual(item_bounds(items[1]), (5.0, 7.0))
        self.assertEqual(item_bounds(items[2]), (7.0, 12.0))

    def test_all_tracks_shifts_every_panel(self):
        items = [make_item(0, 10, "a")]
        other = [make_item(3, 6, "b")]
        new = make_item(0, 2, "n")
        insert_at_playhead(items, new, 5.0, "all_tracks", panels={"t1": items, "t2": other})
        self.assertEqual(item_bounds(other[0]), (5.0, 8.0))
        self.assertEqual(item_bounds(items[2]), (7.0, 12.0))

    def test_insert_into_empty_list(self):
        items = []
        new = make_item(0, 3, "n")
        insert_at_playhead(items, new, 2.0, "per_track")
        self.assertEqual(len(items), 1)
        self.assertEqual(item_bounds(items[0]), (2.0, 5.0))

    def test_insert_before_first_item(self):
        items = [make_item(5, 10, "a")]
        new = make_item(0, 2, "n")
        insert_at_playhead(items, new, 1.0, "off")
        self.assertEqual(len(items), 2)
        self.assertEqual(item_bounds(items[0]), (1.0, 3.0))
        self.assertEqual(item_bounds(items[1]), (5.0, 10.0))


class CanInsertMediaTypeTest(unittest.TestCase):
    def test_image_rejected_on_video_only_track(self):
        self.assertFalse(can_insert_media_type(["video"], "image"))
        self.assertTrue(can_insert_media_type(["video", "image"], "image"))
        self.assertTrue(can_insert_media_type(["video"], "video"))

    def test_audio_subtypes_accepted_on_audio_tracks(self):
        self.assertTrue(can_insert_media_type(["audio"], "sound_effect"))
        self.assertTrue(can_insert_media_type(["audio"], "background_audio"))
        self.assertFalse(can_insert_media_type(["video"], "background_audio"))

    def test_text_only_on_text_track(self):
        self.assertTrue(can_insert_media_type(["text"], "text"))
        self.assertFalse(can_insert_media_type(["video"], "text"))


class MutedItemsTest(unittest.TestCase):
    def test_muted_track_yields_empty(self):
        items = [make_item(0, 5)]
        self.assertEqual(muted_items(items, {1, 2}, 2), [])
        self.assertEqual(len(muted_items(items, {2, 3}, 1)), 1)

    def test_unmuted_track_keeps_items(self):
        items = [make_item(0, 5), make_item(6, 9)]
        self.assertEqual(len(muted_items(items, set(), 1)), 2)


class BackgroundAudioInsertionTest(unittest.TestCase):
    def _make_frame(self, track):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = track
        frame.sound_effects_items = []
        frame.background_audio_items = []
        frame.b_roll_items = []
        frame.visual_items = []
        frame.timeline = []
        frame.ripple_mode = "per_track"
        frame.transition_name = "none"
        frame.default_image_duration = 5.0
        frame.last_insert_end = 0.0
        frame.current_time = 0.0
        frame.focused_element = None
        frame.is_dirty = False
        frame.start_time = None
        frame.end_time = None
        frame.say = lambda *args, **kwargs: None
        frame.record_edit = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.capture_edit_state = mock.Mock(return_value={})
        return frame

    def test_background_insertion_keeps_playhead_position(self):
        frame = self._make_frame(BACKGROUND_AUDIO_TRACK)
        with mock.patch.object(frame, "pick_file_for_track", return_value=["a.wav"]), mock.patch.object(
            frame, "_import_media_into_program", side_effect=lambda path: path
        ), mock.patch("video_maker.player.natural_span", return_value=3.0):
            frame._insert_audio_at_playhead(5.0)
        self.assertEqual(frame.current_time, 5.0)
        self.assertEqual(len(frame.background_audio_items), 1)
        self.assertEqual(item_bounds(frame.background_audio_items[0]), (5.0, 8.0))

    def test_sound_effects_inserts_multiple_files(self):
        frame = self._make_frame(SOUND_EFFECTS_TRACK)
        with mock.patch.object(frame, "pick_file_for_track", return_value=["a.wav", "b.wav"]), mock.patch.object(
            frame, "_import_media_into_program", side_effect=lambda path: path
        ), mock.patch("video_maker.player.natural_span", return_value=2.0):
            frame._insert_audio_at_playhead(4.0)
        self.assertEqual(len(frame.sound_effects_items), 2)
        self.assertEqual(item_bounds(frame.sound_effects_items[0]), (4.0, 6.0))
        self.assertEqual(item_bounds(frame.sound_effects_items[1]), (6.0, 8.0))

    def test_background_insertion_imports_file_into_program(self):
        frame = self._make_frame(BACKGROUND_AUDIO_TRACK)
        with mock.patch.object(
            frame, "pick_file_for_track", return_value=["D:/outside/original.wav"]
        ), mock.patch.object(frame, "_import_media_into_program", return_value="C:/imports/abc123.wav"), mock.patch(
            "video_maker.player.natural_span", return_value=3.0
        ):
            frame._insert_audio_at_playhead(5.0)
        item = frame.background_audio_items[0]
        self.assertEqual(item["path"], "C:/imports/abc123.wav")
        self.assertEqual(item["original_path"], os.path.abspath("D:/outside/original.wav"))
        self.assertEqual(item["name"], "original.wav")

    def test_insertion_aborts_when_import_fails(self):
        frame = self._make_frame(BACKGROUND_AUDIO_TRACK)
        with mock.patch.object(frame, "pick_file_for_track", return_value=["a.wav"]), mock.patch.object(
            frame, "_import_media_into_program", return_value=None
        ):
            frame._insert_audio_at_playhead(5.0)
        self.assertEqual(len(frame.background_audio_items), 0)


class ImportMediaTest(unittest.TestCase):
    def _make_frame(self):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.say = lambda *args, **kwargs: None
        return frame

    def test_external_file_is_copied_into_program_root(self):
        frame = self._make_frame()
        temp_dir = tempfile.mkdtemp(prefix="avm_import_")
        program_root = os.path.join(temp_dir, "approot")
        imports_root = os.path.join(program_root, "imports")
        os.makedirs(program_root, exist_ok=True)
        os.makedirs(imports_root, exist_ok=True)
        source = os.path.join(temp_dir, "outside", "clip.mp4")
        os.makedirs(os.path.dirname(source), exist_ok=True)
        with open(source, "wb") as file:
            file.write(b"media-content")
        with mock.patch("video_maker.player.app_data_root", return_value=program_root), mock.patch(
            "video_maker.player.imported_media_root", return_value=imports_root
        ):
            result = frame._import_media_into_program(source)
        self.assertIsNotNone(result)
        self.assertTrue(os.path.dirname(result) == imports_root)
        self.assertTrue(result.endswith(".mp4"))
        self.assertNotEqual(os.path.abspath(result), os.path.abspath(source))
        with open(result, "rb") as file:
            self.assertEqual(file.read(), b"media-content")

    def test_owned_file_inside_program_is_not_copied(self):
        frame = self._make_frame()
        temp_dir = tempfile.mkdtemp(prefix="avm_owned_")
        program_root = os.path.join(temp_dir, "approot")
        os.makedirs(program_root, exist_ok=True)
        source = os.path.join(program_root, "sessions", "s1", "assets", "clip.wav")
        os.makedirs(os.path.dirname(source), exist_ok=True)
        with open(source, "wb") as file:
            file.write(b"owned")
        imports_root = os.path.join(program_root, "imports")
        os.makedirs(imports_root, exist_ok=True)
        with mock.patch("video_maker.player.app_data_root", return_value=program_root), mock.patch(
            "video_maker.player.imported_media_root", return_value=imports_root
        ):
            result = frame._import_media_into_program(source)
        self.assertEqual(os.path.abspath(result), os.path.abspath(source))
        self.assertEqual(len(os.listdir(imports_root)), 0)

    def test_missing_source_returns_none(self):
        frame = self._make_frame()
        self.assertIsNone(frame._import_media_into_program("Z:/does/not/exist.mp4"))

    def test_copy_failure_returns_none(self):
        frame = self._make_frame()
        temp_dir = tempfile.mkdtemp(prefix="avm_fail_")
        program_root = os.path.join(temp_dir, "approot")
        os.makedirs(program_root, exist_ok=True)
        source = os.path.join(temp_dir, "outside", "clip.mp4")
        os.makedirs(os.path.dirname(source), exist_ok=True)
        with open(source, "wb") as file:
            file.write(b"x")
        with mock.patch("video_maker.player.app_data_root", return_value=program_root), mock.patch(
            "video_maker.player.shutil.copy2", side_effect=OSError("disk full")
        ):
            result = frame._import_media_into_program(source)
        self.assertIsNone(result)


class SecondaryInsertionNamingTest(unittest.TestCase):
    def _make_frame(self):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = SECONDARY_VIDEO_TRACK
        frame.b_roll_items = []
        frame.timeline = []
        frame.ripple_mode = "per_track"
        frame.transition_name = "none"
        frame.default_image_duration = 5.0
        frame.last_insert_end = 0.0
        frame.current_time = 0.0
        frame.focused_element = None
        frame.is_dirty = False
        frame.start_time = None
        frame.end_time = None
        frame.say = lambda *args, **kwargs: None
        frame.record_edit = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.capture_edit_state = mock.Mock(return_value={})
        return frame

    def test_secondary_insertion_names_after_original_file(self):
        frame = self._make_frame()
        with mock.patch.object(frame, "pick_file_for_track", return_value=["D:/outside/my_clip.mp4"]), mock.patch.object(
            frame, "_import_media_into_program", return_value="C:/imports/abc123.mp4"
        ), mock.patch("video_maker.player.natural_span", return_value=4.0), mock.patch(
            "video_maker.player.media_kind_for_path", return_value="video"
        ):
            frame._insert_secondary_at_playhead(2.0)
        item = frame.b_roll_items[0]
        self.assertEqual(item["path"], "C:/imports/abc123.mp4")
        self.assertEqual(item["name"], "my_clip.mp4")


class MainVideoInsertionNamingTest(unittest.TestCase):
    def _make_frame(self):
        from video_maker.player import VideoPlayer

        frame = VideoPlayer.__new__(VideoPlayer)
        frame.current_track = MAIN_VIDEO_TRACK
        frame.timeline = []
        frame.b_roll_items = []
        frame.sound_effects_items = []
        frame.background_audio_items = []
        frame.visual_items = []
        frame.ripple_mode = "per_track"
        frame.last_insert_end = 0.0
        frame.current_time = 0.0
        frame.focused_element = None
        frame.is_dirty = False
        frame.start_time = 0.0
        frame.end_time = 10.0
        frame.say = lambda *args, **kwargs: None
        frame.record_edit = lambda *args, **kwargs: None
        frame.apply_edit_state = lambda *args, **kwargs: None
        frame.capture_edit_state = mock.Mock(return_value={})
        return frame

    def test_main_video_insertion_uses_original_filename(self):
        from video_maker.logical_files import new_file_segment

        segment = new_file_segment(
            "C:/imports/def456.mp4", 0.0, 6.0,
            source_file_name="intro.mp4",
        )
        self.assertEqual(segment.path, "C:/imports/def456.mp4")
        self.assertEqual(segment.source_file_name, "intro.mp4")

    def test_main_video_insertion_passes_original_basename(self):
        import builtins as _builtins

        frame = self._make_frame()
        original_dict = _builtins.dict
        calls = []

        def _dict_capture(obj, **kwargs):
            try:
                return original_dict(obj, **kwargs)
            except TypeError:
                calls.append(obj)
                return vars(obj) if hasattr(obj, "__dict__") else {}

        with mock.patch.object(frame, "pick_file_for_track", return_value=["D:/outside/intro.mp4"]), mock.patch.object(
            frame, "_import_media_into_program", return_value="C:/imports/def456.mp4"
        ), mock.patch("video_maker.player.natural_span", return_value=6.0), mock.patch.object(
            frame, "locate_timeline_segment", return_value=(0, None, 0.0)
        ), mock.patch(
            "video_maker.player_modules.professional.split_timeline_segment", return_value=([], [])
        ), mock.patch(
            "video_maker.player_modules.professional.dict", side_effect=_dict_capture
        ):
            frame._insert_main_video_at_playhead(0.0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].path, "C:/imports/def456.mp4")
        self.assertEqual(calls[0].source_file_name, "intro.mp4")


if __name__ == "__main__":
    unittest.main()
