import json
import tempfile
import unittest
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from aquant.desktop_model import DesktopModel, atomic_json
from aquant.domain import ValidationError
from aquant.lifecycle import WatchLock


class DesktopModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.secret = self.root / "secrets.json"
        self.values = {k: "private-test" for k in ("DEEPSEEK_API_KEY", "SMTP_PASSWORD")}
        self.values.update({"SMTP_HOST": "smtp.163.com", "SMTP_USER": "test@163.com", "SMTP_FROM": "test@163.com", "SMTP_TO": "test@gmail.com"})
        atomic_json(self.secret, self.values, private=True)
        self.path = self.root / "config.json"
        atomic_json(self.path, {"state_dir": str(self.root / "state"), "secrets_file": str(self.secret),
                                "instruments": [{"query": "159567", "type": "etf"}], "schedule": ["14:00"],
                                "model": "test", "start_date": "20240101"})
        self.model = DesktopModel(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_constructing_model_never_starts_task(self):
        with patch("subprocess.Popen") as child:
            model = DesktopModel(self.path)
            self.assertIsNone(model.state()["active"])
        child.assert_not_called()

    def test_save_position_maintains_four_decimals_and_watchlist(self):
        self.model.save_position("159567", "etf", 4500, "0.8723")
        self.assertEqual(self.model.positions()[0]["cost"], "0.8723")
        self.model.save_position("510300", "etf", 100, "4.1234")
        self.assertEqual(len(self.model.config()["instruments"]), 2)

    def test_invalid_position_does_not_add_watch_symbol(self):
        original = self.path.read_text()
        with self.assertRaises(ValidationError):
            self.model.save_position("510300", "etf", -1, "4")
        self.assertEqual(self.path.read_text(), original)

    def test_name_lookup_runs_in_background_without_starting_daily_or_sending(self):
        self.model.save_position("159567", "etf", 4500, "0.8723")
        entered, finish = threading.Event(), threading.Event()
        def lookup(directory):
            entered.set()
            finish.wait(3)
            return "名称已记住"
        try:
            with patch("aquant.positions.complete_names", side_effect=lookup), patch("subprocess.Popen") as popen:
                self.model.update_names()
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.model.names_busy())
                with self.assertRaises(ValidationError):
                    self.model.update_names()
                finish.set()
                self.model.name_thread.join(2)
                self.assertFalse(self.model.names_busy())
                self.assertEqual(self.model.name_message, "名称已记住")
                popen.assert_not_called()
                self.assertIsNone(self.model.child)
        finally:
            finish.set()
            if self.model.name_thread:
                self.model.name_thread.join(2)

    def test_settings_never_prefill_secret_fields(self):
        values = self.model.settings()
        self.assertNotIn("DEEPSEEK_API_KEY", values)
        self.assertNotIn("SMTP_PASSWORD", values)
        self.assertNotIn("private-test", json.dumps(values))

    def test_saving_blank_secret_keeps_existing_and_mode_600(self):
        values = self.model.settings()
        values.update({"DEEPSEEK_API_KEY": "", "SMTP_PASSWORD": "", "schedule": "14:10"})
        self.model.save_settings(values)
        self.assertEqual(json.loads(self.secret.read_text())["DEEPSEEK_API_KEY"], "private-test")
        self.assertEqual(self.secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.model.config()["schedule"], ["14:10"])

    def test_invalid_settings_leave_config_and_credentials_unchanged(self):
        before = (self.path.read_text(), self.secret.read_text())
        values = self.model.settings()
        values["SMTP_TO"] = "invalid address"
        with self.assertRaises(ValidationError):
            self.model.save_settings(values)
        self.assertEqual(before, (self.path.read_text(), self.secret.read_text()))

    def test_start_only_explicit_and_parent_pipe_passed(self):
        child = Mock(pid=1234)
        child.poll.return_value = None
        child.stdin.closed = False
        with patch("subprocess.Popen", return_value=child) as popen:
            self.model.start()
        self.assertEqual(popen.call_args.kwargs["env"]["AQUANT_PARENT_PIPE"], "1")
        self.assertIsNotNone(popen.call_args.kwargs["stdin"])
        self.model.stop_owned()
        child.stdin.close.assert_called_once()
        child.poll.return_value = 0
        self.model.release()

    def test_external_task_not_stopped_on_software_exit(self):
        with WatchLock(self.model.root(), "external") as lock:
            self.assertIn("没有", self.model.stop_owned())
            self.assertFalse(lock.should_stop())
            with self.assertRaises(ValidationError):
                self.model.start()

    def test_pending_child_stop_does_not_stop_external_winner(self):
        child = Mock(pid=987654)
        child.poll.return_value = None
        child.stdin.closed = False
        self.model.child = child
        with WatchLock(self.model.root(), "external") as lock:
            self.model.stop_owned()
            self.assertFalse(lock.should_stop())
        child.stdin.close.assert_called_once()

    def test_parent_pipe_eof_stops_watch_process_without_network(self):
        script = '''import sys
from unittest.mock import patch
from aquant.watch import watch_main
with patch('aquant.watch.cycle',return_value='未到发送时间，无操作'):
 raise SystemExit(watch_main(sys.argv[1]))
'''
        env = dict(os.environ, AQUANT_PARENT_PIPE="1")
        child = subprocess.Popen([sys.executable, "-c", script, str(self.path)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            self.assertIn("当前终端启动", child.stdout.readline())
            child.stdin.close()
            child.wait(timeout=6)
            self.assertEqual(child.returncode, 0)
            self.assertIn("本实例已停止", child.stdout.read())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=6)
            for stream in (child.stdin, child.stdout, child.stderr):
                if not stream.closed:
                    stream.close()
