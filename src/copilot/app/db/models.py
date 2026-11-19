# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 表定义（开发 SQLite / 生产 MySQL 8 一套代码）。

与 app/domain.py 一一对应；金额统一 DECIMAL(18,2)，禁止 float 列。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有表的基类。"""


class FeeItemRow(Base):
    """费用项字典。"""

    __tablename__ = "fee_item"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="OTHER")


class RateCardRow(Base):
    """费率卡版本（一条记录一个版本）。"""

    __tablename__ = "rate_card"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(16))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(32), default="")


class RateItemRow(Base):
    """费率项：挂在某个费率卡版本下。"""

    __tablename__ = "rate_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("rate_card.id"), index=True)
    fee_code: Mapped[str] = mapped_column(String(16))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))     # 金额禁用 float
    currency: Mapped[str] = mapped_column(String(8))
    unit: Mapped[str] = mapped_column(String(16), default="40HQ")
    clause_no: Mapped[str] = mapped_column(String(64), default="")


class ShipmentRow(Base):
    """单票。"""

    __tablename__ = "shipment"
    shipment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    customer_name: Mapped[str] = mapped_column(String(64), default="")
    order_no: Mapped[str] = mapped_column(String(32), index=True)
    booking_no: Mapped[str] = mapped_column(String(32), index=True)
    bl_no: Mapped[str] = mapped_column(String(32), index=True)
    bl_date: Mapped[date] = mapped_column(Date)
    route: Mapped[str] = mapped_column(String(32), default="")
    ctn_type: Mapped[str] = mapped_column(String(16), default="40HQ")
    containers: Mapped[str] = mapped_column(String(128), default="")   # 逗号分隔，够用


class ChargeLineRow(Base):
    """费用行（应收/应付）。"""

    __tablename__ = "charge_line"
    line_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipment.shipment_id"), index=True)
    direction: Mapped[str] = mapped_column(String(4))                  # AR / AP
    fee_code: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    container_no: Mapped[str] = mapped_column(String(32), default="")
    source_doc_no: Mapped[str] = mapped_column(String(64), default="")
    rate_version: Mapped[str] = mapped_column(String(16), default="")
    remark: Mapped[str] = mapped_column(Text, default="")


class SourceDocRow(Base):
    """来源单据。"""

    __tablename__ = "source_doc"
    doc_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    doc_date: Mapped[date] = mapped_column(Date)
    file_ref: Mapped[str] = mapped_column(String(256), default="")
    counterparty: Mapped[str] = mapped_column(String(64), default="")


class ClauseRow(Base):
    """制度条款（与 Harness 同源，本地只留摘要与出处）。"""

    __tablename__ = "policy_clause"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_no: Mapped[str] = mapped_column(String(64), index=True)
    clause_no: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    fee_codes: Mapped[str] = mapped_column(String(256), default="")     # 逗号分隔


class KgTripleRow(Base):
    """知识图谱三元组：每条边都带 source_ref，保证可回溯。"""

    __tablename__ = "kg_triple"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(96), index=True)
    predicate: Mapped[str] = mapped_column(String(32), index=True)
    object: Mapped[str] = mapped_column(String(96), index=True)
    source_ref: Mapped[str] = mapped_column(String(128), default="")
    confidence: Mapped[str] = mapped_column(String(8), default="1.0")


class PassThroughRow(Base):
    """实报实销费用（如改配费），不挂费率卡，凭单据计收。"""

    __tablename__ = "pass_through"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[str] = mapped_column(String(32), index=True)
    fee_code: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    source_doc_no: Mapped[str] = mapped_column(String(64), default="")
    clause_no: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
