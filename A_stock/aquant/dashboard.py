"""Loopback-only control panel. No credentials, external assets or broker access."""
from __future__ import annotations

import html
import json
import secrets
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .data import demo_inputs
from .domain import SHANGHAI, ValidationError
from .report import render
from .service import run


def serve(store, cfg, port=8765, demo=False, market_path=None, mode="rules", allow_api=False):
    if demo:
        try:
            store.account()
        except ValidationError:
            store.import_account(demo_inputs()[0])
    token = secrets.token_urlsafe(32)

    def esc(value):
        return html.escape(str(value), quote=True)

    def page(message=""):
        try:
            account = store.account()
        except ValidationError:
            account = {"version": 1, "cash": 0, "holdings": {}, "permissions": ["MAIN"],
                       "confirmed_at": datetime.now(SHANGHAI).isoformat()}
        runs = store.db.execute("SELECT id,body,feedback FROM runs ORDER BY rowid DESC LIMIT 12").fetchall()
        cards = "".join(f'<article><h3>{esc(json.loads(r["body"])["status"])} · '
                        f'{esc(json.loads(r["body"])["as_of"])}</h3><pre>{esc(render(json.loads(r["body"])))}</pre>'
                        + (form("confirm", '<input type="hidden" name="run_id" value="' + esc(r["id"]) + '">'
                                '<button>确认该报告全部实际成交已录入 或未执行</button>') if not r["feedback"] else "")
                        + '</article>' for r in runs)
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>A-Quant 本地控制台</title><style>'
                'body{font:16px/1.65 system-ui;background:#f1f5f9;color:#172033;max-width:1000px;margin:auto;padding:24px}'
                'h1{font-size:30px}article,section{background:white;padding:24px;margin:20px 0;border:1px solid #dbe3ee;border-radius:12px}'
                'textarea{box-sizing:border-box;width:100%;min-height:210px;padding:12px;font:14px/1.5 monospace;border:1px solid #9caec8;border-radius:6px}'
                'button{margin:12px 0;background:#1746a2;color:white;padding:10px 18px;border:0;border-radius:6px;font:inherit;cursor:pointer}'
                'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 system-ui}.status{color:#9a3412}small{color:#475569}</style>'
                '<h1>A-Quant 本地控制台</h1>'
                f'<p class="status">{"离线演示 · 虚构行情及账户" if demo else "研究模式 · 尚未通过实盘准入"} · '
                f'{esc(mode)} · 仅监听本机</p><p>量化证据 → 综合研判 → 仓位风控 → 研究报告 → 实际成交反馈</p>'
                f'<p role="status">{esc(message)}</p>'
                '<section><h2>生成报告</h2><p>报告不会改变持仓，也不会发送邮件或向券商下单。</p>'
                + form("run", '<button>生成本次研究报告</button>') + '</section>'
                '<section><h2>账户快照</h2><p>请先核对券商现金、持仓与可卖数量。跨交易日需重新导入快照。</p>'
                + form("account", '<label for="account">账户 JSON</label><textarea id="account" name="body">'
                       + esc(json.dumps(account, ensure_ascii=False, indent=2)) + '</textarea><button>保存已核对的账户</button>')
                + '</section><section><h2>录入实际成交</h2><p>只记录实际成交；录入后仍需在对应报告下确认本次反馈已完整。</p>'
                + form("fill", '<label for="fill">成交 JSON</label><textarea id="fill" name="body">'
                       + esc(json.dumps({"id": "请填写唯一券商成交编号", "symbol": "600000.SH", "side": "BUY",
                                         "quantity": 100, "price": 10, "fee": 5, "board": "MAIN",
                                         "industry": "演示行业A" if demo else "请填写行业",
                                         "at": demo_inputs()[2].isoformat() if demo else datetime.now(SHANGHAI).isoformat()},
                                        ensure_ascii=False, indent=2)) + '</textarea><button>记录真实成交</button>')
                + '</section><h2>最近报告</h2>' + (cards or '<p>尚无报告。</p>') + '</html>')

    def form(action, content):
        try:
            version = store.account()["version"]
        except ValidationError:
            version = 0
        return (f'<form method="post" action="/{action}"><input type="hidden" name="csrf" value="{token}">'
                f'<input type="hidden" name="version" value="{version}">' + content + '</form>')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never put account data, query strings or tokens in access logs.

        def reply(self, message="", status=200):
            content = page(message).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)

        def valid_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            url = urlparse(self.path)
            if not self.valid_host() or url.path != "/":
                self.send_error(404)
                return
            self.reply(parse_qs(url.query).get("message", [""])[0][:300])

        def do_POST(self):
            if not self.valid_host():
                self.send_error(403)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self.send_error(413)
                return
            values = parse_qs(self.rfile.read(length).decode())
            if not secrets.compare_digest(values.get("csrf", [""])[0], token):
                self.send_error(403)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.send_error(403)
                return
            try:
                version = int(values["version"][0])
                try:
                    current = store.account()["version"]
                except ValidationError:
                    current = 0
                if current != version:
                    raise ValidationError("账户已变化，请刷新页面后重新操作")
                now = demo_inputs()[2] if demo else datetime.now(SHANGHAI)
                if self.path == "/account":
                    store.import_account(json.loads(values["body"][0]), version)
                    message = "账户快照已保存。此前未确认的建议仍需逐份确认执行情况。"
                elif self.path == "/fill":
                    store.fill(json.loads(values["body"][0]), version)
                    message = "成交已记录；请确认对应报告的执行反馈已完整。"
                elif self.path == "/confirm":
                    store.confirm(values["run_id"][0], version, now.isoformat())
                    message = "该报告执行情况已确认。"
                elif self.path == "/run":
                    if demo:
                        market = demo_inputs()[1]
                    elif market_path:
                        from pathlib import Path
                        market = json.loads(Path(market_path).read_text(encoding="utf-8"))
                    else:
                        raise ValidationError("真实研究请启动时指定--market行情文件")
                    slot = f"panel-v{version}"
                    result = run(store, market, cfg, now, slot, mode, demo=demo, allow_api=allow_api)
                    message = f"报告状态：{result['status']}。同账户版本再次点击返回原报告；请注意有效期。"
                else:
                    raise ValidationError("未知操作")
                self.send_response(303)
                self.send_header("Location", "/?message=" + quote(message))
                self.end_headers()
            except (ValidationError, ValueError, KeyError, OSError) as exc:
                self.reply(str(exc) if isinstance(exc, ValidationError) else "输入格式错误，请检查JSON及字段。", 400)

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"本地控制台：http://127.0.0.1:{server.server_port}（Ctrl+C停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
