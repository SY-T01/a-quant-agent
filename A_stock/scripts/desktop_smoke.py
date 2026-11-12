"""Exercise native widgets in a temporary profile; no API, mail or watch startup."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import tkinter as tk
from unittest.mock import Mock, patch
from native_capture import capture_own_window

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aquant.desktop import DesktopApp
from aquant.desktop_model import DesktopModel, atomic_json


def main():
    output = ROOT / "runtime/desktop-qa"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        config_path = base / "config.json"
        secrets_path = base / "secrets.json"
        atomic_json(config_path, {"state_dir": str(base / "state"), "secrets_file": str(secrets_path),
                                 "instruments": [{"query": "159567", "type": "etf"}],
                                 "schedule": ["14:00"], "model": "deepseek-flash", "start_date": "20240101"})
        atomic_json(secrets_path, {"SMTP_HOST": "smtp.163.com", "SMTP_USER": "demo@163.com",
                                  "SMTP_FROM": "demo@163.com", "SMTP_TO": "demo@gmail.com",
                                  "DEEPSEEK_API_KEY": "offline-test-only", "SMTP_PASSWORD": "offline-test-only"}, private=True)
        model = DesktopModel(config_path)
        model.save_position("159567", "etf", 4500, "0.8723")
        model.save_position("000001", "stock", 100, "12.3456")
        atomic_json(model.root() / "data/catalog-etf.json", {"names": {"159567": "创新药ETF（演示）"}})
        atomic_json(model.root() / "data/catalog-stock.json", {"names": {"000001": "示例公司"}})
        root = tk.Tk()
        root.geometry("1080x780+70+70")
        app = DesktopApp(root, model)
        errors = []
        root.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
        pages = [("总览", None, "page-1", "1080x780"), ("选股研究", 0, "page-2", "1080x780"),
                 ("我的持仓", None, "page-3", "1080x780"), ("设置", None, "page-4", "1080x780"),
                 ("报告与诊断", None, "page-5", "1080x780"),
                 ("选股研究", 2, "research-operations", "1080x780"),
                 ("选股研究", 1, "research-sectors", "1080x780"),
                 ("选股研究", 0, "research-minimum", "960x720"),
                 ("选股研究", 2, "research-operations-minimum", "960x720"),
                 ("我的持仓", None, "positions-minimum", "960x720")]
        def photograph(index):
            page, tab, name, _ = pages[index]
            captured = capture_own_window(str(output / (name + ".png")))
            if tab == 0:
                if app.research.tree.winfo_height() <= 100:
                    errors.append(name + ": candidate table too short")
                button = app.research.details_button
                if button.winfo_rooty() + button.winfo_height() > root.winfo_rooty() + root.winfo_height():
                    errors.append(name + ": candidate action clipped")
            if tab == 2 and app.research.log_text.winfo_height() <= 30:
                errors.append(name + ": log area too short")
            results.append({"page": page, "width": root.winfo_width(), "height": root.winfo_height(),
                            "tab": tab, "screenshot": captured})
            if index + 1 < len(pages):
                advance(index + 1)
            else:
                exercise()
        def advance(index):
            page, tab, _, size = pages[index]
            root.geometry(size + "+70+70")
            app.show(page)
            if tab is not None:
                app.research.tabs.select(tab)
            root.lift()
            root.focus_force()
            root.after(400, lambda: photograph(index))
        def exercise():
            try:
                for key, value in (("code", "510300"), ("kind", "ETF"), ("shares", "100"), ("cost", "4.1234")):
                    app.position_vars[key].set(value)
                app.save_position()
                assert len(model.positions()) == 3
                # A name-only update uses a public-data fixture, never the network.
                provider = Mock()
                names = {"159567": "创新药ETF（演示）", "510300": "宽基ETF（演示）", "000001": "示例公司"}
                provider.resolve.side_effect = lambda code, kind: (code, names[code])
                with patch("aquant.watch.AKDaily", return_value=provider):
                    app.update_position_names()
                    model.name_thread.join(3)
                    assert not model.names_busy(), "name lookup did not finish"
                app.refresh_once()
                assert all(row["name"] for row in model.positions())
                selected = next(label for label in app.position_choices if label.startswith("000001"))
                app.position_code_combo.set(selected)
                app.position_code_combo.event_generate("<<ComboboxSelected>>")
                root.update()
                assert app.position_vars["code"].get() == "000001"
                assert app.position_vars["kind"].get() == "股票"
                assert app.position_vars["cost"].get() == "12.3456"
                app.position_vars["shares"].set("123")
                app.load_positions()
                root.update()
                assert app.position_vars["shares"].get() == "123", "refresh overwrote unsaved shares"
                app.settings_vars["schedule"].set("14:05")
                app.save_settings()
                assert model.config()["schedule"] == ["14:05"]
                assert model.child is None
                assert app.research.model.child is None
                app.show("选股研究")
                assert len(app.research.rows) > 0
                app.research.tree.selection_set("0")
                app.research.details()
                for window in root.winfo_children():
                    if isinstance(window, tk.Toplevel):
                        window.destroy()
                assert not errors, errors
                results.append({"native_forms": "passed", "watch_started": False})
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
            finally:
                app.close()
        root.after(700, lambda: advance(0))
        root.mainloop()
    (output / "results.json").write_text(json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2))
    print(json.dumps({"pages": len(results) - 1, "errors": errors, "directory": str(output)}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
