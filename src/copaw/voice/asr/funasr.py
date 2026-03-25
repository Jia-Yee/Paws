# -*- coding: utf-8 -*-
"""FunASR implementation for ASR."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, List, Optional

import numpy as np

from .base import ASRBase, ASRResult

logger = logging.getLogger(__name__)


class FunASR(ASRBase):
    def __init__(
        self,
        model: str = "iic/speech_paraformer_zh-cn_16k_common-vocab8404_pytorch",
        sample_rate: int = 16000,
        device: str = "cpu",
        offline: bool = True,
    ):
        self._model_name = model
        self._sample_rate = sample_rate
        self._device = device
        self._offline = offline
        self._model = None
        self._audio_buffer: List[bytes] = []

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from funasr import AutoModel

            self._model = AutoModel(
                model=self._model_name,
                device=self._device,
                disable_update=True  # 禁用更新检查，加快加载速度
            )
            logger.info(f"FunASR model '{self._model_name}' loaded successfully")
        except ImportError:
            logger.warning("FunASR not installed, using mock implementation")
            self._model = "mock"
        except Exception:
            logger.exception("Failed to load FunASR model")
            raise

    def reset(self) -> None:
        self._audio_buffer.clear()

    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int = 16000,
    ) -> ASRResult:
        if self._model is None:
            self._load_model()

        if self._model == "mock":
            return ASRResult(text="[FunASR not installed]", is_final=True)

        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_float = audio_array.astype(np.float32) / 32768.0

        def _run_inference():
            result = self._model.generate(
                input=audio_float,
                batch_size=1,
            )
            if result and len(result) > 0:
                text = result[0].get("text", "")
                return text
            return ""

        text = await asyncio.to_thread(_run_inference)
        return ASRResult(text=text, is_final=True)

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[ASRResult]:
        self.reset()
        async for audio_chunk in audio_stream:
            self._audio_buffer.append(audio_chunk)
            if len(self._audio_buffer) >= 3:
                combined = b"".join(self._audio_buffer[-3:])
                result = await self.transcribe(combined, sample_rate)
                if result.text:
                    result.is_final = False
                    yield result

        if self._audio_buffer:
            final_audio = b"".join(self._audio_buffer)
            result = await self.transcribe(final_audio, sample_rate)
            result.is_final = True
            yield result

        self.reset()
