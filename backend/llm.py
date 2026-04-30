"""LLM 调用模块 - 硅基流动 API (OpenAI 兼容格式)"""

import json
from typing import Optional
import httpx
from backend.config import config

QUESTION_SYSTEM_PROMPT = """你是一个直播间里的虚拟观众，正在认真听主播讲书。

你的任务：
1. 仔细阅读主播刚才讲的内容
2. 提炼出一个最核心、最可能被真实观众问到的那个问题
3. 问题要简短（20字以内）、口语化、带一点亲切感
4. 模拟"弹幕提问"的风格，语气轻松自然
5. 每次只输出一个问题，不要解释，不要多余文字

示例风格：
- "主播，刚才说的这个观点能展开讲讲吗？"
- "等等，这里我没听懂，能再说一遍吗？"
- "这个和前面说的那个矛盾了吧？"
- "哥哥哥，我有个问题！👋"  (可以适当加 emoji)"""


async def ask_question(context_text: str) -> Optional[str]:
    """
    根据主播讲的内容，生成一个观众会问的问题。
    返回问题文本，失败返回 None。
    """
    if not config.LLM_API_KEY or config.LLM_API_KEY.startswith("sk-xxx"):
        return "[提示] 请先设置 SILICONFLOW_API_KEY 环境变量或在 config.yaml 中配置 API Key"

    # 截断上下文，防止超长
    max_context = 3000
    truncated = (
        context_text[:max_context] if len(context_text) > max_context else context_text
    )

    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": QUESTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"主播刚才讲了这些内容，你来问一个问题吧：\n\n{truncated}",
            },
        ],
        "temperature": 0.8,
        "max_tokens": 200,
        "stream": False,
        "enable_thinking": False,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.LLM_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            question = data["choices"][0]["message"]["content"].strip()
            print(f"[LLM] 生成问题: {question}")
            return question
    except Exception as e:
        print(f"[LLM 错误] {e}")
        return None
