# LLM Arena 项目约定

## 技术栈
- FastAPI + 原生前端（HTML/CSS/JS，无构建工具）+ SQLite（标准库）
- LLM: OpenAI 兼容格式，base_url `https://opencode.ai/zen/go/v1`，Bearer 鉴权
- 依赖: fastapi, uvicorn, httpx, python-dotenv

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
