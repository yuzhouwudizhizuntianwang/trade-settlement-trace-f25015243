# -*- coding: utf-8 -*-
"""数据访问层：一个协议，两种实现。

- MemoryRepository：纯内存，供单测与无依赖自测（本机装不上 SQLAlchemy 时也能跑）
- SqlRepository（见 app/db/sql_repo.py）：SQLAlchemy 实现，MVP 按方案落 SQLite

为什么先做内存实现：业务逻辑（费率匹配/差异/追溯）必须能脱离数据库验证；
生产实现只是把同样的查询换成 SQL，不改服务层代码。
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from typing import Iterable, Protocol

from app.domain import (
    ChargeLine,
    Direction,
    FeeItem,
    RateCard,
    RateItem,
    Shipment,
    SourceDoc,
)


class Repository(Protocol):
    """数据访问协议（结构性子类型，不强制继承）。"""

    def shipment_by_key(self, key_type: str, key_value: str) -> Shipment | None: ...
    def all_shipments(self) -> list[Shipment]: ...
    def lines_of(self, shipment_id: str) -> list[ChargeLine]: ...
    def rate_cards(self, customer_id: str) -> list[RateCard]: ...
    def fee_item(self, code: str) -> FeeItem | None: ...
    def source_doc(self, doc_no: str) -> SourceDoc | None: ...
    def clauses_for(self, fee_code: str) -> list[dict]: ...
    def pass_through_charges(self, shipment_id: str) -> list[dict]: ...
    def pass_through_charges(self, shipment_id: str) -> list[dict]:
        """取该票"按实计收"的费用（如改配费 REB），它们不在费率卡里。

        入参：shipment_id
        出参：dict 列表，含 fee_code / amount / currency / source_doc_no / clause_no
        失败行为：不抛异常，无数据返回空列表。
        """
        return [p for p in self._pass_through if p.get("shipment_id") == shipment_id]

    def triple_search(self, subject: str | None = None,
                      predicate: str | None = None,
                      obj: str | None = None) -> list[dict]: ...


class MemoryRepository:
    """内存实现：把演示数据装在 dict 里，查询语义与 SQL 版保持一致。"""

    def __init__(self, shipments: Iterable[Shipment] = (), lines: Iterable[ChargeLine] = (),
                 rate_cards: Iterable[RateCard] = (), fee_items: Iterable[FeeItem] = (),
                 docs: Iterable[SourceDoc] = (), clauses: Iterable[dict] = (),
                 triples: Iterable[dict] = (), pass_through: Iterable[dict] = ()):
        """构造仓库。

        入参：各实体集合（可为空，后续 add_* 也能补）
        出参：无
        失败行为：不抛异常；空集合时查询返回 None / 空列表。
        """
        self._shipments = {s.shipment_id: s for s in shipments}
        self._lines: list[ChargeLine] = list(lines)
        self._rate_cards: list[RateCard] = list(rate_cards)
        self._fee_items = {f.code: f for f in fee_items}
        self._docs = {d.doc_no: d for d in docs}
        self._clauses = list(clauses)
        self._triples = list(triples)
        # 实报实销类费用（如改配费）：不挂在费率卡上，凭船司账单按实计收
        self._pass_through = list(pass_through)

    # ---------------- 查询 ----------------
    def shipment_by_key(self, key_type: str, key_value: str) -> Shipment | None:
        """按四键之一查单票。

        入参：key_type —— order_no / booking_no / bl_no / container_no；key_value —— 值
        出参：Shipment 或 None
        失败行为：未知 key_type 抛 ValueError（静默返回 None 会把拼错的字段名藏起来）。
        """
        if key_type not in {"order_no", "booking_no", "bl_no", "container_no"}:
            raise ValueError(f"不支持的查询键：{key_type}")
        for s in self._shipments.values():
            if s.keys().get(key_type) == key_value:
                return s
        return None

    def all_shipments(self) -> list[Shipment]:
        """返回全部单票（指标看板与评估集用）。

        入参：无
        出参：Shipment 列表
        失败行为：不抛异常，空库返回空列表。
        """
        return list(self._shipments.values())

    def lines_of(self, shipment_id: str) -> list[ChargeLine]:
        """取一票的所有费用行（应收 + 应付）。

        入参：shipment_id
        出参：费用行列表（可能为空）
        失败行为：不抛异常，空列表由上层判断。
        """
        return [l for l in self._lines if l.shipment_id == shipment_id]

    def rate_cards(self, customer_id: str) -> list[RateCard]:
        """取某客户的全部费率卡版本。

        入参：customer_id
        出参：按生效起始日升序的列表
        失败行为：不抛异常。
        """
        return sorted([r for r in self._rate_cards if r.customer_id == customer_id],
                      key=lambda r: r.effective_from)

    def fee_item(self, code: str) -> FeeItem | None:
        """按代码取费用项字典。

        入参：code —— 如 LSS
        出参：FeeItem 或 None
        失败行为：不抛异常。
        """
        return self._fee_items.get(code)

    def source_doc(self, doc_no: str) -> SourceDoc | None:
        """按单号取来源单据。

        入参：doc_no
        出参：SourceDoc 或 None
        失败行为：不抛异常。
        """
        return self._docs.get(doc_no)

    def clauses_for(self, fee_code: str) -> list[dict]:
        """取与某费用项相关的制度条款（知识图谱里的 GOVERNED_BY 边）。

        入参：fee_code
        出参：条款 dict 列表，含 doc_no / clause_no / title / text
        失败行为：不抛异常。
        """
        return [c for c in self._clauses if fee_code in (c.get("fee_codes") or [])]

    def pass_through_charges(self, shipment_id: str) -> list[dict]:
        """取该票"按实计收"的费用（如改配费 REB），它们不在费率卡里。

        入参：shipment_id
        出参：dict 列表，含 fee_code / amount / currency / source_doc_no / clause_no
        失败行为：不抛异常，无数据返回空列表。
        """
        return [p for p in self._pass_through if p.get("shipment_id") == shipment_id]

    def triple_search(self, subject: str | None = None, predicate: str | None = None,
                      obj: str | None = None) -> list[dict]:
        """按三元组任一端点查询（知识图谱的底层查询）。

        入参：subject / predicate / obj —— 任一可为 None（作为过滤条件）
        出参：匹配的三元组列表
        失败行为：不抛异常。
        """
        out = []
        for t in self._triples:
            if subject and t["subject"] != subject:
                continue
            if predicate and t["predicate"] != predicate:
                continue
            if obj and t["object"] != obj:
                continue
            out.append(t)
        return out


# ============================================================================
# 演示数据（全部取自 docs/04-项目原有资料/M-04_样例单据_三单对照.csv，金额逐行照抄）
# ============================================================================

CUSTOMER_ID = "C-0731"
CUSTOMER_NAME = "宁波恒晟机电"
SHIPMENT_ID = "SO2610-0187"


def build_demo_repository() -> MemoryRepository:
    """构造演示用仓库（一票 BK2610-0187 + 两份费率卡 + 条款 + 图谱）。

    入参：无
    出参：MemoryRepository 实例
    失败行为：不抛异常（数据均为常量）。
    """
    fee_items = [
        FeeItem("OCF", "海运费", "OCEAN_FREIGHT"),
        FeeItem("THC", "码头操作费", "PORT"),
        FeeItem("CUS", "报关费", "CUSTOMS"),
        FeeItem("TRK", "拖车费", "TRUCKING"),
        FeeItem("AGY", "代理费", "AGENCY"),
        FeeItem("LSS", "低硫附加费", "SURCHARGE"),
        FeeItem("PSS", "旺季附加费", "SURCHARGE"),
        FeeItem("REB", "改配费", "SURCHARGE"),
        FeeItem("OTH", "其他费用", "OTHER"),
    ]

    # 费率卡 V3：2026-06-01 ~ 2026-09-30（10 月前的旧价，LSS 还是 980）
    v3 = RateCard(
        customer_id=CUSTOMER_ID, version="V3",
        effective_from=date(2026, 6, 1), effective_to=date(2026, 9, 30),
        approved_by="周慧",
        items=(
            RateItem("OCF", Decimal("1850.00"), "USD", "40HQ", "HC-TST-2026-001 §3.1"),
            RateItem("THC", Decimal("1260.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
            RateItem("CUS", Decimal("380.00"), "CNY", "票", "HC-FIN-2024-07 §5.2"),
            RateItem("TRK", Decimal("1150.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
            RateItem("AGY", Decimal("720.00"), "CNY", "票", "HC-FIN-2024-07 §5.2"),
            RateItem("LSS", Decimal("980.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
        ),
    )
    # 费率卡 V4：2026-10-01 起（LSS 调价至 1180，新增 PSS 980）
    v4 = RateCard(
        customer_id=CUSTOMER_ID, version="V4",
        effective_from=date(2026, 10, 1), effective_to=None,
        approved_by="周慧",
        items=(
            RateItem("OCF", Decimal("1850.00"), "USD", "40HQ", "HC-TST-2026-001 §3.1"),
            RateItem("THC", Decimal("1260.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
            RateItem("CUS", Decimal("380.00"), "CNY", "票", "HC-FIN-2024-07 §5.2"),
            RateItem("TRK", Decimal("1150.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
            RateItem("AGY", Decimal("720.00"), "CNY", "票", "HC-FIN-2024-07 §5.2"),
            RateItem("LSS", Decimal("1180.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
            RateItem("PSS", Decimal("980.00"), "CNY", "40HQ", "HC-FIN-2024-07 §5.2"),
        ),
    )

    shipment = Shipment(
        shipment_id=SHIPMENT_ID, customer_id=CUSTOMER_ID,
        order_no="SO2610-0187", booking_no="BK2610-0187",
        bl_no="HLCUNB26100457", bl_date=date(2026, 10, 9),
        route="NINGBO-RTM", ctn_type="40HQ",
        containers=("MSKU1234567",), customer_name=CUSTOMER_NAME,
    )

    # 客户账单 6 行（10-27 出具）：只有常规项，LSS/PSS/REB 全缺
    lines = [
        ChargeLine("L1", SHIPMENT_ID, Direction.AR, "OCF", Decimal("1850.00"), "USD",
                   "MSKU1234567", "HC-BL-2610-0187", "V3", "按 9 月费率卡"),
        ChargeLine("L2", SHIPMENT_ID, Direction.AR, "THC", Decimal("1260.00"), "CNY",
                   "MSKU1234567", "HC-BL-2610-0187", "V3", ""),
        ChargeLine("L3", SHIPMENT_ID, Direction.AR, "CUS", Decimal("380.00"), "CNY",
                   "MSKU1234567", "HC-BL-2610-0187", "V3", "单证 2 份"),
        ChargeLine("L4", SHIPMENT_ID, Direction.AR, "TRK", Decimal("1150.00"), "CNY",
                   "MSKU1234567", "HC-BL-2610-0187", "V3", "北仑—工厂"),
        ChargeLine("L5", SHIPMENT_ID, Direction.AR, "AGY", Decimal("720.00"), "CNY",
                   "MSKU1234567", "HC-BL-2610-0187", "V3", "协议价"),
        ChargeLine("L6", SHIPMENT_ID, Direction.AR, "OTH", Decimal("260.00"), "CNY",
                   "MSKU1234567", "HC-BL-2610-0187", "V3", "客户投诉：名称不规范"),
    ]

    docs = [
        SourceDoc("MSK-INV-26-10-0912", "carrier_invoice", date(2026, 10, 9),
                  "data/src/MSK-26-10-0912.pdf#p1", "马士基（中国）航运"),
        SourceDoc("MSK-INV-26-10-1023", "carrier_invoice", date(2026, 10, 23),
                  "data/src/MSK-26-10-1023.pdf#p1", "马士基（中国）航运"),
        SourceDoc("HC-BL-2610-0187", "customer_invoice", date(2026, 10, 27),
                  "data/src/HC-BL-2610-0187.pdf#p1", CUSTOMER_NAME),
    ]

    # 制度条款（对应 docs/04 M-02 原文，只摘与本案例相关的）
    clauses = [
        {"doc_no": "HC-FIN-2024-07", "clause_no": "§3.2", "title": "费用行须有来源单据",
         "text": "缺少来源单据编号的费用行不得进入账单。",
         "fee_codes": ["OCF", "THC", "CUS", "TRK", "AGY", "LSS", "PSS", "REB", "OTH"]},
        {"doc_no": "HC-FIN-2024-07", "clause_no": "§5.1", "title": "费率卡版本登记",
         "text": "每次调整须登记版本号、生效起止日期与审批人。",
         "fee_codes": []},
        {"doc_no": "HC-FIN-2024-07", "clause_no": "§5.2", "title": "按提单签发日取费率",
         "text": "出账引用费率卡的生效区间必须覆盖该票提单签发日；跨生效日按提单日取值。",
         "fee_codes": ["OCF", "THC", "CUS", "TRK", "AGY", "LSS", "PSS"]},
        {"doc_no": "HC-FIN-2024-07", "clause_no": "§7.2", "title": "异议期按合同",
         "text": "标准合同 7 日，部分年度协议 15 日，未约定按催告后 30 日；不得统一按 7 日处理。",
         "fee_codes": []},
        {"doc_no": "HC-TST-2026-001", "clause_no": "§3.1", "title": "附加费实报实销",
         "text": "海运费及附加费按船司实收金额向客户实报实销，改配等非常规费用凭船司账单据实计收。",
         "fee_codes": ["OCF", "LSS", "PSS", "REB"]},
    ]

    # 知识图谱三元组：客户→合同→费率卡→费用项→条款；单票→单据→费用行
    triples = [
        {"subject": "Customer:C-0731", "predicate": "HAS_CONTRACT", "object": "Contract:HC-2026-011",
         "source_ref": "HC-TST-2026-001 §3.1", "confidence": "1.0"},
        {"subject": "Contract:HC-2026-011", "predicate": "HAS_RATE", "object": "RateCard:V3",
         "source_ref": "费率卡_2026_V3.xlsx", "confidence": "1.0"},
        {"subject": "Contract:HC-2026-011", "predicate": "HAS_RATE", "object": "RateCard:V4",
         "source_ref": "费率表_2026_V3最终版_改.xlsx", "confidence": "1.0"},
        {"subject": "RateCard:V4", "predicate": "COVERS", "object": "FeeItem:LSS",
         "source_ref": "2026-10-01 调价通知", "confidence": "1.0"},
        {"subject": "RateCard:V4", "predicate": "COVERS", "object": "FeeItem:PSS",
         "source_ref": "旺季区间 10-01~12-31", "confidence": "1.0"},
        {"subject": "RateCard:V3", "predicate": "COVERS", "object": "FeeItem:LSS",
         "source_ref": "2026-06-30 费率卡", "confidence": "1.0"},
        {"subject": "FeeItem:LSS", "predicate": "GOVERNED_BY", "object": "PolicyClause:HC-FIN-2024-07§5.2",
         "source_ref": "HC-FIN-2024-07 §5.2", "confidence": "0.95"},
        {"subject": "FeeItem:REB", "predicate": "GOVERNED_BY", "object": "PolicyClause:HC-TST-2026-001§3.1",
         "source_ref": "HC-TST-2026-001 §3.1", "confidence": "0.92"},
        {"subject": "Shipment:BK2610-0187", "predicate": "BILLED_BY", "object": "SourceDoc:MSK-INV-26-10-0912",
         "source_ref": "船司账单 10-09", "confidence": "1.0"},
        {"subject": "Shipment:BK2610-0187", "predicate": "BILLED_BY", "object": "SourceDoc:MSK-INV-26-10-1023",
         "source_ref": "船司账单 10-23（改配）", "confidence": "1.0"},
        {"subject": "ChargeLine:L1", "predicate": "EVIDENCED_BY", "object": "SourceDoc:HC-BL-2610-0187",
         "source_ref": "客户账单 10-27", "confidence": "1.0"},
        {"subject": "Difference:DIF-2610-0187-01", "predicate": "TRIGGERS",
         "object": "ExceptionType:费率卡版本错配", "source_ref": "RC-V3 vs V4", "confidence": "0.9"},
        {"subject": "Difference:DIF-2610-0187-01", "predicate": "ATTRIBUTED_TO",
         "object": "Role:结算专员", "source_ref": "HC-FIN-2024-07 §5.1", "confidence": "0.8"},
    ]

    # 改配费 REB ¥450：船司 10-23 补发账单，按合同 §3.1 实报实销转应收
    pass_through = [
        {"shipment_id": SHIPMENT_ID, "fee_code": "REB", "amount": Decimal("450.00"),
         "currency": "CNY", "source_doc_no": "MSK-INV-26-10-1023",
         "clause_no": "HC-TST-2026-001 §3.1",
         "note": "甩柜改配，船司补收，凭账单转客户应收"},
    ]

    return MemoryRepository(
        shipments=[shipment], lines=lines, rate_cards=[v3, v4],
        fee_items=fee_items, docs=docs, clauses=clauses, triples=triples,
        pass_through=pass_through,
    )


def get_repository() -> "Repository":
    """按配置返回可用仓库：优先 SQLAlchemy，装不上则退回内存（并打印提示）。

    入参：无
    出参：Repository 实例
    失败行为：
        SQLAlchemy 未安装时**不抛异常**，退回内存仓库并在 stderr 提示——
        这样"只装了标准库"的机器也能演示，但会在日志里明确暴露降级。
    """
    try:
        from app.db.sql_repo import build_sql_repository   # 延迟导入，避免强依赖
        return build_sql_repository()
    except Exception as exc:                               # noqa: BLE001 任何初始化失败都降级
        print(f"[warn] SQL 仓库不可用（{exc}），已降级为内存仓库（数据为演示集）", file=sys.stderr)
        return build_demo_repository()
