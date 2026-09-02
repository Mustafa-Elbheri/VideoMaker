import errno
import io
import json
import os
import shutil
import struct
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass

from video_maker.app_state import normalize_ripple_mode
from video_maker.timeline import TimelineSegment
from video_maker.volume_boost import persisted_master_volume_db, persisted_track_volume_db


PROJECT_EXTENSION = ".elbheri"
PROJECT_SCHEMA_VERSION = 2
# المخططات المقبولة للقراءة. المخطط 1 هو النسخة القديمة التي قد تفتح الآن:
# أي مفتاح احترافي غائب فيها يُملأ بالافتراضي عبر _migrate_state (لا تحويل إجباري).
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
_FORMAT_VERSION = 1
_MAGIC = b"ELBHERI\x00"
_HEADER = struct.Struct(">8sB16s12s")
_TAG_SIZE = 16
_CHUNK_SIZE = 1024 * 1024
_MANIFEST_NAME = "manifest.json"
_MAX_MANIFEST_SIZE = 16 * 1024 * 1024
_ASSET_TOKEN_PREFIX = "asset:"
_KDF_INFO = b"Accessible Video Maker portable project format v1"

# This application key deliberately stays stable between computers and releases
# so a project created by the program can be restored by another installation.
# AES-GCM still authenticates every byte and rejects edited or damaged files.
_APPLICATION_KEY_MATERIAL = bytes.fromhex(
    "d91c9628c24f3f84f7f345ca778d3bc4"
    "bc58c08e25a35db9c945c690d91d61f2"
)


class ProjectError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")


class ProjectCancelled(ProjectError):
    def __init__(self):
        super().__init__("cancelled")


@dataclass(frozen=True)
class ProjectAsset:
    asset_id: str
    source_path: str
    archive_name: str
    original_name: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ProjectSnapshot:
    manifest: dict
    assets: tuple
    total_asset_bytes: int


class _AssetRegistry:
    def __init__(self):
        self._by_path = {}
        self.assets = []

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def register(self, path):
        path = str(path or "")
        if not path:
            return ""
        absolute = os.path.abspath(path)
        if not os.path.isfile(absolute):
            raise ProjectError("missing_asset", os.path.basename(path) or path)
        key = self._key(absolute)
        existing = self._by_path.get(key)
        if existing:
            return _ASSET_TOKEN_PREFIX + existing.asset_id
        asset_id = uuid.uuid4().hex
        original_name = os.path.basename(absolute) or f"media-{asset_id}"
        # Keep the real base name for Tab/Shift+Tab announcements while placing
        # each asset in its own directory to avoid collisions between equal names.
        archive_name = f"assets/{asset_id}/{original_name}"
        stat = os.stat(absolute)
        asset = ProjectAsset(
            asset_id=asset_id,
            source_path=absolute,
            archive_name=archive_name,
            original_name=original_name,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        self._by_path[key] = asset
        self.assets.append(asset)
        return _ASSET_TOKEN_PREFIX + asset_id


def _copy_dict(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _persist_track_volumes(track_volumes_db):
    valid = {}
    for key, db in (track_volumes_db or {}).items():
        valid[str(key)] = persisted_track_volume_db(db)
    return valid


# مفاتيح حالة المحرر الاحترافية الاختيارية (خطوة 10). أي مفتاح غائب في ملف قديم
# يُملأ بالافتراضي عند الاسترجاع حتى لا يفشل فتح المشاريع السابقة.
_PRO_STATE_DEFAULTS = {
    "muted_tracks": [],
    "solo_tracks": [],
    "ripple_mode": "per_track",
    "focused_element": None,
    "selected_element_ids": [],
}


def _migrate_state(state):
    """نسخة مهاجرة من حالة المخططات السابقة: تملأ مفاتيح الاحترافي الغائبة بافتراضات."""
    migrated = _copy_dict(state) if state else {}
    for key, default in _PRO_STATE_DEFAULTS.items():
        if key not in migrated:
            migrated[key] = _copy_dict(default)
    return migrated


def _encode_timeline(timeline, registry):
    encoded = []
    for segment in timeline:
        encoded.append({
            "path": registry.register(segment.path),
            "start": float(segment.start),
            "end": float(segment.end),
            "speed": float(getattr(segment, "speed", 1.0) or 1.0),
            "audio_volume": float(
                getattr(segment, "audio_volume", 1.0)
                if getattr(segment, "audio_volume", 1.0) is not None
                else 1.0
            ),
            "audio_path": registry.register(getattr(segment, "audio_path", "")),
            "audio_start": getattr(segment, "audio_start", None),
            "navigation_group": str(getattr(segment, "navigation_group", "") or ""),
            "source_file_id": str(getattr(segment, "source_file_id", "") or ""),
            "source_file_name": str(getattr(segment, "source_file_name", "") or ""),
            "transition": str(getattr(segment, "transition", "") or ""),
            "transition_duration": max(0.0, float(getattr(segment, "transition_duration", 1.0) or 1.0)),
            "audio_fade_in": max(0.0, float(getattr(segment, "audio_fade_in", 0.0) or 0.0)),
            "audio_fade_out": max(0.0, float(getattr(segment, "audio_fade_out", 0.0) or 0.0)),
        })
    return encoded


def _encode_items(items, registry, path_fields=("path",)):
    result = []
    for item in items or []:
        encoded = _copy_dict(dict(item))
        for field in path_fields:
            if encoded.get(field):
                encoded[field] = registry.register(encoded[field])
        result.append(encoded)
    return result


def _encode_edit_points(edit_points, registry):
    result = []
    for point in edit_points or []:
        encoded = _copy_dict(dict(point))
        restore_segments = []
        for segment in encoded.get("restore_segments", []) or []:
            restored = dict(segment)
            restored["path"] = registry.register(restored.get("path", ""))
            restored["audio_path"] = registry.register(restored.get("audio_path", ""))
            restore_segments.append(restored)
        encoded["restore_segments"] = restore_segments
        result.append(encoded)
    return result


def _encode_chroma_state(chroma_state, registry):
    if not chroma_state:
        return None
    encoded = _copy_dict(dict(chroma_state))
    encoded["render_path"] = registry.register(encoded.get("render_path", ""))
    encoded["source_paths"] = [registry.register(path) for path in encoded.get("source_paths", []) if path]
    return encoded


def capture_runtime_payload(player):
    """Capture the current in-memory workspace for rollback without copying media."""
    return {
        "video_path": str(getattr(player, "video_path", "") or ""),
        "media_kind": str(getattr(player, "media_kind", "video") or "video"),
        "current_time": float(getattr(player, "current_time", 0.0) or 0.0),
        "start_time": getattr(player, "start_time", None),
        "end_time": getattr(player, "end_time", None),
        "volume": float(getattr(player, "volume", 1.0) or 0.0),
        "master_volume_db": persisted_master_volume_db(getattr(player, "master_volume_db", 0.0)),
        "track_volumes_db": _persist_track_volumes(getattr(player, "track_volumes_db", {}) or {}),
        "seek_step": int(getattr(player, "seek_step", 100) or 100),
        "metadata": _copy_dict(dict(getattr(player, "file_metadata", {}) or {})),
        "visual_items": _copy_dict(list(getattr(player, "visual_items", []) or [])),
        "background_audio_items": _copy_dict(list(getattr(player, "background_audio_items", []) or [])),
        "b_roll_items": _copy_dict(list(getattr(player, "b_roll_items", []) or [])),
        "sound_effects_items": _copy_dict(list(getattr(player, "sound_effects_items", []) or [])),
        "main_audio_override_path": str(getattr(player, "main_audio_override_path", "") or ""),
        "main_audio_override_duration": float(getattr(player, "main_audio_override_duration", 0.0) or 0.0),
        "main_audio_override_timeline_duration": float(
            getattr(player, "main_audio_override_timeline_duration", 0.0) or 0.0
        ),
        "main_audio_effect_chain": _copy_dict(list(getattr(player, "main_audio_effect_chain", []) or [])),
        "main_audio_revision": int(getattr(player, "main_audio_revision", 0) or 0),
        "main_audio_source_revision": int(getattr(player, "main_audio_source_revision", 0) or 0),
        "timeline_revision": int(getattr(player, "timeline_revision", 0) or 0),
        "main_audio_format_version": int(getattr(player, "main_audio_format_version", 2) or 2),
        "edit_points": _copy_dict(list(getattr(player, "edit_points", []) or [])),
        "current_edit_point_id": getattr(player, "current_edit_point_id", None),
        "work_images": list(getattr(player, "work_images", []) or []),
        "work_videos": list(getattr(player, "work_videos", []) or []),
        "default_image_duration": float(getattr(player, "default_image_duration", 5.0) or 5.0),
        "transition_name": str(getattr(player, "transition_name", "") or ""),
        "last_insert_end": getattr(player, "last_insert_end", None),
        "window_name": str(getattr(player, "window_name", "") or ""),
        "chroma_render_state": _copy_dict(getattr(player, "chroma_render_state", None))
        if getattr(player, "chroma_render_state", None) else None,
        "timeline": list(getattr(player, "timeline", []) or []),
        "muted_tracks": list(getattr(player, "muted_tracks", []) or []),
        "solo_tracks": list(getattr(player, "solo_tracks", []) or []),
        "ripple_mode": normalize_ripple_mode(getattr(player, "ripple_mode", "per_track")),
        "focused_element": _copy_dict(getattr(player, "focused_element", None))
        if getattr(player, "focused_element", None) else None,
        "selected_element_ids": list(getattr(player, "selected_element_ids", []) or []),
    }


def capture_project_snapshot(player):
    """Capture a complete immutable project description and all referenced files."""
    timeline = list(getattr(player, "timeline", []) or [])
    if not timeline:
        raise ProjectError("empty_timeline")
    registry = _AssetRegistry()
    state = {
        "video_path": registry.register(getattr(player, "video_path", "")),
        "media_kind": str(getattr(player, "media_kind", "video") or "video"),
        "current_time": float(getattr(player, "current_time", 0.0) or 0.0),
        "start_time": getattr(player, "start_time", None),
        "end_time": getattr(player, "end_time", None),
        "volume": float(getattr(player, "volume", 1.0) or 0.0),
        "master_volume_db": persisted_master_volume_db(getattr(player, "master_volume_db", 0.0)),
        "track_volumes_db": _persist_track_volumes(getattr(player, "track_volumes_db", {}) or {}),
        "seek_step": int(getattr(player, "seek_step", 100) or 100),
        "metadata": _copy_dict(dict(getattr(player, "file_metadata", {}) or {})),
        "visual_items": _encode_items(getattr(player, "visual_items", []), registry),
        "background_audio_items": _encode_items(
            getattr(player, "background_audio_items", []),
            registry,
            ("path", "original_path"),
        ),
        "b_roll_items": _encode_items(
            getattr(player, "b_roll_items", []),
            registry,
            ("path", "original_path"),
        ),
        "sound_effects_items": _encode_items(
            getattr(player, "sound_effects_items", []),
            registry,
            ("path", "original_path"),
        ),
        "main_audio_override_path": registry.register(getattr(player, "main_audio_override_path", "")),
        "main_audio_override_duration": float(getattr(player, "main_audio_override_duration", 0.0) or 0.0),
        "main_audio_override_timeline_duration": float(
            getattr(player, "main_audio_override_timeline_duration", 0.0) or 0.0
        ),
        "main_audio_effect_chain": _copy_dict(list(getattr(player, "main_audio_effect_chain", []) or [])),
        "main_audio_revision": int(getattr(player, "main_audio_revision", 0) or 0),
        "main_audio_source_revision": int(getattr(player, "main_audio_source_revision", 0) or 0),
        "timeline_revision": int(getattr(player, "timeline_revision", 0) or 0),
        "main_audio_format_version": int(getattr(player, "main_audio_format_version", 2) or 2),
        "edit_points": _encode_edit_points(getattr(player, "edit_points", []), registry),
        "current_edit_point_id": getattr(player, "current_edit_point_id", None),
        "work_images": [registry.register(path) for path in getattr(player, "work_images", [])],
        "work_videos": [registry.register(path) for path in getattr(player, "work_videos", [])],
        "default_image_duration": float(getattr(player, "default_image_duration", 5.0) or 5.0),
        "transition_name": str(getattr(player, "transition_name", "") or ""),
        "last_insert_end": getattr(player, "last_insert_end", None),
        "window_name": str(getattr(player, "window_name", "") or ""),
        "chroma_render_state": _encode_chroma_state(getattr(player, "chroma_render_state", None), registry),
        "timeline": _encode_timeline(timeline, registry),
        "muted_tracks": [str(key) for key in (getattr(player, "muted_tracks", []) or [])],
        "solo_tracks": [str(key) for key in (getattr(player, "solo_tracks", []) or [])],
        "ripple_mode": normalize_ripple_mode(getattr(player, "ripple_mode", "per_track")),
        "focused_element": _copy_dict(getattr(player, "focused_element", None))
        if getattr(player, "focused_element", None) else None,
        "selected_element_ids": [str(key) for key in (getattr(player, "selected_element_ids", []) or [])],
    }
    assets = tuple(registry.assets)
    manifest = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "created_at": time.time(),
        "state": state,
        "assets": [
            {
                "id": asset.asset_id,
                "archive_name": asset.archive_name,
                "original_name": asset.original_name,
                "size": asset.size,
            }
            for asset in assets
        ],
    }
    return ProjectSnapshot(
        manifest=manifest,
        assets=assets,
        total_asset_bytes=sum(asset.size for asset in assets),
    )


def ensure_project_extension(path):
    path = str(path or "").strip()
    if not path:
        return ""
    if not path.lower().endswith(PROJECT_EXTENSION):
        path += PROJECT_EXTENSION
    return path


def _crypto_types():
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except Exception as error:
        raise ProjectError("crypto_unavailable", str(error)) from error
    return hashes, Cipher, algorithms, modes, HKDF


def _derive_key(salt):
    hashes, _, _, _, HKDF = _crypto_types()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_KDF_INFO,
    ).derive(_APPLICATION_KEY_MATERIAL)


class _EncryptedWriter:
    def __init__(self, raw_file):
        _, Cipher, algorithms, modes, _ = _crypto_types()
        self.raw_file = raw_file
        self.salt = os.urandom(16)
        self.nonce = os.urandom(12)
        self.header = _HEADER.pack(_MAGIC, _FORMAT_VERSION, self.salt, self.nonce)
        key = _derive_key(self.salt)
        self.encryptor = Cipher(algorithms.AES(key), modes.GCM(self.nonce)).encryptor()
        self.encryptor.authenticate_additional_data(self.header)
        self.raw_file.write(self.header)
        self.finalized = False

    def write(self, data):
        if self.finalized:
            raise ValueError("encrypted writer is closed")
        data = bytes(data)
        if data:
            encrypted = self.encryptor.update(data)
            if encrypted:
                self.raw_file.write(encrypted)
        return len(data)

    def tell(self):
        return self.raw_file.tell()

    def flush(self):
        self.raw_file.flush()

    def finalize(self):
        if self.finalized:
            return
        tail = self.encryptor.finalize()
        if tail:
            self.raw_file.write(tail)
        self.raw_file.write(self.encryptor.tag)
        self.raw_file.flush()
        os.fsync(self.raw_file.fileno())
        self.finalized = True


class _ProgressReader:
    def __init__(self, file_obj, progress, cancel_event, completed_ref):
        self.file_obj = file_obj
        self.progress = progress
        self.cancel_event = cancel_event
        self.completed_ref = completed_ref

    def read(self, size=-1):
        _check_cancel(self.cancel_event)
        data = self.file_obj.read(size)
        if data:
            self.completed_ref[0] += len(data)
            self.progress(self.completed_ref[0])
        _check_cancel(self.cancel_event)
        return data


class _DecryptingReader:
    def __init__(self, raw_file, total_size, progress_callback, cancel_event):
        _, Cipher, algorithms, modes, _ = _crypto_types()
        self.raw_file = raw_file
        self.total_size = int(total_size)
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event
        self.header = self.raw_file.read(_HEADER.size)
        if len(self.header) != _HEADER.size:
            raise ProjectError("invalid_format")
        magic, version, salt, nonce = _HEADER.unpack(self.header)
        if magic != _MAGIC:
            raise ProjectError("invalid_format")
        if version != _FORMAT_VERSION:
            raise ProjectError("unsupported_version")
        ciphertext_size = self.total_size - _HEADER.size - _TAG_SIZE
        if ciphertext_size <= 0:
            raise ProjectError("invalid_format")
        self.raw_file.seek(self.total_size - _TAG_SIZE)
        tag = self.raw_file.read(_TAG_SIZE)
        if len(tag) != _TAG_SIZE:
            raise ProjectError("invalid_format")
        self.raw_file.seek(_HEADER.size)
        key = _derive_key(salt)
        self.decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        self.decryptor.authenticate_additional_data(self.header)
        self.remaining = ciphertext_size
        self.completed = 0
        self.finalized = False

    def readable(self):
        return True

    def read(self, size=-1):
        _check_cancel(self.cancel_event)
        if self.remaining <= 0:
            self._finalize()
            return b""
        if size is None or size < 0:
            size = min(_CHUNK_SIZE, self.remaining)
        else:
            size = min(max(1, int(size)), self.remaining)
        encrypted = self.raw_file.read(size)
        if not encrypted:
            raise ProjectError("invalid_format")
        self.remaining -= len(encrypted)
        self.completed += len(encrypted)
        _emit_percent(self.progress_callback, self.completed, max(1, self.total_size - _HEADER.size - _TAG_SIZE))
        try:
            plain = self.decryptor.update(encrypted)
        except Exception as error:
            raise ProjectError("integrity_error") from error
        if self.remaining <= 0:
            self._finalize()
        _check_cancel(self.cancel_event)
        return plain

    def _finalize(self):
        if self.finalized:
            return
        try:
            tail = self.decryptor.finalize()
        except Exception as error:
            raise ProjectError("integrity_error") from error
        if tail:
            # GCM does not normally return a final tail. Refuse an unexpected
            # value rather than silently losing bytes in the tar stream.
            raise ProjectError("integrity_error")
        self.finalized = True

    def finish(self):
        while self.remaining > 0:
            self.read(min(_CHUNK_SIZE, self.remaining))
        self._finalize()


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ProjectCancelled()


def _emit_percent(callback, completed, total):
    if callback:
        callback(max(0, min(100, int((float(completed) * 100.0) / max(1, float(total))))))


def _estimated_archive_size(snapshot, manifest_size):
    size = _HEADER.size + _TAG_SIZE + 1024
    size += 512 + ((manifest_size + 511) // 512) * 512
    for asset in snapshot.assets:
        size += 512 + ((asset.size + 511) // 512) * 512
    return size


def _require_disk_space(directory, required, code):
    try:
        free = shutil.disk_usage(directory).free
    except Exception:
        return
    margin = max(16 * 1024 * 1024, int(required * 0.01))
    if free < required + margin:
        raise ProjectError(code)


def save_project_file(path, snapshot, progress_callback=None, cancel_event=None):
    """Write a complete encrypted project atomically without an unencrypted archive."""
    path = ensure_project_extension(path)
    if not path:
        raise ProjectError("invalid_path")
    destination_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    os.makedirs(destination_dir, exist_ok=True)
    manifest_bytes = json.dumps(snapshot.manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _require_disk_space(destination_dir, _estimated_archive_size(snapshot, len(manifest_bytes)), "no_space_save")
    handle, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=destination_dir)
    completed = [0]
    total = max(1, len(manifest_bytes) + snapshot.total_asset_bytes)

    def report(value):
        _emit_percent(progress_callback, value, total)

    try:
        with os.fdopen(handle, "w+b") as raw_file:
            encrypted = _EncryptedWriter(raw_file)
            with tarfile.open(fileobj=encrypted, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                _check_cancel(cancel_event)
                manifest_info = tarfile.TarInfo(_MANIFEST_NAME)
                manifest_info.size = len(manifest_bytes)
                manifest_info.mode = 0o600
                manifest_info.mtime = int(time.time())
                archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
                completed[0] += len(manifest_bytes)
                report(completed[0])

                for asset in snapshot.assets:
                    _check_cancel(cancel_event)
                    stat = os.stat(asset.source_path)
                    if stat.st_size != asset.size or stat.st_mtime_ns != asset.mtime_ns:
                        raise ProjectError("asset_changed", asset.original_name)
                    info = tarfile.TarInfo(asset.archive_name)
                    info.size = asset.size
                    info.mode = 0o600
                    info.mtime = int(stat.st_mtime)
                    with open(asset.source_path, "rb") as source:
                        archive.addfile(
                            info,
                            _ProgressReader(source, report, cancel_event, completed),
                        )
                    final_stat = os.stat(asset.source_path)
                    if final_stat.st_size != asset.size or final_stat.st_mtime_ns != asset.mtime_ns:
                        raise ProjectError("asset_changed", asset.original_name)
            _check_cancel(cancel_event)
            encrypted.finalize()
            _check_cancel(cancel_event)
        os.replace(temp_path, path)
        try:
            directory_fd = os.open(destination_dir, os.O_RDONLY)
        except Exception:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
        if progress_callback:
            progress_callback(100)
        return path
    except Exception as error:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            raise ProjectError("no_space_save") from error
        raise


def _safe_asset_destination(root, archive_name):
    normalized = archive_name.replace("\\", "/")
    parts = normalized.split("/")
    if len(parts) != 3 or parts[0] != "assets" or not parts[1] or not parts[2]:
        raise ProjectError("invalid_format")
    if any(part in (".", "..") for part in parts):
        raise ProjectError("invalid_format")
    destination = os.path.abspath(os.path.join(root, *parts))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, destination]) != root_abs:
        raise ProjectError("invalid_format")
    return destination


def _read_manifest(archive):
    try:
        member = next(iter(archive))
    except StopIteration as error:
        raise ProjectError("invalid_format") from error
    if member.name != _MANIFEST_NAME or not member.isfile() or member.size > _MAX_MANIFEST_SIZE:
        raise ProjectError("invalid_format")
    stream = archive.extractfile(member)
    if stream is None:
        raise ProjectError("invalid_format")
    raw = stream.read(_MAX_MANIFEST_SIZE + 1)
    if len(raw) > _MAX_MANIFEST_SIZE:
        raise ProjectError("invalid_format")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as error:
        raise ProjectError("invalid_format") from error
    if not isinstance(manifest, dict):
        raise ProjectError("invalid_format")
    if manifest.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise ProjectError("unsupported_version")
    if not isinstance(manifest.get("state"), dict) or not isinstance(manifest.get("assets"), list):
        raise ProjectError("invalid_format")
    return manifest


def _validate_asset_descriptors(manifest):
    descriptors = {}
    total = 0
    for item in manifest.get("assets", []):
        if not isinstance(item, dict):
            raise ProjectError("invalid_format")
        asset_id = str(item.get("id", ""))
        archive_name = str(item.get("archive_name", ""))
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError) as error:
            raise ProjectError("invalid_format") from error
        if not asset_id or asset_id in descriptors or size < 0:
            raise ProjectError("invalid_format")
        expected_name = f"assets/{asset_id}/"
        if not archive_name.startswith(expected_name):
            raise ProjectError("invalid_format")
        descriptors[asset_id] = {
            "archive_name": archive_name,
            "size": size,
        }
        total += size
    return descriptors, total


def _resolve_token(value, asset_paths, allow_empty=True):
    value = str(value or "")
    if not value and allow_empty:
        return ""
    if not value.startswith(_ASSET_TOKEN_PREFIX):
        raise ProjectError("invalid_format")
    asset_id = value[len(_ASSET_TOKEN_PREFIX):]
    path = asset_paths.get(asset_id)
    if not path:
        raise ProjectError("missing_project_asset")
    return path


def _decode_items(items, asset_paths, path_fields=("path",)):
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            raise ProjectError("invalid_format")
        decoded = _copy_dict(item)
        for field in path_fields:
            if decoded.get(field):
                decoded[field] = _resolve_token(decoded[field], asset_paths)
        result.append(decoded)
    return result


def _decode_edit_points(edit_points, asset_paths):
    result = []
    for point in edit_points or []:
        if not isinstance(point, dict):
            raise ProjectError("invalid_format")
        decoded = _copy_dict(point)
        restored = []
        for segment in decoded.get("restore_segments", []) or []:
            item = dict(segment)
            item["path"] = _resolve_token(item.get("path", ""), asset_paths)
            if item.get("audio_path"):
                item["audio_path"] = _resolve_token(item["audio_path"], asset_paths)
            else:
                item["audio_path"] = ""
            restored.append(item)
        decoded["restore_segments"] = restored
        result.append(decoded)
    return result


def _decode_chroma_state(chroma_state, asset_paths):
    if not chroma_state:
        return None
    if not isinstance(chroma_state, dict):
        raise ProjectError("invalid_format")
    decoded = _copy_dict(chroma_state)
    decoded["render_path"] = _resolve_token(decoded.get("render_path", ""), asset_paths)
    decoded["source_paths"] = [
        _resolve_token(value, asset_paths) for value in decoded.get("source_paths", [])
    ]
    return decoded


def _decode_state(state, asset_paths):
    state = _migrate_state(state)
    timeline = []
    for item in state.get("timeline", []) or []:
        if not isinstance(item, dict):
            raise ProjectError("invalid_format")
        try:
            start = float(item["start"])
            end = float(item["end"])
            speed = float(item.get("speed", 1.0) or 1.0)
            volume = float(item.get("audio_volume", 1.0) if item.get("audio_volume", 1.0) is not None else 1.0)
        except (KeyError, TypeError, ValueError) as error:
            raise ProjectError("invalid_format") from error
        if end <= start or speed <= 0:
            raise ProjectError("invalid_format")
        audio_path = ""
        if item.get("audio_path"):
            audio_path = _resolve_token(item["audio_path"], asset_paths)
        audio_start = item.get("audio_start")
        if audio_start is not None:
            try:
                audio_start = float(audio_start)
            except (TypeError, ValueError) as error:
                raise ProjectError("invalid_format") from error
        timeline.append(TimelineSegment(
            _resolve_token(item.get("path", ""), asset_paths),
            start,
            end,
            speed,
            volume,
            audio_path,
            audio_start,
            str(item.get("navigation_group", "") or ""),
            str(item.get("source_file_id", "") or ""),
            str(item.get("source_file_name", "") or ""),
            str(item.get("transition", "") or ""),
            max(0.0, float(item.get("transition_duration", 1.0) or 1.0)),
            max(0.0, float(item.get("audio_fade_in", 0.0) or 0.0)),
            max(0.0, float(item.get("audio_fade_out", 0.0) or 0.0)),
        ))
    if not timeline:
        raise ProjectError("empty_timeline")
    payload = {
        "video_path": _resolve_token(state.get("video_path", ""), asset_paths),
        "media_kind": state.get("media_kind", "video"),
        "current_time": state.get("current_time", 0.0),
        "start_time": state.get("start_time"),
        "end_time": state.get("end_time"),
        "volume": state.get("volume", 1.0),
        "master_volume_db": persisted_master_volume_db(state.get("master_volume_db", 0.0)),
        "track_volumes_db": _persist_track_volumes(state.get("track_volumes_db", {}) or {}),
        "seek_step": state.get("seek_step", 100),
        "metadata": _copy_dict(state.get("metadata", {})),
        "visual_items": _decode_items(state.get("visual_items", []), asset_paths),
        "background_audio_items": _decode_items(
            state.get("background_audio_items", []),
            asset_paths,
            ("path", "original_path"),
        ),
        "b_roll_items": _decode_items(
            state.get("b_roll_items", []),
            asset_paths,
            ("path", "original_path"),
        ),
        "sound_effects_items": _decode_items(
            state.get("sound_effects_items", []),
            asset_paths,
            ("path", "original_path"),
        ),
        "main_audio_override_path": (
            _resolve_token(state.get("main_audio_override_path", ""), asset_paths)
            if state.get("main_audio_override_path") else ""
        ),
        "main_audio_override_duration": state.get("main_audio_override_duration", 0.0),
        "main_audio_override_timeline_duration": state.get("main_audio_override_timeline_duration", 0.0),
        "main_audio_effect_chain": _copy_dict(state.get("main_audio_effect_chain", [])),
        "main_audio_revision": state.get("main_audio_revision", 0),
        "main_audio_source_revision": state.get("main_audio_source_revision", 0),
        "timeline_revision": state.get("timeline_revision", 0),
        "main_audio_format_version": state.get("main_audio_format_version", 2),
        "edit_points": _decode_edit_points(state.get("edit_points", []), asset_paths),
        "current_edit_point_id": state.get("current_edit_point_id"),
        "work_images": [_resolve_token(value, asset_paths) for value in state.get("work_images", [])],
        "work_videos": [_resolve_token(value, asset_paths) for value in state.get("work_videos", [])],
        "default_image_duration": state.get("default_image_duration", 5.0),
        "transition_name": state.get("transition_name", ""),
        "last_insert_end": state.get("last_insert_end"),
        "window_name": state.get("window_name", ""),
        "chroma_render_state": _decode_chroma_state(state.get("chroma_render_state"), asset_paths),
        "timeline": timeline,
        "muted_tracks": [str(key) for key in state.get("muted_tracks", [])],
        "solo_tracks": [str(key) for key in state.get("solo_tracks", [])],
        "ripple_mode": normalize_ripple_mode(state.get("ripple_mode", "per_track")),
        "focused_element": _copy_dict(state.get("focused_element"))
        if isinstance(state.get("focused_element"), dict) else None,
        "selected_element_ids": [str(key) for key in state.get("selected_element_ids", [])],
    }
    return payload


def restore_project_file(path, progress_callback=None, cancel_event=None):
    """Authenticate, extract, validate and return a portable project payload."""
    path = str(path or "")
    if not os.path.isfile(path):
        raise ProjectError("invalid_path")
    size = os.path.getsize(path)
    if size <= _HEADER.size + _TAG_SIZE:
        raise ProjectError("invalid_format")
    extraction_root = tempfile.mkdtemp(prefix="elbheri_project_")
    try:
        with open(path, "rb") as raw_file:
            reader = _DecryptingReader(raw_file, size, progress_callback, cancel_event)
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                manifest = _read_manifest(archive)
                descriptors, total_assets = _validate_asset_descriptors(manifest)
                descriptors_by_name = {
                    item["archive_name"]: (asset_id, item)
                    for asset_id, item in descriptors.items()
                }
                if len(descriptors_by_name) != len(descriptors):
                    raise ProjectError("invalid_format")
                _require_disk_space(extraction_root, total_assets, "no_space_restore")
                asset_paths = {}
                seen_names = set()
                manifest_revisited = False
                for member in archive:
                    _check_cancel(cancel_event)
                    # TarFile's iterator may replay the first member already read
                    # by _read_manifest. Accept that one replay only.
                    if member.name == _MANIFEST_NAME:
                        if manifest_revisited:
                            raise ProjectError("invalid_format")
                        manifest_revisited = True
                        continue
                    if not member.isfile() or member.name in seen_names:
                        raise ProjectError("invalid_format")
                    seen_names.add(member.name)
                    matching = descriptors_by_name.get(member.name)
                    if matching is None:
                        raise ProjectError("invalid_format")
                    asset_id, descriptor = matching
                    if member.size != descriptor["size"]:
                        raise ProjectError("integrity_error")
                    destination = _safe_asset_destination(extraction_root, member.name)
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ProjectError("invalid_format")
                    remaining = member.size
                    with open(destination, "wb") as output:
                        while remaining > 0:
                            _check_cancel(cancel_event)
                            chunk = source.read(min(_CHUNK_SIZE, remaining))
                            if not chunk:
                                raise ProjectError("integrity_error")
                            output.write(chunk)
                            remaining -= len(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    asset_paths[asset_id] = destination
            reader.finish()
        _check_cancel(cancel_event)
        if set(asset_paths) != set(descriptors):
            raise ProjectError("missing_project_asset")
        payload = _decode_state(manifest["state"], asset_paths)
        if progress_callback:
            progress_callback(100)
        return payload, extraction_root
    except Exception as error:
        shutil.rmtree(extraction_root, ignore_errors=True)
        if isinstance(error, ProjectError):
            raise
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            raise ProjectError("no_space_restore") from error
        if isinstance(error, tarfile.TarError):
            raise ProjectError("integrity_error") from error
        raise


def project_error_text_key(error, operation):
    code = getattr(error, "code", "")
    mapping = {
        "cancelled": "تم إلغاء حفظ المشروع" if operation == "save" else "تم إلغاء استعادة المشروع",
        "crypto_unavailable": "مكتبة تشفير المشاريع غير متوفرة",
        "missing_asset": "أحد ملفات المشروع غير موجود",
        "asset_changed": "تغير أحد ملفات المشروع أثناء الحفظ",
        "empty_timeline": "المشروع لا يحتوي على خط زمني صالح",
        "invalid_path": "مسار ملف المشروع غير صالح",
        "invalid_format": "هذا الملف ليس مشروع بحيري صالحا",
        "unsupported_version": "إصدار ملف المشروع غير مدعوم",
        "integrity_error": "ملف المشروع تالف أو تم تعديله",
        "missing_project_asset": "ملف المشروع ناقص أو تالف",
        "no_space_save": "لا توجد مساحة كافية لحفظ المشروع",
        "no_space_restore": "لا توجد مساحة كافية لاستعادة المشروع",
    }
    return mapping.get(code, "تعذر حفظ المشروع" if operation == "save" else "تعذر استعادة المشروع")
