import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from aquant.desktop_model import DesktopModel, atomic_json
from aquant.domain import ValidationError
from aquant.lifecycle import WatchLock, active
from aquant.menubar import menu_state
from aquant.watch import manual_requested


class ManualSendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'config.json'
        self.secrets = self.root / 'secret.json'
        atomic_json(self.secrets, {k: 'offline-test' for k in ('DEEPSEEK_API_KEY','SMTP_HOST','SMTP_USER','SMTP_PASSWORD','SMTP_FROM','SMTP_TO')}, private=True)
        atomic_json(self.path, dict(state_dir=str(self.root/'state'), secrets_file=str(self.secrets), instruments=[dict(query='159567',type='etf')], schedule=['14:00'], start_date='20240101',model='test'))
        self.model = DesktopModel(self.path)

    def test_stopped_task_starts_only_one_owned_once_process(self):
        child = Mock(pid=999999)
        child.poll.return_value = None
        with patch('aquant.desktop_model.subprocess.Popen', return_value=child) as popen:
            self.model.send_now()
            args = popen.call_args.args[0]
            self.assertIn('--once', args)
            self.assertIn('--retry-failed', args)
            with self.assertRaises(ValidationError):
                self.model.send_now()
            self.assertEqual(popen.call_count, 1)
        child.poll.return_value = 0
        self.model.release()

    def test_external_task_cannot_receive_commands(self):
        with WatchLock(self.model.root(), 'scheduled'):
            with self.assertRaises(ValidationError):
                self.model.send_now()
            self.assertFalse((self.model.root()/'manual-send-request.json').exists())

    def test_command_bound_to_instance_and_duplicate_rejected(self):
        with WatchLock(self.model.root(), 'scheduled') as lock:
            self.model.child = Mock(pid=os.getpid())
            self.model.child.poll.return_value = None
            self.model.send_now()
            self.assertTrue(manual_requested(self.model.root(), lock.instance))
            self.assertFalse(manual_requested(self.model.root(), 'different-instance'))
            with self.assertRaises(ValidationError):
                self.model.send_now()

    def test_menu_disables_busy_external_and_closing(self):
        self.assertTrue(menu_state({'owned':False}).can_manual)
        for state in ({'manual_busy':True}, {'active':{'pid':1},'owned':False}):
            self.assertFalse(menu_state(state).can_manual)
        self.assertFalse(menu_state({}, closing=True).can_manual)

    def test_live_scheduled_worker_handles_manual_and_continues_offline(self):
        program = '''import sys
from unittest.mock import patch
from aquant.watch import watch_main
def offline_cycle(*args, **kwargs):
 return '未到发送时间，无操作' if kwargs['scheduled'] else '离线验证完成：未联网未发信'
with patch('aquant.watch.cycle', side_effect=offline_cycle):
 watch_main(sys.argv[1])
'''
        child = subprocess.Popen([sys.executable, '-B', '-c', program, str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, AQUANT_PARENT_PIPE='1'))
        self.model.child = child
        def cleanup():
            if child.poll() is None:
                child.stdin.close()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        self.addCleanup(cleanup)
        for _ in range(60):
            if active(self.model.root()): break
            time.sleep(.05)
        self.assertIsNotNone(active(self.model.root()))
        self.model.send_now()
        result = self.model.root()/'manual-send-result.json'
        for _ in range(80):
            if result.exists() and not self.model.manual_status()[0]: break
            time.sleep(.05)
        self.assertIn('离线验证完成', self.model.manual_status()[1])
        self.assertIsNone(child.poll())
        self.assertEqual(active(self.model.root())['pid'], child.pid)
        self.assertFalse((self.model.root()/'manual-send-request.json').exists())
        self.model.stop_owned()
        child.wait(timeout=5)
        self.assertIsNone(active(self.model.root()))
