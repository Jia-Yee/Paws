# -*- coding: utf-8 -*-
"""Xiaozhi protocol parser for ESP32 devices."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .constants import (
    AudioFormat,
    DeviceState,
    ListenState,
    MessageType,
    TTSState,
)

logger = logging.getLogger(__name__)


@dataclass
class AudioMessage:
    type: MessageType = MessageType.AUDIO
    format: AudioFormat = AudioFormat.PCM
    sample_rate: int = 16000
    channels: int = 1
    data: bytes = b""
    sequence: int = 0
    timestamp: int = 0


@dataclass
class TextMessage:
    type: MessageType = MessageType.TEXT
    text: str = ""
    sequence: int = 0


@dataclass
class HelloMessage:
    type: MessageType = MessageType.HELLO
    device_id: str = ""
    client_id: str = ""
    version: Union[str, int] = ""
    capabilities: List[str] = field(default_factory=list)
    audio_config: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Any] = field(default_factory=dict)
    transport: str = "websocket"


@dataclass
class StateMessage:
    type: MessageType = MessageType.STATE
    state: DeviceState = DeviceState.IDLE
    message: str = ""


@dataclass
class ConfigMessage:
    type: MessageType = MessageType.CONFIG
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorMessage:
    type: MessageType = MessageType.ERROR
    code: int = 0
    message: str = ""


@dataclass
class ListenMessage:
    type: MessageType = MessageType.LISTEN
    state: ListenState = ListenState.START
    mode: str = "auto"
    text: str = ""


@dataclass
class TTSMessage:
    type: MessageType = MessageType.TTS
    state: TTSState = TTSState.START
    text: str = ""


@dataclass
class STTMessage:
    type: MessageType = MessageType.STT
    text: str = ""
    is_final: bool = True


@dataclass
class LLMMessage:
    type: MessageType = MessageType.LLM
    emotion: str = ""
    text: str = ""


XiaozhiMessage = Union[
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
]


class XiaozhiProtocol:
    def __init__(self):
        self._sequence: int = 0

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % 65536
        return self._sequence

    def parse(self, data: Union[str, bytes]) -> Optional[XiaozhiMessage]:
        if isinstance(data, bytes):
            if len(data) < 4:
                return AudioMessage(data=data)
            try:
                text = data.decode("utf-8")
                if text.startswith("{"):
                    return self._parse_json(text)
            except UnicodeDecodeError:
                pass
            return AudioMessage(data=data)

        if isinstance(data, str):
            if data.startswith("{"):
                return self._parse_json(data)
            return TextMessage(text=data)

        return None

    def _parse_json(self, text: str) -> Optional[XiaozhiMessage]:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON: {text[:100]}")
            return None

        msg_type = obj.get("type", "")

        if msg_type == MessageType.AUDIO.value or "audio" in obj:
            return self._parse_audio(obj)
        elif msg_type == MessageType.HELLO.value:
            return self._parse_hello(obj)
        elif msg_type == MessageType.TEXT.value or ("text" in obj and msg_type == ""):
            return TextMessage(
                type=MessageType.TEXT,
                text=obj.get("text", ""),
                sequence=obj.get("sequence", 0),
            )
        elif msg_type == MessageType.STATE.value:
            return StateMessage(
                type=MessageType.STATE,
                state=DeviceState(obj.get("state", "idle")),
                message=obj.get("message", ""),
            )
        elif msg_type == MessageType.CONFIG.value:
            return ConfigMessage(
                type=MessageType.CONFIG,
                config=obj.get("config", {}),
            )
        elif msg_type == MessageType.ERROR.value:
            return ErrorMessage(
                type=MessageType.ERROR,
                code=obj.get("code", 0),
                message=obj.get("message", ""),
            )
        elif msg_type == MessageType.PING.value:
            return self._parse_ping(obj)
        elif msg_type == MessageType.LISTEN.value:
            return self._parse_listen(obj)
        elif msg_type == MessageType.TTS.value:
            return self._parse_tts(obj)
        elif msg_type == MessageType.STT.value:
            return STTMessage(
                type=MessageType.STT,
                text=obj.get("text", ""),
                is_final=obj.get("is_final", True),
            )
        elif msg_type == MessageType.LLM.value:
            return LLMMessage(
                type=MessageType.LLM,
                emotion=obj.get("emotion", ""),
                text=obj.get("text", ""),
            )
        elif msg_type == MessageType.START_LISTEN.value:
            return ListenMessage(
                type=MessageType.LISTEN,
                state=ListenState.START,
                mode=obj.get("mode", "auto"),
            )
        elif msg_type == MessageType.STOP_LISTEN.value:
            return ListenMessage(
                type=MessageType.LISTEN,
                state=ListenState.STOP,
            )
        elif msg_type == MessageType.ABORT.value:
            return ListenMessage(
                type=MessageType.LISTEN,
                state=ListenState.STOP,
            )

        logger.warning(f"Unknown message type: {msg_type}")
        return None

    def _parse_audio(self, obj: Dict[str, Any]) -> AudioMessage:
        audio_data = b""
        if "audio" in obj:
            audio_b64 = obj["audio"]
            if isinstance(audio_b64, str):
                audio_data = base64.b64decode(audio_b64)
        elif "data" in obj:
            data = obj["data"]
            if isinstance(data, str):
                audio_data = base64.b64decode(data)
            elif isinstance(data, bytes):
                audio_data = data

        audio_config = obj.get("audio_config", obj.get("audio_params", {}))
        audio_format = AudioFormat.PCM
        if audio_config.get("format") == "opus":
            audio_format = AudioFormat.OPUS
        return AudioMessage(
            type=MessageType.AUDIO,
            format=audio_format,
            sample_rate=audio_config.get("sample_rate", 16000),
            channels=audio_config.get("channels", 1),
            data=audio_data,
            sequence=obj.get("sequence", 0),
            timestamp=obj.get("timestamp", 0),
        )

    def _parse_hello(self, obj: Dict[str, Any]) -> HelloMessage:
        features = obj.get("features", {})
        capabilities = obj.get("capabilities", [])
        if isinstance(features, dict):
            capabilities = features.get("capabilities", capabilities)
        
        return HelloMessage(
            type=MessageType.HELLO,
            device_id=obj.get("device_id", obj.get("device-id", "")),
            client_id=obj.get("client_id", obj.get("client-id", "")),
            version=str(obj.get("version", "")),
            capabilities=capabilities,
            audio_config=obj.get("audio_params", obj.get("audio_config", {})),
            features=features,
            transport=obj.get("transport", "websocket"),
        )

    def _parse_ping(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": MessageType.PING, "timestamp": obj.get("timestamp", 0)}

    def _parse_listen(self, obj: Dict[str, Any]) -> ListenMessage:
        state_str = obj.get("state", "start")
        state_map = {
            "start": ListenState.START,
            "stop": ListenState.STOP,
            "detect": ListenState.DETECT,
        }
        return ListenMessage(
            type=MessageType.LISTEN,
            state=state_map.get(state_str, ListenState.START),
            mode=obj.get("mode", "auto"),
            text=obj.get("text", ""),
        )

    def _parse_tts(self, obj: Dict[str, Any]) -> TTSMessage:
        state_str = obj.get("state", "start")
        state_map = {
            "start": TTSState.START,
            "stop": TTSState.STOP,
            "sentence_start": TTSState.SENTENCE_START,
        }
        return TTSMessage(
            type=MessageType.TTS,
            state=state_map.get(state_str, TTSState.START),
            text=obj.get("text", ""),
        )

    def encode_audio(
        self,
        audio_data: bytes,
        format: AudioFormat = AudioFormat.PCM,
        sample_rate: int = 16000,
        channels: int = 1,
        use_base64: bool = True,
    ) -> Union[str, bytes]:
        if use_base64:
            audio_b64 = base64.b64encode(audio_data).decode("ascii")
            msg = {
                "type": MessageType.AUDIO.value,
                "audio": audio_b64,
                "audio_config": {
                    "format": format.value,
                    "sample_rate": sample_rate,
                    "channels": channels,
                },
                "sequence": self._next_sequence(),
            }
            return json.dumps(msg)
        return audio_data

    def encode_text(
        self,
        text: str,
        state: Optional[DeviceState] = None,
    ) -> str:
        msg = {
            "type": MessageType.TEXT.value,
            "text": text,
            "sequence": self._next_sequence(),
        }
        if state:
            msg["state"] = state.value
        return json.dumps(msg)

    def encode_state(
        self,
        state: DeviceState,
        message: str = "",
    ) -> str:
        msg = {
            "type": MessageType.STATE.value,
            "state": state.value,
            "message": message,
        }
        return json.dumps(msg)

    def encode_hello(
        self,
        device_id: str,
        client_id: str = "",
        version: str = "1",
        features: Optional[Dict[str, Any]] = None,
        transport: str = "websocket",
        audio_params: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> str:
        msg = {
            "type": MessageType.HELLO.value,
            "version": int(version),
            "features": features or {"mcp": True},
            "transport": transport,
            "audio_params": audio_params or {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
        if device_id:
            msg["device_id"] = device_id
        if client_id:
            msg["client_id"] = client_id
        if session_id:
            msg["session_id"] = session_id
        return json.dumps(msg)

    def encode_pong(self, timestamp: int = 0) -> str:
        msg = {
            "type": MessageType.PONG.value,
            "timestamp": timestamp,
        }
        return json.dumps(msg)

    def encode_error(
        self,
        code: int,
        message: str,
    ) -> str:
        msg = {
            "type": MessageType.ERROR.value,
            "code": code,
            "message": message,
        }
        return json.dumps(msg)

    def encode_start_listen(self, mode: str = "auto") -> str:
        msg = {
            "type": MessageType.LISTEN.value,
            "state": "start",
            "mode": mode,
        }
        return json.dumps(msg)

    def encode_stop_listen(self) -> str:
        msg = {
            "type": MessageType.LISTEN.value,
            "state": "stop",
        }
        return json.dumps(msg)

    def encode_abort(self, reason: str = "") -> str:
        msg = {
            "type": MessageType.ABORT.value,
        }
        if reason:
            msg["reason"] = reason
        return json.dumps(msg)

    def encode_tts_start(self, session_id: str = "") -> str:
        msg = {
            "type": MessageType.TTS.value,
            "state": "start",
        }
        if session_id:
            msg["session_id"] = session_id
        return json.dumps(msg)

    def encode_tts_stop(self, session_id: str = "") -> str:
        msg = {
            "type": MessageType.TTS.value,
            "state": "stop",
        }
        if session_id:
            msg["session_id"] = session_id
        return json.dumps(msg)

    def encode_tts_sentence(self, text: str, session_id: str = "") -> str:
        msg = {
            "type": MessageType.TTS.value,
            "state": "sentence_start",
            "text": text,
        }
        if session_id:
            msg["session_id"] = session_id
        return json.dumps(msg)

    def encode_stt(self, text: str, session_id: str = "", is_final: bool = True) -> str:
        msg = {
            "type": MessageType.STT.value,
            "text": text,
            "is_final": is_final,
        }
        if session_id:
            msg["session_id"] = session_id
        return json.dumps(msg)
