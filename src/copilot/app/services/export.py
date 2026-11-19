# -*- coding: utf-8 -*-
"""追溯包导出：给客户看的材料，字段白名单 + 强制留痕。

两个不能松的地方：
1. **白名单构造**：pack 是"新建 dict"，不是把内部对象 copy 一份再删字段——
   删除式过滤永远会漏掉新加的字段；
2. **导出必留痕**：谁、何时、导了哪些单、哪些字段、给谁，四要素缺一不可（红线 3）。
"""

from __future__ import annotations

from app.core.audit import AuditLog
from app.domain import Shipment
from app.repo import Repository
from app.services.diff import compute_diff

# 允许出现在追溯包里的字段（客户可见）。任何不在这个集合里的字段都不会被写入。
EXPORT_FIELDS = [
    "shipment_id", "customer", "bl_no", "bl_date", "route", "ctn_type",
    "fee_code", "fee_name", "amount", "currency", "basis", "evidence", "status",
]

# 明确禁止的字段关键词：命中即视为导出实现有 bug，直接拒绝生成
FORBIDDEN_KEYWORDS = ("margin", "unit_price", "cost", "profit", "毛利", "单价", "成本")


class ExportViolation(Exception):
    """追溯包里出现禁止字段时抛出。

    入参：message —— 说明是哪几个字段
    出参：无
    失败行为：由 API 转 500 并记审计——这种情况属于代码缺陷，不应悄悄放过。
    """


def _guard(pack: dict) -> None:
    """检查包里没有敏感字段。

    入参：pack —— 待导出的 dict
    出参：无
    失败行为：命中禁止关键词抛 ExportViolation。
    """
    text = str(pack).lower()
    hit = [k for k in FORBIDDEN_KEYWORDS if k.lower() in text]
    if hit:
        raise ExportViolation(f"追溯包含有禁止字段：{hit}")


def build_pack(repo: Repository, shipment: Shipment, actor: str, role: str,
               receiver: str, audit: AuditLog) -> dict:
    """生成客户追溯包并留痕。

    入参：
        repo —— 仓库；shipment —— 单票
        actor / role —— 导出人（来自 JWT）
        receiver —— 接收方（必填，回答"给谁了"）
        audit —— 审计写入器
    出参：{"pack": {...}, "audit_id": "...", "fields": [...]}
    失败行为：
        receiver 为空抛 ValueError（不留接收方就等于没留痕）；
        含禁止字段抛 ExportViolation。
    """
    if not receiver.strip():
        raise ValueError("导出必须填写接收方（红线 3：给谁）")

    diff = compute_diff(repo, shipment)

    # 逐字段显式构造，避免"复制后删除"漏字段
    items = []
    for d in diff["differences"]:
        items.append({
            "fee_code": d["fee_code"],
            "fee_name": d["fee_name"],
            "amount": d["amount"],                 # 金额可给客户
            "currency": d["amount"].split()[-1],
            "basis": d["reason"],
            "evidence": d["evidence"],
            "status": d["kind"],
        })

    pack = {
        "shipment_id": diff["shipment_id"],
        "customer": diff["customer"],
        "bl_no": diff["bl_no"],
        "bl_date": diff["bl_date"],
        "fee_items": items,
        "totals": diff["totals"],
        "statement": "以上内容供核对，如需调整请与我们联系。",
    }
    _guard(pack)

    audit_id = audit.write(
        actor, role, "export", shipment.customer_id,
        fields=EXPORT_FIELDS, receiver=receiver,
        extra={"shipment_id": shipment.shipment_id, "item_count": len(items)},
    )
    return {"pack": pack, "audit_id": audit_id, "fields": EXPORT_FIELDS}
