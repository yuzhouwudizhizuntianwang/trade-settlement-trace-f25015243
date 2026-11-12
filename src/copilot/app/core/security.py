# -*- coding: utf-8 -*-
"""身份与权限：HS256 JWT（标准库实现）+ 角色权限表 + 客户数据范围校验。

为什么不用第三方 JWT 库：课设交付要求"换台机器就能跑"，PyJWT 依赖会增加安装失败概率；
HS256 只需 hmac + sha256 + base64url，标准库足够，且实现过程可被老师逐行审阅。

两条硬规矩：
1. 用户身份只从 token 里取，绝不接受请求体/对话内容里自称的"我是财务"。
2. 能访问哪些客户，由 token 里的 scope 推导，不从 URL 参数或表单里取。
   原因：参数是用户可控的，token 是我们签发的；前者能被改，后者改不了（没密钥）。
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Iterable

# 角色常量：写死在这里，避免各处手写字符串拼错
ROLE_SETTLER = "settler"      # 结算专员：可看价格，仅限自己负责的客户
ROLE_OPERATOR = "operator"    # 单证操作员：看不到价格与毛利
ROLE_REVIEWER = "reviewer"    # 复核人：可审批补收/减免、可导出
ROLE_AUDITOR = "auditor"      # 合规/审计：只读日志与导出记录

# 角色 → 能力开关。注意：这里是"能不能"的粗粒度判断，具体到单据还要过客户范围校验
ROLE_ABILITIES: dict[str, dict[str, bool]] = {
    ROLE_SETTLER: {"view_price": True, "export": False, "approve": False, "view_audit": False},
    ROLE_OPERATOR: {"view_price": False, "export": False, "approve": False, "view_audit": False},
    ROLE_REVIEWER: {"view_price": True, "export": True, "approve": True, "view_audit": True},
    ROLE_AUDITOR: {"view_price": False, "export": False, "approve": False, "view_audit": True},
}


class TokenError(Exception):
    """令牌缺失、格式错误、签名不符或已过期时抛出。

    入参：message —— 中文说明；code —— 建议的错误码（UNAUTHORIZED / TOKEN_EXPIRED）
    出参：无
    失败行为：由路由层捕获并转成 401，绝不把原始异常暴露给前端。
    """

    def __init__(self, message: str, code: str = "UNAUTHORIZED"):
        super().__init__(message)
        self.code = code


def _b64e(raw: bytes) -> str:
    """base64url 编码（去掉 = 填充，符合 JWT 规范）。

    入参：raw —— 原始字节
    出参：URL 安全字符串
    失败行为：不抛异常。
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    """base64url 解码（自动补回 = 填充）。

    入参：text —— URL 安全字符串
    出参：原始字节
    失败行为：非法字符串抛 TokenError，不向上层泄漏 binascii 异常。
    """
    padding = "=" * (-len(text) % 4)                  # 补齐到 4 的倍数
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception as exc:                          # noqa: BLE001 统一转成令牌错误
        raise TokenError("令牌编码非法") from exc


def create_token(claims: dict, secret: str, expire_minutes: int) -> str:
    """签发 HS256 JWT。

    入参：
        claims         —— 载荷，至少含 sub（用户名）、role（角色）、scope（客户号列表或 ["*"]）
        secret         —— 签名密钥，来自环境变量
        expire_minutes —— 有效期（分钟）
    出参：形如 xxx.yyy.zzz 的令牌字符串
    失败行为：
        claims 缺少 sub/role/scope 时抛 ValueError —— 缺字段的令牌等于没有权限信息，不如不签。
    """
    for required in ("sub", "role", "scope"):
        if required not in claims:                    # 缺字段直接拒绝签发
            raise ValueError(f"签发令牌缺少字段：{required}")

    header = {"alg": "HS256", "typ": "JWT"}           # 固定头部，不允许调用方改算法
    payload = {
        **claims,
        "iat": int(time.time()),                      # 签发时间
        "exp": int(time.time()) + expire_minutes * 60,  # 过期时间（秒）
    }
    segments = [
        _b64e(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
        _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")           # 待签名内容：header.payload
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return ".".join(segments + [_b64e(signature)])               # 拼成三段式


def verify_token(token: str, secret: str) -> dict:
    """校验并解出令牌载荷。

    入参：
        token  —— 待校验令牌
        secret —— 签名密钥
    出参：claims 字典（含 sub/role/scope/exp）
    失败行为：
        缺段、签名不符、过期——三种情况都抛 TokenError，并带上不同 code，便于前端区分。
        校验签名用 hmac.compare_digest（常数时间比较），防时序攻击。
    """
    if not token or token.count(".") != 2:            # 必须是三段
        raise TokenError("令牌格式非法")
    header_b64, payload_b64, signature_b64 = token.split(".")

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, _b64d(signature_b64)):
        raise TokenError("令牌签名校验失败")          # 伪造或密钥不一致

    claims: dict[str, Any] = json.loads(_b64d(payload_b64).decode("utf-8"))
    if int(claims.get("exp", 0)) < int(time.time()):  # 过期判断放在签名之后，避免被伪造的 exp 误导
        raise TokenError("令牌已过期，请重新登录", code="TOKEN_EXPIRED")
    return claims


def abilities(role: str) -> dict[str, bool]:
    """取角色能力表。

    入参：role —— 角色常量字符串
    出参：能力字典（view_price / export / approve / view_audit）
    失败行为：
        未知角色返回全 False（默认拒绝），不抛异常——权限判断失败时必须"关"而不是"开"。
    """
    return ROLE_ABILITIES.get(role, {"view_price": False, "export": False,
                                     "approve": False, "view_audit": False})


def assert_customer_allowed(scope: Iterable[str], customer_id: str) -> None:
    """校验令牌的数据范围是否覆盖目标客户。

    入参：
        scope       —— 令牌里的客户号列表，["*"] 表示全量
        customer_id —— 本次要访问的客户号
    出参：无（通过则静默返回）
    失败行为：
        不在范围内抛 PermissionError —— 由路由层转 403 并记审计。
        为什么不用布尔返回：漏写 if 的代价是跨客户数据泄露，异常更难以被"忘记处理"。
    """
    scope_list = list(scope or [])
    if "*" in scope_list:                             # 全量授权（复核人、审计）
        return
    if customer_id not in scope_list:                 # 精确匹配，不做前缀/模糊匹配
        raise PermissionError(f"无权访问客户 {customer_id} 的数据")
