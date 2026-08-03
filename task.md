# LLM Arena 模型竞技场 — 开发任务书

你是资深全栈工程师，用一整晚的时间，从零构建一个完整的求职作品集项目。**目标是：明早能直接打开浏览器演示给面试官看。**

## 项目定位

LLM Arena（模型竞技场）：一个本地 Web 应用。输入一个问题，同时发给多个大模型，并排对比回答，用户打分，生成排行榜。展示"AI 应用开发能力 + 模型选型理解"。

## 技术栈（必须严格遵守，不许擅自换）

- 后端：Python FastAPI（python3.11，macOS 自带）
- 前端：**原生 HTML/CSS/JavaScript 单页应用**（绝不用 React/Vue/构建工具，零构建步骤）
- 数据库：SQLite（python 标准库 sqlite3，不用 ORM）
- LLM 调用：OpenAI 兼容 chat/completions 格式，用 `httpx`（AsyncClient）或 `openai` 库
  - base_url: `https://opencode.ai/zen/go/v1`
  - 鉴权: `Authorization: Bearer <key>`
- 依赖仅限：fastapi, uvicorn, httpx, python-dotenv

## API Key 处理（安全红线）

- Key 从环境变量 `LLM_ARENA_API_KEY` 读取，**绝不写进任何代码/README/commit**
- 项目放一个 `.env.example`（内容是 `LLM_ARENA_API_KEY=` 占位）
- `.gitignore` 必须包含 `.env`、`venv/`、`__pycache__/`、`*.db`
- 运行/测试时的真实 key 从 `~/.hermes/.env` 文件里读取（该文件有一行 `OPENCODE_GO_API_KEY=sk-...`，用 shell 读取后 export 成 `LLM_ARENA_API_KEY`）

## 网络（重要，本机特殊）

- 直连 `opencode.ai` 会被 Cloudflare 拦截（HTTP 403/超时），**必须走本机 ClashX 代理**
- 测试应用时：`export HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890`
- 注意：**代理环境变量只在启动/测试应用的命令里设置**（子 shell 内），不要污染你自己的工作会话
- httpx 会自动尊重 HTTPS_PROXY 环境变量，应用代码无需硬编码代理

## 可用模型（同一个 key 都可用）

- `deepseek-v4-flash`（便宜快速）
- `deepseek-v4-pro`
- `glm-5.2`
- `kimi-k3`
- `qwen3.7-max`

## 功能需求

1. **对比评测（核心）**
   - 输入框 + 模型多选（默认全选 5 个）+ 分类选择（代码 / 中文写作 / 翻译 / 推理 / 通用）
   - 提交后**并发**调用所有选中模型（asyncio.gather）
   - 结果卡片：模型名、回答（Markdown 渲染）、耗时、token 数
   - 单个模型失败不影响其他（卡片显示错误原因）
2. **打分**：每个回答可打 1-5 分（星标点击），存数据库，可修改
3. **排行榜**：按平均分排序，支持按分类筛选，显示对比次数、平均耗时
4. **历史记录**：所有评测可回看（问题、各模型回答、打分）
5. **健康检查**：页面显示每个模型 API 是否可用（启动时或点击时 ping）

## 页面（中文界面，深色主题，现代 AI 工具质感）

- `/` 评测页：问题输入、模型多选、分类、提交按钮、并排结果卡片
- `/leaderboard` 排行榜页
- `/history` 历史页
- 纯前端 fetch 调后端 API，页面间可跳转（导航栏）

## 目录结构（就放 ~/projects/llm-arena/ 下）

```
app.py             # FastAPI 入口 + 路由
llm_client.py      # LLM 并发调用（asyncio）
database.py        # SQLite 建表 + 操作
static/
  index.html
  leaderboard.html
  history.html
  css/style.css
  js/ (按需)
requirements.txt
.env.example
.gitignore
README.md          # 中文：项目介绍/技术架构/功能截图占位/如何运行/面试要点
CLAUDE.md          # 项目约定（你自己写，简明）
```

数据库表（database.py 里建）：
- `questions(id, text, category, created_at)`
- `answers(id, question_id, model, content, tokens, latency_ms, error, created_at)`
- `ratings(id, answer_id, score, created_at)`（唯一约束 answer_id）

API 设计：
- `POST /api/battle` body `{question, category, models: []}` → 并发调用 → 返回 question_id + answers[]
- `POST /api/rate` body `{answer_id, score}` → 写/更新评分
- `GET /api/leaderboard?category=` → 排行数据
- `GET /api/history` → 历史列表
- `GET /api/history/{id}` → 单次评测详情
- `GET /api/models` → 可用模型列表（静态配置，不暴露 key）

## 实现要求

- 后端 python 文件要有类型注解和简短 docstring
- 前端：回答用简单 Markdown 渲染（自己写个最小渲染器：标题/粗体/代码块/列表，或引入 marked.min.js 的本地副本——**不要用 CDN**，离线可用）
- 界面美观：深色背景、卡片式布局、模型用不同颜色区分、响应式
- 并发数不要太高：同时最多 5 个请求，每个请求 timeout 60s

## 测试步骤（必须实际执行，不能跳过）

```bash
cd ~/projects/llm-arena
python3 -m venv venv && source venv/bin/activate
pip install fastapi uvicorn httpx python-dotenv
# 起服务（注意代理和 key）
export HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890
export LLM_ARENA_API_KEY=$(grep -o 'OPENCODE_GO_API_KEY=.*' ~/.hermes/.env | cut -d= -f2)
uvicorn app:app --host 127.0.0.1 --port 8000 &
# 然后 curl 测试：
curl http://127.0.0.1:8000/api/models
curl -X POST http://127.0.0.1:8000/api/battle -H 'Content-Type: application/json' -d '{"question":"用一句话介绍你自己","category":"通用","models":["deepseek-v4-flash","glm-5.2","kimi-k3"]}'
# 检查返回、打分、排行榜、历史都通
# 测完杀掉 uvicorn
```

**验收标准（全部满足才算完成）：**
1. 三个页面都能打开，界面完整
2. 至少做一次 3 个以上模型的真实对比评测，结果正确入库
3. 打分 → 排行榜数据变化正确
4. README.md 完整（中文、面试可展示，含运行方法）
5. git init 并分阶段提交（`feat: 初始项目结构`、`feat: LLM并发调用`、`feat: 前端页面`、`feat: 测试通过` 等），`.env` 绝不提交

## 约束红线

- 只在 `~/projects/llm-arena/` 目录内操作，不碰系统文件、不 sudo、不装全局包
- API key 绝不落盘到代码/README/git
- 如果某个功能卡住超过 20 分钟，降级：保证核心链路（评测+打分+排行+历史）可用，放弃锦上添花
- 每完成一个里程碑，git commit 一次

开始吧。先初始化项目结构，再逐功能实现，最后测试验收。
