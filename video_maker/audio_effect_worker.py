from __future__ import annotations

import json
import os
import sys
import traceback


WORKER_FLAG = "--video-maker-audio-effect-worker"


def _append_progress(path, percent):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as progress_file:
            progress_file.write(f"{float(percent):.6f}\n")
            progress_file.flush()
    except OSError:
        pass


def _write_text(path, text):
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8", errors="ignore") as output_file:
            output_file.write(str(text or ""))
    except OSError:
        pass


def _install_fatal_log(path):
    if not path:
        return None
    try:
        import faulthandler

        fatal_file = open(path, "a", encoding="utf-8", errors="ignore")
        faulthandler.enable(file=fatal_file, all_threads=True)
        return fatal_file
    except Exception:
        return None


def _payload_path(argv):
    args = list(argv or sys.argv)
    if WORKER_FLAG in args:
        index = args.index(WORKER_FLAG)
        if index + 1 < len(args):
            return args[index + 1]
    if len(args) >= 2:
        return args[1]
    return ""


def main(argv=None):
    payload_file = _payload_path(argv)
    if not payload_file:
        return 2
    try:
        with open(payload_file, "r", encoding="utf-8") as input_file:
            payload = json.load(input_file)
    except Exception:
        return 2

    fatal_file = _install_fatal_log(payload.get("fatal_path", ""))
    try:
        from video_maker.audio_effects import process_audio_with_pedalboard

        process_audio_with_pedalboard(
            payload["input_path"],
            payload["audio_path"],
            payload["audio_filter"],
            lambda percent: _append_progress(payload.get("progress_path", ""), percent),
            None,
        )
        _append_progress(payload.get("progress_path", ""), 100)
        return 0
    except Exception:
        _write_text(payload.get("error_path", ""), traceback.format_exc())
        return 1
    finally:
        if fatal_file is not None:
            try:
                import faulthandler

                faulthandler.disable()
            except Exception:
                pass
            try:
                fatal_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
