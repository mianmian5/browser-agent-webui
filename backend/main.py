"""
Browser Agent WebUI — FastAPI 后端
"""
import asyncio, json, os, uuid, base64
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from rate_limit import check_rate_limit, get_remaining
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import BrowserAgent

app = FastAPI(title="Browser Agent WebUI", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 存储活跃的 Agent 会话
sessions: dict[str, BrowserAgent] = {}

# ─── 模型 → Base URL 映射 ──────────────────────
MODEL_BASE_URLS = {
    "gpt-4o":             "https://api.openai.com/v1",
    "gpt-4o-mini":        "https://api.openai.com/v1",
    "claude-sonnet-4-20250514": "https://api.anthropic.com/v1",
    "deepseek-chat":      "https://api.deepseek.com/v1",
    "qwen-plus":          "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = MODEL_BASE_URLS[DEFAULT_MODEL]

# ─── 请求模型 ────────────────────────────────

class RunRequest(BaseModel):
    task: str
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    headless: bool = True  # 始终 headless

# ─── API ─────────────────────────────────────

@app.get("/")
def root():
    return {"message": "Browser Agent WebUI ✅", "docs": "/docs"}


@app.post("/start")
async def start_agent(req: RunRequest, request: Request):
    """启动一个新的 Browser Agent（免费试用使用默认 Key）"""
    import os
    api_key = req.api_key
    remaining = None
    if not api_key:
        # 免费试用：使用默认 Key，扣减次数
        remaining = check_rate_limit(request)
        api_key = os.environ.get("DEEPSEEK_API_KEY", "sk-f0efe677283146978f0bca38505b83cf")
        print(f"⚡ 免费试用，剩余 {remaining} 次")
    else:
        # 用户使用自己的 Key，不扣免费次数
        rem = get_remaining(request)
        remaining = rem["remaining"]

    session_id = uuid.uuid4().hex[:12]
    agent = BrowserAgent(
        llm_api_key=api_key,
        llm_base_url=req.base_url,
        llm_model=req.model,
        headless=True,  # 服务器环境强制 headless
    )
    agent.set_task(req.task)
    await agent.start()
    sessions[session_id] = agent
    return {"session_id": session_id, "status": "started", "remaining_free": remaining}


@app.get("/stream/{session_id}")
async def stream_agent(session_id: str):
    """SSE 流式返回 Agent 的每一步操作"""
    agent = sessions.get(session_id)
    if not agent:
        raise HTTPException(404, "Session not found")

    async def event_stream():
        try:
            while True:
                result = await agent.run_step(agent.initial_task)
                try:
                    img = await agent.screenshot()
                except:
                    img = ""

                payload = {
                    **result,
                    "screenshot": img,
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                if result.get("done"):
                    break
                await asyncio.sleep(0.5)

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            await agent.stop()
            if session_id in sessions:
                del sessions[session_id]
            yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/rate-limit")
def rate_limit_status(request: Request):
    """查看当前 IP 剩余免费次数"""
    return get_remaining(request)


@app.post("/stop/{session_id}")
async def stop_agent(session_id: str):
    """停止 Agent（设标志，由 SSE 流自行清理）"""
    agent = sessions.get(session_id)
    if agent:
        agent.stopped = True
        return {"status": "stopping"}
    return {"status": "not_found"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
