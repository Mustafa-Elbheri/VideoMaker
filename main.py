import sys

from video_maker.audio_effect_worker import WORKER_FLAG, main as audio_effect_worker_main

if WORKER_FLAG in sys.argv:
    raise SystemExit(audio_effect_worker_main(sys.argv))

import wx

from video_maker.player import VideoPlayer
from video_maker.problem_log import append_problem
from video_maker.single_instance import ensure_single_instance
from video_maker.themes import install_theme_hooks
from video_maker.usage_consent import show_usage_consent_if_needed


def main():
    single_instance_guard = ensure_single_instance()
    if single_instance_guard is None:
        return
    try:
        app = wx.App()
        install_theme_hooks()
        if not show_usage_consent_if_needed():
            return
        player = VideoPlayer(None)
        player.attach_single_instance_guard(single_instance_guard)
        player.Show()
        from video_maker.navigation_sounds import install_navigation_sounds_hook
        install_navigation_sounds_hook()
        app.MainLoop()
    except Exception as error:
        append_problem("main_loop", str(error), exception=error)
        raise
    finally:
        single_instance_guard.release()


if __name__ == "__main__":
    main()
