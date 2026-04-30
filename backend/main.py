"""FastAPI 主服务 - 音频采集 + WebSocket 推送"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.config import config
from backend.audio_capture import AudioCapture
from backend.llm import ask_question

app = FastAPI(title="小主播互动机")

# 当前连接的 WebSocket 客户端
connected_websockets = set()
audio_capture = AudioCapture()

# 挂载前端静态文件
frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def serve_frontend():
    """提供前端页面"""
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/status")
async def get_status():
    """获取当前状态"""
    return {
        "running": audio_capture.is_running,
        "accumulated_chars": audio_capture.total_char_count,
        "threshold": config.MIN_WORDS_FOR_QUESTION,
        "is_speaking": audio_capture.is_speaking,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket - 推送问题给前端"""
    await websocket.accept()
    connected_websockets.add(websocket)
    print(f"[WS] 客户端已连接 ({len(connected_websockets)} 个)")
    try:
        # 保持连接，等待消息（或关闭）
        while True:
            data = await websocket.receive_text()
            # 可以处理来自前端的消息（如重置、配置等）
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        connected_websockets.discard(websocket)
        print(f"[WS] 客户端断开 ({len(connected_websockets)} 个)")


async def broadcast_question(question: str):
    """向所有连接的客户端广播问题"""
    if not connected_websockets:
        print("[广播] 无连接客户端，跳过")
        return
    message = json.dumps(
        {
            "type": "question",
            "text": question,
            "timestamp": asyncio.get_event_loop().time(),
        }
    )
    print(f"[广播] 推送问题: {question}")
    # 并发发送
    await asyncio.gather(
        *[ws.send_text(message) for ws in connected_websockets],
        return_exceptions=True,
    )


async def broadcast_status(status: str, detail: str = ""):
    """广播状态消息"""
    if not connected_websockets:
        return
    message = json.dumps(
        {
            "type": "status",
            "status": status,
            "detail": detail,
        }
    )
    await asyncio.gather(
        *[ws.send_text(message) for ws in connected_websockets],
        return_exceptions=True,
    )


async def audio_loop():
    """音频采集 + LLM 处理循环"""

    # 注册回调
    async def on_question_ready(context_text: str):
        """当音频累积达到阈值时调用"""
        await broadcast_status("processing", f"正在分析 {len(context_text)} 字内容...")
        question = await ask_question(context_text)
        if question:
            await broadcast_question(question)
            await broadcast_status("listening", "继续监听中...")
        else:
            await broadcast_status("error", "生成问题失败，请检查 API Key")

    audio_capture.on_question_ready = on_question_ready

    # 给前端发启动状态
    await broadcast_status("listening", "正在监听麦克风...")
    print("=" * 50)
    print("小主播互动机已启动!")
    print(f"触发阈值: {config.MIN_WORDS_FOR_QUESTION} 字")
    print(f"最大强制触发: {config.MAX_WORDS_FORCE_TRIGGER} 字")
    print(f"LLM 模型: {config.LLM_MODEL}")
    print(f"WebSocket: ws://{config.SERVER_HOST}:{config.SERVER_PORT}/ws")
    print(f"前端页面: http://{config.SERVER_HOST}:{config.SERVER_PORT}")
    print("=" * 50)

    await audio_capture.start()


@app.on_event("startup")
async def startup():
    """启动时自动开始监听"""
    asyncio.create_task(audio_loop())


@app.on_event("shutdown")
async def shutdown():
    """关闭时停止采集"""
    audio_capture.stop()


def main():
    uvicorn.run(
        "backend.main:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
