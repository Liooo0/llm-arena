# LLM Arena 项目约定

## 技术栈
- FastAPI + 原生前端（HTML/CSS/JS，无构建工具）+ SQLite（标准库）
- LLM: OpenAI 兼容格式，base_url `https://opencode.ai/zen/go/v1`，Bearer 鉴权
- 依赖: fastapi, uvicorn, httpx, python-dotenv

## 模型分层（作品集核心叙事）
- **生产默认**: `deepseek-v4-flash`（快速便宜，适合日常高并发）— 其他项目（liodesktop / renovation-bot / job-hunter / boss-zhipin-helper / jobintel-dashboard）的生产默认模型
- **高级推理**: `deepseek-v4-pro`（深度推理、代码分析）— liodesktop 复杂核验、llm-arena benchmark
- **benchmark 参照系**: glm-5.2 / kimi-k3 / qwen3.7-max + 国内直连备选（deepseek-v4-flash-0731 / qwen3.8-max-preview）
- llm-arena 必须同时接 Flash + Pro，用对战数据证明「模型选型能力」

## 运行
```bash
cd ~/projects/llm-arena
source venv/bin/activate
export HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890
export LLM_ARENA_API_KEY=$(grep -o 'OPENCODE_GO_API_KEY=.*' ~/.hermes/.env | cut -d= -f2)
uvicorn app:app --host 127.0.0.1 --port 8000
```

## 红线
- API key 只从环境变量 LLM_ARENA_API_KEY 读，绝不写进代码/README/git
- .env、venv/、*.db 在 .gitignore 里
- 本机访问 opencode.ai 必须走代理 127.0.0.1:7890（HTTPS_PROXY）
- 只在本目录操作，不用 sudo
