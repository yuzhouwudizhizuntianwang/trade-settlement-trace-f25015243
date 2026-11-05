# -*- coding: utf-8 -*-
"""TST 数据契约（Stage 02 产出，Stage 05 实现）。

设计依据：
- REQ-01 单票归集：四键（订单/订舱/提单/箱）→ Shipment
- REQ-02 费率校验：RateCard 必须带生效区间与版本号
- REQ-05 追溯链：ChargeLine → ChargeSource → RateCard → Voucher 四级
- RL-1 合规：PriceField 标记敏感字段，禁止进入外部调用

刻意不做的事：
- 不做「最省事」的 dict 传递（字段必须显式，避免 fee_remark 式自由文本）
- 不在模型层做业务判断（判断属于 Stage 05 的引擎）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class ChargeDirection(str, Enum):
    """费用方向。应收 = 向客户收，应付 = 向供应商付。"""

    RECEIVABLE = "AR"
    PAYABLE = "AP"


class ChargeCategory(str, Enum):
    """费用大类。对应 REQ-02 的费率卡匹配维度。

    注意：CargoWare 侧是自由文本 `fee_remark`（E-018），
    因此需要一个归一化映射表把自由文本落到这里的枚举值。
    """

    OCEAN_FREIGHT = "OCEAN_FREIGHT"      # 海运费
    SURCHARGE = "SURCHARGE"              # 附加费（LSS/PSS/BAF/改配）
    CUSTOMS = "CUSTOMS"                  # 报关费
    TRUCKING = "TRUCKING"                # 拖车费
    WAREHOUSE = "WAREHOUSE"              # 仓储费
    AGENCY = "AGENCY"                    # 代理费
    OTHER = "OTHER"                      # 其他（客户投诉「看不懂」的重灾区）


class VerifyStatus(str, Enum):
    """证据状态。对应 docs/06 证据台账的三种状态。"""

    VERIFIED = "已核实"
    PENDING = "待核实"
    CONFLICT = "有矛盾"


@dataclass(frozen=True)
class RateCard:
    """费率卡中的一个条目。版本与生效区间是 REQ-02 的核心。

    反面教材（E-007）：共享盘 7 个 Excel，命名含「最终版」「最终版2」「改」，
    无版本号、无生效期，导致 29/30 票错配。
    """

    customer_id: str
    category: ChargeCategory
    item_code: str                  # 如 LSS / PSS / THC
    unit_price: Decimal
    currency: str                    # CNY / USD / EUR
    unit: str                        # 如 20GP / 40HQ / 票 / 天
    effective_from: date
    effective_to: Optional[date]     # None 表示长期有效
    version: str                     # 如 "2026-V3"
    approved_by: str                 # 审批人（无审批人的费率卡不得出账）

    def covers(self, business_date: date) -> bool:
        """该条目是否覆盖给定业务日期（口径：以提单签发日为准，见 docs/02 M-02）。"""
        if business_date < self.effective_from:
            return False
        if self.effective_to is not None and business_date > self.effective_to:
            return False
        return True


@dataclass(frozen=True)
class ChargeSource:
    """来源单据。REQ-05 追溯链的第一级，缺失即为「断链」。"""

    source_id: str                   # 单号/邮件 ID/文件名
    source_type: str                 # carrier_invoice / customs_bill / email / contract
    received_at: Optional[date]
    file_ref: str                    # 原件存放引用（不得为空，空即断链）
    extract_confidence: Optional[float] = None  # 解析置信度，低于阈值转人工


@dataclass
class ChargeLine:
    """一笔费用行。归集到单票的原子单位（REQ-01）。"""

    line_id: str
    shipment_id: str
    direction: ChargeDirection
    category: ChargeCategory
    item_code: str
    amount: Decimal
    currency: str
    quantity: Decimal = Decimal("1")
    unit: str = "票"

    # 归属四键（REQ-01）：任一为空都可能导致错挂
    order_no: Optional[str] = None
    booking_no: Optional[str] = None
    bl_no: Optional[str] = None
    container_no: Optional[str] = None

    # 追溯与审计
    source: Optional[ChargeSource] = None
    rate_version: Optional[str] = None       # 命中的 RateCard.version
    entered_by: Optional[str] = None
    entered_at: Optional[date] = None

    # 校验结果（由 REQ-04 的引擎写入，不在本层判断）
    matched_rate: bool = False
    is_missing: bool = False                 # 应有未录
    is_duplicate: bool = False
    verify_status: VerifyStatus = VerifyStatus.PENDING

    def is_traceable(self) -> bool:
        """REQ-05 的最小判定：有来源单据且有原件引用。"""
        return bool(self.source and self.source.file_ref)

    def trace_break(self) -> list[str]:
        """返回断链的层级名，空列表表示完整。用于 AC-05 的「不得虚假完整」。"""
        breaks: list[str] = []
        if not self.source:
            breaks.append("来源单据")
        elif not self.source.file_ref:
            breaks.append("原件引用")
        if not self.rate_version:
            breaks.append("费率依据")
        if not self.entered_by:
            breaks.append("录入责任")
        return breaks


@dataclass
class Shipment:
    """一票业务。REQ-01 的归集容器。"""

    shipment_id: str
    customer_id: str
    order_no: str
    booking_no: str
    bl_no: Optional[str] = None
    bl_date: Optional[date] = None            # 结算周期起点（docs/02 M-02）
    containers: list[str] = field(default_factory=list)
    charges: list[ChargeLine] = field(default_factory=list)

    def ar_total(self) -> Decimal:
        return sum(
            (c.amount for c in self.charges if c.direction is ChargeDirection.RECEIVABLE),
            Decimal("0"),
        )

    def ap_total(self) -> Decimal:
        return sum(
            (c.amount for c in self.charges if c.direction is ChargeDirection.PAYABLE),
            Decimal("0"),
        )

    def unbilled_risk(self) -> list[ChargeLine]:
        """开票前需要人看的行：未匹配费率 / 疑似缺项 / 疑似重复 / 断链。"""
        risky = []
        for c in self.charges:
            if c.direction is not ChargeDirection.RECEIVABLE:
                continue
            if not c.matched_rate or c.is_missing or c.is_duplicate or not c.is_traceable():
                risky.append(c)
        return risky


@dataclass(frozen=True)
class TraceLink:
    """追溯链上的一环，用于前端展示与导出（REQ-05 / REQ-10）。"""

    level: int
    label: str                  # 来源单据 / 费率条款 / 承担方 / 凭证
    value: str
    evidence_id: Optional[str] = None   # 对应 docs/06 的 E- 编号
    missing: bool = False
