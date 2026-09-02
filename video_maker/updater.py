import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from video_maker.app_info import APP_VERSION
from video_maker.app_paths import user_data_path
from video_maker.tls_trust import describe_update_ssl_context, get_update_ssl_context


GITHUB_REPOSITORY = "Mustafa-Elbheri/VideoMaker"
ASSET_PATTERNS = ["VideoMakerSetup.exe", ".exe", ".msi", ".zip"]
GITHUB_API = "https://api.github.com/repos/{repository}/releases/latest"
GITHUB_LATEST_PAGE = "https://github.com/{repository}/releases/latest"
UPDATE_CHECK_ATTEMPTS = 3
UPDATE_DOWNLOAD_ATTEMPTS = 3
UPDATE_CHECK_TIMEOUT = 20
UPDATE_DOWNLOAD_TIMEOUT = 30
RETRY_DELAYS = (0.5, 1.25)
UPDATE_LOG_MAX_BYTES = 1024 * 1024
UPDATE_INSTALL_ARGUMENTS = [
    "/SP-",
    "/SILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/TASKS=desktopicon,startmenuicon",
]


class UpdateError(Exception):
    def __init__(self, message, details="", **params):
        super().__init__(message)
        self.message = message
        self.params = params
        self.details = str(details or "").strip()


class _InvalidDownloadError(OSError):
    pass


class _RequestFailure(Exception):
    def __init__(self, url, error):
        super().__init__(str(error))
        self.url = str(url)
        self.error = error

    @property
    def retryable(self):
        error = self.error
        if isinstance(error, urllib.error.HTTPError):
            return error.code in {408, 425, 429} or 500 <= error.code <= 599
        return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


def version_parts(value):
    text = str(value or "").strip().lstrip("vV")
    numbers = [int(part) for part in re.findall(r"\d+", text)]
    return tuple(numbers or [0])


def compare_versions(left, right):
    left_parts = list(version_parts(left))
    right_parts = list(version_parts(right))
    size = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (size - len(left_parts)))
    right_parts.extend([0] * (size - len(right_parts)))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def normalize_repository(repository):
    value = str(repository or "").strip().strip("/")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.removesuffix(".git").strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        return ""
    return value


def _user_agent():
    return f"VideoMaker-Updater/{APP_VERSION}"


def _request(url, accept, timeout, headers=None):
    request_headers = {
        "Accept": accept,
        "User-Agent": _user_agent(),
        "Cache-Control": "no-cache",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        return urllib.request.urlopen(
            request,
            timeout=timeout,
            context=get_update_ssl_context(),
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        raise _RequestFailure(url, error) from error


def _iter_error_chain(error):
    current = error
    visited = set()
    while isinstance(current, BaseException) and id(current) not in visited:
        visited.add(id(current))
        yield current
        if isinstance(current, urllib.error.URLError) and isinstance(current.reason, BaseException):
            current = current.reason
            continue
        current = current.__cause__ or current.__context__


def _is_certificate_verification_failure(error):
    verification_error = getattr(ssl, "SSLCertVerificationError", ssl.SSLError)
    return any(isinstance(item, verification_error) for item in _iter_error_chain(error))


def _secure_connection_message(download=False):
    if download:
        return (
            "تعذر إنشاء اتصال آمن لتنزيل التحديث. تحقق من تاريخ ووقت الجهاز "
            "أو إعدادات برنامج الحماية ثم حاول مرة أخرى."
        )
    return (
        "تعذر إنشاء اتصال آمن بخادم التحديث. تحقق من تاريخ ووقت الجهاز "
        "أو إعدادات برنامج الحماية ثم حاول مرة أخرى."
    )


def _read_limited(response, maximum=4 * 1024 * 1024):
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Response exceeded the allowed size")
    return data


def _request_json_once(url):
    try:
        with _request(url, "application/vnd.github+json", UPDATE_CHECK_TIMEOUT) as response:
            raw = _read_limited(response)
        return json.loads(raw.decode("utf-8"))
    except _RequestFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _RequestFailure(url, error) from error


def request_json(url):
    """Compatibility wrapper retained for existing callers."""
    try:
        return _request_json_once(url)
    except _RequestFailure as failure:
        error = failure.error
        details = _single_failure_details("check", "github_api", 1, failure)
        if isinstance(error, urllib.error.HTTPError):
            if error.code == 404:
                raise UpdateError("لا توجد تحديثات متاحة حاليا", details=details)
            raise UpdateError(
                "تعذر الاتصال بخدمة التحديثات رمز الخطأ {error_code}",
                error_code=error.code,
                details=details,
            )
        if isinstance(error, (UnicodeDecodeError, json.JSONDecodeError, ValueError)):
            raise UpdateError("تعذر قراءة بيانات التحديث", details=details)
        if _is_certificate_verification_failure(error):
            raise UpdateError(_secure_connection_message(), details=details)
        raise UpdateError(
            "تعذر الاتصال بخادم التحديث. تأكد من اتصال الإنترنت ثم حاول مرة أخرى.",
            details=details,
        )


def _extract_latest_tag(final_url, page_text, repository):
    decoded_url = urllib.parse.unquote(str(final_url or ""))
    match = re.search(r"/releases/tag/([^/?#]+)", decoded_url, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    escaped_repository = re.escape(repository)
    match = re.search(
        rf'href=["\']/{escaped_repository}/releases/tag/([^"\'?#]+)',
        page_text or "",
        re.IGNORECASE,
    )
    if match:
        return urllib.parse.unquote(match.group(1)).strip()

    match = re.search(r"/releases/tag/([^\"'?#<>\s]+)", page_text or "", re.IGNORECASE)
    if match:
        return urllib.parse.unquote(match.group(1)).strip()
    return ""


def _request_release_page_once(repository):
    url = GITHUB_LATEST_PAGE.format(repository=repository)
    try:
        with _request(url, "text/html,application/xhtml+xml", UPDATE_CHECK_TIMEOUT) as response:
            final_url = response.geturl()
            raw = _read_limited(response)
    except _RequestFailure:
        raise
    except (ValueError, OSError) as error:
        raise _RequestFailure(url, error) from error

    page_text = raw.decode("utf-8", errors="replace")
    tag = _extract_latest_tag(final_url, page_text, repository)
    if not tag:
        raise _RequestFailure(
            url,
            ValueError("The latest release tag was not found in the GitHub release page"),
        )

    encoded_tag = urllib.parse.quote(tag, safe="")
    asset_name = ASSET_PATTERNS[0]
    encoded_asset = urllib.parse.quote(asset_name, safe="")
    return {
        "tag_name": tag,
        "name": tag,
        "body": "",
        "html_url": f"https://github.com/{repository}/releases/tag/{encoded_tag}",
        "assets": [
            {
                "name": asset_name,
                "browser_download_url": (
                    f"https://github.com/{repository}/releases/download/"
                    f"{encoded_tag}/{encoded_asset}"
                ),
            }
        ],
        "_source": "github_release_page",
    }


def _failure_reason(error):
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}: {error.reason}"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, BaseException):
            return f"{type(reason).__name__}: {reason}"
        return f"URLError: {reason}"
    return f"{type(error).__name__}: {error}"


def _diagnostic_line(phase, source, attempt, failure):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        f"[{timestamp}] phase={phase}; source={source}; attempt={attempt}; "
        f"url={failure.url}; error={_failure_reason(failure.error)}"
    )


def _format_diagnostics(lines, result="failed"):
    header = [
        "Video Maker update diagnostics",
        f"result={result}",
        f"app_version={APP_VERSION}",
        f"platform={sys.platform}",
        f"python={sys.version.split()[0]}",
        f"tls_trust={describe_update_ssl_context()}",
    ]
    return "\n".join(header + list(lines or []))


def _single_failure_details(phase, source, attempt, failure):
    return _format_diagnostics([_diagnostic_line(phase, source, attempt, failure)])


def _append_update_log(details):
    # Diagnostics are carried to the visible error dialog.  The central error
    # reporter stores them only when an error is actually shown, so successful
    # fallback/retry attempts never leave a log on the user's device.
    return None


def format_unexpected_error(phase, error):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    details = _format_diagnostics(
        [
            f"[{timestamp}] phase={phase}; source=internal; attempt=1; "
            f"error={type(error).__name__}: {error}"
        ]
    )
    _append_update_log(details)
    return details


def choose_asset(release, patterns):
    assets = release.get("assets") or []
    if not assets:
        return None
    lowered_patterns = [str(pattern).lower() for pattern in patterns if str(pattern).strip()]
    for pattern in lowered_patterns:
        for asset in assets:
            name = str(asset.get("name") or "")
            lower_name = name.lower()
            if lower_name == pattern or lower_name.endswith(pattern) or pattern in lower_name:
                return asset
    return assets[0]


def _release_from_available_source(repository):
    diagnostics = []
    failures = []
    api_url = GITHUB_API.format(repository=repository)

    for attempt in range(1, UPDATE_CHECK_ATTEMPTS + 1):
        try:
            release = _request_json_once(api_url)
            if not isinstance(release, dict) or not (release.get("tag_name") or release.get("name")):
                raise _RequestFailure(
                    api_url,
                    ValueError("The GitHub API response did not contain a release version"),
                )
            if diagnostics:
                _append_update_log(
                    _format_diagnostics(diagnostics, result="recovered_by_github_api")
                )
            release["_source"] = "github_api"
            return release
        except _RequestFailure as failure:
            failures.append(failure)
            diagnostics.append(_diagnostic_line("check", "github_api", attempt, failure))

        try:
            release = _request_release_page_once(repository)
            _append_update_log(
                _format_diagnostics(diagnostics, result="recovered_by_github_release_page")
            )
            return release
        except _RequestFailure as failure:
            failures.append(failure)
            diagnostics.append(
                _diagnostic_line("check", "github_release_page", attempt, failure)
            )

        if attempt < UPDATE_CHECK_ATTEMPTS:
            time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    details = _format_diagnostics(diagnostics)
    _append_update_log(details)
    message = "تعذر الاتصال بخادم التحديث. تأكد من اتصال الإنترنت ثم حاول مرة أخرى."
    if failures and all(
        _is_certificate_verification_failure(failure.error) for failure in failures
    ):
        message = _secure_connection_message()
    raise UpdateError(message, details=details)


def check_for_update():
    repository = normalize_repository(GITHUB_REPOSITORY)
    if not repository or "PUT_GITHUB_USER_HERE" in GITHUB_REPOSITORY:
        raise UpdateError("خدمة التحديث غير جاهزة حاليا")

    release = _release_from_available_source(repository)
    latest_version = release.get("tag_name") or release.get("name") or ""
    if not latest_version:
        details = _format_diagnostics(
            [
                f"phase=check; source={release.get('_source', 'unknown')}; "
                "error=Release version is missing"
            ]
        )
        _append_update_log(details)
        raise UpdateError("تعذر قراءة بيانات التحديث", details=details)

    asset = choose_asset(release, ASSET_PATTERNS)
    return {
        "repository": repository,
        "current_version": APP_VERSION,
        "latest_version": str(latest_version),
        "has_update": compare_versions(APP_VERSION, latest_version) < 0,
        "release_name": release.get("name") or str(latest_version),
        "release_notes": release.get("body") or "",
        "release_url": release.get("html_url")
        or f"https://github.com/{repository}/releases/latest",
        "asset_name": asset.get("name") if asset else "",
        "asset_url": asset.get("browser_download_url") if asset else "",
        "asset_size": asset.get("size") if asset else 0,
        "asset_digest": asset.get("digest") if asset else "",
        "update_source": release.get("_source") or "github_api",
    }


def _download_total(response, existing, status):
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    content_length = int(response.headers.get("Content-Length") or 0)
    if status == 206:
        return existing + content_length
    return content_length


def _prepare_partial_download(part_path, metadata_path, asset_url):
    stored_url = ""
    try:
        stored_url = metadata_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if part_path.exists() and stored_url != asset_url:
        try:
            part_path.unlink()
        except OSError:
            pass
    try:
        metadata_path.write_text(asset_url, encoding="utf-8")
    except OSError:
        pass


def _remove_partial_files(part_path, metadata_path):
    for path in (part_path, metadata_path):
        try:
            path.unlink()
        except OSError:
            pass


def _validate_downloaded_file(path, asset_name):
    if not path.exists() or path.stat().st_size <= 0:
        raise OSError("The downloaded update file is empty")
    suffix = Path(asset_name).suffix.lower()
    with path.open("rb") as handle:
        header = handle.read(8)
    if suffix == ".exe" and not header.startswith(b"MZ"):
        raise _InvalidDownloadError("The downloaded EXE file has an invalid header")
    if suffix == ".msi" and header != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise _InvalidDownloadError("The downloaded MSI file has an invalid header")
    if suffix == ".zip" and not header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        raise _InvalidDownloadError("The downloaded ZIP file has an invalid header")


def _safe_update_filename(name):
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip()
    return safe_name or "VideoMakerUpdate"


def _completed_metadata_path(destination):
    return destination.with_name(destination.name + ".json")


def _versioned_destination(asset_name, latest_version):
    safe_name = _safe_update_filename(asset_name)
    safe_version = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", str(latest_version or "")).strip("._")
    if not safe_version:
        return user_data_path("updates", safe_name)
    path = Path(safe_name)
    version_suffix = f"-{safe_version}"
    if path.stem.endswith(version_suffix):
        return user_data_path("updates", safe_name)
    return user_data_path("updates", f"{path.stem}{version_suffix}{path.suffix}")


def _download_matches_expected(path, update_info, metadata=None):
    asset_name = update_info.get("asset_name") or path.name
    try:
        _validate_downloaded_file(path, asset_name)
        expected_size = int(update_info.get("asset_size") or 0)
        if expected_size and path.stat().st_size != expected_size:
            return False
        if metadata:
            if metadata.get("asset_url") != update_info.get("asset_url"):
                return False
            if str(metadata.get("latest_version") or "") != str(update_info.get("latest_version") or ""):
                return False
        return True
    except (OSError, ValueError, _InvalidDownloadError):
        return False


def _read_completed_metadata(metadata_path):
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_completed_metadata(metadata_path, update_info):
    data = {
        "asset_url": update_info.get("asset_url") or "",
        "asset_name": update_info.get("asset_name") or "",
        "latest_version": update_info.get("latest_version") or "",
        "asset_size": update_info.get("asset_size") or 0,
        "asset_digest": update_info.get("asset_digest") or "",
    }
    try:
        metadata_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _available_completed_destination(destination, update_info):
    if not destination.exists():
        return destination

    completed_metadata = _read_completed_metadata(_completed_metadata_path(destination))
    if _download_matches_expected(destination, update_info, completed_metadata):
        return destination

    stem = destination.stem
    suffix = destination.suffix
    for index in range(1, 100):
        candidate = destination.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        completed_metadata = _read_completed_metadata(_completed_metadata_path(candidate))
        if _download_matches_expected(candidate, update_info, completed_metadata):
            return candidate

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return destination.with_name(f"{stem}-{timestamp}{suffix}")


def find_downloaded_update(update_info):
    asset_name = update_info.get("asset_name") or "VideoMakerUpdate"
    destination = _versioned_destination(asset_name, update_info.get("latest_version"))
    legacy_destination = user_data_path("updates", _safe_update_filename(asset_name))
    for existing_path in (destination, legacy_destination):
        completed_metadata_path = _completed_metadata_path(existing_path)
        completed_metadata = _read_completed_metadata(completed_metadata_path)
        if existing_path == legacy_destination and existing_path != destination and not completed_metadata:
            continue
        if existing_path.exists() and _download_matches_expected(existing_path, update_info, completed_metadata):
            return existing_path
    return None


def _path_modified_at(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def find_pending_downloaded_update():
    updates_dir = user_data_path("updates", ".keep").parent
    if not updates_dir.exists():
        return None
    for path in sorted(updates_dir.iterdir(), key=_path_modified_at, reverse=True):
        if not path.is_file() or path.suffix.lower() not in {".exe", ".msi", ".zip"}:
            continue
        metadata = _read_completed_metadata(_completed_metadata_path(path))
        latest_version = str(metadata.get("latest_version") or "")
        if not latest_version or compare_versions(APP_VERSION, latest_version) >= 0:
            continue
        update_info = {
            "current_version": APP_VERSION,
            "latest_version": latest_version,
            "has_update": True,
            "asset_name": metadata.get("asset_name") or path.name,
            "asset_url": metadata.get("asset_url") or "",
            "asset_size": metadata.get("asset_size") or 0,
            "asset_digest": metadata.get("asset_digest") or "",
            "downloaded_path": path,
            "update_source": "local_download",
        }
        if _download_matches_expected(path, update_info, metadata):
            return update_info
    return None


def update_install_id(path):
    update_path = Path(path)
    try:
        stat = update_path.stat()
    except OSError:
        return ""
    return f"{update_path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"


def update_download_id(update_info):
    return "|".join(
        str(update_info.get(key) or "")
        for key in ("latest_version", "asset_url", "asset_name")
    )


def delete_downloaded_update(path):
    update_path = Path(path)
    for cleanup_path in (
        update_path,
        _completed_metadata_path(update_path),
        update_path.with_name(update_path.name + ".part"),
        update_path.with_name(update_path.name + ".part.url"),
    ):
        try:
            cleanup_path.unlink()
        except OSError:
            pass


def _download_once(asset_url, part_path, progress_callback, cancel_callback):
    existing = part_path.stat().st_size if part_path.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing > 0 else {}

    with _request(
        asset_url,
        "application/octet-stream",
        UPDATE_DOWNLOAD_TIMEOUT,
        headers=headers,
    ) as response:
        status = int(getattr(response, "status", 0) or response.getcode() or 200)
        append = existing > 0 and status == 206
        if not append:
            existing = 0
        total = _download_total(response, existing, status)
        received = existing
        mode = "ab" if append else "wb"
        with part_path.open(mode) as handle:
            if progress_callback and total:
                progress_callback(min(100, int(received * 100 / total)))
            while True:
                if cancel_callback and cancel_callback():
                    raise UpdateError("تم إلغاء تنزيل التحديث")
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                if cancel_callback and cancel_callback():
                    raise UpdateError("تم إلغاء تنزيل التحديث")
                handle.write(chunk)
                received += len(chunk)
                if progress_callback and total:
                    progress_callback(min(100, int(received * 100 / total)))

    if not part_path.exists() or part_path.stat().st_size <= 0:
        raise OSError("The downloaded update file is empty")
    if total and part_path.stat().st_size < total:
        raise OSError(
            f"Incomplete update download: received {part_path.stat().st_size} "
            f"of {total} bytes"
        )


def _finalize_downloaded_part(part_path, destination, metadata_path, update_info):
    final_destination = _available_completed_destination(destination, update_info)
    if final_destination.exists():
        try:
            part_path.unlink()
        except OSError:
            pass
    else:
        os.replace(part_path, final_destination)

    try:
        metadata_path.unlink()
    except OSError:
        pass
    _write_completed_metadata(_completed_metadata_path(final_destination), update_info)
    return final_destination


def download_update(update_info, progress_callback=None, cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise UpdateError("تم إلغاء تنزيل التحديث")
    asset_url = update_info.get("asset_url")
    asset_name = update_info.get("asset_name") or "VideoMakerUpdate"
    if not asset_url:
        raise UpdateError("لا يوجد ملف تحديث قابل للتنزيل في هذا الإصدار")

    destination = _versioned_destination(asset_name, update_info.get("latest_version"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded_path = find_downloaded_update(update_info)
    if downloaded_path:
        if cancel_callback and cancel_callback():
            raise UpdateError("تم إلغاء تنزيل التحديث")
        if progress_callback:
            progress_callback(100)
        return downloaded_path

    part_path = destination.with_name(destination.name + ".part")
    metadata_path = destination.with_name(destination.name + ".part.url")
    _prepare_partial_download(part_path, metadata_path, asset_url)
    diagnostics = []

    for attempt in range(1, UPDATE_DOWNLOAD_ATTEMPTS + 1):
        if cancel_callback and cancel_callback():
            _remove_partial_files(part_path, metadata_path)
            raise UpdateError("تم إلغاء تنزيل التحديث")
        try:
            _download_once(asset_url, part_path, progress_callback, cancel_callback)
            _validate_downloaded_file(part_path, asset_name)
            final_destination = _finalize_downloaded_part(
                part_path,
                destination,
                metadata_path,
                update_info,
            )
            if progress_callback:
                progress_callback(100)
            if diagnostics:
                _append_update_log(
                    _format_diagnostics(diagnostics, result="download_recovered")
                )
            return final_destination
        except UpdateError:
            _remove_partial_files(part_path, metadata_path)
            raise
        except _InvalidDownloadError as error:
            failure = _RequestFailure(asset_url, error)
            diagnostics.append(
                _diagnostic_line("download", "release_asset", attempt, failure)
            )
            try:
                part_path.unlink()
            except OSError:
                pass
            if attempt >= UPDATE_DOWNLOAD_ATTEMPTS:
                break
        except _RequestFailure as failure:
            diagnostics.append(
                _diagnostic_line("download", "release_asset", attempt, failure)
            )
            if isinstance(failure.error, urllib.error.HTTPError) and failure.error.code == 416:
                _remove_partial_files(part_path, metadata_path)
                if attempt < UPDATE_DOWNLOAD_ATTEMPTS:
                    _prepare_partial_download(part_path, metadata_path, asset_url)
                    time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
                    continue
            if attempt >= UPDATE_DOWNLOAD_ATTEMPTS or not failure.retryable:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            failure = _RequestFailure(asset_url, error)
            diagnostics.append(
                _diagnostic_line("download", "release_asset", attempt, failure)
            )
            if isinstance(error, PermissionError):
                _remove_partial_files(part_path, metadata_path)
                if attempt < UPDATE_DOWNLOAD_ATTEMPTS:
                    _prepare_partial_download(part_path, metadata_path, asset_url)
                    time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
                    continue
            if attempt >= UPDATE_DOWNLOAD_ATTEMPTS:
                break

        time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    details = _format_diagnostics(diagnostics)
    _append_update_log(details)
    message = "تعذر تنزيل التحديث. لم يتم إجراء أي تغيير على البرنامج ويمكنك المحاولة مرة أخرى."
    if diagnostics and any("SSLCertVerificationError" in line for line in diagnostics):
        message = _secure_connection_message(download=True)
    raise UpdateError(message, details=details)


def _path_version_matches_current(path, metadata):
    latest_version = str(metadata.get("latest_version") or "")
    if latest_version and compare_versions(APP_VERSION, latest_version) == 0:
        return True
    current_parts = ".".join(str(part) for part in version_parts(APP_VERSION)[:3])
    stem = path.stem.lower()
    return bool(current_parts and re.search(rf"(^|[-_])v?{re.escape(current_parts)}($|[-_])", stem))


def cleanup_installed_update_files():
    updates_dir = user_data_path("updates", ".keep").parent
    if not updates_dir.exists():
        return
    for path in updates_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".exe", ".msi", ".zip"}:
            continue
        metadata_path = _completed_metadata_path(path)
        metadata = _read_completed_metadata(metadata_path)
        if not _path_version_matches_current(path, metadata):
            continue
        for cleanup_path in (
            path,
            metadata_path,
            path.with_name(path.name + ".part"),
            path.with_name(path.name + ".part.url"),
        ):
            try:
                cleanup_path.unlink()
            except OSError:
                pass


def get_update_install_arguments():
    from video_maker.app_state import get_language

    lang_code = get_language("ar")
    inno_lang = {"ar": "arabic", "en": "english", "fr": "french"}.get(lang_code, "arabic")
    return [*UPDATE_INSTALL_ARGUMENTS, f"/LANG={inno_lang}"]


def run_update_file(path):
    update_path = Path(path)
    if not update_path.exists():
        raise UpdateError("ملف التحديث غير موجود")
    args = [str(update_path), *get_update_install_arguments()]
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(args, close_fds=True, creationflags=creationflags)
    else:
        subprocess.Popen(args, close_fds=True)


cleanup_installed_update_files()
