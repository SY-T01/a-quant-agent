import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aquant import control
from aquant.domain import ValidationError
from aquant.lifecycle import WatchLock, active, request_stop


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {"instruments": [{"query": "159567", "type": "etf"}], "schedule": ["14:00"],
                    "state_dir": str(self.root / "state"), "start_date": "20240101", "model": "test"}
        self.path = self.root / "watch.json"
        self.path.write_text(json.dumps(self.cfg))

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_does_not_create_or_start_job(self):
        result = control.status(self.cfg)
        self.assertIn("已停止", result)
        self.assertFalse(Path(self.cfg["state_dir"]).exists())

    def test_status_identifies_owned_process(self):
        with WatchLock(self.cfg["state_dir"], "test"):
            self.assertIn("运行中", control.status(self.cfg))
        self.assertIn("已停止", control.status(self.cfg))

    def test_doctor_does_not_expose_credentials_or_use_api(self):
        values = {key: "PRIVATE-SECRET" for key in ("DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")}
        with patch("aquant.watch.credentials", return_value=values), patch("aquant.watch.synthesize") as model:
            text = control.doctor(self.path)
        self.assertIn("齐全", text)
        self.assertNotIn("PRIVATE-SECRET", text)
        model.assert_not_called()

    def test_schedule_validated_without_overwriting_rest(self):
        control.set_schedule(self.path, "14:10")
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["schedule"], ["14:10"])
        self.assertEqual(saved["instruments"], self.cfg["instruments"])
        with self.assertRaises(ValidationError):
            control.set_schedule(self.path, "26:00")
        self.assertEqual(json.loads(self.path.read_text()), saved)

    def test_recover_never_requeues_uncertain_mail(self):
        root = Path(self.cfg["state_dir"])
        root.mkdir()
        with sqlite3.connect(root / "watch.sqlite") as db:
            db.execute("CREATE TABLE jobs(status TEXT,error TEXT)")
            db.executemany("INSERT INTO jobs VALUES(?, '')", [(s,) for s in ("running", "sending", "unknown", "accepted")])
        with WatchLock(root, "test"):
            with self.assertRaises(ValidationError):
                control.recover(self.cfg)
        control.recover(self.cfg)
        with sqlite3.connect(root / "watch.sqlite") as db:
            self.assertEqual([r[0] for r in db.execute("SELECT status FROM jobs")], ["failed", "unknown", "unknown", "accepted"])

    def test_menu_exit_does_not_start_watch(self):
        with patch("builtins.input", return_value="0"), patch("aquant.watch.watch_main") as watcher, patch("builtins.print"):
            self.assertEqual(control.menu(self.path), 0)
        watcher.assert_not_called()

    def test_stop_cross_process_and_no_leftover_task(self):
        # A short-lived lock fixture, never a real watch/API/mail process.
        script = '''import sys,time
from aquant.lifecycle import WatchLock
with WatchLock(sys.argv[1], 'test-fixture') as lock:
 print('ready',flush=True)
 deadline=time.monotonic()+5
 while not lock.should_stop() and time.monotonic()<deadline: time.sleep(.02)
 print('stopped',flush=True)
'''
        child = subprocess.Popen([sys.executable, "-c", script, self.cfg["state_dir"]], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            self.assertIsNotNone(active(self.cfg["state_dir"]))
            self.assertIn("请求停止", request_stop(self.cfg["state_dir"]))
            out, _ = child.communicate(timeout=5)
            self.assertIn("stopped", out)
            self.assertIsNone(active(self.cfg["state_dir"]))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_offline_preview_uses_cache_without_model_or_smtp(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("需要可选pandas")
        folder = Path(self.cfg["state_dir"]) / "data"
        folder.mkdir(parents=True)
        frame = pd.DataFrame({"日期": pd.date_range("2026-01-01", periods=65).date,
                              "开盘": [1.] * 65, "收盘": [1.] * 65, "最高": [1.1] * 65,
                              "最低": [.9] * 65, "成交额": [1e6] * 65})
        frame.to_csv(folder / "etf-159567.qfq.csv", index=False)
        with patch("aquant.watch.synthesize") as model, patch("aquant.watch.smtp_daily") as sender:
            target = control.offline_preview(self.cfg)
        self.assertIn("旧缓存", target.read_text())
        model.assert_not_called()
        sender.assert_not_called()
