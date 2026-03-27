# -*- coding: utf-8 -*-
"""Base class for ASR providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional


@dataclass
class ASRResult:
    text: str
    is_final: bool = True
    confidence: float = 1.0
    words: Optional[List[dict]] = None


class ASRBase(ABC):
    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int = 16000,
    ) -> ASRResult:
        pass

    @abstractmethod
    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[ASRResult]:
        pass

    @abstractmethod
    def reset(self) -> None:
        pass

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        pass
