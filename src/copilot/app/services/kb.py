# -*- coding: utf-8 -*-
"""知识库检索：Harness 适配层 + 本地镜像兜底。

设计要点：
1. **接口只有 search()**，上层（差异归因、条款解释）不关心后端是 Harness 还是镜像；
2. Harness 是主路径，端点和密钥来自 config.yaml / 环境变量，不写死；
3. 只要检索结果缺 doc_no 或 clause_no，就丢——**没出处的条款不能当依据**（红线 7）；
4. Harness 不通时自动退到本地镜像，并在返回里标 source=local_mirror，让前端能提示降级。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from app.domain import KbHit


class KnowledgeProvider(Protocol):
    """知识库提供方协议。"""

    def search(self, query: str, top_k: int = 5) -> list[KbHit]: ...
    def name(self) -> str: ...


class LocalMirrorProvider:
    """本地镜像库：把 Harness 里的制度条款同步成 markdown，离线也能检索。

    文件格式（data/kb/*.md）：
        ### HC-FIN-2024-07 §5.2 按提单签发日取费率
        正文……
    为什么用这个格式：git diff 可读、不需要数据库、老师能直接改。
    """

    SECTION = re.compile(r"^###\s+(\S+)\s+(§\S+)\s+(.+)$")   # 文档号 条款号 标题

    def __init__(self, kb_dir: str | Path = "data/kb"):
        """加载镜像文件。

        入参：kb_dir —— 镜像目录
        出参：无
        失败行为：目录不存在时**不抛异常**，只是检索结果为空（并可通过 name() 看出为空库）。
        """
        self.kb_dir = Path(kb_dir)
        self.records: list[dict] = []
        self._load()

    def _load(self) -> None:
        """扫描目录，把每个 ### 小节解析成一条记录。

        入参：无
        出参：无
        失败行为：单个文件解析失败时跳过该文件（不因一个坏文件整库不可用）。
        """
        if not self.kb_dir.exists():
            return
        for path in sorted(self.kb_dir.glob("*.md")):
            try:
                current = None
                buffer: list[str] = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    m = self.SECTION.match(line.strip())
                    if m:
                        if current:
                            current["text"] = "\n".join(buffer).strip()
                            self.records.append(current)
                        current = {"doc_no": m.group(1), "clause_no": m.group(2),
                                   "title": m.group(3).strip(), "file": path.name}
                        buffer = []
                    elif current:
                        buffer.append(line)
                if current:
                    current["text"] = "\n".join(buffer).strip()
                    self.records.append(current)
            except Exception:                                  # noqa: BLE001 坏文件跳过
                continue

    def search(self, query: str, top_k: int = 5) -> list[KbHit]:
        """按字符重合度做朴素检索。

        入参：query —— 用户问题；top_k —— 返回条数
        出参：KbHit 列表（按分数降序），source 固定为 local_mirror
        失败行为：不抛异常；无命中返回空列表。
        """
        q_chars = {c for c in query if not c.isspace()}
        if not q_chars:
            return []
        scored: list[KbHit] = []
        for rec in self.records:
            haystack = rec["title"] + rec["text"]
            hit_chars = {c for c in haystack if c in q_chars}
            if not hit_chars:
                continue
            score = len(hit_chars) / len(q_chars)             # 0~1 的粗糙覆盖率
            if rec["clause_no"].lstrip("§") in query:          # 显式提到条款号，加权
                score += 0.5
            scored.append(KbHit(
                doc_no=rec["doc_no"], clause_no=rec["clause_no"], title=rec["title"],
                snippet=rec["text"][:160].replace("\n", " "), score=round(min(score, 1.0), 3),
                source="local_mirror",
            ))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def name(self) -> str:
        """返回提供方名称（供 /health 与前端显示）。

        入参：无
        出参："local_mirror"，含记录数时形如 local_mirror(5)
        失败行为：不抛异常。
        """
        return f"local_mirror({len(self.records)})"


class HarnessProvider:
    """Harness 知识库 HTTP 适配层。

    端点与密钥来自 config.yaml 的 knowledge.harness.*（值为 ${ENV}，启动时注入）。
    返回结构做了容错解析：兼容 {"data":[...]} / {"items":[...]} / [...] 三种常见形态，
    因为 Harness 的正式文档还没拿到，先按最通用的 REST 约定实现，拿到文档后只需改 _pick()。
    """

    def __init__(self, endpoint: str, api_key: str = "", kb_id: str = "",
                 timeout: int = 15, top_k: int = 5, fallback: KnowledgeProvider | None = None):
        """构造适配器。

        入参：
            endpoint —— 服务地址，如 https://harness.example.com/api
            api_key  —— 访问密钥（环境变量注入）
            kb_id    —— 知识库 ID
            timeout  —— 超时秒数（断网不能让页面转圈）
            top_k    —— 默认返回条数
            fallback —— 失败时使用的兜底提供方（本地镜像）
        出参：无
        失败行为：endpoint 为空时构造仍成功，但 search() 会直接走 fallback。
        """
        self.endpoint = (endpoint or "").rstrip("/")
        self.api_key = api_key
        self.kb_id = kb_id
        self.timeout = timeout
        self.top_k = top_k
        self.fallback = fallback

    def search(self, query: str, top_k: int | None = None) -> list[KbHit]:
        """调用 Harness 检索；失败则降级到镜像库。

        入参：query —— 查询；top_k —— 条数
        出参：KbHit 列表（source=harness 或 local_mirror）
        失败行为：
            网络错误 / 非 200 / 结构不符 —— 打印告警后走 fallback，不向上抛异常，
            保证"知识库挂了，差异清单还能看"。
        """
        limit = top_k or self.top_k
        if not self.endpoint:
            return self.fallback.search(query, limit) if self.fallback else []

        payload = {"kb_id": self.kb_id, "query": query, "top_k": limit}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.endpoint}/search",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            hits = self._pick(body, limit)
            if hits:
                return hits
            raise ValueError("Harness 返回空结果")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, ValueError) as exc:
            print(f"[warn] Harness 不可用（{exc}），降级为本地镜像", flush=True)
            return self.fallback.search(query, limit) if self.fallback else []

    @staticmethod
    def _pick(body: dict | list, limit: int) -> list[KbHit]:
        """从 Harness 返回体里提取标准化的 KbHit 列表。

        入参：body —— 原始 JSON；limit —— 条数上限
        出参：KbHit 列表（缺 doc_no / clause_no 的条目会被丢弃）
        失败行为：结构完全无法识别时抛 ValueError，由 search() 转降级。
        """
        items = body
        if isinstance(body, dict):
            for key in ("data", "items", "results", "hits"):
                if isinstance(body.get(key), (list, dict)):
                    items = body[key]
                    break
        if isinstance(items, dict):
            for key in ("items", "results", "hits"):
                if isinstance(items.get(key), list):
                    items = items[key]
                    break
        if not isinstance(items, list):
            raise ValueError("Harness 返回结构无法识别")

        out: list[KbHit] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            doc_no = str(it.get("doc_no") or it.get("document_id") or it.get("file_id") or "")
            clause = str(it.get("clause_no") or it.get("section") or it.get("anchor") or "")
            if not doc_no or not clause:            # 红线 7：没出处的条款一律不要
                continue
            out.append(KbHit(
                doc_no=doc_no, clause_no=clause,
                title=str(it.get("title") or ""),
                snippet=str(it.get("text") or it.get("content") or it.get("snippet") or "")[:160],
                score=float(it.get("score") or 0.0), source="harness",
            ))
        return out

    def name(self) -> str:
        """返回提供方名称。

        入参：无
        出参："harness" 或 "harness(no-endpoint)"
        失败行为：不抛异常。
        """
        return "harness" if self.endpoint else "harness(no-endpoint)"


def build_knowledge_provider(settings=None, kb_dir: str | None = None) -> KnowledgeProvider:
    """按配置组装知识库提供方。

    入参：settings —— 配置对象（可为 None，此时读默认 config.yaml）；kb_dir —— 镜像目录覆盖
    出参：HarnessProvider 或 LocalMirrorProvider
    失败行为：
        配置读取失败时不抛异常，直接返回本地镜像——检索能力降级但不阻断主流程。
    """
    mirror = LocalMirrorProvider(kb_dir or os.environ.get("COPILOT_KB_DIR", "data/kb"))
    try:
        if settings is None:
            from app.core.config import get_settings
            settings = get_settings()
        provider = (settings.get("knowledge.provider") or "local").lower()
        if provider != "harness":
            return mirror
        return HarnessProvider(
            endpoint=settings.get("knowledge.harness.endpoint", ""),
            api_key=settings.get("knowledge.harness.api_key", ""),
            kb_id=settings.get("knowledge.harness.kb_id", ""),
            timeout=int(settings.get("knowledge.harness.timeout_seconds", 15) or 15),
            top_k=int(settings.get("knowledge.harness.top_k", 5) or 5),
            fallback=mirror,
        )
    except Exception as exc:                                  # noqa: BLE001 配置异常不阻断
        print(f"[warn] 知识库配置读取失败（{exc}），使用本地镜像", flush=True)
        return mirror
