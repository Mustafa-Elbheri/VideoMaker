import unittest
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath('.'))

from video_maker.app_paths import ffmpeg_binary
from video_maker.timeline import TimelineSegment
from video_maker.video_editing import build_visual_transition_segment


class VisualTransitionSegmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = ffmpeg_binary()
        cls.work = tempfile.mkdtemp(prefix='test_transition_')
        cls.src = os.path.join(cls.work, 'src.mp4')
        result = subprocess.run(
            [cls.ffmpeg, '-y', '-loglevel', 'error',
             '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=24:duration=3',
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', cls.src],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(cls.src):
            raise unittest.SkipTest('Could not create test source video')

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_returns_path_tempdir_duration(self):
        """Worker contract: (path, temp_dir, new_duration) for timeline transforms."""
        timeline = [TimelineSegment(self.src, 0.0, 3.0)]
        result = build_visual_transition_segment(timeline, 0.5, 2.5, 'fadeout', None, lambda: False, 1.0)
        self.assertEqual(len(result), 3)
        path, temp_dir, new_duration = result
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.getsize(path) > 0)
        self.assertGreater(float(new_duration), 0.0)
        self.assertTrue(os.path.isdir(temp_dir))

    def test_honors_cancellation(self):
        """Cancellation raises OperationCancelled instead of returning a list."""
        from video_maker.operation_control import OperationCancelled
        timeline = [TimelineSegment(self.src, 0.0, 3.0)]
        with self.assertRaises(OperationCancelled):
            build_visual_transition_segment(timeline, 0.5, 2.5, 'mirror', None, lambda: True, 1.0)


if __name__ == '__main__':
    unittest.main()
