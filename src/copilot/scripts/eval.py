# -*- coding: utf-8 -*-
"""评估集回放：用已知答案的样例检验差异识别，输出召回/精确率。

    python scripts/eval.py

为什么要有它：MVP 能报出 ¥2,610 只说明"这一票对了"，不说明"规则没瞎报"。
评估集至少能给出两个数：漏项召回（该报的报了没）与误报（不该报的报了没）。
"""

import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repo import build_demo_repository               # noqa: E402
from app.services.diff import compute_diff                # noqa: E402


def main() -> int:
    """回放全部样例并打印指标。

    入参：无
    出参：退出码（0 全对 / 1 有不一致）
    失败行为：期望文件缺失或找不到单票时打印原因并返回 1。
    """
    path = "data/eval_expected.json"
    if not os.path.exists(path):
        print(f"[x] 缺少期望文件：{path}")
        return 1
    expected = json.loads(open(path, encoding="utf-8").read())["cases"]
    repo = build_demo_repository()

    tp = fp = fn = 0
    all_ok = True
    print("=" * 66)
    print("评估集回放")
    print("=" * 66)

    for case in expected:
        sid = case["shipment_id"]
        ship = repo.shipment_by_key("order_no", sid)
        if not ship:
            print(f"[x] 找不到单票 {sid}")
            all_ok = False
            continue
        diff = compute_diff(repo, ship)
        got_missing = {d["fee_code"] for d in diff["differences"] if d["kind"] == "缺项"}
        want_missing = set(case["missing_codes"])
        got_unmatched = {d["fee_code"] for d in diff["differences"] if d["kind"] == "未匹配"}

        tp += len(got_missing & want_missing)
        fp += len(got_missing - want_missing)
        fn += len(want_missing - got_missing)

        total_ok = Decimal(diff["totals"].get("CNY", "0 CNY").split()[0].replace(",", "")) == \
            Decimal(case["missing_total_cny"].replace(",", ""))
        card_ok = diff["rate_card"] == case["expect_rate_card"]
        unmatched_ok = got_unmatched == set(case["unmatched_codes"])

        ok = got_missing == want_missing and total_ok and card_ok and unmatched_ok
        all_ok = all_ok and ok
        print(f"\n[{sid}] {'✅ 通过' if ok else '❌ 不一致'}")
        print(f"   缺项  期望 {sorted(want_missing)} / 实际 {sorted(got_missing)}")
        print(f"   金额  期望 {case['missing_total_cny']} / 实际 {diff['totals'].get('CNY')}")
        print(f"   版本  期望 {case['expect_rate_card']} / 实际 {diff['rate_card']}")
        print(f"   未匹配 期望 {case['unmatched_codes']} / 实际 {sorted(got_unmatched)}")

    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    print("\n" + "-" * 66)
    print(f"漏项召回率 {recall:.0%}｜精确率 {precision:.0%}｜TP={tp} FP={fp} FN={fn}")
    print("=" * 66)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
