# -*- coding: utf-8 -*-
"""知识图谱服务：三元组的 2 跳扩展与路径证据。

为什么不用图数据库：课设规模万级三元组，一张表 + BFS 就够；
每条边都带 source_ref（条款号/单据号），保证"关系可回溯"。
"""

from __future__ import annotations

from app.repo import Repository

MAX_HOPS = 2          # 硬上限：超过 2 跳关系会爆炸，答案也会变得不可解释


def _neighbors(repo: Repository, node: str) -> list[dict]:
    """取一个节点的所有相邻边（出边 + 入边）。

    入参：repo —— 仓库；node —— 形如 "RateCard:V4"
    出参：三元组列表，每条追加 direction 字段（out/in）
    失败行为：不抛异常，无邻居返回空列表。
    """
    out = [dict(t, direction="out") for t in repo.triple_search(subject=node)]
    inn = [dict(t, direction="in") for t in repo.triple_search(obj=node)]
    return out + inn


def expand(repo: Repository, entity: str, hops: int = MAX_HOPS) -> dict:
    """从实体出发做有限跳扩展，返回按层组织的路径。

    入参：
        repo —— 仓库
        entity —— 实体标识，如 "RateCard:V4" 或 "Customer:C-0731"
        hops —— 跳数（>2 会被截断到 2）
    出参：
        {"root": entity, "layers": [{"hop":1,"edges":[...],"nodes":[...]}, ...]}
    失败行为：不抛异常；实体不存在时 layers 为空。
    """
    hops = min(max(int(hops or 1), 1), MAX_HOPS)     # 夹在 1..2，防外部传入 99
    visited = {entity}
    layers: list[dict] = []
    frontier = [entity]

    for hop in range(1, hops + 1):
        edges: list[dict] = []
        next_frontier: list[str] = []
        for node in frontier:
            for edge in _neighbors(repo, node):
                # 取边的另一端，统一成"从当前节点看出去"的表述
                other = edge["object"] if edge["subject"] == node else edge["subject"]
                edge_view = {
                    "from": node if edge["subject"] == node else other,
                    "predicate": edge["predicate"],
                    "to": other if edge["subject"] == node else node,
                    "source_ref": edge.get("source_ref", ""),
                    "confidence": edge.get("confidence", ""),
                    "direction": "out" if edge["subject"] == node else "in",
                }
                edges.append(edge_view)
                if other not in visited:
                    visited.add(other)
                    next_frontier.append(other)
        layers.append({"hop": hop, "edges": edges, "nodes": next_frontier})
        frontier = next_frontier
        if not frontier:                              # 没有新节点就提前结束
            break

    return {"root": entity, "max_hops": hops, "layers": layers}


def why_applicable(repo: Repository, customer_id: str, fee_code: str,
                   version: str = "") -> list[dict]:
    """回答"这条费率为什么适用于这个客户"——返回一条可读路径。

    入参：
        repo —— 仓库；customer_id —— 客户号；fee_code —— 费用项
        version —— 期望命中的费率卡版本（传了优先选它，避免走到旧版本）
    出参：路径边列表（客户 → 合同 → 费率卡 → 费用项）
    失败行为：不抛异常；路径断在哪就返回已走到的部分。
    """
    path: list[dict] = []
    cur = f"Customer:{customer_id}"
    for predicate, target_prefix in (("HAS_CONTRACT", "Contract:"),
                                     ("HAS_RATE", "RateCard:")):
        edges = repo.triple_search(subject=cur, predicate=predicate)
        if not edges:
            break
        # 优先选"覆盖该费用项"的那条边，选不到就用第一条
        chosen = edges[0]
        if version:                                  # 指定版本：直接命中，避免走旧版
            for e in edges:
                if e["object"].endswith(f":{version}"):
                    chosen = e
                    break
        else:
            for e in edges:                          # 未指定：优先能覆盖该费用项的版本
                if repo.triple_search(subject=e["object"], predicate="COVERS",
                                      obj=f"FeeItem:{fee_code}"):
                    chosen = e
                    break
        path.append({"from": cur, "predicate": predicate, "to": chosen["object"],
                     "source_ref": chosen.get("source_ref", "")})
        cur = chosen["object"]

    covers = repo.triple_search(subject=cur, predicate="COVERS", obj=f"FeeItem:{fee_code}")
    if covers:
        path.append({"from": cur, "predicate": "COVERS", "to": f"FeeItem:{fee_code}",
                     "source_ref": covers[0].get("source_ref", "")})
    return path
