import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from stock_selector import launcher
from stock_selector.pdf_export import export_report

class PDFTests(unittest.TestCase):
    def test_menu_pdf_export_does_not_call_ai_or_update(self):
        runner=Mock(return_value=Mock(returncode=0))
        launcher.dispatch('pdf',runner=runner)
        self.assertEqual(runner.call_args_list[0].args[0][-1],'stock_selector.pdf_export')
        self.assertEqual(runner.call_args_list[1].args[0][0],'open')
        self.assertNotIn('env',runner.call_args_list[0].kwargs)
    def test_failed_pdf_worker_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'report.json';source.write_text('{}');output=Path(tmp)/'report.pdf';output.write_bytes(b'previous pdf')
            with patch('stock_selector.pdf_export.importlib.util.find_spec',return_value=None),patch('stock_selector.pdf_export.subprocess.run',return_value=Mock(returncode=1)):
                with self.assertRaises(RuntimeError):export_report(source,output)
            self.assertEqual(output.read_bytes(),b'previous pdf')

if __name__=='__main__':unittest.main()
