# -*- coding: utf-8 -*-
"""ASR (Automatic Speech Recognition) module."""
from .base import ASRBase, ASRResult
from .funasr import FunASR

__all__ = ["ASRBase", "ASRResult", "FunASR"]
