# -*- coding: utf-8 -*-
"""Voice processor that integrates VAD, ASR, and TTS."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .vad import VADBase, SileroVAD, VADResult
from .asr import ASRBase, FunASR, ASRResult
from .tts import TTSBase, EdgeTTS

logger = logging.getLogger(__name__)


@dataclass
class VoiceProcessorConfig:
    vad_type: str = "silero"
    vad_threshold: float = 0.5
    asr_type: str = "funasr"
    asr_model: str = "iic/speech_paraformer_zh-cn_16k_common-vocab8404_pytorch"
    tts_type: str = "edge"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    sample_rate: int = 16000
    min_speech_duration_ms: int = 500
    min_silence_duration_ms: int = 300
    speech_timeout_ms: int = 2000


@dataclass
class SpeechSegment:
    audio_data: bytes
    start_time: float = 0.0
    end_time: float = 0.0
    vad_results: List[VADResult] = field(default_factory=list)


class VoiceProcessor:
    def __init__(
        self,
        config: Optional[VoiceProcessorConfig] = None,
        on_speech_detected: Optional[Callable[[bytes], None]] = None,
        on_text_recognized: Optional[Callable[[str, bool], None]] = None,
    ):
        self._config = config or VoiceProcessorConfig()
        self._on_speech_detected = on_speech_detected
        self._on_text_recognized = on_text_recognized

        self._vad: Optional[VADBase] = None
        self._asr: Optional[ASRBase] = None
        self._tts: Optional[TTSBase] = None

        self._audio_buffer: List[bytes] = []
        self._speech_buffer: List[bytes] = []
        self._is_speaking: bool = False
        self._speech_start_time: float = 0.0
        self._silence_start_time: float = 0.0
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._vad = self._create_vad()
        self._asr = self._create_asr()
        self._tts = self._create_tts()
        logger.info(
            f"VoiceProcessor initialized: VAD={self._config.vad_type}, "
            f"ASR={self._config.asr_type}, TTS={self._config.tts_type}"
        )

    def _create_vad(self) -> VADBase:
        if self._config.vad_type == "silero":
            return SileroVAD(
                sample_rate=self._config.sample_rate,
                threshold=self._config.vad_threshold,
                min_speech_duration_ms=self._config.min_speech_duration_ms,
                min_silence_duration_ms=self._config.min_silence_duration_ms,
            )
        raise ValueError(f"Unknown VAD type: {self._config.vad_type}")

    def _create_asr(self) -> ASRBase:
        if self._config.asr_type == "funasr":
            return FunASR(
                model=self._config.asr_model,
                sample_rate=self._config.sample_rate,
            )
        raise ValueError(f"Unknown ASR type: {self._config.asr_type}")

    def _create_tts(self) -> TTSBase:
        if self._config.tts_type == "edge":
            return EdgeTTS(voice=self._config.tts_voice)
        raise ValueError(f"Unknown TTS type: {self._config.tts_type}")

    def reset(self) -> None:
        self._audio_buffer.clear()
        self._speech_buffer.clear()
        self._is_speaking = False
        if self._vad:
            self._vad.reset()
        if self._asr:
            self._asr.reset()

    async def process_audio(
        self,
        audio_data: bytes,
        sample_rate: Optional[int] = None,
    ) -> Optional[str]:
        if self._asr is None:
            await self.initialize()

        sample_rate = sample_rate or self._config.sample_rate
        
        # 直接处理整个音频数据，不使用 VAD
        # 因为我们已经在 ESP32 端处理了 VAD
        text = await self._process_speech_segment(audio_data)
        return text
    
    async def _process_speech_segment(self, audio_data: Optional[bytes] = None) -> Optional[str]:
        if audio_data is None:
            if not self._speech_buffer:
                return None
            audio_data = b"" .join(self._speech_buffer)
        
        if self._on_speech_detected:
            self._on_speech_detected(audio_data)

        if self._asr is None:
            return None

        result = await self._asr.transcribe(audio_data, self._config.sample_rate)
        if result.text and self._on_text_recognized:
            self._on_text_recognized(result.text, result.is_final)

        logger.info(f"ASR result: {result.text}")
        return result.text

    async def synthesize_speech(
        self,
        text: str,
    ) -> bytes:
        if self._tts is None:
            await self.initialize()
        return await self._tts.synthesize(text)

    async def synthesize_speech_stream(
        self,
        text: str,
    ) -> AsyncIterator[bytes]:
        if self._tts is None:
            await self.initialize()
        async for chunk in self._tts.synthesize_stream(text):
            yield chunk

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def sample_rate(self) -> int:
        return self._config.sample_rate

    @property
    def tts_sample_rate(self) -> int:
        if self._tts is None:
            return 24000
        return self._tts.sample_rate