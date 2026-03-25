# -*- coding: utf-8 -*-
"""VAD (Voice Activity Detection) module."""
from .base import VADBase, VADResult
from .silero import SileroVAD
from .energy import EnergyVAD

__all__ = ["VADBase", "VADResult", "SileroVAD", "EnergyVAD"]


def create_vad(
    vad_type: str = "auto",
    sample_rate: int = 16000,
    threshold: float = 0.5,
    **kwargs,
) -> VADBase:
    """创建 VAD 实例。
    
    Args:
        vad_type: VAD 类型 ("silero", "energy", "auto")
        sample_rate: 采样率
        threshold: 语音检测阈值
        **kwargs: 其他参数
        
    Returns:
        VADBase: VAD 实例
    """
    if vad_type == "energy":
        return EnergyVAD(
            sample_rate=sample_rate,
            threshold=threshold,
            min_speech_duration_ms=kwargs.get("min_speech_duration_ms", 250),
            min_silence_duration_ms=kwargs.get("min_silence_duration_ms", 100),
        )
    
    if vad_type == "silero" or vad_type == "auto":
        try:
            vad = SileroVAD(
                sample_rate=sample_rate,
                threshold=threshold,
                min_speech_duration_ms=kwargs.get("min_speech_duration_ms", 250),
                min_silence_duration_ms=kwargs.get("min_silence_duration_ms", 100),
                model_path=kwargs.get("model_path"),
            )
            # 尝试加载模型
            vad._load_model()
            return vad
        except Exception as e:
            if vad_type == "silero":
                raise
            # 自动回退到 EnergyVAD
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to load SileroVAD: {e}, falling back to EnergyVAD"
            )
            return create_vad("energy", sample_rate, threshold, **kwargs)
    
    raise ValueError(f"Unknown VAD type: {vad_type}")
