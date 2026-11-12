# -*- coding: utf-8 -*-
"""Phase 1 核心逻辑自测（只用标准库，不依赖 FastAPI/PyYAML，随时可跑）。

覆盖三件事：
1. 统一响应结构是否正确（含 fail 的兜底行为）；
2. 出网过滤器是否真的拦得住价格类字段（含变体命名）；
3. 令牌的签名/过期/越权校验是否有效。

    python tests/test_core_logic.py
"""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.redline import (  # noqa: E402  导入放 sys.path 之后，保证脚本方式可跑
    OutboundFilter,
    RedlineViolation,
    sum_amounts,
    to_decimal,
)
from app.core.response import fail, is_ok, ok  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.security import (  # noqa: E402
    ROLE_OPERATOR,
    ROLE_SETTLER,
    TokenError,
    abilities,
    assert_customer_allowed,
    create_token,
    verify_token,
)

SECRET = "unit-test-secret"


class TestResponse(unittest.TestCase):
    """统一响应结构。"""

    def test_ok_shape(self):
        """成功结构必须三个键齐全，error 为 None。"""
        payload = ok({"a": 1})
        self.assertTrue(is_ok(payload))
        self.assertEqual(set(payload.keys()), {"ok", "data", "error"})

    def test_ok_extra_merged(self):
        """extra 字段要合并进 data，而不是散在顶层。"""
        payload = ok({"a": 1}, total=3)
        self.assertEqual(payload["data"], {"a": 1, "total": 3})

    def test_fail_shape(self):
        """失败结构的 error 必须含 code 与 msg。"""
        payload = fail("FORBIDDEN", "无权访问")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FORBIDDEN")

    def test_fail_blank_code(self):
        """code 为空时兜底成 UNKNOWN，前端不会拿到 null。"""
        self.assertEqual(fail("", "")["error"]["code"], "UNKNOWN")


class TestRedline(unittest.TestCase):
    """出网字段过滤：这是数据红线的最后一道闸。"""

    def setUp(self):
        # 白名单只放"不含钱"的字段；黑名单覆盖常见金额别名
        self.filter = OutboundFilter(
            allow_fields=["fee_code", "fee_name", "status", "doc_type"],
            deny_fields=["unit_price", "price", "amount", "margin", "rate", "单价", "金额"],
        )

    def test_blocks_amount(self):
        """带 amount 的行必须被剔除，且记录路径。"""
        payload = {"fee_code": "LSS", "amount": "1180.00"}
        clean, blocked = self.filter.scan(payload)
        self.assertNotIn("amount", clean)
        self.assertIn("amount", blocked)

    def test_blocks_variant_naming(self):
        """price_v2 / unit_price_cny 这类变体也要拦（包含匹配）。"""
        payload = {"fee_code": "PSS", "price_v2": 980, "unit_price_cny": 12}
        clean, _ = self.filter.scan(payload)
        self.assertEqual(clean, {"fee_code": "PSS"})

    def test_blocks_nested(self):
        """嵌套结构的金额也要拦，不能只扫第一层。"""
        payload = {"charges": [{"fee_code": "THC", "amount": "1260.00"}]}
        clean, blocked = self.filter.scan(payload)
        self.assertEqual(clean, {"charges": [{"fee_code": "THC"}]})
        self.assertTrue(any("charges[0].amount" in b for b in blocked))

    def test_assert_safe_raises(self):
        """严格模式：只要有敏感字段就抛异常，不能静默放行。"""
        with self.assertRaises(RedlineViolation):
            self.filter.assert_safe({"fee_code": "LSS", "unit_price": 1})

    def test_assert_safe_passes(self):
        """纯合规数据应原样通过。"""
        payload = {"fee_code": "LSS", "status": "应收未收"}
        self.assertEqual(self.filter.assert_safe(payload), payload)

    def test_empty_allowlist_refused(self):
        """空白名单等于放行一切，构造时就必须失败。"""
        with self.assertRaises(ValueError):
            OutboundFilter(allow_fields=[], deny_fields=["amount"])

    def test_float_rejected(self):
        """金额传 float 直接拒绝（财务场景不接受浮点误差）。"""
        with self.assertRaises(ValueError):
            to_decimal(0.1, "amount")

    def test_sum_is_exact(self):
        """Decimal 求和必须精确：0.1+0.2 应等于 0.3。"""
        total = sum_amounts([{"amount": "0.1"}, {"amount": "0.2"}])
        self.assertEqual(total, Decimal("0.3"))


class TestSecurity(unittest.TestCase):
    """令牌与数据范围。"""

    def test_roundtrip(self):
        """签发后能验回，claims 不丢字段。"""
        token = create_token({"sub": "chenlili", "role": ROLE_SETTLER, "scope": ["C-0731"]},
                             SECRET, 60)
        claims = verify_token(token, SECRET)
        self.assertEqual(claims["sub"], "chenlili")
        self.assertEqual(claims["scope"], ["C-0731"])

    def test_tamper_rejected(self):
        """改动载荷后签名失效，必须拒绝。"""
        token = create_token({"sub": "chenlili", "role": ROLE_SETTLER, "scope": ["C-0731"]},
                             SECRET, 60)
        head, _, tail = token.split(".")
        forged = f"{head}.eyJzdWIiOiJhZG1pbiJ9.{tail}"   # 换成 {"sub":"admin"}
        with self.assertRaises(TokenError):
            verify_token(forged, SECRET)

    def test_wrong_secret(self):
        """换密钥验不过。"""
        token = create_token({"sub": "a", "role": ROLE_SETTLER, "scope": ["C-0731"]},
                             SECRET, 60)
        with self.assertRaises(TokenError):
            verify_token(token, "another-secret")

    def test_expired(self):
        """过期令牌要报 TOKEN_EXPIRED，便于前端区分。"""
        token = create_token({"sub": "a", "role": ROLE_SETTLER, "scope": ["C-0731"]},
                             SECRET, -1)                  # 负分钟数 = 已过期
        with self.assertRaises(TokenError) as ctx:
            verify_token(token, SECRET)
        self.assertEqual(ctx.exception.code, "TOKEN_EXPIRED")

    def test_scope_denied(self):
        """跨客户访问必须抛 PermissionError。"""
        with self.assertRaises(PermissionError):
            assert_customer_allowed(["C-0731"], "C-0800")

    def test_scope_wildcard(self):
        """全量授权（*）放行。"""
        assert_customer_allowed(["*"], "C-0800")          # 不抛即通过

    def test_operator_cannot_see_price(self):
        """单证操作员看不到价格，权限表默认拒绝未知角色。"""
        self.assertFalse(abilities(ROLE_OPERATOR)["view_price"])
        self.assertFalse(abilities("unknown_role")["view_price"])


class TestLLM(unittest.TestCase):
    """模型适配层：无模型环境也必须可跑。"""

    def test_mock_health(self):
        """mock 模式必须报告可达，且不假装调用了模型。"""
        health = LLMClient("mock", "", "stub").health()
        self.assertTrue(health["reachable"])
        self.assertIn("未调用", health["detail"])

    def test_mock_prefix(self):
        """桩输出必须带 [MOCK] 前缀，防止被当真实结论。"""
        out = LLMClient("mock", "", "stub").chat([{"role": "user", "content": "看下差异"}])
        self.assertTrue(out.startswith("[MOCK]"))
        self.assertIn("differences", out)

    def test_rules_renders_diff(self):
        """rules 后端必须产出可用结论：条数、金额、依据都要在。"""
        import json as _json
        data = {"differences": [{"kind": "缺项", "fee_code": "LSS",
                                 "fee_name": "低硫附加费", "amount": "1180.00",
                                 "currency": "CNY", "evidence": "HC-FIN-2024-07 §5.2"}],
                "summary": "共发现 1 项差异："}
        msg = "解释一下 [[DATA]]" + _json.dumps(data, ensure_ascii=False) + "[[/DATA]]"
        out = LLMClient("rules", "", "").chat([{"role": "user", "content": msg}])
        self.assertIn("LSS", out)
        self.assertIn("1180.00", out)
        self.assertIn("§5.2", out)                 # 依据必须带出处
        self.assertIn("本地规则生成", out)          # 明确标注未用大模型

    def test_rules_trace_marks_break(self):
        """追溯链缺级时必须标断链，不能含糊成"完整"。"""
        import json as _json
        msg = "追溯 [[DATA]]" + _json.dumps({"trace": ["船司账单", "费率卡 V4"], "break_at": 3},
                                          ensure_ascii=False) + "[[/DATA]]"
        out = LLMClient("rules", "", "").chat([{"role": "user", "content": msg}])
        self.assertIn("缺失", out)
        self.assertIn("断链位置：第 3 级", out)

    def test_ollama_fallback_to_rules(self):
        """Ollama 不可达时必须降级并在输出里标注，不能静默冒充模型结论。"""
        client = LLMClient("ollama", "http://127.0.0.1:9/v1", "qwen2.5:7b",
                           timeout=1, max_retries=0, fallback="rules")
        out = client.chat([{"role": "user", "content": "看差异"}])
        self.assertIn("降级说明", out)
        self.assertIn("本地规则生成", out)
        self.assertEqual(client.last_mode, "rules")

    def test_ollama_no_fallback_raises(self):
        """配置 fallback=none 时必须抛错，让调用方感知故障。"""
        client = LLMClient("ollama", "http://127.0.0.1:9/v1", "qwen2.5:7b",
                           timeout=1, max_retries=0, fallback="none")
        with self.assertRaises(Exception):
            client.chat([{"role": "user", "content": "看差异"}])

    def test_invalid_provider(self):
        """未支持的 provider 必须在构造时失败，不能静默降级。"""
        with self.assertRaises(ValueError):
            LLMClient("openai-cloud", "", "x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
