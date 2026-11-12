import contextlib
import io
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from stock_selector import launcher

class LauncherTests(unittest.TestCase):
    def setup_report(self,tmp):
        root=Path(tmp);report=root/'reports/sector_research/index.html';report.parent.mkdir(parents=True);report.write_text('report');return root
    def test_view_only_opens_existing_report_without_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.setup_report(tmp);runner=Mock(return_value=Mock(returncode=0))
            with patch.object(launcher,'PROJECT',root):self.assertEqual(launcher.dispatch('view',runner=runner),0)
            self.assertEqual(runner.call_count,1);self.assertEqual(runner.call_args.args[0][0],'open')
    def test_update_failure_never_opens_a_new_report(self):
        runner=Mock(return_value=Mock(returncode=1))
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(launcher.dispatch('update',runner=runner),1)
        self.assertEqual(runner.call_count,1)
        self.assertEqual(runner.call_args.args[0][-1],'stock_selector.weekly')
    def test_invalid_key_never_calls_paid_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.setup_report(tmp);runner=Mock()
            with patch.object(launcher,'PROJECT',root),patch.dict('os.environ',{},clear=True),contextlib.redirect_stdout(io.StringIO()) as text:
                self.assertEqual(launcher.dispatch('ai',runner=runner,prompt=lambda p:'中文密钥'),1)
            runner.assert_not_called();self.assertNotIn('中文密钥',text.getvalue())
    def test_valid_key_only_passed_to_ai_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.setup_report(tmp);runner=Mock(return_value=Mock(returncode=0))
            with patch.object(launcher,'PROJECT',root),patch.dict('os.environ',{},clear=True),contextlib.redirect_stdout(io.StringIO()) as text:
                launcher.dispatch('ai',runner=runner,prompt=lambda p:'secret-test')
            self.assertEqual(runner.call_args_list[0].kwargs['env']['DEEPSEEK_API_KEY'],'secret-test')
            self.assertNotIn('env',runner.call_args_list[1].kwargs);self.assertNotIn('secret-test',text.getvalue())
            self.assertFalse(list(root.rglob('.env*')))
    def test_replay_does_not_request_key_or_call_ai(self):
        runner=Mock(return_value=Mock(returncode=0));prompt=Mock()
        launcher.dispatch('replay',runner=runner,prompt=prompt)
        prompt.assert_not_called()
        self.assertIn('--replay-ai',runner.call_args_list[0].args[0])
        self.assertNotIn('--ai',runner.call_args_list[0].args[0])
    def test_application_bundle_and_single_visible_entry(self):
        root=Path(__file__).resolve().parents[1]
        app=root/'股票研究助手.app/Contents'
        with (app/'Info.plist').open('rb') as f:info=plistlib.load(f)
        self.assertTrue((app/'MacOS'/info['CFBundleExecutable']).is_file())
        self.assertTrue((root/'.launcher/启动.command').is_file())
        self.assertFalse(list(root.glob('*.command')))

if __name__=='__main__':unittest.main()
