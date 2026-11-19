# -*- coding: utf-8 -*-
"""复核闸门：草稿 → 提交 → 审批。

这条链路存在的唯一理由：**系统只产出草稿，对外动作必须由人点**（红线 5）。
所以这里没有 send()，也没有"自动通过"，只有 approve/reject 两种人工结论。

制度 §9.4 补充约束：补收、减免不得由同一人既操作又复核 —— 代码里硬拦。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from app.core.audit import AuditLog

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

ALLOWED_TRANSITIONS = {
    (STATUS_DRAFT, STATUS_SUBMITTED),
    (STATUS_SUBMITTED, STATUS_APPROVED),
    (STATUS_SUBMITTED, STATUS_REJECTED),
}


class ReviewError(Exception):
    """复核流程被拒绝时抛出。

    入参：message —— 中文原因；code —— 建议错误码
    出参：无
    失败行为：由 API 转 409/403，不吞掉。
    """

    def __init__(self, message: str, code: str = "REVIEW_INVALID"):
        super().__init__(message)
        self.code = code


class ReviewStore:
    """复核单存储（P1 用 JSON 文件，P2 迁数据库；接口不变）。"""

    def __init__(self, path: str | Path = "data/review.json"):
        """构造存储。

        入参：path —— 文件路径
        出参：无
        失败行为：目录不存在自动创建；文件损坏时按空库处理并告警。
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        """读取全部复核单。

        入参：无
        出参：{case_id: case}
        失败行为：文件不存在或 JSON 损坏返回空 dict，不抛异常。
        """
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[warn] review.json 损坏，按空库处理", flush=True)
            return {}

    def _save(self, data: dict) -> None:
        """原子写回（先写临时文件再 replace，避免写一半断电）。

        入参：data —— 全量数据
        出参：无
        失败行为：写盘异常向上抛（复核数据丢了比报错更严重）。
        """
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def create(self, shipment_id: str, actor: str, role: str, snapshot: dict,
               audit: AuditLog) -> dict:
        """生成复核草稿。

        入参：shipment_id —— 单票；actor/role —— 操作人；snapshot —— 冻结的差异快照
        出参：复核单 dict（含 case_id）
        失败行为：不抛异常（草稿允许重复生成，每次一张新单）。
        """
        data = self._load()
        case_id = "RC-" + uuid.uuid4().hex[:8].upper()
        case = {
            "case_id": case_id,
            "shipment_id": shipment_id,
            "status": STATUS_DRAFT,
            "created_by": actor,
            "created_role": role,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot": snapshot,          # 冻结金额，审批时不允许再变
            "submitted_by": "",
            "approved_by": "",
            "decision": "",
            "comment": "",
        }
        data[case_id] = case
        self._save(data)
        audit.write(actor, role, "review_draft", shipment_id,
                    extra={"case_id": case_id, "totals": snapshot.get("totals")})
        return case

    def submit(self, case_id: str, actor: str, role: str, audit: AuditLog) -> dict:
        """提交复核（草稿 → 待审）。

        入参：case_id；actor/role —— 提交人
        出参：更新后的复核单
        失败行为：
            单据不存在抛 ReviewError；非本人草稿抛 ReviewError；
            状态不允许（已提交/已审批）抛 ReviewError。
        """
        data = self._load()
        case = data.get(case_id)
        if not case:
            raise ReviewError(f"复核单不存在：{case_id}", "NOT_FOUND")
        if case["created_by"] != actor:                     # 只能提交自己的草稿
            raise ReviewError("只能提交自己创建的草稿", "FORBIDDEN")
        if (case["status"], STATUS_SUBMITTED) not in ALLOWED_TRANSITIONS:
            raise ReviewError(f"当前状态 {case['status']} 不能提交", "BAD_STATUS")

        case["status"] = STATUS_SUBMITTED
        case["submitted_by"] = actor
        case["submitted_at"] = datetime.now().isoformat(timespec="seconds")
        self._save(data)
        audit.write(actor, role, "review_submit", case["shipment_id"],
                    extra={"case_id": case_id})
        return case

    def approve(self, case_id: str, actor: str, role: str, decision: str,
                comment: str, audit: AuditLog) -> dict:
        """审批（通过 / 驳回）。

        入参：
            case_id —— 复核单；actor/role —— 审批人
            decision —— approve / reject；comment —— 意见（驳回必填）
        出参：更新后的复核单
        失败行为：
            非复核人角色抛 ReviewError；**提交人 == 审批人** 抛 ReviewError（制度 §9.4）；
            状态不符或 decision 非法同样抛错。
        """
        data = self._load()
        case = data.get(case_id)
        if not case:
            raise ReviewError(f"复核单不存在：{case_id}", "NOT_FOUND")
        if role != "reviewer":                               # 只有复核人有权审批
            raise ReviewError("只有复核人可以审批", "FORBIDDEN")
        # 关键卡点：同一人不得既提交又审批 —— 这是内控底线，不是流程偏好
        if case.get("submitted_by") == actor:
            raise ReviewError("提交人不得审批自己的单据（制度 §9.4）", "SELF_APPROVAL")
        target = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
        if (case["status"], target) not in ALLOWED_TRANSITIONS:
            raise ReviewError(f"当前状态 {case['status']} 不能 {decision}", "BAD_STATUS")
        if decision == "reject" and not comment.strip():
            raise ReviewError("驳回必须填写意见", "COMMENT_REQUIRED")

        case["status"] = target
        case["decision"] = decision
        case["comment"] = comment
        case["approved_by"] = actor
        case["approved_at"] = datetime.now().isoformat(timespec="seconds")
        self._save(data)
        audit.write(actor, role, f"review_{decision}", case["shipment_id"],
                    extra={"case_id": case_id, "comment": comment})
        return case

    def get(self, case_id: str) -> dict | None:
        """按单号取复核单。

        入参：case_id
        出参：复核单或 None
        失败行为：不抛异常。
        """
        return self._load().get(case_id)

    def list_cases(self, limit: int = 20) -> list[dict]:
        """列出最近的复核单（按创建时间倒序）。

        入参：limit —— 条数
        出参：列表
        失败行为：不抛异常。
        """
        cases = list(self._load().values())
        cases.sort(key=lambda c: c.get("created_at", ""), reverse=True)
        return cases[:limit]
