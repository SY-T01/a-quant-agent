"""Small ctypes bridge to the bundled native macOS menu bar, without PyObjC."""
from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MenuState:
    mode: int
    status: str
    schedule: str
    can_start: bool
    can_stop: bool
    can_quit: bool
    can_manual: bool = False


def menu_state(state, *, closing=False, stopping=False, failed=False):
    owned = bool(state.get("owned"))
    running = bool(state.get("active") or owned)
    pending = closing or (stopping and owned)
    if pending:
        mode, label = 2, "正在退出" if closing else "停止中"
    elif failed:
        mode, label = 3, "状态读取异常，请打开主窗口检查"
    elif running:
        mode, label = 1, "运行中" if owned else "终端任务运行中"
    else:
        mode, label = 0, "已停止"
    return MenuState(mode, "自动日报：" + label,
                     "交易日 " + state.get("schedule", "—") + "（北京时间）",
                     not running and not pending and not failed,
                     owned and not pending, not closing,
                     not pending and not failed and not state.get("manual_busy") and
                     not state.get("manual_external") and not (running and not owned))


class MacMenuBar:
    ACTIONS = {1: "show", 2: "start", 3: "stop", 4: "quit", 5: "manual"}

    def __init__(self, library, root, actions):
        self.root = root
        self.actions = actions
        self.library = ctypes.CDLL(str(Path(library)))
        self.handle = None
        self.poll_id = None
        lib = self.library
        lib.aquant_menubar_create.argtypes = []
        lib.aquant_menubar_create.restype = ctypes.c_void_p
        lib.aquant_menubar_next_action.argtypes = [ctypes.c_void_p]
        lib.aquant_menubar_next_action.restype = ctypes.c_int
        lib.aquant_menubar_update.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 5 + [ctypes.c_char_p] * 2
        lib.aquant_menubar_update.restype = None
        lib.aquant_menubar_activate.argtypes = [ctypes.c_void_p]
        lib.aquant_menubar_activate.restype = None
        lib.aquant_menubar_snapshot.argtypes = [ctypes.c_void_p]
        lib.aquant_menubar_snapshot.restype = ctypes.c_char_p
        lib.aquant_menubar_close.argtypes = [ctypes.c_void_p]
        lib.aquant_menubar_close.restype = None
        lib.aquant_menubar_restore_dock.argtypes = []
        lib.aquant_menubar_restore_dock.restype = None
        self.handle = lib.aquant_menubar_create()
        if not self.handle:
            lib.aquant_menubar_restore_dock()
            raise RuntimeError("无法创建菜单栏入口")
        self.poll_id = self.root.after(75, self._poll_actions)

    def _poll_actions(self):
        # Only a normal Tk timer can enter Tk safely. A Cocoa callback can run
        # inside nextEvent while _tkinter owns its non-recursive Tcl mutex.
        self.poll_id = None
        try:
            for _ in range(16):
                if not self.handle:
                    break
                action = self.library.aquant_menubar_next_action(self.handle)
                if not action:
                    break
                handler = self.actions.get(self.ACTIONS.get(action))
                if handler:
                    handler()
        finally:
            if self.handle:
                self.poll_id = self.root.after(75, self._poll_actions)

    def update(self, state):
        if self.handle:
            self.library.aquant_menubar_update(self.handle, state.mode, state.can_start,
                state.can_stop, state.can_quit, state.can_manual, state.status.encode(), state.schedule.encode())

    def activate(self):
        if self.handle:
            self.library.aquant_menubar_activate(self.handle)

    def snapshot(self):
        value = self.library.aquant_menubar_snapshot(self.handle) if self.handle else None
        return json.loads(value) if value else None

    def close(self):
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if self.handle:
            self.library.aquant_menubar_close(self.handle)
            self.handle = None

    def restore_dock(self):
        self.library.aquant_menubar_restore_dock()
