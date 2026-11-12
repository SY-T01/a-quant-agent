"""Exercise real Cocoa menu actions with temporary data and an offline worker."""
import ctypes
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aquant.desktop import DesktopApp
from aquant.desktop_model import DesktopModel, atomic_json
from aquant.lifecycle import active
from scripts.native_capture import capture_own_window


def native_click(menu_bar, tag):
    """Press the status button from Cocoa's loop and select from the open menu."""
    callback_type = ctypes.CFUNCTYPE(None)
    function = menu_bar.library.aquant_menubar_test_click
    function.argtypes = [ctypes.c_void_p, ctypes.c_int, callback_type]
    function.restype = ctypes.c_int
    assert function(menu_bar.handle, tag, menu_bar.qa_capture)


def main():
    output = ROOT / 'runtime/desktop-qa'
    output.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with tempfile.TemporaryDirectory(prefix='aquant-menu-qa-') as tmp:
        base = Path(tmp)
        config = base / 'config.json'
        atomic_json(config, {'state_dir': str(base / 'state'), 'secrets_file': str(base / 'secrets.json'),
            'instruments': [{'query': '159567', 'type': 'etf'}], 'schedule': ['14:00'],
            'model': 'offline-test', 'start_date': '20240101'})
        atomic_json(base / 'secrets.json', dict(DEEPSEEK_API_KEY='offline-test', SMTP_PASSWORD='offline-test',
            SMTP_HOST='smtp.example.invalid', SMTP_USER='test@example.invalid',
            SMTP_FROM='test@example.invalid', SMTP_TO='test@example.invalid'), private=True)
        model = DesktopModel(config)
        root = tk.Tk()
        root.withdraw()
        app = DesktopApp(root, model)
        assert app.install_menu_bar(os.environ.get('AQUANT_QA_MENU_LIBRARY'))
        menu = app.menubar
        menu_screenshots = []
        @ctypes.CFUNCTYPE(None)
        def capture_menu():
            # This callback runs inside native tracking: pixel I/O only, no Tk.
            try:
                path = output / f'menu-popup-{len(menu_screenshots)+1}.png'
                menu_screenshots.append(capture_own_window(str(path), layer=None))
            except Exception as exc:
                errors.append(str(exc))
        menu.qa_capture = capture_menu
        root.report_callback_exception = lambda kind, value, tb: fail(value)
        app.error = lambda exc: fail(exc)

        # The real watch loop and parent pipe run only against temporary files.
        # Its cycle is patched in the child before watch_main starts: no network.
        original_popen = subprocess.Popen
        child_script = ("import sys\nfrom unittest.mock import patch\nfrom aquant.watch import watch_main\n"
                        "with patch('aquant.watch.cycle', return_value='offline fixture'):\n"
                        " raise SystemExit(watch_main(sys.argv[1]))\n")

        def offline_worker(argv, **kwargs):
            return original_popen([sys.executable, '-u', '-c', child_script, str(config)], **kwargs)

        def fail(exc):
            errors.append(str(exc))
            app.close()

        def record(name):
            app.refresh_once()
            state = menu.snapshot()
            assert state['activation_policy'] == 1, state
            assert state['visible'], state
            if '--native-bundle-child' in sys.argv:
                assert state['bundle_id'] == 'local.aquant.assistant.qa', state
            checks.append({'check': name, 'window': root.state(), **state})
            return state

        def guard(fn):
            def call():
                try:
                    fn()
                except Exception as exc:
                    fail(exc)
            return call

        def await_condition(condition, done, timeout=6):
            deadline = time.monotonic() + timeout
            def poll():
                if condition():
                    done()
                elif time.monotonic() > deadline:
                    raise AssertionError('Native lifecycle check timed out')
                else:
                    root.after(100, guard(poll))
            root.after(100, guard(poll))

        def initial():
            state = record('launch_hidden_without_starting_report')
            assert root.state() == 'withdrawn'
            assert model.child is None and state['mode'] == 0
            native_click(menu, 1)
            await_condition(lambda: root.state() == 'normal', opened)

        def opened():
            state = record('open_window_from_native_status_button_without_dock')
            assert state['menu_open_count'] == 1 and state['menu_close_count'] == 1
            assert not state['menu_open']
            assert root.state() == 'normal'
            checks.append({'screenshot': capture_own_window(str(output / 'menu-bar-window.png'))})
            native_click(menu, 2)
            await_condition(lambda: bool(active(model.root())), started)

        def started():
            state = record('start_offline_report_from_native_menu')
            assert state['mode'] == 1 and not state['can_start'] and state['can_stop']
            app.close_window()
            root.after(500, guard(hidden))

        def hidden():
            state = record('close_window_keeps_worker_and_menu_alive')
            assert root.state() == 'withdrawn'
            assert model.child.poll() is None and active(model.root())
            assert state['mode'] == 1
            native_click(menu, 1)
            await_condition(lambda: root.state() == 'normal', reopened)

        def reopened():
            record('reopen_after_window_close')
            assert root.state() == 'normal'
            native_click(menu, 5)
            await_condition(lambda: (model.root() / 'manual-send-result.json').exists() and not model.manual_status()[0], manual_done)

        def manual_done():
            record('manual_send_from_native_menu_keeps_scheduled_worker')
            assert model.child.poll() is None
            assert 'offline fixture' in model.manual_status()[1]
            native_click(menu, 3)
            await_condition(lambda: model.child.poll() is not None, stopped)

        def stopped():
            state = record('stop_report_keeps_app_available')
            assert state['mode'] == 0 and state['can_start'] and not state['can_stop']
            assert active(model.root()) is None
            native_click(menu, 2)
            await_condition(lambda: bool(active(model.root())), quitting)

        def quitting():
            record('quit_menu_stops_owned_worker')
            native_click(menu, 4)

        root.after(20000, lambda: fail('Overall native menu test timed out'))
        root.after(250, guard(initial))
        with patch('aquant.desktop_model.subprocess.Popen', side_effect=offline_worker):
            root.mainloop()
        assert not menu.handle
        assert model.child is None or model.child.poll() is not None
        assert active(model.root()) is None
        assert len(menu_screenshots) == 7, menu_screenshots
        checks.append({'check': 'exit_removes_menu_and_leaves_no_worker', 'passed': True})
    result = {'checks': checks, 'errors': errors, 'native_button_presses': len(menu_screenshots),
              'menu_screenshots': menu_screenshots, 'real_accounts_used': False, 'network_used': False}
    (output / 'menu-bar-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'checks': len(checks), 'errors': errors, 'network_used': False}, ensure_ascii=False))
    return bool(errors)


def bundled_main():
    from scripts.build_mac_app import compile_launcher, compile_statusbar
    result_path = ROOT / 'runtime/desktop-qa/menu-bar-results.json'
    result_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='aquant-menu-bundle-') as tmp:
        app = Path(tmp) / 'A-Quant QA.app'
        contents = app / 'Contents'
        resources = contents / 'Resources'
        resources.mkdir(parents=True)
        original = ROOT / 'A-Quant.app/Contents'
        info = plistlib.loads((original / 'Info.plist').read_bytes())
        info.update(CFBundleIdentifier='local.aquant.assistant.qa', CFBundleDisplayName='A-Quant 离线验证')
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        shutil.copy2(original / 'Resources/AppIcon.icns', resources / 'AppIcon.icns')
        compile_statusbar(resources / 'libAQuantMenuBar.dylib', testing=True)
        entry = ('import runpy,sys,os; sys.argv=["menu_bar_smoke.py","--native-bundle-child"]; '
                 f'os.environ["AQUANT_QA_MENU_LIBRARY"]={str(resources / "libAQuantMenuBar.dylib")!r}; '
                 f'runpy.run_path({str(ROOT / "scripts/menu_bar_smoke.py")!r},run_name="__main__")')
        compile_launcher(contents / 'MacOS/AQuant', ROOT, '-c', entry)
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
        subprocess.run(['/usr/bin/open', '-W', '-n', str(app)], timeout=40, check=True)
        result = json.loads(result_path.read_text())
        assert not result['errors'], result['errors']
        assert all(check.get('bundle_id', 'local.aquant.assistant.qa') == 'local.aquant.assistant.qa'
                   for check in result['checks'])
        assert result['native_button_presses'] == 7
        print(json.dumps({'native_bundle': 'passed', 'checks': len(result['checks']),
                          'native_button_presses': result['native_button_presses'], 'network_used': False}))
    return 0


if __name__ == '__main__':
    # The QA-only button driver lives in the temporary test component.
    raise SystemExit(main() if '--native-bundle-child' in sys.argv else bundled_main())
