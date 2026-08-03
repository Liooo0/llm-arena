#!/usr/bin/env bash
# LLM Arena 验收测试脚本
# 真实发起 3 模型对比评测 → 打分 → 校验排行榜与历史，全部通过后打印 ACCEPTANCE PASSED。
# 用法：先完成 README 里的 配置 key 和 代理 两步，再执行
#   bash scripts/acceptance_test.sh
#
# 注意：脚本本身不含任何 key；key 从环境变量 LLM_ARENA_API_KEY 读取。

set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
PY=python3  # 用系统 python3 解析 JSON 即可，脚本不依赖 venv 包
N="${N:-3}" # 参与评测的模型数，默认 3（省时），可设 N=5 全量

if [ -z "${LLM_ARENA_API_KEY:-}" ]; then
  echo "[FAIL] 缺少环境变量 LLM_ARENA_API_KEY，请先 export（见 README）" >&2
  exit 1
fi

echo "==> 1/5 GET /api/models"
curl -sf "$BASE/api/models" | $PY -c "import json,sys; d=json.load(sys.stdin); assert len(d['models'])>0 and len(d['categories'])>0; print('    模型', len(d['models']), '个, 分类', len(d['categories']), '个')"

MODELS=$(curl -sf "$BASE/api/models" | $PY -c "import json,sys; print(json.dumps([m['id'] for m in json.load(sys.stdin)['models']][:$N]))")

echo "==> 2/5 POST /api/battle（$N 个模型真实并发评测）"
BATTLE=$(curl -sf -X POST "$BASE/api/battle" -H 'Content-Type: application/json' \
  -d "{\"question\":\"用 Python 写一个快速排序函数，并解释它的时间复杂度。\",\"category\":\"代码\",\"models\":$MODELS}")
echo "$BATTLE" | $PY -c "
import json,sys
d=json.load(sys.stdin)
assert d['question_id']>0, 'question_id 无效'
ok=sum(1 for a in d['answers'] if not a['error'])
print(f'    question_id={d[\"question_id\"]}, 成功 {ok}/{len(d[\"answers\"])}')
assert ok>0, '没有任何模型回复成功'
"
QID=$(echo "$BATTLE" | $PY -c "import json,sys; print(json.load(sys.stdin)['question_id'])")

echo "==> 3/5 POST /api/rate（给每个回答打分）"
echo "$BATTLE" | $PY -c "
import json,sys,urllib.request
d=json.load(sys.stdin)
for i,a in enumerate(d['answers']):
    score=(i%5)+1
    req=urllib.request.Request('$BASE/api/rate',
        data=json.dumps({'answer_id':a['answer_id'],'score':score}).encode(),
        headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(req) as r:
        assert r.status==200, f'rating HTTP {r.status}'
    print(f'    answer_id={a[\"answer_id\"]} -> {score}星')
"

echo "==> 4/5 GET /api/leaderboard"
curl -sf "$BASE/api/leaderboard" | $PY -c "
import json,sys
d=json.load(sys.stdin)
assert len(d['items'])>0, '排行榜为空'
for it in sorted(d['items'], key=lambda x:-(x['avg_score'] or 0))[:5]:
    print(f\"    {it['model']:20s} avg={it['avg_score']} count={it['battle_count']} lat={it['avg_latency_ms']}ms\")
assert any(it['avg_score'] is not None for it in d['items']), '打分为生效'
"

echo "==> 5/5 GET /api/history 与详情"
curl -sf "$BASE/api/history" | $PY -c "
import json,sys
d=json.load(sys.stdin)
assert any(i['id']==$QID for i in d['items']), '评测未出现在历史'
"
curl -sf "$BASE/api/history/$QID" | $PY -c "
import json,sys
d=json.load(sys.stdin)
assert len(d['answers'])>0, '详情无回答'
assert all(a['rating'] is not None for a in d['answers']), '存在未打分回答'
print('    历史详情正常,', len(d['answers']), '条回答均含评分')
"

echo ""
echo "=============================="
echo "  ✅ ACCEPTANCE PASSED"
echo "=============================="
