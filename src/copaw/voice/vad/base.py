# -*- coding: utf-8 -*-
"""Base class for VAD providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class VADResult:
    is_speech: bool
    probability: float = 0.0
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


class VADBase(ABC):
    @abstractmethod
    def is_speech(self, audio_data: bytes, sample_rate: int = 16000) -> VADResult:
        pass

    @abstractmethod
    def reset(self) -> None:
        pass

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        pass
