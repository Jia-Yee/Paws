# -*- coding: utf-8 -*-
"""ESP32 Device Connection - Manages individual device connections."""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import websockets

from .constants import DeviceState
from .protocol import XiaozhiProtocol

if TYPE_CHECKING:
    from ....voice import VoiceProcessor
    from ....voice.opus import OpusDecoder, OpusEncoder

logger = logging.getLogger(__name__)


class ESP32DeviceConnection:
    """Manages a single ESP32 device connection."""

    def __init__(
        self,
        device_id: str,
        websocket: websockets.ServerConnection,
        protocol: XiaozhiProtocol,
    ):
        self.device_id = device_id
        self.websocket = websocket
        self.protocol = protocol
        self.state: DeviceState = DeviceState.IDLE
        self.client_id: str = ""
        self.version: str = ""
        self.session_id: str = ""
        self.audio_config: Dict[str, Any] = {}
        self.last_activity: float = time.time()
        self.voice_processor: Optional[VoiceProcessor] = None
        self._audio_buffer: List[bytes] = []
        self._is_aborted: bool = False
        self._speaking_task: Optional[asyncio.Task] = None
        self._opus_decoder: Optional[OpusDecoder] = None
        self._opus_encoder: Optional[OpusEncoder] = None
        self._new_session_event: asyncio.Event = asyncio.Event()

        # Audio processing attributes (ref: xiaozhi-esp32-server)
        self.asr_audio_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.executor = ThreadPoolExecutor(max_workers=5)

        # VAD attributes
        self.vad: Any = None
        self.client_have_voice = False
        self.client_voice_stop = False
        self.first_activity_time = 0.0  # 记录首次活动的时间（毫秒）
        self.last_activity_time = 0.0  # 统一的活动时间戳（毫秒）
        self.client_listen_mode = "auto"

        # ASR attributes
        self.asr: Any = None
        self.asr_audio = []
        self.current_speaker = None  # 存储当前说话人

        # TTS attributes
        self.tts: Any = None
        self.client_is_speaking = False

        # iot related
        self.iot_descriptors = {}

        # 标记连接是否来自MQTT
        self.conn_from_mqtt_gateway = False

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def is_idle(self, timeout_seconds: float = 300.0) -> bool:
        """Check if connection has been idle for too long."""
        return time.time() - self.last_activity > timeout_seconds

    def reset_audio_states(self) -> None:
        """Reset audio processing states."""
        self.asr_audio = []
        self.client_have_voice = False
        self.client_voice_stop = False

    async def _process_mqtt_audio_message(self, message):
        """
        处理来自MQTT网关的音频消息，解析16字节头部并提取音频数据

        Args:
            message: 包含头部的音频消息

        Returns:
            bool: 是否成功处理了消息
        """
        try:
            # 提取头部信息
            timestamp = int.from_bytes(message[8:12], "big")
            audio_length = int.from_bytes(message[12:16], "big")

            # 提取音频数据
            if audio_length > 0 and len(message) >= 16 + audio_length:
                # 有指定长度，提取精确的音频数据
                audio_data = message[16 : 16 + audio_length]
                # 基于时间戳进行排序处理
                self._process_websocket_audio(audio_data, timestamp)
                return True
            elif len(message) > 16:
                # 没有指定长度或长度无效，去掉头部后处理剩余数据
                audio_data = message[16:]
                self.asr_audio_queue.put(audio_data)
                return True
        except Exception as e:
            logger.error(f"解析WebSocket音频包失败: {e}")

        # 处理失败，返回False表示需要继续处理
        return False

    def _process_websocket_audio(self, audio_data, timestamp):
        """处理WebSocket格式的音频包"""
        # 初始化时间戳序列管理
        if not hasattr(self, "audio_timestamp_buffer"):
            self.audio_timestamp_buffer = {}
            self.last_processed_timestamp = 0
            self.max_timestamp_buffer_size = 20

        # 如果时间戳是递增的，直接处理
        if timestamp >= self.last_processed_timestamp:
            self.asr_audio_queue.put(audio_data)
            self.last_processed_timestamp = timestamp

            # 处理缓冲区中的后续包
            processed_any = True
            while processed_any:
                processed_any = False
                for ts in sorted(self.audio_timestamp_buffer.keys()):
                    if ts > self.last_processed_timestamp:
                        buffered_audio = self.audio_timestamp_buffer.pop(ts)
                        self.asr_audio_queue.put(buffered_audio)
                        self.last_processed_timestamp = ts
                        processed_any = True
                        break
        else:
            # 乱序包，暂存
            if len(self.audio_timestamp_buffer) < self.max_timestamp_buffer_size:
                self.audio_timestamp_buffer[timestamp] = audio_data
            else:
                self.asr_audio_queue.put(audio_data)
