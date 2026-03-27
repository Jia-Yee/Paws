# -*- coding: utf-8 -*-
"""ESP32 Channel for CoPaw.

This module provides WebSocket-based and MQTT-based communication with ESP32 devices
using the Xiaozhi protocol for voice and text interaction.
"""
from .channel import ESP32Channel
from .mqtt_channel import ESP32MQTTChannel
from .protocol import (
    XiaozhiProtocol,
    AudioMessage,
    TextMessage,
    HelloMessage,
    StateMessage,
    ConfigMessage,
    ErrorMessage,
    ListenMessage,
    TTSMessage,
    STTMessage,
    LLMMessage,
)
from .constants import (
    MessageType,
    AudioFormat,
    DeviceState,
    ListenState,
    TTSState,
)

__all__ = [
    "ESP32Channel",
    "ESP32MQTTChannel",
    "XiaozhiProtocol",
    "AudioMessage",
    "TextMessage",
    "HelloMessage",
    "StateMessage",
    "ConfigMessage",
    "ErrorMessage",
    "ListenMessage",
    "TTSMessage",
    "STTMessage",
    "LLMMessage",
    "MessageType",
    "AudioFormat",
    "DeviceState",
    "ListenState",
    "TTSState",
]
