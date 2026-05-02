"""语音转文字模块 - 使用 faster-whisper (本地模型)"""

import io
import os
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from backend.config import config

# 模型缓存目录
_MODEL_CACHE_DIR = Path(__file__).parent.parent / "whisper_models"

# 全局模型实例
_model = None


def get_model():
    global _model
    if _model is None:
        model_size = config.WHISPER_MODEL_SIZE
        device = config.WHISPER_DEVICE
        compute_type = config.WHISPER_COMPUTE_TYPE

        print(f"[STT] 加载 Whisper 模型: {model_size} (device={device}, compute_type={compute_type})")

        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        _model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=4,
            num_workers=1,
            download_root=str(_MODEL_CACHE_DIR),
        )
        print("[STT] 模型加载完成")
    return _model


async def transcribe_audio(audio_bytes: bytes, sample_rate: int) -> str:
    """将音频字节流转写为文字"""
    try:
        model = get_model()

        # bytes → numpy array (int16 → float32)
        audio_array = (
            np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )

        # faster-whisper 转写
        segments, info = model.transcribe(
            audio_array,
            language="zh",
            beam_size=5,
            vad_filter=True,  # 自动过滤静音
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        full_text = "".join(text_parts)
        return full_text

    except Exception as e:
        print(f"[STT 错误] {e}")
        return ""
