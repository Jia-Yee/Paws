# -*- coding: utf-8 -*-
"""Constants for ESP32 channel."""
from enum import Enum


class MessageType(str, Enum):
    AUDIO = "audio"
    TEXT = "text"
    START_LISTEN = "start_listen"
    STOP_LISTEN = "stop_listen"
    LISTEN = "listen"
    ABORT = "abort"
    HELLO = "hello"
    PING = "ping"
    PONG = "pong"
    CONFIG = "config"
    STATE = "state"
    ERROR = "error"
    STT = "stt"
    TTS = "tts"
    LLM = "llm"
    MCP = "mcp"
    SYSTEM = "system"
    ALERT = "alert"
    CUSTOM = "custom"


class AudioFormat(str, Enum):
    PCM = "pcm"
    OPUS = "opus"
    WAV = "wav"


class DeviceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"
    PROCESSING = "processing"


class ListenState(str, Enum):
    START = "start"
    STOP = "stop"
    DETECT = "detect"


class TTSState(str, Enum):
    START = "start"
    STOP = "stop"
    SENTENCE_START = "sentence_start"


DEFAULT_AUDIO_SAMPLE_RATE = 16000
DEFAULT_AUDIO_CHANNELS = 1
DEFAULT_AUDIO_BITS = 16
DEFAULT_AUDIO_FRAME_SIZE = 960

DEFAULT_TTS_SAMPLE_RATE = 24000

OPUS_FRAME_DURATION_MS = 60
