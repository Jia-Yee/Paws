# -*- coding: utf-8 -*-
"""Opus audio codec implementation for ESP32 communication."""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class OpusCodecError(Exception):
    """Opus codec error."""
    pass


class OpusEncoder:
    """Opus encoder for converting PCM to Opus format."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_duration_ms: int = 60,
        application: str = "audio",
    ):
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_duration_ms = frame_duration_ms
        self._application = application
        self._encoder = None
        self._frame_size = int(sample_rate * frame_duration_ms / 1000)
        
    def initialize(self) -> None:
        """Initialize the Opus encoder."""
        try:
            import opuslib
            
            app_mode = {
                "audio": opuslib.APPLICATION_AUDIO,
                "voip": opuslib.APPLICATION_VOIP,
                "low_delay": opuslib.APPLICATION_RESTRICTED_LOWDELAY,
            }.get(self._application, opuslib.APPLICATION_AUDIO)
            
            self._encoder = opuslib.Encoder(
                self._sample_rate,
                self._channels,
                app_mode,
            )
            logger.info(
                f"Opus encoder initialized: {self._sample_rate}Hz, "
                f"{self._channels}ch, {self._frame_duration_ms}ms frame"
            )
        except ImportError:
            raise OpusCodecError("opuslib not installed. Run: pip install opuslib")
        except Exception as e:
            raise OpusCodecError(f"Failed to initialize Opus encoder: {e}")
    
    def encode(self, pcm_data: bytes) -> bytes:
        """Encode PCM audio data to Opus.
        
        Args:
            pcm_data: PCM audio data (16-bit signed integers, little-endian)
            
        Returns:
            Opus encoded data
        """
        if self._encoder is None:
            self.initialize()
        
        # Convert bytes to numpy array
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        
        # Ensure we have the correct frame size
        if len(audio_array) < self._frame_size:
            # Pad with zeros
            audio_array = np.pad(
                audio_array, 
                (0, self._frame_size - len(audio_array))
            )
        elif len(audio_array) > self._frame_size:
            # Take only the first frame
            audio_array = audio_array[:self._frame_size]
        
        # Encode
        try:
            opus_data = self._encoder.encode(
                audio_array.tobytes(),
                self._frame_size,
            )
            return opus_data
        except Exception as e:
            logger.error(f"Opus encoding error: {e}")
            raise OpusCodecError(f"Encoding failed: {e}")
    
    def encode_stream(self, pcm_data: bytes) -> list[bytes]:
        """Encode PCM audio data to multiple Opus frames.
        
        Args:
            pcm_data: PCM audio data (can be longer than one frame)
            
        Returns:
            List of Opus encoded frames
        """
        if self._encoder is None:
            self.initialize()
        
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        frames = []
        
        # Process in chunks of frame_size
        for i in range(0, len(audio_array), self._frame_size):
            chunk = audio_array[i:i + self._frame_size]
            if len(chunk) < self._frame_size:
                chunk = np.pad(chunk, (0, self._frame_size - len(chunk)))
            frames.append(self.encode(chunk.tobytes()))
        
        return frames


class OpusDecoder:
    """Opus decoder for converting Opus to PCM format."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_duration_ms: int = 60,
    ):
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_duration_ms = frame_duration_ms
        self._decoder = None
        self._frame_size = int(sample_rate * frame_duration_ms / 1000)
    
    def initialize(self) -> None:
        """Initialize the Opus decoder."""
        try:
            import opuslib
            
            self._decoder = opuslib.Decoder(
                self._sample_rate,
                self._channels,
            )
            logger.info(
                f"Opus decoder initialized: {self._sample_rate}Hz, "
                f"{self._channels}ch, {self._frame_duration_ms}ms frame"
            )
        except ImportError:
            raise OpusCodecError("opuslib not installed. Run: pip install opuslib")
        except Exception as e:
            raise OpusCodecError(f"Failed to initialize Opus decoder: {e}")
    
    def decode(self, opus_data: bytes) -> bytes:
        """Decode Opus audio data to PCM.
        
        Args:
            opus_data: Opus encoded audio data
            
        Returns:
            PCM audio data (16-bit signed integers, little-endian)
        """
        if self._decoder is None:
            self.initialize()
        
        try:
            pcm_data = self._decoder.decode(opus_data, self._frame_size)
            return pcm_data
        except Exception as e:
            logger.error(f"Opus decoding error: {e}")
            raise OpusCodecError(f"Decoding failed: {e}")
    
    def decode_stream(self, opus_frames: list[bytes]) -> bytes:
        """Decode multiple Opus frames to PCM.
        
        Args:
            opus_frames: List of Opus encoded frames
            
        Returns:
            Combined PCM audio data
        """
        pcm_data = b""
        for frame in opus_frames:
            pcm_data += self.decode(frame)
        return pcm_data
