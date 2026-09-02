import copy
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath("."))

from video_maker.edit_history import EditHistory
from video_maker.encrypted_projects import (
    PROJECT_SCHEMA_VERSION,
    ProjectError,
    _decode_state,
    capture_project_snapshot,
    capture_runtime_payload,
    restore_project_file,
    save_project_file,
)
from video_maker.text_overlay import TextOverlayOptions, serialize_text_options
from video_maker.timeline import TimelineSegment
from video_maker.track_items import new_dynamic_text_item
from video_maker.work_sessions import session_payload


def _text_options(text="مرحبا", font_size=40):
    return TextOverlayOptions(
        text=text,
        font_path="C:\\fonts\\a.ttf",
        font_name="Arial",
        font_size=font_size,
        color=(255, 255, 255, 255),
        background="",
        background_opacity=0,
        position="center_bottom",
        box_width_percent=60,
    )


def _text_item(text="مرحبا", start=1.0, end=4.0):
    return new_dynamic_text_item(_text_options(text), start, end)


def _media_files(media_dir):
    media = {}
    for name in ("main.mp4", "sfx.wav", "bg.wav", "broll.png"):
        path = os.path.join(media_dir, name)
        with open(path, "wb") as file_obj:
            file_obj.write(os.urandom(64))
        media[name] = path
    return media


def _snapshot_player(media):
    text_item = {
        "id": "text-1",
        "type": "text",
        "path": "",
        "start": 2.0,
        "end": 5.0,
        "options": dict(serialize_text_options(_text_options("مرحبا", 44))),
        "is_dynamic": True,
    }
    sfx_item = {
        "id": "sfx-1",
        "type": "audio",
        "path": media["sfx.wav"],
        "original_path": media["sfx.wav"],
        "start": 1.0,
        "end": 3.0,
        "volume": 0.5,
        "speed": 1.0,
        "source_offset": 0.0,
    }
    bg_item = {
        "id": "bg-1",
        "type": "audio",
        "path": media["bg.wav"],
        "original_path": media["bg.wav"],
        "start": 0.0,
        "end": 6.0,
        "volume": 0.4,
        "speed": 1.0,
        "source_offset": 0.0,
    }
    broll_item = {
        "id": "b-1",
        "type": "image",
        "path": media["broll.png"],
        "original_path": media["broll.png"],
        "start": 0.5,
        "end": 4.5,
    }
    timeline = [TimelineSegment(media["main.mp4"], 0.0, 10.0, 1.0, 1.0, "", None, "", "", "", "", 1.0)]
    return SimpleNamespace(
        timeline=timeline,
        video_path=media["main.mp4"],
        media_kind="video",
        current_time=2.5,
        start_time=None,
        end_time=None,
        volume=1.0,
        master_volume_db=0.0,
        seek_step=100,
        file_metadata={"title": "تجربة"},
        visual_items=[text_item],
        background_audio_items=[bg_item],
        b_roll_items=[broll_item],
        sound_effects_items=[sfx_item],
        main_audio_override_path="",
        main_audio_override_duration=0.0,
        main_audio_override_timeline_duration=0.0,
        main_audio_effect_chain=[],
        main_audio_revision=0,
        main_audio_source_revision=0,
        timeline_revision=0,
        main_audio_format_version=2,
        edit_points=[],
        current_edit_point_id=None,
        work_images=[media["broll.png"]],
        work_videos=[media["main.mp4"]],
        default_image_duration=5.0,
        transition_name="",
        last_insert_end=None,
        window_name="",
        chroma_render_state=None,
        muted_tracks={"sound_effects", "background_audio"},
        ripple_mode="all_tracks",
        focused_element=dict(text_item),
        selected_element_ids={"text-1", "sfx-1"},
    )


class ProStateProjectRoundTripTest(unittest.TestCase):
    def test_full_round_trip_restores_pro_state(self):
        with tempfile.TemporaryDirectory() as media_dir, tempfile.TemporaryDirectory() as out_dir:
            player = _snapshot_player(_media_files(media_dir))
            snapshot = capture_project_snapshot(player)
            project_path = os.path.join(out_dir, "proj.elbheri")
            save_project_file(project_path, snapshot)
            payload, extraction_root = restore_project_file(project_path)
            try:
                self.assertEqual(sorted(payload["muted_tracks"]), ["background_audio", "sound_effects"])
                self.assertEqual(payload["ripple_mode"], "all_tracks")
                self.assertEqual(sorted(payload["selected_element_ids"]), ["sfx-1", "text-1"])
                self.assertEqual(payload["focused_element"]["id"], "text-1")
                self.assertEqual(payload["focused_element"]["options"]["text"], "مرحبا")
                sound_effects = payload["sound_effects_items"]
                self.assertEqual([item["id"] for item in sound_effects], ["sfx-1"])
                self.assertAlmostEqual(float(sound_effects[0]["start"]), 1.0)
                self.assertAlmostEqual(float(sound_effects[0]["volume"]), 0.5)
                texts = [item for item in payload["visual_items"] if item.get("is_dynamic")]
                self.assertEqual(len(texts), 1)
                self.assertEqual(texts[0]["id"], "text-1")
                self.assertEqual(texts[0]["options"]["text"], "مرحبا")
                self.assertEqual(len(payload["timeline"]), 1)
                self.assertAlmostEqual(float(payload["timeline"][0].start), 0.0)
                self.assertAlmostEqual(float(payload["timeline"][0].end), 10.0)
            finally:
                shutil.rmtree(extraction_root, ignore_errors=True)

    def test_old_schema_one_project_still_opens(self):
        with tempfile.TemporaryDirectory() as media_dir, tempfile.TemporaryDirectory() as out_dir:
            player = _snapshot_player(_media_files(media_dir))
            project_path = os.path.join(out_dir, "old.elbheri")
            with mock.patch("video_maker.encrypted_projects.PROJECT_SCHEMA_VERSION", 1):
                snapshot = capture_project_snapshot(player)
                self.assertEqual(snapshot.manifest["schema_version"], 1)
                save_project_file(project_path, snapshot)
            payload, extraction_root = restore_project_file(project_path)
            try:
                self.assertEqual(payload["ripple_mode"], "all_tracks")
                self.assertEqual(sorted(payload["muted_tracks"]), ["background_audio", "sound_effects"])
            finally:
                shutil.rmtree(extraction_root, ignore_errors=True)

    def test_unsupported_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as media_dir, tempfile.TemporaryDirectory() as out_dir:
            player = _snapshot_player(_media_files(media_dir))
            project_path = os.path.join(out_dir, "future.elbheri")
            with mock.patch("video_maker.encrypted_projects.PROJECT_SCHEMA_VERSION", 99):
                snapshot = capture_project_snapshot(player)
                save_project_file(project_path, snapshot)
            with self.assertRaises(ProjectError) as context:
                restore_project_file(project_path)
            self.assertEqual(context.exception.code, "unsupported_version")


class ProStateDecodeDefaultsTest(unittest.TestCase):
    def test_old_state_without_pro_keys_gets_defaults(self):
        state = {
            "timeline": [
                {"path": "", "start": 0.0, "end": 10.0, "speed": 1.0, "audio_volume": 1.0},
            ],
            "sound_effects_items": [
                {"id": "s1", "path": "", "start": 1.0, "end": 2.0, "volume": 0.5},
            ],
        }
        payload = _decode_state(state, {})
        self.assertEqual(payload["muted_tracks"], [])
        self.assertEqual(payload["ripple_mode"], "per_track")
        self.assertIsNone(payload["focused_element"])
        self.assertEqual(payload["selected_element_ids"], [])
        self.assertEqual([item["id"] for item in payload["sound_effects_items"]], ["s1"])

    def test_decode_preserves_supplied_pro_keys(self):
        state = {
            "timeline": [
                {"path": "", "start": 0.0, "end": 10.0, "speed": 1.0, "audio_volume": 1.0},
            ],
            "muted_tracks": ["sound_effects"],
            "ripple_mode": "off",
            "focused_element": {"id": "t1"},
            "selected_element_ids": ["t1"],
        }
        payload = _decode_state(state, {})
        self.assertEqual(payload["muted_tracks"], ["sound_effects"])
        self.assertEqual(payload["ripple_mode"], "off")
        self.assertEqual(payload["focused_element"], {"id": "t1"})
        self.assertEqual(payload["selected_element_ids"], ["t1"])

    def test_invalid_ripple_mode_falls_back_to_default(self):
        state = {
            "timeline": [
                {"path": "", "start": 0.0, "end": 10.0, "speed": 1.0, "audio_volume": 1.0},
            ],
            "ripple_mode": "sideways",
        }
        payload = _decode_state(state, {})
        self.assertEqual(payload["ripple_mode"], "per_track")


class ProStateUndoRedoTest(unittest.TestCase):
    def _edit_frame(self):
        from video_maker.player import VideoPlayer

        text = _text_item("أ", 1.0, 4.0)
        frame = VideoPlayer.__new__(VideoPlayer)
        frame.timeline = []
        frame.media_kind = "video"
        frame.video_path = "main.mp4"
        frame.visual_items = [text]
        frame.background_audio_items = []
        frame.b_roll_items = []
        frame.sound_effects_items = []
        frame.main_audio_override_path = ""
        frame.main_audio_override_duration = 0.0
        frame.main_audio_override_timeline_duration = 0.0
        frame.main_audio_effect_chain = []
        frame.main_audio_revision = 0
        frame.main_audio_source_revision = 0
        frame.timeline_revision = 0
        frame.main_audio_format_version = 2
        frame.edit_points = []
        frame.current_edit_point_id = None
        frame.current_time = 0.0
        frame.start_time = None
        frame.end_time = None
        frame.last_insert_end = None
        frame.file_metadata = {}
        frame.is_dirty = True
        frame.chroma_render_state = None
        frame.focused_element = dict(text)
        frame.selected_element_ids = {"text-1"}
        frame.element_clipboard = None
        frame.muted_tracks = {"sound_effects"}
        frame.ripple_mode = "all_tracks"
        return frame

    def test_capture_edit_state_covers_pro_lists(self):
        frame = self._edit_frame()
        state = frame.capture_edit_state()
        for key in (
            "sound_effects_items",
            "muted_tracks",
            "ripple_mode",
            "focused_element",
            "selected_element_ids",
            "element_clipboard",
        ):
            self.assertIn(key, state)
        self.assertEqual(state["ripple_mode"], "all_tracks")
        self.assertEqual(state["muted_tracks"], {"sound_effects"})

    def test_undo_undoes_text_edit_and_restore_returns_it(self):
        frame = self._edit_frame()
        before = copy.deepcopy(frame.capture_edit_state())
        frame.visual_items[0]["options"]["text"] = "نص معدل"
        after = frame.capture_edit_state()
        self.assertNotEqual(before, after)
        history = EditHistory(10)
        self.assertTrue(history.record("تعديل النص", before, after))
        operation, undo_state = history.undo()
        self.assertEqual(operation, "تعديل النص")
        self.assertEqual(undo_state, before)
        self.assertEqual(undo_state["visual_items"][0]["options"]["text"], "أ")
        operation, redo_state = history.restore()
        self.assertEqual(operation, "تعديل النص")
        self.assertEqual(redo_state, after)
        self.assertEqual(redo_state["visual_items"][0]["options"]["text"], "نص معدل")

    def test_mute_ripple_edits_are_recordable(self):
        frame = self._edit_frame()
        before = frame.capture_edit_state()
        frame.muted_tracks.add("background_audio")
        frame.ripple_mode = "off"
        after = frame.capture_edit_state()
        self.assertNotEqual(before, after)
        history = EditHistory(10)
        history.record("تبديل كتم التراك", before, after)
        _, undo_state = history.undo()
        self.assertEqual(undo_state["muted_tracks"], {"sound_effects"})
        self.assertEqual(undo_state["ripple_mode"], "all_tracks")


class ProStatePayloadKeysTest(unittest.TestCase):
    def _runtime_frame(self):
        return SimpleNamespace(
            video_path="",
            media_kind="video",
            current_time=0.0,
            start_time=None,
            end_time=None,
            volume=1.0,
            master_volume_db=0.0,
            seek_step=100,
            file_metadata={},
            visual_items=[],
            background_audio_items=[],
            b_roll_items=[],
            sound_effects_items=[],
            main_audio_override_path="",
            main_audio_override_duration=0.0,
            main_audio_override_timeline_duration=0.0,
            main_audio_effect_chain=[],
            main_audio_revision=0,
            main_audio_source_revision=0,
            timeline_revision=0,
            main_audio_format_version=2,
            edit_points=[],
            current_edit_point_id=None,
            work_images=[],
            work_videos=[],
            default_image_duration=5.0,
            transition_name="",
            last_insert_end=None,
            window_name="",
            chroma_render_state=None,
            timeline=[],
            muted_tracks={"sound_effects"},
            ripple_mode="all_tracks",
            focused_element={"id": "t1"},
            selected_element_ids={"t1"},
        )

    def test_runtime_payload_carries_pro_state(self):
        payload = capture_runtime_payload(self._runtime_frame())
        self.assertEqual(payload["muted_tracks"], ["sound_effects"])
        self.assertEqual(payload["ripple_mode"], "all_tracks")
        self.assertEqual(payload["focused_element"], {"id": "t1"})
        self.assertEqual(payload["selected_element_ids"], ["t1"])

    def test_session_payload_carries_pro_state(self):
        player = self._runtime_frame()
        payload = session_payload(
            "جلسة",
            player,
            [],
            visual_items=[],
            background_audio_items=[],
            b_roll_items=[],
            sound_effects_items=[],
            work_images=[],
            work_videos=[],
            edit_points=[],
        )
        self.assertEqual(payload["muted_tracks"], ["sound_effects"])
        self.assertEqual(payload["ripple_mode"], "all_tracks")
        self.assertEqual(payload["focused_element"], {"id": "t1"})
        self.assertEqual(payload["selected_element_ids"], ["t1"])

    def test_new_schema_version_is_two(self):
        self.assertEqual(PROJECT_SCHEMA_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
