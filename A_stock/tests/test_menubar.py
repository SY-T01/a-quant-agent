import unittest
from unittest.mock import Mock, patch

from aquant.desktop import DesktopApp
from aquant.menubar import MacMenuBar, menu_state


class MenuStateTests(unittest.TestCase):
    def test_stopped_menu_does_not_claim_the_report_is_running(self):
        state = menu_state({"active": None, "owned": False, "schedule": "14:00"})
        self.assertEqual(state.mode, 0)
        self.assertIn("已停止", state.status)
        self.assertTrue(state.can_start)
        self.assertFalse(state.can_stop)
        self.assertIn("14:00", state.schedule)

    def test_owned_starting_child_already_blocks_a_second_start(self):
        state = menu_state({"active": None, "owned": True})
        self.assertEqual(state.mode, 1)
        self.assertFalse(state.can_start)
        self.assertTrue(state.can_stop)

    def test_external_task_cannot_be_stopped_by_menu(self):
        state = menu_state({"active": {"pid": 1234}, "owned": False})
        self.assertIn("终端", state.status)
        self.assertFalse(state.can_start)
        self.assertFalse(state.can_stop)

    def test_stopping_and_quitting_disable_actions(self):
        for closing in (False, True):
            state = menu_state({"owned": True}, closing=closing, stopping=True)
            self.assertEqual(state.mode, 2)
            self.assertFalse(state.can_start)
            self.assertFalse(state.can_stop)
            self.assertEqual(state.can_quit, not closing)

    def test_unknown_state_is_not_a_green_running_indicator(self):
        state = menu_state({}, failed=True)
        self.assertEqual(state.mode, 3)
        self.assertIn("异常", state.status)
        self.assertFalse(state.can_start)


class WindowLifecycleTests(unittest.TestCase):
    def app(self):
        app = DesktopApp.__new__(DesktopApp)
        app.root, app.model, app.menubar = Mock(), Mock(), Mock()
        app.closing = False
        app.note = Mock()
        return app

    def test_window_close_only_hides_and_keeps_owned_task_alive(self):
        app = self.app()
        app.close_window()
        app.root.withdraw.assert_called_once()
        app.model.stop_owned.assert_not_called()
        app.root.destroy.assert_not_called()
        app.menubar.close.assert_not_called()

    def test_exit_requests_stop_and_waits_for_owned_child(self):
        app = self.app()
        app.model.child.poll.return_value = None
        with patch.object(app, "wait_close") as wait:
            app.close()
        app.model.stop_owned.assert_called_once()
        wait.assert_called_once()
        app.root.destroy.assert_not_called()
        app.menubar.close.assert_not_called()

    def test_exit_without_owned_task_removes_menu_and_window(self):
        app = self.app()
        app.model.child = None
        menu = app.menubar
        app.close()
        menu.close.assert_called_once()
        app.root.destroy.assert_called_once()
        app.model.stop_owned.assert_not_called()

    def test_native_actions_are_processed_only_by_normal_tk_poll(self):
        menu = MacMenuBar.__new__(MacMenuBar)
        menu.root, menu.handle, menu.library = Mock(), 1, Mock()
        menu.poll_id = None
        action = Mock()
        menu.actions = {"start": action}
        menu.library.aquant_menubar_next_action.side_effect = [2, 0]
        action.assert_not_called()
        menu._poll_actions()
        action.assert_called_once()
        menu.root.after.assert_called_once_with(75, menu._poll_actions)

    def test_quit_during_poll_does_not_schedule_more_work(self):
        menu = MacMenuBar.__new__(MacMenuBar)
        menu.root, menu.handle, menu.library = Mock(), 1, Mock()
        menu.poll_id = None
        menu.actions = {"quit": menu.close}
        menu.library.aquant_menubar_next_action.return_value = 4
        menu._poll_actions()
        self.assertIsNone(menu.handle)
        menu.library.aquant_menubar_close.assert_called_once_with(1)
        menu.root.after.assert_not_called()

    def test_close_cancels_poll_and_frees_native_handle_once(self):
        menu = MacMenuBar.__new__(MacMenuBar)
        menu.root, menu.handle, menu.library = Mock(), 1, Mock()
        menu.poll_id = 'timer-1'
        menu.close()
        menu.close()
        menu.root.after_cancel.assert_called_once_with('timer-1')
        menu.library.aquant_menubar_close.assert_called_once_with(1)

    def test_menu_failure_leaves_a_visible_usable_window(self):
        app = self.app()
        app.menubar = None
        with patch("aquant.desktop.sys.platform", "darwin"), patch("aquant.desktop.MacMenuBar", side_effect=OSError):
            self.assertFalse(app.install_menu_bar("missing.dylib"))
        app.root.deiconify.assert_called_once()
        app.model.start.assert_not_called()
