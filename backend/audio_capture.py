"""音频采集 + VAD 语音活动检测模块"""

import asyncio
import numpy as np
import sounddevice as sd
import webrtcvad
from collections import deque
from backend.config import config
from backend.stt import transcribe_audio
from pathlib import Path
import yaml


def _reload_thresholds():
    """从 config.yaml 重新读取触发阈值（前端可能已修改）"""
    try:
        config_path = Path(__file__).parent.parent / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        config.MIN_WORDS_FOR_QUESTION = cfg.get(
            "MIN_WORDS_FOR_QUESTION", config.MIN_WORDS_FOR_QUESTION
        )
        config.MAX_WORDS_FORCE_TRIGGER = cfg.get(
            "MAX_WORDS_FORCE_TRIGGER", config.MAX_WORDS_FORCE_TRIGGER
        )
        config.TRIGGER_PHRASES = cfg.get("TRIGGER_PHRASES", config.TRIGGER_PHRASES)
    except Exception:
        pass


class AudioCapture:
    def __init__(self):
        self.vad = webrtcvad.Vad(2)  # 灵敏度 0-3, 2 较平衡
        self.sample_rate = config.SAMPLE_RATE
        self.channels = config.CHANNELS
        self.chunk_duration_ms = config.CHUNK_DURATION_MS
        self.chunk_size = config.CHUNK_SIZE  # 16000 * 0.03 = 480 samples
        self.silence_timeout_chunks = config.SILENCE_CHUNKS  # ~50 chunks = 1.5s

        # 状态
        self.is_running = False
        self.is_speaking = False
        self.silence_chunk_count = 0
        self.current_speech_buffer = bytearray()
        self.total_text_accumulated = ""  # 累积的总文字
        self.total_char_count = 0

        # VAD 需要 10ms/20ms/30ms 帧，我们用 30ms
        self.vad_frame_size = int(self.sample_rate * 0.03)  # 480 samples
        assert self.vad_frame_size % 2 == 0, "VAD frame must be even"

        # 回调接口
        self.on_utterance_complete = None  # 一句话说完回调: func(audio_bytes)
        self.on_question_ready = None  # 问题生成回调: func(question)

        # 截断提示词列表
        self.trigger_phrases = config.TRIGGER_PHRASES

    def _vad_has_speech(self, audio_chunk: bytes) -> bool:
        """检测音频块是否包含语音"""
        # webrtcvad 需要 10/20/30ms 帧，16-bit PCM
        if len(audio_chunk) < self.vad_frame_size * 2:
            return False
        # 给 VAD 一个完整帧
        frame = audio_chunk[: self.vad_frame_size * 2]
        try:
            return self.vad.is_speech(frame, self.sample_rate)
        except Exception:
            return False

    async def _process_audio_stream(self):
        """音频处理主循环"""
        audio_buffer = bytearray()
        frames_per_chunk = max(1, self.chunk_size // self.vad_frame_size)

        def audio_callback(indata, frames, time_info, status):
            """sounddevice 回调 - 将音频数据放入缓冲区"""
            nonlocal audio_buffer
            if status:
                print(f"[音频] 状态: {status}")
            audio_buffer.extend(indata.tobytes())

        self.is_running = True

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self.chunk_size,
            callback=audio_callback,
        ):
            print("[音频] 麦克风已启动，等待说话...")
            while self.is_running:
                await asyncio.sleep(0.01)  # 10ms 轮询

                if len(audio_buffer) < self.vad_frame_size * 2:
                    continue

                # 取一帧给 VAD
                frame = audio_buffer[: self.vad_frame_size * 2]
                audio_buffer = audio_buffer[self.vad_frame_size * 2 :]

                has_speech = self._vad_has_speech(bytes(frame))

                if has_speech:
                    self.current_speech_buffer.extend(frame)
                    self.silence_chunk_count = 0
                    if not self.is_speaking:
                        self.is_speaking = True
                        print("[音频] 检测到说话开始")
                else:
                    if self.is_speaking:
                        self.silence_chunk_count += 1
                        # 仍在静默期内，继续缓存
                        self.current_speech_buffer.extend(frame)

                        if self.silence_chunk_count >= self.silence_timeout_chunks:
                            # 一句话说完了
                            await self._on_speech_end()
                    # 不在说话状态，丢弃

                # 最大录音保护
                max_frames = int(
                    self.sample_rate
                    * config.MAX_RECORDING_MINUTES
                    * 60
                    / self.vad_frame_size
                )
                if (
                    len(self.current_speech_buffer)
                    > max_frames * self.vad_frame_size * 2
                ):
                    print(
                        f"[音频] 达到最长录音时间 {config.MAX_RECORDING_MINUTES} 分钟，强制截断"
                    )
                    if self.is_speaking:
                        await self._on_speech_end()

    async def _on_speech_end(self):
        """一句话结束：转文字 + 累积"""
        speech_bytes = bytes(self.current_speech_buffer)
        self.current_speech_buffer = bytearray()
        self.is_speaking = False
        self.silence_chunk_count = 0

        if len(speech_bytes) < 1600:  # 太短（<0.1s）忽略
            return

        print(f"[音频] 说话结束，音频大小: {len(speech_bytes)} bytes")

        # 异步转文字
        text = await transcribe_audio(speech_bytes, self.sample_rate)
        if text and len(text.strip()) > 5:
            self.total_text_accumulated += text
            self.total_char_count = len(self.total_text_accumulated)
            print(f"[累积] 当前总字数: {self.total_char_count}")
            print(f"[文字] {text[:100]}...")

            # 检测是否包含截断提示词（优先级最高）
            if self._has_trigger_phrase(text):
                print(f"[截断] 检测到截断提示词，立即触发提问！")
                await self._trigger_question()
                return

            # 重新读取阈值（前端可能已修改config.yaml）
            _reload_thresholds()

            # 检查是否达到提问阈值
            if self.total_char_count >= config.MAX_WORDS_FORCE_TRIGGER:
                await self._trigger_question()
            elif self.total_char_count >= config.MIN_WORDS_FOR_QUESTION:
                await self._trigger_question()

    def _has_trigger_phrase(self, text: str) -> bool:
        """检测文本中是否包含截断提示词"""
        for phrase in config.TRIGGER_PHRASES:
            if phrase in text:
                print(f"[截断] 命中提示词: '{phrase}'")
                return True
        return False

    async def _trigger_question(self):
        """触发 LLM 生成问题"""
        question_text = self.total_text_accumulated
        self.total_text_accumulated = ""
        self.total_char_count = 0

        print(f"\n========== 触发提问 ==========")
        print(f"累积文字: {len(question_text)} 字")
        print(f"==============================\n")

        if self.on_question_ready:
            await self.on_question_ready(question_text)

    async def start(self):
        """启动采集"""
        await self._process_audio_stream()

    def stop(self):
        """停止采集"""
        self.is_running = False
