# -*- coding: utf-8 -*-
"""MVP 业务自测：不依赖 FastAPI / SQLAlchemy / 任何网络，纯标准库跑完整闭环。

    python scripts/selftest.py

它证明四件事：
1. 四键归一到同一票；
2. 按提单签发日命中 V4（而不是 9 月的 V3）；
3. 报出 3 条缺项、合计 ¥2,610（1180+980+450），另有 1 条"未匹配"但不计入缺口；
4. 追溯链四段、第 4 段如实标"待出账"；知识库与图谱都能给出来源。

退出码：0 全部通过；1 有断言失败（方便 CI 用）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repo import build_demo_repository                     # noqa: E402
from app.services.collect import build_view, get_shipment      # noqa: E402
from app.services.diff import compute_diff                      # noqa: E402
from app.services.graph import why_applicable                   # noqa: E402
from app.services.kb import LocalMirrorProvider                 # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, actual, expected) -> None:
    """断言并打印一行结果。

    入参：name —— 断言描述；actual —— 实际值；expected —— 期望值
    出参：无
    失败行为：不抛异常，累计失败计数，由 main() 决定退出码。
    """
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}（期望 {expected!r}，实际 {actual!r}）")


def main() -> int:
    """执行全部自测。

    入参：无
    出参：进程退出码（0 通过 / 1 失败）
    失败行为：任何未捕获异常都会打印堆栈并返回 1。
    """
    repo = build_demo_repository()

    print("=" * 68)
    print("结算复核助手 · MVP 自测（内存仓库，无需任何第三方依赖）")
    print("=" * 68)

    # ---- 1. 四键归集 ----
    print("\n[1] 四键归一到同一票")
    keys = [("booking_no", "BK2610-0187"), ("bl_no", "HLCUNB26100457"),
            ("order_no", "SO2610-0187"), ("container_no", "MSKU1234567")]
    ids = {get_shipment(repo, kt, kv).shipment_id for kt, kv in keys}
    check("四个键指向同一 shipment_id", ids, {"SO2610-0187"})

    shipment = get_shipment(repo, "booking_no", "BK2610-0187")
    view = build_view(repo, shipment)
    print(f"     客户：{view['customer']} ｜ 提单日：{view['bl_date']} ｜ {view['route']} ｜ {view['ctn_type']}")
    print(f"     已录应收 {len(view['receivable'])} 行，命中费率卡 {view['rate_card']}")

    # ---- 2. 费率版本 ----
    print("\n[2] 按提单签发日取费率版本（制度 §5.2）")
    check("10-09 提单命中 V4（不是 V3）", view["rate_card"], "V4")

    # ---- 3. 差异 ----
    print("\n[3] 应有 vs 已录 差异")
    diff = compute_diff(repo, shipment)
    missing = [d for d in diff["differences"] if d["kind"] == "缺项"]
    check("缺项条数", len(missing), 3)
    check("缺项合计", diff["totals"], {"CNY": "2,610.00 CNY"})
    check("缺项明细",
          sorted(f"{d['fee_code']}={d['amount']}" for d in missing),
          ["LSS=1,180.00 CNY", "PSS=980.00 CNY", "REB=450.00 CNY"])
    unmatched = [d for d in diff["differences"] if d["kind"] == "未匹配"]
    check("未匹配（不计入缺口）", [d["fee_code"] for d in unmatched], ["OTH"])

    print("     差异明细：")
    for d in diff["differences"]:
        print(f"       {d['kind']:4s} {d['fee_code']:4s} {d['amount']:>14s}  "
              f"{d['expected']} → {d['actual']}  依据={d['evidence']}")

    # ---- 4. 追溯链 ----
    print("\n[4] 追溯链四级（缺哪级标哪级）")
    lss = next(d for d in missing if d["fee_code"] == "LSS")
    levels = lss["trace"]
    check("链长度", len(levels), 4)
    check("断链级别（第 4 级待出账）", [lv["level"] for lv in levels if lv["missing"]], [4])
    for lv in levels:
        flag = "断" if lv["missing"] else "通"
        print(f"       [{flag}] {lv['level']}. {lv['label']}：{lv['value']}")

    # ---- 5. 知识库 ----
    print("\n[5] 知识库检索（Harness 镜像，必须带出处）")
    kb = LocalMirrorProvider("data/kb")
    hits = kb.search("附加费 费率卡 版本 该用哪一版", 3)
    check("镜像库已加载", bool(hits), True)
    check("命中条款均带文号", all(h.doc_no and h.clause_no for h in hits), True)
    for h in hits:
        print(f"       {h.doc_no} {h.clause_no} {h.title}（score={h.score}）")

    # ---- 6. 知识图谱 ----
    print("\n[6] 知识图谱：这条费率为什么适用于这个客户")
    path = why_applicable(repo, "C-0731", "LSS", "V4")
    check("路径跳数", len(path), 3)
    check("终点是 LSS", path[-1]["to"], "FeeItem:LSS")
    for e in path:
        print(f"       {e['from']} -[{e['predicate']}]-> {e['to']}   出处={e['source_ref']}")

    print("\n" + "=" * 68)
    print(f"结果：通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 68)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
