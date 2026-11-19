# -*- coding: utf-8 -*-
"""审计日志：只追加的 JSONL，永不改写。

为什么用追加文件而不是数据库表：
1. 红线要求"3 年不可删除"，追加文件天然只能往后长，误删一眼能看出来（行数对不上）；
2. 不依赖数据库可用性——数据库挂了也要能记"有人试图越权"；
3. 每行一个 JSON，老师用记事本就能查。

P2 会双写数据库，但文件保留，作为"不可篡改的底账"。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    """审计日志写入器。"""

    def __init__(self, path: str | Path = "data/audit.jsonl"):
        """构造写入器。

        入参：path —— 日志路径（默认 data/audit.jsonl）
        出参：无
        失败行为：不抛异常；目录不存在时自动创建。
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, actor: str, role: str, action: str, target: str,
              fields: list[str] | None = None, receiver: str = "",
              result: str = "ok", extra: dict | None = None) -> str:
        """追加一条审计记录。

        入参：
            actor —— 操作人（来自 JWT 的 sub，绝不接受前端传入）
            role —— 角色
            action —— 动作：query / diff / trace / kb / graph / export / review_* / denied
            target —— 对象：单票号、条款、导出批次等
            fields —— 涉及的字段列表（导出必填，用于回答"导了哪些字段"）
            receiver —— 数据接收方（导出必填）
            result —— ok / denied / error
            extra —— 其他上下文
        出参：审计号（uuid 前 12 位），便于回执给前端
        失败行为：
            写盘失败时打印告警但不抛异常——审计失败不能把业务请求打死，
            但要立刻在 stderr 暴露（运维能看到）。
        """
        audit_id = uuid.uuid4().hex[:12]
        record = {
            "audit_id": audit_id,
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "actor": actor,
            "role": role,
            "action": action,
            "target": target,
            "fields": fields or [],
            "receiver": receiver,
            "result": result,
            "extra": extra or {},
        }
        try:
            # 以 "a" 追加：任何情况下都不 truncate，保证"只追加"语义
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[warn] 审计写入失败：{exc}", file=os.sys.stderr, flush=True)
        return audit_id

    def tail(self, n: int = 20) -> list[dict]:
        """读最近 n 条（只读，供演示与自检）。

        入参：n —— 条数
        出参：记录列表（按时间正序）
        失败行为：文件不存在返回空列表，不抛异常。
        """
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # 半行/坏行跳过，不能让一条坏数据卡住审计查询
        return out
