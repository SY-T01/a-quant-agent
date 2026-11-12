import json
import tempfile
import unittest
from pathlib import Path

from aquant.domain import ValidationError
from aquant.lifecycle import WatchLock, active, request_stop


class LifecycleTests(unittest.TestCase):
    def test_lock_rejects_second_instance_and_stop_is_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            with WatchLock(directory, "test") as first:
                self.assertEqual(active(directory)["instance"], first.instance)
                with self.assertRaises(ValidationError):
                    with WatchLock(directory, "test"):
                        pass
                self.assertIn("请求停止", request_stop(directory))
                self.assertTrue(first.should_stop())
            self.assertIsNone(active(directory))
            with WatchLock(directory, "test") as second:
                self.assertFalse(second.should_stop())

    def test_stale_pid_file_not_considered_running(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "watch.lock").write_text(json.dumps({"pid": 123, "instance": "old"}))
            self.assertIsNone(active(directory))
            self.assertIn("未运行", request_stop(directory))
