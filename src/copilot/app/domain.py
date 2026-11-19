# -*- coding: utf-8 -*-
"""领域模型（纯 Python，不依赖数据库 / Web 框架）。

为什么单独一层：
1. 业务规则（费率匹配、差异识别、追溯链）必须能脱离数据库单测；
2. 换 SQLite→MySQL 时这一层不动；
3. 金额一律 Decimal，模型层就挡住 float。

约定：字段名与 docs/技术方案.md §5 的表结构一一对应，改这里要同步改 db/models.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Direction(str, Enum):
    """费用方向：应收（向客户收）/ 应付（向供应商付）。"""

    AR = "AR"
    AP = "AP"


class DiffKind(str, Enum):
    """差异类型。P0 只做前三类，其余留 P1-5。"""

    MISSING = "缺项"          # 应有未录
    DUPLICATE = "重复"        # 同一费用项重复计入
    AMOUNT = "金额不符"       # 同项金额不一致
    UNMATCHED = "未匹配"      # 有已录但没有应有依据（P1 再细分归因）


@dataclass(frozen=True)
class FeeItem:
    """费用项字典（LSS / PSS / THC 这类）。"""

    code: str
    name: str
    category: str = "SURCHARGE"      # 与 CargoWare 自由文本的归一结果对应


@dataclass(frozen=True)
class RateItem:
    """费率卡里的一条：某费用项在该版本下的价格。

    clause_no 记录这一条依据的制度条款，追溯链第 2 级要用。
    """

    fee_code: str
    unit_price: Decimal
    currency: str
    unit: str = "40HQ"
    clause_no: str = ""


@dataclass(frozen=True)
class RateCard:
    """费率卡版本。

    生效区间是硬约束：出账必须用覆盖提单签发日的版本（制度 §5.2）。
    """

    customer_id: str
    version: str
    effective_from: date
    effective_to: date | None
    approved_by: str
    items: tuple[RateItem, ...] = ()

    def covers(self, business_date: date) -> bool:
        """判断该版本是否覆盖给定业务日期。

        入参：business_date —— 通常取提单签发日
        出参：True 表示覆盖
        失败行为：不抛异常；effective_to 为 None 视为长期有效。
        """
        if business_date < self.effective_from:
            return False
        if self.effective_to is not None and business_date > self.effective_to:
            return False
        return True


@dataclass(frozen=True)
class SourceDoc:
    """来源单据（船司账单 / 客户账单 / 邮件）。file_ref 为空即断链。"""

    doc_no: str
    doc_type: str
    doc_date: date
    file_ref: str = ""
    counterparty: str = ""


@dataclass(frozen=True)
class ChargeLine:
    """已录入的一行费用（来自客户账单或系统）。"""

    line_id: str
    shipment_id: str
    direction: Direction
    fee_code: str
    amount: Decimal
    currency: str
    container_no: str = ""
    source_doc_no: str = ""
    rate_version: str = ""
    remark: str = ""


@dataclass(frozen=True)
class Shipment:
    """一票业务。bl_date 是结算周期起点，也是费率版本取值依据。"""

    shipment_id: str
    customer_id: str
    order_no: str
    booking_no: str
    bl_no: str
    bl_date: date
    route: str
    ctn_type: str
    containers: tuple[str, ...] = ()
    customer_name: str = ""

    def keys(self) -> dict[str, str]:
        """返回四键映射，供归集服务按任一键查询。

        入参：无
        出参：{"order_no": ..., "booking_no": ..., "bl_no": ..., "container_no": ...}
        失败行为：不抛异常；无箱号时 container_no 取空串。
        """
        return {
            "order_no": self.order_no,
            "booking_no": self.booking_no,
            "bl_no": self.bl_no,
            "container_no": self.containers[0] if self.containers else "",
        }


@dataclass(frozen=True)
class RequiredCharge:
    """"应有费用"——由费率卡 + 作业项算出来，而不管有没有录。

    basis 是可读依据（"费率卡 V4 + 制度 §5.2"），追溯链与前端都要显示它。
    """

    fee_code: str
    fee_name: str
    amount: Decimal
    currency: str
    rate_version: str
    clause_no: str
    basis: str
    source_doc_no: str = ""          # 由某张单据驱动时填写（如改配费来自船司账单）


@dataclass
class Difference:
    """一条差异。P0 只填前五个字段，归因字段留 P1。"""

    kind: DiffKind
    fee_code: str
    fee_name: str
    amount: Decimal
    currency: str
    expected: str = ""               # 应有（金额或"应存在"）
    actual: str = ""                 # 实有
    reason: str = ""
    evidence: str = ""               # 单据号或条款号
    trace: list[dict] = field(default_factory=list)   # 前端直接渲染的追溯链


@dataclass(frozen=True)
class TraceLevel:
    """追溯链的一级。missing=True 表示断在这里。"""

    level: int
    label: str
    value: str
    evidence: str = ""
    missing: bool = False


@dataclass(frozen=True)
class KbHit:
    """知识库检索命中。必须带 doc_no + clause_no，否则视为无效命中。"""

    doc_no: str
    clause_no: str
    title: str
    snippet: str
    score: float
    source: str = "harness"          # harness / local_mirror
