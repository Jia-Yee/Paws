# -*- coding: utf-8 -*-
"""Silero VAD implementation."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.request import urlretrieve
import hashlib

import numpy as np

from .base import VADBase, VADResult

logger = logging.getLogger(__name__)

# Silero VAD 模型下载地址
SILERO_VAD_URLS = [
    # GitHub 官方
    "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.jit",
    # 国内镜像
    "https://ghproxy.com/https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.jit",
    "https://mirror.ghproxy.com/https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.jit",
]

SILERO_VAD_MD5 = "6c6e8a2b2e8e8c8e8e8e8e8e8e8e8e8e8"  # 简化校验


class SileroVAD(VADBase):
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 100,
        model_path: Optional[str] = None,
    ):
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._min_speech_duration_ms = min_speech_duration_ms
        self._min_silence_duration_ms = min_silence_duration_ms
        self._model_path = model_path
        self._model = None
        self._reset_states()

    def _reset_states(self) -> None:
        self._h = None
        self._c = None
        self._speech_start: Optional[int] = None
        self._speech_frames: int = 0
        self._silence_frames: int = 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _get_model_path(self) -> Path:
        """获取模型路径，如果不存在则下载。"""
        if self._model_path:
            return Path(self._model_path)
        
        # 默认模型路径
        model_dir = Path.home() / ".cache" / "copaw" / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        return model_dir / "silero_vad.jit"

    def _download_model(self, model_path: Path) -> bool:
        """下载模型文件。"""
        for url in SILERO_VAD_URLS:
            try:
                logger.info(f"Downloading Silero VAD model from: {url}")
                urlretrieve(url, model_path)
                logger.info(f"Model downloaded to: {model_path}")
                return True
            except Exception as e:
                logger.warning(f"Failed to download from {url}: {e}")
                continue
        
        logger.error("Failed to download Silero VAD model from all mirrors")
        return False

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            
            model_path = self._get_model_path()
            
            # 如果模型不存在，尝试下载
            if not model_path.exists():
                if not self._download_model(model_path):
                    raise RuntimeError("Failed to download Silero VAD model")
            
            # 加载模型
            logger.info(f"Loading Silero VAD model from: {model_path}")
            self._model = torch.jit.load(str(model_path))
            self._model.eval()
            self._h = None
            self._c = None
            logger.info("Silero VAD model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load Silero VAD model: {e}")
            logger.error(
                "\n请手动下载模型:\n"
                "  方法1 - 使用国内镜像:\n"
                "    curl -L -o ~/.cache/copaw/models/silero_vad.jit \\\n"
                "      https://ghproxy.com/https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.jit\n"
                "\n"
                "  方法2 - 使用 wget:\n"
                "    wget -O ~/.cache/copaw/models/silero_vad.jit \\\n"
                "      https://ghproxy.com/https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.jit\n"
            )
            raise

    def reset(self) -> None:
        self._reset_states()

    def is_speech(self, audio_data: bytes, sample_rate: int = 16000) -> VADResult:
        if self._model is None:
            self._load_model()

        import torch

        # 确保 audio_data 长度是 2 的倍数（np.int16 每个元素占 2 字节）
        if len(audio_data) % 2 != 0:
            # 截断到最接近的 2 的倍数
            audio_data = audio_data[:len(audio_data) // 2 * 2]
        
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_float = audio_array.astype(np.float32) / 32768.0
        
        # Silero VAD 需要固定长度的音频块
        # 16000 Hz: 512 样本, 8000 Hz: 256 样本
        chunk_size = 512 if sample_rate == 16000 else 256
        
        # 如果音频太短，填充到 chunk_size
        if len(audio_float) < chunk_size:
            audio_float = np.pad(audio_float, (0, chunk_size - len(audio_float)))
        # 如果音频太长，只取前 chunk_size 样本
        elif len(audio_float) > chunk_size:
            audio_float = audio_float[:chunk_size]
        
        audio_tensor = torch.from_numpy(audio_float)

        with torch.no_grad():
            # 新版 Silero VAD 模型只需要 (audio, sample_rate)
            speech_prob = self._model(audio_tensor.unsqueeze(0), sample_rate)

        prob = speech_prob.item()
        is_speech = prob >= self._threshold

        return VADResult(
            is_speech=is_speech,
            probability=prob,
        )
