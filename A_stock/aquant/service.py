from __future__ import annotations

from .domain import ValidationError
from .llm import synthesize, validate_synthesis
from .strategy import (allocate, baseline, check_context, evidence, report_base)


def run(store, market, cfg, as_of, slot, mode="rules", demo=False,
        replay=None, allow_api=False):
    account = store.account()
    report = report_base(account, market, cfg, as_of, slot, mode)
    previous = store.session(report["session_key"])
    if previous:
        return previous
    try:
        check_context(market, account, cfg, as_of, demo)
        if store.unresolved():
            raise ValidationError("存在尚未确认执行情况的历史建议；录入成交并执行confirm后重试下一报告")
        packet = evidence(market, account, cfg)
        report["evidence"] = packet
        if mode == "rules":
            synthesis = baseline(packet)
            report["llm_audit"] = {"provider": "none", "model": "rule-baseline-v1"}
        elif mode == "replay":
            if replay is None:
                raise ValidationError("replay模式需要冻结的模型输出")
            synthesis = validate_synthesis(replay, packet)
            report["llm_audit"] = {"provider": "replay", "model": "frozen-response"}
        elif mode == "deepseek":
            synthesis, report["llm_audit"] = synthesize(packet, cfg["deepseek_model"], allow_api)
        else:
            raise ValidationError("未知运行模式")
        report["synthesis"] = synthesis
        report["summary"] = synthesis["summary"]
        report["decisions"], report["projection"] = allocate(packet, synthesis, market, account, cfg)
        report["status"] = "DEMO" if demo else "RESEARCH"
        report["demo"] = demo
    except (ValidationError, KeyError, TypeError, ValueError, StopIteration) as exc:
        report["errors"] = [str(exc) if isinstance(exc, ValidationError) else "输入字段缺失或类型无效，请检查数据格式"]
        report["decisions"] = []
        report["projection"] = {}
        report["summary"] = "暂停交易建议，请处理数据或账户异常。"
    store.save_run(report)
    return report
