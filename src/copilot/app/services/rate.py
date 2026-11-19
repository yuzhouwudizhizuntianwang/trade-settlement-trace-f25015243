# -*- coding: utf-8 -*-
"""费率服务：选版本 + 生成"应有费用"。

这是整套系统最关键的 20 行逻辑：
- 版本选错 → 应收算错 → 后面所有差异都错；
- 所以选版本只认一个依据：**提单签发日**落在哪个版本的生效区间（制度 §5.2）。
"""

from __future__ import annotations

from decimal import Decimal

from app.domain import RateCard, RequiredCharge, Shipment
from app.repo import Repository


class RateCardMissing(Exception):
    """没有覆盖提单签发日的费率卡版本时抛出。

    入参：message —— 中文说明
    出参：无
    失败行为：由上层转成"阻断出账"，绝不允许用默认值兜底。
    """


def pick_rate_card(repo: Repository, customer_id: str, bl_date) -> RateCard:
    """按提单签发日挑选唯一生效的费率卡版本。

    入参：
        repo —— 数据仓库
        customer_id —— 客户号
        bl_date —— 提单签发日（date）
    出参：RateCard（保证覆盖 bl_date）
    失败行为：
        无任何版本覆盖时抛 RateCardMissing；多个版本同时覆盖时抛 ValueError（说明数据脏了，
        宁可停下来让人查，也不要随机挑一个）。
    """
    covering = [c for c in repo.rate_cards(customer_id) if c.covers(bl_date)]
    if not covering:
        raise RateCardMissing(f"客户 {customer_id} 在 {bl_date} 没有生效的费率卡，禁止出账")
    if len(covering) > 1:
        versions = ", ".join(c.version for c in covering)
        raise ValueError(f"费率卡版本重叠：{versions}，请先清理数据")
    return covering[0]


def required_charges(repo: Repository, shipment: Shipment) -> tuple[list[RequiredCharge], RateCard]:
    """生成这一票的"应有费用"清单。

    入参：
        repo —— 数据仓库
        shipment —— 单票（含 bl_date / ctn_type）
    出参：
        (应有费用列表, 命中的费率卡)——费率卡要一并返回，前端要显示"依据 V4"
    失败行为：
        缺费率卡时抛 RateCardMissing，由 API 转成 409 并提示"缺费率卡，阻断出账"。
    """
    card = pick_rate_card(repo, shipment.customer_id, shipment.bl_date)
    required: list[RequiredCharge] = []

    # ① 费率卡覆盖的常规项：只取适用于本箱型的条目（unit=票的不限箱型）
    for item in card.items:
        if item.unit not in {shipment.ctn_type, "票", ""}:
            continue
        fee = repo.fee_item(item.fee_code)
        required.append(RequiredCharge(
            fee_code=item.fee_code,
            fee_name=fee.name if fee else item.fee_code,
            amount=item.unit_price,              # Decimal，不做任何浮点换算
            currency=item.currency,
            rate_version=card.version,
            clause_no=item.clause_no,
            basis=f"费率卡 {card.version}（{card.effective_from} 起） + {item.clause_no}",
        ))

    # ② 实报实销项（改配费等）：不在费率卡里，凭船司账单据实计收（合同 §3.1）
    for pt in repo.pass_through_charges(shipment.shipment_id):
        fee = repo.fee_item(pt["fee_code"])
        required.append(RequiredCharge(
            fee_code=pt["fee_code"],
            fee_name=fee.name if fee else pt["fee_code"],
            amount=pt["amount"],
            currency=pt["currency"],
            rate_version="实报实销",
            clause_no=pt["clause_no"],
            basis=f"船司账单 {pt['source_doc_no']} + {pt['clause_no']}",
            source_doc_no=pt["source_doc_no"],
        ))

    return required, card


def summarize(required: list[RequiredCharge]) -> dict[str, Decimal]:
    """按币种汇总应有费用（USD 与 CNY 不能相加）。

    入参：required —— 应有费用列表
    出参：{"CNY": Decimal, "USD": Decimal}
    失败行为：不抛异常，空列表返回空 dict。
    """
    total: dict[str, Decimal] = {}
    for r in required:
        total[r.currency] = total.get(r.currency, Decimal("0")) + r.amount
    return total
