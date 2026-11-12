from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from .domain import ValidationError, timestamp


def render(report):
    labels = {"BUY": "买入", "SELL": "卖出", "HOLD": "不动"}
    lines = [f"A-Quant Agent｜{report['status']}｜{report['as_of']}",
             "研究/演示输出，尚未通过实盘策略准入；不是自动委托。",
             f"覆盖：{report['coverage']}；账户版本：{report['account_version']}",
             f"报告ID：{report['id']}；有效至：{report['expires_at']}", "", report["summary"]]
    if report["mode"] == "rules":
        lines.append("分析模式：规则基线，未调用DeepSeek。")
    if report["mode"] == "replay":
        lines.append("分析模式：冻结响应回放，未调用真实API。")
    for error in report["errors"]:
        lines.append(f"暂停原因：{error}")
    for d in report["decisions"]:
        lines += ["", f"{d['name']} {d['symbol']}：{labels[d['action']]} {d['quantity']}股",
                  f"参考价{d['reference_price']:.2f}元，预算价{d['budget_price']:.2f}元，估计费用{d['estimated_fee']:.2f}元。",
                  f"原因：{d['reason']}"]
        if d["forced_exit"]:
            lines.append("规则触发风险退出；无法成交时风险仍然存在。")
        lines.extend(f"风控：{b}" for b in d["blockers"])
    if report["projection"]:
        p = report["projection"]
        lines += ["", f"买入含费预算：{p['buy_cost_with_fees']:.2f}元。",
                  f"不依赖卖出时的预计现金：{p['cash_without_sales']:.2f}元。",
                  f"仅在全部成交条件下预计现金：{p['cash_if_all_filled']:.2f}元。",
                  "建议不改变账户；请录入实际成交，再确认本次建议执行情况。"]
    lines += ["", "价格超过预算条件或有效时间后需重新评估；邮件更新不代表已撤销券商旧委托。"]
    return "\n".join(lines)


def write_report(report, directory):
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stem = target / report["id"]
    text = render(report)
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    stem.with_suffix(".txt").write_text(text, encoding="utf-8")
    stem.with_suffix(".html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>A-Quant 研究报告</title><style>body{max-width:850px;margin:40px auto;padding:0 24px;'
        'font:16px/1.8 system-ui;color:#1e293b;background:#f8fafc}pre{white-space:pre-wrap;'
        'font:inherit;background:white;padding:28px;border:1px solid #cbd5e1;border-radius:12px}</style>'
        f'<pre>{html.escape(text)}</pre></html>', encoding="utf-8")
    return stem.with_suffix(".html")


def send(store, run_id, now):
    report = json.loads(store.run(run_id)["body"])
    if report["status"] == "DEMO" or report.get("demo"):
        raise ValidationError("演示报告不允许发送到真实邮箱")
    if now > timestamp(report["expires_at"]) or now < timestamp(report["as_of"]):
        raise ValidationError("报告过期或来自未来，禁止发送")
    if store.account()["version"] != report["account_version"]:
        raise ValidationError("账户版本已变化，旧报告不能发送")
    config = {k: os.environ.get(k) for k in
              ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO")}
    if not all(config.values()):
        raise ValidationError("SMTP配置不完整")
    message = EmailMessage()
    message["Subject"] = f"[A-Quant {report['status']}] {report['as_of']}"
    message["From"] = config["SMTP_FROM"]
    message["To"] = config["SMTP_TO"]
    message["Message-ID"] = f"<{run_id}@aquant.local>"
    message.set_content(render(report))
    store.claim_email(run_id)
    try:
        with smtplib.SMTP_SSL(config["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465")),
                              timeout=20, context=ssl.create_default_context()) as client:
            client.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
            refused = client.send_message(message)
            if refused:
                raise ValidationError("部分收件人被拒绝")
        store.email_status(run_id, "accepted")
    except Exception:
        store.email_status(run_id, "unknown")
        raise ValidationError("邮件发送结果未知，请检查收件箱及SMTP日志，不自动重发") from None
