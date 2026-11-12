# -*- coding: utf-8 -*-
"""FastAPI 入口（Phase 1：骨架 + 鉴权 + 红线闸门演示）。

Phase 1 只保证三件事能跑通：
1. /health 能起来（证明依赖与配置正确）；
2. /auth/login 能签发令牌、/auth/me 能用令牌换回身份与能力；
3. /redline/preview 能证明"带价格字段的数据出不去"。
业务查询（费用归集、差异比对、追溯链）留到 Phase 2，避免骨架还没稳就堆业务。
"""

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.redline import OutboundFilter, RedlineViolation
from app.core.response import fail, ok
from app.core.security import (
    TokenError,
    abilities,
    assert_customer_allowed,
    create_token,
    verify_token,
)

app = FastAPI(title="结算复核助手 · settlement-copilot", version="0.1.0-phase1")

SETTINGS = get_settings()                                        # 启动即读配置（缺失就崩，见 config.py）
LLM = LLMClient(                                                # 模型客户端：provider=mock 时离线可用
    provider=SETTINGS.get("llm.provider", "mock"),
    base_url=SETTINGS.get("llm.base_url", ""),
    model=SETTINGS.get("llm.model", ""),
    api_key=SETTINGS.get("llm.api_key", ""),
    timeout=int(SETTINGS.get("llm.timeout_seconds", 30)),
    max_retries=int(SETTINGS.get("llm.max_retries", 1)),
    fallback=str(SETTINGS.get("llm.fallback", "rules")),
)
OUTBOUND = OutboundFilter(                                       # 出网过滤器全局只造一个
    allow_fields=SETTINGS.get("redline.outbound_allow_fields", []),
    deny_fields=SETTINGS.get("redline.outbound_deny_fields", []),
)


class LoginBody(BaseModel):
    """登录请求体。只允许账号口令两个字段，多传的字段会被 pydantic 忽略。"""

    username: str
    password: str


def _unauthorized(code: str, msg: str) -> JSONResponse:
    """统一构造 401 响应。

    入参：code —— 错误码；msg —— 中文说明
    出参：JSONResponse（HTTP 401，body 为统一结构）
    失败行为：不抛异常。
    """
    return JSONResponse(status_code=401, content=fail(code, msg))


def current_user(authorization: str = Header(default="")) -> dict:
    """从 Authorization 头解出当前用户（FastAPI 依赖）。

    入参：authorization —— 形如 "Bearer xxx.yyy.zzz"
    出参：令牌 claims（含 sub / role / scope / abilities）
    失败行为：
        缺头、格式错、签名错、过期——一律抛 HTTPException(401)。
        注意：这里绝不读取请求体里的 username/role，身份只能来自我们签发的令牌。
    """
    if not authorization.startswith("Bearer "):                  # 必须带 Bearer 前缀
        raise HTTPException(status_code=401, detail="缺少 Bearer 令牌")
    token = authorization[len("Bearer "):].strip()
    try:
        claims = verify_token(token, SETTINGS.get("auth.jwt_secret", ""))
    except TokenError as exc:                                    # 统一转 401，不泄漏内部细节
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    claims["abilities"] = abilities(claims.get("role", ""))      # 能力表随身份一起返回
    return claims


@app.get("/health")
def health() -> dict:
    """健康检查。

    入参：无（走 GET）
    出参：统一结构，data 含服务名、环境、知识库提供方、配置是否齐全
    失败行为：
        配置缺失时不抛异常，而是在 data.missing 里列出来——健康检查要能"带着病"回答，
        让运维看到缺什么，而不是直接 500。
    """
    missing = SETTINGS.validate([
        "auth.jwt_secret",
        "llm.base_url",
        "knowledge.harness.endpoint",
    ])
    return ok({
        "service": SETTINGS.get("app.name"),
        "env": SETTINGS.get("app.env"),
        "knowledge_provider": SETTINGS.get("knowledge.provider"),
        "missing": missing,                                      # 空列表表示配置齐全
    })


@app.post("/auth/login")
def login(body: LoginBody) -> JSONResponse:
    """登录并签发令牌。

    入参：body.username / body.password（JSON）
    出参：{"ok": true, "data": {"token": "...", "role": "...", "scope": [...]}}
    失败行为：
        账号不存在或口令不符——统一返回 401 且**不区分**是账号错还是口令错，
        避免被用来枚举用户；演示账号来自 config.yaml 的 auth.demo_users。
    """
    users = SETTINGS.get("auth.demo_users", []) or []
    matched = next(
        (u for u in users
         if u.get("username") == body.username and u.get("password") == body.password),
        None,
    )
    if not matched:
        return _unauthorized("UNAUTHORIZED", "账号或口令不正确")

    token = create_token(
        claims={
            "sub": matched["username"],                          # 用户名
            "role": matched["role"],                             # 角色
            "scope": matched.get("customer_scope", []),          # 客户范围（数据权限核心）
        },
        secret=SETTINGS.get("auth.jwt_secret", ""),
        expire_minutes=int(SETTINGS.get("auth.expire_minutes", 120)),
    )
    return JSONResponse(status_code=200, content=ok({
        "token": token,
        "role": matched["role"],
        "scope": matched.get("customer_scope", []),
        "abilities": abilities(matched["role"]),
    }))


@app.get("/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    """返回当前令牌对应的身份与能力。

    入参：user —— 由 current_user 依赖注入
    出参：统一结构，data 含 username / role / scope / abilities
    失败行为：令牌无效时由依赖抛 401，本函数不会被执行。
    """
    return ok({
        "username": user.get("sub"),
        "role": user.get("role"),
        "scope": user.get("scope", []),
        "abilities": user.get("abilities", {}),
    })


@app.post("/redline/preview")
def redline_preview(payload: dict, user: dict = Depends(current_user)) -> JSONResponse:
    """红线闸门演示：把一段业务数据过一遍出网过滤器，返回能发出去的版本与被拦下的字段。

    入参：
        payload —— 任意业务数据（通常是"准备拼进提示词"的费用行集合）
        user    —— 当前身份（用于演示客户范围校验）
    出参：
        统一结构，data = {"clean": 过滤后数据, "blocked": 被拦字段路径, "customer_checked": ...}
    失败行为：
        命中敏感字段不返回 403（这是"预览"接口，要让人看到拦了什么），
        但真实出网调用必须走 assert_safe（抛异常），两者区别见 redline.py 注释。
    """
    customer_id = str(payload.get("customer_id", ""))            # 演示：数据里的客户号
    scope_check = "skipped"                                      # 默认不校验（没传客户号时）
    if customer_id:
        try:
            assert_customer_allowed(user.get("scope", []), customer_id)
            scope_check = "passed"                               # 在范围内
        except PermissionError as exc:
            scope_check = "denied"                               # 越权：真实接口应直接 403
            return JSONResponse(status_code=403, content=fail("FORBIDDEN", str(exc),
                                                              {"scope_check": scope_check}))

    try:
        clean = OUTBOUND.assert_safe(payload)                     # 严格模式：有敏感字段就抛
        blocked: list[str] = []                                   # 没抛说明全都合规
    except RedlineViolation as exc:                               # 预览场景：展示被拦内容
        clean = OUTBOUND.scan(payload)[0]                         # 取裁剪后的副本
        blocked = exc.blocked                                      # 记下具体字段路径

    return JSONResponse(status_code=200, content=ok({
        "clean": clean,
        "blocked": blocked,
        "scope_check": scope_check,
        "role": user.get("role"),
    }))


@app.get("/llm/ping")
def llm_ping() -> dict:
    """模型探活：返回当前 provider、模型名与可达性。

    入参：无
    出参：统一结构，data = {"provider","model","reachable","detail"}
    失败行为：
        不抛异常。模型不可达时 reachable=false，detail 说明原因——
        探活接口必须能"报告故障"，而不是自己也挂掉。
    """
    return ok(LLM.health())


@app.post("/llm/demo")
def llm_demo() -> dict:
    """演示一次完整的"取数 → 生成结论"调用，用于验证模型链路通不通。

    入参：无（内部用一段固定样例，避免把真实客户数据塞进请求）
    出参：统一结构，data = {"text": 生成内容, "mode": ollama/rules, "provider": ...}
    失败行为：
        Ollama 不可达且 fallback=none 时返回 503；fallback=rules 时正常返回，
        并在 text 里明确写"本次由本地规则生成"，不冒充模型输出。
    """
    sample = (
        "请解释这票的应收差异。[[DATA]]"
        '{"differences":[{"kind":"缺项","fee_code":"LSS","fee_name":"低硫附加费",'
        '"amount":"1180.00","currency":"CNY","evidence":"HC-FIN-2024-07 §5.2"}],'
        '"summary":"共发现 1 项差异："}[[/DATA]]'
    )
    try:
        text = LLM.chat([{"role": "system", "content": "你是结算复核助手。"},
                         {"role": "user", "content": sample}])
    except Exception as exc:                              # noqa: BLE001 模型异常统一转 503
        return JSONResponse(status_code=503,
                            content=fail("LLM_UNAVAILABLE", f"模型不可用：{exc}"))
    return ok({"text": text, "mode": getattr(LLM, "last_mode", "unknown"),
               "provider": SETTINGS.get("llm.provider"), "model": SETTINGS.get("llm.model")})
