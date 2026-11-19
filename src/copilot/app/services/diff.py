# -*- coding: utf-8 -*-
"""差异服务：应有费用 vs 已录应收。

P0 只认四类：缺项 / 重复 / 金额不符 / 未匹配（已录但无应有依据）。
"账单拆行""归属错误""汇率口径"留 P1-5，因为它们需要更多上下文，
在 MVP 里硬塞会把误报率抬高。

金额比较一律用 Decimal，且按 (费用项, 箱号) 配对，避免多箱业务的串行错误。
"""

from __future__ import annotations

from decimal import Decimal

from app.domain import ChargeLine, DiffKind, Difference, Direction, Shipment
from app.repo import Repository
from app.services.rate import RateCardMissing, required_charges
from app.services.trace import build_trace


def _fmt(amount: Decimal, currency: str) -> str:
    """把金额格式化成 "1,180.00 CNY" 便于展示与比对。

    入参：amount —— Decimal；currency —— 币种
    出参：字符串
    失败行为：不抛异常。
    """
    return f"{amount:,.2f} {currency}"


def compute_diff(repo: Repository, shipment: Shipment) -> dict:
    """计算一票业务的差异清单。

    入参：repo —— 仓库；shipment —— 单票
    出参：
        {"rate_card": "V4", "required": [...], "differences": [...], "totals": {...}}
    失败行为：
        缺费率卡时抛 RateCardMissing（由 API 转 409，阻断出账）。
        其他情况不抛异常，差异为空时返回空列表。
    """
    required, card = required_charges(repo, shipment)

    # 只看应收行：应付行的差异属于对账（P1）
    lines = [l for l in repo.lines_of(shipment.shipment_id) if l.direction == Direction.AR]

    # 按费用项分组：某费用项可能多箱各一行
    by_code: dict[str, list[ChargeLine]] = {}
    for line in lines:
        by_code.setdefault(line.fee_code, []).append(line)

    differences: list[Difference] = []
    required_codes = {r.fee_code for r in required}

    for req in required:
        matched = by_code.get(req.fee_code, [])

        if not matched:
            # ① 缺项：应有但没有 —— 本项目 90% 的漏收都在这一类
            differences.append(Difference(
                kind=DiffKind.MISSING, fee_code=req.fee_code, fee_name=req.fee_name,
                amount=req.amount, currency=req.currency,
                expected=_fmt(req.amount, req.currency), actual="未录入",
                reason=f"费率卡 {req.rate_version} 要求收取，账单未体现",
                evidence=req.clause_no,
                trace=[lv.__dict__ for lv in build_trace(
                    repo, shipment, req.fee_code, req.rate_version,
                    req.clause_no, req.source_doc_no, None)],
            ))
            continue

        # ② 重复：同一费用项出现多行且来源单据相同 —— 多箱分批时模板复制的典型症状
        if len(matched) > 1:
            docs = {l.source_doc_no for l in matched}
            if len(docs) == 1:
                total = sum((l.amount for l in matched), Decimal("0"))
                differences.append(Difference(
                    kind=DiffKind.DUPLICATE, fee_code=req.fee_code, fee_name=req.fee_name,
                    amount=total - req.amount, currency=req.currency,
                    expected=_fmt(req.amount, req.currency), actual=f"{len(matched)} 行 / {_fmt(total, req.currency)}",
                    reason="同一费用项重复计入（多箱分批时模板复制）",
                    evidence=matched[0].source_doc_no,
                    trace=[lv.__dict__ for lv in build_trace(
                        repo, shipment, req.fee_code, req.rate_version,
                        req.clause_no, req.source_doc_no, matched[0])],
                ))
                continue

        # ③ 金额不符：同项但金额不等于费率卡 —— 用旧版本费率卡的典型结果
        for line in matched:
            if line.amount != req.amount:
                diff_amt = req.amount - line.amount
                differences.append(Difference(
                    kind=DiffKind.AMOUNT, fee_code=req.fee_code, fee_name=req.fee_name,
                    amount=diff_amt, currency=req.currency,
                    expected=_fmt(req.amount, req.currency), actual=_fmt(line.amount, line.currency),
                    reason=f"账单按 {line.rate_version or '未知版本'} 计价，应为 {req.rate_version}",
                    evidence=req.clause_no,
                    trace=[lv.__dict__ for lv in build_trace(
                        repo, shipment, req.fee_code, req.rate_version,
                        req.clause_no, req.source_doc_no, line)],
                ))

    # ④ 未匹配：已录但没有应有依据（如"其他费用"），P0 只登记不归因
    for line in lines:
        if line.fee_code in required_codes:
            continue
        fee = repo.fee_item(line.fee_code)
        differences.append(Difference(
            kind=DiffKind.UNMATCHED, fee_code=line.fee_code,
            fee_name=fee.name if fee else line.fee_code,
            amount=line.amount, currency=line.currency,
            expected="无对应费率项", actual=_fmt(line.amount, line.currency),
            reason="该费用项不在费率卡与实报实销清单中，需人工确认是否应转拆",
            evidence=line.source_doc_no,
            trace=[lv.__dict__ for lv in build_trace(
                repo, shipment, line.fee_code, card.version, "", "", line)],
        ))

    # 汇总：只累加"真金白银的缺口"，未匹配不并入（它可能是误收，不是漏收）
    missing_total: dict[str, Decimal] = {}
    for d in differences:
        if d.kind in (DiffKind.MISSING, DiffKind.AMOUNT):
            missing_total[d.currency] = missing_total.get(d.currency, Decimal("0")) + d.amount

    return {
        "shipment_id": shipment.shipment_id,
        "customer": f"{shipment.customer_name}（{shipment.customer_id}）",
        "bl_no": shipment.bl_no,
        "bl_date": shipment.bl_date.isoformat(),
        "rate_card": card.version,
        "rate_card_period": f"{card.effective_from} ~ {card.effective_to or '长期'}",
        "required": [
            {"fee_code": r.fee_code, "fee_name": r.fee_name,
             "amount": _fmt(r.amount, r.currency), "basis": r.basis,
             "clause_no": r.clause_no, "rate_version": r.rate_version}
            for r in required
        ],
        "differences": [
            {"kind": d.kind.value, "fee_code": d.fee_code, "fee_name": d.fee_name,
             "amount": _fmt(d.amount, d.currency), "expected": d.expected,
             "actual": d.actual, "reason": d.reason, "evidence": d.evidence,
             "trace": d.trace}
            for d in differences
        ],
        "totals": {cur: _fmt(v, cur) for cur, v in missing_total.items()},
        "blocked": False,
    }


__all__ = ["compute_diff", "RateCardMissing"]
