# -*- coding: utf-8 -*-
"""大模型适配层：一个接口，三种后端。

四种后端：
- rules —— 本地规则生成器（不需要装任何模型，产出与模型同结构的结论，只少一点措辞自然度）
- mock  —— 单测用的哑桩，只返回固定 JSON
- ollama / vllm —— 真实模型（OpenAI 兼容）

为什么要有 rules / mock：
老师要求"本地部署 Ollama"，但课设环境往往装不上（没显卡、没网、磁盘不够）。
本层提供 provider=mock 的确定性桩：不装任何模型也能把整条链路跑通、能演示、能跑测试；
真要接 Ollama 时只改 config.yaml 的 provider，代码一行不动。

铁律：
- provider=mock 时，返回内容一律带 "[MOCK]" 前缀，防止拿去当真实结论；
- app.env=prod 且 provider=mock 时，启动直接失败——演示可以假，生产不能。
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any

_MOCK_MARK = "[MOCK] "          # 桩输出统一前缀，任何下游都能一眼识别


class LLMError(Exception):
    """模型调用失败（超时、连接失败、返回非 JSON、HTTP 非 200）。

    入参：message —— 中文说明；retryable —— 是否值得重试
    出参：无
    失败行为：由上层转成 {"ok": false, "error": {"code": "LLM_UNAVAILABLE"}}，
             并触发规则引擎降级（不阻塞用户拿到差异清单）。
    """

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class RuleRenderer:
    """无模型时的确定性生成器：把结构化数据渲染成中文结论。

    为什么它能"替代模型"：结算场景的结论（缺哪些项、金额多少、依据哪条）
    本来就来自规则引擎与知识图谱，模型只负责把结论说得通顺。
    所以这里用模板把同样的结论拼出来，结构、数字、依据完全一致，只是措辞朴素。
    """

    MARK_OPEN = "[[DATA]]"
    MARK_CLOSE = "[[/DATA]]"

    def render(self, messages: list[dict]) -> str:
        """渲染一次回复。

        入参：messages —— 对话历史，最后一条 user 里应含 [[DATA]]{json}[[/DATA]]
        出参：中文说明文本（结尾固定注明"本地规则生成"）
        失败行为：找不到数据块或 JSON 非法时，返回"缺少结构化数据"的说明，不抛异常。
        """
        text = ""
        for msg in reversed(messages):                       # 取最后一条用户输入
            if msg.get("role") == "user":
                text = str(msg.get("content", ""))
                break
        try:
            raw = text.split(self.MARK_OPEN, 1)[1].split(self.MARK_CLOSE, 1)[0]
            data = json.loads(raw)
        except (IndexError, json.JSONDecodeError):
            return "缺少结构化数据，无法生成结论。请先调用 query_shipment / diff_charges。（本地规则生成）"
        return self._render(data) + "（本地规则生成，未使用大模型）"

    def _render(self, data: dict) -> str:
        """按数据类型选择模板。

        入参：data —— 结构化结果
        出参：中文文本
        失败行为：未知结构时退化为通用摘要，不抛异常。
        """
        if "differences" in data:
            return self._render_diff(data["differences"], data.get("summary", ""))
        if "trace" in data:
            return self._render_trace(data)
        return f"已读取 {len(data)} 个字段：" + "、".join(list(data)[:8])

    def _render_diff(self, diffs: list, summary: str) -> str:
        """渲染差异清单：一条一行，带金额与依据。

        入参：diffs —— 差异列表；summary —— 可选汇总句
        出参：多行文本
        失败行为：空列表时返回"未发现差异"。
        """
        if not diffs:
            return "本票未发现应收差异。"
        lines = [f"共发现 {len(diffs)} 项差异：" if not summary else summary]
        for idx, d in enumerate(diffs, 1):
            lines.append(
                f"{idx}. {d.get('kind', '未分类')}｜{d.get('fee_code', '')} {d.get('fee_name', '')}"
                f"｜{d.get('amount', '')} {d.get('currency', '')}"
                f"｜依据：{d.get('evidence', '待核实')}"
            )
        lines.append("以上为草稿，需结算专员确认后方可出账；系统不自动发送。")
        return "\n".join(lines)

    def _render_trace(self, data: dict) -> str:
        """渲染四级追溯链，明确标出断链级别。

        入参：data —— {"trace": [...], "break_at": ...}
        出参：文本
        失败行为：trace 非列表时按空处理。
        """
        levels = ["来源单据", "费率条款", "承担方", "凭证"]
        got = data.get("trace") or []
        lines = ["追溯链："]
        for i, name in enumerate(levels, 1):
            hit = got[i - 1] if i - 1 < len(got) else None
            lines.append(f"{i}. {name}：{hit or '缺失'}")
        if data.get("break_at"):
            lines.append(f"断链位置：第 {data['break_at']} 级，需人工补证。")
        else:
            lines.append("链路完整。")
        return "\n".join(lines)


class LLMClient:
    """模型客户端（OpenAI 兼容协议）。

    Ollama 与 vLLM 都实现了 /chat/completions，所以只需一个实现 + 不同的 base_url。
    """

    def __init__(self, provider: str, base_url: str, model: str,
                 api_key: str = "", timeout: int = 30, max_retries: int = 1,
                 fallback: str = "rules"):
        """构造客户端。

        入参：
            provider    —— mock / ollama / vllm
            base_url    —— 形如 http://127.0.0.1:11434/v1（mock 可为空）
            model       —— 模型名，如 qwen2.5:7b
            api_key     —— 本地模型通常为空，字段保留便于切云
            timeout     —— 单次请求秒数，必须有（断网不能让页面转圈）
            max_retries —— 重试次数，默认 1，避免把断网放大成雪崩
            fallback    —— 模型不可达时的降级后端：rules（本地规则）/ none（直接抛错）
        出参：无
        失败行为：provider 不在白名单内抛 ValueError（启动即失败，不静默降级）。
        """
        allowed = {"rules", "mock", "ollama", "vllm"}
        if provider not in allowed:
            raise ValueError(f"不支持的 llm.provider={provider}，只允许 {sorted(allowed)}")
        self.provider = provider
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        # 降级策略：主路径永远是 Ollama；模型没起来时用本地规则兜底，并在输出里明确标注，
        # 保证"结论照出、来源可选"，而不是整站 500。
        self.fallback = fallback if fallback in {"rules", "none"} else "rules"
        self.last_mode = "unknown"      # 最近一次调用实际走的路径：ollama / rules

    # ------------------------------------------------------------------ 对外
    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        """发起一次对话，返回纯文本。

        入参：
            messages    —— [{"role": "system|user|assistant", "content": "..."}]
            temperature —— 采样温度，结算场景默认 0.2（要稳定、别发挥）
        出参：模型回复文本
        失败行为：
            mock：永不失败（返回带 [MOCK] 的确定性文本）
            真实后端：超时/连接失败重试 max_retries 次，仍失败抛 LLMError
        """
        if self.provider == "rules":                     # 无模型：规则生成，结论结构一致
            return RuleRenderer().render(messages)
        if self.provider == "mock":                      # 桩：不联网，立即返回
            return self._mock(messages)

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,                             # 不流式，简化前端
        }
        last_error = None
        for attempt in range(self.max_retries + 1):       # 0..max_retries
            try:
                text = self._post("/chat/completions", payload)
                self.last_mode = self.provider            # 真实模型返回
                return text
            except LLMError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.max_retries:
                    break
                time.sleep(0.5 * (attempt + 1))          # 线性退避，别做指数把课上时间耗光

        # 走到这里说明 Ollama 不可用：按配置降级
        if self.fallback == "rules":
            self.last_mode = "rules"
            body = RuleRenderer().render(messages)
            return body + f"\n[降级说明] Ollama 不可达（{last_error}），本次结论由本地规则生成。"
        raise last_error or LLMError("模型调用失败")

    def health(self) -> dict:
        """探活：返回当前后端可用性与模型名，供 /llm/ping 使用。

        入参：无
        出参：{"provider":…, "model":…, "reachable": bool, "detail": str}
        失败行为：不抛异常，把错误写进 detail（探活接口本身不能 500）。
        """
        if self.provider == "rules":
            return {"provider": "rules", "model": "rule-renderer", "reachable": True,
                    "mode": "rules", "fallback": self.fallback,
                    "detail": "本地规则生成，未加载任何模型（结论来自确定性引擎）"}
        if self.provider == "mock":
            return {"provider": "mock", "model": "stub", "reachable": True,
                    "mode": "mock", "fallback": self.fallback,
                    "detail": "离线桩，未调用任何模型"}
        try:
            self._post("/chat/completions", {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 8,
                "stream": False,
            })
            return {"provider": self.provider, "model": self.model, "reachable": True,
                    "mode": self.provider, "fallback": self.fallback,
                    "detail": "Ollama 连接正常，全部推理在本机完成"}
        except LLMError as exc:
            return {"provider": self.provider, "model": self.model, "reachable": False,
                    "mode": "rules" if self.fallback == "rules" else "unavailable",
                    "fallback": self.fallback,
                    "detail": f"Ollama 不可达：{exc}；已按 fallback={self.fallback} 处理"}

    # ------------------------------------------------------------------ 内部
    def _post(self, path: str, payload: dict) -> str:
        """发一次 HTTP 请求并取出回复文本。

        入参：path —— 相对 base_url 的路径；payload —— 请求体
        出参：choices[0].message.content 字符串
        失败行为：
            网络错误/超时 → LLMError(retryable=True)；4xx → retryable=False（重试没意义）；
            返回体不是预期结构 → LLMError（防止把 HTML 错误页当答案）。
        """
        if not self.base_url:
            raise LLMError("llm.base_url 未配置", retryable=False)
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LLMError(f"模型返回 HTTP {exc.code}", retryable=exc.code >= 500) from exc  # 4xx 重试无意义
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"模型不可达：{exc}", retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise LLMError("模型返回内容不是 JSON", retryable=False) from exc

        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("模型返回结构不符合 OpenAI 兼容格式", retryable=False) from exc

    def _mock(self, messages: list[dict]) -> str:
        """离线桩：按关键词返回确定性 JSON，供无模型环境演示与单测。

        入参：messages —— 对话历史（只取最后一条 user 内容做关键词匹配）
        出参：带 [MOCK] 前缀的字符串
        失败行为：永不抛异常。
        """
        text = ""
        for msg in reversed(messages):                  # 从后往前找最后一条用户输入
            if msg.get("role") == "user":
                text = str(msg.get("content", ""))
                break
        if "差异" in text or "缺项" in text:
            body: Any = {"differences": [
                {"kind": "缺项", "fee_code": "LSS", "amount": "1180.00", "reason": "费率卡版本未同步"},
                {"kind": "缺项", "fee_code": "PSS", "amount": "980.00", "reason": "同上"},
            ], "note": "桩输出，仅用于链路演示"}
        elif "追溯" in text or "依据" in text:
            body = {"trace": ["来源单据", "费率条款", "承担方", "凭证"], "break_at": None}
        else:
            body = {"echo": text[:80], "note": "未命中桩规则"}
        return _MOCK_MARK + json.dumps(body, ensure_ascii=False)
