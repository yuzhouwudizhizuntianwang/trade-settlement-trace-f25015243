"""贸易服务结算与费用追溯（TST）· 数据契约雏形。

阶段说明：本包在 Stage 02 结束时只固化「数据契约」（字段与关系），
业务逻辑在 Stage 05 构建集成时实现。此处代码的作用是让 docs/07 的
REQ-01 / REQ-02 / REQ-05 有可评审的字段依据。

禁止在此放任何真实客户数据。
"""

from .models import (  # noqa: F401
    ChargeCategory,
    ChargeDirection,
    ChargeLine,
    ChargeSource,
    RateCard,
    Shipment,
    TraceLink,
    VerifyStatus,
)

__all__ = [
    "Shipment",
    "ChargeLine",
    "RateCard",
    "ChargeSource",
    "TraceLink",
    "ChargeDirection",
    "ChargeCategory",
    "VerifyStatus",
]

__version__ = "0.1.0-stage02"
