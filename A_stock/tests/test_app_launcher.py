import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_mac_app import ROOT, compile_launcher


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "native macOS launcher")
class AppLauncherTests(unittest.TestCase):
    def test_python_runs_in_same_process_using_project_venv(self):
        # A separate temporary project: no GUI, private settings, or mail task.
        with tempfile.TemporaryDirectory(prefix="aquant 本机 ' ") as tmp:
            root = Path(tmp)
            (root / ".venv").symlink_to(ROOT / ".venv", target_is_directory=True)
            program = ("import json,os,sys; import tkinter; "
                       "print(json.dumps(dict(pid=os.getpid(),executable=sys.executable,"
                       "cwd=os.getcwd(),stdin=sys.stdin.read(),umask=os.umask(0o077))))")
            executable = root / "AQuant"
            compile_launcher(executable, root, "-c", program)
            with subprocess.Popen([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                stdout, stderr = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, stderr.decode())
                result = json.loads((root / "runtime/desktop-app.log").read_text())
                # A child Python process would create another Dock application.
                self.assertEqual(result["pid"], child.pid)
                self.assertEqual(result["executable"], str(root / ".venv/bin/python"))
                self.assertEqual(result["cwd"], str(root.resolve()))
                self.assertEqual(result["stdin"], "")
                self.assertEqual(result["umask"], 0o077)
                self.assertEqual(stdout, b"")
                self.assertEqual((root / "runtime/desktop-app.log").stat().st_mode & 0o777, 0o600)
