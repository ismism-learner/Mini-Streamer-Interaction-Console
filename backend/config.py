"""配置加载模块"""

import os
import yaml
from pathlib import Path


def _load_env(env_path: Path):
    """手动加载 .env 文件（不用 python-dotenv 减少依赖）"""
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = value


class Config:
    def __init__(self):
        # 加载 .env 文件
        env_path = Path(__file__).parent.parent / ".env"
        _load_env(env_path)

        config_path = Path(__file__).parent.parent / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # 音频参数
        self.SAMPLE_RATE = cfg.get("SAMPLE_RATE", 16000)
        self.CHANNELS = cfg.get("CHANNELS", 1)
        self.CHUNK_DURATION_MS = cfg.get("CHUNK_DURATION_MS", 30)
        self.SILENCE_TIMEOUT_MS = cfg.get("SILENCE_TIMEOUT_MS", 1500)
        self.MAX_RECORDING_MINUTES = cfg.get("MAX_RECORDING_MINUTES", 10)

        # 触发条件
        self.MIN_WORDS_FOR_QUESTION = cfg.get("MIN_WORDS_FOR_QUESTION", 2000)
        self.MAX_WORDS_FORCE_TRIGGER = cfg.get("MAX_WORDS_FORCE_TRIGGER", 3000)

        # 截断提示词
        self.TRIGGER_PHRASES = cfg.get("TRIGGER_PHRASES", ["你明白了吗", "听懂了吗"])

        # 显示配置
        self.DISPLAY_FONT_FAMILY = cfg.get(
            "DISPLAY_FONT_FAMILY", "Microsoft YaHei UI, PingFang SC, sans-serif"
        )
        self.DISPLAY_FONT_SIZE = cfg.get("DISPLAY_FONT_SIZE", 28)
        self.DISABLE_EMOJI = cfg.get("DISABLE_EMOJI", False)
        self.DISPLAY_X = cfg.get("DISPLAY_X", -1)
        self.DISPLAY_Y = cfg.get("DISPLAY_Y", -1)
        self.DISPLAY_WIDTH = cfg.get("DISPLAY_WIDTH", 420)
        self.DISPLAY_HEIGHT = cfg.get("DISPLAY_HEIGHT", 340)
        self.DISPLAY_DISAPPEAR_MODE = cfg.get("DISPLAY_DISAPPEAR_MODE", "timed")
        self.DISPLAY_DISAPPEAR_SECONDS = cfg.get("DISPLAY_DISAPPEAR_SECONDS", 4)
        self.DISPLAY_MAX_TEXT_LENGTH = cfg.get("DISPLAY_MAX_TEXT_LENGTH", 50)

        # STT 配置
        self.WHISPER_MODEL_SIZE = cfg.get("WHISPER_MODEL_SIZE", "small")
        self.WHISPER_DEVICE = cfg.get("WHISPER_DEVICE", "cpu")
        self.WHISPER_COMPUTE_TYPE = cfg.get("WHISPER_COMPUTE_TYPE", "int8")

        # LLM 配置
        self.LLM_API_BASE = cfg.get("LLM_API_BASE", "https://api.siliconflow.cn/v1")
        self.LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY") or cfg.get(
            "LLM_API_KEY", ""
        )
        self.LLM_MODEL = cfg.get("LLM_MODEL", "deepseek-ai/DeepSeek-V3")

        # 服务器配置
        self.SERVER_HOST = cfg.get("SERVER_HOST", "127.0.0.1")
        self.SERVER_PORT = cfg.get("SERVER_PORT", 8765)

        # 计算帧大小
        self.CHUNK_SIZE = int(self.SAMPLE_RATE * self.CHUNK_DURATION_MS / 1000)
        self.SILENCE_CHUNKS = self.SILENCE_TIMEOUT_MS // self.CHUNK_DURATION_MS


config = Config()
