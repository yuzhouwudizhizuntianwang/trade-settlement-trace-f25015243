# -*- coding: utf-8 -*-
"""Harness 知识库连通性探测：拿到端点后先跑这个，再改 config。

    python scripts/harness_probe.py

做三件事：
1. 读 config.yaml 的 knowledge.harness.*（密钥来自环境变量）；
2. 按 POST {endpoint}/search 发一次真实请求；
3. 打印 HTTP 状态、耗时、返回结构摘要、是否能解析出 doc_no + clause_no。

退出码：0 可用；2 未配置端点；1 请求失败或结构不可解析。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    """执行探测。

    入参：无
    出参：退出码
    失败行为：捕获所有网络异常并打印原因，不抛堆栈给使用者。
    """
    try:
        from app.core.config import get_settings
        st = get_settings()
    except Exception as exc:                              # noqa: BLE001 配置读不了就没法探测
        print(f"[x] 读配置失败：{exc}")
        return 1

    endpoint = st.get("knowledge.harness.endpoint", "")
    kb_id = st.get("knowledge.harness.kb_id", "")
    api_key = st.get("knowledge.harness.api_key", "")
    if not endpoint:
        print("[!] knowledge.harness.endpoint 为空。")
        print("    在 .env 里设置 COPILOT_HARNESS_ENDPOINT / COPILOT_HARNESS_API_KEY / COPILOT_HARNESS_KB_ID 后再跑。")
        print("    在此之前系统会使用本地镜像（data/kb/*.md），行为一致但少了在线出处。")
        return 2

    payload = {"kb_id": kb_id, "query": "附加费 费率卡 版本", "top_k": 3}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(f"{endpoint.rstrip('/')}/search",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            status = resp.status
    except urllib.error.HTTPError as exc:
        print(f"[x] HTTP {exc.code}：{exc.reason}（检查路径是否为 /search、鉴权头是否正确）")
        return 1
    except Exception as exc:                              # noqa: BLE001
        print(f"[x] 请求失败：{exc}")
        return 1

    cost = round(time.time() - started, 2)
    print(f"[ok] HTTP {status}  耗时 {cost}s")
    print(f"[..] 顶层键：{list(body)[:8] if isinstance(body, dict) else type(body).__name__}")

    # 复用适配层的容错解析，验证能否拿到可引用的条款
    from app.services.kb import HarnessProvider
    hits = HarnessProvider._pick(body, 3)                 # noqa: SLF001 探测脚本直接用内部解析
    print(f"[..] 可解析条目：{len(hits)}")
    for h in hits:
        print(f"     {h.doc_no} {h.clause_no} {h.title}（score={h.score}）")
    if not hits:
        print("[!] 没能解析出 doc_no + clause_no，说明返回结构与适配层不匹配，需要改 HarnessProvider._pick()")
        return 1
    print("[✓] Harness 可用，把 config.yaml 的 knowledge.provider 设为 harness 即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
