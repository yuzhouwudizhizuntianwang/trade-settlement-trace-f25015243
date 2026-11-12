# -*- coding: utf-8 -*-
"""数据红线：出网字段白名单过滤 + 金额精度保护。

这是整个系统唯一允许"接触外部模型"的出口，所有发给 LLM / Harness 的 payload 必须过这里。
为什么卡这么死：客户费率、单价、毛利属商业秘密（制度第十五条），一旦随提示词发到外部，
既无法撤回也无法证明没发生；事后追责没有意义，只能在发出前拦。
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


class RedlineViolation(Exception):
    """命中数据红线时抛出。

    入参：message —— 中文说明；blocked —— 被拦截的字段路径列表
    出参：无（异常对象）
    失败行为：由调用方捕获后转成 {"ok": false, "error": {"code": "REDLINE_BLOCKED"}}
    """

    def __init__(self, message: str, blocked: Iterable[str] | None = None):
        super().__init__(message)                 # 交给基类保存文本
        self.blocked = list(blocked or [])        # 记录具体字段，便于审计与前端提示


class OutboundFilter:
    """出网字段过滤器（白名单 + 黑名单双保险）。

    设计取舍：只用白名单会漏掉新增字段（新加的 price_v2 自动放行），只用黑名单会漏掉
    没起对名字的字段（cost、unit_amt）。所以两个都做：先按黑名单拦，再按白名单裁。
    """

    def __init__(self, allow_fields: Iterable[str], deny_fields: Iterable[str]):
        """构造过滤器。

        入参：
            allow_fields —— 允许出网的字段名（大小写不敏感）
            deny_fields  —— 命中即拦截的敏感字段名（大小写不敏感，含中文别名）
        出参：无
        失败行为：
            两个参数之一为空时抛 ValueError —— 空白名单等于"什么都放行"，必须让启动失败。
        """
        # 归一化：去空格、转小写，避免 "Amount" 和 "amount " 绕过
        self.allow = {str(f).strip().lower() for f in allow_fields or []}
        self.deny = {str(f).strip().lower() for f in deny_fields or []}
        if not self.allow or not self.deny:
            raise ValueError("出网白名单/黑名单不能为空：空白名单等于放行一切，不允许启动")

    def _key_hit(self, key: str, pool: set[str]) -> bool:
        """判断字段名是否命中某个集合（支持包含匹配，防 price_v2 这类变体）。

        入参：key —— 字段名；pool —— 白名单或黑名单集合
        出参：True 表示命中
        失败行为：key 非字符串时转成字符串再判断，不抛异常。
        """
        k = str(key).strip().lower()                       # 统一小写，避免大小写绕过
        if k in pool:                                      # 精确命中
            return True
        return any(token in k for token in pool if token)  # 包含命中：price_v2、unit_price_cny 都能抓到

    def scan(self, payload: Any, path: str = "") -> tuple[Any, list[str]]:
        """递归扫描并裁剪 payload，返回"可出网"的副本与被拦截字段路径。

        入参：
            payload —— 待发送数据（dict / list / 标量）
            path    —— 当前递归路径，用于审计时定位（如 data.charges[0].amount）
        出参：
            (clean_payload, blocked_paths)
            clean_payload：容器结构保留，叶子字段只保留白名单内的；blocked_paths 记录被剔除的字段路径
        失败行为：
            不抛异常。遇到无法遍历的对象（自定义类实例）时按标量处理，直接返回原值。
        """
        blocked: list[str] = []

        # 字典：逐键决定保留还是剔除
        if isinstance(payload, dict):
            clean: dict[str, Any] = {}
            for key, value in payload.items():
                child_path = f"{path}.{key}" if path else str(key)
                if self._key_hit(key, self.deny):           # 黑名单优先：宁可错杀
                    blocked.append(child_path)
                    continue
                # 容器（dict / list）不做白名单判断，直接下钻：
                # 因为容器名千变万化（charges、lines、items），但里面的叶子字段仍会被逐个过滤，
                # 安全性由叶子层保证，不会因为"容器名没登记"就把整块业务数据丢掉。
                if isinstance(value, (dict, list, tuple)):
                    child, sub_blocked = self.scan(value, child_path)
                    clean[key] = child
                    blocked.extend(sub_blocked)
                    continue
                if not self._key_hit(key, self.allow):      # 标量且不在白名单：不发
                    blocked.append(child_path)
                    continue
                clean[key] = value                          # 标量且命中白名单：保留
            return clean, blocked

        # 列表：逐元素扫描，任一元素命中都记录
        if isinstance(payload, (list, tuple)):
            clean_list = []
            for idx, item in enumerate(payload):
                cleaned, sub_blocked = self.scan(item, f"{path}[{idx}]")
                clean_list.append(cleaned)
                blocked.extend(sub_blocked)
            return clean_list, blocked

        # 标量：直接返回（注意：标量本身无法判断语义，所以金额必须在字段名层面被拦掉）
        return payload, blocked

    def assert_safe(self, payload: Any) -> Any:
        """严格模式：只要有任何字段被拦截就抛异常，用于"必须完整出网"的场景。

        入参：payload —— 待发送数据
        出参：干净的副本
        失败行为：
            命中敏感字段时抛 RedlineViolation（带 blocked 路径），调用方必须转成 403。
        """
        clean, blocked = self.scan(payload)
        if blocked:
            raise RedlineViolation(
                f"请求包含不得出网的字段：{', '.join(blocked[:5])}", blocked
            )
        return clean


def to_decimal(value: Any, field: str = "amount") -> Decimal:
    """把入参安全转成 Decimal，禁止 float 参与金额运算。

    入参：
        value —— 数字或字符串（"1,180.00" 这种带千分位的也支持）
        field —— 字段名，仅用于报错提示
    出参：Decimal 实例
    失败行为：
        无法解析时抛 ValueError，并说明是哪个字段——金额算错比报错更危险，宁可中断。
    """
    if isinstance(value, Decimal):                 # 已经是 Decimal 直接返回
        return value
    if isinstance(value, float):                   # float 一律拒绝：0.1+0.2 的教训
        raise ValueError(f"字段 {field} 不允许传 float，请传字符串或 Decimal")
    try:
        return Decimal(str(value).replace(",", "").strip())  # 去千分位后再解析
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"字段 {field} 无法解析为金额：{value!r}") from exc


def sum_amounts(items: Iterable[Any], field: str = "amount") -> Decimal:
    """按 Decimal 汇总一组金额。

    入参：
        items —— 可迭代对象，元素为 dict（取 field）或可直接转 Decimal 的值
        field —— dict 中的金额字段名
    出参：Decimal 合计
    失败行为：
        任一元素无法解析时抛 ValueError；空集合返回 Decimal("0")。
    """
    total = Decimal("0")                            # 用 Decimal 起点，避免混入 float
    for item in items:
        raw = item.get(field) if isinstance(item, dict) else item
        total += to_decimal(raw, field)             # 逐个累加，出错即中断
    return total
