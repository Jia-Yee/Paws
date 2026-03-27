# -*- coding: utf-8 -*-
"""Edge TTS implementation."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from .base import TTSBase

logger = logging.getLogger(__name__)


class EdgeTTS(TTSBase):
    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        sample_rate: int = 24000,
        rate: str = "+0%",
        volume: str = "+0%",
    ):
        self._voice = voice
        self._sample_rate = sample_rate
        self._rate = rate
        self._volume = volume
        self._communicate = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def sample_width(self) -> int:
        return 2

    async def _ensure_communicate(self, text: str):
        if self._communicate is not None:
            return
        try:
            import edge_tts

            self._communicate = edge_tts.Communicate(
                text=text,
                voice=self._voice,
                rate=self._rate,
                volume=self._volume,
            )
        except ImportError:
            logger.warning("edge-tts not installed")
            raise

    async def synthesize(
        self,
        text: str,
    ) -> bytes:
        try:
            import edge_tts
            import io
            from pydub import AudioSegment

            communicate = edge_tts.Communicate(
                text=text,
                voice=self._voice,
                rate=self._rate,
                volume=self._volume,
            )
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            
            # 将 MP3 转换为 PCM
            if audio_data:
                mp3_io = io.BytesIO(audio_data)
                audio = AudioSegment.from_mp3(mp3_io)
                # 转换为 16-bit PCM
                pcm_data = audio.set_frame_rate(self._sample_rate).set_channels(1).raw_data
                return pcm_data
            return b""
        except ImportError:
            logger.warning("edge-tts or pydub not installed, returning empty audio")
            return b""
        except Exception:
            logger.exception("EdgeTTS synthesize failed")
            return b""

    async def synthesize_stream(
        self,
        text: str,
    ) -> AsyncIterator[bytes]:
        try:
            import edge_tts

            communicate = edge_tts.Communicate(
                text=text,
                voice=self._voice,
                rate=self._rate,
                volume=self._volume,
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except ImportError:
            logger.warning("edge-tts not installed")
            return
        except Exception:
            logger.exception("EdgeTTS synthesize_stream failed")
            return
