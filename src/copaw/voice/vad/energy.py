# -*- coding: utf-8 -*-
"""Simple VAD implementation using energy-based detection."""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from .base import VADBase, VADResult

logger = logging.getLogger(__name__)


class EnergyVAD(VADBase):
    """Simple energy-based VAD that doesn't require model download."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.01,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 100,
    ):
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._min_speech_duration_ms = min_speech_duration_ms
        self._min_silence_duration_ms = min_silence_duration_ms
        self._energy_history: list = []
        self._is_speaking: bool = False
        self._speech_frames: int = 0
        self._silence_frames: int = 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def reset(self) -> None:
        self._energy_history.clear()
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0

    def _calculate_energy(self, audio_data: bytes) -> float:
        """Calculate RMS energy of audio data."""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        if len(audio_array) == 0:
            return 0.0
        # Normalize to [-1, 1] range
        audio_float = audio_array.astype(np.float32) / 32768.0
        # Calculate RMS energy
        energy = np.sqrt(np.mean(audio_float ** 2))
        return float(energy)

    def is_speech(self, audio_data: bytes, sample_rate: int = 16000) -> VADResult:
        """Detect if audio contains speech based on energy."""
        energy = self._calculate_energy(audio_data)
        
        # Update energy history for adaptive threshold
        self._energy_history.append(energy)
        if len(self._energy_history) > 100:
            self._energy_history.pop(0)
        
        # Calculate adaptive threshold
        if len(self._energy_history) >= 10:
            avg_energy = np.mean(self._energy_history)
            threshold = max(self._threshold, avg_energy * 1.5)
        else:
            threshold = self._threshold
        
        is_speech = energy >= threshold
        
        # Update state
        if is_speech:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
        
        return VADResult(
            is_speech=is_speech,
            probability=min(1.0, energy / threshold) if threshold > 0 else 0.0,
        )


# 工厂函数 - 根据可用性选择 VAD 实现
def create_vad(
    vad_type: str = "auto",
    sample_rate: int = 16000,
    threshold: float = 0.5,
    **kwargs,
) -> VADBase:
    """Create VAD instance based on type and availability.
    
    Args:
        vad_type: "silero", "energy", or "auto" (try silero first, fallback to energy)
        sample_rate: Audio sample rate
        threshold: Detection threshold
        **kwargs: Additional arguments for specific VAD implementation
    
    Returns:
        VADBase instance
    """
    if vad_type == "energy":
        logger.info("Using EnergyVAD")
        return EnergyVAD(
            sample_rate=sample_rate,
            threshold=0.01,  # Energy threshold is different from probability
            min_speech_duration_ms=kwargs.get("min_speech_duration_ms", 250),
            min_silence_duration_ms=kwargs.get("min_silence_duration_ms", 100),
        )
    
    if vad_type == "silero" or vad_type == "auto":
        try:
            from .silero import SileroVAD
            vad = SileroVAD(
                sample_rate=sample_rate,
                threshold=threshold,
                min_speech_duration_ms=kwargs.get("min_speech_duration_ms", 250),
                min_silence_duration_ms=kwargs.get("min_silence_duration_ms", 100),
                model_path=kwargs.get("model_path"),
            )
            logger.info("Using SileroVAD")
            return vad
        except Exception as e:
            if vad_type == "silero":
                raise
            logger.warning(f"Failed to load SileroVAD: {e}, falling back to EnergyVAD")
            return create_vad("energy", sample_rate, threshold, **kwargs)
    
    raise ValueError(f"Unknown VAD type: {vad_type}")
