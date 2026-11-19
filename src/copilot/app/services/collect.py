# -*- coding: utf-8 -*-
"""归集服务：按四键取单票，并把费用行整理成前端可直接渲染的结构。"""

from __future__ import annotations

from decimal import Decimal

from app.domain import Direction, Shipment
from app.repo import Repository
from app.services.rate import RateCardMissing, required_charges


def get_shipment(repo: Repository, key_type: str, key_value: str) -> Shipment | None:
    """按任一键查单票。

    入参：repo —— 仓库；key_type —— 四键之一；key_value —— 值
    出参：Shipment 或 None
    失败行为：key_type 非法时由 repo 抛 ValueError。
    """
    return repo.shipment_by_key(key_type, key_value)


def build_view(repo: Repository, shipment: Shipment) -> dict:
    """组装单票视图（费用行 + 应有费用 + 费率卡）。

    入参：repo —— 仓库；shipment —— 单票
    出参：可直接 JSON 化的 dict
    失败行为：缺费率卡时 blocked=True 并给出提示，不抛异常（前端要能显示原因）。
    """
    lines = repo.lines_of(shipment.shipment_id)
    ar = [l for l in lines if l.direction == Direction.AR]
    ap = [l for l in lines if l.direction == Direction.AP]

    def fmt(amount: Decimal, currency: str) -> str:
        """金额格式化：千分位 + 两位小数 + 币种。

        入参：amount —— Decimal；currency —— 币种
        出参：字符串
        失败行为：不抛异常。
        """
        return f"{amount:,.2f} {currency}"

    blocked = False
    block_reason = ""
    required: list[dict] = []
    card_version = ""
    try:
        reqs, card = required_charges(repo, shipment)
        card_version = card.version
        required = [{"fee_code": r.fee_code, "fee_name": r.fee_name,
                     "amount": fmt(r.amount, r.currency), "basis": r.basis}
                    for r in reqs]
    except RateCardMissing as exc:                     # 缺费率卡：阻断出账，但要告诉人为什么
        blocked = True
        block_reason = str(exc)

    return {
        "shipment_id": shipment.shipment_id,
        "customer": f"{shipment.customer_name}（{shipment.customer_id}）",
        "order_no": shipment.order_no,
        "booking_no": shipment.booking_no,
        "bl_no": shipment.bl_no,
        "bl_date": shipment.bl_date.isoformat(),
        "route": shipment.route,
        "ctn_type": shipment.ctn_type,
        "containers": list(shipment.containers),
        "rate_card": card_version,
        "blocked": blocked,
        "block_reason": block_reason,
        "receivable": [{"line_id": l.line_id, "fee_code": l.fee_code, "amount": fmt(l.amount, l.currency),
                        "currency": l.currency, "source_doc_no": l.source_doc_no,
                        "rate_version": l.rate_version, "remark": l.remark} for l in ar],
        "payable": [{"line_id": l.line_id, "fee_code": l.fee_code, "amount": fmt(l.amount, l.currency)}
                    for l in ap],
        "required": required,
    }
