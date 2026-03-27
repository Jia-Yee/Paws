# -*- coding: utf-8 -*-
"""Utilities for ESP32 channel."""
import hashlib
import struct
from typing import Optional

from .constants import AudioFormat


def pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
) -> bytes:
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm_data)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm_data


def wav_to_pcm(wav_data: bytes) -> bytes:
    if len(wav_data) < 44:
        return wav_data
    if wav_data[:4] != b"RIFF":
        return wav_data
    return wav_data[44:]


def resample_audio(
    audio_data: bytes,
    from_rate: int,
    to_rate: int,
    channels: int = 1,
) -> bytes:
    if from_rate == to_rate:
        return audio_data

    try:
        import numpy as np
        from scipy import signal

        samples = np.frombuffer(audio_data, dtype=np.int16)
        num_samples = int(len(samples) * to_rate / from_rate)
        resampled = signal.resample(samples, num_samples)
        return resampled.astype(np.int16).tobytes()
    except ImportError:
        return audio_data


def detect_audio_format(data: bytes) -> AudioFormat:
    if len(data) < 4:
        return AudioFormat.PCM
    if data[:4] == b"RIFF":
        return AudioFormat.WAV
    if data[:4] in (b"\x00\x00\x00\x00", b"\xff\xf3"):
        return AudioFormat.OPUS
    return AudioFormat.PCM


def generate_device_id(device_info: Optional[dict] = None) -> str:
    import time
    import uuid

    base = f"{time.time()}-{uuid.uuid4()}"
    if device_info:
        for key in ["mac", "chip_id", "serial"]:
            if key in device_info:
                base += f"-{device_info[key]}"
    return hashlib.md5(base.encode()).hexdigest()[:16]


def calculate_audio_duration_ms(
    audio_size: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
) -> int:
    bytes_per_sample = channels * bits // 8
    samples = len(audio_size) // bytes_per_sample
    duration_ms = int(samples * 1000 / sample_rate)
    return duration_ms
