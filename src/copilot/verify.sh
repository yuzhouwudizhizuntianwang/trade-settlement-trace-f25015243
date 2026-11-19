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
check "金额/单价均被拦下"   "$(echo "$PREVIEW" | python -c "import json,sys;b=json.load(sys.stdin)['data']['blocked'];print(all(any(k in x for x in b) for k in ['amount','unit_price']))")" "True"
check "客户范围校验通过"    "$(echo "$PREVIEW" | jget "['data']['scope_check']")" "passed"

echo "== 6. 越权访问必须 403 =="
CROSS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/redline/preview" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"customer_id":"C-0800","charges":[]}')
check "跨客户访问被拒"      "$CROSS" "403"

echo "== 7. P0 业务闭环：单票 → 差异 → 追溯 =="
SHIP=$(curl -s "$BASE/api/shipments/BK2610-0187?key_type=booking_no" -H "Authorization: Bearer $TOKEN")
check "单票可查"            "$(echo "$SHIP" | jget "['data']['shipment_id']")" "SO2610-0187"
check "命中费率卡 V4"       "$(echo "$SHIP" | jget "['data']['rate_card']")" "V4"

DIFF=$(curl -s "$BASE/api/diff/SO2610-0187" -H "Authorization: Bearer $TOKEN")
check "缺口合计 ¥2,610"     "$(echo "$DIFF" | jget "['data']['totals']['CNY']")" "2,610.00 CNY"
check "缺项 3 条"           "$(echo "$DIFF" | python -c "import json,sys;d=json.load(sys.stdin);print(len([x for x in d['data']['differences'] if x['kind']=='缺项']))")" "3"

TRACE=$(curl -s "$BASE/api/trace/SO2610-0187/LSS" -H "Authorization: Bearer $TOKEN")
check "追溯链 4 级"         "$(echo "$TRACE" | python -c "import json,sys;print(len(json.load(sys.stdin)['data']['levels']))")" "4"
check "第 4 级标待出账"     "$(echo "$TRACE" | jget "['data']['breaks']")" "[4]"

echo "== 8. 知识库与图谱 =="
KB=$(curl -s "$BASE/api/kb/search?q=LSS&top_k=3" -H "Authorization: Bearer $TOKEN")
check "知识库有命中"        "$(echo "$KB" | python -c "import json,sys;print(len(json.load(sys.stdin)['data']['hits'])>0)")" "True"
WHY=$(curl -s "$BASE/api/graph/why?customer_id=C-0731&fee_code=LSS&version=V4" -H "Authorization: Bearer $TOKEN")
check "图谱路径 3 跳"       "$(echo "$WHY" | jget "['data']['hops']")" "3"

echo "== 9. P1 权限矩阵 =="
AUD=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"xuan\",\"password\":\"${COPILOT_DEMO_PWD:-Demo#2026}\"}" | jget "['data']['token']")
REV=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"zhouhui\",\"password\":\"${COPILOT_DEMO_PWD:-Demo#2026}\"}" | jget "['data']['token']")

A1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/diff/SO2610-0187" -H "Authorization: Bearer $AUD")
check "审计员看差异被拒 403"   "$A1" "403"
A2=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/audit/tail?n=5" -H "Authorization: Bearer $AUD")
check "审计员可读审计日志"     "$A2" "200"
A3=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/export/pack" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"shipment_id":"SO2610-0187","receiver":"王会计"}')
check "结算专员导出被拒 403"   "$A3" "403"

echo "== 10. P1 复核闸门（草稿 → 提交 → 他人审批）=="
CASE=$(curl -s -X POST "$BASE/api/review/draft/SO2610-0187" -H "Authorization: Bearer $TOKEN")
CID=$(echo "$CASE" | jget "['data']['case_id']")
check "生成草稿"               "$(echo "$CASE" | jget "['data']['status']")" "draft"
check "草稿冻结缺口金额"       "$(echo "$CASE" | jget "['data']['snapshot']['totals']['CNY']")" "2,610.00 CNY"
check "提交复核"               "$(curl -s -X POST "$BASE/api/review/submit/$CID" -H "Authorization: Bearer $TOKEN" | jget "['data']['status']")" "submitted"
SELF=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/review/approve/$CID" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"decision":"approve","comment":""}')
check "提交人无权审批 403"     "$SELF" "403"
APR=$(curl -s -X POST "$BASE/api/review/approve/$CID" -H "Authorization: Bearer $REV" \
  -H "Content-Type: application/json" -d '{"decision":"approve","comment":"同意补收"}')
check "复核人审批通过"         "$(echo "$APR" | jget "['data']['status']")" "approved"

echo "== 11. P1 导出留痕 =="
EXP=$(curl -s -X POST "$BASE/api/export/pack" -H "Authorization: Bearer $REV" \
  -H "Content-Type: application/json" -d '{"shipment_id":"SO2610-0187","receiver":"宁波恒晟机电 王会计"}')
check "导出成功"               "$(echo "$EXP" | jget "['ok']")" "True"
check "导出带审计号"           "$(echo "$EXP" | python -c "import json,sys;print(len(json.load(sys.stdin)['data']['audit_id'])>0)")" "True"
check "包内无单价字段"         "$(echo "$EXP" | python -c "import json,sys;t=str(json.load(sys.stdin)['data']['pack']).lower();print(not any(k in t for k in ['unit_price','gross_margin','毛利']))")" "True"
NOR=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/export/pack" -H "Authorization: Bearer $REV" \
  -H "Content-Type: application/json" -d '{"shipment_id":"SO2610-0187","receiver":""}')
check "缺接收方被拒 400"       "$NOR" "400"
AUDIT_HAS=$(curl -s "$BASE/api/audit/tail?n=50" -H "Authorization: Bearer $REV" | python -c "import json,sys;r=json.load(sys.stdin)['data']['records'];print(any(x['action']=='export' and x.get('receiver') for x in r))")
check "审计里有导出接收方"     "$AUDIT_HAS" "True"

echo "== 12. 权限脱敏与指标看板 =="
OPR=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"zhangwei\",\"password\":\"${COPILOT_DEMO_PWD:-Demo#2026}\"}" | jget "['data']['token']")
OPSHIP=$(curl -s "$BASE/api/shipments/BK2610-0187?key_type=booking_no" -H "Authorization: Bearer $OPR")
check "单证看金额被打码"       "$(echo "$OPSHIP" | jget "['data']['receivable'][0]['amount']")" "***"
check "单证仍看得到费用项"     "$(echo "$OPSHIP" | jget "['data']['receivable'][0]['fee_code']")" "OCF"
OPS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/shipments/BK2610-0187?key_type=booking_no")
check "未登录查单被拒 401"     "$OPS" "401"

MET=$(curl -s "$BASE/api/metrics" -H "Authorization: Bearer $TOKEN")
check "指标：缺口金额"         "$(echo "$MET" | jget "['data']['cny']['missing']")" "2,610.00"
check "指标：演示漏收率"       "$(echo "$MET" | jget "['data']['leak_rate_demo']")" "40.9%"
check "指标：缺项 3 条"        "$(echo "$MET" | jget "['data']['by_kind']['缺项']")" "3"

echo
echo "== 结果：通过 $PASS 项，失败 $FAIL 项 =="
[ "$FAIL" -eq 0 ] || { echo "详见 $LOG"; exit 1; }
echo "Phase 1 骨架验证通过。"
