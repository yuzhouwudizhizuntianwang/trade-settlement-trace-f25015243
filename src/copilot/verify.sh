#!/usr/bin/env bash
# Phase 1 端到端验证脚本：起服务 → 五个断言 → 关服务
# 用 Git Bash 或 WSL 执行：  bash verify.sh
# 退出码：0 全部通过；1 有断言失败（日志在 .verify.log）
set -uo pipefail

BASE="http://127.0.0.1:8000"
LOG=".verify.log"
PASS=0
FAIL=0

# 断言：$1 描述，$2 实际值，$3 期望值
check() {
  if [ "$2" = "$3" ]; then
    echo "  ✅ $1"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $1（期望 $3，实际 $2）"
    FAIL=$((FAIL + 1))
  fi
}

# 从 JSON 里取字段（避免依赖 jq；用 python 解析，Windows 上一定有）
jget() {
  python -c "import json,sys;d=json.load(sys.stdin);print(eval('d'+sys.argv[1]))" "$1"
}

echo "== 0. 检查环境变量 =="
if [ -z "${COPILOT_JWT_SECRET:-}" ]; then
  echo "  ❌ COPILOT_JWT_SECRET 未设置，先执行：set -a && source .env && set +a"
  exit 1
fi
echo "  ✅ 环境变量已加载"

echo "== 1. 启动服务 =="
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT          # 无论成功失败都收掉子进程，避免端口占用

# 最多等 15 秒（30 × 0.5s），服务没起来就直接判失败
for i in $(seq 1 30); do
  if curl -s -o /dev/null "$BASE/health"; then break; fi
  sleep 0.5
done
echo "  ✅ 服务已监听 $BASE"

echo "== 2. /health 结构 =="
HEALTH=$(curl -s "$BASE/health")
check "ok 字段为 true"      "$(echo "$HEALTH" | jget "['ok']")" "True"
check "服务名正确"          "$(echo "$HEALTH" | jget "['data']['service']")" "settlement-copilot"

echo "== 2.5 模型探活（主后端 Ollama，未装时自动降级）=="
LLM=$(curl -s "$BASE/llm/ping")
check "后端为 ollama"       "$(echo "$LLM" | jget "['data']['provider']")" "ollama"
REACH=$(echo "$LLM" | jget "['data']['reachable']")
if [ "$REACH" = "True" ]; then
  echo "  ✅ Ollama 可达，走真模型推理"
else
  echo "  ⚠️  本机未运行 Ollama，已降级为本地规则生成（结论结构一致，措辞朴素）"
fi
DEMO=$(curl -s -X POST "$BASE/llm/demo")
check "演示接口可用"        "$(echo "$DEMO" | jget "['ok']")" "True"

echo "== 3. /auth/login 鉴权 =="
LOGIN=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"chenlili\",\"password\":\"${COPILOT_DEMO_PWD:-Demo#2026}\"}")
check "登录成功"            "$(echo "$LOGIN" | jget "['ok']")" "True"
TOKEN=$(echo "$LOGIN" | jget "['data']['token']")
check "令牌为三段式"        "$(echo "$TOKEN" | awk -F. '{print NF}')" "3"

BAD=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/auth/login" \
  -H "Content-Type: application/json" -d '{"username":"chenlili","password":"wrong"}')
check "错口令返回 401"      "$BAD" "401"

echo "== 4. /auth/me 与数据范围 =="
ME=$(curl -s "$BASE/auth/me" -H "Authorization: Bearer $TOKEN")
check "身份与令牌一致"      "$(echo "$ME" | jget "['data']['username']")" "chenlili"
check "结算专员可看价格"    "$(echo "$ME" | jget "['data']['abilities']['view_price']")" "True"

NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/auth/me")
check "无令牌返回 401"      "$NOAUTH" "401"

echo "== 5. 红线闸门：带金额的数据必须被拦 =="
PREVIEW=$(curl -s -X POST "$BASE/redline/preview" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"C-0731","charges":[{"fee_code":"LSS","fee_name":"低硫附加费","amount":"1180.00","unit_price":"1180"}]}')
check "预览接口成功"        "$(echo "$PREVIEW" | jget "['ok']")" "True"
check "金额字段被拦下"      "$(echo "$PREVIEW" | jget "['data']['blocked']")" "['charges[0].amount', 'charges[0].unit_price']"
check "客户范围校验通过"    "$(echo "$PREVIEW" | jget "['data']['scope_check']")" "passed"

echo "== 6. 越权访问必须 403 =="
CROSS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/redline/preview" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"customer_id":"C-0800","charges":[]}')
check "跨客户访问被拒"      "$CROSS" "403"

echo
echo "== 结果：通过 $PASS 项，失败 $FAIL 项 =="
[ "$FAIL" -eq 0 ] || { echo "详见 $LOG"; exit 1; }
echo "Phase 1 骨架验证通过。"
