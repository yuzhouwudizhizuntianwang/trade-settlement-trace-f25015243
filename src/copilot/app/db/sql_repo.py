# -*- coding: utf-8 -*-
"""SQLAlchemy 仓库实现：把 domain 对象与数据库行互相转换。

原则：
1. 服务层只认 Repository 协议，不认 SQL —— 换 MySQL 时这里改连接串即可；
2. 金额读出后一律转 Decimal（Numeric 列已是 Decimal，仍显式转一次防驱动差异）；
3. 首次启动自动建表 + 灌演示数据（幂等：有数据就跳过）。
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Base,
    ChargeLineRow,
    ClauseRow,
    FeeItemRow,
    KgTripleRow,
    PassThroughRow,
    RateCardRow,
    RateItemRow,
    ShipmentRow,
    SourceDocRow,
)
from app.domain import (
    ChargeLine,
    Direction,
    FeeItem,
    RateCard,
    RateItem,
    Shipment,
    SourceDoc,
)


class SqlRepository:
    """基于 SQLAlchemy 的仓库实现，接口与 MemoryRepository 完全一致。"""

    def __init__(self, session_factory: sessionmaker):
        """构造仓库。

        入参：session_factory —— SQLAlchemy 会话工厂
        出参：无
        失败行为：不抛异常（连接失败在 build 阶段暴露）。
        """
        self._sf = session_factory

    def _session(self) -> Session:
        """开一个会话（调用方用 with 关闭）。

        入参：无
        出参：Session
        失败行为：连接异常向上抛，由 API 层转 503。
        """
        return self._sf()

    # ---------------- 查询 ----------------
    def shipment_by_key(self, key_type: str, key_value: str) -> Shipment | None:
        """按四键之一查单票。

        入参：key_type —— 四键之一；key_value —— 值
        出参：Shipment 或 None
        失败行为：未知键抛 ValueError。
        """
        col = {"order_no": ShipmentRow.order_no, "booking_no": ShipmentRow.booking_no,
               "bl_no": ShipmentRow.bl_no, "container_no": ShipmentRow.containers}
        if key_type not in col:
            raise ValueError(f"不支持的查询键：{key_type}")
        with self._session() as s:
            stmt = select(ShipmentRow)
            if key_type == "container_no":                 # 箱号存在逗号分隔字段里
                stmt = stmt.where(ShipmentRow.containers.like(f"%{key_value}%"))
            else:
                stmt = stmt.where(col[key_type] == key_value)
            row = s.scalars(stmt).first()
            return self._to_shipment(row) if row else None

    def lines_of(self, shipment_id: str) -> list[ChargeLine]:
        """取一票的费用行。

        入参：shipment_id
        出参：ChargeLine 列表
        失败行为：不抛异常。
        """
        with self._session() as s:
            rows = s.scalars(select(ChargeLineRow).where(
                ChargeLineRow.shipment_id == shipment_id)).all()
            return [self._to_line(r) for r in rows]

    def rate_cards(self, customer_id: str) -> list[RateCard]:
        """取客户全部费率卡版本（按生效日升序）。

        入参：customer_id
        出参：RateCard 列表
        失败行为：不抛异常。
        """
        with self._session() as s:
            cards = s.scalars(select(RateCardRow).where(
                RateCardRow.customer_id == customer_id).order_by(RateCardRow.effective_from)).all()
            out = []
            for c in cards:
                items = s.scalars(select(RateItemRow).where(RateItemRow.card_id == c.id)).all()
                out.append(RateCard(
                    customer_id=c.customer_id, version=c.version,
                    effective_from=c.effective_from, effective_to=c.effective_to,
                    approved_by=c.approved_by or "",
                    items=tuple(RateItem(fee_code=i.fee_code, unit_price=Decimal(i.unit_price),
                                         currency=i.currency, unit=i.unit,
                                         clause_no=i.clause_no or "") for i in items),
                ))
            return out

    def fee_item(self, code: str) -> FeeItem | None:
        """按代码取费用项。

        入参：code
        出参：FeeItem 或 None
        失败行为：不抛异常。
        """
        with self._session() as s:
            row = s.get(FeeItemRow, code)
            return FeeItem(row.code, row.name, row.category) if row else None

    def source_doc(self, doc_no: str) -> SourceDoc | None:
        """按单号取来源单据。

        入参：doc_no
        出参：SourceDoc 或 None
        失败行为：不抛异常。
        """
        with self._session() as s:
            row = s.get(SourceDocRow, doc_no)
            return SourceDoc(row.doc_no, row.doc_type, row.doc_date, row.file_ref,
                             row.counterparty or "") if row else None

    def clauses_for(self, fee_code: str) -> list[dict]:
        """取与费用项相关的条款。

        入参：fee_code
        出参：条款 dict 列表
        失败行为：不抛异常。
        """
        with self._session() as s:
            rows = s.scalars(select(ClauseRow)).all()
            return [{"doc_no": r.doc_no, "clause_no": r.clause_no, "title": r.title,
                     "text": r.text, "fee_codes": (r.fee_codes or "").split(",")}
                    for r in rows if fee_code in (r.fee_codes or "").split(",")]

    def pass_through_charges(self, shipment_id: str) -> list[dict]:
        """取实报实销费用。

        入参：shipment_id
        出参：dict 列表
        失败行为：不抛异常。
        """
        with self._session() as s:
            rows = s.scalars(select(PassThroughRow).where(
                PassThroughRow.shipment_id == shipment_id)).all()
            return [{"shipment_id": r.shipment_id, "fee_code": r.fee_code,
                     "amount": Decimal(r.amount), "currency": r.currency,
                     "source_doc_no": r.source_doc_no, "clause_no": r.clause_no,
                     "note": r.note} for r in rows]

    def triple_search(self, subject: str | None = None, predicate: str | None = None,
                      obj: str | None = None) -> list[dict]:
        """按三元组端点查图谱边。

        入参：subject / predicate / obj（任一可为 None）
        出参：边 dict 列表
        失败行为：不抛异常。
        """
        with self._session() as s:
            stmt = select(KgTripleRow)
            if subject:
                stmt = stmt.where(KgTripleRow.subject == subject)
            if predicate:
                stmt = stmt.where(KgTripleRow.predicate == predicate)
            if obj:
                stmt = stmt.where(KgTripleRow.object == obj)
            return [{"subject": r.subject, "predicate": r.predicate, "object": r.object,
                     "source_ref": r.source_ref, "confidence": r.confidence}
                    for r in s.scalars(stmt).all()]

    # ---------------- 转换 ----------------
    @staticmethod
    def _to_shipment(row: ShipmentRow) -> Shipment:
        """行 → 领域对象。

        入参：row —— ShipmentRow
        出参：Shipment
        失败行为：不抛异常。
        """
        return Shipment(
            shipment_id=row.shipment_id, customer_id=row.customer_id,
            order_no=row.order_no, booking_no=row.booking_no, bl_no=row.bl_no,
            bl_date=row.bl_date, route=row.route, ctn_type=row.ctn_type,
            containers=tuple(x for x in (row.containers or "").split(",") if x),
            customer_name=row.customer_name or "",
        )

    @staticmethod
    def _to_line(row: ChargeLineRow) -> ChargeLine:
        """行 → 费用行。

        入参：row —— ChargeLineRow
        出参：ChargeLine
        失败行为：不抛异常。
        """
        return ChargeLine(
            line_id=row.line_id, shipment_id=row.shipment_id,
            direction=Direction(row.direction), fee_code=row.fee_code,
            amount=Decimal(row.amount), currency=row.currency,
            container_no=row.container_no or "", source_doc_no=row.source_doc_no or "",
            rate_version=row.rate_version or "", remark=row.remark or "",
        )


def _seed(session: Session) -> None:
    """首次建库时灌入演示数据（幂等）。

    入参：session —— 会话
    出参：无
    失败行为：数据已存在则直接返回；异常向上抛（建库失败应当暴露）。
    """
    if session.scalars(select(ShipmentRow)).first():
        return
    from app.repo import build_demo_repository as _demo
    demo = _demo()

    for code, item in demo._fee_items.items():                       # noqa: SLF001 演示数据
        session.add(FeeItemRow(code=code, name=item.name, category=item.category))

    for card in demo._rate_cards:                                     # noqa: SLF001
        row = RateCardRow(customer_id=card.customer_id, version=card.version,
                          effective_from=card.effective_from, effective_to=card.effective_to,
                          approved_by=card.approved_by)
        session.add(row)
        session.flush()                                               # 拿到自增 id
        for it in card.items:
            session.add(RateItemRow(card_id=row.id, fee_code=it.fee_code,
                                    unit_price=it.unit_price, currency=it.currency,
                                    unit=it.unit, clause_no=it.clause_no))

    for s in demo._shipments.values():                                # noqa: SLF001
        session.add(ShipmentRow(
            shipment_id=s.shipment_id, customer_id=s.customer_id,
            customer_name=s.customer_name, order_no=s.order_no, booking_no=s.booking_no,
            bl_no=s.bl_no, bl_date=s.bl_date, route=s.route, ctn_type=s.ctn_type,
            containers=",".join(s.containers)))

    for l in demo._lines:                                             # noqa: SLF001
        session.add(ChargeLineRow(
            line_id=l.line_id, shipment_id=l.shipment_id, direction=l.direction.value,
            fee_code=l.fee_code, amount=l.amount, currency=l.currency,
            container_no=l.container_no, source_doc_no=l.source_doc_no,
            rate_version=l.rate_version, remark=l.remark))

    for d in demo._docs.values():                                     # noqa: SLF001
        session.add(SourceDocRow(doc_no=d.doc_no, doc_type=d.doc_type, doc_date=d.doc_date,
                                 file_ref=d.file_ref, counterparty=d.counterparty))

    for c in demo._clauses:                                           # noqa: SLF001
        session.add(ClauseRow(doc_no=c["doc_no"], clause_no=c["clause_no"],
                              title=c["title"], text=c["text"],
                              fee_codes=",".join(c.get("fee_codes") or [])))

    for t in demo._triples:                                           # noqa: SLF001
        session.add(KgTripleRow(subject=t["subject"], predicate=t["predicate"],
                                object=t["object"], source_ref=t.get("source_ref", ""),
                                confidence=t.get("confidence", "1.0")))

    for p in demo._pass_through:                                      # noqa: SLF001
        session.add(PassThroughRow(shipment_id=p["shipment_id"], fee_code=p["fee_code"],
                                   amount=p["amount"], currency=p["currency"],
                                   source_doc_no=p["source_doc_no"], clause_no=p["clause_no"],
                                   note=p.get("note", "")))
    session.commit()


def build_sql_repository(db_url: str | None = None) -> SqlRepository:
    """建连接 + 建表 + 灌数据，返回 SQL 仓库。

    入参：db_url —— 连接串（默认 sqlite:///data/app.db，可用 COPILOT_DB_URL 覆盖）
    出参：SqlRepository
    失败行为：
        连接/建表失败时抛异常，由 get_repository() 捕获并降级为内存仓库。
    """
    url = db_url or os.environ.get("COPILOT_DB_URL", "sqlite:///data/app.db")
    Path("data").mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        _seed(session)                                    # 首次启动灌演示数据
    return SqlRepository(factory)
