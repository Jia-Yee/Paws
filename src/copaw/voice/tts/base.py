# -*- coding: utf-8 -*-
"""Base class for TTS providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class TTSBase(ABC):
    @abstractmethod
    async def synthesize(
        self,
        text: str,
    ) -> bytes:
        pass

    @abstractmethod
    async def synthesize_stream(
        self,
        text: str,
    ) -> AsyncIterator[bytes]:
        pass

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        pass

    @property
    @abstractmethod
    def sample_width(self) -> int:
        pass
