# -*- coding: utf-8 -*-
"""FastAPI 入口（Phase 1：骨架 + 鉴权 + 红线闸门演示）。

Phase 1 只保证三件事能跑通：
1. /health 能起来（证明依赖与配置正确）；
2. /auth/login 能签发令牌、/auth/me 能用令牌换回身份与能力；
3. /redline/preview 能证明"带价格字段的数据出不去"。
业务查询（费用归集、差异比对、追溯链）留到 Phase 2，避免骨架还没稳就堆业务。
"""

import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.redline import OutboundFilter, RedlineViolation
from app.core.response import fail, ok
from app.core.audit import AuditLog
from app.repo import get_repository
from app.services.collect import build_view, get_shipment
from app.services.diff import compute_diff
from app.services.graph import expand, why_applicable
from app.services.kb import build_knowledge_provider
from app.services.rate import RateCardMissing
from app.services.metrics import compute as compute_metrics
from app.services.review import ReviewError, ReviewStore
from app.services.export import ExportViolation, build_pack
from app.core.security import (
    TokenError,
    abilities,
    assert_customer_allowed,
    create_token,
    verify_token,
)

app = FastAPI(title="结算复核助手 · settlement-copilot", version="0.2.0-p0")

# 启动时构建数据仓库与知识库：SQLite 优先，缺依赖自动降级为内存演示库（stderr 会提示）
REPO = get_repository()

SETTINGS = get_settings()                                        # 启动即读配置（缺失就崩，见 config.py）
KB = build_knowledge_provider(SETTINGS)                          # Harness 优先，失败降级本地镜像
AUDIT = AuditLog("data/audit.jsonl")                             # 只追加审计日志
REVIEW = ReviewStore("data/review.json")                         # 复核单（P1 文件存储）
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


def require_capability(user: dict, capability: str, action: str, target: str) -> None:
    """校验角色能力，不通过则记审计并抛 403。

    入参：
        user —— 当前身份（含 abilities）
        capability —— view_price / export / approve / view_audit
        action/target —— 用于审计记录
    出参：无（通过则静默返回）
    失败行为：
        能力不足时**先记审计再抛 HTTPException(403)**——越权尝试本身就是要留痕的事件。
    """
    if not user.get("abilities", {}).get(capability, False):
        AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "denied", target,
                    result="denied", extra={"need": capability, "action": action})
        raise HTTPException(status_code=403, detail=f"角色 {user.get('role')} 无 {capability} 权限")


def mask_amounts(view: dict) -> dict:
    """把视图里的金额字段打码（供无 view_price 权限的角色使用）。

    入参：view —— build_view() 的返回值
    出参：深拷贝后的视图，金额字段替换为 "***"
    失败行为：不抛异常；字段不存在时跳过。

    为什么打码而不是隐藏行：单证要核对自己录的费用项是否存在，
    但不需要知道金额——结构留着，数字抹掉，才是"最小必要可见"。
    """
    out = json.loads(json.dumps(view, ensure_ascii=False))
    for key in ("receivable", "required"):
        for row in out.get(key, []) or []:
            if "amount" in row:
                row["amount"] = "***"
    for key in ("payable",):
        for row in out.get(key, []) or []:
            if "amount" in row:
                row["amount"] = "***"
    return out


def _unauthorized(code: str, msg: str) -> JSONResponse:
    """统一构造 401 响应。

    入参：code —— 错误码；msg —— 中文说明
    出参：JSONResponse（HTTP 401，body 为统一结构）
    失败行为：不抛异常。
    """
    return JSONResponse(status_code=401, content=fail(code, msg))


def current_user(authorization: str = Header(default=""),
                 x_copilot_token: str = Header(default="")) -> dict:
    """从请求头解出当前用户（FastAPI 依赖）。

    入参：
        authorization  —— 标准头，形如 "Bearer xxx.yyy.zzz"
        x_copilot_token —— 备用头，纯令牌，无 Bearer 前缀
    出参：令牌 claims（含 sub / role / scope / abilities）
    失败行为：
        缺头、格式错、签名错、过期——一律抛 HTTPException(401)。
        注意：绝不读取请求体里的 username/role，身份只能来自我们签发的令牌。

    为什么要有备用头：托管平台的分享网关会注入它自己的 Authorization 头，
    把我们带的值覆盖掉（表现为"无论带不带令牌都报签名失败"）。
    X-Copilot-Token 不在网关的注入名单里，因此能原样到达应用。
    这仍然满足"身份只从 JWT 取"——换的只是传输通道，令牌本身没变。
    """
    token = ""
    # 顺序很关键：先看自定义头。托管网关会往每个请求注入它自己的 Authorization，
    # 若先读 Authorization，我们自己的令牌就永远被顶掉（实测表现：带什么令牌都"签名校验失败"）。
    if x_copilot_token.strip():
        token = x_copilot_token.strip()
    elif authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少令牌")
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


# ============================================================================
# P0 业务接口（单票 → 差异 → 追溯 → 知识库 → 图谱）
# 全部要求登录：身份只从 JWT 取，不接受请求参数自报身份（红线 6）
# ============================================================================

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """返回演示页面（原生 HTML + 原生 JS，无构建步骤）。

    入参：无
    出参：index.html 内容
    失败行为：文件缺失时返回一段说明文字而不是 500。
    """
    path = Path("web/index.html")
    if not path.exists():
        return HTMLResponse("<h1>web/index.html 不存在</h1>", status_code=500)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/shipments/{key_value}")
def api_shipment(key_value: str, key_type: str = "booking_no",
                 user: dict = Depends(current_user)) -> JSONResponse:
    """按四键之一取单票视图（费用行 + 应有费用 + 费率卡）。

    入参：
        key_value —— 键值，如 BK2610-0187
        key_type  —— order_no / booking_no / bl_no / container_no（默认订舱号）
    出参：统一结构，data 为 build_view() 结果
    失败行为：
        查不到返回 404；key_type 非法返回 400；跨客户访问返回 403（scope 校验）。
    """
    try:
        shipment = get_shipment(REPO, key_type, key_value)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=fail("BAD_KEY_TYPE", str(exc)))
    if not shipment:
        return JSONResponse(status_code=404, content=fail("NOT_FOUND", f"未找到 {key_type}={key_value}"))
    try:
        assert_customer_allowed(user.get("scope", []), shipment.customer_id)   # 数据隔离
    except PermissionError as exc:
        AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "denied",
                    shipment.customer_id, result="denied", extra={"reason": "scope"})
        return JSONResponse(status_code=403, content=fail("FORBIDDEN", str(exc)))
    view = build_view(REPO, shipment)
    if not user.get("abilities", {}).get("view_price", False):
        view = mask_amounts(view)          # 单证/审计：看得到结构，看不到金额
    AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "query", shipment.shipment_id,
                extra={"masked": not user.get("abilities", {}).get("view_price", False)})
    return JSONResponse(status_code=200, content=ok(view))


@app.get("/api/diff/{shipment_id}")
def api_diff(shipment_id: str, user: dict = Depends(current_user)) -> JSONResponse:
    """计算一票的差异清单（缺项/重复/金额不符/未匹配）。

    入参：shipment_id —— 单票号（演示数据里等于订单号）
    出参：统一结构，data 见 compute_diff()
    失败行为：
        查不到 404；缺费率卡返回 409 并给出原因（阻断出账，不给默认值）；越权 403。
    """
    shipment = get_shipment(REPO, "order_no", shipment_id)
    if not shipment:
        return JSONResponse(status_code=404, content=fail("NOT_FOUND", f"未找到单票 {shipment_id}"))
    try:
        assert_customer_allowed(user.get("scope", []), shipment.customer_id)
    except PermissionError as exc:
        return JSONResponse(status_code=403, content=fail("FORBIDDEN", str(exc)))
    require_capability(user, "view_price", "diff", shipment.shipment_id)
    try:
        data = compute_diff(REPO, shipment)
        AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "diff", shipment.shipment_id,
                    extra={"totals": data.get("totals")})
        return JSONResponse(status_code=200, content=ok(data))
    except RateCardMissing as exc:
        return JSONResponse(status_code=409, content=fail("RATE_CARD_MISSING", str(exc)))


@app.get("/api/trace/{shipment_id}/{fee_code}")
def api_trace(shipment_id: str, fee_code: str,
              user: dict = Depends(current_user)) -> JSONResponse:
    """取某票某个费用项的追溯链（四级，缺失标断链）。

    入参：shipment_id —— 单票号；fee_code —— 费用代码，如 LSS
    出参：统一结构，data = {"fee_code","levels":[...],"breaks":[...]}
    失败行为：查不到单票 404；越权 403；不抛异常。
    """
    shipment = get_shipment(REPO, "order_no", shipment_id)
    if not shipment:
        return JSONResponse(status_code=404, content=fail("NOT_FOUND", f"未找到单票 {shipment_id}"))
    try:
        assert_customer_allowed(user.get("scope", []), shipment.customer_id)
    except PermissionError as exc:
        return JSONResponse(status_code=403, content=fail("FORBIDDEN", str(exc)))

    require_capability(user, "view_price", "trace", shipment.shipment_id)
    diff = compute_diff(REPO, shipment)
    hit = next((d for d in diff["differences"] if d["fee_code"] == fee_code), None)
    if hit:
        levels = hit["trace"]
    else:                                          # 无差异时也可查（例如已正确的项）
        from app.services.trace import build_trace
        line = next((l for l in REPO.lines_of(shipment.shipment_id) if l.fee_code == fee_code), None)
        levels = [lv.__dict__ for lv in build_trace(
            REPO, shipment, fee_code, diff["rate_card"], "", "", line)]
    breaks = [lv["level"] for lv in levels if lv.get("missing")]
    AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "trace",
                f"{shipment.shipment_id}/{fee_code}", extra={"breaks": breaks})
    return JSONResponse(status_code=200, content=ok(
        {"fee_code": fee_code, "levels": levels, "breaks": breaks}))


@app.get("/api/kb/search")
def api_kb(q: str, top_k: int = 5, user: dict = Depends(current_user)) -> JSONResponse:
    """检索知识库（Harness 优先，失败自动降级本地镜像）。

    入参：q —— 查询词；top_k —— 条数
    出参：统一结构，data = {"provider":..., "hits":[{doc_no,clause_no,title,snippet,score,source}]}
    失败行为：不抛异常；无命中返回空列表并在 provider 中标明后端。
    """
    hits = KB.search(q, top_k)
    AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "kb", q,
                extra={"hits": len(hits), "provider": KB.name()})
    return JSONResponse(status_code=200, content=ok({
        "provider": KB.name(),
        "hits": [{"doc_no": h.doc_no, "clause_no": h.clause_no, "title": h.title,
                  "snippet": h.snippet, "score": h.score, "source": h.source} for h in hits],
    }))


@app.get("/api/graph/why")
def api_graph_why(customer_id: str, fee_code: str, version: str = "",
                  user: dict = Depends(current_user)) -> JSONResponse:
    """知识图谱：解释"这条费率为什么适用于这个客户"。

    入参：customer_id —— 客户号；fee_code —— 费用项；version —— 期望命中的费率卡版本
    出参：统一结构，data = {"path":[{from,predicate,to,source_ref}], "hops": 2}
    失败行为：不抛异常；路径为空表示图谱里没有这条关系（应提示"未找到依据"）。
    """
    path = why_applicable(REPO, customer_id, fee_code, version)
    return JSONResponse(status_code=200, content=ok({"path": path, "hops": len(path)}))


@app.get("/api/graph/expand")
def api_graph_expand(entity: str, hops: int = 2,
                     user: dict = Depends(current_user)) -> JSONResponse:
    """知识图谱：从实体出发 2 跳扩展，用于展示关联关系。

    入参：entity —— 如 RateCard:V4；hops —— 1 或 2
    出参：统一结构，data 见 graph.expand()
    失败行为：不抛异常。
    """
    return JSONResponse(status_code=200, content=ok(expand(REPO, entity, hops)))


# ============================================================================
# P1 接口：复核闸门 / 导出留痕 / 审计查询
# 红线 5：系统只产出草稿，没有"发送"接口；审批必须换人（制度 §9.4）
# ============================================================================

class ReviewDecisionBody(BaseModel):
    """审批请求体。"""

    decision: str = "approve"      # approve / reject
    comment: str = ""


class ExportBody(BaseModel):
    """导出请求体。"""

    shipment_id: str
    receiver: str                 # 接收方，必填（红线 3）


@app.post("/api/review/draft/{shipment_id}")
def api_review_draft(shipment_id: str, user: dict = Depends(current_user)) -> JSONResponse:
    """生成复核草稿（冻结当前差异快照）。

    入参：shipment_id —— 单票号
    出参：统一结构，data 为复核单
    失败行为：查不到单票 404；无 view_price 权限 403；缺费率卡 409。
    """
    shipment = get_shipment(REPO, "order_no", shipment_id)
    if not shipment:
        return JSONResponse(status_code=404, content=fail("NOT_FOUND", f"未找到单票 {shipment_id}"))
    require_capability(user, "view_price", "review_draft", shipment.shipment_id)
    try:
        diff = compute_diff(REPO, shipment)
    except RateCardMissing as exc:
        return JSONResponse(status_code=409, content=fail("RATE_CARD_MISSING", str(exc)))

    snapshot = {
        "rate_card": diff["rate_card"],
        "totals": diff["totals"],
        "difference_count": len(diff["differences"]),
        "missing": [d["fee_code"] for d in diff["differences"] if d["kind"] == "缺项"],
    }
    case = REVIEW.create(shipment.shipment_id, user.get("sub", "?"), user.get("role", "?"),
                         snapshot, AUDIT)
    return JSONResponse(status_code=200, content=ok(case))


@app.post("/api/review/submit/{case_id}")
def api_review_submit(case_id: str, user: dict = Depends(current_user)) -> JSONResponse:
    """提交复核（草稿 → 待审）。

    入参：case_id —— 复核单号
    出参：统一结构，data 为更新后的复核单
    失败行为：非本人草稿 / 状态不符返回 409；不存在 404。
    """
    try:
        return JSONResponse(status_code=200,
                            content=ok(REVIEW.submit(case_id, user.get("sub", "?"),
                                                     user.get("role", "?"), AUDIT)))
    except ReviewError as exc:
        code = 404 if exc.code == "NOT_FOUND" else 409
        return JSONResponse(status_code=code, content=fail(exc.code, str(exc)))


@app.post("/api/review/approve/{case_id}")
def api_review_approve(case_id: str, body: ReviewDecisionBody,
                      user: dict = Depends(current_user)) -> JSONResponse:
    """审批（通过 / 驳回）。

    入参：case_id；body.decision（approve/reject）；body.comment
    出参：统一结构，data 为更新后的复核单
    失败行为：
        非复核人 403；提交人自审 409（SELF_APPROVAL）；驳回无意见 409。
    """
    require_capability(user, "approve", "review_approve", case_id)
    try:
        return JSONResponse(status_code=200,
                            content=ok(REVIEW.approve(case_id, user.get("sub", "?"),
                                                      user.get("role", "?"), body.decision,
                                                      body.comment, AUDIT)))
    except ReviewError as exc:
        code = 404 if exc.code == "NOT_FOUND" else (403 if exc.code == "FORBIDDEN" else 409)
        return JSONResponse(status_code=code, content=fail(exc.code, str(exc)))


@app.get("/api/review/{case_id}")
def api_review_get(case_id: str, user: dict = Depends(current_user)) -> JSONResponse:
    """查看复核单。

    入参：case_id
    出参：统一结构，data 为复核单
    失败行为：不存在 404。
    """
    case = REVIEW.get(case_id)
    if not case:
        return JSONResponse(status_code=404, content=fail("NOT_FOUND", f"复核单不存在：{case_id}"))
    return JSONResponse(status_code=200, content=ok(case))


@app.post("/api/export/pack")
def api_export_pack(body: ExportBody, user: dict = Depends(current_user)) -> JSONResponse:
    """导出客户追溯包（字段白名单 + 强制留痕）。

    入参：body.shipment_id；body.receiver（必填）
    出参：统一结构，data = {"pack":..., "audit_id":..., "fields":[...]}
    失败行为：
        无 export 权限 403；receiver 为空 400；包内出现敏感字段 500（属代码缺陷）。
    """
    require_capability(user, "export", "export", body.shipment_id)
    shipment = get_shipment(REPO, "order_no", body.shipment_id)
    if not shipment:
        return JSONResponse(status_code=404, content=fail("NOT_FOUND", f"未找到单票 {body.shipment_id}"))
    try:
        out = build_pack(REPO, shipment, user.get("sub", "?"), user.get("role", "?"),
                         body.receiver, AUDIT)
        return JSONResponse(status_code=200, content=ok(out))
    except ValueError as exc:
        return JSONResponse(status_code=400, content=fail("BAD_REQUEST", str(exc)))
    except ExportViolation as exc:                    # 代码缺陷：必须暴露而不是静默截断
        return JSONResponse(status_code=500, content=fail("EXPORT_VIOLATION", str(exc)))


@app.get("/api/audit/tail")
def api_audit_tail(n: int = 20, user: dict = Depends(current_user)) -> JSONResponse:
    """查看最近审计记录（合规/审计只读能力）。

    入参：n —— 条数
    出参：统一结构，data = {"records":[...]}
    失败行为：无 view_audit 权限 403。
    """
    require_capability(user, "view_audit", "audit_tail", "audit")
    return JSONResponse(status_code=200, content=ok({"records": AUDIT.tail(n)}))


@app.get("/api/metrics")
def api_metrics(user: dict = Depends(current_user)) -> JSONResponse:
    """指标看板：缺口金额、演示口径漏收率、差异类型分布。

    入参：无
    出参：统一结构，data 见 services/metrics.compute()
    失败行为：无 view_price 权限 403（指标含金额）；计算异常 500 并记审计。
    """
    require_capability(user, "view_price", "metrics", "global")
    try:
        data = compute_metrics(REPO)
    except Exception as exc:                              # noqa: BLE001 看板异常不能静默
        AUDIT.write(user.get("sub", "?"), user.get("role", "?"), "metrics", "global",
                    result="error", extra={"error": str(exc)})
        return JSONResponse(status_code=500, content=fail("METRICS_ERROR", str(exc)))
    return JSONResponse(status_code=200, content=ok(data))
