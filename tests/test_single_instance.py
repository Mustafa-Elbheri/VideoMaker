import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_maker import single_instance


class SingleInstanceTest(unittest.TestCase):
    def test_close_request_matches_current_process_and_token_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_user_data_path(name):
                return root / name

            guard = single_instance.SingleInstanceGuard()
            guard.owned = True

            with patch.object(single_instance, "user_data_path", fake_user_data_path):
                request = {
                    "action": "replace_instance",
                    "request_id": "request-1",
                    "requester_pid": 12345,
                    "target_pid": single_instance.os.getpid(),
                    "target_token": guard.instance_token,
                }
                single_instance._write_json(single_instance.request_path(), request)

                self.assertEqual(guard.close_request_for_this_instance(), request)
                self.assertEqual(guard.close_request_for_this_instance(), {})

    def test_request_existing_instance_close_writes_targeted_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_user_data_path(name):
                return root / name

            guard = single_instance.SingleInstanceGuard()
            with patch.object(single_instance, "user_data_path", fake_user_data_path), patch.object(
                single_instance, "_post_close_to_process_windows", return_value=2
            ) as post_close:
                request = guard.request_existing_instance_close(777, "owner-token")
                saved = single_instance._read_json(single_instance.request_path())

            self.assertEqual(saved["action"], "replace_instance")
            self.assertEqual(saved["request_id"], request["request_id"])
            self.assertEqual(saved["target_pid"], 777)
            self.assertEqual(saved["target_token"], "owner-token")
            post_close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
