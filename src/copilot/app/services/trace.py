# -*- coding: utf-8 -*-
"""追溯链服务：把一笔费用的四级依据拼出来，缺哪级就标哪级。

红线：不允许"看起来完整"。没有原件引用、没有费率版本、没有凭证，都必须如实标 missing，
因为审计与客户答疑都靠这条链，链子造假比没有链更危险。
"""

from __future__ import annotations

from app.domain import ChargeLine, Shipment, TraceLevel
from app.repo import Repository

LEVEL_LABELS = ["来源单据", "费率条款", "承担方", "凭证"]


def _carrier_doc_no(repo: Repository, shipment: Shipment) -> str:
    """从知识图谱里找这一票的船司账单号（BILLED_BY 边）。

    入参：repo —— 仓库；shipment —— 单票
    出参：账单号字符串；找不到时返回空串
    失败行为：不抛异常——找不到就返回空串，由上层标"断链"。
    """
    triples = repo.triple_search(subject=f"Shipment:{shipment.booking_no}", predicate="BILLED_BY")
    if not triples:
        return ""
    # BILLED_BY 的宾语形如 SourceDoc:MSK-INV-26-10-0912，去掉前缀
    return triples[0]["object"].split(":", 1)[-1]


def build_trace(repo: Repository, shipment: Shipment, fee_code: str,
                rate_version: str = "", clause_no: str = "",
                source_doc_no: str = "", line: ChargeLine | None = None) -> list[TraceLevel]:
    """构建一笔费用的四级追溯链。

    入参：
        repo —— 仓库
        shipment —— 单票
        fee_code —— 费用代码（如 LSS）
        rate_version —— 命中的费率卡版本（无则空）
        clause_no —— 依据条款号（无则空）
        source_doc_no —— 指定来源单据号（实报实销项用）
        line —— 若该费用已录入，传入费用行以补第 4 级凭证
    出参：TraceLevel 列表，长度恒为 4
    失败行为：不抛异常；缺失的一级 missing=True。
    """
    levels: list[TraceLevel] = []

    # ① 来源单据：优先用传入的（实报实销），否则从图谱找船司账单
    doc_no = source_doc_no or _carrier_doc_no(repo, shipment)
    doc = repo.source_doc(doc_no) if doc_no else None
    if doc and doc.file_ref:
        levels.append(TraceLevel(1, LEVEL_LABELS[0],
                                 f"{doc.doc_no}（{doc.doc_type}，{doc.doc_date}）",
                                 evidence=doc.doc_no))
    elif doc:
        # 单据存在但没有原件引用 —— 属于断链，不能算完整
        levels.append(TraceLevel(1, LEVEL_LABELS[0], f"{doc.doc_no}（无原件引用）",
                                 evidence=doc.doc_no, missing=True))
    else:
        levels.append(TraceLevel(1, LEVEL_LABELS[0], "未找到来源单据", missing=True))

    # ② 费率条款：版本号 + 条款号，两者缺一都算断
    if rate_version and clause_no:
        levels.append(TraceLevel(2, LEVEL_LABELS[1], f"{rate_version} · {clause_no}",
                                 evidence=clause_no))
    else:
        levels.append(TraceLevel(2, LEVEL_LABELS[1],
                                 f"缺依据（版本={rate_version or '无'}，条款={clause_no or '无'}）",
                                 missing=True))

    # ③ 承担方：从合同关系推断，写客户名而不是客户号，方便人工核对
    contract = repo.triple_search(subject=f"Customer:{shipment.customer_id}",
                                  predicate="HAS_CONTRACT")
    if contract:
        levels.append(TraceLevel(3, LEVEL_LABELS[2],
                                 f"{shipment.customer_name}（{shipment.customer_id}）",
                                 evidence=contract[0]["object"].split(":", 1)[-1]))
    else:
        levels.append(TraceLevel(3, LEVEL_LABELS[2], "未找到合同关系", missing=True))

    # ④ 凭证：已出账才有，未出账就老实写"待出账"并标 missing
    if line is not None:
        levels.append(TraceLevel(4, LEVEL_LABELS[3],
                                 f"账单行 {line.line_id}（{line.source_doc_no}）",
                                 evidence=line.source_doc_no))
    else:
        levels.append(TraceLevel(4, LEVEL_LABELS[3], "待出账（尚无凭证）", missing=True))

    return levels


def trace_breaks(levels: list[TraceLevel]) -> list[int]:
    """返回断链的级别号列表。

    入参：levels —— 四级链
    出参：如 [1, 4] 表示第 1、4 级缺失；空列表表示完整
    失败行为：不抛异常。
    """
    return [lv.level for lv in levels if lv.missing]
