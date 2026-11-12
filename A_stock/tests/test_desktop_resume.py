import os
import unittest
from unittest.mock import Mock, patch

from aquant.desktop import resume_daily_once


class MigrationResumeTests(unittest.TestCase):
    def app(self):
        app = Mock(closing=False)
        app.model.state.return_value = {"active": None, "owned": False}
        return app

    def test_normal_open_never_starts_daily_task(self):
        with patch.dict(os.environ, {}, clear=True):
            app = self.app()
            resume_daily_once(app)
            app.start.assert_not_called()
            app.model.state.assert_not_called()

    def test_explicit_resume_is_consumed_before_child_starts(self):
        with patch.dict(os.environ, {"AQUANT_RESUME_DAILY_ONCE": "1"}, clear=True):
            app = self.app()
            app.start.side_effect = lambda: self.assertNotIn("AQUANT_RESUME_DAILY_ONCE", os.environ)
            resume_daily_once(app)
            resume_daily_once(app)
            app.start.assert_called_once()

    def test_running_or_starting_task_is_not_duplicated(self):
        for state in ({"active": {"pid": 123}, "owned": False}, {"active": None, "owned": True}):
            with self.subTest(state=state), patch.dict(os.environ, {"AQUANT_RESUME_DAILY_ONCE": "1"}, clear=True):
                app = self.app()
                app.model.state.return_value = state
                resume_daily_once(app)
                app.start.assert_not_called()
                self.assertNotIn("AQUANT_RESUME_DAILY_ONCE", os.environ)

    def test_smoke_test_and_closing_app_never_resume(self):
        for smoke_test, closing in ((True, False), (False, True)):
            with self.subTest(smoke_test=smoke_test, closing=closing), patch.dict(os.environ, {"AQUANT_RESUME_DAILY_ONCE": "1"}, clear=True):
                app = self.app()
                app.closing = closing
                resume_daily_once(app, smoke_test=smoke_test)
                app.start.assert_not_called()
                self.assertNotIn("AQUANT_RESUME_DAILY_ONCE", os.environ)

    def test_bad_config_does_not_start_and_reports_error(self):
        with patch.dict(os.environ, {"AQUANT_RESUME_DAILY_ONCE": "1"}, clear=True):
            app = self.app()
            error = FileNotFoundError("configuration")
            app.model.state.side_effect = error
            resume_daily_once(app)
            app.start.assert_not_called()
            app.error.assert_called_once_with(error)
