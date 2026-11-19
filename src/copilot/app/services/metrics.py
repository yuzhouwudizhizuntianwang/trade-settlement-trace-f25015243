# -*- coding: utf-8 -*-
"""指标计算：把"拦住了多少漏收"变成可复现的数字。

口径必须写在返回值里，而不是只给一个百分比 —— 否则换个样本口径就变了，
评审时无法对账（docs/02 已经把口径争议写死过一次）。
"""

from __future__ import annotations

from decimal import Decimal

from app.domain import DiffKind
from app.repo import Repository
from app.services.diff import compute_diff


def _dec(text: str) -> Decimal:
    """把 "1,180.00 CNY" 这类展示串还原成 Decimal。

    入参：text —— 带千分位与币种的金额串
    出参：Decimal
    失败行为：无法解析返回 Decimal("0")（指标宁可偏保守，不抛异常打断看板）。
    """
    try:
        return Decimal(text.split()[0].replace(",", ""))
    except (IndexError, ArithmeticError):
        return Decimal("0")


def compute(repo: Repository) -> dict:
    """汇总所有演示单票的差异指标。

    入参：repo —— 仓库
    出参：dict，含样本数、缺口金额、已录金额、演示口径漏收率、按类型的条目数
    失败行为：
        单票缺费率卡时跳过该票并计入 blocked，不抛异常（看板不能因为一票坏数据整体挂掉）。
    """
    shipments = repo.all_shipments()          # 走公开接口，内存/SQL 实现都成立
    missing_total = Decimal("0")
    recorded_total = Decimal("0")
    by_kind: dict[str, int] = {}
    blocked: list[str] = []

    for ship in shipments:
        try:
            diff = compute_diff(repo, ship)
        except Exception:                                  # noqa: BLE001 缺费率卡等
            blocked.append(ship.shipment_id)
            continue
        # 只统计 CNY 侧，USD 不与 CNY 相加（币种不同，相加没有意义）
        missing_total += _dec(diff["totals"].get("CNY", "0 CNY"))
        recorded_total += sum((_dec(l["amount"]) for l in build_recorded(repo, ship)), Decimal("0"))
        for d in diff["differences"]:
            by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1

    should_total = recorded_total + missing_total
    rate = (missing_total / should_total * 100) if should_total else Decimal("0")

    return {
        "shipments": len(shipments),
        "blocked": blocked,
        "cny": {
            "recorded": f"{recorded_total:,.2f}",
            "missing": f"{missing_total:,.2f}",
            "should": f"{should_total:,.2f}",
        },
        "leak_rate_demo": f"{rate:.1f}%",
        "by_kind": by_kind,
        "note": "演示口径：仅统计本次演示数据中的 CNY 应收侧，正式口径见 docs/02（漏收金额 ÷ 同期应收总额）",
    }


def build_recorded(repo: Repository, shipment) -> list[dict]:
    """取某票已录应收（仅 CNY），供指标累加。

    入参：repo —— 仓库；shipment —— 单票
    出参：[{"amount": "1,260.00 CNY"}] 形式
    失败行为：不抛异常。
    """
    out = []
    for line in repo.lines_of(shipment.shipment_id):
        if line.direction.value == "AR" and line.currency == "CNY":
            out.append({"amount": f"{line.amount:,.2f} {line.currency}"})
    return out
